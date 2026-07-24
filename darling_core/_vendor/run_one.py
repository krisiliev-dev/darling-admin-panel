# -*- coding: utf-8 -*-
"""Process the next pending product through ONE dedicated Hermes agent run.

All file I/O and bookkeeping happen here in plain Python (stdlib only).
The agent gets exactly one product, its ground truth, the shared guide, and
the web/vision toolsets — nothing else. Its final message must be one JSON
object, which this driver validates and records.

State model (append-only, resumable):
  tasks.jsonl    input queue (from prepare_tasks.py)
  results.jsonl  one line per successfully verified product
  failures.jsonl one line per failed attempt; codes with >= MAX_ATTEMPTS are skipped
  guide.md       shared reviewer guide; agent lessons are appended here

Exit codes: 0 = processed one product, 2 = queue empty, 1 = attempt failed.
"""
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent
TASKS_PATH = HERE / "tasks.jsonl"
CATEGORIES_PATH = HERE / "categories.json"
# Env overrides let bake-off/test runs use a different model and results file
# without touching the production configuration.
RESULTS_PATH = Path(os.environ.get("PIPELINE_RESULTS_PATH") or (HERE / "results.jsonl"))
NO_LESSONS = os.environ.get("PIPELINE_NO_LESSONS") == "1"
# Parallel lanes: PIPELINE_SHARD="0/2" makes this process claim only codes with
# md5(code) % 2 == 0. Each lane appends to its own results file; the done-set
# always reads ALL results*.jsonl so lanes never duplicate work.
SHARD = os.environ.get("PIPELINE_SHARD", "")
FAILURES_PATH = HERE / "failures.jsonl"
GUIDE_PATH = HERE / "guide.md"
LOG_PATH = HERE / "runner.log"
DESC_HASHES_PATH = HERE / "desc_hashes.txt"

