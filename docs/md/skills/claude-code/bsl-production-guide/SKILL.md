---
name: bsl-production-guide
description: Implement and deploy Boring Semantic Layer (BSL) in production — connect a database, infer/author semantic models, joins, query, serve over MCP, and harden with schema-per-tenant multi-tenancy and auth. Use when standing up, deploying, integrating, or securing a BSL service (especially multi-tenant MCP servers).
---

# BSL Production Guide

End-to-end guide for taking the Boring Semantic Layer (BSL) into production:
connect a database, build semantic models, query them, serve them over MCP,
and — most importantly — **harden the server for multi-tenant production**.

This is the *deployer's* overview. For deep authoring/querying mechanics use
the two focused skills and don't duplicate them here:

- **`bsl-model-builder`** — dimensions, measures, joins, YAML model authoring.
- **`bsl-query-expert`** — query syntax, filters, time grains, charts (at runtime
  against a live server).

## The production stack (mental model)

```
raw data / warehouse
      │  (1) connect            profiles.yml · get_connection() · ibis backend
      ▼
ibis connection
      │  (2) model              to_semantic_table / from_yaml / infer_schema
      ▼
SemanticTable(s)               dimensions · measures · joins
      │  (3) serve             MCPSemanticModel  (FastMCP 3.0)
      ▼
MCP server  ──(4) harden──►    TenancyConfig + auth  (schema-per-tenant)
      │
      ▼
agent / LLM client             query_model, get_model, search_dimension_values, …
```

Install the pieces you need (extras are additive):

```bash
pip install 'boring-semantic-layer[mcp]'            # MCPSemanticModel server
pip install 'boring-semantic-layer[mcp-code-mode]'  # + CodeMode transforms
pip install 'boring-semantic-layer[examples]'       # duckdb + xorq for local dev
```

---

## 1. Connect a database

BSL runs on any [ibis](https://ibis-project.org/) backend (DuckDB, Postgres,
Snowflake, BigQuery, …). Four ways to get a connection, in increasing order of
production-readiness:

**a. Pass an ibis connection directly** (simplest, good for embedding):

```python
import ibis
con = ibis.duckdb.connect("warehouse.ddb")
tbl = con.table("flights")
```

**b. Profiles file** (recommended for production — keeps credentials out of code):

```yaml
# profiles.yml
my_db:
  type: duckdb
  database: warehouse.ddb
```

```python
from boring_semantic_layer import from_yaml
models = from_yaml("models.yml", profile="my_db", profile_path="profiles.yml")
```

Profiles resolve from (first match wins): an explicit `profile_path`,
`~/.config/bsl/profiles/<name>.yml`, then `<project>/profiles.yml`.

**c. Environment variables** (containers / CI):

```bash
export BSL_PROFILE=my_db
export BSL_PROFILE_FILE=/etc/bsl/profiles.yml
```

```python
from boring_semantic_layer.profile import get_connection
con = get_connection()  # reads BSL_PROFILE / BSL_PROFILE_FILE
```

**d. `connect_source` MCP tool** — when an agent should add a new backend at
runtime. Tests connectivity, lists tables (row counts capped at 100), and
returns a proposed `profiles.yml` block for the caller to persist. Enabled via
`include_schema_tools=True` (see §5).

---

## 2. Generate / author models

A `SemanticTable` wraps an ibis table with dimension/measure metadata. Three
authoring paths:

**a. Python API** (see `bsl-model-builder` for the full surface):

```python
from boring_semantic_layer import to_semantic_table

flights = (
    to_semantic_table(tbl, name="flights", description="Flight records")
    .with_dimensions(
        carrier=lambda t: t.carrier,
        flight_date={"expr": lambda t: t.flight_date, "is_time_dimension": True,
                     "smallest_time_grain": "day"},
    )
    .with_measures(
        flight_count={"expr": lambda t: t.count(), "description": "Total flights"},
        avg_delay=lambda t: t.dep_delay.mean(),
    )
    .join_one(carriers, lambda f, c: f.carrier == c.code)   # join_one / join_many / join_cross
)
```

After a join, fields are model-prefixed: `flights.carrier`, `carriers.name`.

**b. YAML** (recommended for production — version-controlled, reviewable):

```python
from boring_semantic_layer import from_yaml
models = from_yaml("models.yml", profile="my_db", profile_path="profiles.yml")
flights = models["flights"]
```

