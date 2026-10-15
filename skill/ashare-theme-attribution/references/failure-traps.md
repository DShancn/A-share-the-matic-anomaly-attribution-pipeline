# 静默失败陷阱

**这六个陷阱全都不报错。** 它们返回 HTTP 200、返回空列表、或返回看起来完全正常的错误结论。归因管线的输出会直接影响投资判断，所以宁可显式抛错也不要静默降级。

任何取数失败一律显式抛错。**禁止 `except: return []`**——空列表和「确实没有公告」在下游不可区分。

---

## T1 · 同花顺盘中不返回价格字段

| | |
|---|---|
| **症状** | `anomalies` 返回 0 个异动题材，但 `snapshot` 报告摄取了几十只强势股 |
| **根因** | getharden 接口盘中只返回 `id/name/code/reason/date/market`，价格字段（`zhangfu`/`close`/`huanshou`/`chengjiaoe`）要等收盘结算后才填充。异动检测的过滤条件 `avg_pct_chg >= 9.0` 对 NULL 恒为假，于是全部题材被过滤掉 |
| **修法** | `snapshot` 在写入前检查价格字段缺失比例，全缺失时抛错拒写；部分缺失时向 stderr 告警。**不要写入 NULL 价格**——一旦写进去，后续 `anomalies` 会安静地返回 0，没人知道是数据问题还是当天真没异动 |
| **调度** | 15:30 之后跑。收盘后具体几点结算未实测确认，建议带 3 次 20 分钟间隔重试 |

自检：

```bash
python3 -c "
import duckdb
c = duckdb.connect('data/vest.duckdb')
print(c.execute('''SELECT trade_date, count(*), count(pct_chg) AS has_price
                   FROM hot_stock GROUP BY 1 ORDER BY 1 DESC LIMIT 5''').fetchall())
"
```

`count(*)` 与 `has_price` 不相等即中招。

---

## T2 · 巨潮 orgId 前缀猜测对 603xxx 全部失效

| | |
|---|---|
| **症状** | 某只股票公告 0 条，HTTP 200，`totalAnnouncement: 0`，无任何报错 |
| **根因** | orgId 格式**不统一**。`600519→gssh0600519`、`000530→gssz0000530` 是前缀式，但 `603533→9900033063`、`603598→9900024134` 是纯数字式。按代码前缀拼 orgId 对所有 603xxx 股票静默返回空 |
| **影响面** | 603xxx 是上交所主板的一大段号，命中率极高。掌阅科技(603533) 就中招——它是 AI短剧 题材的领涨股，公告全丢会让归因失去最重要的证据源 |
| **修法** | 用 `topSearch/query` 接口解析真实 orgId 并缓存，前缀拼接只作为解析失败时的兜底 |

自检：

```bash
curl -s -X POST "http://www.cninfo.com.cn/new/information/topSearch/query" \
  -H "Content-Type: application/x-www-form-urlencoded" -H "X-Requested-With: XMLHttpRequest" \
  -d "keyWord=603533&maxSecNum=10&maxListNum=5"
```

返回的 `orgId` 若为纯数字，说明前缀猜测必然失败。

> 已安装的 `a-stock-data` skill 的巨潮实现同时带 T2 与 T3 两个缺陷，**不要照抄**。它的其他层（行情、资金流、龙虎榜、研报）仍可正常复用。

---

## T3 · 巨潮日级时间戳把同日因果方向判反

| | |
|---|---|
| **症状** | 异动当日发布的公告被当成异动原因，或反过来把原因当成结果 |
| **根因** | 巨潮对多数公告只给日级精度，`announcementTime` 表现为北京时间当地零点。若把它压成 `%Y-%m-%d` 就彻底丢失了「这条记录其实不知道具体几点」这个信息；若拿零点去和 09:30/15:00 比较，会得出「当日 00:00 早于开盘，所以是原因」的伪结论 |
| **修法** | 保留原始毫秒并附 `ts_precision ∈ {second, minute, day, unknown}`。日级精度**仅在与异动同日时**判为 `undetermined`；跨日的日级记录仍可判定（早于异动日开盘即 `pre_session`）。这一点极易搞反 |
| **纪律** | `undetermined` 桶的证据在研究卡里必须显式声明「先后不可判定」，不得为了叙事完整而假定顺序 |

