"""Public builder/worker behavior with isolated source trees and fake unrar."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from pi.apps.media_organizer import alias_links, alias_main, config


class AliasLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="alias-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.mirror = self.root / "mirror"
        self.mirror.mkdir()
        self.patches = patch.multiple(config, MEDIA_ROOT=self.source,
                                     MIRROR_ROOT=self.mirror,
                                     LOCK_PATH=self.root / "alias.lock",
                                     ALIAS_LOG_PATH=self.root / "alias.log")
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop(config.ALIAS_OUTPUT_ROOT_ENV, None)
        self.stdout = redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.stderr = redirect_stderr(io.StringIO())
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def media(self, name, size=None):
        path = self.source / "torrent" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            stream.truncate(config.MIN_MEDIA_BYTES + 1 if size is None else size)
        return path

    def listing(self, links=None):
        links = links or self.source / "links"
        return {str(path.relative_to(links)): os.readlink(path)
                for path in links.rglob("*") if path.is_symlink()}

    def fake_unrar(self, success=True):
        binary = self.root / "bin" / "unrar"
        binary.parent.mkdir()
        action = (f'printf extracted > invoked; truncate -s '
                  f'{config.ALIAS_EXTRACTED_MIN_BYTES + 1} "Extracted.1080p.mkv"'
                  if success else 'exit 1')
        binary.write_text('#!/bin/sh\n' + action + '\n')
        binary.chmod(0o755)
        os.environ["PATH"] = str(binary.parent) + os.pathsep + os.environ["PATH"]

    def test_full_tree_and_permissions_keep_targets_unchanged(self):
        tv = self.media("TV/Show Name/Season 1/Show.S01E01.1080p.x264-GROUP.mkv")
        movie = self.media("Movies/Film.2020.1080p.mkv")
        doc = self.media("Documentaries/Series/Episode.1080p.mp4")
        new = self.media("New/Fresh.1080p.avi")
        incomplete = self.media("incomplete/Still.Downloading.rar")
        tv.chmod(0o640)
        self.media("TV/Loose.mkv")  # Historical TV needs a show directory.
        self.media("Documentaries/Loose.mkv")  # Documentary root files ignored.
        links = alias_links.build_links(self.source, "full")
        self.assertEqual(self.listing(links), {
            "TV/Show_Name/Show_S01E01": str(tv), "Movies/Film_2020": str(movie),
            "Documentaries/Episode": str(doc), "New/Fresh": str(new),
            "incomplete/Still_Downloading": str(incomplete),
        })
        for path in [links] + [p for p in links.rglob("*") if not p.is_symlink()]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o777)
        self.assertEqual(tv.stat().st_mode & 0o777, 0o640)

    def test_new_refresh_preserves_other_categories_and_removes_stale_entries(self):
        first = self.media("New/Old.mkv")
        movie = self.media("Movies/Kept.mkv")
        alias_links.build_links(self.source, "full")
        first.unlink()
        fresh = self.media("New/Fresh.mkv")
        alias_links.build_links(self.source, "new")
        self.assertEqual(self.listing(), {"Movies/Kept": str(movie), "New/Fresh": str(fresh)})
        self.assertFalse(any(p.name.startswith(".") for p in self.source.rglob("*")))

    def test_filters_are_strict_case_sensitive_extensions_and_case_insensitive_samples(self):
        kept = self.media("New/Kept.mkv")
        for name, size in [("Exactly.mkv", config.MIN_MEDIA_BYTES), ("Small.mp4", 20),
                           ("Sample.mkv", None), ("SAMPLE/Film.mkv", None),
                           (".hidden/Film.mkv", None), ("Upper.MKV", None),
                           ("Text.txt", None)]:
            self.media("New/" + name, size)
        alias_links.build_links(self.source, "full")
        self.assertEqual(self.listing(), {"New/Kept": str(kept)})

    def test_failed_build_preserves_previous_full_and_incremental_trees(self):
        self.media("New/Original.mkv")
        for mode in ("full", "new"):
            with self.subTest(mode=mode):
                alias_links.build_links(self.source, "full")
                before = self.listing()
                with patch("pi.apps.media_organizer.alias_links.clean_title",
                           side_effect=OSError("name failure")):
                    with self.assertRaisesRegex(OSError, "name failure"):
                        alias_links.build_links(self.source, mode)
                self.assertEqual(self.listing(), before)

    def test_failed_publication_restores_full_and_both_incremental_trees(self):
        self.media("New/Original.mkv")
        self.media("incomplete/Original.mkv")
        original_rename = os.rename
        for mode in ("full", "new"):
            alias_links.build_links(self.source, "full")
            before = self.listing()
            def fail_stage(source, destination, **kwargs):
                source = Path(source)
                if (mode == "full" and source.name.startswith(".links-stage.")) or (
                        mode == "new" and source.name == "New" and
                        source.parent.name.startswith(".alias-new.")):
                    raise OSError("publish failure")
                return original_rename(source, destination, **kwargs)
            with patch("os.rename", side_effect=fail_stage):
                with self.assertRaisesRegex(OSError, "publish failure"):
                    alias_links.build_links(self.source, mode)
            self.assertEqual(self.listing(), before)

    def test_stale_rollback_path_is_never_overwritten(self):
        self.media("New/Fresh.mkv")
        alias_links.build_links(self.source, "full")
        old = self.source / f".links-old.{os.getpid()}"
        old.mkdir()
        (old / "keep").write_text("recovery")
        before = self.listing()
        with self.assertRaises(FileExistsError):
            alias_links.build_links(self.source, "full")
        self.assertEqual((old / "keep").read_text(), "recovery")
        self.assertEqual(self.listing(), before)

    def test_extracts_archive_with_fake_unrar_and_never_deletes_seed_files(self):
        archive = self.media("New/Archive/Release.rar")
        seed = archive.with_suffix(".r00")
        seed.write_text("seed")
        self.fake_unrar()
        alias_links.build_links(self.source, "new")
        self.assertEqual(self.listing(), {"New/Extracted": str(archive.parent / "Extracted.1080p.mkv")})
        self.assertTrue(archive.exists())
        self.assertEqual(seed.read_text(), "seed")
        self.assertTrue((archive.parent / "invoked").exists())

    def test_existing_extracted_video_avoids_unrar(self):
        self.media("New/Archive/Release.rar")
        video = self.media("New/Archive/Film.mkv", config.ALIAS_EXTRACTED_MIN_BYTES + 1)
        with patch("subprocess.run", side_effect=AssertionError("must not extract")):
            alias_links.build_links(self.source, "new")
        self.assertEqual(self.listing(), {"New/Film": str(video)})

    def test_incomplete_archives_are_not_extracted(self):
        archive = self.media("incomplete/Pending.rar")
        with patch("subprocess.run", side_effect=AssertionError("must not extract")):
            alias_links.build_links(self.source, "new")
        self.assertEqual(self.listing(), {"incomplete/Pending": str(archive)})

    def test_failed_extraction_does_not_publish(self):
        self.media("New/Original.mkv")
        alias_links.build_links(self.source, "new")
        self.media("New/Archive/Release.rar")
        self.fake_unrar(success=False)
        before = self.listing()
        with self.assertRaisesRegex(RuntimeError, "did not find vidfile"):
            alias_links.build_links(self.source, "new")
        self.assertEqual(self.listing(), before)

    def test_output_override_never_mutates_live_or_extracts(self):
        kept = self.media("New/Kept.mkv")
        alias_links.build_links(self.source, "full")
        before = self.listing()
        os.environ[config.ALIAS_OUTPUT_ROOT_ENV] = str(self.root / "verification")
        with patch("subprocess.run", side_effect=AssertionError("no external commands")):
            links = alias_links.build_links(self.source, "full")
        self.assertEqual(self.listing(links), {"New/Kept": str(kept)})
        self.assertEqual(self.listing(), before)
        self.media("New/Archive/Release.rar")
        with self.assertRaisesRegex(RuntimeError, "will not extract"):
            alias_links.build_links(self.source, "new")
        self.assertEqual(self.listing(), before)

    def test_output_override_refuses_overlap(self):
        for output in (self.source, self.source / "links", self.root, Path("/")):
            with self.subTest(output=output), self.assertRaises(ValueError):
                alias_links.build_links(self.source, "full", output_root=output)

    def test_output_override_refuses_symlink_back_into_live_links(self):
        self.media("New/Kept.mkv")
        alias_links.build_links(self.source, "full")
        before = self.listing()
        output = self.root / "verification"
        output.mkdir()
        (output / "links").symlink_to(self.source / "links", target_is_directory=True)
        for mode in ("full", "new"):
            with self.assertRaisesRegex(ValueError, "private directory"):
                alias_links.build_links(self.source, mode, output_root=output)
        self.assertEqual(self.listing(), before)

    def test_worker_verification_bypasses_lock_mirror_and_notification(self):
        self.media("New/Kept.mkv")
        os.environ[config.ALIAS_OUTPUT_ROOT_ENV] = str(self.root / "verification")
        with patch("pi.apps.media_organizer.alias_links.alias_lock",
                   side_effect=AssertionError("no lock")), patch(
                       "pi.apps.media_organizer.alias_links.notify_video_library",
                       side_effect=AssertionError("no notify")):
            self.assertEqual(alias_main.main([]), 0)
        self.assertTrue((self.root / "verification/links/New/Kept").is_symlink())
        self.assertFalse((self.mirror / "links").exists())

    def test_lock_waits_in_another_process_without_losing_request(self):
        code = ("from pi.apps.media_organizer.alias_links import alias_lock; "
                "print('ready', flush=True); "
                "exec(\"with alias_lock():\\n print('acquired', flush=True)\")")
        env = dict(os.environ, VAN_ALIAS_MEDIA_LOCK=str(config.LOCK_PATH))
        with alias_links.alias_lock():
            child = subprocess.Popen([sys.executable, "-c", code], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline(), b"ready\n")
            self.assertFalse(select.select([child.stdout], [], [], 0.15)[0])
        out, err = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out, b"acquired\n")

    def test_worker_notifies_http_only_after_successful_build(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.headers.get("X-Van-Video")))
                self.send_response(204)
                self.end_headers()
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.media("New/Kept.mkv")
        url = f"http://127.0.0.1:{server.server_port}/api/torrents/reconcile"
        with patch.object(config, "ALIAS_RECONCILE_URL", url), patch(
                "pi.apps.media_organizer.alias_links.source_is_mounted",
                side_effect=lambda source: source == self.source):
            self.assertEqual(alias_links.run_locked("new"), 0)
            self.assertEqual(received, [("/api/torrents/reconcile", "1")])
            self.media("New/Archive/Release.rar")
            self.fake_unrar(success=False)
            with patch("sys.stderr", io.StringIO()):
                self.assertEqual(alias_links.run_locked("new"), 1)
            self.assertEqual(len(received), 1)
        with patch("pi.apps.media_organizer.alias_links.source_is_mounted", return_value=False), patch(
                "pi.apps.media_organizer.alias_links.notify_video_library") as notify:
            self.assertEqual(alias_links.run_locked("full"), 0)
            notify.assert_not_called()

    def test_full_worker_updates_both_mounted_sources(self):
        live = self.media("New/Live.mkv")
        mirror = self.mirror / "torrent/Movies/Mirror.mkv"
        mirror.parent.mkdir(parents=True)
        with mirror.open("wb") as stream:
            stream.truncate(config.MIN_MEDIA_BYTES + 1)
        with patch("pi.apps.media_organizer.alias_links.source_is_mounted", return_value=True), patch(
                "pi.apps.media_organizer.alias_links.notify_video_library") as notify:
            self.assertEqual(alias_links.run_locked("full"), 0)
            notify.assert_called_once()
        self.assertEqual(self.listing(), {"New/Live": str(live)})
        self.assertEqual(self.listing(self.mirror / "links"), {"Movies/Mirror": str(mirror)})

    def test_wrapper_keeps_arguments_and_imports_from_unrelated_cwd(self):
        repo = Path(__file__).resolve().parents[3]
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(["bash", str(repo / "pi/scripts/alias_media.sh"), "bad"],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_detached_main_preserves_protocol_and_disconnects_all_pipes(self):
        for args, mode in (([], "full"), (["new"], "new")):
            with patch("pi.apps.media_organizer.alias_main.subprocess.Popen") as launch:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(alias_main.main(args), 0)
                self.assertEqual(output.getvalue(), f"alias media {mode} scheduled\n")
                call = launch.call_args
                self.assertEqual(call.args[0][-1], mode)
                self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
                self.assertTrue(call.kwargs["start_new_session"])
                self.assertTrue(call.kwargs["close_fds"])
                self.assertEqual(call.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
                self.assertNotEqual(call.kwargs["stdout"], subprocess.PIPE)
                self.assertEqual(call.kwargs["stdout"], call.kwargs["stderr"])

    def test_actual_detached_worker_returns_while_lock_is_held(self):
        environment = {"VAN_MEDIA_ROOT": str(self.source),
                       "VAN_MEDIA_MIRROR_ROOT": str(self.mirror),
                       "VAN_MEDIA_MIRROR_MOUNT": str(self.root),
                       "VAN_ALIAS_MEDIA_LOCK": str(config.LOCK_PATH),
                       "VAN_ALIAS_MEDIA_LOG": str(config.ALIAS_LOG_PATH)}
        original_popen, children = subprocess.Popen, []
        def launch(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child
        with patch.dict(os.environ, environment), alias_links.alias_lock():
            with patch("pi.apps.media_organizer.alias_main.subprocess.Popen", side_effect=launch):
                self.assertEqual(alias_main.main(["new"]), 0)
                self.assertEqual(len(children), 1)
                self.assertIsNone(children[0].poll())
        self.assertEqual(children[0].wait(timeout=5), 0)
        self.assertIn("status=0", config.ALIAS_LOG_PATH.read_text())
        self.assertFalse((self.source / "links").exists())

    def test_invalid_cli_arguments_exit_two_and_do_not_schedule(self):
        for args in (["full"], ["unexpected"], ["new", "extra"]):
            with patch("sys.stderr", io.StringIO()), patch(
                    "pi.apps.media_organizer.alias_main.subprocess.Popen") as launch:
                self.assertEqual(alias_main.main(args), 2)
                launch.assert_not_called()
        child = subprocess.run([sys.executable, "-m", "pi.apps.media_organizer.alias_main", "bad"],
                               capture_output=True, text=True)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(child.stdout, "")
        self.assertIn("usage:", child.stderr)


if __name__ == "__main__":
    unittest.main()
