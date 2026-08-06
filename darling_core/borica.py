"""BORICA APGW / 3-D Secure protocol core.

Implements P-OM-41 "Secure payments via Internet — instruction for virtual
merchants", version 7.0 / 17.04.2026, signing scheme MAC_GENERAL.

This module is the ONE place where the Borica wire format lives on the Python
side. It does no I/O, holds no credentials and has no framework dependency, so
it can be unit-gated against BORICA's own published vectors (see
tests/test_borica.py — five real signatures from BORICA's test terminal
V1800001 are verified byte-for-byte by that suite).

------------------------------------------------------------------------------
THE ONE THING THAT WILL BITE YOU  (spec pp. 26/30/49/50)
------------------------------------------------------------------------------
The signed string is a concatenation of <byte-length-in-UTF-8><value> per field,
in a fixed per-message order. A field that is missing or empty contributes a
single byte 0x2D ("-") with NO length prefix.

  REQUEST  strings END with an extra "-"  (the RFU placeholder).
  RESPONSE strings DO NOT.

Section 5 of the spec prints the response example strings *with* a trailing "-"
and claims "Signature = [true]".  That is a documentation error.  Five real
(macSourceValue, P_SIGN) pairs published in the same document verify against
BORICA's own APGW public key ONLY when the trailing RFU byte is omitted; none
verify with it.  BORICA's own PHP verification sample (spec §9.7) also lists the
MAC fields without RFU and passes a string with no trailing "-".  Trust the
signatures, not the tables.

`verify_response` therefore checks the canonical (no-RFU) form first and falls
back to the with-RFU form, reporting WHICH form matched. Accepting either is not
a weakening — both require BORICA's private key — but a run of ALT verdicts means
BORICA changed the scheme and must raise an ops issue rather than pass silently.

------------------------------------------------------------------------------
Money rules in force here
------------------------------------------------------------------------------
* A payment is settled if and only if ACTION == "0" AND RC == "00". Anything
  else is not money in the till.
* A negative RC is NOT final. It can flip to success inside GUARDTIME (15 min).
  Poll TRTYPE=90 and only treat the answer as final after GUARDTIME + 1 min.
  `classify()` returns three states for exactly this reason — never a boolean.
* A signature that does not verify means the message did not come from BORICA.
  Treat it as no message at all: do not read the amount off it, do not mark the
  order paid, do not show the customer a result derived from it.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as _dcfield
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)
from cryptography.x509 import load_pem_x509_certificate

__all__ = [
    "FINAL_AFTER_SECONDS",
    "GATEWAY_URLS",
    "GUARDTIME_SECONDS",
    "PORTAL_URLS",
    "TRTYPE_COMPLETION",
    "TRTYPE_PREAUTH",
    "TRTYPE_PREAUTH_REVERSAL",
    "TRTYPE_REVERSAL",
    "TRTYPE_SALE",
    "TRTYPE_STATUS",
    "BoricaError",
    "Outcome",
    "Verdict",
    "auth_step_text",
    "build_completion",
    "build_preauth",
    "build_preauth_reversal",
    "build_reversal",
    "build_sale",
    "build_status_check",
    "classify",
    "eci_text",
    "format_amount",
    "format_order",
    "load_private_key",
    "load_public_key",
    "m_info",
    "mac_source",
    "new_nonce",
    "rc_text",
    "request_sign_fields",
    "response_sign_fields",
    "sign_request",
    "utc_timestamp",
    "verify_response",
]

# --------------------------------------------------------------------------
# Transaction types (spec §4)
# --------------------------------------------------------------------------
TRTYPE_SALE = "1"                 # payment
TRTYPE_PREAUTH = "12"             # block funds, settle later with a Completion
TRTYPE_COMPLETION = "21"          # settle a pre-authorisation (amount <= preauth)
TRTYPE_PREAUTH_REVERSAL = "22"    # cancel a pre-authorisation (amount == preauth)
TRTYPE_REVERSAL = "24"            # refund a Sale (amount <= sale), within 30 days
TRTYPE_STATUS = "90"              # status check, last 24 h only

GUARDTIME_SECONDS = 900           # 15 min; APGW rejects older TIMESTAMPs
FINAL_AFTER_SECONDS = 960         # GUARDTIME + 1 min buffer (spec §10.8.1)

GATEWAY_URLS = {
    "test": "https://3dsgate-dev.borica.bg/cgi-bin/cgi_link",
    "prod": "https://3dsgate.borica.bg/cgi-bin/cgi_link",
}
PORTAL_URLS = {
    "test": "https://3dsgate-dev.borica.bg/mwp_cert",
    "prod": "https://3dsgate.borica.bg/mwp/static/",
}

# Browser-redirected transactions (the cardholder's browser POSTs the form).
# Everything else is a direct server-to-server call from us to APGW.
BROWSER_TRTYPES = frozenset({TRTYPE_SALE, TRTYPE_PREAUTH})


class BoricaError(ValueError):
    """A request could not be built, or a response could not be parsed."""


# --------------------------------------------------------------------------
# Signing string (spec §5.1, Table 10)
# --------------------------------------------------------------------------
_RFU = "-"          # single byte 0x2D, stands in for RFU and for any empty field

# Fields signed in the REQUEST, per transaction type. RFU is appended by
# request_sign_fields() for every type except 90, which has no RFU at all.
_REQ_BASE = ("TERMINAL", "TRTYPE", "AMOUNT", "CURRENCY", "ORDER", "TIMESTAMP", "NONCE")
_REQ_FIELDS = {
    TRTYPE_SALE: _REQ_BASE,
    TRTYPE_PREAUTH: _REQ_BASE,
    TRTYPE_COMPLETION: _REQ_BASE,
    TRTYPE_PREAUTH_REVERSAL: _REQ_BASE,
    TRTYPE_REVERSAL: _REQ_BASE,
    TRTYPE_STATUS: ("TERMINAL", "TRTYPE", "ORDER", "NONCE"),
}

# Fields signed in the RESPONSE — identical for every transaction type, and
# WITHOUT a trailing RFU (see module docstring).
_RESP_FIELDS = (
    "ACTION", "RC", "APPROVAL", "TERMINAL", "TRTYPE", "AMOUNT", "CURRENCY",
    "ORDER", "RRN", "INT_REF", "PARES_STATUS", "ECI", "TIMESTAMP", "NONCE",
)


def request_sign_fields(trtype: str) -> tuple[str, ...]:
    """Ordered field names for the request signing string, RFU included."""
    trtype = str(trtype)
    try:
        base = _REQ_FIELDS[trtype]
    except KeyError:
        raise BoricaError(f"unsupported TRTYPE {trtype!r}") from None
    return base if trtype == TRTYPE_STATUS else base + ("RFU",)


def response_sign_fields() -> tuple[str, ...]:
    """Ordered field names for the response signing string (no RFU)."""
    return _RESP_FIELDS


def mac_source(fields: Sequence[str], values: Mapping[str, object]) -> str:
    """Build the MAC_GENERAL signing string.

    Each field contributes ``str(len(utf8_bytes)) + value``; a missing or empty
    field (and RFU always) contributes a single ``-``.
    """
    out = []
    for name in fields:
        if name == "RFU":
            out.append(_RFU)
            continue
        raw = values.get(name)
        text = "" if raw is None else str(raw)
        if text == "":
            out.append(_RFU)
        else:
            out.append(f"{len(text.encode('utf-8'))}{text}")
    return "".join(out)


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------
def load_private_key(pem: bytes | str, password: bytes | str | None = None):
    """Load our RSA signing key. Password-protected keys are supported and
    recommended (spec §5.2); the password comes from the environment, never
    from source or from a transcript."""
    if isinstance(pem, str):
        pem = pem.encode()
    if isinstance(password, str):
        password = password.encode()
    key = load_pem_private_key(pem, password=password or None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise BoricaError("Borica requires an RSA private key")
    if key.key_size < 2048:
        raise BoricaError(f"Borica requires a 2048-bit key, got {key.key_size}")
    return key


def load_public_key(pem: bytes | str):
    """Load BORICA's APGW public key. Accepts either the bare public key or the
    certificate BORICA publishes (MPI_OW_APGW_*.cer)."""
    if isinstance(pem, str):
        pem = pem.encode()
    if b"CERTIFICATE" in pem:
        return load_pem_x509_certificate(pem).public_key()
    return load_pem_public_key(pem)


# --------------------------------------------------------------------------
# Sign / verify (spec §9.6, §9.7 — openssl_sign/openssl_verify + ALGO_SHA256)
# --------------------------------------------------------------------------
def sign_request(fields: Mapping[str, object], private_key) -> str:
    """Return P_SIGN: uppercase hex of RSA PKCS#1 v1.5 / SHA-256 over the
    request MAC string. 2048-bit key -> 512 hex characters."""
    trtype = str(fields.get("TRTYPE") or "")
    src = mac_source(request_sign_fields(trtype), fields)
    sig = private_key.sign(src.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return binascii.hexlify(sig).decode().upper()


@dataclass(frozen=True)
class Verdict:
    """Three-state result of checking P_SIGN on an APGW response.

    ok       -- the message provably came from BORICA
    variant  -- "canonical" (no RFU, expected) | "rfu" (legacy/with RFU) | ""
    source   -- the exact string that was verified, for incident forensics
    """
    ok: bool
    variant: str
    source: str

    @property
    def needs_alert(self) -> bool:
        """True when the signature was good but only in the non-canonical form.
        Payments still process; ops must be told the scheme moved."""
        return self.ok and self.variant == "rfu"


def verify_response(fields: Mapping[str, object], public_key) -> Verdict:
    """Check P_SIGN on an APGW response. Never raises on a bad signature —
    callers must branch on ``.ok`` and treat False as 'no message received'."""
    psign = str(fields.get("P_SIGN") or "").strip()
    if not psign:
        return Verdict(False, "", "")
    psign = re.sub(r"\s+", "", psign)
    try:
        sig = binascii.unhexlify(psign)
    except (binascii.Error, ValueError):
        return Verdict(False, "", "")
    base = mac_source(_RESP_FIELDS, fields)
    for variant, src in (("canonical", base), ("rfu", base + _RFU)):
        try:
            public_key.verify(sig, src.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            continue
        return Verdict(True, variant, src)
    return Verdict(False, "", base)


# --------------------------------------------------------------------------
# Field helpers
# --------------------------------------------------------------------------
def new_nonce() -> str:
    """16 unpredictable bytes, uppercase hex. Must be unique per terminal per
    24 h — os.urandom collisions are not a practical concern, but the caller
    still stores it, because NONCE is how a response is matched to an order."""
    return binascii.hexlify(os.urandom(16)).decode().upper()


def utc_timestamp(when: datetime | None = None) -> str:
    """YYYYMMDDHHMMSS in UTC. APGW rejects anything more than 15 minutes off its
    own clock, so the host running this MUST have working NTP."""
    when = when or datetime.now(UTC)
    if when.tzinfo is None:
        raise BoricaError("naive datetime; timestamps must be explicit UTC")
    return when.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def format_amount(amount) -> str:
    """Amount with a decimal point and exactly two decimals, e.g. '9.00'.

    Money never round-trips through float here: pass a Decimal or a string.
    A float argument is refused rather than silently binary-rounded.
    """
    if isinstance(amount, float):
        raise BoricaError("pass amounts as Decimal or str, never float")
    try:
        dec = Decimal(str(amount).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        raise BoricaError(f"invalid amount {amount!r}") from None
    if dec <= 0:
        raise BoricaError(f"amount must be positive, got {dec}")
    return str(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def format_order(order) -> str:
    """ORDER: exactly 6 digits, right aligned, zero padded (spec Table 1).
    Must be unique for the terminal within 24 h — the caller owns that."""
    text = str(order).strip()
    if not text.isdigit():
        raise BoricaError(f"ORDER must be digits only, got {order!r}")
    if len(text) > 6:
        raise BoricaError(f"ORDER must fit in 6 digits, got {text!r}")
    return text.rjust(6, "0")


def _bor_order_id(order6: str, suffix: str) -> str:
    """AD.CUST_BOR_ORDER_ID = ORDER + up to 16 chars, digits/Latin, no ';'.

    This is the value that reaches the accountant: it travels in BORICA's
    financial files and is what the bank statement is reconciled against.
    """
    clean = re.sub(r"[^0-9A-Za-z]", "", _latin(str(suffix), 64))[:16]
    return order6 + clean


# Bulgarian -> Latin, one entry per case. Kept as an explicit table rather than
# a lower()-then-restore trick: multi-character mappings (ц->ts, щ->sht) change
# the string length, so any position-based re-capitalisation silently corrupts
# the result. The PHP twin uses the identical table.
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht", "ъ": "a",
    "ь": "y", "ю": "yu", "я": "ya",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ж": "Zh",
    "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N",
    "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F",
    "Х": "H", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Sht", "Ъ": "A",
    "Ь": "Y", "Ю": "Yu", "Я": "Ya",
}


def _latin(text: str, limit: int) -> str:
    """3-DS v2 cardholder fields are Latin-only; transliterate and clip."""
    if not text:
        return ""
    out = "".join(_CYR2LAT.get(ch, ch) for ch in str(text))
    out = "".join(ch for ch in out if 0x20 <= ord(ch) <= 0x7E)
    return re.sub(r"\s+", " ", out).strip()[:limit]


def m_info(
    cardholder_name: str = "",
    email: str = "",
    phone: str = "",
    bill_address: str = "",
    ship_address: str = "",
    force_challenge: bool = False,
) -> str:
    """Base64 of the EMV 3-DS v2 data set (spec Table 1 / §3.1).

    BORICA marks M_INFO mandatory for Sale and Pre-authorisation: cardholder
    name plus e-mail and/or phone, plus a billing and/or shipping address.
    Richer data means the issuer is more likely to authenticate frictionlessly,
    which directly raises the conversion rate on the card path.

    ``phone`` is a Bulgarian number in any local form; it is split into the
    {cc, subscriber} shape BORICA expects.
    """
    data: dict[str, object] = {}
    if force_challenge:
        # threeDSRequestorChallengeInd 04 = merchant requests SCA outright.
        data["threeDSRequestorChallengeInd"] = "04"
    name = _latin(cardholder_name, 45)
    if name:
        data["cardholderName"] = name
    if email:
        data["email"] = str(email).strip()[:254]
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits:
        if digits.startswith("00359"):
            digits = digits[5:]
        elif digits.startswith("359"):
            digits = digits[3:]
        elif digits.startswith("0"):
            digits = digits[1:]
        if digits:
            data["mobilePhone"] = {"cc": "359", "subscriber": digits}
    bill = _latin(bill_address, 50)
    ship = _latin(ship_address, 50)
    if bill:
        data["billAddrLine1"] = bill
    if ship:
        data["shipAddrLine1"] = ship
    if not data:
        return ""
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(blob.encode("utf-8")).decode()


# --------------------------------------------------------------------------
# Request builders
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Merchant:
    """Static per-terminal configuration. Values come from the acquirer with
    the contract; none of them are secrets, but TERMINAL/MERCHANT differ
    between the test and production environments."""
    terminal: str            # TID, 8 chars, e.g. V1800001
    merchant_id: str         # MID, 10 chars
    name: str                # shown to the cardholder on BORICA's page
    url: str = ""
    email: str = ""
    country: str = "BG"
    gmt: str = "+03"         # +03 summer, +02 winter — display only
    currency: str = "EUR"    # the terminal is single-currency; must match
    lang: str = "BG"

    def base(self, trtype: str, order6: str, timestamp: str, nonce: str) -> dict:
        out = {
            "TERMINAL": self.terminal,
            "TRTYPE": trtype,
            "ORDER": order6,
            "TIMESTAMP": timestamp,
            "NONCE": nonce,
            "MERCHANT": self.merchant_id,
            "MERCH_NAME": self.name,
            "COUNTRY": self.country,
            "MERCH_GMT": self.gmt,
            "LANG": self.lang,
        }
        if self.url:
            out["MERCH_URL"] = self.url
        if self.email:
            out["EMAIL"] = self.email
        return out


def _finish(fields: dict, private_key) -> dict:
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    fields["P_SIGN"] = sign_request(fields, private_key)
    return fields


def _money_request(
    trtype: str, merchant: Merchant, order, amount, description: str,
    private_key, *, merchant_order_id: str = "", timestamp: str | None = None,
    nonce: str | None = None, rrn: str = "", int_ref: str = "",
    minfo: str = "", extra: Mapping[str, object] | None = None,
) -> dict:
    order6 = format_order(order)
    fields = merchant.base(trtype, order6, timestamp or utc_timestamp(), nonce or new_nonce())
    fields["AMOUNT"] = format_amount(amount)
    fields["CURRENCY"] = merchant.currency
    fields["DESC"] = (description or "")[:50]
    fields["ADDENDUM"] = "AD,TD"
    fields["AD.CUST_BOR_ORDER_ID"] = _bor_order_id(order6, merchant_order_id or order6)
    if rrn:
        fields["RRN"] = rrn
    if int_ref:
        fields["INT_REF"] = int_ref
    if minfo:
        fields["M_INFO"] = minfo
    if extra:
        fields.update(extra)
    return _finish(fields, private_key)


def build_sale(merchant: Merchant, order, amount, description, private_key, **kw) -> dict:
    """TRTYPE=1. The returned dict is rendered as a self-submitting HTML form
    and POSTed to the gateway BY THE CARDHOLDER'S BROWSER, never by us."""
    return _money_request(TRTYPE_SALE, merchant, order, amount, description, private_key, **kw)


