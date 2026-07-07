# aura/executors/browser_executor.py
# Minimal BROWSER executor for REALTIME_QUERY (online path).
#
# Uses HTTPS-only DuckDuckGo Instant Answer API — no shell, no Playwright.
# Voice input is passed only as a validated search query string.

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from aura.schemas.command import ExecutionResult, ExecutorType

logger = logging.getLogger("aura.browser_executor")

_MAX_QUERY_LEN = 500
_DDG_API = "https://api.duckduckgo.com/"


class BrowserExecutor:
    """Fetches live web answers for realtime voice queries."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._timeout = float(config.get("browser", {}).get("timeout", 15))

    def run(self, action: str, params: dict[str, Any]) -> ExecutionResult:
        handlers = {
            "search": self.search,
            "navigate": self.navigate,
        }
        handler = handlers.get(action)
        if handler is None:
            return ExecutionResult(
                success=False,
                output=f"Browser action '{action}' is not supported yet.",
                executor=ExecutorType.BROWSER,
            )
        return handler(params)

    def search(self, params: dict[str, Any]) -> ExecutionResult:
        query = params.get("query") or params.get("prompt", "")
        if not isinstance(query, str):
            return ExecutionResult(
                success=False,
                output="I need a search query to look that up.",
                executor=ExecutorType.BROWSER,
            )

        query = _sanitize_query(query)
        if not query:
            return ExecutionResult(
                success=False,
                output="I need a search query to look that up.",
                executor=ExecutorType.BROWSER,
            )

        try:
            response = httpx.get(
                _DDG_API,
                params={
                    "q": query,
                    "format": "json",
                    "no_redirect": 1,
                    "no_html": 1,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("Browser search failed for %r: %s", query, exc)
            return ExecutionResult(
                success=False,
                output="I couldn't fetch live results right now.",
                executor=ExecutorType.BROWSER,
                error=str(exc),
            )

        answer = (data.get("Answer") or "").strip()
        abstract = (data.get("AbstractText") or "").strip()
        heading = (data.get("Heading") or "").strip()

        if answer:
            output = answer
        elif abstract:
            output = abstract
            if heading:
                output = f"{heading}: {abstract}"
        else:
            related = data.get("RelatedTopics") or []
            snippet = _first_related_snippet(related)
            if snippet:
                output = snippet
            else:
                output = f"I searched for {query} but didn't find a concise live answer."

        return ExecutionResult(
            success=True,
            output=output,
            executor=ExecutorType.BROWSER,
            data={"source": "duckduckgo", "query": query},
        )

    def navigate(self, params: dict[str, Any]) -> ExecutionResult:
        """Placeholder — full Playwright navigation is Phase 7."""
        url = params.get("url", "")
        if not isinstance(url, str) or not url.strip():
            return ExecutionResult(
                success=False,
                output="I need a URL to navigate to.",
                executor=ExecutorType.BROWSER,
            )
        return ExecutionResult(
            success=False,
            output="Browser navigation is not implemented yet. Try a search instead.",
            executor=ExecutorType.BROWSER,
        )


def _sanitize_query(text: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", text).strip()
    return cleaned[:_MAX_QUERY_LEN]


def _first_related_snippet(related: list[Any]) -> str:
    for item in related:
        if isinstance(item, dict):
            text = item.get("Text", "")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""
