# Local vulnerability review — 2026-10-23

Publication is **blocked** by unresolved HIGH/CRITICAL component findings. No dependency versions, overrides, ignore files or severity filters were changed to hide findings. This report describes static scanner results, not verified exploitation of ReconcileIQ.

Trivy **0.76.1**, immutable scanner digest `sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c`, scanned root uv/pnpm locks including development dependencies and the final Linux/arm64 API/worker/web images. The [machine-readable inventory](vulnerabilities.json) records scanner database timestamps, local report hashes, image identifiers, every HIGH/CRITICAL ID/package/version tuple and fix availability. Raw full reports remain in ignored `reports/` and are appropriate for the narrowly scoped CI artifacts.

| Artifact                                      | Critical | High | Medium | Low | Unknown | Scope                                                      |
| --------------------------------------------- | -------: | ---: | -----: | --: | ------: | ---------------------------------------------------------- |
| Locked application + development dependencies |        0 |    1 |      0 |   0 |       0 | One transitive frontend lint dependency                    |
| API                                           |        1 |   79 |    143 | 233 |       2 | Debian 13.7 OS/OCR runtime and installed Python components |
| Worker                                        |        1 |   79 |    143 | 233 |       2 | Same component inventory as API; separate image            |
| Minimal web                                   |        1 |   56 |     87 |  83 |       1 | Debian 12.15/Node standalone runtime                       |

Counts are **package/CVE occurrences per artifact**, not unique vulnerabilities or independent exploit paths. API and worker share most findings. Locked production application dependencies alone had zero findings in this database snapshot; including development dependencies found `braces@3.0.3`, `CVE-2026-93687` (HIGH, no fixed version supplied). Its observed path is `eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces`. Review upstream remediation and build-tool exposure; do not declare it fixed because it is absent from standalone runtime code.

The initial web image included bundled npm/Yarn tooling and had 250 occurrences, including 67 HIGH. Removing those unused runtime tools reduced it to 228 occurrences and 56 HIGH, without changing the root pnpm lock or application dependencies. This is an actual attack-surface reduction; remaining findings are unresolved. The release gate was actually invoked against the worker and returned **exit 1 for 80 HIGH/CRITICAL occurrences**, as intended. It did not push an image.

Selected triage:

- Python images report `CVE-2026-6653` for `libxml2` as CRITICAL. Debian tracks the installed trixie package as vulnerable with a “minor issue/no DSA” note and no fix for that suite in the checked record. Scanner severity and vendor urgency differ; no exploit or remediation is claimed. [Debian record](https://security-tracker.debian.org/tracker/CVE-2026-6653).
- Tesseract 5.5.0 has several HIGH findings, including `CVE-2026-73066`; upstream fixes that traineddata deserialization issue in 5.5.3 while the checked Debian suite remains affected. The image supplies trusted English traineddata and rejects arbitrary document instructions; that does not resolve the entire OCR security inventory or its sandbox limits. [Debian/upstream references](https://security-tracker.debian.org/tracker/CVE-2026-73066).
- Web reports `CVE-2023-45853` for `zlib1g` as CRITICAL. Debian explicitly notes bookworm's vulnerable contributed MiniZip code is not built into these binary packages. This supports an applicability review, **not** an automatic scanner exception. The gate remains unchanged pending a reviewed disposition. [Debian binary applicability note](https://security-tracker.debian.org/tracker/CVE-2023-45853).
- Numerous OS findings have no fixed version in this scanner database. Retain the complete HIGH/CRITICAL inventory, review maintained base/suite updates and vendor advisories, test any actual patch, and rescan. No wholesale distro switch, dependency override or unreviewed source build was introduced solely to reduce alert counts.

This is a point-in-time local report. Hosted linux/amd64 release images must be scanned independently; these counts do not certify them. PostgreSQL/RabbitMQ and the archived custom MinIO image are not included in these three first-party image counts; their lifecycle/patch review remains a production gate. Scanner absence of a finding is not proof of security. See [production readiness](production-readiness.md).
