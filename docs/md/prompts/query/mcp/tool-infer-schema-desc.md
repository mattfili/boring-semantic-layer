Infer a BSL semantic model from a raw data source — table, parquet, csv, or json.

Use this when the user wants to:
- Bootstrap a new semantic model from data they haven't modeled yet
- Add a new table from the existing warehouse to their semantic layer
- Convert a flat file into a queryable BSL model

Returns a `proposed_yaml` string (just the new model block — no `profile:` header,
safe to append to an existing `models.yml`), per-column classifications with
reasoning, and any potential joins to existing models in the registry. The
caller (agent or plugin) is responsible for reviewing, possibly editing, and
persisting the YAML to disk via the standard Write tool.

This tool never writes to disk and never registers a new model in the running
server. Restart the server after persisting new YAML to pick it up.

Parameters:
- `table_name`: Name to use for the new model (kebab/snake, no whitespace).
- `source`: Either a table name in the connected backend, or a filesystem path.
- `source_type`: One of `"table"`, `"csv"`, `"parquet"`, `"json"`. If omitted,
  inferred from the file extension; defaults to `"table"`.
- `description`: Optional model-level description.
- `profile`: Optional profile name; reserved for future use.

Returns a dict with:
- `proposed_yaml`: Ready-to-append YAML block.
- `column_classifications`: List of `{column, dtype, classification, aggregation,
  is_time_dimension, smallest_time_grain, description, reasoning}`.
- `potential_joins`: List of `{column, matches_model, matches_dimension,
  suggested_type, reasoning}` — empty if no FK-shape columns matched.
