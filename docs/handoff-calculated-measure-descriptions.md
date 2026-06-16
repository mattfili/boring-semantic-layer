# Calculated Measure Descriptions — Breaking Change Handoff

## What changed

Calculated measures now carry descriptions through the entire pipeline. Previously, descriptions were silently discarded during measure classification, and the MCP/agent/server APIs returned calculated measures as a flat list of names.

## API breaking change: `calculated_measures` response format

**Before** (list of names):
```json
{
  "measures": {
    "total_visits": {"description": "Total visit volume"},
    "population": {"description": "Estimated population"}
  },
  "calculated_measures": ["visits_per_1k", "pct_inpatient"]
}
```

**After** (dict with descriptions):
```json
{
  "measures": {
    "total_visits": {"description": "Total visit volume"},
    "population": {"description": "Estimated population"}
  },
  "calculated_measures": {
    "visits_per_1k": {"description": "Visit volume per 1,000 population"},
    "pct_inpatient": {"description": "Inpatient share of total visits"}
  }
}
```

This affects three endpoints:
- **MCP** `get_model` tool and `semantic://models/{name}` resource (`agents/backends/mcp.py`)
- **LangGraph** `_get_model` tool (`agents/tools.py`)
- **Server API** model metadata response (`server/api.py`)

### Migration for consumers

If you parse `calculated_measures` as a list:
```python
# Before
calc_names = response["calculated_measures"]  # ["visits_per_1k", ...]

# After
calc_names = list(response["calculated_measures"].keys())  # same result
calc_with_desc = response["calculated_measures"]  # {"visits_per_1k": {"description": "..."}, ...}
```

For MCP consumers, the description value is `null` when no description was provided on the measure definition.

For LangGraph consumers, the value is a string (the description, or `"calculated measure"` as fallback).

## How descriptions flow

1. **Definition** — User provides description via dict syntax or YAML:
   ```python
   model.with_measures(
       ratio={"expr": lambda t: t.x / t.y, "description": "X to Y ratio"}
   )
   ```
   ```yaml
   calculated_measures:
     ratio:
       expr: "_.x / _.y"
       description: "X to Y ratio"
   ```

2. **Classification** — `_classify_measure()` in `ops.py` detects the measure is calculated and wraps it in a `DescribedMeasure(expr=<AST>, description="X to Y ratio")`

3. **Storage** — `calc_measures: FrozenDict[str, Any]` on `SemanticTableOp` now stores `DescribedMeasure` wrappers instead of raw AST nodes

4. **Output** — `get_model` reads `.description` from the wrapper

## Internal: `DescribedMeasure` AST node

A new frozen attrs class in `measure_scope.py`:

```python
@frozen
class DescribedMeasure:
    expr: Any           # The wrapped MeasureExpr AST node (MeasureRef, BinOp, etc.)
    description: str | None = None
```

Helper to strip the wrapper:
```python
from boring_semantic_layer.measure_scope import unwrap_calc_expr

raw_ast = unwrap_calc_expr(value)  # returns value.expr if DescribedMeasure, else value
```

**If you write code that consumes `calc_measures` dict values directly** (e.g., custom compilation, analysis, or serialization), call `unwrap_calc_expr()` before doing isinstance checks or accessing AST node attributes. The existing compilation pipeline (`compile_all.py`, `_compile_formula`, `_resolve_aggregation_exprs`) already receives unwrapped expressions — no changes needed there.

## Serialization backward compatibility

Serialized calc measures now use a richer format when a description is present:

```python
# Old format (still read correctly on deserialization)
{"ratio": ("calc_binop", "div", ("measure_ref", "x"), ("measure_ref", "y"))}

# New format (emitted when description exists)
{"ratio": {"expr": ("calc_binop", "div", ("measure_ref", "x"), ("measure_ref", "y")),
           "description": "X to Y ratio"}}
```

The deserializer handles both formats transparently. A `DescribedMeasure` with `description=None` serializes in the old tuple format (no wrapper on roundtrip).

## Files modified

| File | Change |
|------|--------|
| `measure_scope.py` | New `DescribedMeasure` class + `unwrap_calc_expr` helper |
| `ops.py` | `_classify_measure` wraps calc path; unwrap at 3 consumption points; join merge handles wrapper |
| `graph_utils.py` | Unwrap before `_collect_measure_refs` |
| `serialization/extract.py` | Serialize description alongside AST |
| `serialization/reconstruct.py` | Deserialize both old and new formats |
| `agents/backends/mcp.py` | `calculated_measures` → dict with descriptions |
| `agents/tools.py` | Same |
| `server/api.py` | Same |
