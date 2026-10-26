"""Standard JSON-RPC 2.0 MCP Server (Model Context Protocol) for AI Agents.

Can be mounted in Claude Desktop / Cursor / Windsurf or called via HTTP `/api/mcp`.
Exposes 3 specialized tools:
  1. compare_cn_ecommerce_prices
  2. summarize_product_reviews
  3. batch_compare_products
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, List

from fine2api.tavily_engine import TavilyEcommerceEngine
from fine2api.connectors import AffiliateConnectorHub
from fine2api.analyzer import build_intelligence_report


MCP_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": "compare_cn_ecommerce_prices",
        "description": "全方位搜索并总结国内各大电商平台（淘宝、天猫、京东、拼多多、闲鱼、什么值得买）的商品价格、历史低价、百亿补贴/国补到手价及选购建议。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "商品名称或型号，如 'iPhone 16 Pro 256G' 或 '大疆 Osmo Pocket 3'",
                },
                "deep_mode": {
                    "type": "boolean",
                    "description": "是否开启多流深度检索模式",
                    "default": True,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "summarize_product_reviews",
        "description": "聚合知乎、什么值得买、V2EX、B站、小红书等社区的真实买家评测，提取商品优缺点、通病与避坑指南。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "商品名称或型号",
                }
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "batch_compare_products",
        "description": "批量对比多个同品类候选商品在淘宝、京东、拼多多的全网最低价、中位价与入手推荐指数。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "候选商品列表，例如 ['索尼 WH-1000XM5', 'Bose QC Ultra', 'AirPods Max']",
                }
            },
            "required": ["keywords"],
        },
    },
]


async def handle_mcp_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    req_id = payload.get("id")
    method = payload.get("method", "")
    params = payload.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fine2api-cn-ecommerce-radar", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS_SCHEMA},
        }

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments") or {}
        engine = TavilyEcommerceEngine()
        hub = AffiliateConnectorHub()

        if tool_name in ("compare_cn_ecommerce_prices", "summarize_product_reviews"):
            kw = str(args.get("keyword", "")).strip()
            if not kw:
                return _mcp_error(req_id, -32602, "Missing required argument: keyword")
            streams = await engine.search_product_omniverse(
                kw, deep_mode=bool(args.get("deep_mode", True))
            )
            aff_items = await hub.fetch_structured_skus(kw)
            report = build_intelligence_report(kw, streams, aff_items)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": report.markdown_report}],
                    "structuredReport": report.model_dump(),
                },
            }

        if tool_name == "batch_compare_products":
            kws = [str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()][:5]
            if not kws:
                return _mcp_error(req_id, -32602, "Missing required argument: keywords")

            async def _one(k: str):
                s = await engine.search_product_omniverse(k, deep_mode=False)
                a = await hub.fetch_structured_skus(k)
                return build_intelligence_report(k, s, a)

            reports = await asyncio.gather(*[_one(k) for k in kws])
            md_lines = [
                "# 📊 批量商品横评比价汇总",
                "| 商品型号 | 全网最低价 | 最低价平台 | 全网中位价 | 二手参考价 | 推荐指数 | 核心选购建议 |",
                "| :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
            ]
            for r in reports:
                low = f"¥{r.global_lowest_price:.0f}" if r.global_lowest_price else "-"
                med = f"¥{r.global_median_price:.0f}" if r.global_median_price else "-"
                sec = f"¥{r.secondhand_estimate:.0f}" if r.secondhand_estimate else "-"
                md_lines.append(
                    f"| **{r.query}** | {low} | {r.global_lowest_platform or '-'} | {med} | {sec} | **{r.buy_timing_score}/100** | {r.buy_verdict[:55]}... |"
                )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": "\n".join(md_lines)}],
                    "reports": [r.model_dump() for r in reports],
                },
            }

        return _mcp_error(req_id, -32601, f"Unknown tool: {tool_name}")

    return _mcp_error(req_id, -32601, f"Unsupported method: {method}")


def _mcp_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def run_stdio_mcp_server() -> None:
    """Run MCP server over stdin/stdout for local AI Agent integration."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            resp = asyncio.run(handle_mcp_request(payload))
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            err = _mcp_error(None, -32700, str(exc))
            sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_mcp_server()
