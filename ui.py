"""LaunchScout UI: dark-blue design system with a light-blue accent.

Tokens match .streamlit/config.toml. Everything here is presentation only; app.py
keeps the logic and calls inject_styles(), hero() and agent_card().
"""

from __future__ import annotations

import html

import streamlit as st

# Design tokens
NAVY_950 = "#071426"   # page background
NAVY_900 = "#0B1F3A"   # sidebar, raised panels
NAVY_850 = "#0F2747"   # inputs, expanders
NAVY_800 = "#12305A"   # card hover, table header
LINE = "#1E3A66"       # borders and dividers
SKY_400 = "#38BDF8"    # primary accent (light blue)
SKY_300 = "#7DD3FC"    # links, done state
SKY_100 = "#E0F2FE"    # accent text on dark
TEXT = "#E6F0FF"
MUTED = "#9FB3D1"

AGENT_BLURB = {
    "Market Researcher": "Finds competitors, prices and channels",
    "Review Analyst": "Mines reviews for likes and complaints",
    "Strategy Analyst": "Builds the matrix, gap and positioning",
    "Report Writer": "Writes the sourced founder report",
}

STEPS = ["Idea input", "Research", "Reviews", "Strategy", "Write", "Deliver"]

_CSS = f"""
<style>
:root {{
  --ls-navy-950:{NAVY_950}; --ls-navy-900:{NAVY_900}; --ls-navy-850:{NAVY_850};
  --ls-navy-800:{NAVY_800}; --ls-line:{LINE}; --ls-sky-400:{SKY_400};
  --ls-sky-300:{SKY_300}; --ls-sky-100:{SKY_100}; --ls-text:{TEXT}; --ls-muted:{MUTED};
}}
/* page: soft light-blue glow from the top */
[data-testid="stAppViewContainer"] {{
  background: radial-gradient(1200px 500px at 15% -10%, rgba(56,189,248,.14), transparent 60%),
              radial-gradient(900px 400px at 100% 0%, rgba(30,58,102,.55), transparent 60%),
              var(--ls-navy-950);
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stSidebar"] {{ border-right: 1px solid var(--ls-line); }}
.block-container {{ padding-top: 2.2rem; max-width: 1180px; }}
h1, h2, h3 {{ letter-spacing: -.01em; }}
h2, h3 {{ color: var(--ls-sky-100); }}

/* hero */
.ls-hero {{
  border: 1px solid var(--ls-line); border-radius: 18px; padding: 28px 30px 22px;
  background: linear-gradient(135deg, rgba(56,189,248,.16) 0%, rgba(15,39,71,.92) 45%, var(--ls-navy-900) 100%);
  box-shadow: 0 18px 40px -24px rgba(56,189,248,.45);
  margin-bottom: 18px;
}}
.ls-eyebrow {{
  display:inline-block; font-size:.74rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
  color: var(--ls-navy-950); background: var(--ls-sky-400); border-radius: 999px; padding: 3px 10px;
}}
.ls-hero h1 {{ font-size: 2.35rem; margin: .45rem 0 .25rem; color: #fff; padding: 0; }}
.ls-hero p {{ color: var(--ls-muted); font-size: 1.02rem; margin: 0 0 16px; max-width: 760px; }}
.ls-steps {{ display:flex; flex-wrap:wrap; gap:8px; }}
.ls-step {{
  font-size:.82rem; color: var(--ls-sky-100); border:1px solid var(--ls-line);
  background: rgba(7,20,38,.55); border-radius: 999px; padding: 5px 12px;
}}
.ls-step b {{ color: var(--ls-sky-400); margin-right: 6px; font-variant-numeric: tabular-nums; }}

/* form card */
[data-testid="stForm"] {{
  background: rgba(11,31,58,.78); border: 1px solid var(--ls-line) !important; border-radius: 16px;
  padding: 18px 20px 14px;
}}
[data-testid="stForm"] label p {{ color: var(--ls-sky-100); font-weight: 600; }}

/* primary button: light blue with dark text for contrast */
button[kind="primary"], button[kind="primaryFormSubmit"] {{
  background: linear-gradient(180deg, #5CCBFA, var(--ls-sky-400)) !important;
  color: var(--ls-navy-950) !important; border: 0 !important; font-weight: 700 !important;
  box-shadow: 0 8px 22px -10px rgba(56,189,248,.8);
}}
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {{ filter: brightness(1.07); }}
button[kind="secondary"] {{ border-color: var(--ls-line) !important; }}
button[kind="secondary"]:hover {{ border-color: var(--ls-sky-400) !important; color: var(--ls-sky-300) !important; }}

/* agent status cards */
.ls-agent {{
  border:1px solid var(--ls-line); border-radius: 14px; padding: 14px 14px 12px; min-height: 132px;
  background: var(--ls-navy-900); transition: border-color .2s, box-shadow .2s;
}}
.ls-agent .ls-ico {{ font-size: 1.6rem; line-height: 1; }}
.ls-agent .ls-name {{ font-weight: 700; color: #fff; margin-top: 8px; }}
.ls-agent .ls-blurb {{ color: var(--ls-muted); font-size: .8rem; margin: 2px 0 10px; min-height: 2.2em; }}
.ls-pill {{ display:inline-flex; align-items:center; gap:6px; font-size:.78rem; font-weight:700;
  border-radius:999px; padding: 3px 10px; }}
.ls-dot {{ width:8px; height:8px; border-radius:50%; display:inline-block; }}
.ls-waiting .ls-pill {{ color: var(--ls-muted); background: rgba(159,179,209,.10); }}
.ls-waiting .ls-dot {{ background: var(--ls-muted); }}
.ls-working {{ border-color: var(--ls-sky-400); box-shadow: 0 0 0 3px rgba(56,189,248,.14); }}
.ls-working .ls-pill {{ color: var(--ls-navy-950); background: var(--ls-sky-400); }}
.ls-working .ls-dot {{ background: var(--ls-navy-950); animation: ls-pulse 1.1s ease-in-out infinite; }}
.ls-done .ls-pill {{ color: var(--ls-sky-300); background: rgba(125,211,252,.12); }}
.ls-done .ls-dot {{ background: var(--ls-sky-300); }}
@keyframes ls-pulse {{ 0%,100% {{ opacity: 1; transform: scale(1); }} 50% {{ opacity: .35; transform: scale(.7); }} }}
@media (prefers-reduced-motion: reduce) {{ .ls-working .ls-dot {{ animation: none; }} }}

/* bordered containers (live log), expanders */
[data-testid="stVerticalBlockBorderWrapper"] {{ background: rgba(11,31,58,.65); border-color: var(--ls-line) !important; }}
[data-testid="stExpander"] details {{ border-color: var(--ls-line) !important; background: rgba(11,31,58,.55); border-radius: 12px; }}
[data-testid="stExpander"] summary:hover p {{ color: var(--ls-sky-300); }}
code {{ color: var(--ls-sky-100) !important; background: rgba(56,189,248,.10) !important; }}

/* report tables */
[data-testid="stMarkdownContainer"] table {{ border-collapse: separate; border-spacing: 0; width: 100%;
  border: 1px solid var(--ls-line); border-radius: 12px; overflow: hidden; }}
[data-testid="stMarkdownContainer"] th {{ background: var(--ls-navy-800); color: var(--ls-sky-100);
  font-weight: 700; border-color: var(--ls-line) !important; }}
[data-testid="stMarkdownContainer"] td {{ border-color: var(--ls-line) !important; vertical-align: top; }}
[data-testid="stMarkdownContainer"] tr:nth-child(even) td {{ background: rgba(15,39,71,.45); }}
[data-testid="stMarkdownContainer"] a {{ color: var(--ls-sky-300); }}
hr {{ border-color: var(--ls-line) !important; }}
</style>
"""


def inject_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def hero() -> None:
    steps = "".join(f'<span class="ls-step"><b>{i:02d}</b>{s}</span>' for i, s in enumerate(STEPS, 1))
    st.markdown(
        f"""<div class="ls-hero">
  <span class="ls-eyebrow">CrewAI · 4 agents</span>
  <h1>🔭 LaunchScout</h1>
  <p>A crew of four AI agents researches your market and writes a competitor report with
  prices, gaps and a launch plan. Every price and claim links back to its source.</p>
  <div class="ls-steps">{steps}</div>
</div>""",
        unsafe_allow_html=True,
    )


def agent_card(icon: str, name: str, state: str) -> str:
    """HTML for one agent status card; state is waiting / working / done."""
    label = {"waiting": "waiting", "working": "working…", "done": "done"}[state]
    return (
        f'<div class="ls-agent ls-{state}"><div class="ls-ico">{icon}</div>'
        f'<div class="ls-name">{html.escape(name)}</div>'
        f'<div class="ls-blurb">{html.escape(AGENT_BLURB.get(name, ""))}</div>'
        f'<span class="ls-pill"><span class="ls-dot"></span>{label}</span></div>'
    )
