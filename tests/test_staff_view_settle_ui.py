"""员工类型页「两条线」交互单元测试（离屏 Qt）—— 覆盖 2a/2b/3。

覆盖：
- 表头 7 列：类型 / 开票 / 报销 / 业务金额方式 / 说明 / 人数 / 角色；
- 取消勾选「开票」→ 下拉立即留空 + 灰显 + 内置类型名去粗，但库中 net_basis 按原样保留；
- 取消勾选后刷新（=重新打开页面）不得被写回（is_invoice 不回弹、口径不清）；
- 重新勾选「开票」→ 下拉回填库中保留的原口径 + 类型名恢复加粗（不得退化成默认口径）。

运行：python tests/test_staff_view_settle_ui.py
（需 PySide6，门禁在本机 venv 跑；沙箱只做语法校验。）
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="staff_view_ui_")) / "ui.db"

import app.db as appdb  # noqa: E402

appdb.DB_PATH = TMP

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.engine import staff_type as st  # noqa: E402
import app.ui.staff_view as sv  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def row_of(view, name) -> int:
    for r in range(view.type_table.rowCount()):
        it = view.type_table.item(r, 0)
        if it is not None and it.text() == name:
            return r
    return -1


def main() -> int:
    # 独立库：建表 + 跑迁移（与真实启动同一条路径，顺带验证两线列补齐 + is_settle 已删）
    appdb.init_db(backfill=False)
    conn = sqlite3.connect(TMP)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.commit()   # is_invoice / can_expense / net_basis 等由 init_db 的迁移块补齐
    conn.close()

    # 造一个"内置 + 已进业务线"的类型，等价于用户勾选「开票」后的状态
    st.add_type("合伙", conn=appdb.get_conn())
    c = appdb.get_conn()
    c.execute("UPDATE staff_type_def SET is_builtin=1 WHERE name='合伙'")
    c.execute("UPDATE staff_type_def SET is_invoice=1, net_basis='开票净额' WHERE name='合伙'")
    c.commit()
    c.close()

    view = sv.StaffView()
    view.resize(1100, 700)
    view.refresh()
    app.processEvents()

    # ===== 1. 表头：7 列（两条线：开票 / 报销）=====
    hdr = [view.type_table.horizontalHeaderItem(i).text()
           for i in range(view.type_table.columnCount())]
    check("员工类型 7 列表头",
          hdr == ["类型", "开票", "报销", "业务金额方式", "说明", "人数", "角色"], f"got={hdr}")

    # ===== 2. 初始状态：开票勾选 + 口径下拉显示库中值 + 内置名加粗 =====
    r = row_of(view, "合伙")
    check("找到 合伙 行", r >= 0, f"row={r}")
    check("合伙 初始开票勾选", view.type_table.item(r, 1).checkState() == Qt.CheckState.Checked)
    combo = view.type_table.cellWidget(r, 3)
    check("下拉是 QComboBox", isinstance(combo, QComboBox))
    check("下拉枚举未改（开票净额/收款净额）",
          [combo.itemText(i) for i in range(combo.count())] == ["开票净额", "收款净额"],
          f"got={[combo.itemText(i) for i in range(combo.count())]}")
    check("下拉显示库中口径", combo.currentText() == "开票净额", f"got={combo.currentText()!r}")
    check("初始下拉可用", combo.isEnabled() is True)
    check("内置参与行 类型名加粗", view.type_table.item(r, 0).font().bold() is True)

    # ===== 3. 取消勾选「开票」（点击路径）：下拉留空+灰显，但库中口径按原样保留 =====
    view.type_table.item(r, 1).setCheckState(Qt.CheckState.Unchecked)
    app.processEvents()
    db = st.get_type("合伙")
    # 「显示留空」只作用于页面；库里的 net_basis 必须原样保留，否则重新勾选会退化成默认口径
    check("取消勾选 → 库中 net_basis 保留原值", db["net_basis"] == "开票净额",
          f"got={db['net_basis']!r}")
    check("取消勾选 → 库中 is_invoice=0", db["is_invoice"] == 0, f"got={db['is_invoice']}")
    combo = view.type_table.cellWidget(r, 3)
    check("取消勾选 → 下拉立即留空", combo.currentText() == "", f"got={combo.currentText()!r}")
    check("取消勾选 → 下拉灰显", combo.isEnabled() is False)
    check("取消勾选 → 内置名去粗", view.type_table.item(r, 0).font().bold() is False)

    # ===== 4. 刷新（=重新打开页面）不得写回 =====
    view.refresh()
    app.processEvents()
    check("刷新后仍是未勾选", view.type_table.item(r, 1).checkState() == Qt.CheckState.Unchecked)
    combo = view.type_table.cellWidget(r, 3)
    check("刷新后下拉仍留空", combo.currentText() == "", f"got={combo.currentText()!r}")
    check("刷新后下拉仍灰显", combo.isEnabled() is False)
    check("刷新后未加粗", view.type_table.item(r, 0).font().bold() is False)
    check("刷新后库中口径仍保留", st.get_type("合伙")["net_basis"] == "开票净额",
          f"got={st.get_type('合伙')['net_basis']!r}")

    # ===== 5. 重新勾选「开票」：回填库中保留的原口径（恢复，不是填默认）+ 恢复加粗 =====
    view.type_table.item(r, 1).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    combo = view.type_table.cellWidget(r, 3)
    check("重新勾选 → 下拉可用", combo.isEnabled() is True)
    check("重新勾选 → 下拉回填原口径", combo.currentText() == "开票净额",
          f"got={combo.currentText()!r}")
    check("重新勾选 → 库中口径仍是开票净额（未被默认口径覆盖）",
          st.get_type("合伙")["net_basis"] == "开票净额",
          f"got={st.get_type('合伙')['net_basis']!r}")
    check("重新勾选 → 读数回到 ( True, 开票净额 )", st.business_flags_of("合伙") == (True, "开票净额"),
          f"got={st.business_flags_of('合伙')}")
    check("重新勾选 → 类型名恢复加粗", view.type_table.item(r, 0).font().bold() is True)

    view.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
