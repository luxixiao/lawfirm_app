"""导入页（`import_view`）职工花名册导入：空类型硬拦（方案 B）+ 去掉写死的「聘用」。

这是「职工清单」的**第二个入口**：`_do_import()` 的 `ftype == "staff"` 分支。
`a0b108b` 只治了 `staff_view.import_staff()`（用了 `or ["聘用"]` + `stype or "聘用"`
两处兜底），这里**同一个毛病第二副本**：

- 原 `:360` `stype = stype or "聘用"` —— 空类型静默改写成写死的「聘用」；
- 导入前从不校验该类型是否真存在于 `staff_type_def`（全文件原本 grep 不到
  `ensure_types`）→ 员工被挂上类型表里没有的行 → `settle_flags_of()` 返回
  `(False, "")` → 判「不参与结算」→ 后续导费用台账被 `expense_validation` 逐行拒绝，
  而报错文案还误导用户去类型页找。

覆盖：R 全填正常导入 / B1 部分没填→整批拦下、三张表零变化 /
     B2 全没填→拦下、文案含人数与前 10 人（超 10 人「等 N 人」）/
     B3 类型表已有该类型 → 不覆盖既有行 / 批量与单文件两种模式的结果正确 /
     删兜底后空类型不会被写成「聘用」。

运行：python tests/test_import_view_missing_type.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="import_view_missing_")) / "ui.db"

import app.db as appdb  # noqa: E402

appdb.DB_PATH = TMP

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

import app.ui.import_view as IV  # noqa: E402

OK, FAILS = 0, []

_REAL_MSG = IV.QMessageBox
_REAL_PARSE = IV.parse_staff_file

# 文件名含「职工」→ guess_type 判为 staff（见 import_view.guess_type）
STAFF_PATH = "C:/fake/职工清单.xlsx"


class _MsgSpy:
    """替身弹窗：只记录，避免离屏下真弹模态框。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def _rec(self, kind: str, title: str, text: str) -> None:
        self.calls.append((kind, title, text))

    def warning(self, parent, title: str, text: str, *a, **k):
        self._rec("warning", title, text)
        return QMessageBox.StandardButton.Ok

    def information(self, parent, title: str, text: str, *a, **k):
        self._rec("information", title, text)
        return QMessageBox.StandardButton.Ok

    def critical(self, parent, title: str, text: str, *a, **k):
        self._rec("critical", title, text)
        return QMessageBox.StandardButton.Ok

    def question(self, parent, title: str, text: str, *a, **k):
        self._rec("question", title, text)
        return QMessageBox.StandardButton.No


MSG = _MsgSpy()


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def snap() -> tuple:
    """三张表的快照：用来证明「拦截后一张都没动」。"""
    conn = sqlite3.connect(TMP)
    try:
        n_staff = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
        n_batch = conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0]
        types = conn.execute(
            "SELECT name, is_settle, net_basis FROM staff_type_def ORDER BY name").fetchall()
        staff = conn.execute(
            "SELECT name, staff_type FROM staff ORDER BY name").fetchall()
    finally:
        conn.close()
    return (n_staff, n_batch, types, staff)


def do_import(rows: list, quiet: bool = False):
    """跑一次职工清单导入（解析打替身，其余全真：guess_type / 写库 / 日志）。"""
    IV.parse_staff_file = lambda path: (list(rows), "hash-1")  # type: ignore[assignment]
    view = IV.ImportView()
    view.resize(900, 600)
    app.processEvents()
    view._do_import(STAFF_PATH, quiet=quiet)
    app.processEvents()
    return view


def blocked_popup():
    return [c for c in MSG.calls if c[0] == "warning" and c[1] == "导入被拦下"]


