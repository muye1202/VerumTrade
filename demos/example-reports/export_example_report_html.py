from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path


DETAILS_RE = re.compile(
    r"<details(?P<open>\s+open)?\>\s*"
    r"<summary><strong>(?P<title>.*?)</strong></summary>\s*"
    r"(?P<body>.*?)"
    r"</details>",
    re.DOTALL,
)

MACHINE_OUTPUT_PANEL_TITLES = {
    "Full Evidence Graph JSON",
    "Investment Debate State",
    "Risk Debate State",
    "Market Snapshot",
    "Decision Guard",
    "Tool Cache Metrics",
    "Vendor Telemetry",
    "Analyst Workbench Metrics",
}


# Curated glossary of the finance terms and symbols that recur in Verumtrade
# reports. Every entry whose aliases appear in the report is listed in the
# "Key terms & symbols" reference card near the top. Entries with ``inline:
# True`` also get a dotted-underline tooltip the first time the symbol is used
# within each section, so a new reader can hover for a one-line definition
# without leaving the flow of the report.
GLOSSARY: list[dict] = [
    {
        "term": "ATR / ATR(14)",
        "aliases": ["ATR(14)", "ATR"],
        "inline": True,
        "short": "Average True Range — the typical daily high-to-low move; a volatility gauge (bigger = wider swings).",
        "long": "Average True Range, here measured over 14 days. It captures how far the price typically travels in a day. A larger ATR means wider swings, so protective stops need more room.",
    },
    {
        "term": "VWAP",
        "aliases": ["VWAP"],
        "inline": True,
        "short": "Volume-Weighted Average Price — the average traded price weighted by volume; a fair-value/flow benchmark.",
        "long": "Volume-Weighted Average Price. The average price paid through the session weighted by how much traded at each level. Trading above VWAP is often read as buyers in control; below as sellers.",
    },
    {
        "term": "OI (Open Interest)",
        "aliases": ["OI"],
        "inline": True,
        "short": "Open Interest — the number of option contracts currently outstanding (not yet closed).",
        "long": "Open Interest is the count of option contracts that are still open. A high put/call OI ratio (e.g. 2.23) means far more open puts than calls, which can signal hedging or bearish positioning.",
    },
    {
        "term": "HBM",
        "aliases": ["HBM"],
        "inline": True,
        "short": "High-Bandwidth Memory — stacked high-speed memory used in AI accelerators; a key Micron product.",
        "long": "High-Bandwidth Memory: stacked, very fast memory used alongside AI GPUs/accelerators. Demand for HBM is the core bullish narrative for Micron (MU) in this report.",
    },
    {
        "term": "EMA10 / EMA",
        "aliases": ["EMA10", "EMA"],
        "inline": True,
        "short": "Exponential Moving Average — a moving average that weights recent prices more (EMA10 = 10-day).",
        "long": "Exponential Moving Average. Like a simple moving average but weighting recent days more heavily, so it reacts faster to new prices. EMA10 is a short-term trend reference.",
    },
    {
        "term": "SMA50 / SMA200 / SMA",
        "aliases": ["SMA200", "SMA50", "SMA"],
        "inline": True,
        "short": "Simple Moving Average — the average close over N days (SMA50 = medium-term, SMA200 = long-term trend).",
        "long": "Simple Moving Average: the plain average closing price over N days. SMA50 tracks the medium-term trend and SMA200 the long-term trend; closing below them is a common invalidation signal.",
    },
    {
        "term": "MTUM",
        "aliases": ["MTUM"],
        "inline": True,
        "short": "iShares MSCI USA Momentum Factor ETF — used here as a proxy for how crowded the momentum trade is.",
        "long": "MTUM is the iShares momentum-factor ETF. \"MTUM-SPY +13.9%/20d\" means momentum stocks have outrun the S&P 500 recently — a sign the momentum trade is crowded and prone to fast unwinds.",
    },
    {
        "term": "10-Q / 10-K",
        "aliases": ["10-Q", "10-K"],
        "inline": True,
        "short": "SEC filings — 10-Q is the quarterly financial report, 10-K the annual one.",
        "long": "Standardized financial reports companies file with the U.S. SEC. The 10-Q is filed each quarter and the 10-K annually. In this run the 10-Q text was garbled and could not be used as evidence.",
    },
    {
        "term": "T+1",
        "aliases": ["T+1"],
        "inline": True,
        "short": "Trade date plus one business day — when the data is published or a trade settles.",
        "long": "\"T+1\" means one business day after the trade date. FINRA dark-pool short-volume data publishes on a T+1 basis, so today's institutional activity is not yet visible.",
    },
    {
        "term": "Realized volatility",
        "aliases": ["realized vol", "realized volatility"],
        "inline": False,
        "short": "How much the price has actually moved recently, annualized into a percentage.",
        "long": "A backward-looking measure of how much the price has actually moved, scaled to a yearly figure. ~119% annualized here is very high and argues for smaller position sizes and wider stops.",
    },
    {
        "term": "Put/call ratio",
        "aliases": ["put/call", "put-heavy", "put/call ratio"],
        "inline": False,
        "short": "Puts versus calls — by volume (today's activity) or open interest (standing positions).",
        "long": "Compares put activity to call activity. A volume ratio near 0.90 means slightly more call trading today, while a high open-interest ratio (2.23) means many more standing puts — mixed, hedged positioning.",
    },
    {
        "term": "Dark pool",
        "aliases": ["dark pool", "dark-pool", "off-exchange"],
        "inline": False,
        "short": "Private, off-exchange venues where large institutional trades execute without showing on public quotes.",
        "long": "Private trading venues where big institutions trade away from public exchanges. Dark-pool/off-exchange prints help reveal whether large players are quietly accumulating or distributing.",
    },
    {
        "term": "Short interest / days-to-cover",
        "aliases": ["short interest", "days-to-cover", "days to cover"],
        "inline": False,
        "short": "How much stock is sold short, and how many days of volume it would take shorts to buy back.",
        "long": "Short interest is the amount of stock sold short (often as a % of float). Days-to-cover estimates how long shorts would need to buy it back. High values raise the odds of a short squeeze.",
    },
    {
        "term": "Gamma / short squeeze",
        "aliases": ["gamma squeeze", "short-cover", "short cover", "squeeze"],
        "inline": False,
        "short": "A self-reinforcing rally where shorts and option hedgers are forced to buy, pushing price higher.",
        "long": "A squeeze is a feedback loop: a rising price forces short sellers (and option market-makers hedging gamma) to buy, which pushes the price up further. It can amplify moves well beyond fundamentals.",
    },
    {
        "term": "Float",
        "aliases": ["% of float", "low float"],
        "inline": False,
        "short": "The shares actually available to trade in the open market.",
        "long": "The portion of a company's shares freely available for public trading. A low float makes a stock easier to squeeze, because relatively small buying can move the price sharply.",
    },
    {
        "term": "Return windows (5D / 1M / 2M)",
        "aliases": ["5D", "1M", "2M", "20D high", "20D low"],
        "inline": False,
        "short": "Price change over a trailing window — 5 days, 1 month, 2 months; \"20D high\" = highest close in 20 days.",
        "long": "Shorthand for trailing price changes: 5D = last 5 trading days, 1M ≈ one month, 2M ≈ two months. \"20D high/low\" is the highest/lowest close over the last 20 trading days, used as breakout or invalidation levels.",
    },
]


ZH_EXACT_TEXT: dict[str, str] = {
    "Verumtrade example report": "Verumtrade 示例报告",
    "Example Verumtrade Report: MU (2026-06-23)": "Verumtrade 示例报告：MU (2026-06-23)",
    "Search report sections": "搜索报告章节",
    "Expand all": "全部展开",
    "Collapse all": "全部折叠",
    "No report sections match this search.": "没有报告章节匹配该搜索。",
    "Key terms & symbols": "关键术语与符号",
    "Run Snapshot": "运行快照",
    "Scenario Snapshot": "情景快照",
    "Analyst Reports": "分析师报告",
    "Evidence & Tracing": "证据与追踪",
    "Trading Pipeline": "交易流水线",
    "Final Verdict": "最终结论",
    "Additional Diagnostics": "附加诊断",
    "Historical example generated from a completed local Verumtrade analysis session. This is not investment advice. The report reflects the data, model outputs, and configuration available when the run was produced.": "这是由一次已完成的本地 Verumtrade 分析会话生成的历史示例。这不是投资建议。报告反映的是该次运行生成时可用的数据、模型输出和配置。",
    "New to the jargon? Underlined terms in the report show a definition on hover — or expand this card for the full list.": "不熟悉这些术语？报告中带下划线的术语可悬停查看定义，也可以展开此卡片查看完整列表。",
    "Field": "字段",
    "Value": "值",
    "Analysis date": "分析日期",
    "Time horizon": "时间跨度",
    "Session status": "会话状态",
    "Created at": "创建时间",
    "Primary setup": "主要 setup",
    "Setup quality": "Setup quality",
    "Recommended action": "Recommended action",
    "Execution intent": "Execution intent",
    "completed": "已完成",
    "Scenario": "情景",
    "Probability": "概率",
    "Target / Risk": "目标 / 风险",
    "Path": "路径",
    "Domain Inference": "Domain Inference",
    "Human memo": "人工备忘",
    "Active Hypotheses": "Active Hypotheses",
    "Key Observations": "Key Observations",
    "Questions Investigated": "Questions Investigated",
    "Discarded Explanations": "Discarded Explanations",
    "Unexplained But Decision-Relevant": "未解释但与决策相关",
    "Watch Items / Falsifiers": "Watch Items / Falsifiers",
    "Near-Term Catalysts": "Near-Term Catalysts",
    "Recent Material Events": "Recent Material Events",
    "Thesis Impact": "Thesis Impact",
    "Risk Controls": "Risk Controls",
    "Catalyst / Event-Risk Report": "Catalyst / Event-Risk 报告",
    "Average True Range, here measured over 14 days. It captures how far the price typically travels in a day. A larger ATR means wider swings, so protective stops need more room.": "Average True Range，这里按 14 天衡量。它反映价格一天内通常会移动多远。ATR 越大，波动越宽，保护性 stops 需要留出更大空间。",
    "Volume-Weighted Average Price. The average price paid through the session weighted by how much traded at each level. Trading above VWAP is often read as buyers in control; below as sellers.": "Volume-Weighted Average Price，即按各价位成交量加权后的盘中平均成交价格。价格在 VWAP 上方通常被解读为买方占优；低于 VWAP 则偏向卖方占优。",
    "Open Interest is the count of option contracts that are still open. A high put/call OI ratio (e.g. 2.23) means far more open puts than calls, which can signal hedging or bearish positioning.": "Open Interest 是仍未平仓的期权合约数量。较高的 put/call OI ratio（如 2.23）表示未平仓 puts 明显多于 calls，可能意味着对冲或偏空仓位。",
    "High-Bandwidth Memory: stacked, very fast memory used alongside AI GPUs/accelerators. Demand for HBM is the core bullish narrative for Micron (MU) in this report.": "High-Bandwidth Memory：与 AI GPUs/accelerators 搭配使用的堆叠式高速内存。HBM 需求是本报告中 Micron (MU) 的核心 bullish narrative。",
    "Exponential Moving Average. Like a simple moving average but weighting recent days more heavily, so it reacts faster to new prices. EMA10 is a short-term trend reference.": "Exponential Moving Average 类似 simple moving average，但对近期价格赋予更高权重，因此对新价格反应更快。EMA10 是短期趋势参考。",
    "Simple Moving Average: the plain average closing price over N days. SMA50 tracks the medium-term trend and SMA200 the long-term trend; closing below them is a common invalidation signal.": "Simple Moving Average：N 天收盘价的普通平均值。SMA50 跟踪中期趋势，SMA200 跟踪长期趋势；跌破这些均线通常是常见的 invalidation signal。",
    "MTUM is the iShares momentum-factor ETF. \"MTUM-SPY +13.9%/20d\" means momentum stocks have outrun the S&P 500 recently — a sign the momentum trade is crowded and prone to fast unwinds.": "MTUM 是 iShares momentum-factor ETF。\"MTUM-SPY +13.9%/20d\" 表示 momentum stocks 近期跑赢 S&P 500，这说明 momentum trade 较拥挤，容易出现快速 unwind。",
    "Standardized financial reports companies file with the U.S. SEC. The 10-Q is filed each quarter and the 10-K annually. In this run the 10-Q text was garbled and could not be used as evidence.": "公司向美国 SEC 提交的标准化财务报告。10-Q 为季度报告，10-K 为年度报告。本次运行中 10-Q 文本乱码，无法作为 evidence 使用。",
    "\"T+1\" means one business day after the trade date. FINRA dark-pool short-volume data publishes on a T+1 basis, so today's institutional activity is not yet visible.": "\"T+1\" 表示交易日后的一个工作日。FINRA dark-pool short-volume 数据按 T+1 发布，因此今天的机构活动尚不可见。",
    "A backward-looking measure of how much the price has actually moved, scaled to a yearly figure. ~119% annualized here is very high and argues for smaller position sizes and wider stops.": "衡量价格实际已经波动多少的回看指标，并折算为年化数值。这里约 119% annualized 非常高，支持更小 position sizes 和更宽 stops。",
    "Compares put activity to call activity. A volume ratio near 0.90 means slightly more call trading today, while a high open-interest ratio (2.23) means many more standing puts — mixed, hedged positioning.": "比较 puts 与 calls 的活跃度。volume ratio 接近 0.90 表示今日 calls 交易略多；而较高 open-interest ratio（2.23）表示存量 puts 多得多，属于混合且带对冲的 positioning。",
    "Private trading venues where big institutions trade away from public exchanges. Dark-pool/off-exchange prints help reveal whether large players are quietly accumulating or distributing.": "大型机构在公开交易所之外交易的私有场所。Dark-pool/off-exchange prints 有助于判断大资金是在悄悄 accumulating 还是 distributing。",
    "Short interest is the amount of stock sold short (often as a % of float). Days-to-cover estimates how long shorts would need to buy it back. High values raise the odds of a short squeeze.": "Short interest 是被卖空的股票数量（通常按 float 百分比表示）。Days-to-cover 估计 shorts 买回股票需要多久。数值较高会提高 short squeeze 的概率。",
    "A squeeze is a feedback loop: a rising price forces short sellers (and option market-makers hedging gamma) to buy, which pushes the price up further. It can amplify moves well beyond fundamentals.": "Squeeze 是一种反馈循环：价格上涨迫使 short sellers（以及为 gamma 对冲的 option market-makers）买入，从而进一步推高价格。它可能把涨跌幅放大到远超 fundamentals 的程度。",
    "The portion of a company's shares freely available for public trading. A low float makes a stock easier to squeeze, because relatively small buying can move the price sharply.": "公司股票中可自由公开交易的部分。低 float 更容易发生 squeeze，因为相对较小的买盘也可能明显推动价格。",
    "Shorthand for trailing price changes: 5D = last 5 trading days, 1M ≈ one month, 2M ≈ two months. \"20D high/low\" is the highest/lowest close over the last 20 trading days, used as breakout or invalidation levels.": "Trailing price changes 的简写：5D = 最近 5 个交易日，1M ≈ 一个月，2M ≈ 两个月。\"20D high/low\" 是最近 20 个交易日的最高/最低收盘价，用作 breakout 或 invalidation levels。",
}


