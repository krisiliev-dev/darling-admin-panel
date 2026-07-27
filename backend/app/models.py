# -*- coding: utf-8 -*-
"""Auth + audit tables (admin.db). Roles: owner / staff / packer.
`is_superuser` mirrors role=="owner" and gates FastAPI-Users' admin routes."""
import datetime
import uuid

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import DateTime, Index, LargeBinary, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# task-oriented roles: owner = local break-glass superuser; manager = Barsy Мениджър
# (full access); staff/packer = limited. Full access is signalled by is_superuser.
ROLES = ("owner", "manager", "staff", "packer")


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"
    # Partial unique index: AT MOST ONE owner ever — DB-enforced, closing the
    # first-run setup race (two concurrent /setup calls with different emails).
    __table_args__ = (
        Index("uq_single_owner", "role", unique=True,
              sqlite_where=text("role = 'owner'")),
    )
    # SQLAlchemyBaseUserTableUUID gives: id, email, hashed_password,
    # is_active, is_superuser, is_verified
    full_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(16), default="staff", index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class IntakeDraft(Base):
    """An approved product draft from the intake flow. Panel-owned (admin.db) — a safe
    staging record between human approval and creation in Barsy. Price is staff-entered
    (never AI — price policy). `status`: approved → pushed (to Barsy)."""
    __tablename__ = "intake_drafts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    # operational fields (human) — the AI never sets these
    code: Mapped[str] = mapped_column(String(64), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(64), default="")
    price: Mapped[str] = mapped_column(String(32), default="")   # euros, as string
    unit: Mapped[str] = mapped_column(String(16), default="бр")
    # enriched fields (AI draft, human-approved)
    name: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    attributes_json: Mapped[str] = mapped_column(Text, default="[]")
    # SEO / Shopify metafields — pipeline suggestions, human-editable (mapped to darling.* on push)
    meta_title: Mapped[str] = mapped_column(String(255), default="")
    meta_description: Mapped[str] = mapped_column(Text, default="")
    # formal Shopify product-taxonomy id (e.g. "hg-11-2"); `category` holds the human breadcrumb
    shopify_category_id: Mapped[str] = mapped_column(String(64), default="")
    brand: Mapped[str] = mapped_column(String(128), default="")   # canonical brand (→ Shopify vendor)
    # orderable at <=0 stock (backorder / supplier dropship, e.g. Mira-N) → Barsy sale_below_zero='YES'
    oversell: Mapped[bool] = mapped_column(default=False)
    weight_kg: Mapped[str] = mapped_column(String(16), default="")  # kg, string → Barsy extra_data.weight_kg
    model: Mapped[str] = mapped_column(String(64), default="")
    # lifecycle. For delivery-sourced products: new_from_delivery → done_from_delivery (enriched,
    # stays checkmarked in the queue) → the whole delivery leaves together when it's finalised.
    status: Mapped[str] = mapped_column(String(24), default="approved", index=True)
    barsy_article_id: Mapped[int | None] = mapped_column(nullable=True)
    approved_by: Mapped[str] = mapped_column(String(255), default="")
    pushed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # when this draft is a NEW product that arrived on a delivery, link back to the delivery line
    # so pushing it to Barsy can write the resulting article_id onto that line (finalise gate).
    delivery_line_id: Mapped[int | None] = mapped_column(nullable=True, index=True)


