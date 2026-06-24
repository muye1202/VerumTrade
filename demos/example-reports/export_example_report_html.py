from __future__ import annotations

import argparse
import html
import re
from pathlib import Path


DETAILS_RE = re.compile(
    r"<details(?P<open>\s+open)?\>\s*"
    r"<summary><strong>(?P<title>.*?)</strong></summary>\s*"
    r"(?P<body>.*?)"
    r"</details>",
    re.DOTALL,
)


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


def render_markdown_fragment(markdown: str) -> str:
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

    if in_fence:
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
        is_open = bool(match.group("open"))
        body = render_markdown_fragment(match.group("body"))
        expanded = "true" if is_open else "false"
        hidden = "" if is_open else " hidden"
        open_class = " is-open" if is_open else ""
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
            f'{_render_panels(body)}</section>'
        )

    _SECTION_SEEN.clear()
    intro_html = render_markdown_fragment(intro)

    # The glossary card defines the terms, so it should not itself be annotated.
    _ANNOTATE = False
    glossary_html = _build_glossary_card(markdown)
    _ANNOTATE = True

    if glossary_html:
        nav_items.insert(
            0, '<a href="#key-terms" data-nav-target="key-terms">Key terms &amp; symbols</a>'
        )

    nav_html = "\n".join(nav_items)
    sections_html = glossary_html + "\n" + "\n".join(rendered_sections)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)} | Verumtrade example report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --surface-2: #eef2f5;
      --text: #1f2933;
      --muted: #667085;
      --line: #d9e0e7;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --warn: #b45309;
      --shadow: 0 12px 32px rgba(31, 41, 51, 0.08);
      --measure: 74ch;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.62 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    .app-header {{
      position: sticky;
      top: 0;
      z-index: 20;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(10px);
    }}
    .brand {{ display: grid; gap: 2px; min-width: 0; }}
    .brand strong {{ font-size: 16px; }}
    .brand span {{ color: var(--muted); font-size: 12px; }}
    .toolbar {{ display: flex; align-items: center; gap: 8px; }}
    .toolbar input {{
      width: min(30vw, 340px);
      min-width: 180px;
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      background: #fff;
      color: var(--text);
    }}
    .toolbar button, .panel-toggle {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      cursor: pointer;
    }}
    .toolbar button {{ height: 36px; padding: 0 12px; }}
    .toolbar button:hover, .panel-toggle:hover {{ border-color: var(--accent); }}
    .report-layout {{
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
      gap: 24px;
      max-width: 1440px;
      margin: 0 auto;
      padding: 24px;
    }}
    .side-nav {{
      position: sticky;
      top: 78px;
      align-self: start;
      display: grid;
      gap: 6px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }}
    .side-nav a {{
      display: block;
      padding: 8px 10px;
      border-radius: 6px;
      color: var(--muted);
      text-decoration: none;
      font-weight: 600;
    }}
    .side-nav a:hover, .side-nav a.is-active {{ color: var(--accent); background: #e7f5f2; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; min-width: 0; }}
    /* Let sections shrink so wide tables/code scroll inside their own box
       instead of stretching the whole page when panels are expanded. */
    main > * {{ min-width: 0; }}
    .intro, .report-section {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }}
    .intro {{ padding: 20px 22px; }}
    .section-heading {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--surface-2);
      border-radius: 8px 8px 0 0;
    }}
    .report-section {{ scroll-margin-top: 92px; }}
    h1, h2, h3, h4 {{ margin: 0 0 10px; line-height: 1.25; letter-spacing: -0.01em; }}
    h1 {{ font-size: 26px; }}
    h2 {{ font-size: 20px; }}
    h3 {{ font-size: 17px; margin-top: 24px; color: var(--accent); }}
    h4 {{ font-size: 14px; margin-top: 18px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
    p {{ margin: 0 0 12px; }}
    /* Keep prose to a comfortable reading measure; tables stay full width. */
    .panel-body > p, .panel-body > ul, .panel-body > ol,
    .intro p, .intro ul, blockquote {{ max-width: var(--measure); }}
    blockquote {{
      margin: 0;
      padding: 12px 14px;
      border-left: 4px solid var(--warn);
      border-radius: 0 6px 6px 0;
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
    th {{ position: sticky; top: 0; background: #eef2f7; font-size: 11.5px; letter-spacing: 0.03em; color: #475467; text-transform: uppercase; }}
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
    body.has-empty-search .empty-search {{ display: block; }}
    @media (max-width: 900px) {{
      .app-header {{ align-items: stretch; flex-direction: column; }}
      .toolbar {{ flex-wrap: wrap; }}
      .toolbar input {{ width: 100%; min-width: 0; }}
      .report-layout {{ grid-template-columns: 1fr; padding: 14px; }}
      .side-nav {{ position: static; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
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
      {sections_html}
      <div class="empty-search">No report sections match this search.</div>
    </main>
  </div>
  <script>
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
