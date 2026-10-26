# ⚡ fine2api · 国内全平台电商商品智能比价与口碑总结雷达

基于 **Tavily 多流并发深度检索引擎 (Multi-Stream Search)** + **免费导购/联盟开放 API 连接器** + **MCP (Model Context Protocol)** 构建的国内全网电商商品情报聚合系统。支持对 **淘宝、天猫、京东、拼多多、闲鱼、什么值得买、知乎、V2EX、小红书** 的商品报价、百亿补贴/国家补贴史低价、二手行情与真实买家口碑进行一站式大范围拉取与结构化总结。

---

## ✨ 核心特性

1. **3 路 Tavily 并发深度搜索流 (`fine2api/tavily_engine.py`)**
   - **好价与补贴流 (`deals`)**：定向扫描什么值得买、慢慢买、京东、天猫、拼多多，捕捉百亿补贴、国家补贴（15%~20%）、88VIP/PLUS 券后到手价与历史低价。
   - **跨平台比价流 (`platforms`)**：覆盖京东自营、天猫官旗、拼多多百亿补贴及闲鱼二手近 7 日成交均价。
   - **真实口碑与避坑流 (`reviews`)**：聚合知乎、什么值得买社区、V2EX、B站测评，自动抽取核心优点、通病槽点与渠道防坑指南。
2. **高精度价格清洗与中位数聚类引擎 (`fine2api/analyzer.py`)**
   - 自动识别并剔除“立减 300 元券”、“满 200 减 30”、“返现 50 元”等优惠券差额干扰项与配件价格干扰，精准还原各平台真实到手价矩阵。
3. **免费导购联盟 API 扩展槽 (`fine2api/connectors.py`)**
   - 原生预留好单库 (`HAODANKU_API_KEY`)、折淘客 (`ZHETAOKE_APP_KEY`)、大淘客 (`DATAOKE_APP_KEY`) 等免费 CPS 开放平台接口，配置后自动融合实时券后 SKU 列表。
4. **全栈三端形态支持**
   - **Web 可视化雷达看板**：支持单品全网深扒、多商品同品类横向比价、一键复制完整 Markdown 报告。
   - **标准 MCP Server (`stdio` & `HTTP JSON-RPC 2.0`)**：可直接接入 Claude Desktop、Cursor、Windsurf 等 AI Agent。
   - **CLI 命令行工具**：终端一行命令生成 Markdown 或 JSON 深度选购报告。

---

## 🚀 快速开始

### 1. 启动 Web 可视化控制台与 API 服务

```bash
# 默认使用环境变量中的 TAVILY_API_KEY
python3 -m fine2api.cli serve --host 0.0.0.0 --port 8000
```

浏览器访问 `http://localhost:8000` 即可使用可视化比价看板。

### 2. CLI 命令行直接总结商品

```bash
# 输出完整 Markdown 选购报告
python3 -m fine2api.cli search "大疆 Osmo Pocket 3"

# 输出结构化 JSON 数据
python3 -m fine2api.cli search "iPhone 16 Pro 256G" --json
```

### 3. 接入 Claude / Cursor (MCP 协议)

在 `claude_desktop_config.json` 或 `.cursor/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "fine2api-cn-ecommerce": {
      "command": "python3",
      "args": ["-m", "fine2api.cli", "mcp"],
      "env": {
        "TAVILY_API_KEY": "tvly-your-api-key"
      }
    }
  }
}
```

内置 3 个 MCP 工具：
- `compare_cn_ecommerce_prices`：全平台比价、国补/百亿补贴史低价与选购建议。
- `summarize_product_reviews`：全网真实评测优缺点与避坑汇总。
- `batch_compare_products`：多款同品类商品横向比价矩阵。
