"""零鉴权数据源适配器。

三个源都经实测可用（2026-10-15）：同花顺热点、巨潮公告、东财个股新闻。
时间戳一律保留原始精度并显式标注 ts_precision，供归因管线做
publish_ts < 异动时刻 的因果方向校验。
"""

from __future__ import annotations

import json
import re
import time
from datetime import date as _date
from datetime import datetime

import requests

from . import CN_TZ

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "Chrome/117.0.0.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")

# 东财接口需要限流防封，同花顺/巨潮无此要求
_EM_MIN_INTERVAL = 0.6
_em_last_call = 0.0


def _em_get(url: str, **kwargs) -> requests.Response:
    global _em_last_call
    wait = _EM_MIN_INTERVAL - (time.monotonic() - _em_last_call)
    if wait > 0:
        time.sleep(wait)
    _em_last_call = time.monotonic()
    kwargs.setdefault("timeout", 15)
    return requests.get(url, **kwargs)


def _retry(fn, attempts: int = 3, delay: float = 1.0):
    """网络调用重试。上游偶发 read timeout，不重试会静默丢失整只股票的语料。"""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    raise last


def _from_ms(ms) -> tuple[str, str]:
    """Unix 毫秒 -> (北京时间字符串, 精度)。

    必须显式传 tz=CN_TZ。裸 fromtimestamp() 按宿主机本地时区解释，在非北京
    时区的机器上会把北京零点挪到前一天 16:00，于是日级精度被误标为秒级，
    「先后不可判定」的证据被洗白成「可作为原因」——整条因果约束静默失效。

    巨潮对部分公告类型只给日级精度，表现为北京零点。这类记录若参与秒级
    先后比较会得出错误的因果方向，因此必须标记出来。
    """
    if not isinstance(ms, (int, float)):
        return "", "unknown"
    dt = datetime.fromtimestamp(ms / 1000, tz=CN_TZ)
    precision = "day" if (dt.hour, dt.minute, dt.second) == (0, 0, 0) else "second"
    return dt.strftime("%Y-%m-%d %H:%M:%S"), precision


def _from_str(raw: str) -> tuple[str, str]:
    """字符串时间 -> (规范化时间戳, 精度)。"""
    raw = (raw or "").strip()
    if not raw:
        return "", "unknown"
    for fmt, precision in (
        ("%Y-%m-%d %H:%M:%S", "second"),
        ("%Y-%m-%d %H:%M", "minute"),
        ("%Y-%m-%d", "day"),
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S"), precision
        except ValueError:
            continue
    return raw, "unknown"


def ths_hot(day: str | None = None) -> list[dict]:
    """同花顺当日强势股 + 题材归因标签。

    reason 是同花顺编辑部人工运营的题材 tags，形如
    "冰雪工程+数据中心液冷+储能温控+冷热设备"。

    注意：reason 属于第三方编辑观点，不是客观事实，入库时必须保留
    来源标记，否则研究卡会继承别人的叙事框架。
    """
    day = day or _date.today().strftime("%Y-%m-%d")
    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{day}/orderby/date/orderway/desc/charset/GBK/"
    )
    def call():
        return requests.get(url, headers={"User-Agent": UA}, timeout=10).json()

    payload = _retry(call)
    if payload.get("errocode", 0) != 0:
        raise RuntimeError(f"同花顺热点错误: {payload.get('errormsg', '')}")

    rows = []
    for item in payload.get("data") or []:
        rows.append(
            {
                "trade_date": item.get("date") or day,
                "code": str(item.get("code", "")).zfill(6),
                "name": item.get("name", ""),
                "reason": item.get("reason", "") or "",
                "pct_chg": item.get("zhangfu"),
                "close": item.get("close"),
                "turnover_rate": item.get("huanshou"),
                "amount": item.get("chengjiaoe"),
                "big_order_net": item.get("ddejingliang"),
                "market": item.get("market"),
            }
        )
    return rows


def _cninfo_org_id_fallback(code: str) -> str:
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


_org_id_cache: dict[str, str] = {}


def cninfo_org_id(code: str) -> str:
    """解析巨潮 orgId。

    不能用代码前缀推断：格式并不统一。600519 -> gssh0600519，
    但 603533 -> 9900033063。前缀猜测对 603xxx 会静默返回 0 条公告。
    """
    code = str(code).zfill(6)
    if code in _org_id_cache:
        return _org_id_cache[code]

    def call():
        return requests.post(
            "http://www.cninfo.com.cn/new/information/topSearch/query",
            data={"keyWord": code, "maxSecNum": 10, "maxListNum": 5},
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
        ).json()

    org_id = ""
    try:
        for item in _retry(call) or []:
            if str(item.get("code", "")).zfill(6) == code and item.get("orgId"):
                org_id = item["orgId"]
                break
    except Exception:
        org_id = ""

    org_id = org_id or _cninfo_org_id_fallback(code)
    _org_id_cache[code] = org_id
    return org_id


def cninfo_announcements(code: str, page_size: int = 30) -> list[dict]:
    """巨潮公告检索，保留原始时间戳与 PDF 全文地址。"""
    code = str(code).zfill(6)
    payload = {
        "stock": f"{code},{cninfo_org_id(code)}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }

    def call():
        return requests.post(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data=payload,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.cninfo.com.cn/new/disclosure",
                "Origin": "https://www.cninfo.com.cn",
            },
            timeout=15,
        ).json()

    rows = []
    for item in _retry(call).get("announcements") or []:
        ts, precision = _from_ms(item.get("announcementTime"))
        adjunct = item.get("adjunctUrl") or ""
        rows.append(
            {
                "code": code,
                "name": item.get("secName", ""),
                "anno_id": str(item.get("announcementId", "")),
                "title": _TAG_RE.sub("", item.get("announcementTitle", "") or ""),
                "publish_ts": ts,
                "ts_precision": precision,
                "pdf_url": f"https://static.cninfo.com.cn/{adjunct}" if adjunct else "",
                "type_code": item.get("announcementType", "") or "",
            }
        )
    return rows


def eastmoney_stock_news(code: str, page_size: int = 20) -> list[dict]:
    """东财个股新闻（JSONP），时间戳为秒级。"""
    code = str(code).zfill(6)
    inner = json.dumps(
        {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": page_size,
                    "preTag": "",
                    "postTag": "",
                }
            },
        },
        separators=(",", ":"),
    )
    def call():
        return _em_get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": "jQuery_news", "param": inner},
            headers={"User-Agent": UA, "Referer": "https://so.eastmoney.com/"},
        ).text

    text = _retry(call)
    data = json.loads(text[text.index("(") + 1 : text.rindex(")")])

    rows = []
    for item in data.get("result", {}).get("cmsArticleWebOld") or []:
        ts, precision = _from_str(item.get("date", ""))
        rows.append(
            {
                "code": code,
                "title": _TAG_RE.sub("", item.get("title", "") or ""),
                "summary": _TAG_RE.sub("", item.get("content", "") or "")[:300],
                "source": item.get("mediaName", ""),
                "publish_ts": ts,
                "ts_precision": precision,
                "url": item.get("url", ""),
            }
        )
    return rows
