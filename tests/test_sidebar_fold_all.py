"""侧边栏「全部折叠」按钮单元测试（离屏 Qt）。

覆盖：
- 按钮存在 / 非 checkable / tooltip 说明「收起所有展开的分组」/ 复用 #groupToggle QSS；
- 点击 → 所有分组子项折叠、状态写进 QSettings；
- 不改变 `_collapsed`（侧边栏整体收起状态）；
- 单向：再点一次不会反向展开；整体收起（rail）时按钮隐藏。

运行：python tests/test_sidebar_fold_all.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui.sidebar import SidebarWidget  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


NAV = [
    ("业务", [("staff", "员工管理"), ("invoice", "发票管理")]),
    ("财务", [("calc", "业务计算"), ("expense", "费用台账")]),
]


def main() -> int:
    # QSettings 是跨进程持久的：先清掉上一次运行留下的状态，保证起始态可预测
    _pre = SidebarWidget(NAV)
    for key in ("collapsed_groups", "pinned", "collapsed"):
        _pre._settings.remove(key)
    _pre._settings.sync()
    _pre.close()

    sb = SidebarWidget(NAV)
    sb.set_collapsed(False, animate=False)
    sb.apply_motion_pref()          # 关动画立即落位，避免断言读到中间态
    app.processEvents()

    titles = sb._group_titles
    check("分组标题 2 个", titles == ["业务", "财务"], f"got={titles}")

    # ===== 1. 按钮本体 =====
    btn = sb.fold_all_btn
    check("按钮存在", btn is not None)
    check("按钮非 checkable（不继承 pin_btn 语义）", btn.isCheckable() is False)
    check("tooltip 说明收起所有展开的分组",
          "收起所有展开的分组" in btn.toolTip(), f"got={btn.toolTip()!r}")
    check("复用 #groupToggle 样式", btn.objectName() == "groupToggle", f"got={btn.objectName()!r}")
    check("与 pin_btn 同属顶部按钮区", btn.parentWidget() is sb.pin_btn.parentWidget())

    # ===== 2. 点击前：默认全展开 =====
    sb.apply_motion_pref()  # 关动画立即落位
    check("点击前 全部展开", all(sb._panels[t].is_open() for t in titles),
          f"got={[sb._panels[t].is_open() for t in titles]}")

    # ===== 3. 点击 → 全部折叠 + 持久化 =====
    btn.click()
    app.processEvents()
    check("点击后 全部折叠", all(not sb._panels[t].is_open() for t in titles),
          f"got={[sb._panels[t].is_open() for t in titles]}")
    check("点击后 _folded 全为 True", all(sb._folded[t] for t in titles),
          f"got={sb._folded}")
    check("点击后 标题折叠态写进 QSettings",
          sb._settings.value("collapsed_groups", "", type=str)
          == "|".join(titles),
          f"got={sb._settings.value('collapsed_groups', '', type=str)!r}")

    # ===== 4. 不影响侧边栏整体收起（_collapsed / railWidth）=====
    check("整体收起状态未被改动", sb._collapsed is False, f"got={sb._collapsed}")
    check("整体展开宽度不变", sb.width() >= 200, f"got={sb.width()}")
    check("rail 折叠未被联动", btn.isHidden() is False)

    # ===== 5. 单向：再点一次仍是折叠（不提供「全部展开」）=====
    btn.click()
    app.processEvents()
    check("二次点击 仍为折叠", all(not sb._panels[t].is_open() for t in titles),
          f"got={[sb._panels[t].is_open() for t in titles]}")

    # ===== 6. 整体收起（rail）时按钮隐藏，与 pin_btn 一致 =====
    sb.set_collapsed(True, animate=False)
    app.processEvents()
    check("rail 收起时按钮隐藏", btn.isHidden() is True)
    check("rail 收起时 pin_btn 同样隐藏", sb.pin_btn.isHidden() is True)

    sb.close()
    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
