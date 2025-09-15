# app.py
# ADHD Focus Control App for Streamlit + Ollama + SQLite
# Author: Anthony-ready build

import os
import time
import uuid
import sqlite3
from datetime import datetime, timedelta

import requests
import streamlit as st
import streamlit.components.v1 as components

# -----------------------------
# Configuration
# -----------------------------
DB_PATH = os.getenv("FOCUS_DB_PATH", "data/focus.db")
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
ALARM_AUDIO_URL = os.getenv("ALARM_AUDIO_URL", "")  # optional
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")  # optional

AUTO_BREAK_ENABLED = os.getenv("AUTO_BREAK_ENABLED", "true").lower() == "true"
AUTO_BREAK_MINUTES = int(os.getenv("AUTO_BREAK_MINUTES", "5"))

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

# -----------------------------
# Database
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def migrate(conn):
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY,
        title TEXT,
        context TEXT,
        est_minutes INTEGER,
        tag TEXT,
        priority INTEGER,
        created_at TEXT,
        status TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY,
        task_id TEXT,
        mode TEXT,
        duration_min INTEGER,
        start_ts TEXT,
        end_ts TEXT,
        energy TEXT,
        note TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS interruptions(
        id TEXT PRIMARY KEY,
        session_id TEXT,
        ts TEXT,
        content TEXT
    )
    """)
    conn.commit()

CONN = get_conn()
migrate(CONN)

# -----------------------------
# Helpers
# -----------------------------
def now_utc_iso() -> str:
    return datetime.utcnow().isoformat()

def ollama_chat(system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"(Coach offline) {e}"

def play_completion_signals():
    js = """
    <script>
      const oldTitle = document.title;
      document.title = "Session Done!";
      try { if (navigator.vibrate) navigator.vibrate([300, 150, 300]); } catch(e) {}
      setTimeout(() => { document.title = oldTitle; }, 15000);
    </script>
    """
    html = js
    if ALARM_AUDIO_URL:
        html += f"""
        <audio autoplay>
          <source src="{ALARM_AUDIO_URL}">
        </audio>
        """
    components.html(html, height=0)

def notify_n8n(payload: dict):
    if not N8N_WEBHOOK_URL:
        return
    try:
        requests.post(N8N_WEBHOOK_URL, json=payload, timeout=5)
    except Exception:
        pass

def insert_task(title, context, est, tag, prio):
    tid = str(uuid.uuid4())
    CONN.execute(
        "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",
        (tid, title, context, int(est), tag, int(prio), now_utc_iso(), "open"),
    )
    CONN.commit()
    return tid

def start_session(task_id, mode, duration_min, energy, note):
    sid = str(uuid.uuid4())
    CONN.execute(
        "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
        (sid, task_id, mode, int(duration_min), now_utc_iso(), "", energy, note),
    )
    CONN.commit()
    return sid

def end_session(session_id):
    end_ts = now_utc_iso()
    CONN.execute("UPDATE sessions SET end_ts=? WHERE id=?", (end_ts, session_id))
    CONN.commit()
    return end_ts

def log_interruption(session_id, text):
    iid = str(uuid.uuid4())
    CONN.execute(
        "INSERT INTO interruptions VALUES(?,?,?,?)",
        (iid, session_id, now_utc_iso(), text.strip()),
    )
    CONN.commit()

def get_open_tasks(limit=100):
    return CONN.execute(
        "SELECT id, title, context, est_minutes, tag, priority "
        "FROM tasks WHERE status='open' "
        "ORDER BY priority DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

def get_today_sessions():
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = datetime.utcnow().replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    return CONN.execute(
        "SELECT id, mode, duration_min, start_ts, end_ts, energy "
        "FROM sessions WHERE start_ts BETWEEN ? AND ? ORDER BY start_ts DESC",
        (start, end),
    ).fetchall()

# -----------------------------
# Streamlit App
# -----------------------------
st.set_page_config(page_title="ADHD Focus", page_icon="✅")

# Auto refresh while a timer is active
active = st.session_state.get("active_session")
break_state = st.session_state.get("break_session")
if active or break_state:
    st.autorefresh(interval=1000, key="ticker")

st.title("ADHD Focus Control")
st.caption("Quick capture, focused sessions, kind accountability.")

# -----------------------------
# Sidebar: capture and coach
# -----------------------------
with st.sidebar:
    st.header("Quick Capture")
    with st.form("capture_form", clear_on_submit=True):
        title = st.text_input("Task title")
        context = st.text_area("Context or next step")
        est = st.number_input("Estimate minutes", 5, 240, 25)
        tag = st.selectbox("Tag", ["Deep Work", "Shallow Work", "Admin", "Outreach", "Personal"])
        prio = st.slider("Priority", 1, 5, 3)
        submitted = st.form_submit_button("Add Task")
        if submitted:
            if title.strip():
                insert_task(title.strip(), context.strip(), est, tag, prio)
                st.success("Task added")
            else:
                st.warning("Add a task title")

    st.markdown("---")
    st.header("Energy Check")
    energy = st.radio("How do you feel right now?", ["Low", "Medium", "High"], index=1)

    if st.checkbox("Need a supportive nudge"):
        sys = "You are a warm, concise ADHD focus coach. Keep messages under 120 words."
        usr = f"Energy: {energy}. Give one tiny next action to begin without perfectionism and one sentence of encouragement."
        st.info(ollama_chat(sys, usr))

# -----------------------------
# Start Session
# -----------------------------
st.subheader("Start a Focus Session")
open_tasks = get_open_tasks()
task_labels = [f"[{row['priority']}] {row['title']} • {row['tag']} • ~{row['est_minutes']}m" for row in open_tasks]
selection = st.selectbox("Pick a task", options=["Select a task"] + task_labels)

mode = st.selectbox("Mode", ["Pomodoro 25/5", "Timebox 45", "Timebox 60"])
note = st.text_input("Session intention (optional)")

if st.button("Start Session"):
    if selection == "Select a task":
        st.warning("Select a task first")
    else:
        idx = task_labels.index(selection)
        task_id = open_tasks[idx]["id"]
        duration = 25 if mode == "Pomodoro 25/5" else (45 if mode == "Timebox 45" else 60)
        sid = start_session(task_id, mode, duration, energy, note)
        st.session_state["active_session"] = {
            "id": sid,
            "task_id": task_id,
            "duration": duration,
            "start": now_utc_iso(),
            "mode": mode,
        }
        st.session_state.pop("session_auto_completed", None)
        st.session_state.pop("log_distraction", None)
        st.success("Session started")

# -----------------------------
# Active Session UI
# -----------------------------
def render_session_timer():
    active = st.session_state.get("active_session")
    if not active:
        return

    st.markdown("---")
    st.subheader("Active Session")

    start_dt = datetime.fromisoformat(active["start"])
    total_seconds = active["duration"] * 60
    elapsed = max(0, int((datetime.utcnow() - start_dt).total_seconds()))
    remaining = max(0, total_seconds - elapsed)
    mins, secs = divmod(remaining, 60)
    pct_done = int(100 * min(elapsed, total_seconds) / total_seconds) if total_seconds else 100

    st.progress(min(pct_done, 100))
    st.metric("Time Remaining", f"{mins:02d}:{secs:02d}")

    # Auto complete
    if remaining == 0 and not st.session_state.get("session_auto_completed"):
        end_ts = end_session(active["id"])
        st.session_state["session_auto_completed"] = True
        play_completion_signals()
        st.success("Session complete. Great work.")

        notify_n8n({
            "event": "session_complete",
            "session_id": active["id"],
            "task_id": active["task_id"],
            "duration_min": active["duration"],
            "ended_at": end_ts,
        })

        # Optional auto break for Pomodoro mode
        if AUTO_BREAK_ENABLED and active["mode"] == "Pomodoro 25/5":
            st.session_state["break_session"] = {
                "minutes": AUTO_BREAK_MINUTES,
                "start": now_utc_iso(),
            }
            st.info(f"Break started for {AUTO_BREAK_MINUTES} minutes.")
        # Clear active session
        st.session_state.pop("active_session", None)
        st.stop()

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Log Distraction"):
            st.session_state["log_distraction"] = True
    with col2:
        if st.button("Coach Prompt"):
            sys = "You are a warm ADHD focus coach. Two sentences max. Encourage returning to the task."
            usr = "Give a gentle re-focus reminder that reduces shame."
            st.info(ollama_chat(sys, usr))
    with col3:
        if st.button("End Session Now"):
            end_ts = end_session(active["id"])
            st.success("Session ended")
            notify_n8n({
                "event": "session_ended_early",
                "session_id": active["id"],
                "task_id": active["task_id"],
                "duration_min": active["duration"],
                "ended_at": end_ts,
            })
            st.session_state.pop("active_session", None)
            st.stop()

    if st.session_state.get("log_distraction"):
        with st.form("interrupt_form"):
            txt = st.text_input("What tried to pull your attention?")
            submitted = st.form_submit_button("Save")
            if submitted and txt.strip():
                log_interruption(active["id"], txt)
                st.session_state["log_distraction"] = False
                st.info("Logged. Return to task.")

render_session_timer()

# -----------------------------
# Break Timer
# -----------------------------
def render_break_timer():
    br = st.session_state.get("break_session")
    if not br:
        return
    st.markdown("---")
    st.subheader("Break")

    start_dt = datetime.fromisoformat(br["start"])
    total_seconds = br["minutes"] * 60
    elapsed = max(0, int((datetime.utcnow() - start_dt).total_seconds()))
    remaining = max(0, total_seconds - elapsed)
    mins, secs = divmod(remaining, 60)
    pct_done = int(100 * min(elapsed, total_seconds) / total_seconds) if total_seconds else 100

    st.progress(min(pct_done, 100))
    st.metric("Break Remaining", f"{mins:02d}:{secs:02d}")

    if remaining == 0 and not br.get("auto_completed"):
        play_completion_signals()
        st.success("Break complete. Ready for the next block.")
        st.session_state["break_session"]["auto_completed"] = True
        # Clear break after signal
        st.session_state.pop("break_session", None)
        st.stop()

    if st.button("Skip Break"):
        st.session_state.pop("break_session", None)
        st.info("Break skipped")

render_break_timer()

# -----------------------------
# Today at a glance
# -----------------------------
st.markdown("---")
st.subheader("Today at a glance")

today_sessions = get_today_sessions()
if not today_sessions:
    st.write("No sessions yet. Start your first one.")
else:
    total_planned = sum(s["duration_min"] for s in today_sessions)
    total_completed = 0
    for s in today_sessions:
        if s["end_ts"]:
            # Count full planned minutes as completed for simple metric
            total_completed += s["duration_min"]

    st.metric("Total Focus Minutes (planned)", f"{total_planned} min")
    st.metric("Completed Sessions", f"{sum(1 for s in today_sessions if s['end_ts'])}")

    for s in today_sessions:
        started = s["start_ts"].replace("T", " ").split(".")[0]
        status = "Done" if s["end_ts"] else "Active"
        st.write(f"{started} • {s['mode']} • {s['duration_min']} min • Energy {s['energy']} • {status}")
