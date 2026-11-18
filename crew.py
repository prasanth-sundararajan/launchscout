"""LaunchScout crew: four agents that research a market and write a competitor report.

Nothing here is specific to one idea or city: every task reads the form values
through CrewAI's {placeholder} interpolation, so the same code works for any input.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from crewai import LLM, Agent, Crew, Process, Task
from report_utils import price_guardrail
from tools import FileWriterTool, ScrapeWebsiteTool, make_review_tools, make_search_tool

OUTPUT_DIR = Path(__file__).parent / "output"
REPORT_PATH = OUTPUT_DIR / "report.md"
PRICE_CHECK_PATH = OUTPUT_DIR / "price_check.json"

# Order matters: the app uses it to show which agent is working.
AGENT_ORDER = ["Market Researcher", "Review Analyst", "Strategy Analyst", "Report Writer"]


def make_llm() -> LLM:
    """Model is configurable through LLM_MODEL (any LiteLLM-style id CrewAI accepts).

    Default is Claude Opus 5.5. max_tokens is set generously because the model's
    thinking tokens count against it and the final report is long.
    """
    model = os.getenv("LLM_MODEL") or "anthropic/claude-opus-5-5"
    return LLM(model=model, max_tokens=int(os.getenv("LLM_MAX_TOKENS") or "16000"))


def build_crew(
    step_callback: Callable[[str, Any], None] | None = None,
    task_callback: Callable[[Any], None] | None = None,
    hierarchical: bool = False,
    evidence: list[str] | None = None,
) -> Crew:
    """step_callback(agent_role, step) fires on every agent step, so the UI always
    knows which agent produced it (also in hierarchical mode)."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    llm = make_llm()

    def cb(role: str):
        if step_callback is None:
            return None
        return lambda step: step_callback(role, step)

    search = make_search_tool()
    scrape = ScrapeWebsiteTool()
    file_writer = FileWriterTool()

    # ---------------------------------------------------------------- agents
    researcher = Agent(
        role="Market Researcher",
        goal=(
            "Find 3 to 5 real, currently operating competitors for '{idea}' in {city}, "
            "with their prices, offers and sales channels, each backed by a source URL."
        ),
        backstory=(
            "You spent eight years as a field researcher for a retail consultancy in India. "
            "You never trust a number you cannot link to, you check delivery apps, Google "
            "Maps listings, Instagram pages and company websites, and you write down the "
            "exact URL where you saw every price."
        ),
        tools=[search, scrape],
        llm=llm,
        step_callback=cb("Market Researcher"),
        max_iter=15,
        verbose=True,
    )

    review_analyst = Agent(
        role="Review Analyst",
        goal=(
            "For each competitor found, learn what customers praise and what they complain "
            "about, quoting real reviews with their source URLs."
        ),
        backstory=(
            "You ran customer-experience research for a food-delivery startup. You read "
            "hundreds of Google, Zomato, Swiggy, Justdial and Reddit reviews a week and can "
            "spot the complaint that keeps coming back. You separate one angry review from "
            "a real pattern and say which is which."
        ),
        tools=[search, scrape, *make_review_tools()],
        llm=llm,
        step_callback=cb("Review Analyst"),
        max_iter=15,
        verbose=True,
    )

    strategist = Agent(
        role="Strategy Analyst",
        goal=(
            "Turn the research into a competitor comparison matrix, identify the single "
            "clearest market gap, and recommend a price and positioning for '{idea}' "
            "aimed at {target_customer} in {city}."
        ),
        backstory=(
            "You are a former strategy consultant who now advises first-time founders. "
            "You only reason from evidence your team handed you, you never invent "
            "competitors or numbers, and you are blunt about where the opportunity is "
            "and where it is not."
        ),
        tools=[],
        llm=llm,
        step_callback=cb("Strategy Analyst"),
        verbose=True,
    )

    writer = Agent(
        role="Report Writer",
        goal=(
            "Write a clean, founder-friendly competitor report in Markdown with every "
            "price and claim linked to its source, and save it as report.md."
        ),
        backstory=(
            "You write investor and founder briefs. You use plain words, short sentences "
            "and tables, and you keep every citation the researchers gave you. A founder "
            "should be able to read your report in ten minutes and act on it the same day."
        ),
        tools=[file_writer],
        llm=llm,
        step_callback=cb("Report Writer"),
        verbose=True,
    )

    # ----------------------------------------------------------------- tasks
    research_task = Task(
        description=(
            "Research the market for this business idea.\n"
            "- Business idea: {idea}\n- City: {city}\n- Target customer: {target_customer}\n"
            "- Known competitors (may be empty): {known_competitors}\n\n"
            "Use web search and scrape competitor pages to find 3 to 5 REAL businesses "
            "that already sell something a {target_customer} customer would choose instead "
            "of {idea} in {city}. Include any known competitors listed above. Prefer local "
            "players; add a national player only if it actually serves {city}.\n"
            "For each competitor record: name, what they sell, price (per unit / per meal / "
            "per month as listed, in local currency), current offers or subscriptions, sales "
            "channels (own website, app, Swiggy, Zomato, WhatsApp, Instagram, store...), and "
            "the URL of every page where you found a fact. A competitor only counts if you found "
            "a price for it: if a site hides its prices, check its Swiggy/Zomato listing, Google "
            "results or Instagram, and if you still find no price, replace it with another "
            "competitor that lists one. Copy each price exactly as the page shows it. Never "
            "estimate, infer or guess a price from 'market standards': a competitor whose price "
            "you could only guess does not count. Never invent a business, a price or a URL."
        ),
        expected_output=(
            "A Markdown section titled '## Competitors' containing:\n"
            "1. A table with columns: Competitor | Offering | Price | Offers / Subscription | "
            "Sales channels | Source URLs. At least 3 rows, each with a price (or 'not listed') "
            "and at least one full https:// source URL.\n"
            "2. Below the table, one short paragraph per competitor with any extra detail.\n"
            "3. A '### Research notes' list of anything you could not verify."
        ),
        agent=researcher,
        guardrail=price_guardrail(evidence=evidence),  # sends guessed/unbacked prices back
        guardrail_max_retries=3,
    )

    review_task = Task(
        description=(
            "For every competitor in the research, first use the 'Google Maps reviews' tool "
            "(if you have it) with the competitor name and {city}: its lowest-rated reviews "
            "are the best source of real complaints, and each review has its own link to "
            "cite. Check the Maps listing is the same business. Then search for more customer "
            "reviews and feedback "
            "(Google Maps, Zomato, Swiggy, Justdial, Instagram comments, Reddit, blogs). "
            "Scrape the pages when needed. For each competitor find what customers like and "
            "what they complain about. Mark a point as a 'pattern' only if it appears in 2 or "
            "more reviews. Cite the page where the review itself appears (Google Maps, "
            "Zomato, Justdial, Reddit thread...), not the competitor's own homepage. Then list the complaints that show up across several competitors. "
            "If you cannot find reviews for a competitor, say so instead of guessing. "
            "Every like or complaint must carry the full https:// URL of the exact page you "
            "read it on; a point you cannot link to a URL must be left out, never cited as "
            "just 'Instagram' or 'Swiggy reviews'."
        ),
        expected_output=(
            "A Markdown section titled '## Customer Feedback' with one '### <Competitor>' "
            "subsection per competitor, each containing:\n"
            "- 'Likes:' 2 to 4 bullets\n- 'Complaints:' 2 to 4 bullets\n"
            "Each bullet ends with its source in the form ([source](https://...)) and is "
            "tagged (pattern) or (single review).\n"
            "Then a '### Common complaints across competitors' list naming which competitors "
            "each complaint applies to."
        ),
        agent=review_analyst,
        context=[research_task],
    )

    strategy_task = Task(
        description=(
            "Using ONLY the competitor research and customer feedback you were given, "
            "build the strategy for launching '{idea}' in {city} for {target_customer}.\n"
            "1. Build a comparison matrix of all competitors: price, channel, main strength, "
            "main weakness (taken from the complaints).\n"
            "2. Identify the ONE clearest market gap: a need customers have that no competitor "
            "serves well. Explain the evidence in 2 to 3 sentences, citing the competitors.\n"
            "3. Recommend a price point and justify it against the competitor prices.\n"
            "4. Write a one-line positioning statement that includes the price.\n"
            "5. Name the 2 or 3 biggest risks."
        ),
        expected_output=(
            "Markdown with these exact headings:\n"
            "'## Comparison Matrix' - table: Competitor | Price | Channel | Strength | Weakness\n"
            "'## Market Gap' - 2 to 4 sentences\n"
            "'## Recommended Price' - the price and a short justification\n"
            "'## Positioning' - one quoted line that includes the price\n"
            "'## Risks' - 2 to 3 bullets\n"
            "Keep the source URLs from the research attached to any number you repeat."
        ),
        agent=strategist,
        context=[research_task, review_task],
    )

    report_task = Task(
        description=(
            "Write the final founder report for '{idea}' in {city} (target: {target_customer}) "
            "from the research, feedback and strategy you were given. Do not add competitors, "
            "prices or claims that are not in that work.\n"
            "Use only prices the research found on a page. Never estimate or infer a price; "
            "leave out a competitor that has no found price. Every Sources entry must contain "
            "a full https:// URL: drop any claim whose source has no URL rather than citing a "
            "site name. Write each section heading once; never repeat a heading.\n"
            "Number every distinct source URL as [1], [2], ... and put the matching number "
            "after each price or claim in the text and tables. Every row of the Key Competitors "
            "table must contain at least one [n], and every complaint bullet must end with [n]. "
            "Each number appears exactly once in Sources; if one competitor has several URLs "
            "(website, review page...), give each URL its own number. Write a practical 30-day launch "
            "plan with one paragraph or 3 to 4 bullets per week, specific to {city} and "
            "{target_customer}.\n"
            "When the report is written, use the file writer tool to save it with "
            "filename 'report.md' in directory '" + str(OUTPUT_DIR).replace("\\", "/") + "' "
            "(overwrite: true). Your final answer must be the full report itself."
        ),
        expected_output=(
            "A complete Markdown report that starts with "
            "'# Competitor Report: {idea}, {city}' and has exactly these six sections in order:\n"
            "'## 1. Key Competitors' - table: Competitor | Price | Channel | Strength | Weakness, "
            "each price followed by its [n] source number\n"
            "'## 2. What Customers Complain About' - bullets naming the competitors affected, with [n]\n"
            "'## 3. Market Gap' - short paragraph\n"
            "'## 4. Recommended Positioning' - the one-line offer with price, plus 2 to 3 "
            "sentences of reasoning\n"
            "'## 5. 30-Day Launch Plan' - Week 1, Week 2, Week 3, Week 4\n"
            "'## Sources' - numbered list '[n] <title> - <full https URL>' with every URL used.\n"
            "No text before the title and nothing after Sources."
        ),
        agent=writer,
        context=[research_task, review_task, strategy_task],
        guardrail=price_guardrail(can_search=False, evidence=evidence),
        # CrewAI saves the writer's raw final answer here. It is kept apart from
        # report.md because some models answer only "report saved" after using the
        # file tool; run() picks the real report and writes report.md.
        output_file=str(OUTPUT_DIR / "report_task_output.md"),
        guardrail_max_retries=3,
    )

    crew_kwargs: dict[str, Any] = dict(
        agents=[researcher, review_analyst, strategist, writer],
        tasks=[research_task, review_task, strategy_task, report_task],
        verbose=True,
        task_callback=task_callback,
    )
    if hierarchical:
        # Stretch goal: a manager LLM plans and delegates instead of a fixed order.
        crew_kwargs.update(process=Process.hierarchical, manager_llm=make_llm())
    else:
        crew_kwargs.update(process=Process.sequential)
    return Crew(**crew_kwargs)


