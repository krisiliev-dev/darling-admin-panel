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
from decimal import ROUND_HALF_UP, Decimal
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


def _form_pairs(data, prefix=""):
    """Flatten a nested body the way PHP's http_build_query does — awbRecipient[name],
    parcels[0][weight] — because that is what Sameday's own SDK sends and their validator
    expects. None values are dropped, exactly as PHP drops nulls; booleans become 1/0."""
    if isinstance(data, dict):
        items = list(data.items())
    elif isinstance(data, (list, tuple)):
        items = list(enumerate(data))
    else:
        return [(prefix, data)]
    out = []
    for key, val in items:
        name = f"{prefix}[{key}]" if prefix else str(key)
        if val is None:
            continue
        if isinstance(val, bool):
            val = 1 if val else 0
        if isinstance(val, (dict, list, tuple)):
            out.extend(_form_pairs(val, name))
        else:
            out.append((name, val))
    return out


def _coord(v):
    """A latitude/longitude from a courier feed → float, or None when there isn't one.

    None, never 0.0. Zero is a real point in the Gulf of Guinea, and an office silently
    placed there is not "unknown" — it is confidently wrong, and it sorts as nearest to
    nothing anyone will ever stand in. Couriers hand these back as floats (Speedy, Econt) and
    as strings (Pigeon, BoxNow), so both are accepted.
    """
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if -180.0 <= f <= 180.0 else None