def main() -> int:
    try:
        IV.QMessageBox = MSG  # type: ignore[assignment]
        appdb.init_db(backfill=False)
        appdb.get_conn().close()
        check("前置：类型表初始为空", len(snap()[2]) == 0, f"got={snap()[2]}")

        # ===== R）全部填了类型 → 正常导入，类型按需补进 staff_type_def =====
        MSG.calls.clear()
        do_import([("张三", "合伙", ""), ("李四", "聘用", "")])
        n_staff, n_batch, types, staff = snap()
        kind_names = [t[0] for t in types]
        check("R：不弹拦截框", not blocked_popup(), f"got={MSG.calls}")
        check("R：两人入库", sorted(s[0] for s in staff) == ["张三", "李四"], f"got={staff}")
        check("R：类型如实写入（不是写死的聘用）",
              dict(staff) == {"张三": "合伙", "李四": "聘用"}, f"got={dict(staff)}")
        check("R：清单里的类型按需补进类型表", kind_names == ["合伙", "聘用"],
              f"got={kind_names}")
        check("R：产生 1 条 import_batch", n_batch == 1, f"got={n_batch}")
        check("R：弹出导入成功", ("information", "导入成功") in
              [(k, t) for k, t, _x in MSG.calls], f"got={MSG.calls}")

        # ===== B1）部分人没填 → 整批拦下，三张表零变化 =====
        MSG.calls.clear()
        before = snap()
        do_import([("张三", "合伙", ""), ("李四", "", "")])
        blk = blocked_popup()
        check("B1：弹「导入被拦下」", len(blk) == 1, f"got={MSG.calls}")
        if blk:
            text = blk[0][2]
            check("B1：文案点明人数", "1 人" in text, f"got={text!r}")
            check("B1：文案点明具体姓名", "李四" in text, f"got={text!r}")
            check("B1：文案说明后果（不参与结算 + 台账逐行拒绝）",
                  "不参与结算" in text and "逐行拒绝" in text, f"got={text!r}")
            check("B1：文案提示类型来源（员工类型页新建）",
                  "员工类型" in text and "新建" in text, f"got={text!r}")
        after = snap()
        check("B1：staff 零变化", after[0] == before[0], f"{before[0]} → {after[0]}")
        check("B1：import_batch 零变化", after[1] == before[1], f"{before[1]} → {after[1]}")
        check("B1：staff_type_def 零变化", after[2] == before[2],
              f"{before[2]} → {after[2]}")
        check("B1：没被静默写成「聘用」", after[0] == before[0] and after[3] == before[3],
              f"got={after[3]}")

        # ===== B2）全部没填 → 同样拦下；超 10 人只列前 10 + 「等 N 人」 =====
        MSG.calls.clear()
        do_import([("甲", "", ""), ("乙", "", "")])
        blk2 = blocked_popup()
        check("B2：全员未填也拦", len(blk2) == 1, f"got={MSG.calls}")
        if blk2:
            check("B2：文案给的是 2 人", "2 人" in blk2[0][2], f"got={blk2[0][2]!r}")

        MSG.calls.clear()
        before12 = snap()
        do_import([(f"员工{i:02d}", "", "") for i in range(12)])
        blk12 = blocked_popup()
        check("B2-b：12 人未填 → 同样拦下", len(blk12) == 1, f"got={MSG.calls}")
        if blk12:
            mtext = blk12[0][2]
            check("B2-b：文案说 12 人", "12 人" in mtext, f"got={mtext!r}")
            check("B2-b：只列前 10 人姓名", "员工00" in mtext and "员工09" in mtext,
                  f"got={mtext!r}")
            check("B2-b：超出部分写「等 2 人」", "等 2 人" in mtext, f"got={mtext!r}")
        check("B2-b：三张表零变化", snap() == before12)

        # ===== B3）类型表已有该类型 → 不覆盖既有行（is_settle / net_basis 不动）=====
        conn = appdb.get_conn()
        conn.execute("UPDATE staff_type_def SET is_settle=1, net_basis='开票净额' "
                     "WHERE name='合伙'")
        conn.commit()
        conn.close()
        pre_types = snap()[2]
        check("B3：前置——合伙已勾选参与 + 口径=开票净额",
              dict((t[0], (t[1], t[2])) for t in pre_types)["合伙"] == (1, "开票净额"),
              f"got={pre_types}")
        MSG.calls.clear()
        do_import([("王五", "合伙", "")])
        post_types = snap()[2]
        check("B3：既有类型未被覆盖（is_settle / net_basis 保持原值）",
              dict((t[0], (t[1], t[2])) for t in post_types)["合伙"] == (1, "开票净额"),
              f"got={post_types}")

        # ===== 两种调用模式的差别（决定批量会不会误报成功）=====
        MSG.calls.clear()
        do_import([("李四", "", "")])            # 单文件模式：弹框收口，不抛
        check("模式①单文件：弹「导入被拦下」且不外抛", len(blocked_popup()) == 1,
              f"got={MSG.calls}")

        MSG.calls.clear()
        raised = False
        try:
            do_import([("李四", "", "")], quiet=True)   # 批量模式：必须抛回调用方
        except IV.MissingStaffTypeError:
            raised = True
        check("模式②批量（quiet=True）：抛 MissingStaffTypeError → 计入失败文件",
              raised)
        check("模式②批量：不弹框（quiet 语义）", MSG.calls == [], f"got={MSG.calls}")
        return 0
    finally:
        IV.QMessageBox = _REAL_MSG      # type: ignore[assignment]
        IV.parse_staff_file = _REAL_PARSE  # type: ignore[assignment]


if __name__ == "__main__":
    rc = main()
    print(f"PASS {OK} checks" if (rc == 0 and not FAILS) else
          ("FAILED:\n" + "\n".join(FAILS) if FAILS else "FAILED"))
    sys.exit(0 if (rc == 0 and not FAILS) else 1)
