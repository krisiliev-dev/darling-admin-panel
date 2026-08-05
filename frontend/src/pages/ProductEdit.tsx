import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { ArticleDetail, IntakeDraft } from '../lib/api'
import { PageHeader, Loading } from '../components/PageHeader'
import { ProductEditor } from '../components/ProductEditorCore'

// Редакция на продукт (#70 slice 2) — the Нов продукт editor in EDIT mode over a LIVE Barsy article.
// Loads GET /api/catalog/{id}; saves DIRECT via POST /api/catalog/{id} (owner decision: the product
// is already live, no draft/approve). AI enrichment is create-only; the naming recipe is a button.
export function ProductEdit() {
  const { articleId } = useParams()
  const navigate = useNavigate()
  const aid = Number(articleId)
  const [detail, setDetail] = useState<ArticleDetail | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    setDetail(null); setErr('')
    api.get<ArticleDetail>(`/api/catalog/${aid}`).then(setDetail)
      .catch(() => setErr('Продуктът не е намерен.'))
  }, [aid])
  if (err) return <p className="pencil" style={{ padding: '1rem' }}>{err}</p>
  if (!detail) return <Loading />
  // map the live article onto the editor's value shape (meta_* is the editor's internal SEO name)
  const draftLike = {
    id: 0, code: detail.code || '', barcode: detail.barcode || '',
    price: detail.price != null ? String(detail.price) : '', unit: detail.unit || 'бр',
    name: detail.name || '', category: detail.category || '', shopify_category_id: '',
    brand: detail.brand || '', series: '', description: detail.description || '',
    attributes: detail.attributes || [], meta_title: detail.seo_title || '',
    meta_description: detail.seo_description || '', model: '',
    oversell: !!detail.oversell, weight_kg: detail.weight_kg || '', status: 'pushed',
  } as unknown as IntakeDraft
  const back = () => navigate('/products')
  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <button className="btn-paper" onClick={back} style={{ justifySelf: 'start' }}>← Назад към каталога</button>
      <PageHeader title={`Редакция · ${detail.name || '#' + aid}`} subtitle="редакцията записва директно в Barsy" />
      <ProductEditor draft={draftLike} edit={{ articleId: aid, detail }} onDone={back} onCancel={back} />
    </div>
  )
}
