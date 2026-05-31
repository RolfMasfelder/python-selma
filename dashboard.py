import streamlit as st
import httpx
import uuid

import json
import os
from typing import Any, Dict, Final, Generator

WEBCHAT_STREAM_URL: Final = "http://localhost:8000/webchat/stream"
TITLE: Final = "👩🏻 Selma Agent Dashboard"

# -- Configuration
st.set_page_config(
    page_title=TITLE, 
    layout="wide",
    initial_sidebar_state='expanded'
)

if 'config_editing' not in st.session_state:
        st.session_state.config_editing = False
        
# -- Custom CSS for fixed sidebar width
st.markdown("""
    <style>
    [data-testid="stSidebar"] {
        min-width: 200px;
        max-width: 200px;
    }
    </style>
""", unsafe_allow_html=True)

if "user_id" not in st.session_state:
    st.session_state.user_id = "dashboard"

if "messages" not in st.session_state:
    st.session_state.messages = []

if "processing" not in st.session_state:
    st.session_state.processing = False

if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

def parse_sse_events(response: httpx.Response) -> Generator[dict, None, None]:
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
CONFIG_FILE = ".selma/selma.json"

def read_raw_file(filepath: str) -> str:
    """
    Reads the file as raw text. 
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}") 
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content: str = f.read()
        return content


def write_raw_file(filepath: str, content: str) -> None:
    """Writes the provided raw string content to a file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)


@st.dialog("⚙️ Settings", width="large")
def settings_dialog():
    st.subheader("Configuration")

    if 'config_raw_content' not in st.session_state or not st.session_state.config_editing:
        st.session_state.config_raw_content = read_raw_file(CONFIG_FILE)

    if not st.session_state.config_editing:

        col_a, col_b = st.columns([0.8, 0.2])

        with col_a:
            st.info(f"Currently viewing: `{CONFIG_FILE}`")
        with col_b:
            if st.button("✎ Edit File"):
                st.session_state.config_editing = True
                st.rerun()

        try:
            # Parse the raw string into a typed Dictionary for the st.json viewer
            json.loads(st.session_state.config_raw_content)
            st.json(json.loads(st.session_state.config_raw_content))
        except Exception as e:
            st.error(f"Error details: {e.msg} at line {e.lineno}, column {e.colno}")
            st.code(st.session_state.config_raw_content)

    else:
        # --- EDIT MODE ---
        st.subheader("Edit Mode")
        st.caption("Editing raw text. No comments allowed in standard JSON.")
        
        # edited_text will be a string from the text_area
        edited_text: str = st.text_area(
            label="JSON Content", 
            value=st.session_state.config_raw_content, 
            height=400
        )
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("💾 Save Changes"):
                try:
                    # Validation: Try to parse the input string to ensure it's valid JSON
                    _: Dict[str, Any] = json.loads(edited_text) 
                    
                    # Write the raw string to the file
                    write_raw_file(CONFIG_FILE, edited_text)
                    
                    st.session_state.config_raw_content = edited_text
                    st.session_state.config_editing = False

                    st.success("File updated successfully.")
                    st.rerun()

                except json.JSONDecodeError as e:
                    st.error(f"Validation Failed: {e.msg} at line {e.lineno}")
                    
        with col2:
            if st.button("✖ Discard"):
                st.session_state.config_editing = False
                st.rerun()

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
            payload = {
                "user_id": st.session_state.user_id,
                "text": prompt,
                "user_name": "Admin"
            }
            tool_status = st.empty()
            reply_box = st.empty()
            full_reply = ""

            with httpx.Client() as client:
                with client.stream("POST", WEBCHAT_STREAM_URL, json=payload, timeout=300.0) as response:
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

