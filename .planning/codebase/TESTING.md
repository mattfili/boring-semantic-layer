# Testing Patterns

**Analysis Date:** 2026-03-12

## Test Framework

**Runner:**
- pytest (configured in `pyproject.toml` under `[tool.pytest.ini_options]`)
- Async support via `pytest-asyncio` plugin
- Command to run all tests: `pytest` (typical invocation)
- Command for async tests: Tests marked with `@pytest.mark.asyncio` run via pytest-asyncio

**Assertion Library:**
- pytest's built-in assertion introspection (no special assertion library)
- Standard `assert` statements throughout codebase
- `assert` allowed in tests per `[tool.ruff.lint.per-file-ignores]` rule `S101`

**Run Commands:**
```bash
pytest                              # Run all tests
pytest -m "not slow"                # Run fast tests (exclude slow marker)
pytest src/boring_semantic_layer/tests/test_semantic_mcp.py  # Specific test file
pytest -v                           # Verbose output
pytest --co                         # List tests without running
```

**Coverage:**
- No coverage enforcement configured
- Coverage would be checked via: `pytest --cov=src/boring_semantic_layer`

## Test File Organization

**Location:**
- Tests co-located with source code: `src/boring_semantic_layer/tests/`
- Subdirectory tests mirrored: `src/boring_semantic_layer/chart/tests/`
- Integration tests in separate directory: `src/boring_semantic_layer/tests/integration/`

**Naming:**
- Test modules: `test_<feature>.py` pattern
- Example test files:
  - `test_semantic_mcp.py` - MCP server/client testing
  - `test_mcp_json_parsing.py` - MCP parameter validation
  - `test_query.py` - Query API testing
  - `test_bi_traps.py` - BI anti-pattern prevention testing
  - `test_deferred_api.py` - Ibis deferred expression testing

**Structure:**
```
src/boring_semantic_layer/
├── tests/
│   ├── conftest.py                    # Session/module fixtures
│   ├── test_semantic_mcp.py           # Main MCP functionality
│   ├── test_mcp_json_parsing.py       # JSON parameter validation
│   ├── test_query.py                  # Query operations
│   ├── test_bi_traps.py               # BI anti-patterns
│   ├── test_yaml.py                   # YAML config
│   └── fixtures/                      # Test data/helpers
│       ├── connections.py             # Database connection management
│       └── datasets.py                # Test dataset loading
├── chart/tests/
│   ├── test_chart.py
│   ├── test_md_parser.py
│   └── ...
└── agents/tests/
    ├── test_semantic_mcp.py           # Agent-specific MCP tests
    └── test_mcp_json_parsing.py       # Agent-specific validation
```

## Test Structure

**Suite Organization:**
```python
class TestMCPSemanticModelInitialization:
    """Test MCPSemanticModel initialization."""

    def test_init_with_models(self, sample_models):
        """Test initialization with semantic models."""
        mcp = MCPSemanticModel(models=sample_models, name="Test MCP Server")
        assert mcp.models == sample_models
        assert mcp.name == "Test MCP Server"
```

**Patterns:**
- Classes group related tests: `TestListModels`, `TestGetModel`, `TestQueryModel`
- Test methods prefixed with `test_`: `test_list_models()`, `test_list_models_empty()`
- Descriptive docstrings for each test explaining what is being tested
- Fixtures passed as method parameters
- One assertion focus per test (though multi-assertions allowed for related checks)

**Setup/Teardown:**
- Fixtures handle setup: `@pytest.fixture(scope="module")` for shared setup
- Teardown via context manager: `yield resource; resource.cleanup()`
- Example in `conftest.py`:
  ```python
  @pytest.fixture(scope="module")
  def connection_manager():
      manager = ConnectionManager(in_memory=True)
      yield manager
      manager.close()
      reset_connection_manager()
  ```

**Fixture Scopes:**
- `scope="session"` - Shared across entire test run (dataset manager)
- `scope="module"` - Shared within test module (database connections)
- No scope (function) - Fresh per test (most fixtures)

