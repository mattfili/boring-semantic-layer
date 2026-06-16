List database backends supported by the schema-tools MCP path.

Returns which curated backends are currently installed (importable) and which
are available to install via `pip install ibis-framework[<backend>]`.

Use this before `connect_source` to figure out:
- Whether the user's target backend is supported
- Whether the necessary ibis extra is already installed
- The exact `pip install` command if it isn't

Returns a dict with:
- `installed_backends`: List of backend names currently importable.
- `available_backends`: List of backend names supported but not installed.
- `install_instructions`: Mapping of backend name → `pip install` command.

This call never touches the network or any database. It just reads the
ibis-framework install state.
