"""Pure-logic tests for schema_inference module.

No MCP, no live backend — covers the dataclasses, classify_column,
find_potential_joins, render_yaml, and the infer_schema orchestrator.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import ibis.expr.datatypes as dt
import pytest

from boring_semantic_layer.schema_inference import (
    ColumnClassification,
    PotentialJoin,
    ProposedSchema,
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
