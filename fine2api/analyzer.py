"""Price Matrix Extractor, Subsidy & Historical-Low Detector, and Review Synthesizer."""

from __future__ import annotations

import datetime
import re
import statistics
from typing import Dict, Any, List, Optional, Tuple

from fine2api.models import (
    PriceMention,
    PlatformSummary,
    SentimentPoint,
    SourceEvidence,
    ProductIntelligenceReport,
)


PLATFORM_RULES: List[Tuple[str, str, List[str]]] = [
    ("jd", "京东 (自营/第三方)", ["京东", "jd.com", "狗东", "plus会员", "京东百亿补贴"]),
    ("taobao", "淘宝 / 天猫", ["淘宝", "天猫", "tmall.com", "taobao.com", "88vip", "猫超", "天猫超市", "淘金币"]),
    ("pdd", "拼多多 (百亿补贴)", ["拼多多", "多多", "pdd", "yangkeduo.com", "百亿补贴三人团", "多多百亿补贴"]),
    ("xianyu", "闲鱼 (二手参考)", ["闲鱼", "二手", "成色", "近7日成交均价", "仅拆封"]),
    ("smzdm", "什么值得买 / 导购好价", ["什么值得买", "smzdm.com", "值友", "好价", "慢慢买", "manmanbuy"]),
]

PRICE_REGEX = re.compile(
    r"(?:(?:低至|到手价?|实付|售价|补贴价?|折后|原价|日常价?|均价|好价|直降后|价格为?|现价|首发价?)[:：\s约为]*"
    r"(?:¥|￥|RMB)?\s*(\d{2,6}(?:\.\d{1,2})?)\s*(?:元|块)?)"
    r"|(?:(?:¥|￥)\s*(\d{2,6}(?:\.\d{1,2})?))"
    r"|(?:(\d{2,6}(?:\.\d{1,2})?)\s*元(?:起|到手|包邮)?)"
)

DISCOUNT_CONTEXT_HINTS = [
    "立减", "直降", "至高直降", "最高直降", "狂降", "大降", "省", "已省",
    "优惠券", "代金券", "消费券", "补贴券", "金刚券", "返现", "返卡", "返e卡",
    "定金", "尾款", "满", "红包", "津贴", "膨胀", "差价", "加价", "折合"
]

ACCESSORY_OR_NOISE_HINTS = [
    "续航手柄", "三脚架", "收纳包", "内存卡", "钢化膜", "保护壳", "滤镜", "nd镜", "增广镜", "麦克风", "随心换", "租"
]

PRICE_TAG_RULES = [
    ("国补到手价", ["国补", "国家补贴", "以旧换新补贴", "政府补贴"]),
    ("百亿补贴价", ["百亿补贴", "百补"]),
    ("历史低价/神价", ["历史低价", "史低", "神价", "最低", "新低", "跌至", "低至"]),
    ("二手成交参考", ["二手", "闲鱼", "成交均价", "回收"]),
    ("首发/原价", ["原价", "首发价", "官方定价", "发售价", "面价"]),
    ("券后/活动到手价", ["到手", "实付", "券后", "折后", "plus", "88vip"]),
]

PROS_KEYWORDS = [
    "优点", "优势", "出色", "惊艳", "提升明显", "续航强", "性价比高", "值得买", "真香", "好评",
    "舒服", "稳定", "清晰", "流畅", "质感好", "降噪强", "画质好"
]

CONS_KEYWORDS = [
    "缺点", "不足", "通病", "槽点", "遗憾", "发热", "续航尿崩", "品控", "偏贵", "溢价",
    "不折叠", "沉重", "售后差", "断连", "噪音", "缩水"
]

PITFALL_KEYWORDS = [
    "避坑", "注意", "千万别", "建议选", "防伪", "翻新", "后封", "砍单", "保修", "发票", "鉴别"
]


def _detect_platforms_in_text(text: str, url: str = "") -> List[Tuple[str, str]]:
    combined = f"{text} {url}".lower()
    matched: List[Tuple[str, str]] = []
    for key, label, keywords in PLATFORM_RULES:
        if any(k in combined for k in keywords):
            matched.append((key, label))
    return matched


def _detect_badge_for_url(url: str, text: str) -> str:
    u = url.lower()
    if "smzdm.com" in u:
        return "什么值得买"
    if "jd.com" in u:
        return "京东"
    if "tmall.com" in u or "taobao.com" in u:
        return "淘宝/天猫"
    if "yangkeduo.com" in u:
        return "拼多多"
    if "zhihu.com" in u:
        return "知乎评测"
    if "v2ex.com" in u:
        return "V2EX社区"
    if "bilibili.com" in u:
        return "B站测评"
    if "xiaohongshu.com" in u:
        return "小红书"
    detected = _detect_platforms_in_text(text, url)
    if detected:
        return detected[0][1].split(" ")[0]
    return "全网资讯"


