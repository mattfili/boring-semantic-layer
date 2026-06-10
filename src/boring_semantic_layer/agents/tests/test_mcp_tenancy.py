"""Protocol-level tenancy tests for MCPSemanticModel.

In-memory FastMCP transport does NOT support auth (verified on fastmcp
3.2.4), so later test classes drive a real Streamable HTTP app in-process
via httpx.ASGITransport. This module starts with constructor validation.
"""

import ibis
import pandas as pd
import pytest
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
