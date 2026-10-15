# vest — A股题材异动归因管线

从「某题材今天为什么大涨」出发，用**机械的时间约束**排除事后归因谬误，产出一张可回看、可证伪的研究卡，并把值得深挖的标的交接给财报分析。

归因的产物是**研究队列**，不是交易信号。本工具不生成买卖点、仓位或目标价。

---

## 为什么需要它

板块暴涨之后去问「为什么涨」，你**总能**找到一个说得通的故事。这是叙事谬误的教科书场景，也是绝大多数「异动归因」工具的实际产出。

防御不能靠提示词里的「请客观分析」，必须靠机械约束。A股异动发生在交易日 `D` 的盘中（09:30–15:00），于是每条证据都可以按发布时间被硬性归类：

| 分桶 | 条件 | 能否作为原因 |
|---|---|---|
| `pre_session` | `D` 之前，或 `D` 当日 09:30 前 | ✅ 可以 |
| `intraday` | `D` 当日 09:30–15:00 | ⚠️ 可能是因，也可能是媒体追涨报道，须逐条说明 |
| `post_close` | `D` 当日 15:00 后，或 `D` 之后 | ❌ 物理上只能是结果 |
| `undetermined` | 仅日级精度且与 `D` 同日 | ❓ 先后不可判定，必须显式声明 |

### 一个真实例子

2026-10-14「AI短剧」题材，掌阅科技(603533) +10.0%、引力传媒(603598) +9.99%。

收盘后 18:28:59 有一条极具解释力的新闻：**「对标红果短剧！腾讯上线绿泡泡短剧，社交属性重构分发逻辑，或利好上游制作方」**。

任何朴素的归因都会抓这条当答案。但它发布于收盘后——**在物理上不可能是当日涨幅的原因**。本管线把它关进 `post_close` 桶，转而去看 `pre_session`：那里有 10-12 的「连续2个交易日涨幅偏离值累计超20%」、10-13 的异常波动公告、10-09 的板块领涨报道，以及**一条都没有的基本面催化事件**（无协议、无订单、无政策）。

结论：这是资金接力的延续段，不是新事件驱动。叠加掌阅科技 9-25 的股东减持计划公告，判定为 `情绪资金驱动`——但「AI短剧业务本身值不值钱」作为独立问题进入财报深挖队列。

完整卡片见 [`skill/ashare-theme-attribution/assets/card-example.json`](skill/ashare-theme-attribution/assets/card-example.json)。

---

## 架构：工具层与 LLM 层分离

本仓库只是**工具层**。它负责取数、时间约束、持久化，不做任何主观判断。

**LLM 层由你的 agent 承担**（Qoder、Claude、或任何能执行命令的 agent）。`vest evidence` 输出结构化证据包 JSON，agent 读完后写研究卡，再用 `vest save-card` 入库。

这样设计的原因：仓库里不内嵌任何 LLM API 调用，因此没有 key 管理、没有模型绑定、没有额外的网络依赖。

```
你的 agent ──读取──> vest evidence (JSON 证据包，已按因果时间分桶)
    │
    ├──> 四分类：事实 / 推论 / 观点 / 待验证
    ├──> 反方论证 + 可证伪观察点
    └──> 基本面判定 ──> vest save-card ──> DuckDB
                              │
                              └──> followup_queue 交接给财报分析
```

---

## 安装

需要 Python ≥ 3.10，以及能访问中国大陆财经站点的网络。

### 用 uv（推荐，跨平台，自动管理 Python）

```bash
git clone <repo-url> && cd <repo-name>
uv venv
uv pip install -e .
```

### 用标准 venv + pip

```bash
python3 -m venv .venv

# Linux / macOS
.venv/bin/pip install -e .

# Windows
.venv\Scripts\pip install -e .
```

依赖只有三个：`requests`、`duckdb`、`tzdata`（Windows 无系统时区库，必须显式带上）。

要把本工具作为 **MCP 服务**挂载给别的 agent，额外装一个可选依赖：

```bash
uv pip install -e ".[mcp]"       # 或 .venv/bin/pip install -e ".[mcp]"
```

`mcp` 必须 `>=2,<3`：2.x 把 `FastMCP` 改名为 `MCPServer`，网上大量教程给的是 1.x 的
`from mcp.server.fastmcp import FastMCP`，在 2.x 下直接 `ModuleNotFoundError`。

---

## 快速开始

```bash
# 1. 摄取某个交易日的强势股快照并聚合题材热度
#    ⚠️ 只能归因「上一个交易日」：同花顺的价格字段在 T 日 17:14 仍未结算，
#    T+1 日 12:43 才实测可用。跑当日会被守卫拒绝写入，这是刻意设计。
vest snapshot 2026-10-14

# 2. 看异动题材排行
vest anomalies 2026-10-14

# 3. 对某个题材出证据包（自动联网补齐成分股公告与新闻）
vest evidence "AI短剧" 2026-10-14

# 4. 由 agent 写研究卡后入库
vest save-card cards/2026-10-14_AI短剧.json

# 5. 查看研究卡与回看状态
vest cards

# 6. review_at 到期后回填实际验证结果
vest review 2026-10-14_AI短剧 "10-15 高开后回落，换手维持高位，判定成立"
```

