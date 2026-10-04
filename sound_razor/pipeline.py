"""File normalization and pretrained source separation."""

import contextlib
import importlib.util
import logging
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .sources import youtube_audio

DEFAULT_MODEL = "htdemucs_6s.yaml"
VOCAL_MODEL = "UVR_MDXNET_KARA_2.onnx"
MODEL_STEMS = {
    VOCAL_MODEL: ("vocals", "instrumental"),
    "htdemucs.yaml": ("vocals", "drums", "bass", "other"),
    DEFAULT_MODEL: ("vocals", "drums", "bass", "guitar", "piano", "other"),
}
STEM_CHOICES = ("vocals", "drums", "bass", "guitar", "piano", "other", "instrumental")


def select_model(model: str | None, stems: list[str] | None) -> tuple[str, tuple[str, ...] | None]:
    requested = tuple(dict.fromkeys(stems)) if stems is not None else None
    if requested is not None:
        if not requested or set(requested) - set(STEM_CHOICES):
            raise ValueError("Choose stems from: " + ", ".join(STEM_CHOICES))
    if model is None:
        if requested is None or set(requested) & {"guitar", "piano"}:
            model = DEFAULT_MODEL
        elif set(requested) <= {"vocals", "instrumental"}:
            model = VOCAL_MODEL
        else:
            model = "htdemucs.yaml"
    supported = MODEL_STEMS.get(model)
    if requested is not None:
        if supported is None:
            raise ValueError("--stems requires a known model: " + ", ".join(MODEL_STEMS))
        required = set(requested)
        missing = required - set(supported)
        if missing:
            raise ValueError(f"{model} cannot output {', '.join(sorted(missing))}; "
                             f"supported stems: {', '.join(supported)}")
    return model, requested if requested is not None else supported


def combine_other(by_stem: dict[str, Path], model_stems: tuple[str, ...],
                  selected: tuple[str, ...]) -> None:
    """Fold unselected model outputs into Other."""
    if "other" not in selected:
        return
    available = set(model_stems)
    names = ["other", *sorted(available - set(selected))]
    missing = set(names) - by_stem.keys()
    if missing:
        raise RuntimeError(f"Cannot build Other; missing stems: {', '.join(sorted(missing))}")
    if len(names) == 1:
        return
    destination = by_stem["other"]
    # Float WAV preserves sums above full scale without clipping or changing gain.
    # Write separately because FFmpeg cannot overwrite an input it is reading.
    with tempfile.TemporaryDirectory(prefix="other-mix-", dir=destination.parent) as temporary:
        mixed = Path(temporary) / "other.wav"
        inputs = [argument for name in names for argument in ("-i", str(by_stem[name]))]
        subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *inputs,
            "-filter_complex", f"amix=inputs={len(names)}:duration=longest:normalize=0",
            "-c:a", "pcm_f32le", str(mixed),
        ], check=True, stdout=sys.stderr)
        mixed.replace(destination)


def normalize(source: Path, destination: Path) -> None:
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-map", "0:a:0", "-vn", "-ar", "44100", "-ac", "2",
        "-c:a", "pcm_s16le", str(destination),
    ], check=True, stdout=sys.stderr)


def output_directory(source: str, output: Path | None, youtube: bool = False) -> Path:
    if output is not None:
        directory = output.expanduser().resolve()
    elif youtube:
        parent = Path("stems").resolve()
        parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="track-", dir=parent))
    else:
        source_path = Path(source).expanduser().absolute()
        directory = source_path.with_name(source_path.stem).resolve()
        if directory == source_path.resolve():
            raise ValueError("Cannot derive an output folder from an extensionless input; use --output.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def separate(source: str, output: Path | None = None, model: str | None = None,
             cache: Path = Path(".sound-razor/models"), youtube: bool = False,
             stems: list[str] | None = None) -> list[Path]:
    model, selected_stems = select_model(model, stems)
    if not youtube and not Path(source).expanduser().is_file():
        raise ValueError(f"Audio file does not exist: {source}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Install FFmpeg and put it on PATH.")
    if importlib.util.find_spec("audio_separator") is None:
        raise RuntimeError('Install separation support: pip install -e ".[separation]"')
    if youtube and importlib.util.find_spec("yt_dlp") is None:
        raise RuntimeError('Install YouTube support: pip install -e ".[youtube]"')
    output = output_directory(source, output, youtube)
    cache = cache.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sound-razor-") as temporary:
        work = Path(temporary)
        audio = youtube_audio(source, work) if youtube else Path(source).expanduser().resolve()
        normalized = work / "input.wav"
        normalize(audio, normalized)
        # Upstream progress/prints must not corrupt the JSON result on stdout.
        with contextlib.redirect_stdout(sys.stderr):
            # ORT 1.30's native telemetry uploader can crash during shutdown on
            # macOS. Disable it before importing anything that initializes ORT;
            # disable_telemetry_events() after import is too late for startup events.
            os.environ["ORT_DISABLE_TELEMETRY"] = "1"
            from audio_separator.separator import Separator

            separator = Separator(
                output_dir=str(work), output_format="WAV",
                model_file_dir=str(cache), log_level=logging.WARNING,
                mdx_params={"hop_length": 1024, "segment_size": 256,
                            "overlap": 0.25, "batch_size": 1, "enable_denoise": False},
                demucs_params={"segment_size": "Default", "shifts": 0,
                               "overlap": 0.25, "segments_enabled": True},
            )
            separator.load_model(model_filename=model)
            print(f"Separating with {model}; stems: "
                  f"{', '.join(selected_stems) if selected_stems else 'all model outputs'}", file=sys.stderr)
            names = {stem: stem for stem in MODEL_STEMS.get(model, ())}
            results = separator.separate(str(normalized), custom_output_names=names or None)
            del separator
        paths = [Path(name) if Path(name).is_absolute() else work / name for name in results]
        if len(paths) < 2 or any(not path.is_file() or path.stat().st_size <= 44 for path in paths):
            raise RuntimeError("Separator did not produce valid stem files.")
        if selected_stems is not None:
            by_stem = {path.stem: path for path in paths}
            missing = set(selected_stems) - set(by_stem)
            if missing:
                raise RuntimeError(f"Model did not produce requested stems: {', '.join(sorted(missing))}")
            combine_other(by_stem, MODEL_STEMS.get(model, ()), selected_stems)
            paths = [by_stem[stem] for stem in selected_stems]
        # Persist requested stems, with unselected parts folded into Other when requested.
        destinations = [output / path.name for path in paths]
        if any(path.is_dir() for path in destinations):
            raise ValueError("An output filename conflicts with an existing directory.")
        result = [Path(shutil.move(str(path), str(destination))).resolve()
                for path, destination in zip(paths, destinations)]
        settings = output / "separation.json"
        settings.write_text(json.dumps({"stems": selected_stems}, indent=2) + "\n")
        return result


def train(dataset: Path) -> None:
    raise NotImplementedError(
        "Fine-tuning is not implemented: the inference backend has no training API. "
        "Provide aligned mixtures and isolated reference stems, choose a trainable "
        "architecture, and hold out songs for evaluation. Mixed album tracks alone "
        "are not supervised training targets. See README.md."
    )
