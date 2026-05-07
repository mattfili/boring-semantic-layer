# Schema and Source MCP Tools — Design

**Status**: Approved, ready for implementation plan
**Date**: 2026-05-07
**Branch**: `feat/schema-tools`
**Source brief**: `docs/BSL_FORK_SKILLS_PROMPT.md` (Feature 2)
**Predecessor**: PR #4 (skills as MCP resources, merged)

## 1. Goal

Add three opt-in MCP tools to `MCPSemanticModel` that help bootstrap BSL YAML from raw data and connect new ibis backends. All side-effect-free: tools return strings (proposed YAML, classifications, table listings); the consuming agent/plugin/user writes them to disk.

When the constructor flag is not set (default), behavior is identical to today.

## 2. Operating contract

| Mode | Configuration | Result |
|---|---|---|
| Default (no schema tools) | `MCPSemanticModel(...)` | Identical to today. No new tools registered. |
| Schema tools enabled | `MCPSemanticModel(..., include_schema_tools=True)` | `infer_schema`, `connect_source`, `list_backends` registered |

The flag is independent of the existing `include_domain_context_tool` and `include_add_skill_tool` flags from PR #4. Schema tools have no dependency on `skills_dir` configuration.

## 3. Architecture

Three new files, mirroring the PR #4 split (`skills.py` + `_skill_mcp.py`):

| File | Responsibility |
|---|---|
| `src/boring_semantic_layer/schema_inference.py` | Pure-Python heuristics. Takes an ibis schema → returns `ProposedSchema` dataclass with classifications, potential joins, rendered YAML. No MCP/FastMCP dependency. |
| `src/boring_semantic_layer/agents/backends/_schema_mcp.py` | MCP tool registration. Wires `infer_schema`, `connect_source`, `list_backends` onto an `MCPSemanticModel` instance. |
| `src/boring_semantic_layer/agents/backends/_source_inspection.py` | Connection-test helpers: opens an ibis backend (xorq path → fallback), lists tables with row counts, builds proposed profile YAML. |

Plus three new prompt-description markdown files in `docs/md/prompts/query/mcp/`:

- `tool-infer-schema-desc.md`
- `tool-connect-source-desc.md`
- `tool-list-backends-desc.md`

Bundled into the wheel via the existing `shared-data` mechanism.

### Constructor change to `MCPSemanticModel.__init__` (`mcp.py`)

```python
def __init__(
    self,
    ...,
    include_domain_context_tool: bool = True,   # existing
    include_add_skill_tool: bool = True,         # existing
    include_schema_tools: bool = False,          # NEW: opt-in
    ...
):
    ...
    self._include_schema_tools = include_schema_tools
    ...
    if self._include_schema_tools:
        self._register_schema_tools()
```

### No new optional dependency

The transient in-memory DuckDB used for file-source inference relies on `ibis-framework[duckdb]`, which is already pulled by `examples` and `dev` extras. Documented as a soft requirement; no enforcement.

## 4. Components

### 4.1 `schema_inference.py` (~250 lines)

```python
@dataclass(frozen=True)
class ColumnClassification:
    column: str
    dtype: str
    classification: Literal["dimension", "measure"]   # drives YAML section placement
    aggregation: str | None         # for measures: "sum"/"count"/"mean"/None
    is_time_dimension: bool         # YAML flag; only meaningful when classification == "dimension"
    smallest_time_grain: str | None # YAML field; set iff is_time_dimension
    description: str
    reasoning: str

@dataclass(frozen=True)
class PotentialJoin:
    column: str                    # FK column on the new model
    matches_model: str             # target model name
    matches_dimension: str         # target dimension name
    suggested_type: str            # "one" (default), agent flips if needed
    reasoning: str

@dataclass(frozen=True)
class ProposedSchema:
    table_name: str
    description: str
    columns: list[ColumnClassification]
    potential_joins: list[PotentialJoin]
    proposed_yaml: str             # rendered, ready-to-append

def classify_column(name: str, ibis_dtype) -> ColumnClassification: ...
def find_potential_joins(
    columns: list[ColumnClassification],
    existing_models: Mapping[str, SemanticTable],
) -> list[PotentialJoin]: ...
def render_yaml(proposed: ProposedSchema, profile: str | None) -> str: ...
def infer_schema(
    table_name: str,
    ibis_table,
    existing_models: Mapping[str, SemanticTable],
    *,
    description: str | None = None,
    profile: str | None = None,
) -> ProposedSchema: ...
```

**Classification heuristics** (best-guess, ready-to-drop-in):

