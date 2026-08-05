"""Dispatch to a handler chosen by deployment configuration, not by this repository."""
from __future__ import annotations

import importlib
import os
from types import ModuleType


def load_handler() -> ModuleType:
    # The handler name arrives from the deployment environment. Nothing in this repository
    # sets or defaults TOOLBELT_HANDLER, so which module under handlers/ is live cannot be
    # determined from the code here.
    name = os.environ["TOOLBELT_HANDLER"]
    return importlib.import_module(f"{__package__}.handlers.{name}")
