---
name: ashare-theme-attribution
description: "A股板块/题材异动的因果归因管线：按 publish_ts 相对交易时段(09:30/15:00)把公告与新闻机械分桶为 pre_session/intraday/post_close/undetermined，只允许 pre_session 作为异动原因，再产出带四分类(事实/推论/观点/待验证)、反方论证、可证伪观察点与基本面判定的研究卡。当用户问「某板块今天为什么大涨」「XX概念暴涨的原因」「跑今天的盘后归因」「异动复盘」「写研究卡」，或需要从题材异动中筛出值得做财报深挖的标的时使用。内含同花顺热点 reason tags 拆词聚合题材轴、巨潮公告 topSearch+hisAnnouncement、东财个股新闻三个零鉴权数据源的调用要点，以及已实测的静默失败陷阱(巨潮 orgId 前缀猜测对 603xxx 返回 HTTP 200 但 0 条、同花顺盘中价格字段为空导致异动数为 0、巨潮日级时间戳精度会把同日因果方向判反、东财需限流与重试、宿主机时区会反转归因结论、数据库相对路径会静默新建空库)。不用于生成买卖信号或短线择时(改用 ashare-short-term-trading)，不用于纯取数(改用 a-stock-data)，不用于财报分析本身(改用 financial-statement-analysis 或 financial-report-minesweeper)。"
---

# A股题材异动归因

## Overview

从「某题材今天为什么大涨」出发，用机械的时间约束排除事后归因谬误，产出一张可回看、可证伪的研究卡，并把值得深挖的标的交接给财报分析 skill。归因的产物是**研究队列**，不是交易信号。

## 何时用 / 何时不用

**用**：板块或题材异动的因果归因、盘后异动复盘、写研究卡、从题材热度中筛出需要做财报深挖的标的。

**不用**：
- 要买卖点、仓位、择时 → `ashare-short-term-trading`
- 只要取数（行情/资金流/龙虎榜/研报） → `a-stock-data`、`westockdata`
- 已经锁定标的要看财务 → `financial-statement-analysis`、`financial-report-minesweeper`
- 交割单复盘 → `ashare-short-term-trading`

## 硬约束

违反任一条，本次归因作废、重写。这些约束存在的原因是：异动发生**之后**去问「为什么涨」，总能找到一个说得通的故事。防御必须是机械的，不能靠自觉。

1. **只有 `pre_session` 桶可作为异动原因。** `post_close` 在物理上只能是结果，严禁当原因；`intraday` 可能是因也可能是媒体追涨报道，须逐条说明；`undetermined` 必须显式标注「先后不可判定」，不得假定。
2. **同花顺 `reason` 题材标签是编辑部人工运营的观点**，是叙事框架而非客观事实。只能进「观点」类并标注来源，不得进「事实」。
3. **时间戳保留原始精度并带 `ts_precision`，且一律按北京时间解释。** 日级精度（`day`）的记录**仅在与异动同日时**不可判先后；跨日的日级记录仍可判定（早于异动日开盘即 `pre_session`）。这一点极易搞反。宿主机时区会直接反转结论，见 T5。
4. **不确定就放「待验证」。** 不得为了让四个分类都非空而虚报确定性。
5. **卡片必须走到基本面判定。** 停在「因为 XX 公告所以涨」等于建了个追涨工具。`fundamental_verdict` 非「噪声」时必须写 `followup_queue`。
6. **不产出仓位、买卖点、目标价、涨跌预测。**

## 前置探测

先确认工具层是否在场，再决定走 CLI 还是手动降级路径。以下探测与平台无关：

```bash
command -v vest || command -v vest.exe \
  || ls .venv/bin/vest 2>/dev/null \
  || ls .venv/Scripts/vest.exe 2>/dev/null
```

四条都落空说明 vest 未安装。**不要猜绝对路径**——本 skill 随仓库分发，仓库可能被 clone 到任何位置。此时有两个选择：

- 若能定位仓库根（含 `pyproject.toml` 与 `src/vest/` 的目录），在其中执行 `uv pip install -e .`（或 `pip install -e .`）后重试
- 否则走下方降级路径

