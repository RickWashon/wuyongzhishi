#!/usr/bin/env python3
import argparse
import calendar
import html as html_escape
import json
import os
import re
import shutil
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


PUBLIC_DIR = Path("public")
CONFIG_PATH = Path("config/config.yaml")
DEPLOY_CONFIG_PATH = Path("config/deploy.yaml")
PERIODIC_TIMEZONE = os.environ.get("PERIODIC_TIMEZONE", "Asia/Shanghai")
AI_INTERESTS_PATH = Path("config/ai_interests.txt")


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


# 周期报告规则。
#
# latest_file:
#   保留在 public/ 根目录下的「最新入口」，例如 /weekly.html。
#   这样不会破坏 history.html 里的固定入口。
#
# archive_dir:
#   保存所有历史周报/月报的归档目录，例如：
#   public/periodic/weekly/2026-jan-week-01.html
#   public/periodic/monthly/2026-jan.html
PERIODIC_RULES = {
    "weekly": {
        "enabled_key": "weekly_report",
        "latest_file": "weekly.html",
        "archive_dir": "periodic/weekly",
        "history_file": "weekly-history.html",
        "history_title": "TrendRadar 周报归档",
        "history_subtitle": "查看已生成的全部周报",
        "item_label": "周报",
        "title": "TrendRadar 一周总结",
        "subtitle": "基于最近一周历史报告生成",
        "lookback_days": 7,
        "min_days": 5,
    },
    "monthly": {
        "enabled_key": "monthly_report",
        "latest_file": "monthly.html",
        "archive_dir": "periodic/monthly",
        "history_file": "monthly-history.html",
        "history_title": "TrendRadar 月报归档",
        "history_subtitle": "查看已生成的全部月报",
        "item_label": "月报",
        "title": "TrendRadar 月度总结",
        "subtitle": "基于最近一个月历史报告生成",
        "lookback_days": 31,
        "min_days": 18,
    },
}


MONTH_ABBR = [
    "",
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
]


def get_periodic_now() -> datetime:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(PERIODIC_TIMEZONE))
        except Exception:
            pass
    return datetime.now()


def get_periodic_today() -> str:
    return get_periodic_now().strftime("%Y-%m-%d")


def get_period_id(period: str, now: datetime | None = None) -> str:
    """
    返回周期报告的唯一周期 ID。

    weekly 使用 ISO 周：
      2026-W21

    monthly 使用年月：
      2026-05
    """
    if now is None:
        now = get_periodic_now()

    if period == "weekly":
        iso_year, iso_week, _ = now.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"

    if period == "monthly":
        return now.strftime("%Y-%m")

    return now.strftime("%Y-%m-%d")


