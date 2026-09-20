"""会计科目页：一级科目卡片 + 二级科目列表（树形归属，方案 C）。

交互口径（对齐已确认的决策）：
- 一级科目为卡片；卡片内为其二级科目列表，支持：
  · 卡内拖动排序、跨卡片拖动搬运（改归属到另一一级科目）
  · 选中二级后上移/下移（精确定位兜底）/ 移动到其它一级
  · 双击二级改名；卡片标题双击改一级名
- 一级卡片顺序由卡片头「上移/下移」调整（写入 account_subject 一级 sort_order）。
- 主表从空起步：首开为空，需手动新增或从 Excel 导入；旧费用台账不参与校验。
- 改名级联更新费用台账的 subject1/subject2；删除一级级联删其二级（仅主表配置，不动历史台账）。
- 导入费用台账时，科目校验见 importer 的 validate_ledger_subjects 接入。
"""
from __future__ import annotations

import openpyxl
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QLayout,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app.ui import scale
from app.ui.widgets import CaptionLabel, PageHeader, PushButton
from app.db import get_conn
from app.engine.change_log import log_change
from app.engine import account_subject as asub


# ---------------------------------------------------------------------------
# 二级科目列表（卡内排序 / 跨卡片搬运）
# ---------------------------------------------------------------------------

class SubjectListWidget(QListWidget):
    """二级科目列表：id 驱动，支持卡内重排与跨卡片搬运。

    与费用类型页的 TypeListWidget 同构，但拖拽落库改为按 id 调 view.persist()
    （persist 统一按 id 重建整棵树并写 parent_id + sort_order，避免名字歧义/重复）。
    """

    def __init__(self, parent_subject_id: int, view: "AccountSubjectView", parent=None) -> None:
        super().__init__(parent)
        self._parent_id = parent_subject_id
        self._view = view
        self._items: List[Tuple[int, str]] = []          # [(id, name), ...] 真源
        self._dragging_ids: List[int] = []
        self._dragging_names: List[str] = []
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
    def set_subjects(self, items: List[Dict]) -> None:
        self._items = [(it["id"], it["name"]) for it in items]
        self._render()

    def _render(self) -> None:
        self.clear()
        for sid, name in self._items:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.addItem(item)

    def subject_ids(self) -> List[int]:
        return [i for i, _ in self._items]

    def subject_names(self) -> List[str]:
        return [n for _, n in self._items]

    def selected_ids(self) -> List[int]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.count()) if self.item(i).isSelected()]

    # -- 拖拽 -----------------------------------------------------------
    def startDrag(self, supportedActions) -> None:  # noqa: N802
        self._dragging_ids = [self.item(i).data(Qt.ItemDataRole.UserRole)
                              for i in range(self.count()) if self.item(i).isSelected()]
        self._dragging_names = [self.item(i).text()
                                for i in range(self.count()) if self.item(i).isSelected()]
        super().startDrag(supportedActions)

    def _drop_row(self, event) -> int:
        idx = self.indexAt(event.position().toPoint())
        if not idx.isValid():
            return len(self._items)
        row = idx.row()
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem:
            row += 1
        return row

    def dropEvent(self, event) -> None:  # noqa: N802
        src = event.source()
        if not isinstance(src, SubjectListWidget):
            event.ignore()
            return
        ids = list(getattr(src, "_dragging_ids", []) or [])
        if not ids:
            event.ignore()
            return
        self.apply_drop(ids, src, self._drop_row(event))
        event.acceptProposedAction()

    def apply_drop(self, ids: List[int], src: "SubjectListWidget", drop_row: int) -> None:
        """把 ids 从 src 搬到本列表的 drop_row 处（src is self 即卡内重排）。"""
        if not ids:
            return
        moving = set(ids)
        if src is self:
            current = list(self._items)
            if drop_row < 0:
                drop_row = len(current)
            removed_before = sum(1 for i, n in current[:drop_row] if i in moving)
            remaining = [(i, n) for i, n in current if i not in moving]
            insert_at = max(0, min(drop_row - removed_before, len(remaining)))
            for k, mid in enumerate(ids):
                name = dict(self._items)[mid]
                remaining.insert(insert_at + k, (mid, name))
            self._items = remaining
        else:
            src._items = [(i, n) for i, n in src._items if i not in moving]
            src._render()
            current = list(self._items)
            insert_at = max(0, min(drop_row, len(current)))
            name_map = dict(src._items)
            for k, mid in enumerate(ids):
                current.insert(insert_at + k, (mid, name_map[mid]))
            self._items = current
        self._render()
        self._view.persist()

    def _on_rename(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.ItemDataRole.UserRole)
        old = item.text()
        new, ok = QInputDialog.getText(self, "改名二级科目", "新的二级科目名称：", text=old)
        if not ok:
            return
        new = new.strip()
        if not new or new == old:
            return
        self._view.rename_subject(sid, new)


