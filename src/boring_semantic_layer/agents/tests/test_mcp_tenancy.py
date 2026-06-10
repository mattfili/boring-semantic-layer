"""Protocol-level tenancy tests for MCPSemanticModel.

In-memory FastMCP transport does NOT support auth (verified on fastmcp
3.2.4), so later test classes drive a real Streamable HTTP app in-process
via httpx.ASGITransport. This module starts with constructor validation.
"""

import json
from contextlib import asynccontextmanager

import httpx
import ibis
import pandas as pd
import pytest
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

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
    """DuckDB stand-in for schema-per-tenant Postgres: same shape, different data."""
    con = ibis.duckdb.connect(":memory:")
    for schema, carrier in [("tenant_alpha", "AA"), ("tenant_beta", "BB")]:
        con.raw_sql(f"CREATE SCHEMA {schema}")
        df = pd.DataFrame({"carrier": [carrier] * 3, "dep_delay": [1.0, 2.0, 3.0]})
        con.create_table("tenancy_flights", df, database=schema)
    return con


def make_factory(con):
    """Build a per-schema model factory backed by the given connection."""

    def factory(schema: str):
        tbl = con.table("tenancy_flights", database=schema)
        model = (
            to_semantic_table(tbl, name="flights")
            .with_dimensions(carrier=lambda t: t.carrier)
            .with_measures(flight_count=lambda t: t.count())
        )
        return {"flights": model}

    return factory


@asynccontextmanager
async def tenant_client(server, token: str | None):
    """MCP client against an in-process Streamable HTTP app.

    A fresh http_app() per context: the MCP session manager refuses to run
    twice on one app instance. URL is /mcp WITHOUT trailing slash (the
    trailing-slash 307 redirect breaks the MCP client).

    When the Client connect raises (e.g. HTTP 401), the lifespan context
    wraps it in an ExceptionGroup during teardown. We unwrap and re-raise
    the leaf so callers can match on the original exception type.
    """
    app = server.http_app()

    def factory(headers=None, auth=None, **kwargs):
        """Build an httpx async client wired to the in-process ASGI app."""
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
    try:
        async with app.router.lifespan_context(app), Client(transport) as client:
            yield client
    except Exception as exc:
        # Lifespan teardown wraps a connect-time error (e.g. 401) in an
        # ExceptionGroup. Unwrap to let callers match the original exception.
        eg = getattr(exc, "exceptions", None)
        if eg and len(eg) == 1:
            raise eg[0] from exc
        raise


@pytest.fixture(scope="module")
def tenant_mcp(tenant_con):
    """Multi-tenant server over the two-schema DuckDB fixture."""
    return MCPSemanticModel(
        models=make_factory(tenant_con),
        tenancy=TenancyConfig(allowed_schemas=frozenset({"tenant_alpha", "tenant_beta"})),
        auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
    )


def records_from(result) -> list[dict]:
    """Extract the records list from a query_model tool result."""
    payload = json.loads(result.content[0].text)
    return payload["records"]


QUERY_ARGS = {
    "model_name": "flights",
    "dimensions": ["carrier"],
    "measures": ["flight_count"],
    "get_chart": False,
}


class TestConstructorValidation:
    def test_tenancy_requires_callable_models(self, tenant_con):
        """Passing a static mapping with tenancy=TenancyConfig() must raise."""
        with pytest.raises(ValueError, match="callable"):
            MCPSemanticModel(
                models={"flights": object()},
                tenancy=TenancyConfig(),
                auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
            )

    def test_tenancy_requires_auth_provider(self, tenant_con):
        """Passing a factory with tenancy but no auth must raise."""
        with pytest.raises(ValueError, match="auth provider"):
            MCPSemanticModel(
                models=make_factory(tenant_con),
                tenancy=TenancyConfig(),
            )

    def test_factory_without_tenancy_rejected(self, tenant_con):
        """Passing a callable models without tenancy=TenancyConfig() must raise."""
        with pytest.raises(ValueError, match="tenancy"):
            MCPSemanticModel(models=make_factory(tenant_con))

    def test_valid_tenant_construction(self, tenant_con):
        """A fully-specified tenant server must construct without error."""
        server = MCPSemanticModel(
            models=make_factory(tenant_con),
            tenancy=TenancyConfig(allowed_schemas=frozenset({"tenant_alpha", "tenant_beta"})),
            auth=StaticTokenVerifier(tokens=VERIFIER_TOKENS),
        )
        assert server._tenancy is not None


class TestTenantIsolation:
    """Per-request tenant model resolution: each token sees only its schema."""

    @pytest.mark.asyncio
    async def test_tenant_sees_only_its_schema_rows(self, tenant_mcp):
        """Alpha token returns only AA rows from the alpha schema."""
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            recs = records_from(await client.call_tool("query_model", QUERY_ARGS))
        assert [r["carrier"] for r in recs] == ["AA"]
        assert recs[0]["flight_count"] == 3

    @pytest.mark.asyncio
    async def test_same_query_two_tenants_two_results(self, tenant_mcp):
        """Cache isolation: identical query shape, per-tenant results; the
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
        """list_models returns the tenant's own model names."""
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            result = await client.call_tool("list_models", {})
        assert "flights" in json.loads(result.content[0].text)

    @pytest.mark.asyncio
    async def test_cross_tenant_model_name_not_addressable(self, tenant_mcp):
        """A model name prefixed with another tenant's schema must not resolve."""
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            with pytest.raises(ToolError, match="not found"):
                await client.call_tool(
                    "query_model",
                    {**QUERY_ARGS, "model_name": "tenant_beta.flights"},
                )

    @pytest.mark.asyncio
    async def test_missing_schema_claim_rejected(self, tenant_mcp):
        """Token without a schema claim must be rejected inside the tool."""
        async with tenant_client(tenant_mcp, "token-no-claim") as client:
            with pytest.raises(ToolError, match="missing the 'schema' claim"):
                await client.call_tool("list_models", {})

    @pytest.mark.asyncio
    async def test_schema_outside_allowlist_rejected(self, tenant_mcp):
        """Token with a schema not in allowed_schemas must be rejected."""
        async with tenant_client(tenant_mcp, "token-evil") as client:
            with pytest.raises(ToolError, match="allowed schemas"):
                await client.call_tool("list_models", {})

    @pytest.mark.asyncio
    async def test_invalid_token_rejected_at_transport(self, tenant_mcp):
        """An unrecognised token must produce an HTTP 401 before reaching any tool."""
        with pytest.raises(httpx.HTTPStatusError):
            async with tenant_client(tenant_mcp, "no-such-token") as client:
                await client.list_tools()

    @pytest.mark.asyncio
    async def test_resource_reads_tenant_scoped(self, tenant_mcp):
        """The semantic://models resource must reflect the requesting tenant's models."""
        async with tenant_client(tenant_mcp, "token-alpha") as client:
            contents = await client.read_resource("semantic://models")
        assert "flights" in contents[0].text


class TestStdioRefusal:
    """Multi-tenant servers must refuse STDIO transport at startup."""

    @pytest.mark.asyncio
    async def test_stdio_transport_refused(self, tenant_mcp):
        """Explicit transport='stdio' must raise RuntimeError."""
        with pytest.raises(RuntimeError, match="cannot run over STDIO"):
            await tenant_mcp.run_async(transport="stdio")

    @pytest.mark.asyncio
    async def test_default_transport_refused(self, tenant_mcp):
        """Default transport (None → STDIO) must raise RuntimeError."""
        with pytest.raises(RuntimeError, match="cannot run over STDIO"):
            await tenant_mcp.run_async()
