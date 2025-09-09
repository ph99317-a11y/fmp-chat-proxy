from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class PriceRequest(BaseModel):
    symbol: str

class FundamentalsRequest(BaseModel):
    symbol: str
    period: str = Field("ttm", description="ttm | annual | quarter")
    limit: int = 1

class NewsRequest(BaseModel):
    symbol: str
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    limit: int = 30

class AnalyzeRequest(BaseModel):
    symbol: str
    # Perioden pro Reporttyp (Balance hat kein T
