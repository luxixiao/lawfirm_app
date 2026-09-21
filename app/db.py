"""数据库连接、建表、WAL 管理"""
from __future__ import annotations

import json
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

-- 工资表原始镜表：工资文档 Excel 逐 sheet 逐行 1:1 镜像（不归一化，保留所有原始列）。
-- 三个 sheet 列结构不同，故取并集存列：分成报酬/工资/预发经营所得 各自成列，
-- 每行只填自己 sheet 对应的列（其余留空），以最大程度复刻源表格原貌。
-- 账期 period 由 import_batch 带出（来自文件名，如 25.1 -> 2025-01）。
CREATE TABLE IF NOT EXISTS raw_salary (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_key         TEXT NOT NULL DEFAULT '',   -- lawyer / partner / logistics
    sheet_name        TEXT NOT NULL DEFAULT '',   -- 聘用律师 / 合伙人 / 后勤 (实)
    block_no          INTEGER NOT NULL DEFAULT 1, -- 同 sheet 内第几个数据块（聘用律师含分成报酬块与工资块）
    row_no            INTEGER NOT NULL DEFAULT 0,
    item_type         TEXT NOT NULL DEFAULT '',   -- 金额项目：分成报酬 / 工资 / 预发经营所得
    seq               TEXT,                        -- 编号
    staff_name        TEXT,                        -- 姓名（已去对齐空格，如「傅  强」->「傅强」）
    share_raw         TEXT, share_num    REAL,     -- 分成报酬
    salary_raw        TEXT, salary_num   REAL,     -- 工资
    partner_raw       TEXT, partner_num  REAL,     -- 预发经营所得
    tax_raw           TEXT, tax_num      REAL,     -- 代扣个所税
    fund_raw          TEXT, fund_num     REAL,     -- 代扣公积金
    net_raw           TEXT, net_num      REAL,     -- 实发金额
    pension_raw       TEXT,                        -- 养（混合类型：数值或文本如「退休」）
    medical_raw       TEXT,                        -- 医疗
    unemployment_raw  TEXT,                        -- 失业
    remark            TEXT,
    import_batch_id   INTEGER,
    created_at        TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_raw_salary_sheet ON raw_salary(sheet_key);
CREATE INDEX IF NOT EXISTS idx_raw_salary_batch ON raw_salary(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_raw_salary_name  ON raw_salary(staff_name);

-- 导入日志（持久化：程序关闭后仍可查看历史导入记录，不再随会话清空）
CREATE TABLE IF NOT EXISTS import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT NOT NULL,                    -- YYYY-MM-DD HH:MM:SS
    file_name   TEXT NOT NULL DEFAULT '',
    batch_type  TEXT NOT NULL DEFAULT '',         -- invoice/ledger/expense/staff/salary
    period      TEXT NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL DEFAULT 1,       -- 1=成功 0=失败
    message     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_import_log_at ON import_log(imported_at);

-- 个税申报数据（每年 11 月导出的「1-11 月累计」申报表，一年一份）。
-- 税局导出表格式每年可能变（增列/减列/换序），故采用**弹性取值**：
--   核心字段按税局「字段编号」映射（编号体系稳定），未知/新增列存入 extra_json，缺列留空。
CREATE TABLE IF NOT EXISTS tax_declaration (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    year        TEXT NOT NULL,                    -- 申报年份（每年一份），如 "2025"
    seq         TEXT,                              -- 1  序号
    staff_name  TEXT,                              -- 2  姓名
    -- 累计情况（税局编号 22 / 23 / 24）
    income            REAL DEFAULT 0,              -- 22 累计收入额
    basic_deduction   REAL DEFAULT 0,              -- 23 累计减除费用
    special_deduction REAL DEFAULT 0,              -- 24 累计专项扣除
    -- 累计专项附加扣除（25 ~ 30）
    child_edu    REAL DEFAULT 0,                   -- 25 子女教育
    elderly      REAL DEFAULT 0,                   -- 26 赡养老人
    housing_loan REAL DEFAULT 0,                   -- 27 住房贷款利息
    housing_rent REAL DEFAULT 0,                   -- 28 住房租金
    education    REAL DEFAULT 0,                   -- 29 继续教育
    infant       REAL DEFAULT 0,                   -- 30 3岁以下婴幼儿照护
    -- 税款计算（35 ~ 41）
    taxable      REAL DEFAULT 0,                   -- 35 应纳税所得额
    tax_rate     TEXT,                             -- 36 税率/预扣率（原文，可能是 "-"）
    quick_ded    REAL DEFAULT 0,                   -- 37 速算扣除数
    payable      REAL DEFAULT 0,                   -- 38 应纳税额
    relief       REAL DEFAULT 0,                   -- 39 减免税额
    paid         REAL DEFAULT 0,                   -- 40 已缴税额
    refill       REAL DEFAULT 0,                   -- 41 应补/退税额
    net_paid     REAL DEFAULT 0,                   -- （无编号列）实际已纳税额
    extra_json   TEXT,                             -- 未知/新增列 {"列名": 原始值}
    file_name    TEXT NOT NULL DEFAULT '',
    imported_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_tax_decl_year ON tax_declaration(year);
CREATE INDEX IF NOT EXISTS idx_tax_decl_name ON tax_declaration(staff_name);

-- 费用扣除（每年一份「1-12 月」个税费用扣除情况）。
-- 与个税申报表不同：该表**没有税局字段编号行**，故以**列名关键词**为主锚点匹配；
-- 未知/新增列同样存入 extra_json，缺列留空，重名列自动加序号，格式变动不影响导入。
CREATE TABLE IF NOT EXISTS tax_deduction (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    year        TEXT NOT NULL,                    -- 年份（一年一份），如 "2025"
    staff_name  TEXT,                              -- 姓名
    basic_deduction   REAL DEFAULT 0,              -- 累计减除费用
    special_deduction REAL DEFAULT 0,              -- 累计专项扣除
    additional_total  REAL DEFAULT 0,              -- 累计专项附加扣除（合计列）
    child_edu     REAL DEFAULT 0,                  -- 累计子女教育支出扣除
    education     REAL DEFAULT 0,                  -- 累计继续教育支出扣除
    housing_loan  REAL DEFAULT 0,                  -- 累计住房贷款利息支出扣除
    housing_rent  REAL DEFAULT 0,                  -- 累计住房租金支出扣除
    elderly       REAL DEFAULT 0,                  -- 累计赡养老人支出扣除
    infant        REAL DEFAULT 0,                  -- 累计3岁以下婴幼儿照护
    pension       REAL DEFAULT 0,                  -- 累计个人养老金
    other_deduction REAL DEFAULT 0,                -- 累计其他扣除
    donation      REAL DEFAULT 0,                  -- 累计准予扣除的捐赠
    other_income  REAL DEFAULT 0,                  -- 其他单位累计收入
    other_deduct  REAL DEFAULT 0,                  -- 其他单位累计扣除
    other_relief  REAL DEFAULT 0,                  -- 其他单位累计减免税额
    extra_json    TEXT,                            -- 未知/新增/重名列 {"列名": 原始值}
    file_name     TEXT NOT NULL DEFAULT '',
    imported_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_tax_ded_year ON tax_deduction(year);
CREATE INDEX IF NOT EXISTS idx_tax_ded_name ON tax_deduction(staff_name);

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

-- 收款认定快照（方案E：已收认定维度对账）：导入时按(批次,发票号)落
-- 「源声称收款」(expected_json) 与「本批次实际写入的收款」(actual_json)，
-- 供导入校验-已收认定维度做「同批次内 期望 vs 实际」对账，
-- 避免累计台账跨月覆盖 collection 导致的误报。
CREATE TABLE IF NOT EXISTS received_snapshot (
    import_batch_id INTEGER NOT NULL,
    invoice_no      TEXT NOT NULL,
    expected_json   TEXT NOT NULL DEFAULT '{}',
    actual_json     TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (import_batch_id, invoice_no)
);
CREATE INDEX IF NOT EXISTS idx_recv_snap_inv ON received_snapshot(invoice_no);

-- 费用类型维护（全集 + 归类：报酬发放/住房公积金/保险费/汽油费/其他）
CREATE TABLE IF NOT EXISTS expense_cat (
    expense_type TEXT PRIMARY KEY,
    category     TEXT NOT NULL DEFAULT '报销摊销等',
    sort_order   INTEGER NOT NULL DEFAULT 0,   -- 全局顺序 = 分类顺序 + 类内顺序
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

-- 导入校验「已确认异常」备注：手动改数据导致的正常差异，留说明防误改/忘改。
-- 维度 dim ∈ {invoice, handler, received}，按(发票号, 维度, 账期)唯一。
CREATE TABLE IF NOT EXISTS anomaly_note (
    invoice_no   TEXT NOT NULL,
    dim          TEXT NOT NULL,
    period       TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (invoice_no, dim, period)
);
CREATE INDEX IF NOT EXISTS idx_anomaly_note_period ON anomaly_note(period, dim);

-- ============ 员工类型定义（可自定义，员工管理页「员工类型」Tab 维护）============
-- 参与结算由 staff_type_def.is_settle 控制（内置三类 + 公共/行政 默认参与），
-- 净额口径由 net_basis 控制（'开票净额' | '收款净额'）；person_settlement 改用
-- staff_type.settle_flags_of 读取，不再按类型名硬编码。
-- 内置三类 is_builtin=1：禁止删除与改名（改名会断结算口径），说明可改。
-- 自定义类型（如"顾问""实习"）默认不参与，可在员工类型页自行开启参与结算。
CREATE TABLE IF NOT EXISTS staff_type_def (
    name        TEXT PRIMARY KEY,
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    note        TEXT DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- 费用分类说明（分类固定 6 类：报酬发放/住房公积金/保险费/汽油费/报销摊销等/公共专属费用；说明可自定义填写，供费用类型页分组展示）
CREATE TABLE IF NOT EXISTS expense_category (
    name        TEXT PRIMARY KEY,
    note        TEXT DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- ============ 会计科目（一级/二级，树形归属；校验费用台账科目）============
-- level=1 为一级科目（parent_id 为 NULL）；level=2 为二级科目，parent_id 指向所属一级科目 id。
-- 排序：一级按全局 sort_order；二级按 parent_id + sort_order（即「所属一级内」的顺序）。
-- 主表从空起步：不自动从费用台账 harvest；旧台账科目不参与校验（仅「账面情况」按账期汇总时如实展示）。
CREATE TABLE IF NOT EXISTS account_subject (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    level       INTEGER NOT NULL,        -- 1 = 一级科目, 2 = 二级科目
    parent_id   INTEGER,                -- 一级科目为 NULL；二级科目指向所属一级科目 id
    name        TEXT NOT NULL,
    code        TEXT DEFAULT '',        -- 科目编码（可选）
    sort_order  INTEGER NOT NULL DEFAULT 0,
    note        TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
-- 两层「同层级内名称唯一」：一级按 (level=1, name)；二级按 (parent_id, name)
CREATE UNIQUE INDEX IF NOT EXISTS idx_subject_l1 ON account_subject(level, name) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_subject_l2 ON account_subject(parent_id, name) WHERE level = 2;

-- ============ 分成计算（内嵌类 Excel 引擎，spec: calc_engine_spec.md）============
-- 表格主表：content 为 JSON 整表（方案甲）
-- {"version":1,"rows":..,"cols":..,"cells":{"r,c":{"raw":..,"kind":..}},"params":{..},"col_headers":[..]}
-- workbook_id 预埋工作簿层（本期恒为 1，日后加 calc_workbook 表无需迁数据）。
-- updated_by/updated_at：DB 在 Seafile 同步范围内，展示"最后编辑人/时间"提示后写覆盖风险。
CREATE TABLE IF NOT EXISTS calc_sheet (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workbook_id INTEGER NOT NULL DEFAULT 1,
    name        TEXT    NOT NULL,
    sheet_order INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT    DEFAULT '',
    created     TEXT    DEFAULT (datetime('now','localtime')),
    updated_at  TEXT    DEFAULT (datetime('now','localtime')),
    content     TEXT    NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_calc_sheet_name ON calc_sheet(name);
CREATE INDEX IF NOT EXISTS idx_calc_sheet_wb ON calc_sheet(workbook_id, sheet_order);

-- 自定义指标（B 层）：definition 公式模板，$职工/$年/$月 占位
-- 例：DATA($职工,"业务收入",$年,$月)*0.3
CREATE TABLE IF NOT EXISTS calc_indicator (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    definition  TEXT NOT NULL,
    note        TEXT DEFAULT '',
    updated_by  TEXT DEFAULT '',
    created     TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_calc_ind_name ON calc_indicator(name);
"""


def get_conn() -> sqlite3.Connection:
    """获取连接（WAL 模式 + 外键约束 + 行工厂）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate_expense_category_rename(conn) -> None:
    """迁移：费用分类「其他」改名为「报销摊销等」（报销/摊销等兜底分类）。幂等。

    仅影响历史库：把已存在的「其他」分类名与归到「其他」的类型一并改名；
    新库由 `ensure_categories` 直接建出「报销摊销等」，本迁移无行可改。
    """
    conn.execute("UPDATE expense_category SET name='报销摊销等' WHERE name='其他'")
    conn.execute("UPDATE expense_cat SET category='报销摊销等' WHERE category='其他'")


def init_db(backfill: bool = True) -> None:
    """建库建表（幂等）。backfill=False 时跳过收款认定快照补齐（延后到首屏后执行）。"""
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
        # 迁移：员工类型参与结算开关（staff_type_def.is_settle / net_basis）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
        if "is_settle" not in cols:
            conn.execute("ALTER TABLE staff_type_def ADD COLUMN is_settle INTEGER NOT NULL DEFAULT 0")
        if "net_basis" not in cols:
            conn.execute("ALTER TABLE staff_type_def ADD COLUMN net_basis TEXT NOT NULL DEFAULT '收款净额'")
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
        # 迁移：「停用」功能已取消 —— 离职人员仍会发生业务，用全局开关表达"不再参与"
        # 属口径错误（会连带把其所有年份的结算身份判成「其他」→ 业务收入 0）。
        # 历史停用记录统一恢复为在职；is_active 列保留但不再出现 0 值。
        conn.execute("UPDATE staff SET is_active = 1 WHERE is_active != 1")
        # 迁移：费用分类「其他」改名为「报销摊销等」（新增第 6 类「公共专属费用」由 ensure_categories 补齐）
        _migrate_expense_category_rename(conn)
        from app.engine.expense_cat import ensure_categories
        ensure_categories(conn)
        # 迁移：收款认定快照（方案E）全量回溯——对尚无快照的 active 批次重解析存档源，
        # 还原「导入时源声称收款」(expected) 与「按账期窗口过滤的累计库」(actual)。
        # 幂等：仅补齐缺快照批次；单批次失败跳过，不阻断启动。
        # backfill 默认开启；main.py 传 False 将其延后到首屏之后执行，避免阻塞启动。
        if backfill:
            _backfill_received_snapshot(conn)
        conn.commit()
    finally:
        conn.close()


def run_received_snapshot_backfill() -> None:
    """首屏渲染后再补齐收款认定快照（避免阻塞启动）。幂等，失败仅告警。"""
    conn = get_conn()
    try:
        _backfill_received_snapshot(conn)
        conn.commit()
    finally:
        conn.close()


def _backfill_received_snapshot(conn) -> None:
    """对尚无快照的 active 发票台账批次，重解析存档源补齐 received_snapshot。

    仅补齐缺失批次（幂等）；解析模块不可用或单批次解析/写入失败仅打印告警并跳过，不阻断启动。
    """
    rows = conn.execute(
        "SELECT id, period, archive_path FROM import_batch "
        "WHERE batch_type='ledger' AND status='active' "
        "AND id NOT IN (SELECT DISTINCT import_batch_id FROM received_snapshot)"
    ).fetchall()
    if not rows:
        return
    try:
        from app.importer.archive_helper import resolve_archive
        from app.importer.ledger_import import parse_ledger_file
        from app.importer.importer import compute_expected_receipts
    except Exception as e:  # noqa: BLE001
        print(f"[init_db] backfill received_snapshot skipped (import failed): {e}")
        return
    for b in rows:
        try:
            _fill_one_received_snapshot(
                conn, b, resolve_archive, parse_ledger_file, compute_expected_receipts
            )
        except Exception as e:  # noqa: BLE001
            print(f"[init_db] backfill received_snapshot skip batch {b['id']}: {e}")
    conn.commit()


def _fill_one_received_snapshot(conn, b, resolve_archive, parse_ledger_file,
                               compute_expected_receipts) -> None:
    """补齐单个批次的收款认定快照：期望来自源文件，实际来自累计库按账期窗口过滤。"""
    period = b["period"]
    archive = (b["archive_path"] or "").strip()
    if not archive:
        return  # 早期无存档文件，无法还原源期望；校验时走兼容回退
    path = resolve_archive(archive)
    if not path.exists():
        return
    parsed = parse_ledger_file(str(path), period)
    # 实际：累计库按月份窗口(<=账期)还原该批次导入时的视图（累计台账特性）
    live_act: Dict[str, List[Tuple[str, float, str]]] = {}
    for r in conn.execute(
        "SELECT invoice_no, receipt_date, amount, person_name FROM collection "
        "WHERE source='import' AND substr(receipt_date,1,7) <= ?",
        (period,),
    ):
        no = r["invoice_no"]
        ym = (r["receipt_date"] or "")[:7]
        live_act.setdefault(no, []).append((ym, r["amount"], r["person_name"] or ""))
    for inv in parsed.get("invoices", []):
        no = inv["invoice_no"]
        exp_total, exp_items = compute_expected_receipts(inv)
        act_items = live_act.get(no, [])
        act_total = sum(a for _, a, _ in act_items)
        conn.execute(
            "INSERT OR REPLACE INTO received_snapshot "
            "(import_batch_id, invoice_no, expected_json, actual_json) VALUES (?,?,?,?)",
            (b["id"], no,
             json.dumps({"total": exp_total, "items": exp_items}, ensure_ascii=False),
             json.dumps(
                 {"total": act_total,
                  "items": [{"ym": ym, "amount": a, "person": p} for ym, a, p in act_items]},
                 ensure_ascii=False)),
        )


def checkpoint() -> None:
    """WAL 合并回主库（同步软件前调用，保证 db 为单一文件）"""
    conn = get_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
