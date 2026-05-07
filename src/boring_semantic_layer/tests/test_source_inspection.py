"""Tests for _source_inspection: open_backend, list_tables_with_counts,
build_profile_yaml, open_transient_duckdb_for_file, _sanitize_error.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from io import StringIO

import pandas as pd
import pytest
import yaml as _pyyaml

from boring_semantic_layer.agents.backends._source_inspection import (
    TableSummary,
    _sanitize_error,
    build_profile_yaml,
    list_tables_with_counts,
    open_backend,
    open_transient_duckdb_for_file,
)


class TestOpenBackend:
    def test_open_duckdb_in_memory_via_xorq(self):
        con = open_backend({"type": "duckdb", "database": ":memory:"})
        # Xorq's DuckDB profile returns an ibis backend
        assert con.list_tables() == []
        # Sanity: SELECT 1 works
        result = con.sql("SELECT 1 AS x").execute()
        assert int(result["x"].iloc[0]) == 1

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend type"):
            open_backend({"type": "definitely-not-a-real-backend"})

    def test_missing_type_raises(self):
        with pytest.raises(ValueError, match="must include 'type'"):
            open_backend({"database": ":memory:"})


class TestListTablesWithCounts:
    def test_empty_connection_returns_empty(self):
        con = open_backend({"type": "duckdb", "database": ":memory:"})
        assert list_tables_with_counts(con) == ([], False)

    def test_lists_tables_with_row_counts(self):
        con = open_backend({"type": "duckdb", "database": ":memory:"})
        con.create_table("a", pd.DataFrame({"x": [1, 2, 3]}))
        con.create_table("b", pd.DataFrame({"y": [10]}))
        results, truncated = list_tables_with_counts(con)
        assert truncated is False
        by_name = {t.name: t for t in results}
        assert by_name["a"].row_count == 3
        assert by_name["b"].row_count == 1
        assert by_name["a"].count_error is None

    def test_truncates_at_cap(self):
        con = open_backend({"type": "duckdb", "database": ":memory:"})
        for i in range(5):
            con.create_table(f"t{i}", pd.DataFrame({"x": [i]}))
        results, truncated = list_tables_with_counts(con, limit_tables=3)
        assert len(results) == 3
        assert truncated is True

    def test_count_error_recorded_not_raised(self, monkeypatch):
        """If COUNT(*) blows up on one table, that table appears with count_error
        and the call still returns successfully."""
        con = open_backend({"type": "duckdb", "database": ":memory:"})
        con.create_table("ok_table", pd.DataFrame({"x": [1]}))

        # Patch list_tables to return a bogus name; con.table(name) will raise
        original_list = con.list_tables

        def fake_list(*a, **kw):
            return [*original_list(*a, **kw), "definitely_does_not_exist"]

        monkeypatch.setattr(con, "list_tables", fake_list)

        results, _ = list_tables_with_counts(con)
        by_name = {t.name: t for t in results}
        assert by_name["ok_table"].count_error is None
        assert by_name["definitely_does_not_exist"].row_count is None
        assert by_name["definitely_does_not_exist"].count_error is not None

    def test_per_table_timeout_recorded(self, monkeypatch):
        """Slow COUNT(*) query times out and shows up with count_error."""
        import time

        from boring_semantic_layer.agents.backends import _source_inspection

        con = open_backend({"type": "duckdb", "database": ":memory:"})
        con.create_table("slow_t", pd.DataFrame({"x": [1]}))

        # Patch _count_table to sleep longer than the timeout
        def slow_count(con, name):
            time.sleep(2)
            return 0

        monkeypatch.setattr(_source_inspection, "_count_table", slow_count)

        results, _ = list_tables_with_counts(con, timeout_seconds=0.1)
        by_name = {t.name: t for t in results}
        assert by_name["slow_t"].row_count is None
        assert "timed out" in by_name["slow_t"].count_error.lower()

    def test_table_summary_is_frozen_dataclass(self):
        t = TableSummary(name="foo", row_count=10, count_error=None)
        with pytest.raises(FrozenInstanceError):
            t.name = "bar"


class TestBuildProfileYaml:
    def test_basic_profile_round_trips(self):
        rendered = build_profile_yaml(
            "warehouse",
            backend="postgres",
            params={"host": "db.example.com", "port": 5432, "database": "prod"},
        )
        parsed = _pyyaml.safe_load(StringIO(rendered))
        assert "warehouse" in parsed
        assert parsed["warehouse"]["type"] == "postgres"
        assert parsed["warehouse"]["host"] == "db.example.com"
        assert parsed["warehouse"]["port"] == 5432

    def test_env_var_literals_preserved(self):
        rendered = build_profile_yaml(
            "warehouse",
            backend="postgres",
            params={"host": "${PG_HOST}", "password": "${PG_PASSWORD}"},
        )
        # The literal `${PG_HOST}` must round-trip — no expansion at render time
        assert "${PG_HOST}" in rendered
        assert "${PG_PASSWORD}" in rendered
        parsed = _pyyaml.safe_load(StringIO(rendered))
        assert parsed["warehouse"]["host"] == "${PG_HOST}"


class TestOpenTransientDuckdbForFile:
    def test_parquet_path_loads_table(self, tmp_path):
        df = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        parquet_path = tmp_path / "sample.parquet"
        df.to_parquet(parquet_path)

        con, tbl = open_transient_duckdb_for_file(str(parquet_path), "parquet")
        try:
            assert tbl.count().execute() == 3
            schema = tbl.schema()
            assert "x" in schema
            assert "y" in schema
        finally:
            if hasattr(con, "disconnect"):
                con.disconnect()

    def test_csv_path_loads_table(self, tmp_path):
        csv_path = tmp_path / "sample.csv"
        csv_path.write_text("x,y\n1,a\n2,b\n")
        con, tbl = open_transient_duckdb_for_file(str(csv_path), "csv")
        try:
            assert tbl.count().execute() == 2
        finally:
            if hasattr(con, "disconnect"):
                con.disconnect()

    def test_unsupported_format_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported"):
            open_transient_duckdb_for_file(str(tmp_path / "x.xlsx"), "xlsx")  # type: ignore


class TestSanitizeError:
    def test_scrubs_password_pattern(self):
        msg = "auth failed for user pg_admin password=hunter2"
        out = _sanitize_error(msg, params={})
        assert "hunter2" not in out
        assert "password=" not in out

    def test_scrubs_url_credentials(self):
        msg = "connection failed: postgres://admin:secret123@db.example.com/prod"
        out = _sanitize_error(msg, params={})
        assert "secret123" not in out
        assert "admin:secret123" not in out

    def test_scrubs_param_values(self):
        msg = "bad credentials for token abcd-1234-xyz"
        out = _sanitize_error(msg, params={"token": "abcd-1234-xyz"})
        assert "abcd-1234-xyz" not in out

    def test_preserves_non_credential_message(self):
        msg = "host db.example.com unreachable"
        out = _sanitize_error(msg, params={})
        assert "db.example.com unreachable" in out

    def test_scrubs_bearer_tokens(self):
        msg = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.token.sig"
        out = _sanitize_error(msg, params={})
        assert "eyJhbGc" not in out
