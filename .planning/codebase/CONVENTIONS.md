# Coding Conventions

**Analysis Date:** 2026-03-12

## Naming Patterns

**Files:**
- Module files use lowercase with underscores: `config.py`, `query.py`, `graph_utils.py`
- Test files follow pattern: `test_<module>.py` (e.g., `test_semantic_mcp.py`, `test_bi_traps.py`)
- Package directories use lowercase with underscores: `boring_semantic_layer`, `agents/backends`, `chart/md_parser`

**Functions:**
- Public functions use lowercase with underscores: `to_semantic_table()`, `get_dimensions()`, `safe_eval()`
- Private/internal functions prefixed with single underscore: `_parse_json_string()`, `_find_time_dimension()`, `_get_prompts_dir()`
- Functional API functions suffixed with underscore to avoid keyword conflicts: `filter_()`, `group_by_()`, `aggregate_()`, `mutate_()`, `order_by_()`
- MCP tool methods registered via decorator without underscore: `list_models()`, `get_model()`, `query_model()`, `search_dimension_values()`

**Variables:**
- Constants in UPPER_CASE: `TIME_GRAIN_TRANSFORMATIONS`, `SAFE_NODES`, `SYSTEM_INSTRUCTIONS`
- Local variables lowercase with underscores: `model_name`, `flight_count`, `col_name`
- Dictionary keys match field names: `{"name": ..., "dimensions": ..., "measures": ...}`
- Tuple element names use snake_case in imports: `from returns.result import Result, safe`

**Types:**
- Type hints use PEP 604 union syntax `X | Y` instead of `Union[X, Y]`
- Generic collections lowercase: `list[str]`, `dict[str, Any]`, `Mapping[str, Any]`
- Optional types use pipe union: `str | None` instead of `Optional[str]`
- Literal types for enums: `Literal["TIME_GRAIN_YEAR", "TIME_GRAIN_MONTH", ...]`

**Classes:**
- PascalCase: `SemanticModel`, `MCPSemanticModel`, `SafeEvalError`, `TestFanOutTrap`
- Test classes prefixed with `Test`: `TestMCPSemanticModelInitialization`, `TestListModels`
- MCP server class extends `FastMCP`: `class MCPSemanticModel(FastMCP)`

## Code Style

**Formatting:**
- Tool: Ruff (via `[tool.ruff]` in `pyproject.toml`)
- Line length: 100 characters (configured in `[tool.ruff]`)
- Target Python version: 3.10+ (via `target-version = "py310"`)

**Linting:**
- Tool: Ruff with comprehensive rule set
- Selected rules include: E (errors), F (pyflakes), I (isort), UP (pyupgrade), B (bugbear), SIM (simplify)
- Ignored: E501 (line-too-long, handled by formatter), S101 (assert-used in tests), COM812 (trailing comma conflicts)
- Per-file ignores for tests: `S101`, `PLR2004` (magic values), `ANN` (type annotations)

**Docstring Style:**
- Format: Google-style docstrings with Args/Returns/Examples sections
- Example: `api.py` and `utils.py` show full docstring pattern with description, Args, Returns, Note/Examples sections
- MCP tools use markdown files loaded via `load_prompt()` for descriptions

**Import Organization:**
- Order: Standard library → third-party → local imports
- First-party configured: `known-first-party = ["boring_semantic_layer"]` in `[tool.ruff.lint.isort]`
- Absolute imports preferred over relative imports
- `from __future__ import annotations` at module start for forward references

**Path Aliases:**
- Not used in main source code
- Ibis vendored via xorq: `from xorq.vendor.ibis...` or `import xorq.api as xo`

## Error Handling

**Patterns:**
- Explicit exception types: `raise ValueError()`, `raise KeyError()`, `raise TypeError()`
- MCP tools raise `ValueError` with descriptive messages caught as `ToolError` by FastMCP client
- Example in `mcp.py` line 238-239: `if model_name not in self.models: raise ValueError(f"Model {model_name} not found")`
- Validation errors via Pydantic `ValidationError` for JSON parameter parsing
- Try/except blocks avoid nested structures - use loops with try/except when parsing multiple values (see `query.py` line 219-221)

**MCP-Specific Error Handling:**
- Tool functions raise `ValueError` with context-specific messages
- Client library (`fastmcp.Client`) converts `ValueError` to `ToolError` with same message
- Schema validation errors from `BeforeValidator` produce Pydantic `ValidationError`

**Filter Validation:**
- Dictionary filters checked for required keys: `"field"`, `"operator"`, `"value"` or `"values"`
- Operator validation against defined set in `FILTER_OPERATORS` mapping
- Compound filters require non-empty `conditions` list

