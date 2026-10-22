"""Mirrors of the versioned ML contracts in /contracts (repo root, shared by backend, ML and frontend)."""

import os
from pathlib import Path


def _find_contracts_dir() -> Path:
    """CONTRACTS_DIR env, else the nearest ancestor that has contracts/insight.schema.json
    (repo root in development, /app inside the Docker image)."""
    env = os.environ.get("CONTRACTS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "contracts"
        if (candidate / "insight.schema.json").is_file():
            return candidate
    raise FileNotFoundError("contracts/insight.schema.json not found; set CONTRACTS_DIR")


CONTRACTS_DIR = _find_contracts_dir()
