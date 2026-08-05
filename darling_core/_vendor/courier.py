# -*- coding: utf-8 -*-
"""Courier-agnostic shipping layer for the ops console / admin panel (owner 2026-07-19).

A single interface over the Bulgarian couriers so fulfilment can create a shipment, print a
label, quote a price, track, and list pickup points without caring which courier it is.
Replaces paying for izprati.bg — same capabilities, in-house.

Adapters (by priority):
  Speedy   — REST/JSON, api.speedy.bg/v1, userName/password in body   [primary, implemented]
  Econt    — JSON, ee.econt.com/services, HTTP Basic                  [primary, implemented]
  BoxNow   — api-production.boxnow.bg, locker-only (АПМ)              [implemented 2026-08-05]
  SameDay  — API + easybox lockers                                    [pickup boxes, stub]
  PigeonExpress, Evropat                                              [secondary, stub]

Credentials live in ~/.hermes/.env, one pair per courier, e.g. SPEEDY_USER / SPEEDY_PASS.
This module self-loads that file (process env wins), so callers that don't pre-load it (the
FastAPI panel) still find the keys. Nothing here creates a REAL shipment until credentials
are present AND the caller passes do_write=True — a label is a real-world action, so the
console confirms before committing. calculate()/offices()/track() are side-effect-free.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

_ENV_FILE = Path.home() / ".hermes" / ".env"


@lru_cache(maxsize=1)
def _env_file_vals():
    """Parse ~/.hermes/.env once (same minimal parser as barsy_api)."""
    vals = {}
    if _ENV_FILE.exists():
        for ln in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def _cred(key):
    """Process env overrides the shared secret file."""
    return os.environ.get(key) or _env_file_vals().get(key, "")


def _amt(v):
    """Tolerant money parse for courier payout feeds — handles comma-decimals ("19,99") and
    thousands separators ("1.234,56"); None/blank/garbage → 0.0. The rightmost , or . is the decimal."""
    import re
    s = re.sub(r"[^\d,.\-]", "", str(v if v is not None else "")).strip(".,")
    if not s:
        return 0.0
    sep = max(s.rfind(","), s.rfind("."))
    num = (re.sub(r"[.,]", "", s[:sep]) + "." + s[sep + 1:]) if sep != -1 else s
    try:
        return round(float(num), 2)
    except ValueError:
        return 0.0


# each entry: env var names for credentials + the API base
REGISTRY = {
    "speedy":  {"user": "SPEEDY_USER",  "pass": "SPEEDY_PASS",  "base": "https://api.speedy.bg/v1"},
    "econt":   {"user": "ECONT_USER",   "pass": "ECONT_PASS",   "base": "https://ee.econt.com/services"},
    "boxnow":  {"user": "BOXNOW_ID",    "pass": "BOXNOW_SECRET", "base": "https://api-production.boxnow.bg/api/v1"},
    "sameday": {"user": "SAMEDAY_USER", "pass": "SAMEDAY_PASS", "base": "https://api.sameday.bg/api"},
    "pigeon":  {"user": "PIGEON_USER",  "pass": "PIGEON_PASS",  "base": "https://api.pigeonexpress.com/v1"},
    "evropat": {"user": "EVROPAT_USER", "pass": "EVROPAT_PASS", "base": ""},
}

BG_LABELS = {"speedy": "Спиди", "econt": "Еконт", "boxnow": "BoxNow",
             "sameday": "Sameday", "pigeon": "Pigeon Express", "evropat": "Европът"}

# Bulgaria is on the euro since 2026 — couriers quote and collect COD in EUR.
CURRENCY = "EUR"


class CourierError(Exception):
    pass


class Shipment:
    """Normalized shipment request fulfilment builds from a Shopify order; each adapter maps
    it to that courier's payload. Delivery is EITHER to a door `address` OR to `office_id`
    (a courier office / locker) — pass one."""
    def __init__(self, order_ref, recipient, phone, address=None, office_id=None,
                 cod_amount=0.0, weight_kg=1.0, contents="стоки за дома", parcels=1,
                 declared_value=None, note="", email="", private_person=True,
                 fragile=False, inspection=False):
        self.order_ref = order_ref            # Shopify order name, for our records
        self.recipient = recipient            # full name
        self.phone = phone
        self.email = email
        self.private_person = private_person
        self.address = address                # dict {city, postcode, street, street_no, ...} for door
        self.office_id = office_id            # OR a courier office / locker id for pickup
        self.cod_amount = float(cod_amount)   # наложен платеж (EUR); 0 = prepaid
        self.weight_kg = float(weight_kg)
        self.contents = contents
        self.parcels = int(parcels)
        self.declared_value = declared_value
        self.note = note
        self.fragile = bool(fragile)           # Дарлинг policy: home goods are usually fragile
        self.inspection = bool(inspection)     # преглед — recipient may OPEN before paying (not test)


class Courier:
    """Base adapter. Subclasses implement the per-courier API calls."""
    name = "base"

    def __init__(self):
        cfg = REGISTRY[self.name]
        self.user = _cred(cfg["user"])
        self.password = _cred(cfg["pass"])
        self.base = cfg["base"]

    def configured(self):
        return bool(self.user and self.password and self.base)

    def _require(self):
        if not self.configured():
            raise CourierError(
                f"{BG_LABELS.get(self.name, self.name)}: липсват ключове "
                f"({REGISTRY[self.name]['user']}/{REGISTRY[self.name]['pass']}) в ~/.hermes/.env")

    # --- interface (each adapter overrides) ------------------------------------------
    def ping(self) -> dict:
        """Cheap auth check + sender identity. {ok, sender_name, sender_place} or raises."""
        raise NotImplementedError

    def create_shipment(self, shp: "Shipment", do_write=False) -> dict:
        """Create the shipment (only when do_write=True). Returns
        {shipment_id, tracking_number, cod_amount, price, currency, label_ref}."""
        raise NotImplementedError

    def label_pdf(self, shipment_id) -> bytes:
        """Printable label as PDF bytes."""
        raise NotImplementedError

    def track(self, tracking_number) -> dict:
        """{status, status_bg, delivered: bool, last_ts, events:[...]}"""
        raise NotImplementedError

    def offices(self, city=None):
        """Pickup points / lockers, for the office picker."""
        raise NotImplementedError

    def calculate(self, shp: "Shipment") -> dict:
        """{price, currency} quote — no side effects."""
        raise NotImplementedError

    def payout_report(self, date_from, date_to) -> list:
        """COD payout report for a date window (YYYY-MM-DD) →
        [{waybill, collected, fee, payout_date, status}]. Overridden per courier; [] if unsupported.
        Read-only. (Speedy/Econt shapes to validate live once shipments exist at launch.)"""
        return []


def get(name) -> Courier:
    name = (name or "").lower()
    cls = _ADAPTERS.get(name)
    if not cls:
        raise CourierError(f"unknown courier: {name}")
    return cls()


def available():
    """[(name, bg_label, configured?)] — drives the console's courier picker."""
    out = []
    for n in REGISTRY:
        try:
            out.append((n, BG_LABELS.get(n, n), get(n).configured()))
        except Exception:
            out.append((n, BG_LABELS.get(n, n), False))
    return out


