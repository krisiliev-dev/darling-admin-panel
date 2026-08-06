"""Prove the Python and PHP BORICA cores agree, byte for byte.

    python crosscheck_borica.py        # exit 0 = identical

Two implementations of one money protocol will drift. The vector file catches
drift away from BORICA; this catches drift between our own halves — the failure
mode where the storefront signs one string and the panel reconciles against
another, and nobody notices until a refund goes to the wrong order.

It found a real one on the first run: the Python transliterator rebuilt
capitalisation by zipping the original string against its translated form, which
misaligns the moment a Cyrillic letter maps to two Latin ones (ц -> ts,
щ -> sht). "Свищов, ул. Дунав 3" came out as "Svishtov, ul. dunav".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

# The two halves live in two repos that sit side by side under darling/.
# Override either with an argument or the environment when they do not.
PANEL = Path(os.environ.get("BORICA_PY_ROOT") or (HERE / ".."))
PHP_PROTOCOL = Path(
    sys.argv[1] if len(sys.argv) > 1 else
    os.environ.get("BORICA_PHP_PROTOCOL")
    or HERE / ".." / ".." / "darling-woo" / "plugin" / "darling-checkout"
    / "includes" / "class-borica-protocol.php"
).resolve()
PHP_BIN = os.environ.get("BORICA_PHP_BIN", "php")

sys.path.insert(0, str(PANEL.resolve()))

from darling_core import borica as b

AMOUNTS = ["9", "19.9", "1234,50", "0.295", "0.294", "0.999", "99.999", "1", "1.005", "10.00", "0.01"]
ORDERS = ["1", "123", "000123", "999999"]
AD = [("000007", "WC;1234-Дарлинг"), ("000042", "Поръчка 42"),
      ("000001", "abcdefghijklmnopqrstuvwxyz")]
MINFO = [
    {"cardholder_name": "Иван Петров", "email": "i@x.bg", "phone": "0893361670",
     "bill_address": "Свищов, ул. Дунав 3"},
    {"cardholder_name": "Георги Ц. Щърбов", "phone": "+359888123456", "force_challenge": True},
    {"cardholder_name": "ANNA-MARIA", "ship_address": "Sofia, bul. Vitosha 1"},
]
TRTYPES = ["1", "12", "21", "22", "24", "90"]
MAC_INPUT = {"TERMINAL": "V1800001", "AMOUNT": "1.00", "CURRENCY": "EUR",
             "ORDER": "000042", "TIMESTAMP": "20260806143005", "NONCE": "A" * 32}
RCS = ["00", "05", "-17", "-19", "65", "1A", "zzz"]
ECIS = ["05", "06", "07", "00", "01", "02", ""]
AUTHS = ["PARES_Y", "ARES_Y", "RREQ_N", "VERES_N", ""]
VERDICTS = [("0", "00"), ("0", "05"), ("2", "00"), ("2", "05"),
            ("3", "-19"), ("21", "65"), ("3", "-40"), ("", "")]

PHP = r"""<?php
define('DARLING_BORICA_CLI', true);
require getenv('BORICA_PHP_PROTOCOL');
$P = 'Darling_Borica_Protocol';
$in = json_decode(file_get_contents('php://stdin'), true);
$out = array();
foreach ($in['amounts'] as $a)  { $out['amt'][] = $P::format_amount($a); }
foreach ($in['orders'] as $o)   { $out['ord'][] = $P::format_order($o); }
foreach ($in['ad'] as $c)       { $out['ad'][]  = $P::bor_order_id($c[0], $c[1]); }
foreach ($in['minfo'] as $m)    { $out['mi'][]  = $P::m_info($m); }
foreach ($in['trtypes'] as $t)  {
    $vals = $in['mac_input']; $vals['TRTYPE'] = $t;
    $out['mac'][] = $P::mac_source($P::request_sign_fields($t), $vals);
}
foreach ($in['rcs'] as $r)      { $out['rc'][] = $P::rc_text($r); }
foreach ($in['ecis'] as $e)     { $x = $P::eci_text($e);       $out['eci'][]  = array($x['authenticated'], $x['text']); }
foreach ($in['auths'] as $a)    { $x = $P::auth_step_text($a); $out['auth'][] = array($x['version'], $x['authenticated'], $x['text']); }
foreach ($in['verdicts'] as $v) { $out['cls'][] = $P::classify(array('ACTION' => $v[0], 'RC' => $v[1]))['state']; }
ksort($out);
echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
"""


def python_side() -> dict:
    return {
        "amt": [b.format_amount(a) for a in AMOUNTS],
        "ord": [b.format_order(o) for o in ORDERS],
        "ad": [b._bor_order_id(o, s) for o, s in AD],
        "mi": [b.m_info(**m) for m in MINFO],
        "mac": [b.mac_source(b.request_sign_fields(t), dict(MAC_INPUT, TRTYPE=t)) for t in TRTYPES],
        "rc": [b.rc_text(r) for r in RCS],
        "eci": [list(b.eci_text(e)) for e in ECIS],
        "auth": [list(b.auth_step_text(a)) for a in AUTHS],
        "cls": [b.classify({"ACTION": a, "RC": r}).state for a, r in VERDICTS],
    }


def php_side() -> dict:
    if not PHP_PROTOCOL.is_file():
        print(f"cannot find the PHP core at {PHP_PROTOCOL}\n"
              "pass its path as the first argument or set BORICA_PHP_PROTOCOL", file=sys.stderr)
        raise SystemExit(2)
    runner = HERE / "_crosscheck_runner.php"
    runner.write_text(PHP, encoding="utf-8")
    payload = json.dumps({
        "amounts": AMOUNTS, "orders": ORDERS, "ad": [list(x) for x in AD],
        "minfo": [{"name": m.get("cardholder_name", ""), "email": m.get("email", ""),
                   "phone": m.get("phone", ""), "bill_address": m.get("bill_address", ""),
                   "ship_address": m.get("ship_address", ""),
                   "force_challenge": m.get("force_challenge", False)} for m in MINFO],
        "trtypes": TRTYPES, "mac_input": MAC_INPUT, "rcs": RCS, "ecis": ECIS,
        "auths": AUTHS, "verdicts": [list(v) for v in VERDICTS],
    }, ensure_ascii=False)
    try:
        env = dict(os.environ, BORICA_PHP_PROTOCOL=str(PHP_PROTOCOL))
        try:
            proc = subprocess.run([PHP_BIN, str(runner)], env=env,
                                  input=payload.encode("utf-8"), capture_output=True, check=True)
        except FileNotFoundError:
            print(f"{PHP_BIN} not found — this check needs PHP as well as Python, and a "
                  "skip that exits 0 would look exactly like a pass", file=sys.stderr)
            raise SystemExit(2)
    finally:
        runner.unlink(missing_ok=True)
    return json.loads(proc.stdout.decode("utf-8"))


def main() -> int:
    py, php = python_side(), php_side()
    diffs = [k for k in sorted(set(py) | set(php)) if py.get(k) != php.get(k)]
    for key in diffs:
        print(f"DIFF {key}\n  python: {py.get(key)}\n  php   : {php.get(key)}")
    total = sum(len(v) for v in py.values())
    if diffs:
        print(f"\nFAIL — {len(diffs)} of {len(py)} groups differ")
        return 1
    print(f"OK  Python and PHP agree on {total} values across {len(py)} groups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
