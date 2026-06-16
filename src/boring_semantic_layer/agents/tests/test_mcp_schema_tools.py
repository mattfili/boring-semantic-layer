"""Integration tests for schema tools on MCPSemanticModel.

All tests go through the MCP protocol (`async with Client(mcp)`) — never
touch internal APIs. Module-scoped DuckDB fixture; unique table names per
test class to avoid clobbering across the shared connection.
"""

from __future__ import annotations

import json
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
    async def test_flag_enabled_registers_all_three(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
            assert "infer_schema" in tool_names
            assert "connect_source" in tool_names
            assert "list_backends" in tool_names

    @pytest.mark.asyncio
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


class TestListBackends:
    """list_backends — synchronous, no DB calls."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("list_backends_t", df, overwrite=True)
        return "list_backends_t"

    @pytest.mark.asyncio
    async def test_returns_installed_and_available_lists(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool("list_backends", {})
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "installed_backends" in data
            assert "available_backends" in data
            assert "install_instructions" in data
            # duckdb is a hard dep — must be installed
            assert "duckdb" in data["installed_backends"]

    @pytest.mark.asyncio
    async def test_install_instructions_keys_match_supported(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool("list_backends", {})
            data = json.loads(result.content[0].text) if result.content else result.data
            expected = {
                "duckdb",
                "postgres",
                "snowflake",
                "bigquery",
                "mysql",
                "sqlite",
                "clickhouse",
            }
            assert set(data["install_instructions"].keys()) == expected
            for hint in data["install_instructions"].values():
                assert "pip install" in hint
                assert "ibis-framework[" in hint

    @pytest.mark.asyncio
    async def test_tool_annotations_readonly(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            t = tools["list_backends"]
            assert t.annotations.readOnlyHint is True
            assert t.annotations.destructiveHint is False


class TestConnectSourceLocal:
    """connect_source against an in-memory DuckDB."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("conn_local_t", df, overwrite=True)
        return "conn_local_t"

    @pytest.mark.asyncio
    async def test_connects_to_duckdb_memory(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "connect_source",
                {
                    "backend": "duckdb",
                    "profile_name": "test_local",
                    "connection_params": {"database": ":memory:"},
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert data["status"] == "connected"
            assert "test_local:" in data["proposed_profile_yaml"]
            assert "type: duckdb" in data["proposed_profile_yaml"]
            assert isinstance(data["available_tables"], list)

    @pytest.mark.asyncio
    async def test_unsupported_backend_raises_tool_error(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "connect_source",
                    {
                        "backend": "definitely-not-supported",
                        "profile_name": "x",
                        "connection_params": {},
                    },
                )
            assert (
                "not supported" in str(exc_info.value).lower()
                or "supported" in str(exc_info.value).lower()
            )


class TestReadOnlyAnnotations:
    """All three tools must declare read-only annotations."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("ro_annot_t", df, overwrite=True)
        return "ro_annot_t"

    @pytest.mark.asyncio
    async def test_all_tools_readonly(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            for name in ("infer_schema", "connect_source", "list_backends"):
                t = tools[name]
                assert t.annotations.readOnlyHint is True, f"{name} should be readOnly"
                assert t.annotations.destructiveHint is False, f"{name} should not be destructive"


class TestConnectSourceCredentialScrub:
    """Credentials must never appear in error messages."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("scrub_t", df, overwrite=True)
        return "scrub_t"

    @pytest.mark.asyncio
    async def test_password_scrubbed_from_connection_failure(
        self, con, setup_table, tmp_path, monkeypatch
    ):
        """Force open_backend to raise with a credential-bearing message;
        assert the ToolError doesn't leak the password."""

        def fake_open(config):
            raise Exception("auth failed for user pg_admin password=hunter2 token=secret_xyz")

        # The tool imports open_backend lazily inside the registration; patch
        # it on the source module so the deferred import sees the fake.
        monkeypatch.setattr(
            "boring_semantic_layer.agents.backends._source_inspection.open_backend",
            fake_open,
        )

        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "connect_source",
                    {
                        "backend": "postgres",
                        "profile_name": "scrub_test",
                        "connection_params": {
                            "host": "x",
                            "password": "hunter2",
                            "token": "secret_xyz",
                        },
                    },
                )
            msg = str(exc_info.value)
            assert "hunter2" not in msg
            assert "secret_xyz" not in msg


class TestConnectSourceTruncation:
    """When >100 tables exist, the response truncates and reports it."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("trunc_t", df, overwrite=True)
        return "trunc_t"

    @pytest.mark.asyncio
    async def test_truncates_at_100(self, con, setup_table, tmp_path, monkeypatch):
        """Patch list_tables_with_counts to return more than 100 rows."""
        from boring_semantic_layer.agents.backends._source_inspection import TableSummary

        def fake_list(con, *, limit_tables=100):
            n = limit_tables  # 100
            summaries = [
                TableSummary(name=f"t{i}", row_count=i, count_error=None) for i in range(n)
            ]
            return summaries, True  # truncated

        monkeypatch.setattr(
            "boring_semantic_layer.agents.backends._source_inspection.list_tables_with_counts",
            fake_list,
        )

        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "connect_source",
                {
                    "backend": "duckdb",
                    "profile_name": "trunc_test",
                    "connection_params": {"database": ":memory:"},
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert data["truncated"] is True
            assert len(data["available_tables"]) == 100


class TestInferSchemaTable:
    """infer_schema against an existing table in the profile's connection."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame(
            {
                "carrier_id": [1, 2, 3],
                "origin": ["JFK", "LAX", "ORD"],
                "flight_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]).date,
                "dep_delay": [5.0, 10.0, 0.0],
            }
        )
        con.create_table("infer_table_t", df, overwrite=True)
        return "infer_table_t"

    @pytest.mark.asyncio
    async def test_returns_proposed_yaml_and_classifications(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "new_flights",
                    "source": setup_table,
                    "source_type": "table",
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "new_flights:" in data["proposed_yaml"]
            classifications = {c["column"]: c for c in data["column_classifications"]}
            assert classifications["carrier_id"]["classification"] == "dimension"
            assert classifications["dep_delay"]["classification"] == "measure"
            assert classifications["flight_date"]["is_time_dimension"] is True


class TestInferSchemaJoins:
    """When a registry model matches the FK prefix, surface a potential join."""

    @pytest.fixture(scope="class")
    def setup_tables(self, con):
        flights_df = pd.DataFrame({"carrier_id": [1, 2], "origin": ["A", "B"]})
        carriers_df = pd.DataFrame({"id": [1, 2], "name": ["AA", "UA"]})
        con.create_table("join_flights_t", flights_df, overwrite=True)
        con.create_table("join_carriers_t", carriers_df, overwrite=True)
        return ("join_flights_t", "join_carriers_t")

    @pytest.mark.asyncio
    async def test_potential_join_to_registered_carriers(self, con, setup_tables, tmp_path):
        flights_table, carriers_table = setup_tables
        yaml_path = tmp_path / "cfg.yml"
        _write_yaml(
            yaml_path,
            f"""
carriers:
  table: {carriers_table}
  dimensions:
    id: _.id
    name: _.name
  measures:
    carrier_count: _.count()
""",
        )
        bundle = from_yaml(
            str(yaml_path),
            tables={carriers_table: con.table(carriers_table)},
        )
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "flights",
                    "source": flights_table,
                    "source_type": "table",
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            joins = data["potential_joins"]
            assert len(joins) == 1
            assert joins[0]["matches_model"] == "carriers"
            assert joins[0]["matches_dimension"] == "id"


class TestInferSchemaFile:
    """infer_schema against the inferable.parquet fixture (file-source path)."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("infer_file_baseline", df, overwrite=True)
        return "infer_file_baseline"

    @pytest.fixture(scope="class")
    def parquet_path(self) -> Path:
        path = (
            Path(__file__).parent.parent.parent
            / "tests"
            / "fixtures"
            / "sample_tables"
            / "inferable.parquet"
        )
        assert path.exists(), f"fixture missing: {path}"
        return path

    @pytest.mark.asyncio
    async def test_infers_from_parquet_extension(self, con, setup_table, parquet_path, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "orders_from_file",
                    "source": str(parquet_path),
                    # No explicit source_type → inferred from .parquet extension
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "orders_from_file:" in data["proposed_yaml"]
            cls = {c["column"]: c for c in data["column_classifications"]}
            assert cls["order_id"]["classification"] == "dimension"
            assert cls["customer_id"]["classification"] == "dimension"
            assert cls["order_total"]["classification"] == "measure"
            assert cls["order_date"]["is_time_dimension"] is True
            assert cls["is_paid"]["classification"] == "dimension"

    @pytest.mark.asyncio
    async def test_explicit_source_type_overrides_extension(
        self, con, setup_table, parquet_path, tmp_path
    ):
        """Explicit source_type beats extension inference."""
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            # Pass source_type="parquet" explicitly — should still work
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "orders_explicit",
                    "source": str(parquet_path),
                    "source_type": "parquet",
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "orders_explicit:" in data["proposed_yaml"]


class TestInferSchemaErrors:
    """All error paths from spec section 6.1."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("infer_err_t", df, overwrite=True)
        return "infer_err_t"

    @pytest.mark.asyncio
    async def test_missing_table_raises(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "x",
                        "source": "definitely_not_a_table",
                        "source_type": "table",
                    },
                )
            assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_file_raises(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "x",
                        "source": "/nonexistent/path/data.parquet",
                    },
                )
            msg = str(exc_info.value).lower()
            assert "not found" in msg or "does not exist" in msg

    @pytest.mark.asyncio
    async def test_unsupported_file_extension_raises(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            xlsx = tmp_path / "data.xlsx"
            xlsx.write_bytes(b"")  # empty file
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "x",
                        "source": str(xlsx),
                    },
                )
            # Ext inference defaults to "table" for unknown extensions; .xlsx
            # falls back to looking it up as a table — should report not found
            assert (
                "not found" in str(exc_info.value).lower()
                or "unsupported" in str(exc_info.value).lower()
            )

    @pytest.mark.asyncio
    async def test_name_collision_with_existing_model(self, con, setup_table, tmp_path):
        """`table_name` must not collide with an already-registered model."""
        bundle = _basic_bundle(con, setup_table, tmp_path)  # registers "flights"
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "flights",  # collides
                        "source": setup_table,
                        "source_type": "table",
                    },
                )
            assert "already" in str(exc_info.value).lower()
