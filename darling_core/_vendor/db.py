# -*- coding: utf-8 -*-
"""SQLite system-of-record — the robust replacement for parsing ~26MB of JSONL on every
query, and native versioning/concurrency in place of the ts-bump / last-wins / lockfile
machinery we hand-rolled.

Design (event-sourced, mirrors what the JSONL already does, but indexed + ACID):
  * versions  — append-only history: one row per stored product version (every stage).
  * products  — the current canonical (latest ts) per code, upserted on each version.

WAL mode + busy_timeout make the concurrent lanes safe writers with no rewrite races.

AUTHORITATIVE STORE (owner directive, corrected 2026-07-19): the DB is the system of record.
All readers read the DB (review_server, run_one/pipeline_v2 done-set + queue, etc.); writers
dual-write DB-FIRST, then append the JSONL. The `results*.jsonl` are a rebuildable append-only
EXPORT / disaster-recovery source — `ingest()` reconstructs the DB from them — but they are NOT
what the pipeline reads at runtime. (The earlier "JSONL is ground truth" note was stale.)
"""
import json, glob, os, sqlite3
from pathlib import Path

HERE = Path(__file__).parent

# The DB is resolved from PIPELINE_DB when set, else next to this file (unchanged default, so the
# pipeline behaves exactly as before). The override exists because this module gets imported from a
# vendored copy inside the panel: resolving the database relative to `__file__` tied the DATA ROOT to
# the MODULE PATH, so moving the import moved the data lookup with it and the panel read a database
# that wasn't there. Data root and import path are now independent. (2026-07-31, panel #98)
DB_PATH = Path(os.environ["PIPELINE_DB"]) if os.environ.get("PIPELINE_DB") else HERE / "pipeline.db"

_STAGE_KEYS = [
    ("name_builder:convention", "rename"),
    ("human_edit", "human"),
    ("regression_restore", "restore"),
    ("field_tidy_applied", "tidy"),
    ("reviewer_autofix", "reviewer"),
    ("agent_fallback_direct", "enrich_fallback"),
    ("agentic", "enrich"),
]


def _stage(notes):
    ns = [str(n) for n in (notes or [])]
    # notes are APPENDED over the product's lifetime and copied forward by every writer,
    # so scan from the END: the last stage marker present is the stage of THIS version
    for n in reversed(ns):
        for key, tag in _STAGE_KEYS:
            if n == key or n.startswith(key + ":"):
                return tag
    return "snapshot"


def connect():
    # sqlite CREATES a missing file rather than failing, so a misconfigured PIPELINE_DB produced an
    # empty 4KB database: the first query said "no such table", and a LATER one would have read it
    # as an EMPTY CATALOGUE — a wrong answer wearing the costume of a real one. An explicitly
    # configured path that isn't there is a misconfiguration, never a first run, so refuse it.
    # (The default path may still be created on demand — that IS a legitimate first run.)
    if os.environ.get("PIPELINE_DB") and not DB_PATH.exists():
        raise FileNotFoundError(
            f"PIPELINE_DB points at {DB_PATH}, which does not exist. Refusing to create an empty "
            f"database there — it would report 'no such table' now and an empty catalogue later.")
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con=None, rebuild=False):
    c = con or connect()
    if rebuild:
        c.executescript("DROP TABLE IF EXISTS versions; DROP TABLE IF EXISTS products;")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS versions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT NOT NULL,
      ts TEXT,
      stage TEXT,
      confidence TEXT,
      needs_human INTEGER,
      primary_model TEXT,
      total_tokens INTEGER,
      fields_json TEXT,
      notes_json TEXT,
      record_json TEXT,
      UNIQUE(code, ts, stage)
    );
    CREATE INDEX IF NOT EXISTS idx_versions_code ON versions(code);
    CREATE INDEX IF NOT EXISTS idx_versions_ts ON versions(ts);
    CREATE TABLE IF NOT EXISTS products(
      code TEXT PRIMARY KEY,
      ts TEXT,
      confidence TEXT,
      needs_human INTEGER,
      primary_model TEXT,
      total_tokens INTEGER,
      fields_json TEXT,
      record_json TEXT,
      notes_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_products_conf ON products(confidence);
    """)
    # migration for pre-existing DBs (ALTER is a no-op error if the column exists)
    try:
        c.execute("ALTER TABLE products ADD COLUMN notes_json TEXT")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS model_spend(
        model TEXT PRIMARY KEY, provider TEXT, tokens INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS model_spend_code(
        code TEXT, model TEXT, tokens INTEGER DEFAULT 0, PRIMARY KEY(code, model))""")
    c.commit()
    if con is None:
        c.close()


