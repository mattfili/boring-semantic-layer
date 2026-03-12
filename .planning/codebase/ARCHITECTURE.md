# Architecture

**Analysis Date:** 2026-03-12

## Pattern Overview

**Overall:** Modular semantic abstraction layer with multi-backend support (CLI, MCP, LangGraph agents)

**Key Characteristics:**
- **Semantic-first design**: Wraps Ibis table expressions with domain-aware metadata (dimensions, measures)
- **Protocol-agnostic tools**: Core semantic operations decouple from agent backends (MCP, LangGraph, CLI)
- **Lazy evaluation with metadata tagging**: xorq-based serialization captures semantic intent for optimization
- **Optional dependency stratification**: Core BSL requires only Ibis; agents, MCP, and charting are optional
- **Expression-based query interface**: Fluent API for chaining semantic operations without exposing SQL

## Layers

**Core Semantic Layer:**
- Purpose: Define and manipulate semantic models as augmented Ibis tables
- Location: `src/boring_semantic_layer/expr.py`, `src/boring_semantic_layer/ops.py`
- Contains: `SemanticModel`, `SemanticTable` classes; dimension/measure definitions; query operators
- Depends on: Ibis, attrs, returns library for Result types
- Used by: All downstream backends (MCP, LangGraph, CLI)

**Query & Parameter Interface:**
- Purpose: Translate parameter-based queries into semantic operations (alternative to method chaining)
- Location: `src/boring_semantic_layer/query.py`
- Contains: Parameter validators, time grain transformations, filter builders
- Depends on: Core semantic layer, safe_eval utilities
- Used by: MCP server for tool parameter handling, CLI query parsing

**YAML Configuration Parser:**
- Purpose: Load semantic models from YAML configuration; enable profile-based database switching
- Location: `src/boring_semantic_layer/yaml.py`
- Contains: YAML parsing, dimension/measure extraction, model composition, join resolution
- Depends on: Core semantic layer, profile manager, safe_eval
- Used by: CLI initialization, examples, MCP server instantiation

**Serialization & Metadata Tagging:**
- Purpose: Extract semantic metadata and serialize to xorq tags for optimization and persistence
- Location: `src/boring_semantic_layer/serialization/`
- Contains: Operation tree extraction, metadata freezing, xorq tagging, reconstruction
- Depends on: xorq (optional), Ibis backends
- Used by: Advanced caching, metadata introspection

**Agent Tools & Utilities:**
- Purpose: Provide tool implementations for agents; handle chart generation and documentation
- Location: `src/boring_semantic_layer/agents/tools.py`, `src/boring_semantic_layer/agents/utils/`
- Contains: `BSLTools` class with tool executors, chart generation, prompt loading
- Depends on: Core semantic layer, charting backends, markdown documentation
- Used by: All agent backends (MCP, LangGraph)

**Agent Backends:**
- Purpose: Implement protocol-specific agent interfaces (MCP, LangGraph, CLI)
- Location: `src/boring_semantic_layer/agents/backends/`
- Contains: `MCPSemanticModel` (FastMCP-based), `LangGraphBackend` (LangGraph-based)
- Depends on: BSLTools, agent frameworks (fastmcp, langchain, langgraph)
- Used by: External clients (Claude Desktop, custom integrations)

**CLI Entry Points:**
- Purpose: Provide command-line interfaces for chat, rendering, skill distribution
- Location: `src/boring_semantic_layer/agents/cli.py`, `src/boring_semantic_layer/agents/chats/`
- Contains: Argument parsing, command routing, output formatting
- Depends on: Agent backends, utilities
- Used by: `bsl` command-line tool

## Data Flow

**Model Loading Flow:**
1. YAML definition → `yaml.py` parser
2. Parser creates `SemanticModel` with dimensions/measures
3. Returns `Mapping[str, SemanticModel]` for agent registration

**Query Execution Flow (Parameter-based):**
1. Agent receives parameters (model_name, dimensions, measures, filters, etc.)
2. Tool executor (MCP or LangGraph) converts parameters to semantic operations via `query.py`
3. `SemanticModel.query()` chains: `group_by()` → `filter()` → `order_by()` → `limit()`
4. Result passed to `generate_chart_with_data()` for visualization
5. Returns: Records (DataFrame) + Chart (JSON/HTML) + Metadata

**MCP Tool Registration Flow:**
1. `MCPSemanticModel.__init__()` stores models and calls `_register_tools()`
2. `_register_tools()` uses `@self.tool()` decorator to register FastMCP tools:
   - `list_models()` - returns model names
   - `get_model(model_name)` - returns schema with dimensions/measures
   - `query_model(...)` - executes parameterized query
   - `search_dimension_values(...)` - dimension value enumeration
   - `get_time_range(model_name)` - time bounds for time series
3. FastMCP serializes tool definitions to JSON Schema
4. Client (Claude Desktop) calls tool → FastMCP invokes registered function → returns result

**LangGraph Backend Flow:**
1. `LangGraphBackend.__init__()` extends `BSLTools` and creates LangGraph agent
2. Calls `get_callable_tools()` to convert tool definitions to LangChain StructuredTools
3. LangGraph middleware stack applied (TodoList, ContextEditing, Summarization, AnthropicPromptCaching)
4. Agent receives user input → checks tool calls → executes via `BSLTools.execute()` → loops until done