def _attach_live_events(step_callback: Callable[[str, Any], None]) -> list[tuple[Any, Any]]:
    """Forward CrewAI tool events to step_callback. CrewAI 1.x runs tools through
    native function calling, which does not reach Agent.step_callback, so the live
    log listens on the event bus instead."""
    from crewai.events import (
        ToolUsageErrorEvent,
        ToolUsageFinishedEvent,
        ToolUsageStartedEvent,
        crewai_event_bus,
    )

    def role_of(event: Any) -> str:
        return (getattr(event, "agent_role", None) or "Agent").strip()

    def started(source: Any, event: Any) -> None:
        args = event.tool_args if isinstance(event.tool_args, str) else ", ".join(
            f"{k}={v}" for k, v in event.tool_args.items() if k != "content")
        step_callback(role_of(event), f"🔧 **{event.tool_name}** `{args[:160]}`")

    def finished(source: Any, event: Any) -> None:
        out = " ".join(str(event.output).split())[:220]
        step_callback(role_of(event), f"📄 {event.tool_name} returned: {out}…")

    def errored(source: Any, event: Any) -> None:
        step_callback(role_of(event), f"⚠️ {event.tool_name} failed: {str(event.error)[:200]}")

    pairs = [(ToolUsageStartedEvent, started), (ToolUsageFinishedEvent, finished),
             (ToolUsageErrorEvent, errored)]
    for event_type, handler in pairs:
        crewai_event_bus.register_handler(event_type, handler)
    return pairs


