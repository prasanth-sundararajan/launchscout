"""Capture one screenshot per output requirement of a running LaunchScout app.

Runs a real crew (it spends API credits), then saves into docs/screenshots/ and
the downloaded report into docs/sample/.

    # needs Playwright + Chromium; easiest in its Docker image:
    docker run --rm -v "${PWD}:/work" -w /work mcr.microsoft.com/playwright/python:v1.49.0-noble \
        python scripts/take_screenshots.py --url https://www.kowsalyagroups.com/techstack/launchscout/
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
SAMPLE = ROOT / "docs" / "sample"

FORM = {
    "Business idea": "Healthy millet meal boxes delivered for lunch",
    "City": "Coimbatore",
    "Target customer": "Office workers, age 25 to 40",
}


def shot(page: Page, name: str) -> None:
    page.wait_for_timeout(800)
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    print("saved", name, flush=True)


def to_top(page: Page, selector: str, offset: int = 70) -> None:
    """Scroll Streamlit's main pane so `selector` sits near the top of the viewport."""
    page.locator(selector).first.evaluate(
        "(el, off) => { el.scrollIntoView({block: 'start'});"
        " const m = document.querySelector('[data-testid=\"stMain\"]') || document.scrollingElement;"
        " m.scrollBy(0, -off); }", offset)
    page.wait_for_timeout(600)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8510/")
    ap.add_argument("--timeout-min", type=int, default=15)
    args = ap.parse_args()
    SHOTS.mkdir(parents=True, exist_ok=True)
    SAMPLE.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 900}, device_scale_factor=1,
                                accept_downloads=True)
        page.goto(args.url, wait_until="networkidle")
        page.get_by_text("Run research crew").wait_for(timeout=60_000)

        # 1. Input form
        for label, value in FORM.items():
            box = page.get_by_label(label, exact=True)
            box.fill(value)
        page.get_by_label("Known competitors (optional, comma separated)").fill("")
        shot(page, "01_input_form")

        # 2. Live progress: which agent is working and what it found
        page.get_by_role("button", name="Run research crew").click()
        page.get_by_text("Live progress").wait_for(timeout=60_000)
        page.get_by_text("search").first.wait_for(timeout=120_000)
        page.wait_for_timeout(25_000)
        to_top(page, "h2:has-text('Live progress')")
        shot(page, "02_live_progress_researcher")
        page.get_by_text("Review Analyst —").first.wait_for(timeout=args.timeout_min * 60_000)
        page.wait_for_timeout(20_000)
        to_top(page, "h2:has-text('Live progress')")
        shot(page, "03_live_progress_review_analyst")

        # Wait for the finished report
        page.get_by_text("Quality checklist").wait_for(timeout=args.timeout_min * 60_000)
        page.wait_for_timeout(2_000)

        # 4. Agents handing work to the next agent (task context)
        handover = page.get_by_text("Market Researcher handed over its work").first
        handover.click()
        page.wait_for_timeout(800)
        handover.evaluate("el => el.scrollIntoView({block: 'start'})")
        shot(page, "04_agent_handover")
        handover.click()

        # 5. Report header: downloads + quality checklist
        to_top(page, "h3:has-text('Report'), h2:has-text('Report')")
        shot(page, "05_report_downloads_and_checklist")

        # 6-10. The six report sections
        sections = [
            ("06_key_competitors", "1. Key Competitors"),
            ("07_customer_complaints", "2. What Customers Complain About"),
            ("08_market_gap_and_positioning", "3. Market Gap"),
            ("09_launch_plan", "5. 30-Day Launch Plan"),
            ("10_sources", "Sources"),
        ]
        for name, heading in sections:
            to_top(page, f"h2:has-text('{heading}')")
            shot(page, name)

        # Downloads: Markdown and PDF
        for label, fname in (("Markdown", "report.md"), ("PDF", "report.pdf")):
            with page.expect_download() as dl:
                page.get_by_role("button", name=label).click()
            dl.value.save_as(str(SAMPLE / fname))
            print("downloaded", fname, flush=True)

        browser.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"done in {int(time.time() - t0)} s")
