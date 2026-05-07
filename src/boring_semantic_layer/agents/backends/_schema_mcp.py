"""MCP tool registrations for the schema-tools opt-in path."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

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