# ---------------------------------------------------------------------------
# 一级科目卡片
# ---------------------------------------------------------------------------

class SubjCard(QFrame):
    def __init__(self, sid: int, name: str, view: "AccountSubjectView") -> None:
        super().__init__()
        self._id = sid
        self._view = view
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # 卡片头：一级名称（双击改名）+ 上移/下移/删除一级
        head = QHBoxLayout()
        self.title = QLabel(name)
        self.title.setObjectName("cardTitle")
        head.addWidget(self.title)
        head.addStretch()
        b_up1 = PushButton("上移")
        b_up1.setToolTip("上移这张一级科目卡片")
        b_up1.clicked.connect(lambda: self._view.move_level1(self._id, -1))
        b_down1 = PushButton("下移")
        b_down1.setToolTip("下移这张一级科目卡片")
        b_down1.clicked.connect(lambda: self._view.move_level1(self._id, 1))
        b_del1 = PushButton("删除一级")
        b_del1.setToolTip("删除该一级科目及其下全部二级（仅删主表，不动历史台账）")
        b_del1.clicked.connect(self._delete_level1)
        for b in (b_up1, b_down1, b_del1):
            b.setObjectName("cardBtn")
            b.setFixedHeight(scale.px(24))
            fm = b.fontMetrics()
            b.setMinimumWidth(fm.horizontalAdvance(b.text()) + scale.px(26))
            head.addWidget(b)
        # 双击标题改名一级
        self.title.setToolTip("双击改名一级科目")
        self.title.mouseDoubleClickEvent = lambda e: self._rename_level1()  # type: ignore[assignment]
        lay.addLayout(head)

        # 二级列表
        self.list = SubjectListWidget(self._id, view)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.list, 1)

        # 卡内操作条：二级的 新增/上移/下移/移动/删除
        bar = QHBoxLayout()
        bar.setSpacing(4)
        b_add = PushButton("+ 二级")
        b_add.clicked.connect(self._add_level2)
        b_up = PushButton("上移")
        b_up.setToolTip("上移选中的二级科目")
        b_up.clicked.connect(lambda: self._move_level2(-1))
        b_down = PushButton("下移")
        b_down.setToolTip("下移选中的二级科目")
        b_down.clicked.connect(lambda: self._move_level2(1))
        self.b_move = PushButton("移动")
        self.b_move.setToolTip("把选中的二级科目移动到其它一级科目下")
        menu = QMenu(self.b_move)
        menu.aboutToShow.connect(self._fill_move_menu)
        self.b_move.setMenu(menu)
        b_del = PushButton("删除")
        b_del.setToolTip("删除选中的二级科目")
        b_del.clicked.connect(self._delete_level2)
        for b in (b_add, b_up, b_down, self.b_move, b_del):
            b.setObjectName("cardBtn")
            b.setFixedHeight(scale.px(24))
            fm = b.fontMetrics()
            extra = 40 if b.menu() is not None else 26
            b.setMinimumWidth(fm.horizontalAdvance(b.text()) + scale.px(extra))
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

    # -- 数据 -----------------------------------------------------------
    def set_subjects(self, items: List[Dict]) -> None:
        self.list.set_subjects(items)
        self.title.setText(self._name())

    def _name(self) -> str:
        return self.title.text()

    def selected_ids(self) -> List[int]:
        return self.list.selected_ids()

    # -- 操作 -----------------------------------------------------------
    def _rename_level1(self) -> None:
        old = self._name()
        new, ok = QInputDialog.getText(self, "改名一级科目", "新的一级科目名称：", text=old)
        if not ok:
            return
        new = new.strip()
        if not new or new == old:
            return
        self._view.rename_subject(self._id, new)

    def _add_level2(self) -> None:
        name, ok = QInputDialog.getText(self, "新增二级科目",
                                        f"在「{self._name()}」下新增二级科目：")
        if not ok:
            return
        self._view.add_level2(self._id, name)

    def _move_level2(self, direction: int) -> None:
        sel = self.selected_ids()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中要移动的二级科目")
            return
        self._view.move_level2(sel[0], direction)

    def _fill_move_menu(self) -> None:
        menu = self.b_move.menu()
        menu.clear()
        for sid, cname in self._view.level1_choices(exclude=self._id):
            menu.addAction(cname, lambda _=False, s=sid: self._move_to(s))

    def _move_to(self, target_id: int) -> None:
        sel = self.selected_ids()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中要移动的二级科目")
            return
        self._view.move_level2_to(sel, target_id)

    def _delete_level2(self) -> None:
        sel = self.selected_ids()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中要删除的二级科目")
            return
        self._view.delete_level2(sel)

    def _delete_level1(self) -> None:
        if QMessageBox.question(
                self, "删除一级科目",
                f"确定删除一级科目「{self._name()}」？\n其下所有二级科目也会一并删除"
                "（仅删主表配置，历史费用台账仍保留原科目文字）。") != QMessageBox.StandardButton.Yes:
            return
        self._view.delete_level1(self._id)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

