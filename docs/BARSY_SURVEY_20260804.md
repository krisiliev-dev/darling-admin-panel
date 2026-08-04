# Barsy platform survey — every menu, the API behind it, and the panel gap map (2026-08-04)

Captured live from darling7.barsy.online (v3.0.1252) with an XHR/fetch hook while walking the
admin menus. Read-only pass: list pages opened, edit forms LOADED (never saved), zero writes.

## The one API pattern that unlocks everything

Every page in Barsy talks to the server the same two ways:

1. `POST /endpoints/json?bid=1` with `{"<page_id>": {"params": {...}}}` — returns the PAGE
   STRUCTURE (forms, fields, allowed values, buttons). `page_id` is exactly the URL hash:
   `#articles` → `articles_index`, `#clients_edit` → `clients_edit`.
2. `POST /endpoints/json/<page_id>?bid=1` with
   `{"params":{}, "action_type":"values", "active_struct_id":"eStructList_1", "_search":true,
     "rows":N, "page_num":1, "order_by":..., "order_dir":..., "filters":{...}}`
   — returns the GRID DATA `{page_num, total, records, rows[], totals{}}`.

**Consequence: the 500-entry menu inventory below IS the read-API inventory.** Any list, any
report, any config table is fetchable by its page id with the same envelope — including every
Справки report (`reports_sales_by_periods`, `store_amounts_by_stores`, …). The connector's
недокументирани methods (`Articles_getlist`, `Requests_save`, …) are the RPC layer; the page ids
are a second, complete surface for reads.

## Menu inventory (528 links; sections + notable pages)

**Каталог**: Артикули · Категории · Модификатори · Ценови правила (правила/групи) · Клиенти ·
Доставчици · Спедитори · Потребители (списък, работни графици, заявки) · Публичен достъп ·
Бележки · Актуализация везни.

**Склад**: Зареждания (отворени/затворени/нови/по копия) · Прехвърляния · Изписвания ·
**Производство** (storeproductions) · Ревизии · **Заявки-вътрешни** (irequests, с матрица и
шаблони) · Заявки-доставчици (requests — вече наши) · Складов калкулатор (storecalc).

**Каса**: Нова продажба (accounts_edit) · Сметки (отворени/затворени) · Текущи поръчки /
Обработка на поръчки · Плащания (последни, ново, **Извлечение банка** payments_bankstatements) ·
Клиентски заявки (списък, **Календар доставки**, нова) · Пакетни карти · Ваучери · Абонаментни
сметки · Резервации (със салонна схема) · Фискални устройства.

**Документи**: Фактури · Гаранции (със статуси + шаблони) · Договори · Оферти ·
Приемо-предавателни протоколи · Интрастат · Сделки (CRM pipeline: активни/мои/категории).

**Справки** (~200 reports): Мониторинг (обороти, натоварване, по ТО) · Продажби (по
периоди/артикули/категории/обекти/потоци/потребители/клиенти/сметки/поръчки/сторно/начини на
плащане/ДДС групи) · Плащания · Склад (наличности вкл. **към дата** и **по партиди**, движения,
изписване, прехвърляния, зареждания по доставчици/групи/месеци, **промени доставни цени**,
ревизии) · Потребители (отработени часове, комисионни) · Клиенти (баланс, история, дубликати) ·
Артикули (**промени в продажни цени**, промени в себестойност, **синхронизация е-shop**) ·
Клиентски заявки · Фактури · Счетоводни (**експорт към Ажур/Плюс Минус/Бизнес
Навигатор/SAP/НАП/МикроАкаунт/ДелтаПро**) · Дневник събития (по действия/потребители/IP) ·
Фискални отчети · **КЛЕН** (klen_index) · Отпечатани бележки (receipts_index).

