"""Web tools for the crew.

- Search: SerperDevTool (Google via serper.dev) when SERPER_API_KEY is set,
  otherwise a free DuckDuckGo search so the crew still runs without that key.
- Scrape / write: the official crewai_tools ScrapeWebsiteTool and FileWriterTool
  when they import. On some Windows machines Application Control blocks a native
  DLL crewai_tools pulls in (grpc via chromadb); then small drop-in versions with
  the same names are used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


class _SearchInput(BaseModel):
    search_query: str = Field(..., description="The search query to run")


class _SerperSearch(BaseTool):
    name: str = "Search the internet with Serper"
    description: str = "Searches Google and returns titles, links and snippets. Input: search_query."
    args_schema: type[BaseModel] = _SearchInput
    n_results: int = 10

    def _run(self, search_query: str) -> str:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
            data=json.dumps({"q": search_query, "num": self.n_results,
                             "gl": os.getenv("SEARCH_COUNTRY") or "in"}),
            timeout=30,
        )
        if r.status_code != 200:
            return f"Search failed ({r.status_code}): {r.text[:200]}"
        data = r.json()
        lines = [f"Title: {i.get('title')}\nLink: {i.get('link')}\nSnippet: {i.get('snippet', '')}\n---"
                 for i in data.get("organic", [])[: self.n_results]]
        lines += [f"Place: {p.get('title')} | rating {p.get('rating')} ({p.get('ratingCount')} reviews)"
                  f" | {p.get('address')}\n---" for p in data.get("places", [])[:5]]
        return "\n".join(lines) or "No results."


class DuckDuckGoSearchTool(BaseTool):
    name: str = "Search the internet"
    description: str = "Searches the web and returns titles, links and snippets. Input: search_query."
    args_schema: type[BaseModel] = _SearchInput
    n_results: int = 10

    def _run(self, search_query: str) -> str:
        from ddgs import DDGS

        results, error = [], None
        # Single backends are flaky; try a few in turn.
        for backend in ("auto", "google", "bing", "yahoo"):
            try:
                results = DDGS(timeout=15).text(search_query, backend=backend,
                                                max_results=self.n_results)
                if results:
                    break
            except Exception as exc:
                error = exc
        if not results:
            return f"Search failed ({error}). Try a shorter or different query."
        lines = [f"Title: {r.get('title')}\nLink: {r.get('href')}\nSnippet: {r.get('body', '')}\n---"
                 for r in results]
        return "\n".join(lines) or "No results. Try a different query."


class _ReviewsInput(BaseModel):
    business_name: str = Field(..., description="Competitor name, e.g. 'Mealzy'")
    city: str = Field("", description="City to narrow the Google Maps match, e.g. 'Coimbatore'")


class GoogleReviewsTool(BaseTool):
    """Real Google Maps reviews via Serper: finds the business, then returns its
    lowest-rated reviews (complaints) and most relevant reviews (likes), each with
    the link to that review on Google Maps."""

    name: str = "Google Maps reviews"
    description: str = (
        "Gets real Google Maps reviews for one business: its rating, review count, "
        "the lowest-rated reviews (complaints) and the most relevant reviews (likes), "
        "each with a link to the review. Inputs: business_name, city."
    )
    args_schema: type[BaseModel] = _ReviewsInput
    per_sort: int = 8

    def _post(self, endpoint: str, payload: dict) -> dict:
        r = requests.post(
            f"https://google.serper.dev/{endpoint}",
            headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
            data=json.dumps({**payload, "gl": os.getenv("SEARCH_COUNTRY") or "in"}),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _run(self, business_name: str, city: str = "") -> str:
        if not os.getenv("SERPER_API_KEY", "").strip():
            return "Google Maps reviews need SERPER_API_KEY; use web search instead."
        try:
            places = self._post("places", {"q": f"{business_name} {city}".strip()}).get("places", [])
        except Exception as exc:
            return f"Place lookup failed: {exc}"
        if not places:
            return f"No Google Maps listing found for '{business_name}'. Try web search."
        p = places[0]
        lines = [
            f"Google Maps listing: {p.get('title')} | {p.get('address', '')} | rating "
            f"{p.get('rating')} from {p.get('ratingCount')} reviews",
            "Check the listing name matches the competitor before using these reviews.",
        ]
        seen: set[str] = set()
        for sort, label in (("lowestRating", "LOWEST-RATED (complaints)"),
                            ("mostRelevant", "MOST RELEVANT (likes and complaints)")):
            try:
                reviews = self._post("reviews", {"cid": p.get("cid"), "sortBy": sort}).get("reviews", [])
            except Exception as exc:
                lines.append(f"{label}: could not load reviews ({exc})")
                continue
            picked = [r for r in reviews if r.get("snippet") and r.get("id") not in seen][: self.per_sort]
            lines.append(f"\n{label}:" if picked else f"\n{label}: no reviews with text")
            for r in picked:
                seen.add(r.get("id"))
                text = " ".join(str(r["snippet"]).split())[:350]
                lines.append(f"- {r.get('rating')} stars, {r.get('date')}: \"{text}\"\n  Link: {r.get('link')}")
        return "\n".join(lines)


def make_review_tools() -> list[BaseTool]:
    """Extra tools for the Review Analyst: Google Maps reviews when Serper is set up."""
    return [GoogleReviewsTool()] if os.getenv("SERPER_API_KEY", "").strip() else []


def make_search_tool() -> BaseTool:
    """Serper if a key is configured, else DuckDuckGo (no key needed)."""
    if os.getenv("SERPER_API_KEY", "").strip():
        try:
            from crewai_tools import SerperDevTool

            return SerperDevTool(n_results=10, country=os.getenv("SEARCH_COUNTRY") or "in")
        except Exception:
            return _SerperSearch()
    return DuckDuckGoSearchTool()


try:
    from crewai_tools import FileWriterTool, ScrapeWebsiteTool  # noqa: F401

    USING = "crewai_tools"
except Exception:  # ImportError or a blocked DLL
    USING = "built-in fallback"

    class _ScrapeInput(BaseModel):
        website_url: str = Field(..., description="Full URL of the page to read")

    class ScrapeWebsiteTool(BaseTool):
        name: str = "Read website content"
        description: str = "Reads the visible text of a web page. Input: website_url."
        args_schema: type[BaseModel] = _ScrapeInput
        max_chars: int = 8000

        def _run(self, website_url: str) -> str:
            try:
                r = requests.get(website_url, headers=_UA, timeout=30)
                r.raise_for_status()
            except Exception as exc:
                return f"Could not open {website_url}: {exc}"
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ").split())
            return text[: self.max_chars] or "Page had no readable text."

    class _WriteInput(BaseModel):
        filename: str = Field(..., description="File name, e.g. report.md")
        content: str = Field(..., description="Full text to write")
        directory: str | None = Field(None, description="Folder to write into")
        overwrite: bool | str = Field(True, description="Overwrite an existing file")

    class FileWriterTool(BaseTool):
        name: str = "File Writer Tool"
        description: str = "Writes text content to a file. Inputs: filename, content, directory, overwrite."
        args_schema: type[BaseModel] = _WriteInput

        def _run(self, filename: str, content: str, directory: str | None = None,
                 overwrite: bool | str = True) -> str:
            folder = Path(directory or ".")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / Path(filename).name
            if path.exists() and str(overwrite).lower() not in ("true", "1", "yes"):
                return f"{path} exists and overwrite is false."
            path.write_text(content, encoding="utf-8")
            return f"Content successfully written to {path}"
