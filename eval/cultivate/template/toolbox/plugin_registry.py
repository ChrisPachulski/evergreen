"""Runtime plugin loading with an intentionally indirect module target."""
from __future__ import annotations

import importlib
from types import ModuleType

PLUGIN_CONFIG = {"demo": {"family": "adapter", "flavor": "runtime"}}


def load_demo_plugin() -> ModuleType:
    config = PLUGIN_CONFIG["demo"]
    module_name = f"{__package__}.plugins.{config['family']}_{config['flavor']}"
    return importlib.import_module(module_name)
