# -*- coding: utf-8 -*-
"""Office coordinates and radius search (2026-08-06).

Two classes of bug are guarded here, and both have already happened once in this file's
neighbourhood.

The first is the response cap. `?city=София` once returned a single office because the list
was sliced to 300 alphabetically BEFORE the city filter ran. On a map the same ordering
mistake is worse: the row a top-N slice drops is exactly the locker nearest the customer.

The second is Speedy's field naming. Their office address carries `x` and `y`, and `x` is the
LONGITUDE. Reading them in the obvious order puts every Bulgarian office off the coast of
Somalia — a radius search then returns nothing at all while looking like it worked.
"""
import json
from types import SimpleNamespace

import pytest

from app.core import courier as courier_mod
from app.routers import public_couriers as pc

SOFIA = (42.6977, 23.3219)


@pytest.fixture(autouse=True)
def _clear_office_cache():
    pc._OFFICE_CACHE.clear()
    yield
    pc._OFFICE_CACHE.clear()


def _set_token(monkeypatch, token="s3cret-abc"):
    monkeypatch.setattr(pc, "get_settings", lambda: SimpleNamespace(office_lookup_token=token))
    return {"x-office-token": token}


def _office(i, lat, lon, city="София"):
    return {"id": str(i), "name": f"Офис {i}", "address": f"ул. {i}", "city": city,
            "lat": lat, "lon": lon, "kind": "locker"}


# ------------------------------------------------------------------- distance
def test_one_degree_of_latitude_is_about_111_km():
    d = pc._haversine_km(42.0, 25.0, 43.0, 25.0)
    assert 110.5 < d < 111.5, d


def test_distance_to_self_is_zero():
    assert pc._haversine_km(*SOFIA, *SOFIA) == pytest.approx(0.0, abs=1e-9)


def test_svishtov_to_sofia_is_a_plausible_road_apart():
    """Свищов ≈ 43.62 N, 25.35 E. Straight-line to Sofia is ~190 km; if this ever reads as
    ~19 or ~1900 someone has swapped a coordinate or a unit."""
    d = pc._haversine_km(43.6167, 25.3500, *SOFIA)
    assert 150 < d < 230, d


