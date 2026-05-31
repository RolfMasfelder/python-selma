# ============================================================
# heartbeat.py
#
# Proactive agent turn: runs on a schedule without user input.
# Configurable via selma.json → "heartbeat": { "every": "30m", ... }
#
# Phase 1:
#   - Interval-based loop (every)
#   - Active-hours check (active_hours)
#   - HEARTBEAT.md empty? → skip turn
#   - HEARTBEAT_OK detection → silent acks are discarded
#   - target="last" → write alert to pending_alerts queue
#   - light_context → only HEARTBEAT.md in system prompt
#   - isolated_session → fresh session per run
# ============================================================

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Tracks when the next heartbeat will fire; None when disabled or not yet started.
next_heartbeat_at: datetime | None = None

HEARTBEAT_TOKEN = "HEARTBEAT_OK"
HEARTBEAT_TRANSCRIPT_PROMPT = "[Selma heartbeat]"
HEARTBEAT_PROMPT = (
    "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
    "Do not infer or repeat old tasks from prior chats. "
    f"If nothing needs attention, reply {HEARTBEAT_TOKEN}."
)


# ── Interval Parser ──────────────────────────────────────────

def parse_interval_seconds(every: str) -> int:
    """
    Parses "30m" → 1800, "1h" → 3600, "90s" → 90, "0m" → 0.
    Unknown format: 0 (disabled).
    """
    s = every.strip().lower()
    try:
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("s"):
            return int(s[:-1])
    except ValueError:
        pass
    logger.warning("Heartbeat: unknown interval %r — disabled", every)
    return 0


# ── Active Hours Check ───────────────────────────────────────

def is_within_active_hours(cfg) -> bool:
    """
    Returns True when the current local time falls within the
    configured window. cfg is an ActiveHoursConfig instance.
    """
    try:
        tz = ZoneInfo(cfg.timezone)
        now: dtime = datetime.now(tz).time()
        start = dtime.fromisoformat(cfg.start)
        end = dtime.fromisoformat(cfg.end)
        return start <= now <= end
    except Exception as e:
        logger.warning("Heartbeat: active_hours error | %s", e)
        return True     # run anyway when in doubt


# ── Is HEARTBEAT.md empty? ───────────────────────────────────

def is_heartbeat_content_effectively_empty(content: str | None) -> bool:
    """
    Returns True when HEARTBEAT.md has no actionable content.
    Considered empty: whitespace, ATX headers (# with space), empty
    list items (- / * / +), Markdown fences (```).
    File missing (content=None) → False (run anyway).
    """
    if content is None:
        return False

    for line in content.split("\n"):
        t = line.strip()
        if not t:
            continue
        if re.match(r'^#+(\s|$)', t):
            continue
        if re.match(r'^[-*+]\s*(\[[\sXx]?\]\s*)?$', t):
            continue
        if re.match(r'^```[A-Za-z0-9_-]*$', t):
            continue
        return False    # real content found

    return True


# ── Token-Stripping ──────────────────────────────────────────

def _strip_markup(text: str) -> str:
    """Removes HTML tags and Markdown wrappers for token detection."""
    text = re.sub(r'<[^>]*>', ' ', text)
    text = text.replace('&nbsp;', ' ')
    text = re.sub(r'^[*`~_]+', '', text)
    text = re.sub(r'[*`~_]+$', '', text)
    return text


def _strip_token_at_edges(raw: str) -> tuple[str, bool]:
    """
    Removes HEARTBEAT_OK from start/end (including up to 4 non-word characters
    directly after the token at the end). Returns (cleaned_text, was_stripped).
    """
    token = HEARTBEAT_TOKEN
    trailing_re = re.compile(
        re.escape(token) + r'[^\w]{0,4}$'
    )

    if token not in raw:
        return raw, False

    text = raw
    did_strip = False
    changed = True
    while changed:
        changed = False
        t = text.strip()
        if t.startswith(token):
            after = t[len(token):].lstrip()
            text = after
            did_strip = True
            changed = True
            continue
        if trailing_re.search(t):
            idx = t.rfind(token)
            before = t[:idx].rstrip()
            after = t[idx + len(token):].lstrip()
            text = (before + after).rstrip() if before else after.rstrip()
            did_strip = True
            changed = True

    return ' '.join(text.split()), did_strip


