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

app = FastAPI(title="FMP + ChatGPT Proxy", version="0.6.0")

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
price_cache = TTLCache(60)     # quotes 60s
news_cache  = TTLCache(300)    # news 5 min
fund_cache  = TTLCache(1800)   # fundamentals 30 min

# --- Auth-Check ---
def _auth_check(authorization: Optional[str]):
    if settings.action_key and authorization != f"Bearer {settings.action_key}":
        raise HTTPException(status_code=401, detail="Unauthorized")

# --- Fehlertolerante Helper ---
async def try_fetch(fn: Callable[[], Any], fallback=None):
    """Führe einen async-Call aus (als Callable übergeben), liefere fallback bei Fehlern."""
    try:
        return await fn()
    except Exception:
        return fallback

async def try_ttm_then_annual(ttm_fn: Callable[[], Any], annual_fn: Callable[[], Any]):
    """
    Erzeuge und await'e die Coroutines erst hier drin.
    Erst TTM versuchen; wenn leer/Fehler -> Annual.
    """
    try:
        data = await ttm_fn()
        if not data:
            data = await annual_fn()
        return data
    except Exception:
        return await annual_fn()

def looks_like_ticker(s: str) -> bool:
    s2 = s.strip().upper()
    if len(s2) <= 6 and s2.isalnum():
        return True
    if "." in s2 and 1 <= len(s2.split(".", 1)[0]) <= 6:
        return True
    return False

async def optional_call(module: Any, func_name: str, *args, **kwargs):
    """Rufe fmp.<func_name>(*args) auf, wenn vorhanden; sonst [] zurück."""
    func = getattr(module, func_name, None)
    if func is None:
        return []
    try:
        return await func(*args, **kwargs)
    except Exception:
        return []

async def resolve_symbol(user_input: str) -> str:
    """Freitext (z. B. 'SAP' / 'Olvi Oyj') → symbol. Falls schon Ticker, gib ihn zurück."""
    if not user_input:
        return user_input
    s = user_input.strip()
    if looks_like_ticker(s):
        return s.upper()
    # FMP-Suche (falls in fmp.py vorhanden)
    results = await optional_call(fmp, "search_symbol", s, None, 10)
    if isinstance(results, list) and results:
        sym = results[0].get("symbol") or results[0].get("ticker") or s
        return (sym or s).upper()
    return s.upper()

# --- Endpoints ---

@app.get("/health")
async def health():
    return {"ok": True, "model": settings.openai_model}

@app.post("/price")
async def price(req: PriceRequest, authorization: str = Header(None)):
    _auth_check(authorization)
    key = f"price:{req.symbol.upper()}"
    cached = price_cache.get(key)
    if cached:
        return {"symbol": req.symbol.upper(), "data": cached, "cached": True}
    try:
        data = await fmp.get_price_quote(req.symbol)
        price_cache.set(key, data)
        return {"symbol": req.symbol.upper(), "data": data, "cached": False}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/fundamentals")
async def fundamentals(req: FundamentalsRequest, authorization: str = Header(None)):
    _auth_check(authorization)
    key = f"fund:{req.symbol.upper()}:{req.period}:{req.limit}"
    cached = fund_cache.get(key)
    if cached:
        return {"symbol": req.symbol.upper(), "period": req.period, "data": cached, "cached": True}
    try:
        income     = await fmp.get_income_statement(req.symbol, req.period, req.limit)
        balance    = await fmp.get_balance_sheet(req.symbol, "annual" if req.period == "ttm" else req.period, req.limit)
        cashflow   = await fmp.get_cash_flow(req.symbol, req.period, req.limit)
        keymetrics = await fmp.get_key_metrics(req.symbol, req.period, req.limit)
        ratios     = await fmp.get_financial_ratios(req.symbol, req.period, req.limit)
        profile    = await fmp.get_company_profile(req.symbol)
        peers      = await fmp.get_peers(req.symbol)
        bundle = {
            "income": income, "balance": balance, "cashflow": cashflow,
            "key_metrics": keymetrics, "ratios": ratios, "profile": profile, "peers": peers
        }
        fund_cache.set(key, bundle)
        return {"symbol": req.symbol.upper(), "period": req.period, "data": bundle, "cached": False}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/news")
async def news(req: NewsRequest, authorization: str = Header(None)):
    _auth_check(authorization)
    key = f"news:{req.symbol.upper()}:{req.from_date}:{req.to_date}:{req.limit}"
    cached = news_cache.get(key)
    if cached:
        return {"symbol": req.symbol.upper(), "data": cached, "cached": True}
    try:
        data = await fmp.get_company_news(req.symbol, req.from_date, req.to_date, req.limit)
        news_cache.set(key, data)
        return {"symbol": req.symbol.upper(), "data": data, "cached": False}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/resolve")
async def resolve(q: str = Query(..., description="Firmenname oder Teil des Tickers"),
                  exchange: Optional[str] = None,
                  authorization: str = Header(None)):
    _auth_check(authorization)
    results = await optional_call(fmp, "search_symbol", q, exchange, 10)
    return {"query": q, "exchange": exchange, "results": results or []}

