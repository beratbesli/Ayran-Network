# Security policy

## Scope

Ayran-Network is a local Linux TUI. It samples process and socket metadata,
can optionally send public IP addresses to a configured HTTPS Geo-IP service,
and can request process suspend/resume/terminate actions after confirmation.
Exported JSONL and CSV snapshots may contain sensitive host and process data.

## Safe operation

- Run as an unprivileged user by default.
- Keep Geo-IP disabled unless the outbound disclosure is intentional.
- Use a dedicated, least-privilege service if system-wide visibility is needed;
  do not grant capabilities to a general-purpose Python interpreter.
- Treat exported snapshots as sensitive local data.
- Review configured Geo-IP endpoints before enabling the feature.

## Reporting

Do not publish exploit details, credentials, or private snapshots in an issue.
Use GitHub private vulnerability reporting from the repository Security tab. If
that is unavailable, contact the maintainer through
[@beratbesli](https://github.com/beratbesli) before public disclosure.

Include the affected version or commit, operating system, reproduction steps,
impact, and a proposed mitigation where possible.
