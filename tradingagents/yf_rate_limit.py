"""Rate-limited yfinance wrapper for TradingAgents.

Patches yfinance's YfData._make_request (the central method ALL
data fetches go through) to enforce a minimum interval between
Yahoo Finance API calls. Also adds retry on HTTP 429.

Yahoo aggressively rate-limits non-US IPs.
Without this, TradingAgents' concurrent analyst nodes trigger
simultaneous yfinance calls → YFRateLimitError.
"""
import time
import random
import threading
import logging

logger = logging.getLogger(__name__)
_PATCHED = False
_LAST_CALL = 0.0
_LOCK = threading.Lock()
_MIN_INTERVAL = 1.2  # seconds between Yahoo Finance API calls


def apply_rate_limit(min_interval=None):
    """Monkey-patch yfinance YfData._make_request with rate limiting."""
    global _PATCHED, _MIN_INTERVAL
    if _PATCHED:
        return
    if min_interval is not None:
        _MIN_INTERVAL = min_interval
    _PATCHED = True

    from yfinance.data import YfData

    _orig_make_request = YfData._make_request

    def _rate_limited_make_request(self, url, request_method=None, **kwargs):
        """Rate-limited + retry wrapper around YfData._make_request."""
        global _LAST_CALL

        # Enforce minimum interval between calls
        with _LOCK:
            elapsed = time.monotonic() - _LAST_CALL
            if elapsed < _MIN_INTERVAL:
                delay = _MIN_INTERVAL - elapsed + random.uniform(0, 0.3)
                time.sleep(delay)
            _LAST_CALL = time.monotonic()

        # Retry on rate limit
        for attempt in range(4):
            try:
                return _orig_make_request(self, url, request_method=request_method, **kwargs)
            except Exception as e:
                err_str = str(e)
                is_rate_limit = (
                    "Rate" in err_str or "429" in err_str or
                    "Too Many Requests" in err_str
                )
                if is_rate_limit and attempt < 3:
                    wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                    logger.warning(
                        "Yahoo rate limited, waiting %.0fs (attempt %d/4)", wait, attempt + 1
                    )
                    time.sleep(wait)
                    continue
                raise

    YfData._make_request = _rate_limited_make_request
    logger.info("yfinance rate limiter active (%.1fs between calls)", _MIN_INTERVAL)
