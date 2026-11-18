"""Helpers: Markdown -> PDF, and a checker for the 'Done checklist'."""

from __future__ import annotations

import io
import re

import markdown
from xhtml2pdf import pisa

PDF_CSS = """
@page { size: A4; margin: 1.8cm; }
body { font-family: Helvetica; font-size: 10pt; line-height: 1.4; color: #222; }
h1 { font-size: 18pt; color: #1b3a5c; border-bottom: 2px solid #1b3a5c; padding-bottom: 4px; }
h2 { font-size: 13pt; color: #1b3a5c; margin-top: 14px; }
h3 { font-size: 11pt; }
table { width: 100%; margin: 6px 0; }
th { background: #1b3a5c; color: #fff; padding: 4px; font-size: 9pt; text-align: left; }
td { border: 1px solid #ccc; padding: 4px; font-size: 9pt; vertical-align: top; }
a { color: #1a5fb4; }
code { font-family: Courier; }
"""


def markdown_to_pdf(md_text: str) -> bytes:
    # The built-in PDF fonts have no rupee glyph; spell it out so it never renders as a box.
    md_text = md_text.replace("₹", "Rs ")
    body = markdown.markdown(md_text, extensions=["tables", "sane_lists"])
    html = f"<html><head><meta charset='utf-8'><style>{PDF_CSS}</style></head><body>{body}</body></html>"
    buf = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buf, encoding="utf-8")
    if result.err:
        raise RuntimeError("PDF conversion failed")
    return buf.getvalue()


REQUIRED_SECTIONS = [
    ("Key Competitors", r"^##\s*1\.?\s*Key Competitors"),
    ("What Customers Complain About", r"^##\s*2\.?\s*What Customers Complain About"),
    ("Market Gap", r"^##\s*3\.?\s*Market Gap"),
    ("Recommended Positioning", r"^##\s*4\.?\s*Recommended Positioning"),
    ("30-Day Launch Plan", r"^##\s*5\.?\s*30-Day Launch Plan"),
    ("Sources", r"^##\s*Sources"),
]


def _section(md: str, heading_regex: str) -> str:
    m = re.search(heading_regex, md, re.M | re.I)
    if not m:
        return ""
    rest = md[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def check_report(md: str) -> list[tuple[str, bool, str]]:
    """Return (check, passed, detail) rows for the project's done checklist."""
    checks: list[tuple[str, bool, str]] = []

    missing = [name for name, rx in REQUIRED_SECTIONS if not re.search(rx, md, re.M | re.I)]
    checks.append(("All 6 report sections present", not missing,
                   "missing: " + ", ".join(missing) if missing else "all found"))

    t = competitor_table(_section(md, REQUIRED_SECTIONS[0][1]))
    n = len(t["rows"])
    checks.append(("At least 3 competitors", n >= 3, f"{n} in table"))
    checks.append(("3+ competitors with a found price and a source", len(t["complete"]) >= 3,
                   f"{len(t['complete'])} complete; {len(t['priced'])}/{n} show a found price, "
                   f"{len(t['sourced'])}/{n} cite a source"))
    checks.append(("No estimated prices", not t["estimated"],
                   "guessed: " + ", ".join(t["estimated"]) if t["estimated"] else "none"))

    sources = _section(md, REQUIRED_SECTIONS[5][1])
    entries = [l for l in sources.splitlines() if re.match(r"^\s*(?:[-*]\s*)?(\[\d+\]|\d+\.)", l)]
    no_link = [l for l in entries if not re.search(r"https?://", l)]
    urls = set(re.findall(r"https?://[^\s)\]>;]+", sources))
    checks.append(("Every source has a link", bool(entries) and not no_link,
                   f"{len(entries) - len(no_link)}/{len(entries)} entries have a URL; "
                   f"{len(urls)} unique URLs"))
    return checks


# Words a model uses when it guessed a price instead of finding it on a page.
# (Bare "approx" is allowed: Zomato itself lists "₹400 for two (approx)".)
ESTIMATE_RX = re.compile(
    r"estimat|inferr|assum|market standard|likely|probabl|unclear|not legible|guess|typical",
    re.I,
)


def competitor_table(section: str) -> dict[str, list[str]]:
    """Parse the first Markdown table in a section.

    Finds the Price column from the header, so it works for both the researcher's
    table and the final report's table. Returns competitor names grouped as
    rows / priced (a found price) / estimated (a guessed price) / sourced / complete.
    """
    lines = [l.strip() for l in section.splitlines() if l.strip().startswith("|")]
    lines = [l for l in lines if not re.match(r"^\|\s*:?-{2,}", l)]
    if not lines:
        return {k: [] for k in ("rows", "priced", "estimated", "sourced", "complete")}

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip("|").split("|")]

    header = [h.lower() for h in cells(lines[0])]
    price_i = next((i for i, h in enumerate(header) if "price" in h), 1)
    out: dict[str, list[str]] = {k: [] for k in ("rows", "priced", "estimated", "sourced", "complete")}
    for line in lines[1:]:
        c = cells(line)
        name = c[0].strip("* ") or "?"
        price = re.sub(r"\[\d+\]", "", c[price_i] if len(c) > price_i else "")
        guessed = bool(re.search(r"(?<!\*)\*(?!\*)", price) or ESTIMATE_RX.search(price))
        out["rows"].append(name)
        if guessed:
            out["estimated"].append(name)
        elif re.search(r"\d", price):
            out["priced"].append(name)
        if re.search(r"\[\d+\]|https?://", line):
            out["sourced"].append(name)
    out["complete"] = [n for n in out["priced"] if n in out["sourced"]]
    return out


