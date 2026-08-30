# ============================================================
# compaction unit tests
#
# Deckung für selma/compaction.py (compact_session):
#   - Datei fehlt → ok, nicht compacted
#   - create_agent_session schlägt fehl → ok=False
#   - zu wenig Messages → ok, nicht compacted
#   - compact() schlägt fehl → ok=False
#   - Erfolg → compacted=True + Token-Zählung
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from selma import compaction
from selma.compaction import CompactionResult, compact_session
from selma.config import ModelConfig


def _config() -> SimpleNamespace:
    return SimpleNamespace(model=ModelConfig(model="test-model"))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeSession:
    """Mock einer AgentSession: Messages + compact()."""

    def __init__(self, contents, compact_error=None):
        self.state = SimpleNamespace(messages=[SimpleNamespace(content=c) for c in contents])
        self._compact_error = compact_error
        self.compact = AsyncMock(side_effect=self._compact_error, name="compact")

    def _apply_compaction(self):
        # summarisiert: alle alten Messages gegen eine Zusammenfassung
        self.state.messages = [SimpleNamespace(content="Zusammenfassung der bisherigen Konversation")]


def test_result_missing_file(tmp_path):
    result = _run(compact_session(str(tmp_path / "fehlt.jsonl"), _config()))
    assert isinstance(result, CompactionResult)
    assert result.ok is True
    assert result.compacted is False
    assert result.reason == "Session file does not exist"


def test_result_create_session_fails(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text("{}")

    with patch.object(
        compaction,
        "create_agent_session",
        side_effect=RuntimeError("boom"),
    ):
        result = _run(compact_session(str(session_file), _config()))

    assert result.ok is False
    assert result.compacted is False
    assert "boom" in result.reason


def test_too_few_messages(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text("{}")

    fake = _FakeSession(["Nur eine Nachricht"])
    with patch.object(compaction, "create_agent_session", AsyncMock(return_value=fake)):
        result = _run(compact_session(str(session_file), _config()))

    assert result.ok is True
    assert result.compacted is False
    assert "Too few messages" in result.reason
    fake.compact.assert_not_awaited()


def test_compact_failure(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text("{}")

    fake = _FakeSession(["Ein", "Zwei"], compact_error=RuntimeError("llm down"))
    with patch.object(compaction, "create_agent_session", AsyncMock(return_value=fake)):
        result = _run(compact_session(str(session_file), _config()))

    assert result.ok is False
    assert result.compacted is False
    assert "llm down" in result.reason
    fake.compact.assert_awaited_once()


def test_compact_success_counts_tokens(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text("{}")

    def _after_compact():
        fake._apply_compaction()

    fake = _FakeSession(
        [
            "Dies ist eine umfangreiche erste Nachricht mit viel Kontext zur Diskussion",
            "Und hier eine zweite Nachricht, die ebenfalls ziemlich lang geworden ist",
            "Drittens: noch eine ausführliche Nachricht, damit vor der Kompression eindeutig mehr Tokens vorliegen",
        ],
        compact_error=None,
    )
    fake.compact.side_effect = _after_compact

    with patch.object(compaction, "create_agent_session", AsyncMock(return_value=fake)):
        result = _run(compact_session(str(session_file), _config()))

    assert result.ok is True
    assert result.compacted is True
    assert result.tokens_before > result.tokens_after
