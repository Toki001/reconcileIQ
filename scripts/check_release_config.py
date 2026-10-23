"""Check resolved Compose policy without printing its credential-bearing configuration."""

import json
import subprocess


def main() -> None:
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.release.yml", "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Release Compose validation failed; configuration withheld")
    config = json.loads(result.stdout)
    assert config["name"] == "reconcileiq-release"
    assert config["networks"]["backend"]["internal"] is True
    for name, service in config["services"].items():
        assert service.get("mem_limit") and service.get("cpus")
        assert service.get("restart") == "unless-stopped"
        if name == "web":
            assert all(p["host_ip"] == "127.0.0.1" for p in service["ports"])
        else:
            assert not service.get("ports"), f"Unexpected public infrastructure port: {name}"
        if name in {"api", "worker"}:
            assert service["environment"]["RECONCILE_AI_ENABLED"] == "false"
            assert not service["environment"].get("RECONCILE_AI_API_KEY")
            assert service["read_only"]
            assert "release_preflight.py" in " ".join(service["command"])
    print("Release Compose policy passed; configuration and credentials withheld.")


if __name__ == "__main__":
    main()