def strip_heartbeat_token(
    text: str | None,
    *,
    mode: str = "heartbeat",
    max_ack_chars: int = 300,
) -> dict:
    """
    Checks whether the reply is a heartbeat ack and strips the token.

    mode="heartbeat": remainder ≤ max_ack_chars → should_skip=True (delivery suppressed)
    mode="message":   always remove token, always deliver remainder

    Returns: {"should_skip": bool, "text": str, "did_strip": bool}
    """
    if not text or not text.strip():
        return {"should_skip": True, "text": "", "did_strip": False}

    trimmed = text.strip()

    # Markup normalisation for token detection
    normalized = _strip_markup(trimmed)

    has_token = HEARTBEAT_TOKEN in trimmed or HEARTBEAT_TOKEN in normalized

    if not has_token:
        return {"should_skip": False, "text": trimmed, "did_strip": False}

    stripped_orig, did_orig = _strip_token_at_edges(trimmed)
    stripped_norm, did_norm = _strip_token_at_edges(normalized)

    if did_orig and stripped_orig:
        stripped, did_strip = stripped_orig, did_orig
    else:
        stripped, did_strip = stripped_norm, did_norm

    if not did_strip:
        return {"should_skip": False, "text": trimmed, "did_strip": False}

    if not stripped:
        return {"should_skip": True, "text": "", "did_strip": True}

    if mode == "heartbeat" and len(stripped) <= max_ack_chars:
        return {"should_skip": True, "text": "", "did_strip": True}

    return {"should_skip": False, "text": stripped, "did_strip": True}


# ── Single Heartbeat Turn ────────────────────────────────────

async def run_heartbeat_turn(config, workspace_dir: str) -> str | None:
    """Executes a single heartbeat agent turn and returns the reply."""
    from runtime import RuntimeEnv, agent_command as run_agent, DeliveryContext

    cfg = config.heartbeat

    session_key = (
        f"heartbeat:{uuid.uuid4().hex[:8]}"
        if cfg.isolated_session
        else "heartbeat:main"
    )

    chunks: list[str] = []
    delivery = DeliveryContext(on_partial_reply=chunks.append)

    await run_agent(
        HEARTBEAT_PROMPT,
        session_key=session_key,
        delivery=delivery,
        runtime=RuntimeEnv(cwd=workspace_dir),
    )

    return "".join(chunks) or None


# ── Heartbeat-Loop ───────────────────────────────────────────

async def heartbeat_loop(
    config,
    workspace_dir: str,
    pending_alerts: asyncio.Queue,
) -> None:
    """
    Async loop: sleeps for `every` seconds, then runs an agent turn.
    Silent acks (HEARTBEAT_OK) are discarded.
    Alerts are placed in pending_alerts for the poll endpoint.
    """
    interval_s = parse_interval_seconds(config.heartbeat.every)
    if interval_s == 0:
        logger.info("Heartbeat disabled (every=0m)")
        return

    logger.info("Heartbeat started | every=%s (%ds)", config.heartbeat.every, interval_s)

    while True:
        global next_heartbeat_at
        next_heartbeat_at = (datetime.now().astimezone() + timedelta(seconds=interval_s)).replace(microsecond=0)
        await asyncio.sleep(interval_s)

        # Active hours check
        if config.heartbeat.active_hours:
            if not is_within_active_hours(config.heartbeat.active_hours):
                logger.debug("Heartbeat skipped: outside active_hours")
                continue

        # Is HEARTBEAT.md empty?
        hb_path = Path(workspace_dir) / "HEARTBEAT.md"
        content: str | None = None
        if hb_path.exists():
            content = hb_path.read_text(encoding="utf-8")
        if content is not None and is_heartbeat_content_effectively_empty(content):
            logger.debug("Heartbeat skipped: HEARTBEAT.md is empty")
            continue

        # Agent turn
        try:
            reply = await run_heartbeat_turn(config, workspace_dir)
        except Exception as e:
            logger.error("Heartbeat turn failed | error=%s", e)
            continue

        if not reply:
            continue

        # Check token
        result = strip_heartbeat_token(
            reply,
            mode="heartbeat",
            max_ack_chars=config.heartbeat.ack_max_chars,
        )
        if result["should_skip"]:
            logger.debug("Heartbeat: silent ack — no delivery")
            continue

        # Deliver alert
        alert_text = result["text"] or reply
        logger.info("Heartbeat Alert | chars=%d", len(alert_text))

        if config.heartbeat.target == "last":
            await pending_alerts.put(alert_text)
        else:
            logger.debug("Heartbeat: target=none — alert discarded")