def get_month_week_index(now: datetime) -> int:
    """
    返回当月第几周，按自然日期粗分：
    1-7 日为 week 01，8-14 日为 week 02，依次类推。

    这样归档文件名会更直观：
    2026-jan-week-01.html
    """
    return ((now.day - 1) // 7) + 1


def get_period_archive_filename(period: str, now: datetime | None = None) -> str:
    """
    生成周期报告归档文件名。

    weekly:
      2026-jan-week-01.html

    monthly:
      2026-jan.html
    """
    if now is None:
        now = get_periodic_now()

    month = MONTH_ABBR[now.month]

    if period == "weekly":
        week_index = get_month_week_index(now)
        return f"{now.year}-{month}-week-{week_index:02d}.html"

    if period == "monthly":
        return f"{now.year}-{month}.html"

    return f"{now.strftime('%Y-%m-%d')}.html"


def get_period_output_paths(period: str) -> tuple[Path, Path, Path]:
    """
    返回周期报告的三个输出位置：

    archive_path:
      带日期/周期名的归档文件，永久保留，方便后续回看和月报复用。

    latest_path:
      public/periodic/weekly/latest.html 或 public/periodic/monthly/latest.html。

    root_latest_path:
      public/weekly.html 或 public/monthly.html，作为固定入口，供 history.html 链接。
    """
    rule = PERIODIC_RULES[period]
    archive_dir = PUBLIC_DIR / str(rule["archive_dir"])
    archive_dir.mkdir(parents=True, exist_ok=True)

    archive_path = archive_dir / get_period_archive_filename(period)
    latest_path = archive_dir / "latest.html"
    root_latest_path = PUBLIC_DIR / str(rule["latest_file"])

    return archive_path, latest_path, root_latest_path


def read_yaml_simple(path: Path) -> dict[str, Any]:
    """
    Prefer PyYAML if available. If not, use a small fallback that supports
    the simple config shape used here.
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

        lower = value.lower()
        if lower == "true":
            parsed_value: Any = True
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


def parse_weekly_schedule(value: Any) -> int | None:
    """
    weekly_report:
      false -> disabled
      true  -> 1, Monday
      1-7   -> Monday-Sunday
    """
    if value is False or value is None:
        return None

    if value is True:
        return 1

    try:
        day = int(value)
    except Exception:
        print(f"Invalid periodic.weekly_report={value}, disable weekly report")
        return None

    if 1 <= day <= 7:
        return day

    print(f"Invalid periodic.weekly_report={value}, expected 1-7/true/false, disable weekly report")
    return None


def parse_monthly_schedule(value: Any) -> int | None:
    """
    monthly_report:
      false -> disabled
      true  -> 1
      1-31  -> day of month
    """
    if value is False or value is None:
        return None

    if value is True:
        return 1

    try:
        day = int(value)
    except Exception:
        print(f"Invalid periodic.monthly_report={value}, disable monthly report")
        return None

    if 1 <= day <= 31:
        return day

    print(f"Invalid periodic.monthly_report={value}, expected 1-31/true/false, disable monthly report")
    return None


def get_effective_monthly_schedule_day(now: datetime, schedule_day: int) -> int:
    """
    如果配置的月报日期超过当月最大日期，则使用当月最后一天。

    例：
    monthly_report: 31
    - 1 月按 31 号生成
    - 2 月按 28/29 号生成
    - 4 月按 30 号生成
    """
    last_day = calendar.monthrange(now.year, now.month)[1]
    return min(schedule_day, last_day)


def should_generate_periodic_report(period: str, schedule_day: int | None) -> bool:
    if schedule_day is None:
        return False

    now = get_periodic_now()

    if period == "weekly":
        return now.isoweekday() == schedule_day

    if period == "monthly":
        effective_day = get_effective_monthly_schedule_day(now, schedule_day)
        return now.day == effective_day

    return False

    now = get_periodic_now()

    if period == "weekly":
        return now.isoweekday() == schedule_day

    if period == "monthly":
        return now.day == schedule_day

    return False


def read_ai_config() -> dict[str, Any]:
    data = read_yaml_simple(CONFIG_PATH)
    ai_config = data.get("ai") or {}

    if not isinstance(ai_config, dict):
        ai_config = {}

    model = os.environ.get("AI_MODEL", "").strip() or str(ai_config.get("model", "")).strip()
    api_key = os.environ.get("AI_API_KEY", "").strip() or str(ai_config.get("api_key", "")).strip()
    api_base = os.environ.get("AI_API_BASE", "").strip() or str(ai_config.get("api_base", "")).strip()

    timeout = ai_config.get("timeout", 120)
    temperature = ai_config.get("temperature", None)
    max_tokens = ai_config.get("max_tokens", None)
    num_retries = ai_config.get("num_retries", 1)
    fallback_models = ai_config.get("fallback_models", [])

    extra_params = ai_config.get("extra_params", {})
    if not isinstance(extra_params, dict):
        extra_params = {}

    try:
        timeout = int(timeout)
    except Exception:
        timeout = 120

    try:
        num_retries = int(num_retries)
    except Exception:
        num_retries = 1

    if not isinstance(fallback_models, list):
        fallback_models = []

    return {
        "model": model,
        "api_key": api_key,
        "api_base": api_base,
        "timeout": timeout,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "num_retries": num_retries,
        "fallback_models": fallback_models,
        "extra_params": extra_params,
    }


def read_periodic_config() -> dict[str, Any]:
    data = read_yaml_simple(CONFIG_PATH)
    periodic = data.get("periodic") or {}

    if not isinstance(periodic, dict):
        periodic = {}

    level = str(periodic.get("ai_analysis_level", "simple")).strip().lower()
    if level not in AI_ANALYSIS_PROFILES:
        print(f"Unknown periodic.ai_analysis_level={level}, fallback to simple")
        level = "simple"

    weekly_schedule = parse_weekly_schedule(periodic.get("weekly_report", 1))
    monthly_schedule = parse_monthly_schedule(periodic.get("monthly_report", False))

    return {
        "weekly_report": weekly_schedule is not None,
        "monthly_report": monthly_schedule is not None,
        "weekly_schedule_day": weekly_schedule,
        "monthly_schedule_day": monthly_schedule,
        "ai_analysis_level": level,
    }





def cleanup_old_local_html() -> None:
    retention_days = read_retention_days()
    cutoff = get_periodic_now().date() - timedelta(days=retention_days)
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
    For each date folder, use the latest HTML report.
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
    cutoff = get_periodic_now().date() - timedelta(days=lookback_days - 1)

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


def summarize_report_for_ai(report_path: Path, profile: dict[str, Any]) -> str:
    raw = report_path.read_text(encoding="utf-8", errors="ignore")

    day = report_path.parent.name
    time_text = report_path.stem.replace("-", ":")

    word_names = dedupe(extract_class_texts(raw, "word-name"))
    standalone_names = dedupe(extract_class_texts(raw, "standalone-name"))
    feed_names = dedupe(extract_class_texts(raw, "feed-name"))
    news_titles = extract_class_texts(raw, "news-title")
    rss_titles = extract_class_texts(raw, "rss-title")
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


def read_ai_interests() -> str:
    if not AI_INTERESTS_PATH.exists():
        print("config/ai_interests.txt not found, continue without interests scope")
        return ""

    try:
        text = AI_INTERESTS_PATH.read_text(encoding="utf-8").strip()
    except Exception as exc:
        print(f"Failed to read config/ai_interests.txt: {exc}")
        return ""

    # 去掉纯注释和空行，减少 token
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(line)

    return "\n".join(lines).strip()


def build_periodic_prompt(period: str, level: str, source_text: str) -> str:
    profile = AI_ANALYSIS_PROFILES[level]
    rule = PERIODIC_RULES[period]
    title = str(rule["title"])
    interests_text = read_ai_interests()

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

    interests_block = ""
    if interests_text:
        interests_block = f"""
用户关注领域限定，来自 config/ai_interests.txt：
{interests_text}

请优先围绕这些关注方向做周期总结。材料中与关注方向无关的娱乐、社会闲谈、低价值噪声可以弱化或忽略。
但如果某个非关注方向事件具有明显跨领域影响，也可以作为弱信号简要提及。
""".strip()

    return f"""
你是 TrendRadar 的周期总结分析器。请基于以下历史热点报告材料，生成《{title}》。

分析深度：{level}
分析要求：{profile["style"]}

{interests_block}

要求：
- 不要逐条复述新闻；
- 不要简单按 HTML 中出现次数排序来决定重点；
- 优先结合用户关注领域、TR 关键词/标签、跨日持续性和平台共振来判断重点；
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
    try:
        from litellm import completion
    except Exception as exc:
        print(f"litellm import failed, skip periodic report: {exc}")
        return None

    ai_config = read_ai_config()
    profile = AI_ANALYSIS_PROFILES[level]

    model = ai_config["model"]
    api_key = ai_config["api_key"]
    api_base = ai_config["api_base"]
    timeout = ai_config["timeout"]
    num_retries = ai_config["num_retries"]
    fallback_models = ai_config["fallback_models"]
    extra_params = ai_config["extra_params"]

    if not model:
        print("AI model is empty, skip periodic report")
        return None

    kwargs: dict[str, Any] = {
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
        "timeout": timeout,
        "num_retries": num_retries,
    }

    # API key 允许从环境变量或 config.yaml 传入。
    # 对于部分本地模型/网关，可能不需要 key；这里不强制报错。
    if api_key:
        kwargs["api_key"] = api_key

    # LiteLLM 原生提供商如 deepseek/deepseek-chat 通常不需要 api_base。
    # OpenAI 兼容中转/自建网关才需要 api_base。
    if api_base:
        kwargs["api_base"] = api_base

    # 周期报告按 simple/medium/deep 控制输出长度。
    max_tokens = int(profile["max_output_tokens"])
    if max_tokens > 0:
        kwargs["max_tokens"] = max_tokens

    # 优先用周期报告档位里的 temperature。
    # 如果你想完全跟随 config/config.yaml 的 ai.temperature，也可以改这里。
    kwargs["temperature"] = profile["temperature"]

    if fallback_models:
        kwargs["fallbacks"] = fallback_models

    if extra_params:
        kwargs.update(extra_params)

    try:
        response = completion(**kwargs)
    except Exception as exc:
        print(f"Periodic AI request failed via LiteLLM, skip: {exc}")
        return None

    try:
        content = response["choices"][0]["message"]["content"]
    except Exception:
        try:
            content = response.choices[0].message.content
        except Exception as exc:
            print(f"Failed to parse LiteLLM response, skip: {exc}")
            return None

    if not content:
        print("LiteLLM returned empty content, skip periodic report")
        return None

    return str(content).strip()


def already_generated_current_period(period: str) -> bool:
    """
    检查当前周期的归档文件是否已经存在。

    workflow 前面会先从 R2 restore public/。
    如果 R2 里已经有当前周期归档，例如：
      public/periodic/weekly/2026-jan-week-01.html
    本地也会存在，直接跳过 AI，避免同一周期重复花 token。

    注意：这里检查 archive_path，而不是 latest 入口。
    latest 会被下一个周期覆盖，但 archive 会永久保留。
    """
    archive_path, _, _ = get_period_output_paths(period)

    if archive_path.exists():
        print(f"{period} report archive already exists: {archive_path}, skip AI generation")
        return True

    return False


def text_to_html_content(text: str) -> str:
    escaped = html_escape.escape(text)

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
    now = get_periodic_now()
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")
    generated_date = now.strftime("%Y-%m-%d")
    period_id = get_period_id(period, now)
    archive_filename = get_period_archive_filename(period, now)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape.escape(title)}</title>
  <meta name="trendradar-periodic-type" content="{html_escape.escape(period)}">
  <meta name="trendradar-periodic-generated-date" content="{html_escape.escape(generated_date)}">
  <meta name="trendradar-periodic-period-id" content="{html_escape.escape(period_id)}">
  <meta name="trendradar-periodic-archive-file" content="{html_escape.escape(archive_filename)}">
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

    # 如果当前周期的归档文件已经从 R2 restore 回本地，就不再重复分析。
    if already_generated_current_period(period):
        return False

    reports = filter_reports_by_period(period)
    day_count = len({path.parent.name for path in reports})

    if day_count < min_days:
        print(
            f"{period} report enabled but not enough history days "
            f"({day_count}/{min_days}), skip"
        )
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

    # 同时写入：
    # 1. 周期归档文件：永久保留
    # 2. periodic/.../latest.html：该类型报告的 latest 副本
    # 3. /weekly.html 或 /monthly.html：固定入口，供 history.html 链接
    archive_path, latest_path, root_latest_path = get_period_output_paths(period)
    archive_path.write_text(html, encoding="utf-8")
    latest_path.write_text(html, encoding="utf-8")
    root_latest_path.write_text(html, encoding="utf-8")

    print(
        f"Generated {archive_path} and updated {latest_path}, {root_latest_path} "
        f"from {day_count} days, {len(reports)} reports"
    )
    return True


