# -*- coding: utf-8 -*-
"""The sellable-online image rule, on the CONNECTOR side.

The panel already refuses to put a product online without a picture (catalog.update_article
and catalog.mass_op). The pipeline that will push the 22.8k catalogue did not: build() set
is_public=1 on CREATE and the pictures were uploaded afterwards, so a product whose processed
cutout never landed went online blank. One rule, two surfaces - this is the second one.

The test that matters most is the RERUN one. push_images skips filenames already on the
article, so a correct second run uploads zero. Deciding "has a picture" from the upload
counter would leave every already-imaged product a permanent draft, which is why the decision
reads what Barsy holds instead.
"""
import sys
import types

import pytest


@pytest.fixture()
def bp(monkeypatch):
    """barsy_push with its Barsy client and its record source replaced by fakes.

    Nothing here touches the network or the SQLite system-of-record; barsy_collect is stubbed
    into sys.modules because build() imports it lazily and it would otherwise read real data.
    """
    import barsy_push as mod

    fake_bcol = types.ModuleType("barsy_collect")
    fake_bcol._export_price_qty = lambda: {}
    monkeypatch.setitem(sys.modules, "barsy_collect", fake_bcol)
    monkeypatch.setattr(mod, "_fields", lambda code: ({}, {}))
    monkeypatch.setattr(mod, "_cat_id_for", lambda meta, ensure=False: None)
    monkeypatch.setattr(mod, "_unit_id", lambda fields, existing: 1)
    return mod


class FakeBarsy:
    """Records every call. `files` is what Files_getlist reports on the article."""

    class BarsyError(Exception):
        pass

    def __init__(self, files=None, files_raises=False, upload_raises=False):
        self.files = list(files or [])
        self.files_raises = files_raises
        self.upload_raises = upload_raises
        self.calls = []
        self.uploads = []

    def call(self, method, body=None):
        self.calls.append((method, body))
        if method == "Files_getlist":
            if self.files_raises:
                raise RuntimeError("barsy timed out")
            return list(self.files)
        if method == "Articles_create":
            return {"article_id": 4242}
        return {"ok": True}

    def upload_image(self, article_id, data, filename):
        if self.upload_raises:
            raise RuntimeError("413 too large")
        self.uploads.append(filename)
        self.files.append({"name": filename})

    def updates(self):
        return [b for m, b in self.calls if m == "Articles_update"]


@pytest.fixture()
def cutout(tmp_path, monkeypatch):
    """One processed cutout on disk, as image-pipeline/processed/<code>.png would be."""
    def _make(bp_mod, code="210038"):
        p = tmp_path / f"{code}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr(bp_mod, "_processed_images", lambda c, max_images=6: [p])
        return p
    return _make


# ---------------------------------------------------------------- build(): create is offline
def test_a_created_article_is_not_public(bp):
    mode, body = bp.build("210038", {}, {})
    assert mode == "create"
    assert body["article"]["is_public"] == 0


def test_a_created_article_is_still_for_sale(bp):
    """Sellable in the shop and over the phone is a different question from listed online."""
    _, body = bp.build("210038", {}, {})
    assert body["article"]["is_for_sale"] == 1


def test_an_update_never_touches_the_online_state(bp):
    """A pipeline run must not quietly take a live listing down - or put one up."""
    mode, body = bp.build("210038", {"210038": {"article_id": 7}}, {})
    assert mode == "update"
    assert "is_public" not in body["article"]
    assert "is_for_sale" not in body["article"]


# --------------------------------------------------------- publish_if_imaged(): the decision
def test_publishes_once_a_picture_is_on_the_article(bp, cutout, monkeypatch):
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    published, reason = bp.publish_if_imaged("210038", 4242)
    assert (published, reason) == (True, "")
    assert fake.updates() == [{"article_id": 4242, "article": {"is_public": 1}}]


def test_the_update_puts_the_record_id_at_the_top_level(bp, cutout, monkeypatch):
    """Barsy contract: article_id at TOP level on updates, not inside `article`."""
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    bp.publish_if_imaged("210038", 4242)
    body = fake.updates()[0]
    assert body["article_id"] == 4242
    assert "article_id" not in body["article"]


def test_a_rerun_that_uploads_nothing_still_publishes(bp, tmp_path, monkeypatch):
    """THE one. push_images skips filenames already on the article, so a correct second run
    uploads zero. That is not the same fact as "there is no picture"."""
    p = tmp_path / "210038.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(bp, "_processed_images", lambda c, max_images=6: [p])
    fake = FakeBarsy(files=[{"name": "210038.png"}])          # already there from run one
    monkeypatch.setattr(bp, "b", fake)
    published, reason = bp.publish_if_imaged("210038", 4242)
    assert fake.uploads == []                                  # nothing re-uploaded
    assert (published, reason) == (True, "")


def test_no_processed_cutout_means_it_stays_a_draft(bp, monkeypatch):
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    monkeypatch.setattr(bp, "_processed_images", lambda c, max_images=6: [])
    published, reason = bp.publish_if_imaged("210038", 4242)
    assert published is False
    assert reason == "no_processed_image"
    assert fake.updates() == []


def test_a_failed_upload_leaves_it_a_draft(bp, cutout, monkeypatch):
    fake = FakeBarsy(upload_raises=True)
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    published, reason = bp.publish_if_imaged("210038", 4242)
    assert published is False
    assert "413" in reason
    assert fake.updates() == []


def test_an_unanswerable_picture_check_fails_closed(bp, cutout, monkeypatch):
    """Barsy not answering is not evidence of a picture. Publishing on a timeout is exactly
    how a blank product reaches the storefront."""
    fake = FakeBarsy(files_raises=True)
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    published, reason = bp.publish_if_imaged("210038", 4242)
    assert published is False
    assert reason.startswith("picture_check_failed")
    assert fake.updates() == []


def test_an_empty_file_list_is_not_a_picture(bp, cutout, monkeypatch):
    """Barsy answered and holds nothing - distinct from not answering, same refusal."""
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    monkeypatch.setattr(bp, "push_images", lambda c, aid, max_images=6: (0, None))
    published, reason = bp.publish_if_imaged("210038", 4242)
    assert published is False
    assert fake.updates() == []


# ------------------------------------------------------------------- push_one(): end to end
def test_push_one_creates_offline_then_publishes(bp, cutout, monkeypatch):
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    mode, aid = bp.push_one("210038", catalog={}, bc={})
    assert (mode, aid) == ("create", 4242)
    create = next(b for m, b in fake.calls if m == "Articles_create")
    assert create["article"]["is_public"] == 0                 # offline at creation
    assert fake.updates() == [{"article_id": 4242, "article": {"is_public": 1}}]


def test_push_one_without_images_leaves_a_draft(bp, cutout, monkeypatch):
    """No pictures were attempted, so nothing proves there is one. It must not go online."""
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    bp.push_one("210038", catalog={}, bc={}, images=False)
    assert fake.updates() == []


def test_push_one_on_an_existing_product_does_not_change_its_online_state(bp, cutout, monkeypatch):
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    cutout(bp)
    mode, aid = bp.push_one("210038", catalog={"210038": {"article_id": 7}}, bc={})
    assert mode == "update"
    for body in fake.updates():
        assert "is_public" not in body.get("article", {})


def test_push_one_with_no_cutout_never_publishes(bp, monkeypatch):
    fake = FakeBarsy()
    monkeypatch.setattr(bp, "b", fake)
    monkeypatch.setattr(bp, "_processed_images", lambda c, max_images=6: [])
    bp.push_one("210038", catalog={}, bc={})
    assert fake.updates() == []
