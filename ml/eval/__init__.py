"""Offline evaluation. Uses the backend domain code (validator, scorer, tagger, build_context) as the reference."""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
