import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { Section } from '../components/Section'
import { Field } from '../components/form/Field'
import { Select } from '../components/form/inputs'
import { ActionBar } from '../components/form/Actions'

// Именуване — the naming system, written down where it can be read and changed (owner asked
// "What is our default formula for name composition?" and the answer lived only in
// backend/app/naming.py).
//
// The composer is deterministic — NO LLM at runtime. Every product name in the catalogue is built
// the same way, so the shop reads as one shop.
type Options = Record<string, { default: string | null; variants: string[] }>

const VARIANT_BG: Record<string, string> = {
  prefix: 'отпред (прилагателно)',
  suffix: 'отзад (фраза)',
  off: 'не се показва',
  none: 'не се показва',
}
const ATTR_BG: Record<string, string> = {
  Color: 'Цвят', Material: 'Материал', Shape: 'Форма', Feature: 'Особеност',
  Pattern: 'Десен', Finish: 'Покритие',
}

export function Naming() {
  const { me } = useAuth()
  const owner = me?.role === 'owner'
  const [opts, setOpts] = useState<Options>({})
  const [style, setStyle] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => {
    api.get<{ options: Options }>('/api/intake/recipe/options')
      .then((d) => setOpts(d.options || {})).catch(() => {})
    api.get<{ global: Record<string, string> }>('/api/intake/recipe')
      .then((d) => { setStyle(d.global || {}); setSaved(d.global || {}) }).catch(() => {})
  }, [])

  const dirty = JSON.stringify(style) !== JSON.stringify(saved)

  const save = async () => {
    setBusy(true); setErr(''); setNote('')
    try {
      await api.put('/api/intake/recipe', { style })
      setSaved(style)
      setNote('Записано. Нови продукти се именуват по новата рецепта.')
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Записът се провали.')
    } finally { setBusy(false) }
  }

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <Sheet clip="left">
        <Section title="Формулата">
          <p className="font-type" style={{ fontSize: '.85rem', lineHeight: 1.55, margin: 0 }}>
            Името се сглобява <b>детерминистично</b> — без AI в момента на записа, за да излиза
            еднакво всеки път:
          </p>
          <div className="font-type" style={{
            fontSize: '.8rem', background: 'var(--paper2)', border: '1px solid var(--line)',
            borderRadius: 2, padding: '.6rem .7rem', margin: '.5rem 0', lineHeight: 1.7,
          }}>
            <b>Марка</b> + <b>прилагателни отпред</b> (цвят → материал → форма) +
            {' '}<b>съществителното на категорията</b> (единствено число) +
            {' '}<b>фрази отзад</b> + <b>размер</b>
          </div>
          <p className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)', lineHeight: 1.55, margin: 0 }}>
            Правилата, които струват грешки, ако се забравят: марката се слага{' '}
            <b>дословно</b> отпред — собствено име, не се членува и не се прекроява
            („Luminarc Бяла керамична чиния 22см“). Съществителното идва от <b>листото</b> на
            Shopify таксономията, в единствено число, а прилагателните се съгласуват по <b>род</b>{' '}
            с него. Прилагателно без известна форма за този род се <b>изхвърля</b> — по-добре
            празно, отколкото сгрешена граматика. Размерът се залепя без интервали и с кирилски
            мерни единици (22см, 1,5л).
          </p>
        </Section>
      </Sheet>

      <Sheet clip="right">
        <Section title="Рецепта (глобална)" right={
          <span className="font-type" style={{ fontSize: '.7rem', color: 'var(--pencil)' }}>
            всяка категория може да я презапише от нейния продукт
          </span>
        }>
          {Object.keys(opts).length === 0 ? (
            <p className="pencil" style={{ fontSize: '.8rem' }}>Няма настройваеми елементи.</p>
          ) : (
            <div style={{ display: 'flex', gap: '.8rem', flexWrap: 'wrap' }}>
              {Object.entries(opts).map(([attr, cfg]) => (
                <Field key={attr} label={ATTR_BG[attr] || attr}
                  hint={cfg.default ? `по подразбиране: ${VARIANT_BG[cfg.default] || cfg.default}` : undefined}>
                  <Select
                    value={style[attr] || ''}
                    width="12rem"
                    placeholder="по подразбиране"
                    options={cfg.variants.map((v) => ({ value: v, label: VARIANT_BG[v] || v }))}
                    disabled={!owner}
                    onChange={(v) => setStyle((s) => {
                      const n = { ...s }
                      if (v) n[attr] = v; else delete n[attr]
                      return n
                    })}
                  />
                </Field>
              ))}
            </div>
          )}
          {owner && (
            <ActionBar busy={busy} error={err} note={note}>
              <button className="btn-paper btn-red" disabled={busy || !dirty} onClick={() => void save()}>
                Запази рецептата
              </button>
              <button className="btn-paper" disabled={busy || !dirty} onClick={() => setStyle(saved)}>
                Отказ
              </button>
            </ActionBar>
          )}
          {!owner && (
            <p className="pencil" style={{ fontSize: '.78rem', marginTop: '.5rem' }}>
              Само собственикът може да променя рецептата.
            </p>
          )}
        </Section>
      </Sheet>

      <Sheet clip="left">
        <Section title="Какво пипа рецептата">
          <p className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)', lineHeight: 1.55, margin: 0 }}>
            Рецептата казва <b>къде</b> да излезе всеки атрибут — отпред като прилагателно, отзад
            като фраза, или изобщо да не се показва. Тя не измисля стойности: показва само това,
            което вече е попълнено в продукта. Промяната важи за <b>новите</b> имена; вече
            записаните продукти се преименуват нарочно, от Продукти → Каталог → масово
            преименуване.
          </p>
        </Section>
      </Sheet>
    </div>
  )
}
