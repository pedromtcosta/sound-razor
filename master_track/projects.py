"""Portable song projects: an original recording, optional metadata, and stems."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".opus", ".ogg", ".m4a", ".aac", ".aiff", ".aif"}
METADATA_FIELDS = ("title", "artist", "album", "year")


def metadata_fields(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Metadata must be a JSON object.")
    fields = {}
    for key in METADATA_FIELDS:
        value = data.get(key, "")
        if not isinstance(value, (str, int)) or len(str(value)) > 500:
            raise ValueError(f"Invalid metadata field: {key}")
        fields[key] = str(value).strip()
    return fields


def atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError("Metadata symlinks are not supported.")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".metadata-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_metadata(project: Path) -> dict:
    path = project / "metadata.json"
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ValueError("metadata.json must be a regular JSON file under 1 MB.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("metadata.json must contain a JSON object.")
    return data


def project_path(root: Path, identifier: str) -> Path:
    if not identifier or identifier.startswith(".") or Path(identifier).name != identifier or "\\" in identifier:
        raise ValueError("Invalid project identifier.")
    path = root / identifier
    if path.is_symlink() or not path.is_dir() or path.resolve().parent != root.resolve():
        raise ValueError("Project not found in the selected folder.")
    return path


def describe_project(path: Path) -> dict:
    warnings = []
    try:
        data = read_metadata(path)
        fields = metadata_fields(data)
    except (ValueError, OSError) as error:
        data, fields = {}, dict.fromkeys(METADATA_FIELDS, "")
        warnings.append(f"Metadata: {error}")
    originals = sorted(p.name for p in path.iterdir()
                       if p.is_file() and not p.is_symlink() and p.suffix.lower() in AUDIO_EXTENSIONS)
    requested = data.get("original_file")
    original = requested if isinstance(requested, str) and requested in originals else None
    if original is None and len(originals) == 1:
        original = originals[0]
    elif original is None and len(originals) > 1:
        warnings.append("Multiple original recordings: set original_file in metadata.json to choose one.")
    if not original:
        warnings.append("No unambiguous original recording in this project.")
    stems_dir = path / "stems"
    stems = []
    if stems_dir.is_dir() and not stems_dir.is_symlink():
        order = {name: i for i, name in enumerate(("vocals", "guitar", "bass", "drums", "piano", "other", "instrumental", "lead", "rhythm"))}
        for p in sorted(stems_dir.iterdir(), key=lambda p: (order.get(p.stem, 99), p.name)):
            if p.is_file() and not p.is_symlink() and p.suffix.lower() == ".wav":
                stems.append({"name": p.stem, "file": p.name, "bytes": p.stat().st_size,
                              "version": p.stat().st_mtime_ns})
    return {"id": path.name, **fields,
            "title": fields["title"] or (Path(original).stem if original else path.name),
            "original": original, "stems": stems, "warnings": warnings}


def list_projects(root: Path) -> list[dict]:
    results = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_dir() and not path.is_symlink() and not path.name.startswith("."):
            try:
                results.append(describe_project(path))
            except OSError:
                continue
    return results


def probe_audio(path: Path) -> None:
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                             "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=30, check=True)
    if not json.loads(result.stdout).get("streams"):
        raise ValueError("The imported file does not contain a readable audio stream.")


def import_project(root: Path, source: Path, filename: str, fields: dict) -> dict:
    if Path(filename).name != filename or "\\" in filename or filename.startswith("."):
        raise ValueError("Invalid audio filename.")
    if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("Unsupported audio extension.")
    fields = metadata_fields(fields)
    probe_audio(source)
    title = fields["title"] or Path(filename).stem
    label = " - ".join(part for part in (fields["artist"], title) if part)
    slug = re.sub(r'[^\w .()-]+', "-", label, flags=re.UNICODE).strip(" .-")[:120] or "Untitled"
    with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temporary:
        work = Path(temporary)
        shutil.copy2(source, work / filename)
        atomic_json(work / "metadata.json", {"schema_version": 1, **fields,
                                             "title": title, "original_file": filename})
        # Reserve a destination without overwriting an existing project.
        index = 1
        while True:
            destination = root / (slug if index == 1 else f"{slug} ({index})")
            try:
                destination.mkdir()
                break
            except FileExistsError:
                index += 1
        try:
            for child in work.iterdir():
                shutil.move(str(child), destination / child.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return describe_project(destination)


def save_metadata(project: Path, fields: dict) -> dict:
    data = read_metadata(project)  # Don't silently discard malformed or custom data.
    data.update(metadata_fields(fields))
    atomic_json(project / "metadata.json", data)
    return describe_project(project)
