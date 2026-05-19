#!/usr/bin/env python3
import argparse
import html as html_escape
import json
import os
import re
import shutil
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PUBLIC_DIR = Path("public")
CONFIG_PATH = Path("config/config.yaml")
DEPLOY_CONFIG_PATH = Path("config/deploy.yaml")


AI_ANALYSIS_PROFILES = {
    "simple": {
        "max_reports": 7,
        "max_titles_per_report": 12,
        "include_daily_ai_blocks": False,
        "max_input_chars": 18000,
        "max_output_tokens": 1200,
        "temperature": 0.3,
        "style": "粗略分析，抓主线，不展开太多细节。",
    },
    "medium": {
        "max_reports": 7,
        "max_titles_per_report": 25,
        "include_daily_ai_blocks": True,
        "max_input_chars": 42000,
        "max_output_tokens": 2200,
        "temperature": 0.35,
        "style": "常规分析，兼顾主线、变化、异动和建议。",
    },
    "deep": {
        "max_reports": 14,
        "max_titles_per_report": 45,
        "include_daily_ai_blocks": True,
        "max_input_chars": 90000,
        "max_output_tokens": 4200,
        "temperature": 0.4,
        "style": "深度分析，强调跨日演化、共振、分歧、弱信号和下阶段研判。",
    },
}


PERIODIC_RULES = {
    "weekly": {
        "enabled_key": "weekly_report",
        "output_file": "weekly.html",
        "title": "TrendRadar 一周总结",
        "subtitle": "基于最近一周历史报告生成",
        "lookback_days": 7,
        "min_days": 5,
    },
    "monthly": {
        "enabled_key": "monthly_report",
        "output_file": "monthly.html",
        "title": "TrendRadar 月度总结",
        "subtitle": "基于最近一个月历史报告生成",
        "lookback_days": 31,
        "min_days": 18,
    },
}


def read_yaml_simple(path: Path) -> dict[str, Any]:
    """
    Lightweight YAML reader.

    Prefer PyYAML if available. If not, use a small fallback that supports
    the config shape used here:

    periodic:
      weekly_report: true
      monthly_report: false
      ai_analysis_level: simple
    """
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8")

    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data or {}
    except Exception:
        pass

    result: dict[str, Any] = {}
    current_section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            result[current_section] = {}
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        parsed_value: Any
        lower = value.lower()
        if lower == "true":
            parsed_value = True
        elif lower == "false":
            parsed_value = False
        elif re.fullmatch(r"-?\d+", value):
            parsed_value = int(value)
        else:
            parsed_value = value.strip("\"'")

        if current_section and raw_line.startswith(" "):
            section = result.setdefault(current_section, {})
            if isinstance(section, dict):
                section[key] = parsed_value
        else:
            result[key] = parsed_value

    return result


def read_retention_days(default_days: int = 30) -> int:
    if not DEPLOY_CONFIG_PATH.exists():
        print("config/deploy.yaml not found, use default 30 days")
        return default_days

    try:
        data = read_yaml_simple(DEPLOY_CONFIG_PATH)

        if "retention_days" in data:
            return int(data["retention_days"])

        html_config = data.get("html")
        if isinstance(html_config, dict) and "retention_days" in html_config:
            return int(html_config["retention_days"])

    except Exception as exc:
        print(f"Failed to read config/deploy.yaml, use default {default_days} days: {exc}")
        return default_days

    print(f"retention_days not found, use default {default_days} days")
    return default_days


def read_periodic_config() -> dict[str, Any]:
    data = read_yaml_simple(CONFIG_PATH)
    periodic = data.get("periodic") or {}

    if not isinstance(periodic, dict):
        periodic = {}

    level = str(periodic.get("ai_analysis_level", "simple")).strip().lower()
    if level not in AI_ANALYSIS_PROFILES:
        print(f"Unknown periodic.ai_analysis_level={level}, fallback to simple")
        level = "simple"

    return {
        "weekly_report": bool(periodic.get("weekly_report", True)),
        "monthly_report": bool(periodic.get("monthly_report", False)),
        "ai_analysis_level": level,
    }


