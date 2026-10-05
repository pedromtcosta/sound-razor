import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from sound_razor.model_cache import cached_download


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "model.bin"
        self.payload = b"model fixture" * 4096
        self.declared_size = None
        self.requests = 0
        self.status = 200
        self.pause_first = False
        self.started = threading.Event()
        self.release = threading.Event()
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                test.requests += 1
                payload = test.payload
                self.send_response(test.status)
                self.send_header("Content-Length", str(test.declared_size or len(payload)))
                self.end_headers()
                try:
                    if test.pause_first and test.requests == 1:
                        self.wfile.write(payload[:1024])
                        test.started.set()
                        test.release.wait(timeout=10)
                        self.wfile.write(payload[1024:])
                    else:
                        self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}/model"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_truncated_response_never_becomes_a_cache_hit(self):
        self.declared_size = len(self.payload) + 100
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            cached_download(self.url, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob(".model-download-*")))
        self.declared_size = None
        cached_download(self.url, self.destination)
        cached_download(self.url, self.destination)
        self.assertEqual(self.requests, 2)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_killed_download_does_not_block_retry(self):
        self.pause_first = True
        script = "from pathlib import Path; import sys; from sound_razor.model_cache import cached_download; cached_download(sys.argv[1], Path(sys.argv[2]))"
        process = subprocess.Popen([sys.executable, "-c", script, self.url, str(self.destination)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(self.started.wait(timeout=5))
            process.terminate()
            process.wait(timeout=5)
            self.assertFalse(self.destination.exists())
            cached_download(self.url, self.destination)
            self.assertEqual(self.destination.read_bytes(), self.payload)
            self.assertEqual(self.requests, 2)
        finally:
            self.release.set()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_legacy_or_corrupt_binary_is_downloaded_again(self):
        self.destination.write_bytes(b"old interrupted download")
        cached_download(self.url, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.destination.write_bytes(b"later corruption")
        cached_download(self.url, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertEqual(self.requests, 2)

    def test_pinned_hash_rejects_bad_download_then_repairs_cache(self):
        good = self.payload
        expected = hashlib.sha256(good).hexdigest()
        self.destination.write_bytes(b"bad cached model")
        # A receipt alongside a tampered checkpoint must never override its pin.
        (self.root / ".model.bin.sha256").write_text(hashlib.sha256(b"bad cached model").hexdigest())
        self.payload = b"wrong remote model"
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            cached_download(self.url, self.destination, expected)
        self.assertEqual(self.destination.read_bytes(), b"bad cached model")
        self.payload = good
        cached_download(self.url, self.destination, expected)
        cached_download(self.url, self.destination, expected)
        self.assertEqual(self.destination.read_bytes(), good)
        self.assertEqual(self.requests, 2)

    def test_http_errors_allow_the_backend_to_try_its_fallback(self):
        self.status = 404
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            cached_download(self.url, self.destination)
        self.assertFalse(self.destination.exists())
        self.status = 200
        cached_download(self.url, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_corrupt_receipt_does_not_prevent_recovery(self):
        self.destination.write_bytes(b"old interrupted download")
        (self.root / ".model.bin.sha256").write_bytes(b"\xff\xfe")
        cached_download(self.url, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_invalid_metadata_is_repaired_and_cached_json_works_offline(self):
        destination = self.root / "metadata.json"
        destination.write_bytes(b'{"partial":')
        self.payload = b"not JSON"
        with self.assertRaisesRegex(RuntimeError, "Invalid model metadata"):
            cached_download(self.url, destination)
        self.payload = b'{"models": []}'
        cached_download(self.url, destination)
        cached_download("https://invalid.invalid/unreachable", destination)
        self.assertEqual(destination.read_bytes(), self.payload)
        self.assertEqual(self.requests, 2)

    def test_symlinked_cache_entries_cannot_write_outside_cache(self):
        outside = self.root / "keep.bin"
        outside.write_bytes(b"keep")
        try:
            self.destination.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Filesystem does not support symlinks: {error}")
        with self.assertRaisesRegex(ValueError, "regular files"):
            cached_download(self.url, self.destination)
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertEqual(self.requests, 0)