def _slim_record(d):
    """Full record minus the bulky meta.agent_log (deep-debug only, not needed by any
    reader) — keeps the DB lean while giving readers everything else."""
    r = dict(d)
    meta = dict(r.get("meta") or {})
    meta.pop("agent_log", None)
    r["meta"] = meta
    return r


def _row_from_record(d):
    f = d.get("fields") or {}
    meta = d.get("meta") or {}
    notes = meta.get("notes") or []
    usage = meta.get("usage") or {}
    return {
        "code": str(d.get("code") or ""),
        "ts": d.get("ts") or "",
        "stage": _stage(notes),
        "confidence": d.get("confidence"),
        "needs_human": 1 if d.get("needs_human") else 0,
        "primary_model": meta.get("primary_model") or usage.get("primary_model"),
        "total_tokens": usage.get("total_tokens"),
        "fields_json": json.dumps(f, ensure_ascii=False),
        "notes_json": json.dumps(notes, ensure_ascii=False),
        "record_json": json.dumps(_slim_record(d), ensure_ascii=False),
    }


def record_version(d, con=None):
    """Insert one product version + upsert canonical if this ts is the newest. Best-effort
    dual-write: callers wrap in try/except so a DB hiccup never breaks the pipeline."""
    r = _row_from_record(d)
    if not r["code"]:
        return
    c = con or connect()
    try:
        c.execute("""INSERT OR IGNORE INTO versions
            (code,ts,stage,confidence,needs_human,primary_model,total_tokens,fields_json,notes_json,record_json)
            VALUES (:code,:ts,:stage,:confidence,:needs_human,:primary_model,:total_tokens,:fields_json,:notes_json,:record_json)""", r)
        row = c.execute("SELECT ts FROM products WHERE code=?", (r["code"],)).fetchone()
        if row is None or (r["ts"] or "") >= (row[0] or ""):
            c.execute("""INSERT INTO products(code,ts,confidence,needs_human,primary_model,total_tokens,fields_json,record_json,notes_json)
                VALUES (:code,:ts,:confidence,:needs_human,:primary_model,:total_tokens,:fields_json,:record_json,:notes_json)
                ON CONFLICT(code) DO UPDATE SET
                  ts=excluded.ts, confidence=excluded.confidence, needs_human=excluded.needs_human,
                  primary_model=excluded.primary_model, total_tokens=excluded.total_tokens,
                  fields_json=excluded.fields_json, record_json=excluded.record_json,
                  notes_json=excluded.notes_json""", r)
        # incremental per-model spend counter (lean read for the billing guard). Version
        # writers COPY records (renames, human edits, autofixes), so raw accumulation would
        # re-count the same usage dict; a per-(code,model) high-watermark adds only the
        # genuinely new tokens. (Trade-off: a full re-enrichment that spends LESS than the
        # prior run registers 0 — rare, and errs on the safe/overcounted side overall.)
        try:
            usage = (d.get("meta") or {}).get("usage") or {}
            for m, dd in (usage.get("by_model") or {}).items():
                t = int(dd.get("pt", 0) or 0) + int(dd.get("ct", 0) or 0)
                if t <= 0:
                    continue
                m, prov = str(m), str(dd.get("provider") or "")
                row = c.execute("SELECT tokens FROM model_spend_code WHERE code=? AND model=?",
                                (r["code"], m)).fetchone()
                counted = int(row[0]) if row else 0
                if t > counted:
                    delta = t - counted
                elif t < counted and r["stage"] in ("enrich", "enrich_fallback"):
                    delta = t          # smaller FRESH run: count it fully, reset watermark
                else:
                    delta = 0          # copied-forward usage (renames, edits, autofixes)
                if delta:
                    c.execute("""INSERT INTO model_spend(model, provider, tokens) VALUES(?,?,?)
                        ON CONFLICT(model) DO UPDATE SET
                          tokens = tokens + excluded.tokens, provider = excluded.provider""",
                              (m, prov, delta))
                    c.execute("""INSERT INTO model_spend_code(code, model, tokens) VALUES(?,?,?)
                        ON CONFLICT(code, model) DO UPDATE SET tokens = excluded.tokens""",
                              (r["code"], m, t))
        except Exception:
            pass
        if con is None:                                 # don't break a caller-managed transaction
            c.commit()
    finally:
        if con is None:
            c.close()


def ingest(verbose=True):
    """(Re)build the DB from every results.lane*.jsonl. Drops+recreates so a schema
    change is picked up. Idempotent — safe to re-run."""
    init_db(rebuild=True)
    c = connect()
    n = 0
    try:
        for fp in [str(HERE / "results.jsonl")] + glob.glob(str(HERE / "results.lane*.jsonl")):
            if not Path(fp).exists():
                continue
            for line in open(fp, encoding="utf-8", errors="replace"):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not d.get("code"):
                    continue
                record_version(d, con=c)
                n += 1
        c.commit()
    finally:
        c.close()
    if verbose:
        print(f"ingested {n} version rows")
    return n