自检：见 T5，两者共用同一段解析代码。

---

## T4 · 东财偶发超时导致整只股票语料缺失

| | |
|---|---|
| **症状** | 证据包里某只成分股一条新闻都没有，但同题材其他股票正常 |
| **根因** | `search-api-web.eastmoney.com` 偶发 read timeout。若批量摄取时对单只股票的异常做吞掉处理，该股语料就整体缺失，且缺失与「确实没有相关新闻」不可区分 |
| **修法** | 相邻请求间隔 ≥0.6s 限流；`Timeout`/`ConnectionError` 重试 3 次、退避递增；单只最终失败时向 stderr 明确报出代码，不静默跳过 |

自检：

```bash
python3 -c "
import duckdb
c = duckdb.connect('data/vest.duckdb')
print(c.execute('''SELECT code, count(*) FROM news GROUP BY 1 ORDER BY 2''').fetchall())
"
```

对照异动成分股清单，缺哪只一目了然。

---

## T5 · 宿主机时区决定归因结论

| | |
|---|---|
| **症状** | 同一份数据在不同机器上得出**相反**的因果结论，两边都不报错 |
| **根因** | `datetime.fromtimestamp(ms/1000)` 不传 `tz` 时按**宿主机本地时区**解释 Unix 毫秒。A股全部时间语义（开盘 09:30、收盘 15:00、公告日切）都是北京时间。在 UTC 机器上，北京零点的日级公告被解析成前一天 16:00，且精度从 `day` 误标为 `second` |
| **后果** | `undetermined` 被洗白成 `pre_session`——一条「先后不可判定」的证据变成了「确认的原因」。整套防事后归因机制静默失效 |

实测对照（同一枚 Unix 毫秒 `1791907200000`，即北京时间 2026-10-14 00:00:00）：

| 宿主机 TZ | 解析结果 | ts_precision | 分桶 |
|---|---|---|---|
| Asia/Shanghai | 2026-10-14 00:00:00 | `day` | `undetermined` ✅ |
| UTC | 2026-10-13 16:00:00 | `second` | `pre_session` ❌ |

**修法**：所有时间戳解析与比较显式使用 `zoneinfo.ZoneInfo("Asia/Shanghai")`。`date.today()` 同理——海外机器上会差一天，须改用 `datetime.now(CN_TZ).date()`。Windows 无系统时区库，依赖里必须带 `tzdata`。

自检（回归测试已覆盖，跨 4 个时区断言结果一致）：

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v -k Timezone
```

---

## T6 · 数据库路径依赖 cwd，换目录静默新建空库

| | |
|---|---|
| **症状** | 从项目外目录运行 `vest cards` 报「暂无研究卡」，`vest anomalies` 报 0 个异动——但数据明明都在 |
| **根因** | 默认库路径写成相对路径 `data/vest.duckdb`，锚定的是**当前工作目录**。从别处调用时会在该目录下新建一个只有 schema 的空库，原库安然无恙，没有任何报错 |
| **为什么危险** | 症状和「今天真的没有异动」完全一样。定时任务若因工作目录变化而写到别处，会连续多天产出「无异动」的空报告 |
| **修法** | 按包位置锚定：可编辑安装/源码检出时定位到仓库根的 `data/`；常规安装到 site-packages 时改用 `~/.vest/`（不能往包目录里写）。始终支持 `VEST_DB` 环境变量覆盖。向上查找 cwd 祖先目录**不管用**——项目外调用时同样落空 |

自检：

```bash
cd /tmp && vest cards    # 应当能看到项目里的研究卡，而不是「暂无研究卡」
```
