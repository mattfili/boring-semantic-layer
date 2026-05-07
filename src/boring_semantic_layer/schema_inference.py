"""Pure-Python heuristics for inferring BSL semantic models from raw schemas.

Takes an ibis schema + sample data and produces a :class:`ProposedSchema` with
column classifications, potential joins to existing models, and ready-to-append
YAML. The output is consumed by the MCP ``infer_schema`` tool but the module has
no FastMCP dependency — it can be used standalone.

All heuristics are best-effort. Every classification surfaces a ``reasoning``
field so an agent can review and override before persisting the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ColumnClassification:
    """One column's inferred semantic role.

    Attributes:
        column: Column name.
        dtype: ibis dtype as a string (e.g. ``"int64"``, ``"timestamp"``).
        classification: Either ``"dimension"`` or ``"measure"``.
        aggregation: For measures, the suggested aggregation
            (``"sum"``/``"count"``/``"mean"``); ``None`` for dimensions.
        is_time_dimension: True for date/timestamp dimensions; drives the YAML
            ``is_time_dimension`` flag.
        smallest_time_grain: ``"TIME_GRAIN_DAY"`` for dates,
            ``"TIME_GRAIN_SECOND"`` for timestamps; ``None`` otherwise.
        description: Human-readable description (snake_case -> Title Case).
        reasoning: Why this classification was picked. Surfaced to the agent.
    """

    column: str
    dtype: str
    classification: Literal["dimension", "measure"]
    aggregation: str | None
    is_time_dimension: bool
    smallest_time_grain: str | None
    description: str
    reasoning: str


@dataclass(frozen=True)
class PotentialJoin:
    """A suggested join between this proposed model and an existing one.

    Attributes:
        column: FK-shaped column on the new model (e.g. ``carrier_id``).
        matches_model: Existing model name (e.g. ``carriers``).
        matches_dimension: Dimension name in the matched model.
        suggested_type: ``"one"`` by default; agent can flip to ``"many"``/``"cross"``.
        reasoning: Which prefix matched and which dim-rule fired.
    """

    column: str
    matches_model: str
    matches_dimension: str
    suggested_type: str
    reasoning: str


@dataclass(frozen=True)
class ProposedSchema:
    """Full result of :func:`infer_schema`: classifications, joins, rendered YAML."""

    table_name: str
    description: str
    columns: list[ColumnClassification]
    potential_joins: list[PotentialJoin]
    proposed_yaml: str
