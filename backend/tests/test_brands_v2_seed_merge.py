# -*- coding: utf-8 -*-
"""The seed->live contract for the v2 brand registry.

These assert the RESULT on disk after a load, not that a helper was called: what the panel would
actually serve. The bug being locked down: the seed used to be copied once, so after the
2026-08-03 registry review 28 pipeline-fixed brands never reached the panel and nothing said so.
"""
import importlib
import json

import pytest


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    """A brands_v2 module pointed at a throwaway seed + live pair."""
    import app.brands_v2 as m
    importlib.reload(m)
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    conf = tmp_path / "confirmed.json"
    conf.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "_SEED", seed)
    monkeypatch.setattr(m, "_LIVE", live)
    monkeypatch.setattr(m, "_CONF", conf)

    def write_seed(brands: dict):
        seed.write_text(json.dumps({"schema_version": 2, "brands": brands}, ensure_ascii=False),
                        encoding="utf-8")

    def read_live() -> dict:
        return json.loads(live.read_text(encoding="utf-8"))

    m.write_seed = write_seed          # type: ignore[attr-defined]
    m.read_live = read_live            # type: ignore[attr-defined]
    return m


def _b(canonical, **kw):
    base = {"canonical": canonical, "aliases": [], "prefixes": [], "series": [],
            "gs1_owner": None, "logo": None}
    base.update(kw)
    return base


def test_first_load_fills_an_empty_live_registry_from_the_seed(reg):
    reg.write_seed({"lenox": _b("Lenox"), "luminarc": _b("Luminarc")})
    assert {r["id"] for r in reg.brands()} == {"lenox", "luminarc"}


def test_a_brand_added_to_the_seed_later_reaches_the_panel(reg):
    """The actual 2026-08-03 failure: live was written from an older seed and then frozen."""
    reg.write_seed({"lenox": _b("Lenox")})
    assert {r["id"] for r in reg.brands()} == {"lenox"}

    reg.write_seed({"lenox": _b("Lenox"), "bursev": _b("Bursev")})
    assert {r["id"] for r in reg.brands()} == {"lenox", "bursev"}


def test_a_machine_owned_brand_follows_the_pipeline(reg):
    reg.write_seed({"lenox": _b("Lenox", prefixes=["380000000"])})
    reg.brands()
    reg.write_seed({"lenox": _b("Lenox", prefixes=["380000000", "380000001"])})
    assert reg.brand("lenox")["prefixes"] == ["380000000", "380000001"]


def test_an_owner_edit_survives_every_later_seed(reg):
    reg.write_seed({"lenox": _b("Lenox", aliases=["lennox"])})
    reg.brands()
    reg.edit("lenox", canonical="Lenox BG", add={"aliases": ["ленокс"]})

    reg.write_seed({"lenox": _b("Lenox", aliases=["something-else"])})
    row = reg.brand("lenox")
    assert row["canonical"] == "Lenox BG"
    assert "ленокс" in row["aliases"]
    assert "something-else" not in row["aliases"]
    assert row["edited"] is True


def test_a_deleted_brand_is_not_resurrected_by_a_seed_refresh(reg):
    reg.write_seed({"lenox": _b("Lenox"), "bursev": _b("Bursev")})
    reg.brands()
    reg.delete("bursev")
    assert {r["id"] for r in reg.brands()} == {"lenox"}

    # a new seed still lists it — the tombstone must hold
    reg.write_seed({"lenox": _b("Lenox"), "bursev": _b("Bursev", prefixes=["380000002"])})
    assert {r["id"] for r in reg.brands()} == {"lenox"}
    assert reg.read_live()["deleted"] == ["bursev"]


def test_recreating_a_deleted_brand_lifts_the_tombstone(reg):
    reg.write_seed({"bursev": _b("Bursev")})
    reg.brands()
    reg.delete("bursev")
    reg.create("bursev", "Bursev")
    assert reg.brand("bursev") is not None
    assert reg.read_live().get("deleted") == []


def test_a_panel_created_brand_is_never_touched_by_the_seed(reg):
    reg.write_seed({"lenox": _b("Lenox")})
    reg.brands()
    reg.create("darling", "Darling")
    reg.write_seed({"lenox": _b("Lenox"), "extra": _b("Extra")})
    ids = {r["id"] for r in reg.brands()}
    assert ids == {"lenox", "darling", "extra"}
    assert reg.brand("darling")["canonical"] == "Darling"


def test_a_pre_flag_live_file_that_differs_from_the_seed_is_frozen_not_overwritten(reg):
    """Migration case: a live registry written before `edited` existed carries no ownership
    information, so a difference is assumed to be a human's and is preserved."""
    reg._LIVE.write_text(json.dumps({
        "schema_version": 2,
        "brands": {"lenox": _b("Lenox ръчно редактиран", aliases=["ленокс"])},
    }, ensure_ascii=False), encoding="utf-8")
    reg.write_seed({"lenox": _b("Lenox"), "new": _b("New")})

    rows = {r["id"]: r for r in reg.brands()}
    assert rows["lenox"]["canonical"] == "Lenox ръчно редактиран"   # kept
    assert rows["lenox"]["edited"] is True                          # and frozen from now on
    assert "new" in rows                                            # new seed brands still arrive


def test_a_pre_flag_live_brand_identical_to_the_seed_stays_machine_owned(reg):
    reg._LIVE.write_text(json.dumps({"schema_version": 2, "brands": {"lenox": _b("Lenox")}},
                                    ensure_ascii=False), encoding="utf-8")
    reg.write_seed({"lenox": _b("Lenox")})
    reg.brands()
    assert reg.brand("lenox")["edited"] is False

    reg.write_seed({"lenox": _b("Lenox", prefixes=["380000009"])})
    assert reg.brand("lenox")["prefixes"] == ["380000009"]


def test_an_unreadable_seed_leaves_the_live_registry_untouched(reg):
    reg.write_seed({"lenox": _b("Lenox")})
    reg.brands()
    reg._SEED.write_text("{ this is not json", encoding="utf-8")
    assert {r["id"] for r in reg.brands()} == {"lenox"}


def test_a_missing_seed_leaves_the_live_registry_untouched(reg):
    reg.write_seed({"lenox": _b("Lenox")})
    reg.brands()
    reg._SEED.unlink()
    assert {r["id"] for r in reg.brands()} == {"lenox"}


def test_an_unchanged_seed_does_not_rewrite_the_live_file(reg):
    reg.write_seed({"lenox": _b("Lenox")})
    reg.brands()
    before = reg._LIVE.read_bytes()
    reg.brands()
    reg.brands()
    assert reg._LIVE.read_bytes() == before


def test_summary_reports_how_many_brands_the_owner_owns(reg):
    reg.write_seed({"a": _b("A"), "b": _b("B"), "c": _b("C")})
    reg.brands()
    reg.edit("a", canonical="A!")
    reg.delete("c")
    s = reg.summary()
    assert s["brands"] == 2
    assert s["owner_edited"] == 1
    assert s["deleted"] == 1
