import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pi.apps.video_library import video_qbittorrent as qbt


HASH_A = "1" * 40
HASH_B = "2" * 40
HASH_V2 = "a" * 64


def torrent(
    torrent_id,
    *,
    name="Example Show Season 1",
    save_path,
    download_path,
    content_path,
    progress=0.5,
    state="downloading",
    infohash_v1=None,
    infohash_v2=HASH_V2,
):
    return {
        "hash": torrent_id,
        "infohash_v1": torrent_id if infohash_v1 is None else infohash_v1,
        "infohash_v2": infohash_v2,
        "name": name,
        "state": state,
        "progress": progress,
        "total_size": 2_000,
        "save_path": str(save_path),
        "download_path": str(download_path) if download_path else "",
        "content_path": str(content_path) if content_path else "",
    }


def torrent_file(
    index,
    name,
    *,
    size=1_000,
    progress=0.5,
    piece_range=(10, 20),
):
    return {
        "index": index,
        "name": name,
        "size": size,
        "progress": progress,
        "priority": 1,
        "availability": 0.75,
        "piece_range": list(piece_range),
    }


class FakeTransport:
    """Stateful qB API stand-in; no real network requests escape."""

    def __init__(self, torrents=(), files=None, *, login_required=False):
        self.torrents = list(torrents)
        self.files = dict(files or {})
        self.login_required = login_required
        self.logged_in = not login_required
        self.login_username = None
        self.login_password = None
        self.requests = []
        self.raise_error = None
        self.files_status = {}

    def request(self, method, url, *, headers, body, timeout):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        if self.raise_error is not None:
            raise self.raise_error
        parsed = urlsplit(url)
        if parsed.path == "/api/v2/auth/login":
            values = parse_qs((body or b"").decode("utf-8"), keep_blank_values=True)
            self.login_username = values.get("username", [None])[0]
            self.login_password = values.get("password", [None])[0]
            if self.login_username == "van user" and self.login_password == "p&ss=word":
                self.logged_in = True
                return qbt.HttpResponse(200, b"Ok.")
            return qbt.HttpResponse(200, b"Fails.")
        if self.login_required and not self.logged_in:
            return qbt.HttpResponse(403, b"Forbidden")
        if parsed.path == "/api/v2/app/version":
            return qbt.HttpResponse(200, b"v4.5.2")
        if parsed.path == "/api/v2/app/webapiVersion":
            return qbt.HttpResponse(200, b"2.8.3")
        if parsed.path == "/api/v2/torrents/info":
            values = parse_qs(parsed.query)
            result = self.torrents
            if "hashes" in values:
                wanted = set(values["hashes"][0].split("|"))
                result = [item for item in result if item["hash"].casefold() in wanted]
            return qbt.HttpResponse(200, json.dumps(result).encode("utf-8"))
        if parsed.path == "/api/v2/torrents/files":
            values = parse_qs(parsed.query)
            torrent_id = values.get("hash", [""])[0]
            status = self.files_status.get(torrent_id, 200)
            if status != 200:
                return qbt.HttpResponse(status, b"")
            if torrent_id not in self.files:
                return qbt.HttpResponse(404, b"")
            return qbt.HttpResponse(
                200,
                json.dumps(self.files[torrent_id]).encode("utf-8"),
            )
        return qbt.HttpResponse(404, b"")


