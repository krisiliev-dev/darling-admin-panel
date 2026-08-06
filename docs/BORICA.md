# Borica vPOS — full read of the specification, and what we built from it

Written 2026-08-06 by the panel session, from a front-to-back read of everything
published at https://3dsgate-dev.borica.bg/. Status: **protocol core built and
gated; nothing wired to a terminal yet, because we have no credentials.**

Sources read in full, not skimmed:

| Document | Size | What it settles |
|---|---|---|
| `P-OM-41_BORICA_eCommerce_CGI_interface_v_7.0_EN.pdf` | 55 pp, v7.0 / 17.04.2026 | The whole interface: fields, transaction types, cryptography, worked examples, test cards, error codes, PHP samples, FAQ |
| `APGW_RC.pdf` | 1 p | The negative (gateway-side) response codes, EN + BG |
| `AUTH_STEP_RES_TABLE.pdf` | 1 p | 3-DS v1 **and** v2 authentication outcomes (the spec's own appendix only lists v2) |
| `ECI.pdf` | 1 p | Electronic Commerce Indicator per scheme |

---

## 1. The finding that would have cost us a launch

The message signature (`P_SIGN`) is RSA PKCS#1 v1.5 over SHA-256, applied to a
string built by concatenating `<utf8-byte-length><value>` for a fixed ordered
list of fields. A field that is missing or empty contributes a single byte
`0x2D` — `-` — with no length prefix. So far, so documented.

The spec then says there is a field called **RFU** ("reserved for future use")
which never appears in any message but *does* participate in the signing string
as one `-`. Section 5, Table 10 and every worked example in section 6 print the
**response** signing strings with that trailing `-` and annotate them
`Signature = [true]`.

**That is wrong, and it is the single most expensive error in the document.**

The document publishes five real `(signing string, P_SIGN)` pairs from BORICA's
own test terminal V1800001, plus BORICA's APGW public key. We checked all five:

```
trtype 90  RC 00   verifies WITHOUT the trailing "-"    with it: no
trtype 90  RC -24  verifies WITHOUT the trailing "-"    with it: no
trtype 24  RC 00   verifies WITHOUT the trailing "-"    with it: no
trtype 21  RC -20  verifies WITHOUT the trailing "-"    with it: no
trtype 22  RC 95   verifies WITHOUT the trailing "-"    with it: no
```

Five for five, zero contradictions. BORICA's own PHP verification sample (§9.7)
agrees: its field list omits RFU and the string it passes has no trailing dash.

So the rule is:

- **Requests** we sign: signing string **ends with `-`** (RFU included).
  Confirmed by BORICA's PHP *signing* sample (§9.6), whose data string ends `-`.
- **Responses** we verify: signing string **has no trailing `-`**.
- `TRTYPE=90` (status check) requests have no RFU at all — the signed fields are
  exactly `TERMINAL, TRTYPE, ORDER, NONCE`.

Had we implemented the tables as printed, every response would have failed
verification. The symptom is indistinguishable from a key mix-up or a tampered
message: you would sit there rotating certificates while real payments piled up
unrecognised. This is why the read was worth doing front to back.

Our verifier accepts the canonical (no-RFU) form first and falls back to the
with-RFU form, but **reports which one matched**. Accepting either is not a
weakening — both require BORICA's private key — but a run of `rfu` verdicts
means BORICA moved the scheme, and that must raise an ops issue rather than
pass silently.

Three other documentation errors we tripped over, recorded so nobody re-derives
them: Table 14's response example mixes fields from two different transactions
(its `ORDER` does not match its own request); the "reversed successfully" JSON
in §6.2 carries a `NONCE` that does not match its request; and Table 20 lists
`AMOUNT 3.00` while its own signing string encodes `1.00`. In all three cases
the *signature* is the arbiter, not the table.

---

## 2. The protocol, as we now understand it

### Message flow

Everything goes through the cardholder's browser as an HTML form POST — we never
see card data and never call the gateway directly for a sale.

```
customer clicks Pay
  -> our page renders a self-submitting <form> with the signed fields
  -> browser POSTs to  https://3dsgate.borica.bg/cgi-bin/cgi_link
  -> BORICA collects the card, runs 3-D Secure with the issuer's ACS
  -> browser POSTs the signed result back to our BACKREF
```

The BACKREF is **registered with BORICA per terminal**, not sent in the request.
Ours is already registered for production:
`https://darlinghome.bg/wc-api/darling_borica/`. If the log ever says
"Missing BACKREF parameter, using default", the terminal record is wrong and
only the acquirer can fix it.

Server-to-server (GET or POST, JSON answers) for everything else: status check,
reversal, completion, pre-auth reversal.

### Transaction types

| TRTYPE | Meaning | Notes |
|---|---|---|
| 1 | Sale | The only one we need at launch |
| 12 | Pre-authorisation | Blocks funds; must be settled by a Completion |
| 21 | Completion | Amount ≤ the pre-auth; one per pre-auth; 30-day window |
| 22 | Reversal of pre-authorisation | Amount **must equal** the pre-auth |
| 24 | Reversal of a Sale | Amount ≤ the sale; **one per sale, ever**; 30-day window |
| 90 | Transaction status check | Last 24 h only; JSON answer |

`ORDER`, `RRN` and `INT_REF` on 21/22/24 are the **original** transaction's
values — which is why we must persist `RRN` and `INT_REF` the moment a sale
succeeds. Without them no refund is possible through the API at all, and the
only remedy is a phone call to the acquirer.

### Fields we send on a Sale

`TERMINAL, TRTYPE, AMOUNT, CURRENCY, ORDER, DESC, MERCHANT, MERCH_NAME,
MERCH_URL, EMAIL, COUNTRY, MERCH_GMT, LANG, ADDENDUM, AD.CUST_BOR_ORDER_ID,
TIMESTAMP, M_INFO, NONCE, P_SIGN` — of which BORICA marks mandatory:
`TERMINAL, TRTYPE, AMOUNT, CURRENCY, ORDER, DESC, MERCHANT, MERCH_NAME,
ADDENDUM, AD.CUST_BOR_ORDER_ID, TIMESTAMP, M_INFO, NONCE, P_SIGN`.

Constraints that are easy to get wrong:

- `ORDER` is **exactly six digits** and must be unique for the terminal within
  24 hours. Our WooCommerce order numbers will pass 999999 eventually; the
  mapping between Woo order id and Borica ORDER must be stored, not computed.
- `NONCE` is 16 random bytes as 32 uppercase hex characters, also unique per
  terminal per 24 h. It is echoed back, so `(TERMINAL, ORDER, NONCE)` is how a
  response is matched to an order.
- `TIMESTAMP` is UTC `YYYYMMDDHHMMSS` and must be within **15 minutes** of
  BORICA's clock. A box with broken NTP produces `RC -17` with
  `STATUSMSG "Expired transaction"` on every single payment.
- `AMOUNT` carries a decimal point: `9.00`, `0.29`. The terminal is
  single-currency and the request currency must match it — **EUR** for us, since
  Bulgaria is on the euro from 2026-01-01.
- `DESC` is at most 50 characters. Cyrillic is fine, UTF-8 only.
- `AD.CUST_BOR_ORDER_ID` = the six-digit ORDER concatenated with up to 16 more
  characters, digits and Latin letters, **no semicolon**. This is the value that
  travels in BORICA's financial files to the bank — it is what the accountant
  reconciles the statement against, so it must contain the Woo order number.
- `ADDENDUM` must be the literal `AD,TD` whenever `AD.CUST_BOR_ORDER_ID` is sent.

### M_INFO — the field that decides our conversion rate

`M_INFO` is base64 of a small JSON object carrying EMV 3-DS v2 data: cardholder
name (Latin, ≤45 chars), e-mail and/or phone as `{"cc":"359","subscriber":"..."}`,
and a billing and/or shipping address line (≤50 chars). BORICA marks it
mandatory for Sale and Pre-authorisation.

It is worth filling in properly rather than minimally. The richer the data, the
more likely the issuer authenticates *frictionlessly* — no SMS code, no app
switch, no drop-off. Under 3-DS v2 this is the main lever a merchant has on card
conversion, and we get it for free from the checkout form we already collect.

Setting `{"threeDSRequestorChallengeInd":"04"}` forces a challenge instead. We
should not do that by default; it is a tool for a specific fraud case, not a
security posture. (If we ever want it: it is one flag on `m_info()`.)

### Reading the answer

Two fields decide everything, and they must be read **together**:

> **A payment is settled if and only if `ACTION == "0"` AND `RC == "00"`.**

Beyond that, `RC` has three regimes, and collapsing them into a boolean is the
classic way to lose money in both directions:

- **Positive `RC` other than `00`** — the issuer's host refused. Final. Tell the
  customer, release the stock.
- **Negative `RC`** — a *gateway-side* code, and **not final**. It can still
  become `00` within GUARDTIME (15 min): the customer pressed Back, got an error
  page, then carried on and paid. The spec is explicit that the correct response
  is to poll `TRTYPE=90`, and that the answer is only final after **GUARDTIME +
  1 minute = 16 minutes**. Marking these as failed immediately loses real sales;
  marking them as paid loses real money.
- **`ACTION 21` (soft decline, `RC 65` Mastercard / `1A` Visa)** — APGW itself
  re-runs the transaction with strong authentication. Not our decision yet;
  wait. If the second attempt also fails, it ends as `RC 1A`.

Our `classify()` therefore returns `paid` / `declined` / `pending` plus a
`final` flag, in both implementations. Three states, never a boolean.

Two response fields must reach a human. `CARDHOLDERINFO` is text the issuer's
ACS wants shown to the cardholder — the spec says the merchant **must** display
it. `AUTH_STEP_RES` and `ECI` record how strongly the cardholder was
authenticated, which is what a chargeback argument turns on: `ECI 05` (Visa,
Diners, Bcard and all domestic traffic) or `ECI 02` (Mastercard) mean full 3-D
Secure authentication and liability shift; `06/07` and `00/01` do not.

The `AUTH_STEP_RES` annex is more useful than the spec's own appendix because it
documents the 3-DS **v1** values (`VERES_*`, `PARES_*`) that BORICA still returns
for older cards, not just the v2 ones (`ARES_*`, `RREQ_*`). Both sets are mapped.

### Error codes worth memorising

`-17` is the one we will actually meet. It means "terminal denied access", and
the FAQ lists exactly four causes: wrong signing string, wrong private key,
wrong signing scheme configured on the terminal, or a `TIMESTAMP` more than 15
minutes off. Nothing else. `-19` is a cardholder authentication failure (card not
enrolled, wrong password, customer cancelled). `58` means the terminal was never
activated by the acquirer, which is a phone call, not a code change.

---

## 3. What we built

Two implementations of one protocol, gated by one shared vector file.

```
darling_core/borica.py                    Python core        57 tests green
class-darling-borica-protocol.php         PHP core          104 assertions green
borica_vectors.json                       BORICA's own vectors, both sides read it
test_borica.py                            pytest gate
test-borica-protocol.php                  standalone PHP gate (no PHPUnit needed)
crosscheck_borica.py                      proves the two agree, value by value
make-borica-csr.sh                        key + CSR generator, correct file naming
```

The vector file is not hand-written. It carries nine request signing strings
lifted verbatim from the spec's tables, and five complete response objects with
BORICA's **real signatures** and BORICA's APGW public key. Both gates verify
those signatures for real. If BORICA ever changes the signing string, these fail
before any money moves — which is the only kind of test worth having on a
payment path.

Why two implementations. The storefront needs PHP: the Sale is a WooCommerce
checkout redirect and the BACKREF is a WooCommerce API endpoint. The panel needs
Python for reconciliation and back-office refunds. Writing the crypto twice on a
money path is a genuine risk, so `crosscheck_borica.py` runs both halves over the
same 54 inputs — amounts, order numbers, financial-file identifiers, M_INFO
payloads, signing strings for all six transaction types, response classification
— and fails on any difference.

It earned its place on the first run. The Python transliterator rebuilt
capitalisation by zipping the original string against its translated form, which
misaligns the moment a Cyrillic letter maps to two Latin ones (`ц`→`ts`,
`щ`→`sht`). "Свищов, ул. Дунав 3" came out as "Svishtov, ul. dunav" — silently
wrong data in the field that drives frictionless authentication, and exactly the
sort of thing no unit test written against its own implementation would catch.

Money-path decisions baked into both cores:

- Amounts never touch a float. Python refuses a `float` argument outright;
  PHP does string-based round-half-up and needs no bcmath.
- A response whose signature does not verify is treated as **no message at
  all** — no amount read from it, no order completed, no result shown.
- Reversal and completion refuse to build without the original `RRN`/`INT_REF`
  rather than sending a request that will be rejected.
- An empty or unparseable response classifies as `pending`, never as `paid` and
  never as `declined`.

---

## 4. What still has to happen

### Blocked on the contract (Friday, possibly Monday)

We need `TERMINAL` (TID, `V` + 7 digits) and `MERCHANT` (MID, 10 digits), for
test and production separately — the spec warns they differ between
environments.

The moment the TID arrives, **before** anything else, run `make-borica-csr.sh`
twice, once with `T` and once with `P`. It generates a password-protected
2048-bit RSA key and a CSR with the seven certificate fields BORICA requires
(`CN` = `darlinghome.bg` with no scheme, `OU` = TID, `O`, `L`, `ST`, `C=BG`,
e-mail), named to their convention `VNNNNNNN_YYYYMMDD_Z.csr` inside
`VNNNNNNN_YYYYMMDD_Z.zip`. Only the zip is uploaded to the Merchant Portal;
the portal refuses private keys by design and so should we.

The script is written so **Kris runs it** on the box that will hold the key, and
so the passphrase is typed at the openssl prompt rather than passed as an
argument — it must not land in shell history, a transcript, or a commit. When
the signed `.cer` comes back, the script prints the `md5` of the key modulus so
it can be compared against the certificate's; a mismatch there is the difference
between a working terminal and an unexplained `RC -17`.

Please confirm the certificate identity fields before they are baked in, since
they cannot be edited afterwards without a re-issue: organisation
**Darling 7 OOD**, locality **Svishtov**, province **Veliko Tarnovo**, e-mail
**orders@darlinghome.bg**. Say the word and any of them change.

### One decision we should not make alone

The private key has to live wherever signing happens. The Sale is signed on the
**woo box** (checkout) and the BACKREF is verified there. Refunds and status
checks are back-office work that naturally belongs to the **panel box**.

The clean answer is one key on one box: keep it on the woo box, and have the
panel issue refunds through Woo's existing REST API rather than talking to
BORICA itself. One key, one blast radius, one implementation on the money path.
The alternative — the same key on both boxes — is operationally normal but
doubles the exposure for no functional gain. Our recommendation is the first,
and the Python core then serves reconciliation and stays the executable
reference the PHP is checked against.

### Then, in order

Build the WooCommerce gateway around the proven core: a checkout redirect that
renders the signed form, a `wc-api/darling_borica/` handler that verifies before
it does anything else, `RRN`/`INT_REF` persisted on the order, idempotency
claimed atomically on the Woo order id (the same rule as the courier bookings —
the BACKREF and the status poller are two routes to one completion, and a miss
means a double-settled order), and a poller that re-checks every `pending`
transaction after the 16-minute mark.

Then the test-terminal run, against BORICA's fixed-response cards
(`4341792000000044` Visa, `5100789999999895` Mastercard, any future expiry, any
CVV, OTP `111111`, and an amount ending `.65` to force a soft decline). Only
after that is green does production get a single real card payment, immediately
reversed with `TRTYPE=24` — the RESULT gate, with the Merchant Portal
transaction as the location the verification points at.

### Standing operational notes

BORICA publishes its APGW public keys at https://3dsgate-dev.borica.bg/ and
expects each merchant to have a *procedure* for replacing them. Ours should be:
the key path is configuration, not code; a staleness watchdog warns before the
certificate expires; and a failed verification raises an ops issue rather than
being retried into silence. The same watchdog logic we already use for backups
applies — watch staleness, not errors.

Merchant Portal (production `https://3dsgate.borica.bg/mwp/static/`) shows
Authentications, Pending, Rejected and Transactions; the *Tagged Data* tab is
where `AD.CUST_BOR_ORDER_ID` and `DESC` appear, and it is the reconciliation
surface the office will actually use.

---

## 5. Test cards, for whoever runs the test terminal

| Brand | PAN | Expiry | CVV | OTP |
|---|---|---|---|---|
| Visa | 4341792000000044 | any future MMYY | any 3 digits | 111111 |
| Mastercard | 5100789999999895 | any future MMYY | any 3 digits | 111111 |

An amount ending in `.65` forces `RC 65`/`1A` (soft decline, so the ACTION 21
path can be exercised). An amount of exactly `1234.56` on Visa makes APGW return
a populated `CARDHOLDERINFO`, which is how the "must display to the cardholder"
requirement gets tested.