def _collect_evidence(evidence: list[str]) -> list[tuple[Any, Any]]:
    """Keep the text of every search result and page the agents read this run, so
    prices can be checked against it."""
    from crewai.events import ToolUsageFinishedEvent, crewai_event_bus

    def finished(source: Any, event: Any) -> None:
        if "file" not in str(event.tool_name).lower():  # skip the report writer's own file
            evidence.append(str(event.output))

    crewai_event_bus.register_handler(ToolUsageFinishedEvent, finished)
    return [(ToolUsageFinishedEvent, finished)]


def _pick_report(final_answer: str, started_at: float) -> str:
    """The writer both saves report.md with the file tool and returns the report.
    Weaker models sometimes return only 'report saved'; keep whichever is the report."""
    candidates = [final_answer]
    if REPORT_PATH.exists() and REPORT_PATH.stat().st_mtime >= started_at:
        candidates.append(REPORT_PATH.read_text(encoding="utf-8"))
    cleaned = [re.sub(r"^```(?:markdown)?\s*|\s*```$", "", c.strip()) for c in candidates]
    return max(cleaned, key=lambda c: ("## Sources" in c or "## sources" in c.lower(), len(c)))


def run(inputs: dict[str, str], step_callback: Callable[[str, Any], None] | None = None,
        **kwargs: Any) -> str:
    """Run the crew, save output/report.md and return the final report Markdown."""
    import time

    inputs = {**inputs, "known_competitors": inputs.get("known_competitors") or "none given"}
    started_at = time.time()
    evidence: list[str] = []
    pairs = _collect_evidence(evidence)
    pairs += _attach_live_events(step_callback) if step_callback else []
    try:
        result = build_crew(step_callback=step_callback, evidence=evidence,
                            **kwargs).kickoff(inputs=inputs)
        final_answer = str(getattr(result, "raw", result))
    except Exception:
        # Some models save the report with the file tool and then return an empty
        # final message, which CrewAI raises on. Keep the saved report in that case.
        saved = REPORT_PATH.read_text(encoding="utf-8") if (
            REPORT_PATH.exists() and REPORT_PATH.stat().st_mtime >= started_at) else ""
        if "## sources" not in saved.lower():
            raise
        final_answer = ""
    finally:
        from crewai.events import crewai_event_bus

        for event_type, handler in pairs:
            crewai_event_bus.off(event_type, handler)
    from report_utils import clean_report, verify_prices

    report = clean_report(_pick_report(final_answer, started_at))
    REPORT_PATH.write_text(report, encoding="utf-8")
    # Record which prices are backed by pages read this run; the app shows it.
    PRICE_CHECK_PATH.write_text(json.dumps(verify_prices(report, evidence), indent=2,
                                           ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":  # quick CLI run with the sample input
    from dotenv import load_dotenv

    load_dotenv()
    print(
        run(
            {
                "idea": "Healthy millet meal boxes delivered for lunch",
                "city": "Coimbatore",
                "target_customer": "Office workers, age 25 to 40",
                "known_competitors": "",
            }
        )
    )
