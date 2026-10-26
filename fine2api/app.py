"""FastAPI Web Server + REST API + MCP HTTP Endpoint for fine2api."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from fine2api.tavily_engine import TavilyEcommerceEngine
from fine2api.connectors import AffiliateConnectorHub
from fine2api.analyzer import build_intelligence_report
from fine2api.mcp_server import handle_mcp_request, MCP_TOOLS_SCHEMA


app = FastAPI(
    title="fine2api · 全网电商商品智能雷达",
    description="基于 Tavily 多流深度搜索 + 联盟开放接口 + MCP 协议的淘宝/京东/拼多多/闲鱼比价与口碑总结引擎",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


class AnalyzeRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=120, description="商品名称或型号")
    deep_mode: bool = Field(default=True, description="是否开启多流深度搜索")


class BatchCompareRequest(BaseModel):
    keywords: List[str] = Field(..., min_length=1, max_length=6, description="待横向对比的多个商品型号")


@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    hub = AffiliateConnectorHub()
    return {
        "status": "ok",
        "service": "fine2api-cn-ecommerce-radar",
        "version": "1.0.0",
        "connectors": hub.status(),
        "mcp_tools_count": len(MCP_TOOLS_SCHEMA),
    }


@app.post("/api/analyze")
async def analyze_product(req: AnalyzeRequest) -> Dict[str, Any]:
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="商品关键词不能为空")

    engine = TavilyEcommerceEngine()
    if not engine.is_configured():
        raise HTTPException(status_code=503, detail="未配置 TAVILY_API_KEY 环境变量")

    hub = AffiliateConnectorHub()
    streams_task = engine.search_product_omniverse(keyword, deep_mode=req.deep_mode)
    aff_task = hub.fetch_structured_skus(keyword)
    streams_data, aff_items = await asyncio.gather(streams_task, aff_task)

    report = build_intelligence_report(keyword, streams_data, aff_items)
    return report.model_dump()


@app.post("/api/batch-compare")
async def batch_compare(req: BatchCompareRequest) -> Dict[str, Any]:
    keywords = [k.strip() for k in req.keywords if k.strip()][:5]
    if not keywords:
        raise HTTPException(status_code=400, detail="请至少提供一个商品名称")

    engine = TavilyEcommerceEngine()
    if not engine.is_configured():
        raise HTTPException(status_code=503, detail="未配置 TAVILY_API_KEY 环境变量")

    hub = AffiliateConnectorHub()

    async def _analyze_one(kw: str):
        streams = await engine.search_product_omniverse(kw, deep_mode=False)
        aff = await hub.fetch_structured_skus(kw)
        return build_intelligence_report(kw, streams, aff)

    reports = await asyncio.gather(*[_analyze_one(k) for k in keywords])
    best_value = None
    valid_reports = [r for r in reports if r.global_lowest_price is not None]
    if valid_reports:
        best_value = max(valid_reports, key=lambda r: r.buy_timing_score).query

    return {
        "count": len(reports),
        "recommended_pick": best_value,
        "reports": [r.model_dump() for r in reports],
    }


@app.post("/api/mcp")
async def mcp_http_endpoint(payload: Dict[str, Any]) -> JSONResponse:
    resp = await handle_mcp_request(payload)
    return JSONResponse(content=resp)


@app.get("/", response_class=HTMLResponse)
async def index_page() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
