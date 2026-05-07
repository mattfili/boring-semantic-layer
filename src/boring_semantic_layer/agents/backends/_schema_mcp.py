"""MCP tool registrations for the schema-tools opt-in path."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp import Context
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


def register_schema_tools(server: MCPSemanticModel, prompts_dir: Path) -> None:
    """Register all three schema tools on ``server``."""
    _register_list_backends(server, prompts_dir)
    _register_connect_source(server, prompts_dir)
    _register_infer_schema(server, prompts_dir)


def _register_list_backends(server: MCPSemanticModel, prompts_dir: Path) -> None:
    @server.tool(
        name="list_backends",
        description=(
            load_prompt(prompts_dir, "tool-list-backends-desc.md")
            or "List supported and installed ibis backends."
        ),
        tags={"discovery"},
        annotations=_READONLY_ANNOTATIONS,
    )
    async def list_backends(ctx: Context | None = None) -> dict:
        if ctx:
            await ctx.info("Enumerating supported ibis backends")
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


def _register_connect_source(server: MCPSemanticModel, prompts_dir: Path) -> None:
    @server.tool(
        name="connect_source",
        description=(
            load_prompt(prompts_dir, "tool-connect-source-desc.md")
            or "Test a backend connection and propose a profile YAML."
        ),
        tags={"metadata"},
        annotations=_READONLY_ANNOTATIONS,
    )
    async def connect_source(
        backend: str,
        profile_name: str,
        connection_params: dict,
        ctx: Context | None = None,
    ) -> dict:
        # Imports inside function body so monkeypatch on the module path takes effect
        from ._source_inspection import (
            _sanitize_error,
            build_profile_yaml,
            list_tables_with_counts,
            open_backend,
        )

        if backend not in SUPPORTED_BACKENDS:
            raise ToolError(
                f"Backend '{backend}' not supported. Supported: {list(SUPPORTED_BACKENDS)}"
            )

        # Verify the ibis extra is installed
        try:
            importlib.import_module(f"ibis.backends.{backend}")
        except ImportError as exc:
            raise ToolError(f"Backend '{backend}' not installed. {INSTALL_HINTS[backend]}") from exc

        config = {"type": backend, **connection_params}
        con = None
        warning = None
        try:
            if ctx:
                await ctx.info(f"Connecting to {backend} backend...")
                await ctx.report_progress(progress=10, total=100)

            try:
                con = open_backend(config)
            except Exception as exc:
                raise ToolError(_sanitize_error(str(exc), connection_params)) from exc

            if ctx:
                await ctx.info("Connected. Listing tables (cap=100)...")
                await ctx.report_progress(progress=50, total=100)

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

            if ctx:
                await ctx.report_progress(progress=100, total=100)

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


def _resolve_table_source(server: MCPSemanticModel, source: str):
    """Return (ibis_table, transient_con). transient_con is None for table sources."""
    if not server.models:
        raise ToolError(
            "Cannot resolve table source: server has no registered models. "
            "Either configure at least one model first, or pass a file path."
        )
    sample_model = next(iter(server.models.values()))
    try:
        con = sample_model.table._find_backend()
    except Exception as exc:
        # Fallback: try .op().source chain
        con = getattr(sample_model.table.op(), "source", None)
        if con is None:
            raise ToolError(
                "Could not resolve the connection from the running models. "
                "Pass a file path with source_type='parquet' / 'csv' / 'json' instead."
            ) from exc
    if source not in con.list_tables():
        available = con.list_tables()[:10]
        raise ToolError(
            f"Table '{source}' not found in the connected backend. "
            f"Available (first 10): {available}"
        )
    return con.table(source), None


def _resolve_file_source(source: str, source_type: str):
    """Open a transient DuckDB and read the file."""
    from ._source_inspection import _sanitize_error, open_transient_duckdb_for_file

    try:
        con, tbl = open_transient_duckdb_for_file(source, source_type)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(_sanitize_error(str(exc), {})) from exc
    return tbl, con


def _register_infer_schema(server: MCPSemanticModel, prompts_dir: Path) -> None:
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
    async def infer_schema(
        table_name: str,
        source: str,
        source_type: str | None = None,
        description: str | None = None,
        profile: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        # Resolve effective source_type
        if source_type is None:
            ext = Path(source).suffix.lower().lstrip(".")
            source_type = ext if ext in {"parquet", "csv", "json"} else "table"

        if table_name in server.models:
            raise ToolError(
                f"Model name '{table_name}' already registered. "
                f"Existing models: {list(server.models.keys())}"
            )

        if ctx:
            await ctx.info(f"Inferring schema for '{table_name}' from {source_type} source")
            await ctx.report_progress(progress=10, total=100)

        transient_con = None
        try:
            if source_type == "table":
                ibis_tbl, _ = _resolve_table_source(server, source)
            elif source_type in {"csv", "parquet", "json"}:
                ibis_tbl, transient_con = _resolve_file_source(source, source_type)
            else:
                raise ToolError(
                    f"Unsupported source_type '{source_type}'. Supported: table, csv, parquet, json"
                )

            if not ibis_tbl.schema():
                raise ToolError(f"Source '{source}' has no columns")

            if ctx:
                await ctx.report_progress(progress=50, total=100)

            proposed = _infer(
                table_name=table_name,
                ibis_table=ibis_tbl,
                existing_models=server.models,
                description=description,
                profile=profile,
            )

            if ctx:
                await ctx.report_progress(progress=100, total=100)

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
