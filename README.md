# Boring Semantic Layer (BSL)

The Boring Semantic Layer (BSL) is a lightweight semantic layer based on [Ibis](https://ibis-project.org/).

**Key Features:**
- **Lightweight**: `pip install boring-semantic-layer`
- **Ibis-powered**: Built on top of [Ibis](https://ibis-project.org/), supporting any database engine that Ibis integrates with (DuckDB, Snowflake, BigQuery, PostgreSQL, and more)
- **MCP-friendly**: Perfect for connecting LLMs to structured data sources

## Quick Start

```bash
pip install 'boring-semantic-layer[examples]'
```

**1. Define your ibis input table**

```python
import ibis

# Create a simple in-memory table
flights_tbl = ibis.memtable({
    "origin": ["JFK", "LAX", "JFK", "ORD", "LAX"],
    "carrier": ["AA", "UA", "AA", "UA", "AA"]
})
```

**2. Define a semantic table**

```python
from boring_semantic_layer import to_semantic_table
flights = (
    to_semantic_table(flights_tbl, name="flights")
    .with_dimensions(origin=lambda t: t.origin)
    .with_measures(flight_count=lambda t: t.count())
)
```

**3. Query it**

```python
result_df = flights.group_by("origin").aggregate("flight_count").execute()
```

---

## 🤖 Agent Skills

BSL ships **skills** that teach coding agents (Claude Code, Cursor, Codex) how to
build, query, and deploy BSL. Install them into your project with the `bsl` CLI:

```bash
bsl skill list                      # see all available skills
bsl skill install claude-code       # install all skills for Claude Code
                                    # (also supported: cursor, codex)
```

For Claude Code this writes each skill to `.claude/skills/<name>/SKILL.md`, where
it is auto-discovered and invocable as a `/<name>` slash command.

| Skill | What it covers |
|-------|----------------|
| **`bsl-production-guide`** | End-to-end production deployment: connect a database, infer/author models, joins, query, serve over MCP, and harden with schema-per-tenant multi-tenancy + auth. Start here for production. |
| **`bsl-model-builder`** | Building semantic models — dimensions, measures, joins, and YAML config. |
| **`bsl-query-expert`** | Querying models at runtime — `group_by`/`aggregate`/`filter`, time grains, and charts. |

---

## 📚 Documentation

**[→ View the full documentation](https://boringdata.github.io/boring-semantic-layer/)**

---

*This project is a joint effort by [xorq-labs](https://github.com/xorq-labs/xorq) and [boringdata](https://www.boringdata.io/).*

*We welcome feedback and contributions!*

---

*Freely inspired by the awesome [Malloy](https://github.com/malloydata/malloy) project. We loved the vision, just took the Python route.*