**State Management:**
- **Per-request**: Parameters flow through semantic operations (immutable)
- **Per-session (LangGraph)**: Conversation history stored in `conversation_history` list
- **Per-model (CLI/MCP)**: Models cached in `self.models` Mapping (loaded once at startup)

## Key Abstractions

**SemanticModel (Primary):**
- Purpose: Represents a queryable semantic table with dimensions and measures
- Examples: `src/boring_semantic_layer/expr.py` line 397 (`SemanticModel.__init__`)
- Pattern: Wraps Ibis table + metadata; supports fluent method chaining (`.filter().group_by().aggregate()`)
- Construction: Via `to_semantic_table()` API or `SemanticModel()` constructor directly

**Dimension:**
- Purpose: Represents a groupable attribute with optional time grain, entity, or event timestamp metadata
- Examples: `src/boring_semantic_layer/ops.py` (Dimension class)
- Pattern: Deferred expression (_.col) + metadata; resolved at execution time
- Usage in YAML: Simple string ("_.col_name") or extended dict with description/grains

**Measure:**
- Purpose: Represents an aggregatable metric
- Examples: `src/boring_semantic_layer/ops.py` (Measure class)
- Pattern: Can be base (direct table column aggregation) or calculated (from measures/dimensions)

**MCPSemanticModel (MCP-specific):**
- Purpose: Extends FastMCP with semantic-aware tool registration
- Examples: `src/boring_semantic_layer/agents/backends/mcp.py` line 47
- Pattern: Subclass of FastMCP; `_register_tools()` installs tools using decorator pattern
- Tool registration: `@self.tool()` decorator binds function + description + prompt loading

**BSLTools (Multi-backend):**
- Purpose: Encapsulates tool implementations (list_models, get_model, query_model, get_documentation)
- Examples: `src/boring_semantic_layer/agents/tools.py` line 129
- Pattern: Executor class; `execute(name, arguments)` dispatches to handler methods
- Used by: MCP (indirectly via MCPSemanticModel), LangGraph (via `get_callable_tools()`)

**SemanticJoin/SemanticFilter/SemanticAggregate:**
- Purpose: Operation nodes in semantic expression tree
- Examples: `src/boring_semantic_layer/expr.py` (SemanticJoin, SemanticFilter, SemanticAggregate classes)
- Pattern: Each operation wraps a source + operation-specific parameters; chainable
- Execution: Converted to Ibis via `to_untagged()` before execution

## Entry Points

**MCP Server:**
- Location: `examples/example_mcp.py` or `src/boring_semantic_layer/agents/backends/mcp.py`
- Triggers: `MCPSemanticModel(models).run()` or subprocess via `uv run example_mcp.py`
- Responsibilities: Listen on stdio for MCP protocol, invoke tools, return results to client

**CLI Chat:**
- Location: `src/boring_semantic_layer/agents/cli.py` line 314 (`main()`)
- Triggers: `bsl chat --sm path/to/models.yml --llm anthropic:claude-opus-4-20250514`
- Responsibilities: Parse args, instantiate `LangGraphBackend`, run agent conversation loop

**CLI Render:**
- Location: `src/boring_semantic_layer/agents/cli.py` (`cmd_render()`)
- Triggers: `bsl render path/to/dashboard.md`
- Responsibilities: Parse markdown, execute BSL queries, render HTML dashboard

**LangGraph Agent Query:**
- Location: `src/boring_semantic_layer/agents/backends/langgraph.py` line 95 (`query()`)
- Triggers: Direct call from CLI or custom integration
- Responsibilities: Accept user input, orchestrate tool calls, return final response

## Error Handling

**Strategy:** Result types (returns library) for semantic operations; exceptions for agent layers

**Patterns:**
- Core semantic: Uses `Result[T, E]` to represent success/failure without exceptions
- Tool executors: Catch exceptions, return error strings to LLM or console
- MCP: Raises ValueError with descriptive messages; FastMCP serializes to client
- LangGraph: ToolException with guidance text; middleware summarizes context
- Query execution: Truncates large error messages; adds helpful tips for common mistakes (e.g., `in` vs `.isin()`)

## Cross-Cutting Concerns

**Logging:**
- No structured logging framework; uses Python standard logging via `argparse` verbose flag
- MCP/CLI rely on stderr for debug output

**Validation:**
- Dimension/measure names validated at YAML parse time
- Filter expressions validated via safe_eval with allowlist
- Tool parameters validated via Pydantic (MCP) or LangChain (LangGraph)

**Authentication:**
- Handled by profile manager (`src/boring_semantic_layer/profile.py`)
- Profiles loaded from profiles.yml (connection credentials)
- Environment variable support via dotenv

**Caching:**
- xorq-based smart aggregation caching (optional via `to_tagged()` with storage backend)
- Tool definition caching via `@cache` decorator (functools)
- Model loading cached at startup

**Documentation:**
- Prompt templates loaded from `docs/md/prompts/` (or installed location)
- Skill files distributed via `bsl` CLI
- Tool descriptions from markdown files (e.g., "tool-list-models-desc.md")

---

*Architecture analysis: 2026-03-12*