# ======================================================================================
# Speedy — api.speedy.bg/v1. userName/password in the JSON body of every request.
# ======================================================================================
class Speedy(Courier):
    name = "speedy"
    DEFAULT_SERVICE_ID = 505  # "СТАНДАРТ 24 ЧАСА" (COD-allowed; confirmed on this contract)

    def _post(self, path, payload, raw=False):
        self._require()
        body = dict(payload)
        body["userName"] = self.user
        body["password"] = self.password
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            raise CourierError(f"Спиди HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}")
        except Exception as e:
            raise CourierError(f"Спиди мрежова грешка: {e}")
        if raw:
            return data
        out = json.loads(data.decode("utf-8", "replace"))
        if isinstance(out, dict) and out.get("error"):
            err = out["error"]
            raise CourierError(f"Спиди: {err.get('message', err)}")
        return out

    @lru_cache(maxsize=1)
    def _sender(self):
        """The contract's registered sender (auto-discovered — no manual config needed)."""
        clients = self._post("/client/contract", {"language": "BG"}).get("clients", [])
        if not clients:
            raise CourierError("Спиди: няма подател по договора.")
        return clients[0]

    def ping(self):
        s = self._sender()
        return {"ok": True, "sender_name": s.get("clientName", ""),
                "sender_place": s.get("address", {}).get("siteAddressString", ""),
                "client_id": s.get("clientId")}

    def _resolve_site(self, city, postcode=None):
        """Speedy needs a numeric siteId, not a free-text city name. Resolve it via
        /location/site (best match on name, optionally postcode)."""
        if not city:
            return None
        try:
            out = self._post("/location/site", {"countryId": 100, "name": city.strip()})
        except Exception:
            return None
        sites = out.get("sites") or []
        if postcode:
            for s in sites:
                if str(s.get("postCode")) == str(postcode):
                    return s.get("id")
        return sites[0].get("id") if sites else None

    def _recipient(self, shp: "Shipment"):
        r = {"privatePerson": bool(shp.private_person),
             "clientName": shp.recipient, "contactName": shp.recipient,
             "phone1": {"number": shp.phone}}
        if shp.email:
            r["email"] = shp.email
        if shp.office_id:
            r["pickupOfficeId"] = int(shp.office_id)
        else:
            a = shp.address or {}
            site_id = a.get("site_id") or self._resolve_site(a.get("city"), a.get("postcode"))
            r["address"] = {
                "countryId": 100,
                "siteId": site_id,
                "siteName": None if site_id else a.get("city"),
                "postCode": a.get("postcode"),
                "streetName": a.get("street"),
                "streetId": a.get("street_id"),
                "streetNo": a.get("street_no", "") or "1",
                "blockNo": a.get("block", ""),
                "entranceNo": a.get("entrance", ""),
                "floorNo": a.get("floor", ""),
                "apartmentNo": a.get("apartment", ""),
                "addressNote": a.get("note", ""),
            }
        return r

    def _service(self, shp: "Shipment"):
        svc = {"serviceId": self.DEFAULT_SERVICE_ID, "autoAdjustPickupDate": True}
        add = {}
        if shp.cod_amount > 0:
            add["cod"] = {"amount": round(shp.cod_amount, 2), "currencyCode": CURRENCY,
                          "processingType": "CASH"}
        if shp.declared_value:
            add["declaredValue"] = {"amount": round(float(shp.declared_value), 2),
                                    "currencyCode": CURRENCY}
        if shp.inspection:
            # преглед: option-before-payment OPEN. ⚠ VERIFY-LIVE: Speedy OBPD also needs a
            # return-shipment service configured; confirm on the first real label.
            add["obpd"] = {"option": "OPEN", "returnShipmentPayer": "SENDER"}
        # NOTE: Speedy has no simple "fragile" boolean (it's packaging/handling); shp.fragile is
        # carried for parity but not mapped to a Speedy field — revisit if a contract service exists.
        if add:
            svc["additionalServices"] = add
        return svc

    def _shipment_payload(self, shp: "Shipment"):
        return {
            "language": "BG",
            "sender": {"clientId": self._sender().get("clientId")},
            "recipient": self._recipient(shp),
            "service": self._service(shp),
            "content": {"parcelsCount": shp.parcels, "totalWeight": shp.weight_kg,
                        "contents": shp.contents, "package": "КАШОН"},
            # COD collected from recipient; if prepaid the sender pays the courier fee
            "payment": {"courierServicePayer": "RECIPIENT" if shp.cod_amount > 0 else "SENDER"},
            "ref1": shp.order_ref,
        }

    def _calc_payload(self, shp: "Shipment"):
        """Speedy /calculate wants a different recipient shape than /shipment: the location
        goes under `addressLocation` (a resolved siteId), and services are a LIST."""
        rec = {"privatePerson": bool(shp.private_person), "phone1": {"number": shp.phone}}
        if shp.office_id:
            rec["pickupOfficeId"] = int(shp.office_id)
        else:
            a = shp.address or {}
            site_id = a.get("site_id") or self._resolve_site(a.get("city"), a.get("postcode"))
            if not site_id:
                raise CourierError("Спиди: не намерих населеното място на получателя.")
            rec["addressLocation"] = {"siteId": site_id}
        svc: dict = {"serviceIds": [self.DEFAULT_SERVICE_ID], "autoAdjustPickupDate": True}
        # 🚨 The COD service MUST be attached here too, not only in _service() (the shipment builder).
        # Without it Speedy prices a parcel carrying no наложен платеж and returns the SAME total with
        # and without COD — so the quote came back with cod_fee 0.00 while the real label was booked
        # WITH the surcharge and we paid it. Quote path and booking path disagreeing is a silent
        # per-order loss, on 55–60% of orders. `courierServicePayer` below is NOT a substitute: it
        # decides who pays the courier fee, not what the COD surcharge is.
        # Verified live 2026-07-31 (Свищов, 1kg, 13.50 EUR): 5.94 without → 6.25 with.
        # NB the shape differs from _service(): calculate takes `serviceIds` (a LIST) while the
        # shipment builder takes a singular `serviceId`. Only `additionalServices` is common — keep
        # each envelope verbatim rather than factoring them together.
        if shp.cod_amount > 0:
            svc["additionalServices"] = {
                "cod": {"amount": round(shp.cod_amount, 2), "currencyCode": CURRENCY,
                        "processingType": "CASH"}}
        return {
            "language": "BG", "sender": {"clientId": self._sender().get("clientId")},
            "recipient": rec,
            "service": svc,
            "content": {"parcelsCount": shp.parcels, "totalWeight": shp.weight_kg,
                        "contents": shp.contents, "package": "КАШОН"},
            "payment": {"courierServicePayer": "RECIPIENT" if shp.cod_amount > 0 else "SENDER"},
        }

    def calculate(self, shp: "Shipment"):
        out = self._post("/calculate", self._calc_payload(shp))
        calcs = out.get("calculations") or []
        if not calcs:
            raise CourierError("Спиди: няма изчисление за този адрес.")
        price = calcs[0].get("price", {})
        return {"price": round(price.get("total", 0), 2), "currency": price.get("currency", CURRENCY),
                "courier": "speedy"}

    def validate(self, shp: "Shipment"):
        """Speedy BG has NO label-validate mode (unlike Econt's mode:"validate"). The cost-free check
        is /calculate — it prices the request and rejects a bad address, but does NOT fully validate
        every service option. To confirm option fields (обявена стойност / преглед) with certainty,
        create_shipment(do_write=True) then cancel() before pickup (no charge). This returns calculate."""
        return self.calculate(shp)

    def create_shipment(self, shp: "Shipment", do_write=False):
        if not do_write:
            raise CourierError("Спиди: create_shipment изисква do_write=True (реален етикет).")
        out = self._post("/shipment", self._shipment_payload(shp))
        parcels = out.get("parcels") or []
        return {
            "courier": "speedy",
            "shipment_id": out.get("id"),
            "tracking_number": str(parcels[0].get("id")) if parcels else str(out.get("id")),
            "cod_amount": shp.cod_amount,
            "price": round((out.get("price") or {}).get("total", 0), 2),
            "currency": (out.get("price") or {}).get("currency", CURRENCY),
            "label_ref": out.get("id"),
        }

    def cancel(self, shipment_id, comment="test"):
        """Cancel a shipment BEFORE pickup — no charge. Speedy BG has no separate sandbox host, so the
        cost-free way to verify a full label (incl. options like обявена стойност / преглед) is to
        create a real shipment, read the response, then cancel it here before the courier collects."""
        return self._post("/shipment/cancel", {"shipmentId": str(shipment_id), "comment": comment})

    def label_pdf(self, shipment_id):
        return self._post("/print",
                          {"parcels": [{"parcel": {"id": int(shipment_id)}}], "paperSize": "A6"},
                          raw=True)

    def track(self, tracking_number):
        out = self._post("/track", {"parcels": [{"id": str(tracking_number)}], "language": "BG"})
        parcels = out.get("parcels") or []
        ops = (parcels[0].get("operations") if parcels else []) or []
        last = ops[-1] if ops else {}
        return {
            "status": last.get("operationCode", ""),
            "status_bg": last.get("description", "няма данни"),
            "delivered": any(o.get("operationCode") == 803 for o in ops),  # 803 = delivered
            "last_ts": last.get("dateTime", ""),
            "events": [{"ts": o.get("dateTime", ""), "text": o.get("description", "")} for o in ops],
        }

    def offices(self, city=None):
        payload = {"countryId": 100, "language": "BG"}
        if city:
            payload["name"] = city
        out = self._post("/location/office", payload)
        return [{"id": o.get("id"), "name": o.get("name"),
                 "address": o.get("address", {}).get("fullAddressString", ""),
                 "city": o.get("address", {}).get("siteName", "")}
                for o in (out.get("offices") or [])]

    def payout_report(self, date_from, date_to):
        """Speedy COD payout report (Payments Service). Verified shape: POST /payments with
        fromDate/toDate + includeDetails → `payouts[]`, each Payout carrying `details[]`
        (PayoutDetails) keyed by `shipmentId` = waybill. We flatten to per-waybill rows. Falls back to
        payout-level rows if details are absent. VALIDATE LIVE at launch (no shipments pre-launch)."""
        out = self._post("/payments", {"fromDate": date_from, "toDate": date_to,
                                        "includeDetails": True, "language": "BG"})
        payouts = out.get("payouts") or out.get("payments") or out.get("rows") or []
        norm = []
        for p in payouts:
            pdate = p.get("date") or p.get("payoutDate") or p.get("transferDate") or ""
            details = p.get("details") or []
            if details:
                for d in details:
                    norm.append({
                        "waybill": str(d.get("shipmentId") or d.get("waybill") or d.get("parcelId") or ""),
                        "collected": _amt(d.get("amount")),
                        "fee": None,
                        "payout_date": d.get("deliveryDate") or pdate,
                        "status": p.get("paymentType") or p.get("docType") or "",
                    })
            else:
                fee = p.get("fee", p.get("commission"))
                norm.append({
                    "waybill": str(p.get("shipmentId") or p.get("parcelId") or p.get("waybill")
                                   or p.get("docId") or p.get("id") or ""),
                    "collected": _amt(p.get("amount", p.get("codAmount"))),
                    "fee": _amt(fee) if fee is not None else None,
                    "payout_date": pdate,
                    "status": p.get("paymentType") or p.get("status") or "",
                })
        return norm