def build_preauth(merchant: Merchant, order, amount, description, private_key, **kw) -> dict:
    """TRTYPE=12. Blocks the amount; must be followed by a Completion."""
    return _money_request(TRTYPE_PREAUTH, merchant, order, amount, description, private_key, **kw)


def build_reversal(merchant: Merchant, order, amount, description, private_key,
                   *, rrn: str, int_ref: str, **kw) -> dict:
    """TRTYPE=24, refund of a Sale. ORDER/RRN/INT_REF are the ORIGINAL sale's.
    Amount may be lower than the sale; only ONE reversal per sale, ever, and
    only within 30 days. Server-to-server."""
    if not rrn or not int_ref:
        raise BoricaError("reversal needs RRN and INT_REF from the original sale")
    return _money_request(TRTYPE_REVERSAL, merchant, order, amount, description,
                          private_key, rrn=rrn, int_ref=int_ref, **kw)


def build_completion(merchant: Merchant, order, amount, description, private_key,
                     *, rrn: str, int_ref: str, **kw) -> dict:
    """TRTYPE=21, settle a pre-authorisation. Amount <= the pre-auth amount."""
    if not rrn or not int_ref:
        raise BoricaError("completion needs RRN and INT_REF from the pre-authorisation")
    return _money_request(TRTYPE_COMPLETION, merchant, order, amount, description,
                          private_key, rrn=rrn, int_ref=int_ref, **kw)