| Pattern | classification | aggregation | is_time_dimension | Notes |
|---|---|---|---|---|
| Bool dtype | dimension | — | false | Three-valued semantics preserved by ibis null handling |
| String dtype | dimension | — | false | always |
| Date dtype | dimension | — | true | `smallest_time_grain="TIME_GRAIN_DAY"` |
| Timestamp dtype | dimension | — | true | `smallest_time_grain="TIME_GRAIN_SECOND"` |
| Numeric, name matches `^id$\|.*_id$\|.*_key$\|.*_code$` | dimension | — | false | identifier-shaped numerics never become measures |
| Numeric, name ends `_count\|_total\|_amount\|_sum` | measure | sum | — | |
| Numeric, name ends `_rate\|_pct\|_percent\|_ratio\|_avg\|_mean` | measure | mean | — | |
| Numeric, other | measure | sum | — | default fallback |

`is_entity` is **not** set in v1. Joins in BSL are declarative (`model: + left_on: + right_on: + type:`); `is_entity` is optional metadata and not required for join correctness.

**Description generation**: split snake_case, title-case. Drop common suffixes (`_id` → "ID"). One line per column.

**`find_potential_joins` algorithm** (replaces the misframed `is_entity` heuristic):

1. For each new-schema column matching `*_id|*_key|*_code`:
2. Strip the suffix to get a prefix (`carrier_id` → `carrier`).
3. Scan `existing_models` for a model name matching the prefix in singular/plural form (`carrier`, `carriers`).
4. In the matched model, walk dimensions in priority order to pick the join target:
   1. dim named exactly `id`
   2. dim matching `<model_name>_id`
   3. dim named `code`
   4. first dim
5. Default `suggested_type: "one"` (FK→PK is the overwhelming common case).
6. Surface the matched-prefix and dim-rule in `reasoning` so the agent can flip cardinality if modeling the inverse direction.

**`render_yaml` output contract**: emits **only** the new model block, with no `profile:` header. The output is safely string-appendable to an existing `models.yml`.

### 4.2 `_source_inspection.py` (~150 lines)

```python
def open_backend(profile_config: dict) -> ibis.BaseBackend:
    """Try xorq.vendor.ibis.backends.profiles.Profile(...).get_con() first
    (matches BSL's existing profile.py loader). Fall back to plain
    ibis.<backend>.connect(**params) if xorq doesn't support the backend."""

def list_tables_with_counts(con, *, limit_tables: int = 100) -> list[TableSummary]:
    """SELECT count(*) per table; cap to avoid surprise."""

def build_profile_yaml(profile_name: str, backend: str, params: dict) -> str:
    """Render BSL's existing `type: <backend>` + flat connection field shape.
    Preserve `${VAR}` literals — no expansion at test time."""

def open_transient_duckdb_for_file(
    path: str,
    source_type: Literal["csv", "parquet", "json"],
) -> tuple[ibis.BaseBackend, ibis.Table]:
    """ibis.duckdb.connect(':memory:') + connection.read_<format>(path, table_name='_inferred')."""
```

### 4.3 `_schema_mcp.py` (~220 lines)

Three tool registrations on an `MCPSemanticModel` instance:

| Tool | Tags | Annotations | Description source |
|---|---|---|---|
| `infer_schema` | `{"discovery", "metadata"}` | `READONLY_ANNOTATIONS` | `tool-infer-schema-desc.md` |
| `connect_source` | `{"metadata"}` | `READONLY_ANNOTATIONS` | `tool-connect-source-desc.md` |
| `list_backends` | `{"discovery"}` | `READONLY_ANNOTATIONS` | `tool-list-backends-desc.md` |

All three:

- Use `ToolError` from `fastmcp.exceptions` for failures.
- Accept `ctx: Context | None = None` and call `ctx.info()` / `ctx.report_progress()` at major steps.
- Are read-only by construction. BSL has no `INSERT/UPDATE/DELETE/CREATE` paths anywhere; these tools only call `con.list_tables()`, `con.table(name).schema()`, `con.table(name).count()`, and `con.table(name).limit(N)`. All SELECTs.

`list_backends` uses a static curated list (`duckdb, postgres, snowflake, bigquery, mysql, sqlite, clickhouse`) per the brief. Detection: `importlib.import_module(f"ibis.backends.{b}")`. Static install-instructions map.

## 5. Data flow

### 5.1 `infer_schema(table_name, source, source_type=None, description=None, profile=None)`

```
1. Resolve source_type (explicit > extension inference > default "table")
2. Open backend:
   - "table"     → reuse the MCPSemanticModel's existing connection (from profile)
   - "csv|parquet|json" → ibis.duckdb.connect(":memory:") + read_<format>(...)
3. ibis_table.schema() + ibis_table.limit(20).execute()  (sample frame)
4. For each column → classify_column → ColumnClassification
5. find_potential_joins(columns, self.models) → list[PotentialJoin]
6. render_yaml(...) → string (model block only, no profile: header)
7. Return {proposed_yaml, column_classifications, potential_joins}
8. Drop the transient backend if we created one (try/finally)
```

