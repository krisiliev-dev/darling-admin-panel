# Barsy categories: pictures, and DELETE — captured live 2026-08-07

Clean-room experiment on `darling7.barsy.online` (v3.0.1252), run at the owner's instruction and
with his hands on the upload. Every contract below was captured from the admin UI's own XHR, not
inferred. A throwaway category was created, given a picture, read back through the API, and then
deleted — and the delete was captured too.

**Two long-standing claims in our code and docs are corrected here. Both were wrong.**

---

## 1. `Categories_Delete` EXISTS — the #85c note is wrong

`app/routers/catalog.py` carries a comment from the #85c probe stating:

> DELETE = does NOT exist on this install („Тази структура няма дефинирана функция триене";
> `Categories_delete` 501/500s on every body shape; `Categories_remove` is unknown) → honest 501.

That is **false**. Captured live:

```json
{"Categories_Delete": {"cats": {"0": 19646}, "mode": "0"}}
→ {"Categories_Delete": 1}
```

The category vanished from the tree immediately. Two things the earlier probe almost certainly got
wrong:

- **the case**: the method is `Categories_Delete`, capital D — like `Categories_Create` and
  `Categories_Get`, and unlike the lowercase `categories_edit` UI helper
- **the shape**: it takes `cats`, a **map keyed by index** (`{"0": <cat_id>}`), plus a `mode` —
  not `{"cat_id": N}`. A body of the wrong shape is what produced the 500s.

### The three delete modes

The UI offers three, so `mode` is not a boolean:

| mode | UI label | meaning |
| --- | --- | --- |
| `"0"` | Без артикули и модификатори | delete the category only; its articles and modifiers are **decategorised**, not deleted |
| `"1"` | Заедно с всички включени артикули и модификатори | delete the category **together with** its articles and modifiers |
| `"2"` | Местене преди изтриване | move the contents somewhere first, then delete |

**Only `mode: "0"` has been verified.** Mode 1 destroys articles and must not be probed casually;
mode 2 needs a destination parameter that has not been captured.

The UI confirms twice before acting — an action picker, then „Сигурен ли сте…" warning that
articles will be decategorised. Any panel-side delete should keep that two-step shape.

**Action:** the panel currently returns an honest 501 for category deletes on the strength of that
wrong comment. It can now implement the real thing, defaulting to `mode: "0"`.

---

## 2. `cat_picture` IS populated — and my earlier "it never is" was wrong

Measured on the clean test category immediately after the upload:

```
Categories_Get      cat_picture: str(len=17), truthy=true
Categories_getTree  cat_picture: str(len=17), truthy=true
```

17 characters is exactly `fbgardenparty.png`. **`cat_picture` holds the FILE NAME**, not a URL and
not an id. It is carried by *both* `Categories_Get` and `Categories_getTree`, so the category tree
**can** serve slug → banner in one cached call after all.

### Why an earlier probe said otherwise, and the real finding hiding in it

The first measurement was taken on category **3949**, where `cat_picture` genuinely *was* null,
and that reading was correct. The **inference** — "it is never populated" — was wrong, because
3949 had a history the clean category did not:

1. a probe file was uploaded → it became the category's main picture
2. that probe file was **deleted**
3. a second file was uploaded and remained in the collection

**Deleting the main picture clears `cat_picture` and Barsy does NOT promote a remaining file into
its place.** So a category can hold pictures in its PICTURE collection while `cat_picture` is null
— an orphaned-main state.

Worse for diagnosis: **the admin UI hides this.** Главна снимка still rendered a thumbnail for
3949, because the UI falls back to the first file in the collection when `cat_picture` is empty.
So the screen said "there is a main picture" while the API said null, and both were telling the
truth about different things.

**Consumers must therefore treat `cat_picture` as three-valued**, not present/absent:

- non-empty → that filename is the banner
- empty **but** `Files_getList` non-empty → orphaned main; fall back to the first file, or prompt
  the owner to pick one. Do not report "no banner"
