# UX преглед — „Дарлинг · панел" (frontend audit, 2026-08-03)

Scope: `fe/Intake.tsx`, `Shell.tsx`, `PageHeader.tsx`, `Paper.tsx`, `Home.tsx`, `Products.tsx`,
`Deliveries.tsx`, `BrandRegistryV2.tsx`, `Brands.tsx`, `Settings.tsx`, `BrandPicker.tsx`,
`Confirm.tsx`, `TableControls.tsx`, `index.css`. Judged as: keyboard-first Bulgarian shop staff
reviewing AI-prefilled drafts. Principles cited: NN/g form-design guidelines (single column,
top labels, inline+summary errors), GOV.UK Design System (question flow, error summary, one
primary action), Luke Wroblewski *Web Form Design* (primary-action placement, path to completion).

---

## 1. Top 10 issues by user impact

**#1 — Keyboard users cannot pick a category or brand at all.**
`Intake.tsx` `TaxonomyPicker` (L579) and `BrandPicker.tsx` (L38): options are selected only via
`onMouseDown`. Keyboard activation (Enter/Space) fires `click`, never `mousedown` — and `onBlur`
closes the list after 150–180 ms, so tabbing to an option dismisses it. In a keyboard-first tool
this makes the two most important pickers mouse-only. There is also no ArrowUp/Down, Enter, Esc,
or `role="combobox"/listbox"`.
**Fix:** implement the WAI-ARIA combobox pattern: keep focus in the input, ArrowDown/Up moves an
`aria-activedescendant` highlight, Enter picks, Esc closes; select on `onClick` with
`onMouseDown={e => e.preventDefault()}` to survive blur. One shared `<Combobox>` for both pickers.

**#2 — The editor's field order fights its own workflow; focus starts at the bottom.**
The declared flow (Intake.tsx L10-14) is: barcode + images → „Обогати" → review AI suggestions →
save. But the barcode field — the actual first input, with `autoFocus` (L296) — is the *last* row
of the right column, below category/brand/name/price which are all still empty at that moment.
The user's focus lands mid-page while the eye starts at the top; after enrichment they must tab
*backwards* to review. Violates "match field order to task order" (NN/g #2).
**Fix:** two visible steps. Step 1 (only thing shown initially): Баркод + снимки + primary button
„Попълни автоматично". Step 2 (revealed/enabled after enrichment): the review fields, top-down in
review order (see §2). Progressive disclosure also removes the current dead state where 15 empty
fields invite manual typing that enrichment will overwrite.

**#3 — Primary action and errors live at the very bottom of a long form; nothing is sticky.**
`Intake.tsx` L362-368: Запази/Отказ plus the *only* error/note area sit below gallery, fields,
attributes, description + duplicate preview, meta, weight, and the suppliers table — several
screens down. A validation failure ("Задължителна е поне една снимка") renders next to the button,
nowhere near the offending element at the top. Long-form best practice (GOV.UK error summary;
sticky action bars for editors).
**Fix:** sticky bottom bar (position: sticky; bottom: 0; paper background, top border) containing:
save-state text, error summary with anchor links to fields, „Отказ", and the primary „Запази".
Scroll-to + focus the first invalid field on failed submit.

**#4 — AI suggestions render as grey cursive — they look like placeholders, not data.**
`pencil()` (L185-186) styles enriched values in `var(--hand)` (Comic Sans-family) grey. Grey text
in a field is the universal placeholder affordance → staff may believe the field is empty and that
grey text won't be saved. It is also low-contrast and cursive fonts are being removed by owner
decree.
**Fix:** white inputs, normal type, plus an explicit per-field „AI" chip (or left border tick in
`--pencil2`) that disappears on first edit. Same reviewed/unreviewed signal, readable, and the
value unambiguously *is* the value. Add a one-line banner after enrichment: „Полетата са попълнени
автоматично — прегледай и коригирай."