# The uv-tools install owns the ~/.hermes profile (free-first model chain,
# tavily web search, gemini vision, running gateway). The venv hermes.exe uses
# a different home (AppData\Local\hermes, zai-only) — do NOT use it here.
HERMES_EXE = r"C:\Users\lora_\AppData\Roaming\uv\tools\hermes-agent\Scripts\hermes.exe"
HERMES_HOME = r"C:\Users\lora_\.hermes"
TOOLSETS = "web,vision"
# Model chain (2026-07-13 rebuild). A 16-model Bulgarian benchmark (5 tasks incl.
# Russian-seeded, scored on RU-letter/garble/CJK/EN contamination) ranked Qwen
# far above the old Mistral worker, which leaked Russian in ~50% of production
# output (the root cause of the high review rate). Results (lower=better):
#   qwen-flash 1.8 (fast 2.5s), qwen3.5-397b 2.4, qwen3-max 2.4, qwen-plus 3.4,
#   Mistral-Large-3 ~unmeasurable/high (prod-evidenced). gpt-oss truncates; small
#   Qwen3 dumps <think>; Aya/Command leaked. See memory bg-model-benchmark.
# WORKER = qwen-flash on Alibaba's free Singapore quota (dashscope-intl); this
# also offloads NVIDIA, relieving the rate-limiting that starved the vision pass.
# Worker failover chain (2026-07-13, free-tier strategy). Alibaba grants each model
# its OWN ~1M free-token quota, so we chain across ALL the clean Qwen chat models —
# ~7 fresh ones × 1M ≈ millions of free tokens — before spilling to other providers.
# As each model's quota exhausts (403 AllocationQuota.FreeTierOnly), it is written to
# EXHAUSTED_PATH and skipped for all future products (no wasted 403 round-trip). All
# probed clean on Bulgarian (ru=0); ordered fastest-first for throughput.
# The WORKER runs via direct OpenAI-compatible API (run_worker_direct) — pure text
# generation, sidesteps the Hermes CLI's context-compression bug on our ~20k prompts.
# CRITICAL free-tier insight (verified 2026-07-13): on Alibaba, each model — INCLUDING
# every DATED snapshot of qwen-plus — carries its OWN ~1M free-token grant. So dated
# snapshots multiply capacity (qwen-plus is exhausted, but qwen-plus-2025-07-14 etc. are
# fresh). All entries below were probed OK + clean (ru=0), fastest-first. Add more dated
# snapshots here as quotas drain — the exhausted-cache skips drained ones automatically.
WORKER_CHAIN = [
    # OWNER POLICY (2026-07-17): DashScope first — fresh free grants (700k-capped each),
    # then the BILLED workhorses (under the DASHSCOPE_BILLED_OK spend cap). Only when
    # DashScope is done/capped does traffic flow to the other free tiers, ordered by
    # quality: Mistral (frontier, ~1B tok/mo) -> Cerebras -> Groq -> NVIDIA -> tail.
    # --- DashScope: drained-grant models (skipped while exhausted) ---
    ["-m", "qwen3.6-flash-2026-04-16", "--provider", "dashscope-intl"],
    ["-m", "qwen3.6-flash", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-flash-2026-02-23", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-flash", "--provider", "dashscope-intl"],
    ["-m", "qwen3.6-27b", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-122b-a10b", "--provider", "dashscope-intl"],
    ["-m", "qwen3.6-35b-a3b", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-35b-a3b", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-27b", "--provider", "dashscope-intl"],
    ["-m", "qwen-plus-2025-09-11", "--provider", "dashscope-intl"],
    # --- DashScope: fresh free grants (700k hard cap each) ---
    ["-m", "qwen-plus-character", "--provider", "dashscope-intl"],
    ["-m", "qwen-plus-latest", "--provider", "dashscope-intl"],
    # qwen-plus-2025-01-25 REMOVED (owner 2026-07-19: NO free quota — see NEVER_CALL)
    ["-m", "qwen3.6-plus-2026-04-02", "--provider", "dashscope-intl"],
    ["-m", "qwen3.6-plus", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-plus-2026-04-20", "--provider", "dashscope-intl"],
    ["-m", "qwen3.7-plus", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-plus-2026-02-15", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-plus", "--provider", "dashscope-intl"],
    ["-m", "qwen3-max-2026-01-23", "--provider", "dashscope-intl"],
    ["-m", "qwen3-max", "--provider", "dashscope-intl"],
    ["-m", "qwen3-max-2025-09-23", "--provider", "dashscope-intl"],
    ["-m", "qwen3-max-preview", "--provider", "dashscope-intl"],
    ["-m", "qwen3.7-max-preview", "--provider", "dashscope-intl"],
    ["-m", "qwen3.7-max-2026-05-17", "--provider", "dashscope-intl"],
    ["-m", "qwen3.6-max-preview", "--provider", "dashscope-intl"],
    ["-m", "qwen3.7-max", "--provider", "dashscope-intl"],
    ["-m", "qwen3.7-max-2026-06-08", "--provider", "dashscope-intl"],
    ["-m", "qwen3.7-max-2026-05-20", "--provider", "dashscope-intl"],
    # --- DashScope: MORE free-grant models (owner 2026-07-20: 62 qwen chat models exist, tap
    #     the unused ones — each is its own ~700k free grant; the cap + exhausted-cache guard). ---
    ["-m", "qwen-flash", "--provider", "dashscope-intl"],
    ["-m", "qwen-max", "--provider", "dashscope-intl"],
    ["-m", "qwen-plus", "--provider", "dashscope-intl"],
    ["-m", "qwen-plus-2025-07-14", "--provider", "dashscope-intl"],
    ["-m", "qwen-plus-2025-04-28", "--provider", "dashscope-intl"],
    ["-m", "qwen-turbo", "--provider", "dashscope-intl"],
    ["-m", "qwen3-next-80b-a3b-instruct", "--provider", "dashscope-intl"],
    ["-m", "qwen3.5-397b-a17b", "--provider", "dashscope-intl"],
    ["-m", "qwen3-235b-a22b-instruct-2507", "--provider", "dashscope-intl"],
    ["-m", "qwen3-235b-a22b", "--provider", "dashscope-intl"],
    ["-m", "qwen3-32b", "--provider", "dashscope-intl"],
    ["-m", "qwen3-30b-a3b-instruct-2507", "--provider", "dashscope-intl"],
    ["-m", "qwen3-30b-a3b", "--provider", "dashscope-intl"],
    ["-m", "qwen3-14b", "--provider", "dashscope-intl"],
    ["-m", "qwen3-8b", "--provider", "dashscope-intl"],
    # --- DashScope: BILLED workhorses (spend-capped; newest first) ---
    ["-m", "qwen3.7-plus-2026-05-26", "--provider", "dashscope-intl"],
    ["-m", "qwen-plus-2025-12-01", "--provider", "dashscope-intl"],
    # --- free tiers: BEST model per provider FIRST, then that provider's weaker models on
    # exhaustion, before moving to the next provider (owner 2026-07-18). DashScope OFF
    # (arrearage). Cross-provider order by quality+reliability: Mistral (frontier) → Groq
    # (fast, large) → zai → Cerebras → NVIDIA → Cohere. All model names probed live. ---
    # Mistral RE-ENABLED 2026-07-20 with a fresh working key (z32mAy…). Free "Experiment" tier
    # covers ALL models at $0 under the shared ~1B/month pool → these are FREE; the 975M provider
    # cap (MISTRAL_BUDGET) blocks the whole provider before it can silently bill. Most-capable
    # general models only, best→weaker (coding/OCR/audio/embed models excluded):
    ["-m", "mistral-large-latest", "--provider", "mistral"],    # flagship (mistral-large-2512)
    ["-m", "mistral-medium-latest", "--provider", "mistral"],   # strong + faster (medium-3.5)
    ["-m", "mistral-small-latest", "--provider", "mistral"],    # efficient tail
    ["-m", "openai/gpt-oss-120b", "--provider", "groq"],      # 120B best on Groq
    ["-m", "llama-3.3-70b-versatile", "--provider", "groq"],
    ["-m", "qwen/qwen3.6-27b", "--provider", "groq"],
    ["-m", "llama-3.1-8b-instant", "--provider", "groq"],     # 8B: highest free rpd, last resort
    ["-m", "glm-4.7", "--provider", "zai"],                   # full GLM before flash
    ["-m", "glm-4.7-flash", "--provider", "zai"],
    ["-m", "gpt-oss-120b", "--provider", "cerebras"],         # (paid-gated until billing active)
    ["-m", "gemma-4-31b", "--provider", "cerebras"],
    ["-m", "nvidia/llama-3.3-nemotron-super-49b-v1", "--provider", "nvidia"],  # strong+fast
    ["-m", "meta/llama-3.3-70b-instruct", "--provider", "nvidia"],
    ["-m", "qwen/qwen3.5-397b-a17b", "--provider", "nvidia"],  # 397B best-quality but ~2min: last
    ["-m", "command-a-03-2025", "--provider", "cohere"],
]
# Back-compat default (a few call sites reference MODEL_ARGS); polish uses a fresh model.
MODEL_ARGS = list(WORKER_CHAIN[0])
EXHAUSTED_PATH = HERE / "exhausted_models.txt"

# HARD BLOCKLIST (owner 2026-07-19): models with ZERO free quota — every call BILLS, so they
# must NEVER be invoked (the 700k budget cap is reactive; it can't protect a model that bills
# from token #1). Enforced at the completion choke points AND folded into the exhausted set so
# the WORKER_CHAIN skips them. Omni/realtime + voice-enrollment are audio/realtime (no free
# tier); the three LLMs below are paid-only snapshots.
NEVER_CALL = {
    "qwen-omni-turbo-realtime-latest", "qwen-omni-turbo-latest", "voice-enrollment",
    "qwen-plus-character-ja", "qwen-plus-2025-01-25", "glm-5.2-fast-preview",
}

REVIEW_ENABLED = os.environ.get("PIPELINE_REVIEW", "1") == "1"
REVIEW_MODEL_ARGS = ["-m", "glm-4.7-flash", "--provider", "zai"]  # vestigial; reviewer uses REVIEW_CHAIN
REVIEW_TOOLSETS = "search"


# HARD BUDGET GUARD (2026-07-16 billing incident): DashScope free grants are 1M tokens
# per model, ONE-TIME. On a billable account, exceeding the grant does NOT 403 — it
# silently rolls to pay-as-you-go (this is how qwen-plus-2025-12-01 billed 188M+ tokens).
# So we enforce the grant OURSELVES: any dashscope model whose measured cumulative usage
# exceeds DASHSCOPE_BUDGET is treated as exhausted, permanently. Additionally, dashscope
# is DISABLED entirely unless env DASHSCOPE_OK=1 (owner must explicitly re-enable after
# settling the balance and accepting the per-model budget policy).
BILLED_WORKHORSES = {"qwen3.7-plus-2026-05-26", "qwen-plus-2025-12-01"}
DASHSCOPE_BUDGET = 700_000                      # 300k margin: 12 lanes have ~1 product in flight each before telemetry lands, so the cap must absorb concurrent drift
_BUDGET_SPENT = None


def _dashscope_allowed():
    # env var OR flag file (cron lanes don't inherit the shell env, so the file is the
    # reliable cross-process switch — delete DASHSCOPE_OK file to cut Alibaba off again).
    # DASHSCOPE_OFF=1 per-process override: the reviewer fleet runs with it so reviews go
    # to Mistral/Cerebras/Groq and the DashScope grants stay reserved for ENRICHMENT.
    if os.environ.get("DASHSCOPE_OFF") == "1":
        return False
    return os.environ.get("DASHSCOPE_OK") == "1" or (HERE / "DASHSCOPE_OK").exists()


def _dashscope_spend():
    """{model: measured dashscope tokens} — lean indexed read from model_spend (kept
    incrementally by db.record_version). The old full record_json re-parse per check was
    a measurable lane-throughput drag."""
    spent = {}
    try:
        import db as _db
        con = _db.connect()
        try:
            for m, t in con.execute("SELECT model, tokens FROM model_spend "
                                    "WHERE provider='dashscope-intl'").fetchall():
                spent[str(m)] = int(t or 0)
        finally:
            con.close()
    except Exception:
        pass
    return spent


def _billed_cap_millions():
    """Owner-controlled BILLED-overflow ceiling: the DASHSCOPE_BILLED_OK file contains a
    number = max NEW billed tokens in MILLIONS beyond the free grants (edit to raise;
    delete the file for free-grants-only). Missing/invalid file = 0 (no billed usage)."""
    try:
        return float((HERE / "DASHSCOPE_BILLED_OK").read_text(encoding="utf-8").strip().split()[0])
    except Exception:
        return 0.0


def billed_overflow_tokens(spent=None):
    """Measured tokens beyond each model's 1M free grant — the part that BILLS."""
    spent = spent if spent is not None else _dashscope_spend()
    return sum(max(0, t - 1_000_000) for t in spent.values())


def _budget_exhausted():
    """dashscope models to block, per the owner's spend policy:
      * free-grants-only (no billed cap): block any model beyond DASHSCOPE_BUDGET (700k).
      * billed allowed (cap set): allow overflow while cumulative billed tokens stay
        under the cap; when the cap is reached, fall back to free-grant policy."""
    global _BUDGET_SPENT
    if _BUDGET_SPENT is None:
        spent = _dashscope_spend()
        cap_m = _billed_cap_millions()
        over = {m for m, t in spent.items() if t > DASHSCOPE_BUDGET}
        if cap_m > 0 and billed_overflow_tokens(spent) < cap_m * 1_000_000:
            # billing authorized: fresh grants STAY protected at 700k (they must drain
            # for free, one by one); only the designated workhorses may bill.
            _BUDGET_SPENT = over - BILLED_WORKHORSES
        else:
            _BUDGET_SPENT = over
    return _BUDGET_SPENT


# MISTRAL BUDGET GUARD (owner 2026-07-20, card now on file): the free "Experiment" tier is
# ~1B tokens/MONTH shared across ALL models (not per-model grants). With a card attached,
# crossing 1B does NOT 403 — it silently bills at full rates (the DashScope trap). So we guard
# the WHOLE provider: once cumulative Mistral spend hits MISTRAL_BUDGET, every Mistral model is
# treated as exhausted. NOTE model_spend is cumulative ALL-TIME while the grant is MONTHLY — if
# this ever trips, reset the provider='mistral' rows at a month boundary (as done for dashscope).
MISTRAL_BUDGET = 975_000_000
_MISTRAL_BLOCK = None


def _mistral_spend_total():
    tot = 0
    try:
        import db as _db
        con = _db.connect()
        try:
            for (t,) in con.execute("SELECT tokens FROM model_spend WHERE provider='mistral'").fetchall():
                tot += int(t or 0)
        finally:
            con.close()
    except Exception:
        pass
    return tot


def _mistral_over_budget():
    """>= the 975M cap -> block every Mistral model (both chat + vision)."""
    global _MISTRAL_BLOCK
    if _MISTRAL_BLOCK is None:
        if _mistral_spend_total() >= MISTRAL_BUDGET:
            _MISTRAL_BLOCK = {c[c.index("-m") + 1] for c in WORKER_CHAIN
                              if c[c.index("--provider") + 1] == "mistral"}
            _MISTRAL_BLOCK |= {m for p, m in VL_CHAIN if p == "mistral"}
        else:
            _MISTRAL_BLOCK = set()
    return _MISTRAL_BLOCK


def _load_exhausted():
    try:
        ex = set(l.strip() for l in EXHAUSTED_PATH.read_text(encoding="utf-8").splitlines() if l.strip())
    except FileNotFoundError:
        ex = set()
    ex |= _budget_exhausted()
    ex |= _mistral_over_budget()                      # provider-wide 975M/month cap
    ex |= NEVER_CALL                                 # no-free-quota models: never chain to them
    return ex


def _mark_exhausted(model):
    with open(EXHAUSTED_PATH, "a", encoding="utf-8") as f:
        f.write(model + "\n")


def _is_quota_error(msg):
    m = str(msg).lower()
    return "freetieronly" in m or "free quota has been exhausted" in m or "allocationquota" in m


def _is_arrearage(msg):
    m = str(msg).lower()
    return "arrearage" in m or "overdue" in m or "account is in good standing" in m


def maybe_pause_on_arrearage(exc):
    """CIRCUIT BREAKER (owner's request): the instant a provider reports arrearage —
    i.e. ANY charge accrued on an account with no payment method — write the PAUSE
    sentinel so EVERY lane stops immediately. Returns True if it paused."""
    if _is_arrearage(exc):
        try:
            (HERE / "PAUSE").write_text(
                "AUTO-PAUSED (arrearage/charge detected) " +
                datetime.now().isoformat(timespec="seconds") +
                " — a charge accrued on the model provider. All lanes stopped; resolve billing to resume.",
                encoding="utf-8")
        except Exception:
            pass
        try:
            log("!!! ARREARAGE/charge detected -> AUTO-PAUSED (PAUSE sentinel written). !!!")
        except Exception:
            pass
        return True
    return False
# 7 min: GLM p95 is ~150s; anything past this is a provider hang, and two of
# these (worker + reviewer) must fit inside the cron shim's kill window.
AGENT_TIMEOUT_S = 420
MAX_ATTEMPTS = 3
MAX_PROMPT_CHARS = 24000

ENRICHED_COLS = [
    "Код", "Каталожен номер", "Наименование", "Синоними", "Описание",
    "Мета наименование", "Мета описание", "Бранд", "Доставчик", "Категории",
    "Мярка", "Цена", "Количество", "Снимки", "Тегло (кг)", "Активен",
    "Предварителна поръчка", "Атрибути", "Варианти", "Серия", "Свързани",
]
# Fields the agent is allowed to change. Everything else is copied from the original.
EDITABLE = [
    "Наименование", "Описание", "Мета наименование", "Мета описание", "Бранд",
    "Категории", "Тегло (кг)", "Атрибути", "Варианти", "Свързани", "Синоними",
]
# Empiric/operational columns are DRIVER-OWNED: taken verbatim from the source
# and Microinvest, never generated or even shown to the agent (owner directive
# 2026-07-11 — so the AI cannot leak a price/quantity/code into any text field).
OPERATIONAL_COLS = [
    "Код", "Каталожен номер", "Цена", "Количество", "Мярка", "Доставчик",
    "Активен", "Предварителна поръчка",
]

# Dedicated vision-verification pass (3rd agent, 2026-07-11): checks the FINAL
# product against its photo for shape/type/material/colour/count. Catches errors
# the text reviewer (no image) and the worker (may trust a misleading name like
# "Ø20 cm" on a rectangular pan) both miss. Direct NVIDIA call, not through
# hermes, so we control the model and payload size.
VISION_VERIFY_ENABLED = os.environ.get("PIPELINE_VISION_VERIFY", "1") == "1"
VISION_VERIFY_MODEL = os.environ.get("PIPELINE_VISION_MODEL",
                                     "meta/llama-3.2-90b-vision-instruct")
# Vision failover chain (2026-07-18): NVIDIA's vision endpoint was 504-ing, which silently
# killed the photo QC. mistral-medium is VL-capable and independent, so vision survives one
# provider outage. Each entry: (provider-key-in-DIRECT_PROVIDERS or "nvidia", model).
# Vision chain (owner 2026-07-20): Alibaba qwen-VL FIRST — free grants, and NVIDIA vision was
# carrying it alone (13.8M tokens, now timing out). Mistral dropped (key dead / 401).
VL_CHAIN = [("dashscope-intl", "qwen-vl-max"),       # WORKING + fast (~1.1s, probe 2026-07-21) — promoted
            ("dashscope-intl", "qwen3-vl-flash"),
            ("dashscope-intl", "qwen3-vl-plus"),     # quota-exhausted now; cheap-fail + auto-skip, may recover
            ("nvidia", VISION_VERIFY_MODEL),         # TIMING OUT 2026-07-21 (nvidia vision endpoint) — demoted
            ("mistral", "mistral-medium-latest")]    # DEAD key 401 2026-07-21 — last-resort until re-keyed

# Language-polish pass: rewrites wording (facts frozen) when garbled/foreign
# words survive review, so language issues become clean output not human work.
POLISH_ENABLED = os.environ.get("PIPELINE_POLISH", "0") == "1"

# Common Russian-isms models leak into Bulgarian text. Deterministic fix runs
# before spellcheck so these become clean instead of escalating to a human.
RU_FIX = {
    "подходит": "подходящ", "покрытие": "покритие", "покрытия": "покрития",
    "покрытието": "покритието", "обеспечава": "осигурява",
    "обеспечи": "осигури", "обеспечиващ": "осигуряващ", "современен": "съвременен",
    "современа": "съвременна", "можно": "може", "применение": "приложение",
    "цвет": "цвят", "изготовлен": "изработен", "изготовена": "изработена",
    "прочный": "здрав", "ёмкость": "вместимост", "неза": "не", "достаточно": "достатъчно",
    "остатки": "остатъци", "также": "също", "длина": "дължина", "объем": "обем",
    "нержавеема": "неръждаема", "нержавеюща": "неръждаема", "металлен": "метален",
    "стойкост": "устойчивост", "готовене": "готвене", "губка": "гъба",
    "чистящо": "почистващо", "чистящи": "почистващи", "детергент": "препарат",
    # Recurring leaks observed in live output (2026-07 audit). Only unambiguous
    # orthographic cognates — identical meaning, Russian spelling — are listed;
    # semantic substitutions are deliberately excluded to protect accuracy.
    "другие": "други", "результат": "резултат", "результати": "резултати",
    "результата": "резултата", "результате": "резултат", "результаты": "резултати",
    "интерьер": "интериор", "интерьера": "интериора", "интерьере": "интериора",
    "интерьеры": "интериори", "интерьери": "интериори", "интерьор": "интериор",
    "интерьора": "интериора", "интерьоре": "интериора",
    "обеспечива": "осигурява", "обеспечивает": "осигурява",
    "пятна": "петна", "пятно": "петно", "пятен": "петна",
    "напиток": "напитка", "напитки": "напитки",
    "создадете": "създадете", "создайте": "създайте", "создава": "създава",
    "создаде": "създаде", "создаване": "създаване",
    "избегавайте": "избягвайте", "избегайте": "избягвайте", "избегава": "избягва",
    "изпечване": "изпичане",
    "профессионал": "професионал", "профессионална": "професионална",
    "профессионален": "професионален", "профессионално": "професионално",
    "профессионални": "професионални", "профессионализъм": "професионализъм",
    # Russian-only words (no Bulgarian meaning) that leaked into live output —
    # direct 1:1 translations, not risky semantic guesses (2026-07-14 audit).
    "бренд": "бранд", "бренда": "бранда", "брендове": "брандове",
    # NOTE (owner 2026-07-19): «посуда» is acceptable Bulgarian usage — do NOT flag or
    # auto-replace it. Only genuinely-Russian forms below.
    "нержавеща": "неръждаема", "нержавещата": "неръждаемата",
    "нержавейка": "неръждаема стомана", "нержавей": "неръждаема",
    "герметичен": "херметичен", "герметичният": "херметичният",
    "герметична": "херметична", "герметично": "херметично",
    "загрязнение": "замърсяване", "загрязнения": "замърсявания",
    "загрязнений": "замърсявания", "загрязнявания": "замърсявания",
    "герметичен": "херметичен", "герметична": "херметична",
    "герметично": "херметично", "герметични": "херметични",
    "герметичност": "херметичност", "герметичността": "херметичността",
    "аутентичен": "автентичен", "аутентична": "автентична",
    "аутентично": "автентично", "аутентични": "автентични",
    "аутентичния": "автентичния", "аутентичният": "автентичният",
    "аутентичность": "автентичност",
    "латун": "месинг", "латунен": "месингов", "латунна": "месингова",
    "оцинкован": "поцинкован", "оцинкована": "поцинкована",
    "оцинковано": "поцинковано", "оцинковани": "поцинковани",
    # Inflections the exact-match map missed but the marker net caught (2026-07-14).
    "нержавееща": "неръждаема", "нержавеещи": "неръждаеми",
    "нержавеещ": "неръждаем", "нержавеещо": "неръждаемо",
    "нержавейка": "неръждаема стомана", "нержавейки": "неръждаема стомана",
    "профессионалната": "професионалната", "профессионалния": "професионалния",
    "профессионалният": "професионалният", "профессионалните": "професионалните",
    "профессионалната": "професионалната",
    "современно": "съвременно", "современни": "съвременни", "современ": "съвременен",
    "современые": "съвременни", "современых": "съвременни",
    "посудомийка": "съдомиялна", "посудомийна": "съдомиялна",
    "посудомия": "съдомиялна", "посудомоечная": "съдомиялна",
    "посудомийки": "съдомиялни", "посудомийните": "съдомиялните",
    "современа": "съвременна", "современите": "съвременните",
}

# Curated Russian-word markers — stems that are UNAMBIGUOUSLY Russian and have no
# valid Bulgarian word sharing them. Used as the genuine text-contamination signal
# in the agentic path (replacing a crude spellcheck word-count that false-positived
# on valid commerce vocabulary). RU_FIX already auto-corrects the known forms above;
# this net catches inflections/variants that slip past the exact-match map. Keep it
# HIGH-precision — only add a stem when no Bulgarian word legitimately contains it.
RU_MARKERS = re.compile(
    r"(?:загрязн|герметич|аутентич|профессио|нержаве|"
    r"обеспечи|обеспеча|покрыти|результат|интерьер|создад|создай|"
    r"избега|современ)",
    re.IGNORECASE,
)


def russian_survivors(text):
    """Words in `text` that match a curated Russian-only marker (post RU_FIX).
    These are genuine Cyrillic contamination — NOT phunspell dictionary misses."""
    if not text:
        return []
    return [w for w in re.findall(r"[а-яА-ЯёэыЁЭЫ]+", str(text))
            if RU_MARKERS.search(w)]

# Supplier / operator names are NOT product brands (an agent once branded a
# product "LEVENA" because the supplier company checked out on the web).
SUPPLIER_NAMES = [
    "levena", "левена", "mira-n", "mira n", "мира-н", "мира н", "egera", "егера",
    "inhouse", "in house", "ин хаус", "city lights", "city-lights", "сити лайтс",
    "vertex", "вертекс", "kalian", "калиан", "plamplast", "плампласт",
    "elbulgaria", "ел българия", "microinvest", "микроинвест",
]


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        # cp1252 stdout under some launchers: Cyrillic log lines must never turn a
        # successfully written record into a spurious "failed"
        print(line.encode("ascii", "replace").decode())
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_jsonl_codes(path, key="code"):
    codes = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        codes.append(json.loads(line)[key])
                    except Exception:
                        pass
    return codes


def _in_shard(code):
    if not SHARD:
        return True
    try:
        idx, total = (int(x) for x in SHARD.split("/"))
    except ValueError:
        # A typo'd PIPELINE_SHARD used to silently claim EVERY code — 12 lanes then
        # duplicate each other's work. Fail loudly instead.
        raise ValueError(f"malformed PIPELINE_SHARD={SHARD!r} (expected 'i/N')")
    import hashlib
    return int(hashlib.md5(code.encode()).hexdigest(), 16) % total == idx


_DONE_CACHE = None
_TASKCODES_CACHE = None
_PRIORITY_CACHE = {"v": None, "loaded": False}


def _priority_set():
    """Optional processing allow-list. When V2_PRIORITY.txt exists, lanes serve ONLY the
    codes listed there (one per line) — used to process a high-confidence batch first
    (e.g. products whose brand is GS1-confirmed) and then idle until the file is extended.
    Returns a set, or None when the file is absent (serve everything). Re-read when the
    file's mtime changes so appending a new batch resumes the fleet without a restart."""
    p = HERE / "V2_PRIORITY.txt"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _PRIORITY_CACHE.update(v=None, loaded=True, mtime=None)
        return None
    if _PRIORITY_CACHE.get("mtime") != mtime:
        codes = {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                 if ln.strip()}
        _PRIORITY_CACHE.update(v=codes, loaded=True, mtime=mtime)
    return _PRIORITY_CACHE["v"]


_REPROCESS_CACHE = {"key": None, "v": frozenset()}
_REPROCESSED_THIS_PROC = set()      # codes this lane process has already re-served (avoid looping)
REPROCESS_TAG = os.environ.get("V2_REPROCESS_TAG") or "reproc_embed1"


def _reprocess_set():
    """Force-reprocess allow-list. When V2_REPROCESS.txt exists, its codes are re-served even
    though they already have a v2 record — used to re-run flagged products through an improved
    pipeline (the embedding-grounded leaf consensus). A code drops out the moment its record
    carries REPROCESS_TAG, so the pass is idempotent and resumable: each listed code is
    reprocessed exactly once, then treated as done. Returns a set (empty when the file is
    absent). GATED on env V2_REPROCESS=1 so ONLY the dedicated reprocess driver's lanes re-serve
    — cron/priority lanes (which don't stamp REPROCESS_TAG) must ignore the file, else they'd loop
    forever on it and abandon their normal queue."""
    if os.environ.get("V2_REPROCESS") != "1":
        return set()
    p = HERE / "V2_REPROCESS.txt"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _REPROCESS_CACHE.update(key=None, v=frozenset())
        return set()
    if _REPROCESS_CACHE.get("key") != mtime:
        targets = {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
        try:
            import db as _db
            targets -= _db.codes_with_tag(REPROCESS_TAG)         # already redone -> drop
        except Exception:
            pass
        _REPROCESS_CACHE.update(key=mtime, v=frozenset(targets))
    return set(_REPROCESS_CACHE["v"]) - _REPROCESSED_THIS_PROC


def _done_set():
    """Codes already processed. DB-first (indexed, no ~26MB parse per lane process; the
    DB is authoritative and written first) UNION the small legacy results.jsonl (predates
    lane files); full JSONL scan only as fallback. Cached per-process + grown via
    mark_done()."""
    global _DONE_CACHE
    if _DONE_CACHE is None:
        s = None
        try:
            import db as _db
            if (HERE / "V2_MODE").exists():
                # v2 reprocessing: EVERYTHING is todo until it has a v2 version;
                # union the sidecar of records whose DB write failed (JSONL-only)
                s = _db.done_codes_v2()
                s.update(load_jsonl_codes(HERE / "v2_done_pending.jsonl"))
            elif _db.is_ready():
                s = _db.done_codes()
                s.update(load_jsonl_codes(HERE / "results.jsonl"))   # tiny legacy file
        except Exception:
            s = None
        if s is None:
            s = set()
            if (HERE / "V2_MODE").exists():
                # v2 fallback: only records carrying the v2 marker count as done
                for rp in HERE.glob("results.lane*.jsonl"):
                    try:
                        with open(rp, encoding="utf-8", errors="replace") as f:
                            for line in f:
                                if '"v2"' in line:
                                    try:
                                        s.add(str(json.loads(line).get("code") or ""))
                                    except Exception:
                                        pass
                    except OSError:
                        pass
                s.update(load_jsonl_codes(HERE / "v2_done_pending.jsonl"))
            else:
                for rp in [HERE / "results.jsonl", *HERE.glob("results.lane*.jsonl")]:
                    s.update(load_jsonl_codes(rp))
        _DONE_CACHE = s
    return _DONE_CACHE


def mark_done(code):
    if _DONE_CACHE is not None:
        _DONE_CACHE.add(str(code))


def _fail_counts():
    fc = {}
    v2 = (HERE / "V2_MODE").exists()
    try:
        with open(FAILURES_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                if v2 and '"pipeline": "v2"' not in line:
                    continue            # v1-era failures must not starve the v2 queue
                try:
                    c = str(json.loads(line).get("code") or "")
                except Exception:
                    continue
                if c:
                    fc[c] = fc.get(c, 0) + 1
    except FileNotFoundError:
        pass
    return fc


def _next_task_scan(target_code, fail_counts):
    """Original fallback: stream tasks.jsonl. Used when the DB tasks table isn't ready."""
    reproc = _reprocess_set()
    done = _done_set() - reproc                    # force-reprocess codes are "not done"
    prio = _priority_set()
    if reproc:                                     # a reprocess pass serves ONLY these codes
        prio = reproc                              # overrides any standing V2_PRIORITY batch
    with open(TASKS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            task = json.loads(line)
            code = str(task["code"])
            if target_code is not None:
                if code == str(target_code):
                    return task, fail_counts.get(code, 0)
                continue
            if code in done or fail_counts.get(code, 0) >= MAX_ATTEMPTS \
                    or not _in_shard(code) \
                    or (prio is not None and code not in prio):
                continue
            if code in reproc:
                _REPROCESSED_THIS_PROC.add(code)   # don't re-serve within this process
            return task, fail_counts.get(code, 0)
    return None, 0


def next_task(target_code=None):
    """Return (task, fail_count) for the next undone in-shard product, or (None, 0).
    Fast path: `done` cached from the authoritative JSONL + candidate codes/task fetched
    from the indexed DB (no 167MB scan). Falls back to the streaming scan if the DB tasks
    table isn't ready — identical semantics either way."""
    fail_counts = _fail_counts()
    try:
        import db
        if db.tasks_ready():
            if target_code is not None:
                t = db.get_tasks([target_code]).get(str(target_code))
                return (t, fail_counts.get(target_code, 0)) if t else (None, 0)
            global _TASKCODES_CACHE
            if _TASKCODES_CACHE is None:
                _TASKCODES_CACHE = db.all_task_codes()
            reproc = _reprocess_set()
            done = _done_set() - reproc                 # force-reprocess codes are "not done"
            prio = _priority_set()
            if reproc:                                  # a reprocess pass serves ONLY these codes
                prio = reproc                           # overrides any standing V2_PRIORITY batch
            for code in _TASKCODES_CACHE:
                if str(code) in done or fail_counts.get(code, 0) >= MAX_ATTEMPTS \
                        or not _in_shard(code) \
                        or (prio is not None and str(code) not in prio):
                    continue
                t = db.get_tasks([code]).get(code)
                if t:
                    if str(code) in reproc:
                        _REPROCESSED_THIS_PROC.add(str(code))
                    return t, fail_counts.get(code, 0)
            return None, 0
    except Exception:
        pass
    return _next_task_scan(target_code, fail_counts)


def known_categories():
    if CATEGORIES_PATH.exists():
        return json.loads(CATEGORIES_PATH.read_text(encoding="utf-8"))
    return []


def guide_text():
    text = GUIDE_PATH.read_text(encoding="utf-8") if GUIDE_PATH.exists() else ""
    if len(text) > 9000:
        text = text[:6000] + "\n…[съкратено]…\n" + text[-2500:]
    return text


def build_prompt(task):
    # Hide empiric/operational fields from the agent entirely (owner directive):
    # code, price, quantity, unit, supplier — the AI never sees them, so it
    # cannot echo them into a name or description.
    product = {k: v for k, v in task["product"].items()
               if v is not None and k not in OPERATIONAL_COLS}
    gt = {k: v for k, v in task["ground_truth"].items() if v is not None}
    if gt.get("Scrape raw (JSON)"):  # scrub prices out of the raw scrape blob
        gt["Scrape raw (JSON)"] = re.sub(
            r'"scrape_price_[a-z]+"\s*:\s*"[^"]*"\s*,?', "", gt["Scrape raw (JSON)"])
    candidates = task["related_candidates"]
    filter_schema = task.get("filter_schema", [])

    match_kind = str(task["ground_truth"].get("Match kind") or "")
    weak_match_block = ""
    if match_kind.startswith(("name", "model")):
        weak_match_block = f"""
## ⚠ СЛАБО СЪВПАДЕНИЕ ({match_kind})
Скрейпнатите данни и снимката са намерени по ПРИБЛИЗИТЕЛНО име — може да описват ДРУГ,
подобен продукт (една обява често е съпоставена на няколко складови кода). Затова:
- НЕ пренасяй съдържание/цвят/брой части от снимката или скрейпа, освен ако складовото
  име ги потвърждава;
- НЕ преразказвай оригиналното описание (то е недоверено AI съдържание);
- направи МИНИМАЛНИ безопасни корекции (език, формат, каталожен номер), опиши
  несигурността в "Проблеми" и сложи "needs_human": true.
"""
    prompt = f"""Ти си прецизен проверител на продуктови данни за български онлайн магазин.
Обработваш ТОЧНО ЕДИН продукт. Никога не измисляй факти — работи само с данните по-долу и с инструментите web_search / vision_analyze.
{weak_match_block}

## РЪКОВОДСТВО (спазвай го стриктно)
{guide_text()}

## ПРОДУКТ — текущи стойности (AI-генерирани, може да съдържат грешки)
```json
{json.dumps(product, ensure_ascii=False, indent=1)}
```

## GROUND TRUTH — надеждни данни (складова база Microinvest + скрейпнати данни от доставчик)
```json
{json.dumps(gt, ensure_ascii=False, indent=1)}
```

## КАНДИДАТИ за "Свързани" (продукти със същия префикс на кода; избирай САМО от тях)
```json
{json.dumps(candidates, ensure_ascii=False)}
```

## ФИЛТЪРНА СХЕМА за категорията (ключове и ДОПУСТИМИ стойности за Атрибути)
```json
{json.dumps(filter_schema, ensure_ascii=False, indent=1)}
```

## ВАЛИДНИ КАТЕГОРИИ на сайта (Категории може да съдържа САМО такива, разделени с ";")
{"; ".join(known_categories())}

## ЗАДАЧА
Провери и коригирай AI-генерираните полета: Наименование, Описание, Мета наименование, Мета описание, Бранд, Категории, Тегло (кг), Атрибути, Синоними, Варианти, Свързани.
1. Сравни текущите стойности с ground truth. При противоречие ground truth печели.
2. Наименование: приложи формулата от ръководството (тип продукт първо, без каталожни номера, без съкращения, единици на латиница).
3. Бранд: ако липсва — вземи от "Бранд (скрейп)"; ако го няма там, но има вероятен бранд в името, провери с web_search дали брандът съществува. Ако не може да се потвърди — остави празно.
4. Описание: следвай SEO HTML формата от ръководството (h2 + увод + <ul> характеристики + употреба). Материалът трябва да е потвърден.
5. Атрибути: нормализирай по филтърната схема — това захранва филтрите на сайта. Попълни всички известни групи с допустими стойности.
6. Снимка: ако има URL (Снимки/Снимка), извикай vision_analyze ВЕДНЪЖ и провери едновременно: материал/цвят/форма/брой части за описанието и атрибутите + одит на снимката (воден знак? бял фон? няколко продукта? качество? съответствие с името?). Резултата от одита запиши в "Снимка проблеми". Брой части, изписан на опаковката (напр. "12" на кутията), е надежден източник.
   ЧЕСТНОСТ: ако vision_analyze върне грешка — опитай ВТОРИ път. Ако и той е неуспешен, запиши "визуална проверка": "неуспешна" и премини в ОГРАНИЧЕН РЕЖИМ:
   - единственият надежден източник е GROUND TRUTH (складово име, скрейпнати данни);
   - оригиналните Наименование/Описание/Атрибути са НЕДОВЕРЕНИ (те са AI-генерираните данни, които поправяме) — НЕ преразказвай съдържание/цветове/брой части от тях;
   - описанието остава кратко и общо (без списък на съдържанието, без цвят/материал), а липсите се отбелязват в "Проблеми". Излъгано "ок" разваля данните на магазина.
7. Свързани: само реални варианти на същия модел (различие само в размер/цвят), избрани от списъка с кандидати. Ако няма — празно.
8. Емпирични стойности (код, цена, количество, мярка) НЕ ти се дават и НЕ ги връщаш —
   те се управляват отделно от системата. Никога не споменавай цена/количество/код в текст.
9. Всеки открит, но нерешен проблем опиши кратко в "Проблеми".

## ФОРМАТ НА ОТГОВОРА (СТРИКТЕН)
Финалният ти отговор е САМО ЕДИН ```json блок — БЕЗ обяснения, разсъждения или резюме
преди или след него. Всякакъв текст извън JSON блока е ГРЕШКА и проваля обработката.
Ключове (САМО тези съдържателни полета): {", ".join(EDITABLE)}, плюс:
- "Проблеми": низ или null
- "Снимка проблеми": низ (напр. "воден знак; цветен фон") или null
- "визуална проверка": "ок" | "неуспешна" | "няма снимка". "неуспешна" е САМО когато
  инструментът върне ГРЕШКА. Успешен анализ, който ПРОТИВОРЕЧИ на текста, е "ок" —
  снимката печели, а противоречието се описва в "Проблеми".
- "confidence": "high" | "medium" | "low"
- "needs_human": true/false — САМО за НЕРЕШЕНИ проблеми: противоречие между източници,
  съмнение за грешна идентификация на продукта, невъзможност да се спази правило.
  Проблем, РЕШЕН по правилата (бранд оставен празен, защото е непотвърден; тегло,
  оценено и отбелязано; описание без непотвърден материал), НЕ изисква needs_human —
  отбелязва се само в "Проблеми".
- "lesson": ЕДНО кратко общовалидно правило за бъдещи продукти (ако научи нещо ново, което го няма в ръководството), иначе null
Започни отговора си директно с ```json
"""
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS] + "\n…[входът е съкратен — работи с наличното]"
    return prompt


def extract_json(text):
    blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except Exception:
            pass
    # fallback: last balanced top-level object
    end = text.rfind("}")
    while end != -1:
        depth = 0
        for start in range(end, -1, -1):
            if text[start] == "}":
                depth += 1
            elif text[start] == "{":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:end + 1])
                    except Exception:
                        break
        end = text.rfind("}", 0, max(end - 1, 0)) if end > 0 else -1
    return None


# Russian-only Cyrillic letters that never appear in literary Bulgarian — a
# reliable contamination signal, safe to transliterate.
_RU_LETTERS = str.maketrans({"э": "е", "Э": "Е", "ы": "и", "Ы": "И",
                             "ё": "е", "Ё": "Е"})


def apply_ru_fix(text):
    """Deterministically replace common Russian-isms with Bulgarian, preserving
    case, and transliterate Russian-only letters. Runs before spellcheck so
    leaked words become clean, not human work."""
    if not text:
        return text, False

    def repl(m):
        w = m.group(0)
        fix = RU_FIX.get(w.lower())
        if not fix:
            return w
        return fix.capitalize() if w[:1].isupper() else fix

    pattern = re.compile(r"[а-яА-ЯёэыЁЭЫ]+")
    out = pattern.sub(repl, str(text)).translate(_RU_LETTERS)
    return out, out != str(text)


def validate(task, parsed):
    """Returns (fields, meta_notes). Raises ValueError when unusable.

    The agent only returns EDITABLE content fields; operational columns
    (Код, Цена, Количество, Мярка, Доставчик, ...) and Снимки/Серия are taken
    verbatim from the source — the AI never touches or even sees them.
    """
    notes = []
    original = task["product"]

    fields = {}
    for col in ENRICHED_COLS:
        if col not in EDITABLE:
            # Driver-owned: code, empiric values, image URL, series — verbatim.
            fields[col] = original.get(col)
            continue
        if col in parsed:
            v = parsed[col]
            if col == "Атрибути":
                # Normalize the filter-feed format regardless of how the model
                # shaped it (dict, newlines, list) -> "Ключ: Стойност; ..."
                if isinstance(v, dict):
                    v = "; ".join(f"{k}: {val}" for k, val in v.items() if val)
                elif isinstance(v, (list, tuple)):
                    v = "; ".join(str(x) for x in v if x)
                elif v:
                    v = re.sub(r"\s*[\r\n]+\s*", "; ", str(v).strip())
            elif isinstance(v, (list, tuple)):  # e.g. Синоними as an array
                v = ", ".join(str(x) for x in v if x is not None)
            elif isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            fields[col] = None if v in ("", "null") else v
        else:
            fields[col] = original.get(col)
            notes.append(f"missing_key:{col}")

    # Deterministic Russian-ism cleanup on all customer-facing text.
    for col in ("Наименование", "Описание", "Мета наименование",
                "Мета описание", "Синоними", "Атрибути"):
        if fields.get(col):
            fixed, changed = apply_ru_fix(fields[col])
            if changed:
                fields[col] = fixed
                notes.append(f"ru_fixed:{col}")

    # AI-filler clichés (research: hurt trust + read as AI). Flag so the polish
    # pass rewrites them; the flag itself is not escalated (advisory).
    desc_low = str(fields.get("Описание") or "").lower()
    for cliche in ("издигнете", "перфектен за всеки", "не търсете повече",
                   "в днешно време", "висококачествен продукт",
                   "elevate your", "look no further", "perfect for any"):
        if cliche in desc_low:
            notes.append(f"ai_cliche:{cliche}")
            break

    name = str(fields.get("Наименование") or "").strip()
    if not name:
        raise ValueError("empty Наименование")

    # Category must stay within the site's category tree.
    cats = known_categories()
    if cats and fields.get("Категории"):
        parts = [p.strip() for p in str(fields["Категории"]).split(";") if p.strip()]
        if any(p not in cats for p in parts):
            fields["Категории"] = original.get("Категории")
            notes.append("reverted_invalid_category")

    # Brand: never a supplier name.
    brand = str(fields.get("Бранд") or "").strip().lower()
    if brand and any(s in brand for s in SUPPLIER_NAMES):
        notes.append(f"dropped_supplier_brand:{fields['Бранд']}")
        fields["Бранд"] = None

    # Weight policy: never emptied — restore the original if the agent removed it.
    if not fields.get("Тегло (кг)") and original.get("Тегло (кг)"):
        fields["Тегло (кг)"] = original.get("Тегло (кг)")
        notes.append("restored_weight")

    # Fabrication firewall for Материал/Цвят: a visual claim needs visual proof.
    # Value is kept only when its stem appears in GROUND TRUTH text (the till +
    # scraped supplier data), or the worker reported a successful image check.
    # The original Enriched fields are NOT a source — they are the AI-generated
    # data under repair (a "black wallet set" survived that way while the photo
    # showed a green camping kit).
    vision_ok = str(parsed.get("визуална проверка") or "").strip().lower() in ("ок", "ok")
    source_text = json.dumps(task.get("ground_truth"), ensure_ascii=False).lower()

    def textually_sourced(value):
        # Trim inflection endings: "Меден" must match GT's "медно покритие".
        words = str(value).strip().lower().split()
        if not words:
            return False
        stem = words[0][:max(3, len(words[0]) - 2)]
        return stem in source_text

    # Attributes: drop placeholder/junk values agents sometimes emit.
    attrs = fields.get("Атрибути")
    if attrs:
        kept = []
        for part in str(attrs).split(";"):
            part = part.strip()
            if not part:
                continue
            key = part.split(":", 1)[0].strip() if ":" in part else ""
            value = part.split(":", 1)[1].strip() if ":" in part else part
            if value.lower() in {"needs_human", "unknown", "null", "none", "n/a", "?", "-", ""}:
                notes.append(f"dropped_attr:{part[:40]}")
                continue
            if key in ("Материал", "Цвят") and not vision_ok and not textually_sourced(value):
                notes.append(f"dropped_unsourced:{part[:40]}")
                continue
            kept.append(part)
        fields["Атрибути"] = "; ".join(kept) if kept else None

    # Related products must come from the candidate list; cap at 10.
    allowed = {c["Код"] for c in task["related_candidates"]}
    rel = fields.get("Свързани")
    if rel:
        kept = []
        for part in re.split(r"[;\n]+", str(rel)):
            part = part.strip()
            if not part:
                continue
            rcode = part.split("|")[0].strip()
            if rcode in allowed:
                kept.append(part)
            else:
                notes.append(f"dropped_related:{rcode}")
        if len(kept) > 10:
            notes.append(f"related_capped:{len(kept)}")
            kept = kept[:10]
        fields["Свързани"] = "; ".join(kept) if kept else None

    # --- Text-quality guardrails -------------------------------------------
    # Name: no leading catalog codes, no banned abbreviations, length cap.
    name = str(fields.get("Наименование") or "")
    stripped = re.sub(r"^\s*[\d/.\-]{4,}\s+", "", name).strip()
    if stripped and stripped != name:
        fields["Наименование"] = stripped
        notes.append("stripped_catalog_prefix")
        name = stripped
    if re.search(r"\bК-т\b|\d+\s?бр\b|\bс/у\b", name, re.IGNORECASE):
        notes.append("name_abbreviation")
    if len(name) > 80:
        # Trim at a word/comma boundary rather than escalate — an over-long
        # name has a safe deterministic fix.
        cut = name[:80]
        boundary = cut.rfind(",") if "," in cut[60:] else cut.rfind(" ")
        fields["Наименование"] = (cut[:boundary] if boundary > 40 else cut).strip().rstrip(",")
        notes.append("name_trimmed")

    # Description: structure, meta-commentary, Russian-isms, duplication risk.
    desc = str(fields.get("Описание") or "")
    if desc:
        cleaned = re.sub(
            r"\s*\((?:визуално потвърдено|ако материалът позволява|приблизително|"
            r"не може да се потвърди[^)]*)\)", "", desc, flags=re.IGNORECASE)
        if cleaned != desc:
            fields["Описание"] = cleaned
            notes.append("stripped_meta_comments")
            desc = cleaned
        # Placeholder bullets ("Материал: Непотвърден") are internal state, not
        # customer copy — drop the whole <li>.
        cleaned = re.sub(
            r"<li>(?:<strong>)?[^<:]{2,30}:?(?:</strong>)?\s*"
            r"(?:Непотвърден[оа]?|Неизвестен[оа]?|Липсва|N/?A|unknown|няма данни)\.?\s*</li>",
            "", desc, flags=re.IGNORECASE)
        if cleaned != desc:
            fields["Описание"] = cleaned
            notes.append("stripped_placeholder_bullets")
            desc = cleaned
        if "<h2>" not in desc or "<ul>" not in desc:
            notes.append("desc_structure")
        for bad_word in ("подходит", "применение", "изготовлен", "прочный", "ёмкость"):
            if bad_word in desc.lower():
                notes.append(f"russianism:{bad_word}")
        words = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", desc)))
        if not 50 <= words <= 450:
            notes.append(f"desc_length:{words}w")

    # Homoglyph repair: models sometimes emit Latin lookalikes inside Cyrillic
    # words ("тоaлетна" with Latin 'a') — invisible to readers, poison for site
    # search. Any mostly-Cyrillic word gets its Latin lookalikes transliterated.
    _HOMO = str.maketrans("aeopcxyABEKMHOPCTX", "аеорсхуАВЕКМНОРСТХ")

    def _fix_homoglyphs(text):
        def fix_word(m):
            w = m.group(0)
            cyr = sum(1 for ch in w if "а" <= ch.lower() <= "я")
            lat = sum(1 for ch in w if ch in "aeopcxyABEKMHOPCTX")
            return w.translate(_HOMO) if cyr > lat else w
        return re.sub(r"\w*[а-яА-Я]\w*", fix_word, str(text))

    for k in ("Наименование", "Описание", "Мета наименование", "Мета описание",
              "Синоними", "Атрибути"):
        v = fields.get(k)
        if v:
            fixed = _fix_homoglyphs(v)
            if fixed != v:
                fields[k] = fixed
                notes.append(f"homoglyphs_fixed:{k}")

    # Deterministic Bulgarian spellcheck (phunspell/bg_BG) over customer-facing
    # text — catches garbled words both models and the reviewer keep missing.
    try:
        from bg_spell import unknown_words
        text_blob = " ".join(str(fields.get(k) or "") for k in
                             ("Наименование", "Описание", "Мета наименование",
                              "Мета описание", "Атрибути"))
        known_blob = json.dumps(task.get("ground_truth"), ensure_ascii=False) + \
            " " + json.dumps(task.get("filter_schema", []), ensure_ascii=False)
        bad_words = unknown_words(text_blob, limit=8, known_text=known_blob)
        if bad_words:
            notes.append("misspelled:" + ",".join(bad_words))
    except Exception as spell_err:
        notes.append(f"spellcheck_unavailable:{str(spell_err)[:60]}")

    # Meta lengths (soft flags; the human queue picks up chronic offenders).
    if len(str(fields.get("Мета наименование") or "")) > 70:
        notes.append("meta_title_long")
    md_len = len(str(fields.get("Мета описание") or ""))
    if fields.get("Мета описание") and not 100 <= md_len <= 220:
        notes.append(f"meta_desc_len:{md_len}")

    # Synonyms: dedupe, cap at 15 terms.
    syn = fields.get("Синоними")
    if syn:
        seen_terms, terms = set(), []
        for term in str(syn).split(","):
            term = term.strip()
            if term and term.casefold() not in seen_terms:
                seen_terms.add(term.casefold())
                terms.append(term)
        if len(terms) > 15:
            notes.append(f"synonyms_capped:{len(terms)}")
            terms = terms[:15]
        fields["Синоними"] = ", ".join(terms)

    # Weight: numeric and physically plausible for household goods.
    w = fields.get("Тегло (кг)")
    if w is not None:
        try:
            wf = float(str(w).replace(",", "."))
            if not 0.005 <= wf <= 100:
                fields["Тегло (кг)"] = original.get("Тегло (кг)")
                notes.append(f"weight_implausible:{w}")
        except (TypeError, ValueError):
            fields["Тегло (кг)"] = original.get("Тегло (кг)")
            notes.append(f"weight_not_numeric:{w}")
    return fields, notes


def maybe_add_lesson(lesson):
    if NO_LESSONS:
        return False
    if not lesson or not isinstance(lesson, str):
        return False
    lesson = " ".join(lesson.split())
    # Agents sometimes echo the guide's bullet format — strip any "- [date]" prefixes.
    lesson = re.sub(r"^(?:-\s*)?(?:\[\d{4}-\d{2}-\d{2}\]\s*)+", "", lesson).strip()
    if not (10 <= len(lesson) <= 300):
        return False
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    existing = [ln for ln in guide.splitlines() if ln.strip().startswith("- ")]
    if len(existing) >= 80:  # keep the guide bounded
        return False
    new_words = set(re.findall(r"\w+", lesson.lower()))
    for line in existing:
        words = set(re.findall(r"\w+", line.lower()))
        if new_words and len(new_words & words) / len(new_words) > 0.7:
            return False  # near-duplicate of an existing rule
    with open(GUIDE_PATH, "a", encoding="utf-8") as f:
        f.write(f"- [{date.today().isoformat()}] {lesson}\n")
    return True


DIRECT_PROVIDERS = {
    "dashscope-intl": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                       "DASHSCOPE_INTL_API_KEY"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "zai": ("https://api.z.ai/api/paas/v4", "GLM_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "cohere": ("https://api.cohere.com/compatibility/v1", "COHERE_API_KEY"),
}
WORKER_SYS = (
    "Ти си прецизен редактор на продуктови текстове за български онлайн магазин "
    "за домашни стоки. Пишеш ИЗКЛЮЧИТЕЛНО на чист, естествен български език. "
    "АБСОЛЮТНО ЗАБРАНЕНИ са руски и сръбски думи и буквите ы, э, ё. Без английски "
    "или китайски думи в текста. Спазвай точно исканата JSON структура и започни "
    "отговора си директно с ```json."
)


_DIRECT_CLIENTS = {}


def _direct_key(key_env):
    # Prefer the hermes .env (the managed store where keys are rotated) over a
    # possibly-stale process/User env var — a User env var is cached by the running
    # gateway and inherited by every cron child, so without this a rotated key would
    # not take effect until the whole gateway restarts (2026-07-15: exactly this bit
    # us when swapping the arrears Alibaba key for a fresh one).
    env_path = Path(HERMES_HOME) / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key_env + "="):
                v = line.split("=", 1)[1].strip()
                if v:
                    return v
    return os.environ.get(key_env, "")


def _direct_client(base_url, key_env):
    if base_url not in _DIRECT_CLIENTS:
        from openai import OpenAI
        _DIRECT_CLIENTS[base_url] = OpenAI(api_key=_direct_key(key_env),
                                           base_url=base_url, timeout=AGENT_TIMEOUT_S,
                                           max_retries=0)  # chain handles failover
    return _DIRECT_CLIENTS[base_url]


REVIEW_SYS = (
    "Ти си строг независим контролен редактор за български онлайн магазин. "
    "Проверяваш чужда продуктова обработка спрямо подадените достоверни данни и "
    "връщаш САМО искания JSON. Пиши на чист български без руски/сръбски думи."
)


def run_direct(prompt, model_args, system=WORKER_SYS, max_tokens=2000):
    """Direct OpenAI-compatible chat completion. Returns (rc, out, err) so the
    call site stays identical. Raises on provider error (caller
    fails over)."""
    model = model_args[model_args.index("-m") + 1]
    provider = model_args[model_args.index("--provider") + 1]
    if model in NEVER_CALL:
        raise RuntimeError(f"blocked no-free-quota model (NEVER_CALL): {model}")
    base_url, key_env = DIRECT_PROVIDERS[provider]
    client = _direct_client(base_url, key_env)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=max_tokens)
    try:
        import telemetry as _tel
        _tel.record(model, provider, resp)
    except Exception:
        pass
    return 0, (resp.choices[0].message.content or ""), ""


def run_worker_direct(prompt, model_args):
    return run_direct(prompt, model_args, system=WORKER_SYS)


def generate_with_chain(prompt, system=WORKER_SYS):
    """Generate through WORKER_CHAIN, skipping models already in exhausted_models.txt
    and marking any that newly report free-quota exhaustion. Returns (text, model)
    or raises if every model fails. Used by the worker AND the auxiliary passes
    (vision-correction, polish) so none of them dies on a single drained quota."""
    exhausted = _load_exhausted()
    chain = [a for a in WORKER_CHAIN if a[a.index("-m") + 1] not in exhausted]
    if not _dashscope_allowed():                       # billing incident: dashscope opt-in only
        chain = [a for a in chain if a[a.index("--provider") + 1] != "dashscope-intl"]
    chain = chain or [a for a in WORKER_CHAIN if a[a.index("--provider") + 1] != "dashscope-intl"]
    last = None
    for args in chain:
        model = args[args.index("-m") + 1]
        try:
            _, out, _ = run_direct(prompt, args, system=system)
            return out, model
        except Exception as e:
            last = e
            maybe_pause_on_arrearage(e)
            if _is_quota_error(e) and model not in exhausted:
                _mark_exhausted(model)
                exhausted.add(model)
    raise last if last else RuntimeError("no model available")


# Reviewer failover chain — direct API, spread across providers so no single
# free-tier rate limit (zai saturates fast under 6 lanes) stalls the queue.
# Llama on Groq leads: fast (~2s), different family from the Qwen worker, and a
# quota independent of the worker's Alibaba and the vision pass's NVIDIA.
REVIEW_CHAIN = [
    # OPTION B (owner 2026-07-20): reliable dashscope models FIRST (different from the worker's
    # primary qwen3.6-flash, so it's still an independent second opinion) — the old groq/zai/nvidia-
    # only chain was "unavailable" ~366 times, killing the second leaf vote. Flaky tail kept as backup.
    ["-m", "qwen-max", "--provider", "dashscope-intl"],
    ["-m", "qwen3-235b-a22b-instruct-2507", "--provider", "dashscope-intl"],
    ["-m", "qwen-plus", "--provider", "dashscope-intl"],
    ["-m", "llama-3.3-70b-versatile", "--provider", "groq"],
    ["-m", "glm-4.7-flash", "--provider", "zai"],
    ["-m", "meta/llama-3.3-70b-instruct", "--provider", "nvidia"],
]


def run_reviewer_direct(prompt):
    ex = _load_exhausted()
    last = None
    for args in REVIEW_CHAIN:
        model = args[args.index("-m") + 1]
        if model in ex:                            # skip quota-drained models (don't 403-walk them)
            continue
        try:
            return run_direct(prompt, args, system=REVIEW_SYS)
        except Exception as e:
            last = e
            if _is_quota_error(e):
                _mark_exhausted(model)
    raise last if last else RuntimeError("no reviewer available")


_NV_CLIENT = None


def _nvidia_client():
    global _NV_CLIENT
    if _NV_CLIENT is None:
        from openai import OpenAI
        key = os.environ.get("NVIDIA_API_KEY", "")
        if not key:
            env_path = HERMES_HOME + r"\.env"
            for line in Path(env_path).read_text(encoding="utf-8").splitlines():
                if line.startswith("NVIDIA_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
        _NV_CLIENT = OpenAI(api_key=key,
                            base_url="https://integrate.api.nvidia.com/v1",
                            timeout=120)
    return _NV_CLIENT


VISION_CALL_TIMEOUT = 30          # seconds per VL provider — a hung provider fails fast, next is tried

def vision_complete(content, max_tokens=300):
    """Run a vision chat over VL_CHAIN, returning the text of the first provider that answers.
    Raises only if every VL provider fails. Used by the record-vs-photo verify and the classifier
    vision tie-break so both survive a vision outage. Each provider call is capped at
    VISION_CALL_TIMEOUT so a hung endpoint can never freeze a lane. Quota-exhausted models are
    skipped via the shared EXHAUSTED_PATH guard and newly-drained ones are marked on a 403 — so a
    dead vision model is walked at most once, not on every product (was a ~60s/product leak)."""
    ex = _load_exhausted()
    last = None
    for prov, model in VL_CHAIN:
        if model in NEVER_CALL or model in ex:
            continue
        try:
            if prov == "nvidia":
                client = _nvidia_client()
            else:
                base_url, key_env = DIRECT_PROVIDERS[prov]
                client = _direct_client(base_url, key_env)
            r = client.chat.completions.create(
                model=model, temperature=0, max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
                timeout=VISION_CALL_TIMEOUT)          # hard per-provider cap — never freeze a lane
            txt = r.choices[0].message.content or ""
            if txt.strip():
                return txt
        except Exception as e:
            last = e
            if _is_quota_error(e):                 # 403 free-quota drained -> skip it from now on
                _mark_exhausted(model)
    if last:
        raise last
    return ""


def _encode_url(u):
    """Percent-encode a URL's path/query so raw supplier filenames with spaces or
    parens (e.g. mira-n '140 - Copy.jpg', '187 (2).png') fetch correctly instead
    of raising 'control characters' or 404. Idempotent (safe='%' keeps existing %)."""
    try:
        p = urllib.parse.urlsplit(u)
        path = urllib.parse.quote(p.path, safe="/%")
        query = urllib.parse.quote(p.query, safe="=&%")
        return urllib.parse.urlunsplit((p.scheme, p.netloc, path, query, p.fragment))
    except Exception:
        return u


def _origin(u):
    try:
        p = urllib.parse.urlsplit(u)
        return f"{p.scheme}://{p.netloc}/"
    except Exception:
        return u


def verify_against_image(image_url, fields):
    """Independent vision check of the FINAL product against its photo.

    Returns dict {ok, mismatch, corrections} or None if vision unavailable.
    Only judges what a photo can show: product type, shape, material, colour,
    piece count. A stronger model (llama-90b-vision) than the worker's inline
    check, with the single job of catching contradictions like a round name on
    a rectangular pan.
    """
    if not image_url:
        return None
    try:
        req = urllib.request.Request(_encode_url(image_url), headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120 Safari/537.36"),
            "Referer": _origin(image_url)})
        data = urllib.request.urlopen(req, timeout=30).read()
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        return {"ok": None, "error": f"image_load:{str(e)[:80]}"}

    prompt = (
        "Ти си контрол на качеството за онлайн магазин. Разгледай ВНИМАТЕЛНО само СНИМКАТА.\n"
        "1) Опиши накратко какво виждаш.\n"
        "2) Прецени ЦЯЛОСТНО дали снимката наистина показва ЗАЯВЕНИЯ продукт: правилен ли е видът, "
        "основният цвят, формата и броят; не показва ли снимката ДРУГ продукт, само ЧАСТ от него, "
        "или е повредена/грешно обработена.\n"
        "Отговори САМО с JSON:\n"
        '{"виждам": "<вид, основен цвят, материал, форма, брой — накратко>", '
        '"съответства": true/false, '
        '"разминаване": "<конкретното разминаване с една фраза, или null>", '
        '"тежест": "критично/дребно/няма", '
        '"сигурност": "висока/средна/ниска", '
        '"видим_бранд": "<марка ВЪРХУ продукта/опаковката, или null>"}\n\n'
        "ПРАВИЛА за тежест:\n"
        "• критично — ЯСНО и значимо несъответствие, което клиент би забелязал: друг продукт, "
        "грешен ОСНОВЕН цвят, грешна форма, грешен брой, ИЛИ снимката не показва продукта / показва "
        "само ЧАСТ от него / е повредена или грешно обработена (напр. остатък след изрязване).\n"
        "• дребно — малка или несигурна разлика (нюанс, осветление, опаковка, ъгъл).\n"
        "• няма — съответства.\n"
        "За СТЪКЛО/прозрачни продукти: отраженията и прозрачността правят цвят/материал трудни за "
        "преценка — тази НЕЯСНОТА сама по себе си НЕ е критично (все пак провери продукта). НО ако "
        "виждаш ДРУГ продукт, грешна форма, само ЧАСТ или повредена снимка — това Е критично, "
        "независимо от материала.\n"
        "сигурност=висока САМО ако снимката е ясна и наистина си сигурен.\n"
        "видим_бранд: чети САМО марка отпечатана върху продукта/опаковката; ИГНОРИРАЙ водни знаци и "
        "лога на магазини/доставчици (напр. EGERA, LEVENA, MIRA-N). Не отгатвай; ако не си сигурен → null.\n\n"
        f"ЗАЯВЕНО:\nИме: {fields.get('Наименование')}\n"
        f"Атрибути: {fields.get('Атрибути')}\n"
        f"Описание (само за проверка): {str(fields.get('Описание'))[:400]}"
    )
    try:
        out = vision_complete([
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}],
            max_tokens=300)
        m = re.search(r"\{.*\}", out, re.DOTALL)
        v = json.loads(m.group(0)) if m else None
        if not v:
            return {"ok": None, "error": "no_json"}
        # HOLISTIC verdict, not a per-property checklist. The model describes the photo, then
        # judges the WHOLE product against the record and grades severity. A CONFIDENT, CRITICAL
        # discrepancy — wrong product, wrong main colour/shape/count, OR a broken/partial/wrong
        # image (e.g. the cake stand that image-processing cropped down to just the cake) — FORCES
        # review. A minor or uncertain difference is a soft note (image_unverified), never a flag.
        # The prompt tells the model to IGNORE transparent-glass / reflection ambiguity, which is
        # what killed the false positives the old per-property material/colour checks produced
        # (owner 2026-07-20). This catches EVERYTHING real (wrong colour, wrong product, bad image),
        # not just geometry, while staying quiet on ambiguous materials.
        sev = str(v.get("тежест") or "").strip().lower()
        conf = str(v.get("сигурност") or "").strip().lower()
        disc = v.get("разминаване") or v.get("забележка")
        if sev == "критично" and conf == "висока":
            return {"ok": False, "mismatch": [str(disc)[:60] if disc else "критично"],
                    "soft": [], "detail": v, "confidence": conf}
        if sev in ("критично", "дребно") or (v.get("съответства") is False):
            # a real-but-unconfident or minor discrepancy: record it, don't pull into the queue
            return {"ok": None, "mismatch": [], "soft": [str(disc)[:60]] if disc else [],
                    "detail": v, "confidence": conf}
        return {"ok": True, "mismatch": [], "soft": [], "detail": v, "confidence": conf}
    except Exception as e:
        return {"ok": None, "error": f"vision_call:{str(e)[:80]}"}