**Настройки**: Търговски обекти · Каси · Устройства · Фирми · Складове · Начини на плащане ·
Банкови сметки · Валути/Езици/Държави/Населени места · Клиенти (групи, прагове, източници,
е-портфейли) · Доставчици (списък, групи) · Потребители (роли, права по роли/по ТО, заявки за
права) · Достъп (профили, мрежи) · Артикули (характеристики, групи атрибути, мерни единици,
**разфасовки**, тара, масов превод) · Карти/ваучери/бонус програми · Обслужване (работни процеси,
статуси на поръчки, причини отказ) · Потоци · Причини (ревизии, производство, изписване) ·
Други (редактор етикети, **статуси на клиентски заявки**, складови категории, **шаблони за
e-mail**, формуляри) · Системни (отдалечени системи, репликация, архиви, **Webhooks: събития +
абонаменти**, изчистване на данни).

## Verified facts the panel needs

**The articles webhook IS live.** `webhooks_subscriptions` shows subscription 1, user `claude`,
event „Промяна при артикули", status 1, pointing at `panel.darlinghome.bg/webhooks/barsy/article`
— and `webhooks_events` shows deliveries from 2026-08-03. The round-5 owner action is done.
Two caveats: (a) it authenticates via the deprecated `?token=` query param, not the
`X-Darling-Token` header; (b) that token was echoed into a session transcript during this
capture — **rotate it** and re-subscribe with the header. Only the `articles` event is
subscribed; `categories`, `clientorders`, `clients`, `accounts` events exist and are one
`Webhooks_subscribe` away.

**Config tables (live values):**
- Начини на плащане: 1 В брой · 2 Карта (присъствена) · 3 Кредит · 4 Чек · 5 По банка ·
  **6 Наложен платеж** (the COD paymethod_id the connector must book with).
- Складове: 1 Основен · 2 „ТЕСТ — Склад 2 (изтрий преди старт)" ← still to delete.
- Статуси на клиентски заявки: 1 Отворена · 2 За обработка · 3 Приета · 4 Готова ·
  5 Приключена · 6 Отказана — with per-status flags `can_edit_rows`, `ask_for_barsy`,
  **`reserve_amount`** (a client-order status can RESERVE stock).
- Причини отказ: 12 refusal reasons incl. per-reason `upd_store` (whether stock returns).

**Спедитори са първокласен обект в Barsy.** `speditors_edit` offers native provider classes:
**Speedy, Econt, Leo, Pick Up, Post Office, euShipments** (no BoxNow), with
to-address/to-office flags and provider settings. And `clientorders_edit` carries
`speditor_id`, `tracking_number`, `delivery_address`, `delivery_date/until`, `with_invoice`,
`preferred_paymethod_id` — a Barsy client order natively models the shipment.

**Full article model** (`articles_edit`): article_name, article_name_public_ml (multilang!),
article_aliases, ref_num, barcode, actual_price, avg_delivery_price, max_discount,
min_profit_percent, amount_type, stream, tax, lot_type, part_factor, is_for_sale /
is_for_client_order / is_for_load / is_for_store_move / is_for_store_out / is_for_irequest /
is_for_request / is_service / is_public, min/max_amount (+ **by_depots**), min/max_order, code,
intrastat, description_ml + description_public_ml, default_package_id, sale_below_zero +
storemove/storeload_return/storeout_below_zero, process_time, article_icon, article_color,
**slug**, export_id, ref_id.

**Full client model** (`clients_edit`): name/tel/email/auth, group, discount, client_code,
credit_limit, paid_period, card-less permissions, invoice block (company, bulstat, vat_num, mol,
email_invoices), delivery_address, lang, source, recruiter, ml_subscribed, e-wallets.

## UI worth copying into the panel

