"""
Time utility functions.
Moved from app.py lines 871-892 — NO LOGIC CHANGES.
"""
from __future__ import annotations


def extract_icon(label: str) -> str:
    """Return only the emoji part of a label."""
    if " " in label:
        return label.split(" ", 1)[0]
    return label  # fallback
