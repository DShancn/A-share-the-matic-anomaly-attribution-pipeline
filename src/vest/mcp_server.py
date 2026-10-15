"""vest MCP server — 把归因管线暴露为跨 agent 可调用的工具。

任何支持 MCP 的客户端（Claude Desktop / Claude Code / Cursor / Windsurf / Zed /
自建 agent）都能挂载本服务，无需 Qoder。

因果纪律写在 instructions 与工具描述里：移植的不只是函数，还有那套防事后归因
的约束。只拿到函数而不知道纪律的 agent，依然会做事后归因。
"""

from __future__ import annotations

import json
from typing import Any

from . import attribute
from . import snapshot as snapshot_mod
from . import store

_INSTRUCTIONS = """\
vest：A股题材异动归因管线。异动只是发现入口，归因产物是研究卡，不是交易信号。

必须遵守的因果纪律（这些不是建议，违反即归因作废）：

1. 只有 pre_session 桶的证据可作为异动原因。
   post_close（收盘后发布）在物理上只能是异动的**结果**，严禁当作原因。
   存在这条约束的原因：异动发生之后再去找解释，总能找到说得通的故事，
   那是叙事谬误。防御必须是机械的，不能靠自觉。
2. intraday 桶可能是原因，也可能是媒体对当日上涨的追涨报道，须逐条判断并说明理由。
3. undetermined 桶（仅日级时间精度且与异动同日）必须显式声明「先后不可判定」，
   不得为了叙事完整而假定时间顺序。
4. evidence 返回的 editorial_tags 是同花顺编辑部人工运营的题材标签，属第三方观点，
   不得归入「事实」，只能作为「观点」并标注来源。
5. 研究卡四分类为 事实/推论/观点/待验证。无法确定的放「待验证」，
   不要为了填满分类而虚报确定性。
6. 每张卡必须写 counter_argument（反方论证）与 falsifiable_check（可证伪的后续观察点），
   并给出 fundamental_verdict ∈ {基本面变化, 情绪资金驱动, 待验证, 噪声}。
   停在「因为某公告所以涨」等于做了个追涨工具。
7. 不产出仓位、买卖点、目标价、涨跌预测。

数据时效：同花顺热点接口的价格字段在交易日 T 当天不结算，需 T+1 才可归因 T。
不要在 T 日尝试归因 T 日。
"""


def _classify(publish_ts: str, ts_precision: str, anomaly_date: str) -> dict[str, str]:
    timing = attribute.classify_timing(publish_ts or None, ts_precision, anomaly_date)
    allowed = {
        attribute.PRE_SESSION: "可作为异动原因",
        attribute.INTRADAY: "可能是原因，须逐条判断是否为媒体追涨报道",
        attribute.POST_CLOSE: "严禁作为原因——物理上只能是异动的结果",
        attribute.UNDETERMINED: "不得假定时间顺序，须显式声明先后不可判定",
    }
    return {"timing": timing, "permitted_use": allowed[timing]}