ZH_PHRASE_REPLACEMENTS: list[tuple[str, str]] = [
    ("generated from", "生成自"),
    ("This is not investment advice.", "这不是投资建议。"),
    ("The report reflects the data, model outputs, and configuration available when the run was produced.", "报告反映的是该次运行生成时可用的数据、模型输出和配置。"),
    ("confirms and price works toward the next reward zone", "得到确认，价格向下一个 reward zone 推进"),
    ("Setup remains unresolved; preserve optionality until trigger quality improves.", "Setup 仍未解决；在 trigger quality 改善前保留 optionality。"),
    ("Setup fails or catalyst risk dominates before confirmation.", "Setup 失败，或 catalyst risk 在确认前占主导。"),
    ("Event risk rating:", "Event risk rating："),
    ("Catalyst score:", "Catalyst score："),
    ("Thesis break score:", "Thesis break score："),
    ("Thesis support score:", "Thesis support score："),
    ("Recommended action:", "Recommended action："),
    ("Rationale:", "Rationale："),
    ("Momentum-driven, high-volatility rally", "由 momentum 驱动的高波动 rally"),
    ("with contaminated/unverified guidance claims", "伴随受污染/未经验证的 guidance claims"),
    ("and elevated macro positioning/regulatory risks", "以及较高的 macro positioning/regulatory risks"),
    ("Freeze initiating new buys until", "在以下条件出现前冻结新增买入："),
    ("company-level confirmation", "公司层面确认"),
    ("earnings/guidance/clean filing", "earnings/guidance/clean filing"),
    ("if already long", "如果已经 long"),
    ("consider reviewing sizing and tighten risk controls", "考虑复核 sizing 并收紧 risk controls"),
    ("Rapid multi-week rally", "快速多周 rally"),
    ("leadership move in crowded momentum factor", "crowded momentum factor 中的 leadership move"),
    ("realized vol", "realized vol"),
    ("annualized", "年化"),
    ("after close", "收盘后"),
    ("Peer guidance/print can re-rate the whole semiconductor/memory basket", "同业 guidance/print 可能重估整个 semiconductor/memory basket"),
    ("dated timing risk", "有明确日期的 timing risk"),
    ("requires verification", "需要验证"),
    ("Regulatory / policy narrative", "Regulatory / policy narrative"),
    ("export controls / tariffs / probes", "export controls / tariffs / probes"),
    ("If escalates, could be a material re-rating trigger.", "如果升级，可能成为重要的 re-rating trigger。"),
    ("Large short-term rally", "短期大幅 rally"),
    ("with leadership characteristics in momentum factor", "具备 momentum factor 中的 leadership characteristics"),
    ("price at/near", "价格处于/接近"),
    ("highs", "高点"),
    ("elevated", "偏高"),
    ("Recent daily volume", "近期日成交量"),
    ("vs 20D avg", "相对于 20D avg"),
    ("move accompanied by roughly average volume", "走势伴随大致平均的成交量"),
    ("not extreme conviction volume", "不是极端 conviction volume"),
    ("filing detected in feed", "feed 中检测到 filing"),
    ("extracted text", "提取文本"),
    ("garbled / non-actionable", "乱码 / 不可操作"),
    ("filing requires verification", "filing 需要验证"),
    ("Supporting:", "Supporting："),
    ("Breaking:", "Breaking："),
    ("Strong FYQ3 guidance driven by AI/HBM demand", "由 AI/HBM demand 驱动的强劲 FYQ3 guidance"),
    ("revenue and EPS growth", "revenue 和 EPS 增长"),
    ("strong gross margins", "强劲 gross margins"),
    ("Price breakout / leadership behavior", "Price breakout / leadership behavior"),
    ("market is pricing durable upside", "市场正在定价 durable upside"),
    ("Crowded momentum factor", "Crowded momentum factor"),
    ("prone to fast unwinds", "容易快速 unwind"),
    ("soft signals or peer weakness", "soft signals 或 peer weakness"),
    ("could lead to re-rating or supply-chain disruptions", "可能导致 re-rating 或 supply-chain disruptions"),
    ("can re-rate the basket", "可能重估 basket"),
    ("near-term correction", "短期 correction"),
    ("Do not add new size until", "在以下条件出现前不要增加新 size："),
    ("verified company guidance/earnings", "经过验证的 company guidance/earnings"),
    ("justify current price", "证明当前价格合理"),
    ("meaningful intra-day/close confirmation", "有意义的 intra-day/close confirmation"),
    ("above-average volume", "高于平均水平的 volume"),
    ("If initiating/adding", "如果 initiate/add"),
    ("use smaller-than-normal size", "使用小于正常水平的 size"),
    ("given realized vol", "考虑到 realized vol"),
    ("set wider ATR-based stops", "设置更宽的 ATR-based stops"),
    ("avoid fixed tight stops", "避免固定的 tight stops"),
    ("Reference technical stop anchors", "参考 technical stop anchors"),
    ("medium-term invalidation", "中期 invalidation"),
    ("consider partial profit-taking / trailing stops", "考虑 partial profit-taking / trailing stops"),
    ("on weakness", "在走弱时"),
    ("Monitor", "监控"),
    ("consider re-assessing risk budget", "考虑重新评估 risk budget"),
    ("before and after", "前后"),
    ("Verify", "验证"),
    ("company press releases", "company press releases"),
    ("before assuming fundamental support", "在假设 fundamental support 前"),
    ("re-run analysis", "重新运行分析"),
    ("regulatory headlines", "regulatory headlines"),
    ("escalate to risk-judge review", "升级到 risk-judge review"),
    ("if credible policy moves emerge", "如果出现可信的 policy moves"),
    ("Source", "来源"),
    ("Event type", "事件类型"),
    ("Date", "日期"),
    ("Thesis impact", "Thesis impact"),
    ("Confidence", "Confidence"),
    ("Claim", "Claim"),
    ("Primary market signal supporting", "支持该判断的主要市场信号"),
    ("Indicates wide intraday moves", "表明盘中波动较宽"),
    ("larger stop distance", "需要更大的 stop distance"),
    ("Potentially material", "可能具有重要性"),
    ("currently unusable", "当前不可用"),
    ("must verify", "必须验证"),
    ("If verified", "如果得到验证"),
    ("direct thesis-supporting evidence", "直接支持 thesis 的 evidence"),
    ("known calendar risk", "已知 calendar risk"),
    ("Regulatory escalation", "Regulatory escalation"),
    ("materially break thesis", "实质性破坏 thesis"),
    ("Short-term regime is ambiguous", "短期 regime 并不明确"),
    ("tilted toward fragile momentum", "但偏向 fragile momentum"),
    ("outsized multi-week breakout", "超常的多周 breakout"),
    ("structurally favors continuation", "在结构上有利于 continuation"),
    ("while price remains above", "只要价格保持在"),
    ("short-term dynamic support", "短期 dynamic support"),
    ("very high", "非常高"),
    ("material uncertainty", "重要不确定性"),
    ("weight of evidence", "证据权重"),
    ("supports", "支持"),
    ("rather than", "而不是"),
    ("clean, conviction-driven breakout", "干净且由 conviction 驱动的 breakout"),
    ("Active Hypotheses (summary)", "Active Hypotheses（摘要）"),
    ("Momentum continuation", "Momentum continuation"),
    ("Claim:", "Claim："),
    ("Support:", "Support："),
    ("Against:", "Against："),
    ("Confidence:", "Confidence："),
    ("Key unresolved:", "关键未解决项："),
    ("Options/positioning-driven", "Options/positioning-driven"),
    ("at least partly driven by", "至少部分由"),
    ("rather than pure fundamental conviction", "而不是纯粹的 fundamental conviction"),
    ("increases likelihood of sharp reversals", "提高急剧 reversal 的可能性"),
    ("if flows reverse", "如果 flows 反转"),
    ("conflicting options signals", "互相冲突的 options signals"),
    ("missing dark-pool short data", "缺失 dark-pool short data"),
    ("institutional positioning unclear", "institutional positioning 不清楚"),
    ("strong visible price momentum", "明显强劲的 price momentum"),
    ("over multiple weeks", "持续多周"),
    ("reconcile price discrepancy", "调和 price discrepancy"),
    ("get short-interest / block trade evidence", "获取 short-interest / block trade evidence"),
    ("Key Observations (top 6)", "Key Observations（前 6 项）"),
    ("Questions Investigated (backlog — priority)", "Questions Investigated（backlog — priority）"),
    ("highest priority", "最高优先级"),
    ("Decision relevance", "Decision relevance"),
    ("Discarded Explanations", "Discarded Explanations"),
    ("was discarded as implausible", "因不合理而被排除"),
    ("given sustained multi-week momentum", "考虑到持续多周 momentum"),
    ("lack of one-day volume spike", "缺乏单日 volume spike"),
    ("validate a panic unwind", "验证 panic unwind"),
    ("The vendor/quote discrepancy", "vendor/quote discrepancy"),
    ("most important unresolved anomaly", "最重要的未解决 anomaly"),
    ("directly affects", "直接影响"),
    ("should be treated with caution", "应谨慎对待"),
    ("Watch:", "Watch："),
    ("Falsifier for", "Falsifier for"),
    ("Immediate recommended operational next steps", "Immediate recommended operational next steps"),
    ("non-executable, investigative", "不可执行，仅调查"),
    ("Priority 1", "Priority 1"),
    ("Priority 2", "Priority 2"),
    ("Priority 3", "Priority 3"),
    ("high urgency", "高紧迫性"),
    ("must be re-run", "必须重新运行"),
    ("materially changes risk sizing", "会实质改变 risk sizing"),
    ("plausible", "合理"),
    ("Obtain", "获取"),
    ("confirm whether", "确认是否"),
    ("suggests mean reversion", "暗示 mean reversion"),
    ("This ledger/memo intentionally focuses on diagnosis and data gaps.", "该 ledger/memo 有意聚焦 diagnosis 和 data gaps。"),
    ("It does not include", "它不包含"),
    ("execution-level entry/stop/target numbers", "execution-level entry/stop/target numbers"),
    ("will be provided once", "将在以下条件满足后提供："),
    ("critical anomalies", "critical anomalies"),
    ("are resolved", "得到解决"),
    ("Narrative and decision rationale", "叙述与决策理由"),
    ("Final Trade Decision", "Final Trade Decision"),
    ("Summary of the debate (key points)", "辩论摘要（关键点）"),
    ("Context and constraints that drive the final call", "驱动最终判断的背景与约束"),
    ("Decision logic", "Decision logic"),
    ("Price-anchor rationale (how triggers relate to reference)", "Price-anchor rationale（trigger 如何关联 reference）"),
    ("Which patches I accept / reject and why", "接受 / 拒绝哪些 patches 以及原因"),
    ("Does this materially change the trader", "这是否实质改变 trader"),
    ("Final recommendation (clear, portfolio-aware)", "最终建议（清晰且 portfolio-aware）"),
    ("Summary of the debate", "辩论摘要"),
    ("key points", "关键点"),
    ("Push for a larger, front-loaded entry", "主张更大且前置的 entry"),
    ("on a confirmed 20-day-high breakout", "在确认的 20-day-high breakout 上"),
    ("because options/gamma-driven squeezes pay off front-loaded", "因为 options/gamma-driven squeezes 对前置参与回报更高"),
    ("supports a", "支持"),
    ("initial tranche", "初始 tranche"),
    ("scale to", "扩展到"),
    ("conditional on follow-through", "以 follow-through 为条件"),
    ("Key evidence cited", "引用的关键 evidence"),
    ("peer strength and company guidance signals", "peer strength 和 company guidance signals"),
    ("improving fundamentals", "改善中的 fundamentals"),
    ("Middle ground", "折中立场"),
    ("recognize asymmetric upside but respect fragility", "承认 asymmetric upside，但尊重 fragility"),
    ("Recommends initial", "建议初始"),
    ("on confirmed breakout", "在确认 breakout 后"),
    ("add a second tranche", "增加第二个 tranche"),
    ("only after", "仅在之后"),
    ("non-declining volume", "non-declining volume"),
    ("verifiable options-unwind / company confirmation", "可验证的 options-unwind / company confirmation"),
    ("Keeps stop and TP intact", "保持 stop 和 TP 不变"),
    ("constrains single-day exposure", "限制单日 exposure"),
    ("summarized across debate", "跨辩论汇总"),
    ("Wants much smaller initial exposure", "希望初始 exposure 显著更小"),
    ("and/or multi-day confirmation before scaling", "并/或在 scaling 前等待多日确认"),
    ("because the trade sits inside", "因为该交易位于"),
    ("critically vulnerable momentum crowd", "critically vulnerable momentum crowd"),
    ("balance-sheet/flow risks", "balance-sheet/flow risks"),
    ("Portfolio rules", "Portfolio rules"),
    ("no existing MU position assumed", "假设当前没有 MU position"),
    ("SELL is invalid", "SELL 无效"),
    ("Trader intent is", "Trader intent 为"),
    ("keep conditional plan", "保留 conditional plan"),
    ("Market regime and per-ticker pullback vulnerability", "Market regime 与单 ticker pullback vulnerability"),
    ("This is a conservative override", "这是 conservative override"),
    ("prefer reduced sizing", "倾向降低 sizing"),
    ("tighter invalidation", "更严格的 invalidation"),
    ("wait-for-trigger", "wait-for-trigger"),
    ("unless a strong, verifiable catalyst offsets vulnerability", "除非强且可验证的 catalyst 抵消 vulnerability"),
    ("There are several admissible bullish signals", "存在多个可采纳的 bullish signals"),
    ("but also material execution and liquidity risks", "但也存在重要 execution 和 liquidity risks"),
    ("momentum crowding", "momentum crowding"),
    ("parabolic run", "parabolic run"),
    ("proposing larger immediate sizing", "提出更大 immediate sizing"),
    ("was invalidated in patch validation", "在 patch validation 中被判无效"),
    ("neutral patch is admissible", "neutral patch 可采纳"),
    ("balances upside capture vs. downside fragility", "平衡 upside capture 与 downside fragility"),
    ("I adopt", "我采纳"),
    ("because a concrete trigger exists", "因为存在具体 trigger"),
    ("daily close above the 20D high", "日收盘价高于 20D high"),
    ("with volume follow-through", "并伴随 volume follow-through"),
    ("or verifiable options-unwind", "或可验证的 options-unwind"),
    ("This satisfies", "这满足"),
    ("executor", "executor"),
    ("requirement", "要求"),
    ("Given the CRITICAL pullback vulnerability", "鉴于 CRITICAL pullback vulnerability"),
    ("I decline", "我拒绝"),
    ("large single-day bite", "较大的单日 bite"),
    ("accept", "接受"),
    ("balanced sizing", "balanced sizing"),
    ("explicit short-duration confirmation rules", "明确的短期确认规则"),
    ("reduce single-day exposure", "降低单日 exposure"),
    ("while preserving conditional participation", "同时保留有条件参与"),
    ("if the breakout proves durable", "如果 breakout 证明可持续"),
    ("Action plan", "Action plan"),
    ("conditional BUY", "conditional BUY"),
    ("on a clean breakout", "在 clean breakout 上"),
    ("Initial tranche", "初始 tranche"),
    ("position size on trigger", "trigger 出现时的 position size"),
    ("hard cap", "hard cap"),
    ("only after very short, objective follow-through", "仅在非常短且客观的 follow-through 之后"),
    ("explicit verifiable options-unwind/company confirmation", "明确且可验证的 options-unwind/company confirmation"),
    ("Stop and TP are numeric", "Stop 和 TP 是数字化的"),
    ("tied to reference price", "绑定到 reference price"),
    ("limit a single-trade loss", "限制单笔交易损失"),
    ("giving room for intraday squeeze dynamics", "同时为 intraday squeeze dynamics 留出空间"),
    ("Use the trader-provided current market reference price", "使用 trader 提供的当前 market reference price"),
    ("sits approximately", "大约位于"),
    ("above the reference price", "高于 reference price"),
    ("Requiring a daily close above that level", "要求日收盘价高于该水平"),
    ("plus volume reduces false breakouts", "并结合 volume 可减少 false breakouts"),
    ("while still allowing capture", "同时仍可捕捉"),
    ("front-loaded squeeze moves", "前置的 squeeze moves"),
    ("limits loss", "限制亏损"),
    ("captures a reasonable upside", "捕捉合理 upside"),
    ("if breakout runs", "如果 breakout 延续"),
    ("concrete numeric bounds", "具体数字边界"),
    ("aligned to the", "与"),
    ("month horizon", "月 horizon 对齐"),
    ("Accepted", "Accepted"),
    ("Rejected", "Rejected"),
    ("it is admissible", "它可采纳"),
    ("addresses the pullback vulnerability", "处理 pullback vulnerability"),
    ("by limiting initial size", "通过限制 initial size"),
    ("preserves conditional scaling", "保留 conditional scaling"),
    ("participate in a genuine squeeze", "参与真正的 squeeze"),
    ("patch validation marks it as invalid", "patch validation 将其标记为无效"),
    ("targets a stale plan version", "目标是过期 plan version"),
    ("too large a single-day bite", "单日 bite 过大"),
    ("critical pullback vulnerability", "critical pullback vulnerability"),
    ("execution/liquidity risk", "execution/liquidity risk"),
    ("No other patches materially alter the plan", "没有其他 patches 实质改变该计划"),
    ("This final decision refines", "该最终决策细化了"),
    ("specifying precise trigger conditions", "明确了精确 trigger conditions"),
    ("explicit initial and scale sizing", "明确 initial 和 scale sizing"),
    ("numeric levels", "数字水平"),
    ("short follow-through rule", "短期 follow-through rule"),
    ("aligned with", "与"),
    ("overall thesis", "整体 thesis"),
    ("tightens risk controls", "收紧 risk controls"),
    ("material, concrete refinement", "实质且具体的 refinement"),
    ("do not buy now", "现在不要买入"),
    ("Use v2 conditional plan below", "使用下方 v2 conditional plan"),
    ("preserves optionality", "保留 optionality"),
    ("limits single-trade loss", "限制单笔交易亏损"),
    ("given critical fragility", "考虑到 critical fragility"),
    ("participates meaningfully", "有意义地参与"),
    ("if the breakout and evidence", "如果 breakout 和 evidence"),
    ("follow-through arrive", "follow-through 出现"),
    ("no existing position assumed", "假设没有现有 position"),
    ("limited initial size", "受限 initial size"),
    ("hard cap", "hard cap"),
    ("concentration", "concentration"),
    ("Trader planned", "Trader 原计划"),
    ("with unclear size and trigger", "size 和 trigger 不明确"),
    ("Execution plan with two branches", "带两个 branches 的 execution plan"),
    ("condition", "condition"),
    ("action", "action"),
    ("additional tranche", "additional tranche"),
    ("Default action remains", "Default action 保持"),
    ("overall time_horizon", "overall time_horizon"),
    ("confidence", "confidence"),
    ("Final decision", "Final decision"),
    ("Risk review", "Risk review"),
    ("Trader plan", "Trader plan"),
    ("Bull case", "Bull case"),
    ("Bear case", "Bear case"),
    ("Base case", "Base case"),
    ("operational note", "操作备注"),
    ("validate market-price before order placement", "下单前验证 market-price"),
    ("Operational precondition", "操作前提"),
    ("Do not execute", "不要执行"),
    ("unless", "除非"),
    ("Suggested watch-entry / trigger", "建议 watch-entry / trigger"),
    ("Stop-loss", "Stop-loss"),
    ("Take-profit", "Take-profit"),
    ("Holding horizon / time-stop", "Holding horizon / time-stop"),
    ("Invalidation logic", "Invalidation logic"),
    ("avoid new positions", "避免新建 positions"),
    ("set alerts", "设置 alerts"),
    ("The price-anchor anomaly", "price-anchor anomaly"),
    ("until resolved", "在解决前"),
    ("risk calculations", "risk calculations"),
    ("are unreliable", "不可靠"),
    ("Absence of", "缺少"),
    ("we do not know whether", "我们不知道是否"),
    ("will amplify or dampen", "会放大还是削弱"),
    ("dominant theme", "主导 theme"),
    ("news/analyst-centric", "以 news/analyst 为中心"),
    ("critical metadata anomaly", "关键 metadata anomaly"),
    ("must be validated", "必须验证"),
    ("before any numeric execution sizing", "在任何 numeric execution sizing 之前"),
    ("positive narrative", "正面 narrative"),
    ("sector-wide analyst upgrades", "sector-wide analyst upgrades"),
    ("sharp mean-reversion", "急剧 mean-reversion"),
    ("likely wrong", "很可能错误"),
    ("operations must confirm", "operations 必须确认"),
    ("market price", "market price"),
    ("analyst target raises", "analyst target raises"),
    ("positive cross-stock momentum", "正面的 cross-stock momentum"),
    ("limits inferences", "限制推断"),
    ("retail-driven reflexivity", "retail-driven reflexivity"),
    ("Potential cross-stock lift", "Potential cross-stock lift"),
    ("correlated flows", "correlated flows"),
    ("trade sizing", "trade sizing"),
    ("Earnings", "Earnings"),
    ("Mgmt commentary", "Mgmt commentary"),
    ("explicit confirmation", "明确确认"),
    ("major catalyst", "主要 catalyst"),
    ("direct falsifier", "直接 falsifier"),
    ("data integrity", "data integrity"),
    ("raw evidence", "raw evidence"),
    ("evidence", "evidence"),
    ("decision", "decision"),
]


