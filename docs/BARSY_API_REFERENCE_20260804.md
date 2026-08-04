# Barsy full API & page reference (darling7.barsy.online, v3.0.1252) — 2026-08-04

Complete extraction of Barsy's undocumented JSON-RPC surface, harvested read-only from every one
of the 480 reachable admin page structures plus the runtime-captured connector methods. This is
the reference the vendor docs don't provide. Companion docs: BARSY_SURVEY (menus + UI patterns),
BARSY_API_MAP (write conventions + captured body shapes), PANEL_PARITY_QUEUE (gaps + queue).

## How to call anything here

- **Page structure / form model**: `POST /endpoints/json?bid=1` with `{"<page_id>":{"id":<id?>,
  "params":{"bid":1}}}` → the page's forms, fields, allowed values, and action targets. The
  `values` keys returned ARE the write schema for that entity.
- **Grid data (any list or report)**: `POST /endpoints/json/<page_id>?bid=1` with
  `{"params":{},"action_type":"values","active_struct_id":"eStructList_1","_search":true,
  "rows":N,"page_num":1,"order_by":...,"order_dir":...,"filters":{...}}` →
  `{rows[],total,records,totals{}}`.
- **Method (action)**: `POST /endpoints/json?bid=1` with `{"<Method>":{...}}`. Capitalized
  targets below are the methods; find each one's parameter shape by reading the page that
  declares it (its `key_name`/`key_value`), or capture the UI firing it. Delete key names are
  per-entity (`Articles_delete{article_id}`, `Clients_delete{client_id}`, `requests_delete{id}`).
  Page-style saves are FULL-REPLACE — read-modify-write always.

## A. Complete distinct method catalog (264)

