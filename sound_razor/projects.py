"""Portable song projects: an original recording, optional metadata, and stems."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import hashlib

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
    separation = {}
    settings = stems_dir / "separation.json"
    if not stems_dir.is_symlink() and settings.is_file() and not settings.is_symlink():
        try:
            if settings.stat().st_size > 1000000:
                raise ValueError("Separation settings are too large.")
            separation = json.loads(settings.read_text())
            if not isinstance(separation, dict):
                raise ValueError("Invalid separation settings.")
            saved = separation.get("guitar_split") or []
            saved = [saved] if isinstance(saved, dict) else saved
            if not isinstance(saved, list) or any(not isinstance(item, dict) or not isinstance(item.get("title"), str) for item in saved):
                raise ValueError("Invalid saved track labels.")
            separation["guitar_split"] = saved
        except (ValueError, OSError):
            separation = {}
            warnings.append("Could not read separation settings.")
    split = separation.get("guitar_split")
    labels = {f"guitar-target-{i}": item["title"] for i, item in enumerate(split or [], 1)}
    if split:
        labels.update({"guitar-target": split[0]["title"], "guitar-remainder": "Guitar remainder"})
    if stems_dir.is_dir() and not stems_dir.is_symlink():
        order = {name: i for i, name in enumerate(("vocals", "guitar", "bass", "drums", "piano", "other", "instrumental", "lead", "rhythm"))}
        order.update({f"guitar-target-{i}": 1 + i / (len(split) + 2) for i in range(1, len(split or []) + 1)})
        order["guitar-remainder"] = 1.99
        for p in sorted(stems_dir.iterdir(), key=lambda p: (order.get(p.stem, 99), p.name)):
            if (p.name == 'guitar.wav' and separation.get('lead_rhythm_split')
                    and all((stems_dir / name).is_file() and not (stems_dir / name).is_symlink()
                            for name in ('lead.wav', 'rhythm.wav'))):
                continue
            if p.is_file() and not p.is_symlink() and p.suffix.lower() == ".wav":
                if p.name == "guitar.wav" and split and (stems_dir / "guitar-target-1.wav").is_file():
                    continue  # Retain the unsplit source on disk without doubling playback.
                stems.append({"name": labels.get(p.stem, p.stem), "file": p.name, "bytes": p.stat().st_size,
                              "version": p.stat().st_mtime_ns})
    cover = cover_path(path, data)
    return {"id": path.name, **fields, "cover": cover.name if cover else None,
            "title": fields["title"] or (Path(original).stem if original else path.name),
            "original": original, "stems": stems, "separation": separation, "warnings": warnings}


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


def import_project(root: Path, source: Path, filename: str, fields: dict, enrichment=None, origin=None) -> dict:
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
        metadata = {"schema_version": 1, **fields, "title": title, "original_file": filename}
        if origin:
            metadata['source'] = origin
        apply_enrichment(work, metadata, enrichment)
        atomic_json(work / "metadata.json", metadata)
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


def cover_path(project: Path, metadata=None):
    data = read_metadata(project) if metadata is None else metadata
    name = data.get('cover_file')
    if not isinstance(name, str) or Path(name).name != name or '\\' in name:
        return None
    path = project / name
    if path.suffix.lower() not in {'.jpg', '.png'} or path.is_symlink() or not path.is_file():
        return None
    return path if path.stat().st_size <= 5_000_000 else None


def apply_enrichment(project, data, enrichment):
    if enrichment is None:
        return
    if not enrichment.get('prepared'):
        raise ValueError('Select a metadata match before saving.')
    data['metadata_source'] = {key: enrichment.get(key) for key in ('release_id', 'group_id', 'recording_id')}
    data['metadata_source']['provider'] = 'MusicBrainz'
    data.pop('cover_file', None)
    cover = enrichment.get('cover')
    if cover:
        extension = '.png' if enrichment['cover_type'] == 'image/png' else '.jpg'
        name = 'cover-' + hashlib.sha256(cover).hexdigest()[:20] + extension
        destination = project/name
        if destination.is_symlink():
            raise ValueError('Cover symlinks are not supported.')
        with tempfile.NamedTemporaryFile(dir=project, prefix='.cover-', delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(cover)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        data['cover_file'] = name


def save_metadata(project: Path, fields: dict, enrichment=None) -> dict:
    data = read_metadata(project)  # Don't silently discard malformed or custom data.
    data.update(metadata_fields(fields))
    apply_enrichment(project, data, enrichment)
    atomic_json(project / "metadata.json", data)
    return describe_project(project)
