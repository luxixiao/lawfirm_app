"""导入职工清单：空类型硬拦（方案 B，2026-09-25 拍板）—— 离屏 Qt，纯逻辑优先。

背景：员工类型表**初始为空**（需求方 2026-09-25 拍板），`import_staff()` 里原先有
两处「系统替用户决定」的兜底 —— `ensure_types(..., or ["聘用"])` 与
`if not stype: stype = "聘用"`。两者都属预置，已按方案 B 去掉：
- `ensure_types()` **保留但去掉 `or ["聘用"]`**：清单里如实填了「合伙」的人需要它把
  该类型建进行，否则会被挂上一个类型表里根本没有的类型 → 判「不参与结算」→
  导费用台账被逐行拒绝；
- 空类型**不再默认补「聘用」**，改为**导入前硬拦、整批拒绝、零写库**。

覆盖：R1 全部填类型 / R2 未出现过的类型自动建行且**不预置** /
     B1 部分人未填 → 拦截 + 三张表零变化 / B2 全员未填 → 同样拦截 /
     B3 拦截后不弹顺序指引且标记未置位 / B4 类型表已建好 → 不拦截不提示。

运行：python tests/test_staff_import_missing_type.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="staff_missing_type_")) / "ui.db"

import app.db as appdb  # noqa: E402

appdb.DB_PATH = TMP

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.engine import staff_type as st  # noqa: E402
import app.ui.staff_view as sv  # noqa: E402

OK, FAILS = 0, []

_REAL_MSG = sv.QMessageBox
_REAL_FILE = sv.QFileDialog
_REAL_PARSE = sv.parse_staff_file


class _MsgSpy:
    """替身弹窗：只记录调用，避免离屏下真的弹模态框。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def _rec(self, kind: str, title: str, text: str) -> None:
        self.calls.append((kind, title, text))

    def information(self, parent, title: str, text: str, *a, **k):
        self._rec("information", title, text)
        return QMessageBox.StandardButton.Ok

    def warning(self, parent, title: str, text: str, *a, **k):
        self._rec("warning", title, text)
        return QMessageBox.StandardButton.Ok

    def critical(self, parent, title: str, text: str, *a, **k):
        self._rec("critical", title, text)
        return QMessageBox.StandardButton.Cancel


class _FileSpy:
    """替身文件对话框：返回一个固定路径（解析由 `parse_staff_file` 替身接管）。"""

    def getOpenFileName(self, *a, **k):
        return ("/fake/staff.xlsx", "")


MSG = _MsgSpy()


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def snap() -> tuple:
    """三张表的快照：用于证明「拦截后零写库」。"""
    conn = sqlite3.connect(TMP)
    try:
        n_batch = conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0]
        rows = conn.execute("SELECT name, staff_type FROM staff ORDER BY name").fetchall()
        types = conn.execute("SELECT name FROM staff_type_def ORDER BY name").fetchall()
    finally:
        conn.close()
    return (n_batch, rows, types)


def do_import(rows: list):
    """走完整的 `import_staff()` 路径（文件对话框 / 解析 / 写库全部打替身）。

    返回 view：调用方要能看它的 `_settle_order_hinted` 是否被置位。
    """
    sv.parse_staff_file = lambda path: (list(rows), "hash-1")
    view = sv.StaffView()
    view.resize(900, 600)
    app.processEvents()
    view.import_staff()
    app.processEvents()
    return view