再确认**归因对象**。同花顺热点接口的价格字段在交易日 T 当天不结算：实测 T 日 17:14 仍只返回
`id/name/code/reason/date/market` 六个键，而 T-1 日返回 13 个字段且价格完整。确切结算点在
「T 日 17:14 之后 ~ T+1 日 12:43 之前」之间，尚未测出边界。

**因此归因对象必须是上一个交易日，不是今天。** 若用户问「今天为什么涨」，如实说明当日数据要到
次日才结算，可以先对上一个交易日跑归因。注意用北京时间判断，不要用宿主机本地时间。

**vest 不在场时的降级路径**：按 [references/data-sources.md](references/data-sources.md) 自行取数（三个源全部零鉴权），把证据整理成 `[{"publish_ts": ..., "ts_precision": ...}]` 交给 [scripts/bucket_evidence.py](scripts/bucket_evidence.py) 分桶，再手写卡片 JSON。该脚本仅依赖标准库，Python 3.9+ 即可运行。分桶这一步**不要靠心算**。

## 流程

### 1. 快照：摄取强势股并聚合题材轴

```bash
vest snapshot <上一个交易日>     # 幂等，可重复跑。不要传今天
```

题材轴由同花顺 `reason` tags 按 `+` 拆词聚合得出，**不依赖概念板块成分股接口**，因此无需 APIKey，且对「AI短剧」这类尚未纳入正式概念分类的新题材同样有效。

非交易日返回 `hot_stock: 0`，此时往前再找一个工作日重试，最多回退 7 个自然日；全部为 0 则如实报告无数据，不要编造。

### 2. 异动排行

```bash
vest anomalies --json [--limit 10] [--min-stocks 2] [--min-avg-pct 9.0]
```

冷启动规则：`stock_count >= 2 或 max_pct_chg >= 19.0`，且 `avg_pct_chg >= 9.0`，按家数、均涨排序。

输出里的 `zscore_available` 为 false 时（历史快照不足 20 个交易日），**不得声称做了 z-score 异常检测**，如实说明用的是冷启动阈值。

返回 0 个异动题材时，先排查 T1 与 T6 再下结论说「今天没有异动」——这两者的症状与真的没有异动完全相同。

### 3. 证据包

```bash
vest evidence "<题材名>" [YYYY-MM-DD] [--window 4] [--skip-ingest]
```

默认回溯 4 天，联网补齐成分股的巨潮公告与东财新闻，输出**已按因果时间分桶**的 JSON：`evidence.{pre_session,intraday,post_close,undetermined}`，外加 `editorial_tags`（已标注为观点）与 `constraint` 文本。

题材名必须与 `anomalies` 输出的字符串完全一致，否则报「无异动记录」。

### 4. 写研究卡

逐字段规则见 [references/card-schema.md](references/card-schema.md)，真实范例见 [assets/card-example.json](assets/card-example.json)。必填：

- `classification`：事实 / 推论 / 观点 / 待验证 四类，观点类须带 `source`
- `counter_argument`：写出支持**相反结论**的最强论证，并说明为何仍不采信（或采信）
- `falsifiable_check`：「若判定成立，接下来应观察到 X」，X 必须可在巨潮或行情数据中验证；并写出反向条件
- `fundamental_verdict` ∈ {基本面变化, 情绪资金驱动, 待验证, 噪声}
- `followup_queue`：非「噪声」时必填，每项含 code / name / question / trigger
- `review_at`：异动日后 5 个交易日（约 7 个自然日）

先落盘 `cards/<日期>_<题材>.json`，便于人工检查后再入库。

### 5. 入库、回看、交接

```bash
vest save-card cards/2026-10-14_AI短剧.json    # 或 - 从 stdin 读
vest cards                                      # 列出卡片与回看状态
vest review <card_id> "<实际观察到的结果>"       # review_at 到期时回填
```

`review` 是判断账本的闭环：不回填，卡片就只是当天情绪的快照，无法校准判断质量。回填要诚实记录判定错误的情况，一个全是「判定正确」的账本没有信息量。

