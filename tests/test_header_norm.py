"""表头空白容忍：所有「按表头名找列」的地方一律忽略空白。

回归背景（2026-09-17 用户报障「发票台账页面，对方变空了」）：
真台账 4 个 sheet 的对方列表头实际是「对  方」（中间两个空格，用于对齐排版），
而 raw_ledger 镜像取列用的是**原文精确匹配** header.index("对方") → 永远取不到 →
raw_ledger.buyer 93/93 全空 →「发票台账」页对方列整列空白。
（既有 test_raw_ledger_mirror.py 的 fixture 表头写的是「对方」无空格，所以从未复现。）

口径（用户拍板）：**忽略空格** —— 台账里有时代空格有时不带；**其他表头同样处理**，
不只是「对方」。实现：excel_reader.norm_header() 抹掉全部空白（半角/全角 U+3000/
Tab/换行），所有表头比较（find_header_row / col_index / _raw_cell / 各 importer
的列名兜底匹配）都走它。

本测试覆盖的不止「对方」，而是**每个解析器**的表头匹配点，防止回归时只修一处。

运行：python tests/test_header_norm.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

from app.db import SCHEMA  # noqa: E402
from app.importer import deduction_import as ded  # noqa: E402
from app.importer import importer as imp  # noqa: E402
from app.importer import ledger_import as led  # noqa: E402
from app.importer import salary_import as sal  # noqa: E402
from app.importer import staff_import as stf  # noqa: E402
from app.importer import tax_import as tax  # noqa: E402
from app.importer.excel_reader import col_index, find_header_row, norm_header  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


FW_SPACE = "\u3000"  # 全角空格


def main() -> int:
    # ---------- 1) norm_header 本体 ----------
    check("norm_header 半角空格", norm_header("对  方") == "对方", repr(norm_header("对  方")))
    check("norm_header 全角空格", norm_header(f"对{FW_SPACE}方") == "对方")
    check("norm_header Tab/换行", norm_header("对\t方\n") == "对方")
    check("norm_header 混合空白", norm_header(f" 累 计{FW_SPACE}收入额\t") == "累计收入额")
    check("norm_header 空值安全", norm_header(None) == "" and norm_header("") == "")
    check("norm_header 非字符串安全", norm_header(123) == "123")

    # ---------- 2) find_header_row / col_index ----------
    rows = [
        ["浙江震天律师事务所 2025年1月发票台账", "", "", "", ""],
        ["序号", "开票日期", "发票号码", "对  方", "金额", "经办人", "备注", "案号"],
    ]
    hr = find_header_row(rows, ["发票号码", "经办人"])
    check("find_header_row 命中带空格表头", hr == 1, hr)
    check("find_header_row 表头行本身含空格列仍命中", hr >= 0)
    check("find_header_row 缺列返回 -1", find_header_row(rows, ["不存在的列名"]) == -1)
    hdr = rows[1]
    check("col_index 空格式「对  方」", col_index(hdr, "对方", "购方") == 3,
          col_index(hdr, "对方", "购方"))
    check("col_index 全角空格", col_index([f"对{FW_SPACE}方", "金额"], "对方") == 0)
    check("col_index 未命中 -1", col_index(hdr, "收到日期") == -1)

    # ---------- 3) _raw_cell（镜像取列的唯一入口）----------
    row = ["2", "25.1.8", "T1", "甲公司", "1000", "张三1000", "", "(2025)A1"]
    check("_raw_cell 空格式表头取到对方", imp._raw_cell(row, hdr, "对方", "购方") == "甲公司",
          repr(imp._raw_cell(row, hdr, "对方", "购方")))
    check("_raw_cell 全角空格表头取到对方",
          imp._raw_cell(["2", "甲公司"], ["序 号", f"对{FW_SPACE}方"], "对方") == "甲公司")
    check("_raw_cell 无命中返回空", imp._raw_cell(row, hdr, "不存在的列") == "")
    check("_raw_cell 空 header/row 安全",
          imp._raw_cell(None, hdr, "对方") == "" and imp._raw_cell(row, None, "对方") == "")

    # ---------- 4) 镜像落库（bug 本体：buyer 必须有值）----------
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    item = {
        "sheet": "sheet1", "sheet_name": "已开票已入账", "row_no": 3,
        "header": hdr,  # 带空格的「对  方」
        "raw_row": list(row),
        "invoice_no": "T1", "buyer": "甲公司", "total_amount": 1000.0,
        "handlers": [("张三", 1000.0)], "handler_text": "张三1000",
        "remark_raw": "", "case_no": "(2025)A1", "is_red": False,
    }
    imp._insert_raw_ledger(conn, item, 1, "invoice")
    r = conn.execute("SELECT buyer, handler_text FROM raw_ledger WHERE invoice_no='T1'").fetchone()
    check("镜像 buyer 非空（空格式表头）", (r["buyer"] or "") == "甲公司", repr(r["buyer"]))
    check("镜像经办人仍取台账原文", r["handler_text"] == "张三1000", repr(r["handler_text"]))

    # 全角空格表头 + sheet4 预收款路径
    h4 = ["序号", f"收到日{FW_SPACE}期", "发票号码", "对  方", "金额", "经办人", "备注", "案号"]
    p = {
        "sheet": "sheet4", "sheet_name": "已入账未开票", "row_no": 2,
        "header": h4,
        "raw_row": ["1", "25.1.5", "", "丁公司", "300", "赵六", "", ""],
        "received_date": "2025-01-05", "buyer": "丁公司", "amount": 300.0,
        "person_text": "赵六", "remark": "", "case_no": "",
    }
    imp._insert_raw_ledger(conn, p, 1, "prepayment")
    rp = conn.execute("SELECT buyer, recv_date_raw FROM raw_ledger WHERE kind='prepayment'").fetchone()
    check("预收款镜像 buyer 非空（全角空格表头）", (rp["buyer"] or "") == "丁公司", repr(rp["buyer"]))
    check("预收款镜像收到日期（表头带全角空格）仍取到", rp["recv_date_raw"] == "25.1.5",
          repr(rp["recv_date_raw"]))

    # ---------- 5) ledger_import._parse_invoice_sheet（解析侧 buyer）----------
    led_rows = [
        ["序号", "开票日期", "发票号码", "对  方", "金额", "经办人", "备注", "案号"],
        ["1", "25.1.8", "L1", "戊公司", "1000", "张三1000", "", "(2025)B1"],
    ]
    l_items, l_problems = led._parse_invoice_sheet(led_rows, "sheet1", "已开票已入账", "2025-01")
    check("解析侧 buyer 取到（空格式表头）",
          bool(l_items) and l_items[0].get("buyer") == "戊公司",
          f"{len(l_items)} {l_items[0].get('buyer') if l_items else ''}")
    check("表头带空格不再产生问题行", l_problems == [], l_problems)

    # sheet4 的买方可落在「汇款」列名上
    led_s4 = [
        ["序号", "收到日期", "发票号码", "汇 款 单 位", "金额", "经办人", "备注", "案号"],
        ["1", "25.1.5", "", "己公司", "300", "赵六", "", ""],
    ]
    s4_items, s4_problems = led._parse_sheet4(led_s4, "已入账未开票", "2025-01")
    check("sheet4 汇款列（带空格）取到 buyer",
          bool(s4_items) and s4_items[0].get("buyer") == "己公司",
          f"{s4_items[0].get('buyer') if s4_items else ''} {s4_problems}")

    # ---------- 6) classify_sheets（sheet 名带空格）----------
    m = led.classify_sheets(["已开票已入账", f"已开票{FW_SPACE}未入账 ", "应收账款", "已入账未开票"])
    check("classify_sheets 容忍 sheet 名空格",
          m.get("sheet1") == "已开票已入账"
          and m.get("sheet2") == f"已开票{FW_SPACE}未入账 "
          and m.get("sheet3") == "应收账款"
          and m.get("sheet4") == "已入账未开票",
          m)

    # ---------- 7) 其他解析器的表头匹配 ----------
    # 7a 费用扣除
    ded_names = ["姓 名", "累计减除费用", f"累计专项{FW_SPACE}扣除", "子女教育", "备 注"]
    mapped, extra = ded._build_col_map(ded_names)
    check("deduction 姓名（带空格）命中", mapped.get(0) == "staff_name", mapped)
    check("deduction 专项扣除（带全角空格）命中", mapped.get(2) == "special_deduction", mapped)
    check("deduction 子女教育命中", mapped.get(3) == "child_edu", mapped)
    check("deduction 未识别列进 extra 且保留原始列名（不移空格）",
          extra.get(4) == "备 注", extra)
    check("deduction _find_header_row 容忍空格",
          ded._find_header_row([["表头"], ["姓 名", "累计减除费用"]]) == 1)

    # 7b 个税（列名兜底：无字段编号行）
    t_rows = [["序号 ", "姓 名", "累计收入额", f"住{FW_SPACE}房租金", "实际已纳税额"]]
    t_map, t_extra = tax._build_col_map(t_rows, 0, -1, 5)
    check("tax 姓名（带空格）兜底命中", t_map.get(1) == "staff_name", t_map)
    check("tax 住房租金（带全角空格）兜底命中", t_map.get(3) == "housing_rent", t_map)
    check("tax 实际已纳税额命中", t_map.get(4) == "net_paid", t_map)
    check("tax FIELD_BY_NAME_NORM 已建",
          tax._FIELD_BY_NAME_NORM.get("实际已纳税额") == "net_paid")

    # 7c 工资表
    s_cols = sal._map_columns(["编 号", "姓 名", "分成报酬", "实发 金额"])
    check("salary 编号/姓名（带空格）命中",
          s_cols.get("seq") == 0 and s_cols.get("staff_name") == 1, s_cols)
    check("salary 实发金额（带空格）命中", s_cols.get("net_raw") == 3, s_cols)
    check("salary _looks_like_header 容忍空格",
          sal._looks_like_header(["编 号", "姓 名", "分成报酬", "实发金额"]))
    check("salary _sheet_key_of 容忍 sheet 名空格",
          sal._sheet_key_of(f"合伙{FW_SPACE}人") == "partner"
          and sal._sheet_key_of("后勤 (实)") == "logistics")

    # 7d 职工清单
    check("staff _find_header_row 容忍空格",
          stf._find_header_row([["花名册"], ["姓 名", "类 型", "备注"]]) == 1)
    check("staff _find_header_row 无表头返回 -1",
          stf._find_header_row([["姓 名"], ["类 型"]]) == -1)

    # ---------- 8) 端到端：真表头 xlsx → parse_ledger_file ----------
    tmp = Path(tempfile.mkdtemp(prefix="hdrnorm_")) / "2025.1台账.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "已开票已入账"
    ws.append(["浙江震天律师事务所 2025年1月发票台账"])
    ws.append(["序号", "开票日期", "发票号码", "对  方", "金额", "经办人", "备注", "案号"])
    ws.append(["1", "25.1.8", "E1", "庚公司", "1200", "张三1200", "", "(2025)C1"])
    ws2 = wb.create_sheet("已入账未开票")
    ws2.append(["序号", "收到日期", "发票号码", "汇 款 单 位", "金额", "经办人", "备注", "案号"])
    ws2.append(["1", "25.1.5", "", "辛公司", "400", "李四", "", ""])
    wb.save(tmp)
    wb.close()

    data = led.parse_ledger_file(str(tmp), "2025-01", in_library=set())
    e_inv = data["invoices"][0] if data["invoices"] else {}
    check("端到端：解析 buyer = 庚公司", e_inv.get("buyer") == "庚公司", e_inv.get("buyer"))
    check("端到端：无问题行", data["problems"] == [], data["problems"])
    check("端到端：sheet4 buyer = 辛公司",
          bool(data["prepayments"]) and data["prepayments"][0].get("buyer") == "辛公司",
          data["prepayments"])

    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    conn2.executescript(SCHEMA)
    imp._insert_raw_ledger(conn2, e_inv, 1, "invoice")
    imp._insert_raw_ledger(conn2, data["prepayments"][0], 1, "prepayment")
    got = conn2.execute(
        "SELECT kind, buyer, handler_text FROM raw_ledger ORDER BY kind").fetchall()
    check("端到端：镜像 buyer 全部非空（0 行空）",
          all((x["buyer"] or "") for x in got),
          [(x["kind"], x["buyer"]) for x in got])
    check("端到端：镜像 buyer 值正确",
          {(x["kind"], x["buyer"]) for x in got}
          == {("invoice", "庚公司"), ("prepayment", "辛公司")},
          [(x["kind"], x["buyer"]) for x in got])

    tmp.unlink()
    conn.close()
    conn2.close()

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
