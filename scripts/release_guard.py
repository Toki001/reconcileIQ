"""Read-only publication preflight. No registry writes; fail closed on uncertainty."""

import base64
import json
import os
import re
import subprocess
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VERSION = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def request(url: str, token: str, *, manifest: bool = False) -> dict[str, Any]:
    with urlopen(
        Request(
            url,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": (
                    "application/vnd.oci.image.index.v1+json, "
                    "application/vnd.docker.distribution.manifest.list.v2+json, "
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                    if manifest
                    else "application/json"
                ),
            },
        ),
        timeout=20,
    ) as response:
        data: dict[str, Any] = json.load(response)
        return data


def main() -> None:
    repository, sha, ref = (
        os.environ[key] for key in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF")
    )
    version = ref.removeprefix("refs/tags/")
    if not ref.startswith("refs/tags/") or VERSION.fullmatch(version) is None:
        raise ValueError("Publishing requires an explicit vMAJOR.MINOR.PATCH tag")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("Publishing requires manual dispatch")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Invalid commit")
    origin = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
    if origin.removesuffix(".git").lower() != ("https://github.com/" + repository).lower():
        raise ValueError("Repository remote does not match the workflow repository")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sha:
        raise ValueError("Checkout differs from requested immutable commit")
    token = os.environ["GH_TOKEN"]
    environment = request(
        f"https://api.github.com/repos/{repository}/environments/ghcr-release", token
    )
    if not any(
        rule.get("type") == "required_reviewers" and rule.get("reviewers")
        for rule in environment.get("protection_rules", [])
    ):
        raise ValueError("Configure required reviewers on the ghcr-release environment")
    # Check registry absence explicitly. Network/auth errors never mean a tag is absent.
    for service in ("web", "api", "worker"):
        name = repository.lower() + "-" + service
        query = urlencode({"service": "ghcr.io", "scope": f"repository:{name}:pull,push"})
        basic = base64.b64encode((os.environ["GITHUB_ACTOR"] + ":" + token).encode()).decode()
        with urlopen(
            Request("https://ghcr.io/token?" + query, headers={"Authorization": "Basic " + basic}),
            timeout=20,
        ) as response:
            registry_token = json.load(response)["token"]
        for tag in ("sha-" + sha, version):
            try:
                request(f"https://ghcr.io/v2/{name}/manifests/{tag}", registry_token, manifest=True)
            except HTTPError as error:
                if error.code != 404:
                    raise ValueError("Registry permissions or availability unverified") from None
                problem = json.load(error)
                if not all(
                    item.get("code") in {"MANIFEST_UNKNOWN", "NAME_UNKNOWN"}
                    for item in problem.get("errors", [])
                ) or not problem.get("errors"):
                    raise ValueError("Registry absence response unrecognized") from None
            else:
                raise ValueError("Immutable release tag already exists; never overwrite")
    print("Publication preflight passed: remote, protected review and unused immutable tags.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "Publication blocked: release ref, remote, permissions, review or registry unverified."
        )
        raise SystemExit(1) from None
