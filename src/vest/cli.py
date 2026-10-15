"""命令行入口。

evidence 子命令输出 JSON 证据包，供 Qoder agent 作为 LLM 层消费：
Python 侧只负责取数与时间因果约束，摘要/分类/反方论证由 agent 完成，
不内嵌任何 LLM API 调用。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import CN_TZ, attribute, snapshot, store


def _today() -> str:
    """北京时间的今天。date.today() 按宿主机时区，在海外机器上会差一天。"""
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def cmd_snapshot(args) -> None:
    conn = store.connect()
    print(json.dumps(snapshot.snapshot(conn, args.day), ensure_ascii=False))


def cmd_anomalies(args) -> None:
    conn = store.connect()
    result = attribute.detect_anomalies(conn, args.day, args.min_stocks, args.min_avg_pct, args.limit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    print(f"{result['trade_date']}  历史快照 {result['history_days']} 天"
          f"  z-score {'可用' if result['zscore_available'] else '不可用(需≥20天)'}")
    print(f"异动题材 {len(result['anomalies'])} 个\n")
    for a in result["anomalies"]:
        names = " ".join(f"{m['name']}({m['pct_chg']}%)" for m in a["members"])
        print(f"  {a['theme']:<14} 家数={a['stock_count']:<3} 均涨={a['avg_pct_chg']}%  {names}")


def cmd_evidence(args) -> None:
    conn = store.connect()
    day = args.day or _today()

    detection = attribute.detect_anomalies(conn, day, min_stocks=1, min_avg_pct=0, limit=500)
    target = next((a for a in detection["anomalies"] if a["theme"] == args.theme), None)
    if target is None:
        sys.exit(f"{day} 无题材「{args.theme}」记录，请先执行: vest snapshot {day}")

    codes = [m["code"] for m in target["members"]]
    if not args.skip_ingest:
        stats = snapshot.ingest_corpus(conn, codes, args.news_limit, args.anno_limit)
        print(f"# 语料摄取 {stats}", file=sys.stderr)

    bundle = attribute.recall_evidence(conn, args.theme, day, args.window)
    print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))


def cmd_cards(args) -> None:
    conn = store.connect()
    rows = conn.execute(
        """SELECT card_id, theme, anomaly_date, fundamental_verdict, review_at, review_note
           FROM research_card ORDER BY anomaly_date DESC, card_id"""
    ).fetchall()
    if not rows:
        print("暂无研究卡")
        return
    for card_id, theme, d, verdict, review_at, note in rows:
        flag = "已回看" if note else (f"待回看({review_at})" if review_at else "未排期")
        print(f"  {card_id:<34} {theme:<14} {verdict or '-':<12} {flag}")


def cmd_save_card(args) -> None:
    conn = store.connect()
    card = json.load(sys.stdin if args.file == "-" else open(args.file, encoding="utf-8"))
    card_id = attribute.save_card(conn, card)
    print(f"已写入研究卡: {card_id}")


def cmd_review(args) -> None:
    conn = store.connect()
    conn.execute(
        "UPDATE research_card SET review_note = ?, review_at = CURRENT_DATE WHERE card_id = ?",
        [args.note, args.card_id],
    )
    print(f"已回看: {args.card_id}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="vest", description="价值投资归因管线")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="摄取某日强势股快照并聚合题材热度")
    s.add_argument("day", nargs="?", default=None)
    s.set_defaults(func=cmd_snapshot)

    a = sub.add_parser("anomalies", help="题材异动排行")
    a.add_argument("day", nargs="?", default=None)
    a.add_argument("--min-stocks", type=int, default=2)
    a.add_argument("--min-avg-pct", type=float, default=9.0)
    a.add_argument("--limit", type=int, default=15)
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_anomalies)

    e = sub.add_parser("evidence", help="输出题材的时间窗对齐证据包(JSON)")
    e.add_argument("theme")
    e.add_argument("day", nargs="?", default=None)
    e.add_argument("--window", type=int, default=4, help="回溯天数，默认 4")
    e.add_argument("--news-limit", type=int, default=30)
    e.add_argument("--anno-limit", type=int, default=40)
    e.add_argument("--skip-ingest", action="store_true", help="只用已入库语料，不联网")
    e.set_defaults(func=cmd_evidence)

    c = sub.add_parser("cards", help="列出研究卡与回看状态")
    c.set_defaults(func=cmd_cards)

    sc = sub.add_parser("save-card", help="从 JSON 写入研究卡")
    sc.add_argument("file", nargs="?", default="-")
    sc.set_defaults(func=cmd_save_card)

    r = sub.add_parser("review", help="回填研究卡的实际验证结果")
    r.add_argument("card_id")
    r.add_argument("note")
    r.set_defaults(func=cmd_review)

    args = p.parse_args(argv)
    if getattr(args, "day", None) is None and args.cmd in ("snapshot", "anomalies"):
        args.day = _today()
    try:
        args.func(args)
    except (RuntimeError, ValueError) as exc:
        sys.exit(f"错误: {exc}")


if __name__ == "__main__":
    main()