class QbittorrentFixture:
    def __init__(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.incomplete = self.root / "torrent" / "incomplete"
        self.final = self.root / "torrent" / "New"
        self.incomplete.mkdir(parents=True)
        self.final.mkdir(parents=True)

    def cleanup(self):
        self.tempdir.cleanup()

    def client(self, transport, **kwargs):
        return qbt.QbittorrentClient(
            temp_roots=(self.incomplete,),
            final_roots=(self.final,),
            client_id="vanpi-qbt",
            transport=transport,
            **kwargs,
        )


class QbittorrentResolutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = QbittorrentFixture()
        self.addCleanup(self.fixture.cleanup)

    def multi_file_transport(self):
        pack = "Example Show Season 1"
        summary = torrent(
            HASH_A,
            save_path=self.fixture.final,
            download_path=self.fixture.incomplete,
            content_path=self.fixture.incomplete / pack,
        )
        files = [
            torrent_file(0, f"{pack}/Season 01/Episode One.mkv", piece_range=(0, 99)),
            torrent_file(1, f"{pack}/Season 01/Episode Two.mkv", piece_range=(100, 199)),
        ]
        return FakeTransport((summary,), {HASH_A: files})

    def test_resolves_multifile_temp_path_with_spaces_and_retains_identity_data(self):
        transport = self.multi_file_transport()
        client = self.fixture.client(transport)
        target = (
            self.fixture.incomplete
            / "Example Show Season 1"
            / "Season 01"
            / "Episode One.mkv"
        )

        record = client.resolve_path(target)

        self.assertIsNotNone(record)
        self.assertEqual(record.identity, qbt.TorrentFileIdentity("vanpi-qbt", HASH_A, 0))
        self.assertEqual(record.infohash_v1, HASH_A)
        self.assertEqual(record.infohash_v2, HASH_V2)
        self.assertEqual(record.relative_path, "Example Show Season 1/Season 01/Episode One.mkv")
        self.assertEqual(record.expected_size, 1_000)
        self.assertEqual(record.progress, 0.5)
        self.assertEqual(record.piece_range, (0, 99))
        self.assertEqual(record.location_kind, "temporary")
        self.assertEqual(record.matched_path, str(target.resolve()))
        self.assertIn(
            str(
                (
                    self.fixture.final
                    / "Example Show Season 1"
                    / "Season 01"
                    / "Episode One.mkv"
                ).resolve()
            ),
            record.final_paths,
        )

    def test_temp_to_final_reconciliation_preserves_identity(self):
        transport = self.multi_file_transport()
        client = self.fixture.client(transport)
        temporary_path = (
            self.fixture.incomplete
            / "Example Show Season 1"
            / "Season 01"
            / "Episode Two.mkv"
        )
        before = client.resolve_path(temporary_path)

        transport.torrents[0].update(
            state="uploading",
            progress=1.0,
            download_path="",
            content_path=str(self.fixture.final / "Example Show Season 1"),
        )
        for item in transport.files[HASH_A]:
            item["progress"] = 1.0

        records = client.reconcile_completed_torrent(HASH_A.upper())
        after = next(item for item in records if item.identity.file_index == 1)

        self.assertEqual(before.identity, after.identity)
        self.assertTrue(after.complete)
        self.assertIn(str(temporary_path.resolve()), after.temporary_paths)
        final_path = (
            self.fixture.final
            / "Example Show Season 1"
            / "Season 01"
            / "Episode Two.mkv"
        )
        self.assertIn(str(final_path.resolve()), after.final_paths)
        self.assertEqual(client.resolve_path(final_path).identity, before.identity)

    def test_unknown_media_under_a_root_returns_none(self):
        transport = self.multi_file_transport()
        client = self.fixture.client(transport)
        target = self.fixture.incomplete / "random camera clip.mp4"

        self.assertIsNone(client.resolve_path(target))
        file_requests = [
            request
            for request in transport.requests
            if urlsplit(request["url"]).path == "/api/v2/torrents/files"
        ]
        self.assertEqual(file_requests, [])

    def test_unrelated_torrent_summary_does_not_trigger_files_request(self):
        transport = self.multi_file_transport()
        unrelated = torrent(
            HASH_B,
            name="Unrelated Movie.mkv",
            save_path=self.fixture.final,
            download_path=self.fixture.incomplete,
            content_path=self.fixture.incomplete / "Unrelated Movie.mkv",
            infohash_v2="",
        )
        transport.torrents.append(unrelated)
        transport.files[HASH_B] = [torrent_file(0, "Unrelated Movie.mkv")]
        client = self.fixture.client(transport)
        target = (
            self.fixture.incomplete
            / "Example Show Season 1"
            / "Season 01"
            / "Episode One.mkv"
        )

        self.assertEqual(client.resolve_path(target).identity.torrent_id, HASH_A)

        requested_hashes = [
            parse_qs(urlsplit(request["url"]).query)["hash"][0]
            for request in transport.requests
            if urlsplit(request["url"]).path == "/api/v2/torrents/files"
        ]
        self.assertEqual(requested_hashes, [HASH_A])

    def test_same_path_claimed_by_two_torrents_is_explicitly_ambiguous(self):
        shared_name = "Same File.mkv"
        first = torrent(
            HASH_A,
            name=shared_name,
            save_path=self.fixture.final,
            download_path=self.fixture.incomplete,
            content_path=self.fixture.incomplete / shared_name,
            infohash_v2="",
        )
        second = torrent(
            HASH_B,
            name=shared_name,
            save_path=self.fixture.final,
            download_path=self.fixture.incomplete,
            content_path=self.fixture.incomplete / shared_name,
            infohash_v2="",
        )
        files = [torrent_file(0, shared_name)]
        transport = FakeTransport((first, second), {HASH_A: files, HASH_B: files})
        client = self.fixture.client(transport)

        with self.assertRaisesRegex(qbt.QbittorrentAmbiguousPath, "2 qBittorrent files"):
            client.resolve_path(self.fixture.incomplete / shared_name)

    def test_unavailable_client_has_deterministic_error_and_timeout(self):
        transport = FakeTransport()
        transport.raise_error = OSError("a host-specific secret detail")
        client = self.fixture.client(transport, timeout=1.25)

        with self.assertRaises(qbt.QbittorrentUnavailable) as caught:
            client.resolve_path(self.fixture.incomplete / "anything.mkv")

        self.assertEqual(str(caught.exception), "qBittorrent Web API is unavailable")
        self.assertEqual(transport.requests[0]["timeout"], 1.25)
        self.assertNotIn("secret", str(caught.exception))

    def test_path_traversal_and_paths_outside_roots_never_reach_api(self):
        transport = self.multi_file_transport()
        client = self.fixture.client(transport)
        traversal = os.path.join(str(self.fixture.incomplete), "..", "New", "movie.mkv")

        with self.assertRaises(qbt.QbittorrentUnsafePath):
            client.resolve_path(traversal)
        with self.assertRaises(qbt.QbittorrentUnsafePath):
            client.resolve_path(self.fixture.root / "outside.mkv")

        self.assertEqual(transport.requests, [])

    def test_symlink_escape_is_rejected(self):
        outside = self.fixture.root / "outside"
        outside.mkdir()
        escape = self.fixture.incomplete / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        client = self.fixture.client(self.multi_file_transport())

        with self.assertRaises(qbt.QbittorrentUnsafePath):
            client.resolve_path(escape / "movie.mkv")

    def test_unsafe_relative_path_from_api_is_rejected(self):
        transport = self.multi_file_transport()
        transport.files[HASH_A][0]["name"] = "../outside.mkv"
        client = self.fixture.client(transport)

        with self.assertRaises(qbt.QbittorrentProtocolError):
            client.torrent_files(HASH_A)

    def test_metadata_conflict_has_a_specific_error(self):
        transport = self.multi_file_transport()
        transport.files_status[HASH_A] = 409
        client = self.fixture.client(transport)

        with self.assertRaises(qbt.QbittorrentMetadataUnavailable):
            client.reconcile_completed_torrent(HASH_A)

    def test_empty_file_list_while_magnet_fetches_metadata_is_specific(self):
        transport = self.multi_file_transport()
        transport.files[HASH_A] = []
        client = self.fixture.client(transport)

        with self.assertRaises(qbt.QbittorrentMetadataUnavailable):
            client.reconcile_completed_torrent(HASH_A)

    def test_zero_byte_torrent_member_piece_range_is_retained(self):
        transport = self.multi_file_transport()
        transport.files[HASH_A].append(
            torrent_file(
                2,
                "Example Show Season 1/empty.txt",
                size=0,
                piece_range=(200, 0),
            )
        )
        client = self.fixture.client(transport)

        records = client.reconcile_completed_torrent(HASH_A)

        self.assertEqual(records[2].piece_range, (200, 0))


class QbittorrentAuthenticationAndHookTests(unittest.TestCase):
    def setUp(self):
        self.fixture = QbittorrentFixture()
        self.addCleanup(self.fixture.cleanup)

    def test_cookie_login_posts_encoded_secret_without_putting_it_in_url_or_repr(self):
        transport = FakeTransport(login_required=True)
        credentials = qbt.QbittorrentCredentials("van user", "p&ss=word")
        client = self.fixture.client(transport, credentials=credentials)

        self.assertEqual(client.version(), "v4.5.2")

        self.assertEqual(transport.login_username, "van user")
        self.assertEqual(transport.login_password, "p&ss=word")
        self.assertNotIn("p&ss=word", repr(credentials))
        self.assertNotIn("p&ss=word", transport.requests[0]["url"])
        self.assertEqual(transport.requests[0]["headers"]["Origin"], "http://127.0.0.1:8080")
        self.assertEqual(transport.requests[0]["headers"]["Referer"], "http://127.0.0.1:8080/")

    def test_local_auth_bypass_requires_no_login_request(self):
        transport = FakeTransport()
        client = self.fixture.client(transport)

        self.assertEqual(client.webapi_version(), "2.8.3")
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(urlsplit(transport.requests[0]["url"]).path, "/api/v2/app/webapiVersion")

    def test_stable_hook_identifier_round_trips_and_rejects_untrusted_text(self):
        identity = qbt.TorrentFileIdentity("vanpi-qbt", HASH_A.upper(), 17)

        self.assertEqual(identity.torrent_id, HASH_A)
        self.assertEqual(
            qbt.TorrentFileIdentity.from_hook_id(
                identity.hook_id,
                expected_client_id="vanpi-qbt",
            ),
            identity,
        )
        bad_values = (
            HASH_A,
            f"qbt1:vanpi-qbt:{HASH_A}:../17",
            f"qbt1:vanpi-qbt:{HASH_A}:01",
            f"qbt1:vanpi-qbt;touch-pwned:{HASH_A}:1",
            f"qbt1:other:{HASH_A}:1",
        )
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(qbt.InvalidHookIdentifier):
                    qbt.TorrentFileIdentity.from_hook_id(
                        value,
                        expected_client_id="vanpi-qbt",
                    )

    def test_raw_completion_torrent_identifier_is_strict(self):
        transport = FakeTransport()
        client = self.fixture.client(transport)

        for value in ("", "1" * 39, "g" * 40, HASH_A + ";shutdown", "../" + HASH_A):
            with self.subTest(value=value):
                with self.assertRaises(qbt.InvalidHookIdentifier):
                    client.reconcile_completed_torrent(value)
        self.assertEqual(transport.requests, [])

    def test_non_loopback_and_url_embedded_credentials_are_rejected(self):
        common = {
            "temp_roots": (self.fixture.incomplete,),
            "final_roots": (self.fixture.final,),
        }
        with self.assertRaises(qbt.QbittorrentConfigurationError):
            qbt.QbittorrentClient(base_url="http://192.168.6.103:8080", **common)
        with self.assertRaises(qbt.QbittorrentConfigurationError):
            qbt.QbittorrentClient(base_url="http://admin:secret@127.0.0.1:8080", **common)


class _LoginServerHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        values = parse_qs(body.decode("utf-8"))
        expected_origin = f"http://127.0.0.1:{self.server.server_port}"
        valid = (
            self.path == "/api/v2/auth/login"
            and values == {"username": ["van user"], "password": ["p&ss=word"]}
            and self.headers.get("Origin") == expected_origin
            and self.headers.get("Referer") == expected_origin + "/"
        )
        self.send_response(200)
        if valid:
            self.send_header("Set-Cookie", "SID=test-session; path=/")
        payload = b"Ok." if valid else b"Fails."
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        authorized = "SID=test-session" in self.headers.get("Cookie", "")
        if self.path == "/api/v2/app/version" and authorized:
            payload = b"v4.5.2"
            self.send_response(200)
        else:
            payload = b"Forbidden"
            self.send_response(403)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class UrllibTransportIntegrationTests(unittest.TestCase):
    def test_real_transport_retains_login_cookie_against_fake_local_server(self):
        fixture = QbittorrentFixture()
        self.addCleanup(fixture.cleanup)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _LoginServerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = qbt.QbittorrentClient(
            base_url=f"http://127.0.0.1:{server.server_port}",
            temp_roots=(fixture.incomplete,),
            final_roots=(fixture.final,),
            credentials=qbt.QbittorrentCredentials("van user", "p&ss=word"),
            timeout=2,
        )

        self.assertEqual(client.version(), "v4.5.2")


if __name__ == "__main__":
    unittest.main()
