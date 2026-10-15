# 数据源

三个源全部零鉴权、无需 APIKey，实测可用（2026-10-15）。vest 的 `sources.py` 已封装，本文供降级路径手写调用时参考。

通用请求头：

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36
```

---

## 1. 同花顺热点 — 当日强势股 + 题材归因

题材轴的唯一来源。`reason` 是同花顺编辑部人工运营的题材标签，形如 `冰雪工程+数据中心液冷+储能温控+冷热设备`。

```
GET http://zx.10jqka.com.cn/event/api/getharden/date/{YYYY-MM-DD}/orderby/date/orderway/desc/charset/GBK/
```

响应为 GBK 编码 JSON，顶层 `errocode` 非 0 时看 `errormsg`。`data` 为数组。

| 原字段 | 含义 | 备注 |
|---|---|---|
| `code` | 6 位代码 | 需 zfill(6)，上游可能丢前导零 |
| `name` | 简称 | |
| **`reason`** | **题材归因标签** | `+` 分隔，拆词后按词频聚合即得题材轴 |
| `zhangfu` | 涨幅 % | **盘中为空，见 T1** |
| `close` | 收盘价 | 同上 |
| `huanshou` | 换手率 % | 同上 |
| `chengjiaoe` | 成交额 | 同上 |
| `ddejingliang` | 大单净量 | 同上 |
| `market` | 市场代码 | 17=沪主板 33=深主板 等 |

**为什么用 reason 拆词而不是概念板块成分股接口**：概念板块接口要么需要 APIKey（westock），要么只有约 100 个申万行业（东财 push2），且正式概念分类对新题材滞后——「AI短剧」这类刚出现的题材往往还没被收录。reason 拆词由当日真实涨停股反推题材，天然覆盖新题材。

**限流**：无需。实测 73ms 返回。

---

## 2. 巨潮公告 — 最高价值证据源

公告是唯一能验证「协议/订单/政策/业绩」类基本面事件的来源，且带可下载 PDF 全文。

### 2.1 先解析 orgId（**不可用代码前缀猜测，见 T2**）

```
POST http://www.cninfo.com.cn/new/information/topSearch/query
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest

keyWord={code}&maxSecNum=10&maxListNum=5
```

返回数组，取 `code` 完全匹配项的 `orgId`。**格式不统一**：

| 代码 | orgId | 格式 |
|---|---|---|
| 600519 | `gssh0600519` | 前缀式 |
| 000530 | `gssz0000530` | 前缀式 |
| **603533** | **`9900033063`** | **纯数字** |
| **603598** | **`9900024134`** | **纯数字** |

解析结果应缓存，避免重复请求。

### 2.2 检索公告

```
POST https://www.cninfo.com.cn/new/hisAnnouncement/query
Content-Type: application/x-www-form-urlencoded
Referer: https://www.cninfo.com.cn/new/disclosure
Origin: https://www.cninfo.com.cn

stock={code},{orgId}&tabName=fulltext&pageSize=30&pageNum=1&column=&category=&plate=&seDate=&searchkey=&secid=&sortName=&sortType=&isHLtitle=true
```

| 原字段 | 含义 | 备注 |
|---|---|---|
| `announcementTitle` | 标题 | 可能含 `<em>` 高亮标签，需剥离 |
| **`announcementTime`** | **Unix 毫秒** | **必须按北京时间解释，见 T3** |
| `announcementId` | 公告 ID | 与 code 联合做主键 |
| `adjunctUrl` | 相对路径 | PDF 全文 = `https://static.cninfo.com.cn/{adjunctUrl}` |
| `announcementTypeName` | 类型名 | **已返回 null**，不要依赖 |
| `announcementType` | 类型代码 | 形如 `01010503\|\|010112\|\|012103`，无公开映射表 |
| `secName` | 证券简称 | |

类型名不可用时，直接用标题文本判断类型即可，不要去逆推类型代码。

**限流**：无需，但建议失败重试（同 T4 的网络抖动）。

---

## 3. 东财个股新闻 — 秒级时间戳

唯一提供秒级发布时间的源，因此是判定盘中/盘后的主力证据。JSONP 接口。

```
GET https://search-api-web.eastmoney.com/search/jsonp?cb=jQuery_news&param={URL编码的JSON}
Referer: https://so.eastmoney.com/
```

`param` 的 JSON 结构：

```json
{"uid":"","keyword":"603533","type":["cmsArticleWebOld"],"client":"web",
 "clientType":"web","clientVersion":"curr",
 "param":{"cmsArticleWebOld":{"searchScope":"default","sort":"default",
          "pageIndex":1,"pageSize":20,"preTag":"","postTag":""}}}
```

剥掉 JSONP 外壳（首 `(` 到末 `)` 之间）后解析。文章列表在 `result.cmsArticleWebOld`，**直接就是数组**，不是 `{list: [...]}` 嵌套。

| 原字段 | 含义 |
|---|---|
| `title` | 标题，含 `<em>` 高亮标签需剥离 |
| `content` | 摘要，同样需剥离标签 |
| `date` | **`YYYY-MM-DD HH:MM:SS`，秒级，北京时间** |
| `mediaName` | 媒体名 |
| `url` | 原文链接，可做主键 |

**限流**：必须。相邻请求间隔 ≥0.6s，否则易被封。**且必须重试**，见 T4。

---

## 未采用的源

| 源 | 为什么不用 |
|---|---|
| `westock` CLI | 需专属 APIKey 鉴权，且概念板块不支持北向持股数据 |
| 财联社 `cls.cn` | 2026-05 起旧接口全面 404，新版需签名认证 |
| 东财 push2 北向资金 | 2024-08 后净买额字段上游断供，返回 NaN/0 |
| 东财概念板块 | 只有约 100 个申万行业，不是题材粒度 |