1. **Column picker done right** („Настройка" on every list): modal with tabs Колони / Филтри /
   Други; a SEARCH box over columns; columns grouped by section (ЦЕНИ, СЕБЕСТОЙНОСТ И ПЕЧАЛБА…);
   radio = sort column, checkbox = visibility; „Запази настройките" persists per user. Plus
   „Добави собствена колона" and „Вземи настройки от предишни". This is the spec for our
   ProductList wrench icon.
2. **Collapsed filters**: 4-5 primary filters inline, „+ още 18 филтъра" expands, explicit
   „Приложи филтрите" button (no per-keystroke refetch), quick date chips Период / Къс период /
   Месец / Дата with ◀ ▶ steppers.
3. **Bulk operations toolbar** on lists: Клиентски заявки has Печат протоколи · Смяна статус ·
   Направи обща сметка · Създай фактура · Направи сметки. Артикули has Масови операции ·
   Печат на етикети · Баркодове · Импорт.
4. **Grouping dropdown** („Без групиране" → group rows by a column) and print + export icons on
   every single list.
5. Per-row ⓘ info popover and a **bell** (watch/subscribe to changes on a row).
6. Breadcrumb + sub-tab row (Каса » Клиентски заявки » Всички/Календар/Нова) — same shape as
   our folder tabs, validating that IA.

## Panel ⇄ Barsy wiring map

Correctly wired today: products (Articles_* + articles webhook + per-code reconcile), deliveries
(Storeloads), store ops (storeouts/moves/revisions via dry-run-gated writes), заявки (`requests`
as of 149f8ad), suppliers (Suppliers_*), invoices (own queue), COD (Accounts + storno path).

Gaps found, in priority order:

1. **Клиентски заявки are the missing middle of the order flow.** Woo orders currently book
   straight to Accounts (fiscal). Barsy's own model is Clientorder (reserves stock via status,
   carries speditor + tracking + delivery calendar, converts to сметка/фактура with built-in
   bulk ops). Wiring Woo checkout → `Clientorders_save` (status За обработка), fulfilment →
   status Готова + tracking_number, delivery → Направи сметка, would give stock reservation and
   an ERP-visible order pipeline instead of a panel-only one. This also matches the existing
   `clientorders` webhook event for real-time sync back.
2. **Клиенти: the panel never creates/updates Barsy clients.** Repeat Woo customers should
   land in `Clients_save` (email/tel/delivery_address/invoice block), so credit limits, groups
   and история work. Today only GDPR lookup exists.
3. **Fiscal УНП source for Accounts_storno (task #72)**: Справки → Отпечатани бележки
   (`receipts_index`) / КЛЕН (`klen_index`) list printed receipts — the receipt's УНП should be
   retrievable per account from there at booking time. Probe next session.
4. **Article fields the panel editor doesn't expose yet**: slug (storefront-relevant!),
   article_name_public_ml / description_public_ml (public vs internal naming — maps exactly to
   our Име/Публично име split), min/max_amount **by depots**, разфасовки (default_package_id),
   is_for_* visibility flags beyond sale, article_aliases (we only write them on create).
5. **Reports as data**: Анализи uses a local sales seed; every Barsy report is one grid POST
   away (e.g. `reports_sales_by_periods`, `store_amounts_by_stores`,
   `reports_storeloads_by_suppliers`, `reports_articles_actual_prices` = price-change audit).
   The seed can be retired page by page.
6. **Webhook coverage**: subscribe `categories`, `clientorders`, `clients` events so taxonomy
   and order state stay real-time like articles do. And rotate + header-auth the token (above).
7. **Ценови правила (Pricelists)**: panel reads them for promos; Barsy also has pricelist
   GROUPS and per-client-group pricing — untouched, relevant for B2B/Horeca tiers later.
8. Available but unused (decide deliberately, not by omission): производство, вътрешни заявки,
   складов калкулатор, пакетни карти/ваучери/бонус програми, абонаменти, резервации, гаранции,
   договори, оферти, протоколи, сделки (CRM), извлечение банка (bank statement import — could
   feed Разчети reconciliation), счетоводни експорти (Ажур export for the accountant!).

## Test residue noticed

Склад 2 („ТЕСТ — изтрий преди старт") still exists; заявка #2 (sent, история); client
„Тест Клиент" and „ТЕСТ ФИРМА ЕООД" visible in account history rows (accounts are
support-reset-only, known).
