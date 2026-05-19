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


PUBLIC_DIR = Path("public")
DEPLOY_CONFIG = Path("config/deploy.yaml")


def read_retention_days(default_days: int = 30) -> int:
    if not DEPLOY_CONFIG.exists():
        print("config/deploy.yaml not found, use default 30 days")
        return default_days

    try:
        for line in DEPLOY_CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("retention_days:"):
                value = line.split(":", 1)[1].strip()
                return int(value)
    except Exception as exc:
        print(f"Failed to read config/deploy.yaml, use default {default_days} days: {exc}")
        return default_days

    print(f"retention_days not found, use default {default_days} days")
    return default_days


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
        # 不动 public/index.html、public/latest、public/history.html
        if not date_pattern.match(name):
            continue

        folder_date = datetime.strptime(name, "%Y-%m-%d").date()
        if folder_date < cutoff:
            print(f"Deleting old html folder: {path}")
            shutil.rmtree(path)
            deleted += 1

    print(f"HTML retention days: {retention_days}")
    print(f"Deleted old html folders: {deleted}")


def collect_history_rows():
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


def generate_history_index() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    rows = collect_history_rows()

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
        <a class="header-btn" href="/#tab-0">返回最新</a>
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
    generate_history_index()
    add_history_button_to_index_page()


def main() -> None:
    parser = argparse.ArgumentParser(description="TrendRadar Pages deployment helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("cleanup", help="Cleanup old local HTML folders")
    subparsers.add_parser("history", help="Generate public/history.html")
    subparsers.add_parser("button", help="Add history button to public/index.html")
    subparsers.add_parser("prepare", help="Run cleanup, generate history, and add history button")
    subparsers.add_parser("notify", help="Send report links to WeCom")

    args = parser.parse_args()

    if args.command == "cleanup":
        cleanup_old_local_html()
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
