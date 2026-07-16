"""Configuration: DEFAULTS, then loom.toml's [logloom] table, then LOGLOOM_* env. Later wins."""

import os
import tomllib

DEFAULTS = {"format": "json", "level": ""}


def load_config(path="loom.toml"):
    """Merge the three config layers; the environment always has the last word."""
    merged = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "rb") as handle:
            merged.update(tomllib.load(handle).get("logloom", {}))
    for key in DEFAULTS:  # unknown LOGLOOM_* vars are ignored on purpose
        value = os.environ.get(f"LOGLOOM_{key.upper()}")
        if value is not None:
            merged[key] = value
    return merged