`fundamental_verdict` 为「基本面变化」或「待验证」时，把 `followup_queue` 里的标的与具体问题交接给 `financial-statement-analysis`（财务状况与发展方向）或 `financial-report-minesweeper`（排雷），并说明**本次异动归因结论不构成买入理由**——业务是否值钱是独立问题。

注意：判定为「情绪资金驱动」**不等于**标的没有研究价值。本次异动的归因与「这家公司值不值这个价」是两个问题，不要因为一次题材涨停就跳过本该做的基本面功课。

## 数据源与已知陷阱

完整端点、参数、字段映射见 [references/data-sources.md](references/data-sources.md)。六个陷阱的症状、成因、修法与自检命令见 [references/failure-traps.md](references/failure-traps.md)。**它们全都是静默失败**——不报错，只给出看起来正常的空结果或错误结论：

| # | 症状 | 根因 |
|---|---|---|
| T1 | `anomalies` 返回 0 个，快照有几十行但价格全 NULL | 同花顺 getharden 在交易日 T 当天不返回价格字段（实测 T 日 17:14 仍未结算），须改为归因 T-1 |
| T2 | 某股公告 0 条，HTTP 200 无报错 | 巨潮 orgId 用代码前缀猜测；603xxx 全部命中此坑，须改用 topSearch 解析 |
| T3 | 同日公告的因果方向判反 | 巨潮部分公告只给日级精度（北京零点），未标 `ts_precision` |
| T4 | 部分个股新闻语料整只缺失 | 东财接口偶发 read timeout，未重试；且需 0.6s 限流防封 |
| T5 | 同一份数据在不同机器上得出相反结论 | 时间戳按宿主机时区解释；UTC 机器上日级公告被洗白成 `pre_session` |
| T6 | 从项目外运行报「暂无研究卡」/ 0 个异动 | 数据库用相对路径锚定 cwd，静默新建空库 |

已安装的 `a-stock-data` skill 中，巨潮部分同时带 T2 与 T3 两个缺陷（前缀猜 orgId、把毫秒时间戳压成 `%Y-%m-%d`）。**归因管线不要照抄它的巨潮实现**，按本 skill 的 references 走；它的其他层（行情、资金流、龙虎榜、研报）仍可正常复用。

任何取数失败一律显式抛错。**禁止 `except: return []`**——空列表和「确实没有公告」在下游不可区分。

## 验证清单

交付前逐条自查：

- [ ] 卡片里作为原因的每一条证据都在 `pre_session` 桶，且 `publish_ts` 早于异动日 09:30（北京时间）
- [ ] `post_close` 桶的诱惑性解释已被显式排除并写明排除理由（这是最容易出错的一步）
- [ ] `undetermined` 桶若存在，卡片中有对应的不确定性声明
- [ ] 同花顺题材标签只出现在「观点」类且带来源
- [ ] `counter_argument` 是真的反方论证，不是把结论换个说法；且已检查成分股启动前有无股东减持/业绩预警/立案调查
- [ ] `falsifiable_check` 里的观察点可被第三方数据验证，不是「继续关注」；且写了反向条件
- [ ] `fundamental_verdict` 非「噪声」时 `followup_queue` 非空
- [ ] 全文无仓位、买卖点、目标价
- [ ] 未声称使用了尚未就绪的 z-score 检测
- [ ] 回归测试通过：`python3 -m unittest discover -s tests -p "test_*.py"`

## Resources

- [references/data-sources.md](references/data-sources.md) — 三个零鉴权源的端点、参数、字段映射、限流与重试规则，以及未采用的源及原因
- [references/failure-traps.md](references/failure-traps.md) — T1–T6 静默失败的症状、成因、修法、自检命令
- [references/card-schema.md](references/card-schema.md) — 研究卡逐字段规则、四分类纪律、回看与交接
- [scripts/bucket_evidence.py](scripts/bucket_evidence.py) — 仅依赖标准库（Python 3.9+）的时间分桶器；vest 不在场时用它机械执行因果约束
- [assets/card-example.json](assets/card-example.json) — 2026-10-14「AI短剧」真实研究卡，示范如何用时间分桶排除收盘后利好
