# -*- coding: utf-8 -*-
"""Auth + audit tables (admin.db). Roles: owner / staff / packer.
`is_superuser` mirrors role=="owner" and gates FastAPI-Users' admin routes."""
import datetime
import uuid

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import String, DateTime, Text, Index, UniqueConstraint, func, text
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
    model: Mapped[str] = mapped_column(String(64), default="")
    # lifecycle
    status: Mapped[str] = mapped_column(String(16), default="approved", index=True)
    barsy_article_id: Mapped[int | None] = mapped_column(nullable=True)
    approved_by: Mapped[str] = mapped_column(String(255), default="")
    pushed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # when this draft is a NEW product that arrived on a delivery, link back to the delivery line
    # so pushing it to Barsy can write the resulting article_id onto that line (finalise gate).
    delivery_line_id: Mapped[int | None] = mapped_column(nullable=True, index=True)


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


class SupplierPref(Base):
    """Persistent per-supplier settings for заявки — so email + minimum-order + lead time survive
    across orders (a supplier, not a single заявка, owns these)."""
    __tablename__ = "supplier_prefs"

    supplier: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    min_order: Mapped[float] = mapped_column(default=0.0)   # € below which a заявка isn't worth sending
    lead_days: Mapped[int] = mapped_column(default=0)       # typical days from order to delivery
    notes: Mapped[str] = mapped_column(Text, default="")


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
