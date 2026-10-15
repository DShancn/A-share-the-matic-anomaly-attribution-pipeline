"""vest — A股题材归因管线。"""

from __future__ import annotations

from zoneinfo import ZoneInfo

# A股全部时间语义（开盘 09:30、收盘 15:00、公告日切）都是北京时间。
# 必须显式指定，不能用 datetime.fromtimestamp() 的默认行为——那按宿主机
# 本地时区解释 Unix 毫秒，换台机器就会把日级精度公告误判成秒级精度，
# 进而让「先后不可判定」的证据被洗白成「可作为原因」。
CN_TZ = ZoneInfo("Asia/Shanghai")
