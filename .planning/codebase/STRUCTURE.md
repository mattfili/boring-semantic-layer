# Codebase Structure

**Analysis Date:** 2026-03-12

## Directory Layout

```
boring-semantic-layer/
├── src/boring_semantic_layer/          # Main package
│   ├── __init__.py                     # Public API + lazy imports (MCPSemanticModel, LangGraphBackend)
│   ├── expr.py                         # SemanticModel, SemanticTable, expression classes
│   ├── ops.py                          # Dimension, Measure, operation nodes (SemanticJoin, etc.)
│   ├── query.py                        # Parameter-based query interface (query_model tool backend)
│   ├── api.py                          # Public functional API (to_semantic_table, join_one, join_many)
│   ├── yaml.py                         # YAML model loader + profile resolution
│   ├── profile.py                      # Database connection profiles + environment handling
│   ├── convert.py                      # Dispatch handlers for semantic → ibis conversion
│   ├── format.py                       # Dispatch handlers for ibis formatting
│   ├── utils.py                        # Helper utilities (safe_eval, safe_math, etc.)
│   ├── config.py                       # Global options/configuration
│   ├── serialization/                  # Metadata extraction & xorq tagging
│   │   ├── __init__.py                 # Public API (to_tagged, from_tagged)
│   │   ├── extract.py                  # Operation tree extraction, metadata serialization
│   │   ├── freeze.py                   # Recursive freeze for xorq compatibility
│   │   ├── reconstruct.py              # Rebuild BSL operations from tagged metadata
│   │   ├── context.py                  # Serialization context configuration
│   │   └── helpers.py                  # Utility functions
│   ├── chart/                          # Charting backends (altair, plotly, plotext)
│   │   ├── __init__.py                 # Chart factory
│   │   ├── base.py                     # Base chart interface
│   │   ├── altair_chart.py             # Altair (Vega-Lite) implementation
│   │   ├── plotly_chart.py             # Plotly implementation
│   │   ├── plotext_chart.py            # Plotext (terminal) implementation
│   │   ├── echarts/                    # ECharts adapter for JS charting
│   │   │   ├── backend.py              # ECharts backend
│   │   │   ├── interface.py            # Type definitions
│   │   │   └── types.py                # TypedDict definitions
│   │   ├── md_parser/                  # Markdown dashboard parsing & execution
│   │   │   ├── parser.py               # Markdown to AST parser
│   │   │   ├── core.py                 # Dashboard core structures
│   │   │   ├── executor.py             # Execute embedded BSL queries
│   │   │   ├── converter.py            # Dashboard → HTML conversion
│   │   │   └── renderer.py             # HTML output rendering
│   │   └── tests/                      # Chart tests
│   ├── agents/                         # Agent backends & CLI
│   │   ├── cli.py                      # Main CLI entry point (bsl command)
│   │   ├── tools.py                    # BSLTools class - unified tool implementations
│   │   ├── backends/                   # Protocol-specific agent implementations
│   │   │   ├── __init__.py             # Lazy imports (LangGraphBackend)
│   │   │   ├── mcp.py                  # MCPSemanticModel (FastMCP-based)
│   │   │   └── langgraph.py            # LangGraphBackend (LangGraph-based with middleware)
│   │   ├── chats/                      # Chat implementations (CLI, Slack)
│   │   │   ├── cli.py                  # Interactive CLI chat
│   │   │   └── slack.py                # Slack bot integration
│   │   ├── utils/                      # Agent utilities
│   │   │   ├── chart_handler.py        # generate_chart_with_data() function
│   │   │   ├── prompts.py              # load_prompt() for markdown files
│   │   │   └── tokens.py               # Token counting utilities
│   │   ├── eval/                       # Evaluation/testing utilities
│   │   │   └── eval.py                 # Evaluation framework
│   │   └── tests/                      # Agent tests
│   ├── tests/                          # Core semantic tests
│   │   ├── fixtures/                   # Test data (flights.yml, etc.)
│   │   ├── integration/                # Integration tests
│   │   └── test_*.py                   # Various test modules
│   └── [other files]                   # graph_utils, nested_access, etc.
├── examples/                           # Example scripts
│   ├── example_mcp.py                  # MCP server example
│   ├── example_mcp_cohort.py           # MCP with cohort analysis
│   ├── example_openai_tool.py          # OpenAI function calling integration
│   ├── flights.yml                     # Example semantic model (flights data)
│   ├── profiles.yml                    # Example database profiles
│   └── [other examples]                # Advanced modeling, joins, etc.
├── docs/                               # Documentation
│   ├── md/                             # Markdown documentation
│   │   ├── prompts/                    # Tool descriptions & system prompts
│   │   │   ├── query/                  # Query-specific prompts
│   │   │   │   ├── mcp/                # MCP server prompts
│   │   │   │   └── langchain/          # LangGraph agent prompts
│   │   │   └── [other prompts]
│   │   ├── skills/                     # Skills for distribution (Claude Code, Cursor, Codex)
│   │   ├── doc/                        # Reference documentation
│   │   └── index.json                  # Documentation index
│   └── web/                            # Web-based documentation site
├── scripts/                            # Utility scripts
├── pyproject.toml                      # Package configuration, dependencies, entry points
├── Makefile                            # Development tasks
├── requirements-dev.txt                # Development dependencies
└── .planning/codebase/                 # This directory (generated analysis docs)
```

