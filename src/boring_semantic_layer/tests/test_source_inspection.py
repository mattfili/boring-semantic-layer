"""Tests for _source_inspection: open_backend, list_tables_with_counts,
build_profile_yaml, open_transient_duckdb_for_file, _sanitize_error.
"""

from __future__ import annotations

import pytest

from boring_semantic_layer.agents.backends._source_inspection import open_backend


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
