# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.0.x   | :white_check_mark: |
| < 2.0   | :x:                |

## Reporting a Vulnerability

Please **do not open a public issue** for security problems.

Report vulnerabilities privately through GitHub:
**[Report a vulnerability](https://github.com/KOKUTBON/xBAKE/security/advisories/new)**
(repository *Security* tab → *Report a vulnerability*).

Include:

- The type of issue (e.g. unauthenticated control of the actuator, path traversal, credential exposure)
- Steps or a proof of concept to reproduce it
- Affected components (web API, database, hardware layer, deployment files)
- The potential impact on a running production line

You should receive an acknowledgement within a few days. Once a fix is released, reporters
are credited in the release notes unless they prefer to stay anonymous.

## Deployment Recommendations

- Keep the default `web.host` of `127.0.0.1` unless remote access is required. When binding
  to `0.0.0.0`, set `web.auth_token` or the `XBAKE_WEB_TOKEN` environment variable so control
  endpoints (configuration changes, rejector test, counter reset) require a bearer token.
- Read-only endpoints (video stream, metrics, inspection log, CSV export) are not protected by
  the token. Place the device on an isolated network, or put the dashboard behind a reverse
  proxy (e.g. Nginx or Caddy) with TLS and authentication.
- Change default operating-system passwords on edge devices and keep them updated.
- Store Google service account keys and other secrets in `secrets/` (ignored by git) and never
  commit `config.json` files that contain tokens.
- The rejector drives physical equipment: install emergency stops and guarding according to
  your local machine-safety regulations. Software must never be the only safety measure.

## Private reporting contact

Email [kokutbon@gmail.com](mailto:kokutbon@gmail.com) with subject `[xBAKE] Security report`. Include affected version/commit, reproduction using synthetic data, preconditions and impact. Do not include live credentials or personal data. This mailbox is the fallback while the repository is private or GitHub's reporting form is unavailable.

After publication, prefer [GitHub private vulnerability reporting](https://github.com/KOKUTBON/xBAKE/security/advisories/new) when enabled. Acknowledgment and remediation depend on maintainer availability; no response deadline is guaranteed. Coordinate disclosure and fixes privately.
