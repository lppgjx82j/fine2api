from __future__ import annotations

import asyncio
from fastapi.testclient import TestClient

from fine2api.app import app
from fine2api.analyzer import build_intelligence_report
from fine2api.mcp_server import handle_mcp_request


MOCK_STREAMS = {
    "deals": {
        "stream": "deals",
        "query": "大疆 Osmo Pocket 3",
        "answer": "京东国补后低至1886元，天猫百亿补贴低至1698元。",
        "results": [
            {
                "title": "大疆 Osmo Pocket 3 京东百亿补贴好价",
                "url": "https://www.smzdm.com/p/1001",
                "content": "京东目前活动售价2399元，百亿补贴立减300元券，叠加国补后实付低至1886元。淘宝天猫旗舰店88VIP到手价约2199元。",
                "score": 0.95,
            },
            {
                "title": "拼多多百亿补贴大疆Pocket 3史低",
                "url": "https://www.smzdm.com/p/1002",
                "content": "拼多多百亿补贴三人团到手价1799元，闲鱼二手近7日成交均价1950元。",
                "score": 0.91,
            },
        ],
    },
    "platforms": {
        "stream": "platforms",
        "query": "大疆 Osmo Pocket 3 平台对比",
        "answer": "各平台价格在1799元至2799元之间。",
        "results": [
            {
                "title": "Pocket 3 全渠道比价",
                "url": "https://post.smzdm.com/p/2001",
                "content": "官方原价3499元，京东日常售价2799元。",
                "score": 0.88,
            }
        ],
    },
    "reviews": {
        "stream": "reviews",
        "query": "大疆 Osmo Pocket 3 评测",
        "answer": "一英寸大底夜景画质出色，旋转屏方便。",
        "results": [
            {
                "title": "Pocket 3 深度使用体验",
                "url": "https://zhuanlan.zhihu.com/p/3001",
                "content": "优点是一英寸传感器夜景画质好，三轴云台防抖稳定。缺点是云台排线比较脆弱，价格长期溢价偏贵。避坑建议优先选官方随心换保障。",
                "score": 0.92,
            }
        ],
    },
}


def test_price_extractor_filters_coupons_and_builds_matrix():
    report = build_intelligence_report("大疆 Osmo Pocket 3", MOCK_STREAMS)
    assert report.global_lowest_price is not None
    assert report.global_lowest_price >= 1500
    assert report.secondhand_estimate == 1950.0
    assert len(report.platform_matrix) == 5
    assert len(report.pros) >= 1
    assert len(report.cons) >= 1
    assert len(report.pitfalls) >= 1
    assert "大疆 Osmo Pocket 3" in report.markdown_report


def test_health_and_mcp_tools_list():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["mcp_tools_count"] == 3

    mcp_resp = asyncio.run(
        handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    )
    assert mcp_resp["id"] == 1
    tools = mcp_resp["result"]["tools"]
    assert len(tools) == 3
    assert tools[0]["name"] == "compare_cn_ecommerce_prices"