# ======================================================================================
# Econt — ee.econt.com/services. HTTP Basic auth; JSON in, JSON out.
# ======================================================================================
class Econt(Courier):
    name = "econt"

    def _post(self, path, payload):
        self._require()
        tok = base64.b64encode(f"{self.user}:{self.password}".encode()).decode("ascii")
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Basic " + tok})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                text = r.read().decode("utf-8", "replace").strip()
        except urllib.error.HTTPError as e:
            raise CourierError(f"Еконт HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}")
        except Exception as e:
            raise CourierError(f"Еконт мрежова грешка: {e}")
        if not text:
            return {}  # some Econt endpoints (getClientProfiles for a bare account) return empty
        out = json.loads(text)
        if isinstance(out, dict) and out.get("type") and "error" in str(out.get("type", "")).lower():
            raise CourierError(f"Еконт: {out.get('message', out)}")
        return out

    @lru_cache(maxsize=1)
    def _profile(self):
        out = self._post("/Profile/ProfileService.getClientProfiles.json", {})
        profiles = out.get("profiles") or []
        return profiles[0] if profiles else {}

    def ping(self):
        # Confirm auth with a light nomenclature call (profile can be empty for this account).
        self._post("/Nomenclatures/NomenclaturesService.getCountries.json", {})
        p = self._profile()
        client = p.get("client", {}) if p else {}
        addrs = p.get("addresses") or []
        place = ""
        if addrs:
            city = addrs[0].get("city") or {}
            full = addrs[0].get("fullAddress") or addrs[0].get("street") or ""
            place = " ".join(x for x in [city.get("name") or "", full] if x).strip()
        return {"ok": True, "sender_name": client.get("name") or self.user,
                "sender_place": place, "sender_configured": bool(addrs)}

    def _sender_block(self):
        """Sender client + address from the Econt profile (falls back to ECONT_SENDER_* env)."""
        p = self._profile()
        client = p.get("client") if p else None
        addrs = p.get("addresses") if p else None
        sender_client = None
        sender_address = None
        if client:
            sender_client = {"name": client.get("name"),
                             "phones": [ph for ph in (client.get("phones") or [])] or [_cred("ECONT_SENDER_PHONE")]}
        if addrs:
            a = addrs[0]
            sender_address = {"city": a.get("city"), "street": a.get("street"),
                              "num": a.get("num"), "other": a.get("other", "")}
        return sender_client, sender_address

    def _label(self, shp: "Shipment"):
        sc, sa = self._sender_block()
        label = {
            "senderClient": sc,
            "senderAddress": sa,
            "receiverClient": {"name": shp.recipient, "phones": [shp.phone]},
            "packCount": shp.parcels,
            "shipmentType": "pack",
            "weight": shp.weight_kg,
            "shipmentDescription": shp.contents,
        }
        if shp.email:
            label["receiverClient"]["email"] = shp.email
        if shp.office_id:
            label["receiverOfficeCode"] = str(shp.office_id)
        else:
            a = shp.address or {}
            label["receiverAddress"] = {
                "city": {"name": a.get("city"), "postCode": a.get("postcode"),
                         "country": {"code3": "BGR"}},
                "street": a.get("street", ""), "num": a.get("street_no", ""),
                "other": a.get("note", ""),
            }
        services = {}
        if shp.cod_amount > 0:
            services.update({"cdAmount": round(shp.cod_amount, 2), "cdType": "get",
                             "cdCurrency": CURRENCY})
        if shp.declared_value:
            # обявена стойност / застраховка. ⚠ VERIFY-LIVE: confirm exact Econt field at first label.
            services["declaredValueAmount"] = round(float(shp.declared_value), 2)
        if services:
            label["services"] = services
        # преглед — recipient may OPEN the parcel before paying (Дарлинг policy; not "тест").
        # ⚠ VERIFY-LIVE: confirm the Econt field name on the first real label.
        if shp.inspection:
            label["payAfterAccept"] = True
        if shp.fragile:
            # Econt has no boolean "fragile"; flag it in the description so the courier handles it.
            label["shipmentDescription"] = f"{shp.contents} · чупливо"
        return label

    def calculate(self, shp: "Shipment"):
        out = self._post("/Shipments/LabelService.createLabel.json",
                         {"label": self._label(shp), "mode": "calculate"})
        label = out.get("label", out)
        return {"price": round(label.get("totalPrice", 0) or 0, 2),
                "currency": label.get("currency", CURRENCY), "courier": "econt"}

    def validate(self, shp: "Shipment"):
        """mode:"validate" — checks the full label request (address + option fields) WITHOUT creating
        a shipment or incurring cost. Use to confirm option field names (declaredValue/преглед/etc.)
        cost-free before the first real label. Returns the raw courier response."""
        return self._post("/Shipments/LabelService.createLabel.json",
                          {"label": self._label(shp), "mode": "validate"})

    def create_shipment(self, shp: "Shipment", do_write=False):
        if not do_write:
            raise CourierError("Еконт: create_shipment изисква do_write=True (реален етикет).")
        out = self._post("/Shipments/LabelService.createLabel.json",
                         {"label": self._label(shp), "mode": "create"})
        label = out.get("label", out)
        return {
            "courier": "econt",
            "shipment_id": label.get("shipmentNumber"),
            "tracking_number": label.get("shipmentNumber"),
            "cod_amount": shp.cod_amount,
            "price": round(label.get("totalPrice", 0) or 0, 2),
            "currency": label.get("currency", CURRENCY),
            "label_ref": label.get("pdfURL"),  # Econt returns a URL to the PDF
        }

    def cancel(self, shipment_id, comment="test"):
        """Delete a shipment before it's handed to the courier — no charge. Same purpose as Speedy's
        cancel: create a real label to verify the full request cost-free, then remove it."""
        return self._post("/Shipments/LabelService.deleteLabels.json",
                          {"shipmentNumbers": [str(shipment_id)]})

    def label_pdf(self, shipment_id):
        # Econt exposes the label as a URL; fetch it. shipment_id here is the pdfURL.
        url = shipment_id if str(shipment_id).startswith("http") else None
        if not url:
            raise CourierError("Еконт: подай pdfURL (label_ref) за изтегляне на етикета.")
        # SSRF guard: this fetches a URL that may originate from stored/upstream data. Pin it to
        # Econt's own host so it can never be turned into an arbitrary internal GET.
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host != "ee.econt.com" and not host.endswith(".econt.com"):
            raise CourierError("Еконт: неразрешен адрес за етикет.")
        with urllib.request.urlopen(url, timeout=40) as r:
            return r.read()

    def track(self, tracking_number):
        out = self._post("/Shipments/ShipmentService.getShipmentStatuses.json",
                         {"shipmentNumbers": [str(tracking_number)]})
        statuses = out.get("shipmentStatuses") or []
        st = statuses[0].get("status", {}) if statuses else {}
        history = st.get("trackingEvents") or []
        return {
            "status": st.get("shortDeliveryStatus", ""),
            "status_bg": st.get("deliveryStatus", "няма данни"),
            "delivered": (st.get("shortDeliveryStatus", "") or "").lower() == "delivered",
            "last_ts": history[-1].get("time", "") if history else "",
            "events": [{"ts": e.get("time", ""), "text": e.get("destinationDetails", "") or e.get("officeName", "")}
                       for e in history],
        }

    def offices(self, city=None):
        out = self._post("/Nomenclatures/NomenclaturesService.getOffices.json",
                         {"countryCode": "BGR"})
        offices = out.get("offices") or []
        if city:
            cl = city.lower()
            offices = [o for o in offices if cl in (o.get("address", {}).get("city", {}).get("name", "") or "").lower()]
        # Return the FULL nomenclature (no [:400] cap): callers fetch with city=None and filter by
        # city in-process, and a 400-cap sliced the list alphabetically so most cities (incl. most of
        # София) were cut off before the filter (the ?city=София→1 bug). The office endpoint caches
        # per-TTL and caps the RESPONSE, so the wire payload stays bounded.
        # Drop offices with no `code`. The storefront validates a customer's chosen office against
        # this list and then books on the same field (`receiverOfficeCode`), so an entry with a null
        # id is unbookable — but worse, it fails as `office_not_validated`, which reads as "the
        # customer picked a bad office" rather than "the nomenclature had a hole". An error that
        # blames a person for a data fault is worse than a louder one, because someone acts on it
        # and rings the customer back. Unselectable is the honest behaviour. (Hermes, 2026-07-31)
        return [{"id": o.get("code"), "name": o.get("name"),
                 "address": o.get("address", {}).get("fullAddress", ""),
                 "city": o.get("address", {}).get("city", {}).get("name", "")}
                for o in offices if o.get("code") not in (None, "")]

    def payout_report(self, date_from, date_to):
        """Econt COD payout report for a period (наложен платеж / CD). Endpoint/field shape per Econt
        docs — VALIDATE LIVE at launch (no shipments pre-launch)."""
        out = self._post("/Shipments/PaymentReportService.PaymentReport.json",
                         {"dateFrom": date_from, "dateTo": date_to})
        # verified: PaymentReport → rows (PaymentReportRow) with num=waybill, amount, payDate, payType
        rows = (out.get("paymentReport") or out.get("rows") or out.get("elements")
                or out.get("shipments") or [])
        norm = []
        for r in rows:
            amt = r.get("amount", r.get("cdPaidAmount", r.get("CD_send_sum", r.get("codAmount"))))
            norm.append({
                "waybill": str(r.get("num") or r.get("shipmentNumber") or r.get("waybill") or ""),
                "collected": _amt(amt),
                "fee": None,
                "payout_date": r.get("payDate") or r.get("cdPaidTime") or r.get("CD_send_time") or "",
                "status": r.get("payType") or r.get("type") or r.get("status") or "",
            })
        return norm


