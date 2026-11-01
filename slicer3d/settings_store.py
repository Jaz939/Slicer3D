"""Remember the user's exposure settings per printer between sessions."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields, replace

from .printers import PRINTERS, ExposureSettings, PrinterProfile


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "Slicer3D", "settings.json")


def _read() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_printer(key: str) -> PrinterProfile:
    """Built-in profile with any saved exposure changes applied."""
    printer = PRINTERS[key]
    saved = _read().get("exposure", {}).get(key, {})
    valid = {f.name for f in fields(ExposureSettings)}
    changes = {k: v for k, v in saved.items() if k in valid}
    return replace(printer, exposure=replace(printer.exposure, **changes)) if changes else printer


def save_printer(printer: PrinterProfile) -> None:
    data = _read()
    data.setdefault("exposure", {})[printer.key] = asdict(printer.exposure)
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def last_printer() -> str:
    key = _read().get("last_printer")
    return key if key in PRINTERS else next(iter(PRINTERS))


def remember_printer(key: str) -> None:
    data = _read()
    data["last_printer"] = key
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
