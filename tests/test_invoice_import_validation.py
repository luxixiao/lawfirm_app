#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""销项文档解析期校验（A5 价税合计必填 / A6 开票日期必填 / C3 取消后跨期红字放行）。

背景（2026-09-27 用户判决）：
- A5：价税合计原「为空 → 静默记 0.0」，会把 0 元票混进库 ⇒ 改必填。
- A6：开票日期原「为空 → 静默留空串」，该票会在按月份分桶时凭空消失 ⇒ 改必填。
- C3「红字原票必须已入库」取消 ⇒ 跨期红字（原票不在本批）在解析期必须放行，
  否则整本销项会被几张跨年红字绑架（实测 2025-01 整期 86 张被 3 张跨年红字拦下）。

本测试只跑纯解析（不连库、不写库），用 openpyxl 现造最小 xlsx。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook  # noqa: E402

from app.importer.excel_reader import ImportError_  # noqa: E402
from app.importer.invoice_import import parse_invoice_file  # noqa: E402

OK, FAILS = 0, []

HEADERS = ["发票号码", "发票种类", "开票日期", "发票状态", "凭证号", "货物或应税劳务名称",
           "购方名称", "价税合计", "不含税金额", "税率", "税额", "备注"]


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


def make_xlsx(rows, tmpdir: Path, name: str = "s.xlsx") -> str:
    """rows：数据行（不含表头），每行是 list[str]，长度不足补空。"""
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(list(r) + [""] * (len(HEADERS) - len(r)))
    p = tmpdir / name
    wb.save(str(p))
    wb.close()
    return str(p)


def row(no, total, date, remark="", kind="电子普票"):
    return [no, kind, date, "正常", "", "律师费", "甲公司", total, "", "", "", remark]


def expect_block(tmpdir: Path, rows, kw: str) -> bool:
    """解析 rows，期望被拦截且异常信息含 kw；返回是否命中。"""
    path = make_xlsx(rows, tmpdir, f"blk_{abs(hash((kw, tuple(map(tuple, rows)))))}.xlsx")
    try:
        parse_invoice_file(path, "2025-01")
    except ImportError_ as e:
        return kw in str(e)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="lawfirm_inv_val_"))
    try:
        # ---------------------------------------------------------------- #
        # A5：价税合计必填
        # ---------------------------------------------------------------- #
        check("A5-1：价税合计为空 → 应被拦截",
              expect_block(tmp, [row("INV-A5", "", "2025-01-10")], "价税合计为空"))

        p = make_xlsx([row("INV-OK1", "1000.00", "2025-01-10")], tmp, "ok1.xlsx")
        invs = parse_invoice_file(p, "2025-01")
        check("A5-2：价税合计正常 → 解析出 1 张且金额正确",
              len(invs) == 1 and abs(invs[0]["total_amount"] - 1000.0) < 0.01,
              str(invs))

        # ---------------------------------------------------------------- #
        # A6：开票日期必填
        # ---------------------------------------------------------------- #
        check("A6-1：开票日期为空 → 应被拦截",
              expect_block(tmp, [row("INV-A6", "1000.00", "")], "开票日期为空"))

        p = make_xlsx([row("INV-OK2", "1000.00", "2025-01-10")], tmp, "ok2.xlsx")
        invs = parse_invoice_file(p, "2025-01")
        check("A6-2：开票日期正常 → 归一化成 2025-01-10",
              len(invs) == 1 and invs[0]["invoice_date"] == "2025-01-10",
              str(invs))

        # ---------------------------------------------------------------- #
        # C3 取消：跨期红字（原票不在本批）解析期必须放行
        # ---------------------------------------------------------------- #
        p = make_xlsx([
            row("BLUE-1", "9000.00", "2025-01-05"),
            row("RED-CROSS", "-9000.00", "2025-01-20",
                remark="被红冲蓝字数电票号码：24332000000398937755"),
        ], tmp, "cross.xlsx")
        try:
            invs = parse_invoice_file(p, "2025-01")
            ok = (len(invs) == 2
                  and invs[1]["is_red"] is True
                  and invs[1]["orig_invoice_no"] == "24332000000398937755")
            check("C3：跨期红字（原票不在本批）→ 解析期放行且带出原票号", ok, str(invs))
        except ImportError_ as e:
            check("C3：跨期红字（原票不在本批）→ 解析期放行且带出原票号", False, f"被拦: {e}")

        # ---------------------------------------------------------------- #
        # 红字缺原票号 → 仍属硬拦（C3 取消的是「须已入库」，不是「须填号」）
        # ---------------------------------------------------------------- #
        p = make_xlsx([
            row("RED-NOORIG", "-500.00", "2025-01-20", remark=""),
        ], tmp, "noorig.xlsx")
        invs = parse_invoice_file(p, "2025-01")
        check("红字未填原票号 → 解析期放行（由 assert_red_has_orig 在写库前拦）",
              len(invs) == 1 and invs[0]["orig_invoice_no"] == "", str(invs))

        # ---------------------------------------------------------------- #
        # 本月内超额红冲 → 解析期拦截（原票在本批，连库那条不管）
        # ---------------------------------------------------------------- #
        check("本月内超额红冲 → 解析期拦截",
              expect_block(tmp, [
                  row("243320000001", "1000.00", "2025-01-05"),
                  row("243320000002", "-2000.00", "2025-01-20",
                      remark="被红冲蓝字数电票号码：243320000001"),
              ], "红冲校验失败"))
    finally:
        for f in tmp.glob("*.xlsx"):
            try:
                f.unlink()
            except OSError:
                pass

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