def cleanup_old_local_html() -> None:
    retention_days = read_retention_days()
    cutoff = datetime.now().date() - timedelta(days=retention_days)
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    if not PUBLIC_DIR.is_dir():
        raise SystemExit("public directory not found")

    deleted = 0

    for path in PUBLIC_DIR.iterdir():
        if not path.is_dir():
            continue

        name = path.name

        # 只清理 public/2026-05-16 这种日期目录
        # 不动 public/index.html、public/latest、public/history.html、weekly.html、monthly.html
        if not date_pattern.match(name):
            continue

        folder_date = datetime.strptime(name, "%Y-%m-%d").date()
        if folder_date < cutoff:
            print(f"Deleting old html folder: {path}")
            shutil.rmtree(path)
            deleted += 1

    print(f"HTML retention days: {retention_days}")
    print(f"Deleted old html folders: {deleted}")


def collect_history_rows() -> list[tuple[str, str, str, str]]:
    rows = []

    if not PUBLIC_DIR.exists():
        return rows

    for date_dir in sorted(PUBLIC_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue

        name = date_dir.name
        if len(name) != 10 or name[4] != "-" or name[7] != "-":
            continue

        for html_file in sorted(date_dir.glob("*.html"), reverse=True):
            rel = html_file.relative_to(PUBLIC_DIR).as_posix()
            time_text = html_file.stem.replace("-", ":")
            title = f"{name} {time_text}"
            rows.append((title, rel, name, time_text))

    return rows


def get_latest_report_per_day() -> list[Path]:
    """
    For each date folder, use the latest html report.
    This avoids feeding many repeated same-day reports into weekly/monthly analysis.
    """
    reports: list[Path] = []

    if not PUBLIC_DIR.exists():
        return reports

    date_dirs = [
        path
        for path in PUBLIC_DIR.iterdir()
        if path.is_dir()
        and len(path.name) == 10
        and path.name[4] == "-"
        and path.name[7] == "-"
    ]

    for date_dir in sorted(date_dirs, reverse=True):
        html_files = sorted(date_dir.glob("*.html"), reverse=True)
        if html_files:
            reports.append(html_files[0])

    return reports


def filter_reports_by_period(period: str) -> list[Path]:
    rule = PERIODIC_RULES[period]
    lookback_days = int(rule["lookback_days"])
    cutoff = datetime.now().date() - timedelta(days=lookback_days - 1)

    reports = []

    for report in get_latest_report_per_day():
        day = report.parent.name
        try:
            report_date = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue

        if report_date >= cutoff:
            reports.append(report)

    return reports


def strip_tags(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html_escape.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_class_texts(html: str, class_name: str) -> list[str]:
    pattern = re.compile(
        rf'<[^>]*class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</[^>]+>',
        re.S | re.I,
    )
    values = []
    for match in pattern.findall(html):
        text = strip_tags(match)
        if text:
            values.append(text)
    return values


def extract_ai_blocks(html: str) -> list[tuple[str, str]]:
    titles = extract_class_texts(html, "ai-block-title")
    contents = extract_class_texts(html, "ai-block-content")
    blocks = []

    for idx, title in enumerate(titles):
        content = contents[idx] if idx < len(contents) else ""
        if title or content:
            blocks.append((title, content))

    return blocks


def summarize_report_for_ai(report_path: Path, profile: dict[str, Any]) -> str:
    raw = report_path.read_text(encoding="utf-8", errors="ignore")

    day = report_path.parent.name
    time_text = report_path.stem.replace("-", ":")

    word_names = extract_class_texts(raw, "word-name")
    standalone_names = extract_class_texts(raw, "standalone-name")
    feed_names = extract_class_texts(raw, "feed-name")
    news_titles = extract_class_texts(raw, "news-title")
    rss_titles = extract_class_texts(raw, "rss-title")

    # 去重但保持顺序
    def dedupe(items: list[str]) -> list[str]:
        seen = set()
        out = []
        for item in items:
            key = item.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    word_names = dedupe(word_names)
    standalone_names = dedupe(standalone_names)
    feed_names = dedupe(feed_names)
    titles = dedupe(news_titles + rss_titles)

    max_titles = int(profile["max_titles_per_report"])
    titles = titles[:max_titles]

    lines = [
        f"## {day} {time_text}",
        "",
    ]

    if word_names:
        lines.append("关键词主题：")
        lines.append("、".join(word_names[:20]))
        lines.append("")

    if feed_names:
        lines.append("RSS/订阅主题：")
        lines.append("、".join(feed_names[:20]))
        lines.append("")

    if standalone_names:
        lines.append("独立源点：")
        lines.append("、".join(standalone_names[:20]))
        lines.append("")

    if titles:
        lines.append("代表性新闻标题：")
        for title in titles:
            lines.append(f"- {title}")
        lines.append("")

    if profile.get("include_daily_ai_blocks"):
        blocks = extract_ai_blocks(raw)
        if blocks:
            lines.append("单日报告中的 AI 分析：")
            for block_title, block_content in blocks[:6]:
                lines.append(f"### {block_title}")
                lines.append(block_content[:1600])
                lines.append("")

    return "\n".join(lines).strip()


def build_periodic_source_text(period: str, level: str, reports: list[Path]) -> str:
    profile = AI_ANALYSIS_PROFILES[level]
    max_reports = int(profile["max_reports"])
    selected_reports = reports[:max_reports]

    chunks = [
        summarize_report_for_ai(report_path, profile)
        for report_path in selected_reports
    ]

    text = "\n\n---\n\n".join(chunk for chunk in chunks if chunk)
    max_chars = int(profile["max_input_chars"])

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[输入材料因长度限制已截断]"

    return text


def build_periodic_prompt(period: str, level: str, source_text: str) -> str:
    profile = AI_ANALYSIS_PROFILES[level]
    rule = PERIODIC_RULES[period]
    title = str(rule["title"])

    if level == "simple":
        sections = """
请输出以下部分：
1. 周期核心主线
2. 热点主题变化
3. 值得继续关注的信号
4. 下阶段观察清单
"""
    elif level == "medium":
        sections = """
请输出以下部分：
1. 周期核心热点态势
2. 主题热度变化与跨日演化
3. 跨平台共振与舆论分歧
4. 异动与弱信号
5. 下阶段观察清单
6. 研判策略建议
"""
    else:
        sections = """
请输出以下部分：
1. 周期总览：一句话判断本周期主线
2. 核心热点态势：分宏观主线、产业主线、社会舆论主线
3. 主题热度演化：哪些主题升温、降温、反复出现
4. 跨平台共振与认知温差：哪些话题在不同来源之间表现不同
5. 异动与弱信号：找出突发、低频但可能重要的信号
6. 结构性判断：哪些趋势可能延续，哪些只是短期噪声
7. 下阶段观察清单：列出 5-8 个继续跟踪的问题
8. 研判策略建议：面向投资者、品牌方、普通读者分别给建议
"""

    return f"""
你是 TrendRadar 的周期总结分析器。请基于以下历史热点报告材料，生成《{title}》。

分析深度：{level}
分析要求：{profile["style"]}

要求：
- 不要逐条复述新闻；
- 重点提炼趋势、变化、共振、分歧和弱信号；
- 保持中文输出；
- 标题清晰，适合直接渲染成 HTML；
- 如果材料不足，请明确说明不确定性；
- 不要编造材料中没有的信息；
- 风格参考 TrendRadar 单日报告中的“核心热点态势 / 舆论风向争议 / 异动与弱信号 / 研判策略建议”。

输出结构：
{sections}

以下是输入材料：
{source_text}
""".strip()


def call_ai(prompt: str, level: str) -> str | None:
    api_key = os.environ.get("AI_API_KEY", "").strip()
    api_base = os.environ.get("AI_API_BASE", "").strip().rstrip("/")
    model = os.environ.get("AI_MODEL", "").strip()

    if not api_key or not model:
        print("AI_API_KEY or AI_MODEL is empty, skip periodic report")
        return None

    if not api_base:
        api_base = "https://api.openai.com/v1"

    profile = AI_ANALYSIS_PROFILES[level]

    url = f"{api_base}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是一个中文热点趋势分析助手，擅长从多日新闻报告中提炼趋势、分歧、共振和弱信号。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": profile["temperature"],
        "max_tokens": profile["max_output_tokens"],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        print(f"Periodic AI request failed, skip: {exc}")
        return None

    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"Failed to parse AI response, skip: {exc}")
        return None


def text_to_html_content(text: str) -> str:
    escaped = html_escape.escape(text)

    # Very small markdown-like rendering.
    escaped = re.sub(r"^### (.+)$", r"<h3>\1</h3>", escaped, flags=re.M)
    escaped = re.sub(r"^## (.+)$", r"<h2>\1</h2>", escaped, flags=re.M)
    escaped = re.sub(r"^# (.+)$", r"<h1>\1</h1>", escaped, flags=re.M)
    escaped = re.sub(r"^\d+\.\s+(.+)$", r"<h2>\1</h2>", escaped, flags=re.M)
    escaped = re.sub(r"^- (.+)$", r"<li>\1</li>", escaped, flags=re.M)

    lines = escaped.splitlines()
    out = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("<li>") and stripped.endswith("</li>"):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(stripped)
            continue

        if in_list:
            out.append("</ul>")
            in_list = False

        if not stripped:
            continue

        if stripped.startswith("<h1>") or stripped.startswith("<h2>") or stripped.startswith("<h3>"):
            out.append(stripped)
        else:
            out.append(f"<p>{stripped}</p>")

    if in_list:
        out.append("</ul>")

    return "\n".join(out)


def render_periodic_html(
    period: str,
    level: str,
    ai_text: str,
    reports: list[Path],
) -> str:
    rule = PERIODIC_RULES[period]
    title = str(rule["title"])
    subtitle = str(rule["subtitle"])
    content_html = text_to_html_content(ai_text)
    day_count = len({path.parent.name for path in reports})
    report_count = len(reports)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape.escape(title)}</title>
  <style>
    * {{
      box-sizing: border-box;
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      margin: 0;
      padding: 16px;
      background: #fafafa;
      color: #333;
      line-height: 1.6;
    }}

    .container {{
      max-width: 960px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 2px 16px rgba(0,0,0,0.06);
    }}

    .header {{
      position: relative;
      overflow: hidden;
      padding: 36px 28px 32px;
      color: #ffffff;
      text-align: center;
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
    }}

    .header-watermark {{
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      font-size: clamp(42px, 8vw, 88px);
      font-weight: 900;
      letter-spacing: 0.05em;
      color: rgba(255, 255, 255, 0.14);
      pointer-events: none;
      z-index: 1;
      white-space: nowrap;
      user-select: none;
    }}

    .header-actions {{
      position: absolute;
      top: 16px;
      right: 16px;
      z-index: 2;
      display: flex;
      gap: 8px;
    }}

    .header-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 10px 18px;
      border-radius: 6px;
      border: 1px solid rgba(255,255,255,0.3);
      background: rgba(255,255,255,0.2);
      color: #ffffff;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      backdrop-filter: blur(10px);
      transition: all 0.2s ease;
    }}

    .header-btn:hover {{
      background: rgba(255,255,255,0.3);
      border-color: rgba(255,255,255,0.5);
      transform: translateY(-1px);
      text-decoration: none;
    }}

    .header-title {{
      position: relative;
      z-index: 2;
      margin: 0 0 12px;
      font-size: 26px;
      font-weight: 800;
    }}

    .header-subtitle {{
      position: relative;
      z-index: 2;
      margin: 0;
      font-size: 14px;
      opacity: 0.9;
    }}

    .summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      padding: 22px 28px;
      border-bottom: 1px solid #eef0f4;
      background: #ffffff;
    }}

    .summary-item {{
      padding: 14px 16px;
      border-radius: 10px;
      background: #f8fafc;
    }}

    .summary-label {{
      display: block;
      margin-bottom: 4px;
      color: #6b7280;
      font-size: 12px;
    }}

    .summary-value {{
      color: #111827;
      font-size: 17px;
      font-weight: 700;
    }}

    .content {{
      padding: 28px;
    }}

    .report-content h1,
    .report-content h2,
    .report-content h3 {{
      color: #1f2937;
      line-height: 1.35;
    }}

    .report-content h1 {{
      margin: 0 0 18px;
      font-size: 24px;
    }}

    .report-content h2 {{
      margin: 28px 0 12px;
      padding-bottom: 8px;
      border-bottom: 1px solid #eef0f4;
      font-size: 19px;
    }}

    .report-content h3 {{
      margin: 20px 0 8px;
      font-size: 16px;
      color: #4f46e5;
    }}

    .report-content p {{
      margin: 10px 0;
      color: #374151;
      font-size: 15px;
    }}

    .report-content ul {{
      margin: 8px 0 16px;
      padding-left: 22px;
    }}

    .report-content li {{
      margin: 6px 0;
    }}

    .footer {{
      padding: 18px 28px 24px;
      border-top: 1px solid #eef0f4;
      color: #6b7280;
      font-size: 13px;
      text-align: center;
      background: #f8f9fa;
    }}

    .footer a {{
      color: #4f46e5;
      text-decoration: none;
      font-weight: 600;
    }}

    .footer a:hover {{
      color: #7c3aed;
      text-decoration: underline;
    }}

    @media (max-width: 600px) {{
      body {{
        padding: 12px;
      }}

      .header {{
        padding: 28px 20px 24px;
      }}

      .header-actions {{
        position: static;
        justify-content: center;
        margin-bottom: 16px;
      }}

      .summary {{
        grid-template-columns: 1fr;
        padding: 18px 20px;
      }}

      .content {{
        padding: 20px;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="header-watermark">TrendRadar</div>
      <div class="header-actions">
        <a class="header-btn" href="/history.html">历史</a>
        <a class="header-btn" href="/#tab-0">最新</a>
      </div>
      <h1 class="header-title">{html_escape.escape(title)}</h1>
      <p class="header-subtitle">{html_escape.escape(subtitle)}</p>
    </div>

    <div class="summary">
      <div class="summary-item">
        <span class="summary-label">覆盖天数</span>
        <span class="summary-value">{day_count} 天</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">参考报告</span>
        <span class="summary-value">{report_count} 份</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">分析深度</span>
        <span class="summary-value">{html_escape.escape(level)}</span>
      </div>
    </div>

    <div class="content">
      <div class="report-content">
        {content_html}
      </div>
    </div>

    <div class="footer">
      由 <strong>TrendRadar</strong> 生成 · {html_escape.escape(now_text)} ·
      <a href="/history.html">返回历史报告</a>
    </div>
  </div>
</body>
</html>
"""


def generate_periodic_report(period: str, level: str) -> bool:
    rule = PERIODIC_RULES[period]
    min_days = int(rule["min_days"])
    output_file = str(rule["output_file"])

    reports = filter_reports_by_period(period)
    day_count = len({path.parent.name for path in reports})

    if day_count < min_days:
        print(
            f"{period} report enabled but not enough history days "
            f"({day_count}/{min_days}), skip"
        )
        old_file = PUBLIC_DIR / output_file
        if old_file.exists():
            old_file.unlink()
            print(f"Removed stale {old_file}")
        return False

    source_text = build_periodic_source_text(period, level, reports)
    if not source_text.strip():
        print(f"No source text for {period} report, skip")
        return False

    prompt = build_periodic_prompt(period, level, source_text)
    ai_text = call_ai(prompt, level)

    if not ai_text:
        print(f"No AI content for {period} report, skip")
        return False

    html = render_periodic_html(period, level, ai_text, reports)
    output_path = PUBLIC_DIR / output_file
    output_path.write_text(html, encoding="utf-8")

    print(f"Generated {output_file} from {day_count} days, {len(reports)} reports")
    return True


def generate_periodic_reports_if_enabled() -> None:
    periodic = read_periodic_config()
    level = periodic["ai_analysis_level"]

    print(
        "Periodic config: "
        f"weekly_report={periodic['weekly_report']}, "
        f"monthly_report={periodic['monthly_report']}, "
        f"ai_analysis_level={level}"
    )

    if periodic["weekly_report"]:
        generate_periodic_report("weekly", level)
    else:
        stale = PUBLIC_DIR / "weekly.html"
        if stale.exists():
            stale.unlink()
            print("weekly_report disabled, removed stale public/weekly.html")

    if periodic["monthly_report"]:
        generate_periodic_report("monthly", level)
    else:
        stale = PUBLIC_DIR / "monthly.html"
        if stale.exists():
            stale.unlink()
            print("monthly_report disabled, removed stale public/monthly.html")


def generate_history_index() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    rows = collect_history_rows()

    periodic = read_periodic_config()
    weekly_exists = (PUBLIC_DIR / "weekly.html").exists()
    monthly_exists = (PUBLIC_DIR / "monthly.html").exists()

    action_links = ['<a class="header-btn" href="/#tab-0">返回最新</a>']
    if periodic["weekly_report"] and weekly_exists:
        action_links.append('<a class="header-btn" href="/weekly.html">一周总结</a>')
    if periodic["monthly_report"] and monthly_exists:
        action_links.append('<a class="header-btn" href="/monthly.html">月度总结</a>')

    action_links_html = "\n          ".join(action_links)

    cards = "\n".join(
        f'''
        <a class="history-card" href="/{html_escape.escape(rel)}">
          <div class="history-main">
            <div class="history-title">{html_escape.escape(title)}</div>
            <div class="history-meta">TrendRadar 历史报告</div>
          </div>
          <div class="history-date">
            <span>{html_escape.escape(day)}</span>
            <strong>{html_escape.escape(time_text)}</strong>
          </div>
        </a>
        '''
        for title, rel, day, time_text in rows
    )

    if not cards:
        cards = '''
        <div class="empty-card">
          暂无历史报告
        </div>
        '''

    now_text = datetime.now().strftime("%m-%d %H:%M")

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TrendRadar 历史报告</title>
  <style>
    * {{
      box-sizing: border-box;
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      margin: 0;
      padding: 16px;
      background: #fafafa;
      color: #333;
      line-height: 1.5;
    }}

    .container {{
      max-width: 960px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 2px 16px rgba(0,0,0,0.06);
    }}

    .header {{
      position: relative;
      overflow: hidden;
      padding: 36px 28px 32px;
      color: #ffffff;
      text-align: center;
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
    }}

    .header-watermark {{
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      font-size: clamp(42px, 8vw, 88px);
      font-weight: 900;
      letter-spacing: 0.05em;
      color: rgba(255, 255, 255, 0.14);
      pointer-events: none;
      z-index: 1;
      white-space: nowrap;
      user-select: none;
    }}

    .header-actions {{
      position: absolute;
      top: 16px;
      right: 16px;
      z-index: 2;
      display: flex;
      gap: 8px;
    }}

    .header-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 10px 18px;
      border-radius: 6px;
      border: 1px solid rgba(255,255,255,0.3);
      background: rgba(255,255,255,0.2);
      color: #ffffff;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      backdrop-filter: blur(10px);
      transition: all 0.2s ease;
    }}

    .header-btn:hover {{
      background: rgba(255,255,255,0.3);
      border-color: rgba(255,255,255,0.5);
      transform: translateY(-1px);
      text-decoration: none;
    }}

    .header-title {{
      position: relative;
      z-index: 2;
      margin: 0 0 12px;
      font-size: 26px;
      font-weight: 800;
    }}

    .header-subtitle {{
      position: relative;
      z-index: 2;
      margin: 0;
      font-size: 14px;
      opacity: 0.9;
    }}

    .summary {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      padding: 22px 28px;
      border-bottom: 1px solid #eef0f4;
      background: #ffffff;
    }}

    .summary-item {{
      padding: 14px 16px;
      border-radius: 10px;
      background: #f8fafc;
    }}

    .summary-label {{
      display: block;
      margin-bottom: 4px;
      color: #6b7280;
      font-size: 12px;
    }}

    .summary-value {{
      color: #111827;
      font-size: 18px;
      font-weight: 700;
    }}

    .content {{
      padding: 28px;
    }}

    .history-list {{
      display: grid;
      gap: 12px;
    }}

    .history-card {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      border: 1px solid #eef0f4;
      border-radius: 12px;
      color: inherit;
      text-decoration: none;
      background: #ffffff;
      transition: all 0.18s ease;
    }}

    .history-card:hover {{
      border-color: #c7d2fe;
      background: #f8faff;
      box-shadow: 0 8px 24px rgba(79,70,229,0.08);
      transform: translateY(-1px);
      text-decoration: none;
    }}

    .history-title {{
      color: #1f2937;
      font-size: 15px;
      font-weight: 700;
    }}

    .history-meta {{
      margin-top: 4px;
      color: #6b7280;
      font-size: 12px;
    }}

    .history-date {{
      flex-shrink: 0;
      min-width: 116px;
      padding-left: 16px;
      border-left: 1px solid #eef0f4;
      text-align: right;
    }}

    .history-date span {{
      display: block;
      color: #6b7280;
      font-size: 12px;
    }}

    .history-date strong {{
      display: block;
      color: #4f46e5;
      font-size: 18px;
    }}

    .empty-card {{
      padding: 24px;
      border-radius: 12px;
      background: #f8fafc;
      color: #6b7280;
      text-align: center;
    }}

    .footer {{
      padding: 18px 28px 24px;
      border-top: 1px solid #eef0f4;
      color: #6b7280;
      font-size: 13px;
      text-align: center;
      background: #f8f9fa;
    }}

    .footer a {{
      color: #4f46e5;
      text-decoration: none;
      font-weight: 600;
    }}

    .footer a:hover {{
      color: #7c3aed;
      text-decoration: underline;
    }}

    @media (max-width: 600px) {{
      body {{
        padding: 12px;
      }}

      .header {{
        padding: 28px 20px 24px;
      }}

      .header-actions {{
        position: static;
        justify-content: center;
        flex-wrap: wrap;
        margin-bottom: 16px;
      }}

      .summary {{
        grid-template-columns: 1fr;
        padding: 18px 20px;
      }}

      .content {{
        padding: 20px;
      }}

      .history-card {{
        align-items: flex-start;
        flex-direction: column;
      }}

      .history-date {{
        width: 100%;
        min-width: 0;
        padding-left: 0;
        padding-top: 10px;
        border-left: none;
        border-top: 1px solid #eef0f4;
        text-align: left;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="header-watermark">TrendRadar</div>
      <div class="header-actions">
        {action_links_html}
      </div>
      <h1 class="header-title">TrendRadar 历史报告</h1>
      <p class="header-subtitle">查看已归档的热点新闻分析</p>
    </div>

    <div class="summary">
      <div class="summary-item">
        <span class="summary-label">历史报告数</span>
        <span class="summary-value">{len(rows)} 份</span>
      </div>
      <div class="summary-item">
        <span class="summary-label">更新时间</span>
        <span class="summary-value">{now_text}</span>
      </div>
    </div>

    <div class="content">
      <div class="history-list">
        {cards}
      </div>
    </div>

    <div class="footer">
      由 <strong>TrendRadar</strong> 生成 · <a href="/#tab-0">返回最新报告</a>
    </div>
  </div>
</body>
</html>
"""

    (PUBLIC_DIR / "history.html").write_text(html, encoding="utf-8")
    print(f"Generated history.html with {len(rows)} reports")


def add_history_button_to_index_page() -> None:
    index_path = PUBLIC_DIR / "index.html"
    history_path = PUBLIC_DIR / "history.html"

    if not index_path.exists():
        print("public/index.html not found, skip adding history button")
        return

    if not history_path.exists():
        print("public/history.html not found, skip adding history button")
        return

    html = index_path.read_text(encoding="utf-8")

    if "trendradar-history-button" in html:
        print("History button already exists, skip")
        return

    style = """
            .history-btn {
                position: absolute;
                top: 16px;
                left: 16px;
                z-index: 10;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-height: 38px;
                padding: 10px 18px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.2);
                color: white;
                font-size: 13px;
                font-weight: 600;
                line-height: 1;
                text-decoration: none;
                backdrop-filter: blur(10px);
                -webkit-backdrop-filter: blur(10px);
                transition: all 0.2s ease;
                white-space: nowrap;
            }

            .history-btn:hover {
                background: rgba(255, 255, 255, 0.3);
                border-color: rgba(255, 255, 255, 0.5);
                color: white;
                text-decoration: none;
                transform: translateY(-1px);
            }

            body.dark-mode .history-btn {
                color: white;
            }

            @media (max-width: 480px) {
                .history-btn {
                    position: static;
                    display: inline-flex;
                    margin: 0 auto 12px auto;
                }
            }
"""

    button = '<a id="trendradar-history-button" class="history-btn" href="/history.html">历史</a>'

    if ".history-btn" not in html:
        if "</style>" in html:
            html = html.replace("</style>", style + "\n        </style>", 1)
        else:
            print("No </style> found, skip adding history button style")

    marker = '<div class="header-watermark">TrendRadar</div>'
    if marker in html:
        html = html.replace(marker, marker + "\n                " + button, 1)
    else:
        fallback = '<div class="header">'
        if fallback in html:
            html = html.replace(fallback, fallback + "\n                " + button, 1)
        else:
            print("No header marker found, skip adding history button")
            return

    index_path.write_text(html, encoding="utf-8")
    print("Added history button to public/index.html")


def send_report_link_to_wecom() -> None:
    webhook = os.environ.get("WEWORK_WEBHOOK_URL", "").strip()
    msg_type = os.environ.get("WEWORK_MSG_TYPE", "markdown").strip().lower()
    report_url_raw = os.environ.get("REPORT_URL", "").strip()

    if not webhook:
        print("WEWORK_WEBHOOK_URL is empty, skip WeCom notification")
        return

    # 支持换行、逗号、分号分隔多个 URL
    urls = [
        item.strip()
        for item in re.split(r"[\n,;]+", report_url_raw)
        if item.strip()
    ]

    latest_url = urls[0] if len(urls) >= 1 else ""
    history_url = urls[1] if len(urls) >= 2 else ""

    if not latest_url:
        print("REPORT_URL is empty, skip report link notification")
        return

    history_exists = (PUBLIC_DIR / "history.html").exists()

    content_lines = [
        "【TrendRadar 网页版报告】",
        "",
        "本期热点报告已更新。",
        "",
        "查看最新新闻：",
        latest_url,
    ]

    if history_url and history_exists:
        content_lines.extend([
            "",
            "查看历史新闻：",
            history_url,
        ])
    else:
        content_lines.extend([
            "",
            "历史新闻：暂无可用历史页，后续生成后会自动开放。",
        ])

    content = "\n".join(content_lines)

    if msg_type == "text":
        payload = {
            "msgtype": "text",
            "text": {
                "content": content,
            },
        }
    else:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        }

    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        print(f"WeCom notification failed, but workflow will continue: {exc}")


def prepare_pages() -> None:
    cleanup_old_local_html()

    # 先生成周期报告，再生成 history.html。
    # 这样 history.html 可以根据 weekly.html / monthly.html 是否存在来显示入口。
    generate_periodic_reports_if_enabled()
    generate_history_index()
    add_history_button_to_index_page()


def main() -> None:
    parser = argparse.ArgumentParser(description="TrendRadar Pages deployment helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("cleanup", help="Cleanup old local HTML folders")
    subparsers.add_parser("periodic", help="Generate weekly/monthly periodic reports if enabled")
    subparsers.add_parser("history", help="Generate public/history.html")
    subparsers.add_parser("button", help="Add history button to public/index.html")
    subparsers.add_parser("prepare", help="Run cleanup, periodic reports, history, and index button")
    subparsers.add_parser("notify", help="Send report links to WeCom")

    args = parser.parse_args()

    if args.command == "cleanup":
        cleanup_old_local_html()
    elif args.command == "periodic":
        generate_periodic_reports_if_enabled()
    elif args.command == "history":
        generate_history_index()
    elif args.command == "button":
        add_history_button_to_index_page()
    elif args.command == "prepare":
        prepare_pages()
    elif args.command == "notify":
        send_report_link_to_wecom()


if __name__ == "__main__":
    main()
