# Sameday — their WooCommerce guide, their real API, and what we built

Written 2026-08-06 by the panel session, after reading Sameday's `user_guide_for_WooCommerce`
v1 (27 pages) front to back and then mapping the actual API out of their official plugin.
Status: **adapter written and unit-gated; no call has ever been made to Sameday, because
the API credentials are not issued yet.**

## 1. The guide is not an API document

The file Sameday sent is a *plugin* manual. It says: install `SamedayCourier Shipping` from
the WordPress repository, paste your API-LIVE username and password into its settings
screen, import the nomenclatures, configure four services and a pickup point, and generate
waybills from the WooCommerce order screen. It contains no endpoints, no payloads and no
status codes.

So "is what we implemented in line with what they recommend" has two answers.

**On the storefront, no — and deliberately.** Their recommended route is their plugin, and
we are not going to install it. Three reasons, in order of weight. Their own guide says, in
the errors section, that the module *is not fully compatible with HPOS* — WooCommerce's
high-performance order storage, which is the default for new stores and the thing our order
tables sit on. Second, the plugin owns the checkout rate, the locker map, the AWB and the
COD tax, which is precisely the surface the panel already owns for Speedy, Econt and
BoxNow; two systems both deciding shipping price on the same order is how the Speedy COD
incident happened. Third, we already have the pattern: BoxNow and Pigeon were both mapped
out of their official plugins into `connector/courier.py`, and Sameday now follows it.

**On the operational rules, yes.** Everything the guide *does* specify is either encoded in
the adapter or listed below as a setting somebody has to make in their portal. Those parts
are not optional — they are how their account behaves.

## 2. What the guide actually pins down

The credentials are a **username and password**, not an API key, and the plugin labels them
"API-LIVE". Labels print **A4 or A6**. Estimated cost is computed from **weight in kilograms
and the recipient city**, and the guide is blunt that the city name must be identical to the
one in their system or the estimate silently falls back to the fixed tariff — which is why
their plugin offers to replace the checkout city field with a nomenclature dropdown. There
is a percentage **extra fee** knob meant for VAT, and a **repayment tax** that is added when
the customer picks cash on delivery.

Four services exist on a normal account: `24H` national to address, `Out of home` national
to a Sameday Point or easybox, and the two crossborder variants for Romania, Hungary and
Bulgaria. The out-of-home service name in the checkout is **mandatory** — their guide says
so outright, and their plugin hardcodes the Bulgarian string. **Pickup points must exist and
exactly one must be marked default**; that is the "Точка на връщане" field on the ticket, and
nothing can be booked without it.

Their error list names the three failures they actually see: a **422 "creating AWBs
blocked"** meaning the account is not cleared to issue waybills (sales agent, not code); a
missing **recipient phone or e-mail**, both mandatory; and an invalid `lockerLastMile` or a
map that will not open, which they attribute to theme and plugin conflicts.

And then the one that matters most to us.

## 3. Two traps that would have cost real money

### The currency

The Bulgarian instance quotes and collects in **BGN**. Their own plugin asks for BGN
explicitly and converts to EUR in PHP at the fixed peg — we found the conversion class in
their source, hardcoding `0.511292`, which is `1 / 1.95583`.

Our entire stack is EUR. A BGN quote read as EUR prices every parcel at roughly half, and a
COD amount sent as EUR when the API expects BGN under-collects by the same factor on the
55–60 % of orders that are cash on delivery. Neither shows up as an error anywhere; it shows
up at the payout reconciliation, weeks later.

The adapter therefore converts on both edges with `Decimal` at the statutory rate, and
**refuses outright** any response whose currency it did not ask for rather than assuming. If
Sameday moves the BG instance to euro — plausible, post-adoption — the first quote raises a
clear error instead of quietly halving the price. That behaviour is unit-tested.

### The internal reference

`clientInternalReference` is unique **forever**, even after a waybill is cancelled. Their FAQ
tells you to solve a collision by appending a letter and trying again.

We must never do that. A suffix-and-retry on a collision books a *second* waybill for one
order: two labels, two бона, two COD collections against one payment. The collision is not a
naming problem, it is the API telling us the order is already booked.

So the reference is the order reference verbatim, which turns Sameday's constraint into a
free idempotency key on the exact thing our house rule already says to claim atomically. The
adapter maps that 422 to a message that says "this order ALREADY has a waybill, do not add a
suffix, go and read the existing one in eAWB", and that mapping is tested.

## 4. The real API, mapped from their plugin

Read out of `samedaycourier-shipping` v1.11.1 (`lib/sameday-courier`, their PHP SDK, plus
`classes/*.php`).

