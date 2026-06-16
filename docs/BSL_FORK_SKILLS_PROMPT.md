# BSL Fork: Native Skills and Schema Tooling

## Prompt for Claude Code (Plan Mode)

> **Repo**: `mattfili/boring-semantic-layer` (fork of `boringdata/boring-semantic-layer`)
>
> **Goal**: Add two optional feature sets to BSL that make it "agent-native" when desired but change nothing when not configured:
> 1. **Skills as MCP resources** — domain context (SKILL.md files) defined alongside semantic models in YAML, automatically registered as MCP resources
> 2. **Schema and source tooling** — MCP tools for inferring BSL YAML from raw data and connecting new ibis backends
>
> **Core constraint**: Everything is optional. A YAML with no `skills_dir` keys and a server that doesn't enable schema tools must behave identically to upstream BSL. Zero regressions. If you don't configure skills, no skill-related tools or resources are registered. BSL remains a pure semantic layer.

---

## 1. Context

### What BSL Does Today

BSL handles semantic models: YAML defines dimensions, measures, joins, and `from_yaml()` loads them into ibis-backed `SemanticTable` objects. `MCPSemanticModel` wraps those tables and registers MCP tools (`query_model`, `search_dimension_values`, `get_model`, `get_time_range`, etc.) and MCP resources (`semantic://models`).

### What BSL Does NOT Do Today

1. **Serve domain context**: The NPO Catalogue has domain skills (Form 990 interpretation guide, grant matching methodology) hardcoded in its server. These are MCP resources (`skill://form-990/SKILL.md`), aggregated by a custom `get_domain_context` tool. This should be a BSL feature.

2. **Help build itself**: Today, building a BSL YAML schema requires manual authoring. BSL knows its own YAML format and has ibis introspection — it should be able to infer schemas from raw data and provision new backend connections. These tools should live in BSL, not in a consuming plugin.

### What This Changes

After this work, BSL has three operating modes:

| Mode | Configuration | Result |
|---|---|---|
| **Pure semantic layer** | No `skills_dir`, no schema tools | Identical to upstream BSL today |
| **Semantic layer + domain context** | `skills_dir` in YAML | Models + skills served as MCP resources |
| **Full agent-native** | `skills_dir` + schema/source tools enabled | Models + skills + self-building tools |

The consuming server (NPO Catalogue, a Claude Code plugin, etc.) can use any subset. The plugin layer above BSL handles Claude Code-specific concerns: slash commands, hooks, file extraction (PDF/Excel/docx via Anthropic skills), pre-commit automation, and plugin distribution.

---

## 2. Feature 1: Skills as MCP Resources

### 2.1 What a Skill Is

A skill is a directory containing a `SKILL.md` file and optionally additional files:

```
<skill-name>/
├── SKILL.md                # Required. YAML frontmatter + markdown body.
├── <additional>.md         # Optional. More instructions or reference docs.
├── references/             # Optional. Reference materials (PDFs, CSVs, etc.)
│   ├── glossary.csv
│   └── source_doc.pdf
└── scripts/                # Optional. Executable code.
    └── validate.py
```

#### SKILL.md Format

```markdown
---
name: form-990
description: >
  Use this skill whenever working with US nonprofit or foundation data,
  interpreting Form 990 filings, analyzing grant relationships, or
  evaluating mission/geographic alignment between funders and recipients.
---

# Form 990 Interpretation Guide

## What is a Form 990?
...
```

### 2.2 Progressive Disclosure (Three Levels)

This is the most important design constraint. Skills load progressively to avoid bloating the MCP client's context window:

**Level 1 — Metadata (~100 tokens per skill):**
- Parsed eagerly when `from_yaml()` runs
- Contains ONLY `name` and `description` from SKILL.md YAML frontmatter
- Stored in memory. This is what `get_domain_context` returns.
- A server with 20 skills costs ~2,000 tokens at startup

**Level 2 — Instructions (target: under 5k tokens):**
- The full markdown body of SKILL.md below the frontmatter
- Read from disk only when the client calls `read_resource("skill://<name>/SKILL.md")`
- Never in memory at startup

**Level 3+ — Resources and code (effectively unlimited):**
- All other files in the skill directory
- Read from disk only when explicitly requested via `read_resource`
- Binary files (PDFs, images) base64-encoded when served
- Scripts meant to be executed by the client, not loaded into context

**Enforcement**: `from_yaml()` parses frontmatter eagerly (Level 1). It does NOT read SKILL.md bodies or other files. All Level 2+ content is lazy.

### 2.3 YAML Configuration

