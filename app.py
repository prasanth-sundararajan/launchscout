"""LaunchScout Streamlit app: form -> live agent log -> report + downloads.

Run:  streamlit run app.py
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

# CrewAI's console log prints emoji; a Windows console (cp1252) can't encode them
# and CrewAI's own event handlers then error. Force UTF-8 output.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

load_dotenv()

from crew import AGENT_ORDER, OUTPUT_DIR, PRICE_CHECK_PATH, run  # noqa: E402
from report_utils import check_report, markdown_to_pdf  # noqa: E402
import run_limit  # noqa: E402
import ui  # noqa: E402

st.set_page_config(page_title="LaunchScout", page_icon="🔭", layout="wide")
ui.inject_styles()

AGENT_ICONS = {
    "Market Researcher": "🔎",
    "Review Analyst": "💬",
    "Strategy Analyst": "🧭",
    "Report Writer": "📝",
}

# ------------------------------------------------------------------ sidebar
# On a public server set LOCK_SETTINGS=true: visitors then cannot change the model or
# type keys (os.environ is shared by every visitor of the app).
LOCKED = os.getenv("LOCK_SETTINGS", "").lower() in ("1", "true", "yes")

with st.sidebar:
    st.header("Settings")
    default_model = os.getenv("LLM_MODEL") or "anthropic/claude-opus-5-5"
    if LOCKED:
        model = default_model
        st.caption(f"Model: `{model}` (set by the server)")
    else:
        st.caption("Keys are read from `.env`; you can also paste them here for this run only.")
        model = st.text_input("Model (LLM_MODEL)", default_model)
    provider_key = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}.get(
        model.split("/")[0], "ANTHROPIC_API_KEY")
    for var in (provider_key, "SERPER_API_KEY"):
        if not LOCKED and not os.getenv(var, "").strip():
            val = st.text_input(var + (" (optional)" if var == "SERPER_API_KEY" else ""), type="password")
            if val:
                os.environ[var] = val
        st.write(("✅ " if os.getenv(var, "").strip() else ("➖ " if var == "SERPER_API_KEY" else "❌ ")) + var)
    if not os.getenv("SERPER_API_KEY", "").strip():
        st.caption("No Serper key: searching with DuckDuckGo instead.")
    os.environ["LLM_MODEL"] = model
    hierarchical = st.toggle("Hierarchical process (manager agent)", value=False,
                             help="Stretch goal: a manager LLM delegates the work instead of a fixed order.")

    # Daily run limit (run_limit.py): runs used today + what is left
    bud = run_limit.status()
    st.divider()
    st.subheader("Daily runs")
    if bud["limit"]:
        st.progress(min(bud["used"] / bud["limit"], 1.0))
        st.write(f"**{bud['used']}** of **{bud['limit']}** runs used today")
        st.caption(f"{bud['runs_left']} run(s) left today. Resets at midnight (India time).")
    else:
        st.caption(f"No daily limit (MAX_RUNS_PER_DAY=0). {bud['used']} run(s) today.")

# --------------------------------------------------------------------- form
ui.hero()

if not bud["can_start"]:
    st.warning(f"**Daily limit reached.** All {bud['limit']} runs for today have been used. "
               "New runs open again at midnight (India time).")

with st.form("idea"):
    c1, c2 = st.columns(2)
    idea = c1.text_input("Business idea", "Healthy millet meal boxes delivered for lunch")
    city = c2.text_input("City", "Coimbatore")
    target = c1.text_input("Target customer", "Office workers, age 25 to 40")
    known = c2.text_input("Known competitors (optional, comma separated)", "")
    submitted = st.form_submit_button("Run research crew", type="primary",
                                      disabled=not bud["can_start"])


def describe_step(step) -> str:
    """Turn a CrewAI step object (AgentAction / AgentFinish / ToolResult) into one log line."""
    if isinstance(step, str):  # already formatted (tool events from crew.run)
        return step
    tool = getattr(step, "tool", None)
    if tool:
        inp = str(getattr(step, "tool_input", ""))[:160]
        res = str(getattr(step, "result", "") or "").strip().replace("\n", " ")
        line = f"🔧 **{tool}** `{inp}`"
        return line + (f"\n\n> {res[:220]}…" if res else "")
    out = getattr(step, "output", None)
    if out is not None:
        return "✅ Finished its answer: " + str(out).strip().replace("\n", " ")[:260] + "…"
    res = getattr(step, "result", None)
    if res is not None:
        return "📄 Tool result: " + str(res).strip().replace("\n", " ")[:220] + "…"
    return str(step)[:260]


def run_crew_with_live_log(inputs: dict[str, str]) -> str | None:
    events: queue.Queue = queue.Queue()
    holder: dict = {}

    def on_step(role, step):
        events.put(("step", role, describe_step(step)))

    def on_task(output):
        events.put(("task", getattr(output, "agent", "?"), str(getattr(output, "raw", ""))))

    def worker():
        try:
            holder["report"] = run(inputs, step_callback=on_step,
                                   task_callback=on_task, hierarchical=hierarchical)
        except Exception as exc:  # surfaced in the UI below
            holder["error"] = exc
        finally:
            events.put(("end", None, None))

    threading.Thread(target=worker, daemon=True).start()

    state = {a: "waiting" for a in AGENT_ORDER}
    if not hierarchical:
        state[AGENT_ORDER[0]] = "working"
    cols = st.columns(4)
    cards = [c.empty() for c in cols]
    log_box = st.container(height=420, border=True)
    started = time.time()
    timer = st.empty()

    def draw_cards():
        for card, agent in zip(cards, AGENT_ORDER):
            card.markdown(ui.agent_card(AGENT_ICONS[agent], agent, state[agent]),
                          unsafe_allow_html=True)

    draw_cards()
    done = False
    while not done:
        try:
            kind, role, payload = events.get(timeout=0.5)
        except queue.Empty:
            timer.caption(f"Running for {int(time.time() - started)} s")
            continue
        if kind == "end":
            done = True
        elif kind == "step":
            if role in state and state[role] == "waiting":
                state[role] = "working"
            log_box.markdown(f"**{AGENT_ICONS.get(role, '🤖')} {role}** — {payload}")
        elif kind == "task":
            role = role.strip()
            if role in state:
                state[role] = "done"
                if not hierarchical:
                    i = AGENT_ORDER.index(role)
                    if i + 1 < len(AGENT_ORDER):
                        state[AGENT_ORDER[i + 1]] = "working"
            with log_box.expander(f"📦 {role} handed over its work"):
                st.markdown(payload)
        draw_cards()
    timer.caption(f"Finished in {int(time.time() - started)} s")

    if "error" in holder:
        if isinstance(holder["error"], run_limit.RunLimitReached):
            st.warning(str(holder["error"]))
        else:
            st.error(f"The crew stopped with an error: {holder['error']}")
        return None
    report = holder.get("report", "")  # crew.run already saved output/report.md
    # Keep a dated copy of every run (base for the "weekly re-run" stretch goal).
    runs = OUTPUT_DIR / "runs"
    runs.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{inputs['idea']}-{inputs['city']}".lower()).strip("-")[:30]
    try:
        (runs / f"{slug}-{datetime.now():%Y%m%d-%H%M}.md").write_text(report, encoding="utf-8")
    except OSError:  # e.g. Windows path-length limit; the main report is already saved
        pass
    return report


if submitted:
    missing = [] if os.getenv(provider_key, "").strip() else [provider_key]
    if not idea.strip() or not city.strip() or not target.strip():
        st.warning("Please fill in the business idea, city and target customer.")
    elif missing:
        st.warning("Missing API key(s): " + ", ".join(missing))
    else:
        st.subheader("Live progress")
        report = run_crew_with_live_log(
            {"idea": idea.strip(), "city": city.strip(),
             "target_customer": target.strip(), "known_competitors": known.strip()}
        )
        if report:
            st.session_state["report"] = report
            try:
                st.session_state["price_check"] = json.loads(
                    PRICE_CHECK_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                st.session_state["price_check"] = None

# ------------------------------------------------------------------- report
report = st.session_state.get("report")
if report:
    st.divider()
    st.subheader("Report")
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button("⬇️ Markdown", report, file_name="report.md", mime="text/markdown")
    try:
        d2.download_button("⬇️ PDF", markdown_to_pdf(report), file_name="report.pdf",
                           mime="application/pdf")
    except Exception as exc:
        d2.caption(f"PDF unavailable: {exc}")

    with st.expander("Quality checklist", expanded=True):
        for name, ok, detail in check_report(report):
            st.write(f"{'✅' if ok else '⚠️'} **{name}** — {detail}")
        pc = st.session_state.get("price_check")
        if pc:  # prices checked against the pages the agents read during this run
            priced = {n: r for n, r in pc.items() if r["amounts"]}
            unbacked = [f"{n} (₹{', ₹'.join(r['missing'])})" for n, r in priced.items() if r["missing"]]
            st.write(f"{'⚠️' if unbacked else '✅'} **Prices found in pages the agents read** — "
                     + (f"not found: {'; '.join(unbacked)}" if unbacked
                        else f"{len(priced)}/{len(priced)} prices backed"))

    st.markdown(report)
