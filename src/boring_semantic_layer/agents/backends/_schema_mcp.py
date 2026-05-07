"""MCP tool registrations for the schema-tools opt-in path."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

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
    def list_backends() -> dict:
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
    def connect_source(
        backend: str,
        profile_name: str,
        connection_params: dict,
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
            try:
                con = open_backend(config)
            except Exception as exc:
                raise ToolError(_sanitize_error(str(exc), connection_params)) from exc

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