def _classify_price_tag(sentence: str) -> str:
    s_lower = sentence.lower()
    for tag, words in PRICE_TAG_RULES:
        if any(w in s_lower for w in words):
            return tag
    return "日常/活动价"


def _is_likely_discount_delta(clause: str, raw_val: float, match_span: Tuple[int, int]) -> bool:
    start, end = match_span
    prefix = clause[max(0, start - 10) : start]
    suffix = clause[end : min(len(clause), end + 10)]
    if any(k in prefix for k in ("低至", "实付", "到手", "售价", "均价", "原价", "现价", "折后")):
        return False
    for hint in DISCOUNT_CONTEXT_HINTS:
        if hint in prefix:
            return True
    if any(w in suffix for w in ["券", "优惠", "补贴券", "红包", "津贴", "巅峰"]):
        return True
    if "满" in prefix or prefix.endswith("减"):
        return True
    if "领券" in clause and "券" in suffix:
        return True
    return False


def _extract_keyword_tokens(keyword: str) -> List[str]:
    tokens = [
        t.lower()
        for t in re.split(r"[\s/\-_,，（）()]+", keyword.strip())
        if len(t.strip()) >= 2
    ]
    return tokens or [keyword.lower().strip()]


def _is_relevant_to_keyword(keyword_tokens: List[str], title: str, sentence: str) -> bool:
    combined = f"{title} {sentence}".lower()
    matched = sum(1 for t in keyword_tokens if t in combined)
    if matched == 0:
        return False
    if len(keyword_tokens) >= 2 and matched < 1:
        return False
    return True


def extract_price_candidates(
    keyword: str,
    streams_data: Dict[str, Dict[str, Any]],
    affiliate_items: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[PriceMention], List[SourceEvidence]]:
    raw_candidates: List[Dict[str, Any]] = []
    sources: List[SourceEvidence] = []
    seen_urls: set[str] = set()
    kw_tokens = _extract_keyword_tokens(keyword)

    for stream_name, sdata in streams_data.items():
        for item in sdata.get("results", []):
            url = item.get("url") or ""
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            score = float(item.get("score") or 0.0)
            full_text = f"{title}。{content}"
            if not _is_relevant_to_keyword(kw_tokens, title, content):
                continue

            sentences = re.split(r"[。！？!\n；;]", full_text)
            extracted_in_doc: List[float] = []

            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                if any(acc in sent.lower() for acc in ACCESSORY_OR_NOISE_HINTS) and "套装" not in sent:
                    continue
                if not _is_relevant_to_keyword(kw_tokens, title, sent):
                    continue
                clauses = [c.strip() for c in re.split(r"[，,]", sent) if c.strip()]
                active_platforms = _detect_platforms_in_text(sent, url)
                for clause in clauses:
                    clause_platforms = _detect_platforms_in_text(clause, "")
                    if clause_platforms:
                        active_platforms = clause_platforms
                    for m in PRICE_REGEX.finditer(clause):
                        val_str = m.group(1) or m.group(2) or m.group(3)
                        if not val_str:
                            continue
                        try:
                            val = float(val_str)
                        except ValueError:
                            continue
                        if val < 15 or val > 150000:
                            continue
                        end_pos = m.end()
                        if val in (2023, 2024, 2025, 2026, 2027) and end_pos < len(clause) and clause[end_pos : end_pos + 1] == "年":
                            continue
                        if _is_likely_discount_delta(clause, val, m.span()):
                            continue

                        platforms = clause_platforms or active_platforms or _detect_platforms_in_text(full_text, url) or [("smzdm", "什么值得买 / 导购好价")]
                        clause_tag = _classify_price_tag(clause)
                        price_tag = clause_tag if clause_tag != "日常/活动价" else _classify_price_tag(sent)
                        extracted_in_doc.append(val)

                        for p_key, p_label in platforms:
                            raw_candidates.append(
                                {
                                    "platform": p_label,
                                    "platform_key": p_key,
                                    "price": val,
                                    "price_tag": price_tag,
                                    "context": sent[:160],
                                    "source_title": title[:120],
                                    "source_url": url,
                                }
                            )

            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append(
                    SourceEvidence(
                        stream=stream_name,
                        platform_badge=_detect_badge_for_url(url, full_text),
                        title=title or url,
                        url=url,
                        snippet=content[:260],
                        score=round(score, 3),
                        extracted_prices=sorted(list(set(extracted_in_doc)))[:6],
                    )
                )

    if affiliate_items:
        for aitem in affiliate_items:
            p = float(aitem.get("price") or 0)
            if p > 0:
                raw_candidates.append(
                    {
                        "platform": aitem.get("platform", "淘宝/天猫"),
                        "platform_key": aitem.get("platform_key", "taobao"),
                        "price": p,
                        "price_tag": "联盟实时券后价",
                        "context": f"{aitem.get('title', '')} (券: ¥{aitem.get('coupon', 0)}, 销量: {aitem.get('sales', '未知')})",
                        "source_title": aitem.get("title", ""),
                        "source_url": aitem.get("url", ""),
                    }
                )

    if len(raw_candidates) >= 3:
        all_vals = sorted(c["price"] for c in raw_candidates)
        med = statistics.median(all_vals)
        filtered_candidates = [
            c for c in raw_candidates if (0.52 * med <= c["price"] <= 1.85 * med)
        ]
        if len(filtered_candidates) >= 2:
            raw_candidates = filtered_candidates

    deduped: List[PriceMention] = []
    seen_keys: set[Tuple[str, float, str]] = set()
    for c in sorted(raw_candidates, key=lambda x: x["price"]):
        k = (c["platform_key"], round(c["price"], 2), c["source_url"])
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(PriceMention(**c))

    if deduped:
        valid_min = min(m.price for m in deduped)
        valid_max = max(m.price for m in deduped)
        for src in sources:
            src.extracted_prices = [
                p for p in src.extracted_prices if valid_min <= p <= valid_max
            ]

    return deduped, sources


