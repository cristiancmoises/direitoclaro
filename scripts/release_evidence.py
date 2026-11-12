"""Inspect release-bound technical receipt integrity; never authorize deployment."""

import argparse
import json
from pathlib import Path

from app.release_evidence import inspect_evidence
from scripts.quality import save_private


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--observed-artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    expected = json.loads(args.observed_artifacts.read_text())
    result = inspect_evidence(args.manifest, expected_artifacts=expected)
    save_private(args.output, result)
    if not all(result["checks"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