### 5.2 `connect_source(backend, profile_name, connection_params)`

```
1. Validate backend in supported set; if not → ToolError
2. Try import the ibis extra; if missing → ToolError with pip command
3. Build profile dict: {"type": backend, **connection_params}
4. open_backend(...)  — xorq Profile → ibis fallback
   - on auth/network failure → ToolError(_sanitize_error(...))
5. tables = list_tables_with_counts(con, limit_tables=100)
   - per-table COUNT(*); per-table 5s timeout
   - errors recorded as {row_count: null, count_error: "..."}, do not fail the call
   - if >100 tables → first 100 + truncated: true
6. proposed_profile_yaml = build_profile_yaml(...)  — preserves ${VAR}
7. Return {
       status: "connected",
       proposed_profile_yaml,
       available_tables,
       truncated: bool,           # true if >100 tables existed
       warning: str | None,        # set if list_tables() failed after successful connect
   }
8. Close connection (try/finally)
```

### 5.3 `list_backends()`

```
1. SUPPORTED = ["duckdb", "postgres", "snowflake", "bigquery", "mysql", "sqlite", "clickhouse"]
2. INSTALL_HINTS = {b: f"pip install 'ibis-framework[{b}]'" for b in SUPPORTED}
3. installed, available = [], []
   for b in SUPPORTED:
       try: importlib.import_module(f"ibis.backends.{b}"); installed.append(b)
       except ImportError: available.append(b)
4. Return {installed_backends, available_backends, install_instructions}
```

No DB calls. Synchronous.

## 6. Error handling

### 6.1 `infer_schema`

