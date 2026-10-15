"""题材异动检测与时间窗对齐的证据召回。

核心机制：事后归因偏差的防御不是靠提示词，而是靠机械的时间分桶。
板块异动发生在交易日 D 的盘中（09:30-15:00），据此把每条证据分成四类，
只有 pre_session 能无条件作为原因，post_close 在物理上不可能是原因。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import duckdb

from .store import upsert

OPEN = (9, 30)
CLOSE = (15, 0)

# 证据与异动时刻的时间关系
PRE_SESSION = "pre_session"      # 开盘前发布，可作为原因
INTRADAY = "intraday"            # 盘中发布，可能是因也可能是媒体追涨报道
POST_CLOSE = "post_close"        # 收盘后发布，只能是结果
UNDETERMINED = "undetermined"    # 仅日级精度且与异动同日，先后不可判定


def classify_timing(publish_ts, ts_precision: str, anomaly_date: str) -> str:
    if not publish_ts:
        return UNDETERMINED
    ts = publish_ts if isinstance(publish_ts, datetime) else datetime.fromisoformat(str(publish_ts))
    day = datetime.fromisoformat(anomaly_date)
    open_ts = day.replace(hour=OPEN[0], minute=OPEN[1])
    close_ts = day.replace(hour=CLOSE[0], minute=CLOSE[1])

    if ts.date() != day.date():
        return PRE_SESSION if ts < open_ts else POST_CLOSE
    # 同日：日级精度无法判断先后，不能假装知道
    if ts_precision == "day":
        return UNDETERMINED
    if ts < open_ts:
        return PRE_SESSION
    return INTRADAY if ts <= close_ts else POST_CLOSE


def detect_anomalies(
    conn: duckdb.DuckDBPyConnection,
    day: str,
    min_stocks: int = 2,
    min_avg_pct: float = 9.0,
    limit: int = 15,
) -> dict:
    """按题材聚合强度排序。

    冷启动阶段用无需历史的规则；快照积累到 zscore_ready_days 后
    追加与自身历史对比的 z-score 分支。
    """
    history_days = conn.execute(
        "SELECT count(DISTINCT trade_date) FROM theme_daily"
    ).fetchone()[0]

    rows = conn.execute(
        """
        SELECT theme, stock_count, avg_pct_chg, max_pct_chg, codes
        FROM theme_daily
        WHERE trade_date = CAST(? AS DATE)
          AND (stock_count >= ? OR max_pct_chg >= 19.0)
          AND avg_pct_chg >= ?
        ORDER BY stock_count DESC, avg_pct_chg DESC
        LIMIT ?
        """,
        [day, min_stocks, min_avg_pct, limit],
    ).fetchall()

    anomalies = []
    for theme, count, avg_pct, max_pct, codes in rows:
        members = conn.execute(
            """
            SELECT code, name, pct_chg, turnover_rate, reason
            FROM hot_stock
            WHERE trade_date = CAST(? AS DATE)
              AND code IN (SELECT unnest(string_split(?, ',')))
            ORDER BY pct_chg DESC
            """,
            [day, codes],
        ).fetchall()
        anomalies.append(
            {
                "theme": theme,
                "stock_count": count,
                "avg_pct_chg": avg_pct,
                "max_pct_chg": max_pct,
                "members": [
                    {
                        "code": m[0],
                        "name": m[1],
                        "pct_chg": m[2],
                        "turnover_rate": m[3],
                        "reason": m[4],
                    }
                    for m in members
                ],
            }
        )

    return {
        "trade_date": day,
        "history_days": history_days,
        "zscore_available": history_days >= 20,
        "anomalies": anomalies,
    }


def recall_evidence(
    conn: duckdb.DuckDBPyConnection,
    theme: str,
    day: str,
    window_days: int = 3,
) -> dict:
    """召回该题材成分股在 [D-window, D] 的公告与新闻，并按时间因果分桶。

    只把 pre_session 归入 causes；其余分桶保留但不作为原因，
    这样 LLM 拿到的证据包本身就编码了因果方向约束。
    """
    anomaly = next(
        (a for a in detect_anomalies(conn, day, min_stocks=1, min_avg_pct=0, limit=200)["anomalies"]
         if a["theme"] == theme),
        None,
    )
    if anomaly is None:
        raise ValueError(f"{day} 无题材 {theme} 的异动记录，请先执行 snapshot")

    codes = [m["code"] for m in anomaly["members"]]
    if not codes:
        raise ValueError(f"题材 {theme} 无成分股")

    placeholders = ", ".join(["?"] * len(codes))
    window_start = (datetime.fromisoformat(day) - timedelta(days=window_days)).isoformat()

    annos = conn.execute(
        f"""
        SELECT code, title, publish_ts, ts_precision, pdf_url
        FROM announcement
        WHERE code IN ({placeholders}) AND publish_ts >= CAST(? AS TIMESTAMP)
        ORDER BY publish_ts DESC
        """,
        [*codes, window_start],
    ).fetchall()

    news = conn.execute(
        f"""
        SELECT code, title, publish_ts, ts_precision, source, url
        FROM news
        WHERE code IN ({placeholders}) AND publish_ts >= CAST(? AS TIMESTAMP)
        ORDER BY publish_ts DESC
        """,
        [*codes, window_start],
    ).fetchall()

    raw = [
        {
            "kind": "announcement",
            "code": code,
            "title": title,
            "publish_ts": str(ts) if ts else "",
            "ts_precision": precision,
            "source": "巨潮公告",
            "url": url or "",
            "timing": classify_timing(ts, precision, day),
        }
        for code, title, ts, precision, url in annos
    ] + [
        {
            "kind": "news",
            "code": code,
            "title": title,
            "publish_ts": str(ts) if ts else "",
            "ts_precision": precision,
            "source": src or "",
            "url": url or "",
            "timing": classify_timing(ts, precision, day),
        }
        for code, title, ts, precision, src, url in news
    ]

    buckets: dict[str, list[dict]] = {
        PRE_SESSION: [], INTRADAY: [], POST_CLOSE: [], UNDETERMINED: []
    }
    for item in raw:
        buckets[item["timing"]].append(item)

    # 同花顺题材标签本身属于第三方编辑观点，单独标注而非混入事实证据
    editorial_tags = sorted({t for m in anomaly["members"] for t in (m["reason"] or "").split("+") if t.strip()})

    return {
        "theme": theme,
        "anomaly_date": day,
        "anomaly_detail": {
            "stock_count": anomaly["stock_count"],
            "avg_pct_chg": anomaly["avg_pct_chg"],
            "max_pct_chg": anomaly["max_pct_chg"],
        },
        "members": anomaly["members"],
        "editorial_tags": {
            "source": "同花顺编辑部",
            "type": "观点",
            "note": "人工运营的题材标签，是叙事框架而非客观事实，不可直接归入事实类",
            "tags": editorial_tags,
        },
        "evidence": buckets,
        "constraint": (
            "只有 pre_session 可作为异动原因；post_close 在物理上只能是结果；"
            "undetermined 为仅日级精度且与异动同日，先后不可判定，须显式标注不确定性"
        ),
        "window_days": window_days,
    }


def save_card(conn: duckdb.DuckDBPyConnection, card: dict) -> str:
    """写入研究卡。classification 与 evidence 以 JSON 字符串存储。"""
    card_id = card.get("card_id") or f"{card['anomaly_date']}_{card['theme']}"
    row = {
        "card_id": card_id,
        "theme": card.get("theme"),
        "anomaly_date": card.get("anomaly_date"),
        "anomaly_detail": json.dumps(card.get("anomaly_detail"), ensure_ascii=False),
        "evidence": json.dumps(card.get("evidence"), ensure_ascii=False),
        "classification": json.dumps(card.get("classification"), ensure_ascii=False),
        "counter_argument": card.get("counter_argument"),
        "falsifiable_check": card.get("falsifiable_check"),
        "fundamental_verdict": card.get("fundamental_verdict"),
        "followup_queue": json.dumps(card.get("followup_queue"), ensure_ascii=False),
        "review_at": card.get("review_at"),
        "review_note": card.get("review_note"),
    }
    upsert(conn, "research_card", [row])
    return card_id
