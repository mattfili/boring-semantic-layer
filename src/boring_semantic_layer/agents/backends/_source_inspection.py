"""Connection-test helpers for the schema-tools MCP path.

Splits the responsibilities of the MCP ``connect_source`` and ``infer_schema``
tools that touch live backends:

- :func:`open_backend` — opens an ibis backend from a profile dict, trying
  xorq's Profile first (matches BSL's ``profile.py`` loader) then falling back
  to plain ``ibis.<backend>.connect()``.
- :func:`list_tables_with_counts` — enumerates tables with COUNT(*) per table,
  with per-table timeout and a global cap.
- :func:`build_profile_yaml` — renders BSL profile YAML preserving ``${VAR}`` literals.
- :func:`open_transient_duckdb_for_file` — opens an in-memory DuckDB and reads
  a single file as a table for the file-source path of ``infer_schema``.
- :func:`_sanitize_error` — scrubs credentials from error messages before
  surfacing them to the MCP client.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import ibis
from ibis import BaseBackend


def open_backend(profile_config: dict) -> BaseBackend:
    """Open an ibis backend from a flat profile config dict.

    Tries xorq's Profile first (handles env var substitution automatically);
    falls back to plain ``ibis.<backend>.connect(**params)`` for backends not
    registered in xorq.

    Mirrors :func:`boring_semantic_layer.profile._create_connection_from_config`.
    """
    if "type" not in profile_config:
        raise ValueError("Profile config must include 'type' field")

    config = dict(profile_config)
    conn_type = config.pop("type")

    # Try xorq first
    try:
        from xorq.vendor.ibis.backends.profiles import Profile as XorqProfile

        kwargs_tuple = tuple(sorted(config.items()))
        xorq_profile = XorqProfile(con_name=conn_type, kwargs_tuple=kwargs_tuple)
        return xorq_profile.get_con()
    except AssertionError:
        # xorq doesn't know this backend — fall through to plain ibis
        pass
    except Exception:
        # xorq import or profile construction failed — fall through
        pass

    connect_fn = getattr(ibis, conn_type, None)
    if connect_fn is None or not callable(getattr(connect_fn, "connect", None)):
        raise ValueError(f"Unknown backend type: '{conn_type}'")
    expanded = {k: os.path.expandvars(v) if isinstance(v, str) else v for k, v in config.items()}
    return connect_fn.connect(**expanded)


@dataclass(frozen=True)
class TableSummary:
    name: str
    row_count: int | None
    count_error: str | None


def list_tables_with_counts(
    con: BaseBackend,
    *,
    limit_tables: int = 100,
    timeout_seconds: float = 5.0,
) -> tuple[list[TableSummary], bool]:
    """Enumerate tables with ``COUNT(*)`` per table.

    Returns ``(summaries, truncated)``. A table whose count fails or times
    out appears with ``row_count=None`` and ``count_error`` set; the call
    itself never raises.

    Args:
        con: Open ibis backend.
        limit_tables: Cap on number of tables; if more exist, the first
            ``limit_tables`` are returned and ``truncated`` is True.
        timeout_seconds: Per-table timeout for COUNT(*). Tables that exceed
            this show up with ``count_error="timed out after Xs"``.
    """
    all_names = list(con.list_tables())
    truncated = len(all_names) > limit_tables
    names = all_names[:limit_tables]

    summaries: list[TableSummary] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        for name in names:
            try:
                future = executor.submit(_count_table, con, name)
                count = future.result(timeout=timeout_seconds)
                summaries.append(TableSummary(name=name, row_count=count, count_error=None))
            except concurrent.futures.TimeoutError:
                summaries.append(
                    TableSummary(
                        name=name,
                        row_count=None,
                        count_error=f"timed out after {timeout_seconds}s",
                    )
                )
            except Exception as exc:
                summaries.append(TableSummary(name=name, row_count=None, count_error=str(exc)))
    return summaries, truncated


def _count_table(con: BaseBackend, name: str) -> int:
    return int(con.table(name).count().execute())


def build_profile_yaml(profile_name: str, backend: str, params: dict) -> str:
    """Render a BSL profile YAML block. Preserves ``${VAR}`` literals.

    Output shape (matches BSL's existing profile loader):

    ```yaml
    profile_name:
      type: <backend>
      <param>: <value>
      ...
    ```
    """
    lines = [f"{profile_name}:"]
    lines.append(f"  type: {backend}")
    for key, value in params.items():
        lines.append(f"  {key}: {_yaml_value(value)}")
    return "\n".join(lines) + "\n"


def _yaml_value(v) -> str:
    """Render a YAML scalar preserving ${VAR} literals (not expanded)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if "${" in s or any(ch in s for ch in ":#'\"\\"):
        # Quote anything that could confuse YAML; escape backslash and double-quote
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def open_transient_duckdb_for_file(
    path: str,
    source_type: Literal["csv", "parquet", "json"],
) -> tuple[BaseBackend, ibis.Table]:
    """Open an in-memory DuckDB and read ``path`` as table ``_inferred``.

    The caller is responsible for closing the connection (typically in a
    ``try/finally``). The table name ``_inferred`` is fixed to keep the
    inference contract simple.

    Raises:
        ValueError: If ``source_type`` isn't one of csv/parquet/json or the
            path can't be read.
    """
    if source_type not in {"csv", "parquet", "json"}:
        raise ValueError(f"Unsupported source_type '{source_type}'. Supported: csv, parquet, json")
    if not Path(path).exists():
        raise ValueError(f"File not found: {path}")

    con = ibis.duckdb.connect(":memory:")
    try:
        if source_type == "parquet":
            tbl = con.read_parquet(path, table_name="_inferred")
        elif source_type == "csv":
            tbl = con.read_csv(path, table_name="_inferred")
        else:  # json
            tbl = con.read_json(path, table_name="_inferred")
        return con, tbl
    except Exception:
        # Best-effort cleanup if read fails
        with contextlib.suppress(Exception):
            con.disconnect()
        raise


_PASSWORD_PATTERN = re.compile(r"(password|secret|token|api_key|api-key)=\S+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_URL_CREDS_PATTERN = re.compile(r"(\w+)://([^:/\s]+):([^@/\s]+)@")


def _sanitize_error(msg: str, params: dict | None = None) -> str:
    """Scrub credentials from an error message before surfacing to MCP clients.

    Strips:
      - Any value present verbatim in ``params``.
      - ``password=...``, ``secret=...``, ``token=...``, ``api_key=...`` patterns.
      - ``Bearer <token>`` patterns.
      - URL-shaped credentials (``proto://user:pass@host`` → ``proto://***@host``).
    """
    out = str(msg)
    if params:
        for value in params.values():
            if isinstance(value, str) and value:
                out = out.replace(value, "***")
    out = _PASSWORD_PATTERN.sub("***", out)
    out = _BEARER_PATTERN.sub("Bearer ***", out)
    out = _URL_CREDS_PATTERN.sub(r"\1://***@", out)
    return out
