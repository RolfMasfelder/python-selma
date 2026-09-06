# ============================================================
# test_unit_session_store.py
#
# Unit tests für selma/session_store.py
# (Store-Pfade, load/save, resolve_session, resolve_session_file,
#  update_session_store_after_run, is_session_fresh, reset_session).
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from selma import session_store as ss
from selma.session_store import (
    DEFAULT_AGENT_ID,
    SessionRecord,
    SessionStore,
    SkillsSnapshot,
    _normalize_key,
    _now_iso,
    _sessions_dir,
    _store_path,
    is_session_fresh,
    load_session_store,
    reset_session,
    resolve_session,
    resolve_session_file,
    save_session_store,
    update_session_store_after_run,
)


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isoliert ALLE state-dir-Zugriffe dieser Datei von <projektroot>/.selma und ~/.selma.

    Doppelt abgesichert:
      1. SELMA_STATE_DIR → Prio-1-Fallback von resolve_state_dir()
      2. (tmp_path)/.selma existiert → selbst ohne Env-Variante bleibt
         die Prio-2-Auflösung (cwd/.selma) im tmp-Root und fällt NIE
         auf Home zurück.
    """
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".selma").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _store_path_for(tmp_path: Path, agent_id: str = DEFAULT_AGENT_ID) -> Path:
    # cwd=tmp-Root (simuliertes Projekt-Root) → <root>/.selma/agents/<id>/sessions/
    return ss._store_path(agent_id, cwd=str(tmp_path))


def _write_store(tmp_path: Path, data) -> Path:
    """Schreibt `data` (json-dumpbar oder String) als sessions.json."""
    path = _store_path_for(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _record_key(key: str, **kwargs):
    return {"session_key": key, **kwargs}


# ════════════════════════════════════════════════════════════
# Path-Helfer + kleine Funktionen
# ════════════════════════════════════════════════════════════


def test_sessions_dir_shape(tmp_path):
    d = _sessions_dir("agent-x", cwd=str(tmp_path))
    assert d.name == "sessions"
    assert str(d).endswith("/agents/agent-x/sessions")


def test_store_path_nested(tmp_path):
    p = _store_path("agent-x", cwd=str(tmp_path))
    assert p.name == "sessions.json"
    assert p.parent.name == "sessions"


def test_normalize_key_strips_and_lowercases():
    assert _normalize_key("  Agent:Main:Web  ") == "agent:main:web"
    assert _normalize_key("ABC") == "abc"


def test_now_iso_is_parseable_and_utc():
    dt = datetime.fromisoformat(_now_iso())
    assert dt.tzinfo is not None


def test_session_record_defaults():
    r = SessionRecord()
    assert len(r.session_id) == 8
    assert r.model_override is None
    assert r.thinking_level is None
    assert r.transcript_file is None
    datetime.fromisoformat(r.updated_at)  # kein Raise


def test_skills_snapshot_defaults():
    s = SkillsSnapshot(version="20260906")
    assert s.skill_names == []
    assert s.snapshot_text == ""


# ════════════════════════════════════════════════════════════
# load_session_store
# ════════════════════════════════════════════════════════════


def test_load_missing_file_returns_empty(tmp_path):
    store = load_session_store(cwd=str(tmp_path))
    assert store.sessions == {}
    assert store.store_path == str(_store_path_for(tmp_path))


def test_load_empty_file_returns_empty(tmp_path):
    _write_store(tmp_path, "   \n")
    store = load_session_store(cwd=str(tmp_path))
    assert store.sessions == {}


def test_load_invalid_json_returns_empty(tmp_path):
    _write_store(tmp_path, "{definitely not json")
    store = load_session_store(cwd=str(tmp_path))
    assert store.sessions == {}


def test_load_non_object_returns_empty(tmp_path):
    _write_store(tmp_path, [1, 2, 3])
    store = load_session_store(cwd=str(tmp_path))
    assert store.sessions == {}


def test_load_skips_faulty_entries(tmp_path):
    good = _record_key("agent:main:web")
    bad_thinking = _record_key("agent:main:bad", thinking_level="nonsense")  # Literal-Verstoß
    _write_store(
        tmp_path,
        {
            "agent:main:web": good,
            "agent:main:bad": bad_thinking,
            "agent:main:scalar": "i-am-not-a-dict",  # wird übersprungen (continue)
        },
    )
    store = load_session_store(cwd=str(tmp_path))
    assert set(store.sessions) == {"agent:main:web"}
    assert store.sessions["agent:main:web"].session_key == "agent:main:web"


def test_load_roundtrip_with_save(tmp_path):
    rec = SessionRecord(
        session_key="agent:main:web",
        model_override="some-model",
        thinking_level="high",
        skills_snapshot=SkillsSnapshot(version="v1", skill_names=["a", "b"], snapshot_text="txt"),
    )
    store = SessionStore(sessions={"agent:main:web": rec})
    store.store_path = str(_store_path_for(tmp_path))
    save_session_store(store)

    loaded = load_session_store(cwd=str(tmp_path))
    assert loaded.sessions["agent:main:web"].model_override == "some-model"
    assert loaded.sessions["agent:main:web"].thinking_level == "high"
    assert loaded.sessions["agent:main:web"].skills_snapshot.skill_names == ["a", "b"]
    assert loaded.store_path == store.store_path


# ════════════════════════════════════════════════════════════
# save_session_store
# ════════════════════════════════════════════════════════════


def test_save_creates_parents_and_no_tmp_leftover(tmp_path, monkeypatch):
    store = SessionStore(sessions={"k": SessionRecord(session_key="k")})
    deep = tmp_path / "a" / "b" / "sessions.json"
    store.store_path = str(deep)

    # .tmp-Write muss laufen (sonst wäre atomicity-Code ungetestet)
    writes: list[Path] = []
    orig_write = Path.write_text

    def spy(self, *args, **kwargs):  # noqa: ANN001
        writes.append(self)
        return orig_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy, raising=True)
    save_session_store(store)

    assert deep.exists()
    assert not deep.with_suffix(".tmp").exists()
    assert any(w.suffix == ".tmp" for w in writes)  # atomic: erst .tmp, dann replace


def test_save_failure_removes_tmp_and_raises(tmp_path, monkeypatch):
    store = SessionStore(sessions={"k": SessionRecord(session_key="k")})
    store.store_path = str(_store_path_for(tmp_path))

    def boom(*a, **kw):  # noqa: ANN002, ANN003
        raise OSError("disk on fire")

    monkeypatch.setattr(ss.json, "dumps", boom)
    with pytest.raises(OSError, match="disk on fire"):
        save_session_store(store)
    # .tmp (sofern angelegt) muss nach dem Fehler weg sein
    assert not Path(store.store_path).with_suffix(".tmp").exists()


# ════════════════════════════════════════════════════════════
# resolve_session
# ════════════════════════════════════════════════════════════


def test_resolve_session_by_key_is_case_and_ws_insensitive(tmp_path):
    rec = SessionRecord(session_key="agent:main:web", session_id="abc12345")
    store = SessionStore(sessions={"agent:main:web": rec})
    found, is_new = resolve_session(store, "  Agent:MAIN:Web ", None, SimpleConfig())
    assert not is_new
    assert found is rec


def test_resolve_session_by_id(tmp_path):
    rec = SessionRecord(session_key="whatever", session_id="ffff0000")
    store = SessionStore(sessions={"whatever": rec})
    found, is_new = resolve_session(store, None, "ffff0000", SimpleConfig())
    assert not is_new
    assert found is rec


def test_resolve_session_key_lookup_wins_over_id(tmp_path):
    rec_key = SessionRecord(session_key="agent:main:web", session_id="aaaa1111")
    rec_id = SessionRecord(session_key="other", session_id="bbbb2222")
    store = SessionStore(sessions={"agent:main:web": rec_key, "other": rec_id})
    # key gibt es, id zeigt auf einen anderen Record → Key-Suche gewinnt
    found, is_new = resolve_session(store, "agent:main:web", "bbbb2222", SimpleConfig())
    assert not is_new
    assert found is rec_key


def test_resolve_session_creates_new_for_key_only(tmp_path):
    store = SessionStore(sessions={})
    rec, is_new = resolve_session(store, "Agent:Main:Fresh", None, SimpleConfig())
    assert is_new
    assert rec.session_key == "agent:main:fresh"  # normalisiert gespeichert
    assert "agent:main:fresh" in store.sessions
    assert rec is store.sessions["agent:main:fresh"]


def test_resolve_session_creates_new_without_key_or_id(tmp_path):
    store = SessionStore(sessions={})
    rec, is_new = resolve_session(store, None, None, SimpleConfig())
    assert is_new
    assert len(rec.session_id) == 36  # UUID4 mit Bindestrichen
    assert rec.session_key == rec.session_id[:8]
    assert len(store.sessions) == 1


class SimpleConfig:
    """resolve_session nimmt ein Config entgegen, nutzt es aber nicht."""


# ════════════════════════════════════════════════════════════
# resolve_session_file
# ════════════════════════════════════════════════════════════


def test_resolve_session_file_explicit_override(tmp_path):
    rec = SessionRecord(session_id="abc", transcript_file=str(tmp_path / "deep" / "file.jsonl"))
    path = resolve_session_file(rec)
    assert path == str(tmp_path / "deep" / "file.jsonl")
    assert Path(tmp_path / "deep").is_dir()


def test_resolve_session_file_derived(tmp_path):
    rec = SessionRecord(session_id="abc12345")
    path = resolve_session_file(rec, agent_id="ag2", cwd=str(tmp_path))
    expected_dir = ss._sessions_dir("ag2", cwd=str(tmp_path))
    assert path == str(expected_dir / "abc12345.jsonl")
    assert expected_dir.is_dir()


def test_transcript_file_overrides_derivation(tmp_path):
    explicit = str(tmp_path / "explicit.jsonl")
    rec = SessionRecord(session_id="abc12345", transcript_file=explicit)
    assert resolve_session_file(rec, cwd=str(tmp_path)) == explicit


# ════════════════════════════════════════════════════════════
# update_session_store_after_run
# ════════════════════════════════════════════════════════════


def test_update_after_run_persists_overrides(tmp_path):
    rec = SessionRecord(session_key="agent:Main:Web")  # gemischt → wird normalisiert
    store = SessionStore(sessions={})
    store.store_path = str(_store_path_for(tmp_path))

    update_session_store_after_run(store, rec, provider="ollama", model="qwen")

    key = ss._normalize_key(rec.session_key)
    assert store.sessions[key] is rec
    assert rec.provider_override == "ollama"
    assert rec.model_override == "qwen"
    assert rec.last_interaction_at is not None

    loaded = load_session_store(cwd=str(tmp_path))
    assert loaded.sessions[key].model_override == "qwen"


def test_update_after_run_stores_mixedcase_key_normalized(tmp_path):
    rec = SessionRecord(session_key=" Agent:UPPER:Case ")
    store = SessionStore(sessions={})
    store.store_path = str(_store_path_for(tmp_path))
    update_session_store_after_run(store, rec, provider="p", model="m")
    assert set(store.sessions) == {"agent:upper:case"}


# ════════════════════════════════════════════════════════════
# is_session_fresh
# ════════════════════════════════════════════════════════════


class _FrozenDT(datetime):
    """Feste Uhr: 2026-09-06 12:00 in UTC.

    tz=None → 12:00 naive (vom Code als lokale Zeit behandelt),
    tz=UTC  → 12:00 UTC. Beide Lesarten sind konsistent.
    """

    fixed = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.fixed.replace(tzinfo=None)
        return cls.fixed.astimezone(tz)


def test_fresh_no_interaction_yet():
    assert is_session_fresh(SessionRecord()) is True


def test_fresh_invalid_timestamp():
    rec = SessionRecord(last_interaction_at="not-a-date")
    assert is_session_fresh(rec) is True


def _record_at(dt: datetime) -> SessionRecord:
    return SessionRecord(session_key="k", last_interaction_at=dt.isoformat())


def test_stale_daily_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FrozenDT)  # now = 2026-09-06 12:00
    old = _FrozenDT(2026, 9, 1, 12, 0, tzinfo=UTC)  # 5 Tage alt → vor jeder 4-Uhr-Grenze
    assert is_session_fresh(_record_at(old), at_hour=4) is False


def test_fresh_within_daily_boundary(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FrozenDT)
    recent = _FrozenDT(2026, 9, 6, 11, 0, tzinfo=UTC)  # heute, nach der 4-Uhr-Grenze
    assert is_session_fresh(_record_at(recent), at_hour=4) is True


def test_stale_idle_reset(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FrozenDT)
    last = _FrozenDT(2026, 9, 6, 11, 0, tzinfo=UTC)  # vor 12:00, daily-fresh
    assert is_session_fresh(_record_at(last), at_hour=4, idle_minutes=30) is False


def test_fresh_idle_disabled_or_under_limit(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FrozenDT)
    last = _FrozenDT(2026, 9, 6, 11, 55, tzinfo=UTC)  # 5 min her → idle=10min fresh
    assert is_session_fresh(_record_at(last), at_hour=4, idle_minutes=10) is True
    assert is_session_fresh(_record_at(last), at_hour=4, idle_minutes=0) is True
    assert is_session_fresh(_record_at(last), at_hour=4, idle_minutes=None) is True


def test_naive_timestamp_treated_as_utc(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FrozenDT)
    rec = SessionRecord(session_key="k", last_interaction_at="2026-09-06T11:00:00")  # naive
    assert is_session_fresh(rec, at_hour=4) is True


# ════════════════════════════════════════════════════════════
# reset_session
# ════════════════════════════════════════════════════════════


def test_reset_archives_transcript_and_preserves_overrides(tmp_path):
    transcript = ss._sessions_dir("main", cwd=str(tmp_path)) / "old-id-123.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text('{"role":"user"}\n', encoding="utf-8")

    rec = SessionRecord(
        session_id="old-id-123",
        session_key="agent:main:web",
        model_override="m1",
        provider_override="pr1",
        thinking_level="high",
        last_interaction_at="2026-01-01T00:00:00+00:00",
        transcript_file=str(transcript),
    )
    store = SessionStore(sessions={"agent:main:web": rec})
    store.store_path = str(_store_path_for(tmp_path))

    new_rec = reset_session(store, rec, cwd=str(tmp_path), agent_id="main")

    # Alt-transcript ist archiviert, Original weg
    assert not transcript.exists()
    archived = ss._sessions_dir("main", cwd=str(tmp_path)) / "archive" / "reset" / "old-id-123" / "old-id-123.jsonl"
    assert archived.exists()

    # Neuer Record: ID neu, Key + Overrides + thinking bleiben, Rest leer
    assert new_rec.session_id != "old-id-123"
    assert new_rec.session_key == "agent:main:web"
    assert new_rec.model_override == "m1"
    assert new_rec.provider_override == "pr1"
    assert new_rec.thinking_level == "high"
    assert new_rec.transcript_file is None
    assert new_rec.last_interaction_at is None

    # Store aktualisiert + persistiert
    assert store.sessions["agent:main:web"] is new_rec
    loaded = load_session_store(cwd=str(tmp_path))
    assert loaded.sessions["agent:main:web"].model_override == "m1"
    assert loaded.sessions["agent:main:web"].session_id == new_rec.session_id


def test_reset_without_transcript_file(tmp_path):
    rec = SessionRecord(session_key="k", session_id="x1", model_override="m")
    store = SessionStore(sessions={"k": rec})
    store.store_path = str(_store_path_for(tmp_path))
    new_rec = reset_session(store, rec, cwd=str(tmp_path))
    assert new_rec.session_id != "x1"
    assert store.sessions["k"] is new_rec
    # Kein archiv-Verzeichnis ohne Transcript-Anlass
    assert not (ss._sessions_dir("main", cwd=str(tmp_path)) / "archive").exists()


def test_reset_missing_transcript_file_on_disk_is_ok(tmp_path):
    gone = tmp_path / "never-existed.jsonl"
    rec = SessionRecord(session_key="k", session_id="x2", transcript_file=str(gone))
    store = SessionStore(sessions={"k": rec})
    store.store_path = str(_store_path_for(tmp_path))
    new_rec = reset_session(store, rec, cwd=str(tmp_path))
    assert new_rec.session_id != "x2"
