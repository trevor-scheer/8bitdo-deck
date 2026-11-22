"""Process environment rules shared by packaged Linux integrations."""

import os
from pathlib import Path
import sys


def frozen_application() -> bool:
    """Distinguish our bundle from a frozen host importing an external plugin."""
    bundle = getattr(sys, "_MEIPASS", None)
    return bool(getattr(sys, "frozen", False) and bundle
                and Path(__file__).resolve().is_relative_to(Path(bundle).resolve()))


def external_environment() -> dict[str, str]:
    """Do not force system tools to load libraries from a PyInstaller bundle."""
    environment = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original
        for key in tuple(environment):
            if key.startswith("_PYI_") or key == "PYINSTALLER_RESET_ENVIRONMENT":
                environment.pop(key)
    return environment
