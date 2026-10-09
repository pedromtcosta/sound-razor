import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode, quote

from sound_razor.ui import Library, make_server, selection_stems
from test_projects import wav_bytes


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.library = Library(self.directory / "projects", self.directory / "ui.json", self.directory / "models")
        self.server = make_server(self.library, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        status, body = self.request("GET", "/", authorized=False)
        self.assertEqual(status, 200)
        self.token = re.search(rb'name="sound-razor-token" content="([^"]+)"', body)[1].decode()

    def close(self):
        self.library.cancel()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, data=None, authorized=True, headers=None, include_root=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        request_headers = dict(headers or {})
        if include_root:
            if method == "POST" and path.startswith("/api/projects/") and isinstance(data, dict):
                data = {"root": str(self.library.root), **data}
            elif (method == "GET" and path.startswith("/api/projects/")) or path.startswith("/api/import?"):
                path += ("&" if "?" in path else "?") + urlencode({"root": str(self.library.root)})
        if authorized:
            request_headers["X-sound-razor-Token"] = self.token
        if isinstance(data, dict):
            data = json.dumps(data)
            request_headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, data, request_headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_authorization_and_host_restrictions(self):
        self.assertEqual(self.request("GET", "/api/state", authorized=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/", authorized=False, headers={"Host": "example.com"})[0], 403)
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["projects"], [])

    def test_pitch_preserving_player_assets_are_served_locally(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        self.addCleanup(connection.close)
        for asset in ("/player.js", "/tempo-worklet.js", "/vendor/soundtouch/processor.js"):
            with self.subTest(asset=asset):
                connection.request("GET", asset)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(response.getheader("Content-Type"), ("text/javascript", "application/javascript"))
                self.assertTrue(response.read())
        self.assertEqual(self.request("GET", "/vendor/soundtouch/LICENSE", authorized=False)[0], 200)
        self.assertEqual(self.request("GET", "/vendor/soundtouch/../../../pyproject.toml", authorized=False)[0], 404)

    def test_import_metadata_and_audio_download(self):
        query = urlencode({"filename": "Song.wav", "artist": "Artist", "title": "A <title>"})
        status, body = self.request("POST", f"/api/import?{query}", wav_bytes())
        self.assertEqual(status, 200, body)
        project = json.loads(body)
        identifier = quote(project["id"], safe="")
        status, body = self.request("POST", f"/api/projects/{identifier}/metadata", {"title": "Renamed", "artist": "Artist"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["title"], "Renamed")
        # Real WAV fixture exercises file serving, not model inference.
        stems = self.library.root / project["id"] / "stems"
        stems.mkdir()
        (stems / "vocals.wav").write_bytes(wav_bytes())
        status, body = self.request("GET", f"/api/projects/{identifier}/audio/vocals.wav")
        self.assertEqual(status, 200)
        self.assertEqual(body, wav_bytes())
        self.assertEqual(self.request("GET", f"/api/projects/{identifier}/audio/metadata.json")[0], 400)

    def test_stale_or_missing_library_cannot_change_or_read_another_project(self):
        old_root = self.library.root
        new_root = self.directory / "other-library"
        for root, title in ((old_root, "First song"), (new_root, "Second song")):
            project = root / "Same name"
            (project / "stems").mkdir(parents=True)
            (project / "original.wav").write_bytes(wav_bytes())
            (project / "metadata.json").write_text(json.dumps({"title": title}))
            for stem in ("guitar", "vocals"):
                (project / "stems" / f"{stem}.wav").write_bytes(wav_bytes())
        self.assertEqual(self.request("POST", "/api/library", {"path": str(new_root)})[0], 200)
        route = "/api/projects/" + quote("Same name", safe="")
        for root in (None, str(old_root)):
            for action in ("metadata", "separate", "split-guitar", "delete"):
                with self.subTest(root=root, action=action):
                    body = {"title": "Wrong edit", "preset": "vocals"}
                    if root is not None:
                        body["root"] = root
                    status, response = self.request("POST", route + "/" + action, body, include_root=False)
                    self.assertEqual(status, 400, response)
                    self.assertIn(b"projects folder changed", response)
                    self.assertIsNone(self.library.job)
            for action in ("cover", "audio/vocals.wav"):
                query = "?" + urlencode({"root": root}) if root is not None else ""
                status, response = self.request("GET", route + "/" + action + query, include_root=False)
                self.assertEqual(status, 400, response)
                self.assertIn(b"projects folder changed", response)
        for root, title in ((old_root, "First song"), (new_root, "Second song")):
            self.assertEqual(json.loads((root / "Same name" / "metadata.json").read_text())["title"], title)
        self.assertEqual(self.request("POST", route + "/metadata", {"title": "Correct edit"})[0], 200)
        self.assertEqual(json.loads((new_root / "Same name" / "metadata.json").read_text())["title"], "Correct edit")

    def test_root_persistence_and_invalid_preset(self):
        destination = self.directory / "new-library"
        status, body = self.request("POST", "/api/library", {"path": str(destination), "create": True})
        self.assertEqual(status, 200, body)
        restored = Library(None, self.directory / "ui.json", self.directory / "models")
        self.assertEqual(restored.root, destination)
        status, body = self.request("POST", "/api/import?filename=Song.wav", wav_bytes())
        project = json.loads(body)
        route = f"/api/projects/{quote(project['id'])}/separate"
        status, body = self.request("POST", route, {"preset": "invalid"})
        self.assertEqual(status, 400)
        self.assertIsNone(self.library.job)

    def test_selected_metadata_and_cover_survive_import_and_edit(self):
        import base64
        cover = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT1sAAAAASUVORK5CYII=')
        token = self.library.lookup.remember({'prepared': True, 'cover': cover, 'cover_type': 'image/png', 'release_id': 'release'})
        query = urlencode({'filename': 'Song.wav', 'title': 'Song', 'album': 'Album', 'lookup_token': token})
        status, body = self.request('POST', '/api/import?' + query, wav_bytes())
        self.assertEqual(status, 200, body)
        project = json.loads(body)
        route = '/api/projects/' + quote(project['id'])
        self.assertTrue(project['cover'])
        self.assertEqual(self.request('GET', route+'/cover', authorized=False)[0], 403)
        self.assertEqual(self.request('GET', route+'/cover'), (200, cover))
        self.assertEqual(self.request('POST', route+'/metadata', {'title': 'Renamed'})[0], 200)
        self.assertEqual(self.request('GET', route+'/cover'), (200, cover))
        self.assertEqual(self.request('POST', '/api/metadata/prepare', {'token': 'missing'})[0], 400)
        self.assertEqual(self.request('POST', '/api/metadata/search', {'title': ''})[0], 400)

    def test_custom_selection_validation(self):
        self.assertEqual(selection_stems("vocals"), ["vocals", "instrumental"])
        self.assertEqual(selection_stems("custom", ["guitar", "bass", "guitar"]), ["guitar", "bass"])
        for stems in (None, [], "vocals", ["instrumental", "guitar"], [{}], ["lead"]):
            with self.assertRaises(ValueError):
                selection_stems("custom", stems)
        folder = self.library.root / "Song"
        folder.mkdir()
        (folder / "original.wav").write_bytes(wav_bytes())
        status, _ = self.request("POST", "/api/projects/Song/separate", {"preset": "custom", "stems": []})
        self.assertEqual(status, 400)
        self.assertIsNone(self.library.job)

    def test_youtube_import_auth_validation_and_cancellation(self):
        import importlib.util
        data = {'url': 'https://youtu.be/BaW_jenozKc', 'root': str(self.library.root)}
        self.assertEqual(self.request('POST', '/api/import-youtube', data, authorized=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/import-youtube', {'url': 'file:///tmp/input'})[0], 400)
        if importlib.util.find_spec('yt_dlp') is None:
            self.skipTest('yt-dlp optional dependency is not installed')
        status, body = self.request('POST', '/api/import-youtube', data)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request('POST', '/api/import-youtube', data)[0], 400)
        self.library.cancel()
        self.library.worker.join(timeout=10)
        self.assertFalse(self.library.worker.is_alive())
        self.assertEqual(self.library.job['status'], 'cancelled')
        self.assertEqual(list(self.library.root.iterdir()), [])

    def test_delete_project_requires_matching_root_and_preserves_neighbors(self):
        folder = self.library.root / "Song"
        folder.mkdir()
        (folder / "original.wav").write_bytes(wav_bytes())
        outside = self.directory / "keep.wav"
        outside.write_bytes(wav_bytes())
        (folder / "linked.wav").symlink_to(outside)
        route = "/api/projects/Song/delete"
        self.assertEqual(self.request("POST", route, {"root": "/wrong"})[0], 400)
        self.assertTrue(folder.exists())
        self.assertEqual(self.request("POST", route, {"root": str(self.library.root)}, authorized=False)[0], 403)
        self.assertEqual(self.request("POST", route, {"root": str(self.library.root)})[0], 200)
        self.assertFalse(folder.exists())
        self.assertEqual(outside.read_bytes(), wav_bytes())
        link = self.library.root / "External"
        link.symlink_to(self.directory, target_is_directory=True)
        self.assertEqual(self.request("POST", "/api/projects/External/delete", {"root": str(self.library.root)})[0], 400)
        self.assertTrue(outside.exists())

    def test_failed_cli_run_preserves_previous_stems(self):
        folder = self.library.root / "Broken audio"
        folder.mkdir()
        (folder / "original.wav").write_text("not audio")
        stems = folder / "stems"
        stems.mkdir()
        previous = wav_bytes()
        (stems / "vocals.wav").write_bytes(previous)
        self.library.start(folder.name, "vocals")
        self.library.worker.join(timeout=30)
        self.assertFalse(self.library.worker.is_alive())
        self.assertEqual(self.library.job["status"], "failed")
        self.assertEqual((stems / "vocals.wav").read_bytes(), previous)
        self.assertTrue(self.library.logs)

    def test_guitar_split_validation_and_failure_preserve_stems(self):
        folder = self.library.root / 'Guitars'
        folder.mkdir()
        stems = folder/'stems'
        stems.mkdir()
        route = '/api/projects/Guitars/split-guitar'
        self.assertEqual(self.request('POST', route, {})[0], 400)
        (stems/'guitar.wav').write_bytes(wav_bytes())  # Unsupported sample rate; real CLI must fail.
        (stems/'bass.wav').write_bytes(wav_bytes())
        before = {p.name: p.read_bytes() for p in stems.iterdir()}
        self.assertEqual(self.request('POST', route, {}, authorized=False)[0], 403)
        self.assertEqual(self.request('POST', route, {})[0], 200)
        self.library.worker.join(timeout=30)
        self.assertFalse(self.library.worker.is_alive())
        self.assertEqual(self.library.job['status'], 'failed')
        self.assertEqual({p.name: p.read_bytes() for p in stems.iterdir()}, before)

    def test_guitar_split_rejects_linked_files(self):
        folder = self.library.root/'Guitars'
        stems = folder/'stems'
        stems.mkdir(parents=True)
        (stems/'guitar.wav').write_bytes(wav_bytes())
        outside = self.directory/'outside.json'
        outside.write_text('{}')
        (stems/'separation.json').symlink_to(outside)
        self.assertEqual(self.request('POST', '/api/projects/Guitars/split-guitar', {})[0], 400)
        self.assertIsNone(self.library.job)

    def test_cancelled_cli_run_preserves_previous_stems(self):
        folder = self.library.root / "Cancel audio"
        folder.mkdir()
        (folder / "original.wav").write_bytes(wav_bytes())
        stems = folder / "stems"
        stems.mkdir()
        (stems / "vocals.wav").write_bytes(wav_bytes())
        self.library.start(folder.name, "vocals")
        with self.assertRaisesRegex(ValueError, "before deleting"):
            self.library.delete(folder.name, str(self.library.root))
        # Wait for a real child to launch, then stop it before model downloads.
        deadline = time.monotonic() + 5
        while self.library.process is None and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertIsNotNone(self.library.process)
        self.library.cancel()
        self.library.worker.join(timeout=10)
        self.assertFalse(self.library.worker.is_alive())
        self.assertEqual(self.library.job["status"], "cancelled")
        self.assertEqual((stems / "vocals.wav").read_bytes(), wav_bytes())


if __name__ == "__main__":
    unittest.main()