@app.post("/analyze")
async def analyze(req: AnalyzeRequest, authorization: str = Header(None)):
    """
    Deep-Dive (Deutsch, Value, KGV zuerst) + 5-Jahres-Historien + Estimates + Dividenden.
    Accepts Firmenname ODER Ticker – löst Namen automatisch zu Symbol auf.
    """
    _auth_check(authorization)

    # Name → Ticker
    symbol = await resolve_symbol((req.symbol or "").strip())

    # --- Daten holen (robust, mit Fallbacks) ---
    try:
        # Basis
        quote   = await try_fetch(lambda: fmp.get_price_quote(symbol), [])
        profile = await try_fetch(lambda: fmp.get_company_profile(symbol), [])

        # TTM (oder Annual-Fallback)
        income_ttm = await try_ttm_then_annual(
            lambda: fmp.get_income_statement(symbol, "ttm", limit=1),
            lambda: fmp.get_income_statement(symbol, "annual", limit=1)
        )
        cashflow_ttm = await try_ttm_then_annual(
            lambda: fmp.get_cash_flow(symbol, "ttm", limit=1),
            lambda: fmp.get_cash_flow(symbol, "annual", limit=1)
        )
        # Balance (kein TTM)
        balance_annual_1 = await try_fetch(lambda: fmp.get_balance_sheet(symbol, "annual", limit=1), [])
        if not balance_annual_1:
            balance_annual_1 = await try_fetch(lambda: fmp.get_balance_sheet(symbol, "quarter", limit=1), [])

        key_metrics_ttm = await try_ttm_then_annual(
            lambda: fmp.get_key_metrics(symbol, "ttm", limit=1),
            lambda: fmp.get_key_metrics(symbol, "annual", limit=1)
        )
        ratios_ttm = await try_ttm_then_annual(
            lambda: fmp.get_financial_ratios(symbol, "ttm", limit=1),
            lambda: fmp.get_financial_ratios(symbol, "annual", limit=1)
        )

        # 5-Jahres-Historien (annual, limit=5)
        income_hist   = await try_fetch(lambda: fmp.get_income_statement(symbol, "annual", limit=5), [])
        balance_hist  = await try_fetch(lambda: fmp.get_balance_sheet(symbol, "annual",  limit=5), [])
        cashflow_hist = await try_fetch(lambda: fmp.get_cash_flow(symbol, "annual",    limit=5), [])
        keym_hist     = await try_fetch(lambda: fmp.get_key_metrics(symbol, "annual",  limit=5), [])
        ratios_hist   = await try_fetch(lambda: fmp.get_financial_ratios(symbol, "annual", limit=5), [])

        # Estimates (EPS/Revenue) – optional
        estimates = await optional_call(fmp, "get_analyst_estimates", symbol, "annual", 8)

        # Dividenden (Historie + Kalender) – optional
        dividends_history = await optional_call(fmp, "get_dividends_history", symbol, 200)
        dividend_calendar = await optional_call(fmp, "get_dividend_calendar", symbol)

        # Extras
        peers      = await try_fetch(lambda: fmp.get_peers(symbol), [])
        news_items = await try_fetch(lambda: fmp.get_company_news(symbol, req.from_date, req.to_date, limit=10), [])
    except Exception:
        quote = profile = income_ttm = cashflow_ttm = balance_annual_1 = key_metrics_ttm = ratios_ttm = []
        income_hist = balance_hist = cashflow_hist = keym_hist = ratios_hist = []
        estimates = dividends_history = dividend_calendar = peers = news_items = []

    # --- Prompt bauen ---
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    prompt_base = ANALYZE_TEMPLATE.format(
        perspective="value",
        language="de",
        symbol=symbol.upper(),
        profile=profile,
        quote=quote,
        key_metrics=key_metrics_ttm,
        ratios=ratios_ttm,
        income=income_ttm,
        balance=balance_annual_1,
        cashflow=cashflow_ttm,
        peers=peers,
        news=news_items[:5],
        as_of=as_of
    )
    extras = f"""

ZUSÄTZLICHE ZEITREIHEN (letzte 5 Jahre, annual; Quelle: FMP):
- Income Statements (5J): {income_hist}
- Balance Sheets (5J): {balance_hist}
- Cash Flow Statements (5J): {cashflow_hist}
- Key Metrics (5J): {keym_hist}
- Financial Ratios (5J): {ratios_hist}

ANALYSTEN-SCHÄTZUNGEN (falls vorhanden; EPS/Revenue):
- Estimates: {estimates}

DIVIDENDEN (falls vorhanden):
- Historie: {dividends_history}
- Anstehende Termine: {dividend_calendar}

Hinweis an dich (Assistent): Nutze Schätzungen für Forward-KGV & EPS-Szenarien.
Wenn Daten fehlen, weise explizit darauf hin und arbeite mit den verfügbaren Werten.
"""
    prompt = prompt_base + extras

    # --- OpenAI ---
    try:
        client = get_client()
        result = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Du bist ein gründlicher Value-Investor. "
                        "Schreibe eine ausführliche, datenfundierte Deep-Dive-Analyse auf Deutsch. "
                        "Beginne mit der P/E (KGV)-Bewertung (historisch & relativ), dann weitere Multiples, "
                        "Qualität, Bilanz, Cashflows, Peers, Risiken, News. "
                        "Wenn Daten fehlen, sag es klar. Keine Anlageberatung."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
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