_CONF_RANK = {"high": 2, "medium": 1, "low": 0}
_PRIOR_Q = None


def _prior_quality_index():
    """{code: {conf, brand, fallback, nfields}} for the CURRENT canonical of every
    finished product. Built lazily once per process (a few MB parse), cached — so it
    costs nothing until the first write and nothing on first-time products. Used by the
    write quality-gate to stop a re-processing run from DOWNGRADING a good product."""
    global _PRIOR_Q
    if _PRIOR_Q is not None:
        return _PRIOR_Q
    try:                                               # DB-first: lean columns, no re-parse
        import db as _db
        if _db.is_ready():
            _PRIOR_Q = _db.pipeline_index()
            return _PRIOR_Q
    except Exception:
        pass
    import glob as _glob
    idx, recs = {}, []
    for fp in [str(HERE / "results.jsonl")] + _glob.glob(str(HERE / "results.lane*.jsonl")):
        try:
            for line in open(fp, encoding="utf-8", errors="replace"):
                if line.strip():
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
    for d in sorted(recs, key=lambda d: d.get("ts") or ""):        # last (latest ts) wins
        c = str(d.get("code") or "")
        if not c:
            continue
        f = d.get("fields") or {}
        notes = (d.get("meta") or {}).get("notes") or []
        idx[c] = {"conf": _CONF_RANK.get(str(d.get("confidence")), 1),
                  "brand": str(f.get("Бранд") or "").strip(),
                  "fallback": any("agent_fallback_direct" in str(n) for n in notes),
                  "nfields": sum(1 for v in f.values() if str(v or "").strip()),
                  "v2": any(str(n) == "v2" for n in notes)}
    _PRIOR_Q = idx
    return idx