## Directory Purposes

**`src/boring_semantic_layer/`:**
- Purpose: Core package - semantic abstraction, query execution, agent backends
- Contains: Semantic model classes, query operators, YAML parsing, agent implementations
- Key files: `expr.py` (models), `ops.py` (operations), `query.py` (parameters), `yaml.py` (config)

**`src/boring_semantic_layer/agents/`:**
- Purpose: Agent interfaces - CLI, MCP, LangGraph
- Contains: Tool definitions, backend implementations, CLI command routing
- Key files: `tools.py` (unified tool interface), `backends/mcp.py` (FastMCP server), `backends/langgraph.py` (LangGraph agent)

**`src/boring_semantic_layer/serialization/`:**
- Purpose: Metadata persistence - extract semantic structure for optimization/introspection
- Contains: Operation tree extraction, xorq tagging, metadata reconstruction
- Key files: `extract.py` (tree traversal), `freeze.py` (recursive serialization)

**`src/boring_semantic_layer/chart/`:**
- Purpose: Data visualization - multiple backends + markdown dashboard support
- Contains: Chart implementations, markdown parser, HTML renderer
- Key files: `base.py` (interface), `altair_chart.py` (browser charts), `plotext_chart.py` (terminal charts)

**`examples/`:**
- Purpose: Runnable examples demonstrating MCP, agent, and model composition patterns
- Contains: YAML model definitions, MCP server examples, integration examples
- Key files: `example_mcp.py` (MCP server example), `flights.yml` (test data)

**`docs/md/prompts/`:**
- Purpose: Tool descriptions and system prompts (loaded at runtime)
- Contains: Markdown files for tool descriptions, parameter documentation, system instructions
- Key files: `query/mcp/system.md`, `query/mcp/tool-*.md` for MCP prompts

## Key File Locations

**Entry Points:**
- `src/boring_semantic_layer/__init__.py`: Public API exports (lazy-loaded optional backends)
- `src/boring_semantic_layer/agents/cli.py` (line 314): CLI main() function for `bsl` command
- `examples/example_mcp.py`: MCP server instantiation example

**Configuration:**
- `pyproject.toml`: Package version, dependencies, entry points
- `src/boring_semantic_layer/profile.py`: Database connection profiles
- `src/boring_semantic_layer/config.py`: Global options

**Core Logic:**
- `src/boring_semantic_layer/expr.py`: SemanticModel, SemanticTable classes
- `src/boring_semantic_layer/ops.py`: Dimension, Measure, operation nodes (SemanticJoin, SemanticFilter, SemanticAggregate, etc.)
- `src/boring_semantic_layer/query.py`: Parameter-based query builder (used by MCP query_model tool)
- `src/boring_semantic_layer/yaml.py`: Model loading from YAML

**Agent Backends:**
- `src/boring_semantic_layer/agents/backends/mcp.py`: MCPSemanticModel class (FastMCP implementation)
- `src/boring_semantic_layer/agents/backends/langgraph.py`: LangGraphBackend class (with middleware)
- `src/boring_semantic_layer/agents/tools.py`: BSLTools (unified tool interface for both backends)

**Testing:**
- `src/boring_semantic_layer/tests/test_semantic_mcp.py`: MCP backend tests
- `src/boring_semantic_layer/agents/tests/test_semantic_mcp.py`: Agent MCP tests
- `src/boring_semantic_layer/agents/tests/test_langgraph_backend.py`: LangGraph backend tests

## Naming Conventions

**Files:**
- `test_*.py`: Test modules (pytest auto-discovery)
- `*_test.py`: Alternative test naming (also supported)
- `example_*.py`: Runnable example scripts
- `*_chart.py`: Chart backend implementations (e.g., `altair_chart.py`)
- `tool-*.md`: Tool description prompts (loaded by prompt loader)

**Directories:**
- `backends/`: Protocol-specific implementations
- `chats/`: Chat interface implementations
- `utils/`: Utility modules and helpers
- `tests/`: Test modules (co-located with code)
- `fixtures/`: Test data and fixtures
- `md_parser/`: Markdown-specific functionality

**Classes:**
- `Semantic*`: Core semantic operations (SemanticModel, SemanticTable, SemanticJoin, etc.)
- `*Backend`: Agent backend implementations (LangGraphBackend)
- `*Chart`: Chart backend implementations (AltairChart, PlotlyChart, PlotextChart)

