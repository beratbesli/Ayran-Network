# Security model

## Trust boundaries

1. **Local host:** psutil data is read from the machine running the TUI.
2. **Optional Geo-IP service:** only public IPs selected by the user may be
   sent over HTTPS when Geo-IP is explicitly enabled.
3. **Local export directory:** JSONL and CSV snapshots are written with private
   permissions where the host operating system supports them.
4. **Process control:** suspend, resume, and terminate actions affect local
   processes and must remain explicit, confirmed operations.

The application is designed as a local tool, not as a multi-user server. It
does not provide a network API or an authentication boundary. Do not expose it
through a remote shell or web service without adding authentication, auditing,
and a separate privilege boundary.

## Review checklist for changes

- Does the change disclose a new host or process field to a third party?
- Does it widen the set of processes that can be controlled?
- Does it write exports outside the private default directory?
- Does it add a dependency that executes with user or elevated privileges?
- Are errors logged without leaking command arguments, credentials, or private
  host data?
