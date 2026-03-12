# Codebase Concerns

**Analysis Date:** 2026-03-12

## MCP Implementation Limitations

### Parameter Parsing Fragility

**Issue:** JSON string vs native parameter handling requires custom validators
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 38-44), `src/boring_semantic_layer/tests/test_mcp_json_parsing.py` (entire file)
- Impact: Claude Desktop sends JSON-stringified arrays while other MCP clients send native types. Manual `BeforeValidator(_parse_json_string)` workaround needed on every complex parameter (dimensions, measures, filters, order_by, time_range, chart_spec).
- FastMCP 3.0 opportunity: Built-in parameter coercion and schema generation improvements could eliminate this manual validator pattern entirely.
- Current limitation: Each parameter type needs explicit validator definition, creating duplication and maintenance burden.

### Tool Definition Boilerplate

**Issue:** Tool registration done manually with inner function pattern inside `_register_tools()`
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 59-352)
- Impact: All 6 tools (list_models, get_model, get_time_range, query_model, search_dimension_values, + utilities) require manual setup. Adding new tools or modifying signatures is error-prone.
- FastMCP 3.0 opportunity: Decorator-based tool registration could reduce boilerplate and improve discoverability.
- Current limitation: Tools are nested inside a single method, making it hard to test tools in isolation or document them independently.

### Prompt File Management Complexity

**Issue:** Dual-path prompt loading for installed vs development modes
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 21-35)
- Impact: Tool descriptions loaded from separate markdown files using fallback logic. If prompts directory not found in wheel installation, silently falls back to dev location, making deployment fragile.
- Risk: Deployed MCP servers may load wrong prompts or fail to find them at runtime.
- FastMCP 3.0 opportunity: Structured configuration for prompt/instruction management could eliminate this dual-path complexity.

### Limited Error Context in Tool Responses

**Issue:** Simple ValueError exceptions with minimal context don't always help users diagnose problems
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 73, 116, 239, 274, 280)
- Impact: When model not found, dimension not found, or time dimension missing, error messages are brief. No guidance on valid options or what went wrong.
- Example from get_time_range (line 116): `raise ValueError(f"Model {model_name} has no time dimension")` - doesn't tell user which model has time dimensions.
- FastMCP 3.0 opportunity: Structured error types and richer error responses could provide better context.

### Missing Tool Metadata and Validation

**Issue:** Tool descriptions loaded from external files instead of embedded in code
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 62, 69, 105, 135, 265)
- Impact: Tool documentation separated from implementation. Hard to validate that descriptions match actual behavior. No runtime validation that prompts directory exists before MCP server starts.
- Risk: Stale or missing descriptions when prompts aren't deployed correctly.
- FastMCP 3.0 opportunity: Integrated tool metadata with runtime validation during server initialization.

---

## Deferred Recursion Workaround

**Issue:** Direct column access workaround for Deferred recursion
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 118-126)
- Code: Direct access via `tbl[col_name]` instead of `time_dim.expr(tbl)` to avoid infinite recursion with Deferred objects
- Impact: Fragile workaround that breaks if underlying ibis Deferred behavior changes. Comment indicates known issue but no proper fix.
- Risk: Future ibis or xorq updates could cause get_time_range to fail silently or hang.
- Mitigation needed: Either fix root cause in dimension resolution or add timeout protection for get_time_range queries.

---

## Large Monolithic Files

**Issue:** Core business logic concentrated in very large files
- Files:
  - `src/boring_semantic_layer/ops.py` (4,514 lines) - All dimension/measure operations, join logic, semantic operations
  - `src/boring_semantic_layer/expr.py` (1,614 lines) - All semantic table API and methods
  - `src/boring_semantic_layer/tests/test_bi_traps.py` (2,461 lines) - Comprehensive test suite
- Impact: Hard to navigate, test individual concerns, or understand responsibility boundaries. Single file changes affect many unrelated features.
- Risk: Any refactoring (including FastMCP migration) will require careful extraction and module reorganization to avoid breaking changes.

---

## Type System Fragility

**Issue:** Xorq and ibis type compatibility layer uses try/except fallback pattern
- Files: `src/boring_semantic_layer/ops.py` (lines 19-41)
- Pattern: Try to import from xorq.vendor.ibis first, fall back to standard ibis if not available
- Impact: Two parallel type systems (`_MeanTypes`, `_MinTypes`, `_CountDistinctTypes`) must be maintained. Instanceof checks use tuples to handle both types.
- Risk: Type checking fragile across different ibis versions. Hard to understand which version is active at runtime.
- FastMCP 3.0 opportunity: Standardized type handling in MCP tools could reduce this coupling.