def _quality_gate(code, record):
    """Monotonic-reprocessing guard. Returns (record_or_None, note):
      * None  -> skip the write; the existing canonical is BETTER (block a downgrade),
      * record -> write it (possibly with a lost brand merged back from the prior version).
    Only fires when a prior canonical exists (re-processing); first-time products pass
    straight through. Targets the exact regression we found: a fallback-direct re-run
    that dropped a verified brand / lost fields / lowered confidence."""
    prior = _prior_quality_index().get(str(code))
    if not prior:
        return record, None
    # v2 rebuild over a v1 canonical ALWAYS writes: v1's self-reported confidence is
    # not comparable to v2's stricter scale, and blocking here would loop the product
    # forever (a blocked v2 write never enters done_codes_v2, so every lane re-runs
    # it). The gate only compares like with like (v2 rerun vs v2 canonical).
    _rec_v2 = any(str(n) == "v2" for n in
                  ((record.get("meta") or {}).get("notes") or []))
    if _rec_v2 and not prior.get("v2"):
        return record, None
    f = record.get("fields") or {}
    notes = (record.get("meta") or {}).setdefault("notes", []) if record.get("meta") else []
    if not isinstance(record.get("meta"), dict):
        record["meta"] = {"notes": notes}
    new_conf = _CONF_RANK.get(str(record.get("confidence")), 1)
    new_fallback = any("agent_fallback_direct" in str(n) for n in notes)
    new_brand = str(f.get("Бранд") or "").strip()
    new_nfields = sum(1 for v in f.values() if str(v or "").strip())
    if new_conf < prior["conf"]:
        return None, f"quality_gate_block:lower_conf({prior['conf']}->{new_conf})"
    if new_fallback and not prior["fallback"] and new_nfields < prior["nfields"]:
        return None, "quality_gate_block:fallback_over_agentic"
    if prior["brand"] and not new_brand:                           # reprocess lost the brand
        f["Бранд"] = prior["brand"]
        record["fields"] = f
        notes.append(f"quality_gate_restore_brand:{prior['brand']}")
    return record, None


