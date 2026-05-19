<div align="center" id="trendradar">

<a href="https://github.com/sansan0/TrendRadar" title="TrendRadar">
  <img src="/_image/banner.webp" alt="TrendRadar Banner" width="80%">
</a>

最快<strong>30秒</strong>部署的热点助手 —— 告别无效刷屏，只��真正关心的新闻资讯

</div>

## 我们做了什么增强（相对 TrendRadar 原版）

本仓库基于 TrendRadar 二次封装，主要增强点集中在 **存储、部署形态、历史/周期页面**：

- **存储到 Cloudflare R2（S3 兼容）**：将 HTML 历史归档持久化到 R2，避免每次工作流运行都丢失历史文件。
- **部署到 Cloudflare Pages**：工作流产物直接部署到 Pages，提供更稳定的访问与更灵活的自定义域名能力。
- **历史页面（history.html）**：自动汇总历史报告入口，支持一键回看每日多次运行生成的 HTML 报告。
- **周报 / 月报（weekly.html / monthly.html）**：基于最近一段时间的历史报告，调用 AI 生成周期总结，并自动归档。
- **网页版报告链接推送到企微（企业微信）**：部署完成后，将“最新报告链接 + 历史页链接（若存在）”通过企业微信机器人推送，便于在群里直接打开网页查看。

> 上述能力主要由新增脚本 `scripts/deploy_pages.py` 与修改后的工作流 `.github/workflows/crawler.yml` 提供。

---

## 部署与工作流（新增）

### 1) 工作流整体流程（crawler.yml）

在原有「抓取 + 生成报告」的基础上，我们额外做了几件事：

1. **从 R2 恢复历史 HTML** 到 `public/`（用于保留历史报告、周报/月报归档）。
2. **合并本次生成的 TrendRadar HTML** 到 `public/`（把最新产物覆盖/追加进历史目录）。
3. **运行 `scripts/deploy_pages.py prepare`**：
   - 清理过旧的日目录（按保留天数）
   - 生成 `history.html`
   - 生成周报/月报（若启用且到达计划日）
   - 给首页 `public/index.html` 注入“历史”按钮
4. **把 `public/` 再次同步回 R2**，然后 **部署到 Cloudflare Pages**。
5. **推送网页版报告链接到企业微信**：运行 `scripts/deploy_pages.py notify`，把链接发送到企微机器人。

### 2) 需要的 Secrets（与 R2/Pages/企微相关）

- R2（S3 兼容）
  - `S3_BUCKET_NAME`
  - `S3_ACCESS_KEY_ID`
  - `S3_SECRET_ACCESS_KEY`
  - `S3_ENDPOINT_URL`
  - `S3_REGION`（可选，R2 通常可用 `auto`）

- Cloudflare Pages
  - `CLOUDFLARE_API_TOKEN`
  - `CLOUDFLARE_ACCOUNT_ID`
  - `CLOUDFLARE_PAGE_NAME`

- 企业微信（可选，用于推送网页链接）
  - `WEWORK_WEBHOOK_URL`
  - `WEWORK_MSG_TYPE`（`markdown` 或 `text`）
  - `REPORT_URL`
    - 支持用换行 / 逗号 / 分号分隔多个 URL
    - 第 1 个 URL 视为“最新报告”链接
    - 第 2 个 URL（可选）视为“历史页面”链接（且需 `public/history.html` 存在才会发送）

---

## 历史页面（新增）

`scripts/deploy_pages.py` 会在部署前自动生成：

- `public/history.html`：列出 `public/YYYY-MM-DD/*.html` 目录下的历史报告卡片。
- 同时会尝试给 `public/index.html` 注入一个「历史」按钮，方便从最新报告跳转回历史列表。

历史报告的组织方式是：

- 每次运行生成的 HTML 会归档到 `public/YYYY-MM-DD/HH-MM.html`（示例路径）
- `history.html` 自动扫描并生成入口

---

## 周报 / 月报（新增）

### 生成逻辑

`scripts/deploy_pages.py` 支持周期报告：

- **周报**：`public/weekly.html`
- **月报**：`public/monthly.html`

并且会自动归档到：

- `public/periodic/weekly/<yyyy>-<mon>-week-<nn>.html`
- `public/periodic/monthly/<yyyy>-<mon>.html`

为了避免同一周期重复花费 token，脚本会在 R2 restore 后检查 **当期归档是否已存在**，存在则跳过 AI 生成。

### 配置方式（config/config.yaml 第 12 节）

在 `config/config.yaml` 的 `periodic:` 段配置：

- `weekly_report`：
  - `false`：关闭
  - `true`：默认周一生成
  - `1-7`：周一到周日
- `monthly_report`：
  - `false`：关闭
  - `true`：默认每月 1 号生成
  - `1-31`：每月指定日期生成（若当月不足该日期则取当月最后一天）
- `ai_analysis_level`：周期总结的分析深度（`simple` / `medium` / `deep`）

> 注意：周期报告依赖 AI 能力（通过 LiteLLM 调用），需要正确配置 `AI_API_KEY` / `AI_MODEL`（以及可选的 `AI_API_BASE`）。

---

## 目录结构（与部署相关）

- `public/`：Cloudflare Pages 部署目录
  - `index.html`：最新报告入口
  - `history.html`：历史列表页
  - `YYYY-MM-DD/`：每日历史报告目录
  - `weekly.html` / `monthly.html`：周期报告固定入口
  - `periodic/weekly/`、`periodic/monthly/`：周期报告归档
- `scripts/deploy_pages.py`：Pages 部署前处理脚本（清理、历史页、周期报告、按钮注入、通知）

---

## 关于 TrendRadar 原版

本仓库为 TrendRadar 的导入与二次定制版本，核心抓取/分析能力仍来自 TrendRadar。若你需要对比原版功能或追踪上游更新，请参考原项目：

- TrendRadar：<https://github.com/sansan0/TrendRadar>