## Mocking

**Framework:** Pydantic type validation used instead of traditional mocking for parameter validation tests

**Patterns:**
```python
# Type validation via TypeAdapter (test_mcp_json_parsing.py)
from pydantic import TypeAdapter

def test_parse_json_string_with_array(self):
    ParsedList = Annotated[list[str] | None, BeforeValidator(_parse_json_string)]
    adapter = TypeAdapter(ParsedList)
    result = adapter.validate_python('["a", "b", "c"]')
    assert result == ["a", "b", "c"]
```

**MCP Client Testing:**
```python
# FastMCP Client context manager pattern (test_semantic_mcp.py)
async with Client(mcp) as client:
    result = await client.call_tool("list_models", {})
    models = json.loads(result.content[0].text)
    assert "flights" in models
```

**What to Mock:**
- Database calls avoided - use in-memory DuckDB connections instead
- External APIs not tested - integration tests skip external calls
- Parameter validation tested via Pydantic validators, not mocks

**What NOT to Mock:**
- Ibis table operations (execute them against real in-memory connections)
- Semantic model query execution (execute real queries)
- MCP tool invocation (use real `Client` context manager)
- JSON parsing/validation (test with actual Pydantic validators)

## Fixtures and Factories

**Test Data Patterns:**
```python
@pytest.fixture(scope="module")
def sample_models(con):
    """Create sample semantic tables for testing."""
    flights_df = pd.DataFrame({
        "origin": ["JFK", "LAX", "ORD"] * 10,
        "destination": ["LAX", "JFK", "DEN"] * 10,
        "carrier": ["AA", "UA", "DL"] * 10,
        "flight_date": pd.date_range("2024-01-01", periods=30, freq="D"),
        "dep_delay": [5.2, 8.1, 3.5] * 10,
    })

    flights_tbl = con.create_table("flights", flights_df, overwrite=True)

    flights_model = (
        to_semantic_table(flights_tbl, name="flights")
        .with_dimensions(
            origin=lambda t: t.origin,
            carrier=lambda t: t.carrier,
        )
        .with_measures(
            flight_count={"expr": lambda t: t.count()},
        )
    )

    return {"flights": flights_model}
```

**Location:**
- Test fixtures in `conftest.py` at module/package level
- Inline fixtures within test files for one-off data
- Fixture data: pandas DataFrames, Ibis tables, semantic models
- Reusable test models stored in fixture return dictionary

## Coverage

**Requirements:** No coverage enforcement (not configured in `pyproject.toml`)

**MCP Testing Focus Areas:**
- Tool invocation via `Client.call_tool()`
- Parameter validation (JSON strings, arrays, nested objects)
- Error handling (ToolError exceptions)
- Return value structure and content
- Multi-value parameters (dimensions, measures, filters, order_by)

## Test Types

**Unit Tests:**
- Scope: Individual functions and methods
- Approach: Test single responsibility with clear inputs/outputs
- Examples:
  - `test_parse_json_string_with_array()` - JSON string parsing
  - `test_list_models()` - Tool output format
  - `test_get_model_with_time_dimension()` - Model metadata inclusion

**Integration Tests:**
- Scope: Complete workflows combining multiple components
- Approach: End-to-end scenarios with real data and real execution
- Examples:
  - `test_query_model()` - Full query execution with dimensions, measures, filters
  - `test_query_with_time_grain()` - Time dimension handling
  - `test_query_joined_model()` - Join semantics
  - `test_end_to_end_claude_desktop_query()` - Simulated real usage

**MCP Server/Protocol Tests:**
- Scope: FastMCP server behavior and client interaction
- Pattern: Create `Client(mcp_instance)` context manager
- Examples:
  - `test_list_models()` - Tool discovery
  - `test_get_model_not_found()` - Error handling via ToolError
  - `test_claude_desktop_json_dimensions()` - Real Claude Desktop behavior
  - `test_backward_compatibility_actual_arrays()` - Legacy client support

