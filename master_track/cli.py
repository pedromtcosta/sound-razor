"""CLI: JSON results on stdout, diagnostics on stderr."""

import argparse
import json
import sys
from pathlib import Path

from .pipeline import DEFAULT_MODEL, STEM_CHOICES, separate, train


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
                       help="Stems to save; other includes unselected parts. Default: all six Demucs stems.")
    split.add_argument("--cache", type=Path, default=Path(".master-track/models"))
    guitar = commands.add_parser("split-guitar", help="Split an existing guitar stem into lead and rhythm")
    guitar.add_argument("--file", type=Path, required=True)
    guitar.add_argument("--output", type=Path, required=True)
    guitar.add_argument("--cache", type=Path, default=Path(".master-track/models"))
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
        if args.command == "split-guitar":
            from .guitar_split import split_guitar
            result = [str(path) for path in split_guitar(args.file, args.output, args.cache)]
        elif args.command == "train":
            train(args.dataset)
            return 0
        elif args.command == "ui":
            from .ui import serve
            serve(args.projects, args.port, args.cache)
            return 0
        else:
            result = [str(path) for path in separate(
                str(args.file) if args.file else args.youtube,
                args.output, args.model, args.cache, youtube=bool(args.youtube), stems=args.stems,
            )]
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print("master-track: interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"master-track: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
