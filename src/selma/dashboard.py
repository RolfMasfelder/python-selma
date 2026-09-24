import json
import os
from collections.abc import Generator
from typing import Any, Final

import httpx
import streamlit as st

WEBCHAT_STREAM_URL: Final = "http://localhost:8000/webchat/stream"
TITLE: Final = "👩🏻 Selma Agent Dashboard"

# -- Configuration
st.set_page_config(page_title=TITLE, layout="wide", initial_sidebar_state="expanded")

if "config_editing" not in st.session_state:
    st.session_state.config_editing = False

# -- Custom CSS for fixed sidebar width
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        min-width: 200px;
        max-width: 200px;
    }
    </style>
""",
    unsafe_allow_html=True,
)

if "user_id" not in st.session_state:
    st.session_state.user_id = "dashboard"

if "messages" not in st.session_state:
    st.session_state.messages = []

if "processing" not in st.session_state:
    st.session_state.processing = False

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None


def parse_sse_events(response: httpx.Response) -> Generator[dict]:
    """Reads SSE lines, yields all events as dicts."""
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        yield event
        if event.get("type") in ("done", "error"):
            break


# -- Settings Dialog
CONFIG_FILE: Final = ".selma/selma.json"

# UI-Texte des Settings-Dialogs (String-Konstanten: stabile Referenzen für Tests/Übersetzungen)
SETTINGS_DIALOG_TITLE: Final = "⚙️ Settings"
SETTINGS_SUBHEADER: Final = "Configuration"
VIEW_INFO_TEMPLATE: Final = "Currently viewing: `{path}`"
JSON_PARSE_ERROR_TEMPLATE: Final = "Error details: {msg} at line {lineno}, column {colno}"
VALIDATION_ERROR_TEMPLATE: Final = "Validation Failed: {msg} at line {lineno}"
SAVE_SUCCESS: Final = "File updated successfully."
EDIT_MODE_SUBHEADER: Final = "Edit Mode"
EDIT_MODE_CAPTION: Final = "Editing raw text. No comments allowed in standard JSON."
CONFIG_TEXTAREA_LABEL: Final = "JSON Content"
BTN_EDIT_FILE: Final = "✎ Edit File"
BTN_SAVE_CHANGES: Final = "💾 Save Changes"
BTN_DISCARD: Final = "✖ Discard"


def read_raw_file(filepath: str) -> str:
    """
    Reads the file as raw text.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    with open(filepath, encoding="utf-8") as f:
        content: str = f.read()
        return content


def write_raw_file(filepath: str, content: str) -> None:
    """Writes the provided raw string content to a file."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


def _render_config_view() -> None:
    """Anzeigemodus: aktuelle Config als JSON-Viewer zeigen + Edit-Button bereitstellen."""
    col_a, col_b = st.columns([0.8, 0.2])

    with col_a:
        st.info(VIEW_INFO_TEMPLATE.format(path=CONFIG_FILE))
    with col_b:
        if st.button(BTN_EDIT_FILE):
            st.session_state.config_editing = True
            st.rerun()

    raw = st.session_state.config_raw_content
    try:
        # Parse the raw string into a typed Dictionary for the st.json viewer
        st.json(json.loads(raw))
    except json.JSONDecodeError as e:
        st.error(JSON_PARSE_ERROR_TEMPLATE.format(msg=e.msg, lineno=e.lineno, colno=e.colno))
        # Im Fehlerfall den rohen Inhalt zeigen, damit die fehlerhafte Stelle auffindbar ist
        st.code(raw)


def _save_config(edited_text: str) -> None:
    """Validiere das Edit-Ergebnis und schreibe es; bei ungültigem JSON: Fehler zeigen + im Edit-Modus bleiben."""
    try:
        # Validation: Try to parse the input string to ensure it's valid JSON
        _: dict[str, Any] = json.loads(edited_text)
    except json.JSONDecodeError as e:
        st.error(VALIDATION_ERROR_TEMPLATE.format(msg=e.msg, lineno=e.lineno))
        return

    # Write the raw string to the file
    write_raw_file(CONFIG_FILE, edited_text)
    st.session_state.config_raw_content = edited_text
    st.session_state.config_editing = False
    st.success(SAVE_SUCCESS)
    st.rerun()


def _render_edit_mode() -> None:
    """Bearbeitungsmodus: Config als roher Text, Save/Discard-Buttons."""
    st.subheader(EDIT_MODE_SUBHEADER)
    st.caption(EDIT_MODE_CAPTION)

    # edited_text will be a string from the text_area
    edited_text: str = st.text_area(label=CONFIG_TEXTAREA_LABEL, value=st.session_state.config_raw_content, height=400)

    col1, col2 = st.columns(2)

    with col1:
        if st.button(BTN_SAVE_CHANGES):
            _save_config(edited_text)

    with col2:
        if st.button(BTN_DISCARD):
            st.session_state.config_editing = False
            st.rerun()


@st.dialog(SETTINGS_DIALOG_TITLE, width="large")
def settings_dialog():
    st.subheader(SETTINGS_SUBHEADER)

    if "config_raw_content" not in st.session_state or not st.session_state.config_editing:
        st.session_state.config_raw_content = read_raw_file(CONFIG_FILE)

    if not st.session_state.config_editing:
        _render_config_view()
    else:
        _render_edit_mode()


# -- Sidebar
st.sidebar.header(TITLE)
st.sidebar.image("images/selma.png", width=200)
if st.sidebar.button("⚙️ Settings"):
    settings_dialog()

if st.session_state.config_editing:
    settings_dialog()

# -- Chat

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

incoming = st.chat_input("How can I help you today?", disabled=st.session_state.processing)

if incoming and not st.session_state.processing:
    st.session_state.pending_prompt = incoming
    st.session_state.processing = True
    st.rerun()

if st.session_state.processing and st.session_state.pending_prompt:
    prompt = st.session_state.pending_prompt
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            payload = {"user_id": st.session_state.user_id, "text": prompt, "user_name": "Admin"}
            tool_status = st.empty()
            reply_box = st.empty()
            full_reply = ""

            with httpx.Client() as client:
                # No read timeout: slow (e.g. CPU-only) model inference can take much
                # longer than a fixed timeout would allow. The gateway's own idle
                # timeout (selma.json model.timeout_seconds) is the effective bound.
                stream_timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
                with client.stream("POST", WEBCHAT_STREAM_URL, json=payload, timeout=stream_timeout) as response:
                    for event in parse_sse_events(response):
                        match event.get("type"):
                            case "tool":
                                tool_status.caption(f"🔧 {event.get('name', 'tool')}…")
                            case "chunk":
                                tool_status.empty()
                                full_reply += event.get("text", "")
                                reply_box.markdown(full_reply + "▌")
                            case "error":
                                tool_status.empty()
                                raise RuntimeError(event.get("message", "Unknown error."))
                            case "done":
                                tool_status.empty()
                                reply_box.markdown(full_reply)

            st.session_state.messages.append({"role": "assistant", "content": full_reply})
        except httpx.ConnectError:
            st.error("❌ Gateway unreachable. Is `gateway.py` running?")
        except RuntimeError as e:
            st.error(f"❌ {e}")
        except Exception as e:
            st.error(f"An error occurred: {e}")
        finally:
            st.session_state.pending_prompt = None
            st.session_state.processing = False
            st.rerun()
