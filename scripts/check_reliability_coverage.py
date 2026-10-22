"""Enforce statement targets per critical module; report branch coverage separately."""

import json
from pathlib import Path


def main() -> None:
    report = json.loads(Path(".cache/m05-coverage.json").read_text())
    critical = {"financial.py", "processing.py", "providers.py", "dispatch.py"}
    found = set()
    failures = []
    for path, data in report["files"].items():
        name = Path(path).name
        accounting = "reconcileiq_reconciliation/" in path and name != "__init__.py"
        if not accounting and name not in critical:
            continue
        summary = data["summary"]
        statements = 100 * summary["covered_lines"] / summary["num_statements"]
        branches = 100 * summary["covered_branches"] / summary["num_branches"]
        required = 100 if accounting else 90
        print(
            f"{name}: {statements:.2f}% statements, {branches:.2f}% branches; "
            f"target {required}% statements"
        )
        found.add(name)
        if statements < required:
            failures.append(name)
    assert critical | {"money.py", "accounting.py"} <= found, "Missing coverage source"
    assert not failures, "Critical statement coverage below target: " + ", ".join(failures)


if __name__ == "__main__":
    main()
