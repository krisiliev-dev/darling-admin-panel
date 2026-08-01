# -*- coding: utf-8 -*-
"""Local end-to-end verification of the review-card endpoints against REAL data —
the deployed code path, minus HTTP auth (dependency overridden), zero deploys burned.
Run from admin-panel/backend."""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.users import current_active_user  # noqa: E402


class FakeUser:
    id = 0
    email = "dev-verify@local"
    role = "owner"
    is_active = True
    is_superuser = True
    is_sysadmin = True


app.dependency_overrides[current_active_user] = lambda: FakeUser()
client = TestClient(app)

for pref, label in (("869747179", "Netlon 37/37 covered"),
                    ("869797500", "HORECANO 11/105 covered"),
                    ("885022221", "OCEAN 38/38 covered")):
    r = client.get(f"/api/brand-registry/prefix/{pref}/products?limit=200")
    print(f"=== {label} -> HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:300])
        continue
    d = r.json()
    items = d["items"]
    named = sum(1 for i in items if i["name"])
    photod = sum(1 for i in items if i["photo"])
    print(f"    total={d['total']} returned={d['returned']} named={named} "
          f"with_photo={photod} catalog_available={d.get('catalog_available')}")
    for i in items[:3]:
        print(f"    first cards: {i['code']} | {(i['name'] or '-')[:40]} | photo={bool(i['photo'])}")
