# Barsy API map — conventions, captured write shapes, and the panel wiring checklist (2026-08-04)

Second pass over darling7.barsy.online (v3.0.1252), this time WITH writes: test records were
created through the real UI to capture the exact bodies, then deleted. Residue: none — probe
article 30747 deleted, probe client 40 deleted, the probe клиентска заявка was refused by the
registered-каса gate (see below), so nothing was created. Companion doc:
BARSY_SURVEY_20260804.md (menus + read API + UI patterns).

## The write conventions (this is the master key)

Barsy has TWO write styles, and both are now verified against live captures:

**A. Page-style** — most edit pages save to THEIR OWN page id:
```json
{"<page_id>": {"id": <id-or-null>, "action_type": "save",
               "active_struct_id": "eStructForm_1", "values": {...}}}
```
- `id: null` creates, an id updates. Response = the new id (`{"clients_edit":40}`).
- Row-grid pages use `action_type: "save_list"` and add `"rows":[...]` (clientorders), or a
  named struct (`active_struct_id: "eStructListForm_suppliers"`).
- The `values` keys are EXACTLY the form's field names — which come back from the read call
  `{"<page_id>":{"id":N,"params":{}}}`. So for any entity: read the page structure, and you hold
  the complete write schema. No more one-method-at-a-time discovery.

**B. Method-style** — actions with capitalized targets, declared in each page structure's
`global_actions`/`data_actions` as `"target"` (+ `key_name`/`key_value` for the id parameter):
`Articles_save`, `Articles_save_suppliers`, `Storeloads_save`, `Requests_save`,
`Clientorders_create_accounts`, `Invoices_save`, `Payments_new`, `Articles_delete`…

**Delete key names are per-entity, not generic** (verified the hard way):
`Articles_delete {article_id}` (a bare `{id}` → server error), `Clients_delete {client_id}`,
`requests_delete {id}`. When wiring a delete, read the list page's `data_actions` for the
`key_value` instead of guessing.

## Captured write shapes (live, from the UI's own requests)

**Articles_save** (create → returned id 30747):
```json
{"Articles_save":{"values":{"article_name":"…","ref_num":"","barcode":"","actual_price":"0.01",
 "avg_delivery_price":"","max_discount":"","min_profit_percent":"","amount_unit":"1.000000000",
 "amount_type_id":"1","stream_id":"1","tax_id":"2","lot_type_id":"1","part_factor":"0",
 "delete_flag":0,"is_for_sale":1,"is_for_client_order":1,"is_for_load":1,"is_for_store_move":1,
 "is_for_store_out":1,"is_for_irequest":1,"is_for_request":1,"is_service":0,"is_public":1,
 "min_amount":"","max_amount":"","min_order":"0","max_order":"0","code":"","intrastat":"",
 "user_prc":"","description_ml_sub":"","description_public_ml_sub":"","default_package_id":"",
 "sale_below_zero":"DEF","storemove_below_zero":"DEF", …}}}
```