Windows 下把 `vest` 换成 `.venv\Scripts\vest`（或激活 venv 后直接用 `vest`）。

第 6 步是**判断账本的闭环**。不回填，研究卡就只是当天情绪的快照，永远无法校准判断质量。

---

## 命令参考

| 命令 | 说明 |
|---|---|
| `vest snapshot [日期]` | 摄取强势股快照 + 题材词频聚合。幂等，可重复跑。默认今日（北京时间） |
| `vest anomalies [日期]` | 异动题材排行。`--json` 输出结构化结果，`--min-stocks` / `--min-avg-pct` / `--limit` 调阈值 |
| `vest evidence <题材> [日期]` | 时间因果分桶的证据包 JSON。`--window N` 回溯天数（默认 4），`--skip-ingest` 只用已入库语料 |
| `vest save-card [文件]` | 从 JSON 文件或 stdin 写入研究卡 |
| `vest cards` | 列出研究卡与回看状态 |
| `vest review <card_id> <结论>` | 回填实际验证结果 |

题材轴由同花顺 `reason` 标签按 `+` 拆词聚合得出，**不依赖概念板块成分股接口**——因此无需 APIKey，且对「AI短剧」这类尚未被纳入正式概念分类的新题材同样有效。

数据库位置：源码/可编辑安装时为仓库内 `data/vest.duckdb`；常规安装时为 `~/.vest/vest.duckdb`。可用环境变量 `VEST_DB` 覆盖。

---

## 在其他 agent 中使用

本工具对第三方 agent 有两条接入路径，可并存。

### 1. MCP 服务（推荐，无需 shell）

`vest-mcp` 是一个标准 MCP stdio server，暴露 7 个工具：`snapshot`、`anomalies`、
`evidence`、`classify_timing`、`save_card`、`list_cards`、`review_card`。

**任何支持 MCP 的客户端都能挂载**——Claude Desktop、Claude Code、Cursor、Windsurf、Zed、
自建 agent 等。已用真实 MCP 客户端经 stdio 完成 `initialize` + `list_tools` + `call_tool`
全流程验证（见 `tests/test_mcp_server.py`）。

关键点：**MCP 的 `instructions` 字段会携带整套因果纪律**（六个分桶规则、编辑部标签属观点
不属事实、必须写反方论证与可证伪观察点等）。移植的不只是函数，还有那套防事后归因的约束
——只拿到函数而不知道纪律的 agent，照样会把收盘后的利好当成上涨原因。

> **GUI 客户端必须用绝对路径。** 从 Dock/Finder 启动的 Claude Desktop 不继承你 shell 的
> PATH，`uv run ...` 这类写法对它无效。下面示例统一用绝对路径。

**Claude Desktop** — `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "vest": {
      "command": "/绝对路径/到仓库/.venv/bin/vest-mcp"
    }
  }
}
```

**Claude Code**：

```bash
claude mcp add vest -- /绝对路径/到仓库/.venv/bin/vest-mcp
```

**Cursor** — `.cursor/mcp.json`，配置块与 Claude Desktop 相同。

**通用 MCP 客户端** — 三种已验证的启动形式任选：

| 形式 | 命令 |
|---|---|
| 绝对路径（最稳） | `<repo>/.venv/bin/vest-mcp` |
| uv（需客户端进程能找到 uv） | `uv run --directory <repo> vest-mcp` |
| 模块 | `<repo>/.venv/bin/python -m vest.mcp_server` |

Windows 下把 `.venv/bin/` 换成 `.venv\Scripts\`。

### 2. Agent Skill

`skill/ashare-theme-attribution/` 遵循 **Anthropic Agent Skills 规范**（SKILL.md +
`name`/`description` frontmatter + 渐进披露的 references 子文件），因此可直接用于
Claude Code 与 Claude.ai；在 Qoder 中同样是原生 skill 格式。

```bash
# Claude Code / Claude.ai
cp -R skill/ashare-theme-attribution ~/.claude/skills/