---

## MCP Version Compatibility

**Issue:** FastMCP dependency pinned to version >=2.12.4, currently using 2.13.0.2
- Files: `pyproject.toml` (line 42), `requirements-dev.txt`
- Current gap: FastMCP 3.0 features not yet available. Library locked at v2.x.
- FastMCP 3.0 migration path:
  - Requires version bump to >=3.0.0
  - Tool registration API changes expected
  - Parameter handling may improve but existing validators may need updates
  - New context management and error handling features available

---

## Test Coverage Gaps

**Issue:** MCP tests use async patterns but incomplete coverage of error paths
- Files: `src/boring_semantic_layer/tests/test_semantic_mcp.py` (731 lines), `src/boring_semantic_layer/agents/tests/test_semantic_mcp.py` (857 lines)
- Gaps:
  - No tests for malformed filter expressions
  - No tests for extremely large result sets (records_limit edge cases)
  - No tests for concurrent tool calls or rate limiting
  - No tests for prompt file missing at runtime
- Risk: Production MCP server may fail in undiscovered edge cases.

---

## Join Prefixing Complexity

**Issue:** Join model field prefixing requires careful dimension name resolution
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 122-125)
- Pattern: For joined models, dimension names have table prefix (e.g., 'flights.flight_date') but actual column name is unprefixed
- Impact: Manual string split logic on dimension names. If join logic changes or prefixing strategy evolves, MCP tool needs updates.
- Risk: Joined models may query wrong columns if prefixing rules change.

---

## Search Dimension Values Performance

**Issue:** Dimension value search uses regex on every row during aggregation
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 318-344)
- Pattern: For each dimension value, applies case-insensitive regex matching: `.re_replace(_SEP, " ").strip().contains(search_normalized)`
- Impact: On high-cardinality dimensions (millions of distinct values), search becomes slow even with limit=20.
- Risk: MCP timeouts on large datasets during dimension exploration.
- Improvement opportunity: Pre-filter aggregation before regex, or use database-native string search if available.

---

## Error Handling in Query Execution

**Issue:** Query execution errors propagate as generic ValueErrors without context
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 137-261)
- Pattern: `model.query()` called without try/catch. Any error in query compilation or execution becomes MCP error without diagnostic info.
- Impact: Clients can't distinguish between "model not found" vs "query syntax error" vs "database connection failed".
- Missing: Structured error types, error categorization, recovery suggestions.

---

## Circular Import Risk

**Issue:** Lazy imports in `__init__.py` for optional MCP/LangGraph backends
- Files: `src/boring_semantic_layer/__init__.py` (lines 73-95)
- Pattern: Dynamic `__getattr__` to delay import of agents.backends.mcp and agents.backends.langgraph
- Impact: Import-time behavior depends on optional dependencies being present. Circular import risk if mcp.py imports from higher-level modules.
- Risk: Subtle import failures only discovered at first MCP instantiation, not at server startup.

---

## Serialization Roundtrip Risks

**Issue:** to_tagged/from_tagged serialization assumes xorq availability and schema stability
- Files: `src/boring_semantic_layer/serialization/__init__.py` (lines 57-139)
- Pattern: Tagged expressions with frozen metadata may not survive schema changes
- Impact: If dimension/measure definitions change after serialization, from_tagged may fail or produce stale results.
- Risk: No validation that tagged schema matches current model schema on deserialization.

---

## Performance Under Load

**Issue:** Large ops.py (4514 lines) and complex join logic may cause slow compilation
- Files: `src/boring_semantic_layer/ops.py`
- Impact: Every semantic operation traverses operation graph. Join operations especially complex with name resolution.
- Risk: MCP queries on large joined models with many dimensions/measures may be slow to compile.
- FastMCP 3.0 opportunity: Better caching or compilation strategies could improve performance.

---

## Documentation Maintenance Burden

**Issue:** Tool descriptions split across multiple .md files in docs/md/prompts/query/mcp/
- Files: 23 separate markdown files for tool documentation
- Impact: Adding/removing parameters requires updates in both mcp.py tool definition AND corresponding .md file.
- Risk: Documentation can drift from implementation.
- Example: Query parameter changes (dimensions, measures, filters) need sync in tool-query.md and tool-query-param-*.md files.