**#5 — Silent failures everywhere in Intake.**
`catch { /* ignore */ }` on: creating a blank draft (L26 — the „+ Добави продукт" button just does
nothing on failure), image list load (L153 — user sees "no images" and can't save), image delete
(L170), supplier add/delete (L393-395), name composer. A dead button with no feedback is the worst
error pattern for non-technical staff.
**Fix:** one toast/inline-message helper used by every mutation; on load failures show „Не се
зареди — опитай пак" with a retry button, never an empty state that lies.

**#6 — No dirty-state protection; Back/Отказ discard a fully reviewed form.**
`ProductEditorScreen` L115: „← Назад към списъка" and „Отказ" drop all edits instantly; the editor
is state-mounted (no route), so browser Back leaves the page entirely and F5 loses everything.
The draft already has a server id — the cheapest insurance is autosave.
**Fix:** debounce-PUT the draft on change (it's a draft — saving it is safe; the *real* commit is
the explicit Запази that pushes to Barsy), show „запазено · 12:41" in the sticky bar, and confirm
before leaving with unsaved changes. Give the editor a URL (`/intake/:id`) so refresh and Back
behave predictably — Deliveries detail needs the same.

**#7 — One `busy` flag lies about what's happening.**
L132: `busy` is shared by image upload, enrichment and save. While images upload, the enrich
button reads „обогатява…"; while saving, same. Feedback must name the actual operation (NN/g:
visibility of system status). Enrichment can also take long with zero progress indication and
then *silently overwrites* name/description/attributes (L179-181) — including any manual edits,
because `setTouched({})` resets edit tracking.
**Fix:** separate `uploading/enriching/saving` states; disable only what each blocks; confirm
before re-running enrichment over touched fields („Ще замени редактираните полета — продължи?").

**#8 — Required fields are unmarked and validated one-at-a-time at submit.**
Nothing indicates that images, barcode, price, or category are required; price — the money field —
has no validation at all, and barcode gets no GS1 check-digit validation despite the label
„Баркод (GS1)". The single check that exists (images) surfaces only on click, at the bottom.
**Fix:** mark *optional* fields („Серия — незадължително") rather than starring everything
(GOV.UK); validate on submit with a full error summary + inline messages under each field;
check-digit-validate the barcode on blur (typo-catching for scanned COD-critical data); refuse
save without price and category with specific messages.

**#9 — Backend vocabulary leaks into the reviewer's screen** (details in §4).
Worst single offender: `TaxonomyPicker` prints „Shopify id: aa-2-1-4" under the category field
(L586). Staff reviewing a kettle do not need Shopify taxonomy ids, Barsy `article_id`s, dry-run
„(проба)" jargon, or „изпраща се към Barsy" mechanics. Principle: match the system to the user's
world, hide implementation (Nielsen heuristic #2).
**Fix:** remove or demote to a collapsed „Технически детайли" block visible to owner only.

**#10 — `BrandRegistryV2.tsx` ("Марки") is a different product entirely.**
Blue accent `var(--acc, #0f62fe)` — a token that *doesn't exist* in index.css, so it renders
hard-coded IBM blue; rounded 8px buttons/inputs with its own `btn`/`inputStyle`; white cards; its
own `<main>` with `maxWidth: 1050` inside the Shell's 78rem main; native `window.confirm` (L191)
while the rest of the app has `useConfirm`; custom pagination instead of `Pager`; `<h1>` instead
of `PageHeader`. And it coexists in the nav with „Брандове" (`Brands.tsx`) — two brand pages with
overlapping purpose and disjoint UIs. This is the page that makes the whole panel feel
inconsistent.
**Fix:** merge Марки + Брандове into one page (tabs: Регистър / Без марка), rebuilt on Sheet,
PageHeader, btn-paper, input-paper, Pager, useConfirm. Delete the local style objects.

---

## 2. Recommended Intake editor field order + layout

Keep gallery-left / fields-right, but restructure into three stages with one visual system
(top labels, white inputs, single column inside each pane, sticky footer):

**Stage 1 — Идентификация (the only thing visible on a fresh draft):**
gallery/upload left; right: Баркод (autofocus; Enter = trigger enrichment; GS1 check-digit on
blur) → Цена + Мярка (price is human ground truth from the delivery doc — capture it *before*
AI fills the screen; it is never AI-suggested, so don't visually mix it with suggestions) →
primary button „Попълни автоматично" (rename from „✨ Обогати"; drop the emoji). Everything else
hidden until this runs (or a „Попълни ръчно" link for the no-AI path).

**Stage 2 — Преглед (after enrichment; ordered by dependency, since category → attributes → name):**
1. Категория (drives attributes + name — must be confirmed first; put the combobox here, no id)
2. Марка + Серия on one row (BrandPicker + series input, equal widths)
3. Атрибути — directly under category *in the right column*, not banished full-width below the
   fold; picklists first, free-text after; „други (извън таксономията)" collapsed by default
4. Физически данни — one labelled row: В×Ш×Д (см), Обем, **Тегло (move it up from the bottom —
   it's a physical attribute, not an afterthought)**
5. Име — *after* the inputs that compose it, styled as derived: read-only look with an „↻ авто"
   badge and a „редактирай" affordance; editing freezes recomposition (current behaviour, made
   visible instead of magical)
6. Наличност row: Мярка (if not in Stage 1) + „Може да се поръчва без наличност" checkbox

**Stage 3 — Съдържание (collapsible sections, closed by default, state in header):**
- „Описание" — textarea with an Edit/Преглед toggle instead of the always-appended duplicate
  preview div (L334-337 currently doubles the vertical cost of every description)
- „SEO (мета данни)" — meta title + description with character counters (≈60/160); most staff
  approve without touching these, so hide them (progressive disclosure, NN/g)
- „Доставчици (N)" — collapsed with count; add a note that rows save immediately, because today
  suppliers persist instantly while everything above waits for Запази — two persistence models
  on one screen with no signal

**Sticky footer:** „запазено автоматично · 12:41" | error summary link | Отказ | **Запази и одобри**
(one label everywhere — today it flips between „Добави продукт" and „Запази" by status, L366).
Keep secondary left, primary right, matching the Confirm modal's order (Intake currently reverses it).

Tab order then equals reading order equals task order, top to bottom, no return trips.

## 3. Cross-page inconsistencies

- **Tabs, five ways:** Products/Settings (red-active pills in PageHeader `right`), Home period
  toggle (borderRadius 6, standalone), Deliveries file/photo toggle (paper-active, not red),
  BrandRegistryV2 (blue, radius 8), while a ready `FilterChips` component sits unused by all of
  them. → One `<Tabs>` component; PageHeader gets an optional `tabs` prop.
- **List→detail navigation:** Марки uses real routes (`/marki-v2/:id`); Intake and Deliveries use
  component state (no URL, browser Back exits). Row affordances differ: whole-row click +
  „отвори →" cell (Deliveries), row-click only (Марки), button-rows (Intake list). → Route all
  details; one clickable-row pattern with a real link for keyboard.
- **Tables:** header text is red (Deliveries, Brands, Catalog), pencil-grey (Intake suppliers),
  plain bold with 2px border (BrandRegistryV2); padding .2–.4rem varies; pagination is `Pager`
  (some pages), custom centered buttons (Марки), or absent. → shared table styles + `Pager`.
- **Buttons:** label casing mixes lowercase („редактирай", „махни", „изход") and Capitalized
  („Запази", „Провери"); icon/emoji prefixes are ad hoc („+", „✨", „📄", „📷"); blue buttons on
  Марки. Confirm modal puts Cancel→Confirm left-to-right, Intake puts primary first. → Sentence
  case everywhere, primary rightmost, ban decorative emoji in buttons.
- **Empty states:** rotated sticky-note (Home/Orders/Dashboard), cursive `font-hand` paragraph
  (Intake L63, PublishAudit, Cod — cursive is scheduled for removal), plain type (Deliveries).
  → one `EmptyNote` (already exists) restyled in Nunito/system, used everywhere.
- **Confirm dialogs:** `useConfirm` app-wide, except native `window.confirm` in BrandRegistryV2.
- **Field label component duplicated 3×** (`Field` in Intake, Deliveries, Brands — identical);
  meanwhile attribute rows use side-labels at 12rem and size fields a third grey-caption style,
  three label systems inside one form. → export one `Field` from components; top labels only.
- **Page width/shell:** BrandRegistryV2 renders its own `<main maxWidth:1050>` inside Shell's
  78rem `<main>` — double padding, off-center vs every other page.
- **Nav:** 16 flat items, no grouping, `display:flex` without wrap — overflows below ~1200px with
  no scroll affordance; „Брандове" and „Марки" both present (see #10).

## 4. Backend noise to remove from the UI

- `Intake TaxonomyPicker` L586: **„Shopify id: {categoryId}"** — delete.
- `Intake ApprovedDrafts` L638: „в Barsy #{barsy_article_id}" → „✓ добавен"; success message L609
  „(article_id …)" → drop the id. The confirm text „Това е реален запис в ERP" can stay — that's
  a real-world warning, not plumbing.
- `Intake save` L229: „Записано (проба)" dry-run wording → staff can't act on "проба"; if the
  system is in test mode say „Тестов режим — продуктът не е изпратен" once, globally (banner),
  not per action. Same for ProductBulkPricing L357 „Barsy запис е изключен (проба)…".
- `Intake WaitingList` L79: „· изпраща се към Barsy" → „· завършена" (the batch mechanics are
  invisible work; if status matters show it on Доставки, once).
- `Deliveries` L360/375: „доставка #{storeload_id}" → keep a human reference („изпратена ✓"),
  put the ERP id in a title tooltip if support ever needs it.
- `Deliveries LineTable` „Barsy" column header (L420) → „Статус".
- Raw error passthrough: „Barsy: {b.error}" (Intake L230) and ApiError messages shown verbatim —
  wrap in human text with a „технически детайли" expander.
- `Settings` L94 „ASSISTANT_BACKENDS в ~/.hermes/.env" and courier note about server env paths
  (L281-284) — owner-only at most; staff can do nothing with a dotfile path.
- `Brands` table „Източник" column showing `panel`/`bundled` source enums → icon + BG tooltip,
  or owner-only.
- Intake editor „код {d.code}" floating next to price (L292) — keep, but as a labelled read-only
  „Код" field in Stage 1; a floating pencil note looks like debug output.

*(~90 % of the above is deletion or relabeling — cheap wins before any layout work.)*
