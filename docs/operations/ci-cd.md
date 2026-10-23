# CI/CD and image publishing

## Verification

`.github/workflows/ci.yml` runs on pull requests, main pushes, manual dispatch and reusable workflow calls. It uses GitHub-hosted Ubuntu 24.04, SHA-pinned actions, read-only repository permission, disabled persisted checkout credentials, fixed Python/Node/uv/pnpm tools and frozen root locks. Concurrency cancels superseded verification for the same workflow/ref. No `pull_request_target`, remote production secret, cloud AI key or self-hosted runner is used.

The verify job pulls the reviewed PostgreSQL/RabbitMQ digests from the release example and assigns their development tags only on its disposable VM, then builds/boots the existing Compose stack on that **fresh ephemeral VM**, then runs:

- `make check`: Ruff lint/format, mypy, pytest unit and PostgreSQL integration tests with 100% reconciliation statement coverage, canonical OpenAPI/served GraphQL compatibility, generated client drift, 200 financial plus 100 AI fixture drift, offline evaluations, ESLint, TypeScript, Jest and Prettier.
- `make reliability-check`: private RabbitMQ queues, disposable PostgreSQL schemas/databases, private MinIO bucket and Linux prefork failure/restart tests; existing per-module financial/worker coverage gates remain unchanged.
- Real Linux PDF/OCR/native-network-denial probe, backup/restore/populated-copy Alembic verification, live API/MinIO upload/contracts smoke, controlled processing failure/recovery, then all seven browser regressions using Playwright's version-locked Chromium.
- A distinct worker-target Docker build and resolved private release Compose policy validation. Bootstrap already builds API, web and custom MinIO; frontend production build is also part of bootstrap-lite locally.

There is no connection path/configuration to the developer's retained database. Tests create their own schemas/queues/buckets and CI-only Compose volumes; `down --volumes` cleanup occurs **only on the fresh hosted CI VM**, never as local retained-data advice. Ordinary CI never calls Gemini, even when local development has opted in: fresh synthetic configuration has AI disabled and no provider/ADC secrets. Mock-provider scores are not model quality evidence.

The security job scans Git history with digest-pinned Gitleaks (redacted) and lockfiles with digest-pinned Trivy. Dependency findings are reported, not silently suppressed or automatically fixed. Image publication independently blocks on **all HIGH/CRITICAL image findings, including unfixed ones**. Scanner/DB download failure is a failure, not a clean report. Dependency report visibility differs from a hard vulnerability gate; branch protection should require the verification and secret scan jobs, and remediation must be reviewed before release.

Only explicit vulnerability JSON and coverage metadata are uploaded, retained seven days. No `.cache` directory, `.env`, backup, PDF, browser trace/screenshot, service log or private URL is uploaded. Use sanitized local diagnostics for failures. Workflow commands do not interpolate PR titles, branch strings or user inputs directly as shell code. Pinned actions reduce mutable-reference risk but still require review; update their SHAs and container digests through reviewed changes.

## GHCR preparation — blocked until configured

`.github/workflows/release.yml` supports **manual dispatch on a `vMAJOR.MINOR.PATCH` tag only**, with explicit `publish=true` and repository variable `RECONCILE_GHCR_ENABLED=true`. PRs never publish. The release first calls the complete verification workflow with read-only permissions, then enters the protected `ghcr-release` environment. Only the publishing job receives `packages:write`, through `GITHUB_TOKEN`; `actions:read` permits checking environment protection metadata. No PAT is needed.

Before enabling:

1. Configure/verify the repository's GitHub remote and ownership. Enable Actions, package write permissions and branch/tag protection. Review private Actions/GHCR quotas, storage and transfer costs; no paid hosting is provisioned automatically.
2. Create `ghcr-release` with **required reviewers**, restricted release tags and approved reviewers. The preflight reads its protection rules through GitHub's API and fails closed if unavailable/unprotected. Ensure the token can read that metadata; organization restrictions may block it. Do not weaken the guard to bypass a 403. Required reviewers for private repositories may require a different GitHub plan; verify current account entitlements/costs before enabling and do not change billing automatically.
3. Verify package creation/access will remain **private**, inherited repository permissions and organization package policies. Do not make images public. Set the opt-in repository variable only after these checks.
4. Resolve container HIGH/CRITICAL findings and production-readiness blockers, and approve the actual tested revision/tag. A CI pass alone is not public deployment authorization.
5. Create an immutable release tag and manually dispatch on that tag with publish enabled. This milestone does not create/push a remote tag, configure the external account or run that dispatch.

Preflight compares the checkout commit and HTTPS remote with GitHub's repository, checks protected review configuration and verifies both registry tags are absent. Authentication/network ambiguity blocks publication. It never overwrites existing SHA or version tags. GitHub registry tags are technically mutable to other authorized writers: enforce package permissions and deploy by digest. No `latest` alias is created. A partial multi-image push can still occur on registry failure; retain the digest report, do not overwrite tags, and recover under a new reviewed version/commit.

The three artifacts are `ghcr.io/<owner>/<repository>-web`, `-api` and `-worker`, tagged `sha-<full-commit>` and `<vMAJOR.MINOR.PATCH>`. OCI labels record source, Git revision and release version. All three are built/scanned before login/push; remote manifest digests appear in the job summary for verification. Artifact signatures/SLSA attestations and multi-architecture publication are deferred; current release runner builds linux/amd64. The locally verified development platform is linux/arm64. Custom MinIO remains a separately built pinned third-party image, not a first-party dispatcher artifact.

Local equivalents:

```sh
make release-images
make release-config
make release-db-check
# Validate workflows with a checksum-verified actionlint installation:
actionlint
```

As of Milestone 07, local results are in [verification](../verification/milestone-07.md). A prepared workflow is not an executed GitHub run. No remote, package permission, protected environment, paid allowance or GHCR publishing success is claimed without actual evidence.

References: [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use), [GITHUB_TOKEN package publishing](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), [Compose production guidance](https://docs.docker.com/compose/how-tos/production/).