class DraftImage(Base):
    """A product image staged against an IntakeDraft (panel-owned, admin.db). Held here until the
    article is created in Barsy, then uploaded via Files_upload and marked. Images are MANDATORY for a
    new product (the enrichment pipeline reverse-image-searches them; the shop needs a photo)."""
    __tablename__ = "draft_images"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(index=True)
    filename: Mapped[str] = mapped_column(String(255), default="image.jpg")
    content_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_barsy: Mapped[bool] = mapped_column(default=False)   # true once pushed to Barsy Files
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class DeliveryBatch(Base):
    """A goods-in delivery being received (Доставки page). Panel-owned staging (admin.db): staff
    upload the supplier sheet, scan-confirm quantities, add any new products, then FINALISE — which
    sends the whole thing to Barsy as ONE storeload. Barsy stays the system of record."""
    __tablename__ = "delivery_batches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    supplier: Mapped[str] = mapped_column(String(255), default="")
    supplier_id: Mapped[int | None] = mapped_column(nullable=True)   # Barsy supplier id
    depot_id: Mapped[int] = mapped_column(default=1)                 # warehouse (1 = Основен)
    reference: Mapped[str] = mapped_column(String(128), default="")  # supplier doc / invoice №
    doc_date: Mapped[str] = mapped_column(String(32), default="")    # YYYY-MM-DD
    source: Mapped[str] = mapped_column(String(16), default="excel")  # excel | csv | photo
    # draft (uploaded, scanning) → awaiting_products (has un-added new products) →
    # ready (all confirmed + every line has a Barsy article) → finalised (storeload sent)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    storeload_id: Mapped[int | None] = mapped_column(nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    finalised_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class Zayavka(Base):
    """A per-supplier purchase request (заявка) — a panel-owned outbound order document. One open
    'draft' per supplier accumulates products; sending it (email/export) archives it as 'sent'.
    Barsy stays system-of-record for STOCK — the storeload happens later, at receiving (Доставки)."""
    __tablename__ = "zayavki"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    supplier: Mapped[str] = mapped_column(String(255), default="", index=True)
    supplier_id: Mapped[int | None] = mapped_column(nullable=True)
    supplier_email: Mapped[str] = mapped_column(String(255), default="")
    # draft (open cart) → sent → received (goods came in via Доставки) / cancelled
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_via: Mapped[str] = mapped_column(String(32), default="")   # email | export


class ProductCache(Base):
    """Local mirror of Barsy's article catalogue so search/filter is INSTANT and doesn't hit the
    low-tier Barsy per keystroke. Barsy stays system of record; this is a read cache, kept fresh by
    write-through (panel edits) + a periodic full re-sync. Enables attribute filtering (Цвят:Червен)
    and bulk operations over a selected set."""
    __tablename__ = "product_cache"

    article_id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), default="", index=True)
    name: Mapped[str] = mapped_column(String(255), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(64), default="", index=True)
    category: Mapped[str] = mapped_column(String(255), default="", index=True)
    supplier: Mapped[str] = mapped_column(String(255), default="", index=True)
    brand: Mapped[str] = mapped_column(String(128), default="", index=True)
    shopify_category_id: Mapped[str] = mapped_column(String(64), default="")   # leaf for name recompose
    desc_lc: Mapped[str] = mapped_column(Text, default="")            # lowercased description, for "contains" filter
    price: Mapped[float | None] = mapped_column(default=None)          # actual_price (€)
    stock: Mapped[float | None] = mapped_column(default=None)
    min_stock: Mapped[float | None] = mapped_column(default=None)
    low: Mapped[bool] = mapped_column(default=False)
    attributes_json: Mapped[str] = mapped_column(Text, default="[]")   # [{key,value,key_id,value_id}]
    has_image: Mapped[bool] = mapped_column(default=False)
    # name+code+barcode, Python-lowercased — SQLite LIKE doesn't case-fold Cyrillic, so we search
    # this precomputed field with a lowercased query.
    search_lc: Mapped[str] = mapped_column(String(400), default="", index=True)
    # "key=value|key=value" lowercased — for attribute filtering (Цвят=Червен) via LIKE
    attr_lc: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class SupplierPref(Base):
    """Persistent per-supplier settings for заявки — so email + minimum-order + lead time survive
    across orders (a supplier, not a single заявка, owns these)."""
    __tablename__ = "supplier_prefs"

    supplier: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    min_order: Mapped[float] = mapped_column(default=0.0)   # € below which a заявка isn't worth sending
    lead_days: Mapped[int] = mapped_column(default=0)       # typical days from order to delivery
    website: Mapped[str] = mapped_column(String(500), default="")  # supplier's order/catalogue site
    phone: Mapped[str] = mapped_column(String(64), default="")     # contact phone
    notes: Mapped[str] = mapped_column(Text, default="")
    # Barsy supplier identity — so supplier edits can REFLECT to Barsy (Suppliers_create/update).
    # barsy_supplier_id decides create-vs-update; the rest map to SupplierData fields.
    barsy_supplier_id: Mapped[int | None] = mapped_column(nullable=True, default=None)
    official_name: Mapped[str] = mapped_column(String(255), default="")  # legal name (else `supplier`)
    bulstat: Mapped[str] = mapped_column(String(32), default="")   # ЕИК
    vat_num: Mapped[str] = mapped_column(String(32), default="")   # ДДС номер
    mol: Mapped[str] = mapped_column(String(255), default="")      # МОЛ (single value)
    address: Mapped[str] = mapped_column(String(500), default="")


