"""Trusted, repository-labelled synthetic fixture allowlist; never accept a caller's filename."""

import json
from pathlib import Path


def synthetic_digest_allowed(digest: str) -> bool:
    if len(digest) != 64:
        return False
    for path in (
        Path("tests/fixtures/financial/manifest.json"),
        Path("packages/ai/evals/golden-manifest.json"),
    ):
        try:
            data = json.loads(path.read_text())
            if any(row["pdf_sha256"] == digest for row in data["invoices"]):
                return True
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return False
