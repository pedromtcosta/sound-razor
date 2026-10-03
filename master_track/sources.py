"""Real input acquisition; Spotify supplies metadata only."""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


def spotify_track_id(value: str) -> str:
    if value.startswith("spotify:track:"):
        value = value.removeprefix("spotify:track:")
    elif value.startswith("https://"):
        url = urlparse(value)
        if url.hostname != "open.spotify.com":
            raise ValueError("Expected a Spotify track URL.")
        match = re.fullmatch(r"/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})/?", url.path)
        if not match:
            raise ValueError("Expected a Spotify track URL, not an album or playlist.")
        value = match[1]
    if not re.fullmatch(r"[A-Za-z0-9]{22}", value):
        raise ValueError("Expected a 22-character Spotify track ID, URI, or URL.")
    return value


def _json_request(request: Request) -> dict:
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def spotify_metadata(track: str) -> dict:
    track_id = spotify_track_id(track)
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not secret:
        raise ValueError("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET for metadata lookup.")
    credentials = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    token = _json_request(Request(
        "https://accounts.spotify.com/api/token",
        data=urlencode({"grant_type": "client_credentials"}).encode(),
        headers={"Authorization": f"Basic {credentials}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    ))["access_token"]
    data = _json_request(Request(
        f"https://api.spotify.com/v1/tracks/{track_id}",
        headers={"Authorization": f"Bearer {token}"},
    ))
    return {"id": data["id"], "name": data["name"],
            "artists": [artist["name"] for artist in data["artists"]],
            "album": data["album"]["name"],
            "url": data["external_urls"]["spotify"]}


def youtube_audio(url: str, directory: Path) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"
    }:
        raise ValueError("Expected an HTTPS YouTube video URL.")
    subprocess.run([
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
        "--no-progress", "--format", "bestaudio/best", "--extract-audio",
        "--audio-format", "wav", "--output", str(directory / "source.%(ext)s"),
        "--", url,
    ], check=True, stdout=sys.stderr)
    result = directory / "source.wav"
    if not result.is_file():
        raise RuntimeError("yt-dlp completed without producing source.wav.")
    return result