Hosts are per country: `https://api.sameday.bg` for Bulgaria, `api.sameday.ro`,
`api.sameday.hu`, with demo instances at `sameday-api-bg.demo.zitec.com` and siblings.
Authentication is `POST /api/authenticate` carrying `X-Auth-Username` and `X-Auth-Password`
headers with a form-encoded `remember_me=true`, returning a token that every later call
sends as `X-AUTH-TOKEN`; on a first 401 or 403 their SDK drops the token and retries once.
Bodies are `application/x-www-form-urlencoded` built by PHP's `http_build_query`, so nested
structures flatten to `awbRecipient[name]` and `parcels[0][weight]` — an adapter that posts
JSON gets an empty recipient and a validation error.

| Purpose | Call |
|---|---|
| Services on the contract | `GET /api/client/services` |
| Pickup points | `GET /api/client/pickup-points` |
| Easybox lockers | `GET /api/client/lockers` |
| PUDO points | `GET /api/client/ooh-locations` |
| City / county nomenclature | `GET /api/geolocation/city`, `/api/geolocation/county` |
| Quote | `POST /api/awb/estimate-cost` → `{amount, currency, time}` |
| Book | `POST /api/awb` → `{awbNumber, awbCost, parcels[]}` |
| Label | `GET /api/awb/download/{awb}/{A4\|A6}` |
| Cancel | `DELETE /api/awb/{awb}` |
| Track | `GET /api/client/awb/{awb}/status` |
| Bulk status | `GET /api/client/status-sync?startTimestamp=&endTimestamp=` |

Service codes are `24` door, `LN` easybox, `PP` PUDO, `XB` and `XL` crossborder door and
locker. A locker id goes in `lockerLastMile` for `LN` and in `oohLastMile` for `PP` — same id
space, different field, and swapping them is their documented "invalid lockerLastMile".
Enumerations worth knowing: package type parcel `0`, AWB payment by client `1`, COD collector
client `1`, person type individual `0`. Lists are paginated with `page` and `countPerPage`
and answer `{data, currentPage, pages}`.

One thing we did **not** copy: their plugin computes the COD amount from the cart
**subtotal**, so their own checkout under-collects the shipping fee on COD orders. Ours uses
the order's COD amount.

## 5. What we changed

`connector/courier.py` and `admin-panel/darling_core/_vendor/courier.py`, kept byte-identical
because the panel loads the vendored copy and a fix that lands in only one of them deploys
green and does not run.

The Sameday entry was a two-line stub — `class Sameday(Courier): name = "sameday"` — sitting
in the adapter registry, which means the console listed it as a courier and, the moment
someone put credentials in `~/.hermes/.env`, every call would have surfaced a bare
`NotImplementedError` to an operator. It is now a full adapter, and the base class raises a
sentence in Bulgarian instead of a traceback for anything genuinely unimplemented
(Европът, still).

The registry base URL was also wrong: it read `https://api.sameday.bg/api`, and every
endpoint already begins with `/api`, so every call would have gone to `/api/api/...`.

`backend/tests/test_sameday.py` is the gate — 40 tests, run on the laptop and in CI, no
credentials and no network. It covers the peg arithmetic in both directions, the refusal of
an unexpected currency, COD riding identically through the quote and the booking (the Speedy
regression), the dry-run path making no call at all, the duplicate-reference message, the
mandatory phone, county lookup, locker versus door service routing, and the PHP-style form
encoding.

## 6. What has NOT been verified, plainly

No request in this adapter has ever reached Sameday. There are no credentials. Everything
above is read off their published plugin and their guide, which is the same method that got
BoxNow right, but it is not the same thing as a result.

Before the first real label, on the test account:

1. Confirm `estimate-cost` really answers in BGN on `api.sameday.bg`. If Bulgaria's euro
   adoption has moved it, the adapter raises rather than mis-prices — that is the intended
   behaviour, and the fix is one constant.
2. Confirm `cashOnDelivery` raises the estimate the same way it raises the booking. Quote
   and booking must carry identical COD semantics; the last time they did not, we paid a fee
   per order until someone reconciled a payout.
3. Read the service ids behind codes `24`, `LN` and `PP` on this contract.
4. Read the default pickup point id, and check that Свищов resolves to a county in their
   nomenclature.
5. Then one real waybill, immediately cancelled, with the AWB number written down as the
   location the verification points at.

Until those five are done, Sameday should stay out of the storefront's rate table.

## 7. For the support ticket

The fields Sameday asked for map like this: *Link TC — клиент* and *Подсфера клиент* are
their internal account references and should be left to them or filled from the contract;
*Точка на връщане* is the pickup point, which must exist and be the default one in their
portal before anything can be booked. *Versiune Plugin* and *Versiune Platforma Site* assume
their plugin — the honest answer is that we integrate against the API directly rather than
through the WooCommerce module, and the reason worth giving them is their own note that the
module is not fully HPOS compatible. Support is `plugineasybox@sameday.ro` or a ticket in
UNNO; they say explicitly that they do not take phone support and do not log into merchant
sites, so screenshots and exact error bodies are what move a ticket.
