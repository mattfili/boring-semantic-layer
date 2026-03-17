# boring-semantic-layer

Semantic layer built on ibis. Wraps ibis tables with dimension/measure metadata, then exposes them over MCP (FastMCP 3.0), LangChain agents, or direct Python API.

## Commands

```bash
# Install all extras for development
uv sync --extra dev

# Run tests (use specific paths — no top-level pytest discovery)
uv run pytest src/boring_semantic_layer/tests/ -v              # Core semantic layer
uv run pytest src/boring_semantic_layer/agents/tests/ -v       # MCP + agent backends
uv run pytest src/boring_semantic_layer/chart/tests/ -v        # Chart generation

# Lint and format
uv run ruff check src/
uv run ruff format --check src/

# Build wheel
uv build

# Run examples
uv run python examples/example_mcp.py       # MCP server (blocks on stdio)
uv run python examples/example_mcp_cohort.py # Cohort analysis MCP server
```

## Architecture

```
src/boring_semantic_layer/
├── api.py                  # to_semantic_table(), entity_dimension(), time_dimension()
├── expr.py                 # SemanticTable — the core type (1600+ lines)
├── ops.py                  # 11 immutable operation nodes (@frozen attrs classes, 4500+ lines)
├── query.py                # Query execution, _find_time_dimension()
├── yaml.py                 # from_yaml() / from_config() — YAML model loading
├── profile.py              # Connection/profile management (duckdb, etc.)
├── config.py               # Global options
├── measure_scope.py        # AST for calculated measures
├── graph_utils.py          # BFS dependency graph traversal
├── convert.py              # @convert.register — lower SemanticOps → ibis expressions
├── format.py               # @fmt.register — pretty printing
├── utils.py                # safe_eval() for YAML expressions (AST-validated, no exec)
├── nested_access.py        # Malloy-style automatic nested array access
├── projection_utils.py     # Projection pushdown, TableRequirements
├── serialization/          # to_tagged/from_tagged — xorq roundtrip serialization
├── chart/                  # 5 viz backends: altair, plotly, plotext, echarts, md_parser
│   ├── base.py             # ChartBackend ABC
│   ├── altair_chart.py     # Vega-Lite JSON specs
│   ├── plotly_chart.py     # Interactive/3D charts
│   ├── plotext_chart.py    # Terminal charts (CLI default)
│   ├── echarts/            # ECharts backend (backend.py, interface.py, types.py)
│   ├── echarts_adapter.py  # ECharts adapter layer
│   └── md_parser/          # Dashboard markdown → executed queries
├── agents/
│   ├── backends/
│   │   ├── mcp.py          # MCPSemanticModel (FastMCP 3.0 server)
│   │   └── langgraph.py    # LangGraphBackend (LangChain agent)
│   ├── tools.py            # BSLTools — shared tool definitions for both backends
│   ├── utils/
│   │   ├── prompts.py      # load_prompt() — loads markdown files
│   │   ├── chart_handler.py # generate_chart_with_data() — records + chart output
│   │   └── tokens.py       # Token counting utilities
│   ├── cli.py              # `bsl` CLI entry point
│   └── tests/              # Canonical test location for MCP + agent tests
└── tests/                  # Core semantic layer tests (40+ files)
    ├── fixtures/           # connections.py, datasets.py, sample_tables
    └── integration/        # Malloy integration tests
```

## Core Concepts

### SemanticTable API

```python
from boring_semantic_layer import to_semantic_table

model = (
    to_semantic_table(ibis_table, name="flights", description="Flight data")
    .with_dimensions(
        carrier=lambda t: t.carrier,                    # Simple lambda
        flight_date={                                    # Extended with metadata
            "expr": lambda t: t.flight_date,
            "description": "Departure date",
            "is_time_dimension": True,
            "smallest_time_grain": "day",
        },
    )
    .with_measures(
        flight_count={"expr": lambda t: t.count(), "description": "Total flights"},
        avg_delay=lambda t: t.dep_delay.mean(),
    )
    .join_one(carriers_model, lambda f, c: f.carrier == c.code)  # 1:1 join
)

result = model.query(
    dimensions=["carrier"], measures=["flight_count"],
    filters=[{"field": "carrier", "operator": "=", "value": "AA"}],
    order_by=[["flight_count", "desc"]], limit=10,
    time_grain="TIME_GRAIN_MONTH", time_range={"start": "2024-01-01", "end": "2024-12-31"},
)
```

### Operation Pipeline

All operations are **immutable** (attrs `@frozen`). Method chaining returns new SemanticTable instances:
```
SemanticTable → filter → group_by → aggregate → order_by → limit → execute
```

Lowering to ibis: `convert.py` dispatches `@convert.register(SemanticOp)` to translate each op to ibis. Call `to_untagged(expr)` to get a plain ibis expression for execution.

### Joins and Prefixes

After a join, dimensions/measures get model-name prefixes:
- `flights.carrier`, `flights.flight_count`, `carriers.name`, `carriers.carrier_count`
- Three join types: `join_one()`, `join_many()`, `join_cross()`
- Predicates accept: lambda, string column name, Deferred, or list of strings

### YAML Loading

```python
from boring_semantic_layer import from_yaml
models = from_yaml("models.yml", profile="my_db", profile_path="profiles.yml")
```

