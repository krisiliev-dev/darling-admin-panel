// Typed fetch client. Cookies are same-origin via the Vite proxy, so credentials:'include'.

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// Auth-probe endpoints that legitimately 401 without meaning "session expired" —
// they must NOT trigger the global reset (would loop the refresh).
const AUTH_PROBES = ['/api/users/me', '/api/auth/setup-status', '/api/auth/login', '/api/auth/barsy-login']

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData must NOT get a JSON content-type — the browser sets multipart boundaries itself.
  const isForm = init?.body instanceof FormData
  const res = await fetch(path, {
    credentials: 'include',
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(init?.headers || {}) },
    ...init,
  })
  if (res.status === 401 && !AUTH_PROBES.some((p) => path.startsWith(p))) {
    // session expired mid-use — drop back to login instead of stranding the page
    window.dispatchEvent(new CustomEvent('darling:unauthorized'))
  }
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch { /* non-JSON error */ }
    let message: string
    if (typeof detail === 'string') message = detail
    else if (Array.isArray(detail)) {
      // FastAPI 422 validation errors are a list of {loc, msg}; surface something actionable
      message = detail.map((d: { loc?: unknown[]; msg?: string }) =>
        `${(d.loc ?? []).slice(1).join('.')}: ${d.msg ?? ''}`.trim()).filter(Boolean).join('; ') || 'Невалидни данни'
    } else message = 'Грешка'
    throw new ApiError(res.status, message)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(p: string) => request<T>(p, { method: 'DELETE' }),
  upload: <T>(p: string, form: FormData) => request<T>(p, { method: 'POST', body: form }),
  // FastAPI-Users login expects form-encoded username/password
  loginForm: async (email: string, password: string) => {
    const form = new URLSearchParams({ username: email, password })
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form,
    })
    if (!res.ok) {
      let detail = 'Грешен имейл или парола.'
      try {
        const b = await res.json()
        if (typeof b.detail === 'string') detail = b.detail
      } catch { /* ignore */ }
      throw new ApiError(res.status, detail)
    }
  },
}

// --- shared types ---
export interface Me {
  id: string
  email: string
  full_name: string
  role: 'owner' | 'manager' | 'staff' | 'packer'
  is_superuser: boolean
}

export interface CatalogDashboard {
  cards: {
    total: number
    launch_ready: number
    launch_blocked: number
    unscored: number
    needs_human: number
    reviewed: number
    review_ready: number
    suppliers: number
    avg_retail: number
    latest_snapshot: string | null
  }
  readiness: { label: string; value: number; tone: 'ready' | 'blocked' | 'pending' }[]
  suppliers: { supplier: string; count: number }[]
}

export interface SalesDashboard {
  kpis: {
    products: number
    revenue_total: number
    revenue_today: number
    orders_total: number
    units_total: number
    suppliers: number
    price_snapshots: number
    avg_retail: number
  }
  top_products: { code: string; name: string; value: number }[]
  price_changes: {
    code: string; name: string; old: number; new: number
    pct: number; supplier: string; direction: string
  }[]
}

export interface CourierReadiness {
  key: string
  name: string
  configured: boolean
  ready: boolean
  sender: string
  note: string
}

export interface Office {
  id: string | number
  name: string
  address: string
  city: string
}

export interface OrderItem { title: string; qty: number; sku?: string; barcode?: string }

export interface Order {
  id: string
  name: string
  created: string
  total: number
  cod: boolean
  cod_amount: number
  customer: string
  phone: string
  city: string
  zip: string
  address: string
  items: OrderItem[]
  op_status: string
  op_status_bg: string
  courier: string | null
  tracking: string | null
}

export interface OrdersResponse {
  orders: Order[]
  statuses: Record<string, string>
  warning?: string
}

export interface IntakeAttribute { key: string; value: string }

export interface DeliveryContext { batch_id: number; supplier: string; reference: string; doc_date: string; status: string }
export interface DraftImageMeta { id: number; filename: string; uploaded: boolean }
export interface ShopifyCategory { id: string; name: string; path: string; en: string }
export interface TaxAttrValue { h: string; name: string }
export interface TaxAttribute { handle: string; name: string; free: boolean; values: TaxAttrValue[] }