# Build the inline-tooltip lookup from the glossary. Longer aliases are matched
# first so e.g. "ATR(14)" wins over "ATR" and "SMA200" over "SMA".
_INLINE_TERMS: list[tuple[str, str]] = sorted(
    (
        (alias, entry["short"])
        for entry in GLOSSARY
        if entry.get("inline")
        for alias in entry["aliases"]
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)
_TERM_DEFS = dict(_INLINE_TERMS)
_TERM_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(re.escape(alias) for alias, _ in _INLINE_TERMS) + r")(?![A-Za-z0-9])"
)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"&lt;br\s*/?&gt;", re.IGNORECASE)

# Glossary-tooltip state. Reset per section so each major section annotates the
# first use of a symbol once (avoids underlining every occurrence). Annotation
# is disabled while rendering the glossary card itself.
_SECTION_SEEN: set[str] = set()
_ANNOTATE = True


_PAIR_VALUE = r"(?:'[^']*'|\[[^\]]*\]|True|False|None|-?\d+(?:\.\d+)?)"
_FLAT_DICT_RE = re.compile(r"\{(?:'[^']+':\s*" + _PAIR_VALUE + r"\s*,?\s*)+\}")
_PAIR_RE = re.compile(r"'([^']+)':\s*(" + _PAIR_VALUE + r")")
_FILING_RE = re.compile(r"\{'form_type':[^{}]*\}")
_VALUE_LABELS = {"None": "—", "True": "yes", "False": "no"}


def _render_filing(match: re.Match) -> str:
    block = match.group(0)

    def field(name: str) -> str:
        found = re.search(rf"'{name}':\s*'([^']*)'", block)
        return found.group(1).strip() if found else ""

    parts = [field("form_type") or "Filing"]
    company = field("company_name")
    filed = field("filed_at")
    if company:
        parts.append(f"— {company}")
    if filed:
        parts.append(f"(filed {filed})")
    return " ".join(parts)


def _render_flat_dict(match: re.Match) -> str:
    pairs = _PAIR_RE.findall(match.group(0))
    rendered = []
    for key, value in pairs:
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1].strip()
        value = _VALUE_LABELS.get(value, value)
        rendered.append(f"{key.replace('_', ' ')}: {value}")
    return " · ".join(rendered)


def _humanize_structs(text: str) -> str:
    """Render leaked Python dict/repr fragments as readable prose.

    Several model outputs serialize evidence, observation, filing and summary
    records as raw Python dicts (e.g. ``{'evidence_id': ..., 'note': ...}``).
    Surface the human-readable content and keep record ids as inline code refs
    so the report reads as prose rather than as a debugger dump.
    """
    # Evidence / observation / fact notes -> "note `id`".
    text = re.sub(
        r"\{'(?:evidence_id|obs_id|fact_id)':\s*'([^']*)',\s*'note':\s*'(.*?)'\}",
        lambda m: f"{m.group(2).strip()} `{m.group(1).strip()}`",
        text,
    )

    # Anomaly records with a free-text body and related-fact ids.
    def _unexplained(match: re.Match) -> str:
        body = match.group(2).strip()
        ids = re.findall(r"'([^']+)'", match.group(3))
        refs = ", ".join(f"`{ref}`" for ref in ids)
        return body + (f" (related: {refs})" if refs else "")

    text = re.sub(
        r"\{'id':\s*'([^']*)',\s*'text':\s*'(.*?)',\s*'related_facts':\s*\[(.*?)\]\}",
        _unexplained,
        text,
    )

    # ``{'points': [ ... ]}`` wrappers — unwrap to the (already humanized) body.
    text = re.sub(r"\{'points':\s*\[(.*?)\]\}", lambda m: m.group(1).strip(), text)

    # SEC filing records -> "10-Q — Company Name (filed 2026-06-10)".
    text = _FILING_RE.sub(_render_filing, text)

    # Generic fallback for any remaining flat status dicts.
    text = _FLAT_DICT_RE.sub(_render_flat_dict, text)
    return text


