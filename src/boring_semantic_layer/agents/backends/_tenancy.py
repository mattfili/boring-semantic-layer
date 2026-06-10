"""Schema-per-tenant provisioning for the MCP backend.

Pure tenancy logic: resolve the tenant schema from a request's access-token
claims, cache per-tenant model mappings, and emit audit events. Kept free of
server wiring so every rule here is unit-testable without the MCP protocol.
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

# Unquoted SQL identifier shape (Postgres/DuckDB). Claims come from tokens we
# verify but do not mint — never interpolate an unvalidated claim near SQL.
_SCHEMA_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_MAX_TENANTS = 32


@dataclass(frozen=True)
class TenancyConfig:
    """Configuration for schema-per-tenant request scoping.

    Attributes:
        schema_claim: Token claim holding the tenant's schema name.
        allowed_schemas: Optional allowlist; when set, claims resolving to a
            schema outside it are rejected even if well-formed.
        max_cached_tenants: LRU size for per-tenant model mappings.
        on_query: Optional audit callback (sync or async) receiving one dict
            per audited tool call: tenant_schema, tool, and tool-specific
            detail such as model/dimensions/measures/rowcount.
            Only successful calls are audited; failed calls raise before the
            audit point.
    """

    schema_claim: str = "schema"
    allowed_schemas: frozenset[str] | None = None
    max_cached_tenants: int = _DEFAULT_MAX_TENANTS
    on_query: Callable[[dict[str, Any]], Any] | None = None


def resolve_tenant_schema(token: Any, config: TenancyConfig) -> str:
    """Resolve and validate the tenant schema from an access token.

    Raises ToolError (surfaced to the MCP caller) when the request carries no
    token, the claim is absent/malformed, or the schema is not allowlisted.
    """
    if token is None:
        raise ToolError(
            "This server is multi-tenant and requires an authenticated HTTP "
            "transport; no access token is present on this request."
        )
    schema = token.claims.get(config.schema_claim)
    if not isinstance(schema, str) or not schema:
        raise ToolError(
            f"Access token is missing the '{config.schema_claim}' claim "
            "required for tenant resolution."
        )
    if not _SCHEMA_IDENT.fullmatch(schema):
        raise ToolError("Tenant schema claim is not a valid schema identifier.")
    if config.allowed_schemas is not None and schema not in config.allowed_schemas:
        raise ToolError("Tenant schema is not among the allowed schemas for this server.")
    return schema


class TenantModelCache:
    """LRU cache of per-tenant model mappings, keyed by schema name.

    The lock guards the OrderedDict only; the factory runs outside it (a slow
    factory must not block other tenants — a rare duplicate build is fine).
    """

    def __init__(self, maxsize: int = _DEFAULT_MAX_TENANTS):
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._cache: OrderedDict[str, Mapping[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(
        self,
        schema: str,
        factory: Callable[[str], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Return cached models for ``schema``, building via ``factory`` on miss."""
        with self._lock:
            if schema in self._cache:
                self._cache.move_to_end(schema)
                return self._cache[schema]
        models = factory(schema)
        with self._lock:
            self._cache[schema] = models
            self._cache.move_to_end(schema)
            while len(self._cache) > self._maxsize:
                evicted, _ = self._cache.popitem(last=False)
                logger.info("tenancy: evicted cached models for schema %s", evicted)
        return models


async def emit_audit(config: TenancyConfig, event: dict[str, Any]) -> None:
    """Deliver one audit event to the configured callback, if any.

    Audit failures are logged, never raised — a broken sink must not take the
    query path down. (Logged loudly so the drop is visible, not silent.)
    """
    if config.on_query is None:
        return
    try:
        result = config.on_query(event)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.warning("tenancy: audit callback failed for event %r", event, exc_info=True)
