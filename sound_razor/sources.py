"""YouTube audio acquisition through yt-dlp."""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs


def youtube_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Enter a YouTube video URL.')
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"
    }:
        raise ValueError("Expected an HTTPS YouTube video URL.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Expected a YouTube video URL.')
    if parsed.hostname == 'youtu.be':
        identifier = parsed.path.strip('/')
    elif parsed.path == '/watch':
        identifier = parse_qs(parsed.query).get('v', [''])[0]
    else:
        match = re.fullmatch(r'/(?:shorts|live|embed)/([\w-]{11})/?', parsed.path)
        identifier = match[1] if match else ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
        raise ValueError('Use a single video URL, not a channel or playlist URL.')
    return 'https://www.youtube.com/watch?v=' + identifier


def youtube_command(url: str, directory: Path) -> list[str]:
    return [
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
        "--newline", "--progress", "--progress-delta", "1", "--write-info-json",
        "--format", "bestaudio/best", "--extract-audio",
        "--audio-format", "wav", "--output", str(directory / "source.%(ext)s"),
        "--", youtube_url(url),
    ]


def youtube_audio(url: str, directory: Path) -> Path:
    subprocess.run(youtube_command(url, directory), check=True, stdout=sys.stderr)
    result = directory / "source.wav"
    if not result.is_file():
        raise RuntimeError("yt-dlp completed without producing source.wav.")
    return result