YAML uses unbound `_.column` syntax (not lambdas), parsed through an
AST-validated `safe_eval()` — no `exec`. See `bsl-model-builder` for YAML shape.

**c. `infer_schema` MCP tool** — bootstrap a model from raw data (table, parquet,
csv, json). Returns a `proposed_yaml` block (no `profile:` header, safe to append
to `models.yml`), per-column classifications with reasoning, and candidate joins
to existing models. The caller reviews and writes the YAML. Enabled via
`include_schema_tools=True`.

---

## 3. Query

Models are queried by method chaining or the structured `query()` form (the MCP
`query_model` tool uses the structured form). Full syntax lives in
`bsl-query-expert`; the essentials:

```python
import ibis

# Chained — order_by takes a column name (asc) or ibis.desc(...) (desc)
(flights
    .filter(lambda t: t.carrier == "AA")
    .group_by("flight_date")
    .aggregate("flight_count", "avg_delay")
    .order_by(ibis.desc("flight_count"))
    .limit(10)
    .execute())

# Structured (programmatic / what query_model receives) — order_by is [[field, dir]]

flights.query(
    dimensions=["carrier"], measures=["flight_count"],
    filters=[{"field": "carrier", "operator": "=", "value": "AA"}],
    order_by=[["flight_count", "desc"]], limit=10,
    time_grain="TIME_GRAIN_MONTH",
    time_range={"start": "2024-01-01", "end": "2024-12-31"},
)
```

`aggregate()` takes measure **names as strings**. Use `ibis.cases()` (plural) for
conditionals, `t.all(t.measure)` for percent-of-total, `.mutate()` after
`.order_by()` for window functions.

---

## 4. Serve over MCP

`MCPSemanticModel` subclasses FastMCP 3.0 and exposes the models to any MCP
client (Claude, Pydantic AI, etc.):

```python
from boring_semantic_layer import MCPSemanticModel

mcp = MCPSemanticModel(
    models={"flights": flights},          # or a SemanticModelBundle from from_yaml
    name="Flights MCP",
    include_schema_tools=False,           # set True to expose infer_schema/connect_source/list_backends
    code_mode=False,                      # set True for CodeMode transforms (needs [mcp-code-mode])
)
mcp.run(transport="http")                 # or "stdio" for a single-tenant local server
```

It registers 6 tools (`list_models`, `get_model`, `get_time_range`,
`query_model`, `search_dimension_values`, `summarize_results`), 3 resources
(`semantic://models…`), and 3 prompts. All tools are read-only and raise
`ToolError`. If your YAML configures `skills_dir`, domain skills are served as
MCP resources and a `get_domain_context` tool is registered automatically.

For a **single-tenant** server this is all you need. For **multi-tenant** —
isolating each customer's data behind their token — continue to §5.

---

## 5. ★ Production hardening: schema-per-tenant multi-tenancy

> **The property:** a request authenticated with tenant T's token can see and
> query **only** schema T. One shared runtime, N tenants, isolation enforced
> per request from the caller's verified token.

This is the security-hardened surface. Enable it by passing `tenancy=` and an
`auth=` verifier, and making `models` a **factory** `(schema) -> mapping`:

```python
from boring_semantic_layer import MCPSemanticModel, TenancyConfig, to_semantic_table
from fastmcp.server.auth.providers.jwt import JWTVerifier

def build_models(schema: str):
    """Build the model set bound to ONE tenant's schema.

    Shares one engine-level connection across schemas. If you instead open a
    connection PER schema, close it in TenancyConfig.on_evict (see below).
    """
    tbl = con.table("flights", database=schema)
    return {
        "flights": to_semantic_table(tbl, name="flights")
        .with_dimensions(carrier=lambda t: t.carrier)
        .with_measures(flight_count=lambda t: t.count())
    }

mcp = MCPSemanticModel(
    models=build_models,                      # factory, NOT a static mapping
    tenancy=TenancyConfig(
        schema_claim="schema",                # token claim holding the tenant schema
        allowed_schemas=frozenset({"tenant_alpha", "tenant_beta"}),  # see "Allowlist"
        max_cached_tenants=128,               # LRU size for per-tenant model mappings
        on_query=lambda event: audit_log(event),   # per-tenant audit sink (see below)
        on_evict=lambda schema, models: None,       # close per-schema resources here
    ),
    auth=JWTVerifier(jwks_uri="https://idp.example.com/.well-known/jwks.json"),
)

mcp.run(transport="http")   # STDIO is REFUSED in multi-tenant mode (see below)
```

