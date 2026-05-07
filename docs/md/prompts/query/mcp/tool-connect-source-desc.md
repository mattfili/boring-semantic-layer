Test a connection to a database backend and propose a BSL profile YAML.

Use this when the user wants to:
- Add a new warehouse / database to their semantic layer
- Verify credentials work before persisting them
- Discover what tables exist in a backend

Tests connectivity to the backend, lists available tables (with row counts,
capped at 100), and returns a proposed `profiles.yml` YAML block. The caller
persists the YAML to one of:
- `~/.config/bsl/profiles/<name>.yml` (user-global)
- `<project>/profiles.yml` (project-local)
- inline under `profile:` in `models.yml` (single-server bootstrap)

This tool never writes credentials to disk. ${VAR} literals in
`connection_params` round-trip as literals — they're not expanded at test time.

Parameters:
- `backend`: One of the supported backends (call `list_backends` to see them).
- `profile_name`: Name to use for the new profile.
- `connection_params`: Backend-specific connection params (host, port, etc.).
  May include `${VAR}` literals for credentials that should resolve at runtime.

Returns a dict with:
- `status`: "connected" on success.
- `proposed_profile_yaml`: Ready-to-append profile YAML.
- `available_tables`: List of `{name, row_count, count_error}` (up to 100).
- `truncated`: True if more than 100 tables exist.
- `warning`: Set if `list_tables()` failed after a successful connect.

On connection failure: ToolError with sanitized error (credentials scrubbed).
On unsupported backend or missing ibis extra: ToolError with `pip install` hint.
