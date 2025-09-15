import os
import time
from datetime import datetime, timedelta
import sqlite3
import uuid
import requests
import streamlit as st

DB_PATH = os.getenv("FOCUS_DB_PATH", "data/focus.db")
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

os.makedirs("data", exist_ok=True)

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY, title TEXT, context TEXT, est_minutes INTEGER,
        tag TEXT, priority INTEGER, created_at TEXT, status TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY, task_id TEXT, mode TEXT, duration_min INTEGER,
        start_ts TEXT, end_ts TEXT, energy TEXT, note TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS interruptions(
        id TEXT PRIMARY KEY, session_id TEXT, ts TEXT, content TEXT
    )""")
    conn.commit()
    return conn

conn = db()

def ollama_chat(system_prompt, user_prompt):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"(Coach offline) {e}"

st.set_page_config(page_title="ADHD Focus", page_icon="✅")

st.title("ADHD Focus Control")
st.caption("Quick capture, focused sessions, kind accountability.")

with st.sidebar:
    st.header("Quick Capture")
    title = st.text_input("Task title")
    context = st.text_area("Context or next step")
    est = st.number_input("Estimate (minutes)", 5, 240, 25)
    tag = st.selectbox("Tag", ["Deep Work", "Shallow Work", "Admin", "Outreach", "Personal"])
    prio = st.slider("Priority", 1, 5, 3)
    if st.button("Add Task"):
        tid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",
            (tid, title.strip(), context.strip(), int(est), tag, int(prio),
             datetime.utcnow().isoformat(), "open")
        )
        conn.commit()
        st.success("Task added")

    st.markdown("---")
    st.header("Energy Check")
    energy = st.radio("How do you feel right now?", ["Low", "Medium", "High"], index=1)
    coach_need = st.checkbox("Need a supportive nudge")
    if coach_need:
        sys = "You are a warm, concise ADHD focus coach. Keep messages under 120 words."
        usr = f"Energy: {energy}. Give one short idea to begin a task without perfectionism."
        st.info(ollama_chat(sys, usr))

st.subheader("Start a Focus Session")

# Load open tasks sorted by priority and recency
tasks = conn.execute(
    "SELECT id, title, context, est_minutes, tag, priority FROM tasks WHERE status='open' ORDER BY priority DESC, created_at DESC LIMIT 100"
).fetchall()

task_options = [f"[{t[5]}] {t[1]} • {t[4]} • ~{t[3]}m" for t in tasks]
task_choice = st.selectbox("Pick a task", options=["— Select —"] + task_options)
mode = st.selectbox("Mode", ["Pomodoro 25/5", "Timebox 45", "Timebox 60"])
note = st.text_input("Session intention (optional)")

if st.button("Start Session"):
    if task_choice == "— Select —":
        st.warning("Select a task first")
    else:
        idx = task_options.index(task_choice)
        task_id = tasks[idx][0]
        duration = 25 if mode == "Pomodoro 25/5" else (45 if mode == "Timebox 45" else 60)
        sid = str(uuid.uuid4())
        start = datetime.utcnow()
        conn.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
            (sid, task_id, mode, duration, start.isoformat(), "", energy, note)
        )
        conn.commit()
        st.session_state["active_session"] = {
            "id": sid, "task_id": task_id, "duration": duration,
            "start": start.isoformat(), "mode": mode
        }
        st.success("Session started")

# Active session UI
active = st.session_state.get("active_session")
if active:
    st.markdown("---")
    st.subheader("Active Session")
    start = datetime.fromisoformat(active["start"])
    elapsed = (datetime.utcnow() - start).seconds
    remaining = active["duration"]*60 - elapsed
    remaining = max(0, remaining)
    mins = remaining // 60
    secs = remaining % 60
    st.metric("Time Remaining", f"{mins:02d}:{secs:02d}", delta=None)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Log Distraction"):
            st.session_state["log_distraction"] = True
    with col2:
        if st.button("Coach Prompt"):
            sys = "You are a warm ADHD focus coach. 2 sentences max. Encourage returning to the task."
            usr = "Give a gentle re-focus reminder that reduces shame."
            st.info(ollama_chat(sys, usr))
    with col3:
        if st.button("End Session"):
            end_ts = datetime.utcnow().isoformat()
            conn.execute("UPDATE sessions SET end_ts=? WHERE id=?", (end_ts, active["id"]))
            conn.commit()
            # Mark task done if timebox was 60 and user wants to mark complete
            st.success("Session ended")
            st.session_state.pop("active_session", None)

    if st.session_state.get("log_distraction"):
        with st.form("interrupt_form"):
            txt = st.text_input("What tried to pull your attention?")
            submitted = st.form_submit_button("Save")
            if submitted:
                iid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO interruptions VALUES(?,?,?,?)",
                    (iid, active["id"], datetime.utcnow().isoformat(), txt.strip())
                )
                conn.commit()
                st.session_state["log_distraction"] = False
                st.info("Logged. Return to task.")

st.markdown("---")
st.subheader("Today at a glance")

today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
today_end = datetime.utcnow().replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

today_sessions = conn.execute(
    "SELECT mode, duration_min, start_ts, end_ts, energy FROM sessions "
    "WHERE start_ts BETWEEN ? AND ? ORDER BY start_ts DESC",
    (today_start, today_end)
).fetchall()

if len(today_sessions) == 0:
    st.write("No sessions yet. Start your first one.")
else:
    total_min = sum([s[1] for s in today_sessions])
    st.metric("Total Focus Minutes", f"{total_min} min")
    for s in today_sessions:
        start_local = s[2].replace("T", " ").split(".")[0]
        st.write(f"{start_local} • {s[0]} • {s[1]} min • Energy {s[4]}")
