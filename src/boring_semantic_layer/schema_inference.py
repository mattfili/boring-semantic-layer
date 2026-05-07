"""Pure-Python heuristics for inferring BSL semantic models from raw schemas.

Takes an ibis schema + sample data and produces a :class:`ProposedSchema` with
column classifications, potential joins to existing models, and ready-to-append
YAML. The output is consumed by the MCP ``infer_schema`` tool but the module has
no FastMCP dependency — it can be used standalone.

All heuristics are best-effort. Every classification surfaces a ``reasoning``
field so an agent can review and override before persisting the YAML.
"""

from __future__ import annotations

import re
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


# ---------------------------------------------------------------------------
# Column classification heuristics
# ---------------------------------------------------------------------------

# Identifier-shaped column names — these stay dimensions even when numeric
_ID_PATTERN = re.compile(r"^id$|.*_id$|.*_key$|.*_code$", re.IGNORECASE)
# Sum-shaped suffixes
_SUM_PATTERN = re.compile(r".*(_count|_total|_amount|_sum)$", re.IGNORECASE)
# Mean-shaped suffixes
_MEAN_PATTERN = re.compile(r".*(_rate|_pct|_percent|_ratio|_avg|_mean)$", re.IGNORECASE)


def _humanize(name: str) -> str:
    """Convert snake_case to Title Case with common abbreviation fixups."""
    abbreviations = {"id": "ID", "url": "URL", "uri": "URI", "ein": "EIN", "ssn": "SSN"}
    parts = name.split("_")
    out = []
    for p in parts:
        lower = p.lower()
        if lower in abbreviations:
            out.append(abbreviations[lower])
        else:
            out.append(p.capitalize())
    return " ".join(out)


def classify_column(name: str, ibis_dtype) -> ColumnClassification:
    """Classify one column. Never raises — ambiguity goes into ``reasoning``.

    Heuristic order (first match wins):
      1. Bool → dimension
      2. String → dimension
      3. Date → time dimension (DAY grain)
      4. Timestamp → time dimension (SECOND grain)
      5. Numeric + ID-shape name → dimension (identifier)
      6. Numeric + sum-shape name → measure (sum)
      7. Numeric + mean-shape name → measure (mean)
      8. Numeric default → measure (sum)
      9. Anything else → dimension (conservative fallback)
    """
    dtype_str = str(ibis_dtype)
    description = _humanize(name)

    if ibis_dtype.is_boolean():
        return ColumnClassification(
            column=name,
            dtype=dtype_str,
            classification="dimension",
            aggregation=None,
            is_time_dimension=False,
            smallest_time_grain=None,
            description=description,
            reasoning="bool dtype → dimension",
        )

    if ibis_dtype.is_string():
        return ColumnClassification(
            column=name,
            dtype=dtype_str,
            classification="dimension",
            aggregation=None,
            is_time_dimension=False,
            smallest_time_grain=None,
            description=description,
            reasoning="string dtype → dimension",
        )

    if ibis_dtype.is_date():
        return ColumnClassification(
            column=name,
            dtype=dtype_str,
            classification="dimension",
            aggregation=None,
            is_time_dimension=True,
            smallest_time_grain="TIME_GRAIN_DAY",
            description=description,
            reasoning="date dtype → time dimension at DAY grain",
        )

    if ibis_dtype.is_timestamp():
        return ColumnClassification(
            column=name,
            dtype=dtype_str,
            classification="dimension",
            aggregation=None,
            is_time_dimension=True,
            smallest_time_grain="TIME_GRAIN_SECOND",
            description=description,
            reasoning="timestamp dtype → time dimension at SECOND grain",
        )

    if ibis_dtype.is_numeric():
        if _ID_PATTERN.match(name):
            return ColumnClassification(
                column=name,
                dtype=dtype_str,
                classification="dimension",
                aggregation=None,
                is_time_dimension=False,
                smallest_time_grain=None,
                description=description,
                reasoning="numeric + id/key/code suffix → identifier dimension",
            )
        if _SUM_PATTERN.match(name):
            return ColumnClassification(
                column=name,
                dtype=dtype_str,
                classification="measure",
                aggregation="sum",
                is_time_dimension=False,
                smallest_time_grain=None,
                description=description,
                reasoning="numeric + count/total/amount/sum suffix → sum measure",
            )
        if _MEAN_PATTERN.match(name):
            return ColumnClassification(
                column=name,
                dtype=dtype_str,
                classification="measure",
                aggregation="mean",
                is_time_dimension=False,
                smallest_time_grain=None,
                description=description,
                reasoning="numeric + rate/pct/ratio/avg/mean suffix → mean measure",
            )
        return ColumnClassification(
            column=name,
            dtype=dtype_str,
            classification="measure",
            aggregation="sum",
            is_time_dimension=False,
            smallest_time_grain=None,
            description=description,
            reasoning="numeric default → sum measure",
        )

    # Conservative fallback — unknown dtype → dimension
    return ColumnClassification(
        column=name,
        dtype=dtype_str,
        classification="dimension",
        aggregation=None,
        is_time_dimension=False,
        smallest_time_grain=None,
        description=description,
        reasoning=f"unknown dtype '{dtype_str}' → fallback dimension",
    )
