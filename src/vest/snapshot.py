"""每日快照摄取。

题材轴不依赖概念板块成分股接口，而是从同花顺 reason tags 拆词聚合得出。
这对「AI 漫剧」这类尚未被纳入正式概念分类的新题材同样有效。
"""

from __future__ import annotations

import sys
from collections import defaultdict

import duckdb

from . import sources
from .store import upsert


def split_themes(reason: str) -> list[str]:
    return [t.strip() for t in (reason or "").split("+") if t.strip()]


def aggregate_themes(hot: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in hot:
        for theme in split_themes(row.get("reason", "")):
            buckets[theme].append(row)

    out = []
    for theme, rows in buckets.items():
        pcts = [r["pct_chg"] for r in rows if r.get("pct_chg") is not None]
        out.append(
            {
                "trade_date": rows[0]["trade_date"],
                "theme": theme,
                "stock_count": len(rows),
                "avg_pct_chg": round(sum(pcts) / len(pcts), 3) if pcts else None,
                "max_pct_chg": max(pcts) if pcts else None,
                "codes": ",".join(r["code"] for r in rows),
            }
        )
    out.sort(key=lambda r: (r["stock_count"], r["avg_pct_chg"] or 0), reverse=True)
    return out


def snapshot(conn: duckdb.DuckDBPyConnection, day: str | None = None) -> dict:
    """摄取某日强势股快照并聚合题材热度。幂等，可重复跑。

    必须在收盘后执行：同花顺 getharden 接口盘中只返回 name/code/reason，
    价格字段（zhangfu/close/chengjiaoe）要等收盘后才填充。盘中摄取会写入
    全 NULL 价格，导致异动检测静默返回 0 个结果。
    """
    hot = sources.ths_hot(day)
    if not hot:
        return {"trade_date": day, "hot_stock": 0, "themes": 0}

    missing = sum(1 for r in hot if r.get("pct_chg") is None)
    if missing == len(hot):
        raise RuntimeError(
            f"{hot[0]['trade_date']} 上游未返回任何价格字段（{missing}/{len(hot)} 缺失）。"
            "同花顺的价格字段要到交易日 T 的次日才结算，改传上一个交易日重试，"
            "不要等当天——实测 T 日 17:14 仍未结算。"
        )
    if missing:
        print(f"  ! 警告: {missing}/{len(hot)} 只缺失价格字段", file=sys.stderr)

    day = hot[0]["trade_date"]
    themes = aggregate_themes(hot)
    upsert(conn, "hot_stock", hot)
    upsert(conn, "theme_daily", themes)
    return {"trade_date": day, "hot_stock": len(hot), "themes": len(themes)}


def ingest_corpus(
    conn: duckdb.DuckDBPyConnection,
    codes: list[str],
    news_limit: int = 20,
    anno_limit: int = 30,
) -> dict:
    """为指定股票池拉取公告与新闻，建立带时间戳的本地语料。"""
    annos: list[dict] = []
    news: list[dict] = []
    for code in codes:
        try:
            annos.extend(sources.cninfo_announcements(code, anno_limit))
        except Exception as exc:  # 单只失败不阻断整批
            print(f"  ! 公告 {code}: {exc}")
        try:
            news.extend(sources.eastmoney_stock_news(code, news_limit))
        except Exception as exc:
            print(f"  ! 新闻 {code}: {exc}")

    upsert(conn, "announcement", annos)
    upsert(conn, "news", news)
    return {"codes": len(codes), "announcements": len(annos), "news": len(news)}
