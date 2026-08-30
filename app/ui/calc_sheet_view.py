"""分成计算页（内嵌类 Excel 网格，挂在「分成计算」大类下）

布局（spec: calc_engine_spec.md §5/§8）：
- 左：表格列表（新建 / 复制 / 删除），显示最后编辑人/时间（Seafile 多机同步提示）。
- 右：网格 + 公式栏 + 编辑/查看双模式（默认查看）+ 尾部加行/列。
- 编辑即存：每次单元格改动立即 save_content（updated_by/updated_at 同步刷新）。
- 显示：数值千分位（整数不带小数）；错误值 #REF! 等红色显示；查看模式只读。
- 编辑模式：双击/直接输入进入编辑（公式格编辑态显示原文 raw）；Ctrl+V 粘贴
  TSV 区域（值粘贴，从当前格开始，spec §11.5）。
- 求值：CalcEvaluator 每次重算时新建实例（缓存随重开刷新），引用全部表与
  自定义指标实时取当前库数据；只读不回写业务主表。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.engine import calc_sheet as cs
from app.engine.calc_eval import CalcEvaluator
from app.engine.calc_formula import ErrVal, classify_cell
from app.ui.calc_dialogs import DataRefDialog, IndicatorManagerDialog, ParamDialog
from app.ui.widgets import CaptionLabel, SubtitleLabel

_ERR_RED = QColor("#C0392B")
_FORMULA_GREEN = QColor("#1E7B34")


def _user() -> str:
    return os.environ.get("USERNAME", "") or "本机"


def _fmt(v) -> str:
    """数值显示：整数不带小数、小数两位，均千分位。"""
    if isinstance(v, ErrVal):
        return v.code
    if isinstance(v, (int, float)):
        f = float(v)
        if abs(f - round(f)) < 1e-9:
            return f"{f:,.0f}"
        return f"{f:,.2f}"
    return str(v)


class GridTable(QTableWidget):
    """网格：编辑开始时把公式格的编辑文本切回原文 raw。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.raw_provider = None   # callable(r0, c0) -> raw | None
        self.paste_callback = None  # Ctrl+V → TSV 值粘贴

    def keyPressEvent(self, event):  # noqa: N802 (Qt override)
        if (self.paste_callback is not None
                and event.key() == Qt.Key.Key_V
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.paste_callback()
            return
        super().keyPressEvent(event)

    def edit(self, index, trigger, event):  # noqa: N802 (Qt override)
        if self.raw_provider is not None:
            raw = self.raw_provider(index.row(), index.column())
            if raw not in (None, ""):
                it = self.item(index.row(), index.column())
                if it is not None:
                    it.setText(str(raw))
        return super().edit(index, trigger, event)


class CalcSheetView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.sheet_id: int | None = None
        self.content: dict = {}
        self.edit_mode: bool = False
        self._filling = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        root.addWidget(SubtitleLabel("分成计算"))
        root.addWidget(CaptionLabel(
            "内嵌类 Excel 计算表：引用软件内数据（=DATA）、跨表引用、命名参数。"
            "查看模式默认只读；切到编辑模式后双击或直接输入即可修改，改动自动保存。"
            "计算结果只读引用台账数据，绝不回写业务主表。"))

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # ---------------- 左：表格列表 ----------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 12, 0)
        lv.setSpacing(8)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select_sheet)
        lv.addWidget(self.list, 1)

        lb = QHBoxLayout()
        btn_new = QPushButton("新建")
        btn_copy = QPushButton("复制")
        btn_del = QPushButton("删除")
        for b in (btn_new, btn_copy, btn_del):
            lb.addWidget(b)
        lb.addStretch(1)
        lv.addLayout(lb)
        btn_new.clicked.connect(self._on_new)
        btn_copy.clicked.connect(self._on_copy)
        btn_del.clicked.connect(self._on_delete)
        split.addWidget(left)

        # ---------------- 右：网格区 ----------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(12, 0, 0, 0)
        rv.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_view_mode = QPushButton("查看")
        self.btn_edit_mode = QPushButton("编辑")
        for b in (self.btn_view_mode, self.btn_edit_mode):
            b.setCheckable(True)
        self.btn_view_mode.setChecked(True)
        self.btn_view_mode.clicked.connect(lambda: self._set_mode(False))
        self.btn_edit_mode.clicked.connect(lambda: self._set_mode(True))
        bar.addWidget(QLabel("模式："))
        bar.addWidget(self.btn_view_mode)
        bar.addWidget(self.btn_edit_mode)
        self.btn_ref = QPushButton("插入数据引用")
        self.btn_param = QPushButton("参数")
        self.btn_ind = QPushButton("指标管理")
        self.btn_ref.setToolTip("选 职工+指标+账期，生成 =DATA(...) 写入当前格")
        self.btn_param.setToolTip("本表命名参数，供 PARAM(\"名称\") 引用")
        self.btn_ind.setToolTip("自定义指标：在 DATA() 指标下拉中出现")
        self.btn_ref.clicked.connect(self._on_insert_ref)
        self.btn_param.clicked.connect(self._on_params)
        self.btn_ind.clicked.connect(self._on_indicators)
        bar.addWidget(self.btn_ref)
        bar.addWidget(self.btn_param)
        bar.addWidget(self.btn_ind)
        bar.addStretch(1)
        self.btn_add_row = QPushButton("+行")
        self.btn_add_col = QPushButton("+列")
        self.btn_add_row.clicked.connect(lambda: self._grow(5, 0))
        self.btn_add_col.clicked.connect(lambda: self._grow(0, 3))
        bar.addWidget(self.btn_add_row)
        bar.addWidget(self.btn_add_col)
        rv.addLayout(bar)

        fbar = QHBoxLayout()
        self.lbl_cell = QLabel("")          # 当前格坐标
        self.lbl_cell.setMinimumWidth(56)
        self.fx = QLineEdit()
        self.fx.setPlaceholderText("公式栏（选中单元格显示原文；编辑模式下可改，回车生效）")
        self.fx.returnPressed.connect(self._apply_formula_bar)
        fbar.addWidget(self.lbl_cell)
        fbar.addWidget(self.fx, 1)
        rv.addLayout(fbar)

        self.table = GridTable(0, 0)
        self.table.raw_provider = self._raw_of
        self.table.paste_callback = self.paste_tsv
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.currentCellChanged.connect(self._on_current_cell)
        self.table.itemChanged.connect(self._on_item_changed)
        rv.addWidget(self.table, 1)

        self.lbl_hint = CaptionLabel("")
        rv.addWidget(self.lbl_hint)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        self._set_mode(False)
        self._reload_list()

    # ------------------------------------------------------------------ #
    # 列表 / 打开
    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reload_list()

    def _reload_list(self) -> None:
        keep = self.sheet_id
        self.list.blockSignals(True)
        self.list.clear()
        sheets = cs.list_sheets()
        idx = -1
        for i, s in enumerate(sheets):
            tip = f"最后编辑：{s.get('updated_by') or '—'}  {s.get('updated_at') or ''}"
            self.list.addItem(s["name"])
            self.list.item(i).setData(Qt.ItemDataRole.UserRole, s["id"])
            self.list.item(i).setToolTip(tip)
            if s["id"] == keep:
                idx = i
        self.list.blockSignals(False)
        if idx >= 0:
            self.list.setCurrentRow(idx)
        elif self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.sheet_id = None
            self._load_sheet()

    def _on_select_sheet(self, row: int) -> None:
        if row < 0:
            return
        sid = self.list.item(row).data(Qt.ItemDataRole.UserRole)
        if sid != self.sheet_id:
            self.sheet_id = sid
            self._load_sheet()

    def _load_sheet(self) -> None:
        self.fx.clear()
        self.lbl_cell.clear()
        if self.sheet_id is None:
            self.content = {}
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.lbl_hint.setText("左侧选择或「新建」一张计算表。")
            return
        rec = cs.get_sheet(self.sheet_id)
        if not rec:
            self.sheet_id = None
            self._load_sheet()
            return
        self.content = rec["content"] or {}
        self.lbl_hint.setText(
            f"最后编辑：{rec.get('updated_by') or '—'}  {rec.get('updated_at') or ''}"
            "　（数据库经 Seafile 多机同步：编辑前请确认其他电脑未同时编辑本表，后保存者会覆盖）")
        self._fill()

    # ------------------------------------------------------------------ #
    # 求值与填充
    # ------------------------------------------------------------------ #
    def _recalc_fill(self) -> None:
        """改动后：先保存，再重算并刷新显示（保持当前格）。"""
        if self.sheet_id is None:
            return
        cs.save_content(self.sheet_id, self.content, updated_by=_user())
        r, c = self.table.currentRow(), self.table.currentColumn()
        self._fill()
        if r >= 0 and c >= 0 and r < self.table.rowCount() and c < self.table.columnCount():
            self.table.setCurrentCell(r, c)

    def _fill(self) -> None:
        """按 content 重算并填充网格（显示计算值）。"""
        self._filling = True
        try:
            rows = int(self.content.get("rows") or 0)
            cols = int(self.content.get("cols") or 0)
            self.table.setRowCount(rows)
            self.table.setColumnCount(cols)
            self.table.setHorizontalHeaderLabels(
                [self._col_name(i) for i in range(cols)])
            for r in range(rows):
                vheader_item = QTableWidgetItem(str(r + 1))
                self.table.setVerticalHeaderItem(r, vheader_item)
            if self.sheet_id is not None:
                ev = CalcEvaluator(cur_sheet_id=self.sheet_id)
                name = ev.cur_sheet()
                for r in range(rows):
                    for c in range(cols):
                        self.table.setItem(r, c, self._make_item(ev, name, r, c))
                ev.close()
        finally:
            self._filling = False
        self._on_current_cell(self.table.currentRow(), self.table.currentColumn(), -1, -1)

    @staticmethod
    def _col_name(i: int) -> str:
        s = ""
        n = i + 1
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(ord("A") + r) + s
        return s

    def _make_item(self, ev: CalcEvaluator, name: str, r: int, c: int):
        cell = (self.content.get("cells") or {}).get(f"{r},{c}")
        item = QTableWidgetItem("")
        if not cell:
            return item
        raw = cell.get("raw")
        kind = cell.get("kind") or classify_cell(raw)
        if kind == "formula":
            v = ev.cell_value(name, r, c)
            item.setText(_fmt(v))
            item.setToolTip(str(raw))
            if isinstance(v, ErrVal):
                item.setForeground(QBrush(_ERR_RED))
            elif isinstance(v, (int, float)):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            else:
                item.setForeground(QBrush(_FORMULA_GREEN))  # 公式结果为文本
        else:
            item.setText(_fmt(raw) if kind == "number" else str(raw or ""))
            if kind == "number":
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
        return item

    # ------------------------------------------------------------------ #
    # 选中 / 公式栏
    # ------------------------------------------------------------------ #
    def _raw_of(self, r: int, c: int):
        cell = (self.content.get("cells") or {}).get(f"{r},{c}")
        return cell.get("raw") if cell else None

    def _on_current_cell(self, r: int, c: int, _pr=-1, _pc=-1) -> None:
        if r < 0 or c < 0:
            self.lbl_cell.clear()
            return
        self.lbl_cell.setText(f"{self._col_name(c)}{r + 1}")
        raw = self._raw_of(r, c)
        self.fx.setText("" if raw is None else str(raw))

    def _apply_formula_bar(self) -> None:
        if not self.edit_mode or self.sheet_id is None or self._filling:
            return
        r, c = self.table.currentRow(), self.table.currentColumn()
        if r < 0 or c < 0:
            return
        self._write_cell(r, c, self.fx.text())

    # ------------------------------------------------------------------ #
    # 编辑
    # ------------------------------------------------------------------ #
    def _set_mode(self, edit: bool) -> None:
        self.edit_mode = edit
        self.btn_view_mode.setChecked(not edit)
        self.btn_edit_mode.setChecked(edit)
        if edit:
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
                | QAbstractItemView.EditTrigger.AnyKeyPressed)
            self.fx.setReadOnly(False)
        else:
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.fx.setReadOnly(True)
        for b in (self.btn_add_row, self.btn_add_col, self.btn_ref, self.btn_param):
            b.setEnabled(edit)

    # ------------------------------------------------------------------ #
    # 选择器 / 参数 / 指标管理
    # ------------------------------------------------------------------ #
    def _on_insert_ref(self) -> None:
        if not self.edit_mode or self.sheet_id is None:
            return
        dlg = DataRefDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.formula:
            return
        r, c = self.table.currentRow(), self.table.currentColumn()
        if r < 0 or c < 0:
            r, c = 0, 0
        self._write_cell(r, c, dlg.formula)

    def _on_params(self) -> None:
        if self.sheet_id is None:
            return
        dlg = ParamDialog(self.content.get("params") or {}, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.content["params"] = dlg.params
        self._recalc_fill()

    def _on_indicators(self) -> None:
        dlg = IndicatorManagerDialog(self)
        dlg.exec()
        if dlg.changed:
            self._load_sheet()   # 重建求值器，新指标即时生效

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or not self.edit_mode or self.sheet_id is None:
            return
        self._write_cell(item.row(), item.column(), item.text())

    def _write_cell(self, r: int, c: int, text: str) -> None:
        """用户编辑提交：更新 content → 保存 → 重算刷新。"""
        if r < 0 or c < 0:
            return
        cells = self.content.setdefault("cells", {})
        text = (text or "").strip()
        if text:
            cells[f"{r},{c}"] = {"raw": text, "kind": classify_cell(text)}
        else:
            cells.pop(f"{r},{c}", None)
        self._recalc_fill()

    def _grow(self, d_rows: int, d_cols: int) -> None:
        if self.sheet_id is None or not self.edit_mode:
            return
        self.content["rows"] = int(self.content.get("rows") or 0) + d_rows
        self.content["cols"] = int(self.content.get("cols") or 0) + d_cols
        self._recalc_fill()

    # ------------------------------------------------------------------ #
    # TSV 粘贴（值粘贴，spec §11.5）
    # ------------------------------------------------------------------ #
    def paste_tsv(self) -> None:
        if not self.edit_mode or self.sheet_id is None:
            return
        clip = QApplication.clipboard().text()
        if not clip:
            return
        r0, c0 = self.table.currentRow(), self.table.currentColumn()
        if r0 < 0 or c0 < 0:
            r0, c0 = 0, 0
        cells = self.content.setdefault("cells", {})
        rows = clip.replace("\r\n", "\n").rstrip("\n").split("\n")
        for dr, line in enumerate(rows):
            for dc, val in enumerate(line.split("\t")):
                val = val.strip()
                r, c = r0 + dr, c0 + dc
                if r >= int(self.content.get("rows") or 0):
                    self.content["rows"] = r + 1
                if c >= int(self.content.get("cols") or 0):
                    self.content["cols"] = c + 1
                if val:
                    cells[f"{r},{c}"] = {"raw": val, "kind": classify_cell(val)}
                else:
                    cells.pop(f"{r},{c}", None)
        self._recalc_fill()

    # ------------------------------------------------------------------ #
    # 新建 / 复制 / 删除
    # ------------------------------------------------------------------ #
    def _on_new(self) -> None:
        name, ok = QInputDialog.getText(self, "新建计算表", "表名（禁空格/!、禁 A1 样式）：")
        if not ok:
            return
        try:
            sid = cs.create_sheet(name.strip(), updated_by=_user())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "新建失败", str(e))
            return
        self._reload_list()
        self._select_id(sid)

    def _on_copy(self) -> None:
        if self.sheet_id is None:
            return
        src = cs.get_sheet(self.sheet_id)
        name, ok = QInputDialog.getText(
            self, "复制计算表", "新表名（公式与参数全部保留）：",
            text=f"{src['name']}-副本" if src else "")
        if not ok:
            return
        try:
            sid = cs.copy_sheet(self.sheet_id, name.strip(), updated_by=_user())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "复制失败", str(e))
            return
        self._reload_list()
        self._select_id(sid)

    def _on_delete(self) -> None:
        if self.sheet_id is None:
            return
        rec = cs.get_sheet(self.sheet_id)
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除计算表「{rec['name'] if rec else self.sheet_id}」？该操作不可恢复。")
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            cs.delete_sheet(self.sheet_id)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.sheet_id = None
        self._reload_list()

    def _select_id(self, sid: int) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == sid:
                self.list.setCurrentRow(i)
                return
