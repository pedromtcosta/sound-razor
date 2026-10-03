import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode, quote

from master_track.ui import Library, make_server, selection_stems
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
        self.token = re.search(rb'name="master-track-token" content="([^"]+)"', body)[1].decode()

    def close(self):
        self.library.cancel()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, data=None, authorized=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        request_headers = dict(headers or {})
        if authorized:
            request_headers["X-Master-Track-Token"] = self.token
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
