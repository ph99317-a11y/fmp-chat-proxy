import httpx
from .config import settings

BASE_URL = "https://financialmodelingprep.com/api/v3"


async def _get(path: str, params: dict):
    """
    Interner GET-Helper mit API-Key.
    """
    params = dict(params or {})
    params["apikey"] = settings.fmp_api_key
    url = f"{BASE_URL}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


# -----------------------
# Basis / Quotes / Profile
# -----------------------

async def get_price_quote(symbol: str):
    return await _get(f"quote/{symbol.upper()}", params={})

async def get_company_profile(symbol: str):
    return await _get(f"profile/{symbol.upper()}", params={})


# -----------------------
# Fundamentals (Statements)
# -----------------------

async def get_income_statement(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"income-statement-ttm/{symbol.upper()}", params={})
    return await _get(
        f"income-statement/{symbol.upper()}",
        params={"period": period, "limit": limit},
    )

async def get_balance_sheet(symbol: str, period: str = "annual", limit: int = 1):
    if period not in ("annual", "quarter"):
        period = "annual"
    return await _get(
        f"balance-sheet-statement/{symbol.upper()}",
        params={"period": period, "limit": limit},
    )

async def get_cash_flow(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"cash-flow-statement-ttm/{symbol.upper()}", params={})
    return await _get(
        f"cash-flow-statement/{symbol.upper()}",
        params={"period": period, "limit": limit},
    )


# -----------------------
# Metrics & Ratios
# -----------------------

async def get_key_metrics(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"key-metrics-ttm/{symbol.upper()}", params={})
    return await _get(
        f"key-metrics/{symbol.upper()}",
        params={"period": period, "limit": limit},
    )

async def get_financial_ratios(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"ratios-ttm/{symbol.upper()}", params={})
    return await _get(
        f"ratios/{symbol.upper()}",
        params={"period": period, "limit": limit},
    )


# -----------------------
# Peers & News
# -----------------------

async def get_peers(symbol: str):
    return await _get("stock_peers", params={"symbol": symbol.upper()})

async def get_company_news(symbol: str, from_date: str = None, to_date: str = None, limit: int = 50):
    params = {"tickers": symbol.upper(), "limit": limit}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return await _get("stock_news", params=params)


# -----------------------
# Suche / Resolve
# -----------------------

async def search_symbol(query: str, exchange: str | None = None, limit: int = 10):
    params = {"query": query, "limit": limit}
    if exchange:
        params["exchange"] = exchange
    return await _get("search", params=params)


# -----------------------
# Estimates (EPS/Revenue)
# -----------------------

async def get_analyst_estimates(symbol: str, period: str = "annual", limit: int = 8):
    return await _get(
        f"analyst-estimates/{symbol.upper()}",
        params={"period": period, "limit": limit},
    )


# -----------------------
# Dividenden
# -----------------------

async def get_dividends_history(symbol: str, limit: int = 200):
    return await _get(
        f"historical-price-full/stock_dividend/{symbol.upper()}",
        params={"limit": limit},
    )

async def get_dividend_calendar(symbol: str):
    try:
        return await _get("stock_dividend_calendar", params={"symbol": symbol.upper()})
    except Exception:
        return await _get("dividend_calendar", params={"symbol": symbol.upper()})


# -----------------------
# Financial Growth
# -----------------------

async def get_financial_growth(symbol: str, period: str = "annual", limit: int = 5):
    return await _get(
        f"financial-growth/{symbol.upper()}",
        params={"period": period, "limit": limit},
    )


# -----------------------
# Insider Trades
# -----------------------

async def get_insider_trades(symbol: str, limit: int = 20):
    return await _get("insider-trading", params={"symbol": symbol.upper(), "limit": limit})
