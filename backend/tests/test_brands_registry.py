# -*- coding: utf-8 -*-
"""Brand registry read layer (#99). The registry is machine-derived and read-only here; human
corrections live separately so a regeneration can never discard them."""
import json

import pytest

from app import brands_registry as br


def test_loads_the_shipped_snapshot_and_counts_agree():
    s = br.summary()
    assert s["schema_version"] == br.SCHEMA_VERSION
    assert s["brands"] > 0 and s["prefixes"] > 0
    assert s["asserting_prefixes"] < s["prefixes"]      # most prefixes do NOT assert


def test_a_future_schema_is_REFUSED_not_best_effort(tmp_path):
    """A newer schema misread would produce confident wrong brands across the catalogue, and every
    symptom would look like a data fault rather than a version mismatch. Refuse instead."""
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema_version": 99, "brands": {}, "prefixes": {}}), encoding="utf-8")
    with pytest.raises(br.RegistrySchemaError) as e:
        br.load(p)
    assert "99" in str(e.value)


def test_importing_the_same_snapshot_twice_changes_nothing():
    """Hermes's idempotency rule, applied before the first import rather than after the first surprise:
    their emit_legacy.py wrote derived rows back into its own source, reported '0 changes', looked
    healthy, and nulled 431 records. A second read must be identical to the first."""
    a = br.summary()
    b = br.summary(br.load(force=True))
    assert a == b


def test_asserts_is_the_only_gate_suggestions_never_count_as_branding():
    """A prefix may carry a brand_id WITHOUT asserting — a suggestion awaiting review, not something
    to print. Flattening the two would auto-label products from unreviewed guesses."""
    reg = br.load()
    pf = reg["prefixes"]
    suggesting = [v for v in pf.values() if v.get("brand_id") and not v.get("asserts")]
    assert suggesting, "fixture should contain suggestions"
    queue_prefixes = {r["prefix"] for r in br.review_queue(limit=10_000)}
    for p, v in pf.items():
        if v.get("brand_id") and not v.get("asserts"):
            assert p in queue_prefixes            # every suggestion is reviewable
        if v.get("asserts"):
            assert p not in queue_prefixes        # nothing already asserting sits in the queue


def test_headline_is_not_the_naive_sum_of_per_prefix_counts():
    """698 products carry barcodes under more than one prefix and are counted under each, so summing
    the column overstates the catalogue. The headline counts only ASSERTING prefixes, and must stay
    below the naive total — Hermes printed the wrong figure while building this and had to reconcile."""
    reg = br.load()
    naive = sum(v.get("product_count") or 0 for v in reg["prefixes"].values())
    assert br.summary()["products_under_asserting_prefixes"] < naive


def test_brands_carry_blast_radius():
    """An edit UI that says 'turning this off will unbrand N products' before the click is a different
    decision from the same click with no number attached."""
    rows = br.brands()
    assert rows and all("product_count" in r for r in rows)
    assert rows == sorted(rows, key=lambda r: (-r["product_count"], r["canonical"].lower()))


def test_review_queue_is_ordered_by_what_rides_on_the_decision():
    q = br.review_queue(limit=50)
    assert q and [r["product_count"] for r in q] == sorted(
        (r["product_count"] for r in q), reverse=True)
    assert all(r.get("reason") is not None or r.get("src") for r in q)   # never an unexplained guess
