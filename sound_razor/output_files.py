"""Reject output paths that could overwrite an input or follow a link."""

from pathlib import Path


def validate_outputs(source: Path | None, destinations: list[Path]) -> None:
    for path in destinations:
        if source is not None and (path.resolve() == source.resolve()
                                   or path.exists() and path.samefile(source)):
            raise ValueError(f"Output would overwrite the input recording: {path}. Choose another output folder.")
        if path.is_symlink():
            raise ValueError(f"Output files must not be symlinks: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"Output conflicts with a non-file path: {path}")