# --------------------------------------------------------------- query API -------
def get_product(code):
    c = connect()
    try:
        row = c.execute("SELECT code,ts,confidence,needs_human,primary_model,total_tokens,fields_json "
                        "FROM products WHERE code=?", (str(code),)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    return {"code": row[0], "ts": row[1], "confidence": row[2], "needs_human": bool(row[3]),
            "primary_model": row[4], "total_tokens": row[5], "fields": json.loads(row[6] or "{}")}


_TOTAL_CACHE = {"n": None}


def catalog_total():
    """Full catalog size = distinct codes ingested from the Microinvest export (the `tasks`
    table). Used as the denominator for 'processed X of N' across the review site. Cached for
    the process lifetime (the export only grows when new supplier data is loaded); falls back
    to the historical 14159 if the table can't be read."""
    if _TOTAL_CACHE["n"] is None:
        try:
            c = connect()
            try:
                _TOTAL_CACHE["n"] = c.execute("SELECT COUNT(DISTINCT code) FROM tasks").fetchone()[0] or 14159
            finally:
                c.close()
        except Exception:
            return 14159
    return _TOTAL_CACHE["n"]


def ingest_tasks(verbose=True):
    """Load tasks.jsonl (the 167MB source-of-truth input) into an indexed table so a
    per-code lookup is a keyed query instead of a 167MB scan. Static input — ingest once
    (re-run only when tasks.jsonl changes)."""
    c = connect()
    c.execute("""CREATE TABLE IF NOT EXISTS tasks(code TEXT PRIMARY KEY, task_json TEXT)""")
    tp = HERE / "tasks.jsonl"
    n = 0
    if tp.exists():
        with open(tp, encoding="utf-8", errors="replace") as f:
            batch = []
            for line in f:
                if not line.strip():
                    continue
                try:
                    t = json.loads(line)
                except Exception:
                    continue
                code = str(t.get("code") or "")
                if not code:
                    continue
                batch.append((code, json.dumps(t, ensure_ascii=False)))
                if len(batch) >= 1000:
                    c.executemany("INSERT OR REPLACE INTO tasks(code,task_json) VALUES(?,?)", batch)
                    n += len(batch); batch = []
            if batch:
                c.executemany("INSERT OR REPLACE INTO tasks(code,task_json) VALUES(?,?)", batch)
                n += len(batch)
    c.commit()
    c.close()
    if verbose:
        print(f"ingested {n} tasks")
    return n


def get_tasks(codes):
    """{code: task} for the given codes — one indexed query instead of scanning 167MB."""
    codes = [str(c) for c in codes]
    if not codes:
        return {}
    con = connect()
    try:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
            return {}
        out = {}
        for i in range(0, len(codes), 400):                # chunk to stay under param limits
            chunk = codes[i:i + 400]
            q = "SELECT code,task_json FROM tasks WHERE code IN (%s)" % ",".join("?" * len(chunk))
            for code, tj in con.execute(q, chunk).fetchall():
                try:
                    out[str(code)] = json.loads(tj)
                except Exception:
                    pass
        return out
    finally:
        con.close()


def all_task_codes():
    """Every task code in tasks.jsonl order (insertion order) — cheap: the code column
    only. Used by the pipeline queue to find the next undone in-shard code without
    streaming the 167MB file."""
    con = connect()
    try:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
            return []
        return [str(r[0]) for r in con.execute("SELECT code FROM tasks ORDER BY rowid").fetchall()]
    finally:
        con.close()


def tasks_ready():
    try:
        con = connect()
        try:
            if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
                return False
            return con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] > 0
        finally:
            con.close()
    except Exception:
        return False


def done_codes():
    """All enriched codes — the pipeline's done-set, one indexed query instead of a
    ~26MB JSONL parse per lane process."""
    con = connect()
    try:
        return {str(r[0]) for r in con.execute("SELECT code FROM products").fetchall()}
    finally:
        con.close()


def done_codes_v2():
    """Codes already processed by PIPELINE V2 (notes contain the v2 marker)."""
    con = connect()
    try:
        return {str(r[0]) for r in con.execute(
            "SELECT DISTINCT code FROM versions WHERE notes_json LIKE ?",
            ('%"v2"%',)).fetchall()}
    finally:
        con.close()


def codes_with_tag(tag):
    """Codes whose CURRENT record carries `tag` in its notes. Used to make a reprocess pass
    idempotent: a flagged code drops out of the reprocess queue the moment its re-run record
    is written with the pass's tag (covers pinned products too, which skip the leaf vote)."""
    con = connect()
    try:
        return {str(r[0]) for r in con.execute(
            "SELECT code FROM products WHERE notes_json LIKE ?",
            (f'%{tag}%',)).fetchall()}
    finally:
        con.close()


