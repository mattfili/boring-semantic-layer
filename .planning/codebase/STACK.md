# Technology Stack

**Analysis Date:** 2026-03-12

## Languages

**Primary:**
- Python 3.10+ - Core semantic layer, agents, and MCP server implementation
- JavaScript/TypeScript - Documentation website (React + Vite)

**Secondary:**
- YAML - Semantic model definitions and configuration files
- Markdown - Documentation and prompt templates

## Runtime

**Environment:**
- Python 3.10, 3.11, 3.12 - Core runtime (tested in CI with 3.12)
- Node.js 20 - Documentation site builds

**Package Manager:**
- uv - Primary Python package manager with workspace support
- npm - Node.js package management for docs website
- Lockfiles: `uv.lock`, `docs/web/package-lock.json`

## Frameworks

**Core Semantic Layer:**
- ibis-framework 11.0.0+ - Multi-backend SQL compilation and query abstraction
- xorq 0.3.11+ - Vendored ibis with extended functionality

**Agent & MCP Backends:**
- fastmcp 2.12.4+ - FastMCP server implementation (optional: `[mcp]` extra)
- langchain 0.3.0+ - Agent framework with middleware support
- langchain-core - Core abstractions and tools
- langchain-anthropic, langchain-openai - LLM integrations

**Visualization:**
- altair 5.0.0+ - Declarative visualization (default chart backend)
- plotly 6.3.0+ - Interactive HTML charts
- plotext 5.0.0+ - Terminal-based ASCII charts
- vl-convert-python - Vega-Lite to image conversion

**Configuration & Serialization:**
- pydantic - Data validation and serialization
- pyyaml 6.0+ - YAML parsing for semantic models
- attrs 25.3.0+ - Dataclass-like utilities

**Development & Utilities:**
- returns 0.26.0+ - Functional result/error handling
- toolz 1.0.0+ - Functional utilities
- packaging - Version parsing
- python-dotenv - Environment variable loading

**Testing:**
- pytest - Test runner
- pytest-asyncio - Async test support
- duckdb<1.4 - In-memory database for tests and examples

**Documentation & Web:**
- React 18.3.1 - Component framework
- Vite 5.4.19 - Fast build tool
- TailwindCSS 3.4.17 - Utility CSS
- Recharts, Vega-Embed - Chart rendering in docs
- React Router, React Query - Navigation and data fetching

## Key Dependencies

**Critical:**
- ibis-framework 11.0.0 - Foundation of query compilation and backend abstraction
- pydantic - Type validation for tool schemas and configurations
- fastmcp 2.12.4 - MCP protocol implementation for server functionality

**Infrastructure & Databases:**
- duckdb (vendored via xorq, also <1.4 for tests) - In-memory and file-based SQL engine
- Support for backends via xorq's profile system: DuckDB, PostgreSQL, BigQuery, Snowflake, etc.
- ibis supports 20+ backends through universal table expression API

**LLM Integrations:**
- anthropic 0.75.0 - Anthropic API client (used via langchain-anthropic)
- openai 1.0.0+ - OpenAI API client (used via langchain-openai)
- langchain-anthropic 0.3.0+ - Anthropic-specific middleware (prompt caching, tool use)
- langchain-openai 0.3.0+ - OpenAI integration

**Messaging & Chat:**
- slack-bolt - Slack bot framework (optional: `[slack]` extra, not in core)

## Configuration

**Environment:**
- `.env`, `.env.user` - Local environment variable configuration
- `.envrc`, `.envrc.user` - Direnv environment setup
- Supports lazy loading via `dotenv.load_dotenv()`

**Required Environment Variables (by feature):**
- MCP Server: Uses `PROMPTS_DIR` detection (installed location or dev location)
- LangGraph Backend: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (loaded via langchain)
- Database Profiles: `BSL_PROFILE` and `BSL_PROFILE_FILE` (or pass explicitly)
- Slack Bot: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`

**Build Configuration:**
- `pyproject.toml` - Project metadata, dependencies, build config
- `tsconfig.json` - TypeScript config for docs (not used, JS-only site)
- `.ruff.toml` - Formatting and linting rules (configured in pyproject.toml)
- `flake.nix`, `uv2nix` - Nix packaging for development environment

## Platform Requirements

**Development:**
- Python 3.10+
- uv package manager
- Node.js 20+ (for docs website)
- macOS or Linux (x86_64-linux, aarch64-darwin via Nix)

**Production:**
- Python 3.10+
- No Node.js required (docs are pre-built)
- Any OS with Python support
- Database connectivity via ibis backends (DuckDB, PostgreSQL, BigQuery, Snowflake, etc.)

---

*Stack analysis: 2026-03-12*
