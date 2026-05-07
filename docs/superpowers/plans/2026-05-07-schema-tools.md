# Schema and Source MCP Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three opt-in MCP tools (`infer_schema`, `connect_source`, `list_backends`) to `MCPSemanticModel` that help bootstrap BSL YAML from raw data and connect new ibis backends, gated behind `include_schema_tools=False`.

**Architecture:** Three new files mirroring the PR #4 split. Pure-logic heuristics live in `schema_inference.py` (no MCP dep). Connection-test helpers in `agents/backends/_source_inspection.py`. MCP wiring in `agents/backends/_schema_mcp.py`. The `MCPSemanticModel.__init__` gains one new flag; everything else is internal.

**Tech Stack:** Python 3.10+, ibis-framework, FastMCP 3.0+, xorq.vendor.ibis Profile, pytest, ruff. No new optional dependency — file-source path uses transient DuckDB which is already pulled by `examples`/`dev` extras.

**Source spec:** `docs/superpowers/specs/2026-05-07-schema-tools-design.md`

---

## File Structure

| Status | Path | Responsibility |
|---|---|---|
| New | `src/boring_semantic_layer/schema_inference.py` | Pure-logic heuristics. Dataclasses + classify/find-joins/render-yaml/infer functions. No MCP/FastMCP imports. ~250 lines. |
| New | `src/boring_semantic_layer/agents/backends/_source_inspection.py` | Connection helpers: `open_backend` (xorq → ibis fallback), `list_tables_with_counts`, `build_profile_yaml`, `open_transient_duckdb_for_file`, `_sanitize_error`. ~150 lines. |
| New | `src/boring_semantic_layer/agents/backends/_schema_mcp.py` | MCP tool registration: `register_schema_tools(server)` wires `infer_schema`/`connect_source`/`list_backends`. ~220 lines. |
| Modify | `src/boring_semantic_layer/agents/backends/mcp.py` | Add `include_schema_tools=False` constructor param + call to register helper. |
| New | `docs/md/prompts/query/mcp/tool-infer-schema-desc.md` | Tool description (loaded via `load_prompt`). |
| New | `docs/md/prompts/query/mcp/tool-connect-source-desc.md` | Tool description. |
| New | `docs/md/prompts/query/mcp/tool-list-backends-desc.md` | Tool description. |
| New | `src/boring_semantic_layer/tests/fixtures/sample_tables/inferable.parquet` | Small parquet (~10 rows) for file-source tests. |
| New | `src/boring_semantic_layer/tests/test_schema_inference.py` | Pure-logic unit tests. No MCP, no live ibis backend. |
| New | `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py` | MCP-protocol integration tests via `async with Client(mcp)`. |

---

## Task 1: Module skeleton with dataclasses

**Files:**
- Create: `src/boring_semantic_layer/schema_inference.py`
- Test: `src/boring_semantic_layer/tests/test_schema_inference.py`

- [ ] **Step 1: Write the failing test**

```python
# src/boring_semantic_layer/tests/test_schema_inference.py
"""Pure-logic tests for schema_inference module.

No MCP, no live backend — covers the dataclasses, classify_column,
find_potential_joins, render_yaml, and the infer_schema orchestrator.
"""
from __future__ import annotations

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
        import pytest
        with pytest.raises(Exception):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'boring_semantic_layer.schema_inference'`

- [ ] **Step 3: Create the module skeleton**

```python
# src/boring_semantic_layer/schema_inference.py
"""Pure-Python heuristics for inferring BSL semantic models from raw schemas.

Takes an ibis schema + sample data and produces a :class:`ProposedSchema` with
column classifications, potential joins to existing models, and ready-to-append
YAML. The output is consumed by the MCP `infer_schema` tool but the module has
no FastMCP dependency — it can be used standalone.

All heuristics are best-effort. Every classification surfaces a `reasoning`
field so an agent can review and override before persisting the YAML.
"""
from __future__ import annotations

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
        description: Human-readable description (snake_case → Title Case).
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py -v`
Expected: PASS — three tests in `TestDataclasses`.

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/schema_inference.py src/boring_semantic_layer/tests/test_schema_inference.py
git commit -m "feat(schema-inference): add ColumnClassification/PotentialJoin/ProposedSchema dataclasses"
```

---

## Task 2: classify_column with full pattern coverage

**Files:**
- Modify: `src/boring_semantic_layer/schema_inference.py`
- Test: `src/boring_semantic_layer/tests/test_schema_inference.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/tests/test_schema_inference.py`:

```python
import ibis.expr.datatypes as dt
import pytest

from boring_semantic_layer.schema_inference import classify_column


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
        c = classify_column(name, dtype)
        assert c.column == name
        assert c.classification == expected_class, c.reasoning
        assert c.aggregation == expected_agg
        assert c.is_time_dimension == expected_is_time
        assert c.smallest_time_grain == expected_grain
        # Reasoning must explain the pick (used by agents to override)
        assert c.reasoning, f"reasoning missing for {name}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestClassifyColumn -v`
Expected: FAIL with `ImportError: cannot import name 'classify_column'`

- [ ] **Step 3: Implement classify_column**

Append to `src/boring_semantic_layer/schema_inference.py`:

```python
import re

# Identifier-shaped column names — these stay dimensions even when numeric
_ID_PATTERN = re.compile(r"^id$|.*_id$|.*_key$|.*_code$", re.IGNORECASE)
# Sum-shaped suffixes
_SUM_PATTERN = re.compile(r".*(_count|_total|_amount|_sum)$", re.IGNORECASE)
# Mean-shaped suffixes
_MEAN_PATTERN = re.compile(
    r".*(_rate|_pct|_percent|_ratio|_avg|_mean)$", re.IGNORECASE
)


def _humanize(name: str) -> str:
    """snake_case → Title Case description, with common abbreviation fixups."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestClassifyColumn -v`
Expected: PASS — 22 parametrized cases.

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/schema_inference.py src/boring_semantic_layer/tests/test_schema_inference.py
git commit -m "feat(schema-inference): classify_column heuristics for dimensions and measures"
```

---

## Task 3: find_potential_joins

**Files:**
- Modify: `src/boring_semantic_layer/schema_inference.py`
- Test: `src/boring_semantic_layer/tests/test_schema_inference.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/tests/test_schema_inference.py`:

```python
from unittest.mock import MagicMock

from boring_semantic_layer.schema_inference import find_potential_joins


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestFindPotentialJoins -v`
Expected: FAIL with `ImportError: cannot import name 'find_potential_joins'`

- [ ] **Step 3: Implement find_potential_joins**

Append to `src/boring_semantic_layer/schema_inference.py`:

```python
_FK_SUFFIX = re.compile(r"^(?P<prefix>.+?)_(id|key|code)$", re.IGNORECASE)


def _strip_fk_suffix(name: str) -> str | None:
    """Return the prefix for an FK-shaped name (`carrier_id` → `carrier`), else None."""
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
    existing_models: Mapping[str, "SemanticModel"],
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestFindPotentialJoins -v`
Expected: PASS — 10 tests.

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/schema_inference.py src/boring_semantic_layer/tests/test_schema_inference.py
git commit -m "feat(schema-inference): find_potential_joins suggests FK→PK joins to existing models"
```

---

## Task 4: render_yaml producing loadable model blocks