def build_preauth_reversal(merchant: Merchant, order, amount, description, private_key,
                           *, rrn: str, int_ref: str, **kw) -> dict:
    """TRTYPE=22, cancel a pre-authorisation. Amount MUST equal the pre-auth."""
    if not rrn or not int_ref:
        raise BoricaError("pre-auth reversal needs RRN and INT_REF")
    return _money_request(TRTYPE_PREAUTH_REVERSAL, merchant, order, amount, description,
                          private_key, rrn=rrn, int_ref=int_ref, **kw)


def build_status_check(merchant: Merchant, order, original_trtype: str, private_key,
                       *, nonce: str | None = None) -> dict:
    """TRTYPE=90. Answers in JSON, covers the last 24 h only. This is the
    reconciliation path: it is how an order whose customer closed the browser
    still gets a truthful status."""
    fields = {
        "TERMINAL": merchant.terminal,
        "TRTYPE": TRTYPE_STATUS,
        "ORDER": format_order(order),
        "TRAN_TRTYPE": str(original_trtype),
        "NONCE": nonce or new_nonce(),
    }
    return _finish(fields, private_key)


# --------------------------------------------------------------------------
# Response semantics (spec §3.2, §4.2, §10.8.1)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Outcome:
    """What a verified APGW response means for the order.

    state is one of:
      "paid"     -- ACTION 0 and RC 00. Final. Money is authorised.
      "declined" -- final refusal (positive RC other than 00, or a duplicate).
      "pending"  -- not final yet; re-check with TRTYPE=90 after FINAL_AFTER_SECONDS.

    Never collapse this to a boolean: "not paid" and "not paid yet" have
    opposite correct actions (tell the customer vs. wait and poll).
    """
    state: str
    action: str
    rc: str
    message: str
    final: bool
    amount: str = ""
    rrn: str = ""
    int_ref: str = ""
    approval: str = ""
    card: str = ""
    card_brand: str = ""
    eci: str = ""
    auth_step_res: str = ""
    cardholder_info: str = ""
    raw: Mapping[str, object] = _dcfield(default_factory=dict)

    @property
    def paid(self) -> bool:
        return self.state == "paid"