Access_network_edit, Accounting_ajur_export_anali_config, Accounting_ajur_export_config,
Accounts_edit, accounts_edit, Accounts_place, Accounts_storno, Accounts_view, Amounttypes_edit,
Amounttypes_getList, ArticleDetails_articleDetail, ArticleDetails_deleteKey,
ArticleDetails_getKeyValues, ArticleDetails_save, Articles_article, Articles_create,
Articles_delete, Articles_getList, Articles_getlist, Articles_save, Articles_save_suppliers,
Articles_Search, Articles_search, articles_suppliers, Articles_tips_activate, Articles_update,
Barsys_getList, Cards_articlegroups_edit, Cards_bonus_programs, cards_bonus_programs_instances,
Cards_bonus_programs_instances_edit, Cards_card, Cards_delete, Cards_instances,
Cards_instances_edit, Cards_instances_view, cards_vouchers_instances,
Cards_vouchers_instances_edit, Cards_vouchers_voucher, Categories_getTree, categories_index,
Clientorders_clientorder, Clientorders_create_accounts, Clientorders_edit, clientorders_edit,
Clientorders_statuses_edit, clientorders_view_blank, Clients_client, Clients_delete,
Clients_email_domain_apply, Clients_email_domain_edit, Clients_getGroups, clients_getList,
Clients_getListSmart, Clients_groupedit, Clients_index, Clients_levels_calculate,
Clients_levels_edit, Clients_levels_options, clients_multiplecreate, Clients_save,
Clients_source_edit, Commisions_edit, companies_edit, Companies_edit, Companies_GetList,
Countries_edit, Currencies_currency, deals_cats_edit, deals_deal, deals_edit, Deals_edit,
deals_getList, Deals_getlist, depots_edit, Depots_edit, depots_editgroup, Depots_editgroup,
Depots_getGroupList, Depots_getListCurrent, Destinations_getlist, Destinations_UpdateAll,
Destinations_UpdateOfficial, Documents_intrastats_edit, Documents_intrastats_main_edit,
Ewallets_edit, Forms_edit, Forms_fields_index, invoices_edit, Invoices_edit,
Invoices_getReceivers, Invoices_invoice, Irequests_delete, Irequests_edit, irequests_edit,
Irequests_save, Irequests_view_incoming, Irequests_view_outgoing, Klen_delete,
Labels_cloneTemplate, Labels_templates_edit, Languages_edit, Menus_delete, Menus_edit,
messagehub_send, Messagehub_send, messages_index, messages_new, Messages_view, mon_period_status,
move, Notes_delete, Notes_edit, Notes_view, Orders_statuses_edit, Packages_edit,
paymentmethods_check_connection, paymentmethods_edit, Paymentmethods_history,
Payments_bankstatement_view, Payments_index, Payments_new, Payments_payment,
Payments_requestCheck, Payments_requestView, Persons_getList, Persons_grouptypes_edit,
Persons_index, Persons_infotype, Persons_infotypesedit, Persons_person, Places_delete,
Places_edit, Places_type_delete, Places_type_edit, Places_zoneedit, Poses_admin_register,
Poses_clear_account_overrides, Poses_clear_depot_overrides, Poses_clear_order_overrides,
poses_edit, Poses_edit, Poses_unregister, Pricelists_groups_edit, Pricelists_index,
Printers_delete, Printers_models, Printers_printer, printers_printer, Printers_PrintTest,
Publics_edit, Reasons_production_edit, Reasons_return_edit, Reasons_revision_edit,
Remotesystems_sync_all, Reports_accounts_search, Reports_log, Reports_store_list_details,
Reports_subscriptions_by_clients, Requests_delete, requests_delete, Requests_edit, requests_edit,
Requests_request, Requests_save, Requests_sent_request, Requests_split, Requests_view,
Reservations_delete, Reservations_reservation, Revisions_blank, Revisions_edit, Revisions_view,
Rights_by_barsys_save, Rights_getTree, rights_templates, Roles_clone, roles_edit, Roles_edit,
Roles_getList, salon_schema, Salons_edit, scales_ping, Settings_edit, Showx_groups_edit,
Showx_layouts_edit, Showx_layouts_index, Sittings_edit, Sittings_group_edit, Speditors_edit,
Speditors_SettingsByBarsys, Store_amounts, store_amounts_by_totals, Storecalc_edit,
Storecalc_save, Storecalc_storecalc, storeloads_banktransfer, storeloads_bycopy,
Storeloads_categories_edit, Storeloads_close, Storeloads_create, storeloads_deletecopy,
storeloads_edit, storeloads_index, Storeloads_save, Storeloads_save_autoSave,
Storeloads_storeload, storeloads_storeload, Storemoves_edit, storemoves_edit, Storemoves_save,
Storemoves_storemove, Storeouts_edit, storeouts_edit, Storeouts_reason_edit, Storeouts_save,
Storeouts_storeout, Storeouts_type_edit, Storeproductions_edit, storeproductions_edit,
StoreProductions_producersGetList, Storeproductions_save, Storeproductions_storeproduction,
Storeproductions_view, Streams_depots_irequests_save, Streams_depots_save, streams_edit,
Streams_edit, Streams_getList, Streams_output_options_save, Subscriptions_anulate,
Subscriptions_client, Subscriptions_exec_step1, Subscriptions_getList, subscriptions_subscription,
Subscriptions_subscription, Subscriptions_view, Suppliers_edit, Suppliers_getList,
Suppliers_getlist, Suppliers_groupdelete, Suppliers_groupedit, Suppliers_supplier,
System_Backups_Check, System_Backups_Delete, tare_edit, Tare_edit, Users_getList, Users_user,
Users_user_session, Webhooks_subscribe, workflows_diagram_overview, workflows_workflow,
Workflows_workflow, workingshift_edit, Workingshift_edit, Workschedule_delete, Workschedule_edit,
Workschedule_gcal_sync_row, Workschedule_holiday_edit, Workschedule_list,
Workschedule_requests_view, Workschedule_statuses_edit, Workschedule_view

