# ============================================================
# session_store.py
#
# Session store for my_mono.
#
# Two persistent layers (as in OpenClaw):
#   1. sessions.json  — key/value map: session_key → SessionRecord
#   2. <id>.jsonl     — transcript (managed by AgentSession)
#
# On-disk paths (analogous to OpenClaw):
#   Store:      <state_dir>/agents/<agentId>/sessions/sessions.json
#   Transcript: <state_dir>/agents/<agentId>/sessions/<sessionId>.jsonl
#
# Default agentId: "main"
#
# This file implements layer 1 only.
# Layer 2 is managed by my_mono.agent_session.SessionManager.
#
# Simplifications compared to OpenClaw:
#   - No in-memory cache (no TTL, no invalidation)
#   - No Windows retry logic
#   - No delivery normalisation
#   - No legacy key migrations
#   - No maintenance (no pruning, no rotation)
#   - No locking (single-user, single-process)
# ============================================================

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from helper import resolve_state_dir
from my_mono.tracing import trace_and_log

logger = logging.getLogger(__name__)

# Default agentId when none is explicitly provided.
# Corresponds to DEFAULT_AGENT_ID in OpenClaw.
DEFAULT_AGENT_ID = "main"


# -- Data structures -----------------------------------------------

class SkillsSnapshot(BaseModel):
    """
    Cached copy of loaded skills.
    Stored in the session and rebuilt only when the version changes.
    Corresponds to SkillsSnapshot in OpenClaw.
    """
    version: str                          # e.g. "20240501"
    skill_names: list[str] = Field(default_factory=list)
    snapshot_text: str = ""               # combined text of all skills


class SessionRecord(BaseModel):
    """
    Persisted state of a session between runs.
    Corresponds to SessionEntry in OpenClaw (stored in sessions-store.json).

    In OpenClaw: ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl
    Here:        .my_mono/sessions/<session_key>.json
    """
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    session_key: str = ""
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Stored model overrides (e.g. after /model command)
    model_override: str | None = None
    provider_override: str | None = None

    # Stored thinking level (persistent across sessions)
    thinking_level: Literal["low", "medium", "high"] | None = None

    # Skills snapshot — cached, rebuilt on version change
    skills_snapshot: SkillsSnapshot | None = None

    # Timestamp of the last user interaction (for idle reset check)
    last_interaction_at: str | None = None

    # Path to the JSONL transcript file
    transcript_file: str | None = None


class SessionStore(BaseModel):
    """
    In-memory representation of all known sessions.
    Written to disk by load_session_store() / save_session_store().
    Corresponds to the sessions-store in OpenClaw.
    """
    sessions: dict[str, SessionRecord] = Field(default_factory=dict)
    store_path: str = ".my_mono/sessions-store.json"


# ════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════

def _sessions_dir(agent_id: str = DEFAULT_AGENT_ID, cwd: str = ".") -> Path:
    """
    Directory for store and transcripts of an agent.

    Path: <state_dir>/agents/<agentId>/sessions/

    Corresponds to ~/.openclaw/agents/<agentId>/sessions/ in OpenClaw.
    """
    return resolve_state_dir(cwd) / "agents" / agent_id / "sessions"


def _store_path(agent_id: str = DEFAULT_AGENT_ID, cwd: str = ".") -> Path:
    """
    Absolute path to the sessions.json of an agent.

    Path: <state_dir>/agents/<agentId>/sessions/sessions.json
    """
    return _sessions_dir(agent_id, cwd) / "sessions.json"


def _normalize_key(session_key: str) -> str:
    """
    Normalises a session key: strip + lowercase.

    Corresponds to normalizeStoreSessionKey() in store-entry.ts.
    Reason: OpenClaw always stores keys in lowercase so that
    "Agent:Main:Main" and "agent:main:main" are the same session.
    """
    return session_key.strip().lower()