# each entry: env var names for credentials + the API base
REGISTRY = {
    "speedy":  {"user": "SPEEDY_USER",  "pass": "SPEEDY_PASS",  "base": "https://api.speedy.bg/v1"},
    "econt":   {"user": "ECONT_USER",   "pass": "ECONT_PASS",   "base": "https://ee.econt.com/services"},
    "boxnow":  {"user": "BOXNOW_ID",    "pass": "BOXNOW_SECRET", "base": "https://api-production.boxnow.bg/api/v1"},
    "sameday": {"user": "SAMEDAY_USER", "pass": "SAMEDAY_PASS", "base": "https://api.sameday.bg"},
    # ^ host only: every Sameday endpoint already starts with /api (their SDK builds
    #   host + "/api/..."), so a base ending in /api produces /api/api/... and 404s.
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
    def _unimplemented(self, what):
        raise CourierError(
            f"{BG_LABELS.get(self.name, self.name)}: „{what}“ не е реализирана за този "
            f"куриер. Не гадаем API — surface-ът се снема от техния официален плъгин "
            f"или от записана заявка, преди да се пуска.")

    def ping(self) -> dict:
        """Cheap auth check + sender identity. {ok, sender_name, sender_place} or raises."""
        return self._unimplemented("проверка на достъпа")

    def create_shipment(self, shp: "Shipment", do_write=False) -> dict:
        """Create the shipment (only when do_write=True). Returns
        {shipment_id, tracking_number, cod_amount, price, currency, label_ref}."""
        return self._unimplemented("създаване на пратка")

    def label_pdf(self, shipment_id) -> bytes:
        """Printable label as PDF bytes."""
        return self._unimplemented("етикет")

    def track(self, tracking_number) -> dict:
        """{status, status_bg, delivered: bool, last_ts, events:[...]}"""
        return self._unimplemented("проследяване")

    def offices(self, city=None):
        """Pickup points / lockers, for the office picker."""
        return self._unimplemented("офиси/автомати")

    def calculate(self, shp: "Shipment") -> dict:
        """{price, currency} quote — no side effects."""
        return self._unimplemented("оферта")

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
        rows = []
        for o in (out.get("offices") or []):
            a = o.get("address") or {}
            # ⚠ Speedy calls LONGITUDE `x` and LATITUDE `y`. Read in the obvious order this
            # puts every Bulgarian office in the Indian Ocean and a radius search returns
            # nothing while looking like it worked. Verified live 2026-08-06: x≈24.7, y≈42.1.
            rows.append({"id": o.get("id"), "name": o.get("name"),
                         "address": a.get("fullAddressString", ""),
                         "city": a.get("siteName", ""),
                         "lat": _coord(a.get("y")), "lon": _coord(a.get("x")),
                         # APT = автомат (833 of 1293), OFFICE = counter (460). Verified live.
                         "kind": "locker" if str(o.get("type") or "").upper() == "APT" else "office"})
        return rows

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
        rows = []
        for o in offices:
            if o.get("code") in (None, ""):
                continue
            a = o.get("address") or {}
            loc = a.get("location") or {}       # one office in 630 has none — hence _coord
            rows.append({"id": o.get("code"), "name": o.get("name"),
                         "address": a.get("fullAddress", ""),
                         "city": (a.get("city") or {}).get("name", ""),
                         "lat": _coord(loc.get("latitude")), "lon": _coord(loc.get("longitude")),
                         # isAPS = автомат (38 of 630). isDrive (1) is still a staffed point.
                         "kind": "locker" if o.get("isAPS") else "office"})
        return rows

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
    # Cloudflare 1010-blocks python-urllib's default UA (verified live 2026-08-05 — the same
    # gate Pigeon has); a real product UA passes.
    UA = "DarlingPanel/1.0 (+https://darlinghome.bg)"

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
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": self.UA})
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
                     "User-Agent": self.UA, "Authorization": "Bearer " + self._token()})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise CourierError(f"BoxNow HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:
            raise CourierError(f"BoxNow мрежова грешка: {e}")
        return body if raw else (json.loads(body.decode("utf-8", "replace")) if body else {})

    @staticmethod
    def _bg_phone(p):
        """BoxNow requires INTERNATIONAL format (P405 rejection verified live 2026-08-05 on a
        national-format number; their plugin normalizes every phone to +359 the same way)."""
        p = "".join(str(p or "").split())
        if p.startswith("00"):
            return "+" + p[2:]
        if p.startswith("+"):
            return p
        return "+359" + (p[1:] if p.startswith("0") else p)

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
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": self.UA})
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
                        "city": str(o.get("city") or o.get("addressLine2") or ""),
                        # lat/lng arrive as strings on the public feed. Every BoxNow point is
                        # an автомат — there is no counter network to distinguish.
                        "lat": _coord(o.get("lat")), "lon": _coord(o.get("lng")),
                        "kind": "locker"})
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
                  "contactNumber": self._bg_phone(_cred("BOXNOW_SENDER_PHONE")),
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
            "destination": {"contactName": shp.recipient,
                            "contactNumber": self._bg_phone(shp.phone),
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


# ======================================================================================
# Sameday — api.sameday.bg (BG instance; RO api.sameday.ro, HU api.sameday.hu; the demo
# hosts are sameday-api-bg.demo.zitec.com etc.). Surface mapped 2026-08-06 from Sameday's
# OFFICIAL WooCommerce plugin v1.11.1 (samedaycourier-shipping: lib/sameday-courier PHP SDK
# + classes/*.php) together with their WooCommerce user guide v1 (2026).
#
# Auth: POST /api/authenticate carrying X-Auth-Username / X-Auth-Password headers and a
# url-encoded remember_me=true body → {token, expires_at}. Every other call sends
# X-AUTH-TOKEN. On a first 401/403 the SDK drops the token and retries once; so do we.
#
# Endpoints used here — GET  /api/client/services, /api/client/pickup-points,
#                            /api/client/lockers, /api/client/ooh-locations,
#                            /api/geolocation/city, /api/client/awb/{awb}/status,
#                            /api/awb/download/{awb}/{A4|A6}
#                       POST /api/awb/estimate-cost, /api/awb
#                       DELETE /api/awb/{awb}
# Service codes: 24 = door delivery, LN = easybox locker, PP = PUDO point,
# XB / XL = crossborder door / locker (RO, HU, BG).
#
# 💱 THE MONEY TRAP. The BG instance quotes and collects in BGN. Their own plugin asks for
# BGN (CURRENCY_MAPPER['BG']) and converts to EUR in PHP at the fixed peg. Our stack is EUR
# end to end, so this adapter converts on BOTH edges with Decimal at the official
# 1.95583 rate, and REFUSES any response whose currency it did not ask for rather than
# assuming. Reading a BGN quote as EUR would price every parcel at roughly half — silently,
# on the 55–60 % of orders that are COD.
#
# 🔒 clientInternalReference is unique FOREVER, even after the AWB is cancelled (their FAQ,
# item 4). Their advice is to append a suffix and retry. WE MUST NOT: a suffix on retry
# books a SECOND AWB for one order — two labels, two бона, two COD collections. So the
# reference is the order ref verbatim, which turns it into the idempotency key: "not
# unique" means ALREADY BOOKED, go and read it, not "try again under another name".
#
# ⚠ VERIFY-LIVE — nothing below has been RESULT-verified, because the API credentials are
# not issued yet. Before the first real label, confirm on the test account:
#   1. that /api/awb/estimate-cost really answers in BGN on api.sameday.bg (post-euro this
#      may have moved; the adapter will raise rather than guess if it has),
#   2. that cashOnDelivery raises the estimate the same way it raises the booking — quote
#      and booking must carry identical COD semantics (the Speedy incident),
#   3. the service ids behind codes 24 / LN / PP on this contract,
#   4. the default pickup point id, and that the city/county strings for Свищов resolve.
# ======================================================================================
class Sameday(Courier):
    name = "sameday"

    # Bulgaria's euro conversion rate is fixed by law, not by a market feed.
    BGN_PER_EUR = Decimal("1.95583")
    API_CURRENCY = "BGN"          # what the BG instance speaks (see the money trap above)

    SERVICE_DOOR = "24"
    SERVICE_LOCKER = "LN"
    SERVICE_PUDO = "PP"

    PACKAGE_PARCEL = 0            # PackageType::PARCEL
    AWB_PAYMENT_CLIENT = 1        # AwbPaymentType::CLIENT — the shop pays the courier
    COD_COLLECTOR_CLIENT = 1      # CodCollectorType::CLIENT — COD comes back to us
    PERSON_INDIVIDUAL = 0

    def __init__(self):
        super().__init__()
        self._tok = None
        self._tok_exp = 0.0
        self._svc = None
        self._pp = None
        self._points_by_id = None

    # --- money ---------------------------------------------------------------------
    @classmethod
    def _to_bgn(cls, eur):
        """EUR → BGN at the statutory peg. Decimal throughout: a COD amount that drifts by
        a cent is a cash discrepancy the office has to reconcile by hand."""
        return float((Decimal(str(eur)) * cls.BGN_PER_EUR).quantize(Decimal("0.01"),
                                                                    rounding=ROUND_HALF_UP))

    @classmethod
    def _to_eur(cls, bgn):
        return float((Decimal(str(bgn)) / cls.BGN_PER_EUR).quantize(Decimal("0.01"),
                                                                    rounding=ROUND_HALF_UP))

    @classmethod
    def _price_eur(cls, amount, currency):
        """A quoted amount in whatever Sameday answered → EUR. Unknown currency raises:
        the alternative is quoting half price on every parcel and finding out at payout."""
        cur = str(currency or "").upper()
        if cur == CURRENCY:
            return round(float(amount), 2)
        if cur == cls.API_CURRENCY:
            return cls._to_eur(amount)
        raise CourierError(
            f"Sameday: върна цена във валута {cur or '(празна)'}, а не BGN или EUR — "
            f"не приемаме сумата, докато не се провери курсът.")

    # --- transport -----------------------------------------------------------------
    def _authenticate(self):
        self._require()
        body = urllib.parse.urlencode({"remember_me": "true"}).encode()
        req = urllib.request.Request(
            self.base + "/api/authenticate", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json",
                     "User-Agent": "DarlingPanel/1.0 (+https://darlinghome.bg)",
                     "X-Auth-Username": self.user, "X-Auth-Password": self.password})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                out = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise CourierError(f"Sameday: неуспешен вход HTTP {e.code}: "
                               f"{e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:
            raise CourierError(f"Sameday мрежова грешка при вход: {e}")
        token = out.get("token")
        if not token:
            raise CourierError(f"Sameday: входът не върна token ({str(out)[:160]}).")
        self._tok = token
        # expires_at is an ISO timestamp; we do not parse it, we just re-authenticate on
        # the first 401 the way their own SDK does. A clock we do not control is not a
        # thing to depend on.
        self._tok_exp = 0.0
        return token

    def _call(self, path, payload=None, method=None, params=None, raw=False):
        """One request, with a single re-auth retry on 401/403 (SDK parity)."""
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
        data = urllib.parse.urlencode(_form_pairs(payload)).encode() if payload is not None else None
        for attempt in (1, 2):
            token = self._tok or self._authenticate()
            req = urllib.request.Request(
                url, data=data, method=method or ("POST" if data is not None else "GET"),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Accept": "application/json",
                         "User-Agent": "DarlingPanel/1.0 (+https://darlinghome.bg)",
                         "X-AUTH-TOKEN": token})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                return body if raw else (json.loads(body.decode("utf-8", "replace")) if body else {})
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 1:
                    self._tok = None
                    continue
                text = e.read().decode("utf-8", "replace")
                detail = self._error_detail(text)
                if e.code == 422 and "clientInternalReference" in text:
                    raise CourierError(
                        "Sameday: този номер на поръчка ВЕЧЕ има издаден товарителница "
                        "(clientInternalReference не е уникален). НЕ добавяй суфикс — "
                        "това би издало втора товарителница за същата поръчка. Провери "
                        "съществуващата в eAWB.")
                raise CourierError(f"Sameday HTTP {e.code}: {detail}")
            except Exception as e:
                raise CourierError(f"Sameday мрежова грешка: {e}")

    @staticmethod
    def _error_detail(text):
        """Pull the fields that actually failed out of a Sameday validation body.

        Their 400 is a tree of field names, nearly all of them EMPTY, and the ones carrying
        real messages sit well past the first 300 characters. Truncating the raw body
        therefore hides the diagnosis every single time: the locker failure read as a list of
        empty objects and took a urlopen spy to identify. Falls back to the raw text when the
        shape is not what we expect, because a body we cannot parse is still evidence.
        """
        try:
            d = json.loads(text)
        except Exception:
            return text[:300]
        errs = d.get("errors") if isinstance(d, dict) else None
        if not isinstance(errs, dict):
            return text[:300]
        bad = {}

        def kids(node):
            """Children are a dict of field names, or a LIST for collections like parcels."""
            c = node.get("children")
            if isinstance(c, dict):
                return list(c.items())
            if isinstance(c, list):
                return list(enumerate(c))
            return []

        def walk(prefix, node):
            if not isinstance(node, dict):
                return
            msgs = node.get("errors")
            if msgs:
                bad[prefix] = msgs
            for k, v in kids(node):
                walk(f"{prefix}.{k}" if prefix else str(k), v)

        try:
            for k, v in kids(errs):
                walk(str(k), v)
        except Exception:
            # This function exists to EXPLAIN a failure. If it cannot, it must hand back the
            # raw body rather than raise a second, unrelated error over the top of the first.
            return text[:300]
        parts = [f"{k}: " + "; ".join(str(m) for m in v) for k, v in bad.items()]
        top = errs.get("errors") or []
        if top:
            parts.append("; ".join(str(m) for m in top))
        if not parts:
            return text[:300]
        return (str(d.get("message") or "").strip() + " — " + " | ".join(parts))[:800]

    def _pages(self, path, params=None):
        """Concatenate a paginated GET (data + currentPage/pages)."""
        rows, page = [], 1
        while True:
            out = self._call(path, params=dict(params or {}, page=page, countPerPage=200))
            rows.extend(out.get("data") or [])
            try:
                last = int(out.get("pages") or 1)
            except (TypeError, ValueError):
                last = 1
            if page >= last or not out.get("data"):
                return rows
            page += 1

    # --- nomenclature ---------------------------------------------------------------
    def _services(self):
        if self._svc is None:
            self._svc = self._pages("/api/client/services")
        return self._svc

    def _service_id(self, code):
        """Contract service id for a Sameday service code. Fails loud: booking under the
        wrong service is a different price and a different delivery promise."""
        for s in self._services():
            if str(s.get("serviceCode") or "").upper() == code:
                return s.get("id")
        have = ", ".join(sorted({str(s.get("serviceCode") or "?") for s in self._services()}))
        raise CourierError(f"Sameday: услуга с код {code} не е налична по този договор "
                           f"(налични: {have or '(няма)'}).")

    def _pickup_point(self):
        """Sender pickup point id. SAMEDAY_PICKUP_POINT_ID wins; otherwise the one the
        account marks as default. Their guide is explicit that exactly one must be default."""
        if self._pp:
            return self._pp
        forced = _cred("SAMEDAY_PICKUP_POINT_ID")
        if forced:
            self._pp = forced
            return self._pp
        rows = self._pages("/api/client/pickup-points")
        for p in rows:
            if p.get("defaultPickupPoint"):
                self._pp = p.get("id")
                return self._pp
        raise CourierError("Sameday: няма пункт за приемане по подразбиране в акаунта — "
                           "задай го в eAWB или сложи SAMEDAY_PICKUP_POINT_ID в ~/.hermes/.env.")

    def _points(self):
        """id -> pickup point, built once per adapter instance.

        offices() is two paged endpoints and ~2.5k rows; a quote must not pay for that every
        time it prices a basket. Keyed on str(id) because the locker list and the OOH list
        number their points in the same space but do not agree on the type.
        """
        if self._points_by_id is None:
            self._points_by_id = {str(o["id"]): o for o in self.offices()
                                  if o.get("id") is not None}
        return self._points_by_id

    def _point(self, office_id):
        """The chosen pickup point, or a refusal. NEVER a blank dict: every caller below uses
        this to fill fields Sameday validates, and a falsy default would put us straight back
        to the 400 this exists to prevent."""
        p = self._points().get(str(office_id))
        if p is None:
            raise CourierError(f"Sameday: няма автомат или офис с id {office_id} "
                               "в техния списък.")
        return p

    def _county_for(self, city):
        """Sameday needs the окръг/област next to the city. Their nomenclature knows it, so
        we ask instead of making the operator type it."""
        rows = self._pages("/api/geolocation/city", {"name": city})
        key = "".join(str(city or "").split()).casefold()
        for c in rows:
            if "".join(str(c.get("name") or "").split()).casefold() == key:
                county = c.get("county") or {}
                return county.get("name") if isinstance(county, dict) else county
        return None

    # --- payload --------------------------------------------------------------------
    def _recipient(self, shp: "Shipment"):
        """Their support list names the two fields that fail most often: phone and e-mail
        are mandatory on the AWB, so we refuse before the call instead of after."""
        if not shp.phone:
            raise CourierError("Sameday: телефонът на получателя е задължителен "
                               "(awbRecipient.phone).")
        a = shp.address or {}
        city = a.get("city") or ""
        county = a.get("county") or a.get("region") or ""
        postcode = str(a.get("postcode") or a.get("postal_code") or "")
        street = ", ".join(x for x in (a.get("street"), a.get("street_no")) if x)
        if shp.office_id:
            # Sameday validates the recipient address block even when the parcel goes to a
            # locker: `address` must be >= 3 characters, and city/county must be resolvable
            # (a postal code resolves both). Until 2026-08-07 this sent "-" and two blanks,
            # and EVERY locker quote came back 400 Validation Failed. Copy it off the chosen
            # point, which is what their own plugin does and what the old comment already said.
            p = self._point(shp.office_id)
            street = street or str(p.get("address") or "")
            city = city or str(p.get("city") or "")
            postcode = postcode or str(p.get("postcode") or "")
            # The county is NOT optional for a locker either, and the postal code does not
            # stand in for it - measured against the live API on 2026-08-07. Their own record
            # for the point carries it, so there is no second lookup to get wrong.
            _c = p.get("county")
            if isinstance(_c, dict):
                _c = _c.get("name")
            county = county or str(_c or "")
            if len(street.strip()) < 3:
                # Some points carry a name but a stub address. The name is a real location
                # string; "-" is not, and it is the exact value their validator rejected.
                street = str(p.get("name") or "").strip()
        if not shp.office_id:
            if not city:
                raise CourierError("Sameday: липсва населено място на получателя.")
            if not street:
                raise CourierError("Sameday: липсва адрес на получателя.")
            county = county or self._county_for(city) or ""
            if not county:
                raise CourierError(f"Sameday: не намерих областта за „{city}“ в номенклатурата им.")
        out = {"name": shp.recipient, "phoneNumber": shp.phone, "address": street or "-",
               "personType": self.PERSON_INDIVIDUAL, "cityString": city,
               "countyString": county, "email": shp.email or ""}
        if postcode:
            # Optional, and only sent when we have one: their validator accepts a postal code
            # INSTEAD of county/city, which is the sturdier of the two ways to satisfy it.
            out["postalCode"] = postcode
        return out

    def _body(self, shp: "Shipment", reference=None):
        """ONE builder for the quote and the booking, so the two cannot disagree about COD
        — the Speedy incident, which cost a fee per order until someone reconciled a payout."""
        # PUDO points are service PP, lockers are LN. This sent LN for both, which prices
        # and books a counter drop as an easybox drop.
        kind = self._point(shp.office_id).get("kind") if shp.office_id else None
        if not shp.office_id:
            code = self.SERVICE_DOOR
        else:
            code = self.SERVICE_PUDO if kind == "pudo" else self.SERVICE_LOCKER
        body = {
            "pickupPoint": self._pickup_point(),
            "contactPerson": None,
            "packageType": self.PACKAGE_PARCEL,
            "packageNumber": max(1, shp.parcels),
            "packageWeight": shp.weight_kg,
            "service": self._service_id(code),
            "awbPayment": self.AWB_PAYMENT_CLIENT,
            "cashOnDelivery": self._to_bgn(shp.cod_amount) if shp.cod_amount > 0 else 0,
            "insuredValue": self._to_bgn(shp.declared_value) if shp.declared_value else 0,
            "thirdPartyPickup": 0,
            "serviceTaxes": [],
            "awbRecipient": self._recipient(shp),
            "parcels": [{"weight": shp.weight_kg, "width": 20, "length": 30, "height": 20}],
            "currency": self.API_CURRENCY,
        }
        if reference is not None:
            body.update({
                "cashOnDeliveryReturns": self.COD_COLLECTOR_CLIENT,
                "clientInternalReference": reference,
                "observation": shp.note or "",
                "clientObservation": shp.contents or "",
            })
            if shp.office_id:
                # LN goes in lockerLastMile, PP in oohLastMile — same id space, different
                # field, and sending the wrong one is their documented "invalid
                # lockerLastMile" error. The kind comes from the point itself rather than
                # from a guess about the id.
                if kind == "pudo":
                    body["oohLastMile"] = shp.office_id
                else:
                    body["lockerLastMile"] = shp.office_id
        return body

    # --- interface -------------------------------------------------------------------
    def ping(self):
        self._authenticate()
        pp = self._pickup_point()
        rows = [p for p in self._pages("/api/client/pickup-points") if str(p.get("id")) == str(pp)]
        row = rows[0] if rows else {}
        return {"ok": True, "sender_name": row.get("alias") or row.get("name") or "",
                "sender_place": ", ".join(x for x in (row.get("city"), row.get("address")) if x),
                "pickup_point_id": pp,
                "services": sorted({str(s.get("serviceCode") or "?") for s in self._services()})}

    def offices(self, city=None):
        """Easybox lockers plus PUDO points, in the shape the office picker expects."""
        out = []
        # `lon`, not `lng` — every adapter answers in the same shape or the office picker has
        # to special-case whichever carrier it is talking to.
        for row in self._pages("/api/client/lockers"):
            out.append({"id": row.get("lockerId"), "name": row.get("name"),
                        "city": row.get("city"), "address": row.get("address"),
                        "postcode": row.get("postalCode"), "kind": "locker",
                        # county rides along for the AWB: their validator wants it by name and
                        # a postal code does not resolve it on the BG instance, whatever the
                        # error message offers. Additive - other adapters simply omit it.
                        "county": row.get("county"),
                        "lat": _coord(row.get("lat")), "lon": _coord(row.get("lng"))})
        seen = {str(o["id"]) for o in out if o.get("id") is not None}
        for row in self._pages("/api/client/ooh-locations"):
            oid = row.get("oohId")
            if oid is None or str(oid) in seen:
                # The OOH list is a SUBSET VIEW of the same easyboxes, not a second network:
                # measured 2026-08-07, all 839 of its ids were already in the locker list and
                # every one had oohType 0. Appending them blind listed 839 points twice and
                # labelled the copy a counter, which prices and books an easybox as a PUDO.
                continue
            # oohType 0 is an easybox; anything else is a genuine out-of-home counter. Read
            # the flag rather than assuming the endpoint implies the kind.
            kind = "locker" if str(row.get("oohType")) == "0" else "pudo"
            out.append({"id": oid, "name": row.get("name"),
                        "city": row.get("city"), "address": row.get("address"),
                        "postcode": row.get("postalCode"), "kind": kind,
                        "county": row.get("county"),
                        "lat": _coord(row.get("lat")), "lon": _coord(row.get("lng"))})
        if city:
            key = "".join(str(city).split()).casefold()
            out = [o for o in out if "".join(str(o["city"] or "").split()).casefold() == key]
        return out

    def calculate(self, shp: "Shipment"):
        out = self._call("/api/awb/estimate-cost", self._body(shp))
        if "amount" not in out:
            raise CourierError(f"Sameday: оценката не върна сума ({str(out)[:160]}).")
        return {"price": self._price_eur(out["amount"], out.get("currency")),
                "currency": CURRENCY, "eta": out.get("time")}

    def create_shipment(self, shp: "Shipment", do_write=False):
        # The reference IS the idempotency key — see the note at the top of this class.
        reference = str(shp.order_ref or "").strip()
        if not reference:
            raise CourierError("Sameday: липсва номер на поръчка за clientInternalReference — "
                               "без него товарителницата не може да се съпостави с поръчка.")
        body = self._body(shp, reference=reference)
        if not do_write:
            return {"dry_run": True, "courier": "sameday", "payload": body}
        out = self._call("/api/awb", body)
        awb = out.get("awbNumber")
        if not awb:
            raise CourierError(f"Sameday: заявката не върна awbNumber ({str(out)[:160]}).")
        return {"courier": "sameday", "shipment_id": awb, "tracking_number": str(awb),
                "cod_amount": shp.cod_amount,
                "price": self._price_eur(out.get("awbCost") or 0, self.API_CURRENCY),
                "currency": CURRENCY, "label_ref": str(awb),
                "parcel_ids": [str(p.get("awbNumber") or p.get("position") or "")
                               for p in (out.get("parcels") or [])]}

    def label_pdf(self, shipment_id, fmt="A6"):
        fmt = str(fmt).upper()
        if fmt not in ("A4", "A6"):
            raise CourierError("Sameday: форматът на етикета е A4 или A6.")
        pdf = self._call(f"/api/awb/download/{shipment_id}/{fmt}", raw=True)
        if not pdf.startswith(b"%PDF"):
            raise CourierError(f"Sameday: изтеглянето не върна PDF ({pdf[:60]!r}).")
        return pdf

    def cancel(self, shipment_id, comment=""):
        return self._call(f"/api/awb/{shipment_id}", method="DELETE") or {"ok": True}

    def track(self, tracking_number):
        out = self._call(f"/api/client/awb/{tracking_number}/status")
        exp = out.get("expeditionStatus") or {}
        hist = out.get("expeditionHistory") or []
        return {"status": exp.get("status") or exp.get("statusLabel") or "",
                "status_bg": exp.get("statusLabel") or "",
                "delivered": bool(out.get("delivered")),
                "last_ts": exp.get("statusDate") or "",
                "events": [{"ts": e.get("statusDate") or "",
                            "text": str(e.get("statusLabel") or e.get("status") or "")}
                           for e in hist if isinstance(e, dict)]}


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
                                 else (o.get("city") or {}).get("name", "")) or "",
                        # strings on the wire. /offices returns only type=office on this
                        # account (183 of 183); their АПС lockers are a separate ?type=locker
                        # query, so nothing here is claimed to be one.
                        "lat": _coord(o.get("latitude")), "lon": _coord(o.get("longitude")),
                        "kind": "locker" if str(o.get("type") or "").lower() == "locker" else "office"})
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
