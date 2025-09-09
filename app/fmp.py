import httpx
from .config import settings

BASE_URL = "https://financialmodelingprep.com/api/v3"

async def _get(path: str, params: dict):
    params = dict(params or {})
    params["apikey"] = settings.fmp_api_key
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.get(f"{BASE_URL}/{path.lstrip('/')}", params=params)
        r.raise_for_status()
        return r.json()

# Quotes & Profile
async def get_price_quote(symbol: str):
    return await _get(f"quote/{symbol.upper()}", params={})

async def get_company_profile(symbol: str):
    return await _get(f"profile/{symbol.upper()}", params={})

# Fundamentals
async def get_income_statement(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"income-statement-ttm/{symbol.upper()}", params={})
    return await _get(f"income-statement/{symbol.upper()}", params={"period": period, "limit": limit})

async def get_balance_sheet(symbol: str, period: str = "annual", limit: int = 1):
    if period not in ("annual", "quarter"):
        period = "annual"
    return await _get(f"balance-sheet-statement/{symbol.upper()}", params={"period": period, "limit": limit})

async def get_cash_flow(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"cash-flow-statement-ttm/{symbol.upper()}", params={})
    return await _get(f"cash-flow-statement/{symbol.upper()}", params={"period": period, "limit": limit})

async def get_key_metrics(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"key-metrics-ttm/{symbol.upper()}", params={})
    return await _get(f"key-metrics/{symbol.upper()}", params={"period": period, "limit": limit})

async def get_financial_ratios(symbol: str, period: str = "ttm", limit: int = 1):
    if period == "ttm":
        return await _get(f"ratios-ttm/{symbol.upper()}", params={})
    return await _get(f"ratios/{symbol.upper()}", params={"period": period, "limit": limit})

# Peers
async def get_peers(symbol: str):
    return await _get("stock_peers", params={"symbol": symbol.upper()})

# News
async def get_company_news(symbol: str, from_date: str = None, to_date: str = None, limit: int = 50):
    params = {"tickers": symbol.upper(), "limit": limit}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return await _get("stock_news", params=params)