def classify(fields: Mapping[str, object]) -> Outcome:
    """Interpret an APGW response. Call ONLY after verify_response().ok."""
    action = str(fields.get("ACTION") or "").strip()
    rc = str(fields.get("RC") or "").strip()
    msg = str(fields.get("STATUSMSG") or "").strip() or rc_text(rc)

    if action == "0" and rc == "00":
        state, final = "paid", True
    elif rc.startswith("-"):
        # APGW-side codes are provisional inside GUARDTIME (spec §10.8.1).
        state, final = "pending", False
    elif rc == "":
        state, final = "pending", False
    elif action == "21":
        # Soft decline; APGW itself retries with SCA. Not our decision yet.
        state, final = "pending", False
    else:
        # Positive RC from the issuer's host — final refusal.
        state, final = "declined", True

    return Outcome(
        state=state, action=action, rc=rc, message=msg, final=final,
        amount=str(fields.get("AMOUNT") or ""),
        rrn=str(fields.get("RRN") or ""),
        int_ref=str(fields.get("INT_REF") or ""),
        approval=str(fields.get("APPROVAL") or ""),
        card=str(fields.get("CARD") or ""),
        card_brand=str(fields.get("CARD_BRAND") or ""),
        eci=str(fields.get("ECI") or ""),
        auth_step_res=str(fields.get("AUTH_STEP_RES") or ""),
        # The spec is emphatic: if the issuer's ACS sent text for the
        # cardholder, the merchant MUST show it to them.
        cardholder_info=str(fields.get("CARDHOLDERINFO") or ""),
        raw=dict(fields),
    )