export interface IntakeDraft {
  category: string
  shopify_category_id?: string
  name: string
  attributes: IntakeAttribute[]
  description: string
  meta_title?: string
  meta_description?: string
  model: string
  elapsed_ms: number
  // present on persisted drafts (from /drafts, /approve)
  id?: number
  code?: string
  barcode?: string
  price?: string
  unit?: string
  status?: string
  barsy_article_id?: number | null
  // present on delivery-sourced drafts (from /from-delivery)
  delivery_line_id?: number | null
  completed?: boolean
  delivery?: DeliveryContext
}

// --- delivery-based intake (Приемане: supplier Excel → new-vs-existing → batch to Barsy) ---
export interface DeliveryRow {
  row: number
  supplier_code: string
  name: string
  qty: number
  delivery_price: number
  is_new: boolean
  match_code: string
  match_name: string
  match_method: string        // 'code' | 'name' | ''
  match_confidence: string    // 'high' | 'medium' | ''
  warnings: string[]
}

export interface DeliveryParseResponse {
  columns: Record<string, string>
  positional: boolean
  rows: DeliveryRow[]
  summary: { total: number; new: number; existing: number; total_qty: number }
}

export interface DeliveryCommitResponse {
  ok: boolean
  dry_run: boolean
  created_article_ids: (number | null)[]
  storeload_payload: unknown
  storeload_id: number | null
  warnings: string[]
}

// --- product cache: filterable search + bulk price ops ---
export interface ProductAttr { key: string; value: string; key_id?: number; value_id?: number }
export interface CacheProduct {
  article_id: number; code: string; name: string; barcode: string
  category: string; supplier: string; price: number | null; stock: number | null
  low: boolean; has_image: boolean; attributes: ProductAttr[]
}
export interface CacheSearchResp { total: number; page: number; per_page: number; items: CacheProduct[] }
export interface CacheFacets { suppliers: string[]; categories: string[]; attributes: Record<string, string[]> }
export interface BulkPriceChange { article_id: number; code: string; name: string; old_price: number | null; new_price: number }
export interface BulkPriceSkip { article_id: number; code: string; name: string; old_price: number | null; reason: string }
export interface BulkPricePreview { dry_run: boolean; count: number; changes?: BulkPriceChange[]; skipped?: BulkPriceSkip[]; applied?: number; failed?: string[] }

// --- Заявки (purchase requests) ---
export interface ZayavkaProduct {
  code: string; name: string; supplier: string; suppliers: string[]
  units: number; rev: number; marg: number; stock: number; dead?: boolean; suggested_qty: number
}
export interface ZayavkaCard { key: string; title: string; subtitle: string; products: ZayavkaProduct[] }
export interface ZayavkaSupplier {
  supplier: string; catalog_count: number; open_id: number | null
  items: number; total: number; last_sent: string | null
  email: string; min_order: number; lead_days: number; below_min: boolean
}
export interface ZayavkaLineRow {
  id: number; code: string; supplier_code: string; name: string
  qty: number; delivery_price: number; barcode: string; line_total: number
}
export interface ZayavkaDetailResp {
  id: number; supplier: string; supplier_id: number | null; supplier_email: string
  status: string; notes: string; created_by: string; sent_at: string | null; sent_via: string
  items: number; total: number; lines: ZayavkaLineRow[]
}
export interface SupplierCatalogItem {
  code: string; supplier_code: string; name: string; delivery_price: number; units: number; dead?: boolean
}

// --- Доставки (receiving) ---
export interface DeliveryLineRow {
  id: number
  supplier_code: string
  name: string
  qty: number
  price: number
  barcode: string
  is_new: boolean
  match_code: string
  match_method: string
  match_confidence: string
  article_id: number | null
  scanned_qty: number
  status: string            // pending | confirmed | mismatch
  intake_draft_id: number | null
}

