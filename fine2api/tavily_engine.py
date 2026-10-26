"""Multi-Stream Tavily E-Commerce Search Engine for Chinese Platforms."""

from __future__ import annotations

import asyncio
import os
from typing import Dict, Any, List, Optional
import httpx


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilyEcommerceEngine:
    """Executes parallel, domain-targeted Tavily search streams for Chinese E-Commerce."""

    def __init__(self, api_key: Optional[str] = None, timeout: float = 28.0) -> None:
        self.api_key = (api_key or os.environ.get("TAVILY_API_KEY") or "").strip()
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _search_stream(
        self,
        client: httpx.AsyncClient,
        stream_name: str,
        query: str,
        include_domains: Optional[List[str]] = None,
        max_results: int = 8,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": True,
            "include_raw_content": False,
        }
        if include_domains:
            payload["include_domains"] = include_domains

        try:
            resp = await client.post(TAVILY_SEARCH_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "stream": stream_name,
                "query": query,
                "answer": data.get("answer") or "",
                "results": data.get("results") or [],
                "error": None,
            }
        except Exception as exc:
            return {
                "stream": stream_name,
                "query": query,
                "answer": "",
                "results": [],
                "error": str(exc),
            }

    async def search_product_omniverse(
        self,
        keyword: str,
        focus_platforms: Optional[List[str]] = None,
        deep_mode: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """Run 3 to 4 specialized concurrent Tavily search streams for a product."""
        if not self.is_configured():
            raise RuntimeError("未检测到 TAVILY_API_KEY 环境变量，请先配置 Tavily API Key。")

        streams = [
            (
                "deals",
                f"{keyword} 京东 淘宝 天猫 拼多多 历史低价 百亿补贴 国补 到手价 多少钱 什么值得买",
                ["smzdm.com", "manmanbuy.com", "jd.com", "tmall.com", "taobao.com", "yangkeduo.com"],
                10 if deep_mode else 6,
            ),
            (
                "platforms",
                f"{keyword} 京东自营 天猫旗舰店 拼多多百亿补贴 闲鱼二手 价格对比 哪个平台便宜",
                None,
                8 if deep_mode else 5,
            ),
            (
                "reviews",
                f"{keyword} 深度评测 真实体验 优缺点 通病 避坑 翻车 值得买吗",
                ["zhihu.com", "smzdm.com", "v2ex.com", "bilibili.com", "xiaohongshu.com"],
                8 if deep_mode else 5,
            ),
        ]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            tasks = [
                self._search_stream(client, name, q, domains, limit)
                for name, q, domains, limit in streams
            ]
            completed = await asyncio.gather(*tasks)

        return {item["stream"]: item for item in completed}
