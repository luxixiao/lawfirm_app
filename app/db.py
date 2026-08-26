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
-- person_name：按经办人归因的收款（问题行修正/历史补录可逐人填不同收款额与日期）；
--   普通导入留空''，由结算时按开票份额比例分摊兜底。
CREATE TABLE IF NOT EXISTS collection (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no    TEXT NOT NULL REFERENCES invoice(invoice_no),
    amount        REAL NOT NULL DEFAULT 0,
    receipt_date  TEXT NOT NULL,
    person_name   TEXT NOT NULL DEFAULT '',
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

-- 发票台账原始镜表（方案D）：Excel 每个 sheet 逐行 1:1 镜像，数值列原样存文本、不归一化
-- 与归一化 invoice 表解耦；导入双写、手工增删改、可「同步到 invoice」
CREATE TABLE IF NOT EXISTS raw_invoice (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_name      TEXT NOT NULL DEFAULT '',
    row_no          INTEGER NOT NULL DEFAULT 0,
    seq             TEXT,
    invoice_no      TEXT,
    kind            TEXT,
    invoice_date_raw TEXT,
    status          TEXT,
    voucher_no      TEXT,
    buyer           TEXT,
    total_amount_raw TEXT,
    net_amount_raw  TEXT,
    tax_rate_raw    TEXT,
    tax_raw         TEXT,
    goods           TEXT,
    remark          TEXT,
    synced          INTEGER NOT NULL DEFAULT 0,   -- 0=待同步 1=已同步
    import_batch_id INTEGER,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_raw_inv_no     ON raw_invoice(invoice_no);
CREATE INDEX IF NOT EXISTS idx_raw_inv_synced ON raw_invoice(synced);
CREATE INDEX IF NOT EXISTS idx_raw_inv_batch  ON raw_invoice(import_batch_id);

-- 发票台账原始镜表（统一审计中心数据源之一）：Excel 每个 sheet 逐行 1:1 镜像，
-- 覆盖 sheet1~4 全部原始列（不归一化）；导入双写、可手工编辑、编辑自动写 change_log。
CREATE TABLE IF NOT EXISTS raw_ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_key        TEXT NOT NULL DEFAULT '',     -- sheet1/sheet2/sheet3/sheet4
    sheet_name       TEXT NOT NULL DEFAULT '',
    row_no           INTEGER NOT NULL DEFAULT 0,
    seq              TEXT,                          -- 序号
    invoice_date_raw TEXT,                          -- 开票日期(原始文本)
    invoice_no       TEXT,
    buyer            TEXT,                          -- 对方
    amount_raw       TEXT,                          -- 金额(原始文本)
    amount_num       REAL,                          -- 金额(数值)
    handler_text     TEXT,                          -- 经办人
    remark           TEXT,                          -- 备注
    case_no          TEXT,                          -- 案号
    recv_date_raw    TEXT,                          -- 收到日期(sheet4 预收款)
    kind             TEXT NOT NULL DEFAULT 'invoice',  -- invoice / prepayment
    synced           INTEGER NOT NULL DEFAULT 1,    -- 导入即与文档一致
    import_batch_id  INTEGER,
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_raw_ledger_no    ON raw_ledger(invoice_no);
CREATE INDEX IF NOT EXISTS idx_raw_ledger_sheet ON raw_ledger(sheet_key);
CREATE INDEX IF NOT EXISTS idx_raw_ledger_batch ON raw_ledger(import_batch_id);

-- 数据修改记录（含手动备注）
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    record_id  TEXT NOT NULL,
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    note       TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    -- 修改前整行快照（供修改记录页展示「发票号码/对方/金额/经办人/修改表名」）
    friendly_table TEXT DEFAULT '',
    invoice_no TEXT DEFAULT '',
    buyer      TEXT DEFAULT '',
    amount     TEXT DEFAULT '',
    handlers   TEXT DEFAULT ''
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
        # 迁移：费用类型维护顺序
        cols = [r[1] for r in conn.execute("PRAGMA table_info(expense_cat)")]
        if "sort_order" not in cols:
            conn.execute("ALTER TABLE expense_cat ADD COLUMN sort_order INTEGER DEFAULT 0")
        # 迁移：收款明细按经办人归因（历史补录/问题行修正可逐人不同收款额与日期）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(collection)")]
        if "person_name" not in cols:
            conn.execute("ALTER TABLE collection ADD COLUMN person_name TEXT NOT NULL DEFAULT ''")
        # 迁移：经办人已收覆盖（导入前确认界面可逐人直接改已收金额，存 charge_detail）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(charge_detail)")]
        if "received_override" not in cols:
            conn.execute("ALTER TABLE charge_detail ADD COLUMN received_override REAL DEFAULT NULL")
        # 迁移：行级溯源（发票台账试点）— 记录原始 sheet 名与行号，供导入校验/右键溯源
        for tbl in ("invoice", "charge_detail", "collection", "prepayment"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")]
            if "src_sheet" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN src_sheet TEXT DEFAULT ''")
            if "src_row" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN src_row INTEGER DEFAULT 0")
        # 迁移：修改记录快照列（修改前整行上下文，供修改记录页展示）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(change_log)")]
        for c in ("friendly_table", "invoice_no", "buyer", "amount", "handlers"):
            if c not in cols:
                conn.execute(f"ALTER TABLE change_log ADD COLUMN {c} TEXT DEFAULT ''")
        # 迁移：修正曾错分到 problem_fix 的发票台账镜像行（应回归源文件原始 sheet）
        # 仅影响历史遗留数据；新导入已按源 sheet 归类，不会再产生 problem_fix 行。
        conn.execute(
            "UPDATE raw_ledger SET sheet_key = sheet_name "
            "WHERE sheet_key = 'problem_fix' "
            "AND sheet_name IN ('sheet1','sheet2','sheet3','sheet4')"
        )
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
