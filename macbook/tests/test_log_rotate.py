"""Log inventory and copy-truncate rotation, always against a temporary HOME."""
import contextlib
import fcntl
import gzip
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import crontab_sync
import tool_automation
import tool_settings

from log_rotate import archives, cli, copytruncate, eligibility, growth, inventory, rotate, state
from log_rotate.records import MB

REPOSITORY = Path(__file__).resolve().parents[2]
ENTRY = REPOSITORY / "macbook/scripts/log_rotate.py"
PYTHON = "/usr/bin/python3"
OVER_ONE_MB = 1_200_000


def payload(tag, size=OVER_ONE_MB):
    unit = "{} line of fixture text\n".format(tag).encode()
    return (unit * (size // len(unit) + 1))[:size]


class LogRotateCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = os.path.realpath(self.temporary.name)
        self.crontab = Path(self.home) / "crontab-fixture"
        environment = {"HOME": self.home, "CRONTAB_SYNC_FILE": str(self.crontab)}
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("JACOBR_CONFIG_DIR", "JACOBR_SETTINGS_DIR"):
            os.environ.pop(name, None)

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def write(self, relative, data=b"x", mode=0o644):
        path = self.path(relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as output:
            output.write(data if isinstance(data, bytes) else data.encode())
        os.chmod(path, mode)
        return path

    def read(self, path, mode="rb"):
        with open(path, mode) as source:
            return source.read()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        text = out.getvalue()
        return code, (json.loads(text) if text.startswith(("{", "[")) else text), err.getvalue()

    def inventory(self):
        code, value, _ = self.run_cli("inventory", "--json")
        self.assertEqual(code, 0, value)
        return value

    def entry(self, path, snapshot=None):
        snapshot = snapshot or self.inventory()
        found = [log for log in snapshot["logs"] if log["path"] == path]
        self.assertEqual(len(found), 1, "{} not listed once in {}".format(path, [l["path"] for l in snapshot["logs"]]))
        return found[0]

    def set_policy(self, path, *flags):
        revision = self.inventory()["revision"]
        code, value, _ = self.run_cli("policy", "--revision", revision, path, *flags, "--json")
        self.assertEqual(code, 0, value)
        return value


class SourceTests(LogRotateCase):
    def test_launchagent_logs_are_listed_with_their_label(self):
        plist = {"Label": "com.example.job", "StandardOutPath": self.path("jobs/out.log"),
                 "StandardErrorPath": self.path("jobs/err.log")}
        os.makedirs(self.path("Library/LaunchAgents"))
        with open(self.path("Library/LaunchAgents/com.example.job.plist"), "wb") as output:
            plistlib.dump(plist, output)
        self.write("jobs/out.log", "o")
        snapshot = self.inventory()
        self.assertEqual(self.entry(plist["StandardOutPath"], snapshot)["source"], "launchagent:com.example.job")
        self.assertEqual(self.entry(plist["StandardErrorPath"], snapshot)["state"], "missing")

    def test_crontab_redirect_targets_are_listed(self):
        self.crontab.write_text(
            "logs=$HOME/cronlogs\n"
            "0 * * * * /bin/job >> $logs/a.log 2>&1\n"
            "5 * * * * /bin/job > ~/cronlogs/b.out 2> $HOME/cronlogs/c.err\n"
            "#off 9 * * * * /bin/job &> /dev/null\n"
            "7 * * * * /bin/job &> $logs/d.log\n")
        for name in ("a.log", "b.out", "c.err", "d.log"):
            self.write("cronlogs/" + name, "x")
        snapshot = self.inventory()
        sources = {os.path.basename(log["path"]): log["source"] for log in snapshot["logs"]}
        self.assertEqual(set(sources), {"a.log", "b.out", "c.err", "d.log"})
        self.assertTrue(all(source.startswith("crontab:") for source in sources.values()))

    def test_cron_rotation_ignores_quoted_operators_and_dynamic_commands(self):
        cases = {
            "grep '>' \"$HOME/important.db\"": [],
            'cmd >> ~/x.log 2>&1': [self.path('x.log')],
            'cmd > "$HOME/a b.log"': [self.path('a b.log')],
            'cmd $(echo x) > ~/x.log': [],
            'eval cmd > ~/x.log': [],
            'cmd << EOF > ~/x.log': [],
            'cmd > "$other/x.log"': [],
            r'cmd \> ~/x.log': [],
            'cmd > "$HOME/a.log".db': [],
            'cmd > $HOME/a#b.log': [self.path('a#b.log')],
            'cmd # > $HOME/important.db': [],
            'cmd > "$HOME"/a.log': [],
            'echo hi % > /x.db': [],
            '[[ a > /x.db ]]': [],
            '(( a > /x.db ))': [],
            'echo ${X:-> /x.db }': [],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(crontab_sync.redirect_targets(command, {'HOME': self.home}, self.home), expected)
        self.assertEqual(crontab_sync.redirected_log("grep '>' \"$HOME/important.db\"",
                                                   {'HOME': self.home}, self.home), self.path('important.db'))
        target = self.write('important.db', payload('not a log'))
        self.crontab.write_text("0 * * * * grep '>' \"$HOME/important.db\"\n")
        self.assertNotIn(target, [row['path'] for row in self.inventory()['logs']])
        self.assertEqual(self.run_cli('rotate', '--json')[1]['rotated'], [])
        self.assertEqual(os.path.getsize(target), OVER_ONE_MB)

    def test_scan_roots_depth_extensions_and_symlinks(self):
        self.write(".local/state/app/a.log")
        self.write(".local/state/a/b/c/d/ok.err")
        self.write(".local/state/a/b/c/d/e/too-deep.log")
        self.write(".local/state/app/notes.txt")
        self.write(".local/state/app/a.log.1.gz")
        self.write("dev/scripts/tmp/run.out")
        self.write("Library/Logs/App/app.log")
        self.write("elsewhere/outside-roots.log")
        self.write("realdir/inside.log")
        os.symlink(self.path("realdir"), self.path(".local/state/linkdir"))
        os.symlink(self.path("realdir/inside.log"), self.path(".local/state/app/link.log"))
        listed = {log["path"] for log in self.inventory()["logs"]}
        self.assertEqual(listed, {self.path(p) for p in (
            ".local/state/app/a.log", ".local/state/a/b/c/d/ok.err",
            "dev/scripts/tmp/run.out", "Library/Logs/App/app.log")})

    def test_scan_never_enters_credential_or_synced_directories(self):
        self.write(".local/state/.claude/session.log")
        self.write(".local/state/.codex/session.log")
        self.write("Library/Logs/.claude/x.log")
        self.assertEqual(self.inventory()["logs"], [])

    def test_first_source_wins_and_duplicates_collapse(self):
        target = self.write(".local/state/app/job.log")
        os.makedirs(self.path("Library/LaunchAgents"))
        with open(self.path("Library/LaunchAgents/a.plist"), "wb") as output:
            plistlib.dump({"Label": "com.example.a", "StandardOutPath": target}, output)
        self.crontab.write_text("0 * * * * /bin/job >> {} 2>&1\n".format(target))
        policy_code, _, _ = self.run_cli("add", "--revision", "missing", target, "--json")
        self.assertEqual(policy_code, 0)
        self.assertEqual(self.entry(target)["source"], "launchagent:com.example.a")

    def test_extras_and_ignores_come_from_the_private_config(self):
        extra = self.write("anywhere/custom.log", "x")
        ignored = self.write(".local/state/app/noisy.log", "x")
        code, value, _ = self.run_cli("add", "--revision", "missing", extra, "--json")
        self.assertEqual((code, value["log"]["source"]), (0, "extra"))
        code, value, _ = self.run_cli("ignore", "--revision", value["revision"], ignored, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(value["ignored"], [ignored])
        self.assertNotIn(ignored, [log["path"] for log in value["logs"]])
        code, value, _ = self.run_cli("unignore", "--revision", value["revision"], ignored, "--json")
        self.assertIn(ignored, [log["path"] for log in value["logs"]])


class RefusalTests(LogRotateCase):
    def refused(self, path):
        entry = self.entry(path)
        self.assertEqual(entry["state"], "refused", entry)
        self.assertEqual(entry["bytes"], 0)
        return entry

    def list_via_crontab(self, *paths):
        self.crontab.write_text("".join("0 * * * * /bin/job >> {}\n".format(p) for p in paths))

    def test_paths_outside_home_or_in_excluded_places_are_refused_unread(self):
        inside = self.path(".claude/projects/x.log")
        synced = self.path("Library/Mobile Documents/com~apple~CloudDocs/x.log")
        self.write(".claude/projects/x.log")
        self.list_via_crontab("/var/log/system.log", inside, "'{}'".format(synced))
        self.assertIn("outside", self.refused("/var/log/system.log")["note"])
        self.assertIn(".claude", self.refused(inside)["note"])
        self.assertIn("Mobile Documents", self.refused(synced)["note"])

    def test_symlinks_and_symlinked_directories_are_refused(self):
        real = self.write("real/target.log", payload("t"))
        os.symlink(real, self.path("link.log"))
        os.symlink(self.path("real"), self.path("linkdir"))
        self.list_via_crontab(self.path("link.log"), self.path("linkdir/target.log"))
        self.assertEqual(self.refused(self.path("link.log"))["note"], "symlink")
        self.assertIn("symlink", self.refused(self.path("linkdir/target.log"))["note"])

    def test_hard_links_to_protected_logs_are_refused(self):
        protected = self.write('.claude/session.log', payload('protected'))
        os.makedirs(self.path('.local/state/app'))
        linked = self.path('.local/state/app/session.log')
        os.link(protected, linked)
        self.assertEqual(self.refused(linked)['note'], 'hard-linked file')
        result = self.run_cli('rotate', '--json')[1]
        self.assertEqual(result['rotated'], [])
        self.assertEqual(os.path.getsize(protected), OVER_ONE_MB)

    def test_hard_link_added_during_open_is_refused(self):
        path = self.write('.local/state/app/race.log', payload('race'))
        checked = eligibility.inspect(path, self.home)
        os.link(path, self.path('alias.log'))
        with mock.patch.object(eligibility, 'inspect', return_value=checked):
            with self.assertRaises(copytruncate.LogRotateError) as caught:
                copytruncate.rotate_file(path, self.home, 5, True)
        self.assertIn('hard-linked file', str(caught.exception))
        self.assertEqual(os.path.getsize(path), OVER_ONE_MB)

    def test_files_owned_by_someone_else_are_refused(self):
        other = self.write("job.log", "x")
        self.list_via_crontab(other)
        with mock.patch.object(eligibility, "current_uid", return_value=os.getuid() + 1):
            self.assertEqual(self.refused(other)["note"], "not owned by you")

    def test_directories_and_fifos_are_not_regular_files(self):
        os.makedirs(self.path("dir.log"))
        os.mkfifo(self.path("pipe.log"))
        self.list_via_crontab(self.path("dir.log"), self.path("pipe.log"))
        self.assertEqual(self.refused(self.path("dir.log"))["note"], "not a regular file")
        self.assertEqual(self.refused(self.path("pipe.log"))["note"], "not a regular file")

    def test_rotate_leaves_refused_files_alone_and_says_so(self):
        real = self.write("real/target.log", payload("t"))
        os.symlink(real, self.path("link.log"))
        self.list_via_crontab(self.path("link.log"))
        self.set_policy(real, "--max-mb", "1")
        code, value, _ = self.run_cli("rotate", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([item["path"] for item in value["skipped"]], [self.path("link.log")])
        self.assertTrue(value["skipped"][0]["reason"].startswith("refused"))
        self.assertEqual(value["rotated"], [])
        self.assertEqual(os.path.getsize(real), OVER_ONE_MB)

    def test_rotate_only_rejects_paths_the_inventory_does_not_list(self):
        stranger = self.write("stranger.log", payload("s"))
        code, value, _ = self.run_cli("rotate", "--only", stranger, "--force", "--json")
        self.assertEqual(code, 1)
        self.assertIn("not a known log", value["error"])
        self.assertEqual(os.path.getsize(stranger), OVER_ONE_MB)

    def test_policy_and_add_refuse_off_limits_paths(self):
        for command in (("add",), ("policy", "--max-mb", "2")):
            args = ("--revision", "missing", self.path(".codex/x.log")) + command[1:]
            code, value, _ = self.run_cli(command[0], *args, "--json")
            self.assertEqual(code, 1, value)
            self.assertIn("refused", value["error"])


class RotationTests(LogRotateCase):
    def make_log(self, name="app/job.log", data=None, flags=("--max-mb", "1")):
        path = self.write(".local/state/" + name, payload(name) if data is None else data)
        if flags:
            self.set_policy(path, *flags)
        return path

    def rotate(self, *argv):
        code, value, _ = self.run_cli("rotate", *argv, "--json")
        self.assertEqual(code, 0, value)
        return value

    def test_copy_truncate_keeps_the_inode_and_appenders_continue_at_offset_zero(self):
        path = self.make_log()
        original = self.read(path)
        inode = os.stat(path).st_ino
        writer = os.open(path, os.O_WRONLY | os.O_APPEND)
        self.addCleanup(os.close, writer)
        value = self.rotate()
        self.assertEqual(value["rotated"], [{"path": path, "bytes_before": OVER_ONE_MB,
                                             "archive": path + ".1.gz"}])
        self.assertEqual(os.stat(path).st_ino, inode)
        self.assertEqual(os.path.getsize(path), 0)
        os.write(writer, b"after rotation\n")
        self.assertEqual(self.read(path), b"after rotation\n")
        with gzip.open(path + ".1.gz", "rb") as archive:
            self.assertEqual(archive.read(), original)
        self.assertEqual(stat.S_IMODE(os.stat(path + ".1.gz").st_mode), 0o600)

    def test_keep_shifts_archives_and_drops_the_oldest(self):
        path = self.make_log(flags=("--max-mb", "1", "--keep", "2"))
        generations = []
        for number in range(3):
            data = payload("generation{}".format(number))
            with open(path, "wb") as output:
                output.write(data)
            generations.append(data)
            self.rotate()
        self.assertEqual(sorted(p for p in os.listdir(os.path.dirname(path)) if p != "job.log"),
                         ["job.log.1.gz", "job.log.2.gz"])
        with gzip.open(path + ".1.gz") as newest, gzip.open(path + ".2.gz") as older:
            self.assertEqual((newest.read(), older.read()), (generations[2], generations[1]))

    def test_foreign_archive_names_refuse_without_changing_anything(self):
        for suffix, symlink in (('.99.gz', False), ('.1.gz', True)):
            with self.subTest(suffix=suffix):
                path = self.make_log('app/job' + suffix + '.log')
                foreign = path + suffix
                target = self.write('unrelated' + suffix, b'foreign')
                if symlink:
                    os.symlink(target, foreign)
                else:
                    Path(foreign).write_bytes(b'foreign')
                before = os.lstat(foreign)
                original = self.read(path)
                code, result, _ = self.run_cli('rotate', '--only', path, '--json')
                self.assertEqual(code, 0)
                self.assertEqual(result['skipped'][0]['reason'], 'refused: ' + archives.UNRECORDED_NOTE)
                self.assertEqual(os.lstat(foreign), before)
                self.assertEqual(self.read(target), b'foreign')
                self.assertEqual(self.read(path), original)


    def test_replaced_recorded_archive_is_not_owned(self):
        path = self.make_log()
        self.rotate()
        archive = path + '.1.gz'
        os.rename(archive, archive + '.saved')
        Path(archive).write_bytes(b'foreign replacement')
        Path(path).write_bytes(payload('next'))
        code, result, _ = self.run_cli('rotate', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(result['skipped'][0]['reason'], 'refused: ' + archives.UNRECORDED_NOTE)
        self.assertEqual(self.read(archive), b'foreign replacement')
        self.assertEqual(os.path.getsize(path), OVER_ONE_MB)

    def test_archive_provenance_tracks_normal_shifts(self):
        path = self.make_log(flags=('--max-mb', '1', '--keep', '2'))
        self.rotate()
        first_inode = os.lstat(path + '.1.gz').st_ino
        Path(path).write_bytes(payload('next'))
        self.rotate()
        ledger = state.read_json(self.home, state.ARCHIVES_FILE)
        self.assertEqual(set(ledger), {path + '.1.gz', path + '.2.gz'})
        self.assertEqual(ledger[path + '.2.gz']['st_ino'], first_inode)
        for name, record in ledger.items():
            info = os.lstat(name)
            self.assertEqual(record, {'path': name, 'st_dev': info.st_dev, 'st_ino': info.st_ino, 'size': info.st_size,
                                      'st_birthtime': getattr(info, 'st_birthtime', None)})

    def test_continuous_growth_never_truncates_uncopied_bytes(self):
        path = self.make_log()
        real_copy = copytruncate._copy_pass
        def copy_and_append(*args):
            copied = real_copy(*args)
            with open(path, 'ab') as writer:
                writer.write(b'new bytes\n')
            return copied
        with mock.patch.object(copytruncate, '_copy_pass', side_effect=copy_and_append):
            code, result, _ = self.run_cli('rotate', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(result['errors'][0]['error'], 'grows faster than it can be copied')
        self.assertEqual(os.path.getsize(path), OVER_ONE_MB + copytruncate.MAX_PASSES * len(b'new bytes\n'))
        self.assertEqual(os.listdir(os.path.dirname(path)), ['job.log'])

    def test_short_copy_never_truncates(self):
        path = self.make_log()
        with mock.patch.object(copytruncate.os, 'pread', return_value=b''):
            code, result, _ = self.run_cli('rotate', '--json')
        self.assertEqual(code, 1)
        self.assertIn('grows faster than it can be copied', result['errors'][0]['error'])
        self.assertEqual(os.path.getsize(path), OVER_ONE_MB)
        self.assertEqual(os.listdir(os.path.dirname(path)), ['job.log'])

    def test_preexisting_temporary_name_is_never_deleted(self):
        path = self.make_log()
        collision = Path(path).with_name('.job.log.rotating.' + str(os.getpid()))
        collision.write_bytes(b'not ours')
        self.rotate()
        self.assertEqual(collision.read_bytes(), b'not ours')
        self.assertEqual(os.path.getsize(path), 0)

    def test_replaced_temporary_is_not_published_or_cleaned_up(self):
        path = self.make_log()
        real_write = copytruncate._write_archive
        replacements = []
        def replace_temporary(*args):
            copied = real_write(*args)
            temporary, = Path(path).parent.glob('.job.log.rotating.*')
            temporary.rename(str(temporary) + '.original')
            temporary.write_bytes(b'foreign temporary')
            replacements.append(temporary)
            return copied
        with mock.patch.object(copytruncate, '_write_archive', side_effect=replace_temporary):
            code, result, _ = self.run_cli('rotate', '--json')
        self.assertEqual(code, 1)
        self.assertIn('foreign file at ', result['errors'][0]['error'])
        self.assertEqual(replacements[0].read_bytes(), b'foreign temporary')
        self.assertEqual(os.path.getsize(path), OVER_ONE_MB)
        self.assertFalse(Path(path + '.1.gz').exists())

    def test_uncompressed_archives_and_keep_zero(self):
        path = self.make_log(flags=("--max-mb", "1", "--compress", "off"))
        original = self.read(path)
        self.rotate()
        self.assertEqual(self.read(path + ".1"), original)
        self.set_policy(path, "--keep", "0")
        with open(path, "wb") as output:
            output.write(payload("again"))
        value = self.rotate()
        self.assertIsNone(value["rotated"][0]["archive"])
        self.assertEqual(os.path.getsize(path), 0)
        self.assertEqual(os.listdir(os.path.dirname(path)), ["job.log"])

    def test_archives_are_counted_in_the_inventory(self):
        path = self.make_log()
        self.rotate()
        entry = self.entry(path)
        self.assertEqual((entry["archives"], entry["state"]), (1, "ok"))
        self.assertEqual(entry["archived_bytes"], os.path.getsize(path + ".1.gz"))
        self.assertIsNotNone(entry["last_rotated"])
        self.assertEqual(self.inventory()["totals"]["archived_bytes"], entry["archived_bytes"])

    def test_a_failed_archive_leaves_the_original_and_old_archives_intact(self):
        path = self.make_log()
        original = self.read(path)
        with mock.patch.object(copytruncate, "_write_archive", side_effect=OSError(28, "No space left")):
            code, value, _ = self.run_cli("rotate", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(value["rotated"], [])
        self.assertIn("archive failed", value["errors"][0]["error"])
        self.assertEqual(self.read(path), original)
        self.assertEqual(os.listdir(os.path.dirname(path)), ["job.log"])

    def test_the_archive_is_complete_and_durable_before_the_log_is_truncated(self):
        path = self.make_log()
        original = self.read(path)
        seen = {}
        real_ftruncate = os.ftruncate

        def checking_ftruncate(descriptor, length):
            with gzip.open(path + ".1.gz") as archive:
                seen["archive"] = archive.read()
            seen["log_size"] = os.path.getsize(path)
            return real_ftruncate(descriptor, length)

        with mock.patch.object(copytruncate.os, "ftruncate", checking_ftruncate):
            self.rotate()
        self.assertEqual(seen, {"archive": original, "log_size": OVER_ONE_MB})

    def test_thresholds_force_and_disabled(self):
        small = self.write(".local/state/app/small.log", payload("s", 100_000))
        off = self.make_log("app/off.log", flags=("--max-mb", "1", "--enabled", "off"))
        self.assertEqual(self.entry(off)["state"], "disabled")
        value = self.rotate()
        self.assertEqual(value["rotated"], [])
        self.assertEqual(value["skipped"], [{"path": off, "reason": "disabled"}])
        forced = self.rotate("--force")
        self.assertIn(small, [item["path"] for item in forced["rotated"]])
        self.assertNotIn(off, [item["path"] for item in forced["rotated"]])
        self.assertEqual(os.path.getsize(off), OVER_ONE_MB)
        only = self.rotate("--only", off, "--force")
        self.assertEqual([item["path"] for item in only["rotated"]], [off])

    def test_only_reports_why_a_log_was_not_rotated(self):
        small = self.write(".local/state/app/small.log", payload("s", 100_000))
        value = self.rotate("--only", small)
        self.assertEqual(value["skipped"], [{"path": small, "reason": "below threshold"}])

    def test_the_rotators_own_log_is_rotated_last(self):
        other = self.make_log()
        own = self.write(".local/state/log-rotate/rotate.log", payload("own"))
        self.set_policy(own, "--max-mb", "1")
        value = self.rotate()
        self.assertEqual([item["path"] for item in value["rotated"]], [other, own])
        with gzip.open(own + ".1.gz") as archive:
            self.assertIn("rotated {}".format(other), archive.read().decode())
        self.assertIn("run complete", self.read(own, "r"))

    def test_log_contents_are_never_printed_or_recorded(self):
        secret = "TOKEN-sk-secret-value"
        path = self.make_log(data=(secret + "\n").encode() * 100_000)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli.main(["rotate", "--json"])
            cli.main(["inventory"])
        recorded = self.read(self.path(".local/state/log-rotate/rotate.log"), "r")
        for text in (out.getvalue(), err.getvalue(), recorded):
            self.assertNotIn(secret, text)

    def test_another_run_holding_the_lock_exits_busy(self):
        self.make_log()
        with state.run_lock(self.home):
            code, value, _ = self.run_cli("rotate", "--json")
        self.assertEqual(code, 75)
        self.assertIn("in progress", value["error"])


class GrowthTests(LogRotateCase):
    def test_rate_is_megabytes_written_per_day_between_runs(self):
        path = self.write(".local/state/app/grow.log", b"x" * 1_000_000)
        start = time.time()
        rotate.run_rotation(inventory.take_snapshot(self.home, start), self.home, now=start)
        self.assertIsNone(self.entry(path)["growth_mb_per_day"])
        with open(path, "ab") as output:
            output.write(b"y" * 5 * MB)
        later = start + 3600
        rotate.run_rotation(inventory.take_snapshot(self.home, later), self.home, now=later)
        snapshot = inventory.take_snapshot(self.home, later).as_dict()
        self.assertAlmostEqual(self.entry(path, snapshot)["growth_mb_per_day"], 5 * 24, places=1)

    def test_a_rotation_does_not_look_like_negative_growth(self):
        ledger = growth.advance({}, {"/f": (0, 0)}, 0)
        ledger = growth.advance(ledger, {"/f": (12 * MB, 0)}, 3600)
        self.assertAlmostEqual(ledger["/f"]["mb_per_day"], 12 * 24)
        ledger = growth.advance(ledger, {"/f": (MB, MB)}, 7200)
        self.assertAlmostEqual(ledger["/f"]["mb_per_day"], (12 * 24 + 24) / 2)

    def test_runs_seconds_apart_accumulate_instead_of_spiking(self):
        ledger = growth.advance({}, {"/f": (0, 0)}, 0)
        ledger = growth.advance(ledger, {"/f": (MB, MB)}, 10)
        self.assertIsNone(ledger["/f"]["mb_per_day"])
        ledger = growth.advance(ledger, {"/f": (2 * MB, 2 * MB)}, 3600)
        self.assertAlmostEqual(ledger["/f"]["mb_per_day"], 2 * 24)

    def test_a_stale_rate_is_not_reported(self):
        record = {"mb_per_day": 3.0, "ts": 0}
        self.assertEqual(growth.rate_for(record, 100), 3.0)
        self.assertIsNone(growth.rate_for(record, growth.STALE_AFTER_SECONDS + 1))


class ConfigTests(LogRotateCase):
    def test_policy_overrides_one_file_only_and_is_private(self):
        first = self.write(".local/state/app/a.log", "a")
        second = self.write(".local/state/app/b.log", "b")
        value = self.set_policy(first, "--max-mb", "20", "--keep", "3", "--compress", "off")
        self.assertEqual(value["log"]["policy"],
                         {"max_mb": 20, "keep": 3, "compress": False, "enabled": True, "override": True})
        self.assertEqual(self.entry(second)["policy"],
                         {"max_mb": 10, "keep": 5, "compress": True, "enabled": True, "override": False})
        config = Path(self.home) / ".config/jacobr/log-rotate-files.json"
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertEqual(json.loads(config.read_text())["policies"], {first: {"max_mb": 20, "keep": 3, "compress": False}})

    def test_clear_drops_the_override(self):
        path = self.write(".local/state/app/a.log", "a")
        self.set_policy(path, "--max-mb", "20")
        value = self.set_policy(path, "--clear")
        self.assertFalse(value["log"]["policy"]["override"])
        self.assertEqual(json.loads((Path(self.home) / ".config/jacobr/log-rotate-files.json").read_text())["policies"], {})

    def test_declared_defaults_follow_the_saved_settings(self):
        path = self.write(".local/state/app/a.log", "a")
        tool_settings.save("log-rotate", {"max_mb": 50, "keep": 2})
        entry = self.entry(path)
        self.assertEqual((entry["policy"]["max_mb"], entry["policy"]["keep"], entry["policy"]["override"]), (50, 2, False))
        self.assertEqual(self.inventory()["defaults"]["max_mb"], 50)

    def test_stale_revision_is_a_conflict_and_changes_nothing(self):
        path = self.write(".local/state/app/a.log", "a")
        first = self.set_policy(path, "--max-mb", "20")
        code, value, _ = self.run_cli("policy", "--revision", "missing", path, "--max-mb", "30", "--json")
        self.assertEqual(code, 3)
        self.assertEqual(value["revision"], first["revision"])
        self.assertEqual(self.entry(path)["policy"]["max_mb"], 20)
        for command in ("add", "ignore", "unignore"):
            code, _, _ = self.run_cli(command, "--revision", "stale", path, "--json")
            self.assertEqual(code, 3, command)

    def test_global_keep_zero_is_rejected_but_per_file_zero_stays_valid(self):
        declaration = tool_settings.load_declaration('log-rotate')
        with self.assertRaises(tool_settings.SettingsError):
            tool_settings.validate(declaration, {'keep': 0})
        path = self.write(".local/state/app/default.log", payload("default"))
        self.set_policy(path, '--keep', '0')
        self.assertEqual(self.entry(path)['policy']['keep'], 0)
        code, result, _ = self.run_cli("rotate", "--only", path, "--force", "--json")
        self.assertEqual(code, 0, result)
        self.assertEqual(os.path.getsize(path), 0)
        self.assertFalse(Path(path + '.1.gz').exists())

    def test_invalid_values_name_their_field_and_write_nothing(self):
        path = self.write(".local/state/app/a.log", "a")
        revision = self.inventory()["revision"]
        for flags, field in ((("--max-mb", "0"), "max_mb"), (("--max-mb", "1000"), "max_mb"),
                             (("--keep", "31"), "keep"), (("--keep", "x"), "keep")):
            code, value, _ = self.run_cli("policy", "--revision", revision, path, *flags, "--json")
            self.assertEqual((code, value.get("field")), (1, field), flags)
        self.assertEqual(self.inventory()["revision"], revision)

    def test_a_policy_command_needs_exactly_a_change_or_clear(self):
        path = self.write(".local/state/app/a.log", "a")
        for flags in ((), ("--clear", "--keep", "2")):
            code, value, _ = self.run_cli("policy", "--revision", "missing", path, *flags, "--json")
            self.assertEqual(code, 1, flags)

    def test_a_concurrent_edit_exits_busy(self):
        path = self.write(".local/state/app/a.log", "a")
        lock_path = Path(self.home) / ".config/jacobr/log-rotate-files.json.lock"
        lock_path.parent.mkdir(parents=True)
        with open(lock_path, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            code, _, _ = self.run_cli("policy", "--revision", "missing", path, "--max-mb", "2", "--json")
        self.assertEqual(code, 75)

    def test_a_corrupt_config_is_an_error_not_a_silent_reset(self):
        config = Path(self.home) / ".config/jacobr/log-rotate-files.json"
        config.parent.mkdir(parents=True)
        config.write_text("{not json")
        code, value, _ = self.run_cli("inventory", "--json")
        self.assertEqual(code, 1)
        self.assertIn("invalid JSON", value["error"])
        self.assertEqual(config.read_text(), "{not json")


class StatusTests(LogRotateCase):
    def status(self):
        code, value, _ = self.run_cli("status", "--json")
        self.assertEqual(code, 0, value)
        return tool_automation.validate_status(value)

    def test_never_run_is_unknown_then_ok_after_a_run(self):
        self.write(".local/state/app/a.log", "a")
        self.assertEqual(self.status()["level"], "unknown")
        self.run_cli("rotate", "--json")
        record = self.status()
        self.assertEqual(record["level"], "ok")
        self.assertRegex(record["summary"], r"^\d+ logs")

    def test_a_log_far_over_its_limit_warns_that_rotation_is_failing(self):
        path = self.write(".local/state/app/a.log", payload("a", 3 * MB))
        self.set_policy(path, "--max-mb", "1", "--enabled", "off")
        self.assertNotEqual(self.status()["level"], "warn")
        self.set_policy(path, "--enabled", "on")
        record = self.status()
        self.assertEqual(record["level"], "warn")
        self.assertIn("rotation is failing", record["summary"])

    def test_total_above_the_warn_threshold_warns(self):
        path = self.write(".local/state/app/big.log", b"")
        os.truncate(path, 60 * MB)  # sparse: counts as 60 MB without using the disk
        self.set_policy(path, "--max-mb", "500")
        self.assertEqual(self.status()["level"], "unknown")
        tool_settings.save("log-rotate", {"warn_total_mb": 50})
        record = self.status()
        self.assertEqual(record["level"], "warn")
        self.assertIn("total", record["summary"])


class ThirdPartyLogTests(LogRotateCase):
    def third_party(self):
        return self.write("Library/Logs/OtherApp/main.log", payload("t", 2 * MB))

    def test_scanned_app_logs_start_disabled_but_still_count(self):
        path = self.third_party()
        snapshot = self.inventory()
        entry = self.entry(path, snapshot)
        self.assertEqual((entry["state"], entry["policy"]["enabled"], entry["policy"]["override"]), ("disabled", False, False))
        self.assertEqual(entry["note"], "third-party app log; enable per file")
        self.assertEqual(snapshot["totals"]["bytes"], 2 * MB)
        self.set_policy(path, "--max-mb", "1")
        self.assertEqual(self.entry(path)["state"], "disabled")
        code, value, _ = self.run_cli("rotate", "--json")
        self.assertEqual((value["rotated"], os.path.getsize(path)), ([], 2 * MB))

    def test_a_per_file_policy_or_an_extra_opts_the_log_in(self):
        enabled, added = self.third_party(), self.write("Library/Logs/OtherApp/two.log", payload("u", 2 * MB))
        self.set_policy(enabled, "--max-mb", "1", "--enabled", "on")
        self.assertEqual(self.entry(enabled)["state"], "due")
        self.assertNotIn("note", self.entry(enabled))
        revision = self.inventory()["revision"]
        self.assertEqual(self.run_cli("add", "--revision", revision, added, "--json")[0], 0)
        self.assertEqual(self.entry(added)["policy"]["enabled"], True)
        self.set_policy(enabled, "--clear")
        self.assertEqual(self.entry(enabled)["state"], "disabled")

    def test_a_launchagent_log_under_library_logs_is_not_third_party(self):
        path = self.write("Library/Logs/mine.log", payload("m", 2 * MB))
        os.makedirs(self.path("Library/LaunchAgents"))
        with open(self.path("Library/LaunchAgents/com.example.mine.plist"), "wb") as output:
            plistlib.dump({"Label": "com.example.mine", "StandardOutPath": path}, output)
        entry = self.entry(path)
        self.assertEqual((entry["source"], entry["policy"]["enabled"]), ("launchagent:com.example.mine", True))
        self.assertNotIn("note", entry)


class ProblemTests(LogRotateCase):
    def test_inventory_lists_what_needs_attention_and_status_says_the_same(self):
        big = self.write(".local/state/app/big.log", payload("b", 3 * MB))
        self.set_policy(big, "--max-mb", "1")
        found = self.inventory()["problems"]
        self.assertEqual([(item["level"], item["path"]) for item in found], [("error", big)])
        self.assertIn("rotation is failing", found[0]["text"])
        self.assertIn(found[0]["text"], self.status_summary())
        self.assertEqual(self.inventory()["problems"], found)

    def test_a_refused_entry_stays_visible_but_is_not_a_problem(self):
        link = self.path("jobs/link.log")
        self.write("jobs/real.log")
        os.symlink(self.path("jobs/real.log"), link)
        os.makedirs(self.path("Library/LaunchAgents"))
        with open(self.path("Library/LaunchAgents/com.example.link.plist"), "wb") as output:
            plistlib.dump({"Label": "com.example.link", "StandardOutPath": link}, output)
        self.assertEqual(self.entry(link)["state"], "refused")
        self.assertEqual(self.entry(link)["note"], "symlink")
        self.assertEqual(self.inventory()["problems"], [])
        self.assertNotIn("refused", self.status_summary())

    def status_summary(self):
        return self.run_cli("status", "--json")[1]["summary"]


class EntryPointTests(LogRotateCase):
    def run_entry(self, *argv, extra_env=None):
        environment = {"PATH": "/usr/bin:/bin", "HOME": self.home, "CRONTAB_SYNC_FILE": str(self.crontab),
                       "PYTHONPATH": str(REPOSITORY / "shared/python"), "PYTHONDONTWRITEBYTECODE": "1"}
        environment.update(extra_env or {})
        return subprocess.run([PYTHON, "-B", str(ENTRY), *argv], env=environment, cwd=self.home,
                              capture_output=True, text=True, timeout=60)

    def test_the_stable_entry_serves_the_documented_exit_codes(self):
        path = self.write(".local/state/app/a.log", "a")
        result = self.run_entry("inventory", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["logs"][0]["path"], path)
        stale = self.run_entry("ignore", "--revision", "stale", path, "--json")
        self.assertEqual((stale.returncode, json.loads(stale.stdout)["revision"]), (3, "missing"))
        self.assertEqual(self.run_entry("policy", "--revision", "x").returncode, 2)

    def test_the_stable_entry_reports_busy_while_another_process_rotates(self):
        self.write(".local/state/app/a.log", "a")
        with state.run_lock(self.home):
            result = self.run_entry("rotate", "--json")
        self.assertEqual(result.returncode, 75)

    def test_declarations_and_launchagent_match_the_entry(self):
        declaration = tool_automation.load_declaration("log-rotate")
        commands = [declaration.status.argv] + [action.command.argv for action in declaration.actions]
        self.assertTrue(all(str(ENTRY) in argv for argv in commands))
        self.assertEqual([(a.name, a.confirm) for a in declaration.actions], [("rotate", True)])
        defaults = tool_settings.load_declaration("log-rotate").defaults()
        self.assertEqual(defaults, {"max_mb": 10, "keep": 5, "compress": True, "warn_total_mb": 500})
        with (REPOSITORY / "macbook/launchagents/com.jacobr.log-rotate.plist").open("rb") as source:
            plist = plistlib.load(source)
        self.assertEqual(plist["ProgramArguments"][0], "/usr/bin/python3")
        self.assertEqual(plist["StartInterval"], 3600)
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["EnvironmentVariables"]["PYTHONPATH"], "/Users/jacobr/dev/scripts/shared/python")
        self.assertEqual(plist["StandardOutPath"], "/Users/jacobr/.local/state/log-rotate/launchd.log")
        entry = plist["ProgramArguments"][2].replace("/Users/jacobr/dev/scripts", str(REPOSITORY))
        self.assertTrue(os.path.isfile(entry))


if __name__ == "__main__":
    unittest.main()