#### Parent-Level Skills (Optional)

A `skills_dir` key at the top level of the YAML config. Points to a directory of skill subdirectories. If omitted or the directory doesn't exist: no parent skills, no skill-related resources registered, no change in behavior.

```yaml
profile: default
skills_dir: ./skills          # Optional

organizations:
  table: organizations_tbl
  dimensions:
    ein: _.ein
  measures:
    org_count: _.count()
```

#### Model-Level Skills (Optional)

A `skills_dir` key on an individual model definition. Scoped to that model. If omitted: no model-level skills for that model.

```yaml
organizations:
  table: organizations_tbl
  skills_dir: ./skills/organizations   # Optional
  dimensions:
    ein: _.ein
  measures:
    org_count: _.count()
```

#### All Four Combinations

| Parent `skills_dir` | Model `skills_dir` | Result |
|---|---|---|
| Not set | Not set | Pure semantic layer. BSL works exactly as today. No skill tools or resources registered. |
| Set | Not set | Server-wide domain context only |
| Not set | Set on some models | Per-model domain context only |
| Set | Set on some models | Layered: general + model-specific context |

#### Directory Overlap Convention

If parent `skills_dir` is `./skills` and a model's `skills_dir` is `./skills/organizations`, BSL must not double-register:
1. Scan parent `skills_dir` for immediate subdirectories containing `SKILL.md`
2. Collect all model `skills_dir` absolute paths as an exclusion set
3. Skip any parent subdirectory whose path matches or is a child of a model's `skills_dir`
4. Scan each model's `skills_dir` independently

### 2.4 MCP Resource Registration

When `MCPSemanticModel.register(app)` is called and skills exist, register:

**For each parent-level skill:**
- `skill://<skill-name>/SKILL.md` — main skill file (`text/markdown`)
- `skill://<skill-name>/<relative-path>` — each additional file (appropriate MIME type)
- `skill://<skill-name>/_manifest` — JSON file listing

**For each model-level skill:**
- `skill://<model-name>/<skill-name>/SKILL.md`
- `skill://<model-name>/<skill-name>/<relative-path>`
- `skill://<model-name>/<skill-name>/_manifest`

**If no skills are configured**: no skill resources registered. `list_resources` returns only `semantic://models` (existing behavior).

MIME type inference:

| Extension | MIME Type |
|---|---|
| `.md` | `text/markdown` |
| `.pdf` | `application/pdf` |
| `.csv` | `text/csv` |
| `.json` | `application/json` |
| `.py` | `text/x-python` |
| `.sh` | `text/x-shellscript` |
| `.yaml`, `.yml` | `text/yaml` |
| `_manifest` | `application/json` |
| (other binary) | `application/octet-stream` |

Binary MIME types base64-encoded on serve.

#### `_manifest` Format

```json
{
  "skill_name": "form-990",
  "scope": "parent",
  "files": [
    {"path": "SKILL.md", "mime_type": "text/markdown"},
    {"path": "GLOSSARY.md", "mime_type": "text/markdown"},
    {"path": "references/how_to_read_form_990.pdf", "mime_type": "application/pdf"}
  ]
}
```

For model-scoped skills, `"scope"` is the model name.

### 2.5 `get_domain_context` Tool

**Conditional registration**: Only registered when at least one `skills_dir` is configured (parent or model-level). If no skills exist, this tool is not registered. Pure semantic layer servers don't get a tool they can't use.

No parameters. Returns aggregated Level 1 metadata:

```json
{
  "parent_skills": [
    {
      "name": "form-990",
      "description": "Use when working with US nonprofit data...",
      "files": ["SKILL.md", "GLOSSARY.md", "references/how_to_read_form_990.pdf"],
      "uri_prefix": "skill://form-990/"
    }
  ],
  "model_skills": {
    "organizations": [
      {
        "name": "revenue-analysis",
        "description": "Revenue trend interpretation...",
        "files": ["SKILL.md", "scripts/peer_benchmark.py"],
        "uri_prefix": "skill://organizations/revenue-analysis/"
      }
    ]
  },
  "models": [
    {
      "name": "organizations",
      "description": "Longitudinal records of US tax-exempt organizations"
    }
  ]
}
```

When called, re-scans all `skills_dir` paths to pick up runtime additions (new directories on disk).

### 2.6 `add_skill` Tool

**Conditional registration**: Only registered when at least one `skills_dir` is configured. If no skills directories exist, this tool is absent — not present-but-erroring, just absent.