def main() -> int:
    try:
        sv.QMessageBox = MSG          # type: ignore[assignment]
        sv.QFileDialog = _FileSpy()   # type: ignore[assignment]

        appdb.init_db(backfill=False)
        appdb.get_conn().close()
        check("前置：类型表初始为空", len(st.list_types()) == 0,
              f"got={[t['name'] for t in st.list_types()]}")

        # ===== R1）全部填类型 → 正常导入，两行类型如实写入 =====
        MSG.calls.clear()
        do_import([("张三", "合伙", ""), ("李四", "聘用", "")])
        n_batch, rows, types = snap()
        kinds_r1 = [(k, t) for k, t, _x in MSG.calls]
        # 类型表此刻为空 + 全员填了类型 → 顺序指引照弹（属预期，见 B5）；
        # 这里只要求**没有拦截框**、且导入确实完成
        check("R1：不弹拦截框", not any(k == "warning" for k, _t in kinds_r1), f"got={kinds_r1}")
        check("R1：导入完成提示", ("information", "导入完成") in kinds_r1, f"got={kinds_r1}")
        check("R1：两人如实入库", sorted(r[0] for r in rows) == ["张三", "李四"], f"got={rows}")
        check("R1：类型如实写入 staff",
              dict(rows) == {"张三": "合伙", "李四": "聘用"}, f"got={dict(rows)}")
        check("R1：类型表建出清单里出现过的两个类型",
              [t[0] for t in types] == ["合伙", "聘用"], f"got={[t[0] for t in types]}")
        check("R1：产生了 1 条 import_batch", n_batch == 1, f"got={n_batch}")

        # ===== R2）未出现过的类型自动建行；绝不预置清单里没有的类型 =====
        MSG.calls.clear()
        before_types = [t[0] for t in snap()[2]]
        do_import([("王五", "挂靠", "")])
        after_types = [t[0] for t in snap()[2]]
        check("R2：清单里的新类型自动建行", "挂靠" in after_types, f"got={after_types}")
        check("R2：不预置清单里没出现过的类型（无凭空多出的聘用等）",
              set(after_types) - set(before_types) == {"挂靠"},
              f"新增={set(after_types) - set(before_types)}")

        # ===== B1）部分人未填类型 → 拦截 + 三张表零变化 =====
        MSG.calls.clear()
        base = snap()
        do_import([("张三", "合伙", ""), ("李四", "", "")])
        blocked = [c for c in MSG.calls if c[0] == "warning" and c[1] == "导入被拦下"]
        check("B1：弹「导入被拦下」", len(blocked) == 1, f"got={MSG.calls}")
        if blocked:
            _kind, _title, text = blocked[0]
            check("B1：文案点明人数", "1 人" in text, f"got={text!r}")
            check("B1：文案点明具体姓名", "李四" in text, f"got={text!r}")
            check("B1：文案说明后果（不参与结算 + 台账逐行拒绝）",
                  "不参与结算" in text and "逐行拒绝" in text, f"got={text!r}")
            check("B1：文案提示类型从哪来（员工类型页新建）",
                  "员工类型" in text and "新建" in text, f"got={text!r}")
        after = snap()
        check("B1：staff 零变化", after[1] == base[1], f"{base[1]} → {after[1]}")
        check("B1：import_batch 零变化", after[0] == base[0], f"{base[0]} → {after[0]}")
        check("B1：staff_type_def 零变化", after[2] == base[2], f"{base[2]} → {after[2]}")

        # ===== B2）全员未填 → 同样拦截（复数分支） =====
        MSG.calls.clear()
        before2 = snap()
        do_import([("甲", "", ""), ("乙", "", ""), ("丙", "", "")])
        blocked2 = [c for c in MSG.calls if c[0] == "warning" and c[1] == "导入被拦下"]
        check("B2：全员未填也拦", len(blocked2) == 1, f"got={MSG.calls}")
        if blocked2:
            check("B2：文案给的是 3 人", "3 人" in blocked2[0][2], f"got={blocked2[0][2]!r}")
        check("B2：三张表零变化", snap() == before2)

        # 超出 10 人 → 只列前 10 个 + 「等 N 人」
        MSG.calls.clear()
        many = [(f"员工{i:02d}", "", "") for i in range(12)]
        do_import(many)
        m_blocked = [c for c in MSG.calls if c[0] == "warning" and c[1] == "导入被拦下"]
        check("B2-b：12 人未填 → 同样拦截", len(m_blocked) == 1, f"got={MSG.calls}")
        if m_blocked:
            mtext = m_blocked[0][2]
            check("B2-b：文案说 12 人", "12 人" in mtext, f"got={mtext!r}")
            check("B2-b：只列前 10 个姓名", "员工00" in mtext and "员工09" in mtext,
                  f"got={mtext!r}")
            check("B2-b：超出部分写成「等 2 人」", "等 2 人" in mtext, f"got={mtext!r}")

        # ===== B3）拦截后不弹顺序指引，且标记未置位（下一轮还能提示） =====
        MSG.calls.clear()
        before3 = snap()
        view3 = do_import([("李四", "", "")])
        kinds = [(k, t) for k, t, _x in MSG.calls]
        check("B3：只弹拦截框（顺序指引未弹）",
              kinds == [("warning", "导入被拦下")], f"got={kinds}")
        check("B3：拦截即 return → 顺序指引标记未被置位（下一轮还能正常提示）",
              view3._settle_order_hinted is False, f"got={view3._settle_order_hinted!r}")

        # ===== B4）类型表已建好 → 不拦截、不提示，导入照常 =====
        MSG.calls.clear()
        do_import([("李四", "聘用", "")])
        kinds4 = [(k, t) for k, t, _x in MSG.calls]
        check("B4：类型表非空 + 清单填了类型 → 不拦截（无 warning）",
              not any(k == "warning" for k, _t in kinds4), f"got={kinds4}")
        check("B4：导入成功提示", ("information", "导入完成") in kinds4, f"got={kinds4}")

        # 顺序指引与硬拦互斥的边界：类型表为空 + 全员填类型 → 提示照弹、导入照常
        MSG.calls.clear()
        c = appdb.get_conn()
        c.execute("DELETE FROM staff_type_def")
        c.commit()
        c.close()
        check("B5：清空类型表", len(st.list_types()) == 0,
              f"got={[t['name'] for t in st.list_types()]}")
        do_import([("李四", "聘用", "")])
        kinds5 = [(k, t) for k, t, _x in MSG.calls]
        check("B5：类型表空 + 全员填类型 → 弹顺序指引",
              ("information", "导入顺序提示") in kinds5, f"got={kinds5}")
        check("B5：且能正常导入（不拦截）", ("information", "导入完成") in kinds5,
              f"got={kinds5}")
        return 0
    finally:
        sv.QMessageBox = _REAL_MSG      # type: ignore[assignment]
        sv.QFileDialog = _REAL_FILE     # type: ignore[assignment]
        sv.parse_staff_file = _REAL_PARSE  # type: ignore[assignment]


if __name__ == "__main__":
    rc = main()
    print(f"PASS {OK} checks" if (rc == 0 and not FAILS) else
          ("FAILED:\n" + "\n".join(FAILS) if FAILS else "FAILED"))
    sys.exit(0 if (rc == 0 and not FAILS) else 1)
