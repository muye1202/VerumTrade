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


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _inline_markdown(text: str) -> str:
    rendered = html.escape(text)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", rendered)
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


def render_example_report_html(markdown: str, source_path: Path) -> str:
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
        rendered_sections.append(
            f'<section class="report-section" id="{section_id}" data-section-id="{section_id}">'
            f'<div class="section-heading"><h2>{html.escape(title)}</h2></div>'
            f'{_render_panels(body)}</section>'
        )

    intro_html = render_markdown_fragment(intro)
    nav_html = "\n".join(nav_items)
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
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
    main {{ display: grid; gap: 18px; min-width: 0; }}
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
    h1, h2, h3, h4 {{ margin: 0 0 10px; line-height: 1.25; letter-spacing: 0; }}
    h1 {{ font-size: 26px; }}
    h2 {{ font-size: 20px; }}
    h3 {{ font-size: 17px; margin-top: 18px; }}
    h4 {{ font-size: 15px; margin-top: 16px; }}
    p {{ margin: 0 0 12px; }}
    blockquote {{
      margin: 0;
      padding: 10px 12px;
      border-left: 4px solid var(--warn);
      background: #fff7ed;
      color: #7c2d12;
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
    .table-wrap {{ width: 100%; overflow-x: auto; margin: 12px 0 18px; border: 1px solid var(--line); border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 720px; background: #fff; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; text-align: left; }}
    th {{ background: #f1f5f9; font-size: 12px; color: #475467; text-transform: uppercase; }}
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
