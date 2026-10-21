import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    with TemporaryDirectory(prefix="reconcileiq-contract-") as directory:
        output = Path(directory) / "schema.ts"
        subprocess.run(
            [
                "pnpm",
                "exec",
                "openapi-typescript",
                "packages/contracts/openapi.yaml",
                "-o",
                str(output),
            ],
            check=True,
        )
        if output.read_text() != Path("packages/generated/src/schema.ts").read_text():
            raise SystemExit(
                "TypeScript client drift: run make contracts and commit both artifacts."
            )
    print("Generated TypeScript schema matches OpenAPI.")


if __name__ == "__main__":
    main()