# APGW-side codes (spec Table 23) and the issuer ISO-8583 codes we actually see
# (Table 24). Bulgarian, because this text reaches the office.
_RC_BG = {
    "00": "Успешна трансакция",
    "01": "Свържете се с издателя на картата",
    "04": "Картата е задържана",
    "05": "Отказана от издателя",
    "06": "Грешка",
    "12": "Невалидна трансакция",
    "13": "Невалидна сума",
    "14": "Няма такава карта",
    "15": "Няма такъв издател",
    "17": "Отказано от клиента",
    "30": "Грешка във формата",
    "41": "Изгубена карта",
    "43": "Открадната карта",
    "51": "Недостатъчна наличност",
    "54": "Изтекла карта",
    "55": "Грешен ПИН",
    "57": "Трансакцията не е разрешена за картодържателя",
    "58": "Терминалът не е активиран",
    "59": "Съмнение за измама",
    "62": "Ограничена карта",
    "65": "Изисква се засилена автентикация",
    "1A": "Изисква се засилена автентикация",
    "85": "Няма причина за отказ",
    "88": "Криптографска грешка",
    "89": "Неуспешна автентикация",
    "91": "Издателят е недостъпен",
    "95": "Трансакцията не е намерена",
    "96": "Системна грешка",
    "-1": "Незапълнено задължително поле в заявката",
    "-2": "Заявката съдържа поле с некоректно име",
    "-3": "Авторизационният хост не отговаря",
    "-4": "Няма връзка с авторизационния хост",
    "-5": "Грешка във връзката с авторизационния хост",
    "-6": "Грешка в конфигурацията на APGW",
    "-7": "Неправилен формат на отговора от авторизационния хост",
    "-10": "Грешка в поле „Сума“",
    "-11": "Грешка в поле „Валута“",
    "-12": "Грешка в поле „Идентификатор на търговеца“",
    "-13": "Неправилен IP адрес на търговеца",
    "-15": "Грешка в поле RRN",
    "-16": "В момента се изпълнява друга трансакция на терминала",
    "-17": "Отказан достъп — грешка при проверка на P_SIGN",
    "-19": "Неуспешна автентикация на картодържателя",
    "-20": "Просрочена трансакция — разминаване във времето над 15 минути",
    "-21": "Трансакцията вече е изпълнена",
    "-22": "Невалидни данни за автентикация",
    "-23": "Невалиден контекст на трансакцията",
    "-24": "Несъответствие в данните на трансакцията",
    "-25": "Потвърждението е отказано от картодържателя",
    "-26": "Невалиден BIN на картата",
    "-27": "Невалидно име на търговеца",
    "-28": "Невалидно допълнително поле",
    "-29": "Невалиден отговор от ACS на издателя",
    "-30": "Трансакцията е отказана като измама",
    "-31": "Трансакцията е в процес на обработка",
    "-32": "Дублирана отказана трансакция",
    "-33": "Автентикацията на картодържателя е в процес",
    "-39": "Изчаква се потвърждение от картодържателя",
    "-40": "Трансакцията е в процес на обработка",
}

