# -*- coding: utf-8 -*-
"""The deploy-gap detector (#98). A check that only ever returns "clean" is worthless, so these tests
prove it FIRES — on real drift, and on its own failure."""
import pathlib

from app import core_drift


def test_clean_when_the_loaded_module_matches_the_repo():
    assert core_drift.core_drift() == []


def test_detects_a_module_loaded_from_a_different_file(tmp_path, monkeypatch):
    """THE regression. On the server the panel loaded agent-pipeline/courier.py while the deploy only
    updated _vendor/courier.py, so a fix passed CI, deployed green, reported healthy — and did not
    run. Simulated by pointing the loaded module at a file whose contents differ."""
    fake = tmp_path / "courier.py"
    fake.write_text("# a stale copy the deploy never updated\n", encoding="utf-8")
    from app.core import courier
    monkeypatch.setattr(courier, "__file__", str(fake))
    drift = core_drift.core_drift()
    assert [d["module"] for d in drift] == ["courier"]
    assert drift[0]["loaded_sha"] != drift[0]["repo_sha"]
    assert str(fake) in drift[0]["loaded_from"]      # names the actual file, so the fix is obvious


def test_a_failing_check_reports_failure_not_cleanliness(monkeypatch):
    """A monitor that returns "no problems" when it errored is the exact bug it exists to catch — the
    first version of this function did that, silently returning [] on an import error."""
    def _boom(*a, **k):
        raise RuntimeError("core import exploded")
    monkeypatch.setattr(core_drift, "_sha", _boom)
    from app.core import courier
    monkeypatch.setattr(courier, "__file__", "/definitely/not/the/repo/courier.py")
    out = core_drift.core_drift()
    assert out and out[0]["module"] == "(check failed)"
    assert out != []


def test_health_exposes_drift():
    from app.main import health
    assert "core_drift" in health()


def test_line_endings_alone_are_not_drift(tmp_path, monkeypatch):
    """A Windows-authored copy differs from an LF copy on every byte-line while being the SAME CODE.
    Reporting that as drift is a false positive that cost a real write to shared money-path modules
    (2026-07-31). A detector that cries wolf trains people to ignore the alert that matters."""
    from app.core import courier
    real = pathlib.Path(courier.__file__)
    crlf = tmp_path / "courier.py"
    crlf.write_bytes(real.read_bytes().replace(b"\n", b"\r\n"))
    monkeypatch.setattr(courier, "__file__", str(crlf))
    assert core_drift.core_drift() == []          # same content, different line endings → NOT drift


def test_real_content_change_still_detected_under_crlf(tmp_path, monkeypatch):
    """…and normalising must not blind it: a genuine edge delivered with CRLF is still drift."""
    from app.core import courier
    real = pathlib.Path(courier.__file__)
    changed = tmp_path / "courier.py"
    changed.write_bytes(real.read_bytes().replace(b"\n", b"\r\n") + b"\r\nX = 1\r\n")
    monkeypatch.setattr(courier, "__file__", str(changed))
    assert [d["module"] for d in core_drift.core_drift()] == ["courier"]


# ---------------------------------------------------------------- #98: imports come from the repo ---
def test_shared_modules_are_imported_from_this_repo_not_the_sibling():
    """THE #98 fix. Imports must resolve to `_vendor`, which ships in this repo and therefore cannot be
    stale relative to what CI tested. They used to resolve to ../agent-pipeline, which nothing deploys
    — no cron, no systemd, a local .git with no remote — so it was updated by two people remembering to
    scp. A fix to courier.py then passed CI, deployed green, reported healthy, and did not run;
    production under-charged the Speedy COD surcharge for an unknown period as a result."""
    from app.core import barsy, connector, courier, db, shopify_orders
    vendor = pathlib.Path(__file__).resolve().parents[2] / "darling_core" / "_vendor"
    for mod in (courier, db, barsy, connector, shopify_orders):
        assert pathlib.Path(mod.__file__).resolve().parent == vendor.resolve(), \
            f"{mod.__name__} loaded from {mod.__file__} — not this repo's copy"


def test_data_root_still_points_at_the_sibling_when_present():
    """…and the split must not drag the DATA with it: pipeline.db genuinely lives in agent-pipeline and
    is not ours to relocate. Forcing _vendor wholesale would have broken every catalogue read, which is
    why this was a real change rather than a one-line path flip."""
    import os

    import darling_core
    sibling = pathlib.Path(darling_core.__file__).resolve().parents[2] / "agent-pipeline"
    if sibling.is_dir():
        assert pathlib.Path(darling_core.CORE_ROOT).resolve() == sibling.resolve()
    else:                                   # CI has no sibling — data root falls back to _vendor
        assert os.path.isdir(darling_core.CORE_ROOT)
