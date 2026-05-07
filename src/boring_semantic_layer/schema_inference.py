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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .expr import SemanticModel


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


# ---------------------------------------------------------------------------
# Join inference heuristics
# ---------------------------------------------------------------------------

_FK_SUFFIX = re.compile(r"^(?P<prefix>.+?)_(id|key|code)$", re.IGNORECASE)


def _strip_fk_suffix(name: str) -> str | None:
    """Return the prefix for an FK-shaped name (``carrier_id`` → ``carrier``), else None."""
    m = _FK_SUFFIX.match(name)
    return m.group("prefix") if m else None


def _pick_join_dimension(model_dim_names: list[str], target_model: str) -> str | None:
    """Walk the priority list and return the first matching dim name.

    Order: ``id`` > ``<target_model>_id`` > ``code`` > first dim.
    """
    if "id" in model_dim_names:
        return "id"
    candidate = f"{target_model}_id"
    if candidate in model_dim_names:
        return candidate
    if "code" in model_dim_names:
        return "code"
    if model_dim_names:
        return model_dim_names[0]
    return None


def find_potential_joins(
    columns: list[ColumnClassification],
    existing_models: Mapping[str, SemanticModel],
) -> list[PotentialJoin]:
    """Surface likely joins between FK-shaped columns and existing models.

    For each new-schema column ending in ``_id``/``_key``/``_code``:
      1. Strip the suffix to get a prefix (``carrier_id`` → ``carrier``).
      2. Match the prefix against existing model names in singular/plural form.
      3. Pick a target dim by priority (id > <model>_id > code > first dim).
      4. Default ``suggested_type="one"`` (FK→PK is the common case).
    """
    if not existing_models:
        return []

    joins: list[PotentialJoin] = []
    for col in columns:
        prefix = _strip_fk_suffix(col.column)
        if prefix is None:
            continue

        # Match singular and plural forms
        candidates = {prefix.lower(), f"{prefix.lower()}s"}
        matched_model: str | None = None
        for model_name in existing_models:
            if model_name.lower() in candidates:
                matched_model = model_name
                break
        if matched_model is None:
            continue

        target = existing_models[matched_model]
        try:
            dim_names = list(target.get_dimensions().keys())
        except Exception:
            dim_names = []
        target_dim = _pick_join_dimension(dim_names, matched_model)
        if target_dim is None:
            continue

        joins.append(
            PotentialJoin(
                column=col.column,
                matches_model=matched_model,
                matches_dimension=target_dim,
                suggested_type="one",
                reasoning=(
                    f"FK prefix '{prefix}' matched model '{matched_model}'; "
                    f"picked dim '{target_dim}'"
                ),
            )
        )

    return joins


# ---------------------------------------------------------------------------
# YAML rendering
# ---------------------------------------------------------------------------


def _yaml_scalar(s: str) -> str:
    """Quote a string for safe single-line YAML emission. Always double-quote."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _measure_expr(col: ColumnClassification) -> str:
    """Build the BSL ``_`` expression for a measure given its aggregation."""
    if col.aggregation == "count":
        return "_.count()"
    return f"_.{col.column}.{col.aggregation}()"


def render_yaml(proposed: ProposedSchema, profile: str | None = None) -> str:
    """Render ONLY the new model block as YAML — no ``profile:`` header.

    The ``profile`` arg is accepted for API symmetry but currently unused;
    the caller appends to a YAML file that already has its own ``profile:``.
    Returns a string ending in a newline so it appends cleanly.
    """
    lines: list[str] = []
    lines.append(f"{proposed.table_name}:")
    if proposed.description:
        lines.append(f"  description: {_yaml_scalar(proposed.description)}")
    lines.append(f"  table: {proposed.table_name}")

    # Dimensions — always extended form (`expr:` + `description:`)
    dims = [c for c in proposed.columns if c.classification == "dimension"]
    if dims:
        lines.append("  dimensions:")
        for d in dims:
            lines.append(f"    {d.column}:")
            lines.append(f"      expr: _.{d.column}")
            if d.description:
                lines.append(f"      description: {_yaml_scalar(d.description)}")
            if d.is_time_dimension:
                lines.append("      is_time_dimension: true")
                if d.smallest_time_grain:
                    lines.append(f"      smallest_time_grain: {d.smallest_time_grain}")

    # Measures
    measures = [c for c in proposed.columns if c.classification == "measure"]
    if measures:
        lines.append("  measures:")
        for m in measures:
            lines.append(f"    {m.column}:")
            lines.append(f"      expr: {_measure_expr(m)}")
            if m.description:
                lines.append(f"      description: {_yaml_scalar(m.description)}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def infer_schema(
    table_name: str,
    ibis_table,
    existing_models: Mapping[str, SemanticModel],
    *,
    description: str | None = None,
    profile: str | None = None,
) -> ProposedSchema:
    """Top-level orchestrator: schema → classifications → joins → YAML.

    Args:
        table_name: Name to use for the new model in the YAML.
        ibis_table: An ibis table expression (already bound to a backend).
        existing_models: Mapping of existing semantic models, used for
            join-target detection. Pass ``{}`` for greenfield.
        description: Optional model-level description; defaults to humanized name.
        profile: Currently unused — accepted for API symmetry.
    """
    schema = ibis_table.schema()
    columns = [classify_column(name, dtype) for name, dtype in schema.items()]

    joins = find_potential_joins(columns, existing_models)
    final_description = description if description else _humanize(table_name)

    proposed = ProposedSchema(
        table_name=table_name,
        description=final_description,
        columns=columns,
        potential_joins=joins,
        proposed_yaml="",
    )

    rendered = render_yaml(proposed, profile=profile)
    # Replace the empty proposed_yaml — frozen dataclass requires re-construction
    return ProposedSchema(
        table_name=proposed.table_name,
        description=proposed.description,
        columns=proposed.columns,
        potential_joins=proposed.potential_joins,
        proposed_yaml=rendered,
    )