# ======================================================================================
# BOX NOW — api-production.boxnow.bg. LOCKER-ONLY (АПМ автомати), max 20 kg HARD, locker
# 60×45×36 cm. Surface mapped 2026-08-05 from BOX NOW's OFFICIAL WooCommerce plugin
# v3.0.0_global (github.com/boxltdbg/bx_wp_plugin — box-now-delivery.php + api-helpers +
# cancel/print): POST /api/v1/auth-sessions {grant_type:"client_credentials", client_id,
# client_secret} → access_token (Bearer on everything else); GET /api/v1/origins →
# {data:[{id,name}]} (warehouses; ours is BOXNOW_WAREHOUSE_ID=2 = any-APM, изпращане ОТ
# автомат); POST /api/v1/delivery-requests {orderNumber, invoiceValue, paymentMode
# cod|prepaid, amountToBeCollected, allowReturn, origin{contactName,contactNumber,
# contactEmail, locationId=warehouse}, destination{…, locationId=locker id},
# items:[{value, weight, compartmentSize 1|2|3}]} → {id, parcels:[{id}]};
# POST /api/v1/parcels/{id}:cancel; GET /api/v1/parcels/{id}/label.pdf → PDF bytes.
# Lockers list is PUBLIC (no auth): locationapi-production.boxnow.bg/v1/apms_bg-BG.json.
# Checkout map widget: widget-v5.boxnow.bg?partnerId=BOXNOW_PARTNER_ID (woo lane).
# Compartments (plugin's size table): 1=60×45×8, 2=60×45×17 (default when no dims),
# 3=60×45×36. NO quote API — prices are CONTRACT-FIXED (Анекса): BOXNOW_PRICE_EUR /
# BOXNOW_COD_FEE_EUR in ~/.hermes/.env; calculate() fails LOUDLY without them (no invented
# money). COD at a locker is NEVER cash — card via SMS/Viber/email link (customer copy on
# the storefront must say this; the guide mandates the exact warning text).
# The plugin exposes no tracking endpoint → track() raises honestly (capture rule: don't
# guess — record the real one from the partner portal's XHR if we ever need it).
# BOX NOW REQUIRES a confirmed TEST ORDER before going live (their integration email).
# ======================================================================================
class BoxNow(Courier):
    name = "boxnow"
    LOCKERS_URL = "https://locationapi-production.boxnow.bg/v1/apms_bg-BG.json"
    MAX_KG = 20.0

    def __init__(self):
        super().__init__()
        self._token_val, self._token_exp = "", 0.0

    def _token(self):
        import time
        if self._token_val and time.time() < self._token_exp:
            return self._token_val
        self._require()
        body = json.dumps({"grant_type": "client_credentials",
                           "client_id": self.user, "client_secret": self.password}).encode()
        req = urllib.request.Request(self.base + "/auth-sessions", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                out = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise CourierError(f"BoxNow auth HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:
            raise CourierError(f"BoxNow мрежова грешка при вход: {e}")
        tok = out.get("access_token") or ""
        if not tok:
            raise CourierError(f"BoxNow: auth не върна access_token ({str(out)[:120]}).")
        self._token_val = tok
        self._token_exp = time.time() + min(float(out.get("expires_in") or 3600), 3600) - 60
        return tok

    def _call(self, path, payload=None, method=None, raw=False):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method or ("POST" if data else "GET"),
            headers={"Content-Type": "application/json", "Accept": "*/*",
                     "Authorization": "Bearer " + self._token()})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise CourierError(f"BoxNow HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:
            raise CourierError(f"BoxNow мрежова грешка: {e}")
        return body if raw else (json.loads(body.decode("utf-8", "replace")) if body else {})

    def _warehouse(self):
        wid = _cred("BOXNOW_WAREHOUSE_ID")
        if not wid:
            raise CourierError("BoxNow: липсва BOXNOW_WAREHOUSE_ID в ~/.hermes/.env "
                               "(нашият е 2 — изпращане от автомат, писмото на BOX NOW).")
        return str(wid)

    def ping(self):
        """Auth + config check in one: the token round-trip proves the credentials, and the
        origins list must contain OUR warehouse id or labels would fail later."""
        wid = self._warehouse()
        rows = (self._call("/origins") or {}).get("data") or []
        names = {str(w.get("id")): (w.get("name") or "") for w in rows if isinstance(w, dict)}
        if wid not in names:
            raise CourierError(f"BoxNow: склад {wid} не е в /origins ({sorted(names)}) — "
                               "проверете Warehouse ID с BOX NOW.")
        return {"ok": True, "sender_name": names[wid], "sender_place": f"склад {wid}"}

    def offices(self, city=None):
        """Lockers from the PUBLIC location API (no credentials needed — same source the
        official plugin's map uses)."""
        url = self.LOCKERS_URL + "?size=medium"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            raise CourierError(f"BoxNow: списъкът с автомати не се зареди: {e}")
        out = []
        for o in data.get("data") or []:
            if not isinstance(o, dict) or o.get("id") in (None, ""):
                continue
            out.append({"id": o.get("id"),
                        "name": str(o.get("name") or o.get("title") or ""),
                        "address": str(o.get("address") or o.get("addressLine1") or ""),
                        "city": str(o.get("city") or o.get("addressLine2") or "")})
        if city:
            key = "".join(str(city).split()).casefold()
            out = [o for o in out if key in "".join(o["city"].split()).casefold()
                   or key in "".join(o["address"].split()).casefold()
                   or key in "".join(o["name"].split()).casefold()]
        return out

    def _contract_price(self, cod):
        """NO quote API — the tariff is the contract Анекс. Env-set by the owner; missing
        numbers must FAIL, never guess (money fails closed)."""
        price = _cred("BOXNOW_PRICE_EUR")
        if not price:
            raise CourierError("BoxNow: липсва BOXNOW_PRICE_EUR (цената по Анекса) в "
                               "~/.hermes/.env — BOX NOW няма API за оферта.")
        total = float(price)
        if cod:
            fee = _cred("BOXNOW_COD_FEE_EUR")
            if fee == "":
                raise CourierError("BoxNow: липсва BOXNOW_COD_FEE_EUR (таксата наложен платеж "
                                   "по Анекса) в ~/.hermes/.env.")
            total += float(fee)
        return round(total, 2)

    def calculate(self, shp: "Shipment"):
        if shp.weight_kg > self.MAX_KG:
            raise CourierError(f"BoxNow: {shp.weight_kg} kg надвишава твърдия лимит "
                               f"{self.MAX_KG:.0f} kg за автомат.")
        return {"price": self._contract_price(shp.cod_amount > 0),
                "currency": CURRENCY, "courier": "boxnow"}

    def create_shipment(self, shp: "Shipment", do_write=False):
        if not do_write:
            raise CourierError("BoxNow: create_shipment изисква do_write=True (реален етикет).")
        if not shp.office_id:
            raise CourierError("BoxNow: доставя САМО до автомат — подайте office_id "
                               "(id на автомат от offices()).")
        if shp.weight_kg > self.MAX_KG:
            raise CourierError(f"BoxNow: {shp.weight_kg} kg надвишава лимита {self.MAX_KG:.0f} kg.")
        sender = {"contactName": _cred("BOXNOW_SENDER_NAME"),
                  "contactNumber": _cred("BOXNOW_SENDER_PHONE"),
                  "contactEmail": _cred("BOXNOW_SENDER_EMAIL")}
        if not (sender["contactName"] and sender["contactNumber"]):
            raise CourierError("BoxNow: липсват BOXNOW_SENDER_NAME / BOXNOW_SENDER_PHONE "
                               "в ~/.hermes/.env (контакт на подателя за товарителницата).")
        cod = round(shp.cod_amount, 2)
        item = {"value": f"{(shp.declared_value if shp.declared_value is not None else cod):.2f}",
                "weight": shp.weight_kg,
                "compartmentSize": 2}   # plugin default (60×45×17) — Shipment carries no dims
        payload = {
            "orderNumber": str(shp.order_ref),
            "invoiceValue": f"{cod:.2f}" if cod > 0 else "0",
            "paymentMode": "cod" if cod > 0 else "prepaid",
            "amountToBeCollected": f"{cod:.2f}" if cod > 0 else "0",
            "allowReturn": True,
            "origin": dict(sender, locationId=self._warehouse()),
            "destination": {"contactName": shp.recipient, "contactNumber": shp.phone,
                            "contactEmail": shp.email or "", "locationId": str(shp.office_id)},
            "items": [dict(item) for _ in range(max(1, shp.parcels))],
        }
        out = self._call("/delivery-requests", payload)
        parcels = [p.get("id") for p in (out.get("parcels") or []) if isinstance(p, dict)]
        if not out.get("id") or not parcels:
            raise CourierError(f"BoxNow: заявката не върна parcels ({str(out)[:160]}).")
        return {"courier": "boxnow", "shipment_id": out["id"],
                "tracking_number": str(parcels[0]), "cod_amount": cod,
                "price": self._contract_price(cod > 0), "currency": CURRENCY,
                "label_ref": str(parcels[0]), "parcel_ids": [str(p) for p in parcels]}

    def label_pdf(self, shipment_id):
        pdf = self._call(f"/parcels/{shipment_id}/label.pdf", raw=True)
        if not pdf.startswith(b"%PDF"):
            raise CourierError(f"BoxNow: /label.pdf не върна PDF ({pdf[:60]!r}).")
        return pdf

    def cancel(self, shipment_id, comment=""):
        return self._call(f"/parcels/{shipment_id}:cancel", {}, method="POST")

    def track(self, tracking_number):
        raise CourierError("BoxNow: плъгинът им няма tracking endpoint — не гадаем (правилото "
                           "за capture): при нужда се записва реалната заявка от партньорския "
                           "портал. Статусите идват по имейл/портал.")


class Sameday(Courier):
    name = "sameday"


# ======================================================================================
# Pigeon Express — api.pigeonexpress.com/v1. X-API-Key (pk_live…) + X-API-Secret (sk_live…)
# headers on every request. Surface mapped 2026-08-05 from Pigeon's OFFICIAL WooCommerce
# plugin v1.0.1 (plugins.svn.wordpress.org/pigeon-express-for-woocommerce — ApiClient +
# Labels/BodyBuilder + LabelService): GET /cities, /cities/{id}/streets, /offices
# (?type=locker = АПС автомати), /services; POST /shipments/calculate → data.total_price;
# POST /shipments → data.reference_number + data.label_pdf; GET /shipments/{ref}/track;
# POST /shipments/{ref}/cancel. Envelope: data + meta.{current_page,last_page}; errors are
# success:false + message/errors. Bodies (BodyBuilder): pickup_type office|address
# (+pickup_office_id | pickup_address{city_id,street_id,additional_info}), delivery_type
# address|office|locker (+delivery_office_id | delivery_address{city_id,additional_info}),
# packages[{weight,width,length,height}], inventory_items[{description,quantity}],
# service_type "standard", service_codes {code: bool|number}, who_pays sender|receiver,
# cod_amount. Sender pickup comes from env PIGEON_PICKUP_OFFICE_ID (their portal shows it) —
# quotes/labels raise a clear error until it is set; reads work without it.
# ⚠ VERIFY-LIVE before real labels (the Speedy lesson — quote and booking must carry the
# SAME COD semantics or the difference is a silent per-order loss on 55–60% COD orders):
# confirm the exact COD service code from GET /services and that calculate with cod_amount
# prices ABOVE the same request without it.
# ======================================================================================
class Pigeon(Courier):
    name = "pigeon"

    def _call(self, path, payload=None, method=None):
        self._require()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method or ("POST" if data else "GET"),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "DarlingPanel/1.0 (+https://darlinghome.bg)",
                     "X-API-Key": self.user, "X-API-Secret": self.password})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise CourierError(f"Pigeon HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:
            raise CourierError(f"Pigeon мрежова грешка: {e}")
        out = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(out, dict) and out.get("success") is False:
            raise CourierError(f"Pigeon: {out.get('message') or out.get('errors')}")
        return out

    def _pages(self, path):
        """Concatenate a paginated GET (data + meta.current_page/last_page)."""
        rows, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            out = self._call(f"{path}{sep}page={page}")
            rows.extend(out.get("data") or [])
            meta = out.get("meta") or {}
            try:
                last = int(meta.get("last_page") or 1)
            except (TypeError, ValueError):
                last = 1
            if page >= last:
                return rows
            page += 1

    @lru_cache(maxsize=1)
    def _cities(self):
        return self._pages("/cities")

    def _resolve_city(self, name):
        """City name (BG, case/space-insensitive) → city_id; None if not found."""
        key = "".join(str(name or "").split()).casefold()
        if not key:
            return None
        for c in self._cities():
            nm = "".join(str(c.get("name") or "").split()).casefold()
            if nm == key:
                return c.get("id")
        return None

    def _pickup(self):
        """Sender pickup block. PIGEON_PICKUP_OFFICE_ID (env / ~/.hermes/.env) = the shop's
        drop-off office in Pigeon's system; without it quotes/labels cannot be built."""
        office = _cred("PIGEON_PICKUP_OFFICE_ID")
        if not office:
            raise CourierError("Pigeon: липсва PIGEON_PICKUP_OFFICE_ID (офисът, от който пращаме) "
                               "в ~/.hermes/.env — вижда се в Pigeon портала.")
        return {"pickup_type": "office", "pickup_office_id": int(office)}

    def _body(self, shp: "Shipment"):
        """Shared calculate/create body (BodyBuilder parity). Delivery is office/locker when
        shp.office_id is set (ids are Pigeon office ids; locker ids come from ?type=locker —
        we send delivery_type "office" for both unless the id came marked as a locker)."""
        body = dict(self._pickup())
        if shp.office_id:
            body["delivery_type"] = "office"
            body["delivery_office_id"] = int(shp.office_id)
        else:
            a = shp.address or {}
            city_id = a.get("city_id") or self._resolve_city(a.get("city"))
            if not city_id:
                raise CourierError("Pigeon: не намерих населеното място на получателя.")
            extra = ", ".join(x for x in (a.get("street"), a.get("street_no")) if x)
            body["delivery_type"] = "address"
            body["delivery_address"] = {"city_id": int(city_id), "additional_info": extra}
        # length/width/height are REQUIRED (422 validated live 2026-08-05); Shipment carries no
        # dims, so a standard КАШОН box is assumed — same convention as Speedy's package "КАШОН".
        body["packages"] = [{"weight": shp.weight_kg, "length": 30, "width": 20, "height": 20}]
        body["inventory_items"] = [{"description": shp.contents, "quantity": 1}]
        body["service_type"] = "standard"
        body["who_pays"] = "receiver" if shp.cod_amount > 0 else "sender"
        if shp.cod_amount > 0:
            # 🚨 COD must ride in BOTH the quote and the booking (the Speedy incident): one body
            # builder for both paths = they cannot disagree. VERIFIED LIVE 2026-08-05: cod_amount
            # alone raises total_price (2.59 → 3.34 EUR, 1kg office→office) and the response echoes
            # service_codes:["cod_fee"] — no request-side service code exists or is needed.
            body["cod_amount"] = round(shp.cod_amount, 2)
        return body

    def ping(self):
        # GET /cities/759 is Pigeon's OWN credential check (their Woo plugin's
        # check_credentials_detailed uses exactly this resource). /services 404s on this install.
        out = self._call("/cities/759")
        city = (out.get("data") or {}).get("name") or ""
        return {"ok": True, "sender_name": "", "sender_place": city}

    def offices(self, city=None):
        rows = self._pages("/offices")
        out = []
        for o in rows:
            if not isinstance(o, dict) or o.get("id") in (None, ""):
                continue
            out.append({"id": o.get("id"), "name": o.get("name") or "",
                        "address": (o.get("address") if isinstance(o.get("address"), str)
                                    else (o.get("address") or {}).get("full_address", "")) or "",
                        "city": (o.get("city") if isinstance(o.get("city"), str)
                                 else (o.get("city") or {}).get("name", "")) or ""})
        if city:
            key = "".join(str(city).split()).casefold()
            out = [o for o in out if key in "".join(o["city"].split()).casefold()
                   or key in "".join(o["address"].split()).casefold()]
        return out

    def calculate(self, shp: "Shipment"):
        out = self._call("/shipments/calculate", self._body(shp))
        data = out.get("data") or {}
        if data.get("total_price") in (None, ""):
            raise CourierError("Pigeon: няма изчислена цена за тази заявка.")
        return {"price": round(float(data["total_price"]), 2),
                "currency": data.get("currency") or CURRENCY, "courier": "pigeon"}

    def create_shipment(self, shp: "Shipment", do_write=False):
        if not do_write:
            raise CourierError("Pigeon: create_shipment изисква do_write=True (реален етикет).")
        body = self._body(shp)
        body["receiver_name"] = shp.recipient
        body["receiver_phone"] = shp.phone
        if shp.email:
            body["receiver_email"] = shp.email
        if shp.note:
            body["note"] = shp.note[:1000]
        out = self._call("/shipments", body)
        data = out.get("data") or {}
        ref = data.get("reference_number")
        if not ref:
            raise CourierError(f"Pigeon: създаването не върна reference_number ({str(data)[:120]}).")
        return {"courier": "pigeon", "shipment_id": ref, "tracking_number": str(ref),
                "cod_amount": shp.cod_amount,
                "price": round(float(data.get("total_price") or 0), 2), "currency": CURRENCY,
                "label_ref": ref}

    def cancel(self, shipment_id, comment=""):
        return self._call(f"/shipments/{shipment_id}/cancel", {}, method="POST")

    def track(self, tracking_number):
        out = self._call(f"/shipments/{tracking_number}/track")
        data = out.get("data") or {}
        events = data.get("events") or data.get("history") or []
        last = events[-1] if events else {}
        status_txt = str(data.get("status") or last.get("status") or "")
        return {"status": status_txt,
                "status_bg": str(last.get("description") or status_txt or "няма данни"),
                "delivered": status_txt.casefold() in ("delivered", "доставена", "доставено"),
                "last_ts": str(last.get("date") or last.get("created_at") or ""),
                "events": [{"ts": str(e.get("date") or e.get("created_at") or ""),
                            "text": str(e.get("description") or e.get("status") or "")}
                           for e in events if isinstance(e, dict)]}


class Evropat(Courier):
    name = "evropat"


_ADAPTERS = {"speedy": Speedy, "econt": Econt, "boxnow": BoxNow,
             "sameday": Sameday, "pigeon": Pigeon, "evropat": Evropat}