**Functions:**
- `_*`: Internal/private functions (leading underscore)
- `get_*`: Accessor functions (e.g., `get_dimensions()`)
- `to_*`: Conversion functions (e.g., `to_semantic_table()`)
- `from_*`: Construction/loading functions (e.g., `from_yaml()`)

## Where to Add New Code

**New Feature (Query Capability):**
- Primary code: `src/boring_semantic_layer/expr.py` (add method to SemanticTable/SemanticModel)
- Supporting ops: `src/boring_semantic_layer/ops.py` (add new operation node class if needed)
- Tests: `src/boring_semantic_layer/tests/` (co-located with code)
- Integration: Update `src/boring_semantic_layer/agents/tools.py` if tool-accessible

**New Agent Backend (e.g., FastAPI Server):**
- Implementation: `src/boring_semantic_layer/agents/backends/fastapi.py` (new file)
- Base class: Extend tool interface from `src/boring_semantic_layer/agents/tools.py`
- Tests: `src/boring_semantic_layer/agents/tests/test_fastapi_backend.py`
- Export: Add to `src/boring_semantic_layer/agents/backends/__init__.py` lazy imports

**New Chart Backend:**
- Implementation: `src/boring_semantic_layer/chart/mylib_chart.py` (new file)
- Base class: Inherit from `src/boring_semantic_layer/chart/base.py`
- Factory registration: Update `src/boring_semantic_layer/chart/__init__.py` dispatch
- Tests: `src/boring_semantic_layer/chart/tests/test_mylib_chart.py`

**New CLI Command:**
- Implementation: `src/boring_semantic_layer/agents/cli.py` (add function cmd_mycommand())
- Parser setup: Add subparser in `main()` function
- Entry point: Register in pyproject.toml if needed
- Tests: `src/boring_semantic_layer/agents/tests/test_chats_cli.py`

**Utilities & Helpers:**
- Shared utils: `src/boring_semantic_layer/utils.py` (general utilities)
- Agent utils: `src/boring_semantic_layer/agents/utils/` (agent-specific helpers)
- Chart utils: `src/boring_semantic_layer/chart/utils.py` (charting utilities)

## Special Directories

**`docs/md/prompts/`:**
- Purpose: Tool descriptions and system prompts loaded at runtime
- Generated: No (manually authored)
- Committed: Yes
- Structure: Subdirectories by backend (`query/mcp/`, `query/langchain/`)
- Usage: Loaded via `load_prompt(dir, filename)` - enables prompt engineering without code changes

**`docs/md/skills/`:**
- Purpose: Skills distributed to tools (Claude Code, Cursor, Codex)
- Generated: No (manually authored)
- Committed: Yes
- Structure: `skills/{tool_name}/{skill_name}/SKILL.md`
- Distribution: Via `bsl` CLI command

**`src/boring_semantic_layer/tests/fixtures/`:**
- Purpose: Test data (YAML models, profiles, sample datasets)
- Generated: No (pre-created test fixtures)
- Committed: Yes
- Key files: `flights.yml` (example semantic model), `profiles.yml` (test db configs)

**`src/boring_semantic_layer/tests/integration/`:**
- Purpose: Integration tests requiring database connections
- Generated: No
- Committed: Yes
- Usage: Run with `pytest tests/integration/` (may require --tb-malloy flags)

**`.planning/codebase/`:**
- Purpose: Generated analysis documents (this directory)
- Generated: Yes (via `/gsd:map-codebase` command)
- Committed: Yes (documents tracked in git)
- Contents: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md

## MCP-Specific Architecture Notes (FastMCP 3.0 Migration)

**Current State (FastMCP 2.x):**
- Tool registration: Decorator-based `@self.tool()` in `MCPSemanticModel._register_tools()`
- Tool definitions: Generated from function signatures + docstrings
- Parameter handling: Pydantic validators (`BeforeValidator(_parse_json_string)`) for JSON string coercion
- System instructions: Loaded from markdown (`PROMPTS_DIR/system.md`)

**FastMCP 3.0 Migration Considerations:**
- **Tool registry changes**: Likely move from decorator-in-__init__ to static tool definitions
- **Parameter validation**: May require schema updates if Pydantic v2 validation changes
- **Prompt injection**: System instructions + tool descriptions still markdown-driven; check for new loading patterns
- **Server lifecycle**: `.run()` method may have different initialization pattern
- **Async support**: Consider if FastMCP 3.0 adds async tool support (MCPSemanticModel currently synchronous)

**Key Files to Monitor During Migration:**
- `src/boring_semantic_layer/agents/backends/mcp.py` - Tool registration, schema generation
- `src/boring_semantic_layer/agents/tools.py` - Tool executor logic (protocol-agnostic)
- `src/boring_semantic_layer/agents/utils/prompts.py` - Prompt loading mechanism
- `examples/example_mcp.py` - Server instantiation pattern

---

*Structure analysis: 2026-03-12*