# ACTION codes (spec Table 2)
_ACTION_BG = {
    "0": "Успешно завършена",
    "1": "Открита дублирана трансакция",
    "2": "Отказана от издателя",
    "3": "Грешка при обработка",
    "6": "Дублирана, отказана",
    "7": "Дублирана, грешка в автентикацията",
    "8": "Дублирана, без отговор",
    "21": "Soft decline — повторен опит със засилена автентикация",
}


def rc_text(rc: str) -> str:
    return _RC_BG.get(str(rc).strip(), f"Код {rc}")


def action_text(action: str) -> str:
    return _ACTION_BG.get(str(action).strip(), f"ACTION {action}")


# ECI (spec Table 26/27). Visa values are also used for domestic transactions.
_ECI = {
    "05": ("visa", True, "Картодържател и издател са 3-D Secure; автентикацията е успешна"),
    "06": ("visa", False, "Картодържателят или издателят не е регистриран за 3-D Secure"),
    "07": ("visa", False, "Автентикацията е неуспешна или не е извършена"),
    "02": ("mastercard", True, "Картодържател и издател са 3-D Secure; автентикацията е успешна"),
    "01": ("mastercard", False, "Картодържателят или издателят не е регистриран за 3-D Secure"),
    "00": ("mastercard", False, "Автентикацията е неуспешна или не е извършена"),
}