(Case doubling — `Storeloads_save` vs `storeloads_save` — is real: Barsy exposes some actions
both as a page-id write and a capitalized method. Either envelope works.)

## B. Report / read-grid catalog (226 pages)

The entire Справки tree + stock-amount views. EVERY one is a data grid via
`POST /endpoints/json/<page_id>?bid=1` with the grid envelope — so all of Barsy's reporting is
available to the panel as data. Page ids (labels in the Barsy menu):

klen_index, mon_period_graphs, mon_period_status, mon_rejected_orders, mon_remote_orders,
mon_scheme, mon_summary_by_barsys, mon_totals_index, print_reports, receipts_index,
report_store_outgoes, report_store_outgoes_sum, report_store_store, reports_accounting,
reports_accounting_by_client_orders, reports_accounting_by_sales_invoices,
reports_accounting_payroll, reports_accounting_storeloads, reports_accounts_by_articles,
reports_accounts_by_articles_amount, reports_accounts_by_period_ids, reports_accounts_index,
reports_accounts_search, reports_articles, reports_articles_actual_prices,
reports_articles_by_client_orders_and_clients, reports_articles_delivery_prices,
reports_bonus_programs_and_franchise, reports_cards_and_vouchers, reports_client_orders,
reports_client_orders_details, reports_client_orders_history, reports_client_orders_periods,
reports_client_orders_totals, reports_clients, reports_clients_balance, reports_clients_by_months,
reports_clients_duplicate_unique, reports_clients_groups_balance, reports_clients_groups_totals,
reports_clients_history, reports_clients_history_edit, reports_clients_payments,
reports_clients_recruited, reports_clients_streams, reports_clients_totals, reports_commision,
reports_docs, reports_hours_worked, reports_invoices, reports_invoices_by_tax_groups,
reports_logs, reports_logs_by_action, reports_logs_by_ip, reports_logs_by_users,
reports_logs_graphs, reports_logs_group, reports_lot_list_details, reports_notes, reports_offers,
reports_offers_by_articles, reports_orders, reports_orders_by_users_served,
reports_orders_by_users_served_period_ids, reports_orders_log_full, reports_orders_modificators,
reports_orders_refusals, reports_orders_serve_history, reports_orders_status_change_by_time,
reports_others, reports_payments, reports_payments_barsys, reports_payments_barsys_periods,
reports_payments_barsys_total, reports_payments_by_payment_methods,
reports_payments_by_payment_methods_bymonths, reports_payments_by_payment_methods_period_ids,
reports_payments_by_payment_methods_periods, reports_payments_by_periods,
reports_payments_by_users_paymethods, reports_payments_by_users_period_ids,
reports_payments_details, reports_payments_payment_methods, reports_payments_requests,
reports_payments_users, reports_period_ids, reports_periods, reports_printers,
reports_queue_recalculations, reports_remote_articles, reports_remote_suppliers,
reports_remote_system_logs, reports_reservations, reports_reservations_by_articles,
reports_reservations_by_articles_details, reports_reservations_by_date, reports_revisions,
reports_revisions_group, reports_sales, reports_sales_articles, reports_sales_barsys,
reports_sales_by_articles, reports_sales_by_articles_and_barsys, reports_sales_by_articles_and_hours,
reports_sales_by_articles_and_months, reports_sales_by_articles_and_periods,
reports_sales_by_articles_and_weekdays, reports_sales_by_articles_and_weekdays_comp,
reports_sales_by_barsys, reports_sales_by_barsys_paymethods, reports_sales_by_barsys_periods,
reports_sales_by_cats, reports_sales_by_cats_and_hours, reports_sales_by_cats_and_weekdays,
reports_sales_by_client_persons_months, reports_sales_by_client_persons_total,
reports_sales_by_materials, reports_sales_by_orders, reports_sales_by_orders_by_paymethod,
reports_sales_by_orders_details, reports_sales_by_payment_methods,
reports_sales_by_payment_methods_period_ids, reports_sales_by_payment_methods_periods,
reports_sales_by_persons, reports_sales_by_persons_accounts, reports_sales_by_persons_and_barsys,
reports_sales_by_places, reports_sales_by_poses, reports_sales_by_poses_details,
reports_sales_by_poses_orders, reports_sales_by_poses_periods, reports_sales_by_poses_periods_orders,
reports_sales_by_salons, reports_sales_by_storno, reports_sales_by_streams,
reports_sales_by_streams_and_hours, reports_sales_by_streams_and_periods,
reports_sales_by_streams_bymonths, reports_sales_by_streams_periods, reports_sales_by_tax_groups,
reports_sales_by_users, reports_sales_by_users_paymethods, reports_sales_by_users_period_ids,
reports_sales_by_users_totals, reports_sales_by_users_totals_ii, reports_sales_by_weekdays,
reports_sales_cats, reports_sales_client_persons, reports_sales_clients, reports_sales_orders,
reports_sales_payment_methods, reports_sales_periods, reports_sales_persons, reports_sales_places,
reports_sales_poses, reports_sales_storno, reports_sales_streams, reports_sales_tax_groups,
reports_sales_users, reports_sellout, reports_shared_resources, reports_site_sync, reports_store,
reports_store_amounts, reports_store_amounts_by_date, reports_store_amounts_by_date_and_barsys,
reports_store_amounts_lots, reports_store_irequests, reports_store_irequests_details,
reports_store_irequests_group, reports_store_list_details, reports_store_loads,
reports_store_moves, reports_store_moves_by_articles_barsys, reports_store_moves_details,
reports_store_moves_group, reports_store_outs, reports_store_outs_by_articles,
reports_store_outs_by_barsys, reports_store_outs_details, reports_store_outs_group,
reports_store_productions, reports_store_productions_details, reports_store_productions_details_pu,
reports_store_productions_differences, reports_store_productions_totals, reports_storeloads,
reports_storeloads_by_barsys, reports_storeloads_by_cats, reports_storeloads_by_ops,
reports_storeloads_by_periods, reports_storeloads_by_storeload_cat_and_periods,
reports_storeloads_by_streams, reports_storeloads_by_supplier_groups,
reports_storeloads_by_supplier_groups_bymonths, reports_storeloads_by_suppliers,
reports_storeloads_changed_deliveryprices, reports_storeloads_details, reports_storeloads_totals,
reports_subscriptions, reports_subscriptions_by_articles, reports_subscriptions_by_client_groups,
reports_subscriptions_by_client_groups_totals, reports_subscriptions_by_clients,
reports_subscriptions_by_clients_totals, reports_subscriptions_by_periods,
reports_subscriptions_by_plans, reports_subscriptions_details, reports_systems,
reports_unsold_articles, reports_used_cards, reports_used_cards_by_clients,
reports_used_cards_by_periods, reports_users, reports_users_workflow, reports_wrong_data,
store_amounts, store_amounts_by_barsys, store_amounts_by_barsys_lots, store_amounts_by_depot_groups,
store_amounts_by_depot_groups_lots, store_amounts_by_stores, store_amounts_by_stores_lots,
store_amounts_by_totals, store_amounts_lots, store_amounts_lots_by_date

