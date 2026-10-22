import argparse
from pathlib import Path

import yaml
from graphql import get_introspection_query
from reconcileiq_api.compatibility import assert_graphql_contract
from reconcileiq_api.graphql import schema
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
        result = schema.execute_sync(get_introspection_query(descriptions=False))
        assert result.data is not None and not result.errors
        assert_graphql_contract(Path("packages/contracts/schema.graphql").read_text(), result.data)
        print("Strawberry schema matches the reviewed GraphQL SDL.")
    else:
        path.write_text(content)
        print(f"Exported {path}")


if __name__ == "__main__":
    main()