---

## Prompt Loading Failure Modes

**Issue:** If prompts directory structure changes, SYSTEM_INSTRUCTIONS falls back to hardcoded string
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (line 35)
- Pattern: `SYSTEM_INSTRUCTIONS = load_prompt(PROMPTS_DIR, "system.md") or "MCP server for semantic models"`
- Risk: If system.md not found, generic fallback used without warning. Claude won't have context-specific instructions.
- Missing: Validation that prompts loaded successfully; warning if fallback used.

---

## Joining and Prefixing Edge Cases

**Issue:** Model-prefixed field names in query parameters require special handling
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 122-125)
- Test evidence: `test_query_with_prefixed_fields_on_standalone_model` and `test_get_model_joined` show prefixing works but requires extra logic
- Impact: If user specifies "flights.carrier" on standalone model, needs to resolve to just "carrier". Joined models use full prefix.
- Risk: Inconsistent behavior between standalone and joined models. Query parameter API confusing.

---

## Missing Resource Limits

**Issue:** No limits on query result sizes, record counts, or aggregation cardinality
- Files: `src/boring_semantic_layer/agents/backends/mcp.py`
- Parameters with weak defaults:
  - `records_limit` defaults to None (unlimited)
  - `limit` on dimension search defaults to 20 but no absolute max
  - Aggregation on `search_dimension_values` could return millions of rows before filtering
- Risk: Malicious or accidental queries could consume unbounded memory or time on MCP server.

---

## Lack of Observability

**Issue:** No structured logging, metrics, or instrumentation in MCP server
- Files: `src/boring_semantic_layer/agents/backends/mcp.py`
- Missing:
  - Query execution timing
  - Error rates and types
  - Tool call frequency
  - Slow query detection
  - Dimension value search performance metrics
- Impact: Hard to debug production issues, optimize performance, detect abuse.

---

## Testing Strategy Mismatch

**Issue:** Tests use `anyio` with asyncio backend but MCP may be used with other async backends
- Files: `src/boring_semantic_layer/tests/test_mcp_json_parsing.py` (lines 13-20)
- Pattern: `pytestmark = pytest.mark.anyio` + `anyio_backend` fixture locks to "asyncio"
- Risk: If MCP server needs to run in `trio` or other async context, untested code path.

---

## Chart Integration Complexity

**Issue:** Chart generation tightly coupled to query_model tool with multiple format/backend options
- Files: `src/boring_semantic_layer/agents/backends/mcp.py` (lines 208-260)
- Parameters: chart_backend, chart_format, chart_spec all optional but interdependent
- Risk: Complex branching logic (get_chart flag, format validation, spec merging) all inline in tool definition.
- Missing: Unit tests for all chart combinations; validation that chart_backend/format combination is valid.

---

## Areas That Will Benefit Most from FastMCP 3.0 Migration

1. **Parameter Validation**: Built-in coercion for JSON-stringified parameters eliminates custom validators
2. **Tool Registration**: Decorator-based system replaces manual boilerplate
3. **Error Handling**: Structured error types replace simple ValueError exceptions
4. **Tool Metadata**: Integrated documentation eliminates dual-path prompt loading
5. **Async Context**: Better async/context management for concurrent requests
6. **Resource Limits**: Framework-level request size/timeout limits
7. **Logging/Instrumentation**: Built-in observability hooks
8. **Type Safety**: Better type annotation support with improved schema generation

---

## Refactoring Priorities for FastMCP 3.0 Migration

**High Priority (blocking issues):**
1. Replace manual parameter validators with FastMCP 3.0 coercion
2. Add structured error types with rich context
3. Consolidate tool registration with decorator pattern
4. Add resource limits and timeouts
5. Fix Deferred recursion workaround (or add timeout protection)

**Medium Priority (nice-to-have improvements):**
1. Consolidate tool documentation (embed in code, not external .md files)
2. Add structured logging and metrics
3. Improve error handling in query execution path
4. Add performance validation for large joined models

**Low Priority (future work):**
1. Optimize dimension value search on high-cardinality dimensions
2. Add query compilation caching
3. Separate large ops.py into smaller modules
4. Add async backend flexibility (not just asyncio)

---

*Concerns audit: 2026-03-12*