## C. Reports most relevant to us (retire the local seed page by page — task #84)

- Sales: reports_sales_by_periods, reports_sales_by_articles, reports_sales_by_cats,
  reports_sales_by_payment_methods (COD vs card split), reports_sales_by_storno (refund rate),
  reports_sellout, reports_unsold_articles.
- Stock: store_amounts / store_amounts_by_stores (live availability), reports_store_amounts_by_date
  (stock as of a date), reports_storeloads_by_suppliers (purchasing by supplier),
  reports_articles_actual_prices (price-change audit), reports_articles_delivery_prices (cost
  changes), reports_storeloads_changed_deliveryprices.
- Ops: receipts_index (printed receipts + УНП — feeds #72), print_reports / klen_index (fiscal),
  reports_site_sync (e-shop sync log — what Barsy thinks it pushed to the storefront),
  reports_logs (audit), reports_wrong_data (data-quality check we can mirror in Проблеми).
- Money: reports_accounting_by_sales_invoices, reports_accounting_payroll (оборотна ведомост),
  reports_payments (incl. Извлечение банка feed for Разчети).

## D. Out-of-scope modules (documented for completeness; NOT to be built)

Confirmed out of scope for a home-goods e-shop, kept here so "the full power of Barsy" is on
record and a future decision is informed, not blind:

- **Салони / места / зони / схема** (Salons_edit, Places_edit, Places_zoneedit, salon_schema,
  Sittings_edit) — restaurant floor maps + table reservations.
- **Резервации** (Reservations_reservation, reservations_booking) — table booking.
- **Абонаменти** (Subscriptions_* — anulate/client/exec_step1/subscription/view) — recurring
  billing plans.
- **Пакетни карти / ваучери / бонус програми** (Cards_* , Cards_vouchers_*, Cards_bonus_programs_*)
  — loyalty/gift-card engine.
- **Производство** (Storeproductions_* , StoreProductions_producersGetList) — manufacturing/recipes.
- **Работни графици / смени / комисионни** (Workschedule_*, Workingshift_edit, Commisions_edit,
  reports_hours_worked) — staff scheduling + commission schemes.
- **Работни процеси** (Workflows_workflow, workflows_diagram_overview) — order-processing state
  machines (relevant only if we ever model fulfilment stages in Barsy).
- **Гаранции / договори / оферти / протоколи / сделки** (documents_* , Deals_*) — B2B document +
  CRM pipeline. Оферти/Deals could matter for a future B2B/Horeca lane; parked.
- **Партиди / lot tracking** (lot_type on articles, *_lots reports) — expiry/batch; not our goods.
- **Пакетни карти на модификатори, модификатори** (modificators) — menu modifiers (restaurant).
- **Интрастат** (Documents_intrastats_*) — EU trade stats; only past a threshold.
- **Счетоводни експорти** (Accounting_ajur_export_* + Плюс Минус/Бизнес Навигатор/SAP/НАП/…) —
  IN SCOPE conceptually: the accountant uses Ажур. Keep the export path in mind for the
  accountant hand-off, though the panel need not build it (run it from Barsy directly).

## E. Notable methods worth remembering (in-scope, not yet wired)

- ArticleDetails_save + ArticleDetails_getKeyValues — product characteristics (task #86, HIGH).
- Articles_save_suppliers — multi-supplier links (DONE c16eea9).
- Clientorders_create_accounts — convert a client order into fiscal accounts (task #82).
- Poses_admin_register / Poses_unregister — register this device as a каса (needed for panel
  order/payment writes; the connector identity already passes the gate).
- Destinations_UpdateAll / Destinations_UpdateOfficial — refresh courier office & locality
  catalogs (Speedy/Econt address data).
- Invoices_getReceivers, Invoices_invoice, Invoices_edit(@merge) — invoicing incl. from a payment.
- Payments_bankstatement_view + reports_payments_requests — bank-statement reconciliation feed.
- Remotesystems_sync_all, reports_site_sync — e-shop/remote-system sync control + log.
- Storecalc_save — the складов калкулатор (what-if stock costing).
- clients_multiplecreate — bulk client import (repeat-customer backfill from Woo/Shopify).

## Extraction method (auditability)

Read-only: 480 page structures fetched via the page-structure envelope; action `target`s parsed
out; merged with methods captured live while probing (article/client/order create+delete, supplier
link save). No writes in this pass. 264 distinct methods, 226 report grids, 0 page errors.
