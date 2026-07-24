# -*- coding: utf-8 -*-
"""Thin dispatcher for the assistant's LLM. It does NOT reinvent model routing — it tries a
configured list of EXISTING backends in order, and the first that answers wins. So a backend
erroring or out of quota simply falls through to the next.

Backends (each delegates to infra that already exists):
  claude_cli  Claude Code CLI headless (`claude -p`, tools DISABLED) — runs on your Claude
              SUBSCRIPTION (no per-token API cost). Needs a one-time `claude login`; until then
              it errors and the dispatcher falls through.
  hermes      `hermes -z … --cli` — the existing ~20-model free-first chain (config in
              ~/.hermes/config.yaml). This is the "swappable model layer" already on the box.
  free        run_one.generate_with_chain — the enrichment pipeline's free-quota model chain.
  anthropic   Anthropic Messages API (PAID). Optional; only if ANTHROPIC_API_KEY is set.

Config (env / ~/.hermes/.env), all optional:
  ASSISTANT_BACKENDS   comma list = try order. Default "free" (works today, zero setup, no spend).
                       e.g. "claude_cli,hermes,free" = subscription Claude → free chain → pipeline.
  CLAUDE_CLI_PATH      override the auto-detected claude.exe.
  ASSISTANT_CLAUDE_MODEL  model for the claude_cli backend (optional).
  HERMES_EXE           override the uv-tools hermes.exe.
  ANTHROPIC_API_KEY / ASSISTANT_ANTHROPIC_MODEL  enable + configure the paid API backend.

Swapping models = editing config (or ~/.hermes/config.yaml for the Hermes chain). No code change."""
import glob
import json
import os
import subprocess
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

_HOME = Path.home()
_ENV_FILE = _HOME / ".hermes" / ".env"


@lru_cache(maxsize=1)
def _env_file_vals():
    vals = {}
    if _ENV_FILE.exists():
        for ln in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _env_file_vals().get(key, default)


@lru_cache(maxsize=1)
def _find_claude() -> str:
    """Locate the Claude Code CLI (bundled with the Desktop app), newest version."""
    override = _cfg("CLAUDE_CLI_PATH")
    if override and Path(override).exists():
        return override
    cands = glob.glob(str(_HOME / "AppData/Roaming/Claude/claude-code/*/claude.exe"))
    cands += glob.glob(str(_HOME / ".local/bin/claude"))
    cands.sort()
    return cands[-1] if cands else ""


# --- backends: each returns (text, model_label) or raises -----------------------------
def _claude_cli(system: str, prompt: str) -> tuple[str, str]:
    exe = _find_claude()
    if not exe:
        raise RuntimeError("claude CLI не е намерен")
    args = [exe, "-p", prompt, "--allowedTools", "",  # tools OFF: pure text, safe for the panel
            "--append-system-prompt", system, "--output-format", "text"]
    model = _cfg("ASSISTANT_CLAUDE_MODEL")
    if model:
        args += ["--model", model]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out or "/login" in out.lower():
        raise RuntimeError(f"claude cli: {((p.stderr or out) or 'no output')[:120]}")
    return out, f"claude-code · абонамент ({model or 'default'})"


def _hermes(system: str, prompt: str) -> tuple[str, str]:
    exe = _cfg("HERMES_EXE") or str(_HOME / "AppData/Roaming/uv/tools/hermes-agent/Scripts/hermes.exe")
    if not Path(exe).exists():
        raise RuntimeError("hermes.exe не е намерен")
    env = {**os.environ, "HERMES_HOME": str(_HOME / ".hermes"), "PYTHONIOENCODING": "utf-8"}
    args = [exe, "-z", f"{system}\n\n{prompt}", "--cli"]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=150, env=env)
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out:
        raise RuntimeError(f"hermes: {((p.stderr or out) or 'no output')[:120]}")
    return out, "hermes · безплатна верига"


def _free(system: str, prompt: str) -> tuple[str, str]:
    import run_one  # the pipeline's FREE-QUOTA guarded multi-model chain
    return run_one.generate_with_chain(prompt, system=system)


def _anthropic(system: str, prompt: str) -> tuple[str, str]:
    key = _cfg("ANTHROPIC_API_KEY") or _cfg("ASSISTANT_ANTHROPIC_KEY")
    if not key:
        raise RuntimeError("anthropic backend не е конфигуриран (няма ANTHROPIC_API_KEY)")
    model = _cfg("ASSISTANT_ANTHROPIC_MODEL", "claude-haiku-4-5")
    body = {"model": model, "max_tokens": 800, "system": system,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"anthropic HTTP {e.code}: {e.read().decode('utf-8','replace')[:120]}")
    text = "".join(b.get("text", "") for b in (out.get("content") or []) if b.get("type") == "text")
    if not text.strip():
        raise RuntimeError("anthropic върна празен отговор")
    return text, f"anthropic API · {model}"


_BACKENDS = {"claude_cli": _claude_cli, "hermes": _hermes, "free": _free, "anthropic": _anthropic}


def _order() -> list[str]:
    return [b.strip() for b in _cfg("ASSISTANT_BACKENDS", "free").split(",") if b.strip()]


def answer(system: str, prompt: str) -> tuple[str, str]:
    """Try configured backends in order; first non-empty answer wins. Raises only if every
    backend fails. Blocking (subprocess / HTTP) — call from a threadpool in async routes."""
    errors = []
    for name in _order():
        fn = _BACKENDS.get(name)
        if not fn:
            continue
        try:
            text, model = fn(system, prompt)
            if text and text.strip():
                return text, model
        except Exception as e:  # noqa: BLE001 — any backend failure → try the next
            errors.append(f"{name}: {str(e)[:100]}")
    raise RuntimeError("всички модели са недостъпни — " + " | ".join(errors))


def configured_backends() -> list[dict]:
    """For the panel to show which backends are active + ready."""
    out = []
    for name in _order():
        if name == "claude_cli":
            out.append({"name": "claude_cli", "label": "Claude (абонамент)", "ready": bool(_find_claude())})
        elif name == "hermes":
            exe = _cfg("HERMES_EXE") or str(_HOME / "AppData/Roaming/uv/tools/hermes-agent/Scripts/hermes.exe")
            out.append({"name": "hermes", "label": "Hermes безплатна верига", "ready": Path(exe).exists()})
        elif name == "free":
            out.append({"name": "free", "label": "Безплатна верига (pipeline)", "ready": True})
        elif name == "anthropic":
            out.append({"name": "anthropic", "label": "Anthropic API (платено)",
                        "ready": bool(_cfg("ANTHROPIC_API_KEY") or _cfg("ASSISTANT_ANTHROPIC_KEY"))})
    return out