def _now_iso() -> str:
    """Current time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════
# 1. load_session_store
# ════════════════════════════════════════════════════════════

def load_session_store(agent_id: str = DEFAULT_AGENT_ID, cwd: str = ".") -> SessionStore:
    """
    Reads <state_dir>/agents/<agentId>/sessions/sessions.json from disk.

    Returns an empty SessionStore when:
      - the file does not yet exist (first start)
      - the file is empty or invalid

    Steps:
      1. Determine path via resolve_state_dir(cwd)
      2. File does not exist → return empty store
      3. Parse JSON
      4. Deserialise each entry as SessionRecord
         (faulty entries are skipped, not aborted)
      5. Return SessionStore with the path

    Corresponds to loadSessionStore() in store-load.ts,
    without cache and without Windows retry.
    """
    path = _store_path(agent_id, cwd)

    if not path.exists():
        trace_and_log(logger, f"sessions.json not found, starting with empty store | path={path}")
        return SessionStore(store_path=str(path))

    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            trace_and_log(logger, f"sessions.json is empty | path={path}")
            return SessionStore(store_path=str(path))

        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning(
                "sessions.json has invalid format (not an object), ignoring | path=%s", path
            )
            return SessionStore(store_path=str(path))

        sessions: dict[str, SessionRecord] = {}
        for key, entry in data.items():
            if not isinstance(entry, dict):
                continue
            try:
                sessions[key] = SessionRecord(**entry)
            except Exception as e:
                logger.warning(
                    "SessionRecord could not be loaded, skipping | key=%s error=%s",
                    key, e,
                )

        store = SessionStore(sessions=sessions, store_path=str(path))
        trace_and_log(logger, f"sessions.json loaded | sessions={len(sessions)}")
        return store

    except json.JSONDecodeError as e:
        logger.warning("sessions.json is not valid JSON, ignoring | error=%s", e)
        return SessionStore(store_path=str(path))
    except Exception as e:
        logger.warning("Error loading sessions.json | error=%s", e)
        return SessionStore(store_path=str(path))


# ════════════════════════════════════════════════════════════
# 2. save_session_store
# ════════════════════════════════════════════════════════════

def save_session_store(store: SessionStore) -> None:
    """
    Writes the SessionStore atomically to disk.

    Atomic means: first write to a .tmp file, then rename.
    This ensures sessions.json is never half-written —
    either the old or the new version, never corrupt.

    Steps:
      1. Create directory if it does not exist
      2. Serialise all SessionRecords as dicts
      3. Write JSON to sessions.json.tmp
      4. Rename .tmp → sessions.json (atomic)

    Corresponds to the atomic write part of updateSessionStore()
    in store.ts (OpenClaw).
    """
    path = Path(store.store_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    for key, record in store.sessions.items():
        data[key] = json.loads(record.model_dump_json())

    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(path)
        trace_and_log(logger, f"sessions.json saved | sessions={len(store.sessions)}")
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        logger.error("sessions.json could not be saved | error=%s", e)
        raise


# ════════════════════════════════════════════════════════════
# 3. resolve_session
# ════════════════════════════════════════════════════════════

def resolve_session(
    store: SessionStore,
    session_key: str | None,
    session_id: str | None,
    config,
) -> tuple[SessionRecord, bool]:
    """
    Finds the matching session or creates a new one.

    Returns (session_record, is_new_session).

    Steps:
      1. Take session_key and normalise (lowercase)
         → look up directly in the store
      2. Take session_id
         → linear search through all records
      3. Nothing found → create new SessionRecord,
         add to store, return is_new=True

    The normalisation step comes from store-entry.ts:
    keys are always stored in lowercase so that e.g.
    "Agent:Main:Main" and "agent:main:main" are the same session.

    Corresponds to resolveSessionStoreEntry() + session creation
    from store-entry.ts and store.ts (OpenClaw).
    """
    # ── 1. session_key → direct lookup ──────────────────────
    if session_key:
        normalized = _normalize_key(session_key)
        if normalized in store.sessions:
            record = store.sessions[normalized]
            trace_and_log(logger, f"Session found by key | key={normalized} id={record.session_id}")
            return record, False

    # ── 2. session_id → linear search ───────────────────────
    if session_id:
        for record in store.sessions.values():
            if record.session_id == session_id:
                trace_and_log(logger, f"Session found by id | id={session_id}")
                return record, False

    # ── 3. Create new session ────────────────────────────────
    new_id = str(uuid.uuid4())
    new_key = _normalize_key(session_key or session_id or new_id[:8])

    record = SessionRecord(
        session_id=new_id,
        session_key=new_key,
        updated_at=_now_iso(),
    )
    store.sessions[new_key] = record
    trace_and_log(logger, f"New session created | key={new_key} id={new_id}")
    return record, True


# ════════════════════════════════════════════════════════════
# 4. resolve_session_file
# ════════════════════════════════════════════════════════════

def resolve_session_file(
    session_record: SessionRecord,
    agent_id: str = DEFAULT_AGENT_ID,
    cwd: str = ".",
) -> str:
    """
    Returns the path to the JSONL transcript file.

    Steps:
      1. Is transcript_file set in the SessionRecord?
         → return that path directly (explicit override,
           e.g. when the path was set manually)
      2. Otherwise: derive path:
         <state_dir>/agents/<agentId>/sessions/<sessionId>.jsonl
      3. Create directory if needed
      4. Return path as string

    In OpenClaw: SessionEntry.sessionFile (explicit) or
    automatic derivation from session_id.
    The path is stored in SessionRecord after first resolution
    so it stays stable on subsequent runs.

    Corresponds to the sessionFile handling in OpenClaw.
    """
    # Explicit override takes precedence
    if session_record.transcript_file:
        path = Path(session_record.transcript_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    # Derive automatically from agentId + session_id
    sessions_dir = _sessions_dir(agent_id, cwd)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return str(sessions_dir / f"{session_record.session_id}.jsonl")


# ════════════════════════════════════════════════════════════
# 5. update_session_store_after_run
# ════════════════════════════════════════════════════════════

async def update_session_store_after_run(
    store: SessionStore,
    session_record: SessionRecord,
    result,
    provider: str,
    model: str,
) -> None:
    """
    Updates the store after a completed run.

    Writes back:
      - provider_override and model_override (current model choice)
      - updated_at (timestamp of the last run)

    Steps:
      1. Update session_record in place
      2. Store under normalised key in the store
         (ensures the key is consistently lowercase)
      3. Call save_session_store()

    Note: token counters (inputTokens, outputTokens etc.)
    are not tracked in my_mono. In OpenClaw they would be
    taken from result.meta.usage here.

    Corresponds to updateSessionStoreEntry() from store.ts (OpenClaw).
    """
    session_record.provider_override = provider
    session_record.model_override = model
    session_record.updated_at = _now_iso()
    session_record.last_interaction_at = _now_iso()

    key = _normalize_key(session_record.session_key or session_record.session_id)
    store.sessions[key] = session_record

    save_session_store(store)
    trace_and_log(logger, f"Session store updated after run | key={key} provider={provider} model={model}")


# ════════════════════════════════════════════════════════════
# 6. is_session_fresh
# ════════════════════════════════════════════════════════════

def is_session_fresh(record: SessionRecord, at_hour: int = 4, idle_minutes: int | None = None) -> bool:
    """
    Returns True when the session is still valid (no reset needed).

    Daily reset: the session is stale when last_interaction_at lies
    before today's reset boundary (at_hour:00 local time).
    If the current time is before at_hour, yesterday's boundary is used.

    Idle reset: active only when idle_minutes > 0.
    The session is stale when now - last_interaction_at > idle_minutes.

    A session with no last_interaction_at is always considered fresh
    (new session, first run not yet completed).
    """
    if not record.last_interaction_at:
        return True

    try:
        last = datetime.fromisoformat(record.last_interaction_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return True

    now = datetime.now(timezone.utc)

    # -- Daily reset --------------------------------------------------
    now_local = datetime.now()
    boundary_local = now_local.replace(hour=at_hour, minute=0, second=0, microsecond=0)
    if now_local < boundary_local:
        boundary_local -= timedelta(days=1)
    boundary_utc = boundary_local.astimezone(timezone.utc)

    if last < boundary_utc:
        trace_and_log(logger, f"Session stale (daily reset) | last={last.isoformat()} boundary={boundary_utc.isoformat()}")
        return False

    # -- Idle reset ---------------------------------------------------
    if idle_minutes is not None and idle_minutes > 0:
        idle_limit = last + timedelta(minutes=idle_minutes)
        if now > idle_limit:
            trace_and_log(logger, f"Session stale (idle) | last={last.isoformat()} idle_minutes={idle_minutes}")
            return False

    return True


# ════════════════════════════════════════════════════════════
# 7. reset_session
# ════════════════════════════════════════════════════════════

def reset_session(
    store: SessionStore,
    record: SessionRecord,
    cwd: str = ".",
    agent_id: str = DEFAULT_AGENT_ID,
) -> SessionRecord:
    """
    Resets a session: archives the old transcript and creates a new SessionRecord.

    Preserves across the reset:
      - session_key (same key, continuity for the channel/peer)
      - model_override (if set by user)
      - provider_override (if set by user)
      - thinking_level

    Clears:
      - session_id (new UUID)
      - transcript_file (new file will be created on next run)
      - last_interaction_at
      - skills_snapshot

    The old transcript file is moved to:
      <state_dir>/agents/<agentId>/sessions/archive/reset/<old_session_id>/

    Corresponds to the reset + archival logic in OpenClaw
    (src/config/sessions/store.ts, reset-preserved-selection.ts).
    """
    # -- Archive old transcript --------------------------------------
    if record.transcript_file:
        old_path = Path(record.transcript_file)
        if old_path.exists():
            archive_dir = (
                _sessions_dir(agent_id, cwd)
                / "archive" / "reset" / record.session_id
            )
            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), archive_dir / old_path.name)
            trace_and_log(logger, f"Transcript archived | old_id={record.session_id} archive={archive_dir}")

    # -- Create new record -------------------------------------------
    new_record = SessionRecord(
        session_key=record.session_key,
        model_override=record.model_override,
        provider_override=record.provider_override,
        thinking_level=record.thinking_level,
    )

    key = _normalize_key(record.session_key or new_record.session_id)
    store.sessions[key] = new_record
    save_session_store(store)

    trace_and_log(logger, f"Session reset | old_id={record.session_id} new_id={new_record.session_id} key={key}")
    return new_record
