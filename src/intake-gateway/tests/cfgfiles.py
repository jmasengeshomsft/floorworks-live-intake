"""Loads the fully explicit Dataverse mappings (what discovery derives) from tests/fixtures."""
import json
from pathlib import Path

DIR = Path(__file__).resolve().parent
EXPLICIT = DIR / "fixtures"


def cfg(name: str) -> dict:
    return json.loads((EXPLICIT / f"{name}.json").read_text(encoding="utf-8"))


def raw(name: str) -> str:
    return json.dumps(cfg(name))