**E2E Tests:**
- Framework: Not used as primary testing (integration tests serve this role)
- Pattern: When needed, use `async with Client(mcp)` for real MCP protocol testing

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_list_models(self, sample_models):
    """Test listing all available models."""
    mcp = MCPSemanticModel(models=sample_models)

    async with Client(mcp) as client:
        result = await client.call_tool("list_models", {})
        models = json.loads(result.content[0].text)

        assert "flights" in models
        assert "carriers" in models
```

**Error Testing:**
```python
@pytest.mark.asyncio
async def test_get_model_not_found(self, sample_models):
    """Test getting a non-existent model."""
    mcp = MCPSemanticModel(models=sample_models)

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Model nonexistent not found"):
            await client.call_tool("get_model", {"model_name": "nonexistent"})
```

**Parametrized Tests for Variants:**
```python
class TestQueryModel:
    """Test query_model tool."""

    @pytest.mark.asyncio
    async def test_simple_query(self, sample_models):
        # Basic query

    @pytest.mark.asyncio
    async def test_query_with_filter(self, sample_models):
        # Query with filters

    @pytest.mark.asyncio
    async def test_query_with_time_grain(self, sample_models):
        # Query with time aggregation
```

**JSON Response Parsing:**
```python
result = await client.call_tool("query_model", {...})
data = json.loads(result.content[0].text)  # FastMCP returns TextContent with JSON string
assert "records" in data
assert isinstance(data["records"], list)
```

**MCP Tool Schema Validation:**
```python
def test_schema_has_all_required_fields(self, mcp_server):
    """Test that the generated schema has all required fields."""
    tool = mcp_server._tool_manager._tools["query_model"]
    schema = tool.model_dump()["parameters"]

    assert schema["type"] == "object"
    assert "properties" in schema
    assert "required" in schema
```

## MCP-Specific Testing Patterns

**Tool Testing Pattern:**
1. Create `MCPSemanticModel` with models dict
2. Use `async with Client(mcp) as client:` context manager
3. Call `await client.call_tool(tool_name, params_dict)`
4. Parse JSON response: `json.loads(result.content[0].text)`
5. Assert on parsed data structure

**Parameter Validation Testing:**
- Use `Pydantic TypeAdapter` to test validators in isolation
- Validator function: `_parse_json_string(v: Any) -> Any`
- Tests cover: valid JSON strings, native Python types, None values, invalid JSON

**Client Compatibility Testing:**
- Simulate Claude Desktop: `dimensions: '["field1", "field2"]'` (JSON string)
- Simulate regular MCP client: `dimensions: ["field1", "field2"]` (native list)
- Test backward compatibility by passing actual types and JSON strings

**Tool Error Handling:**
- Tools raise `ValueError` with descriptive message
- FastMCP Client converts to `ToolError` exception
- Tests catch `pytest.raises(ToolError, match="expected message")`

**Schema Compatibility:**
- Array parameters must have `"items"` key for Azure OpenAI compatibility
- Use `Field()` with `json_schema_extra={"items": {...}}` for schema customization
- All parameters should have `description` from loaded prompts

## Test Markers

**Defined Markers:**
- `@pytest.mark.slow` - Marks tests as slow (deselect with `-m "not slow"`)
- `@pytest.mark.asyncio` - Marks async tests for pytest-asyncio

**Usage Example:**
```python
@pytest.mark.slow
def test_preagg_stress():
    """Long-running stress test."""
    # Heavy computation
```

## Fixture Dependencies

**Chain Pattern:**
```python
# session -> module -> function
@pytest.fixture(scope="session")
def dataset_manager():
    return DatasetManager()

@pytest.fixture(scope="module")
def connection_manager():
    manager = ConnectionManager(in_memory=True)
    yield manager
    manager.close()

@pytest.fixture
def ibis_con(connection_manager):
    return connection_manager.get_ibis_connection()

@pytest.fixture
def load_dataset(connection_manager):
    def _load(dataset_name: str):
        return connection_manager.load_parquet_ibis(get_dataset(dataset_name))
    return _load
```

---

*Testing analysis: 2026-03-12*