class ZayavkaLine(Base):
    __tablename__ = "zayavka_lines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    zayavka_id: Mapped[int] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(64), default="")          # OUR product code
    supplier_code: Mapped[str] = mapped_column(String(64), default="")  # the supplier's own code
    name: Mapped[str] = mapped_column(String(255), default="")
    qty: Mapped[float] = mapped_column(default=1.0)
    delivery_price: Mapped[float] = mapped_column(default=0.0)         # per-unit cost (from mapping)
    barcode: Mapped[str] = mapped_column(String(64), default="")


class DeliveryLine(Base):
    """One product row within a delivery. `scanned_qty` accrues from the handheld barcode scanner;
    the line goes green when it equals the claimed `qty`, red on a mismatch."""
    __tablename__ = "delivery_lines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(index=True)
    supplier_code: Mapped[str] = mapped_column(String(64), default="")  # THEIR code from the sheet
    name: Mapped[str] = mapped_column(String(255), default="")
    qty: Mapped[float] = mapped_column(default=0.0)                # claimed on the document
    price: Mapped[float] = mapped_column(default=0.0)             # delivery/cost price
    barcode: Mapped[str] = mapped_column(String(64), default="", index=True)
    is_new: Mapped[bool] = mapped_column(default=True)
    match_code: Mapped[str] = mapped_column(String(64), default="")  # OUR code, if matched
    match_method: Mapped[str] = mapped_column(String(16), default="")
    match_confidence: Mapped[str] = mapped_column(String(16), default="")
    article_id: Mapped[int | None] = mapped_column(nullable=True)  # Barsy id (existing or once added)
    scanned_qty: Mapped[float] = mapped_column(default=0.0)
    # pending → confirmed (scanned == qty) → mismatch (scanned != qty)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    intake_draft_id: Mapped[int | None] = mapped_column(nullable=True)  # for new products


# NOTE: supplier orders (заявки) live in BARSY as draft storeloads (складови зареждания),
# not in a local table — Barsy is the system of record. See routers/purchasing.py.


class NameRecipe(Base):
    """A naming 'recipe' = the v3 composer's style overrides ({attr: variant}, e.g. Color:suffix). One
    GLOBAL row (leaf_id="") is the default; a per-leaf row overrides it for that Shopify leaf, so new
    products in that leaf auto-apply it. Panel-owned (admin.db)."""
    __tablename__ = "name_recipes"

    leaf_id: Mapped[str] = mapped_column(String(64), primary_key=True)   # "" = global default
    style_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Brand(Base):
    """Panel-owned brand store (admin.db). The picker merges the bundled gazetteer snapshot (107
    confirmed brands from pipeline-v3) with these panel-added rows; a future job syncs them back to
    the pipeline's gazetteer.jsonl (brand.upsert). Row schema mirrors the gazetteer exactly:
    {name, aliases, logo, confirmed, source, ts}. `confirmed` is a manual flag — NOT GS1 confirmation."""
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)   # canonical, merge key
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    logo: Mapped[str] = mapped_column(String(512), default="")
    confirmed: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String(32), default="panel")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class CodSettlement(Base):
    """A recorded courier COD payout line (наложен платеж). Panel-owned (admin.db).
    Couriers pay out twice-weekly against a deduction file; each order the courier collected
    for gets a settlement row so we can flag discrepancies (collected != expected)."""
    __tablename__ = "cod_settlement"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # one settlement per order — DB-enforced backstop against double-settle (HIGH-1)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_name: Mapped[str] = mapped_column(String(64), default="")
    courier: Mapped[str] = mapped_column(String(16), default="")
    # waybill/tracking number — the JOIN KEY for future auto-reconciliation against the courier
    # payout API (Speedy /payments, Econt CD settlement). Stored now so retrofitting isn't needed.
    waybill: Mapped[str] = mapped_column(String(64), default="", index=True)
    expected: Mapped[float] = mapped_column(default=0.0)     # COD we set at label time (€)
    collected: Mapped[float] = mapped_column(default=0.0)    # what the courier paid out (€)
    payout_ref: Mapped[str] = mapped_column(String(128), default="")  # matches SettleRequest.reference
    payout_date: Mapped[str] = mapped_column(String(32), default="")
    settled_ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    settled_by: Mapped[str] = mapped_column(String(255), default="")