def eci_text(eci: str, card_brand: str = "") -> tuple[bool, str]:
    """(fully_authenticated, human text). '' -> (False, unknown).

    Note the collision: ECI '00'/'01'/'02' are Mastercard, '05'/'06'/'07' are
    Visa (and Diners/Bcard, and all domestic transactions). There is no value
    that means different things across the two sets, so the brand is only used
    for the wording.
    """
    key = str(eci or "").strip()
    if key not in _ECI:
        return False, "Няма данни за 3-D Secure автентикация"
    _brand, authenticated, text = _ECI[key]
    return authenticated, text


# AUTH_STEP_RES (spec Table 25 + the AUTH_STEP_RES_TABLE annex, which also
# documents the 3-DS v1 values BORICA still returns for legacy cards).
_AUTH_STEP = {
    "VERES_N": (1, False, "Картодържателят не участва в 3-D Secure"),
    "VERES_U": (1, False, "Автентикацията не може да бъде извършена"),
    "PARES_Y": (1, True, "Успешна автентикация"),
    "PARES_A": (1, True, "Регистриран опит за автентикация"),
    "PARES_N": (1, False, "Неуспешна автентикация"),
    "PARES_U": (1, False, "Автентикацията не може да бъде извършена"),
    "ARES_Y": (2, True, "Успешна автентикация без предизвикателство"),
    "ARES_N": (2, False, "Отказана автентикация"),
    "ARES_R": (2, False, "Автентикацията е отхвърлена от издателя"),
    "RREQ_Y": (2, True, "Успешна автентикация със SCA"),
    "RREQ_N": (2, False, "Неуспешна автентикация със SCA"),
    "RREQ_U": (2, False, "Автентикацията не може да бъде извършена"),
}


def auth_step_text(value: str) -> tuple[int, bool, str]:
    """(3DS version, authenticated, Bulgarian text). Unknown -> (0, False, ...)."""
    return _AUTH_STEP.get(str(value or "").strip().upper(),
                          (0, False, "Няма данни за автентикация"))