**Articles_save_suppliers** — THE multi-supplier contract (page `articles_suppliers?id=<article>`,
„Задай доставчици"). Full row-replace ("rows" is the whole set):
```json
{"Articles_save_suppliers":{"id":30747,"action_type":"save","values":{},
 "rows":[{"supplier_id":39,"supplier_name":"МИРА-Н-НАДКА ВАСИЛЕВА","is_default":0,
   "default_delivery_price":"","package_id":"","remote_ref_id":"MIRA-CODE-77",
   "remote_article_name":"ТЕСТ АРТ. ПРИ МИРА"}]}}
```
Row fields: supplier_id, is_default (основен доставчик), default_delivery_price (договорена
цена), package_id (разфасовка), **remote_ref_id = THEIR code**, **remote_article_name = THEIR
name**, plus read-only last_delivery_price / last_load and a weight/ordering action.

**clients_edit save** (create → returned id 40): values = the full client model
(client_name, tel, email, client_auth, group_id, discount, credit_limit, allow_*_nocard flags,
barsys[], client_source_id, invoice block: company_name/bulstat/vat_num/mol/email_invoices,
receiver_name/town/address/zip, default_paymethod_id, ml_subscribed, delivery_address, …).

**clientorders_edit save_list** (captured; REFUSED by the каса gate — shape still exact):
```json
{"clientorders_edit":{"id":null,"action_type":"save_list","values":{
  "order_num":null,"delivery_date":null,"delivery_date_until":null,"client_id":null,
  "client_name":"","deal_id":null,"person_id":null,"discount":"0","contact_name":null,
  "client_tel":null,"client_email":null,"delivery_barsy_id":"1","extra_details":{},
  "speditor_id":null,"tracking_number":null,"with_invoice":0,"currency_id":"1",
  "preferred_paymethod_id":null,
  "delivery_address":{"delivery_type":"address","delivery_type_title":"Доставка до адрес",
                      "address":"{\"address\": \"\"}"},
  "description":"","currency_rate":"1","auto_save":1},
 "rows":[{"client_order_row":"","article_id":11365,"article_name":"Доставка","amount":"1",
   "original_price":"0.00","discount":"0","detail_status":"1","detail_notes":"…","details":null}]}}
```

**The registered-каса gate**: saving a клиентска заявка from an unregistered browser returns
„Текущата заявка изисква да бъде изпратена от регистрирана каса за текущия търговски обект:
Darling7 (ID:1)". The same gate blocked account voids in the 08-03 wipe. There is an API for it —
`Poses_admin_register` (found on payments_new) registers the current device as a каса. The
CONNECTOR's writes work, so its identity is already acceptable; any future panel feature that
писhes client orders/payments must go through the connector's identity, not a fresh session.

## Harvested method targets (from page-structure actions; not exhaustive of params)

- Каталог: Articles_Search, Articles_save, Articles_delete{article_id}, Articles_save_suppliers,
  Categories_getTree, Suppliers_getList, Suppliers_supplier{supplier_id}, Suppliers_edit,
  Amounttypes_getList, Streams_getList, clients_multiplecreate, Clients_getListSmart,
  Clients_getGroups, Clients_client{client_id}, Clients_delete{client_id}, Users_getList,
  Users_user, Roles_getList.
- Склад: Storeloads_save (UI; the connector's Storeloads_create also live),
  Storeloads_save_autoSave, Storeloads_save@confirm_storeload_close,
  Storeloads_storeload{store_load_id} (view), storeloads_bycopy, **storeloads_banktransfer**,
  Storeloads_categories_edit, Storemoves_save(@confirm_save/@confirm_move),
  Storeouts_save, Irequests_save, Revisions_edit{rev_id}/Revisions_view/Revisions_blank,
  Requests_save / messagehub_send / requests_delete{id} (earlier capture).
- Каса: Accounts_view{account_id}, **Clientorders_create_accounts** (заявка → сметки),
  Clientorders_edit{client_order_id}, Clientorders_clientorder (view),
  Clientorders_getDeliveries (calendar), Payments_new, Payments_payment{payment_id},
  Poses_admin_register.
- Документи: Invoices_save, Invoices_edit{invoice_id}, Invoices_print, **Invoices_mail**,
  Invoices_edit@merge_invoices, Invoices_getReceivers, invoices_edit{payment_id} (invoice from
  payment!).
- Спедитори: Speditors_edit{speditor_id}, Speditors_SettingsByBarsys, Destinations_UpdateAll /
  Destinations_UpdateOfficial (refresh office/locality catalogs).
- Настройки: Depots_edit{depot_id}, Depots_editgroup, Webhooks_subscribe (connector-known).

## Panel wiring checklist (what to fix, in order)

1. **Suppliers on a product — WRONG TARGET today.** Нов продукт pushes the first coded supplier
   row into `ref_num` and folds differing names into `article_aliases`. Barsy's real model is
   `Articles_save_suppliers`: N suppliers per article, each with their own code/name/price/
   default flag. Fix `_save_to_barsy`/`push_to_barsy` (intake.py) to call it after
   Articles_create with ALL DraftSupplier rows; ref_num can stay as the Microinvest code. Also
   surface „Основен" + договорена цена in the editor's supplier table — the columns exist in
   Barsy and our table doesn't carry them.
2. **Full-replace semantics everywhere**: Articles_save_suppliers and the page-style saves send
   the complete values/rows set — a partial body BLANKS fields (the users_edit lesson,
   now confirmed as a general rule). Every panel update must read-modify-write.
3. **Catalog editor read path**: `articles_edit{id}` returns the complete article + form model in
   one call — the catalog editor can use it to stay in step with the live article form
   (CLAUDE.md rule) instead of composing from Articles_getlist columns.
4. **Заявки tab**: also render supplier links from `articles_suppliers{id}` when picking
   products for a supplier — Barsy already knows which supplier carries which article.
5. **Clientorders rewire (from the survey)**: shapes now captured; note the каса gate — route
   through the connector's identity.
6. **Deletes**: use per-entity key names from data_actions; never send bare `{id}` on Articles.

## Probe log (auditability)

Created & deleted: article 30747 („ТЕСТ-API изтрий", 0.01 €) + its supplier link to Мира-Н;
client 40 („ТЕСТ-API КЛИЕНТ изтрий"). Refused, nothing created: клиентска заявка (каса gate).
Deliberately NOT probed: Нова продажба / фактура (fiscal + numbered documents — indelible),
плащания (money history), производство/ревизии (stock effects).
