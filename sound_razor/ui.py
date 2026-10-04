"""Local browser UI. Files stay local; separation runs through the CLI subprocess."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid
import importlib.util

from .projects import (atomic_json, describe_project, import_project, list_projects,
                       project_path, save_metadata, cover_path)
from .metadata_lookup import MetadataLookup
from .sources import youtube_url, youtube_command
from .projects import metadata_fields

CUSTOM_STEMS = ("vocals", "guitar", "bass", "drums", "piano", "other")


def selection_stems(preset, stems=None):
    if preset == "vocals":
        return ["vocals", "instrumental"]
    if preset != "custom":
        raise ValueError("Choose vocals/instrumental or custom separation.")
    if not isinstance(stems, list) or not stems:
        raise ValueError("Select at least one instrument.")
    if any(not isinstance(stem, str) or stem not in CUSTOM_STEMS for stem in stems):
        raise ValueError("Unknown custom stem.")
    return list(dict.fromkeys(stems))


STATIC = Path(__file__).with_name("web")


class Library:
    def __init__(self, root: Path | None, config: Path, cache: Path):
        self.lock = threading.RLock()
        self.config = config.resolve()
        self.cache = cache.resolve()
        self.root = None
        self.job = None
        self.process = None
        self.worker = None
        self.logs = deque(maxlen=160)
        self.lookup = MetadataLookup()
        if root is not None:
            self.set_root(str(root), create=True)
        elif self.config.exists():
            try:
                saved = Path(json.loads(self.config.read_text())["root"])
                if saved.is_dir():
                    self.root = saved.resolve()
            except (ValueError, KeyError, OSError):
                pass

    def running(self):
        return self.job is not None and self.job["status"] in {"running", "cancelling"}

    def set_root(self, value: str, create=False):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Choose a projects folder.")
        with self.lock:
            if self.running() or self.worker is not None and self.worker.is_alive():
                raise ValueError("Wait for the current job to finish or cancel it before changing folders.")
            root = Path(value).expanduser().resolve()
            if create:
                root.mkdir(parents=True, exist_ok=True)
            if not root.is_dir():
                raise ValueError("Folder does not exist. Enable ‘Create folder’ to create it.")
            # Check readable listing now, before persisting the selection.
            list(root.iterdir())
            self.config.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.config, {"root": str(root)})
            self.root = root
            self.job = None

    def require_root(self):
        if self.root is None:
            raise ValueError("Choose a projects folder first.")
        return self.root

    def state(self):
        with self.lock:
            job = dict(self.job) if self.job else None
            if job:
                job["logs"] = list(self.logs)
            return {"root": str(self.root) if self.root else None,
                    "projects": list_projects(self.root) if self.root else [], "job": job}

    def start(self, identifier: str, preset: str, stems=None, *, guitar_split=False):
        with self.lock:
            if self.running() or self.worker is not None and self.worker.is_alive():
                raise ValueError("Another download or separation is already running.")
            chosen = ['lead', 'rhythm'] if guitar_split else selection_stems(preset, stems)
            project = project_path(self.require_root(), identifier)
            info = describe_project(project)
            if not guitar_split and not info["original"]:
                raise ValueError("This project needs an unambiguous original audio file.")
            if (project / "stems").is_symlink():
                raise ValueError("The stems folder must not be a symlink.")
            if guitar_split:
                if 'guitar.wav' not in {item['file'] for item in info['stems']}:
                    raise ValueError('Separate the guitar track first.')
                if any(p.is_symlink() or not p.is_file() for p in (project/'stems').iterdir()):
                    raise ValueError('The stems folder must contain regular files only.')
                if any((project/'stems'/name).exists() for name in ('lead.wav', 'rhythm.wav')):
                    raise ValueError('This project already contains lead or rhythm tracks.')
            self.logs.clear()
            self.job = {"id": uuid.uuid4().hex, "project": identifier, "status": "running",
                        "started": time.time(), "finished": None, "error": None,
                        "preset": preset, "stems": chosen, "guitar_split": guitar_split}
            self.worker = threading.Thread(target=self._separate, args=(project, info["original"], chosen, guitar_split), daemon=True)
            self.worker.start()
            return dict(self.job)

    def import_youtube(self, data):
        url = youtube_url(data.get('url'))
        fields = metadata_fields(data)
        with self.lock:
            root = self.require_root()
            if data.get('root') != str(root):
                raise ValueError('The projects folder changed. Reopen YouTube import.')
            if self.running() or self.worker is not None and self.worker.is_alive():
                raise ValueError('Another download or separation is already running.')
            if importlib.util.find_spec('yt_dlp') is None:
                raise ValueError('Install YouTube support: uv pip install --python .venv/bin/python -e ".[youtube]"')
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                raise ValueError('Install FFmpeg and ffprobe on PATH.')
            self.logs.clear()
            self.job = {'id': uuid.uuid4().hex, 'kind': 'youtube', 'project': None,
                        'status': 'running', 'started': time.time(), 'finished': None, 'error': None}
            self.worker = threading.Thread(target=self._youtube, args=(root, url, fields), daemon=True)
            self.worker.start()
            return dict(self.job)

    def _youtube(self, root, url, fields):
        try:
            with tempfile.TemporaryDirectory(prefix='.youtube-', dir=root) as temporary:
                work = Path(temporary)
                with self.lock:
                    if self.job['status'] == 'cancelling':
                        return
                    self.process = subprocess.Popen(youtube_command(url, work), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, errors='replace',
                        env=dict(os.environ, PYTHONUNBUFFERED='1'), start_new_session=True)
                    process = self.process
                for line in process.stdout:
                    with self.lock:
                        self.logs.append(line.rstrip()[-2000:])
                code = process.wait()
                process.stdout.close()
                with self.lock:
                    if self.job['status'] == 'cancelling':
                        return
                    if code:
                        raise ValueError(f'YouTube download failed (exit {code}). See the download log.')
                    info = json.loads((work/'source.info.json').read_text())
                    defaults = {'title': info.get('track') or info.get('title') or 'YouTube recording',
                                'artist': info.get('artist') or info.get('creator') or '',
                                'album': info.get('album') or '',
                                'year': str(info.get('release_year') or info.get('release_date') or '')[:4]}
                    chosen = {key: fields[key] or str(defaults[key])[:500] for key in defaults}
                    result = import_project(root, work/'source.wav', 'original.wav', chosen,
                                            origin={'provider': 'YouTube', 'url': url})
                    self.job.update(status='succeeded', project=result['id'])
        except Exception as error:
            with self.lock:
                if self.job['status'] != 'cancelling':
                    self.job.update(status='failed', error=str(error))
        finally:
            with self.lock:
                if self.job['status'] == 'cancelling':
                    self.job['status'] = 'cancelled'
                self.job['finished'] = time.time()
                self.process = None

    def _separate(self, project: Path, original: str, stems: list[str], guitar_split=False):
        try:
            with tempfile.TemporaryDirectory(prefix=".separation-", dir=project) as temporary:
                stage = Path(temporary) / "stems"
                if guitar_split:
                    shutil.copytree(project/'stems', stage)
                    command = [sys.executable, '-m', 'sound_razor', 'split-guitar',
                               '--file', str(stage/'guitar.wav'), '--output', str(stage), '--cache', str(self.cache)]
                else:
                    command = [sys.executable, "-m", "sound_razor", "separate", "--file", str(project / original),
                               "--output", str(stage), "--cache", str(self.cache), "--stems", *stems]
                env = dict(os.environ, PYTHONUNBUFFERED="1", ORT_DISABLE_TELEMETRY="1")
                with self.lock:
                    if self.job["status"] == "cancelling":
                        return
                    self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                    text=True, errors="replace", env=env, start_new_session=True)
                    process = self.process
                for line in process.stdout:
                    with self.lock:
                        self.logs.append(line.rstrip()[-2000:])
                code = process.wait()
                process.stdout.close()
                with self.lock:
                    if self.job["status"] == "cancelling":
                        return
                if code != 0:
                    raise RuntimeError(f"Separation CLI exited with code {code}. See the process log.")
                for stem in stems:
                    path = stage / f"{stem}.wav"
                    if not path.is_file() or path.stat().st_size <= 44:
                        raise RuntimeError(f"CLI did not produce {stem}.wav.")
                # Publish only a successful run; preserve the old stems on failure.
                with self.lock:
                    if self.job["status"] == "cancelling":
                        return
                    destination = project / "stems"
                    backup = Path(temporary) / "previous-stems"
                    if destination.exists():
                        destination.rename(backup)
                    try:
                        stage.rename(destination)
                    except BaseException:
                        if backup.exists():
                            backup.rename(destination)
                        raise
                    self.job["status"] = "succeeded"
        except Exception as error:
            with self.lock:
                if self.job["status"] != "cancelling":
                    self.job.update(status="failed", error=str(error))
        finally:
            with self.lock:
                if self.job["status"] == "cancelling":
                    self.job["status"] = "cancelled"
                self.job["finished"] = time.time()
                self.process = None

    def delete(self, identifier: str, expected_root: str):
        with self.lock:
            root = self.require_root()
            if expected_root != str(root):
                raise ValueError("The projects folder changed. Reopen the deletion dialog.")
            project = project_path(root, identifier)
            if self.job and self.job["project"] == identifier:
                if self.running() or self.worker is not None and self.worker.is_alive():
                    raise ValueError("Wait for separation to finish or cancel it before deleting this song.")
            shutil.rmtree(project)
            if self.job and self.job["project"] == identifier:
                self.job = None
                self.logs.clear()
            return {"ok": True}

    def cancel(self):
        with self.lock:
            if not self.running():
                return
            self.job["status"] = "cancelling"
            process = self.process
            if process and process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
        if process:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass


def make_server(library: Library, port: int = 8765):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def respond(self, status, body, content_type="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json_body(self):
            size = self.body_size(65536)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            return data

        def body_size(self, maximum):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked requests are not supported.")
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > maximum:
                raise ValueError(f"Request size must be between 1 and {maximum} bytes.")
            return size

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def dispatch(self, method):
            try:
                self.connection.settimeout(120)
                host = self.headers.get("Host", "")
                allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                if host not in allowed or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
                    self.respond(403, {"error": "Local same-origin requests only."})
                    return
                url = urlsplit(self.path)
                path = unquote(url.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                if path.startswith("/api/"):
                    if not secrets.compare_digest(self.headers.get("X-sound-razor-Token", ""), token):
                        self.respond(403, {"error": "Reload the UI to authorize this session."})
                        return
                    result = self.api(method, path, query)
                    if result is not None:
                        self.respond(200, result)
                elif method == "GET" and path in {"/", "/app.js", "/player.js", "/styles.css"}:
                    name = "index.html" if path == "/" else path[1:]
                    body = (STATIC / name).read_bytes().replace(b"__SESSION_TOKEN__", token.encode())
                    self.respond(200, body, mimetypes.guess_type(name)[0] or "text/plain")
                else:
                    self.respond(404, {"error": "Not found."})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                self.respond(400, {"error": str(error)})
            except Exception as error:
                print(f"UI request failed: {error}", file=sys.stderr)
                self.respond(500, {"error": "Unexpected server error; see the terminal."})

        def api(self, method, path, query):
            if method == 'POST' and path == '/api/import-youtube':
                return library.import_youtube(self.json_body())
            if method == 'POST' and path == '/api/metadata/search':
                return {'matches': library.lookup.search(self.json_body())}
            if method == 'POST' and path == '/api/metadata/prepare':
                return library.lookup.prepare(self.json_body().get('token'))
            if method == 'GET' and path.startswith('/api/metadata/cover/'):
                item = library.lookup.get(path.rsplit('/', 1)[-1])
                if not item.get('cover'):
                    raise ValueError('Cover not found.')
                self.respond(200, item['cover'], item['cover_type'])
                return None
            if method == "GET" and path == "/api/state":
                return library.state()
            if method == "GET" and path == "/api/folders":
                folder = Path(query.get("path") or str(library.root or Path.home())).expanduser().resolve()
                if not folder.is_dir():
                    raise ValueError("Folder not found.")
                children = [{"name": p.name, "path": str(p.resolve())} for p in folder.iterdir()
                            if p.is_dir() and not p.name.startswith(".")]
                return {"path": str(folder), "parent": str(folder.parent),
                        "folders": sorted(children, key=lambda p: p["name"].casefold())}
            if method == "POST" and path == "/api/library":
                data = self.json_body()
                library.set_root(data.get("path"), create=data.get("create") is True)
                return library.state()
            if method == "POST" and path == "/api/import":
                size = self.body_size(2 * 1024**3)
                with tempfile.TemporaryDirectory(prefix="sound-razor-upload-") as temporary:
                    upload = Path(temporary) / "upload"
                    with upload.open("wb") as out:
                        remaining = size
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("Audio upload was interrupted.")
                            out.write(chunk)
                            remaining -= len(chunk)
                    with library.lock:
                        if query.get("root") and query["root"] != str(library.require_root()):
                            raise ValueError("The projects folder changed during upload. Please import again.")
                        enrichment = library.lookup.get(query['lookup_token']) if query.get('lookup_token') else None
                        return import_project(library.require_root(), upload, query.get("filename", ""), query, enrichment)
            if method == "POST" and path == "/api/cancel":
                library.cancel()
                return {"ok": True}
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                with library.lock:
                    project = project_path(library.require_root(), parts[2])
                    if method == "POST" and len(parts) == 4 and parts[3] == "metadata":
                        data = self.json_body()
                        enrichment = library.lookup.get(data['lookup_token']) if data.get('lookup_token') else None
                        return save_metadata(project, data, enrichment)
                    if method == 'GET' and len(parts) == 4 and parts[3] == 'cover':
                        cover = cover_path(project)
                        if not cover:
                            raise ValueError('Cover not found.')
                        self.respond(200, cover.read_bytes(), 'image/png' if cover.suffix == '.png' else 'image/jpeg')
                        return None
                    if method == "POST" and len(parts) == 4 and parts[3] == "separate":
                        data = self.json_body()
                        return library.start(parts[2], data.get("preset", "vocals"), data.get("stems"))
                    if method == "POST" and len(parts) == 4 and parts[3] == "split-guitar":
                        self.json_body()
                        return library.start(parts[2], 'guitar', guitar_split=True)
                    if method == "POST" and len(parts) == 4 and parts[3] == "delete":
                        return library.delete(parts[2], self.json_body().get("root"))
                    if method == "GET" and len(parts) == 5 and parts[3] == "audio":
                        name = parts[4]
                        allowed = {s["file"] for s in describe_project(project)["stems"]}
                        if name not in allowed:
                            raise ValueError("Stem not found.")
                        audio = project / "stems" / name
                        # Open while holding the lock, before a completed job swaps directories.
                        handle = audio.open("rb")
                if method == "GET" and len(parts) == 5 and parts[3] == "audio":
                    with handle:
                        self.send_response(200)
                        self.send_header("Content-Type", "audio/wav")
                        self.send_header("Content-Length", str(os.fstat(handle.fileno()).st_size))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        shutil.copyfileobj(handle, self.wfile)
                    return None
            raise ValueError("Unknown API route.")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(projects: Path | None, port: int, cache: Path):
    library = Library(projects, Path(".sound-razor/ui.json"), cache)
    server = make_server(library, port)
    print(f"Sound Razor UI: http://127.0.0.1:{server.server_port}", flush=True)
    print("Open that address in your browser. Stop the server with Ctrl+C.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        library.cancel()
        if library.worker:
            library.worker.join(timeout=8)
        server.server_close()
