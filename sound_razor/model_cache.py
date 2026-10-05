"""Validated, atomic model downloads and a small audio-separator adapter."""

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import urlopen


# Verified against the upstream HTTPS artifacts on 2026-10-04. Pin the YAML as
# well as the weights: Demucs uses it to decide which pickle checkpoint to load.
_DEMUCS = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/"
_UVR = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/"
ARTIFACTS = {
    "5c90dfd2-34c22ccb.th": (_DEMUCS + "5c90dfd2-34c22ccb.th",
        "34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd"),
    "955717e8-8726e21a.th": (_DEMUCS + "955717e8-8726e21a.th",
        "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"),
    "htdemucs_6s.yaml": (_UVR + "htdemucs_6s.yaml",
        "207405151270af8fd81c2373c25d27950916682ac91dca7884a11ce13dad6f58"),
    "htdemucs.yaml": (_UVR + "htdemucs.yaml",
        "239c445d0b14454d541ad8bd9bb271c9e536d267e8a4625208744cbb2e7bb66c"),
    "UVR_MDXNET_KARA_2.onnx": (_UVR + "UVR_MDXNET_KARA_2.onnx",
        "bf32e15105a09c0f7dddd2b67346146334d6f3ecb399ed7638eba2ab07cbf5f4"),
}
BUILTIN_MODELS = {
    "htdemucs_6s.yaml": ("Demucs", ("htdemucs_6s.yaml", "5c90dfd2-34c22ccb.th")),
    "htdemucs.yaml": ("Demucs", ("htdemucs.yaml", "955717e8-8726e21a.th")),
    "UVR_MDXNET_KARA_2.onnx": ("MDX", ("UVR_MDXNET_KARA_2.onnx",)),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _valid_json(path: Path) -> bool:
    try:
        return path.stat().st_size <= 20_000_000 and isinstance(json.loads(path.read_bytes()), dict)
    except (ValueError, OSError, RecursionError):
        return False


def cached_download(url: str, destination: Path, expected_sha256: str | None = None) -> Path:
    """Reuse validated files; an interrupted transfer never becomes a cache hit.

    Pinned hashes authenticate built-in models. Receipts for other binaries only
    detect later corruption; they do not establish trust in custom models.
    """
    destination = destination.expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt = destination.with_name(f".{destination.name}.sha256")
    for path in (destination, receipt):
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError(f"Model cache entries must be regular files: {path}")
    is_json = destination.suffix == ".json"
    if destination.is_file():
        if expected_sha256:
            valid = sha256(destination) == expected_sha256
        elif is_json:
            # Existing JSON caches can be checked without a receipt or network.
            valid = _valid_json(destination)
        else:
            try:
                saved_hash = receipt.read_text(encoding="ascii").strip() if receipt.exists() and receipt.stat().st_size <= 65 else ""
            except (OSError, ValueError):
                saved_hash = ""
            valid = bool(saved_hash) and sha256(destination) == saved_hash
        if valid:
            return destination

    print(f"Downloading {destination.name}…", file=sys.stderr, flush=True)
    with tempfile.TemporaryDirectory(prefix=".model-download-", dir=destination.parent) as temporary:
        work = Path(temporary)
        download = work / "download"
        digest = hashlib.sha256()
        count = 0
        try:
            response = urlopen(url, timeout=60)
        except HTTPError as error:
            # audio-separator catches RuntimeError to try its fallback model URL.
            raise RuntimeError(f"Model download failed: {destination.name} (HTTP {error.code}).") from error
        with response, download.open("wb") as stream:
            length = response.headers.get("Content-Length")
            expected_size = int(length) if length is not None else None
            while block := response.read(1024 * 1024):
                count += len(block)
                if is_json and count > 20_000_000:
                    raise RuntimeError("The model service returned oversized JSON.")
                digest.update(block)
                stream.write(block)
                if count % (32 * 1024 * 1024) == 0:
                    print(f"Downloaded {count // (1024 * 1024)} MiB", file=sys.stderr, flush=True)
        if not count or expected_size is not None and count != expected_size:
            raise RuntimeError(f"Incomplete model download: {destination.name}. Retry the separation.")
        actual_hash = digest.hexdigest()
        if expected_sha256 and actual_hash != expected_sha256:
            raise RuntimeError(f"Model checksum verification failed: {destination.name}.")
        if is_json and not _valid_json(download):
            raise RuntimeError(f"Invalid model metadata: {destination.name}.")
        # Publish after verification; leave any previous file untouched on error.
        # Atomic replacement also avoids following a link introduced mid-download.
        download.replace(destination)
        if not expected_sha256 and not is_json:
            checksum = work / "checksum"
            checksum.write_text(actual_hash + "\n")
            checksum.replace(receipt)
    return destination


def create_separator(**settings):
    # Must precede audio-separator's ONNX Runtime import on macOS.
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    from audio_separator.separator import Separator

    class CachedSeparator(Separator):
        def download_file_if_not_exists(self, url, output_path):
            destination = Path(output_path).absolute()
            if destination.parent.resolve() != Path(self.model_file_dir).resolve():
                raise ValueError("Model downloads must stay inside the model cache.")
            expected = None
            if destination.name in ARTIFACTS:
                url, expected = ARTIFACTS[destination.name]
            cached_download(url, destination, expected)

        def download_model_files(self, model_filename):
            if model_filename not in BUILTIN_MODELS:
                return super().download_model_files(model_filename)
            model_type, files = BUILTIN_MODELS[model_filename]
            # The built-in choices never take executable checkpoint paths from a
            # mutable remote catalog. Verify every required file before loading.
            for name in files:
                url, digest = ARTIFACTS[name]
                cached_download(url, Path(self.model_file_dir) / name, digest)
            self.model_friendly_name = model_filename
            self.model_is_uvr_vip = False
            return model_filename, model_type, model_filename, str(Path(self.model_file_dir) / model_filename), None

    return CachedSeparator(**settings)