## Logging

**Framework:** Python standard `logging` module (not `print()`)

**Usage:**
- Logger created per-module: `logger = logging.getLogger(__name__)` (see `projection_utils.py` line 15)
- Log level: `debug()` for internal operation details, no `info()` or `warning()` for normal flow

**Patterns:**
- Failures in optional operations logged as debug, not raised as errors
- Example in `projection_utils.py` line 78: `logger.debug(f"Failed to extract column names: {e}")`
- Used for non-critical diagnostics (column extraction failures, etc.)

## Comments

**When to Comment:**
- Complex algorithm explanations (see `mcp.py` lines 118-120 about Deferred recursion avoidance)
- Non-obvious field access patterns (e.g., splitting dot-notation fields for joined models)
- Workarounds with issue references (none found, but pattern would include issue number)
- Skip comments for obvious code (standard CRUD operations, simple loops)

**Code Section Headers:**
- Use clear descriptive comments for logical sections within functions
- Example: `# Apply case-insensitive search filter if provided` in `mcp.py` line 321

**Docstrings vs Comments:**
- Docstrings for public functions/classes with Args/Returns/Examples
- Comments for implementation details and decisions
- Inline comments for non-obvious variable transformations

## Function Design

**Size Guidelines:**
- Tool functions (MCP): 1-80 lines (see `list_models()` 5 lines, `search_dimension_values()` ~80 lines)
- Helper functions: Keep focused on single responsibility
- Complex operations broken into helper functions with clear names

**Parameters:**
- Use type hints on all parameters: `def func(param: str, count: int | None = None) -> dict:`
- Default parameters in Annotated Field() for MCP tools with descriptions
- Keyword-only parameters for optional settings via `*` in signature

**Return Values:**
- Explicit return types with type hints
- MCP tools return structured types: `Mapping[str, Any]`, `dict`, `str` (JSON serialized)
- Helper functions return same types they operate on (e.g., `SemanticModel` returns `SemanticModel`)
- Tuple returns for multi-value returns: See `_fetch()` in `mcp.py` returns `(values_list, is_complete)`

**Async Functions:**
- Async used for MCP client-server communication: `async def test_list_models()`
- Context manager pattern: `async with Client(mcp) as client: result = await client.call_tool(...)`
- Tool functions themselves are synchronous; async wrapping done by FastMCP framework

## Module Design

**Exports:**
- Public API in `__init__.py` via explicit imports (e.g., `MCPSemanticModel`, `to_semantic_table`)
- Main classes/functions explicitly exported, internal utilities remain private

**Barrel Files:**
- `__init__.py` files act as package entry points
- Example: `chart/__init__.py` exports chart utilities
- `agents/backends/` subdirectories have focused `__init__.py` files

**MCP Tool Registration Pattern:**
- Tools registered via decorator: `@self.tool(name="...", description="...")`
- Decorator applied during `__init__` via `_register_tools()` method call
- Each tool is a nested function with access to `self.models`
- Tools receive Pydantic-validated parameters with BeforeValidator for JSON parsing

**Prompt Loading Pattern:**
- External descriptions loaded from markdown files via `load_prompt(PROMPTS_DIR, "filename.md")`
- Enables separation of prose documentation from code
- Fallback to default string if file not found: `or "default message"`

## Dependency Injection

**Pattern:**
- Models passed to `MCPSemanticModel` constructor: `MCPSemanticModel(models={...})`
- Stored in instance variable: `self.models`
- Used by all tool functions via closure over instance
- No global state for model registry

## Special Patterns for FastMCP Migration

**Tool Parameter Handling:**
- Use `Annotated` with `BeforeValidator` for JSON string parameter parsing
- Example pattern in `mcp.py` line 139-146:
  ```python
  dimensions: Annotated[
      list[str] | None,
      BeforeValidator(_parse_json_string),
      Field(default=None, description="...")
  ] = None
  ```
- Handles both JSON-stringified params (Claude Desktop) and native Python types (regular MCP clients)
- Validator function `_parse_json_string()` attempts JSON parsing, returns original if not valid

**Schema Generation:**
- Field() with description parameter generates tool schema descriptions
- json_schema_extra for special schema requirements (e.g., nested array items)
- Schema must be compatible with Azure OpenAI and Claude Desktop
- All array types must have proper "items" key in schema

**Error Messages:**
- Include helpful context in ValueError messages for user-facing errors
- Example: `f"Dimension '{dimension_name}' not found in model '{model_name}'. Available dimensions: {list(dims.keys())}"`

---

*Convention analysis: 2026-03-12*