def build_platform_matrix(mentions: List[PriceMention]) -> List[PlatformSummary]:
    target_platforms = [
        ("jd", "京东 (自营 / 百亿补贴)"),
        ("taobao", "淘宝 / 天猫 (旗舰店 / 88VIP)"),
        ("pdd", "拼多多 (百亿补贴 / 品牌黑标)"),
        ("xianyu", "闲鱼 (二手行情参考)"),
        ("smzdm", "什么值得买 (全网历史好价汇总)"),
    ]

    matrix: List[PlatformSummary] = []
    for p_key, p_name in target_platforms:
        p_mentions = [m for m in mentions if m.platform_key == p_key]
        if not p_mentions:
            matrix.append(
                PlatformSummary(
                    platform=p_name,
                    platform_key=p_key,
                    sample_count=0,
                    best_deal_tag="暂未检出直接报价",
                    subsidy_tips=_default_platform_tips(p_key),
                )
            )
            continue

        prices = sorted(m.price for m in p_mentions)
        best = min(p_mentions, key=lambda m: m.price)
        matrix.append(
            PlatformSummary(
                platform=p_name,
                platform_key=p_key,
                lowest_price=round(prices[0], 2),
                median_price=round(statistics.median(prices), 2),
                highest_price=round(prices[-1], 2),
                sample_count=len(prices),
                best_deal_tag=best.price_tag,
                best_deal_context=best.context,
                best_deal_url=best.source_url,
                subsidy_tips=_default_platform_tips(p_key),
            )
        )
    return matrix


def _default_platform_tips(platform_key: str) -> List[str]:
    tips_map = {
        "jd": ["优先看京东自营 + 国家补贴(立减15%~20%)", "叠加 PLUS 会员超级补贴券与以旧换新补贴"],
        "taobao": ["关注天猫官方旗舰店 88VIP 95折 + 消费券", "淘宝百亿补贴频道可叠金刚券与淘金币抵扣"],
        "pdd": ["认准「百亿补贴」带品牌黑标与中国人保正品险店铺", "夜黑风高或三人团通常可刷新全网史低价"],
        "xianyu": ["警惕全新未拆封低价引流贴，优先看个人卖家实拍与信誉", "对照拼多多百亿补贴价，若差价不足15%建议买全新"],
        "smzdm": ["参考值友爆料凑单路径（跨店满减 + 品类券 + 支付立减）", "查看点值率与评论区真实到货反馈"],
    }
    return tips_map.get(platform_key, [])


