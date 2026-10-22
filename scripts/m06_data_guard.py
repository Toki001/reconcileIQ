"""Compare every pre-existing local row over its original columns across additive migration."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from reconcileiq_database.testing import local_postgres_url
from sqlalchemy import MetaData, create_engine, select


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--path", type=Path, default=Path(".cache/m06-original-rows.json"))
    args = parser.parse_args()
    url = local_postgres_url()
    assert url.host == "127.0.0.1" and url.port == 15432
    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(engine)
    before = {} if args.capture else json.loads(args.path.read_text())
    result: dict[str, Any] = {}
    verified = 0
    with engine.connect() as connection:
        for name, table in metadata.tables.items():
            if name == "alembic_version" or (not args.capture and name not in before):
                continue
            names = list(table.columns.keys()) if args.capture else before[name]["columns"]
            columns = [table.c[name] for name in names]
            keys = [column.name for column in table.primary_key.columns]
            rows = {}
            for record in connection.execute(select(*columns)).mappings():
                identity = json.dumps([record[key] for key in keys], default=str)
                rows[identity] = hashlib.sha256(
                    json.dumps(dict(record), sort_keys=True, default=str).encode()
                ).hexdigest()
            if args.capture:
                result[name] = {"columns": names, "rows": rows}
            else:
                for identity, digest in before[name]["rows"].items():
                    assert rows.get(identity) == digest, f"Pre-existing row changed in {name}"
                    verified += 1
    if args.capture:
        fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, sort_keys=True)
        print(
            json.dumps(
                {
                    "tables": len(result),
                    "rows": sum(len(t["rows"]) for t in result.values()),
                    "captured": True,
                }
            )
        )
    else:
        print(
            json.dumps(
                {
                    "original_rows_verified": verified,
                    "original_tables": len(before),
                    "changed_original_rows": 0,
                }
            )
        )
    engine.dispose()


if __name__ == "__main__":
    main()