export interface DeliveryDetailResp {
  id: number
  supplier: string
  supplier_id: number | null
  depot_id: number
  reference: string
  doc_date: string
  source: string
  status: string            // awaiting_products | ready | finalised
  storeload_id: number | null
  created_by: string
  created_at: string | null
  finalised_at: string | null
  total: number
  new: number
  existing: number
  confirmed: number
  mismatched: number
  new_pending: number
  lines: DeliveryLineRow[]
}

export interface DeliveryListItem {
  id: number
  supplier: string
  reference: string
  doc_date: string
  source: string
  status: string
  storeload_id: number | null
  created_at: string | null
  total: number
  new: number
}

export interface InventoryItem {
  article_id: number
  name: string
  amount: number | null
  unit: string
  min_amount: number | null
  avg_cost: number | null
  low: boolean
  zero: boolean
}

export interface InventoryResponse {
  total: number
  summary: { tracked: number; low: number; zero: number }
  items: InventoryItem[]
}

export interface CodOrder {
  order_id: string
  order_name: string
  courier: string
  tracking: string
  status: string
  bucket: string
  bucket_bg: string
  expected: number
  collected: number | null
  discrepancy: boolean
  updated_ts: string | null
}

export interface InsightsResponse {
  orders: number; revenue: number; aov: number; cod_share: number
  customers: { total: number; returning: number; repeat_rate: number }
  by_day: { date: string; orders: number; revenue: number }[]
  top_cities: { city: string; orders: number }[]
  top_products: { name: string; qty: number }[]
  delivery: { delivered: number; refused: number; rto_rate: number }
}

export interface AgingBucket {
  key: string; label: string; days_min: number; days_max: number | null
  value: number; units: number; lots: number
}
export interface StaleItem {
  article_id: number; name: string; supplier: string; oldest_days: number
  qty: number; unit_cost: number | null; sell_price: number | null; value: number
  suggested_markdown_pct: number; suggested_price: number | null
}
export interface AgingResponse {
  as_of: string; currency: string
  buckets: AgingBucket[]
  undated: { lots: number; value: number }
  totals: { value: number; units: number; lots: number; articles: number }
  stale: {
    threshold_days: number; value: number; units: number; articles: number
    pct_of_value: number; items: StaleItem[]; truncated: number
  }
  source?: string; truncated_fetch?: boolean
}

export interface DiagnosisResponse {
  available: boolean
  meta?: { maxdate: string; currency: string; period_from: string; period_to: string
    source: string; note: string; products_total: number; dead_total: number }
  kpi?: { rev: number; gm: number; inv: number; turnover: number; gmroi: number; dsi: number
    dead_val: number; dead_pct: number; dead_n: number; active: number; reorder: number; units: number }
  rev_year?: Record<string, number>
  ts?: Record<string, { rev: number; cogs: number; purch: number; units: number }>
  abc?: Record<string, { count: number; value: number }>
  suppliers?: { name: string; rev: number; marg: number; nprod: number; deadval: number }[]
  dead_products?: { name: string; supplier: string; stock: number; stockval: number
    cover: number; last: string; discontinued: boolean }[]
}

export interface HomeCard {
  id: string; name: string; total: number; customer: string; city: string
  op_status_bg: string; items: { title: string; qty: number; sku: string }[]
}
export interface HomeResponse {
  period: string
  kpi: {
    turnover: number; profit: number; profit_complete: boolean; units: number
    orders_done: number; orders_to_pack: number; reorder_n: number
    top_product: { name: string; units: number } | null
  }
  attention: { tone: string; text: string; to: string }[]
  to_pack: HomeCard[]
  has_sales: boolean
}

export interface ProductRow {
  code: string; name: string; supplier: string; units: number; rev: number; marg: number
  stock: number; stockval: number; price: number | null; cost: number | null
  last: string; cover: number; dead: boolean; discontinued: boolean
}
export interface ProductsResponse {
  available: boolean; total: number; page: number; per_page: number
  sort: string; order: string; products: ProductRow[]
}

