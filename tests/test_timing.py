"""时间因果分桶的单元测试。

分桶是整套归因的唯一硬约束，且为纯函数，必须锁死边界行为与宿主机时区无关性。
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from vest import store
from vest.attribute import classify_timing
from vest.sources import _from_ms

ANOMALY_DATE = "2026-10-14"

# 巨潮：北京时间 2026-10-14 00:00:00 的日级精度公告
DAY_PRECISION_SAME_DAY_MS = 1791907200000


class TestSessionBoundaries(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            ("2026-10-13 23:59:00", "second", "pre_session"),
            ("2026-10-14 09:29:59", "second", "pre_session"),
            ("2026-10-14 09:30:00", "second", "intraday"),
            ("2026-10-14 12:03:23", "second", "intraday"),
            ("2026-10-14 15:00:00", "second", "intraday"),
            ("2026-10-14 15:00:01", "second", "post_close"),
            ("2026-10-14 18:28:59", "second", "post_close"),
            ("2026-10-15 00:00:00", "day", "post_close"),
        ]
        for ts, precision, expected in cases:
            with self.subTest(ts=ts, precision=precision):
                got = classify_timing(datetime.fromisoformat(ts), precision, ANOMALY_DATE)
                self.assertEqual(got, expected)


class TestDayPrecision(unittest.TestCase):
    """日级精度仅在与异动同日时不可判定，跨日仍可判定——这一点极易搞反。"""

    def test_same_day_is_undetermined(self):
        self.assertEqual(
            classify_timing(datetime(2026, 10, 14), "day", ANOMALY_DATE), "undetermined"
        )

    def test_earlier_day_is_pre_session(self):
        self.assertEqual(
            classify_timing(datetime(2026, 10, 13), "day", ANOMALY_DATE), "pre_session"
        )

    def test_later_day_is_post_close(self):
        self.assertEqual(
            classify_timing(datetime(2026, 10, 15), "day", ANOMALY_DATE), "post_close"
        )

    def test_missing_timestamp_is_undetermined(self):
        self.assertEqual(classify_timing(None, "unknown", ANOMALY_DATE), "undetermined")


@unittest.skipUnless(hasattr(time, "tzset"), "time.tzset() 仅在类 Unix 平台可用")
class TestTimezoneInvariance(unittest.TestCase):
    """回归测试：宿主机时区不得影响解析结果与分桶结论。

    _from_ms 曾用裸 datetime.fromtimestamp()。在 TZ=UTC 下，北京零点的日级
    公告被解析成前一天 16:00 且精度误标为 second，于是 undetermined 被洗白
    成 pre_session——不报错，只是结论反了。
    """

    HOST_TZS = ("Asia/Shanghai", "UTC", "America/New_York", "Pacific/Kiritimati")

    def _parse_under(self, tz_name: str):
        original = os.environ.get("TZ")
        os.environ["TZ"] = tz_name
        time.tzset()
        try:
            return _from_ms(DAY_PRECISION_SAME_DAY_MS)
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_parse_is_timezone_independent(self):
        for tz in self.HOST_TZS:
            with self.subTest(tz=tz):
                self.assertEqual(self._parse_under(tz), ("2026-10-14 00:00:00", "day"))

    def test_bucketing_is_timezone_independent(self):
        for tz in self.HOST_TZS:
            ts, precision = self._parse_under(tz)
            with self.subTest(tz=tz):
                self.assertEqual(
                    classify_timing(datetime.fromisoformat(ts), precision, ANOMALY_DATE),
                    "undetermined",
                )


class TestDbPath(unittest.TestCase):
    def test_env_override_wins(self):
        os.environ["VEST_DB"] = "/tmp/vest-test.duckdb"
        try:
            self.assertEqual(store._default_db_path(), Path("/tmp/vest-test.duckdb"))
        finally:
            del os.environ["VEST_DB"]

    def test_anchors_to_project_root_regardless_of_cwd(self):
        """从项目外调用也必须找到项目库，不得静默新建空库。"""
        root = Path(store.__file__).resolve().parents[2]
        original = os.getcwd()
        os.chdir(tempfile.gettempdir())
        try:
            self.assertEqual(store._default_db_path(), root / "data" / "vest.duckdb")
        finally:
            os.chdir(original)


if __name__ == "__main__":
    unittest.main()
