# Schema-Level Tenant Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A request authenticated with tenant T's token can see and query **only** schema T — multi-tenant Postgres, schema-per-tenant, identical view shapes, one shared `MCPSemanticModel` runtime.

**Architecture:** A new opt-in `TenancyConfig` on `MCPSemanticModel`. In tenant mode, `models` is a factory callable `(schema: str) -> Mapping[str, SemanticTable]`; every tool/resource resolves the tenant schema from the request's access-token claims (`get_access_token().claims`) and fetches per-tenant models from a schema-keyed LRU cache. STDIO is refused in tenant mode (no tokens → checks silently pass). Schema tools are admin-scope-gated via component-level `auth=require_scopes(...)`. An optional audit callback logs `(tenant, tool, query, rowcount)`.

**Tech Stack:** FastMCP 3.2.4 (`fastmcp.server.auth`, `fastmcp.server.dependencies.get_access_token`, `StaticTokenVerifier`, client `BearerAuth`), ibis (`con.table(name, database=schema)`), DuckDB schemas in tests as a stand-in for Postgres schemas.

---

## Validated facts (do not re-derive; these were empirically verified on 2026-06-10 against the installed deps)

1. **fastmcp 3.2.4 is installed.** All of these import: `fastmcp.server.auth.{AuthContext, AccessToken, require_scopes}`, `fastmcp.server.dependencies.get_access_token`, `fastmcp.server.middleware.AuthMiddleware`, `fastmcp.server.auth.providers.jwt.StaticTokenVerifier`, `fastmcp.client.auth.BearerAuth`.
2. **In-memory transport does NOT support auth.** `Client(mcp, auth=BearerAuth(...))` raises `ValueError: This transport does not support auth`. Despite what FastMCP docs examples show, tests MUST use the ASGI harness below.
3. **The ASGI harness works.** `StreamableHttpTransport("http://test/mcp", auth=BearerAuth(token), httpx_client_factory=<ASGITransport factory>)` against `mcp.http_app()` wrapped in `app.router.lifespan_context(app)` delivers: claims via `get_access_token()`, `require_scopes` list-filtering AND call denial, 401 for bad/missing tokens. URL must be `/mcp` (no trailing slash — trailing slash 307-redirects and the MCP client fails on it).
4. **The MCP `StreamableHTTPSessionManager` can only `run()` once per app instance.** Build a fresh `http_app()` per client context in tests.
5. **`StaticTokenVerifier` claims:** extra keys in the token dict (e.g. `"schema": "tenant_a"`) surface in `token.claims`.
6. **ibis + DuckDB schema-qualified access works:** `con.table("flights", database="tenant_a")` after `CREATE SCHEMA tenant_a` — same API shape as Postgres schemas.
7. **`FastMCP.run_async` signature:** `(self, transport: Transport | None = None, show_banner: bool | None = None, **transport_kwargs)`. `transport=None` defaults to STDIO.
8. **Xorq cache audit result:** the MCP query path performs **no** xorq caching today. `aggregate_cache_storage` only applies in the `to_tagged()` serialization path (`serialization/__init__.py`), which no MCP tool calls. The only new cache this PR introduces is the tenant-model LRU, which is keyed by schema. The cross-tenant query test (Task 5) guards the end-to-end property. State this in the PR body.
9. **`self.models` read sites in `mcp.py`** (all inside per-request closures, so a per-request resolver swap covers everything): `list_models` body, `get_model`, `get_time_range`, `query_model`, `search_dimension_values` (all via `resolve_model(self.models, ...)`), and the three resource functions in `_register_resources`. `build_domain_context` (skills) also reads it, but tenant mode has no skills (factory ≠ bundle), so `_has_skills` is False and that tool is never registered.

## Out of scope (by design — record in PR body)

