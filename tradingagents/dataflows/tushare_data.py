
"""Tushare data vendor for TradingAgents A-share analysis.

Provides OHLCV, fundamentals, income statement, balance sheet, and cashflow
via Tushare Pro API. Designed to be registered as a vendor in
dataflows/interface.py alongside yfinance and alpha_vantage.

Ticker format: A-share 6-digit code (e.g. "600519") or with suffix
("600519.SH", "000858.SZ"). The module normalizes to Tushare's
<code>.<exchange> format internally.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import tushare as ts

from .symbol_utils import NoMarketDataError

logger = logging.getLogger(__name__)

# ── Tushare setup ──
# Token comes from the TUSHARE_TOKEN env var (loaded from .env by the
# package __init__). TUSHARE_HTTP_URL optionally points at a private
# Tushare-compatible server; unset means the official endpoint.
_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
ts.set_token(_TOKEN)
_PRO = ts.pro_api()
_HTTP_URL = os.environ.get("TUSHARE_HTTP_URL", "")
if _HTTP_URL:
    _PRO._DataApi__http_url = _HTTP_URL

# ── Ticker normalization ──
def _to_ts_code(raw: str) -> str:
    """Normalize ticker to Tushare format (e.g. '601138.SH')."""
    s = raw.strip().upper()
    # Strip known suffixes
    for sfx in ('.SS', '.SZ', '.SH', '.BJ'):
        if s.endswith(sfx):
            s = s[:-3]
            break
    # Add correct exchange suffix
    if s.startswith(('6', '9')):
        return f"{s}.SH"
    elif s.startswith(('8', '4')):
        return f"{s}.BJ"
    else:
        return f"{s}.SZ"

def _from_ts_code(ts_code: str) -> str:
    """Strip exchange suffix, return bare 6-digit code."""
    return ts_code.split('.')[0]


# ── OHLCV (replaces yfinance.history) ──
def get_Tushare_data_online(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch A-share daily OHLCV data via Tushare, return CSV string."""
    ts_code = _to_ts_code(symbol)

    # Tushare date format: YYYYMMDD
    start = start_date.replace('-', '')
    end = end_date.replace('-', '')

    try:
        df = _PRO.daily(ts_code=ts_code, start_date=start, end_date=end)
    except Exception as e:
        raise NoMarketDataError(symbol, ts_code, str(e))

    if df is None or df.empty:
        raise NoMarketDataError(symbol, ts_code, "no rows returned")

    # Rename columns to match yfinance convention
    col_map = {
        'trade_date': 'Date',
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'vol': 'Volume',
        'amount': 'Amount',
    }
    df = df.rename(columns=col_map)
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date').sort_index()

    # Round prices
    for col in ['Open', 'High', 'Low', 'Close']:
        if col in df.columns:
            df[col] = df[col].round(2)

    # Add Adj Close (same as Close for A-shares)
    df['Adj Close'] = df['Close']

    # Build CSV output matching yfinance vendor format
    header = f"# Stock data for {_from_ts_code(ts_code)} ({symbol}) from {start_date} to {end_date}\n"
    return header + df.to_csv()


# ── Fundamentals ──
def get_tushare_fundamentals(ticker: str) -> str:
    """Fetch key financial metrics via Tushare income + daily_basic."""
    ts_code = _to_ts_code(ticker)
    bare = _from_ts_code(ts_code)

    lines = [f"# Fundamentals for {bare} ({ts_code})\n"]

    try:
        # Latest daily basic (PE, PB, market cap, etc.)
        basic = _PRO.daily_basic(ts_code=ts_code,
                                  start_date=(datetime.now() - timedelta(days=30)).strftime('%Y%m%d'),
                                  end_date=datetime.now().strftime('%Y%m%d'))
        if basic is not None and not basic.empty:
            latest = basic.iloc[-1]
            lines.append(f"PE (TTM): {latest.get('pe_ttm', 'N/A')}")
            lines.append(f"PB: {latest.get('pb', 'N/A')}")
            lines.append(f"Total Market Cap (10k RMB): {latest.get('total_mv', 'N/A')}")
            lines.append(f"Turnover Rate: {latest.get('turnover_rate', 'N/A')}%")
            lines.append(f"Volume Ratio: {latest.get('volume_ratio', 'N/A')}")
            lines.append("")

        # Latest income statement for revenue/profit
        income = _PRO.income(ts_code=ts_code, end_date=datetime.now().strftime('%Y1231'),
                               fields='end_date,revenue,n_income,basic_eps,diluted_eps',
                               limit=4)
        if income is not None and not income.empty:
            lines.append("## Recent Income (万元)")
            for _, row in income.iterrows():
                lines.append(
                    f"{row.get('end_date', 'N/A')}: "
                    f"Revenue={row.get('revenue', 0)/1e4:.2f}亿 "
                    f"NetProfit={row.get('n_income', 0)/1e4:.2f}亿 "
                    f"EPS={row.get('basic_eps', 'N/A')}"
                )
            lines.append("")

        if not lines or len(lines) <= 1:
            lines.append("No fundamental data available.")
    except Exception as e:
        logger.warning("Tushare fundamentals for %s failed: %s", ts_code, e)
        lines.append(f"Note: partial data — {e}")

    return "\n".join(lines)


