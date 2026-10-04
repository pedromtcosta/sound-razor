---
name: container-status-check
description: Check Dockerfile and Compose status, including base-image and built-image vulnerabilities reported as Docker DX critical_high_vulnerabilities warnings. Use for requested container status checks or investigation of Docker image vulnerability diagnostics.
---

# Container status check

Report container configuration validity and image vulnerability findings separately. A successful Compose validation or build does not establish that an image has no known vulnerabilities.

## Scope and prerequisites

- Read the project's Dockerfiles and Compose files. Identify every external `FROM` image, including stages used by `COPY --from`; exclude stage aliases and `scratch`. Resolve build arguments and target platform from project configuration. If these cannot be resolved, report the missing coverage.
- Check available tooling with `docker compose version` and `docker scout version`. Use `docker compose config --quiet` for configuration validation without exposing interpolated credentials.
- Respect existing installation/build constraints. A status check does not authorize installing tools, building application images, editing image tags, enabling cloud repository monitoring, or pushing images. Scanner registry access and local cache writes may require normal tool escalation. If tools, authentication or network access are unavailable, report the check as incomplete, never clean.

## Vulnerability checks

Prefer Docker Scout to investigate Docker DX image diagnostics. Confirm supported flags with the installed CLI's help when needed. For each external image, use an explicit registry source to avoid silently inspecting a stale local tag:

```sh
docker scout cves --only-severity critical,high --exit-code --format markdown --platform linux/arm64 registry://python:3.11-slim-bookworm
```

Substitute the actual image and configured platform; `linux/arm64` above is an example. Scan each deployment platform if more than one is configured. Record resolved digest, platform, scanner version, timestamp, counts, CVE identifiers, affected package versions and available fixes. Preserve scanner output in a task-specific report file when useful. Do not sum per-package occurrences and call that count unique CVEs.

Scout's `--exit-code` returns 2 when vulnerabilities are detected. Treat that as a finding, not a scanner failure. Other failures require reading diagnostics; distinguish an incomplete scan from a completed scan with findings. Do not use `--only-fixed` or `--ignore-base` for the primary check: those filters hide relevant warnings. Include high/critical findings without available fixes.

If the application image already exists locally, scan it separately using its explicit local reference, for example:

```sh
docker scout cves --only-severity critical,high --exit-code --format markdown local://sound-razor:local
```

Record its ID/digest and whether it is known to match current sources. If absent, mark application-image scanning as not run; do not build solely to complete the report when builds have not been authorized. A base-image scan does not cover packages installed by `apt` or `pip`, nor all copied artifacts in the final image.

If Scout cannot run and Trivy is already available, use its image scan for HIGH and CRITICAL severities, including OS and language packages. Consult its installed help for flags and label the result as Trivy evidence; counts need not match Docker DX. Do not substitute a Dockerfile linter or filesystem dependency scan for an image scan.

## Report and interpretation

Lead with `findings`, `no high/critical findings in completed scans`, or `incomplete`. If findings and missing coverage coexist, report both. Include a compact table with check, image/digest, platform, result and evidence/report path. Summarize actionable CVEs and fixed versions, plus checks that could not run.

Treat editor counts supplied by the user as reported evidence until independently verified. Explain discrepancies using image digest, platform, scan time, vulnerability database and scanner differences; do not promise identical counts or infer that a warning is fixed because a tag changed.

Suggest remediation from actual findings. Compare candidate image scans and compatibility before recommending a base-image change. Rebuilding with a refreshed base or selecting a newer distribution can help but is not a guaranteed fix. Apply changes only within the requested scope, then rescan the actual rebuilt image when authorized. Do not suppress the editor warning as remediation.

Reference: [Docker Scout CVE command and exit-code semantics](https://docs.docker.com/reference/cli/docker/scout/cves/).