Parameters:

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | yes | | Skill name (kebab-case, becomes directory name) |
| `description` | string | yes | | Skill description for SKILL.md frontmatter |
| `steps` | string | yes | | Markdown body content for SKILL.md |
| `model_name` | string | no | `null` | If provided, writes to that model's `skills_dir`. If null, writes to parent `skills_dir`. |
| `reference` | string | no | `null` | Additional reference content → written as `REFERENCE.md` |

Behavior:
1. Determine target: if `model_name` provided and that model has a `skills_dir`, use it. If `model_name` provided but that model has no `skills_dir`, fall back to parent `skills_dir`. If neither exists, this tool wouldn't be registered in the first place.
2. Create `<target>/<name>/SKILL.md` with frontmatter + body
3. If `reference` provided, create `<target>/<name>/REFERENCE.md`
4. Return confirmation with the new skill's URI prefix
5. New skill is immediately discoverable via `list_resources` and `get_domain_context` (because both re-scan on call)

### 2.7 Override Pattern for Custom Implementations

The NPO Catalogue has a custom `get_domain_context` that does more than BSL's default (includes efficiency tips, analytical workflow guidance, interleaves skill descriptions with model metadata). It should be able to keep that.

`MCPSemanticModel.register()` should accept a parameter to control which skill-related tools are registered:

```python
mcp_model.register(
    app,
    # Control which tools BSL registers. Defaults to True when skills exist.
    include_domain_context_tool=True,   # Register get_domain_context
    include_add_skill_tool=True,        # Register add_skill
)
```

If the consuming server sets `include_domain_context_tool=False`, BSL skips registering it. The consuming server can then register its own `get_domain_context` that calls BSL's skill metadata internally but formats the response differently, adds custom content, or includes information BSL doesn't know about.

The skill *resources* (SKILL.md files as MCP resources) are always registered when `skills_dir` is configured — those are data, not tools. The consuming server can't easily provide those. Only the tools on top (aggregation, writing) are overridable.

The NPO Catalogue migration path:
1. Set `skills_dir` in YAML, move skill directories there → BSL registers resources automatically
2. Set `include_domain_context_tool=False` → keep custom `get_domain_context`
3. Set `include_add_skill_tool=True` → use BSL's native `add_skill` (or False to keep custom)
4. Over time, migrate custom `get_domain_context` to BSL's default if it's sufficient

---

## 3. Feature 2: Schema and Source Tooling

### 3.1 Why This Lives in BSL, Not the Plugin

BSL knows its own YAML format. BSL knows ibis. BSL can introspect databases through ibis connections. The consuming plugin shouldn't need to reverse-engineer BSL's YAML schema or ibis backend configuration. If BSL can build itself, the plugin's job shrinks to orchestration (reading uploaded files, routing to BSL tools) and Claude Code-specific UX (slash commands, hooks).

### 3.2 `infer_schema` Tool

**Registration**: Opt-in. Not registered by default. The consuming server enables it:

```python
mcp_model.register(
    app,
    include_schema_tools=True,   # Register infer_schema and connect_source
)
```

Parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `table_name` | string | yes | Name for the new model |
| `source` | string | yes | Table name (for connected databases) or file path |
| `source_type` | string | no | `"table"` (default), `"csv"`, `"parquet"`, `"json"`. Auto-detected from file extension if not provided. |
| `description` | string | no | Model description. If omitted, inferred from table/file name. |
| `profile` | string | no | Connection profile to use. Defaults to the YAML's `profile`. |

Behavior:
1. Connect to the data source via ibis (using profile from `profiles.yml` or the specified profile)
2. Introspect the schema: column names, dtypes, sample values
3. Classify each column:
   - String/categorical/boolean → dimension
   - Numeric → measure (heuristic: columns ending in `_id`, `_code`, `_key` → dimension even if numeric; columns ending in `_amount`, `_total`, `_count`, `_rate` → measure with appropriate aggregation)
   - Timestamp/date → time dimension with appropriate grain