YAML expressions use `_.column` syntax (Deferred), parsed through `safe_eval()` with AST validation.

## MCP Server (FastMCP 3.0)

`MCPSemanticModel` subclasses `FastMCP` and registers:
- **6 tools**: `list_models`, `get_model`, `get_time_range`, `query_model`, `search_dimension_values`, `summarize_results`
- **3 resources**: `semantic://models`, `semantic://models/{name}`, `semantic://models/{name}/time-range` — all with `Annotations(audience=["assistant"], priority=...)` from `mcp.types`
- **3 prompts**: `query_guide`, `model_exploration_guide`, `getting_started`

All tool descriptions load from `docs/md/prompts/query/mcp/*.md` (23 files). These are bundled into the wheel via `shared-data`.

### FastMCP 3.0+ Features

- **ToolError**: All tools raise `ToolError` (from `fastmcp.exceptions`) instead of `ValueError` for proper MCP error propagation
- **ToolAnnotations**: Every tool has `readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=False` via shared `_READONLY_ANNOTATIONS` constant
- **Tool Tags**: `{"discovery"}`, `{"metadata"}`, `{"query"}`, `{"analysis"}` — used for tool categorization
- **Context integration**: `ctx: Context | None = None` on `get_model`, `get_time_range`, `query_model`, `search_dimension_values`, `summarize_results` — provides `ctx.info()`, `ctx.report_progress()`
- **Session state**: `query_model` stores `last_query` and `last_result` via `ctx.set_state()`; `summarize_results` reads them via `ctx.get_state()`
- **ctx.elicit()**: `_resolve_model()` and `search_dimension_values` try interactive model/dimension resolution via elicitation before raising `ToolError` — gracefully degrades when client doesn't support it
- **ctx.sample()**: `summarize_results` tool uses `ctx.sample()` to generate NL summaries of query results via the connected LLM
- **Resource annotations**: All resources have `Annotations(audience=["assistant"], priority=float)` from `mcp.types`
- **CodeMode**: Optional `code_mode=True` constructor parameter — requires `fastmcp[code-mode]>=3.1.0` (install via `boring-semantic-layer[mcp-code-mode]`). Uses `_build_code_mode_transforms()` factory with `GetTags`, `Search`, `GetSchemas` discovery tools

## Optional Dependencies

| Extra | What it provides | Gated feature |
|-------|-----------------|---------------|
| `mcp` | `fastmcp>=3.0.0` | `MCPSemanticModel` |
| `mcp-code-mode` | `fastmcp[code-mode]>=3.1.0` | CodeMode transforms |
| `agent` | langchain, rich, plotext | `LangGraphBackend`, CLI |
| `viz-altair` | altair, vl-convert | Altair chart backend |
| `viz-plotly` | plotly, kaleido | Plotly chart backend |
| `viz-plotext` | plotext | Terminal chart backend |
| `examples` | xorq[duckdb], duckdb | Example scripts |
| `dev` | All of the above + test/lint tools | Development |

Lazy imports in `__init__.py` via `__getattr__` — `MCPSemanticModel` and `LangGraphBackend` raise helpful `ImportError` if extras aren't installed.

## Test Patterns

- **Module-scoped** `con` fixture: shared DuckDB in-memory connection
- **Module-scoped** `sample_models` fixture: reusable SemanticTable instances
- MCP tests use `async with Client(mcp) as client` — always go through MCP protocol, never internal APIs
- Use `@pytest.mark.asyncio` for async tests
- Use unique table names per test class to avoid DuckDB table clobbering (module-scoped `con` is shared)

## Gotchas

- **Table name collisions**: Tests sharing a module-scoped DuckDB `con` will clobber each other if they `create_table("flights", ...)` with different schemas. Always use unique names per test class.
- **Deferred recursion**: `dim.expr(tbl)` returns a Deferred that causes infinite recursion in `tbl.aggregate()`. Access columns directly via `tbl[col_name]` instead (see `get_time_range` implementation).
- **Joined dimension column lookup**: After a join, dimension names are prefixed (`flights.flight_date`) but the underlying table column is just `flight_date`. Use `.split(".")[-1]` when accessing the raw ibis column.
- **Pre-commit hooks**: Commits auto-run ruff lint+format, codespell, `uv-lock`, and `uv-export` (regenerates `requirements-dev.txt`). If a hook fails, the commit didn't happen — fix and create a new commit.
- **Prompt file resolution**: `_get_prompts_dir()` checks the installed wheel location (`sys.prefix/share/bsl/prompts/`) first, falls back to `docs/md/prompts/` in dev. If prompts seem missing, check which path is resolving.
- **`_parse_json_string` validator**: Claude Desktop sends JSON-stringified arrays as strings (`'["a","b"]'`). The `BeforeValidator` on tool parameters handles this transparently.

## Code Style

- **Ruff**: `line-length = 100`, target `py310`
- **isort**: `known-first-party = ["boring_semantic_layer"]`
- **Immutability**: Operation nodes use attrs `@frozen` — never mutate, always return new instances
- **Functional patterns**: Heavy use of currying, composition, `returns` library (Result/Maybe monads)
- **Lazy imports**: Optional features gated behind `try/except` and `__getattr__`
- **No type annotations** required in test files (ruff per-file-ignores)
- **Dispatch pattern**: `convert.py` and `format.py` use `@register` dispatchers — add new operations by registering converters
