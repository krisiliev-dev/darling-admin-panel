# Compliance checklist — Дарлинг 7 (for the accountant + legal)

Two launch-gate items. **The panel does NOT implement fiscalisation or make legal determinations** —
these are for your accountant / legal advisor. This is the checklist of what to confirm and what the
panel already does to support it. Sources at the bottom.

---

## 1. Наредба Н-18 — fiscalisation (accountant sign-off, LAUNCH GATE) ⚖️

**Confirm with the accountant which track Дарлинг is on BEFORE go-live.** The rules differ by how the
customer pays:

- **COD via пощенски паричен превод, courier → your bank (the common case, ~60% of orders):**
  *No fiscal device required*, BUT you must give the customer a document (paper or electronic) with:
  firm name, ЕИК, each item + its **tax group (А/Б/В/Г)**, quantity, unit value, total, and payment
  method. **The courier does NOT issue your fiscal receipt** — you do (Barsy).
- **Card-only, non-present (virtual POS):** fiscal device avoidable IF the software is СУПТО-compliant
  and issues an alternative document with a **10-digit sequential number, the order UNP, and a QR code**,
  delivered electronically on payment authorization, plus a **standardised audit file to НАП by the
  15th** of the following month.
- **Any cash taken in person (Barsy till/POS):** a real **fiscal device IS required**.

**What the panel does to support this (already built / to verify):**
- Barsy is the fiscal point and issues the document — the panel never fiscalises itself.
- The Shopify→Barsy connector must pass the correct **tax group + payment method per line** so Barsy
  issues the right document. → **Verify** the connector's line mapping with the accountant before
  enabling `WEBHOOKS_LIVE` (see WEBHOOKS_LAUNCH.md).
- The panel should store/surface the Barsy document number + link per order for support and audit
  (small addition when the connector goes live).

**Action:** 1-hour sit-down with the accountant to confirm the track + tax-group mapping. Note the
Speedy 2023 COD outage was a fiscal-connectivity failure — this linkage is real and enforced.

---

## 2. GDPR / ОРЗД — customer data + the refuser blacklist (legal baseline) ⚖️

We now hold customer phone/address/order history AND a refusal-risk store + we query nekorekten.com.
Profiling/blacklisting is exactly what КЗЛД scrutinises. Requirements:

- **Lawful basis:** order data = *contract performance*; the **risk/blacklist + nekorekten lookup =
  legitimate interest (fraud/COD-refusal prevention)** — document a short **Legitimate Interest
  Assessment (LIA)** and add a line to the privacy policy that orders are screened against internal
  history + external risk sources.
- **No solely-automated rejection (Art. 22):** the panel is built this way — a risk hit is a **flag
  for staff review**, never an auto-cancel. Keep it that way.
- **Keep the blacklist INTERNAL:** never contribute to / rely on a cross-merchant shared list (legally
  risky, and the nekorekten data is user-generated/unverified). The panel stores only our own
  outcomes + a nekorekten report *count*, not copied report content.
- **Marketing vs transactional consent:** order-confirmation / shipped notifications are transactional
  (contract) — but any *marketing* SMS/Viber needs separate opt-in. Split them if messaging is added.
- **Data-subject rights:** be able to export/delete a customer's data across Shopify + panel on request
  (access / erasure / portability). → **To build:** a small data-subject request tool (panel side).
- **Retention:** define and enforce retention limits (auto-purge stale marketing consents / old PII).

**nekorekten.com specific:** the API is cleared for use (owner confirmed with the operator). Querying
it about our customers is the legitimate-interest processing above — covered by the LIA + privacy-policy
line. Whitelist the backend server's egress IP; keep the API key server-side only.

**⚠ REPORTING to nekorekten (outbound, more sensitive than querying):** the panel can now SUBMIT a
refuser report to nekorekten's public DB (`POST /api/risk/report` → their `/reports`). Publishing a
person's data to a public list is a heavier processing act than looking it up. Requirements before
using it live: (a) privacy-policy line must explicitly state we may report non-collecting/fraudulent
customers to fraud-prevention databases; (b) report only FACTUAL, documented cases (actual RTO/refusal),
never speculation — the record is defamation-exposed; (c) it's built owner/manager-only + explicit
per-report confirm + free-typed reason, NEVER automatic; (d) we store only the returned report id so a
mistaken report can be retracted (DELETE). Recommend legal sign-off on the reporting flow specifically.

**Action:** legal review of the privacy policy + LIA for the blacklist/nekorekten screening; decide
retention periods. None of this blocks building — it's the paperwork around features already built to
be compliant-by-design (flag-not-reject, internal-only, count-not-content).

---

## Sources
- Наредба Н-18 online-store rules: kik-info.com; blog.superhosting.bg (СУПТО details)
- GDPR e-commerce BG: nit.bg; КЗЛД (cpdp.bg)
- nekorekten Terms (Art. 15 "non-commercial" — cleared with operator per owner): nekorekten.com/bg/terms