def generate_periodic_reports_if_enabled() -> None:
    periodic = read_periodic_config()
    level = periodic["ai_analysis_level"]
    now = get_periodic_now()

    print(
        "Periodic config: "
        f"weekly_report={periodic['weekly_report']}, "
        f"weekly_schedule_day={periodic['weekly_schedule_day']}, "
        f"monthly_report={periodic['monthly_report']}, "
        f"monthly_schedule_day={periodic['monthly_schedule_day']}, "
        f"ai_analysis_level={level}, "
        f"now={now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )

    if periodic["weekly_report"]:
        if should_generate_periodic_report("weekly", periodic["weekly_schedule_day"]):
            generate_periodic_report("weekly", level)
        else:
            print(
                "weekly_report enabled but today is not scheduled day "
                f"({now.isoweekday()}/{periodic['weekly_schedule_day']}), keep existing weekly.html"
            )
    else:
        # 关闭周报时，只移除 latest 入口，保留 periodic/weekly/ 下的历史归档。
        stale_root = PUBLIC_DIR / "weekly.html"
        stale_latest = PUBLIC_DIR / "periodic/weekly/latest.html"
        for stale in (stale_root, stale_latest):
            if stale.exists():
                stale.unlink()
                print(f"weekly_report disabled, removed stale {stale}")

    if periodic["monthly_report"]:
        if should_generate_periodic_report("monthly", periodic["monthly_schedule_day"]):
            generate_periodic_report("monthly", level)
        else:
            effective_monthly_day = get_effective_monthly_schedule_day(
                now,
                periodic["monthly_schedule_day"],
            )
            print(
                "monthly_report enabled but today is not scheduled day "
                f"({now.day}/{effective_monthly_day}, configured={periodic['monthly_schedule_day']}), "
                "keep existing monthly.html"
            )
    else:
        # 关闭月报时，只移除 latest 入口，保留 periodic/monthly/ 下的历史归档。
        stale_root = PUBLIC_DIR / "monthly.html"
        stale_latest = PUBLIC_DIR / "periodic/monthly/latest.html"
        for stale in (stale_root, stale_latest):
            if stale.exists():
                stale.unlink()
                print(f"monthly_report disabled, removed stale {stale}")


def parse_periodic_archive_label(period: str, path: Path) -> tuple[str, str]:
    """Return a readable title and sort key for one periodic archive file."""
    stem = path.stem

    if period == "weekly":
        match = re.fullmatch(r"(\d{4})-([a-z]{3})-week-(\d{2})", stem, re.I)
        if match:
            year, month_abbr, week_index = match.groups()
            try:
                month_number = MONTH_ABBR.index(month_abbr.lower())
            except ValueError:
                month_number = 0
            if month_number:
                title = f"{year} 年 {month_number} 月第 {int(week_index)} 周"
                sort_key = f"{year}-{month_number:02d}-{int(week_index):02d}"
                return title, sort_key

    if period == "monthly":
        match = re.fullmatch(r"(\d{4})-([a-z]{3})", stem, re.I)
        if match:
            year, month_abbr = match.groups()
            try:
                month_number = MONTH_ABBR.index(month_abbr.lower())
            except ValueError:
                month_number = 0
            if month_number:
                title = f"{year} 年 {month_number} 月"
                sort_key = f"{year}-{month_number:02d}"
                return title, sort_key

    return stem.replace("-", " "), stem


def collect_periodic_history_rows(period: str) -> list[tuple[str, str, str]]:
    """Collect archived reports and exclude the mutable latest copy."""
    rule = PERIODIC_RULES[period]
    archive_dir = PUBLIC_DIR / str(rule["archive_dir"])
    rows: list[tuple[str, str, str]] = []

    if not archive_dir.exists():
        return rows

    for path in archive_dir.glob("*.html"):
        if path.name == "latest.html":
            continue
        title, sort_key = parse_periodic_archive_label(period, path)
        rel = path.relative_to(PUBLIC_DIR).as_posix()
        rows.append((title, rel, sort_key))

    rows.sort(key=lambda item: item[2], reverse=True)
    return rows


def generate_periodic_history_index(period: str) -> None:
    """Generate a browsable archive page for weekly or monthly reports."""
    rule = PERIODIC_RULES[period]
    rows = collect_periodic_history_rows(period)
    latest_file = str(rule["latest_file"])
    history_file = str(rule["history_file"])
    history_title = str(rule["history_title"])
    history_subtitle = str(rule["history_subtitle"])
    item_label = str(rule["item_label"])

    cards = "\n".join(
        f"""
        <a class="history-card" href="/{html_escape.escape(rel)}">
          <div class="history-main">
            <div class="history-title">{html_escape.escape(title)}</div>
            <div class="history-meta">TrendRadar {html_escape.escape(item_label)}归档</div>
          </div>
          <div class="history-date"><strong>查看</strong></div>
        </a>
        """
        for title, rel, _ in rows
    )

    if not cards:
        cards = f"""
        <div class="empty-card">暂无{html_escape.escape(item_label)}归档</div>
        """

    now_text = get_periodic_now().strftime("%m-%d %H:%M")

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape.escape(history_title)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; margin: 0; padding: 16px; background: #fafafa; color: #333; line-height: 1.5; }}
    .container {{ max-width: 960px; margin: 0 auto; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 16px rgba(0,0,0,.06); }}
    .header {{ position: relative; overflow: hidden; padding: 36px 28px 32px; color: #fff; text-align: center; background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); }}
    .header-watermark {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: clamp(42px, 8vw, 88px); font-weight: 900; letter-spacing: .05em; color: rgba(255,255,255,.14); pointer-events: none; z-index: 1; white-space: nowrap; }}
    .header-actions {{ position: absolute; top: 16px; right: 16px; z-index: 2; display: flex; gap: 8px; }}
    .header-btn {{ display: inline-flex; align-items: center; justify-content: center; min-height: 38px; padding: 10px 18px; border-radius: 6px; border: 1px solid rgba(255,255,255,.3); background: rgba(255,255,255,.2); color: #fff; font-size: 13px; font-weight: 600; text-decoration: none; backdrop-filter: blur(10px); transition: all .2s ease; }}
    .header-btn:hover {{ background: rgba(255,255,255,.3); transform: translateY(-1px); }}
    .header-title {{ position: relative; z-index: 2; margin: 0 0 12px; font-size: 26px; font-weight: 800; }}
    .header-subtitle {{ position: relative; z-index: 2; margin: 0; font-size: 14px; opacity: .9; }}
    .summary {{ display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 16px; padding: 22px 28px; border-bottom: 1px solid #eef0f4; }}
    .summary-item {{ padding: 14px 16px; border-radius: 10px; background: #f8fafc; }}
    .summary-label {{ display: block; margin-bottom: 4px; color: #6b7280; font-size: 12px; }}
    .summary-value {{ color: #111827; font-size: 18px; font-weight: 700; }}
    .content {{ padding: 28px; }}
    .history-list {{ display: grid; gap: 12px; }}
    .history-card {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 18px; border: 1px solid #eef0f4; border-radius: 12px; color: inherit; text-decoration: none; background: #fff; transition: all .18s ease; }}
    .history-card:hover {{ border-color: #c7d2fe; background: #f8faff; box-shadow: 0 8px 24px rgba(79,70,229,.08); transform: translateY(-1px); }}
    .history-title {{ color: #1f2937; font-size: 15px; font-weight: 700; }}
    .history-meta {{ margin-top: 4px; color: #6b7280; font-size: 12px; }}
    .history-date {{ flex-shrink: 0; min-width: 76px; padding-left: 16px; border-left: 1px solid #eef0f4; text-align: right; }}
    .history-date strong {{ color: #4f46e5; font-size: 15px; }}
    .empty-card {{ padding: 24px; border-radius: 12px; background: #f8fafc; color: #6b7280; text-align: center; }}
    .footer {{ padding: 18px 28px 24px; border-top: 1px solid #eef0f4; color: #6b7280; font-size: 13px; text-align: center; background: #f8f9fa; }}
    .footer a {{ color: #4f46e5; text-decoration: none; font-weight: 600; }}
    @media (max-width: 600px) {{
      body {{ padding: 12px; }}
      .header {{ padding: 28px 20px 24px; }}
      .header-actions {{ position: static; justify-content: center; flex-wrap: wrap; margin-bottom: 16px; }}
      .summary {{ grid-template-columns: 1fr; padding: 18px 20px; }}
      .content {{ padding: 20px; }}
      .history-card {{ align-items: flex-start; flex-direction: column; }}
      .history-date {{ width: 100%; min-width: 0; padding: 10px 0 0; border-left: 0; border-top: 1px solid #eef0f4; text-align: left; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="header-watermark">TrendRadar</div>
      <div class="header-actions">
        <a class="header-btn" href="/history.html">返回历史</a>
        <a class="header-btn" href="/{html_escape.escape(latest_file)}">最新{html_escape.escape(item_label)}</a>
      </div>
      <h1 class="header-title">{html_escape.escape(history_title)}</h1>
      <p class="header-subtitle">{html_escape.escape(history_subtitle)}</p>
    </div>
    <div class="summary">
      <div class="summary-item"><span class="summary-label">归档数量</span><span class="summary-value">{len(rows)} 份</span></div>
      <div class="summary-item"><span class="summary-label">更新时间</span><span class="summary-value">{now_text}</span></div>
    </div>
    <div class="content"><div class="history-list">{cards}</div></div>
    <div class="footer">由 <strong>TrendRadar</strong> 生成 · <a href="/history.html">返回历史报告</a></div>
  </div>
</body>
</html>
"""

    (PUBLIC_DIR / history_file).write_text(html, encoding="utf-8")
    print(f"Generated {history_file} with {len(rows)} {period} reports")


def generate_history_index() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    rows = collect_history_rows()

    periodic = read_periodic_config()
    weekly_exists = (PUBLIC_DIR / "weekly.html").exists()
    monthly_exists = (PUBLIC_DIR / "monthly.html").exists()

    action_links = ['<a class="header-btn" href="/#tab-0">返回最新</a>']
    if periodic["weekly_report"] and weekly_exists:
        action_links.append('<a class="header-btn" href="/weekly-history.html">一周总结</a>')
    if periodic["monthly_report"] and monthly_exists:
        action_links.append('<a class="header-btn" href="/monthly-history.html">月度总结</a>')

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

    now_text = get_periodic_now().strftime("%m-%d %H:%M")

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
    # 这样 history.html 可以根据 /weekly.html、/monthly.html 是否存在来显示入口。
    # 周报/月报会同时写入归档目录，例如 public/periodic/weekly/2026-jan-week-01.html。
    generate_periodic_reports_if_enabled()
    generate_periodic_history_index("weekly")
    generate_periodic_history_index("monthly")
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
        generate_periodic_history_index("weekly")
        generate_periodic_history_index("monthly")
        generate_history_index()
    elif args.command == "button":
        add_history_button_to_index_page()
    elif args.command == "prepare":
        prepare_pages()
    elif args.command == "notify":
        send_report_link_to_wecom()


if __name__ == "__main__":
    main()