# ── Income Statement ──
def get_tushare_income_statement(ticker: str) -> str:
    """Fetch income statement via Tushare."""
    ts_code = _to_ts_code(ticker)
    bare = _from_ts_code(ts_code)

    try:
        df = _PRO.income(ts_code=ts_code, limit=8)
        if df is None or df.empty:
            return f"# Income Statement for {bare}: No data available."

        cols = ['end_date', 'revenue', 'total_cogs', 'n_income',
                'basic_eps', 'diluted_eps', 'ebit', 'ebitda']
        available = [c for c in cols if c in df.columns]
        df = df[available].sort_values('end_date', ascending=False)

        lines = [f"# Income Statement for {bare} ({ts_code})\n"]
        lines.append(df.to_csv(index=False))
        return "\n".join(lines)
    except Exception as e:
        return f"# Income Statement for {bare}: Error — {e}"


# ── Balance Sheet ──
def get_tushare_balance_sheet(ticker: str) -> str:
    """Fetch balance sheet via Tushare."""
    ts_code = _to_ts_code(ticker)
    bare = _from_ts_code(ts_code)

    try:
        df = _PRO.balancesheet(ts_code=ts_code, limit=8)
        if df is None or df.empty:
            return f"# Balance Sheet for {bare}: No data available."

        cols = ['end_date', 'total_assets', 'total_liab', 'total_hldr_eqy_exc_min_int',
                'total_cur_assets', 'total_cur_liab']
        available = [c for c in cols if c in df.columns]
        df = df[available].sort_values('end_date', ascending=False)

        lines = [f"# Balance Sheet for {bare} ({ts_code})\n"]
        lines.append(df.to_csv(index=False))
        return "\n".join(lines)
    except Exception as e:
        return f"# Balance Sheet for {bare}: Error — {e}"


# ── Cashflow Statement ──
def get_tushare_cashflow(ticker: str) -> str:
    """Fetch cashflow statement via Tushare."""
    ts_code = _to_ts_code(ticker)
    bare = _from_ts_code(ts_code)

    try:
        df = _PRO.cashflow(ts_code=ts_code, limit=8)
        if df is None or df.empty:
            return f"# Cashflow Statement for {bare}: No data available."

        cols = ['end_date', 'n_cashflow_act', 'c_fr_sale_sg',
                'n_cashflow_inv_act', 'n_cashflow_fin_act', 'free_cashflow']
        available = [c for c in cols if c in df.columns]
        df = df[available].sort_values('end_date', ascending=False)

        lines = [f"# Cashflow Statement for {bare} ({ts_code})\n"]
        lines.append(df.to_csv(index=False))
        return "\n".join(lines)
    except Exception as e:
        return f"# Cashflow Statement for {bare}: Error — {e}"

# Sentinel for unsupported features on Tushare
def _not_supported(feature: str):
    def _inner(*args, **kwargs):
        return f"NOT_SUPPORTED: {feature} is not available via Tushare for A-shares."
    return _inner

get_tushare_insider_transactions = _not_supported("Insider transactions")
get_tushare_news = _not_supported("News (use Eastmoney via a-stock-data)")
get_tushare_global_news = _not_supported("Global news")
get_tushare_indicators = _not_supported("Technical indicators (computed from OHLCV)")