### How isolation is enforced

- **Token claim → schema.** Every request resolves its schema from
  `get_access_token().claims[schema_claim]`. The claim is validated as a real
  schema identifier; a missing/malformed claim raises `ToolError` to the caller.
- **Per-request model factory.** Each request only ever sees the mapping built
  for *its* schema — every table reference is created from that mapping, so a
  request cannot name another tenant's schema.
- **HTTP transport is mandatory; STDIO is refused.** Access tokens only exist on
  HTTP transports. Over STDIO `get_access_token()` is `None` and every tenant
  check would silently pass — so the server **raises at startup** if you try to
  run multi-tenant over `stdio` (or the default `None`). Auth silently off must
  never mean tenancy silently off. Run with `transport="http"` behind TLS.

### Allowlist (defense-in-depth) — set it in production

`allowed_schemas` rejects any claim resolving to a schema outside the set, even
if the token is otherwise valid. Verified tokens make claims trustworthy, but
the allowlist is cheap insurance against an upstream token-minting bug. Omitting
it logs a warning at startup. **Always set it in production.**

### Auth verifier: production vs tests

- **Production:** `JWTVerifier` against your identity provider's JWKS endpoint.
  Unauthenticated requests die at the transport layer, so your checks
  differentiate *tenants*, not *authed-vs-not*.
- **Tests/demos only:** `StaticTokenVerifier` with hardcoded tokens. Never ship it.

### Audit logging

`on_query` receives one dict per **successful** tool call:
`{tenant_schema, tool, …detail}` (model, dimensions, measures, rowcount). Failed
calls raise before the audit point, so this is a data-*access* log, not an
attempt log. The callback may be sync or async; a broken sink is logged, never
allowed to take the query path down.

### Per-schema resource cleanup

The tenant model cache is an LRU of `max_cached_tenants`. If `build_models`
opens a connection **per schema**, wire `on_evict(schema, models)` to close it
on eviction — otherwise it lingers until GC. Factories that share one
engine-level connection (as above) don't need it.

### Caching caveat (xorq)

If you enable xorq result caching, the cache key **must include the tenant
schema** (identical view shapes across schemas make cross-tenant collisions
certain, not rare) **and the view build/version stamp** (re-emitted views serve
stale projections after an upgrade). A cache keyed only on query shape is a
cross-tenant data leak. Audit every cache-key path for both.

### Production checklist

- [ ] `models` is a factory `(schema) -> mapping`; `tenancy=` and `auth=` both set.
- [ ] `allowed_schemas` is set (no startup warning).
- [ ] `auth=JWTVerifier(...)` against real JWKS — **not** `StaticTokenVerifier`.
- [ ] Server runs `transport="http"` behind TLS; STDIO path proven to be refused.
- [ ] `on_query` audit sink wired and monitored.
- [ ] `on_evict` closes per-schema connections (or factory shares one connection).
- [ ] `max_cached_tenants` sized for your tenant count + memory budget.
- [ ] Any xorq cache key includes tenant schema **and** view build stamp.
- [ ] A cross-tenant denial test exists: token A attempting schema B fails.

A complete runnable example lives at `examples/example_mcp_multitenant.py`
(two DuckDB schemas standing in for schema-per-tenant Postgres).

---

## 6. Deploy

- **Extras:** `[mcp]` for the server, `[mcp-code-mode]` for CodeMode, `[examples]`
  for local DuckDB/xorq, `[viz-altair|viz-plotly|viz-plotext]` for charts.
- **Run:** `mcp.run(transport="http")` (multi-tenant) or `transport="stdio"`
  (single-tenant local). Put HTTP behind TLS and your auth proxy.
- **Config out of code:** drive connections via `profiles.yml` +
  `BSL_PROFILE`/`BSL_PROFILE_FILE`; never inline credentials.

---

## Reference documentation

- **Getting Started**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/getting-started.md
- **Semantic Tables**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/semantic-table.md
- **YAML Configuration**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/yaml-config.md
- **Profiles (connections)**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/profile.md
- **Composing / Joins**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/compose.md
- **Query Methods**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/query-methods.md
- **Window Functions**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/windowing.md
- **Charting Overview**: https://github.com/boringdata/boring-semantic-layer/blob/main/docs/md/doc/charting.md