# Qoder
cp -R skill/ashare-theme-attribution ~/.qoder/skills/
```

装好后对 agent 说「跑昨天的盘后归因」或「XX 概念为什么暴涨」即可触发。

skill 内含数据源端点与字段映射、研究卡逐字段规则、六个已实测的静默失败陷阱及自检命令。
即使 `vest` 完全没装，skill 里的 `scripts/bucket_evidence.py` 也能独立运行（仅标准库，
Python 3.9+），保证时间分桶这一步不会被心算替代。

### 3. 纯 CLI

任何有 shell 的 agent 都能直接用 `vest` 命令，无需 MCP 也无需 skill。
仓库根的 [`AGENTS.md`](AGENTS.md) 是写给这类 agent 的说明，格式与内容不绑定任何厂商。

---

## 数据源

三个源全部零鉴权、无需 APIKey：

| 源 | 提供 | 时间精度 |
|---|---|---|
| 同花顺热点 | 当日强势股 + 人工运营的题材归因标签 | 日级 |
| 巨潮 cninfo | 上市公司公告全文 + PDF | 混合（部分仅日级） |
| 东方财富 | 个股新闻 | **秒级** |

详见 [`skill/ashare-theme-attribution/references/data-sources.md`](skill/ashare-theme-attribution/references/data-sources.md)。

**合规说明**：本项目通过公开 HTTP 接口获取公开披露信息，请求间隔限流，仅用于个人研究。数据版权归原作者与交易所所有。使用即表示你自行判断并承担对目标站点服务条款的合规责任。

---

## 已知局限

- **数据有 T+1 延迟。** 同花顺热点接口的价格字段要到交易日 T 的 17:14 之后、T+1 的 12:43 之前的某个时点才结算（实测，确切边界未知）。因此**只能归因上一个交易日**，无法当日出结论。`snapshot` 对未结算日会抛错拒绝写入 NULL 价格——若放宽，`anomalies` 会静默返回 0 个异动，症状与「当天真没异动」完全相同。
- **仅支持 A 股。** 09:30/15:00 交易时段与北京时间假设是硬编码的。
- **冷启动阶段无 z-score。** 异动检测目前用固定阈值规则，需积累 ≥20 个交易日快照后才启用与自身历史对比的分支。`anomalies` 输出里的 `zscore_available` 会如实标注当前状态。
- **归因质量取决于 LLM 层。** 时间分桶是确定性的，但四分类、反方论证、基本面判定都是 agent 的判断，不是可复现的算法输出。研究卡的 `review_at` 回看机制是用来校准它的，不是用来替代它的。
- **证据集不可精确复现。** 东财新闻接口返回的是滚动 top-N，随时间漂移：实测同一题材在 46 分钟内就有一条文章老化出窗、一条新文章进窗（分桶逻辑本身完全确定，43 条共有证据无一换桶）。因此**研究卡内的 `evidence` 字段才是权威记录**，事后重跑 `vest evidence` 得到的证据集会与当初不同。做回看时应以卡片记录为准，重跑仅用于补充新信息。
- **`undetermined` 桶可能很大。** 巨潮对多数公告只给日级精度，因此异动当日的公告往往无法判定先后。这是数据的固有限制，不是可以绕过的工程问题。
- **上游接口随时可能变更。** 已知陷阱与自检命令见 [`failure-traps.md`](skill/ashare-theme-attribution/references/failure-traps.md)。

---

## 项目结构

```
├── AGENTS.md          # 写给任意 agent 的使用说明（不绑定厂商）
├── src/vest/
│   ├── sources.py     # 三个零鉴权数据源适配器（含 orgId 解析、限流、重试）
│   ├── store.py       # DuckDB schema 与幂等 upsert
│   ├── snapshot.py    # 每日快照摄取 + 题材词频聚合
│   ├── attribute.py   # 时间因果分桶、异动检测、证据召回、研究卡
│   ├── cli.py         # 命令行入口
│   └── mcp_server.py  # MCP stdio server（可选 [mcp] extra）
├── tests/
│   ├── test_timing.py    # 分桶边界与跨时区不变性回归测试
│   └── test_mcp_server.py# 工具注册 + 真实 stdio 握手
├── cards/             # 研究卡 JSON
├── data/              # DuckDB（不进版本库）
└── skill/
    └── ashare-theme-attribution/
        ├── SKILL.md
        ├── references/    # 数据源、失败陷阱、卡片字段规则
        ├── scripts/       # 独立分桶器（仅标准库）
        └── assets/        # 真实研究卡示例
```

## 测试

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

13 项。其中 4 项需要可选 `mcp` extra，未安装时自动跳过；包含一次真实 MCP stdio 握手，
因此 `initialize` / `list_tools` / `call_tool` 是被端到端验证的，不是声称可用。

其中 `TestTimezoneInvariance` 跨 4 个宿主机时区断言分桶结果一致——这曾是一个真实缺陷：按宿主机时区解释 Unix 毫秒，会让 UTC 机器上的日级精度公告被误标为秒级，从而把「先后不可判定」洗白成「可作为原因」。

---

## 免责声明

本项目是个人研究工具，不构成任何投资建议。所有输出均为对公开信息的整理与推断，可能存在错误。据此做出的任何投资决策由使用者自行承担。

## License

MIT
