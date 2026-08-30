# ============================================================
# dashboard unit tests
#
# Deckung für selma/dashboard.py (Streamlit-Webchat):
#   - parse_sse_events: data-Zeilen, Skip nicht-data, Skip
#     invalid-JSON, Bruch bei done/error
#   - read_raw_file / write_raw_file: Erfolg + FileNotFoundError
#   - App-Rendering via AppTest:
#       * initial: session-Defaults, Titel, Chat-Input
#       * Chat-Erfolgspfad (tool/chunk/done-Events → Nachrichten)
#       * Gateway-ConnectError → st.error
#       * SSE error-Event → RuntimeError → st.error
#       * Config-Dialog: Bearbeiten, ungültiges JSON, speichern,
#         verwerfen; ungültige Startconfig → st.error + code-Block
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = str(PROJECT_ROOT / "src" / "selma" / "dashboard.py")
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x9a\xcb\x5e\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _prepare_cwd(tmp_path, config=None):
    """Selma-Workspace in tmp_path: config + Image bereitstellen."""
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "selma.png").write_bytes(PNG_1x1)
    if config is not None:
        (tmp_path / ".selma").mkdir()
        (tmp_path / ".selma" / "selma.json").write_text(config, encoding="utf-8")
    return tmp_path


class FakeStreamResponse:
    """httpx-Response-Double: iter_lines() liefert fertige SSE-Zeilen."""

    def __init__(self, lines):
        self._lines = lines

    @staticmethod
    def _sse(event: dict) -> str:
        return "data: " + json.dumps(event)

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeClient:
    """httpx.Client-Double: stream() liefert FakeStreamResponse."""

    def __init__(self, response=None, connect_error=False):
        self._response = response
        self._connect_error = connect_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, *args, **kwargs):
        if self._connect_error:
            raise __import__("httpx").ConnectError("nope", request=object())
        sse = FakeStreamResponse._sse
        resp = self._response or FakeStreamResponse(
            [
                sse({"type": "tool", "name": "read"}),
                sse({"type": "chunk", "text": "Hi"}),
                sse({"type": "chunk", "text": " Rolf"}),
                sse({"type": "done"}),
            ]
        )
        resp._args = (args, kwargs)
        return resp


def test_parse_sse_events_basic():
    from selma.dashboard import parse_sse_events

    lines = [
        "event: foo",
        'data: {"type": "chunk", "text": "a"}',
        "data: not-json",
        'data: {"type": "chunk", "text": "b"}',
        'data: {"type": "done"}',
        'data: {"type": "chunk", "text": "ignored after done"}',
    ]
    events = list(parse_sse_events(FakeStreamResponse(lines)))
    assert events == [
        {"type": "chunk", "text": "a"},
        {"type": "chunk", "text": "b"},
        {"type": "done"},
    ]


def test_parse_sse_events_stops_on_error():
    from selma.dashboard import parse_sse_events

    lines = [
        'data: {"type": "chunk", "text": "x"}',
        'data: {"type": "error", "message": "kaputt"}',
        'data: {"type": "chunk", "text": "nie"}',
    ]
    events = list(parse_sse_events(FakeStreamResponse(lines)))
    assert events[-1] == {"type": "error", "message": "kaputt"}
    assert len(events) == 2


def test_read_write_raw_file(tmp_path):
    from selma.dashboard import read_raw_file, write_raw_file

    f = tmp_path / "cfg.json"
    f.write_text("alt", encoding="utf-8")
    assert read_raw_file(str(f)) == "alt"

    write_raw_file(str(f), "neu")
    assert f.read_text(encoding="utf-8") == "neu"


def test_read_raw_file_missing_raises(tmp_path):
    from selma.dashboard import read_raw_file

    with pytest.raises(FileNotFoundError):
        read_raw_file(str(tmp_path / "fehlt.json"))


# ------------------------------------------------------------------
# App-Level Tests (streamlit.testing)
# ------------------------------------------------------------------


