"""数据库连接、建表、WAL 管理"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# 数据库文件固定放 data/lawfirm.db（整个 lawfirm_app 目录随 Seafile/坚果云同步）
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lawfirm.db"

SCHEMA = """
-- 发票主表（销项导入 + 台账 sheet1/2/3 + 手动补录）
CREATE TABLE IF NOT EXISTS invoice (
    invoice_no      TEXT PRIMARY KEY,
    invoice_date    TEXT NOT NULL,
    buyer           TEXT,
    total_amount    REAL NOT NULL DEFAULT 0,
    kind            TEXT,
    status          TEXT,
    voucher_no      TEXT,
    goods           TEXT,
    net_amount      REAL,
    tax_rate        TEXT,
    tax             REAL,
    orig_invoice_no TEXT,
    remark          TEXT,
    case_no         TEXT,
    source          TEXT NOT NULL DEFAULT 'import',   -- import/manual
    import_batch_id INTEGER,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- 经办人拆分（每张发票一个或多个经办人及开票份额，红字为负）
CREATE TABLE IF NOT EXISTS charge_detail (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no      TEXT NOT NULL REFERENCES invoice(invoice_no),
    person_name     TEXT NOT NULL,
    billing_amount  REAL NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'import',
    import_batch_id INTEGER,
    UNIQUE(invoice_no, person_name)
);

-- 收款明细（台账备注解析 + 手动补录 + 预收款核销生成）
CREATE TABLE IF NOT EXISTS collection (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no    TEXT NOT NULL REFERENCES invoice(invoice_no),
    amount        REAL NOT NULL DEFAULT 0,
    receipt_date  TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'import',
    import_batch_id INTEGER,
    note          TEXT
);

-- 退款（手动确认，可多次、可部分）
CREATE TABLE IF NOT EXISTS refund (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    red_invoice_no  TEXT NOT NULL REFERENCES invoice(invoice_no),
    orig_invoice_no TEXT,
    refund_amount   REAL NOT NULL DEFAULT 0,
    refund_date     TEXT NOT NULL,
    note            TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- 预收款（sheet4 已入账未开票）
CREATE TABLE IF NOT EXISTS prepayment (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_date TEXT,
    buyer         TEXT NOT NULL,
    amount        REAL NOT NULL DEFAULT 0,
    person_text   TEXT,
    case_no       TEXT,
    remark        TEXT,
    source        TEXT NOT NULL DEFAULT 'import',
    import_batch_id INTEGER
);

-- 预收款核销记录
CREATE TABLE IF NOT EXISTS prepayment_offset (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prepayment_id INTEGER NOT NULL REFERENCES prepayment(id),
    invoice_no    TEXT NOT NULL REFERENCES invoice(invoice_no),
    offset_amount REAL NOT NULL DEFAULT 0,
    offset_date   TEXT NOT NULL,
    confirmed     INTEGER NOT NULL DEFAULT 1
);

-- 职工花名册（基础数据：模板导入 + 手动）
CREATE TABLE IF NOT EXISTS staff (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    staff_type  TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    note        TEXT,
    source      TEXT NOT NULL DEFAULT 'import',
    import_batch_id INTEGER,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- 费用台账（本期仅导入落库，供后续结算表使用）
CREATE TABLE IF NOT EXISTS expense_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period          TEXT NOT NULL,
    seq             INTEGER,
    exp_date        TEXT,
    name            TEXT,
    ticket_no       TEXT,
    handler         TEXT,               -- 经手人(真实发生)
    actual_handler  TEXT,               -- 经办人(承担)
    expense_amount  REAL,
    tax_amount      REAL,
    book_amount     REAL,               -- 账面费用金额(= 费用金额-税额)
    expense_type    TEXT,
    voucher_no      TEXT,
    subject1        TEXT,
    subject2        TEXT,
    source          TEXT NOT NULL DEFAULT 'import',
    import_batch_id INTEGER
);

-- 导入批次（支撑撤销/重导/存档追溯）
CREATE TABLE IF NOT EXISTS import_batch (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_type   TEXT NOT NULL,          -- invoice/ledger/expense/staff
    period       TEXT NOT NULL,
    file_name    TEXT NOT NULL,
    archive_path TEXT,
    file_hash    TEXT,
    imported_at  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'   -- active/rolled_back
);

-- 快照
CREATE TABLE IF NOT EXISTS snapshot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    db_backup   TEXT NOT NULL,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_invoice_orig   ON invoice(orig_invoice_no);
CREATE INDEX IF NOT EXISTS idx_charge_invoice ON charge_detail(invoice_no);
CREATE INDEX IF NOT EXISTS idx_collection_inv ON collection(invoice_no);
CREATE INDEX IF NOT EXISTS idx_refund_red     ON refund(red_invoice_no);
CREATE INDEX IF NOT EXISTS idx_batch_type     ON import_batch(batch_type, period);

-- 数据修改记录（含手动备注）
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    record_id  TEXT NOT NULL,
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    note       TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_changelog_record ON change_log(table_name, record_id);

-- 费用类型维护（全集 + 归类：报酬发放/住房公积金/保险费/汽油费/其他）
CREATE TABLE IF NOT EXISTS expense_cat (
    expense_type TEXT PRIMARY KEY,
    category     TEXT NOT NULL DEFAULT '其他',
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    """获取连接（WAL 模式 + 外键约束 + 行工厂）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """建库建表（幂等）"""
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        # 迁移：staff 加入职月份
        cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)")]
        if "hire_month" not in cols:
            conn.execute("ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''")
        # 迁移：数据行身份字段（合伙/聘用/兼职）
        for tbl in ("charge_detail", "expense_ledger"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")]
            if "person_type" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN person_type TEXT DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def checkpoint() -> None:
    """WAL 合并回主库（同步软件前调用，保证 db 为单一文件）"""
    conn = get_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
