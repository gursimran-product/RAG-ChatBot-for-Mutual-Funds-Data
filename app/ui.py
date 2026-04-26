"""
Streamlit UI — Component 11.

Minimal, compliance-aware chat interface:
  - Welcome message
  - 3 example questions
  - Persistent "Facts-only. No investment advice." disclaimer banner
  - Thread-aware chat window with clickable citation links
  - Multi-thread support via st.session_state thread_id
"""

import sys
from pathlib import Path

import os

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SBI Mutual Fund FAQ",
    page_icon="📊",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Disclaimer banner (always visible)
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div style="background:#fff3cd;border-left:4px solid #ffc107;padding:10px 16px;
                border-radius:4px;margin-bottom:16px;">
        <strong>⚠️ Facts-only. No investment advice.</strong><br>
        This assistant provides factual information only. It does not recommend,
        advise, or predict. Always consult a SEBI-registered investment advisor
        before making investment decisions.
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Header & welcome
# ---------------------------------------------------------------------------

st.title("📊 SBI Mutual Fund FAQ Assistant")
st.caption(
    "Ask factual questions about **SBI Gold Fund** and **SBI PSU Fund** (Direct Growth). "
    "Answers are sourced from official Groww fund pages."
)

# ---------------------------------------------------------------------------
# Session initialisation
# ---------------------------------------------------------------------------

if "thread_id" not in st.session_state:
    try:
        resp = requests.post(f"{API_BASE}/session", timeout=5)
        resp.raise_for_status()
        st.session_state.thread_id = resp.json()["thread_id"]
        st.session_state.messages = []
    except Exception as e:
        st.error(f"Could not connect to the FAQ API at {API_BASE}. Make sure it is running.\n\n`{e}`")
        st.stop()

# ---------------------------------------------------------------------------
# Example questions
# ---------------------------------------------------------------------------

with st.expander("💡 Example questions to get started", expanded=True):
    examples = [
        "What is the expense ratio of SBI Gold Fund?",
        "What is the minimum SIP amount for SBI PSU Fund?",
        "What is the exit load for SBI Gold Fund?",
    ]
    cols = st.columns(3)
    for col, q in zip(cols, examples):
        if col.button(q, use_container_width=True):
            st.session_state.prefill = q

# ---------------------------------------------------------------------------
# Chat history display
# ---------------------------------------------------------------------------

for msg in st.session_state.get("messages", []):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("source_url"):
            st.caption(
                f"🔗 [Source]({msg['source_url']}) · {msg.get('fetch_date', '')}"
            )

# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------

prefill = st.session_state.pop("prefill", "")
user_input = st.chat_input(
    "Ask a factual question about the fund…",
    key="chat_input",
) or prefill

if user_input:
    # Display user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Call API
    with st.chat_message("assistant"):
        with st.spinner("Looking up…"):
            try:
                resp = requests.post(
                    f"{API_BASE}/chat",
                    json={
                        "thread_id": st.session_state.thread_id,
                        "query": user_input,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                answer_text = data["answer"]
                source_url  = data.get("source_url", "")
                fetch_date  = data.get("fetch_date", "")

                st.markdown(answer_text)
                if source_url:
                    st.caption(f"🔗 [Source]({source_url}) · {fetch_date}")

                st.session_state.messages.append({
                    "role":       "assistant",
                    "content":    answer_text,
                    "source_url": source_url,
                    "fetch_date": fetch_date,
                })

            except requests.exceptions.ConnectionError:
                err = "Could not reach the FAQ API. Please make sure it is running (`uvicorn app.api:app`)."
                st.error(err)
            except Exception as e:
                st.error(f"Unexpected error: {e}")

# ---------------------------------------------------------------------------
# Sidebar — fund data snapshot
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("📌 Fund Snapshot")
    st.caption("Live data from last ingestion run")
    try:
        funds_resp = requests.get(f"{API_BASE}/funds", timeout=5)
        funds_resp.raise_for_status()
        funds = funds_resp.json()
        for scheme_name, record in funds.items():
            st.subheader(record.get("display_name", scheme_name))
            st.markdown(f"**NAV:** {record.get('nav', 'N/A')}")
            st.markdown(f"**Min SIP:** {record.get('minimum_sip', 'N/A')}")
            st.markdown(f"**Fund Size:** {record.get('fund_size', 'N/A')}")
            st.markdown(f"**Expense Ratio:** {record.get('expense_ratio', 'N/A')}")
            st.markdown(f"**Rating:** {record.get('rating', 'N/A')} ★")
            st.caption(f"Last updated: {record.get('last_updated', 'N/A')}")
            st.divider()
    except Exception:
        st.caption("Fund data unavailable — run the ingestion pipeline first.")

    st.divider()
    if st.button("🔄 New conversation"):
        try:
            resp = requests.post(f"{API_BASE}/session", timeout=5)
            resp.raise_for_status()
            st.session_state.thread_id = resp.json()["thread_id"]
            st.session_state.messages = []
            st.rerun()
        except Exception as e:
            st.error(f"Failed to create session: {e}")