def _apply_glossary(text: str, seen: set[str]) -> str:
    def repl(match: re.Match) -> str:
        token = match.group(1)
        if token in seen:
            return token
        seen.add(token)
        title = html.escape(_TERM_DEFS[token], quote=True)
        return f'<abbr class="gloss" title="{title}">{token}</abbr>'

    return _TERM_PATTERN.sub(repl, text)


def _annotate_terms(rendered: str, seen: set[str]) -> str:
    """Add glossary tooltips to plain text only, skipping tags and code spans."""
    parts: list[str] = []
    pos = 0
    in_code = 0
    for match in _TAG_RE.finditer(rendered):
        chunk = rendered[pos:match.start()]
        parts.append(_apply_glossary(chunk, seen) if in_code == 0 else chunk)
        tag = match.group(0)
        low = tag.lower()
        if low.startswith("<code"):
            in_code += 1
        elif low.startswith("</code"):
            in_code = max(0, in_code - 1)
        parts.append(tag)
        pos = match.end()
    tail = rendered[pos:]
    parts.append(_apply_glossary(tail, seen) if in_code == 0 else tail)
    return "".join(parts)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _inline_markdown(text: str) -> str:
    text = _humanize_structs(text)
    rendered = html.escape(text)
    rendered = _BR_RE.sub("<br>", rendered)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", rendered)
    if _ANNOTATE:
        rendered = _annotate_terms(rendered, _SECTION_SEEN)
    return rendered


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells)


