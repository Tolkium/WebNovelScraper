"""Centralized resource path lookup for both source runs and PyInstaller builds."""

from pathlib import Path
import sys

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))


def resource(*parts: str) -> Path:
    """Return the absolute path to a bundled asset or script relative to the project root."""
    return ROOT.joinpath(*parts)
