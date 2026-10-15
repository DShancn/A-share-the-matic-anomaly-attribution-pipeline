"""MCP 服务的可挂载性测试。

验证的不只是函数能跑，而是第三方 agent 通过标准 MCP 握手真能发现工具、
拿到因果纪律 instructions、并成功调用。mcp 为可选依赖，未安装时跳过。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest

HAS_MCP = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(HAS_MCP, "未安装可选依赖 mcp：pip install -e '.[mcp]'")
class TestToolRegistration(unittest.TestCase):
    def test_seven_tools_with_correct_schemas(self):
        from vest.mcp_server import build_server

        server = build_server()
        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        self.assertEqual(
            names,
            {
                "snapshot",
                "anomalies",
                "evidence",
                "classify_timing",
                "save_card",
                "list_cards",
                "review_card",
            },
        )

        evidence = next(t for t in tools if t.name == "evidence")
        self.assertEqual(sorted(evidence.input_schema["required"]), ["day", "theme"])
        self.assertIn("window_days", evidence.input_schema["properties"])

    def test_instructions_carry_the_discipline(self):
        """instructions 必须包含因果纪律，否则移植的只是函数不是约束。"""
        from vest.mcp_server import _INSTRUCTIONS

        for phrase in (
            "只有 pre_session 桶的证据可作为异动原因",
            "严禁当作原因",
            "先后不可判定",
            "不得归入「事实」",
            "叙事谬误",
        ):
            self.assertIn(phrase, _INSTRUCTIONS)

    def test_classify_tool_returns_permitted_use(self):
        from vest.mcp_server import _classify

        cases = {
            ("2026-10-14 18:28:59", "second"): "post_close",
            ("2026-10-14 00:00:00", "day"): "undetermined",
            ("2026-10-13 09:08:00", "second"): "pre_session",
            ("2026-10-14 12:03:23", "second"): "intraday",
        }
        for (ts, precision), expected in cases.items():
            with self.subTest(ts=ts, precision=precision):
                got = _classify(ts, precision, "2026-10-14")
                self.assertEqual(got["timing"], expected)
                self.assertTrue(got["permitted_use"])


@unittest.skipUnless(HAS_MCP, "未安装可选依赖 mcp")
class TestStdioHandshake(unittest.TestCase):
    """真实 MCP 客户端经 stdio 挂载，与第三方 agent 的接入路径一致。"""

    def test_initialize_list_and_call(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def run():
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "vest.mcp_server"]
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    info = await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool(
                        "classify_timing",
                        {
                            "publish_ts": "2026-10-14 18:28:59",
                            "ts_precision": "second",
                            "anomaly_date": "2026-10-14",
                        },
                    )
                    return info, tools, result

        info, tools, result = asyncio.run(run())

        self.assertEqual(info.server_info.name, "vest")
        self.assertIn("pre_session", info.instructions or "")
        self.assertEqual(len(tools.tools), 7)

        payload = result.structured_content or json.loads(result.content[0].text)
        self.assertEqual(payload["timing"], "post_close")
        self.assertIn("严禁作为原因", payload["permitted_use"])


if __name__ == "__main__":
    unittest.main()