# ----------------------------------------------------------- THE CAP ORDERING
async def test_the_cap_is_applied_after_the_radius_filter(client, monkeypatch):
    """THE regression. 400 offices, and the only five near the customer sit at the END of the
    source list. Cap first and the answer is zero lockers while the shop has five within
    three kilometres; filter first and it is five."""
    far = [_office(i, 43.60 + i * 0.001, 25.35, city="Свищов") for i in range(395)]
    near = [_office(1000 + i, SOFIA[0] + i * 0.005, SOFIA[1]) for i in range(5)]
    monkeypatch.setattr(pc, "_all_offices", lambda k: far + near)
    headers = _set_token(monkeypatch)

    r = await client.get(f"/api/public/couriers/boxnow/offices?lat={SOFIA[0]}&lon={SOFIA[1]}&radius_km=10", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 5
    assert len(body["offices"]) == 5
    assert body["capped"] is False
    assert {o["id"] for o in body["offices"]} == {"1000", "1001", "1002", "1003", "1004"}


async def test_the_cap_still_truncates_and_says_so(client, monkeypatch):
    """Capping is fine — capping SILENTLY is not. A caller has to be able to tell "that is all
    of them" from "here are the first 300"."""
    monkeypatch.setattr(pc, "_all_offices",
                        lambda k: [_office(i, 42.0 + i * 0.0001, 23.0) for i in range(pc._CAP + 40)])
    headers = _set_token(monkeypatch)
    r = await client.get("/api/public/couriers/boxnow/offices", headers=headers)
    body = r.json()
    assert body["count"] == pc._CAP + 40
    assert len(body["offices"]) == pc._CAP
    assert body["capped"] is True


async def test_the_city_filter_still_runs_before_the_cap(client, monkeypatch):
    """The original bug, kept under guard: one Sofia office hidden behind 400 Svishtov ones."""
    rows = [_office(i, 43.6, 25.35, city="Свищов") for i in range(400)]
    rows.append(_office(999, *SOFIA, city="София"))
    monkeypatch.setattr(pc, "_all_offices", lambda k: rows)
    headers = _set_token(monkeypatch)
    r = await client.get("/api/public/couriers/boxnow/offices?city=София", headers=headers)
    body = r.json()
    assert body["count"] == 1
    assert body["offices"][0]["id"] == "999"


# --------------------------------------------------------------- ranking rules
def test_offices_come_back_nearest_first():
    rows = [_office(1, 43.0, 25.0), _office(2, 42.71, 23.33), _office(3, 42.80, 23.40)]
    out, _ = pc._filter_offices(rows, "", *SOFIA)
    assert [o["id"] for o in out] == ["2", "3", "1"]
    assert out[0]["distance_km"] < out[1]["distance_km"] < out[2]["distance_km"]


def test_a_radius_filter_excludes_offices_with_no_coordinates_and_counts_them():
    """We cannot prove an office with no position is within 5 km of anything, so it is out —
    but the count is reported, because a picker that silently loses the only office in town
    is worse than one that says it could not place it."""
    rows = [_office(1, 42.70, 23.33), _office(2, None, None)]
    out, meta = pc._filter_offices(rows, "", *SOFIA, radius_km=20)
    assert [o["id"] for o in out] == ["1"]
    assert meta["no_position"] == 1


def test_without_a_radius_unpositioned_offices_are_still_returned_last():
    """They can be shown, they just cannot be ranked. Dropping them would hide a real office
    from a customer for want of a lat/lon in the courier's own nomenclature."""
    rows = [_office(1, None, None), _office(2, 42.70, 23.33)]
    out, meta = pc._filter_offices(rows, "", *SOFIA)
    assert [o["id"] for o in out] == ["2", "1"]
    assert "distance_km" not in out[1]
    assert meta["no_position"] == 1


def test_city_and_radius_compose():
    rows = [_office(1, 42.70, 23.33, city="София"), _office(2, 42.70, 23.33, city="Пловдив")]
    out, _ = pc._filter_offices(rows, "София", *SOFIA, radius_km=50)
    assert [o["id"] for o in out] == ["1"]


def test_no_geo_query_leaves_the_list_exactly_as_the_courier_gave_it():
    rows = [_office(3, 43.0, 25.0), _office(1, 42.7, 23.3)]
    out, meta = pc._filter_offices(rows)
    assert [o["id"] for o in out] == ["3", "1"]
    assert meta == {}


# ------------------------------------------------------------------ the allow list
async def test_boxnow_is_served(client, monkeypatch):
    """Its adapter has been live since the real test waybill; the comment calling it a stub
    was stale, and the storefront's автомат picker had nothing to talk to."""
    assert "boxnow" in pc._ALLOWED
    monkeypatch.setattr(pc, "_all_offices", lambda k: [_office(1, 42.70, 23.33)])
    headers = _set_token(monkeypatch)
    r = await client.get("/api/public/couriers/boxnow/offices", headers=headers)
    assert r.status_code == 200, r.text


async def test_sameday_is_not_served_while_it_has_no_credentials(client, monkeypatch):
    """Its adapter exists but the keys are not issued, so every lookup would fail upstream.
    A clean 404 beats a 502 where a carrier should be."""
    assert "sameday" not in pc._ALLOWED
    headers = _set_token(monkeypatch)
    r = await client.get("/api/public/couriers/sameday/offices", headers=headers)
    assert r.status_code == 404


# ------------------------------------------------------------------- validation
@pytest.mark.parametrize("qs", ["lat=42.7", "lon=23.3", "radius_km=5"])
async def test_half_a_position_is_refused(client, monkeypatch, qs):
    headers = _set_token(monkeypatch)
    monkeypatch.setattr(pc, "_all_offices", lambda k: [])
    r = await client.get(f"/api/public/couriers/boxnow/offices?{qs}", headers=headers)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("qs", ["lat=91&lon=23", "lat=42&lon=181", "lat=42&lon=23&radius_km=0",
                                "lat=42&lon=23&radius_km=100000"])
async def test_impossible_coordinates_are_refused(client, monkeypatch, qs):
    headers = _set_token(monkeypatch)
    monkeypatch.setattr(pc, "_all_offices", lambda k: [])
    r = await client.get(f"/api/public/couriers/boxnow/offices?{qs}", headers=headers)
    assert r.status_code == 422, r.text


# ==================================================================== adapters
def test_coord_parses_floats_and_strings_and_refuses_junk():
    assert courier_mod._coord(42.5) == 42.5
    assert courier_mod._coord("42.5") == 42.5
    for junk in (None, "", "n/a", [], {}, "1e999", 999.0):
        assert courier_mod._coord(junk) is None, junk


def test_coord_never_invents_zero():
    """0.0 is the Gulf of Guinea, not "unknown". An office placed there is confidently wrong
    and would sort as nearest to nobody."""
    assert courier_mod._coord(None) is None
    assert courier_mod._coord(0) == 0.0        # a real zero passes through; only absence is None


def test_speedy_reads_y_as_latitude_and_x_as_longitude():
    """THE trap. Verified live 2026-08-06: Plovdiv is x=24.76, y=42.12 — so y is the latitude.
    Swapping them lands every office in the Indian Ocean."""
    sp = courier_mod.Speedy.__new__(courier_mod.Speedy)
    sp._post = lambda *a, **k: {"offices": [
        {"id": 1, "name": "ПЛОВДИВ", "type": "OFFICE",
         "address": {"x": 24.761268, "y": 42.120139, "fullAddressString": "ул. X", "siteName": "ПЛОВДИВ"}},
        {"id": 9001, "name": "СОФИЯ - МЕТРО 1 (АВТОМАТ)", "type": "APT",
         "address": {"x": 23.3, "y": 42.7, "fullAddressString": "бул. Y", "siteName": "СОФИЯ"}},
    ]}
    rows = sp.offices()
    assert rows[0]["lat"] == 42.120139 and rows[0]["lon"] == 24.761268
    assert rows[0]["kind"] == "office"
    assert rows[1]["kind"] == "locker"          # APT = автомат
    # A Bulgarian office must land in Bulgaria: 41-44 N, 22-29 E.
    for r in rows:
        assert 41 < r["lat"] < 44 and 22 < r["lon"] < 29


def test_econt_reads_the_nested_location_and_survives_the_one_office_without_it():
    ec = courier_mod.Econt.__new__(courier_mod.Econt)
    ec._post = lambda *a, **k: {"offices": [
        {"code": "7538", "name": "Айдемир", "isAPS": False,
         "address": {"fullAddress": "ул. София №7", "city": {"name": "Айдемир"},
                     "location": {"latitude": 44.05, "longitude": 27.23}}},
        {"code": "9999", "name": "Автомат", "isAPS": True,
         "address": {"fullAddress": "бул. Z", "city": {"name": "София"},
                     "location": {"latitude": 42.7, "longitude": 23.3}}},
        {"code": "8888", "name": "Без координати", "isAPS": False,
         "address": {"fullAddress": "ул. Q", "city": {"name": "Русе"}, "location": None}},
    ]}
    rows = ec.offices()
    assert rows[0]["lat"] == 44.05 and rows[0]["kind"] == "office"
    assert rows[1]["kind"] == "locker"          # isAPS
    assert rows[2]["lat"] is None and rows[2]["lon"] is None


def test_boxnow_reads_string_coordinates_and_is_always_a_locker(monkeypatch):
    payload = json.dumps({"data": [
        {"id": "1840", "lat": "42.64480928803469", "lng": "23.339724129024635",
         "addressLine1": "ул. Проф. Храбър Попов №14", "addressLine2": "София",
         "name": "АМП - New Avenue"},
    ]}).encode()

    class _Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(courier_mod.urllib.request, "urlopen", lambda *a, **k: _Resp())
    bx = courier_mod.BoxNow.__new__(courier_mod.BoxNow)
    rows = bx.offices()
    assert rows[0]["lat"] == pytest.approx(42.6448, abs=1e-3)
    assert rows[0]["lon"] == pytest.approx(23.3397, abs=1e-3)
    assert rows[0]["kind"] == "locker"
    assert rows[0]["city"] == "София"


def test_pigeon_reads_string_coordinates():
    pg = courier_mod.Pigeon.__new__(courier_mod.Pigeon)
    pg._pages = lambda *a, **k: [
        {"id": 90, "name": "Автогарата", "type": "office", "address": "АПРИЛСКО ВЪСТАНИЕ 39",
         "city": {"name": "Разград"}, "latitude": "43.5226221", "longitude": "26.5336124"},
    ]
    rows = pg.offices()
    assert rows[0]["lat"] == pytest.approx(43.5226, abs=1e-3)
    assert rows[0]["kind"] == "office"


def test_every_adapter_answers_in_the_same_shape():
    """One shape or the picker special-cases whichever carrier it is talking to. Sameday used
    to say `lng`; it says `lon` like everyone else now."""
    sp = courier_mod.Speedy.__new__(courier_mod.Speedy)
    sp._post = lambda *a, **k: {"offices": [
        {"id": 1, "name": "n", "type": "APT", "address": {"x": 23.0, "y": 42.0}}]}
    pg = courier_mod.Pigeon.__new__(courier_mod.Pigeon)
    pg._pages = lambda *a, **k: [{"id": 2, "name": "n", "type": "office", "latitude": "42",
                                  "longitude": "23", "address": "a", "city": "c"}]
    sd = courier_mod.Sameday.__new__(courier_mod.Sameday)
    sd._pages = lambda path, *a, **k: (
        [{"lockerId": 3, "name": "n", "city": "c", "address": "a", "postalCode": "1", "lat": "42",
          "lng": "23"}] if "lockers" in path else [])
    required = {"id", "name", "address", "city", "lat", "lon", "kind"}
    for rows in (sp.offices(), pg.offices(), sd.offices()):
        assert required <= set(rows[0]), set(rows[0])
        assert "lng" not in rows[0]
