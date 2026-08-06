"""Gate for the BORICA protocol core.

The interesting half of this file is not "does our code do what we wrote" but
"does our code agree with BORICA". Every vector in borica-vectors.json is lifted
verbatim from P-OM-41 v7.0, and the five response vectors carry REAL signatures
produced by BORICA's test terminal V1800001, checked here against BORICA's own
published APGW public key. If BORICA ever changes the signing string, these fail
before any money moves.
"""
import base64
import json
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from darling_core import borica as b

VECTORS = json.loads((Path(__file__).with_name("borica-vectors.json")).read_text(encoding="utf-8"))


def _apgw_pubkey():
    pem = ("-----BEGIN PUBLIC KEY-----\n"
           + "\n".join(textwrap.wrap(VECTORS["apgw_test_pubkey_b64"], 64))
           + "\n-----END PUBLIC KEY-----\n")
    return b.load_public_key(pem)


@pytest.fixture(scope="module")
def apgw():
    return _apgw_pubkey()


@pytest.fixture(scope="module")
def our_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def merchant():
    return b.Merchant(terminal="V1800001", merchant_id="1600000001",
                      name="Дарлинг", url="https://darlinghome.bg",
                      email="orders@darlinghome.bg", currency="EUR")


# ---------------------------------------------------------------- MAC string
@pytest.mark.parametrize("vec", VECTORS["requests"], ids=lambda v: v["label"])
def test_request_mac_string_matches_borica(vec):
    """Our signing string is byte-identical to the one BORICA prints."""
    fields = vec["fields"]
    got = b.mac_source(b.request_sign_fields(fields["TRTYPE"]), fields)
    assert got == vec["mac_source"]


@pytest.mark.parametrize("vec", VECTORS["responses"],
                         ids=lambda v: f'trtype{v["fields"]["TRTYPE"]}_rc{v["fields"]["RC"]}')
def test_response_mac_string_matches_borica(vec):
    got = b.mac_source(b.response_sign_fields(), vec["fields"])
    assert got == vec["mac_source"]


def test_empty_and_missing_fields_become_one_dash():
    assert b.mac_source(("A", "B", "C"), {"A": "x", "B": ""}) == "1x--"
    assert b.mac_source(("A", "RFU"), {"A": None}) == "--"


def test_mac_length_prefix_counts_utf8_bytes_not_characters():
    # Cyrillic DESC is legal in the request; only fields in the signing string
    # matter, but the byte-length rule must be right if BORICA ever adds one.
    assert b.mac_source(("X",), {"X": "Дарлинг"}) == "14Дарлинг"


# --------------------------------------------------------- real signatures
@pytest.mark.parametrize("vec", VECTORS["responses"],
                         ids=lambda v: f'trtype{v["fields"]["TRTYPE"]}_rc{v["fields"]["RC"]}')
def test_real_borica_response_signature_verifies(vec, apgw):
    verdict = b.verify_response(vec["fields"], apgw)
    assert verdict.ok, "BORICA's own signature failed to verify"
    assert verdict.variant == "canonical", (
        "signature verified only in the with-RFU form; the scheme has moved")
    assert not verdict.needs_alert


def test_tampering_with_the_amount_breaks_verification(apgw):
    fields = dict(VECTORS["responses"][0]["fields"])
    fields["AMOUNT"] = "9999.00"
    assert b.verify_response(fields, apgw).ok is False


def test_tampering_with_rc_breaks_verification(apgw):
    """A declined response edited to look approved must not verify."""
    declined = next(v for v in VECTORS["responses"] if v["fields"]["RC"] != "00")
    fields = dict(declined["fields"])
    fields["ACTION"], fields["RC"] = "0", "00"
    assert b.verify_response(fields, apgw).ok is False


def test_missing_or_garbage_psign_is_not_ok(apgw):
    fields = dict(VECTORS["responses"][0]["fields"])
    for bad in ("", "   ", "ZZZZ", "00" * 10):
        fields["P_SIGN"] = bad
        assert b.verify_response(fields, apgw).ok is False


def test_signature_from_a_different_key_is_rejected(apgw, our_key):
    """The one that matters: an attacker who can POST to our BACKREF but does
    not hold BORICA's private key gets nothing."""
    fields = dict(VECTORS["responses"][0]["fields"])
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    forged = our_key.sign(b.mac_source(b.response_sign_fields(), fields).encode(),
                          padding.PKCS1v15(), hashes.SHA256())
    fields["P_SIGN"] = forged.hex().upper()
    assert b.verify_response(fields, apgw).ok is False


