"""Reference-free lead guitar inference with the tested listra92 checkpoint."""
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

MODEL_URL = ('https://huggingface.co/noblebarkrr/mvsepless_resources/resolve/main/'
             'mel_band_roformer/mbr_lead_rhythm_guitar_listra92.ckpt')
MODEL_SHA256 = 'b3c47bca33609ca1ba0bb2d2076410bfd1eb941b051b72afc1f3e24d12b17eef'


def checkpoint(cache):
    destination = cache.expanduser().resolve() / 'lead_rhythm_listra92.ckpt'
    destination.parent.mkdir(parents=True, exist_ok=True)
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    if destination.is_file() and digest(destination) == MODEL_SHA256:
        return destination
    print('Downloading lead/rhythm model (321 MiB)…', file=sys.stderr, flush=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.guitar-download-') as temporary:
        download = Path(temporary) / 'model.ckpt'
        with urlopen(MODEL_URL, timeout=60) as response, download.open('wb') as stream:
            count = 0
            while block := response.read(8 * 1024 * 1024):
                stream.write(block)
                count += len(block)
                print(f'Downloaded {count // (1024 * 1024)} MiB', file=sys.stderr, flush=True)
        if digest(download) != MODEL_SHA256:
            raise RuntimeError('The downloaded guitar model failed checksum verification.')
        download.replace(destination)
    return destination


def split_guitar(source: Path, output: Path, cache: Path) -> list[Path]:
    if not source.is_file():
        raise ValueError('Separate the guitar track first.')
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from .vendor.roformer.mel_band_roformer import MelBandRoformer
    except ImportError as error:
        raise RuntimeError('Install guitar splitting support: pip install -e ".[guitars]"') from error
    mix, sr = sf.read(source, dtype='float32', always_2d=True)
    if sr != 44100 or mix.shape[1] != 2 or len(mix) == 0 or not np.isfinite(mix).all():
        raise ValueError('Guitar splitting requires a nonempty, finite stereo 44.1 kHz guitar stem.')
    weights = checkpoint(cache)
    torch.set_num_threads(4)
    torch.manual_seed(42)
    config = json.loads((Path(__file__).parent / 'vendor/roformer/lead_rhythm.json').read_text())
    config['multi_stft_resolutions_window_sizes'] = tuple(config['multi_stft_resolutions_window_sizes'])
    with contextlib.redirect_stdout(sys.stderr):
        model = MelBandRoformer(**config)
        model.load_state_dict(torch.load(weights, map_location='cpu', weights_only=True), strict=True)
        model.eval()
    # Same batch-one overlap/add procedure as the tested MSST generic demix.
    chunk, step, fade = 132300, 66150, 13230
    length = len(mix)
    audio = torch.from_numpy(mix.T.copy())
    padded = length > 2 * step
    if padded:
        audio = torch.nn.functional.pad(audio, (step, step), mode='reflect')
    result = torch.zeros_like(audio)
    counter = torch.zeros(audio.shape[-1])
    window = torch.ones(chunk)
    window[:fade] = torch.linspace(0, 1, fade)
    window[-fade:] = torch.linspace(1, 0, fade)
    print('Splitting guitar into lead and rhythm · 50% overlap · CPU', file=sys.stderr, flush=True)
    with torch.inference_mode():
        for start in range(0, audio.shape[-1], step):
            part = audio[:, start:start + chunk]
            size = part.shape[-1]
            part = torch.nn.functional.pad(part, (0, chunk-size), mode='reflect' if size > chunk//2 else 'constant')
            prediction = model(part[None])[0, ..., :size]
            blend = window.clone()
            if start == 0:
                blend[:fade] = 1
            elif start + step >= audio.shape[-1]:
                blend[-fade:] = 1
            result[:, start:start+size] += prediction * blend[:size]
            counter[start:start+size] += blend[:size]
            print(f'Guitar split {min(100, round((start+step)/audio.shape[-1]*100))}%', file=sys.stderr, flush=True)
        result /= counter
    if padded:
        result = result[:, step:-step]
    lead = result.numpy().T
    if lead.shape != mix.shape or not np.isfinite(lead).all():
        raise RuntimeError('Guitar model produced invalid audio.')
    output.mkdir(parents=True, exist_ok=True)
    paths = [output/'lead.wav', output/'rhythm.wav']
    sf.write(paths[0], lead, sr, subtype='FLOAT')
    sf.write(paths[1], mix-lead, sr, subtype='FLOAT')
    settings_path = output/'separation.json'
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings['lead_rhythm_split'] = {'model': 'listra92', 'sha256': MODEL_SHA256, 'overlap': 0.5}
    settings_path.write_text(json.dumps(settings, indent=2)+'\n')
    return [path.resolve() for path in paths]
