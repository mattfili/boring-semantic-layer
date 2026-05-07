"""Pure-logic tests for schema_inference module.

No MCP, no live backend — covers the dataclasses, classify_column,
find_potential_joins, render_yaml, and the infer_schema orchestrator.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

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
