"""DuckDB 存储层。

只持久化三类东西：每日快照、文本语料、研究卡。
财务三表属于季度级慢变量，按需调用现成 skill 即可，不落盘。
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb


def _default_db_path() -> Path:
    """数据库位置。

    不能用相对路径：data/vest.duckdb 依赖调用时的 cwd，从项目外运行会静默
    新建一个空库——症状和时区 bug 同类，不报错，只是结论不对。
    也不能向上查找 cwd 的祖先目录，那在项目外调用时同样落空。
    """
    env = os.environ.get("VEST_DB")
    if env:
        return Path(env).expanduser()

    # 源码检出或可编辑安装：数据跟着仓库走，便于连同 cards/ 一起版本化
    pkg_root = Path(__file__).resolve().parents[2]
    if (pkg_root / "pyproject.toml").exists() and (pkg_root / "src" / "vest").is_dir():
        return pkg_root / "data" / "vest.duckdb"

    # 常规安装到 site-packages：不能往包目录里写，改用用户数据目录
    return Path.home() / ".vest" / "vest.duckdb"


DB_PATH = _default_db_path()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hot_stock (
    trade_date    DATE NOT NULL,
    code          VARCHAR NOT NULL,
    name          VARCHAR,
    reason        VARCHAR,
    pct_chg       DOUBLE,
    close         DOUBLE,
    turnover_rate DOUBLE,
    amount        DOUBLE,
    big_order_net DOUBLE,
    market        INTEGER,
    ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS theme_daily (
    trade_date  DATE NOT NULL,
    theme       VARCHAR NOT NULL,
    stock_count INTEGER,
    avg_pct_chg DOUBLE,
    max_pct_chg DOUBLE,
    codes       VARCHAR,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, theme)
);

CREATE TABLE IF NOT EXISTS announcement (
    code         VARCHAR NOT NULL,
    anno_id      VARCHAR NOT NULL,
    name         VARCHAR,
    title        VARCHAR,
    publish_ts   TIMESTAMP,
    ts_precision VARCHAR,
    pdf_url      VARCHAR,
    type_code    VARCHAR,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, anno_id)
);

CREATE TABLE IF NOT EXISTS news (
    url          VARCHAR NOT NULL,
    code         VARCHAR,
    title        VARCHAR,
    summary      VARCHAR,
    source       VARCHAR,
    publish_ts   TIMESTAMP,
    ts_precision VARCHAR,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (url)
);

CREATE TABLE IF NOT EXISTS research_card (
    card_id            VARCHAR PRIMARY KEY,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    theme              VARCHAR,
    anomaly_date       DATE,
    anomaly_detail     VARCHAR,
    evidence           VARCHAR,
    classification     VARCHAR,
    counter_argument   VARCHAR,
    falsifiable_check  VARCHAR,
    fundamental_verdict VARCHAR,
    followup_queue     VARCHAR,
    review_at          DATE,
    review_note        VARCHAR
);
"""


def connect(db_path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    conn.execute(_SCHEMA)
    return conn


def upsert(conn: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> int:
    """按主键幂等写入，同一天可重复摄取。"""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    quoted = ", ".join(f'"{c}"' for c in cols)
    sql = f'INSERT OR REPLACE INTO {table} ({quoted}) VALUES ({placeholders})'
    payload = [[r.get(c) for c in cols] for r in rows]
    conn.executemany(sql, payload)
    return len(payload)
