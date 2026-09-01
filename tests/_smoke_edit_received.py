"""回归：逐经办人已收编辑弹窗必须可编辑（修复 NoEditTriggers 导致无法编辑）。

复现：双击「各经办人已收」列（或点右侧「编辑各经办人已收」）弹出 HandlerReceivedDialog，
但原代码 `setEditTriggers(NoEditTriggers)` 把整张表锁死，金额单元格双击也进不了编辑态。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.ui.preview_dialog import HandlerReceivedDialog


def main():
    app = QApplication.instance() or QApplication([])

    ev = {
        "invoice_no": "25332000000406956789",
        "handlers": [("周立生", 1000.0), ("陈娟", 500.0)],
        "system_received": {"周立生": 0.0, "陈娟": 0.0},
        "total_amount": 1500.0,
    }
    dlg = HandlerReceivedDialog(ev, {ev["invoice_no"]: {}}, None)

    # 1) 表格允许双击进入编辑（核心修复点：不再是无编辑触发）
    trig = dlg.table.editTriggers()
    assert trig != dlg.table.EditTrigger.NoEditTriggers, f"editTriggers 仍为 NoEditTriggers={trig}"
    assert trig & dlg.table.EditTrigger.DoubleClicked, f"editTriggers 不含 DoubleClicked={trig}"

    # 2) 金额列可编辑、姓名列只读（防止误改经办人）
    amt_item = dlg.table.item(0, 1)
    name_item = dlg.table.item(0, 0)
    assert amt_item.flags() & Qt.ItemFlag.ItemIsEditable, "金额列应可编辑"
    assert not (name_item.flags() & Qt.ItemFlag.ItemIsEditable), "姓名列应只读"

    # 3) 默认带入系统预填值（此处为 0.00）
    assert amt_item.text() == "0.00", amt_item.text()

    print("OK 弹窗允许双击编辑；金额列可编辑、姓名列只读")
    print("ALL PASS")


if __name__ == "__main__":
    main()
