"""CLI: JSON results on stdout, diagnostics on stderr."""

import argparse
import json
import sys
from pathlib import Path

from .pipeline import DEFAULT_MODEL, STEM_CHOICES, separate, train
from .sources import spotify_metadata
from .guitars import GuitarOptions


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="master-track", description="Separate audio into WAV stems.")
    commands = root.add_subparsers(dest="command", required=True)
    split = commands.add_parser("separate", help="Separate a local file or explicit YouTube video")
    source = split.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--youtube", help="HTTPS video URL for audio you may download/process")
    split.add_argument("--output", "--output-folder", type=Path,
                       help="Destination folder (created if needed). Default: input folder/filename without extension; YouTube: stems/track-<id>.")
    split.add_argument("--model", help=f"Override automatic model selection (default: {DEFAULT_MODEL})")
    split.add_argument("--stems", nargs="+", choices=STEM_CHOICES,
                       help="Stems to save; other includes unselected parts; lead/rhythm enable a second SAM Audio stage. Default: all six Demucs stems.")
    split.add_argument("--lead-prompt", help="Description of the lead part to extract")
    split.add_argument("--lead-span", nargs=2, type=float, metavar=("START", "END"),
                       help="Seconds where lead is audible; guides overlapping SAM chunks")
    split.add_argument("--sam-model", help="Hugging Face model ID or local checkpoint directory")
    split.add_argument("--sam-device", choices=("auto", "cpu", "cuda", "mps"))
    split.add_argument("--sam-chunk-seconds", type=float, help="SAM chunk length (default: 20, minimum: 2)")
    split.add_argument("--cache", type=Path, default=Path(".master-track/models"))
    metadata = commands.add_parser("spotify", help="Fetch Spotify track metadata (no audio)")
    metadata.add_argument("track", help="Track ID, URI, or URL")
    training = commands.add_parser("train", help="Fine-tuning boundary (not implemented)")
    training.add_argument("dataset", type=Path)
    ui = commands.add_parser("ui", help="Open a local project library and stem player in your browser")
    ui.add_argument("--projects", type=Path, help="Projects folder; otherwise use the last selection or choose in the UI")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--cache", type=Path, default=Path(".master-track/models"))
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "spotify":
            result = spotify_metadata(args.track)
        elif args.command == "train":
            train(args.dataset)
            return 0
        elif args.command == "ui":
            from .ui import serve
            serve(args.projects, args.port, args.cache)
            return 0
        else:
            options = {key: value for key, value in {
                "prompt": args.lead_prompt, "span": tuple(args.lead_span) if args.lead_span else None,
                "model": args.sam_model, "device": args.sam_device,
                "chunk_seconds": args.sam_chunk_seconds,
            }.items() if value is not None}
            result = [str(path) for path in separate(
                str(args.file) if args.file else args.youtube,
                args.output, args.model, args.cache, youtube=bool(args.youtube), stems=args.stems,
                guitar_options=GuitarOptions(**options) if options else None,
            )]
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print("master-track: interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"master-track: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