def _app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return AppTest.from_file(DASHBOARD_PATH, default_timeout=30)


class _NoRerun:
    """Patch-Kontext: chdir + st.rerun als No-op.

    Das Dashboard ruft nach Aktionen st.rerun(); AppTest würde dadurch das
    Element-Tree (st.error/st.success/Dialog-Widgets) aufräumen, bevor der
    Test asserten kann. Für die App-Logik unter Test reicht ein einzelner
    Skript-Durchlauf.
    """

    def __init__(self, tmp_path):
        self._tmp_path = tmp_path
        self._ctx = pytest.MonkeyPatch.context()
        self._mp = None
        self._reruns: list = []

    def __enter__(self):
        self._mp = self._ctx.__enter__()
        self._mp.chdir(self._tmp_path)
        import streamlit as st

        self._mp.setattr(st, "rerun", lambda *a, **kw: self._reruns.append(1))
        return self

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


def test_initial_render_sets_defaults(tmp_path, monkeypatch):
    _prepare_cwd(tmp_path, config='{"agent": {"name": "Selma"}}')
    at = _app(tmp_path, monkeypatch)
    at.run()

    assert not at.exception
    assert at.session_state["user_id"] == "dashboard"
    assert at.session_state["messages"] == []
    assert at.session_state["processing"] is False
    assert at.session_state["pending_prompt"] is None
    # Streamlit-Sidebar-Header zeigt den Titel
    from selma.dashboard import TITLE

    assert any(TITLE in h.value for h in at.sidebar.header)
    # Chat-Input im Layout
    assert len(at.chat_input) >= 1


def test_chat_success_flow_appends_messages(tmp_path, monkeypatch):
    _prepare_cwd(tmp_path)

    import selma.dashboard as dashboard

    with _NoRerun(tmp_path) as ctx:
        ctx._mp.setattr(dashboard.httpx, "Client", lambda **kw: FakeClient())

        at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
        at.run()

        # Neue Eingabe
        at.chat_input[0].set_value("Testhallo")
        at.run()

    messages = at.session_state["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant"]
    assert messages[0]["content"] == "Testhallo"
    assert messages[1]["content"] == "Hi Rolf"
    assert at.session_state["processing"] is False
    assert at.session_state["pending_prompt"] is None


def test_chat_connection_error_shows_error(tmp_path, monkeypatch):
    _prepare_cwd(tmp_path)

    import selma.dashboard as dashboard

    with _NoRerun(tmp_path) as ctx:
        ctx._mp.setattr(
            dashboard.httpx,
            "Client",
            lambda **kw: FakeClient(connect_error=True),
        )

        at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
        at.run()
        at.chat_input[0].set_value("Hey")
        at.run()

    assert any("Gateway unreachable" in e.value for e in at.error)
    # Nutzer-Nachricht bleibt, Antwort fehlt
    assert [m["role"] for m in at.session_state["messages"]] == ["user"]
    assert at.session_state["processing"] is False


def test_chat_sse_error_event_shows_message(tmp_path, monkeypatch):
    _prepare_cwd(tmp_path)

    import selma.dashboard as dashboard

    resp = FakeStreamResponse(
        [
            FakeStreamResponse._sse({"type": "chunk", "text": "Teil "}),
            FakeStreamResponse._sse({"type": "error", "message": "Modell gestürzt"}),
        ]
    )
    with _NoRerun(tmp_path) as ctx:
        ctx._mp.setattr(dashboard.httpx, "Client", lambda **kw: FakeClient(response=resp))

        at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
        at.run()
        at.chat_input[0].set_value("Hallo")
        at.run()

    assert any("Modell gestürzt" in e.value for e in at.error)
    assert [m["role"] for m in at.session_state["messages"]] == ["user"]


