"""CLI interface for fine2api Omni-Platform E-Commerce Intelligence Radar."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from fine2api.tavily_engine import TavilyEcommerceEngine
from fine2api.connectors import AffiliateConnectorHub
from fine2api.analyzer import build_intelligence_report
from fine2api.mcp_server import run_stdio_mcp_server


async def _run_single(keyword: str, as_json: bool = False) -> None:
    engine = TavilyEcommerceEngine()
    hub = AffiliateConnectorHub()
    streams = await engine.search_product_omniverse(keyword, deep_mode=True)
    aff_items = await hub.fetch_structured_skus(keyword)
    report = build_intelligence_report(keyword, streams, aff_items)
    if as_json:
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    else:
        print(report.markdown_report)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fine2api",
        description="国内全平台电商（淘宝/天猫/京东/拼多多/闲鱼/值得买）商品比价与智能口碑总结系统",
    )
    sub = parser.add_subparsers(dest="cmd")

    search_p = sub.add_parser("search", help="搜索并总结单个商品的全网电商行情与口碑")
    search_p.add_argument("keyword", help="商品名称或型号，如 '大疆 Osmo Pocket 3'")
    search_p.add_argument("--json", action="store_true", help="输出结构化 JSON")

    sub.add_parser("mcp", help="启动标准 MCP (stdio) 服务供 Claude / Cursor 调用")

    serve_p = sub.add_parser("serve", help="启动 Web 可视化控制台与 REST API 服务")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    if args.cmd == "mcp":
        run_stdio_mcp_server()
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("fine2api.app:app", host=args.host, port=args.port, reload=False)
    elif args.cmd == "search":
        asyncio.run(_run_single(args.keyword, as_json=args.json))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
