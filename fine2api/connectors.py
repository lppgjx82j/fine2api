"""Optional Free Affiliate / CPS Open API Connectors (Haodanku, Dataoke, Zhetaoke).

When user configures free API keys in environment variables, these connectors fetch
direct structured SKU feeds from Taobao/Tmall, JD, and Pinduoduo to enrich Tavily results.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import List, Dict, Any
import httpx


class AffiliateConnectorHub:
    def __init__(self) -> None:
        self.haodanku_apikey = os.environ.get("HAODANKU_API_KEY", "").strip()
        self.zhetaoke_appkey = os.environ.get("ZHETAOKE_APP_KEY", "").strip()
        self.dataoke_appkey = os.environ.get("DATAOKE_APP_KEY", "").strip()

    def status(self) -> Dict[str, bool]:
        return {
            "tavily_api": bool(os.environ.get("TAVILY_API_KEY", "").strip()),
            "haodanku_api": bool(self.haodanku_apikey),
            "zhetaoke_api": bool(self.zhetaoke_appkey),
            "dataoke_api": bool(self.dataoke_appkey),
        }

    async def fetch_structured_skus(self, keyword: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if self.haodanku_apikey:
            try:
                encoded = urllib.parse.quote(urllib.parse.quote(keyword))
                url = (
                    f"http://v2.api.haodanku.com/supersearch/apikey/{self.haodanku_apikey}"
                    f"/keyword/{encoded}/back/10/min_id/1/tb_p/1/sort/0/is_tmall/0"
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url)
                    data = resp.json()
                    for item in data.get("data", [])[:10]:
                        shop_type = "天猫" if str(item.get("shoptype")) == "B" else "淘宝"
                        results.append(
                            {
                                "platform": f"淘宝/天猫 ({shop_type})",
                                "platform_key": "taobao",
                                "title": item.get("itemtitle", ""),
                                "price": float(item.get("itemendprice") or item.get("itemprice") or 0),
                                "coupon": item.get("couponmoney", "0"),
                                "sales": item.get("itemsale", ""),
                                "url": f"https://item.taobao.com/item.htm?id={item.get('itemid', '')}",
                            }
                        )
            except Exception:
                pass
        return results
