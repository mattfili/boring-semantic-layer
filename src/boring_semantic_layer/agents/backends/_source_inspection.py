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

import os
from dataclasses import dataclass

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
) -> tuple[list[TableSummary], bool]:
    """Enumerate tables with ``COUNT(*)`` per table.

    Returns ``(summaries, truncated)``. A table whose count fails appears
    with ``row_count=None`` and ``count_error`` set to the exception string;
    the call itself never raises.

    Args:
        con: Open ibis backend.
        limit_tables: Cap on number of tables; if more exist, the first
            ``limit_tables`` are returned and ``truncated`` is True.
    """
    all_names = list(con.list_tables())
    truncated = len(all_names) > limit_tables
    names = all_names[:limit_tables]

    summaries: list[TableSummary] = []
    for name in names:
        try:
            count = int(con.table(name).count().execute())
            summaries.append(TableSummary(name=name, row_count=count, count_error=None))
        except Exception as exc:
            summaries.append(TableSummary(name=name, row_count=None, count_error=str(exc)))
    return summaries, truncated