def pipeline_index():
    """{code: {conf, brand, fallback, nfields}} for the quality gate — lean columns only
    (no record_json parse)."""
    con = connect()
    try:
        rows = con.execute("SELECT code, confidence, fields_json, notes_json FROM products").fetchall()
    finally:
        con.close()
    out = {}
    for code, conf, fj, nj in rows:
        try:
            f = json.loads(fj or "{}")
        except Exception:
            f = {}
        try:
            notes = json.loads(nj or "[]")
        except Exception:
            notes = []
        out[str(code)] = {
            "conf": {"high": 2, "medium": 1, "low": 0}.get(str(conf), 1),
            "brand": str(f.get("Бранд") or "").strip(),
            "fallback": any("agent_fallback_direct" in str(n) for n in notes),
            "nfields": sum(1 for v in f.values() if str(v or "").strip()),
            "v2": any(str(n) == "v2" for n in notes),
        }
    return out


def get_record(code):
    """The FULL canonical record for ONE code — a keyed query, for hot paths that were
    paying a full-catalog canonical_records() parse per request (~3.5s at 4k, ~12s at 14k)."""
    con = connect()
    try:
        row = con.execute("SELECT record_json FROM products WHERE code=?", (str(code),)).fetchone()
    finally:
        con.close()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    return None


def canonical_records():
    """{code: full_record} for the current canonical of every product — the DB-backed
    replacement for globbing + parsing all lane files on every page load."""
    c = connect()
    try:
        rows = c.execute("SELECT code, record_json FROM products").fetchall()
    finally:
        c.close()
    out = {}
    for code, rj in rows:
        if rj:
            try:
                out[str(code)] = json.loads(rj)
            except Exception:
                pass
    return out


def snapshots(code):
    """Every stored FULL version of a code, oldest-first — for the history timeline."""
    c = connect()
    try:
        if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='versions'").fetchone():
            return []                                   # fresh/absent DB: don't 500 the page
        rows = c.execute("SELECT ts, record_json FROM versions WHERE code=? ORDER BY ts",
                         (str(code),)).fetchall()
    finally:
        c.close()
    out = []
    for ts, rj in rows:
        if rj:
            try:
                out.append((ts, "db", json.loads(rj)))
            except Exception:
                pass
    return out


def is_ready():
    """True if the DB exists and has products — so readers can safely prefer it."""
    try:
        if not DB_PATH.exists():
            return False
        c = connect()
        try:
            return c.execute("SELECT COUNT(*) FROM products").fetchone()[0] > 0
        finally:
            c.close()
    except Exception:
        return False


def source_cards(code):
    """The two SOURCE cards for the review UI, straight from the DB:
      microinvest — the till/empiric data (Код, Цена, Наименование, Категории, …),
      supplier    — the scraped supplier listing (BG/EN name, desc, brand, dims, URL)."""
    t = get_tasks([code]).get(str(code)) or {}
    return {"microinvest": t.get("product") or {}, "supplier": t.get("ground_truth") or {}}


def get_history(code):
    c = connect()
    try:
        rows = c.execute("SELECT ts,stage,confidence,primary_model,total_tokens,fields_json,notes_json "
                         "FROM versions WHERE code=? ORDER BY ts", (str(code),)).fetchall()
    finally:
        c.close()
    return [{"ts": r[0], "stage": r[1], "confidence": r[2], "primary_model": r[3],
             "total_tokens": r[4], "fields": json.loads(r[5] or "{}"),
             "notes": json.loads(r[6] or "[]")} for r in rows]


def stats():
    c = connect()
    try:
        prod = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        vers = c.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        conf = dict(c.execute("SELECT confidence,COUNT(*) FROM products GROUP BY confidence").fetchall())
        tok = c.execute("SELECT SUM(total_tokens),AVG(total_tokens) FROM products WHERE total_tokens IS NOT NULL").fetchone()
        by_model = dict(c.execute("SELECT primary_model,COUNT(*) FROM products WHERE primary_model IS NOT NULL "
                                  "GROUP BY primary_model ORDER BY 2 DESC LIMIT 15").fetchall())
    finally:
        c.close()
    return {"products": prod, "versions": vers, "by_confidence": conf,
            "total_tokens": tok[0], "avg_tokens_per_product": round(tok[1] or 0, 1),
            "top_models": by_model}


if __name__ == "__main__":
    import sys
    if "--ingest" in sys.argv:
        ingest()
    print(json.dumps(stats(), ensure_ascii=False, indent=1))