# ------------------------------------------------------------- sign/verify
def test_sign_request_roundtrips(our_key, merchant):
    req = b.build_sale(merchant, 42, Decimal("19.90"), "Тест", our_key)
    assert len(req["P_SIGN"]) == 512
    assert set(req["P_SIGN"]) <= set("0123456789ABCDEF")
    src = b.mac_source(b.request_sign_fields("1"), req)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    our_key.public_key().verify(bytes.fromhex(req["P_SIGN"]), src.encode(),
                                padding.PKCS1v15(), hashes.SHA256())


def test_signing_key_must_be_2048_bit():
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )
    pem = small.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    with pytest.raises(b.BoricaError):
        b.load_private_key(pem)


# ----------------------------------------------------------- field helpers
def test_amount_formatting():
    assert b.format_amount("9") == "9.00"
    assert b.format_amount(Decimal("0.295")) == "0.30"
    assert b.format_amount("1234,50") == "1234.50"
    with pytest.raises(b.BoricaError):
        b.format_amount(19.90)          # float is refused outright
    with pytest.raises(b.BoricaError):
        b.format_amount("0")
    with pytest.raises(b.BoricaError):
        b.format_amount("-5")


def test_order_formatting():
    assert b.format_order(123) == "000123"
    assert b.format_order("000123") == "000123"
    with pytest.raises(b.BoricaError):
        b.format_order("1234567")
    with pytest.raises(b.BoricaError):
        b.format_order("12a")


def test_nonce_is_32_uppercase_hex_and_unique():
    seen = {b.new_nonce() for _ in range(500)}
    assert len(seen) == 500
    for n in seen:
        assert len(n) == 32 and set(n) <= set("0123456789ABCDEF")


def test_timestamp_is_utc():
    when = datetime(2026, 8, 6, 14, 30, 5, tzinfo=UTC)
    assert b.utc_timestamp(when) == "20260806143005"
    # A naive datetime is refused rather than assumed to be UTC: a box in local
    # time would otherwise sign a TIMESTAMP three hours out and get RC -17.
    with pytest.raises(b.BoricaError):
        b.utc_timestamp(when.replace(tzinfo=None))


def test_bor_order_id_is_reconcilable_and_has_no_semicolon(our_key, merchant):
    req = b.build_sale(merchant, 7, "10.00", "Поръчка", our_key,
                       merchant_order_id="WC;1234-Дарлинг")
    ad = req["AD.CUST_BOR_ORDER_ID"]
    assert ad.startswith("000007")
    assert ";" not in ad
    assert len(ad) <= 22
    assert ad.isascii()


def test_minfo_is_base64_json_latin_only():
    raw = b.m_info(cardholder_name="Иван Петров", email="ivan@example.com",
                   phone="0893 361 670", bill_address="Свищов, ул. Дунав 3")
    data = json.loads(base64.b64decode(raw))
    assert data["cardholderName"] == "Ivan Petrov"
    assert data["mobilePhone"] == {"cc": "359", "subscriber": "893361670"}
    assert data["billAddrLine1"].isascii()
    assert "threeDSRequestorChallengeInd" not in data


@pytest.mark.parametrize("given", ["+359893361670", "00359893361670",
                                   "0893361670", "893361670"])
def test_minfo_phone_normalisation(given):
    data = json.loads(base64.b64decode(b.m_info(phone=given)))
    assert data["mobilePhone"] == {"cc": "359", "subscriber": "893361670"}


def test_minfo_can_force_sca():
    data = json.loads(base64.b64decode(b.m_info(cardholder_name="A B", force_challenge=True)))
    assert data["threeDSRequestorChallengeInd"] == "04"


def test_minfo_cardholder_name_clipped_to_45():
    data = json.loads(base64.b64decode(b.m_info(cardholder_name="Ivan " * 40)))
    assert len(data["cardholderName"]) <= 45


# ------------------------------------------------------------- transactions
def test_sale_carries_every_field_borica_marks_mandatory(our_key, merchant):
    req = b.build_sale(merchant, 1, "5.00", "Поръчка 1", our_key,
                       minfo=b.m_info(cardholder_name="Ivan Petrov", email="i@x.bg"))
    for name in ("TERMINAL", "TRTYPE", "AMOUNT", "CURRENCY", "ORDER", "DESC",
                 "MERCHANT", "MERCH_NAME", "ADDENDUM", "AD.CUST_BOR_ORDER_ID",
                 "TIMESTAMP", "M_INFO", "NONCE", "P_SIGN"):
        assert req.get(name), f"missing mandatory field {name}"
    assert req["ADDENDUM"] == "AD,TD"
    assert len(req["DESC"]) <= 50