class PricingRule(Base):
    """Cost-plus-markup repricing (owner 2026-07-21). Retail should track supplier COST: buy at
    €1.00 + 50% markup → sell €1.50; when the supplier cost rises to €1.50 the shelf price should
    become €2.25. Barsy holds cost (delivery_price) + retail (actual_price); this panel-owned table
    holds the intended markup per article + the cost we last priced from, so we can detect when a
    cost change means the price needs the owner's approval to move. Panel proposes; owner applies."""
    __tablename__ = "pricing_rule"

    article_id: Mapped[int] = mapped_column(primary_key=True)  # Barsy article_id
    markup_pct: Mapped[float] = mapped_column(default=0.0)     # 0.5 = +50% over cost
    last_cost: Mapped[float | None] = mapped_column(default=None)  # cost when last (re)priced
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PricingGroupRule(Base):
    """Markup rules by GROUP, not just per-article (owner 2026-07-21). Markup is usually per-SUPPLIER
    (Mira-N 50%, Vertex 60%, Plam Plast 20%), sometimes per category/type. Resolution order when
    pricing an article: its own PricingRule (product override) → this group rule (supplier, then
    category) → none. Managers set these on the bulk repricing page."""
    __tablename__ = "pricing_group_rule"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_pricing_group"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16))    # 'supplier' | 'category'
    key: Mapped[str] = mapped_column(String(255))     # supplier name / category name
    markup_pct: Mapped[float] = mapped_column(default=0.0)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OnlinePrice(Base):
    """Online (Shopify) channel price per article (owner 2026-07-27). The physical store keeps its
    base actual_price untouched; the ONLINE price lives in a NATIVE Barsy price rule scoped to the
    online delivery types (до адрес + офис). This panel-owned table holds the INTENDED online price
    per article — the source of truth the panel pushes into that Barsy rule as a per-article
    custom_price (Pricelists_save_articles), mirroring how PricingRule owns markups. No row for an
    article = no online override = its online price falls back to the store price. The connector then
    reads Barsy current_price in the online delivery-type context and pushes it to Shopify. See
    CHANNEL_PRICING.md."""
    __tablename__ = "online_price"

    article_id: Mapped[int] = mapped_column(primary_key=True)     # Barsy article_id
    custom_price: Mapped[str] = mapped_column(String(32), default="")   # EUR, VAT-incl, as string
    mode: Mapped[str] = mapped_column(String(16), default="exact")      # how last set: exact|pct|fixed
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrderOutcome(Base):
    """COD-refuser defense (owner 2026-07-21). Neither Speedy nor Econt exposes recipient reputation,
    so we build our own: one row per finished delivery attempt, keyed on the normalized phone (+ a
    normalized address), recording delivered vs refused. Repeat refusers then surface a risk flag on
    the next order. Internal-only (never a shared/cross-merchant list) — GDPR lawful basis = fraud
    prevention. A flag is for human review, never an automatic reject."""
    __tablename__ = "order_outcome"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    phone_norm: Mapped[str] = mapped_column(String(20), index=True)   # 359-canonical
    address_norm: Mapped[str] = mapped_column(String(255), default="")
    order_id: Mapped[str] = mapped_column(String(64), default="")
    order_name: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(16))                  # 'delivered' | 'refused'
    cod_amount: Mapped[float] = mapped_column(default=0.0)
    # nekorekten report id IF we reported this refusal to them — an opaque id (not personal data),
    # kept so the report can be retracted (DELETE) if it was a mistake.
    nekorekten_report_id: Mapped[str] = mapped_column(String(64), default="")
    recorded_by: Mapped[str] = mapped_column(String(255), default="")
    recorded_ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Who did what. Appended on side-effectful actions (login, user create, later:
    labels printed, POs generated, stock edits). A packer is not the owner — this is
    the trail that keeps them honest."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_email: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
