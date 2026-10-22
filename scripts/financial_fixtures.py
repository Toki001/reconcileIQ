"""Generate or verify only the dedicated, deterministic synthetic PDF fixture set."""

import argparse
from pathlib import Path

from reconcileiq_database.fixture_data import generate_fixtures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate_fixtures(Path("tests/fixtures/financial"), check=args.check)
    print(
        "Synthetic fixtures verified."
        if args.check
        else "Generated 200 synthetic PDFs and ground truth manifest."
    )


if __name__ == "__main__":
    main()