def build_server() -> Any:
    """构造 MCP server。mcp 为可选依赖，未安装时给出可操作的提示。"""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise SystemExit(
            "MCP 服务需要 mcp 2.x：pip install -e '.[mcp]'\n"
            "注意必须 >=2,<3 —— 2.x 把 FastMCP 改名为 MCPServer，网上常见的 "
            "`from mcp.server.fastmcp import FastMCP` 是 1.x 写法，不可用。\n"
            f"原始错误: {exc}"
        )

    server = MCPServer(name="vest", instructions=_INSTRUCTIONS, version="0.1.0")

    @server.tool(
        title="摄取某交易日快照",
        description="摄取指定交易日的强势股与题材归因标签并聚合题材热度。"
        "必须在 T+1 日调用以获取 T 日数据，否则上游价格字段未结算会报错。幂等，可重复跑。",
    )
    def snapshot(day: str) -> dict[str, Any]:
        """day: YYYY-MM-DD（北京时间交易日）。

        返回摄取条数；题材轴由同花顺 reason 标签按 + 拆词聚合得出，
        不依赖概念板块成分股接口，故对尚未纳入正式概念分类的新题材同样有效。
        """
        conn = store.connect()
        try:
            return snapshot_mod.snapshot(conn, day)
        finally:
            conn.close()

    @server.tool(
        title="异动题材排行",
        description="列出指定交易日按题材聚合强度排序的异动题材。"
        "返回 0 个时先排查数据时效与快照是否已摄取，不要降低阈值凑结果。",
    )
    def anomalies(
        day: str,
        min_stocks: int = 2,
        min_avg_pct: float = 9.0,
        limit: int = 15,
    ) -> dict[str, Any]:
        """返回 anomalies 列表，每项含 theme / stock_count / avg_pct_chg / members。

        zscore_available 为 false 时表示历史快照不足 20 个交易日，
        当前用的是冷启动阈值规则，不得声称做了 z-score 异常检测。
        """
        conn = store.connect()
        try:
            return attribute.detect_anomalies(conn, day, min_stocks, min_avg_pct, limit)
        finally:
            conn.close()

    @server.tool(
        title="时间因果分桶证据包",
        description="召回该题材成分股在异动窗口的公告与新闻，按 publish_ts 相对交易时段"
        "机械分桶。**这是本管线的核心产出，写研究卡前必须调用。**"
        "只有 pre_session 可作为原因；post_close 在物理上只能是结果。",
    )
    def evidence(
        theme: str,
        day: str,
        window_days: int = 4,
        ingest: bool = True,
    ) -> dict[str, Any]:
        """theme 必须与 anomalies 输出的字符串完全一致。

        ingest=True 会联网补齐成分股的巨潮公告与东财新闻语料；
        False 则只用已入库语料（离线或复现时）。
        返回 evidence.{pre_session, intraday, post_close, undetermined} 与 editorial_tags。
        东财新闻返回滚动 top-N，证据集会随时间漂移，因此写进卡片的 evidence 字段
        要抄全原始时间戳与 ts_precision。
        """
        conn = store.connect()
        try:
            if ingest:
                detection = attribute.detect_anomalies(
                    conn, day, min_stocks=1, min_avg_pct=0, limit=500
                )
                target = next(
                    (a for a in detection["anomalies"] if a["theme"] == theme), None
                )
                if target is None:
                    return {"error": f"{day} 无题材「{theme}」记录，请先调用 snapshot"}
                snapshot_mod.ingest_corpus(
                    conn, [m["code"] for m in target["members"]]
                )
            return attribute.recall_evidence(conn, theme, day, window_days)
        finally:
            conn.close()

    @server.tool(
        title="单条证据时间分桶",
        description="判断单条证据相对异动交易日的因果位置。"
        "用于你手工取到证据后自行校验因果方向，不要靠心算——人脑在时间先后判断上"
        "会不自觉偏向已知结果。",
    )
    def classify_timing(
        publish_ts: str, ts_precision: str, anomaly_date: str
    ) -> dict[str, str]:
        """publish_ts: 'YYYY-MM-DD HH:MM:SS'；仅有日期时传 'YYYY-MM-DD'。

        ts_precision 必须如实声明：上游只给到日期就传 "day"，
        给到秒传 "second"。日级精度且与异动同日会判为 undetermined。
        所有时间按北京时间理解，与宿主机时区无关。
        """
        return _classify(publish_ts, ts_precision, anomaly_date)

    @server.tool(
        title="写入研究卡",
        description="把研究卡 JSON 写入数据库。同 card_id 覆盖。"
        "写入前须已调用 evidence 获取分桶证据。",
    )
    def save_card(card_json: str) -> dict[str, Any]:
        """card_json: 研究卡的 JSON 字符串。

        必填字段 theme / anomaly_date / classification（四分类）/
        counter_argument / falsifiable_check / fundamental_verdict / review_at；
        fundamental_verdict 非「噪声」时 followup_queue 必填，
        每项含 code / name / question / trigger。
        """
        card = json.loads(card_json)
        conn = store.connect()
        try:
            return {"card_id": attribute.save_card(conn, card)}
        finally:
            conn.close()

    @server.tool(
        title="列出研究卡与回看状态",
        description="列出全部研究卡，标出 review_at 是否到期。"
        "到期未回看的卡片意味着判断账本没有闭环。",
    )
    def list_cards() -> dict[str, Any]:
        """返回 cards 列表，含 fundamental_verdict 与是否已回填 review_note。"""
        conn = store.connect()
        try:
            rows = conn.execute(
                """SELECT card_id, theme, anomaly_date, fundamental_verdict,
                          review_at, review_note
                   FROM research_card ORDER BY anomaly_date DESC, card_id"""
            ).fetchall()
            return {
                "cards": [
                    {
                        "card_id": r[0],
                        "theme": r[1],
                        "anomaly_date": str(r[2]),
                        "fundamental_verdict": r[3],
                        "review_at": str(r[4]) if r[4] else None,
                        "reviewed": bool(r[5]),
                    }
                    for r in rows
                ]
            }
        finally:
            conn.close()

    @server.tool(
        title="回填回看结果",
        description="研究卡到期后回填实际验证结果，闭环判断账本。"
        "必须诚实记录判定错误的情况；一个全是「判定正确」的账本没有信息量。",
    )
    def review_card(card_id: str, note: str) -> dict[str, str]:
        """note: 实际观察到的结果，以及当初 falsifiable_check 是否应验、判定对错。"""
        conn = store.connect()
        try:
            conn.execute(
                "UPDATE research_card SET review_note = ?, review_at = CURRENT_DATE "
                "WHERE card_id = ?",
                [note, card_id],
            )
            return {"card_id": card_id, "status": "reviewed"}
        finally:
            conn.close()

    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
