# -*- coding: utf-8 -*-
"""In-memory rate limiting for the auth endpoints — brute-force / credential-stuffing defence.

Two layers:
  • per-(ip, username) FAILED-login lockout — the targeted brute-force defence; a correct login
    resets the counter, so a legit user fat-fingering their password isn't punished long.
  • per-ip request cap on the auth paths — coarse spray/DoS mitigation (also covers the mounted
    FastAPI-Users /login, which we can't hook per-attempt).

Single-process, in-memory (thread-safe). Move to Redis if the panel ever runs multi-instance."""
import threading
import time

from fastapi import HTTPException, Request, status

from .config import get_settings

# thresholds (seconds)
FAIL_LIMIT, FAIL_WINDOW = 5, 300      # 5 failed logins per (ip,user) / 5 min → locked
REQ_LIMIT, REQ_WINDOW = 40, 300       # 40 auth requests / 5 min / ip → throttled


class SlidingWindow:
    """Keeps recent hit timestamps per key; prunes on access so memory tracks only live keys."""
    def __init__(self):
        self._hits: dict[object, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key, window, now):
        arr = [t for t in self._hits.get(key, ()) if now - t < window]
        if arr:
            self._hits[key] = arr
        else:
            self._hits.pop(key, None)
        return arr

    def over_limit(self, key, limit, window):
        """(over_limit, retry_after_seconds) — does NOT record; call record() separately."""
        now = time.time()
        with self._lock:
            arr = self._prune(key, window, now)
            if len(arr) >= limit:
                return True, int(window - (now - arr[0])) + 1
            return False, 0

    def record(self, key):
        now = time.time()
        with self._lock:
            self._hits.setdefault(key, []).append(now)

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


_fails = SlidingWindow()   # per (ip, username) failed logins
_reqs = SlidingWindow()    # per ip requests to auth endpoints


def client_ip(request: Request) -> str:
    """The key both rate-limit layers hash on. X-Forwarded-For is fully client-controlled, so
    trusting it blindly lets an attacker rotate the header and dodge every limit. We honour it
    ONLY when trust_proxy is set (panel really is behind a proxy we control), and then take the
    LAST hop — the one our own proxy appended — not the attacker-supplied leftmost entries.
    Otherwise we use the real peer address, which cannot be spoofed over TCP."""
    if get_settings().trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            if hops:
                return hops[-1]
    return request.client.host if request.client else "unknown"


def _429(retry: int):
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                         "Твърде много опити. Опитай пак по-късно.",
                         headers={"Retry-After": str(max(retry, 1))})


def guard_login(request: Request, username: str) -> None:
    """Call BEFORE verifying credentials. Raises 429 if this (ip,user) is locked out."""
    key = (client_ip(request), (username or "").strip().lower())
    over, retry = _fails.over_limit(key, FAIL_LIMIT, FAIL_WINDOW)
    if over:
        raise _429(retry)


def record_login_failure(request: Request, username: str) -> None:
    _fails.record((client_ip(request), (username or "").strip().lower()))


def reset_login(request: Request, username: str) -> None:
    _fails.reset((client_ip(request), (username or "").strip().lower()))


def guard_auth_request(request: Request) -> None:
    """Per-ip cap on auth endpoints (records the hit). Raises 429 when exceeded."""
    ip = client_ip(request)
    over, retry = _reqs.over_limit(ip, REQ_LIMIT, REQ_WINDOW)
    if over:
        raise _429(retry)
    _reqs.record(ip)