def price_guardrail(max_rejections: int = 2, can_search: bool = True,
                    evidence: list[str] | None = None):
    """CrewAI task guardrail: send a competitor table back while it has guessed prices,
    prices that do not appear in any page the agents read (`evidence`), or fewer than
    3 competitors with a found price.

    After `max_rejections` it lets the output through (CrewAI would otherwise fail the
    whole crew); the app's checklist still flags whatever is left.
    """
    state = {"rejections": 0}

    def guardrail(output):  # returns (passed, output or feedback); no annotation: CrewAI checks it
        text = str(getattr(output, "raw", output))
        t = competitor_table(text)
        problems = []
        if evidence is not None:
            checked = verify_prices(text, evidence)
            unbacked = [f"{n} ({', '.join('₹' + a for a in r['missing'])})"
                        for n, r in checked.items() if r["missing"] and n not in t["estimated"]]
            if unbacked:
                problems.append(
                    "These prices do not appear in any page or search result read in this "
                    "run, next to that competitor's name: " + "; ".join(unbacked)
                    + (". Open that competitor's own page or listing and copy the exact "
                       "price, or replace the competitor." if can_search else
                       ". Use the exact price given for that competitor in the research, "
                       "or write 'not listed'."))
            t["complete"] = [n for n in t["complete"] if checked.get(n, {}).get("verified")]
        if t["estimated"]:
            problems.append(
                "These competitors have guessed prices, not prices found on a page: "
                + ", ".join(t["estimated"])
                + (". Search again for their real price (Swiggy/Zomato listing, website, "
                   "Instagram). If you still cannot find one, remove that competitor and add "
                   "another one that publishes its price." if can_search else
                   ". Write 'not listed' as their price instead of a guess, or drop them."))
        if len(t["complete"]) < 3:
            problems.append(
                f"Only {len(t['complete'])} competitors have both a found price and a source; "
                "at least 3 are required.")
        if not problems or state["rejections"] >= max_rejections:
            return True, output
        state["rejections"] += 1
        return False, " ".join(problems) + " Return the complete answer in the same format."

    return guardrail


def dedupe_headings(md: str) -> str:
    """Drop a heading that repeats the one right before it (blank lines aside)."""
    out: list[str] = []
    last_heading = None
    for line in md.splitlines():
        if re.match(r"^#{1,6}\s", line):
            if line.strip() == last_heading:
                continue
            last_heading = line.strip()
        elif line.strip():
            last_heading = None
        out.append(line)
    return "\n".join(out) + ("\n" if md.endswith("\n") else "")


def clean_report(md: str) -> str:
    """Deterministic clean-up applied to every report before it is saved."""
    return normalize_sources(dedupe_headings(md))


def normalize_sources(md: str) -> str:
    """Make every [n] appear once in '## Sources'.

    Models sometimes list the same number twice (e.g. a competitor's website and its
    review page). Merge those into one line per number so each [n] in the text maps
    to exactly one Sources entry, with all of its URLs.
    """
    m = re.search(r"^##[ \t]*Sources[ \t]*$", md, re.M | re.I)
    if not m:
        return md
    head, body = md[: m.end()], md[m.end():]
    entries: dict[int, list[str]] = {}
    order: list[int] = []
    other: list[str] = []
    for line in body.strip().splitlines():
        hit = re.match(r"^\s*(?:[-*]\s*)?\[(\d+)\]\s*(.*?)\s*$", line)
        if not hit:
            if line.strip():
                other.append(line.rstrip())
            continue
        n, text = int(hit.group(1)), hit.group(2)
        if n not in entries:
            entries[n] = []
            order.append(n)
        if text and text not in entries[n]:
            entries[n].append(text)
    if not entries:
        return md
    lines = []
    for n in sorted(order):
        parts = entries[n]
        first, extra = parts[0], parts[1:]
        # Keep only the URLs of extra entries, labelled by what they are.
        extras = []
        for p in extra:
            label, _, url = p.rpartition(" - ")
            extras.append(f"{label.strip()}: {url.strip()}" if label else p)
        lines.append(f"[{n}] {first}" + ("; " + "; ".join(extras) if extras else ""))
    return head + "\n\n" + "  \n".join(lines + other) + "\n"