def test_desc_is_clipped_to_50_chars(our_key, merchant):
    req = b.build_sale(merchant, 1, "5.00", "Дълго описание " * 20, our_key)
    assert len(req["DESC"]) == 50


def test_status_check_signs_only_four_fields_and_no_rfu(our_key, merchant):
    req = b.build_status_check(merchant, 114233, b.TRTYPE_SALE, our_key)
    assert b.request_sign_fields("90") == ("TERMINAL", "TRTYPE", "ORDER", "NONCE")
    assert not b.mac_source(b.request_sign_fields("90"), req).endswith("-")
    assert req["TRAN_TRTYPE"] == "1"
    assert "AMOUNT" not in req


def test_reversal_refuses_without_original_references(our_key, merchant):
    with pytest.raises(b.BoricaError):
        b.build_reversal(merchant, 1, "5.00", "Сторно", our_key, rrn="", int_ref="X")
    with pytest.raises(b.BoricaError):
        b.build_completion(merchant, 1, "5.00", "Довършване", our_key, rrn="R", int_ref="")


def test_reversal_echoes_the_original_references(our_key, merchant):
    req = b.build_reversal(merchant, 145659, "1.00", "Сторно", our_key,
                           rrn="028701253242", int_ref="B7A68A9F37E8586E")
    assert req["RRN"] == "028701253242"
    assert req["INT_REF"] == "B7A68A9F37E8586E"
    assert req["ORDER"] == "145659"
    assert req["TRTYPE"] == "24"


def test_unsupported_trtype_is_refused():
    with pytest.raises(b.BoricaError):
        b.request_sign_fields("99")


# ------------------------------------------------------------ money verdict
def test_only_action0_rc00_counts_as_paid():
    assert b.classify({"ACTION": "0", "RC": "00"}).state == "paid"
    for action, rc in [("0", "05"), ("2", "00"), ("1", "00"), ("2", "05"), ("3", "96")]:
        assert b.classify({"ACTION": action, "RC": rc}).state != "paid", (action, rc)


def test_negative_rc_is_pending_not_declined():
    """The trap: -19/-25/-40 look like failures but can still become RC 00
    inside GUARDTIME. Telling the customer 'declined' here loses a real sale."""
    for rc in ("-1", "-17", "-19", "-25", "-31", "-33", "-40"):
        out = b.classify({"ACTION": "3", "RC": rc})
        assert out.state == "pending"
        assert out.final is False


def test_positive_rc_is_a_final_decline():
    out = b.classify({"ACTION": "2", "RC": "05"})
    assert out.state == "declined" and out.final is True


def test_soft_decline_is_pending():
    assert b.classify({"ACTION": "21", "RC": "65"}).state == "pending"


def test_empty_response_is_pending_never_paid():
    out = b.classify({})
    assert out.state == "pending" and out.paid is False


def test_outcome_carries_the_references_a_refund_will_need():
    out = b.classify(VECTORS["responses"][2]["fields"])
    assert out.rrn and out.int_ref


def test_cardholderinfo_is_surfaced():
    out = b.classify({"ACTION": "2", "RC": "05", "CARDHOLDERINFO": "Обадете се на банката"})
    assert out.cardholder_info == "Обадете се на банката"


def test_rc_text_is_bulgarian_and_never_empty():
    for rc in ("00", "05", "-17", "-19", "65", "1A", "zzz"):
        assert b.rc_text(rc).strip()


def test_eci_authentication_flags():
    assert b.eci_text("05")[0] is True        # Visa fully authenticated
    assert b.eci_text("02")[0] is True        # Mastercard fully authenticated
    for weak in ("06", "07", "00", "01", ""):
        assert b.eci_text(weak)[0] is False


def test_auth_step_res_covers_both_3ds_versions():
    assert b.auth_step_text("PARES_Y") == (1, True, "Успешна автентикация")
    assert b.auth_step_text("ARES_Y")[0] == 2
    assert b.auth_step_text("RREQ_N")[1] is False
    assert b.auth_step_text("")[0] == 0


def test_guardtime_constants_match_the_spec():
    assert b.GUARDTIME_SECONDS == 900
    assert b.FINAL_AFTER_SECONDS == 960


def test_gateway_urls_are_the_documented_ones():
    assert b.GATEWAY_URLS["prod"] == "https://3dsgate.borica.bg/cgi-bin/cgi_link"
    assert b.GATEWAY_URLS["test"] == "https://3dsgate-dev.borica.bg/cgi-bin/cgi_link"
