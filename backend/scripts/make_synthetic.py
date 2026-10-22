"""Generate data/synthetic/ (deterministic): python scripts/make_synthetic.py [--seed N] [--out DIR]"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.synthetic import SEED, write  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", default=str(ROOT / "data" / "synthetic"))
    args = parser.parse_args()
    counts = write(Path(args.out), args.seed)
    print(f"written to {args.out}: {counts}")


if __name__ == "__main__":
    main()