def extract_sentiment_and_pitfalls(
    streams_data: Dict[str, Dict[str, Any]]
) -> Tuple[List[SentimentPoint], List[SentimentPoint], List[SentimentPoint]]:
    pros: List[SentimentPoint] = []
    cons: List[SentimentPoint] = []
    pitfalls: List[SentimentPoint] = []

    seen_sentences: set[str] = set()

    for stream_name in ("reviews", "platforms", "deals"):
        sdata = streams_data.get(stream_name, {})
        for item in sdata.get("results", []):
            url = item.get("url") or ""
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            sentences = re.split(r"[。！？!\n]", f"{title}。{content}")

            for sent in sentences:
                s = sent.strip()
                if len(s) < 12 or len(s) > 130:
                    continue
                norm = re.sub(r"\s+", "", s)
                if norm in seen_sentences:
                    continue

                if any(k in s for k in PITFALL_KEYWORDS) and len(pitfalls) < 6:
                    seen_sentences.add(norm)
                    pitfalls.append(
                        SentimentPoint(
                            category="pitfall",
                            point=s,
                            source_title=title[:80],
                            source_url=url,
                        )
                    )
                elif any(k in s for k in CONS_KEYWORDS) and len(cons) < 6:
                    seen_sentences.add(norm)
                    cons.append(
                        SentimentPoint(
                            category="cons",
                            point=s,
                            source_title=title[:80],
                            source_url=url,
                        )
                    )
                elif any(k in s for k in PROS_KEYWORDS) and len(pros) < 6:
                    seen_sentences.add(norm)
                    pros.append(
                        SentimentPoint(
                            category="pros",
                            point=s,
                            source_title=title[:80],
                            source_url=url,
                        )
                    )

    return pros, cons, pitfalls


def build_intelligence_report(
    keyword: str,
    streams_data: Dict[str, Dict[str, Any]],
    affiliate_items: Optional[List[Dict[str, Any]]] = None,
) -> ProductIntelligenceReport:
    mentions, sources = extract_price_candidates(keyword, streams_data, affiliate_items)
    matrix = build_platform_matrix(mentions)
    pros, cons, pitfalls = extract_sentiment_and_pitfalls(streams_data)

    primary_retail = [m for m in mentions if m.platform_key in ("jd", "taobao", "pdd")]
    retail_mentions = primary_retail if primary_retail else [m for m in mentions if m.platform_key in ("jd", "taobao", "pdd", "smzdm")]
    secondhand_mentions = [m for m in mentions if m.platform_key == "xianyu" or "二手" in m.price_tag]

    global_lowest: Optional[float] = None
    global_lowest_platform: Optional[str] = None
    global_median: Optional[float] = None
    hist_low: Optional[float] = None
    secondhand_est: Optional[float] = None

    if retail_mentions:
        best_m = min(retail_mentions, key=lambda x: x.price)
        global_lowest = round(best_m.price, 2)
        global_lowest_platform = best_m.platform
        global_median = round(statistics.median(m.price for m in retail_mentions), 2)
        hist_candidates = [
            m.price
            for m in retail_mentions
            if any(t in m.price_tag for t in ("历史低价", "国补", "百亿补贴"))
        ]
        hist_low = round(min(hist_candidates) if hist_candidates else global_lowest, 2)

    if secondhand_mentions:
        secondhand_est = round(statistics.median(m.price for m in secondhand_mentions), 2)

    buy_score = 78
    if global_lowest and global_median and global_median > 0:
        discount_ratio = (global_median - global_lowest) / global_median
        buy_score = min(96, max(55, int(72 + discount_ratio * 80)))

    deals_ans = (streams_data.get("deals", {}).get("answer") or "").strip()
    plat_ans = (streams_data.get("platforms", {}).get("answer") or "").strip()
    rev_ans = (streams_data.get("reviews", {}).get("answer") or "").strip()

    summary_parts = []
    if global_lowest and global_median:
        summary_parts.append(
            f"【全网比价结论】针对「{keyword}」，系统并发扫描了京东、淘宝/天猫、拼多多、闲鱼及什么值得买等核心渠道，"
            f"共提取到 {len(mentions)} 条有效价格样本与 {len(sources)} 篇高相关来源。"
            f"当前全网综合中位价约为 ¥{global_median:.0f}，监测到的最低活动/补贴到手价可低至 ¥{global_lowest:.0f}（来源渠道：{global_lowest_platform}）。"
        )
    else:
        summary_parts.append(
            f"【全网检索结论】已针对「{keyword}」完成多平台深度扫描，共聚合 {len(sources)} 条电商与社区情报。"
        )

    if deals_ans:
        summary_parts.append(f"【好价与补贴情报】{deals_ans}")
    elif plat_ans:
        summary_parts.append(f"【多平台行情动态】{plat_ans}")

    if rev_ans:
        summary_parts.append(f"【口碑与评测摘要】{rev_ans}")

    executive_summary = "\n\n".join(summary_parts)

    if global_lowest and global_median:
        spread = global_median - global_lowest
        verdict = (
            f"推荐优先关注【{global_lowest_platform}】的百亿补贴或国补渠道（目标好价区间 ¥{global_lowest:.0f} ~ ¥{ (global_lowest + global_median)/2 :.0f}），"
            f"相比日常中位价（¥{global_median:.0f}）最高可省约 ¥{spread:.0f}；若注重极速退换货与官方发票保障，建议在京东自营叠加国补/PLUS券入手。"
        )
    else:
        verdict = "建议对比京东自营（国补/PLUS券）与拼多多/淘宝百亿补贴实时到手价后下单。"

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    report = ProductIntelligenceReport(
        query=keyword,
        generated_at=now_str,
        global_lowest_price=global_lowest,
        global_lowest_platform=global_lowest_platform,
        global_median_price=global_median,
        historical_low_estimate=hist_low,
        secondhand_estimate=secondhand_est,
        buy_timing_score=buy_score,
        buy_verdict=verdict,
        executive_summary=executive_summary,
        platform_matrix=matrix,
        price_mentions=mentions[:25],
        pros=pros,
        cons=cons,
        pitfalls=pitfalls,
        sources=sources,
        stats={
            "total_sources": len(sources),
            "total_price_samples": len(mentions),
            "streams_executed": len(streams_data),
        },
    )
    report.markdown_report = render_markdown_report(report)
    return report


