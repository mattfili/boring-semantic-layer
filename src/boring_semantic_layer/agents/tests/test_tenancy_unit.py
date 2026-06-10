"""Unit tests for the tenancy module — pure logic, no MCP protocol."""

import pytest
from fastmcp.exceptions import ToolError

from boring_semantic_layer.agents.backends._tenancy import (
    TenancyConfig,
    TenantModelCache,
    emit_audit,
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

    def test_empty_string_claim_treated_as_missing(self):
        token = FakeToken({"schema": ""})
        with pytest.raises(ToolError, match="missing the 'schema' claim"):
            resolve_tenant_schema(token, TenancyConfig())

    @pytest.mark.parametrize(
        "bad",
        ["tenant-a; DROP TABLE x", "a.b", 'a"b', "1tenant", "tenant a", "tenant_a\n"],
    )
    def test_invalid_identifier_raises(self, bad):
        token = FakeToken({"schema": bad})
        with pytest.raises(ToolError, match="not a valid schema identifier"):
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


class TestTenantModelCache:
    def test_maxsize_must_be_positive(self):
        with pytest.raises(ValueError, match="maxsize"):
            TenantModelCache(maxsize=0)

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
        assert any("audit callback failed" in r.getMessage() for r in caplog.records)


def test_tenancy_config_lazy_export():
    """TenancyConfig is importable from the package root (lazy, mcp extra)."""
    import boring_semantic_layer as bsl

    assert bsl.TenancyConfig is TenancyConfig
