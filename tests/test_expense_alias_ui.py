"""费用类型别名「内联显示」UI 单元测试（离屏）—— B+C 折中。

覆盖：
- TypeListWidget.set_types 携带别名：type_names() 仍返回**纯类型名**（数据不污染），
  别名落在自定义 role(ALIAS_ROLE)；
- alias_map() 正确映射；
- 委托 TypeAliasDelegate.controls 的几何：交互行有 × 命中框与 ＋ 框，
  非交互行两者都没有；无别名时只有 ＋；
- 委托 paint() 能在离屏 QPixmap 上跑通（顺带验证 style token 不缺失）；
- 拖拽重建列表（apply_drop）后别名不丢。

运行：python tests/test_expense_alias_ui.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem  # noqa: E402

from app.ui.expense_cat_view import (  # noqa: E402
    ALIAS_ROLE, TypeAliasDelegate, TypeListWidget)

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


class _StubView:
    """替身视图：只接住列表回调，避免碰真实 DB。"""

    def __init__(self):
        self.deleted = None
        self.added = None

    def persist(self):
        pass

    def delete_alias(self, alias, confirm=True):
        self.deleted = (alias, confirm)

    def add_alias_dialog(self, canonical):
        self.added = canonical

    def rename_type(self, old, new):
        pass


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])

    view = _StubView()
    lw = TypeListWidget("住房公积金", view)
    lw.set_types(
        ["住房公积金", "保险费", "汽油费"],
        {"住房公积金": ["公积金"], "保险费": ["社保", "商业保险"]},
    )

    # 1. 纯数据名 + 别名落在 role
    check("type_names 仍是纯类型名",
          lw.type_names() == ["住房公积金", "保险费", "汽油费"], lw.type_names())
    check("别名存 ALIAS_ROLE",
          lw.item(0).data(ALIAS_ROLE) == ["公积金"], lw.item(0).data(ALIAS_ROLE))
    check("多别名存 ALIAS_ROLE",
          lw.item(1).data(ALIAS_ROLE) == ["社保", "商业保险"], lw.item(1).data(ALIAS_ROLE))
    check("无别名项为空列表", lw.item(2).data(ALIAS_ROLE) == [])
    check("alias_map 正确",
          lw.alias_map() == {"住房公积金": ["公积金"],
                             "保险费": ["社保", "商业保险"],
                             "汽油费": []}, lw.alias_map())
    check("默认无悬停行", lw.hover_row() == -1)

    # 2. 委托几何
    d = lw.itemDelegate()
    check("使用 TypeAliasDelegate", isinstance(d, TypeAliasDelegate))
    rect = QRect(0, 0, 300, 26)
    ci = d.controls(rect, "保险费", ["社保", "商业保险"], True)
    check("交互行：2 个别名命中框", len(ci["alias_hits"]) == 2, len(ci["alias_hits"]))
    check("交互行：有 ＋ 框", ci["plus_rect"] is not None)
    check("命中框落在行内",
          all(rect.left() <= h["rect"].left() and h["rect"].right() <= rect.right()
              for h in ci["alias_hits"]), [h["rect"] for h in ci["alias_hits"]])
    check("交互行后缀含别名文本",
          any(it["text"] == "社保" for it in ci["items"]), ci["items"])
    cn = d.controls(rect, "保险费", ["社保", "商业保险"], False)
    check("非交互行：无命中框", cn["alias_hits"] == [])
    check("非交互行：无 ＋ 框", cn["plus_rect"] is None)
    ce = d.controls(rect, "汽油费", [], True)
    check("无别名：后缀为空", ce["items"] == [])
    check("无别名：仍有 ＋ 框", ce["plus_rect"] is not None)

    # 3. paint 冒烟（离屏 QPixmap；顺带验证 palette token 不缺失）
    pix = QPixmap(300, 26)
    painter = QPainter(pix)
    opt = QStyleOptionViewItem()
    opt.rect = rect
    opt.state = QStyle.StateFlag.State_None
    for r in range(lw.count()):
        d.paint(painter, opt, lw.model().index(r, 0))
    opt.state = QStyle.StateFlag.State_Selected
    d.paint(painter, opt, lw.model().index(1, 0))
    painter.end()
    check("delegate paint 跑通", True)

    # 4. 拖拽重建后别名不丢（类内重排：后移一位）
    lw.apply_drop(["住房公积金"], lw, 2)
    check("类内重排后顺序", lw.type_names() == ["保险费", "住房公积金", "汽油费"],
          lw.type_names())
    check("类内重排后别名保留",
          lw.item(1).data(ALIAS_ROLE) == ["公积金"], lw.item(1).data(ALIAS_ROLE))
    check("未移动项别名保留",
          lw.item(0).data(ALIAS_ROLE) == ["社保", "商业保险"])

    # 5. 跨卡片搬运也保留别名（在**目标**列表上调用，src=源列表）
    lw2 = TypeListWidget("保险费", view)
    lw2.set_types(["社保"], {"社保": []})
    lw2.apply_drop(["住房公积金"], lw, 0)
    check("跨卡片搬运后目标含该类型", "住房公积金" in lw2.type_names(), lw2.type_names())
    check("跨卡片搬运后别名跟过来",
          lw2.item(0).data(ALIAS_ROLE) == ["公积金"], lw2.item(0).data(ALIAS_ROLE))
    check("跨卡片搬运后源列表去掉",
          "住房公积金" not in lw.type_names(), lw.type_names())

    print(f"\n通过 {OK} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ✗", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
