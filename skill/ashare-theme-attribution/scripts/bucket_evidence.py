#!/usr/bin/env python3
"""时间因果分桶器（仅依赖标准库）。

vest 未安装时的降级路径。把证据整理成 JSON 喂给本脚本，由它机械执行
publish_ts 相对交易时段的分桶——**这一步不要靠心算**，人脑在时间先后
判断上会不自觉地偏向已经知道的结果。

用法:
    python3 bucket_evidence.py 2026-10-14 < evidence.json
    python3 bucket_evidence.py 2026-10-14 --file evidence.json

输入 JSON: 证据数组，每项至少含
    publish_ts    "YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DD"
    ts_precision  second | minute | day | unknown（缺失按 unknown 处理）
其余字段原样透传到输出。

分桶语义（异动日为 D，A股交易时段 09:30-15:00 北京时间）:
    pre_session    D 之前，或 D 当日 09:30 前发布 -> 可作为异动原因
    intraday       D 当日 09:30-15:00 发布       -> 可能是因，也可能是媒体追涨报道
    post_close     D 当日 15:00 后，或 D 之后     -> 物理上只能是结果，严禁当原因
    undetermined   仅日级精度且与 D 同日          -> 先后不可判定，必须显式声明
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
OPEN = (9, 30)
CLOSE = (15, 0)

FORMATS = (
    ("%Y-%m-%d %H:%M:%S", "second"),
    ("%Y-%m-%d %H:%M", "minute"),
    ("%Y-%m-%d", "day"),
)


def parse_ts(raw: str) -> tuple[datetime | None, str]:
    """解析时间戳，返回 (datetime, 精度)。

    精度以调用方声明的 ts_precision 为准；若未声明则按字符串格式推断。
    """
    raw = (raw or "").strip()
    for fmt, inferred in FORMATS:
        try:
            return datetime.strptime(raw, fmt), inferred
        except ValueError:
            continue
    return None, "unknown"


def classify(publish_ts: str, ts_precision: str | None, anomaly_date: str) -> str:
    ts, inferred = parse_ts(publish_ts)
    if ts is None:
        return "undetermined"

    precision = ts_precision if ts_precision in ("second", "minute", "day") else inferred
    day = datetime.strptime(anomaly_date, "%Y-%m-%d")
    open_ts = day.replace(hour=OPEN[0], minute=OPEN[1])
    close_ts = day.replace(hour=CLOSE[0], minute=CLOSE[1])

    if ts.date() != day.date():
        return "pre_session" if ts < open_ts else "post_close"
    if precision == "day":
        return "undetermined"
    if ts < open_ts:
        return "pre_session"
    return "intraday" if ts <= close_ts else "post_close"


def bucket(items: list[dict], anomaly_date: str) -> dict[str, list[dict]]:
    out = {"pre_session": [], "intraday": [], "post_close": [], "undetermined": []}
    for item in items:
        timing = classify(item.get("publish_ts", ""), item.get("ts_precision"), anomaly_date)
        out[timing].append({**item, "timing": timing})
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="A股异动证据的时间因果分桶器")
    p.add_argument("anomaly_date", help="异动交易日 YYYY-MM-DD")
    p.add_argument("--file", default="-", help="证据 JSON 路径，默认从 stdin 读")
    args = p.parse_args(argv)

    try:
        datetime.strptime(args.anomaly_date, "%Y-%m-%d")
    except ValueError:
        sys.exit(f"错误: 异动日格式应为 YYYY-MM-DD，收到 {args.anomaly_date!r}")

    handle = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
    with handle:
        items = json.load(handle)
    if not isinstance(items, list):
        sys.exit("错误: 输入 JSON 顶层必须是证据数组")

    result = bucket(items, args.anomaly_date)
    summary = {k: len(v) for k, v in result.items()}
    print(
        f"# 异动日 {args.anomaly_date} 分桶结果 {summary}\n"
        f"# 只有 pre_session 可作为原因；post_close 只能是结果\n",
        file=sys.stderr,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
