"""Optional, prompt-guided lead/remaining-guitar separation with SAM Audio."""

from dataclasses import dataclass
import importlib.util
import math
import os
from pathlib import Path
import sys

DEFAULT_PROMPT = "lead electric guitar playing a melodic solo"
GUITAR_ROLES = {"lead", "rhythm"}


@dataclass(frozen=True)
class GuitarOptions:
    model: str = "facebook/sam-audio-small"
    prompt: str = DEFAULT_PROMPT
    span: tuple[float, float] | None = None
    device: str = "auto"
    chunk_seconds: float = 20.0

    def validate(self, duration: float | None = None) -> None:
        if not self.prompt.strip() or not self.model.strip():
            raise ValueError("SAM model and lead prompt must not be empty.")
        if self.device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("SAM device must be auto, cpu, cuda, or mps.")
        if not math.isfinite(self.chunk_seconds) or self.chunk_seconds < 2:
            raise ValueError("--sam-chunk-seconds must be finite and at least 2.")
        if self.span is not None:
            start, end = self.span
            if not all(math.isfinite(v) for v in self.span) or not 0 <= start < end:
                raise ValueError("--lead-span requires 0 <= START < END in seconds.")
            if duration is not None and end > duration:
                raise ValueError(f"--lead-span extends beyond the {duration:.3f}-second input.")


def preflight(options: GuitarOptions) -> None:
    options.validate()
    if importlib.util.find_spec("sam_audio") is None:
        raise RuntimeError(
            'Lead/rhythm separation needs SAM Audio: install -e ".[guitars]" '
            "with pip, then obtain access to the SAM Audio checkpoints on Hugging Face "
            "and authenticate with HF_TOKEN or hf auth login. See README.md."
        )


def chunk_ranges(frames: int, chunk: int, overlap: int):
    if frames <= 0 or not 0 <= overlap < chunk:
        raise ValueError("Invalid audio length or chunk overlap.")
    start = 0
    while start < frames:
        end = min(start + chunk, frames)
        yield start, end
        if end == frames:
            break
        start = end - overlap


def local_anchors(span, start: float, end: float):
    if span is None:
        return None
    left, right = max(span[0], start), min(span[1], end)
    return [[["+", left - start, right - start]]] if left < right else None


def overlap_weights(length: int, overlap: int, first: bool, last: bool):
    import numpy as np

    weights = np.ones(length, dtype=np.float32)
    fade = min(overlap, length)
    if fade and not first:
        weights[:fade] *= np.arange(1, fade + 1, dtype=np.float32) / (fade + 1)
    if fade and not last:
        weights[-fade:] *= np.arange(fade, 0, -1, dtype=np.float32) / (fade + 1)
    return weights


def split_guitars(guitar: Path, directory: Path, cache: Path,
                  options: GuitarOptions) -> dict[str, Path]:
    preflight(options)
    # Set cache/telemetry environment before importing SAM or Hugging Face.
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HOME", str(cache / "huggingface"))
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    import torch
    from sam_audio import SAMAudio, SAMAudioProcessor

    audio, input_rate = sf.read(guitar, dtype="float32", always_2d=True)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Guitar input must contain finite audio samples.")
    options.validate(len(audio) / input_rate)
    device = options.device
    if device == "auto":
        # CUDA is the upstream recommendation; keep MPS experimental and opt-in.
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("--sam-device cuda requested, but CUDA is unavailable.")
    if device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("--sam-device mps requested, but MPS is unavailable.")
    print(f"Loading {options.model} on {device} for lead/rhythm separation", file=sys.stderr)
    # These are real upstream model calls. Access/download/import failures propagate.
    processor = SAMAudioProcessor.from_pretrained(options.model)
    model = SAMAudio.from_pretrained(options.model).eval().to(device)
    rate = processor.audio_sampling_rate
    divisor = math.gcd(input_rate, rate)
    mono = resample_poly(audio.mean(axis=1), rate // divisor, input_rate // divisor)
    total = len(mono)
    chunk = round(options.chunk_seconds * rate)
    overlap = rate  # one-second crossfade
    sums = np.zeros((2, total), dtype=np.float32)
    weights = np.zeros(total, dtype=np.float32)
    windows = list(chunk_ranges(total, chunk, overlap))
    for index, (start, end) in enumerate(windows, 1):
        print(f"SAM guitar chunk {index}/{len(windows)}", file=sys.stderr)
        batch = processor(
            audios=[torch.from_numpy(mono[start:end].copy()).unsqueeze(0)],
            descriptions=[options.prompt],
            anchors=local_anchors(options.span, start / rate, end / rate),
        ).to(device)
        with torch.inference_mode():
            result = model.separate(batch, predict_spans=False, reranking_candidates=1)
        fade = overlap_weights(end - start, overlap, start == 0, end == total)
        # Current SAM API returns one tensor per batch entry, not a single tensor.
        for row, samples in enumerate((result.target[0], result.residual[0])):
            wave = samples.detach().float().cpu().numpy().reshape(-1)
            if len(wave) < end - start or not np.isfinite(wave).all():
                raise RuntimeError("SAM returned incomplete or non-finite audio.")
            sums[row, start:end] += wave[:end - start] * fade
        weights[start:end] += fade
        del batch, result
    if np.any(weights <= 0):
        raise RuntimeError("SAM chunk assembly left uncovered samples.")
    sums /= weights
    paths = {}
    for role, wave in zip(("lead", "rhythm"), sums):
        restored = resample_poly(wave, input_rate // divisor, rate // divisor)[:len(audio)]
        if len(restored) != len(audio) or not np.isfinite(restored).all():
            raise RuntimeError("SAM output duration or samples are invalid.")
        path = directory / f"{role}.wav"
        # Floating-point WAV avoids clipping generated values or normalizing each
        # stem independently. SAM is mono; do not invent stereo information.
        sf.write(path, restored, input_rate, subtype="FLOAT")
        paths[role] = path
    return paths