def _render_table(lines: list[str]) -> str:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    if len(rows) >= 2 and _is_table_separator(lines[1]):
        header = rows[0]
        body = rows[2:]
    else:
        header = []
        body = rows

    parts = ["<div class=\"table-wrap\"><table>"]
    if header:
        parts.append("<thead><tr>")
        parts.extend(f"<th>{_inline_markdown(cell)}</th>" for cell in header)
        parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in body:
        parts.append("<tr>")
        parts.extend(f"<td>{_inline_markdown(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _extract_markdown_table(markdown: str) -> tuple[list[str], list[list[str]]] | None:
    table_lines = [
        line.strip()
        for line in markdown.strip().splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    if len(table_lines) < 2 or not _is_table_separator(table_lines[1]):
        return None

    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in table_lines]
    return rows[0], rows[2:]


def _value_badge(label: str, value: str) -> str:
    value_html = html.escape(value or "—").replace("_", "_<wbr>")
    normalized = value.strip().lower()
    label_key = label.strip().lower()

    if label_key == "recommended action":
        action_cls = normalized if normalized in ("buy", "sell", "hold") else "hold"
        return f'<span class="metric-badge metric-badge--{action_cls}">{value_html}</span>'
    if label_key == "setup quality":
        grade_cls = normalized if normalized in ("a", "b", "c", "d", "f") else "default"
        return f'<span class="metric-badge metric-badge--grade-{grade_cls}">{value_html}</span>'
    if label_key in ("primary setup", "execution intent"):
        return f'<span class="metric-badge metric-badge--neutral">{value_html}</span>'
    return value_html


def _render_run_snapshot_cards(body: str) -> str | None:
    parsed = _extract_markdown_table(body)
    if parsed is None:
        return None

    headers, rows = parsed
    if [h.lower() for h in headers[:2]] != ["field", "value"]:
        return None

    cards: list[str] = ['<div class="snapshot-grid">']
    primary_labels = {"ticker", "recommended action", "setup quality", "execution intent"}
    for label, value, *_ in rows:
        modifier = " snapshot-card--primary" if label.strip().lower() in primary_labels else ""
        cards.append(
            f'<div class="snapshot-card{modifier}">'
            f'<span class="snapshot-label">{html.escape(label)}</span>'
            f'<span class="snapshot-value">{_value_badge(label, value)}</span>'
            f'</div>'
        )
    cards.append("</div>")
    return "".join(cards)


def _render_scenario_snapshot_cards(body: str) -> str | None:
    parsed = _extract_markdown_table(body)
    if parsed is None:
        return None

    headers, rows = parsed
    if [h.lower() for h in headers[:4]] != ["scenario", "probability", "target / risk", "path"]:
        return None

    parts: list[str] = ['<div class="scenario-readout">']
    for scenario, probability, target, path, *_ in rows:
        scenario_key = scenario.strip().lower()
        scenario_cls = scenario_key if scenario_key in ("bull", "base", "bear") else "base"
        try:
            pct = max(0, min(100, round(float(probability) * 100)))
        except ValueError:
            pct = 0
        target_text = target.strip() or "No target"
        parts.append(
            f'<article class="scenario-card scenario-card--{scenario_cls}">'
            f'<div class="scenario-card-header">'
            f'<span class="scenario-label">{html.escape(scenario)}</span>'
            f'<span class="scenario-pct">{pct}%</span>'
            f'</div>'
            f'<div class="scenario-bar" aria-label="{html.escape(scenario)} probability {pct}%">'
            f'<div class="scenario-fill" style="width:{pct}%"></div>'
            f'</div>'
            f'<div class="scenario-price">{html.escape(target_text)}</div>'
            f'<p class="scenario-path">{html.escape(path)}</p>'
            f'</article>'
        )
    parts.append("</div>")
    return "".join(parts)


def _render_section_body(title: str, body: str) -> str:
    if title == "Run Snapshot":
        structured = _render_run_snapshot_cards(body)
        if structured is not None:
            return structured
    if title == "Scenario Snapshot":
        structured = _render_scenario_snapshot_cards(body)
        if structured is not None:
            return structured
    return _render_panels(body)


def _strip_embedded_machine_json(markdown: str) -> str:
    markdown = re.sub(
        r"BEGIN_DECISION_JSON\s*.*?\s*END_DECISION_JSON",
        "",
        markdown,
        flags=re.DOTALL,
    )
    return markdown


def render_markdown_fragment(markdown: str) -> str:
    markdown = _strip_embedded_machine_json(markdown)
    lines = markdown.strip("\n").splitlines()
    parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    table_lines: list[str] = []
    in_fence = False
    fence_lang = ""
    fence_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(item.strip() for item in paragraph).strip()
            if text:
                parts.append(f"<p>{_inline_markdown(text)}</p>")
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            parts.append("<ul>")
            parts.extend(f"<li>{_inline_markdown(item)}</li>" for item in list_items)
            parts.append("</ul>")
            list_items = []

    def flush_table() -> None:
        nonlocal table_lines
        if table_lines:
            parts.append(_render_table(table_lines))
            table_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                if fence_lang.lower() != "json":
                    parts.append(
                        f"<pre><code class=\"language-{html.escape(fence_lang)}\">"
                        f"{html.escape(chr(10).join(fence_lines))}</code></pre>"
                    )
                in_fence = False
                fence_lang = ""
                fence_lines = []
            else:
                flush_paragraph()
                flush_list()
                flush_table()
                in_fence = True
                fence_lang = stripped[3:].strip()
            continue

        if in_fence:
            fence_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            flush_table()
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_list()
            table_lines.append(stripped)
            continue

        flush_table()

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(len(heading.group(1)) + 1, 6)
            title = heading.group(2).strip()
            parts.append(f"<h{level}>{_inline_markdown(title)}</h{level}>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            parts.append(f"<blockquote>{_inline_markdown(stripped.lstrip('>').strip())}</blockquote>")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            list_items.append(bullet.group(1).strip())
            continue

        flush_list()
        paragraph.append(stripped)

    if in_fence and fence_lang.lower() != "json":
        parts.append(f"<pre><code>{html.escape(chr(10).join(fence_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    flush_table()
    return "\n".join(parts)


def _render_panels(section_markdown: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in DETAILS_RE.finditer(section_markdown):
        before = section_markdown[cursor:match.start()]
        if before.strip():
            output.append(render_markdown_fragment(before))

        title = html.unescape(match.group("title")).strip()
        structured = _try_structured_render(title, match.group("body"))
        if structured is None and (
            title in MACHINE_OUTPUT_PANEL_TITLES
            or (title.endswith("JSON") and "```json" in match.group("body"))
        ):
            cursor = match.end()
            continue
        body = structured if structured is not None else render_markdown_fragment(match.group("body"))
        expanded = "false"
        hidden = " hidden"
        open_class = ""
        output.append(
            f'<article class="report-panel{open_class}" data-panel-title="{html.escape(title)}">'
            f'<button class="panel-toggle" type="button" aria-expanded="{expanded}">'
            f'<span>{html.escape(title)}</span><span class="chevron">⌄</span></button>'
            f'<div class="panel-body"{hidden}>{body}</div></article>'
        )
        cursor = match.end()

    remainder = section_markdown[cursor:]
    if remainder.strip():
        output.append(render_markdown_fragment(remainder))
    return "\n".join(output)


def _split_sections(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    detail_ranges = [(match.start(), match.end()) for match in DETAILS_RE.finditer(markdown)]

    def inside_details(position: int) -> bool:
        return any(start <= position < end for start, end in detail_ranges)

    matches = [
        match
        for match in re.finditer(r"^##\s+(.+)$", markdown, re.MULTILINE)
        if not inside_details(match.start())
    ]
    if not matches:
        return markdown, []

    intro = markdown[: matches[0].start()]
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        title = match.group(1).strip()
        body = markdown[match.end() : end]
        sections.append((title, body))
    return intro, sections


def _build_glossary_card(markdown: str) -> str:
    """Build the "Key terms & symbols" reference card from terms present in the report."""
    present = [
        entry
        for entry in GLOSSARY
        if any(alias in markdown for alias in entry["aliases"])
    ]
    if not present:
        return ""

    rows = "".join(
        f"<dt>{html.escape(entry['term'])}</dt>"
        f"<dd>{html.escape(entry['long'])}</dd>"
        for entry in present
    )
    return (
        '<section class="report-section glossary-card" id="key-terms" data-section-id="key-terms">'
        '<div class="section-heading"><h2>Key terms &amp; symbols</h2></div>'
        '<details class="glossary" open>'
        '<summary>New to the jargon? Underlined terms in the report show a definition on hover &mdash; '
        'or expand this card for the full list.</summary>'
        f'<dl>{rows}</dl>'
        '</details></section>'
    )


# ---------------------------------------------------------------------------
# Structured renderers for Trading Pipeline panels
# ---------------------------------------------------------------------------

def _extract_json_from_body(body: str) -> dict | list | None:
    match = re.search(r"```json\s*\n(.*?)```", body, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def _direction_class(direction: str) -> str:
    if direction in ("bullish", "supporting"):
        return "bullish"
    if direction in ("bearish", "opposing"):
        return "bearish"
    return "risk"


def _direction_arrow(direction: str) -> str:
    if direction in ("bullish", "supporting"):
        return "↑"
    if direction in ("bearish", "opposing"):
        return "↓"
    return "⚠"


def _strength_bar_html(strength: float) -> str:
    pct = round(strength * 100)
    return (
        f'<div class="strength-bar">'
        f'<div class="strength-track"><div class="strength-fill" style="width:{pct}%"></div></div>'
        f'<span class="strength-label">{strength:.2f}</span>'
        f'</div>'
    )


def _render_reasoning_trace_structured(body: str) -> str | None:
    lines = [l.strip() for l in body.strip().splitlines() if l.strip().startswith("|")]
    if len(lines) < 3:
        return None

    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 5:
            rows.append({
                "stage": cells[0].strip(),
                "agent": cells[1].strip(),
                "kind": cells[2].strip(),
                "status": cells[3].strip(),
                "summary_raw": cells[4].strip(),
            })
    if not rows:
        return None

    parts = ['<div class="pipeline-trace">']
    for i, row in enumerate(rows):
        status_cls = "available" if row["status"] == "available" else "missing"
        is_last = i == len(rows) - 1

        chips_html = ""
        raw = row["summary_raw"]
        if raw and raw != "{}":
            pairs = _PAIR_RE.findall(raw)
            if pairs:
                chips: list[str] = []
                for key, val in pairs:
                    if val.startswith("'") and val.endswith("'"):
                        val = val[1:-1].strip()
                    val = _VALUE_LABELS.get(val, val)
                    label = key.replace("_", " ")
                    if len(val) > 60:
                        val = val[:57] + "..."
                    chips.append(
                        f'<span class="trace-chip">'
                        f'<span class="trace-chip-label">{html.escape(label)}</span>'
                        f'<span class="trace-chip-value">{html.escape(val)}</span>'
                        f'</span>'
                    )
                chips_html = '<div class="trace-chips">' + "".join(chips) + '</div>'

        kind_label = row["kind"].replace("_", " ")
        status_icon = "✓" if status_cls == "available" else "✗"

        parts.append(
            f'<div class="trace-step">'
            f'<div class="trace-connector">'
            f'<span class="trace-dot trace-dot--{status_cls}"></span>'
            f'{"<span class=\"trace-line\"></span>" if not is_last else ""}'
            f'</div>'
            f'<div class="trace-card">'
            f'<div class="trace-header">'
            f'<span class="trace-stage">{html.escape(row["stage"])}</span>'
            f'<span class="trace-status trace-status--{status_cls}">{status_icon} {html.escape(row["status"])}</span>'
            f'</div>'
            f'<div class="trace-meta">'
            f'<span class="trace-badge trace-badge--agent">{html.escape(row["agent"])}</span>'
            f'<span class="trace-badge trace-badge--kind">{html.escape(kind_label)}</span>'
            f'</div>'
            f'{chips_html}'
            f'</div>'
            f'</div>'
        )

    parts.append('</div>')
    return "\n".join(parts)


def _render_decision_trace_structured(data: dict) -> str | None:
    parts: list[str] = []

    decision = data.get("decision", {})
    action = decision.get("action", "—")
    ticker = decision.get("ticker", "—")
    action_cls = action.lower() if action in ("BUY", "SELL", "HOLD") else "hold"

    parts.append(
        f'<div class="decision-header">'
        f'<span class="action-badge action-badge--{action_cls}">{html.escape(action)}</span>'
        f'<span class="decision-ticker">{html.escape(ticker)}</span>'
        f'</div>'
    )

    thesis = data.get("thesis", {})
    claim = thesis.get("claim", "")
    if claim:
        parts.append(
            f'<div class="decision-thesis">'
            f'<h4>Thesis</h4>'
            f'<p>{html.escape(claim)}</p>'
            f'</div>'
        )

    inference_ids = data.get("inference_ids", [])
    if inference_ids:
        id_chips = "".join(f'<code>{html.escape(i)}</code>' for i in inference_ids)
        parts.append(
            f'<div class="decision-section">'
            f'<h4>Inferences ({len(inference_ids)})</h4>'
            f'<div class="chip-grid">{id_chips}</div>'
            f'</div>'
        )

    source_labels = data.get("source_labels", [])
    if source_labels:
        src_chips = "".join(f'<code>{html.escape(s)}</code>' for s in source_labels)
        parts.append(
            f'<div class="decision-section">'
            f'<h4>Source Labels ({len(source_labels)})</h4>'
            f'<div class="chip-grid">{src_chips}</div>'
            f'</div>'
        )

    fact_ids = data.get("fact_ids", [])
    if fact_ids:
        parts.append(
            f'<div class="decision-section">'
            f'<h4>Fact References</h4>'
            f'<p class="text-muted">{len(fact_ids)} facts linked to this decision</p>'
            f'</div>'
        )

    audit_issues = data.get("audit_issues", [])
    if audit_issues:
        by_domain: dict[str, list[dict]] = {}
        for issue in audit_issues:
            domain = issue.get("domain", "unknown")
            by_domain.setdefault(domain, []).append(issue)

        parts.append(f'<div class="decision-section">')
        parts.append(f'<h4>Audit Issues ({len(audit_issues)})</h4>')
        parts.append('<div class="audit-groups">')
        for domain, issues in sorted(by_domain.items()):
            parts.append('<details class="audit-group">')
            parts.append(
                f'<summary><strong>{html.escape(domain)}</strong>'
                f' <span class="text-muted">({len(issues)} issues)</span></summary>'
            )
            parts.append('<ul class="audit-list">')
            for issue in issues:
                sev = issue.get("severity", "info")
                sev_cls = "high" if sev == "high" else "medium" if sev == "medium" else "low"
                msg = issue.get("message", "")
                node = issue.get("node_id", "")
                parts.append(
                    f'<li class="audit-item audit-item--{sev_cls}">'
                    f'<span class="audit-sev audit-sev--{sev_cls}">{html.escape(sev)}</span>'
                    f'{html.escape(msg)}'
                    f'{f" <code>{html.escape(node)}</code>" if node else ""}'
                    f'</li>'
                )
            parts.append('</ul></details>')
        parts.append('</div></div>')

    return "\n".join(parts)


def _render_decision_brief_structured(data: dict) -> str | None:
    parts: list[str] = []

    ticker = data.get("ticker", "—")
    ref_price = data.get("reference_price")
    time_horizon = data.get("time_horizon", "—")

    parts.append(
        f'<div class="brief-overview">'
        f'<div class="brief-stat"><span class="brief-stat-label">Ticker</span>'
        f'<span class="brief-stat-value">{html.escape(str(ticker))}</span></div>'
        f'<div class="brief-stat"><span class="brief-stat-label">Reference Price</span>'
        f'<span class="brief-stat-value">{html.escape(str(ref_price) if ref_price else "—")}</span></div>'
        f'<div class="brief-stat"><span class="brief-stat-label">Time Horizon</span>'
        f'<span class="brief-stat-value">{html.escape(str(time_horizon))}</span></div>'
        f'</div>'
    )

    evidence_by_domain = data.get("evidence_by_domain", {})
    if evidence_by_domain:
        parts.append('<div class="evidence-domains"><h4>Evidence by Domain</h4>')
        for domain, items in evidence_by_domain.items():
            parts.append(
                f'<div class="evidence-domain">'
                f'<h5 class="domain-label">{html.escape(domain)}'
                f' <span class="text-muted">({len(items)})</span></h5>'
            )
            for item in items:
                direction = item.get("direction", "risk_only")
                dir_cls = _direction_class(direction)
                arrow = _direction_arrow(direction)
                strength = item.get("strength", 0)
                relevance = item.get("decision_relevance", "")
                ev_claim = item.get("claim", "")
                parts.append(
                    f'<div class="evidence-card evidence-card--{dir_cls}">'
                    f'<div class="evidence-meta">'
                    f'<span class="direction-badge direction-badge--{dir_cls}">{arrow} {html.escape(direction)}</span>'
                    f'{_strength_bar_html(strength)}'
                    f'{"<span class=\"relevance-tag\">" + html.escape(relevance) + "</span>" if relevance else ""}'
                    f'</div>'
                    f'<p class="evidence-claim">{html.escape(ev_claim)}</p>'
                    f'</div>'
                )
            parts.append('</div>')
        parts.append('</div>')

    for label, key in [("Top Supporting Evidence", "top_supporting_evidence"),
                       ("Top Opposing Evidence", "top_opposing_evidence")]:
        items = data.get(key, [])
        if items:
            parts.append(
                f'<div class="evidence-domain">'
                f'<h5 class="domain-label">{html.escape(label)}'
                f' <span class="text-muted">({len(items)})</span></h5>'
            )
            for item in items:
                direction = item.get("direction", "risk_only")
                dir_cls = _direction_class(direction)
                arrow = _direction_arrow(direction)
                strength = item.get("strength", 0)
                relevance = item.get("decision_relevance", "")
                ev_claim = item.get("claim", "")
                src_domain = item.get("source_domain", "")
                parts.append(
                    f'<div class="evidence-card evidence-card--{dir_cls}">'
                    f'<div class="evidence-meta">'
                    f'<span class="direction-badge direction-badge--{dir_cls}">{arrow} {html.escape(direction)}</span>'
                    f'{_strength_bar_html(strength)}'
                    f'{"<span class=\"relevance-tag\">" + html.escape(relevance) + "</span>" if relevance else ""}'
                    f'{"<span class=\"domain-tag\">" + html.escape(src_domain) + "</span>" if src_domain else ""}'
                    f'</div>'
                    f'<p class="evidence-claim">{html.escape(ev_claim)}</p>'
                    f'</div>'
                )
            parts.append('</div>')

    constraints = data.get("hard_constraints", [])
    if constraints:
        parts.append(f'<div class="constraints-section"><h4>Hard Constraints ({len(constraints)})</h4>')
        parts.append('<ol class="constraint-list">')
        for c in constraints:
            parts.append(f'<li>{html.escape(c)}</li>')
        parts.append('</ol></div>')

    missing = data.get("missing_or_stale_data", [])
    if missing:
        parts.append(
            f'<div class="missing-data-banner"><h4>Missing / Stale Data ({len(missing)})</h4><ul>'
            + "".join(f'<li>{html.escape(m)}</li>' for m in missing)
            + '</ul></div>'
        )

    return "\n".join(parts)


def _render_setup_diagnosis_structured(data: dict) -> str | None:
    setup = data.get("primary_setup", "—")
    quality = data.get("setup_quality", "—")
    entry = data.get("entry_status", "—")
    trigger_required = data.get("setup_requires_trigger", False)
    trigger_type = data.get("trigger_type", "—")
    quality_cls = quality.lower() if quality in ("A", "B", "C", "D", "F") else "default"

    parts: list[str] = [
        f'<div class="setup-header">'
        f'<div class="setup-type">{html.escape(setup.replace("_", " "))}</div>'
        f'<div class="setup-metrics">'
        f'<span class="quality-badge quality-badge--{quality_cls}">Quality {html.escape(quality)}</span>'
        f'<span class="setup-detail">Entry: <strong>{html.escape(entry)}</strong></span>'
        f'<span class="setup-detail">Trigger: <strong>'
        f'{"required (" + html.escape(trigger_type) + ")" if trigger_required else "not required"}'
        f'</strong></span>'
        f'</div></div>',
    ]

    reasons = data.get("why_this_setup", [])
    if reasons:
        parts.append('<div class="setup-section"><h4>Why this setup</h4><ul class="setup-reasons">')
        for r in reasons:
            parts.append(f'<li>{html.escape(r)}</li>')
        parts.append('</ul></div>')

    invalidations = data.get("what_would_invalidate_setup", [])
    if invalidations:
        parts.append('<div class="setup-section setup-section--warn"><h4>What would invalidate</h4><ul>')
        for inv in invalidations:
            parts.append(f'<li>{html.escape(inv)}</li>')
        parts.append('</ul></div>')

    return "\n".join(parts)


def _render_scenario_analysis_structured(data: dict) -> str | None:
    scenarios: list[tuple[str, float, str, str, str]] = []
    for key, label, color in [
        ("bull_case", "Bull", "#059669"),
        ("base_case", "Base", "#6b7280"),
        ("bear_case", "Bear", "#dc2626"),
    ]:
        case = data.get(key, {})
        if case:
            prob = case.get("probability", 0)
            path = case.get("path", "")
            target = case.get("target_price")
            zone = case.get("expected_price_zone")
            inv = case.get("invalidation_price")
            price_label = ""
            if target:
                price_label = f"Target: ${target}"
            elif zone:
                price_label = f"Range: ${zone}"
            elif inv:
                price_label = f"Invalidation: ${inv}"
            scenarios.append((label, prob, path, price_label, color))

    if not scenarios:
        return None

    rr = data.get("risk_reward_estimate")
    asymmetric = data.get("asymmetric_payoff")
    dominant_risk = data.get("dominant_risk", "")

    parts = ['<div class="scenario-grid">']
    for label, prob, path, price_label, color in scenarios:
        pct = round(prob * 100)
        parts.append(
            f'<div class="scenario-card">'
            f'<div class="scenario-label" style="color:{color}">{html.escape(label)}</div>'
            f'<div class="scenario-prob">'
            f'<span class="scenario-pct">{pct}%</span>'
            f'<div class="scenario-bar"><div class="scenario-fill" style="width:{pct}%;background:{color}"></div></div>'
            f'</div>'
            f'{"<div class=\"scenario-price\">" + html.escape(price_label) + "</div>" if price_label else ""}'
            f'<p class="scenario-path">{html.escape(path)}</p>'
            f'</div>'
        )
    parts.append('</div>')

    metrics: list[str] = []
    if rr is not None:
        metrics.append(f'Risk/Reward: <strong>{rr}</strong>')
    if asymmetric is not None:
        metrics.append(f'Asymmetric payoff: <strong>{"Yes" if asymmetric else "No"}</strong>')
    if dominant_risk:
        metrics.append(f'Dominant risk: {html.escape(dominant_risk)}')
    if metrics:
        parts.append('<div class="scenario-summary">' + " · ".join(metrics) + '</div>')

    return "\n".join(parts)


def _render_execution_plan_structured(data: dict) -> str | None:
    action = data.get("recommended_action", "—")
    intent = data.get("recommended_execution_intent", "—")
    confidence = data.get("confidence", "—")
    action_cls = action.lower() if action in ("BUY", "SELL", "HOLD") else "hold"

    parts: list[str] = [
        f'<div class="exec-header">'
        f'<span class="action-badge action-badge--{action_cls}">{html.escape(action)}</span>'
        f'<span class="exec-intent">{html.escape(intent.replace("_", " "))}</span>'
        f'<span class="confidence-badge confidence-badge--{confidence.lower()}">'
        f'{html.escape(confidence)}</span>'
        f'</div>',
    ]

    params = [
        ("Version", data.get("recommended_decision_version")),
        ("Order type", data.get("order_type")),
        ("Time in force", data.get("time_in_force")),
        ("Stop-loss", data.get("stop_loss")),
        ("Take-profit", data.get("take_profit")),
        ("Position size", data.get("position_size_pct")),
    ]
    param_cells = [
        f'<div class="param-cell"><span class="param-label">{lbl}</span>'
        f'<span class="param-value">{html.escape(str(val))}</span></div>'
        for lbl, val in params if val is not None
    ]
    if param_cells:
        parts.append('<div class="exec-params">' + "".join(param_cells) + '</div>')

    contract = data.get("canonical_json_contract", {})
    branches = contract.get("execution_plan", [])
    if branches:
        parts.append('<div class="exec-section"><h4>Execution Branches</h4>')
        for branch in branches:
            branch_id = branch.get("branch_id", "unnamed")
            priority = branch.get("priority", "—")
            conditions = branch.get("conditions", {})
            action_tmpl = branch.get("action_template", {})

            price_cond = conditions.get("price", {})
            cond_parts: list[str] = []
            if price_cond.get("close_above"):
                cond_parts.append(f'Close above {price_cond["close_above"]}')
            if price_cond.get("close_below"):
                cond_parts.append(f'Close below {price_cond["close_below"]}')

            tmpl_action = action_tmpl.get("action", "—")
            tmpl_cls = tmpl_action.lower() if tmpl_action in ("BUY", "SELL", "HOLD") else "hold"
            size = action_tmpl.get("position_size_pct")

            parts.append(
                f'<div class="exec-branch">'
                f'<div class="branch-header">'
                f'<code>{html.escape(branch_id)}</code>'
                f'<span class="text-muted">Priority {priority}</span>'
                f'</div>'
                + (
                    '<div class="branch-conditions">Conditions: '
                    + ", ".join(html.escape(c) for c in cond_parts)
                    + '</div>' if cond_parts else ''
                )
                + f'<div class="branch-action">'
                f'→ <span class="action-badge action-badge--{tmpl_cls} action-badge--sm">'
                f'{html.escape(tmpl_action)}</span>'
                + (f' · {size*100:.0f}% size' if size else '')
                + (f' · Stop {action_tmpl["stop_loss"]}' if action_tmpl.get("stop_loss") else '')
                + (f' · TP {action_tmpl["take_profit"]}' if action_tmpl.get("take_profit") else '')
                + f'</div></div>'
            )
        parts.append('</div>')

    checks = data.get("compiler_checks", [])
    if checks:
        parts.append('<div class="exec-section"><h4>Compiler Checks</h4><ul class="check-list">')
        for check in checks:
            parts.append(f'<li class="check-item"><span class="check-icon">✓</span> {html.escape(check)}</li>')
        parts.append('</ul></div>')

    return "\n".join(parts)


def _render_self_audit_structured(data: dict) -> str | None:
    passed = data.get("passed", False)
    violations = data.get("violations", [])
    repairs = data.get("repairs_applied", [])
    consistent = data.get("final_action_consistent", False)

    status_cls = "pass" if passed else "fail"
    status_icon = "✓" if passed else "✗"

    parts = [
        f'<div class="audit-status audit-status--{status_cls}">'
        f'<span class="audit-status-icon">{status_icon}</span>'
        f'<div class="audit-status-details">'
        f'<span class="audit-status-label">{"PASSED" if passed else "FAILED"}</span>'
        f'<span class="audit-status-meta">'
        f'{len(violations)} violation{"s" if len(violations) != 1 else ""} · '
        f'{len(repairs)} repair{"s" if len(repairs) != 1 else ""} · '
        f'Action consistent: {"yes" if consistent else "no"}'
        f'</span></div></div>',
    ]

    if violations:
        parts.append('<div class="audit-violations"><h4>Violations</h4><ul>')
        for v in violations:
            parts.append(f'<li>{html.escape(str(v))}</li>')
        parts.append('</ul></div>')

    if repairs:
        parts.append('<div class="audit-repairs"><h4>Repairs Applied</h4><ul>')
        for r in repairs:
            parts.append(f'<li>{html.escape(str(r))}</li>')
        parts.append('</ul></div>')

    return "\n".join(parts)


_DOMAIN_COLORS: dict[str, tuple[str, str, str]] = {
    # (border-left, badge-bg, badge-text)
    "catalyst":    ("#8b5cf6", "#ede9fe", "#5b21b6"),
    "market":      ("#3b82f6", "#dbeafe", "#1e40af"),
    "sentiment":   ("#f59e0b", "#fef3c7", "#92400e"),
    "fundamentals":("#10b981", "#d1fae5", "#065f46"),
    "news":        ("#ef4444", "#fee2e2", "#991b1b"),
}
_DOMAIN_ICONS: dict[str, str] = {
    "catalyst": "⚡", "market": "📊", "sentiment": "💬",
    "fundamentals": "📈", "news": "📰",
}


def _render_evidence_graph_structured(body: str) -> str | None:
    """Parse the markdown table in Evidence Graph Summary and render as grouped cards."""
    lines = body.strip().splitlines()
    rows: list[tuple[str, str, str]] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and "ID" in stripped and "Domain" in stripped:
            in_table = True
            continue
        if in_table and stripped.startswith("|:") and "--" in stripped:
            continue
        if in_table and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 3:
                rows.append((cells[0], cells[1], cells[2]))
        elif in_table and not stripped.startswith("|"):
            in_table = False

    if not rows:
        return None

    # Group by domain
    from collections import OrderedDict
    groups: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    for fact_id, domain, claim in rows:
        groups.setdefault(domain, []).append((fact_id, claim))

    # Find footer text like "Showing 20 of 95..."
    footer_text = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("_Showing") or stripped.startswith("Showing"):
            footer_text = stripped.strip("_")
            break

    parts: list[str] = ['<div class="evidence-graph">']

    # Summary bar
    total = len(rows)
    domain_counts = [(d, len(facts)) for d, facts in groups.items()]
    parts.append('<div class="evgraph-summary">')
    parts.append(f'<span class="evgraph-total">{total} evidence facts</span>')
    parts.append('<div class="evgraph-domain-pills">')
    for d, count in domain_counts:
        colors = _DOMAIN_COLORS.get(d, ("#6b7280", "#f3f4f6", "#4b5563"))
        icon = _DOMAIN_ICONS.get(d, "📋")
        parts.append(
            f'<span class="evgraph-pill" style="background:{colors[1]};color:{colors[2]}">'
            f'{icon} {html.escape(d)} <strong>{count}</strong></span>'
        )
    parts.append('</div></div>')

    # Domain groups
    for domain, facts in groups.items():
        colors = _DOMAIN_COLORS.get(domain, ("#6b7280", "#f3f4f6", "#4b5563"))
        icon = _DOMAIN_ICONS.get(domain, "📋")
        parts.append(f'<div class="evgraph-domain" style="--domain-accent:{colors[0]}">')
        parts.append(
            f'<h4 class="evgraph-domain-title">'
            f'<span class="evgraph-domain-icon">{icon}</span> '
            f'{html.escape(domain.capitalize())}'
            f'<span class="evgraph-domain-count">{len(facts)}</span></h4>'
        )
        for fact_id, claim in facts:
            claim_clean = claim.replace("\\|", "|").strip()
            # Detect if claim starts with markdown heading markers
            claim_display = re.sub(r"^#+\s*", "", claim_clean)
            is_header = claim_clean.startswith("#")
            header_class = " evgraph-fact--header" if is_header else ""
            parts.append(f'<div class="evgraph-fact{header_class}">')
            parts.append(f'<code class="evgraph-fact-id">{html.escape(fact_id)}</code>')
            parts.append(f'<span class="evgraph-fact-claim">{html.escape(claim_display)}</span>')
            parts.append('</div>')
        parts.append('</div>')

    if footer_text:
        footer_text = footer_text.replace(" Full evidence graph JSON is included below.", "")
        parts.append(f'<div class="evgraph-footer">{html.escape(footer_text)}</div>')

    parts.append('</div>')
    return "\n".join(parts)


_JSON_PANEL_RENDERERS: dict[str, object] = {
    "Decision Trace JSON": _render_decision_trace_structured,
    "Trader Decision Brief": _render_decision_brief_structured,
    "Trade Setup Diagnosis": _render_setup_diagnosis_structured,
    "Scenario Analysis": _render_scenario_analysis_structured,
    "Execution Plan Compiler": _render_execution_plan_structured,
    "Trader Self Audit": _render_self_audit_structured,
}


def _try_structured_render(title: str, body: str) -> str | None:
    if title == "Agent Reasoning Trace":
        return _render_reasoning_trace_structured(body)

    if title == "Evidence Graph Summary":
        return _render_evidence_graph_structured(body)

    renderer = _JSON_PANEL_RENDERERS.get(title)
    if renderer is None:
        return None
    data = _extract_json_from_body(body)
    if data is None:
        return None
    return renderer(data)


def _language_switch_script() -> str:
    exact_json = json.dumps(ZH_EXACT_TEXT, ensure_ascii=False, indent=6)
    phrase_json = json.dumps(ZH_PHRASE_REPLACEMENTS, ensure_ascii=False, indent=6)
    return (
        """
    const supportedLanguages = ['en', 'zh-CN'];
    const defaultLanguage = 'en';
    const exactZhText = __EXACT_JSON__;
    const phraseZhReplacements = __PHRASE_JSON__;
    const originalTextNodes = new WeakMap();
    const originalTitle = document.title;
    const languageButtons = Array.from(document.querySelectorAll('[data-lang-option]'));
    const languageSwitcher = document.querySelector('.language-switcher');
    const translationNote = document.querySelector('[data-translation-note]');

    function readStoredLanguage() {
      try {
        return localStorage.getItem('verumtrade-example-report-language');
      } catch {
        return null;
      }
    }

    function writeStoredLanguage(language) {
      try {
        localStorage.setItem('verumtrade-example-report-language', language);
      } catch {
        // Switching still works for this page view when storage is unavailable.
      }
    }

    function getInitialLanguage() {
      const stored = readStoredLanguage();
      if (supportedLanguages.includes(stored)) return stored;
      const browserLanguage = navigator.language || '';
      return browserLanguage.toLowerCase().startsWith('zh') ? 'zh-CN' : defaultLanguage;
    }

    function shouldTranslateTextNode(node) {
      if (!node.nodeValue.trim()) return false;
      const parent = node.parentElement;
      if (!parent) return false;
      if (parent.closest('script, style, code, pre, kbd, samp')) return false;
      return true;
    }

    function collectTextNodes() {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          return shouldTranslateTextNode(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        },
      });
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      return nodes;
    }

    function translateText(value) {
      const trimmed = value.trim();
      if (!trimmed) return value;
      if (exactZhText[trimmed]) return value.replace(trimmed, exactZhText[trimmed]);

      let translated = trimmed;
      phraseZhReplacements.forEach(([source, target]) => {
        translated = translated.split(source).join(target);
      });
      return value.replace(trimmed, translated);
    }

    function restoreEnglish() {
      collectTextNodes().forEach((node) => {
        if (originalTextNodes.has(node)) node.nodeValue = originalTextNodes.get(node);
      });
    }

    function setLanguage(language) {
      const selectedLanguage = supportedLanguages.includes(language) ? language : defaultLanguage;
      restoreEnglish();

      document.documentElement.lang = selectedLanguage;
      document.title = selectedLanguage === 'zh-CN'
        ? 'Verumtrade 示例报告：MU (2026-06-23) | Verumtrade example report'
        : originalTitle;

      if (selectedLanguage === 'zh-CN') {
        collectTextNodes().forEach((node) => {
          if (!originalTextNodes.has(node)) originalTextNodes.set(node, node.nodeValue);
          node.nodeValue = translateText(node.nodeValue);
        });
      }

      languageButtons.forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.langOption === selectedLanguage));
      });
      if (languageSwitcher) {
        languageSwitcher.setAttribute('aria-label', selectedLanguage === 'zh-CN' ? '语言' : 'Language');
      }
      if (translationNote) translationNote.hidden = selectedLanguage !== 'zh-CN';
      writeStoredLanguage(selectedLanguage);
    }

    languageButtons.forEach((button) => {
      button.addEventListener('click', () => setLanguage(button.dataset.langOption));
    });

    setLanguage(getInitialLanguage());
"""
        .replace("__EXACT_JSON__", exact_json)
        .replace("__PHRASE_JSON__", phrase_json)
    )


def render_example_report_html(markdown: str, source_path: Path) -> str:
    global _ANNOTATE

    intro, sections = _split_sections(markdown)
    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    page_title = title_match.group(1).strip() if title_match else "Verumtrade example report"
    section_ids: dict[str, int] = {}
    rendered_sections: list[str] = []
    nav_items: list[str] = []

    for title, body in sections:
        base_id = _slug(title)
        count = section_ids.get(base_id, 0)
        section_ids[base_id] = count + 1
        section_id = base_id if count == 0 else f"{base_id}-{count + 1}"
        nav_items.append(
            f'<a href="#{section_id}" data-nav-target="{section_id}">{html.escape(title)}</a>'
        )
        # Reset glossary tooltips per section so each section annotates the
        # first use of a symbol once rather than every occurrence document-wide.
        _SECTION_SEEN.clear()
        rendered_sections.append(
            f'<section class="report-section" id="{section_id}" data-section-id="{section_id}">'
            f'<div class="section-heading"><h2>{html.escape(title)}</h2></div>'
            f'{_render_section_body(title, body)}</section>'
        )

    _SECTION_SEEN.clear()
    intro_html = render_markdown_fragment(intro)

    # The glossary card defines the terms, so it should not itself be annotated.
    _ANNOTATE = False
    glossary_html = _build_glossary_card(markdown)
    _ANNOTATE = True

    if glossary_html:
        glossary_nav = '<a href="#key-terms" data-nav-target="key-terms">Key terms &amp; symbols</a>'
        nav_items.append(glossary_nav)

    nav_html = "\n".join(nav_items)
    if glossary_html:
        rendered_sections.append(glossary_html)
    sections_html = "\n".join(rendered_sections)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)} | Verumtrade example report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f5f8;
      --surface: #ffffff;
      --surface-2: #f7f9fc;
      --ink: #0f172a;
      --text: #243142;
      --muted: #667085;
      --line: #d8e0ea;
      --line-strong: #bdc8d6;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --accent-3: #7c3aed;
      --good: #047857;
      --bad: #b42318;
      --warn: #b45309;
      --shadow: 0 18px 48px rgba(15, 23, 42, 0.09);
      --shadow-soft: 0 8px 22px rgba(15, 23, 42, 0.06);
      --measure: 74ch;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(37, 99, 235, 0.08), transparent 34rem),
        linear-gradient(180deg, #ffffff 0, var(--bg) 19rem);
      color: var(--text);
      font: 15px/1.62 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    .app-header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: end;
      justify-content: space-between;
      gap: 24px;
      max-width: 1440px;
      margin: 0 auto;
      padding: 28px 24px 18px;
      border-bottom: 1px solid var(--line);
    }}
    .brand {{ display: grid; gap: 5px; min-width: 0; }}
    .brand strong {{ color: var(--ink); font-size: clamp(24px, 3vw, 38px); line-height: 1.08; font-weight: 850; }}
    .brand span {{ color: var(--muted); font-size: 13px; }}
    .toolbar {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
      max-width: 620px;
    }}
    .language-switcher {{
      display: inline-flex;
      gap: 3px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      box-shadow: var(--shadow-soft);
    }}
    .language-switcher button {{
      height: 28px;
      min-width: 44px;
      border: 0;
      border-radius: 5px;
      padding: 0 10px;
      color: var(--muted);
      background: transparent;
      font-weight: 700;
      cursor: pointer;
    }}
    .language-switcher button[aria-pressed="true"] {{
      color: #fff;
      background: var(--accent);
    }}
    .language-switcher button:focus-visible {{
      outline: 3px solid rgba(15, 118, 110, 0.22);
      outline-offset: 2px;
    }}
    .toolbar input {{
      width: min(30vw, 340px);
      min-width: 180px;
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 0 10px;
      background: #fff;
      color: var(--text);
      box-shadow: var(--shadow-soft);
    }}
    .toolbar button, .panel-toggle {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 7px;
      cursor: pointer;
    }}
    .toolbar button {{ height: 36px; padding: 0 12px; font-weight: 750; box-shadow: var(--shadow-soft); }}
    .toolbar button:hover, .panel-toggle:hover {{ border-color: var(--accent); color: var(--accent); }}
    .report-layout {{
      display: grid;
      grid-template-columns: 250px minmax(0, 1fr);
      gap: 24px;
      max-width: 1440px;
      margin: 0 auto;
      padding: 22px 24px 44px;
    }}
    .side-nav {{
      align-self: start;
      display: grid;
      gap: 6px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: var(--shadow-soft);
    }}
    .side-nav a {{
      display: block;
      padding: 8px 10px;
      border-radius: 6px;
      color: var(--muted);
      text-decoration: none;
      font-weight: 720;
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    .side-nav a:hover, .side-nav a.is-active {{ color: var(--ink); background: #ecfdf5; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; min-width: 0; }}
    /* Let sections shrink so wide tables/code scroll inside their own box
       instead of stretching the whole page when panels are expanded. */
    main > * {{ min-width: 0; }}
    .intro, .report-section {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.96);
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .intro {{
      padding: 22px 24px;
      border-color: #c7d2fe;
      background: linear-gradient(135deg, #ffffff 0%, #eff6ff 100%);
    }}
    .section-heading {{
      padding: 15px 18px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, #ffffff 0%, var(--surface-2) 100%);
    }}
    .report-section {{ scroll-margin-top: 20px; }}
    h1, h2, h3, h4 {{ margin: 0 0 10px; line-height: 1.25; letter-spacing: 0; }}
    h1 {{ font-size: 26px; }}
    h2 {{ color: var(--ink); font-size: 20px; }}
    h3 {{ font-size: 17px; margin-top: 24px; color: var(--accent); }}
    h4 {{ font-size: 14px; margin-top: 18px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
    p {{ margin: 0 0 12px; }}
    /* Keep prose to a comfortable reading measure; tables stay full width. */
    .panel-body > p, .panel-body > ul, .panel-body > ol,
    .intro p, .intro ul, blockquote {{ max-width: var(--measure); }}
    blockquote {{
      margin: 0;
      padding: 14px 16px;
      border-left: 4px solid var(--warn);
      border-radius: 0 7px 7px 0;
      background: #fff7ed;
      color: #7c2d12;
    }}
    abbr.gloss {{
      text-decoration: none;
      border-bottom: 1px dotted var(--accent-2);
      cursor: help;
    }}
    .glossary {{ padding: 16px 18px; }}
    .glossary > summary {{
      cursor: pointer;
      color: var(--muted);
      font-weight: 600;
      list-style: none;
      max-width: var(--measure);
    }}
    .glossary > summary::-webkit-details-marker {{ display: none; }}
    .glossary > summary::before {{ content: "▸ "; color: var(--accent-2); }}
    .glossary[open] > summary::before {{ content: "▾ "; }}
    .glossary dl {{
      margin: 14px 0 0;
      display: grid;
      grid-template-columns: minmax(140px, 230px) minmax(0, 1fr);
      gap: 6px 18px;
      align-items: baseline;
    }}
    .glossary dt {{ font-weight: 700; color: var(--text); }}
    .glossary dd {{ margin: 0; color: #475467; }}
    @media (max-width: 640px) {{
      .glossary dl {{ grid-template-columns: 1fr; gap: 2px 0; }}
      .glossary dd {{ margin: 0 0 10px; }}
    }}
    .report-panel + .report-panel {{ border-top: 1px solid var(--line); }}
    .panel-toggle {{
      width: 100%;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 13px 16px;
      border-width: 0;
      border-radius: 0;
      font-weight: 700;
      text-align: left;
      background: #fff;
    }}
    .report-panel.is-open .panel-toggle {{ color: var(--accent); }}
    .chevron {{ transition: transform 0.16s ease; }}
    .report-panel.is-open .chevron {{ transform: rotate(180deg); }}
    .panel-body {{ padding: 18px; border-top: 1px solid var(--line); }}
    .table-wrap {{ width: 100%; overflow-x: auto; margin: 12px 0 18px; border: 1px solid var(--line); border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 680px; background: #fff; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; text-align: left; }}
    th {{ background: #eef2f7; font-size: 11.5px; letter-spacing: 0.03em; color: #475467; text-transform: uppercase; }}
    tbody tr:nth-child(even) td {{ background: #fafbfc; }}
    tbody tr:hover td {{ background: #f1f7f6; }}
    td code {{ font-size: 0.85em; color: #475467; }}
    tr:last-child td {{ border-bottom: 0; }}
    ul {{ margin: 0 0 14px 20px; padding: 0; }}
    li {{ margin: 5px 0; }}
    code {{ padding: 1px 4px; border-radius: 4px; background: #eef2f5; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 0.92em; }}
    pre {{ overflow: auto; padding: 14px; border-radius: 6px; background: #111827; color: #e5e7eb; }}
    pre code {{ padding: 0; background: transparent; color: inherit; }}
    .empty-search {{
      display: none;
      padding: 24px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
      text-align: center;
    }}
    .translation-note {{
      padding: 12px 14px;
      border: 1px solid #bfdbfe;
      border-radius: 8px;
      background: #eff6ff;
      color: #1e3a8a;
      font-size: 14px;
    }}
    body.has-empty-search .empty-search {{ display: block; }}

    /* ── Pipeline trace timeline ── */
    .pipeline-trace {{ padding: 8px 0; }}
    .trace-step {{ display: grid; grid-template-columns: 28px 1fr; gap: 0 14px; }}
    .trace-connector {{ display: flex; flex-direction: column; align-items: center; padding-top: 2px; }}
    .trace-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
    .trace-dot--available {{ background: #059669; box-shadow: 0 0 0 3px rgba(5,150,105,0.18); }}
    .trace-dot--missing {{ background: #dc2626; box-shadow: 0 0 0 3px rgba(220,38,38,0.18); }}
    .trace-line {{ width: 2px; flex: 1; background: var(--line); margin: 4px 0; }}
    .trace-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; background: #fafbfc; }}
    .trace-header {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .trace-stage {{ font-weight: 700; font-size: 14px; }}
    .trace-status {{ font-size: 12px; font-weight: 600; border-radius: 999px; padding: 2px 10px; }}
    .trace-status--available {{ color: #065f46; background: #d1fae5; }}
    .trace-status--missing {{ color: #991b1b; background: #fee2e2; }}
    .trace-meta {{ display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }}
    .trace-badge {{ font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
    .trace-badge--agent {{ background: #e0e7ff; color: #3730a3; }}
    .trace-badge--kind {{ background: #f3f4f6; color: #4b5563; }}
    .trace-chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
    .trace-chip {{ display: inline-flex; gap: 4px; font-size: 12px; padding: 3px 8px; border-radius: 4px; background: #fff; border: 1px solid var(--line); }}
    .trace-chip-label {{ color: var(--muted); }}
    .trace-chip-value {{ font-weight: 600; }}

    /* ── Decision trace / action badges ── */
    .decision-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }}
    .action-badge {{ display: inline-block; font-weight: 800; font-size: 14px; letter-spacing: 0.06em; padding: 6px 16px; border-radius: 6px; text-transform: uppercase; }}
    .action-badge--sm {{ font-size: 12px; padding: 2px 10px; }}
    .action-badge--buy {{ background: #d1fae5; color: #065f46; }}
    .action-badge--sell {{ background: #fee2e2; color: #991b1b; }}
    .action-badge--hold {{ background: #fef3c7; color: #92400e; }}
    .decision-ticker {{ font-size: 22px; font-weight: 800; letter-spacing: -0.01em; }}
    .decision-thesis {{ padding: 14px 16px; border-left: 4px solid var(--accent); background: #f0fdfa; border-radius: 0 8px 8px 0; margin-bottom: 16px; }}
    .decision-thesis h4 {{ margin: 0 0 6px; }}
    .decision-thesis p {{ margin: 0; max-width: var(--measure); }}
    .decision-section {{ margin-bottom: 16px; }}
    .decision-section h4 {{ margin: 0 0 8px; }}
    .chip-grid {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .chip-grid code {{ font-size: 11px; }}
    .text-muted {{ color: var(--muted); }}

    /* ── Audit groups ── */
    .audit-groups {{ display: grid; gap: 6px; }}
    .audit-group summary {{ cursor: pointer; padding: 6px 0; }}
    .audit-list {{ margin: 4px 0 8px 16px; padding: 0; list-style: none; }}
    .audit-item {{ padding: 4px 0; font-size: 13px; border-bottom: 1px solid #f3f4f6; }}
    .audit-sev {{ display: inline-block; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; padding: 1px 6px; border-radius: 3px; margin-right: 6px; }}
    .audit-sev--high {{ background: #fee2e2; color: #991b1b; }}
    .audit-sev--medium {{ background: #fef3c7; color: #92400e; }}
    .audit-sev--low {{ background: #f3f4f6; color: #4b5563; }}

    .snapshot-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
      padding: 18px;
    }}
    .snapshot-card {{
      min-width: 0;
      display: grid;
      align-content: space-between;
      min-height: 96px;
      padding: 15px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      box-shadow: var(--shadow-soft);
    }}
    .snapshot-card--primary {{
      border-color: #b8d7d3;
      background: linear-gradient(180deg, #ffffff 0%, #edfafa 100%);
    }}
    .snapshot-label {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 850;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .snapshot-value {{
      display: block;
      margin-top: 10px;
      color: var(--ink);
      font-size: 16px;
      line-height: 1.2;
      font-weight: 850;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }}
    .metric-badge {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      max-width: 100%;
      padding: 3px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 850;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      overflow-wrap: anywhere;
    }}
    .metric-badge--buy, .metric-badge--grade-a, .metric-badge--grade-b {{ background: #d1fae5; color: #065f46; }}
    .metric-badge--sell, .metric-badge--grade-d, .metric-badge--grade-f {{ background: #fee2e2; color: #991b1b; }}
    .metric-badge--hold, .metric-badge--grade-c {{ background: #fef3c7; color: #92400e; }}
    .metric-badge--neutral, .metric-badge--grade-default {{ background: #e0e7ff; color: #3730a3; }}

    /* ── Evidence dashboard ── */
    .brief-overview {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .brief-stat {{ padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; background: #fafbfc; }}
    .brief-stat-label {{ display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; }}
    .brief-stat-value {{ font-size: 18px; font-weight: 700; }}
    .evidence-domains {{ margin-bottom: 16px; }}
    .evidence-domain {{ margin-bottom: 12px; }}
    .domain-label {{ font-weight: 700; font-size: 14px; margin: 0 0 8px; text-transform: capitalize; color: var(--text); }}
    .evidence-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin: 6px 0; }}
    .evidence-card--bullish {{ border-left: 4px solid #059669; }}
    .evidence-card--bearish {{ border-left: 4px solid #dc2626; }}
    .evidence-card--risk {{ border-left: 4px solid #d97706; }}
    .evidence-meta {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }}
    .direction-badge {{ font-size: 12px; font-weight: 700; padding: 2px 8px; border-radius: 4px; }}
    .direction-badge--bullish {{ background: #d1fae5; color: #065f46; }}
    .direction-badge--bearish {{ background: #fee2e2; color: #991b1b; }}
    .direction-badge--risk {{ background: #fef3c7; color: #92400e; }}
    .strength-bar {{ display: inline-flex; align-items: center; gap: 6px; }}
    .strength-track {{ width: 50px; height: 5px; border-radius: 3px; background: #e5e7eb; overflow: hidden; }}
    .strength-fill {{ height: 100%; border-radius: 3px; background: var(--accent); }}
    .strength-label {{ font-size: 12px; font-weight: 600; color: var(--muted); }}
    .relevance-tag, .domain-tag {{ font-size: 11px; padding: 2px 8px; border-radius: 4px; background: #f3f4f6; color: #4b5563; }}
    .evidence-claim {{ margin: 0; font-size: 13px; max-width: var(--measure); }}
    .constraints-section {{ margin-top: 16px; }}
    .constraint-list {{ margin: 4px 0; padding-left: 20px; }}
    .constraint-list li {{ font-size: 13px; margin: 6px 0; }}
    .missing-data-banner {{ padding: 12px 14px; border: 1px solid #fbbf24; border-radius: 8px; background: #fffbeb; margin-top: 16px; }}
    .missing-data-banner h4 {{ color: #92400e; margin: 0 0 6px; }}
    .missing-data-banner ul {{ margin: 0; padding-left: 18px; }}
    .missing-data-banner li {{ font-size: 13px; color: #78350f; }}

    /* ── Setup diagnosis ── */
    .setup-header {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 0; border-bottom: 1px solid var(--line); margin-bottom: 14px; }}
    .setup-type {{ font-size: 16px; font-weight: 800; letter-spacing: -0.01em; color: var(--accent); }}
    .setup-metrics {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .quality-badge {{ font-size: 13px; font-weight: 800; padding: 4px 12px; border-radius: 6px; }}
    .quality-badge--a {{ background: #d1fae5; color: #065f46; }}
    .quality-badge--b {{ background: #dbeafe; color: #1e40af; }}
    .quality-badge--c {{ background: #fef3c7; color: #92400e; }}
    .quality-badge--d, .quality-badge--f {{ background: #fee2e2; color: #991b1b; }}
    .quality-badge--default {{ background: #f3f4f6; color: #4b5563; }}
    .setup-detail {{ font-size: 13px; color: var(--muted); }}
    .setup-section {{ margin-bottom: 14px; }}
    .setup-section h4 {{ margin: 0 0 6px; }}
    .setup-section--warn {{ padding: 12px 14px; border: 1px solid #fbbf24; border-radius: 8px; background: #fffbeb; }}
    .setup-reasons {{ margin: 0; padding-left: 18px; }}
    .setup-reasons li {{ font-size: 13px; margin: 4px 0; }}

    /* ── Scenario analysis ── */
    .scenario-readout, .scenario-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      padding: 18px;
    }}
    .scenario-grid {{ padding: 0; margin-bottom: 14px; }}
    .scenario-card {{
      min-width: 0;
      border: 1px solid var(--line);
      border-top: 4px solid var(--scenario-accent, var(--accent-2));
      border-radius: 8px;
      padding: 15px;
      background: #ffffff;
      box-shadow: var(--shadow-soft);
    }}
    .scenario-card--bull {{ --scenario-accent: var(--good); }}
    .scenario-card--base {{ --scenario-accent: #64748b; }}
    .scenario-card--bear {{ --scenario-accent: var(--bad); }}
    .scenario-card-header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }}
    .scenario-label {{ color: var(--scenario-accent, var(--accent-2)); font-weight: 850; font-size: 14px; margin-bottom: 8px; }}
    .scenario-prob {{ margin-bottom: 8px; }}
    .scenario-pct {{ color: var(--ink); font-size: 26px; line-height: 1; font-weight: 850; }}
    .scenario-bar {{ height: 6px; border-radius: 3px; background: #e5e7eb; overflow: hidden; margin-top: 4px; }}
    .scenario-fill {{ height: 100%; border-radius: 3px; background: var(--scenario-accent, var(--accent-2)); }}
    .scenario-price {{ color: var(--ink); font-size: 13px; font-weight: 800; margin: 10px 0 6px; }}
    .scenario-path {{ font-size: 13px; margin: 0; color: var(--muted); }}
    .scenario-summary {{ font-size: 13px; color: var(--muted); padding-top: 8px; border-top: 1px solid var(--line); }}

    /* ── Execution plan ── */
    .exec-header {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }}
    .exec-intent {{ font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: var(--muted); }}
    .confidence-badge {{ font-size: 12px; font-weight: 700; padding: 2px 10px; border-radius: 999px; }}
    .confidence-badge--low {{ background: #fef3c7; color: #92400e; }}
    .confidence-badge--medium {{ background: #dbeafe; color: #1e40af; }}
    .confidence-badge--high {{ background: #d1fae5; color: #065f46; }}
    .exec-params {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; margin-bottom: 14px; }}
    .param-cell {{ padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: #fafbfc; }}
    .param-label {{ display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; }}
    .param-value {{ font-size: 15px; font-weight: 700; }}
    .exec-section {{ margin-bottom: 14px; }}
    .exec-section h4 {{ margin: 0 0 8px; }}
    .exec-branch {{ padding: 10px 14px; border: 1px solid var(--line); border-radius: 8px; margin: 6px 0; background: #fafbfc; }}
    .branch-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
    .branch-conditions {{ font-size: 13px; margin-bottom: 4px; color: var(--muted); }}
    .branch-action {{ font-size: 13px; font-weight: 600; }}
    .check-list {{ list-style: none; margin: 0; padding: 0; }}
    .check-item {{ padding: 4px 0; font-size: 13px; }}
    .check-icon {{ color: #059669; margin-right: 6px; }}

    /* ── Self audit status ── */
    .audit-status {{ display: flex; align-items: center; gap: 12px; padding: 14px 16px; border-radius: 8px; }}
    .audit-status--pass {{ background: #ecfdf5; border: 1px solid #a7f3d0; }}
    .audit-status--fail {{ background: #fef2f2; border: 1px solid #fecaca; }}
    .audit-status-icon {{ font-size: 24px; }}
    .audit-status-label {{ font-weight: 800; font-size: 16px; }}
    .audit-status--pass .audit-status-label {{ color: #065f46; }}
    .audit-status--fail .audit-status-label {{ color: #991b1b; }}
    .audit-status-meta {{ font-size: 13px; color: var(--muted); display: block; }}

    /* ── Evidence graph summary ── */
    .evidence-graph {{ padding: 4px 0; }}
    .evgraph-summary {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 12px 16px; border: 1px solid var(--line); border-radius: 10px; background: #fafbfc; margin-bottom: 18px; }}
    .evgraph-total {{ font-size: 15px; font-weight: 800; color: var(--text); white-space: nowrap; }}
    .evgraph-domain-pills {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .evgraph-pill {{ display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 999px; white-space: nowrap; }}
    .evgraph-pill strong {{ font-weight: 800; }}
    .evgraph-domain {{ margin-bottom: 16px; }}
    .evgraph-domain-title {{ display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 700; margin: 0 0 8px; padding-bottom: 6px; border-bottom: 2px solid var(--domain-accent, var(--line)); color: var(--text); }}
    .evgraph-domain-icon {{ font-size: 16px; }}
    .evgraph-domain-count {{ font-size: 11px; font-weight: 700; background: var(--domain-accent, var(--muted)); color: #fff; width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; }}
    .evgraph-fact {{ display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: baseline; padding: 8px 12px; border-left: 3px solid var(--domain-accent, var(--line)); background: #fff; margin: 0; border-bottom: 1px solid #f3f4f6; }}
    .evgraph-fact:first-of-type {{ border-radius: 8px 8px 0 0; }}
    .evgraph-fact:last-of-type {{ border-radius: 0 0 8px 8px; border-bottom: none; }}
    .evgraph-fact:only-of-type {{ border-radius: 8px; }}
    .evgraph-fact--header {{ background: #fafbfc; }}
    .evgraph-fact--header .evgraph-fact-claim {{ font-weight: 700; font-size: 13px; }}
    .evgraph-fact-id {{ font-size: 10px; font-family: var(--font-mono, 'SF Mono', 'Consolas', monospace); padding: 2px 6px; border-radius: 4px; background: #f3f4f6; color: #6b7280; white-space: nowrap; font-weight: 500; }}
    .evgraph-fact-claim {{ font-size: 13px; color: var(--text); word-break: break-word; }}
    .evgraph-footer {{ font-size: 12px; color: var(--muted); font-style: italic; padding: 10px 0 0; text-align: center; }}

    @media (max-width: 900px) {{
      body {{ overflow-x: hidden; }}
      .app-header {{ width: 100%; max-width: 100%; grid-template-columns: minmax(0, 1fr); align-items: stretch; gap: 14px; padding: 28px 14px 18px; }}
      .brand strong {{ max-width: 12ch; }}
      .brand span, h1, h2, h3, h4 {{ overflow-wrap: anywhere; }}
      .toolbar {{ justify-content: flex-start; max-width: none; width: 100%; min-width: 0; flex-wrap: wrap; }}
      .toolbar input {{ width: 100%; min-width: 0; }}
      .toolbar button {{ flex: 1 1 130px; min-width: 0; }}
      .report-layout {{ width: 100%; max-width: 100%; min-width: 0; overflow: hidden; grid-template-columns: minmax(0, 1fr); padding: 14px; }}
      main, .side-nav, .intro, .report-section, .section-heading, .glossary, .panel-body, .table-wrap {{
        width: 100%;
        max-width: 100%;
        min-width: 0;
      }}
      .side-nav {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .intro, .report-section {{ max-width: 100%; }}
      .snapshot-grid {{ grid-template-columns: 1fr; padding: 14px; }}
      .scenario-readout, .scenario-grid {{ grid-template-columns: 1fr; padding: 14px; }}
      .brief-overview {{ grid-template-columns: 1fr; }}
      .exec-params {{ grid-template-columns: 1fr; }}
      .setup-header {{ flex-direction: column; align-items: flex-start; }}
    }}
  </style>
</head>
<body>
  <header class="app-header">
    <div class="brand">
      <strong>Verumtrade example report</strong>
      <span>{html.escape(page_title)} · generated from {html.escape(str(source_path))}</span>
    </div>
    <div class="toolbar">
      <div class="language-switcher" role="group" aria-label="Language">
        <button type="button" data-lang-option="en" aria-pressed="true">EN</button>
        <button type="button" data-lang-option="zh-CN" aria-pressed="false">简体</button>
      </div>
      <input id="reportSearch" type="search" placeholder="Search report sections" aria-label="Search report sections">
      <button type="button" id="expandAll">Expand all</button>
      <button type="button" id="collapseAll">Collapse all</button>
    </div>
  </header>
  <div class="report-layout">
    <nav class="side-nav" aria-label="Report sections">
      {nav_html}
    </nav>
    <main>
      <section class="intro">{intro_html}</section>
      <div class="translation-note" data-translation-note hidden>简体中文视图会翻译报告正文和页面 UI；ticker、ratings、setup、agent names、指标名、配置键以及原始 JSON/code diagnostics 保留英文，便于和原始运行结果逐项核对。</div>
      {sections_html}
      <div class="empty-search">No report sections match this search.</div>
    </main>
  </div>
  <script>
{_language_switch_script()}
    const panels = Array.from(document.querySelectorAll('.report-panel'));
    const sections = Array.from(document.querySelectorAll('.report-section'));
    const navLinks = Array.from(document.querySelectorAll('[data-nav-target]'));
    const search = document.getElementById('reportSearch');

    function setPanel(panel, open) {{
      const button = panel.querySelector('.panel-toggle');
      const body = panel.querySelector('.panel-body');
      panel.classList.toggle('is-open', open);
      button.setAttribute('aria-expanded', String(open));
      body.hidden = !open;
    }}

    panels.forEach((panel) => {{
      panel.querySelector('.panel-toggle').addEventListener('click', () => {{
        setPanel(panel, !panel.classList.contains('is-open'));
      }});
    }});

    document.getElementById('expandAll').addEventListener('click', () => panels.forEach((panel) => setPanel(panel, true)));
    document.getElementById('collapseAll').addEventListener('click', () => panels.forEach((panel) => setPanel(panel, false)));

    search.addEventListener('input', () => {{
      const query = search.value.trim().toLowerCase();
      let visibleCount = 0;
      sections.forEach((section) => {{
        const match = !query || section.textContent.toLowerCase().includes(query);
        section.hidden = !match;
        if (match) visibleCount += 1;
      }});
      document.body.classList.toggle('has-empty-search', visibleCount === 0);
    }});

    const observer = new IntersectionObserver((entries) => {{
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      navLinks.forEach((link) => {{
        link.classList.toggle('is-active', link.dataset.navTarget === visible.target.id);
      }});
    }}, {{ rootMargin: '-25% 0px -65% 0px', threshold: [0.1, 0.4, 0.7] }});
    sections.forEach((section) => observer.observe(section));
  </script>
</body>
</html>
"""


def export_html(markdown_path: Path, html_path: Path | None = None) -> Path:
    markdown = markdown_path.read_text(encoding="utf-8")
    output_path = html_path or markdown_path.with_suffix(".html")
    output_path.write_text(
        render_example_report_html(markdown, source_path=markdown_path),
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export an Verumtrade example markdown report to static HTML.")
    parser.add_argument("markdown_path", type=Path)
    parser.add_argument("html_path", type=Path, nargs="?")
    args = parser.parse_args()
    output_path = export_html(args.markdown_path, args.html_path)
    print(output_path)


if __name__ == "__main__":
    main()