- Per-tenant **skills/resources** (tenant mode disables bundle skills; all components are shared, only data is tenant-scoped).
- SingleRun-side contract wrapper tools and semantic models (downstream's job per SingleRun ADR-0018).
- `LangGraphBackend` and the HTTP API `server/` backend — MCP tenancy only.

## Branch and PR

- Branch `feat/tenant-provisioning` off `main` (NOT off `feat/schema-tools` — that PR is still open; this work is independent. Task 7 touches `_schema_mcp.py` which exists on `main`? **No — it does not.** `_schema_mcp.py` only exists on `feat/schema-tools`. See Task 7 for how to handle this).
- PR: `gh pr create --repo mattfili/boring-semantic-layer --base main` (never the upstream `boringdata` parent).
- Pre-commit hooks run ruff lint+format, codespell, uv-lock, uv-export. If a hook fails, the commit didn't happen — fix and re-commit.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/boring_semantic_layer/agents/backends/_tenancy.py` | Create | Pure tenancy logic: `TenancyConfig`, `resolve_tenant_schema()`, `TenantModelCache`, `emit_audit()`. No FastMCP server imports beyond `ToolError`. |
| `src/boring_semantic_layer/agents/backends/mcp.py` | Modify | Constructor wiring + validation, `_models_for_request()`, per-request reads, `run_async` STDIO refusal, audit emission, schema-tools gating. |
| `src/boring_semantic_layer/__init__.py` | Modify | Lazy export of `TenancyConfig`. |
| `src/boring_semantic_layer/agents/tests/test_tenancy_unit.py` | Create | Unit tests for `_tenancy.py` (no MCP protocol). |
| `src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py` | Create | Protocol-level property tests via the ASGI harness. |
| `examples/example_mcp_multitenant.py` | Create | Runnable multi-tenant example over DuckDB schemas. |

---

### Task 1: `TenancyConfig` + `resolve_tenant_schema`

**Files:**
- Create: `src/boring_semantic_layer/agents/backends/_tenancy.py`
- Test: `src/boring_semantic_layer/agents/tests/test_tenancy_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the tenancy module — pure logic, no MCP protocol."""

import pytest
from fastmcp.exceptions import ToolError

from boring_semantic_layer.agents.backends._tenancy import (
    TenancyConfig,
    resolve_tenant_schema,
)


class FakeToken:
    """Minimal stand-in for fastmcp AccessToken (only .claims is read)."""

    def __init__(self, claims):
        self.claims = claims


class TestResolveTenantSchema:
    def test_resolves_schema_from_default_claim(self):
        config = TenancyConfig()
        token = FakeToken({"schema": "tenant_a"})
        assert resolve_tenant_schema(token, config) == "tenant_a"

    def test_resolves_schema_from_custom_claim(self):
        config = TenancyConfig(schema_claim="tenant")
        token = FakeToken({"tenant": "tenant_b"})
        assert resolve_tenant_schema(token, config) == "tenant_b"

    def test_no_token_raises(self):
        with pytest.raises(ToolError, match="authenticated HTTP transport"):
            resolve_tenant_schema(None, TenancyConfig())

    def test_missing_claim_raises(self):
        token = FakeToken({"sub": "someone"})
        with pytest.raises(ToolError, match="missing the 'schema' claim"):
            resolve_tenant_schema(token, TenancyConfig())

    def test_non_string_claim_raises(self):
        token = FakeToken({"schema": 42})
        with pytest.raises(ToolError, match="missing the 'schema' claim"):
            resolve_tenant_schema(token, TenancyConfig())

    @pytest.mark.parametrize(
        "bad",
        ["tenant-a; DROP TABLE x", "a.b", 'a"b', "1tenant", "", "tenant a"],
    )
    def test_invalid_identifier_raises(self, bad):
        token = FakeToken({"schema": bad})
        with pytest.raises(ToolError, match="not a valid schema identifier|missing the"):
            resolve_tenant_schema(token, TenancyConfig())

    def test_allowlist_blocks_unknown_schema(self):
        config = TenancyConfig(allowed_schemas=frozenset({"tenant_a"}))
        token = FakeToken({"schema": "tenant_z"})
        with pytest.raises(ToolError, match="not among the allowed schemas"):
            resolve_tenant_schema(token, config)

    def test_allowlist_permits_known_schema(self):
        config = TenancyConfig(allowed_schemas=frozenset({"tenant_a"}))
        token = FakeToken({"schema": "tenant_a"})
        assert resolve_tenant_schema(token, config) == "tenant_a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` on `_tenancy`.

- [ ] **Step 3: Write the implementation**

```python
"""Schema-per-tenant provisioning for the MCP backend.

Pure tenancy logic: resolve the tenant schema from a request's access-token
claims, cache per-tenant model mappings, and emit audit events. Kept free of
server wiring so every rule here is unit-testable without the MCP protocol.
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

# Unquoted SQL identifier shape (Postgres/DuckDB). Claims come from tokens we
# verify but do not mint — never interpolate an unvalidated claim near SQL.
_SCHEMA_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class TenancyConfig:
    """Configuration for schema-per-tenant request scoping.

    Attributes:
        schema_claim: Token claim holding the tenant's schema name.
        allowed_schemas: Optional allowlist; when set, claims resolving to a
            schema outside it are rejected even if well-formed.
        admin_scope: OAuth scope required for admin-only components
            (e.g. schema tools) when tenancy is enabled.
        max_cached_tenants: LRU size for per-tenant model mappings.
        on_query: Optional audit callback (sync or async) receiving one dict
            per audited tool call: tenant_schema, tool, and tool-specific
            detail such as model/dimensions/measures/rowcount.
    """

    schema_claim: str = "schema"
    allowed_schemas: frozenset[str] | None = None
    admin_scope: str = "bsl:admin"
    max_cached_tenants: int = 32
    on_query: Callable[[dict[str, Any]], Any] | None = None


def resolve_tenant_schema(token: Any, config: TenancyConfig) -> str:
    """Resolve and validate the tenant schema from an access token.

    Raises ToolError (surfaced to the MCP caller) when the request carries no
    token, the claim is absent/malformed, or the schema is not allowlisted.
    """
    if token is None:
        raise ToolError(
            "This server is multi-tenant and requires an authenticated HTTP "
            "transport; no access token is present on this request."
        )
    schema = token.claims.get(config.schema_claim)
    if not isinstance(schema, str) or not schema:
        raise ToolError(
            f"Access token is missing the '{config.schema_claim}' claim "
            "required for tenant resolution."
        )
    if not _SCHEMA_IDENT.match(schema):
        raise ToolError("Tenant schema claim is not a valid schema identifier.")
    if config.allowed_schemas is not None and schema not in config.allowed_schemas:
        raise ToolError("Tenant schema is not among the allowed schemas for this server.")
    return schema
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -v`
Expected: 8+ PASS (parametrized identifier cases expand).

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/_tenancy.py src/boring_semantic_layer/agents/tests/test_tenancy_unit.py
git commit -m "feat(tenancy): TenancyConfig + claim-based tenant schema resolution"
```

---

### Task 2: `TenantModelCache`

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/_tenancy.py`
- Test: `src/boring_semantic_layer/agents/tests/test_tenancy_unit.py`

- [ ] **Step 1: Write the failing tests** (append to `test_tenancy_unit.py`)

```python
from boring_semantic_layer.agents.backends._tenancy import TenantModelCache


class TestTenantModelCache:
    def test_factory_called_once_per_schema(self):
        calls = []

        def factory(schema):
            calls.append(schema)
            return {"m": schema}

        cache = TenantModelCache(maxsize=4)
        assert cache.get("tenant_a", factory) == {"m": "tenant_a"}
        assert cache.get("tenant_a", factory) == {"m": "tenant_a"}
        assert calls == ["tenant_a"]

    def test_distinct_schemas_get_distinct_models(self):
        cache = TenantModelCache(maxsize=4)
        a = cache.get("tenant_a", lambda s: {"m": s})
        b = cache.get("tenant_b", lambda s: {"m": s})
        assert a != b

    def test_lru_eviction_beyond_maxsize(self):
        calls = []

        def factory(schema):
            calls.append(schema)
            return {"m": schema}

        cache = TenantModelCache(maxsize=2)
        cache.get("t1", factory)
        cache.get("t2", factory)
        cache.get("t3", factory)  # evicts t1
        cache.get("t1", factory)  # rebuild
        assert calls == ["t1", "t2", "t3", "t1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -k Cache -v`
Expected: FAIL with `ImportError: cannot import name 'TenantModelCache'`.

- [ ] **Step 3: Implement** (append to `_tenancy.py`)

```python
class TenantModelCache:
    """LRU cache of per-tenant model mappings, keyed by schema name.

    The lock guards the OrderedDict only; the factory runs outside it (a slow
    factory must not block other tenants — a rare duplicate build is fine).
    """

    def __init__(self, maxsize: int = 32):
        self._maxsize = maxsize
        self._cache: OrderedDict[str, Mapping[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(
        self,
        schema: str,
        factory: Callable[[str], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Return cached models for ``schema``, building via ``factory`` on miss."""
        with self._lock:
            if schema in self._cache:
                self._cache.move_to_end(schema)
                return self._cache[schema]
        models = factory(schema)
        with self._lock:
            self._cache[schema] = models
            self._cache.move_to_end(schema)
            while len(self._cache) > self._maxsize:
                evicted, _ = self._cache.popitem(last=False)
                logger.info("tenancy: evicted cached models for schema %s", evicted)
        return models
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(tenancy): schema-keyed LRU cache for per-tenant models"
```

---

### Task 3: `emit_audit`

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/_tenancy.py`
- Test: `src/boring_semantic_layer/agents/tests/test_tenancy_unit.py`

- [ ] **Step 1: Write the failing tests** (append to `test_tenancy_unit.py`)

```python
import pytest

from boring_semantic_layer.agents.backends._tenancy import emit_audit


class TestEmitAudit:
    @pytest.mark.asyncio
    async def test_sync_callback_receives_event(self):
        events = []
        config = TenancyConfig(on_query=events.append)
        await emit_audit(config, {"tenant_schema": "t_a", "tool": "query_model"})
        assert events == [{"tenant_schema": "t_a", "tool": "query_model"}]

    @pytest.mark.asyncio
    async def test_async_callback_awaited(self):
        events = []

        async def sink(event):
            events.append(event)

        config = TenancyConfig(on_query=sink)
        await emit_audit(config, {"tool": "query_model"})
        assert events == [{"tool": "query_model"}]

    @pytest.mark.asyncio
    async def test_no_callback_is_noop(self):
        await emit_audit(TenancyConfig(), {"tool": "query_model"})

    @pytest.mark.asyncio
    async def test_callback_failure_logged_not_raised(self, caplog):
        def boom(event):
            raise RuntimeError("sink down")

        config = TenancyConfig(on_query=boom)
        with caplog.at_level("WARNING"):
            await emit_audit(config, {"tool": "query_model"})
        assert any("audit callback failed" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -k Audit -v`
Expected: FAIL with `ImportError: cannot import name 'emit_audit'`.

- [ ] **Step 3: Implement** (append to `_tenancy.py`)

```python
async def emit_audit(config: TenancyConfig, event: dict[str, Any]) -> None:
    """Deliver one audit event to the configured callback, if any.

    Audit failures are logged, never raised — a broken sink must not take the
    query path down. (Logged loudly so the drop is visible, not silent.)
    """
    if config.on_query is None:
        return
    try:
        result = config.on_query(event)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.warning("tenancy: audit callback failed for event %r", event, exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(tenancy): emit_audit helper with sync/async callback support"
```

---

### Task 4: Constructor wiring + validation in `MCPSemanticModel`

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/mcp.py` (constructor, ~lines 164–219)
- Test: `src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Protocol-level tenancy tests for MCPSemanticModel.

In-memory FastMCP transport does NOT support auth (verified on fastmcp
3.2.4), so these tests drive a real Streamable HTTP app in-process via
httpx.ASGITransport. The MCP session manager only runs once per app
instance, so a fresh http_app() is built per client context.
"""

import pandas as pd
import pytest
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

import ibis
from boring_semantic_layer import to_semantic_table
from boring_semantic_layer.agents.backends._tenancy import TenancyConfig
from boring_semantic_layer.agents.backends.mcp import MCPSemanticModel

VERIFIER_TOKENS = {
    "token-alpha": {"client_id": "tenant-alpha", "scopes": ["bsl:read"], "schema": "tenant_alpha"},
    "token-beta": {"client_id": "tenant-beta", "scopes": ["bsl:read"], "schema": "tenant_beta"},
    "token-admin-alpha": {
        "client_id": "admin",
        "scopes": ["bsl:read", "bsl:admin"],
        "schema": "tenant_alpha",
    },
    "token-no-claim": {"client_id": "no-claim", "scopes": ["bsl:read"]},
    "token-evil": {"client_id": "evil", "scopes": ["bsl:read"], "schema": "tenant_gamma"},
}


@pytest.fixture(scope="module")
def tenant_con():
    """DuckDB stand-in for schema-per-tenant Postgres: same rows shape, different data."""
    con = ibis.duckdb.connect(":memory:")
    for schema, carrier in [("tenant_alpha", "AA"), ("tenant_beta", "BB")]:
        con.raw_sql(f"CREATE SCHEMA {schema}")
        df = pd.DataFrame({"carrier": [carrier] * 3, "dep_delay": [1.0, 2.0, 3.0]})
        con.create_table("tenancy_flights", df, database=schema)
    return con


def make_factory(con):
    def factory(schema: str):
        tbl = con.table("tenancy_flights", database=schema)
        model = (
            to_semantic_table(tbl, name="flights")
            .with_dimensions(carrier=lambda t: t.carrier)
            .with_measures(flight_count=lambda t: t.count())
        )
        return {"flights": model}

    return factory


class TestConstructorValidation:
    def test_tenancy_requires_callable_models(self, tenant_con):
        with pytest.raises(ValueError, match="callable"):
            MCPSemanticModel(
                models={"flights": object()},
                tenancy=TenancyConfig(),
                auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
            )

    def test_tenancy_requires_auth_provider(self, tenant_con):
        with pytest.raises(ValueError, match="auth provider"):
            MCPSemanticModel(
                models=make_factory(tenant_con),
                tenancy=TenancyConfig(),
            )

    def test_factory_without_tenancy_rejected(self, tenant_con):
        with pytest.raises(ValueError, match="tenancy"):
            MCPSemanticModel(models=make_factory(tenant_con))

    def test_valid_tenant_construction(self, tenant_con):
        server = MCPSemanticModel(
            models=make_factory(tenant_con),
            tenancy=TenancyConfig(allowed_schemas=frozenset({"tenant_alpha", "tenant_beta"})),
            auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
        )
        assert server._tenancy is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'tenancy'`.

- [ ] **Step 3: Implement constructor changes**

In `mcp.py`, add imports near the other relative imports (after `from ._schema_mcp import register_schema_tools`):

```python
from ._tenancy import (
    TenancyConfig,
    TenantModelCache,
    emit_audit,
    resolve_tenant_schema,
)
```

and near the fastmcp imports:

```python
from fastmcp.server.dependencies import get_access_token
```

Change the constructor signature and body. The signature gains `tenancy`:

```python
    def __init__(
        self,
        models: Mapping[str, Any] | SemanticModelBundle | Callable[[str], Mapping[str, Any]],
        name: str = "Semantic Layer MCP Server",
        instructions: str = SYSTEM_INSTRUCTIONS,
        code_mode: bool = False,
        include_domain_context_tool: bool = True,
        include_add_skill_tool: bool = True,
        include_schema_tools: bool = False,
        tenancy: TenancyConfig | None = None,
        **kwargs,
    ):
```

(`Callable` comes from `collections.abc` — extend the existing `from collections.abc import Mapping` import.)

Immediately before `transforms = kwargs.pop("transforms", [])`, add the validation:

```python
        # Tenant mode contract: models must be a per-schema factory (a static
        # mapping would serve one tenant's data to all tenants), and a verifier
        # must be present (unverified claims cannot scope anything).
        is_factory = callable(models) and not isinstance(models, Mapping)
        if tenancy is not None:
            if not is_factory:
                raise ValueError(
                    "Multi-tenant mode requires `models` to be a callable "
                    "(schema: str) -> Mapping[str, SemanticTable]."
                )
            if kwargs.get("auth") is None:
                raise ValueError(
                    "Multi-tenant mode requires an auth provider (`auth=`); "
                    "without token verification, tenant claims cannot be trusted."
                )
        elif is_factory:
            raise ValueError(
                "`models` was passed as a callable but `tenancy` is not set; "
                "pass tenancy=TenancyConfig(...) or a static mapping."
            )
```

After `super().__init__(...)`, replace the `if isinstance(models, SemanticModelBundle):` block's surroundings so tenant mode is handled first:

```python
        self._tenancy = tenancy
        if tenancy is not None:
            self._model_factory = models
            self._tenant_models = TenantModelCache(maxsize=tenancy.max_cached_tenants)
            # No static models in tenant mode; every read goes through
            # _models_for_request(). Bundle skills are not supported here.
            self.models = {}
            self._parent_skills: list[SkillMetadata] = []
            self._model_skills: dict[str, list[SkillMetadata]] = {}
            self._parent_skills_dir: Path | None = None
            self._model_skills_dirs: dict[str, Path] = {}
        elif isinstance(models, SemanticModelBundle):
            ... (existing bundle branch unchanged, but drop the duplicate
                 type annotations if the tenant branch above already declared
                 them — keep annotations on first assignment only)
        else:
            ... (existing plain-mapping branch unchanged)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Run the existing MCP suite to prove no regression**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/ -v`
Expected: all PASS (single-tenant paths untouched).

- [ ] **Step 6: Commit**

```bash
git add -u src/boring_semantic_layer/agents/
git commit -m "feat(mcp): tenancy constructor wiring — factory models + auth required"
```

---

### Task 5: Per-request model resolution + isolation property tests (the heart)

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/mcp.py`
- Test: `src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py`

- [ ] **Step 1: Add the ASGI test harness and failing isolation tests** (append to `test_mcp_tenancy.py`)

```python
import json
from contextlib import asynccontextmanager

import httpx
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError


@asynccontextmanager
async def tenant_client(server, token: str | None):
    """MCP client against an in-process Streamable HTTP app.

    A fresh http_app() per context: the MCP session manager refuses to run
    twice on one app instance. URL is /mcp WITHOUT trailing slash (the
    trailing-slash 307 redirect breaks the MCP client).
    """
    app = server.http_app()

    def factory(headers=None, auth=None, **kwargs):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
            auth=auth,
        )

    transport = StreamableHttpTransport(
        "http://test/mcp",
        auth=BearerAuth(token) if token else None,
        httpx_client_factory=factory,
    )
    async with app.router.lifespan_context(app):
        async with Client(transport) as client:
            yield client


@pytest.fixture(scope="module")
def tenant_mcp(tenant_con):
    return MCPSemanticModel(
        models=make_factory(tenant_con),
        tenancy=TenancyConfig(allowed_schemas=frozenset({"tenant_alpha", "tenant_beta"})),
        auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
    )


def records_from(result) -> list[dict]:
    payload = json.loads(result.content[0].text)
    return payload["records"]


QUERY_ARGS = {
    "model_name": "flights",
    "dimensions": ["carrier"],
    "measures": ["flight_count"],
    "get_chart": False,
}


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_tenant_sees_only_its_schema_rows(self, tenant_mcp):
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            recs = records_from(await client.call_tool("query_model", QUERY_ARGS))
        assert [r["carrier"] for r in recs] == ["AA"]
        assert recs[0]["flight_count"] == 3

    @pytest.mark.asyncio
    async def test_same_query_two_tenants_two_results(self, tenant_mcp):
        """Cache isolation: identical query shape, per-tenant results, and the
        cached-models path (third call) still returns the right tenant's data."""
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            first_a = records_from(await client.call_tool("query_model", QUERY_ARGS))
        async with tenant_client(tenant_mcp, "token-beta") as client:
            b = records_from(await client.call_tool("query_model", QUERY_ARGS))
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            second_a = records_from(await client.call_tool("query_model", QUERY_ARGS))
        assert [r["carrier"] for r in first_a] == ["AA"]
        assert [r["carrier"] for r in b] == ["BB"]
        assert second_a == first_a

    @pytest.mark.asyncio
    async def test_list_models_per_tenant(self, tenant_mcp):
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            result = await client.call_tool("list_models", {})
        assert "flights" in json.loads(result.content[0].text)

    @pytest.mark.asyncio
    async def test_cross_tenant_model_name_not_addressable(self, tenant_mcp):
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            with pytest.raises(ToolError, match="not found"):
                await client.call_tool(
                    "query_model",
                    {**QUERY_ARGS, "model_name": "tenant_beta.flights"},
                )

    @pytest.mark.asyncio
    async def test_missing_schema_claim_rejected(self, tenant_mcp):
        async with tenant_client(tenant_mcp, "token-no-claim") as client:
            with pytest.raises(ToolError, match="missing the 'schema' claim"):
                await client.call_tool("list_models", {})

    @pytest.mark.asyncio
    async def test_schema_outside_allowlist_rejected(self, tenant_mcp):
        async with tenant_client(tenant_mcp, "token-evil") as client:
            with pytest.raises(ToolError, match="allowed schemas"):
                await client.call_tool("list_models", {})

    @pytest.mark.asyncio
    async def test_invalid_token_rejected_at_transport(self, tenant_mcp):
        with pytest.raises(httpx.HTTPStatusError):
            async with tenant_client(tenant_mcp, "no-such-token") as client:
                await client.list_tools()

    @pytest.mark.asyncio
    async def test_resource_reads_tenant_scoped(self, tenant_mcp):
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            contents = await client.read_resource("semantic://models")
        assert "flights" in contents[0].text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -k Isolation -v`
Expected: FAIL — tools read `self.models` (empty dict in tenant mode), so `query_model` raises "Model 'flights' not found".

- [ ] **Step 3: Implement per-request resolution**

Add two methods to `MCPSemanticModel` (place after `__init__`, before `_register_tools`):

```python
    def _current_tenant_schema(self) -> str | None:
        """Tenant schema for this request, or None on single-tenant servers."""
        if self._tenancy is None:
            return None
        return resolve_tenant_schema(get_access_token(), self._tenancy)

    def _models_for_request(self) -> Mapping[str, Any]:
        """Model mapping for the current request.

        Single-tenant: the static mapping. Multi-tenant: models built (or
        cached) for the schema named by the request token's claims — the
        request can never execute against any other schema because every
        table reference is created from this mapping.
        """
        if self._tenancy is None:
            return self.models
        schema = resolve_tenant_schema(get_access_token(), self._tenancy)
        return self._tenant_models.get(schema, self._model_factory)
```

Then swap every per-request `self.models` read (find by code match — line numbers shift):

1. `list_models` body: `return {name: f"Semantic model: {name}" for name in self._models_for_request()}`
2. `get_model`: `model = await resolve_model(self._models_for_request(), model_name, ctx)`
3. `get_time_range`: same swap.
4. `query_model`: same swap.
5. `search_dimension_values`: same swap.
6. `list_models_resource`: first line becomes `models = self._models_for_request()`, then iterate `for model_name in models:` and index `models[model_name]`.
7. `get_model_resource`: `models = self._models_for_request()` then `if model_name not in models: ...` / `_build_model_info(models[model_name])`.
8. `get_time_range_resource`: `models = self._models_for_request()` then same membership/index swap.

Do NOT touch the `build_domain_context(...)` call (skills tool — never registered in tenant mode) or constructor assignments.

- [ ] **Step 4: Add the STDIO refusal** (same edit session — it is part of the isolation property)

Add to `MCPSemanticModel` after `_models_for_request`:

```python
    async def run_async(
        self,
        transport=None,
        show_banner: bool | None = None,
        **transport_kwargs,
    ) -> None:
        """Refuse STDIO in tenant mode: tokens only exist on HTTP transports,
        so over STDIO get_access_token() is None and every tenant check would
        degrade into a hard failure on first tool call — fail loudly at startup
        instead."""
        if self._tenancy is not None and (transport is None or transport == "stdio"):
            raise RuntimeError(
                "Multi-tenant mode cannot run over STDIO: access tokens are "
                "unavailable, so tenant isolation cannot be enforced. "
                "Run with transport='http'."
            )
        return await super().run_async(
            transport=transport, show_banner=show_banner, **transport_kwargs
        )
```

And the test (append to `test_mcp_tenancy.py`):

```python
class TestStdioRefusal:
    @pytest.mark.asyncio
    async def test_stdio_transport_refused(self, tenant_mcp):
        with pytest.raises(RuntimeError, match="cannot run over STDIO"):
            await tenant_mcp.run_async(transport="stdio")

    @pytest.mark.asyncio
    async def test_default_transport_refused(self, tenant_mcp):
        with pytest.raises(RuntimeError, match="cannot run over STDIO"):
            await tenant_mcp.run_async()
```

- [ ] **Step 5: Run the tenancy tests**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -v`
Expected: all PASS. If `ToolError`/`httpx.HTTPStatusError` assertions mismatch on exception type (client may wrap errors), adjust the expected exception to what the FastMCP client actually raises — but the *behavior* (denied/not-found/401) must hold.

- [ ] **Step 6: Run the full agents suite**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add -u src/boring_semantic_layer/agents/
git commit -m "feat(mcp): per-request tenant model resolution + STDIO refusal"
```

---

### Task 6: Audit emission in `query_model` and `search_dimension_values`

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/mcp.py`
- Test: `src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py`

- [ ] **Step 1: Write the failing test** (append to `test_mcp_tenancy.py`)

```python
class TestAudit:
    @pytest.mark.asyncio
    async def test_query_model_emits_audit_event(self, tenant_con):
        events = []
        server = MCPSemanticModel(
            models=make_factory(tenant_con),
            tenancy=TenancyConfig(on_query=events.append),
            auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
        )
        async with tenant_client(server, "token-alpha") as client:
            await client.call_tool("query_model", QUERY_ARGS)
        assert len(events) == 1
        event = events[0]
        assert event["tenant_schema"] == "tenant_alpha"
        assert event["tool"] == "query_model"
        assert event["model"] == "flights"
        assert event["rowcount"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -k Audit -v`
Expected: FAIL — `events` stays empty.

- [ ] **Step 3: Implement**

In `query_model`, immediately before `return result` (after the `if ctx:` state block), add:

```python
            if self._tenancy is not None:
                # Rowcount comes from the serialized result; None (not 0) when
                # records were not requested or are unparsable — an unknown
                # count must not masquerade as an empty result.
                rowcount = None
                try:
                    records = json.loads(result).get("records")
                    if isinstance(records, list):
                        rowcount = len(records)
                except (ValueError, TypeError, AttributeError):
                    pass
                await emit_audit(
                    self._tenancy,
                    {
                        "tenant_schema": self._current_tenant_schema(),
                        "tool": "query_model",
                        "model": model_name,
                        "dimensions": dimensions,
                        "measures": measures,
                        "filters": filters,
                        "limit": limit,
                        "rowcount": rowcount,
                    },
                )
```

In `search_dimension_values`, immediately before its final `return`, add (the returned dict has a `values` list — confirm the local variable name at the return site and use it):

```python
            if self._tenancy is not None:
                await emit_audit(
                    self._tenancy,
                    {
                        "tenant_schema": self._current_tenant_schema(),
                        "tool": "search_dimension_values",
                        "model": model_name,
                        "dimension": dimension_name,
                        "search_term": search_term,
                        "rowcount": len(response["values"]),
                    },
                )
```

(Adjust `response["values"]` to the actual local name of the returned payload in that function.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(tenancy): per-tenant audit events from query tools"
```

---

### Task 7: Admin-scope gating for schema tools in tenant mode

**Dependency note:** `_schema_mcp.py` exists only on `feat/schema-tools`, not `main`. Decision: **base this branch on `feat/schema-tools`** is wrong (entangles two PRs). Instead:

- If `feat/schema-tools` has merged to `main` by execution time: implement this task as written.
- If not: implement the gating so it degrades gracefully — `mcp.py` on `main` has no `include_schema_tools` either, in which case **skip this task entirely** and record it as a follow-up in the PR body ("schema tools gating lands when #5 merges").
- Check with: `git log --oneline main | head -5` and `ls src/boring_semantic_layer/agents/backends/_schema_mcp.py`.

**Files (when applicable):**
- Modify: `src/boring_semantic_layer/agents/backends/_schema_mcp.py`
- Modify: `src/boring_semantic_layer/agents/backends/mcp.py`
- Test: `src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py`

- [ ] **Step 1: Write the failing test** (append to `test_mcp_tenancy.py`)

```python
class TestSchemaToolGating:
    @pytest.fixture(scope="class")
    def gated_mcp(self, tenant_con):
        return MCPSemanticModel(
            models=make_factory(tenant_con),
            tenancy=TenancyConfig(),
            auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
            include_schema_tools=True,
        )

    @pytest.mark.asyncio
    async def test_schema_tools_hidden_without_admin_scope(self, gated_mcp):
        async with tenant_client(gated_mcp, "token-alpha") as client:
            tools = {t.name for t in await client.list_tools()}
        assert "connect_source" not in tools
        assert "list_backends" not in tools
        assert "infer_schema" not in tools

    @pytest.mark.asyncio
    async def test_schema_tools_visible_with_admin_scope(self, gated_mcp):
        async with tenant_client(gated_mcp, "token-admin-alpha") as client:
            tools = {t.name for t in await client.list_tools()}
        assert {"connect_source", "list_backends", "infer_schema"} <= tools

    @pytest.mark.asyncio
    async def test_schema_tool_call_denied_without_admin_scope(self, gated_mcp):
        async with tenant_client(gated_mcp, "token-alpha") as client:
            with pytest.raises(ToolError):
                await client.call_tool("list_backends", {})
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -k Gating -v`
Expected: FAIL — schema tools visible to non-admin tenants.

- [ ] **Step 3: Implement**

In `_schema_mcp.py`, thread an `auth` parameter through:

```python
def register_schema_tools(server, prompts_dir, auth=None) -> None:
    """Register all three schema tools on ``server``.

    ``auth`` is an optional FastMCP auth check (e.g. require_scopes) applied
    to every schema tool — multi-tenant servers gate these to admins.
    """
    _register_list_backends(server, prompts_dir, auth=auth)
    _register_connect_source(server, prompts_dir, auth=auth)
    _register_infer_schema(server, prompts_dir, auth=auth)
```

Each `_register_*` gains `auth=None` in its signature and passes `auth=auth` in its `@server.tool(...)` decorator call.

In `mcp.py`, change the registration call:

```python
        if include_schema_tools:
            schema_tools_auth = None
            if tenancy is not None:
                from fastmcp.server.auth import require_scopes

                schema_tools_auth = require_scopes(tenancy.admin_scope)
            register_schema_tools(self, PROMPTS_DIR, auth=schema_tools_auth)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_tenancy.py -v && uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py -v`
Expected: all PASS (existing schema-tool tests unaffected — `auth=None` default).

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(tenancy): admin-scope gate schema tools in multi-tenant mode"
```

---

### Task 8: Public export + example

**Files:**
- Modify: `src/boring_semantic_layer/__init__.py` (the `__getattr__` at line ~77)
- Create: `examples/example_mcp_multitenant.py`
- Test: `src/boring_semantic_layer/agents/tests/test_tenancy_unit.py`

- [ ] **Step 1: Write the failing test** (append to `test_tenancy_unit.py`)

```python
def test_tenancy_config_lazy_export():
    import boring_semantic_layer as bsl

    assert bsl.TenancyConfig is TenancyConfig
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -k lazy -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the lazy export**

In `src/boring_semantic_layer/__init__.py`, inside `__getattr__`, after the `MCPSemanticModel` block:

```python
    if name == "TenancyConfig":
        try:
            from .agents.backends._tenancy import TenancyConfig

            return TenancyConfig
        except ImportError:
            raise ImportError(
                "TenancyConfig requires the 'mcp' optional dependencies. "
                "Install with: pip install 'boring-semantic-layer[mcp]'"
            ) from None
```

- [ ] **Step 4: Write the example**

Create `examples/example_mcp_multitenant.py`:

```python
"""Multi-tenant MCP server: schema-per-tenant isolation from token claims.

Two DuckDB schemas stand in for schema-per-tenant Postgres. Each tenant's
token carries a `schema` claim; every request only ever queries that schema.

Run:    uv run python examples/example_mcp_multitenant.py
Call:   any MCP client over Streamable HTTP at http://127.0.0.1:8000/mcp
        with header `Authorization: Bearer token-alpha` (or token-beta).
"""

import ibis
import pandas as pd
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from boring_semantic_layer import TenancyConfig, to_semantic_table
from boring_semantic_layer.agents.backends.mcp import MCPSemanticModel

con = ibis.duckdb.connect(":memory:")
for schema, carrier in [("tenant_alpha", "AA"), ("tenant_beta", "BB")]:
    con.raw_sql(f"CREATE SCHEMA {schema}")
    df = pd.DataFrame({"carrier": [carrier] * 3, "dep_delay": [1.0, 2.0, 3.0]})
    con.create_table("flights", df, database=schema)


def build_models(schema: str):
    """Identical model shapes per tenant — only the bound schema differs."""
    tbl = con.table("flights", database=schema)
    model = (
        to_semantic_table(tbl, name="flights", description="Flights for one tenant")
        .with_dimensions(carrier=lambda t: t.carrier)
        .with_measures(flight_count=lambda t: t.count())
    )
    return {"flights": model}


mcp = MCPSemanticModel(
    models=build_models,
    tenancy=TenancyConfig(
        allowed_schemas=frozenset({"tenant_alpha", "tenant_beta"}),
        on_query=lambda event: print(f"[audit] {event}"),
    ),
    # StaticTokenVerifier is for demos/tests only; production uses JWTVerifier
    # against the identity provider's JWKS endpoint.
    auth=StaticTokenVerifier(
        tokens={
            "token-alpha": {"client_id": "tenant-alpha", "scopes": ["bsl:read"], "schema": "tenant_alpha"},
            "token-beta": {"client_id": "tenant-beta", "scopes": ["bsl:read"], "schema": "tenant_beta"},
        }
    ),
)

if __name__ == "__main__":
    mcp.run(transport="http")  # STDIO is refused in multi-tenant mode
```

- [ ] **Step 5: Run tests + smoke the example imports**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_tenancy_unit.py -v && uv run python -c "import ast; ast.parse(open('examples/example_mcp_multitenant.py').read())"`
Expected: PASS + no syntax error. (Don't run the example itself — it blocks serving HTTP.)

- [ ] **Step 6: Commit**

```bash
git add src/boring_semantic_layer/__init__.py examples/example_mcp_multitenant.py src/boring_semantic_layer/agents/tests/test_tenancy_unit.py
git commit -m "feat(tenancy): lazy TenancyConfig export + multi-tenant example"
```

---

### Task 9: Full verification, docs touch, PR

- [ ] **Step 1: Full test suite (all three roots per CLAUDE.md)**

```bash
uv run pytest src/boring_semantic_layer/tests/ -q
uv run pytest src/boring_semantic_layer/agents/tests/ -q
uv run pytest src/boring_semantic_layer/chart/tests/ -q
```
Expected: all PASS.

- [ ] **Step 2: Lint**

```bash
uv run ruff check src/ examples/
uv run ruff format --check src/ examples/
```
Expected: clean.

- [ ] **Step 3: Update CLAUDE.md** — add to the FastMCP 3.0+ Features section:

```markdown
- **Multi-tenancy**: Optional `tenancy=TenancyConfig(...)` constructor parameter — `models` becomes a factory `(schema) -> mapping`; tenant schema resolves per request from token claims (`get_access_token().claims`); STDIO refused; schema tools admin-gated; optional `on_query` audit callback. See `_tenancy.py` and `examples/example_mcp_multitenant.py`.
```

- [ ] **Step 4: Run `dmg-code-quality:cq-review`** (per handoff) and resolve findings.

- [ ] **Step 5: Commit docs, push, open PR**

```bash
git add CLAUDE.md
git commit -m "docs: document multi-tenant mode in CLAUDE.md"
git push -u origin feat/tenant-provisioning
gh pr create --repo mattfili/boring-semantic-layer --base main \
  --title "feat: schema-level tenant provisioning (FastMCP 3.0 auth)" \
  --body "<see PR body checklist below>"
```

PR body must cover: the isolation property and its tests; the xorq cache audit finding (no caching in the MCP query path today; tenant-model LRU keyed by schema; cross-tenant test guards the property); STDIO refusal rationale; out-of-scope items (per-tenant skills, SingleRun wrappers, schema-tools gating follow-up if #5 unmerged); consumer contract pointer (SingleRun connects as Pydantic AI MCP client with tenant token).

---

## Self-review notes (already applied)

- **Handoff item 5 ("event hooks → audit log"):** the fork has NO event-hook infrastructure (verified by repo-wide search). The audit callback in `TenancyConfig.on_query` replaces that assumption — minimal, test-covered, and placed exactly where the handoff wanted the data captured.
- **Handoff item 3 (visibility filtering):** all components are shared; only data is tenant-scoped. Component-level `auth=` is used only where components genuinely differ by privilege (schema tools). This matches the handoff's "if all components are shared... middleware + step 2 may be enough — decide in-code."
- **Handoff item 4 (xorq cache keys):** audited; finding recorded in Validated Facts #8 and the PR body requirement.
- **Type consistency check:** `TenancyConfig`, `resolve_tenant_schema`, `TenantModelCache.get(schema, factory)`, `emit_audit(config, event)` are used with identical signatures across Tasks 1–8.