4. Generate field descriptions from column names (split on `_`, title-case, humanize)
5. Detect potential join keys (columns ending in `_id` that match other models' primary dimensions)
6. Return the proposed YAML as a string (not written to disk — the caller decides what to do with it)

Return format:
```json
{
  "proposed_yaml": "organizations:\n  table: organizations_tbl\n  ...",
  "column_classifications": [
    {"column": "ein", "dtype": "string", "classification": "dimension", "reasoning": "String type, likely identifier"},
    {"column": "total_revenue", "dtype": "float64", "classification": "measure", "aggregation": "sum", "reasoning": "Numeric, '_revenue' suffix"}
  ],
  "potential_joins": [
    {"column": "org_id", "matches_model": "organizations", "matches_dimension": "ein"}
  ]
}
```

The tool returns a *proposal*. It does not write YAML. The consuming agent/plugin/user reviews the proposal, modifies it, and writes the file. This keeps BSL's tool side-effect-free for schema operations.

### 3.3 `connect_source` Tool

**Registration**: Opt-in, same flag as `infer_schema`.

Parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `backend` | string | yes | `"duckdb"`, `"postgres"`, `"snowflake"`, `"bigquery"`, `"mysql"`, `"sqlite"`, `"clickhouse"` |
| `profile_name` | string | yes | Name for the new profile in `profiles.yml` |
| `connection_params` | dict | yes | Backend-specific connection parameters |

Behavior:
1. Validate the backend is supported by the installed ibis extras
2. Attempt to connect with the provided parameters
3. If successful, list available tables with row counts
4. Return the proposed `profiles.yml` entry as a string (not written — caller writes)

Return format:
```json
{
  "status": "connected",
  "proposed_profile_yaml": "my_db:\n  backend: postgres\n  connection:\n    host: localhost\n    ...",
  "available_tables": [
    {"name": "organizations", "row_count": 1800000},
    {"name": "officers", "row_count": 5200000}
  ]
}
```

Again, side-effect-free. Returns a proposal + table listing. The caller writes `profiles.yml`.

### 3.4 `list_backends` Tool

**Registration**: always, when schema tools are enabled.

No parameters. Returns:

```json
{
  "installed_backends": ["duckdb", "postgres"],
  "available_backends": ["snowflake", "bigquery", "mysql", "clickhouse", "sqlite"],
  "install_instructions": {
    "snowflake": "pip install 'ibis-framework[snowflake]'",
    "bigquery": "pip install 'ibis-framework[bigquery]'"
  }
}
```

---

## 4. What the Plugin Layer Handles (Not BSL)

For clarity on separation of concerns. BSL does not need to know about any of this:

| Plugin Responsibility | Why Not BSL |
|---|---|
| **Slash commands** (`/schema-builder`, `/domain-context`, `/connect-source`) | Claude Code UX. These delegate to BSL tools. |
| **File extraction** (PDF/Excel/docx → raw data for `infer_schema`) | Depends on python-docx, openpyxl, pdfplumber, Anthropic skills. BSL is a semantic layer, not an ETL tool. The plugin reads the file, extracts the schema, passes it to BSL's `infer_schema`. |
| **Hooks** (`SessionStart` → inject domain context, `PreToolUse` → validate YAML) | Claude Code lifecycle. BSL serves any MCP client. |
| **Pre-commit automation** (`marketplace.json`, `README.md`, `CLAUDE.md` generation) | Plugin distribution concern. |
| **Skill linting** (frontmatter validation) | Plugin quality gates. |
| **Interactive domain context building** (multi-input sessions with URL fetching, PDF reading) | Orchestration that calls BSL's `add_skill` after extracting content. |
| **CodeMode transform** | FastMCP wrapper concern. Could live in BSL's MCP setup or the plugin's `app.py`. |

---

## 5. Implementation Plan

### Phase 1: YAML Parsing — Skills Discovery

**Files to modify**: the YAML loader (`from_yaml()` implementation).

1. Look for top-level `skills_dir` key. If present, resolve path relative to YAML file. Store on returned object. If absent or directory missing, store `None`.

2. For each model definition, look for `skills_dir` key. If present, resolve and store. Ensure it's not interpreted as a dimension or measure.

3. For each `skills_dir` that exists on disk:
   - Scan immediate subdirectories for `SKILL.md`
   - Parse ONLY YAML frontmatter (between first two `---` delimiters) → extract `name` and `description`
   - Store as Level 1 metadata: `{"name": str, "description": str, "dir_path": Path}`
   - Do NOT read SKILL.md body or any other files

4. Handle overlap exclusion (Section 2.3).

5. Missing directories: warn, don't error.

### Phase 2: MCP Resource Registration

**Files to modify**: `MCPSemanticModel.register()`.

1. Check if any skills were discovered in Phase 1. If none: skip all skill registration. Behavior identical to today.

2. If skills exist: register resources lazily (read from disk on each `read_resource` call).

3. Register `_manifest` resources that walk the skill directory.

4. Binary files: base64-encode on serve.

### Phase 3: Conditional Tool Registration

**Files to modify**: `MCPSemanticModel.register()`.

1. `get_domain_context`: register only when skills exist AND `include_domain_context_tool=True` (default True).

2. `add_skill`: register only when at least one `skills_dir` exists AND `include_add_skill_tool=True` (default True).

3. `infer_schema`, `connect_source`, `list_backends`: register only when `include_schema_tools=True` (default False — opt-in).

```python
def register(
    self,
    app: FastMCP,
    include_domain_context_tool: bool = True,
    include_add_skill_tool: bool = True,
    include_schema_tools: bool = False,
):
    # Always register: query_model, search_dimension_values,
    # get_model, get_time_range, list_models, summarize_results,
    # semantic://models resource

    # Conditional on skills existing:
    if self.has_skills:
        self._register_skill_resources(app)
        if include_domain_context_tool:
            self._register_domain_context_tool(app)
        if include_add_skill_tool:
            self._register_add_skill_tool(app)

    # Conditional on explicit opt-in:
    if include_schema_tools:
        self._register_schema_tools(app)
```

### Phase 4: Schema Tooling Implementation

1. `infer_schema`: ibis introspection + column classification heuristics. Returns YAML proposal, does not write.

2. `connect_source`: ibis connection test + table listing. Returns profile YAML proposal, does not write.

3. `list_backends`: check installed ibis extras.

### Phase 5: Testing

1. **No skills, no schema tools**: `from_yaml()` on vanilla BSL YAML. Verify identical behavior. `list_resources` returns only `semantic://models`. No `get_domain_context` or `add_skill` tools. No `infer_schema` tools.

2. **Parent skills only**: Verify resources registered, `get_domain_context` works, `add_skill` works, progressive disclosure enforced.

3. **Model skills only**: Verify namespaced URIs.

4. **Both levels**: Verify overlap exclusion, both scopes in `get_domain_context` response.

5. **Override pattern**: `include_domain_context_tool=False` → verify BSL skips that tool. Consuming server registers its own. Resources still registered.

6. **Schema tools opt-in**: `include_schema_tools=True` → verify `infer_schema` and `connect_source` are available. `include_schema_tools=False` (default) → verify they're absent.

7. **Missing directory**: `skills_dir` points to non-existent path. Warn. No skills registered. `get_domain_context` not registered. No error.

8. **Binary resources**: PDF in skill directory → base64 served correctly.

9. **Runtime skill addition**: `add_skill` → verify `list_resources` and `get_domain_context` pick it up.

10. **`infer_schema` side-effect-free**: Verify it returns YAML string, does not write files.

11. **`add_skill` fallback**: `model_name` provided but that model has no `skills_dir` → falls back to parent `skills_dir`. If parent also doesn't exist, the tool wouldn't be registered.

---

## 6. YAML Schema Reference

```yaml
# Top-level keys
profile: <string>                    # Connection profile (existing)
skills_dir: <path>                   # Optional: parent-level skills directory
                                     # Resolved relative to YAML file location

# Model definition
<model_name>:
  table: <string>                    # Table or file reference (existing)
  description: <string>              # Model description (existing)
  skills_dir: <path>                 # Optional: model-level skills directory
  dimensions: ...                    # Existing BSL feature
  measures: ...                      # Existing BSL feature
  joins: ...                         # Existing BSL feature
```

## 7. SKILL.md Frontmatter Schema

```yaml
---
# Required by BSL
name: <string>              # kebab-case, should match directory name
description: <string>       # 1-3 sentences. Include trigger phrases.

# Optional (Claude Code convention — BSL stores but does not act on these)
allowed-tools: <string>
disable-model-invocation: <bool>
context: <fork|inline>
agent: <string>
version: <string>
author: <string>
tags: <list[string]>
hooks: <dict>
---
```

BSL reads `name` and `description`. Everything else is stored opaquely.

---

## 8. NPO Catalogue Migration

1. Add `skills_dir: ./skills` to YAML config
2. Move existing skill directories into `./skills/`
3. `MCPSemanticModel.register(app, include_domain_context_tool=False)` — keep custom implementation initially
4. Verify `list_resources` returns same URIs as before
5. Over time: evaluate whether BSL's native `get_domain_context` is sufficient, switch to `True`

---

## 9. Non-Goals

- **Skill execution**: BSL serves files. Clients execute scripts.
- **File parsing**: BSL's `infer_schema` works on ibis-accessible sources (database tables, CSV, parquet). It does not parse PDF/Excel/docx. That's the plugin's job.
- **Skill versioning or dependencies**: Opaque metadata. No graph resolution.
- **Hot reload of model definitions**: Only skills support runtime discovery. Model YAML changes need restart.
- **Upstream merge**: Fork-specific. No-skills behavior identical to upstream.
