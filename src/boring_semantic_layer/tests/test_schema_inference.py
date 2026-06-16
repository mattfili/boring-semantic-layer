"""Pure-logic tests for schema_inference module.

No MCP, no live backend — covers the dataclasses, classify_column,
find_potential_joins, render_yaml, and the infer_schema orchestrator.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from io import StringIO
from unittest.mock import MagicMock

import ibis
import ibis.expr.datatypes as dt
import pandas as pd
import pytest
import yaml as _pyyaml

from boring_semantic_layer.schema_inference import (
    ColumnClassification,
    PotentialJoin,
    ProposedSchema,
    find_potential_joins,
    infer_schema,
    render_yaml,
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


class TestRenderYaml:
    """Spec section 4.1: render_yaml emits ONLY the new model block (no profile: header)."""

    def _proposed(self, **overrides):
        defaults = dict(
            table_name="flights",
            description="Flight data",
            columns=[
                ColumnClassification(
                    column="origin",
                    dtype="string",
                    classification="dimension",
                    aggregation=None,
                    is_time_dimension=False,
                    smallest_time_grain=None,
                    description="Origin",
                    reasoning="string",
                ),
                ColumnClassification(
                    column="flight_date",
                    dtype="date",
                    classification="dimension",
                    aggregation=None,
                    is_time_dimension=True,
                    smallest_time_grain="TIME_GRAIN_DAY",
                    description="Flight Date",
                    reasoning="date",
                ),
                ColumnClassification(
                    column="flight_count",
                    dtype="int64",
                    classification="measure",
                    aggregation="sum",
                    is_time_dimension=False,
                    smallest_time_grain=None,
                    description="Flight Count",
                    reasoning="sum suffix",
                ),
            ],
            potential_joins=[],
            proposed_yaml="",  # filled by render
        )
        defaults.update(overrides)
        return ProposedSchema(**defaults)

    def test_no_profile_header_emitted(self):
        rendered = render_yaml(self._proposed(), profile=None)
        # Top-level keys must not include `profile:` — output is appendable
        parsed = _pyyaml.safe_load(StringIO(rendered))
        assert "profile" not in parsed
        assert "flights" in parsed

    def test_emits_table_field(self):
        rendered = render_yaml(self._proposed(), profile=None)
        parsed = _pyyaml.safe_load(StringIO(rendered))
        assert parsed["flights"]["table"] == "flights"

    def test_dimensions_emitted_with_extended_form(self):
        rendered = render_yaml(self._proposed(), profile=None)
        parsed = _pyyaml.safe_load(StringIO(rendered))
        dims = parsed["flights"]["dimensions"]
        assert dims["origin"]["expr"] == "_.origin"
        assert dims["origin"]["description"] == "Origin"

    def test_time_dimension_flags_set(self):
        rendered = render_yaml(self._proposed(), profile=None)
        parsed = _pyyaml.safe_load(StringIO(rendered))
        fd = parsed["flights"]["dimensions"]["flight_date"]
        assert fd["is_time_dimension"] is True
        assert fd["smallest_time_grain"] == "TIME_GRAIN_DAY"

    def test_measures_emit_aggregation_expr(self):
        rendered = render_yaml(self._proposed(), profile=None)
        parsed = _pyyaml.safe_load(StringIO(rendered))
        m = parsed["flights"]["measures"]
        assert m["flight_count"]["expr"] == "_.flight_count.sum()"
        assert m["flight_count"]["description"] == "Flight Count"

    def test_count_measure_uses_count_aggregation(self):
        cols = [
            ColumnClassification(
                column="row_count",
                dtype="int64",
                classification="measure",
                aggregation="count",
                is_time_dimension=False,
                smallest_time_grain=None,
                description="Row Count",
                reasoning="",
            )
        ]
        rendered = render_yaml(self._proposed(columns=cols), profile=None)
        parsed = _pyyaml.safe_load(StringIO(rendered))
        # count is special-cased — no column reference
        assert parsed["flights"]["measures"]["row_count"]["expr"] == "_.count()"

    def test_description_emitted_at_model_level(self):
        rendered = render_yaml(self._proposed(), profile=None)
        parsed = _pyyaml.safe_load(StringIO(rendered))
        assert parsed["flights"]["description"] == "Flight data"

    def test_round_trip_through_from_yaml_shape(self):
        """Sanity check — emitted YAML parses to a dict matching from_yaml's input shape."""
        rendered = render_yaml(self._proposed(), profile=None)
        parsed = _pyyaml.safe_load(StringIO(rendered))
        flights = parsed["flights"]
        assert isinstance(flights, dict)
        assert "dimensions" in flights
        assert "measures" in flights
        # Every dimension dict has an `expr` field
        for dim_cfg in flights["dimensions"].values():
            assert "expr" in dim_cfg
        for meas_cfg in flights["measures"].values():
            assert "expr" in meas_cfg


@pytest.fixture(scope="module")
def duckdb_con():
    return ibis.duckdb.connect(":memory:")


@pytest.fixture(scope="module")
def flights_table(duckdb_con):
    df = pd.DataFrame(
        {
            "carrier_id": [1, 2, 3],
            "origin": ["JFK", "LAX", "ORD"],
            "flight_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]).date,
            "dep_delay": [5.0, 10.0, 0.0],
        }
    )
    duckdb_con.create_table("infer_flights", df, overwrite=True)
    return duckdb_con.table("infer_flights")


class TestInferSchema:
    def test_infer_returns_proposed_schema(self, flights_table):
        result = infer_schema("flights", flights_table, existing_models={})
        assert isinstance(result, ProposedSchema)
        assert result.table_name == "flights"

    def test_columns_classified_correctly(self, flights_table):
        result = infer_schema("flights", flights_table, existing_models={})
        by_name = {c.column: c for c in result.columns}
        assert by_name["carrier_id"].classification == "dimension"
        assert by_name["origin"].classification == "dimension"
        assert by_name["flight_date"].is_time_dimension is True
        assert by_name["dep_delay"].classification == "measure"

    def test_potential_join_surfaced_when_target_exists(self, flights_table):
        existing = {"carriers": _fake_model("carriers", ["id", "name"])}
        result = infer_schema("flights", flights_table, existing_models=existing)
        assert len(result.potential_joins) == 1
        assert result.potential_joins[0].matches_model == "carriers"

    def test_no_joins_when_registry_empty(self, flights_table):
        result = infer_schema("flights", flights_table, existing_models={})
        assert result.potential_joins == []

    def test_proposed_yaml_is_loadable(self, flights_table):
        result = infer_schema("flights", flights_table, existing_models={})
        parsed = _pyyaml.safe_load(StringIO(result.proposed_yaml))
        assert "flights" in parsed
        assert "dimensions" in parsed["flights"]
        assert "measures" in parsed["flights"]

    def test_description_falls_back_to_humanized_name(self, flights_table):
        result = infer_schema("flights", flights_table, existing_models={})
        # Default description: humanized table name
        assert "Flights" in result.description or "flights" in result.description.lower()

    def test_explicit_description_used(self, flights_table):
        result = infer_schema(
            "flights", flights_table, existing_models={}, description="Flight records 2024"
        )
        assert result.description == "Flight records 2024"
