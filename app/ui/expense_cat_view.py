"""费用类型页：5 张分类卡片 + 类内/跨类拖拽

交互口径（已与需求方确认）：
- 分类固定 5 类（报酬发放/住房公积金/保险费/汽油费/其他），**说明文字可自定义填写**。
- 每张卡片列出该类下的费用类型，支持：
  · 类内拖动排序、跨卡片拖动搬运（改归类）
  · 上移/下移按钮做精确定位兜底（长列表拖拽不好定位）
  · 类内新增、改名（双击）、删除、移动到其它分类
- 顺序与归类会写入 expense_cat.sort_order，直接影响结算表/年度聘用结算表的费用列序。
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QLayout,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from app.ui.widgets import CaptionLabel, PageHeader, PushButton
from app.db import get_conn
from app.engine.change_log import log_change
from app.engine import expense_cat as ec


# ---------------------------------------------------------------------------
# 可拖拽的类型列表（类内排序 / 跨卡片搬运）
# ---------------------------------------------------------------------------

class TypeListWidget(QListWidget):
    """费用类型列表：支持类内重排与跨卡片搬运。

    Qt 的 InternalMove 只允许同一 widget 内移动，跨 widget 必须自己接管 dropEvent：
    从 source 取走条目、按落点插入本列表，最后统一落库。
    """

    def __init__(self, category: str, view: "ExpenseCatView", parent=None) -> None:
        super().__init__(parent)
        self.category = category
        self._view = view
        self._dragging: List[str] = []
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSpacing(1)
        self.setUniformItemSizes(True)
        self.itemDoubleClicked.connect(self._on_rename)

    # -- 取值 -----------------------------------------------------------
    def type_names(self) -> List[str]:
        return [self.item(i).text() for i in range(self.count())]

    def set_types(self, names: List[str]) -> None:
        self.blockSignals(True)
        self.clear()
        for n in names:
            self.addItem(QListWidgetItem(n))
        self.blockSignals(False)

    # -- 拖拽 -----------------------------------------------------------
    def startDrag(self, supportedActions) -> None:  # noqa: N802
        self._dragging = [self.item(i).text() for i in range(self.count())
                          if self.item(i).isSelected()]
        super().startDrag(supportedActions)

    def _drop_row(self, event) -> int:
        idx = self.indexAt(event.position().toPoint())
        if not idx.isValid():
            return self.count()
        row = idx.row()
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem:
            row += 1
        return row

    def dropEvent(self, event) -> None:  # noqa: N802
        src = event.source()
        if not isinstance(src, TypeListWidget):
            event.ignore()
            return
        names = [n for n in getattr(src, "_dragging", []) or [] if n]
        if not names:
            event.ignore()
            return
        self.apply_drop(names, src, self._drop_row(event))
        event.acceptProposedAction()

    def apply_drop(self, names: List[str], src: "TypeListWidget", drop_row: int) -> None:
        """把 names 从 src 搬到本列表的 drop_row 处（src is self 即类内重排）。"""
        if not names:
            return
        moving = set(names)

        if src is self:
            current = self.type_names()
            if drop_row < 0:
                drop_row = len(current)
            removed_before = sum(1 for t in current[:drop_row] if t in moving)
            remaining = [t for t in current if t not in moving]
            insert_at = max(0, min(drop_row - removed_before, len(remaining)))
            for k, n in enumerate(names):
                remaining.insert(insert_at + k, n)
            self.set_types(remaining)
        else:
            src.set_types([t for t in src.type_names() if t not in moving])
            current = self.type_names()
            insert_at = max(0, min(drop_row, len(current)))
            for k, n in enumerate(names):
                current.insert(insert_at + k, n)
            self.set_types(current)
        for n in names:
            self._select(n)
        self._view.persist()

    def _select(self, name: str) -> None:
        for i in range(self.count()):
            if self.item(i).text() == name:
                self.item(i).setSelected(True)

    # -- 改名 -----------------------------------------------------------
    def _on_rename(self, item: QListWidgetItem) -> None:
        old = item.text()
        new, ok = QInputDialog.getText(self, "改名费用类型", "新的费用类型名称：", text=old)
        if not ok:
            return
        new = new.strip()
        if not new or new == old:
            return
        self._view.rename_type(old, new)


# ---------------------------------------------------------------------------
# 分类卡片
# ---------------------------------------------------------------------------

class CategoryCard(QFrame):
    def __init__(self, name: str, note: str, view: "ExpenseCatView") -> None:
        super().__init__()
        self.name = name
        self._view = view
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel(name)
        self.title.setObjectName("cardTitle")
        self.count = CaptionLabel("0")
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addStretch()
        btn_note = PushButton("说明")
        btn_note.setObjectName("cardBtn")      # 与底部操作按钮一致：紧凑样式 + 不裁字
        btn_note.setFixedHeight(24)
        # 按文字宽度设最小宽：窄卡片（窗口被压窄时）也不裁字
        fm = btn_note.fontMetrics()
        btn_note.setMinimumWidth(fm.horizontalAdvance("说明") + 26)
        btn_note.clicked.connect(self._edit_note)
        head.addWidget(btn_note)
        lay.addLayout(head)

        self.note_label = CaptionLabel(note or "（点击「说明」填写本类的口径说明）")
        self.note_label.setWordWrap(True)
        if note:
            self.note_label.setToolTip(note)
        lay.addWidget(self.note_label)

        self.list = TypeListWidget(name, view)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.list, 1)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        b_add = PushButton("新增")
        b_add.clicked.connect(self._add)
        b_up = PushButton("上移")
        b_up.setToolTip("在本类内向上移一位")
        b_up.clicked.connect(lambda: self._move(-1))
        b_down = PushButton("下移")
        b_down.setToolTip("在本类内向下移一位")
        b_down.clicked.connect(lambda: self._move(1))
        # 「移动」而非「移动到」：少一字，五个按钮一行时才放得下（窄窗口不裁字）
        self.b_move = PushButton("移动")
        self.b_move.setToolTip("移动到其它分类")
        menu = QMenu(self.b_move)
        for c in ec.CATEGORIES:
            if c != name:
                menu.addAction(c, lambda _=False, c=c: self._move_to(c))
        self.b_move.setMenu(menu)
        b_del = PushButton("删除")
        b_del.clicked.connect(self._delete)
        for b in (b_add, b_up, b_down, self.b_move, b_del):
            b.setObjectName("cardBtn")      # 紧凑样式，避免被压到 sizeHint 以下
            b.setFixedHeight(24)
            # 按文字宽度设最小宽：窄卡片（窗口被压窄时）也不会裁字。
            # 带菜单的「移动」多留箭头位；其余按 2 字文字 + 留白即可。
            fm = b.fontMetrics()
            extra = 40 if b.menu() is not None else 26
            b.setMinimumWidth(fm.horizontalAdvance(b.text()) + extra)
            bar.addWidget(b)
        bar.addStretch(1)                   # 富余空间留在右侧，按钮保持自身宽度
        lay.addLayout(bar)

    # -- 数据 -----------------------------------------------------------
    def set_types(self, names: List[str]) -> None:
        self.list.set_types(names)
        self.count.setText(f"{len(names)} 项")

    def set_note(self, note: str) -> None:
        txt = (note or "").strip()
        self.note_label.setText(txt or "（点击「说明」填写本类的口径说明）")
        self.note_label.setToolTip(txt)

    def selected(self) -> List[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).isSelected()]

    # -- 操作 -----------------------------------------------------------
    def _edit_note(self) -> None:
        dlg = NoteDialog(self.name, ec.get_category_note(self.name), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            note = dlg.value()
            ec.set_category_note(self.name, note)
            self.set_note(note)

    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "新增费用类型",
                                        f"在「{self.name}」下新增费用类型：")
        if not ok:
            return
        self._view.add_type(name, self.name)

    def _move(self, direction: int) -> None:
        sel = self.selected()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中要移动的费用类型")
            return
        self._view.move_in_category(sel[0], direction)

    def _move_to(self, category: str) -> None:
        sel = self.selected()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中要移动的费用类型")
            return
        self._view.move_to_category(sel, category)

    def _delete(self) -> None:
        sel = self.selected()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中要删除的费用类型")
            return
        self._view.delete_types(sel, self.name)


class NoteDialog(QDialog):
    """分类说明编辑（多行文本）。"""

    def __init__(self, category: str, note: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"说明 — {category}")
        self.resize(420, 220)
        lay = QVBoxLayout(self)
        lay.addWidget(CaptionLabel(f"「{category}」这一类的口径说明，可自定义填写（留空即可）："))
        self.edit = QTextEdit()
        self.edit.setPlainText(note or "")
        lay.addWidget(self.edit, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def value(self) -> str:
        return self.edit.toPlainText().strip()


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

class ExpenseCatView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._cards: Dict[str, CategoryCard] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "费用类型",
            "按 5 类分组维护费用类型。卡片内拖动可调整顺序，拖到别的卡片即改归类；"
            "双击类型名可改名。顺序与归类决定结算表的费用列序。",
        ))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setSpacing(12)
        # 尊重各卡片最小宽 300：窗口被压窄到放不下 3 列时，滚动而非压缩卡片
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

        foot = QHBoxLayout()
        self.hint = CaptionLabel("")
        foot.addWidget(self.hint)
        foot.addStretch()
        btn_sync = PushButton("从费用台账同步")
        btn_sync.clicked.connect(self._sync)
        foot.addWidget(btn_sync)
        btn_reset = PushButton("刷新")
        btn_reset.clicked.connect(self.refresh)
        foot.addWidget(btn_reset)
        lay.addLayout(foot)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # -- 渲染 -----------------------------------------------------------
    def refresh(self) -> None:
        ec.sync_from_ledger()
        cats = ec.list_categories()
        # 首次/分类数量变化时重建卡片
        if {c["name"] for c in cats} != set(self._cards):
            while self.grid.count():
                item = self.grid.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            self._cards = {}
            for i, c in enumerate(cats):
                card = CategoryCard(c["name"], c["note"], self)
                # 300 是按钮行（新增/上移/下移/移动/删除）不裁字的最小宽度
                card.setMinimumWidth(300)
                card.setMinimumHeight(240)
                self._cards[c["name"]] = card
                self.grid.addWidget(card, i // 3, i % 3)
            # 列可拉伸：窗口宽时卡片变宽，窄时不至于被压到最小宽度以下
            for col in range(3):
                self.grid.setColumnStretch(col, 1)
            self.grid.setRowStretch(len(cats) // 3 + 1, 1)
        by_cat = ec.types_by_category()
        for c in cats:
            card = self._cards.get(c["name"])
            if card is None:
                continue
            card.set_types(by_cat.get(c["name"], []))
            card.set_note(c["note"])
        total = sum(len(v) for v in by_cat.values())
        self.hint.setText(f"共 {total} 个费用类型，{len(cats)} 个分类")

    def _sync(self) -> None:
        ec.sync_from_ledger()
        self.refresh()
        QMessageBox.information(self, "已同步", "已从费用台账补齐缺失的费用类型。")

    # -- 落库 -----------------------------------------------------------
    def _layout_now(self) -> Dict[str, List[str]]:
        return {name: card.list.type_names() for name, card in self._cards.items()}

    def persist(self) -> None:
        """拖拽/排序后统一落库（归类 + 全局顺序）。"""
        ec.save_layout(self._layout_now())
        for name, card in self._cards.items():
            card.count.setText(f"{card.list.count()} 项")

    def add_type(self, name: str, category: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        try:
            ec.add_type(name, category)
        except ec.ExpenseCatError as exc:
            QMessageBox.warning(self, "无法新增", str(exc))
            return
        conn = get_conn()
        try:
            log_change(conn, "expense_cat", name, "expense_type", "", name, "费用类型维护")
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def rename_type(self, old: str, new: str) -> None:
        try:
            ec.rename_type(old, new)
        except ec.ExpenseCatError as exc:
            QMessageBox.warning(self, "无法改名", str(exc))
            return
        conn = get_conn()
        try:
            log_change(conn, "expense_cat", old, "expense_type", old, new, "费用类型维护")
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def delete_types(self, names: List[str], category: str) -> None:
        if not names:
            return
        if len(names) == 1:
            n = ec.type_reference_count(names[0])
            tip = (f"确定删除费用类型「{names[0]}」？")
            if n:
                tip += (f"\n\n该类型在费用台账中有 {n} 条记录。"
                        f"\n删除后这些记录仍保留，但结算表不会再单列该类型。")
        else:
            n = sum(ec.type_reference_count(x) for x in names)
            tip = f"确定删除选中的 {len(names)} 个费用类型？"
            if n:
                tip += f"\n\n这些类型在费用台账中共 {n} 条记录。"
        if QMessageBox.question(self, "删除费用类型", tip) != QMessageBox.StandardButton.Yes:
            return
        for name in names:
            ec.delete_type(name)
            conn = get_conn()
            try:
                log_change(conn, "expense_cat", name, "expense_type", name, "", "费用类型维护")
                conn.commit()
            finally:
                conn.close()
        self.refresh()

    def move_in_category(self, name: str, direction: int) -> None:
        if not ec.move_in_category(name, direction):
            return
        self.refresh()
        self._reselect(name)

    def move_to_category(self, names: List[str], category: str) -> None:
        for name in names:
            old = self._category_of(name)
            if old == category:
                continue
            ec.set_category(name, category)
            conn = get_conn()
            try:
                log_change(conn, "expense_cat", name, "category", old, category, "费用类型维护")
                conn.commit()
            finally:
                conn.close()
        self.refresh()
        self._reselect(names[-1])

    def _category_of(self, name: str) -> str:
        m = ec.get_map()
        cat = m.get(name, ec.FALLBACK_CATEGORY)
        return cat if cat in ec.CATEGORIES else ec.FALLBACK_CATEGORY

    def _reselect(self, name: str) -> None:
        for card in self._cards.values():
            for i in range(card.list.count()):
                it = card.list.item(i)
                it.setSelected(it.text() == name)
