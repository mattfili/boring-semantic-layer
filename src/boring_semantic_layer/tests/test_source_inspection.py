"""Tests for _source_inspection: open_backend, list_tables_with_counts,
build_profile_yaml, open_transient_duckdb_for_file, _sanitize_error.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from boring_semantic_layer.agents.backends._source_inspection import (
    TableSummary,
    list_tables_with_counts,
    open_backend,
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

    def test_table_summary_is_frozen_dataclass(self):
        t = TableSummary(name="foo", row_count=10, count_error=None)
        with pytest.raises(FrozenInstanceError):
            t.name = "bar"
