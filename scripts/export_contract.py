import argparse
from pathlib import Path

import yaml
from reconcileiq_api.main import app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path("packages/contracts/openapi.yaml")
    content = yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True)
    if args.check:
        if not path.exists() or path.read_text() != content:
            raise SystemExit("Contract drift: run make contracts and commit the generated files.")
        print("OpenAPI contract matches implemented handlers.")
    else:
        path.write_text(content)
        print(f"Exported {path}")


if __name__ == "__main__":
    main()
