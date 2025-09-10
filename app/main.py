from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from typing import Optional, Callable, Any

from .config import settings
from .cache import TTLCache
from . import fmp
from .schemas import PriceRequest, FundamentalsRequest, NewsRequest, AnalyzeRequest
from .openai_client import get_client
from .prompts import ANALYZE_TEMPLATE

app = FastAPI(title="FMP + ChatGPT Proxy", version="0.7.0")

# --- CORS ---
origins = [o.strip() for o in (settings.cors_origins if isinstance(settings.cors_origins, list)
                               else str(settings.cors_origins).split(','))]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Caches ---
price_cache = TTLCache(60)
news_cache  = TTLCache(300)
fund_cache  = TTLCache(1800)

def _auth_check(authorization: Optional[str]):
    if settings.action_key and authorization != f"Bearer {settings.action_key}":
        raise HTTPException(status_code=401, detail="Unauthorized")

# --- Helpers ---
async def try_fetch(fn: Callable[[], Any], fallback=None):
    try:
        return await fn()
    except Exception:
        return fallback

async def try_ttm_then_annual(ttm_fn: Callable[[], Any], annual_fn: Callable[[], Any]):
    try:
        data = await ttm_fn()
        if not data:
            data = await annual_fn()
        return data
    except Exception:
        return await annual_fn()

async def optional_call(module: Any, func_name: str, *args, **kwargs):
    func = getattr(module, func_name, None)
    if func is None:
        return []
    try:
        return await func(*args, **kwargs)
    except Exception:
        return []

def looks_like_ticker(s: str) -> bool:
    s2 = s.strip().upper()
    if len(s2) <= 6 and s2.isalnum():
        return True
    if "." in s2 and 1 <= len(s2.split(".", 1)[0]) <= 6:
        return True
    return False

async def resolve_symbol(user_input: str) -> str:
    if not user_input:
        return user_input
    s = user_input.strip()
    if looks_like_ticker(s):
        return s.upper()
    results = await optional_call(fmp, "search_symbol", s, None, 10)
    if isinstance(results, list) and results:
        sym = results[0].get("symbol") or results[0].get("ticker") or s
        return (sym or s).upper()
    return s.upper()

def _safe(x, *keys, default=None):
    cur = x
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur

def compact_kpis(income_ttm, balance_1, cashflow_ttm, keym_ttm, ratios_ttm,
                 income_hist, balance_hist, cashflow_hist, keym_hist, ratios_hist,
                 growth_hist, marketcap_quote):
    # Kurzfassung siehe vorherige Nachricht: baut TTM + 5Y Historie kompakt zusammen
    # (Revenue, EBITDA, NetIncome, EPS … etc.)
    # Rückgabe: dict {"ttm": {...}, "hist": {...}}
    ...
    # (Hier kommt der Extractor-Code aus der letzten Antwort rein)
    ...

# --- Endpoints ---

@app.get("/health")
async def health():
    return {"ok": True, "model": settings.openai_model}

@app.post("/price")
async def price(req: PriceRequest, authorization: str = Header(None)):
    _auth_check(authorization)
    data = await fmp.get_price_quote(req.symbol)
    return {"symbol": req.symbol.upper(), "data": data, "cached": False}

@app.post("/analyze")
async def analyze(req: AnalyzeRequest, authorization: str = Header(None)):
    _auth_check(authorization)
    symbol = await resolve_symbol((req.symbol or "").strip())

    # Daten holen
    quote   = await try_fetch(lambda: fmp.get_price_quote(symbol), [])
    profile = await try_fetch(lambda: fmp.get_company_profile(symbol), [])
    income_ttm = await try_ttm_then_annual(
        lambda: fmp.get_income_statement(symbol, "ttm", 1),
        lambda: fmp.get_income_statement(symbol, "annual", 1)
    )
    cashflow_ttm = await try_ttm_then_annual(
        lambda: fmp.get_cash_flow(symbol, "ttm", 1),
        lambda: fmp.get_cash_flow(symbol, "annual", 1)
    )
    balance_annual_1 = await try_fetch(lambda: fmp.get_balance_sheet(symbol, "annual", 1), [])
    key_metrics_ttm  = await try_ttm_then_annual(
        lambda: fmp.get_key_metrics(symbol, "ttm", 1),
        lambda: fmp.get_key_metrics(symbol, "annual", 1)
    )
    ratios_ttm       = await try_ttm_then_annual(
        lambda: fmp.get_financial_ratios(symbol, "ttm", 1),
        lambda: fmp.get_financial_ratios(symbol, "annual", 1)
    )

    # Historien
    income_hist   = await try_fetch(lambda: fmp.get_income_statement(symbol, "annual", 5), [])
    balance_hist  = await try_fetch(lambda: fmp.get_balance_sheet(symbol, "annual", 5), [])
    cashflow_hist = await try_fetch(lambda: fmp.get_cash_flow(symbol, "annual", 5), [])
    keym_hist     = await try_fetch(lambda: fmp.get_key_metrics(symbol, "annual", 5), [])
    ratios_hist   = await try_fetch(lambda: fmp.get_financial_ratios(symbol, "annual", 5), [])
    growth_hist   = await try_fetch(lambda: fmp.get_financial_growth(symbol, "annual", 5), [])

    # Extra
    estimates = await optional_call(fmp, "get_analyst_estimates", symbol, "annual", 8)
    dividends_history = await optional_call(fmp, "get_dividends_history", symbol, 20)
    dividend_calendar = await optional_call(fmp, "get_dividend_calendar", symbol)
    insider_trades    = await optional_call(fmp, "get_insider_trades", symbol, 20)

    marketcap_quote = None
    if isinstance(quote, list) and quote:
        marketcap_quote = quote[0].get("marketCap") or quote[0].get("mktCap")

    # Kompakte KPIs
    kpis = compact_kpis(
        income_ttm, balance_annual_1, cashflow_ttm, key_metrics_ttm, ratios_ttm,
        income_hist, balance_hist, cashflow_hist, keym_hist, ratios_hist,
        growth_hist, marketcap_quote
    )

    # Prompt
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    prompt_base = ANALYZE_TEMPLATE.format(
        perspective="value", language="de", symbol=symbol.upper(),
        profile=profile, quote=quote, key_metrics=key_metrics_ttm, ratios=ratios_ttm,
        income=income_ttm, balance=balance_annual_1, cashflow=cashflow_ttm,
        peers=[], news=[], as_of=as_of
    )
    extras = f"""

KPI-PAKET (kompakt; Quelle: FMP):
{kpis}

ANALYSTEN-SCHÄTZUNGEN:
{estimates}

DIVIDENDEN:
- Historie: {dividends_history}
- Anstehend: {dividend_calendar}

INSIDER-TRADES (letzte 20):
{insider_trades}
"""
    prompt = prompt_base + extras

    try:
        client = get_client()
        result = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": "Du bist ein Value-Investor. Schreibe eine Deep-Dive-Analyse (Deutsch). KGV zuerst. Keine Anlageberatung."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        content = result.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")

    return {
        "symbol": symbol.upper(),
        "as_of_utc": as_of,
        "model": settings.openai_model,
        "analysis_md": content,
    }
