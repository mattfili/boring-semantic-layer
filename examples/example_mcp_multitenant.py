#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "boring-semantic-layer[examples] >= 0.3.9",
#     "boring-semantic-layer[mcp] >= 0.3.9"
# ]
# ///

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
            "token-alpha": {
                "client_id": "tenant-alpha",
                "scopes": ["bsl:read"],
                "schema": "tenant_alpha",
            },
            "token-beta": {
                "client_id": "tenant-beta",
                "scopes": ["bsl:read"],
                "schema": "tenant_beta",
            },
        }
    ),
)

if __name__ == "__main__":
    mcp.run(transport="http")  # STDIO is refused in multi-tenant mode
