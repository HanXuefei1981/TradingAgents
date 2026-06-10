"""A-share news fetcher — Eastmoney direct API.

Replaces yfinance news for Chinese stocks in the sentiment analyst.
Eastmoney's search-api-web provides JSONP news data with no API key
or authentication required.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def fetch_ashare_news(ticker: str, limit: int = 15) -> str:
    """Fetch recent Eastmoney news for an A-share ticker.

    Returns a formatted plaintext block suitable for prompt injection,
    or a placeholder if the API is unreachable.
    """
    # Normalize to 6-digit code
    code = ticker.strip().upper()
    for sfx in (".SH", ".SS", ".SZ", ".BJ"):
        if code.endswith(sfx):
            code = code[:-3]
            break

    try:
        cb = "jQuery_ashare_news"
        inner = json.dumps({
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": limit,
                    "preTag": "",
                    "postTag": "",
                }
            },
        }, separators=(',', ':'))

        import requests as _requests
        r = _requests.get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": cb, "param": inner},
            headers={"User-Agent": _UA, "Referer": "https://so.eastmoney.com/"},
            timeout=10,
        )
        r.raise_for_status()
        text = r.text

        # Parse JSONP: jQuery_xxx({...})
        json_str = text[text.index("(") + 1:text.rindex(")")]
        data = json.loads(json_str)

        # Result structure: {result: {cmsArticleWebOld: [...]}}
        result = data.get("result", {})
        if isinstance(result, dict):
            articles = result.get("cmsArticleWebOld", [])
        elif isinstance(result, list):
            articles = result
        else:
            articles = []
        if not articles:
            return f"<no Eastmoney news found for {code}>"

        lines = [f"Eastmoney news for {code} ({len(articles)} articles):"]
        for a in articles:
            title = re.sub(r"<[^>]+>", "", a.get("title", ""))
            content = re.sub(r"<[^>]+>", "", a.get("content", ""))[:200]
            date = a.get("date", "")
            source = a.get("mediaName", "")
            lines.append(f"[{date} · {source}] {title}")
            if content:
                lines.append(f"  {content}")

        return "\n".join(lines)

    except Exception as exc:
        logger.warning("Eastmoney news fetch failed for %s: %s", code, exc)
        return f"<Eastmoney news unavailable: {type(exc).__name__}>"


def _is_ashare(ticker: str) -> bool:
    """Detect A-share ticker."""
    t = ticker.strip().upper()
    if t.endswith((".SH", ".SS", ".SZ", ".BJ")):
        return True
    if len(t) == 6 and t.isdigit():
        return True
    return False


def fetch_news_smart(ticker: str, start_date: str, end_date: str) -> str:
    """Smart news fetcher: Eastmoney for A-shares, yfinance for others.

    Falls back gracefully on any error.
    """
    if _is_ashare(ticker):
        return fetch_ashare_news(ticker)

    # For non-A-shares: use existing yfinance news
    from tradingagents.agents.utils.agent_utils import get_news
    return get_news.func(ticker, start_date, end_date)