class AccountSubjectView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._cards: Dict[int, SubjCard] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "会计科目",
            "维护一级 / 二级会计科目（树形归属）。卡片内拖动调整二级顺序，拖到别的卡片即改归属；"
            "双击名称可改名（改名会同步费用台账）。导入费用台账时按此处科目校验。顺序决定「账面情况」的展示次序。",
        ))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setSpacing(12)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

        foot = QHBoxLayout()
        self.hint = CaptionLabel("")
        foot.addWidget(self.hint)
        foot.addStretch()
        btn_add1 = PushButton("+ 一级科目")
        btn_add1.clicked.connect(self._add_level1)
        foot.addWidget(btn_add1)
        btn_export = PushButton("导出")
        btn_export.setToolTip("将会计科目主表导出为 Excel")
        btn_export.clicked.connect(self._export)
        foot.addWidget(btn_export)
        btn_import = PushButton("导入")
        btn_import.setToolTip("从 Excel 整表替换会计科目主表")
        btn_import.clicked.connect(self._import)
        foot.addWidget(btn_import)
        btn_refresh = PushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        foot.addWidget(btn_refresh)
        lay.addLayout(foot)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # -- 渲染 -----------------------------------------------------------
    def refresh(self) -> None:
        tree = asub.get_tree()
        # 一级集合变化时重建卡片
        if {p["id"] for p in tree} != set(self._cards):
            while self.grid.count():
                item = self.grid.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            self._cards = {}
            for i, p in enumerate(tree):
                card = SubjCard(p["id"], p["name"], self)
                card.setMinimumWidth(scale.px(300))
                card.setMinimumHeight(scale.px(240))
                self._cards[p["id"]] = card
                self.grid.addWidget(card, i // 3, i % 3)
            for col in range(3):
                self.grid.setColumnStretch(col, 1)
            self.grid.setRowStretch(len(tree) // 3 + 1, 1)
        for p in tree:
            card = self._cards.get(p["id"])
            if card is None:
                continue
            card.set_subjects(p["children"])
        total = sum(len(p["children"]) for p in tree)
        self.hint.setText(f"共 {total} 个二级科目，{len(tree)} 个一级科目")

    def level1_choices(self, exclude: int = None) -> List[Tuple[int, str]]:
        return [(p["id"], p["name"]) for p in asub.get_tree() if p["id"] != exclude]

    # -- 落库 -----------------------------------------------------------
    def persist(self) -> None:
        """拖拽/排序后统一按 id 落库整棵树（一级顺序 + 二级归属与顺序）。"""
        tree = []
        for card in self._cards.values():
            tree.append({"id": card._id, "children": card.list.subject_ids()})
        asub.save_layout_by_ids(tree)

    # -- 增删改（带审计） ----------------------------------------------
    def _log(self, subject_id, old: str, new: str) -> None:
        conn = get_conn()
        try:
            log_change(conn, "account_subject", subject_id, "name", old, new, "会计科目维护")
            conn.commit()
        finally:
            conn.close()

    def _add_level1(self) -> None:
        name, ok = QInputDialog.getText(self, "新增一级科目", "一级科目名称：")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        try:
            sid = asub.add_level1(name)
        except asub.AccountSubjectError as exc:
            QMessageBox.warning(self, "无法新增", str(exc))
            return
        self._log(sid, "", name)
        self.refresh()

    def add_level2(self, parent_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            return
        try:
            sid = asub.add_level2(parent_id, name)
        except asub.AccountSubjectError as exc:
            QMessageBox.warning(self, "无法新增", str(exc))
            return
        self._log(sid, "", name)
        self.refresh()

    def rename_subject(self, subject_id: int, new_name: str) -> None:
        try:
            asub.rename(subject_id, new_name)
        except asub.AccountSubjectError as exc:
            QMessageBox.warning(self, "无法改名", str(exc))
            return
        self._log(subject_id, "", new_name)
        self.refresh()

    def delete_level1(self, subject_id: int) -> None:
        asub.delete(subject_id)
        self._log(subject_id, "", "")
        self.refresh()

    def delete_level2(self, ids: List[int]) -> None:
        for sid in ids:
            asub.delete(sid)
            self._log(sid, "", "")
        self.refresh()

    def move_level1(self, subject_id: int, direction: int) -> None:
        if not asub.move_level1(subject_id, direction):
            return
        self.refresh()

    def move_level2(self, subject_id: int, direction: int) -> None:
        if not asub.move_level2(subject_id, direction):
            return
        self.refresh()

    def move_level2_to(self, ids: List[int], target_id: int) -> None:
        tree = asub.get_tree()
        target = next((p for p in tree if p["id"] == target_id), None)
        if target is None:
            return
        # 把选中二级从其当前父移除、追加到目标父，再统一落库
        by_parent: Dict[int, List[int]] = {}
        for p in tree:
            by_parent[p["id"]] = [c["id"] for c in p["children"]]
        moving = set(ids)
        for pid, kids in by_parent.items():
            by_parent[pid] = [k for k in kids if k not in moving]
        by_parent.setdefault(target_id, []).extend(ids)
        asub.save_layout_by_ids(
            [{"id": p["id"], "children": by_parent.get(p["id"], [])} for p in tree])
        self.refresh()

    # -- 导入 / 导出（Excel） ------------------------------------------
    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出会计科目", "会计科目配置.xlsx", "Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            _export_excel(path, asub.export_rows())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "已导出", f"已导出会计科目配置到：\n{path}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入会计科目", "", "Excel 文件 (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            rows = _import_excel(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(e))
            return
        if not rows:
            QMessageBox.warning(self, "导入失败", "文件中没有可用的科目数据")
            return
        if QMessageBox.question(
                self, "确认导入",
                f"导入将【整表替换】当前全部会计科目（共 {len(rows)} 行）。\n"
                "建议先导出一份备份。确定继续？") != QMessageBox.StandardButton.Yes:
            return
        try:
            asub.import_rows(rows)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(e))
            return
        self.refresh()
        QMessageBox.information(self, "已导入", "会计科目配置已更新。")


# ---------------------------------------------------------------------------
# Excel 主表导入 / 导出（单列格式：一级科目 / 二级科目 / 编码 / 说明）
# ---------------------------------------------------------------------------

def _export_excel(path: str, rows: List[Dict]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "科目"
    ws.append(["一级科目", "二级科目", "编码", "说明"])
    for r in rows:
        ws.append([r.get("level1", ""), r.get("level2", ""), r.get("code", ""), r.get("note", "")])
    wb.save(path)


def _import_excel(path: str) -> List[Dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["科目"] if "科目" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] in (None, ""):
            continue
        rows.append({
            "level1": str(row[0]).strip(),
            "level2": str(row[1]).strip() if len(row) > 1 and row[1] is not None else "",
            "code": str(row[2]).strip() if len(row) > 2 and row[2] is not None else "",
            "note": str(row[3]).strip() if len(row) > 3 and row[3] is not None else "",
        })
    return rows
