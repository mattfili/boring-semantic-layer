"""Integration tests for schema tools on MCPSemanticModel.

All tests go through the MCP protocol (`async with Client(mcp)`) — never
touch internal APIs. Module-scoped DuckDB fixture; unique table names per
test class to avoid clobbering across the shared connection.
"""

from __future__ import annotations

from pathlib import Path

import ibis
import pandas as pd
import pytest
from fastmcp import Client

from boring_semantic_layer import MCPSemanticModel, from_yaml


@pytest.fixture(scope="module")
def con():
    return ibis.duckdb.connect(":memory:")


def _write_yaml(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def _basic_bundle(con, sample_table_name: str, tmp_path: Path):
    """Build a minimal SemanticModelBundle with one model on `sample_table_name`."""
    yaml_path = tmp_path / "cfg.yml"
    _write_yaml(
        yaml_path,
        f"flights:\n  table: {sample_table_name}\n  dimensions:\n    origin: _.origin\n  measures:\n    flight_count: _.count()\n",
    )
    return from_yaml(str(yaml_path), tables={sample_table_name: con.table(sample_table_name)})


class TestSchemaToolsRegistration:
    """Default → tools absent; flag=True → all three present."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"origin": ["JFK", "LAX"], "carrier": ["AA", "UA"]})
        con.create_table("schema_reg_flights", df, overwrite=True)
        return "schema_reg_flights"

    @pytest.mark.asyncio
    async def test_default_constructor_no_schema_tools(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle)
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
            assert "infer_schema" not in tool_names
            assert "connect_source" not in tool_names
            assert "list_backends" not in tool_names
            # Sanity: existing tools still present
            assert "list_models" in tool_names

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="connect_source + infer_schema land in tasks 12+14", strict=False)
    async def test_flag_enabled_registers_all_three(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
            assert "infer_schema" in tool_names
            assert "connect_source" in tool_names
            assert "list_backends" in tool_names

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="full registration lands across tasks 11+12+14", strict=False)
    async def test_flag_independent_of_skill_flags(self, con, setup_table, tmp_path):
        """Schema tools have no dependency on skills_dir."""
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(
            bundle,
            include_schema_tools=True,
            include_domain_context_tool=False,
            include_add_skill_tool=False,
        )
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
            assert "infer_schema" in tool_names
            assert "get_domain_context" not in tool_names
            assert "add_skill" not in tool_names
