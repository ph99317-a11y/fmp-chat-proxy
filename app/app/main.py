   from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone

from .config import settings
from .cache import TTLCache
from . import fmp
from .schemas import PriceRequest, FundamentalsRequest, NewsRequest, AnalyzeRequest
from .openai_client import get_client
from .prompts import ANALYZE_TEMPLATE

app = FastAPI(title="FMP + ChatGPT Proxy", version="0.3.0")

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
price_cache = TTLCache(60)     # 60s for quotes
news_cache = TTLCache(300)     # 5 min for news
fund_cache = TTLCache(1800)    # 30 min for fundamentals

def _auth_check(authorization: str | None):
    """Simple Bearer check so only your Custom GPT can call the API."""
    if settings.action_key and authorization != f"Bearer {settings.action_key}":
        raise HTTPException(status_code=401, detail="Unauthorized")

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
        income = await fmp.get_income_statement(req.symbol, req.period, req.limit)
        # Balance Sheet hat kein TTM -> bei TTM auf annual ausweichen
        balance = await fmp.get_balance_sheet(
            req.symbol, "annual" if req.period == "ttm" else req.period, req.limit
        )
        cashflow = await fmp.get_cash_flow(req.symbol, req.period, req.limit)
        key_metrics = await fmp.get_key_metrics(req.symbol, req.period, req.limit)
        ratios = await fmp.get_financial_ratios(req.symbol, req.period, req.limit)
        profile = await fmp.get_company_profile(req.symbol)
        peers = await fmp.get_peers(req.symbol)
        bundle = {
            "income": income,
            "balance": balance,
            "cashflow": cashflow,
            "key_metrics": key_metrics,
            "ratios": ratios,
            "profile": profile,
            "peers": peers,
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

@app.post("/analyze")
async def analyze(req: AnalyzeRequest, authorization: str = Header(None)):
    """
    Erzeugt eine ausführliche Deep-Dive-Analyse (Deutsch) im Value-Stil.
    Reihenfolge: P/E (KGV) zuerst, dann weitere Multiples, Qualität, Bilanz, Cashflows, Peers, Risiken, News.
    """
    _auth_check(authorization)

    # --- Daten holen ---
    try:
        quote = await fmp.get_price_quote(req.symbol)
        profile = await fmp.get_company_profile(req.symbol)
        income = await fmp.get_income_statement(req.symbol, req.period_income, limit=1)
        balance = await fmp.get_balance_sheet(req.symbol, req.period_balance, limit=1)
        cashflow = await fmp.get_cash_flow(req.symbol, req.period_cashflow, limit=1)
        key_metrics = await fmp.get_key_metrics(req.symbol, req.period_income, limit=1)
        ratios = await fmp.get_financial_ratios(req.symbol, req.period_ratios, limit=1)
        peers = await fmp.get_peers(req.symbol)
        news_items = await fmp.get_company_news(req.symbol, req.from_date, req.to_date, limit=10)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Data fetch failed: {str(e)}")

    # --- Prompt bauen (Deutsch & Value-Fokus erzwingen) ---
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    prompt = ANALYZE_TEMPLATE.format(
        perspective="value",           # Value-Fokus erzwingen
        language="de",                 # immer Deutsch
        symbol=req.symbol.upper(),
        profile=profile,
        quote=quote,
        key_metrics=key_metrics,
        ratios=ratios,
        income=income,
        balance=balance,
        cashflow=cashflow,
        peers=peers,
        news=news_items[:5],
        as_of=as_of
    )

    # --- OpenAI aufrufen (viel Kontext für Deep-Dive) ---
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
                        "Beginne mit der P/E (KGV)-Bewertung, vergleiche historisch (5/10 Jahre) und relativ zu Peers. "
                        "Stets: Keine Anlageberatung."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2000,  # mehr Platz für Deep-Dive
        )
        content = result.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")

    return {
        "symbol": req.symbol.upper(),
        "as_of_utc": as_of,
        "model": settings.openai_model,
        "analysis_md": content,
    }
