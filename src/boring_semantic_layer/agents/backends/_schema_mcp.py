"""MCP tool registrations for the schema-tools opt-in path.

Three tools — :func:`register_schema_tools` wires all of them onto an
``MCPSemanticModel`` instance:

- ``infer_schema`` — propose a BSL model from a raw source.
- ``connect_source`` — test a backend connection and list tables.
- ``list_backends`` — enumerate supported / installed ibis backends.

All three are read-only, idempotent, and never write to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mcp import MCPSemanticModel


def register_schema_tools(server: MCPSemanticModel, prompts_dir: Path) -> None:
    """Register `infer_schema`, `connect_source`, and `list_backends` on ``server``.

    Tools are added in subsequent tasks (10–16). This stub establishes the
    registration entry point.
    """
    # Implementations land in Tasks 11–16.
    pass
