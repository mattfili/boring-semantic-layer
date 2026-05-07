"""Pure-logic tests for schema_inference module.

No MCP, no live backend — covers the dataclasses, classify_column,
find_potential_joins, render_yaml, and the infer_schema orchestrator.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import ibis.expr.datatypes as dt
import pytest

from boring_semantic_layer.schema_inference import (
    ColumnClassification,
    PotentialJoin,
    ProposedSchema,
    find_potential_joins,
)


class TestDataclasses:
    def test_column_classification_is_frozen(self):
        c = ColumnClassification(
            column="origin",
            dtype="string",
            classification="dimension",
            aggregation=None,
            is_time_dimension=False,
            smallest_time_grain=None,
            description="Origin",
            reasoning="string dtype",
        )
        assert c.column == "origin"
        # Frozen — assignment must raise
        with pytest.raises(FrozenInstanceError):
            c.column = "other"

    def test_potential_join_fields(self):
        j = PotentialJoin(
            column="carrier_id",
            matches_model="carriers",
            matches_dimension="id",
            suggested_type="one",
            reasoning="prefix match: carriers",
        )
        assert j.suggested_type == "one"

    def test_proposed_schema_fields(self):
        s = ProposedSchema(
            table_name="flights",
            description="Flight data",
            columns=[],
            potential_joins=[],
            proposed_yaml="flights:\n  table: flights\n",
        )
        assert s.table_name == "flights"


class TestClassifyColumn:
    """Spec section 4.1 classification grid — covers dtype × naming patterns."""

    @pytest.mark.parametrize(
        "name, dtype, expected_class, expected_agg, expected_is_time, expected_grain",
        [
            # Bool dtype → dimension
            ("is_active", dt.boolean, "dimension", None, False, None),
            # String dtype → dimension
            ("origin", dt.string, "dimension", None, False, None),
            ("name", dt.string, "dimension", None, False, None),
            # Date dtype → time dimension at DAY grain
            ("flight_date", dt.date, "dimension", None, True, "TIME_GRAIN_DAY"),
            # Timestamp dtype → time dimension at SECOND grain
            ("created_at", dt.timestamp, "dimension", None, True, "TIME_GRAIN_SECOND"),
            # Identifier-shaped numerics stay dimensions, never become measures
            ("id", dt.int64, "dimension", None, False, None),
            ("carrier_id", dt.int64, "dimension", None, False, None),
            ("user_key", dt.int64, "dimension", None, False, None),
            ("region_code", dt.int64, "dimension", None, False, None),
            ("ein", dt.int64, "measure", "sum", False, None),  # no id/key/code suffix → measure
            # Numeric with sum-suffix names
            ("flight_count", dt.int64, "measure", "sum", False, None),
            ("revenue_total", dt.float64, "measure", "sum", False, None),
            ("grant_amount", dt.float64, "measure", "sum", False, None),
            ("dep_delay_sum", dt.float64, "measure", "sum", False, None),
            # Numeric with mean-suffix names
            ("conversion_rate", dt.float64, "measure", "mean", False, None),
            ("ctr_pct", dt.float64, "measure", "mean", False, None),
            ("error_percent", dt.float64, "measure", "mean", False, None),
            ("cost_ratio", dt.float64, "measure", "mean", False, None),
            ("avg_delay_avg", dt.float64, "measure", "mean", False, None),
            ("price_mean", dt.float64, "measure", "mean", False, None),
            # Default numeric fallback → measure with sum
            ("revenue", dt.float64, "measure", "sum", False, None),
            ("delay", dt.int64, "measure", "sum", False, None),
        ],
    )
    def test_classification_grid(
        self, name, dtype, expected_class, expected_agg, expected_is_time, expected_grain
    ):
        from boring_semantic_layer.schema_inference import classify_column

        c = classify_column(name, dtype)
        assert c.column == name
        assert c.classification == expected_class, c.reasoning
        assert c.aggregation == expected_agg
        assert c.is_time_dimension == expected_is_time
        assert c.smallest_time_grain == expected_grain
        # Reasoning must explain the pick (used by agents to override)
        assert c.reasoning, f"reasoning missing for {name}"


def _fake_model(name: str, dimension_names: list[str]):
    """Construct a fake SemanticModel-like object with a get_dimensions() map."""
    m = MagicMock()
    m.get_dimensions.return_value = {n: MagicMock() for n in dimension_names}
    return m


class TestFindPotentialJoins:
    def _cols(self, *names_with_pattern):
        """Helper: build ColumnClassification list for FK-shaped names."""
        return [
            ColumnClassification(
                column=n,
                dtype="int64",
                classification="dimension",
                aggregation=None,
                is_time_dimension=False,
                smallest_time_grain=None,
                description="",
                reasoning="",
            )
            for n in names_with_pattern
        ]

    def test_empty_registry_returns_empty(self):
        assert find_potential_joins(self._cols("carrier_id"), {}) == []

    def test_single_match_plural_form(self):
        existing = {"carriers": _fake_model("carriers", ["id", "name"])}
        joins = find_potential_joins(self._cols("carrier_id"), existing)
        assert len(joins) == 1
        assert joins[0].column == "carrier_id"
        assert joins[0].matches_model == "carriers"
        assert joins[0].matches_dimension == "id"
        assert joins[0].suggested_type == "one"
        assert "carriers" in joins[0].reasoning

    def test_single_match_singular_form(self):
        existing = {"carrier": _fake_model("carrier", ["id"])}
        joins = find_potential_joins(self._cols("carrier_id"), existing)
        assert len(joins) == 1
        assert joins[0].matches_model == "carrier"

    def test_dim_priority_id_over_others(self):
        # Spec: priority is `id` > `<model>_id` > `code` > first dim
        existing = {"carriers": _fake_model("carriers", ["code", "id", "name"])}
        joins = find_potential_joins(self._cols("carrier_id"), existing)
        assert joins[0].matches_dimension == "id"

    def test_dim_priority_model_id_when_no_plain_id(self):
        existing = {"carriers": _fake_model("carriers", ["carrier_id", "name"])}
        joins = find_potential_joins(self._cols("carrier_id"), existing)
        assert joins[0].matches_dimension == "carrier_id"

    def test_dim_priority_code_when_no_id(self):
        existing = {"carriers": _fake_model("carriers", ["code", "name"])}
        joins = find_potential_joins(self._cols("carrier_id"), existing)
        assert joins[0].matches_dimension == "code"

    def test_dim_priority_first_dim_fallback(self):
        existing = {"carriers": _fake_model("carriers", ["alpha", "beta"])}
        joins = find_potential_joins(self._cols("carrier_id"), existing)
        assert joins[0].matches_dimension == "alpha"

    def test_no_matching_model_returns_empty(self):
        existing = {"orders": _fake_model("orders", ["id"])}
        assert find_potential_joins(self._cols("carrier_id"), existing) == []

    def test_handles_key_and_code_suffix(self):
        existing = {"users": _fake_model("users", ["id"])}
        joins = find_potential_joins(self._cols("user_key"), existing)
        assert len(joins) == 1
        assert joins[0].matches_model == "users"

    def test_skips_non_fk_shaped_columns(self):
        # `revenue` doesn't end in _id/_key/_code → not considered for join
        cols = [
            ColumnClassification(
                column="revenue",
                dtype="float64",
                classification="measure",
                aggregation="sum",
                is_time_dimension=False,
                smallest_time_grain=None,
                description="",
                reasoning="",
            )
        ]
        existing = {"revenues": _fake_model("revenues", ["id"])}
        assert find_potential_joins(cols, existing) == []