_HOMO_TR = str.maketrans("aeopcxyABEKMHOPCTX", "аеорсхуАВЕКМНОРСТХ")


def agentic_guards(fields, task, notes, code):
    """Legacy validate() guardrails restored for the agentic path (they were silently
    lost in the rewrite): homoglyph repair, supplier-brand rejection, weight restore +
    plausibility, name cap, attribute-placeholder drop, synonym/related caps, and the
    duplicate-description check. Deterministic, in-place on `fields`."""
    original = task.get("product") or {}

    # Homoglyph repair: Latin lookalikes inside Cyrillic words poison site search.
    def _fix_homoglyphs(text):
        def fix_word(m):
            w = m.group(0)
            cyr = sum(1 for ch in w if "а" <= ch.lower() <= "я")
            lat = sum(1 for ch in w if ch in "aeopcxyABEKMHOPCTX")
            return w.translate(_HOMO_TR) if cyr > lat else w
        return re.sub(r"\w*[а-яА-Я]\w*", fix_word, str(text))

    for k in ("Наименование", "Описание", "Мета наименование", "Мета описание",
              "Синоними", "Атрибути"):
        v = fields.get(k)
        if v:
            fixed = _fix_homoglyphs(v)
            if fixed != v:
                fields[k] = fixed
                notes.append(f"homoglyphs_fixed:{k}")

    # Brand: never a supplier name.
    brand = str(fields.get("Бранд") or "").strip().lower()
    if brand and any(s in brand for s in SUPPLIER_NAMES):
        notes.append(f"dropped_supplier_brand:{fields['Бранд']}")
        fields["Бранд"] = None

    # Weight: never emptied; numeric and physically plausible for household goods.
    if not fields.get("Тегло (кг)") and original.get("Тегло (кг)"):
        fields["Тегло (кг)"] = original.get("Тегло (кг)")
        notes.append("restored_weight")
    w = fields.get("Тегло (кг)")
    if w is not None:
        try:
            wf = float(str(w).replace(",", "."))
            if not 0.005 <= wf <= 100:
                fields["Тегло (кг)"] = original.get("Тегло (кг)")
                notes.append(f"weight_implausible:{w}")
        except (TypeError, ValueError):
            fields["Тегло (кг)"] = original.get("Тегло (кг)")
            notes.append(f"weight_not_numeric:{w}")

    # Name: strip a leading catalog-digit prefix; cap at 80 chars on a boundary.
    name = str(fields.get("Наименование") or "")
    stripped = re.sub(r"^\s*[\d/.\-]{4,}\s+", "", name).strip()
    if stripped and stripped != name:
        fields["Наименование"] = stripped
        notes.append("stripped_catalog_prefix")
        name = stripped
    if len(name) > 80:
        cut = name[:80]
        boundary = cut.rfind(",") if "," in cut[60:] else cut.rfind(" ")
        fields["Наименование"] = (cut[:boundary] if boundary > 40 else cut).strip().rstrip(",")
        notes.append("name_trimmed")

    # Attributes: drop placeholder values the model uses to dodge a group.
    attrs = fields.get("Атрибути")
    if attrs:
        kept = []
        for part in str(attrs).split(";"):
            part = part.strip()
            if not part:
                continue
            value = part.split(":", 1)[1].strip() if ":" in part else part
            if value.lower() in {"needs_human", "unknown", "null", "none", "n/a", "?", "-", ""}:
                notes.append(f"dropped_attr:{part[:40]}")
                continue
            kept.append(part)
        fields["Атрибути"] = "; ".join(kept) if kept else None

    # Synonyms/related caps (site fields, not essays).
    syn = [t.strip() for t in str(fields.get("Синоними") or "").split(",") if t.strip()]
    if len(syn) > 15:
        notes.append(f"synonyms_capped:{len(syn)}")
        fields["Синоними"] = ", ".join(syn[:15])

    # Duplicate-description check: a description reused across DIFFERENT products is a
    # real store-quality problem (and an SEO one).
    desc_norm = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(fields.get("Описание") or ""))).strip().lower()
    desc_hash = hashlib.md5(desc_norm.encode()).hexdigest() if len(desc_norm) > 80 else None
    if desc_hash and DESC_HASHES_PATH.exists():
        for line in DESC_HASHES_PATH.read_text(encoding="utf-8").splitlines():
            h, _, other = line.partition(" ")
            if h == desc_hash and other.strip() != str(code):
                notes.append(f"duplicate_desc_of:{other.strip()[:20]}")
                break
    return desc_hash


def process_next(target_code=None):
    """Returns 'done', 'empty', or 'failed'. v2-only dispatch — the v1 agentic and inline
    direct-worker paths were excised 2026-07-19 (dead since the V2_MODE switch; run_one
    remains the shared harness the v2 pipeline reuses)."""
    # Reversible pause switch: while (HERE/'PAUSE') exists, lanes do nothing.
    if (HERE / "PAUSE").exists():
        log("PAUSED (PAUSE sentinel present) — skipping this batch.")
        return "empty"
    task, prior_failures = next_task(target_code)
    if task is None:
        log("Queue empty — nothing pending.")
        return "empty"
    import pipeline_v2
    return pipeline_v2.process(task, prior_failures)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    status = process_next(target)
    sys.exit({"done": 0, "empty": 2, "failed": 1}[status])
