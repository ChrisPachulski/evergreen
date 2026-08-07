"""Config loading for shipit."""
import json

DEFAULT_TIMEOUT = 30  # seconds


def load_config(path):
    """Read a JSON config file. Raises KeyError if 'project' is missing."""
    with open(path) as f:
        cfg = json.load(f)
    cfg["project"]  # KeyError on missing — callers rely on the exception
    cfg.setdefault("timeout", DEFAULT_TIMEOUT)
    return cfg


def resolve_setting(cli_value, env_value, file_value, default=None):
    if cli_value is not None:
        return cli_value
    if env_value:
        return env_value
    if file_value is not None:
        return file_value
    return default