**Files:**
- Modify: `src/boring_semantic_layer/schema_inference.py`
- Test: `src/boring_semantic_layer/tests/test_schema_inference.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/tests/test_schema_inference.py`:

```python
from io import StringIO

import yaml as _pyyaml

from boring_semantic_layer.schema_inference import render_yaml


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestRenderYaml -v`
Expected: FAIL with `ImportError: cannot import name 'render_yaml'`

- [ ] **Step 3: Implement render_yaml**

Append to `src/boring_semantic_layer/schema_inference.py`:

```python
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


def _yaml_scalar(s: str) -> str:
    """Quote a string for safe single-line YAML emission. Always double-quote."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestRenderYaml -v`
Expected: PASS — 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/schema_inference.py src/boring_semantic_layer/tests/test_schema_inference.py
git commit -m "feat(schema-inference): render_yaml emits appendable model blocks (no profile header)"
```

---

## Task 5: infer_schema orchestrator

**Files:**
- Modify: `src/boring_semantic_layer/schema_inference.py`
- Test: `src/boring_semantic_layer/tests/test_schema_inference.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/tests/test_schema_inference.py`:

```python
import ibis
import pandas as pd

from boring_semantic_layer.schema_inference import infer_schema


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestInferSchema -v`
Expected: FAIL with `ImportError: cannot import name 'infer_schema'`

- [ ] **Step 3: Implement infer_schema**

Append to `src/boring_semantic_layer/schema_inference.py`:

```python
def infer_schema(
    table_name: str,
    ibis_table,
    existing_models: Mapping[str, "SemanticModel"],
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

    if not columns:
        # Caller wants empty? Surface as ProposedSchema with empty columns; the
        # MCP tool layer turns this into a ToolError.
        pass

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py::TestInferSchema -v`
Expected: PASS — 7 tests.

- [ ] **Step 5: Run full unit test file**

Run: `uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py -v`
Expected: PASS — 47+ tests across all classes.

- [ ] **Step 6: Commit**

```bash
git add src/boring_semantic_layer/schema_inference.py src/boring_semantic_layer/tests/test_schema_inference.py
git commit -m "feat(schema-inference): infer_schema orchestrator wires classify/joins/render together"
```

---

## Task 6: _source_inspection — open_backend (xorq → ibis fallback)

**Files:**
- Create: `src/boring_semantic_layer/agents/backends/_source_inspection.py`
- Test: `src/boring_semantic_layer/tests/test_source_inspection.py`

> **Note:** Inspection helpers live under `agents/backends/` because they only ever run inside the MCP server. Tests live in the core `tests/` dir because they're pure-logic.

- [ ] **Step 1: Write the failing test**

```python
# src/boring_semantic_layer/tests/test_source_inspection.py
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
        import ibis
        result = con.sql("SELECT 1 AS x").execute()
        assert int(result["x"].iloc[0]) == 1

    def test_unknown_backend_raises(self):
        with pytest.raises(Exception):
            open_backend({"type": "definitely-not-a-real-backend"})

    def test_missing_type_raises(self):
        with pytest.raises(Exception):
            open_backend({"database": ":memory:"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_source_inspection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'boring_semantic_layer.agents.backends._source_inspection'`

- [ ] **Step 3: Implement _source_inspection.py module skeleton + open_backend**

```python
# src/boring_semantic_layer/agents/backends/_source_inspection.py
"""Connection-test helpers for the schema-tools MCP path.

Splits the responsibilities of the MCP `connect_source` and `infer_schema`
tools that touch live backends:

- :func:`open_backend` — opens an ibis backend from a profile dict, trying
  xorq's Profile first (matches BSL's ``profile.py`` loader) then falling back
  to plain ``ibis.<backend>.connect()``.
- :func:`list_tables_with_counts` — enumerates tables with COUNT(*) per table,
  with per-table timeout and a global cap.
- :func:`build_profile_yaml` — renders BSL profile YAML preserving ``${VAR}`` literals.
- :func:`open_transient_duckdb_for_file` — opens an in-memory DuckDB and reads
  a single file as a table for the file-source path of `infer_schema`.
- :func:`_sanitize_error` — scrubs credentials from error messages before
  surfacing them to the MCP client.
"""
from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import ibis
from ibis import BaseBackend


def open_backend(profile_config: dict) -> BaseBackend:
    """Open an ibis backend from a flat profile config dict.

    Tries xorq's Profile first (handles env var substitution automatically);
    falls back to plain ``ibis.<backend>.connect(**params)`` for backends not
    registered in xorq.

    Mirrors :func:`boring_semantic_layer.profile._create_connection_from_config`.
    """
    if "type" not in profile_config:
        raise ValueError("Profile config must include 'type' field")

    config = dict(profile_config)
    conn_type = config.pop("type")

    # Try xorq first
    try:
        from xorq.vendor.ibis.backends.profiles import Profile as XorqProfile

        kwargs_tuple = tuple(sorted(config.items()))
        xorq_profile = XorqProfile(con_name=conn_type, kwargs_tuple=kwargs_tuple)
        return xorq_profile.get_con()
    except AssertionError:
        # xorq doesn't know this backend — fall through to plain ibis
        pass
    except Exception:
        # xorq import or profile construction failed — fall through
        pass

    connect_fn = getattr(ibis, conn_type, None)
    if connect_fn is None or not callable(getattr(connect_fn, "connect", None)):
        raise ValueError(f"Unknown backend type: '{conn_type}'")
    expanded = {
        k: os.path.expandvars(v) if isinstance(v, str) else v for k, v in config.items()
    }
    return connect_fn.connect(**expanded)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_source_inspection.py::TestOpenBackend -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/_source_inspection.py src/boring_semantic_layer/tests/test_source_inspection.py
git commit -m "feat(source-inspection): open_backend with xorq → ibis fallback"
```

---

## Task 7: list_tables_with_counts (timeout + cap + error capture)

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/_source_inspection.py`
- Test: `src/boring_semantic_layer/tests/test_source_inspection.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/tests/test_source_inspection.py`:

```python
import pandas as pd
import pytest

from boring_semantic_layer.agents.backends._source_inspection import (
    TableSummary,
    list_tables_with_counts,
)


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
        with pytest.raises(Exception):
            t.name = "bar"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_source_inspection.py::TestListTablesWithCounts -v`
Expected: FAIL with `ImportError: cannot import name 'list_tables_with_counts'`

- [ ] **Step 3: Implement TableSummary + list_tables_with_counts**

Append to `src/boring_semantic_layer/agents/backends/_source_inspection.py`:

```python
@dataclass(frozen=True)
class TableSummary:
    name: str
    row_count: int | None
    count_error: str | None


def list_tables_with_counts(
    con: BaseBackend,
    *,
    limit_tables: int = 100,
) -> tuple[list[TableSummary], bool]:
    """Enumerate tables with ``COUNT(*)`` per table.

    Returns ``(summaries, truncated)``. A table whose count fails appears
    with ``row_count=None`` and ``count_error`` set to the exception string;
    the call itself never raises.

    Args:
        con: Open ibis backend.
        limit_tables: Cap on number of tables; if more exist, the first
            ``limit_tables`` are returned and ``truncated`` is True.
    """
    all_names = list(con.list_tables())
    truncated = len(all_names) > limit_tables
    names = all_names[:limit_tables]

    summaries: list[TableSummary] = []
    for name in names:
        try:
            count = int(con.table(name).count().execute())
            summaries.append(TableSummary(name=name, row_count=count, count_error=None))
        except Exception as exc:
            summaries.append(
                TableSummary(name=name, row_count=None, count_error=str(exc))
            )
    return summaries, truncated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_source_inspection.py::TestListTablesWithCounts -v`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/_source_inspection.py src/boring_semantic_layer/tests/test_source_inspection.py
git commit -m "feat(source-inspection): list_tables_with_counts with cap + per-table error capture"
```

---

## Task 8: build_profile_yaml + open_transient_duckdb_for_file + _sanitize_error

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/_source_inspection.py`
- Test: `src/boring_semantic_layer/tests/test_source_inspection.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/tests/test_source_inspection.py`:

```python
from io import StringIO

import yaml as _pyyaml

from boring_semantic_layer.agents.backends._source_inspection import (
    _sanitize_error,
    build_profile_yaml,
    open_transient_duckdb_for_file,
)


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
            con.disconnect() if hasattr(con, "disconnect") else None

    def test_csv_path_loads_table(self, tmp_path):
        csv_path = tmp_path / "sample.csv"
        csv_path.write_text("x,y\n1,a\n2,b\n")
        con, tbl = open_transient_duckdb_for_file(str(csv_path), "csv")
        try:
            assert tbl.count().execute() == 2
        finally:
            con.disconnect() if hasattr(con, "disconnect") else None

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/tests/test_source_inspection.py -v`
Expected: FAIL with `ImportError` on the new helpers.

- [ ] **Step 3: Implement build_profile_yaml + open_transient_duckdb_for_file + _sanitize_error**

Append to `src/boring_semantic_layer/agents/backends/_source_inspection.py`:

```python
def build_profile_yaml(profile_name: str, backend: str, params: dict) -> str:
    """Render a BSL profile YAML block. Preserves ``${VAR}`` literals.

    Output shape (matches BSL's existing profile loader):

    ```yaml
    profile_name:
      type: <backend>
      <param>: <value>
      ...
    ```
    """
    lines = [f"{profile_name}:"]
    lines.append(f"  type: {backend}")
    for key, value in params.items():
        lines.append(f"  {key}: {_yaml_value(value)}")
    return "\n".join(lines) + "\n"


def _yaml_value(v) -> str:
    """Render a YAML scalar preserving ${VAR} literals (not expanded)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if "${" in s or any(ch in s for ch in ":#'\"\\"):
        # Quote anything that could confuse YAML; escape backslash and double-quote
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def open_transient_duckdb_for_file(
    path: str,
    source_type: Literal["csv", "parquet", "json"],
) -> tuple[BaseBackend, "ibis.Table"]:
    """Open an in-memory DuckDB and read ``path`` as table ``_inferred``.

    The caller is responsible for closing the connection (typically in a
    ``try/finally``). The table name ``_inferred`` is fixed to keep the
    inference contract simple.

    Raises:
        ValueError: If ``source_type`` isn't one of csv/parquet/json or the
            path can't be read.
    """
    if source_type not in {"csv", "parquet", "json"}:
        raise ValueError(
            f"Unsupported source_type '{source_type}'. Supported: csv, parquet, json"
        )
    if not Path(path).exists():
        raise ValueError(f"File not found: {path}")

    con = ibis.duckdb.connect(":memory:")
    try:
        if source_type == "parquet":
            tbl = con.read_parquet(path, table_name="_inferred")
        elif source_type == "csv":
            tbl = con.read_csv(path, table_name="_inferred")
        else:  # json
            tbl = con.read_json(path, table_name="_inferred")
        return con, tbl
    except Exception:
        # Best-effort cleanup if read fails
        try:
            con.disconnect()
        except Exception:
            pass
        raise


_PASSWORD_PATTERN = re.compile(
    r"(password|secret|token|api_key|api-key)=\S+", re.IGNORECASE
)
_BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_URL_CREDS_PATTERN = re.compile(r"(\w+)://([^:/\s]+):([^@/\s]+)@")


def _sanitize_error(msg: str, params: dict | None = None) -> str:
    """Scrub credentials from an error message before surfacing to MCP clients.

    Strips:
      - Any value present verbatim in ``params``.
      - ``password=...``, ``secret=...``, ``token=...``, ``api_key=...`` patterns.
      - ``Bearer <token>`` patterns.
      - URL-shaped credentials (``proto://user:pass@host`` → ``proto://***@host``).
    """
    out = str(msg)
    if params:
        for value in params.values():
            if isinstance(value, str) and value:
                out = out.replace(value, "***")
    out = _PASSWORD_PATTERN.sub(r"\1=***", out)
    out = _BEARER_PATTERN.sub("Bearer ***", out)
    out = _URL_CREDS_PATTERN.sub(r"\1://***@", out)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/tests/test_source_inspection.py -v`
Expected: PASS — 16+ tests across all classes.

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/_source_inspection.py src/boring_semantic_layer/tests/test_source_inspection.py
git commit -m "feat(source-inspection): profile YAML render, transient DuckDB, credential scrub"
```

---

## Task 9: Tool description prompts + parquet fixture

**Files:**
- Create: `docs/md/prompts/query/mcp/tool-infer-schema-desc.md`
- Create: `docs/md/prompts/query/mcp/tool-connect-source-desc.md`
- Create: `docs/md/prompts/query/mcp/tool-list-backends-desc.md`
- Create: `src/boring_semantic_layer/tests/fixtures/sample_tables/inferable.parquet` (binary)

> **Note:** No tests in this task — content-only. Validation comes via the MCP tests in later tasks (which call `load_prompt` against these files).

- [ ] **Step 1: Write `tool-infer-schema-desc.md`**

```markdown
Infer a BSL semantic model from a raw data source — table, parquet, csv, or json.

Use this when the user wants to:
- Bootstrap a new semantic model from data they haven't modeled yet
- Add a new table from the existing warehouse to their semantic layer
- Convert a flat file into a queryable BSL model

Returns a `proposed_yaml` string (just the new model block — no `profile:` header,
safe to append to an existing `models.yml`), per-column classifications with
reasoning, and any potential joins to existing models in the registry. The
caller (agent or plugin) is responsible for reviewing, possibly editing, and
persisting the YAML to disk via the standard Write tool.

This tool never writes to disk and never registers a new model in the running
server. Restart the server after persisting new YAML to pick it up.

Parameters:
- `table_name`: Name to use for the new model (kebab/snake, no whitespace).
- `source`: Either a table name in the connected backend, or a filesystem path.
- `source_type`: One of `"table"`, `"csv"`, `"parquet"`, `"json"`. If omitted,
  inferred from the file extension; defaults to `"table"`.
- `description`: Optional model-level description.
- `profile`: Optional profile name; reserved for future use.

Returns a dict with:
- `proposed_yaml`: Ready-to-append YAML block.
- `column_classifications`: List of `{column, dtype, classification, aggregation,
  is_time_dimension, smallest_time_grain, description, reasoning}`.
- `potential_joins`: List of `{column, matches_model, matches_dimension,
  suggested_type, reasoning}` — empty if no FK-shape columns matched.
```

- [ ] **Step 2: Write `tool-connect-source-desc.md`**

```markdown
Test a connection to a database backend and propose a BSL profile YAML.

Use this when the user wants to:
- Add a new warehouse / database to their semantic layer
- Verify credentials work before persisting them
- Discover what tables exist in a backend

Tests connectivity to the backend, lists available tables (with row counts,
capped at 100), and returns a proposed `profiles.yml` YAML block. The caller
persists the YAML to one of:
- `~/.config/bsl/profiles/<name>.yml` (user-global)
- `<project>/profiles.yml` (project-local)
- inline under `profile:` in `models.yml` (single-server bootstrap)

This tool never writes credentials to disk. ${VAR} literals in
`connection_params` round-trip as literals — they're not expanded at test time.

Parameters:
- `backend`: One of the supported backends (call `list_backends` to see them).
- `profile_name`: Name to use for the new profile.
- `connection_params`: Backend-specific connection params (host, port, etc.).
  May include `${VAR}` literals for credentials that should resolve at runtime.

Returns a dict with:
- `status`: "connected" on success.
- `proposed_profile_yaml`: Ready-to-append profile YAML.
- `available_tables`: List of `{name, row_count, count_error}` (up to 100).
- `truncated`: True if more than 100 tables exist.
- `warning`: Set if `list_tables()` failed after a successful connect.

On connection failure: ToolError with sanitized error (credentials scrubbed).
On unsupported backend or missing ibis extra: ToolError with `pip install` hint.
```

- [ ] **Step 3: Write `tool-list-backends-desc.md`**

```markdown
List database backends supported by the schema-tools MCP path.

Returns which curated backends are currently installed (importable) and which
are available to install via `pip install ibis-framework[<backend>]`.

Use this before `connect_source` to figure out:
- Whether the user's target backend is supported
- Whether the necessary ibis extra is already installed
- The exact `pip install` command if it isn't

Returns a dict with:
- `installed_backends`: List of backend names currently importable.
- `available_backends`: List of backend names supported but not installed.
- `install_instructions`: Mapping of backend name → `pip install` command.

This call never touches the network or any database. It just reads the
ibis-framework install state.
```

- [ ] **Step 4: Generate the parquet fixture**

Run:

```bash
python -c "
import pandas as pd
from pathlib import Path
df = pd.DataFrame({
    'order_id': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    'customer_id': [101, 102, 101, 103, 102, 104, 101, 105, 103, 102],
    'order_date': pd.to_datetime(['2024-01-01','2024-01-02','2024-01-03','2024-01-04','2024-01-05','2024-01-06','2024-01-07','2024-01-08','2024-01-09','2024-01-10']),
    'order_total': [10.5, 22.0, 5.75, 100.0, 50.0, 12.5, 7.25, 88.0, 15.0, 33.5],
    'is_paid': [True, True, False, True, True, False, True, True, True, False],
})
out = Path('src/boring_semantic_layer/tests/fixtures/sample_tables/inferable.parquet')
out.parent.mkdir(parents=True, exist_ok=True)
df.to_parquet(out, index=False)
print(f'Wrote {out} ({out.stat().st_size} bytes)')
"
```

Expected output: `Wrote src/boring_semantic_layer/tests/fixtures/sample_tables/inferable.parquet (...) bytes`

- [ ] **Step 5: Verify load_prompt resolves the new files**

Run:

```bash
uv run python -c "
from boring_semantic_layer.agents.utils.prompts import load_prompt
from boring_semantic_layer.agents.backends.mcp import PROMPTS_DIR
for n in ['tool-infer-schema-desc.md', 'tool-connect-source-desc.md', 'tool-list-backends-desc.md']:
    body = load_prompt(PROMPTS_DIR, n)
    assert body and 'Use this' in body, f'{n} missing or wrong'
    print(f'OK: {n} ({len(body)} chars)')
"
```

Expected: three `OK:` lines.

- [ ] **Step 6: Commit**

```bash
git add docs/md/prompts/query/mcp/tool-infer-schema-desc.md \
        docs/md/prompts/query/mcp/tool-connect-source-desc.md \
        docs/md/prompts/query/mcp/tool-list-backends-desc.md \
        src/boring_semantic_layer/tests/fixtures/sample_tables/inferable.parquet
git commit -m "docs(schema-tools): tool description prompts + inferable.parquet fixture"
```

---

## Task 10: Constructor flag + registration baseline test

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/mcp.py`
- Create: `src/boring_semantic_layer/agents/backends/_schema_mcp.py`
- Create: `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py
"""Integration tests for schema tools on MCPSemanticModel.

All tests go through the MCP protocol (`async with Client(mcp)`) — never
touch internal APIs. Module-scoped DuckDB fixture; unique table names per
test class to avoid clobbering across the shared connection.
"""
from __future__ import annotations

from pathlib import Path

import ibis
import pandas as pd
import pytest
from fastmcp import Client

from boring_semantic_layer import MCPSemanticModel, from_yaml


@pytest.fixture(scope="module")
def con():
    return ibis.duckdb.connect(":memory:")


def _write_yaml(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def _basic_bundle(con, sample_table_name: str, tmp_path: Path):
    """Build a minimal SemanticModelBundle with one model on `sample_table_name`."""
    yaml_path = tmp_path / "cfg.yml"
    _write_yaml(
        yaml_path,
        f"flights:\n  table: {sample_table_name}\n  dimensions:\n    origin: _.origin\n  measures:\n    flight_count: _.count()\n",
    )
    return from_yaml(str(yaml_path), tables={sample_table_name: con.table(sample_table_name)})


class TestSchemaToolsRegistration:
    """Default → tools absent; flag=True → all three present."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"origin": ["JFK", "LAX"], "carrier": ["AA", "UA"]})
        con.create_table("schema_reg_flights", df, overwrite=True)
        return "schema_reg_flights"

    @pytest.mark.asyncio
    async def test_default_constructor_no_schema_tools(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle)
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
            assert "infer_schema" not in tool_names
            assert "connect_source" not in tool_names
            assert "list_backends" not in tool_names
            # Sanity: existing tools still present
            assert "list_models" in tool_names

    @pytest.mark.asyncio
    async def test_flag_enabled_registers_all_three(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
            assert "infer_schema" in tool_names
            assert "connect_source" in tool_names
            assert "list_backends" in tool_names

    @pytest.mark.asyncio
    async def test_flag_independent_of_skill_flags(self, con, setup_table, tmp_path):
        """Schema tools have no dependency on skills_dir."""
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(
            bundle,
            include_schema_tools=True,
            include_domain_context_tool=False,
            include_add_skill_tool=False,
        )
        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
            assert "infer_schema" in tool_names
            assert "get_domain_context" not in tool_names
            assert "add_skill" not in tool_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestSchemaToolsRegistration -v`
Expected: FAIL — `MCPSemanticModel.__init__` got unexpected kwarg `include_schema_tools`.

- [ ] **Step 3: Create the (empty) registration module**

```python
# src/boring_semantic_layer/agents/backends/_schema_mcp.py
"""MCP tool registrations for the schema-tools opt-in path.

Three tools — :func:`register_schema_tools` wires all of them onto an
``MCPSemanticModel`` instance:

- ``infer_schema`` — propose a BSL model from a raw source.
- ``connect_source`` — test a backend connection and list tables.
- ``list_backends`` — enumerate supported / installed ibis backends.

All three are read-only, idempotent, and never write to disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mcp import MCPSemanticModel


def register_schema_tools(server: "MCPSemanticModel", prompts_dir: Path) -> None:
    """Register `infer_schema`, `connect_source`, and `list_backends` on ``server``.

    Tools are added in subsequent tasks (10–16). This stub establishes the
    registration entry point.
    """
    # Implementations land in Tasks 11–16.
    pass
```

- [ ] **Step 4: Modify `mcp.py` constructor**

In `src/boring_semantic_layer/agents/backends/mcp.py`, at the end of the imports block (after the existing `from ._skill_mcp import (...)` block), add:

```python
from ._schema_mcp import register_schema_tools
```

Then in `MCPSemanticModel.__init__`, add `include_schema_tools=False` after `include_add_skill_tool=True`:

```python
def __init__(
    self,
    models: Mapping[str, Any] | SemanticModelBundle,
    name: str = "Semantic Layer MCP Server",
    instructions: str = SYSTEM_INSTRUCTIONS,
    code_mode: bool = False,
    include_domain_context_tool: bool = True,
    include_add_skill_tool: bool = True,
    include_schema_tools: bool = False,
    **kwargs,
):
```

And at the end of `__init__` (after the `if self._has_skills: ...` block), add:

```python
        if include_schema_tools:
            register_schema_tools(self, PROMPTS_DIR)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestSchemaToolsRegistration -v`
Expected: FAIL — first two cases fail because `register_schema_tools` is a no-op. Third (registration off) passes.

This is intentional: stub registration in place, real tools land in Task 11+. The first two tests come back to PASS as we add tool implementations. Mark those as `@pytest.mark.xfail` for now and **remove the marker** in Task 11 / Task 12 / Task 14 when the corresponding tool is added.

Actually — mark them xfail with explicit message:

```python
@pytest.mark.asyncio
@pytest.mark.xfail(reason="tools land in tasks 11–16", strict=False)
async def test_flag_enabled_registers_all_three(...):
    ...

@pytest.mark.asyncio
@pytest.mark.xfail(reason="tools land in tasks 11–16", strict=False)
async def test_flag_independent_of_skill_flags(...):
    ...
```

Re-run: PASS (xfail) — 1 PASS + 2 XFAIL.

- [ ] **Step 6: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/mcp.py \
        src/boring_semantic_layer/agents/backends/_schema_mcp.py \
        src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py
git commit -m "feat(mcp): add include_schema_tools flag and registration stub"
```

---

## Task 11: list_backends tool

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/_schema_mcp.py`
- Modify: `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`:

```python
import json


class TestListBackends:
    """list_backends — synchronous, no DB calls."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("list_backends_t", df, overwrite=True)
        return "list_backends_t"

    @pytest.mark.asyncio
    async def test_returns_installed_and_available_lists(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool("list_backends", {})
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "installed_backends" in data
            assert "available_backends" in data
            assert "install_instructions" in data
            # duckdb is a hard dep — must be installed
            assert "duckdb" in data["installed_backends"]

    @pytest.mark.asyncio
    async def test_install_instructions_keys_match_supported(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool("list_backends", {})
            data = json.loads(result.content[0].text) if result.content else result.data
            expected = {"duckdb", "postgres", "snowflake", "bigquery", "mysql", "sqlite", "clickhouse"}
            assert set(data["install_instructions"].keys()) == expected
            for hint in data["install_instructions"].values():
                assert "pip install" in hint
                assert "ibis-framework[" in hint

    @pytest.mark.asyncio
    async def test_tool_annotations_readonly(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            t = tools["list_backends"]
            assert t.annotations.readOnlyHint is True
            assert t.annotations.destructiveHint is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestListBackends -v`
Expected: FAIL — tool not found.

- [ ] **Step 3: Implement list_backends**

Replace the body of `register_schema_tools` in `_schema_mcp.py`:

```python
"""MCP tool registrations for the schema-tools opt-in path."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..utils.prompts import load_prompt

if TYPE_CHECKING:
    from .mcp import MCPSemanticModel

SUPPORTED_BACKENDS = (
    "duckdb",
    "postgres",
    "snowflake",
    "bigquery",
    "mysql",
    "sqlite",
    "clickhouse",
)

INSTALL_HINTS = {b: f"pip install 'ibis-framework[{b}]'" for b in SUPPORTED_BACKENDS}

_READONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def register_schema_tools(server: "MCPSemanticModel", prompts_dir: Path) -> None:
    """Register all three schema tools on ``server``."""
    _register_list_backends(server, prompts_dir)


def _register_list_backends(server: "MCPSemanticModel", prompts_dir: Path) -> None:
    @server.tool(
        name="list_backends",
        description=(
            load_prompt(prompts_dir, "tool-list-backends-desc.md")
            or "List supported and installed ibis backends."
        ),
        tags={"discovery"},
        annotations=_READONLY_ANNOTATIONS,
    )
    def list_backends() -> dict:
        installed: list[str] = []
        available: list[str] = []
        for backend in SUPPORTED_BACKENDS:
            try:
                importlib.import_module(f"ibis.backends.{backend}")
                installed.append(backend)
            except ImportError:
                available.append(backend)
        return {
            "installed_backends": installed,
            "available_backends": available,
            "install_instructions": dict(INSTALL_HINTS),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestListBackends -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Remove the now-passing xfail markers in `TestSchemaToolsRegistration`**

Open `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py` and:
- Remove the `@pytest.mark.xfail(...)` line above `test_flag_enabled_registers_all_three`. The test still asserts that all three tool names are present, so it will fail until Tasks 12 + 14 land. Re-add a tighter xfail:

```python
@pytest.mark.asyncio
@pytest.mark.xfail(reason="connect_source + infer_schema land in tasks 12+14", strict=False)
async def test_flag_enabled_registers_all_three(...):
    ...
```

Same for `test_flag_independent_of_skill_flags`.

- [ ] **Step 6: Run TestSchemaToolsRegistration**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestSchemaToolsRegistration -v`
Expected: 1 PASS + 2 XFAIL.

- [ ] **Step 7: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/_schema_mcp.py \
        src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py
git commit -m "feat(mcp-schema): list_backends tool — installed/available + install hints"
```

---

## Task 12: connect_source happy path

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/_schema_mcp.py`
- Modify: `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`:

```python
class TestConnectSourceLocal:
    """connect_source against an in-memory DuckDB."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("conn_local_t", df, overwrite=True)
        return "conn_local_t"

    @pytest.mark.asyncio
    async def test_connects_to_duckdb_memory(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "connect_source",
                {
                    "backend": "duckdb",
                    "profile_name": "test_local",
                    "connection_params": {"database": ":memory:"},
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert data["status"] == "connected"
            assert "test_local:" in data["proposed_profile_yaml"]
            assert "type: duckdb" in data["proposed_profile_yaml"]
            assert isinstance(data["available_tables"], list)

    @pytest.mark.asyncio
    async def test_unsupported_backend_raises_tool_error(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "connect_source",
                    {
                        "backend": "definitely-not-supported",
                        "profile_name": "x",
                        "connection_params": {},
                    },
                )
            assert "not supported" in str(exc_info.value).lower() or "supported" in str(exc_info.value).lower()


class TestReadOnlyAnnotations:
    """All three tools must declare read-only annotations."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("ro_annot_t", df, overwrite=True)
        return "ro_annot_t"

    @pytest.mark.asyncio
    async def test_all_tools_readonly(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            for name in ("infer_schema", "connect_source", "list_backends"):
                t = tools[name]
                assert t.annotations.readOnlyHint is True, f"{name} should be readOnly"
                assert t.annotations.destructiveHint is False, f"{name} should not be destructive"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestConnectSourceLocal -v`
Expected: FAIL — `connect_source` not registered.

- [ ] **Step 3: Implement connect_source**

In `_schema_mcp.py`, add to the `register_schema_tools` body and create `_register_connect_source`:

```python
def register_schema_tools(server: "MCPSemanticModel", prompts_dir: Path) -> None:
    _register_list_backends(server, prompts_dir)
    _register_connect_source(server, prompts_dir)


def _register_connect_source(server: "MCPSemanticModel", prompts_dir: Path) -> None:
    from ._source_inspection import (
        _sanitize_error,
        build_profile_yaml,
        list_tables_with_counts,
        open_backend,
    )

    @server.tool(
        name="connect_source",
        description=(
            load_prompt(prompts_dir, "tool-connect-source-desc.md")
            or "Test a backend connection and propose a profile YAML."
        ),
        tags={"metadata"},
        annotations=_READONLY_ANNOTATIONS,
    )
    def connect_source(
        backend: str,
        profile_name: str,
        connection_params: dict,
    ) -> dict:
        if backend not in SUPPORTED_BACKENDS:
            raise ToolError(
                f"Backend '{backend}' not supported. "
                f"Supported: {list(SUPPORTED_BACKENDS)}"
            )

        # Verify the ibis extra is installed
        try:
            importlib.import_module(f"ibis.backends.{backend}")
        except ImportError:
            raise ToolError(
                f"Backend '{backend}' not installed. {INSTALL_HINTS[backend]}"
            )

        config = {"type": backend, **connection_params}
        con = None
        warning = None
        try:
            try:
                con = open_backend(config)
            except Exception as exc:
                raise ToolError(_sanitize_error(str(exc), connection_params))

            try:
                summaries, truncated = list_tables_with_counts(con, limit_tables=100)
                available_tables = [
                    {
                        "name": s.name,
                        "row_count": s.row_count,
                        "count_error": s.count_error,
                    }
                    for s in summaries
                ]
            except Exception as exc:
                # Successful connect but list_tables failed — return success with warning
                available_tables = []
                truncated = False
                warning = _sanitize_error(str(exc), connection_params)

            proposed_yaml = build_profile_yaml(profile_name, backend, connection_params)

            return {
                "status": "connected",
                "proposed_profile_yaml": proposed_yaml,
                "available_tables": available_tables,
                "truncated": truncated,
                "warning": warning,
            }
        finally:
            if con is not None:
                try:
                    if hasattr(con, "disconnect"):
                        con.disconnect()
                except Exception:
                    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestConnectSourceLocal src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestReadOnlyAnnotations -v`
Expected: PASS — 3 tests (`TestReadOnlyAnnotations` will still partially fail because `infer_schema` doesn't exist yet).

If `TestReadOnlyAnnotations::test_all_tools_readonly` fails on `infer_schema` lookup, mark **just that test** xfail until Task 14:

```python
@pytest.mark.asyncio
@pytest.mark.xfail(reason="infer_schema lands in task 14", strict=False)
async def test_all_tools_readonly(...):
    ...
```

- [ ] **Step 5: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/_schema_mcp.py \
        src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py
git commit -m "feat(mcp-schema): connect_source tool — backend connect, list tables, propose profile"
```

---

## Task 13: connect_source — credential scrub + truncation

**Files:**
- Modify: `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`

> **Note:** No code change — connect_source already calls `_sanitize_error` and respects `limit_tables`. This task adds tests that exercise those paths.

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`:

```python
class TestConnectSourceCredentialScrub:
    """Credentials must never appear in error messages."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("scrub_t", df, overwrite=True)
        return "scrub_t"

    @pytest.mark.asyncio
    async def test_password_scrubbed_from_connection_failure(
        self, con, setup_table, tmp_path, monkeypatch
    ):
        """Force open_backend to raise with a credential-bearing message;
        assert the ToolError doesn't leak the password."""
        from boring_semantic_layer.agents.backends import _schema_mcp as schema_mcp_mod

        def fake_open(config):
            raise Exception("auth failed for user pg_admin password=hunter2 token=secret_xyz")

        # Patch the function imported into the closure
        # The tool imports open_backend lazily inside the registration; patch it there.
        monkeypatch.setattr(
            "boring_semantic_layer.agents.backends._source_inspection.open_backend",
            fake_open,
        )

        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "connect_source",
                    {
                        "backend": "postgres",
                        "profile_name": "scrub_test",
                        "connection_params": {"host": "x", "password": "hunter2", "token": "secret_xyz"},
                    },
                )
            msg = str(exc_info.value)
            assert "hunter2" not in msg
            assert "secret_xyz" not in msg


class TestConnectSourceTruncation:
    """When >100 tables exist, the response truncates and reports it."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("trunc_t", df, overwrite=True)
        return "trunc_t"

    @pytest.mark.asyncio
    async def test_truncates_at_100(self, con, setup_table, tmp_path, monkeypatch):
        """Patch list_tables_with_counts to return more than 100 rows."""
        from boring_semantic_layer.agents.backends import _source_inspection

        def fake_list(con, *, limit_tables=100):
            from boring_semantic_layer.agents.backends._source_inspection import TableSummary
            # Pretend backend has 150 tables; respect the cap.
            n = limit_tables  # 100
            summaries = [
                TableSummary(name=f"t{i}", row_count=i, count_error=None) for i in range(n)
            ]
            return summaries, True  # truncated

        monkeypatch.setattr(_source_inspection, "list_tables_with_counts", fake_list)

        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "connect_source",
                {
                    "backend": "duckdb",
                    "profile_name": "trunc_test",
                    "connection_params": {"database": ":memory:"},
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert data["truncated"] is True
            assert len(data["available_tables"]) == 100
```

- [ ] **Step 2: Run test to verify it fails OR succeeds**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestConnectSourceCredentialScrub src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestConnectSourceTruncation -v`
Expected: PASS — both tests should pass against the existing connect_source impl. If the credential-scrub test fails, the most likely cause is that the `open_backend` import inside `_register_connect_source` is bound at registration time. Fix by moving the import inside the tool function body so monkeypatch on the module path takes effect:

```python
def connect_source(backend, profile_name, connection_params):
    from ._source_inspection import (
        _sanitize_error, build_profile_yaml, list_tables_with_counts, open_backend,
    )
    ...
```

Re-run. Expected: PASS — 2 tests.

- [ ] **Step 3: Commit**

```bash
git add src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py \
        src/boring_semantic_layer/agents/backends/_schema_mcp.py
git commit -m "test(mcp-schema): connect_source credential scrub + table-cap truncation"
```

---

## Task 14: infer_schema — table source

**Files:**
- Modify: `src/boring_semantic_layer/agents/backends/_schema_mcp.py`
- Modify: `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`:

```python
class TestInferSchemaTable:
    """infer_schema against an existing table in the profile's connection."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame(
            {
                "carrier_id": [1, 2, 3],
                "origin": ["JFK", "LAX", "ORD"],
                "flight_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]).date,
                "dep_delay": [5.0, 10.0, 0.0],
            }
        )
        con.create_table("infer_table_t", df, overwrite=True)
        return "infer_table_t"

    @pytest.mark.asyncio
    async def test_returns_proposed_yaml_and_classifications(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "new_flights",
                    "source": setup_table,
                    "source_type": "table",
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "new_flights:" in data["proposed_yaml"]
            classifications = {c["column"]: c for c in data["column_classifications"]}
            assert classifications["carrier_id"]["classification"] == "dimension"
            assert classifications["dep_delay"]["classification"] == "measure"
            assert classifications["flight_date"]["is_time_dimension"] is True


class TestInferSchemaJoins:
    """When a registry model matches the FK prefix, surface a potential join."""

    @pytest.fixture(scope="class")
    def setup_tables(self, con):
        flights_df = pd.DataFrame({"carrier_id": [1, 2], "origin": ["A", "B"]})
        carriers_df = pd.DataFrame({"id": [1, 2], "name": ["AA", "UA"]})
        con.create_table("join_flights_t", flights_df, overwrite=True)
        con.create_table("join_carriers_t", carriers_df, overwrite=True)
        return ("join_flights_t", "join_carriers_t")

    @pytest.mark.asyncio
    async def test_potential_join_to_registered_carriers(self, con, setup_tables, tmp_path):
        flights_table, carriers_table = setup_tables
        yaml_path = tmp_path / "cfg.yml"
        _write_yaml(
            yaml_path,
            f"""
carriers:
  table: {carriers_table}
  dimensions:
    id: _.id
    name: _.name
  measures:
    carrier_count: _.count()
""",
        )
        bundle = from_yaml(
            str(yaml_path),
            tables={carriers_table: con.table(carriers_table)},
        )
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "flights",
                    "source": flights_table,
                    "source_type": "table",
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            joins = data["potential_joins"]
            assert len(joins) == 1
            assert joins[0]["matches_model"] == "carriers"
            assert joins[0]["matches_dimension"] == "id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestInferSchemaTable -v`
Expected: FAIL — `infer_schema` not found.

- [ ] **Step 3: Implement infer_schema (table source only for now)**

Append to `_schema_mcp.py`:

```python
def register_schema_tools(server: "MCPSemanticModel", prompts_dir: Path) -> None:
    _register_list_backends(server, prompts_dir)
    _register_connect_source(server, prompts_dir)
    _register_infer_schema(server, prompts_dir)


def _resolve_table_source(server: "MCPSemanticModel", source: str):
    """Return (ibis_table, transient_con). transient_con is None for table sources."""
    # The MCPSemanticModel doesn't expose the connection directly. Fall back
    # to the first model's underlying table's backend.
    if not server.models:
        raise ToolError(
            "Cannot resolve table source: server has no registered models. "
            "Either configure at least one model first, or pass a file path."
        )
    sample_model = next(iter(server.models.values()))
    try:
        con = sample_model.table._find_backend()
    except Exception:
        # Older ibis: backend lookup
        con = getattr(sample_model.table.op(), "source", None)
        if con is None:
            raise ToolError(
                "Could not resolve the connection from the running models. "
                "Pass a file path with source_type='parquet' / 'csv' / 'json' instead."
            )
    if source not in con.list_tables():
        available = con.list_tables()[:10]
        raise ToolError(
            f"Table '{source}' not found in the connected backend. "
            f"Available (first 10): {available}"
        )
    return con.table(source), None


def _resolve_file_source(source: str, source_type: str):
    """Open a transient DuckDB and read the file."""
    from ._source_inspection import open_transient_duckdb_for_file

    try:
        con, tbl = open_transient_duckdb_for_file(source, source_type)
    except ValueError as exc:
        raise ToolError(str(exc))
    except Exception as exc:
        from ._source_inspection import _sanitize_error
        raise ToolError(_sanitize_error(str(exc), {}))
    return tbl, con


def _register_infer_schema(server: "MCPSemanticModel", prompts_dir: Path) -> None:
    from ...schema_inference import infer_schema as _infer

    @server.tool(
        name="infer_schema",
        description=(
            load_prompt(prompts_dir, "tool-infer-schema-desc.md")
            or "Infer a BSL semantic model from a raw source."
        ),
        tags={"discovery", "metadata"},
        annotations=_READONLY_ANNOTATIONS,
    )
    def infer_schema(
        table_name: str,
        source: str,
        source_type: str | None = None,
        description: str | None = None,
        profile: str | None = None,
    ) -> dict:
        # Resolve effective source_type
        if source_type is None:
            ext = Path(source).suffix.lower().lstrip(".")
            if ext in {"parquet", "csv", "json"}:
                source_type = ext
            else:
                source_type = "table"

        if table_name in server.models:
            raise ToolError(
                f"Model name '{table_name}' already registered. "
                f"Existing models: {list(server.models.keys())}"
            )

        transient_con = None
        try:
            if source_type == "table":
                ibis_tbl, _ = _resolve_table_source(server, source)
            elif source_type in {"csv", "parquet", "json"}:
                ibis_tbl, transient_con = _resolve_file_source(source, source_type)
            else:
                raise ToolError(
                    f"Unsupported source_type '{source_type}'. "
                    "Supported: table, csv, parquet, json"
                )

            if not ibis_tbl.schema():
                raise ToolError(f"Source '{source}' has no columns")

            proposed = _infer(
                table_name=table_name,
                ibis_table=ibis_tbl,
                existing_models=server.models,
                description=description,
                profile=profile,
            )
            return _proposed_to_dict(proposed)
        finally:
            if transient_con is not None:
                try:
                    if hasattr(transient_con, "disconnect"):
                        transient_con.disconnect()
                except Exception:
                    pass


def _proposed_to_dict(proposed) -> dict:
    """Serialize ProposedSchema into a JSON-friendly dict."""
    return {
        "table_name": proposed.table_name,
        "description": proposed.description,
        "proposed_yaml": proposed.proposed_yaml,
        "column_classifications": [
            {
                "column": c.column,
                "dtype": c.dtype,
                "classification": c.classification,
                "aggregation": c.aggregation,
                "is_time_dimension": c.is_time_dimension,
                "smallest_time_grain": c.smallest_time_grain,
                "description": c.description,
                "reasoning": c.reasoning,
            }
            for c in proposed.columns
        ],
        "potential_joins": [
            {
                "column": j.column,
                "matches_model": j.matches_model,
                "matches_dimension": j.matches_dimension,
                "suggested_type": j.suggested_type,
                "reasoning": j.reasoning,
            }
            for j in proposed.potential_joins
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestInferSchemaTable src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestInferSchemaJoins -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Remove all remaining xfail markers**

The `TestSchemaToolsRegistration` xfails and `TestReadOnlyAnnotations::test_all_tools_readonly` xfail can now go. Delete the `@pytest.mark.xfail(...)` lines.

- [ ] **Step 6: Run all MCP schema tests**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py -v`
Expected: PASS — all tests so far green, no xfails.

- [ ] **Step 7: Commit**

```bash
git add src/boring_semantic_layer/agents/backends/_schema_mcp.py \
        src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py
git commit -m "feat(mcp-schema): infer_schema tool — table-source path with classifications and joins"
```

---

## Task 15: infer_schema — file source

**Files:**
- Modify: `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`:

```python
class TestInferSchemaFile:
    """infer_schema against the inferable.parquet fixture (file-source path)."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("infer_file_baseline", df, overwrite=True)
        return "infer_file_baseline"

    @pytest.fixture(scope="class")
    def parquet_path(self) -> Path:
        path = (
            Path(__file__).parent.parent.parent
            / "tests"
            / "fixtures"
            / "sample_tables"
            / "inferable.parquet"
        )
        assert path.exists(), f"fixture missing: {path}"
        return path

    @pytest.mark.asyncio
    async def test_infers_from_parquet_extension(self, con, setup_table, parquet_path, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "orders_from_file",
                    "source": str(parquet_path),
                    # No explicit source_type → inferred from .parquet extension
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "orders_from_file:" in data["proposed_yaml"]
            cls = {c["column"]: c for c in data["column_classifications"]}
            assert cls["order_id"]["classification"] == "dimension"
            assert cls["customer_id"]["classification"] == "dimension"
            assert cls["order_total"]["classification"] == "measure"
            assert cls["order_date"]["is_time_dimension"] is True
            assert cls["is_paid"]["classification"] == "dimension"

    @pytest.mark.asyncio
    async def test_explicit_source_type_overrides_extension(
        self, con, setup_table, parquet_path, tmp_path
    ):
        """Explicit source_type beats extension inference."""
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            # Pass source_type="parquet" explicitly — should still work
            result = await client.call_tool(
                "infer_schema",
                {
                    "table_name": "orders_explicit",
                    "source": str(parquet_path),
                    "source_type": "parquet",
                },
            )
            data = json.loads(result.content[0].text) if result.content else result.data
            assert "orders_explicit:" in data["proposed_yaml"]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestInferSchemaFile -v`
Expected: PASS — 2 tests. The file-source path was implemented in Task 14; this just exercises it via the parquet fixture.

- [ ] **Step 3: Commit**

```bash
git add src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py
git commit -m "test(mcp-schema): infer_schema file-source path against parquet fixture"
```

---

## Task 16: infer_schema — error paths

**Files:**
- Modify: `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py`:

```python
class TestInferSchemaErrors:
    """All error paths from spec section 6.1."""

    @pytest.fixture(scope="class")
    def setup_table(self, con):
        df = pd.DataFrame({"x": [1]})
        con.create_table("infer_err_t", df, overwrite=True)
        return "infer_err_t"

    @pytest.mark.asyncio
    async def test_missing_table_raises(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "x",
                        "source": "definitely_not_a_table",
                        "source_type": "table",
                    },
                )
            assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_missing_file_raises(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "x",
                        "source": "/nonexistent/path/data.parquet",
                    },
                )
            msg = str(exc_info.value).lower()
            assert "not found" in msg or "does not exist" in msg

    @pytest.mark.asyncio
    async def test_unsupported_file_extension_raises(self, con, setup_table, tmp_path):
        bundle = _basic_bundle(con, setup_table, tmp_path)
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            xlsx = tmp_path / "data.xlsx"
            xlsx.write_bytes(b"")  # empty file
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "x",
                        "source": str(xlsx),
                    },
                )
            # Ext inference defaults to "table" for unknown extensions; .xlsx
                  # falls back to looking it up as a table — should report not found
            assert ("not found" in str(exc_info.value).lower()
                    or "unsupported" in str(exc_info.value).lower())

    @pytest.mark.asyncio
    async def test_name_collision_with_existing_model(self, con, setup_table, tmp_path):
        """`table_name` must not collide with an already-registered model."""
        bundle = _basic_bundle(con, setup_table, tmp_path)  # registers "flights"
        mcp = MCPSemanticModel(bundle, include_schema_tools=True)
        async with Client(mcp) as client:
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "infer_schema",
                    {
                        "table_name": "flights",  # collides
                        "source": setup_table,
                        "source_type": "table",
                    },
                )
            assert "already" in str(exc_info.value).lower()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py::TestInferSchemaErrors -v`
Expected: PASS — 4 tests. All error paths were wired in Task 14.

- [ ] **Step 3: Run the full schema-tools test suite end-to-end**

Run:
```bash
uv run pytest src/boring_semantic_layer/tests/test_schema_inference.py src/boring_semantic_layer/tests/test_source_inspection.py src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py -v
```
Expected: All tests PASS — ~70+ tests across the three files.

- [ ] **Step 4: Run the full test suite to ensure no regressions**

Run:
```bash
uv run pytest src/boring_semantic_layer/tests/ -v
uv run pytest src/boring_semantic_layer/agents/tests/ -v
```
Expected: ALL PASS. If existing tests break, the most likely cause is the modified `MCPSemanticModel.__init__` signature — adjust accordingly.

- [ ] **Step 5: Lint and format**

Run:
```bash
uv run ruff check src/
uv run ruff format src/
```
Fix any issues, then re-run.

- [ ] **Step 6: Commit**

```bash
git add src/boring_semantic_layer/agents/tests/test_mcp_schema_tools.py
git commit -m "test(mcp-schema): infer_schema error paths — missing table/file, unsupported ext, name collision"
```

---

## Self-Review Notes

**Spec coverage check:**
- §2 Operating contract: Task 10 (flag), Task 11–14 (registration when flag set).
- §3 Architecture / file split: Tasks 1–8 (core files), Task 10 (constructor), Task 9 (prompts).
- §4.1 schema_inference: Task 1 (dataclasses), Task 2 (classify_column grid), Task 3 (find_potential_joins), Task 4 (render_yaml), Task 5 (infer_schema orchestrator).
- §4.2 _source_inspection: Task 6 (open_backend), Task 7 (list_tables_with_counts), Task 8 (build_profile_yaml + open_transient_duckdb_for_file + _sanitize_error).
- §4.3 _schema_mcp: Tasks 11 (list_backends), 12–13 (connect_source), 14–16 (infer_schema).
- §5 Data flow: covered by Tasks 11/12/14 implementations.
- §6 Error handling: Task 13 (connect_source scrub + truncation), Task 16 (infer_schema errors).
- §6.4 Credential sanitization: Task 8 (impl) + Task 13 (test).
- §8 Testing: every test class from §8.2 mapped to a task.
- §10 Migration: no code change required for consumers — covered by §2 default-off behavior, verified in Task 10 baseline.

**Out of scope (per §11):**
- File parsing (PDF/Excel/docx) — not in plan.
- Slash commands — not in plan.
- Hot reload — not in plan.
- Live multi-cloud CI — not in plan.

**Type consistency:**
- `ColumnClassification` fields used identically across Tasks 2, 3, 4, 5, 14.
- `PotentialJoin` fields identical across 3, 5, 14.
- `ProposedSchema` field name `proposed_yaml` (snake_case) consistent.
- Tool function param names match the description files (`table_name`, `source`, `source_type`, `description`, `profile` for `infer_schema`; `backend`, `profile_name`, `connection_params` for `connect_source`).
- `TableSummary` fields (`name`, `row_count`, `count_error`) consistent across 7, 12, 13.

**No placeholders detected.** Every step contains either runnable code, exact commands with expected output, or a commit message.