| Failure | Response |
|---|---|
| `source_type="table"` but table not found | `ToolError` with `con.list_tables()[:10]` hint |
| File not found | `ToolError(f"File not found: {source}")` |
| Unsupported file extension | `ToolError(f"Unsupported file type. Supported: csv, parquet, json")` |
| File parse error | `ToolError(_sanitize_error(...))` |
| Empty schema | `ToolError(f"Source '{source}' has no columns")` |
| `table_name` collides with existing model | `ToolError(...)` — first attempt `ctx.elicit()` for a different name; if unsupported, fall through to error (matches PR #4's `_resolve_model` precedent) |

Classification itself never raises. Ambiguity goes into `reasoning`.

### 6.2 `connect_source`

| Failure | Response |
|---|---|
| Backend not in supported set | `ToolError(f"Backend '{backend}' not supported. Supported: {SUPPORTED}")` |
| Backend supported, ibis extra missing | `ToolError` with `INSTALL_HINTS[backend]` |
| Connection fails | `ToolError(_sanitize_error(...))` |
| `list_tables()` errors after successful connect | Tool succeeds with `available_tables: []` and `warning: "..."` |
| Per-table `COUNT(*)` fails / times out | Table appears with `row_count: null, count_error: "..."` |
| `>100` tables | Returns first 100 with `truncated: true` |

Connection always closed in `try/finally`.

### 6.3 `list_backends`

Effectively cannot fail. `ImportError` is normal flow (classifies a backend as available-but-not-installed).

### 6.4 Credential sanitization

`_sanitize_error(msg, params)` scrubs:

- Any value from `connection_params` appearing verbatim in `msg`
- `password=...`, `secret=...`, `token=...`, `api_key=...`, `Bearer ...` patterns
- URL-shaped strings with embedded credentials (`postgres://user:pass@host/db` → `postgres://***@host/db`)

Used by both `connect_source` and `infer_schema` whenever they wrap external errors.

## 7. End-to-end lifecycle (out of PR scope, in scope for understanding)

This PR ships only the leftmost step of each flow. The rest happens in the consuming plugin layer.

### 7.1 `infer_schema` flow

```
User asks for a model from a CSV
  ↓
Plugin slash command (e.g. /schema-builder, lives in NPO Catalogue plugin)
  ├─ reads file from disk / extracts from PDF (BSL has no role)
  └─ calls infer_schema via MCP ──► BSL returns {proposed_yaml, classifications, potential_joins}
  ↓
Claude (agent) reviews proposed_yaml
  ├─ may ask user about classifications
  ├─ may edit the YAML string
  └─ writes the final YAML via standard Write tool
       ↓
       string-append to existing models.yml (safe because proposed_yaml has no profile: header)
       OR parse-merge-serialize via plugin if structural changes needed
  ↓
Plugin / user restarts BSL server (hot reload is a non-goal)
  ↓
from_yaml() re-runs at startup, new model registered, query_model can hit it
```

### 7.2 `connect_source` flow

```
User asks to connect a Postgres warehouse
  ↓
Plugin slash command (e.g. /connect-source)
  ├─ collects credentials interactively (Claude Code UX)
  └─ calls connect_source via MCP ──► BSL tests connection, lists tables, returns proposed_profile_yaml
  ↓
Claude reviews profile YAML, agent writes to one of:
  - ~/.config/bsl/profiles/<name>.yml      (user-global)
  - <project>/profiles.yml                  (project-local)
  - inline in models.yml under `profile:`   (single-server bootstrap)
  ↓
infer_schema(profile=<new_name>, source=<table>) → bootstrap models against the new connection
```

### 7.3 What this PR does NOT include

- Slash commands — plugin layer
- File extraction (PDF/Excel/docx) — plugin layer
- Hot reload of model definitions — non-goal per brief
- Multi-model YAML merging — caller's responsibility
- Validation roundtrip after write — caller can call `from_yaml()` themselves
- Upstream merge — fork-specific feature

## 8. Testing

| File | Scope |
|---|---|
| `src/boring_semantic_layer/tests/test_schema_inference.py` | Pure-logic tests for `schema_inference.py`. No MCP, no live ibis backend. |
| `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py` | Integration tests through the MCP protocol (`async with Client(mcp)`). Module-scoped DuckDB fixture. |
| `src/boring_semantic_layer/tests/fixtures/sample_tables/inferable.parquet` | Small parquet (~10 rows) for file-source path coverage. |

### 8.1 Unit tests (`test_schema_inference.py`)

- `classify_column` — table-driven test with ~25 dtype × naming-pattern cases
- `find_potential_joins` — empty registry; single-model match; ambiguity (longest-prefix wins); no match
- `render_yaml` — round-trips through `from_yaml`-shaped dict (guarantees we emit loadable YAML)
- Description generation — `total_revenue` → "Total Revenue", `org_id` → "Organization ID"

### 8.2 Integration tests (`test_mcp_schema_tools.py`)

| Test class | Coverage |
|---|---|
| `TestSchemaToolsRegistration` | Default constructor → tools absent. `include_schema_tools=True` → all three present with correct names/tags/annotations. |
| `TestInferSchemaTable` | Inference against an existing table in the profile's connection. |
| `TestInferSchemaFile` | Inference against `inferable.parquet` via transient DuckDB. Asserts the transient connection is closed afterward. |
| `TestInferSchemaJoins` | Two pre-registered models. New schema's `carrier` column → `carriers` model in `potential_joins`. |
| `TestInferSchemaErrors` | Missing table, missing file, unsupported file type, name collision. |
| `TestConnectSourceLocal` | `backend="duckdb"` → `:memory:`. Asserts `proposed_profile_yaml` round-trips through xorq's Profile. |
| `TestConnectSourceCredentialScrub` | Inject a connection raising `Exception("auth failed for user pg_admin password=hunter2")`. Assert `ToolError` message contains neither `hunter2` nor `password=`. |
| `TestConnectSourceTruncation` | Mocked `list_tables` returning 150 names → first 100 + `truncated: true`. |
| `TestListBackends` | `installed_backends` includes `duckdb`. `install_instructions` keys match static map. |
| `TestReadOnlyAnnotations` | Each tool's annotations have `readOnlyHint=True, destructiveHint=False`. |

Test fixtures use unique table names per class (per CLAUDE.md gotcha re: shared module-scoped DuckDB).

### 8.3 Out of scope for v1 tests

- Live Postgres/BigQuery/Snowflake integration. Static-list verification only.
- `ctx.elicit` flow when client doesn't support it. Reuse PR #4 patterns if useful, otherwise skip.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Classification heuristics wrong on real data | All choices surfaced in `reasoning`; agent reviews before persisting; tests cover the pattern grid |
| Transient DuckDB pollutes memory on concurrent calls | `try/finally` closes; `:memory:` is per-connection — no cross-call state |
| `COUNT(*)` slow on huge warehouse tables | 5s per-table timeout, 100-table cap, errors recorded not raised |
| Credentials leak through error messages | `_sanitize_error` helper, tested explicitly |
| `xorq.vendor.ibis` API drift | Connection path already used by BSL — failure here breaks more than this PR |

## 10. Migration

For the consuming server (NPO Catalogue): pass `include_schema_tools=True` to the existing `MCPSemanticModel(...)` call. No other changes required.

The flag is opt-in (default `False`), so unrelated BSL servers see zero behavior change.

## 11. Non-goals

- File parsing (PDF/Excel/docx) — plugin layer
- Writing files to disk — all tools return strings; caller persists
- Executing generated YAML — `infer_schema` returns a proposal, never registers
- Skill-system integration — schema tools are independent of `skills_dir`
- Hot reload of model definitions — restart required after writing new YAML
- Live multi-cloud-warehouse CI testing — covered by static-list checks
- Upstream merge — fork-specific feature
