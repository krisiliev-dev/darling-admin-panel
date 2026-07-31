# -*- coding: utf-8 -*-
"""The deploy-gap detector (#98). A check that only ever returns "clean" is worthless, so these tests
prove it FIRES — on real drift, and on its own failure."""
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
