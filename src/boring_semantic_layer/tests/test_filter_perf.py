"""Regression tests for SemanticFilterOp enrichment.

Covers:
1. Derived-dim filters still resolve correctly (enrichment path not broken).
2. Models with many raw-passthrough dims compile+execute in bounded time
   (skip-identity optimization preventing O(N^2) tree growth).
"""

import time

import ibis
import pandas as pd
import pytest

from boring_semantic_layer import to_semantic_table


@pytest.fixture
def con():
    return ibis.duckdb.connect(":memory:")


def test_filter_on_derived_dim_resolves_through_base(con):
    """A filter that references a derived dim (which depends on another dim)
    must still enrichment-materialize the dependency chain."""
    df = pd.DataFrame({"distance": [100, 500, 1000, 2500, 5000]})
    tbl = con.create_table("dist_flights", df)

    model = (
        to_semantic_table(tbl, "dist_flights")
        .with_dimensions(
            d_one=lambda t: t.distance * 2,
            d_two=lambda t: t.d_one + 1,
        )
        .with_measures(n=lambda t: t.count())
    )

    result = model.filter(lambda t: t.d_two > 1000).group_by().aggregate("n").execute()
    assert result["n"].iloc[0] == 4  # 500,1000,2500,5000 → d_two > 1000


def test_many_dims_filter_compiles_fast(con):
    """200-raw-dim model with a single filter should compile+execute well
    under the pre-patch baseline (~10s+). Bound set at 3s to be resilient
    to CI jitter — the real regression is 100×+ slower."""
    n_rows = 10
    n_dims = 200
    df = pd.DataFrame({f"c{i}": range(n_rows) for i in range(n_dims)})
    tbl = con.create_table("wide", df)

    model = (
        to_semantic_table(tbl, "wide")
        .with_dimensions(**{f"c{i}": (lambda t, name=f"c{i}": t[name]) for i in range(n_dims)})
        .with_measures(n=lambda t: t.count())
    )

    start = time.perf_counter()
    result = model.filter(lambda t: t.c0 >= 0).group_by().aggregate("n").execute()
    elapsed = time.perf_counter() - start

    assert result["n"].iloc[0] == n_rows
    assert elapsed < 3.0, f"compile+execute took {elapsed:.2f}s for {n_dims} dims"


def test_identity_passthrough_lambda_variants(con):
    """Both `lambda t: t.col` and `lambda t, c=col: t[c]` are identity
    passthroughs and must be skipped; a transformed dim in the same model
    still gets enriched."""
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    tbl = con.create_table("mixed", df)

    model = (
        to_semantic_table(tbl, "mixed")
        .with_dimensions(
            a=lambda t: t.a,
            b=lambda t, name="b": t[name],
            b_upper=lambda t: t.b.upper(),
        )
        .with_measures(n=lambda t: t.count())
    )

    result = model.filter(lambda t: t.b_upper == "Y").group_by().aggregate("n").execute()
    assert result["n"].iloc[0] == 1