def test_settings_dialog_edit_and_discard(tmp_path, monkeypatch):
    config = {"agent": {"name": "Selma"}, "model": {"model": "ollama/x"}}
    _prepare_cwd(tmp_path, config=json.dumps(config, indent=2))

    with _NoRerun(tmp_path):
        at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
        at.run()

        # Sidebar-Button „⚙️ Settings" klicken
        at.sidebar.button[0].set_value(True)
        at.run()

        # Dialog erscheint, rohe Config ist drin
        assert at.session_state["config_raw_content"] == json.dumps(config, indent=2)
        assert at.session_state["config_editing"] is False

        # Bearbeitungsmodus starten.
        # AppTest: der Sidebar-Button ist nur im Run seiner eigenen set_value
        # wirksam (One-Shot), "✎ Edit File" im Dialog würde daher nie
        # verarbeitet. Der Edit-Modus wird wie von der App-Logik erwartet
        # über session_state.config_editing gesetzt (Ziel-Zustand identisch).
        at.session_state["config_editing"] = True
        at.run()
        assert at.session_state["config_editing"] is True

        # Discard → zurück in Anzeigemodus, Datei unverändert
        discard = None
        for b in at.button:
            if "✖" in b.label or "Discard" in b.label:
                discard = b
        assert discard is not None, f"Buttons: {[b.label for b in at.button]}"
        discard.set_value(True)
        at.run()

    assert at.session_state["config_editing"] is False
    assert json.loads((tmp_path / ".selma" / "selma.json").read_text(encoding="utf-8")) == config


def test_settings_dialog_save_valid_json(tmp_path, monkeypatch):
    config = {"agent": {"name": "Selma"}}
    _prepare_cwd(tmp_path, config=json.dumps(config))
    new_config = json.dumps({"agent": {"name": "Selma2"}})

    with _NoRerun(tmp_path):
        at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
        at.run()
        at.sidebar.button[0].set_value(True)
        at.run()
        # ins Edit-Modus (siehe test_settings_dialog_edit_and_discard)
        at.session_state["config_editing"] = True
        at.run()
        assert at.session_state["config_editing"] is True

        # Textarea auf neue (gültige) Config setzen und speichern
        ta = at.text_area[0]
        ta.set_value(new_config)
        for b in at.button:
            if "Save Changes" in b.label or "💾" in b.label:
                b.set_value(True)
        at.run()

    assert at.session_state["config_editing"] is False
    assert (tmp_path / ".selma" / "selma.json").read_text(encoding="utf-8") == new_config
    assert any("File updated successfully" in s.value for s in at.success)


def test_settings_dialog_save_invalid_json_rejected(tmp_path, monkeypatch):
    config = {"agent": {"name": "Selma"}}
    _prepare_cwd(tmp_path, config=json.dumps(config))

    with _NoRerun(tmp_path):
        at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
        at.run()
        at.sidebar.button[0].set_value(True)
        at.run()
        # ins Edit-Modus (siehe test_settings_dialog_edit_and_discard)
        at.session_state["config_editing"] = True
        at.run()

        # Ungültiges JSON rein
        at.text_area[0].set_value("{ das ist nicht json")
        for b in at.button:
            if "Save Changes" in b.label or "💾" in b.label:
                b.set_value(True)
        at.run()

    # bleibt im Edit-Modus, nichts gespeichert
    assert at.session_state["config_editing"] is True
    assert any("Validation Failed" in e.value for e in at.error)
    assert (tmp_path / ".selma" / "selma.json").read_text(encoding="utf-8") == json.dumps(config)


def test_settings_dialog_invalid_initial_json_shows_error(tmp_path, monkeypatch):
    # Start-Config ist KEIN valides JSON → st.error + Code-Block statt st.json
    _prepare_cwd(tmp_path, config="definitely { not json")

    with _NoRerun(tmp_path):
        at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
        at.run()
        at.sidebar.button[0].set_value(True)
        at.run()

    assert any("Error details:" in e.value for e in at.error)
