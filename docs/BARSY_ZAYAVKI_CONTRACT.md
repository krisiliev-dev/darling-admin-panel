# Barsy „Заявки - доставчици" — VERIFIED contract (captured 2026-08-04)

Captured live from the Barsy admin UI (darling7.barsy.online) by hooking XHR and performing a
real create → send → delete cycle. Every call below is a POST to `/endpoints/json?bid=1` with
the method name as the single top-level JSON key (same envelope as the rest of the API).

**This supersedes the earlier assumption that Barsy has no supplier-order document.** It does:
the entity is `requests` (Склад → Заявки - доставчици), separate from `Storeloads`.

## Lifecycle
`Нови` (request_status 0) → `Изпратени` (send_date set, via Messagehub) → `Заредени` (converted
to a storeload / delivered). A SENT заявка loses its edit + delete row actions — only info and
re-send remain. So: delete is possible ONLY while the заявка is still new.

## 1. Create / update — `Requests_save`
```json
{"Requests_save": {
  "id": null,                      // null = create; an existing request_id = update
  "action_type": "save",
  "values": {
    "user_name": null,
    "expected_delivery_date": null,   // "Дата за доставка"
    "ordering_view": "0",
    "supplier_id": 39,                // Barsy partner id (Suppliers_getlist)
    "supplier_name": "МИРА-Н-НАДКА ВАСИЛЕВА",
    "description": "",
    "notes": "",
    "depot_id": "",                   // "" = Всички складове
    "client_order_id": null           // links a customer order, if the заявка is driven by one
  },
  "rows": [{
    "article_id": 11365,
    "article_name": "Доставка",
    "amount": "3",                    // Количество (strings, as the UI sends them)
    "package_id": "",                 // Разфасовка ("" = без разфасовка)
    "package_amount": "3",            // Кол. доставчик
    "delivery_price": "",             // Ед. цена
    "detail_notes": ""                // Бележка (per row)
  }]
}}
```
Response: `{"Requests_save": 2}` — the new/updated **request_id**.

Row grid also exposes (read-only, computed by Barsy): Налично, Общо налично, Мин кол, Max кол,
Тотал, Тотал с ДДС, Външен реф. №, Външно реф. име.

## 2. Send to the supplier — `messagehub_send`
Two steps, both `type: "request"`:
```json
// a) open the dialog (prefills template + supplier email)
{"Messagehub_send": {"id": 2, "params": {"type": "request"}}}

// b) actually send
{"messagehub_send": {
  "action_type": "save",
  "active_struct_id": "eStructForm_1",
  "params": {"type": "request"},
  "id": 2,
  "values": {
    "to": "supplier@example.com",
    "template_subject": "Заявка N:$request_id$ от $company_name$",
    "service": "Email",              // also: По телефон, Отдалечени системи
    "template_id": "2",
    "attach_file": 1,                // attaches the „Заявка за доставка" PDF
    "template_body": "На вниманието на $supplier_name$<br>…"
  }
}}
```
Placeholders Barsy expands: `$request_id$`, `$company_name$`, `$supplier_name$`,
`$company_email$`, `$company_tel$`, `$company_fax$`.
VERIFIED end-to-end: a real email was delivered to anthropic@vkriss.com and the заявка moved to
Изпратени with `Дата на изпращане` set.

## 3. Delete — `requests_delete`
```json
{"requests_delete": {"id": 1}}   // -> {"requests_delete": null}
```
Only while the заявка is NEW (not yet sent). Confirmed working: test заявка #1 was deleted.

## 4. Page/read endpoints (for reference)
`requests_index` (list; tabs = Нови/Изпратени/Заредени/Всички), `requests_edit`
(`?mode=0&ordering_view=0&supplier_id=<id>`), `requests_new`, `requests_sent_request`,
`Articles_search` `{article_name, filters:{article_name}, extra_properties:["min"], mode:"requests_edit"}`
— the row article picker; `extra_properties:["min"]` is what returns Мин/Max кол.

## Creation modes (the „Нова заявка" dialog)
- **Празна заявка** — empty (what we captured).
- **Включи артикулите под минимума** — prefill everything below its minimum stock level.
- **Само артикули на доставчика** — restrict to that supplier's articles.
- Plus „Нова заявка по липси" (from shortages) and Наличност спрямо (which depot's stock to
  measure against) + Групиране по.

## Implications for the panel
The existing `routers/purchasing.py` maps заявка → draft **storeload**, which is now known to be
the WRONG entity. The Заявки tab should use `Requests_save` / `messagehub_send` /
`requests_delete` instead, and the „Включи артикулите под минимума" mode is exactly the
auto-replenishment feature the Заявки page was reaching for.

## Test data note
Заявка #1 was created and deleted (clean). Заявка #2 was created and SENT (to Kris's own inbox,
deliberately, to verify the send flow) — a sent заявка cannot be deleted, so it remains as
history. Harmless: 1 line, 0.00 €, supplier Мира-Н.
