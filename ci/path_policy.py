"""Shared repository-path policy for CI evidence and result citations."""

from pathlib import PurePosixPath, PureWindowsPath


MAX_PATH = 1024


def is_protocol_path(path: object) -> bool:
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        return False
    pure = PurePosixPath(path)
    return not (
        "\n" in path
        or "\r" in path
        or "\x00" in path
        or any("\ud800" <= char <= "\udfff" for char in path)
        or PureWindowsPath(path).is_absolute()
        or pure.is_absolute()
        or "\\" in path
        or path != pure.as_posix()
        or any(part in {".", ".."} for part in pure.parts)
    )
