# -*- coding: utf-8 -*-
"""Order → Shipment: weight summed from our DB (× qty), declared value = order total, and the
Дарлинг always-fragile/преглед policy."""
from app.routers import orders as orders_mod


def test_product_weight_barsy_first_then_pipeline(monkeypatch):
    # Barsy (source of truth) wins when it has the weight...
    monkeypatch.setattr(orders_mod, "_barsy_weight", lambda c: 12.5 if c == "HAS" else 0.0)
    monkeypatch.setattr(orders_mod, "_pipeline_weight", lambda c: 1.8)
    assert orders_mod._product_weight("HAS") == 12.5
    # ...else fall back to the pipeline record
    assert orders_mod._product_weight("NOBARSY") == 1.8
    assert orders_mod._product_weight("") == 0.0


def test_order_weight_sums_by_qty(monkeypatch):
    weights = {"A": 1.5, "B": 0.4}
    monkeypatch.setattr(orders_mod, "_product_weight", lambda code: weights.get(str(code), 0.0))
    o = {"items": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 3}, {"sku": "?", "qty": 1}]}
    assert orders_mod._order_weight(o) == 4.2   # 1.5*2 + 0.4*3 + 0


def test_shipment_from_order_applies_policy(monkeypatch):
    monkeypatch.setattr(orders_mod, "_order_weight", lambda o: 2.5)

    class Req:  # minimal ShipRequest stand-in
        office_id = None
        weight_kg = 1.0
        fragile = True
        inspection = True
    o = {"name": "#1001", "customer": "Иван", "phone": "0888", "city": "София",
         "zip": "1000", "address": "ул. Х 1", "cod_amount": 40.0, "total": 55.0}
    shp = orders_mod._shipment_from_order(o, Req())
    assert shp.weight_kg == 2.5              # from our DB, not the 1.0 form default
    assert shp.declared_value == 55.0        # обявена стойност = order total
    assert shp.fragile is True and shp.inspection is True  # Дарлинг default (packer can override)


def test_shipment_fragile_inspection_overridable(monkeypatch):
    monkeypatch.setattr(orders_mod, "_order_weight", lambda o: 1.0)

    class Req:
        office_id = None
        weight_kg = 1.0
        fragile = False       # packer unchecked them for this order
        inspection = False
    shp = orders_mod._shipment_from_order({"name": "#3", "customer": "x", "phone": "1", "cod_amount": 0, "total": 10}, Req())
    assert shp.fragile is False and shp.inspection is False


def test_order_weight_falls_back_to_form(monkeypatch):
    # no known product weights → _shipment_from_order uses the form weight
    monkeypatch.setattr(orders_mod, "_order_weight", lambda o: 0.0)

    class Req:
        office_id = None
        weight_kg = 3.0
        fragile = True
        inspection = True
    shp = orders_mod._shipment_from_order({"name": "#2", "customer": "x", "phone": "1", "cod_amount": 0}, Req())
    assert shp.weight_kg == 3.0
