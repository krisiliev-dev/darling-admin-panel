# -*- coding: utf-8 -*-
"""Verify the LOCAL photo path end-to-end against real data: cards emit
/api/brand-registry/photo/{code} URLs, and the endpoint streams real bytes."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("REVIEW_IMAGES_DIR", r"C:\Users\lora_\darling\image-work\originals")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.users import current_active_user  # noqa: E402


class FakeUser:
    email = "dev@darling.bg"
    role = "owner"
    is_active = True


app.dependency_overrides[current_active_user] = lambda: FakeUser()
c = TestClient(app)

d = c.get("/api/brand-registry/prefix/869797500/products?limit=20").json()
local = [i for i in d["items"] if str(i.get("photo") or "").startswith("/api/brand-registry/photo/")]
ext = [i for i in d["items"] if str(i.get("photo") or "").startswith("http")]
print(f"HORECANO drawer: {d['returned']} cards | LOCAL photos {len(local)} | external {len(ext)}")
if local:
    code = local[0]["code"]
    r = c.get(local[0]["photo"])
    print(f"GET photo/{code} -> {r.status_code}, {len(r.content):,} bytes, "
          f"{r.headers.get('content-type')}")
r404 = c.get("/api/brand-registry/photo/NO-SUCH-CODE")
print(f"unknown code -> {r404.status_code}: {r404.json()['detail'][:50]}")
