"""导入职工清单前的顺序指引单元测试（离屏 Qt，纯逻辑优先）。

背景：员工类型表已改为**初始为空**，而 `ensure_types()` 新建的类型默认
`is_settle=0`。用户若在建好类型并勾选「参与结算」之前就导入费用台账，
`expense_validation` 会把每一行都判为「经办人未参与结算」而逐行拒绝。

口径（本文件守住的）：
- 类型表为空时，**导入前**给出一次顺序提醒（非阻塞，看完可直接继续导入）；
- **只提醒、不代办**：不自动建类型、不代勾「参与结算」、不拦导入；
- 每个实例只提示一次（实例属性，不写 QSettings、不写库）；
- 类型表非空时不再打扰。

运行：python tests/test_staff_import_order_hint.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="staff_import_hint_")) / "ui.db"

import app.db as appdb  # noqa: E402

appdb.DB_PATH = TMP

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.engine import staff_type as st  # noqa: E402
import app.ui.staff_view as sv  # noqa: E402

OK, FAILS = 0, []

_REAL_MSG = sv.QMessageBox
_REAL_FILE = sv.QFileDialog


class _MsgSpy:
    """替身弹窗：只记录调用，避免离屏下 `QMessageBox.information` 真的阻塞。"""

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


# 清单里**全员填了类型**的职工行 —— 顺序指引属于「能导、但顺序不对」的场景；
# 有空类型的场景由 test_staff_import_missing_type.py（方案 B 硬拦）覆盖。
ALL_TYPED = [("张三", "合伙", ""), ("李四", "聘用", "")]


class _FileSpy:
    """替身文件对话框：返回固定路径（解析由 `parse_staff_file` 替身接管）。"""

    def getOpenFileName(self, *a, **k):
        return ("/fake/staff.xlsx", "")


MSG = _MsgSpy()


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def hints() -> list:
    """只取「导入顺序提示」这一类弹框。

    导入本身也会弹「导入完成」（information），故这类断言要按**标题过滤**，
    不能用 `MSG.calls` 的累计条数 —— 否则导入次数一变就误报。
    """
    return [c for c in MSG.calls if c[1] == "导入顺序提示"]


def titles() -> list:
    return [(k, t) for k, t, _x in MSG.calls]


def main() -> int:
    try:
        sv.QMessageBox = MSG          # type: ignore[assignment]
        sv.QFileDialog = _FileSpy()   # type: ignore[assignment]
        sv.parse_staff_file = lambda path: (list(ALL_TYPED), "hash-1")  # type: ignore[assignment]

        # ===== 0. 前置：员工类型表初始为空（提醒只在「空」时出现）=====
        appdb.init_db(backfill=False)
        appdb.get_conn().close()
        check("前置：类型表初始为空", len(st.list_types()) == 0,
              f"got={[t['name'] for t in st.list_types()]}")
        check("替换弹窗已生效", MSG.calls == [], f"got={MSG.calls}")

        # ===== 1. 空库 → 前两次导入各提示一次，且只提示一次 =====
        view = sv.StaffView()
        view.resize(900, 600)
        app.processEvents()

        # ⚠ 顺序（方案 B，2026-09-25）：硬拦 → 顺序指引。提示排在**选到文件之后**，
        #   因为空类型硬拦要先扫清单（见 import_staff）；用户取消选文件则根本走不到提示。
        view.import_staff()            # 有文件 + 全员填类型 → 提示须弹出、导入照常
        app.processEvents()
        check("空类型表 → 导入前提示一次", len(hints()) == 1, f"got={MSG.calls}")
        if hints():
            kind, title, text = hints()[0]
            check("提示走 information 弹窗", kind == "information", f"got={kind}")
            check("提示标题", title == "导入顺序提示", f"got={title!r}")
            check("提示说明先后顺序", "建议先到「员工类型」页建立类型并勾选「参与结算」"
                                  in text and "再导入职工清单" in text, f"got={text}")
            check("提示说明后果", "费用台账" in text and "逐行拒绝" in text, f"got={text}")
        check("空类型表 + 全员填类型 → 导入照常完成（提示不拦人）",
              ("information", "导入完成") in titles(), f"got={titles()}")

        view.import_staff()            # 同一实例第二次：不再打扰
        app.processEvents()
        check("同一实例只提示一次", len(hints()) == 1, f"got={MSG.calls}")

        # ===== 2. 只提醒、不代办：单独调提示（不连带导入）不得自动建类型 =====
        c = appdb.get_conn()
        c.execute("DELETE FROM staff_type_def")   # 上一步导入已造类型，这里只看「提示」本身
        c.commit()
        c.close()
        MSG.calls.clear()               # 计数按段隔离（MSG 是全程累加的替身）
        view_h = sv.StaffView()
        view_h.resize(900, 600)
        app.processEvents()
        check("提示前置：类型表已清空", len(st.list_types()) == 0,
              f"got={[t['name'] for t in st.list_types()]}")
        view_h._maybe_warn_settle_order()   # 只走提示、不导入
        app.processEvents()
        check("提示确实弹了", len(hints()) == 1, f"got={hints()}")
        check("提示不自动建类型", [t["name"] for t in st.list_types()] == [],
              f"got={[t['name'] for t in st.list_types()]}")
        view_h._maybe_warn_settle_order()
        app.processEvents()
        check("同一实例重复调用不再弹", len(hints()) == 1, f"got={hints()}")

        # ⚠ 原这里有一条 `settle_flags_of('聘用') == (False,'')` 的断言，是**空转**的：
        #   第 141 行已把类型表清空，「聘用」根本不存在，`settle_flags_of` 必然返回
        #   (False,"") —— 无论有没有「代勾」都长一个样，坏实现照样绿。
        #   为什么不能改成「先建个类型再验」：提示本身只在类型表为**空**时才弹
        #   （非空即早退），两者不可兼得。「不代勾」改由下面 2-B 段守：既有类型
        #   存在时提示早退，且不许去动那个类型的勾选。
        view_h.close()

        # ===== 2-B：类型表非空 → 提示早退，且不得改动既有类型的勾选 =====
        c = appdb.get_conn()
        c.execute("INSERT OR IGNORE INTO staff_type_def"
                  "(name, is_builtin, note, sort_order, is_settle, net_basis)"
                  " VALUES('挂靠',0,'',1,0,'收款净额')")
        c.commit()
        c.close()
        MSG.calls.clear()
        before = st.settle_flags_of("挂靠")
        check("2-B 前置：挂靠类型存在但未参与结算", before == (False, "收款净额"), f"got={before}")
        view_b = sv.StaffView()
        view_b.resize(900, 600)
        app.processEvents()
        view_b._maybe_warn_settle_order()
        app.processEvents()
        after = st.settle_flags_of("挂靠")
        check("类型非空 → 提示不弹", len(hints()) == 0, f"got={hints()}")
        check("既有类型的勾选未被改动（不代勾）", after == before, f"before={before} after={after}")
        check("既有类型未被删除（不代办）",
              "挂靠" in [t["name"] for t in st.list_types()],
              f"got={[t['name'] for t in st.list_types()]}")
        view_b.close()
        c = appdb.get_conn()
        c.execute("DELETE FROM staff_type_def WHERE name='挂靠'")
        c.commit()
        c.close()
        MSG.calls.clear()

        # ===== 3. 已建类型（含未勾选的）→ 不再提示 =====
        st.add_type("聘用", conn=appdb.get_conn())
        c = appdb.get_conn()
        c.execute("UPDATE staff_type_def SET is_settle=0 WHERE name='聘用'")
        c.commit()
        c.close()

        MSG.calls.clear()
        view2 = sv.StaffView()
        view2.resize(900, 600)
        app.processEvents()
        view2.import_staff()
        app.processEvents()
        check("类型非空 → 不提示", len(hints()) == 0, f"got={hints()}")
        check("类型非空 → 导入正常完成", ("information", "导入完成") in titles(),
              f"got={titles()}")

        # ===== 4. 库回到类型表为空 → 换个实例又提示一次 =====
        # ⚠ 导入现在真的会写 staff / staff_type_def（原先停在选文件那步），故整表清空，
        #   否则 `delete_type` 会因「还有 N 名员工属于该类型」拒绝删除。
        c = appdb.get_conn()
        c.execute("DELETE FROM staff")
        c.execute("DELETE FROM staff_type_def")
        c.commit()
        c.close()
        check("删回类型后类型表为空", len(st.list_types()) == 0,
              f"got={[t['name'] for t in st.list_types()]}")
        MSG.calls.clear()
        view3 = sv.StaffView()
        view3.resize(900, 600)
        app.processEvents()
        view3.import_staff()
        app.processEvents()
        check("新实例 + 空类型表 → 再次提示（提示是每实例一次，非全局一次）",
              len(hints()) == 1, f"got={hints()}")

        view.close()
        view2.close()
        view3.close()
        return 0
    finally:
        sv.QMessageBox = _REAL_MSG      # type: ignore[assignment]
        sv.QFileDialog = _REAL_FILE     # type: ignore[assignment]


if __name__ == "__main__":
    rc = main()
    if rc == 0 and not FAILS:
        print(f"PASS {OK} checks")
        sys.exit(0)
    print("FAILED:\n" + "\n".join(FAILS) if FAILS else "FAILED")
    sys.exit(1)
