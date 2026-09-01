"""回归：问题行修正填写「收款金额/收款日期」后，导入确认页应显示已收而非「未收款」。

复现路径：
  1) 发票台账里某行解析失败 → 进入 problems（待修正）。
  2) 在右侧 ProblemFixPanel 填经办人 + 收款金额 + 收款日期，保存。
  3) _apply_resolved 把修正结果合并进工作副本：新发票带 split_receipts、remark.receipts 为空。
  4) evaluate 必须据此算出 system_received 与 receipt_text，否则界面仍显示「未收款」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.import_confidence import evaluate


def _fixed_invoice(name, billed, recv_amt, recv_ym):
    return {
        "sheet": "sheet1",
        "sheet_name": "已开票已入账",
        "row_no": 1,
        "header": [],
        "raw_row": [],
        "invoice_no": "25332000000406956789",
        "invoice_date": "2025-02-13",
        "buyer": "测试购方",
        "total_amount": billed,
        "handlers": [(name, billed)],
        "handler_text": f"{name}{billed}",
        "remark_raw": "",
        "remark": {"receipts": [], "remaining": None, "pure_date": None,
                   "is_red_remark": False, "is_red_off": False},
        "case_no": "",
        "is_red": False,
        "split_receipts": [(name, recv_amt, recv_ym)],
    }


def main():
    inv = _fixed_invoice("周立生", 1000.0, 800.0, "2025-02")
    evs = evaluate({"invoices": [inv]}, {"周立生"})
    assert len(evs) == 1, evs
    ev = evs[0]
    assert ev["system_received"] == {"周立生": 800.0}, ev["system_received"]
    assert "800.00" in ev["receipt_text"], ev["receipt_text"]
    assert "未收款" not in ev["receipt_text"], ev["receipt_text"]
    print("OK system_received =", ev["system_received"])
    print("OK receipt_text    =", ev["receipt_text"])

    # 空 split_receipts 必须回落到原备注逻辑（无回归）
    inv2 = dict(_fixed_invoice("周立生", 1000.0, 800.0, "2025-02"))
    inv2["split_receipts"] = []
    inv2["remark"] = {"receipts": [("2025-02", 0)], "remaining": None,
                      "pure_date": None, "is_red_remark": False, "is_red_off": False}
    evs2 = evaluate({"invoices": [inv2]}, {"周立生"})
    assert evs2[0]["system_received"] == {"周立生": 1000.0}, evs2[0]["system_received"]
    print("OK 空 split_receipts 回落备注逻辑:", evs2[0]["system_received"])

    print("ALL PASS")


if __name__ == "__main__":
    main()