# ------------------------------------------------------------- price evidence
_STOP = {"the", "and", "foods", "food", "meals", "meal", "kitchen", "cloud", "healthy",
         "delivery", "services", "service", "instagram", "zomato", "swiggy", "diet", "home",
         "homemade", "millet", "millets", "lunch", "box", "boxes", "cafe", "restaurant"}
_CURRENCY = r"(?:₹|rs\.?|inr|rupees?)"


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"(?<=\d),(?=\d)", "", text)  # 3,000 -> 3000


def _name_keys(name: str) -> list[str]:
    """Strings that identify a competitor in page text: the full name, plus its most
    distinctive word (e.g. 'mummy' for "Mummy's Cloud Kitchen")."""
    full = re.sub(r"\(.*?\)", "", _norm(name)).strip()
    words = [re.sub(r"'s?$", "", w) for w in re.findall(r"[a-z][a-z']+", full)]
    distinctive = [w for w in words if len(w) >= 4 and w not in _STOP]
    return [k for k in [full, *distinctive[:1]] if k]


def price_amounts(cell: str) -> list[str]:
    """Rupee amounts written in a price cell, e.g. '₹90 to ₹130 [2]' -> ['90', '130']."""
    cell = re.sub(r"\[\d+\]", "", _norm(cell))
    # Drop numbers that are not prices: 410g, 10%, 6-day, 26 meals, 7 km, "for 2", 2 bowls.
    cell = re.sub(r"\d+\s*-?\s*(?:%|(?:g|gm|grams?|kg|ml|days?|meals?|km|bowls?|items?|plates?"
                  r"|boxes|people|persons?|pax|pcs|weeks?|months?)(?![a-z]))", "", cell)
    cell = re.sub(r"\b(?:for|x)\s*\d+(?!\s*(?:/-|₹|rs))", "", cell)
    return re.findall(r"\d+(?:\.\d+)?", cell)


def verify_prices(md: str, evidence: list[str], window: int = 400) -> dict[str, dict]:
    """For each competitor in the first table of `md`, check every price amount is
    backed by the text agents actually read this run (tool outputs in `evidence`):
    the amount must appear next to a currency sign within `window` characters of the
    competitor's name. Returns {name: {"amounts", "missing", "verified"}}."""
    corpus = [_norm(e) for e in evidence if e]
    lines = [l.strip() for l in md.splitlines() if l.strip().startswith("|")]
    lines = [l for l in lines if not re.match(r"^\|\s*:?-{2,}", l)]
    if not lines:
        return {}
    split = lambda l: [c.strip() for c in l.strip("|").split("|")]  # noqa: E731
    header = [h.lower() for h in split(lines[0])]
    price_i = next((i for i, h in enumerate(header) if "price" in h), 1)
    results: dict[str, dict] = {}
    for line in lines[1:]:
        c = split(line)
        name = c[0].strip("* ") or "?"
        amounts = price_amounts(c[price_i] if len(c) > price_i else "")
        keys = _name_keys(name)
        missing = []
        for amount in amounts:
            amount_rx = re.compile(
                rf"{_CURRENCY}\s*{re.escape(amount)}(?!\d)|(?<!\d){re.escape(amount)}\s*(?:/-|{_CURRENCY})")
            found = False
            for text in corpus:
                # A single page whose title names the competitor backs any price on it.
                # (Not search-result lists: they mix several businesses.)
                is_results_list = "link: http" in text
                if not is_results_list and any(k in text[:300] for k in keys) \
                        and amount_rx.search(text):
                    found = True
                    break
                for m in amount_rx.finditer(text):
                    near = text[max(0, m.start() - window): m.end() + window]
                    if any(k in near for k in keys):
                        found = True
                        break
                if found:
                    break
            if not found:
                missing.append(amount)
        results[name] = {"amounts": amounts, "missing": missing,
                         "verified": bool(amounts) and not missing}
    return results
