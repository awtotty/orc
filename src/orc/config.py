"""Config loader for orc — reads config.toml from the orc source root."""

import os
import tomllib


DEFAULTS = {
    "sandbox": {
        "ports": ["7777:7777"],
        "packages": [],
        "mounts": [],
        "env": [],
    },
}


def _orc_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def config_path():
    return os.path.join(_orc_root(), "config.toml")


def load():
    """Load config.toml and merge with defaults. Returns dict."""
    path = config_path()
    cfg = {}
    if os.path.isfile(path):
        with open(path, "rb") as f:
            cfg = tomllib.load(f)

    # Merge sandbox section with defaults
    result = {}
    for section, defaults in DEFAULTS.items():
        file_section = cfg.get(section, {})
        result[section] = {k: file_section.get(k, v) for k, v in defaults.items()}
    return result