- empty **and** `Files_getList` empty → genuinely no banner; serve the default

---

## 3. The file surface, in full

### Upload

```
POST /endpoints/json/files_upload            multipart/form-data
  files[]                the file (repeatable)
  obj_type               "categories"
  obj_id                 "19646"
  obj_property           "PICTURE"
  extra_properties[0]    "cache_files_count"
  extra_properties[1]    "image_size"
  extra_properties[2]    "pdf_pages"
  extra_properties[3]    "media_duration"
  extra_properties[4]    "alt_files"
→ 200, JSON ARRAY of the uploaded file records (same shape as Files_getList below)
```

Note the routing: **file upload is `/endpoints/json/<method>` with a path segment**, while every
other call posts JSON to `/endpoints/json?bid=1` with the method as the top-level key. Replaying
an upload in the JSON style returns „Моля, посочете валидно действие".

The upload attaches the file **and** sets it as Главна снимка in that one call — no separate
`Files_link_tmp` or `Categories_update` is needed. (`Files_link_tmp {objects}` exists in the admin
JS but does not fire in this flow.) Pressing „Запиши" afterwards is **not** required for the
picture to persist.

### List

```json
{"Files_getList": {"obj_type": "categories", "obj_property": "PICTURE", "obj_id": 19646}}
```

Each record:

| field | notes |
| --- | --- |
| `name` | the filename — this is what `cat_picture` matches on |
| `size` | bytes |
| `type` | MIME, e.g. `image/png` |
| `last_modified` | `"2026-08-07 23:08:57 +0300"` — null immediately after upload, set once stored |
| `url` | `/endpoints/res/files_get?…` — **the banner URL, ready to serve** |
| `thumbnail_url` | a smaller rendition, free of charge |
| `delete_url` | the full URL that deletes this file — see below |
| `delete_cache_url` | clears the cached renditions |
| `content`, `file` | always null in list responses |

`url` and `thumbnail_url` are relative and came back at 107 and 127 characters for the test file.

### Delete a file

```
POST /endpoints/json/files_delete            parameters in the QUERY STRING, empty body
→ 200, literally `true`
```

Barsy hands you this ready-made as `delete_url` on each record, so there is no need to build it —
which is just as well, since the query carries `file, obj_type, obj_id, obj_property`. Verified end
to end: a probe file was uploaded and then removed, and it disappeared from the list.

---

## 4. `Categories_Create`

```json
{"Categories_Create": {"cat": {"cat_name": "ZZ TEST DELETE ME probe", "parent_id": 3949}}}
→ {"Categories_Create": 19646}
```

Returns the new `cat_id` as a bare integer. **It does not mint a slug** — Публичен URL came back
empty on the new category, which confirms that slug minting is the panel's job (the #85c policy of
parent-slug + `-x<N>`).

Category writes are slow on this tier: the UI showed „Моля изчакайте…" for several seconds. The
existing 280-second timeout and re-read-to-confirm discipline in `catalog.py` stands.

---

## 5. Method-name casing, since it has now cost us twice

The JSON API is **case-sensitive** and inconsistent between surfaces:

- domain methods are `CamelCase_CamelCase`: `Categories_Get`, `Categories_Create`,
  `Categories_Delete`, `Files_getList`
- the path-segment routes are **lowercase**: `/endpoints/json/files_upload`,
  `/endpoints/json/files_delete`
- the UI form helpers are lowercase: `categories_edit`

`Categories_delete` (lowercase d) is not a method. That single letter is the likeliest reason the
#85c probe concluded delete did not exist.

## 6. A caveat on probing this API from a browser console

Replaying a captured call by hand from the page — same URL, same JSON body — is repeatedly
rejected with „Моля, посочете валидно действие", while the page's own identical-looking request
succeeds. Something the app adds is not visible in the body or the query string. **Probe by
driving the UI and intercepting XHR, not by replaying**, or you will conclude a working method is
broken. That failure mode is itself a plausible contributor to the wrong #85c delete note.