def render_markdown_report(report: ProductIntelligenceReport) -> str:
    lines = [
        f"# 🛒 全网电商商品深度情报报告：{report.query}",
        f"> 生成时间：{report.generated_at} | 检索引擎：Tavily Multi-Stream + 导购聚合雷达 | 入手推荐指数：**{report.buy_timing_score}/100**",
        "",
        "## 1. 核心选购结论 (Executive Verdict)",
        f"- **一句话建议**：{report.buy_verdict}",
        f"- **全网最低好价参考**：{f'¥{report.global_lowest_price:.2f} ({report.global_lowest_platform})' if report.global_lowest_price else '暂无'}",
        f"- **全网日常中位价**：{f'¥{report.global_median_price:.2f}' if report.global_median_price else '暂无'}",
        f"- **二手(闲鱼)参考均价**：{f'¥{report.secondhand_estimate:.2f}' if report.secondhand_estimate else '暂无直接二手均价'}",
        "",
        "## 2. 跨平台比价矩阵 (淘宝 / 京东 / 拼多多 / 闲鱼 / 值得买)",
        "| 电商平台 | 最低好价 | 日常中位价 | 最高报价 | 样本数 | 最优价格类型 | 代表性好价情报 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]
    for p in report.platform_matrix:
        low_s = f"¥{p.lowest_price:.0f}" if p.lowest_price else "-"
        med_s = f"¥{p.median_price:.0f}" if p.median_price else "-"
        high_s = f"¥{p.highest_price:.0f}" if p.highest_price else "-"
        ctx_s = (p.best_deal_context[:48] + "...") if len(p.best_deal_context) > 48 else (p.best_deal_context or "-")
        lines.append(
            f"| **{p.platform}** | {low_s} | {med_s} | {high_s} | {p.sample_count} | `{p.best_deal_tag}` | {ctx_s} |"
        )

    lines.extend(["", "## 3. 多流智能汇总分析", report.executive_summary, ""])

    lines.append("## 4. 全网真实口碑与避坑指南")
    if report.pros:
        lines.append("### 👍 核心优点")
        for pt in report.pros:
            lines.append(f"- {pt.point} *([{pt.source_title[:30]}]({pt.source_url}))*")
    if report.cons:
        lines.append("### 👎 常见槽点与缺点")
        for pt in report.cons:
            lines.append(f"- {pt.point} *([{pt.source_title[:30]}]({pt.source_url}))*")
    if report.pitfalls:
        lines.append("### ⚠️ 选购避坑提醒")
        for pt in report.pitfalls:
            lines.append(f"- {pt.point} *([{pt.source_title[:30]}]({pt.source_url}))*")

    lines.extend(["", "## 5. 原始好价与测评证据链"])
    for idx, s in enumerate(report.sources[:12], 1):
        prices_badge = f" (识别价格: {', '.join(f'¥{x:.0f}' for x in s.extracted_prices)})" if s.extracted_prices else ""
        lines.append(f"{idx}. **[{s.platform_badge}]** [{s.title}]({s.url}){prices_badge}")

    return "\n".join(lines)
