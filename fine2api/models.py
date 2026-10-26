from __future__ import annotations

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class PriceMention(BaseModel):
    platform: str = Field(..., description="电商平台名称，如 京东、淘宝/天猫、拼多多、闲鱼、什么值得买")
    platform_key: str = Field(..., description="平台标识: jd, taobao, pdd, xianyu, smzdm, other")
    price: float = Field(..., description="识别到的人民币价格 (元)")
    price_tag: str = Field(default="日常/活动价", description="价格类型，如 百亿补贴、国补到手价、历史低价、日常价、二手均价")
    context: str = Field(default="", description="价格出现的原始上下文摘录")
    source_title: str = Field(default="", description="来源标题")
    source_url: str = Field(default="", description="来源链接")
    published_date: Optional[str] = Field(default=None, description="发布日期或时间")


class PlatformSummary(BaseModel):
    platform: str
    platform_key: str
    lowest_price: Optional[float] = None
    median_price: Optional[float] = None
    highest_price: Optional[float] = None
    sample_count: int = 0
    best_deal_tag: str = "暂无直接报价"
    best_deal_context: str = ""
    best_deal_url: str = ""
    subsidy_tips: List[str] = Field(default_factory=list)


class SentimentPoint(BaseModel):
    category: str = Field(..., description="pros (优点) | cons (缺点/通病) | pitfall (避坑建议)")
    point: str
    source_title: str = ""
    source_url: str = ""


class SourceEvidence(BaseModel):
    stream: str = Field(..., description="检索流类型: deals | platforms | reviews | affiliate")
    platform_badge: str
    title: str
    url: str
    snippet: str
    score: float = 0.0
    extracted_prices: List[float] = Field(default_factory=list)


class ProductIntelligenceReport(BaseModel):
    query: str
    generated_at: str
    search_mode: str = "tavily_multistream"
    global_lowest_price: Optional[float] = None
    global_lowest_platform: Optional[str] = None
    global_median_price: Optional[float] = None
    historical_low_estimate: Optional[float] = None
    secondhand_estimate: Optional[float] = None
    buy_timing_score: int = Field(default=75, description="0-100 入手时机推荐指数")
    buy_verdict: str = Field(default="", description="一针见血的选购结论与渠道建议")
    executive_summary: str = Field(default="", description="多流聚合 AI 总结")
    platform_matrix: List[PlatformSummary] = Field(default_factory=list)
    price_mentions: List[PriceMention] = Field(default_factory=list)
    pros: List[SentimentPoint] = Field(default_factory=list)
    cons: List[SentimentPoint] = Field(default_factory=list)
    pitfalls: List[SentimentPoint] = Field(default_factory=list)
    sources: List[SourceEvidence] = Field(default_factory=list)
    markdown_report: str = Field(default="", description="可直接导出或复制的完整 Markdown 深度报告")
    stats: Dict[str, Any] = Field(default_factory=dict)