export interface MetricsItem {
  article_id: number; name: string; stock: number; unit_cost: number; sell_price: number | null
  units_sold: number; velocity: number; days_of_cover: number | null; reorder_point: number
  suggested_qty: number; needs_reorder: boolean; is_dead: boolean; value: number; revenue: number
}
export interface MetricsResponse {
  as_of: string; currency: string; window_days: number; lead_days: number; safety_days: number
  kpi: { inv_value: number; turnover: number | null; dsi: number | null; gmroi: number | null
    dead_value: number; dead_pct: number; dead_n: number; reorder_n: number
    revenue: number; units: number; skus: number }
  reorder: MetricsItem[]; dead: MetricsItem[]; top_sellers: MetricsItem[]; has_sales: boolean
}

export interface QualityAudit {
  total: number; flagged_count: number; clean_count: number
  duplicate_barcodes: number; duplicate_codes: number; duplicate_names: number
  flagged_returned: number; scan_truncated: boolean
  flagged: { article_id: number; code: string; name: string; issues: string[] }[]
}

export interface RiskLookup {
  phone: string
  internal_refusals: number
  internal_delivered: number
  nekorekten: { enabled: boolean; reports: number | null; error?: string }
  risk_level: 'low' | 'medium' | 'high'
  lookup_url: string
}

export interface PricingItem {
  article_id: number
  code: string
  name: string
  supplier: string
  cost: number
  retail: number | null
  implied_markup: number | null
  target_markup: number | null
  markup_source: 'product' | 'supplier' | null
  proposed_retail: number | null
  cost_changed: boolean
  needs_apply: boolean
}

export interface SupplierRule {
  name: string
  product_count: number
  markup_pct: number | null
}

export interface AssistantSuggestion {
  kind: 'goto' | 'sync_tracking'
  label: string
  to: string
}

export interface CatalogItem {
  article_id: number
  code: string
  name: string
  price: number | null
  stock?: number | null
  min_stock?: number | null
  low?: boolean
  has_image?: boolean
}

export interface ArticleDetail {
  article_id: number
  code: string
  name: string
  description: string
  price: number | null
  barcode: string
  unit: string
  category: string
}

export interface StaffMember {
  user_id: number
  user_name: string
  full_name: string
  role: string
  email: string
}

export interface AuditEntry {
  ts: string
  actor: string
  action: string
  target: string
  detail: string
}

export interface AssistantBackend {
  name: string
  label: string
  ready: boolean
}

export interface AssistantResponse {
  answer: string
  model: string
  snapshot: Record<string, unknown>
  suggestions: AssistantSuggestion[]
}

export interface Supplier {
  supplier_id: number
  supplier_name: string
}

export interface BarsyArticle {
  article_id: number
  code: string
  name: string
  price: number | null
}

export interface POLine {
  article_id: number
  name: string
  amount: number
  current_price: number
}

export interface PurchaseOrder {
  id: number
  supplier_id: number | null
  supplier: string
  status: string   // draft | closed
  doc_type: string
  doc_num: string
  doc_date: string
  total: number | null
}

export interface ReorderItem {
  article_id: number
  name: string
  amount: number
  min_amount: number
  unit: string
  suggested_qty: number
}

export interface CodResponse {
  summary: {
    in_transit_amount: number; awaiting_amount: number; settled_amount: number
    in_transit_count: number; awaiting_count: number; settled_count: number
    discrepancy_count: number; discrepancy_amount: number; outstanding_amount: number
    last_payout: string
  }
  orders: CodOrder[]
}

export interface PayoutMatch {
  order_id: string; order_name: string; tracking: string; courier: string
  expected: number; collected: number; bucket: string; discrepancy: boolean
}
export interface PayoutPreview {
  file: string; delimiter: string; columns: { tracking?: string; amount?: string | null }
  skipped_rows: number
  matched: PayoutMatch[]
  unmatched_rows: { tracking: string; collected: number }[]
  delivered_not_paid: { order_id: string; order_name: string; expected: number; tracking: string }[]
  settle_lines: { order_id: string; collected: number }[]
}
