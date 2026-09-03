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

import json
import os
import re

from PySide6.QtCore import Qt, Signal, QStringListModel
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCompleter, QDialog, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QSplitter, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.engine import calc_sheet as cs
from app.engine.calc_eval import CalcEvaluator
from app.engine.calc_formula import ErrVal, classify_cell
from app.exporter.calc_export import export_sheet, suggest_filename
from app.ui.calc_dialogs import DataRefDialog, IndicatorManagerDialog, ParamDialog
from app.ui import scale
from app.ui.scale import PREFS_PATH
from app.ui.widgets import CaptionLabel, SubtitleLabel

_ERR_RED = QColor("#C0392B")
_FORMULA_GREEN = QColor("#1E7B34")

# 缩放范围与步进（Ctrl+滚轮，按表记忆到本机 prefs，不写库）
_ZOOM_MIN, _ZOOM_MAX, _ZOOM_STEP = 0.6, 2.0, 1.1

# 公式栏自动补全候选：内置函数 + 当前表参数片段
_KNOWN_FUNCS = ["SUM", "ROUND", "AVERAGE", "IF", "MIN", "MAX", "ABS", "DATA", "PARAM"]


class _FormulaCompleter(QCompleter):
    """公式栏 Excel 式词条补全：只在光标前的最后一个标识符上匹配，
    候选 = 内置函数名 + 当前表 PARAM("名称") 片段，避免 PAPRM 这类拼写错。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

    @staticmethod
    def _last_token(text: str) -> str:
        m = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", text or "")
        return m.group(0) if m else ""

    def splitPath(self, path: str):
        # 只拿尾部正在拼写的标识符做匹配前缀
        return [self._last_token(path)]

    def pathFromIndex(self, index):
        comp = super().pathFromIndex(index)
        w = self.widget()
        if w is None:
            return comp
        text = w.text()
        token = self._last_token(text)
        if not token:
            return comp
        # 仅替换尾部标识符，保留前面的 =1* 等前缀
        return text[: len(text) - len(token)] + comp


class _GridItemDelegate(QStyledItemDelegate):
    """单元格双击编辑器套用公式补全（与公式栏共用同一候选模型）。"""

    def __init__(self, completer, parent=None):
        super().__init__(parent)
        self._completer = completer

    def createEditor(self, parent, option, index):  # noqa: N802 (Qt override)
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setCompleter(self._completer)
        return editor


def _user() -> str:
    return os.environ.get("USERNAME", "") or "本机"


def _pref(key: str, default=None):
    """读 prefs.json（与皮肤/字号档共用文件，只加键不覆盖）。"""
    try:
        if PREFS_PATH.exists():
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get(key, default)
    except Exception:
        pass
    return default


def _pref_set(key: str, value) -> None:
    try:
        data = {}
        if PREFS_PATH.exists():
            loaded = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        data[key] = value
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


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
    """网格：编辑开始时把公式格的编辑文本切回原文 raw；Ctrl+滚轮缩放。"""

    zoomRequested = Signal(int)   # +1 放大 / -1 缩小（按住 Ctrl 滚动）

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.raw_provider = None   # callable(r0, c0) -> raw | None
        self.paste_callback = None  # Ctrl+V → TSV 值粘贴

    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomRequested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

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
        self.split = split
        root.addWidget(split, 1)

        # ---------------- 左：表格列表（窄栏，可折叠） ----------------
        left = QWidget()
        self.left_widget = left
        left.setMinimumWidth(200)   # 防止窗口偏窄时左栏被压成 0 宽、按钮文字被裁成空白
        left.setMaximumWidth(320)
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
        self.btn_toggle_list = QPushButton("≪")
        self.btn_toggle_list.setFixedWidth(28)
        self.btn_toggle_list.setToolTip("收起左侧表格列表，把空间让给网格")
        self.btn_toggle_list.clicked.connect(self._toggle_list)
        bar.addWidget(self.btn_toggle_list)
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
        self.btn_export = QPushButton("导出Excel")
        self.btn_export.setToolTip(
            "带公式：纯单元格引用公式保留为真实公式，DATA/PARAM 与跨表引用填值\n"
            "不带公式：全部填计算值")
        self.btn_export.clicked.connect(self._on_export)
        bar.addWidget(self.btn_ref)
        bar.addWidget(self.btn_param)
        bar.addWidget(self.btn_ind)
        bar.addWidget(self.btn_export)
        bar.addStretch(1)
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setToolTip("Ctrl+滚轮缩放网格（60%~200%），按表记忆；点「复位」回到 100%")
        bar.addWidget(self.lbl_zoom)
        self.btn_zoom_reset = QPushButton("复位")
        self.btn_zoom_reset.setToolTip("缩放复位 100%（Ctrl+0 已被全局字号占用）")
        self.btn_zoom_reset.clicked.connect(self._reset_zoom)
        bar.addWidget(self.btn_zoom_reset)
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
        self.fx.setPlaceholderText(
            "公式栏（支持补全：输入 SUM/ROUND/PARAM… 弹候选，↑↓选择回车确认；回车生效）")
        self.fx.returnPressed.connect(self._apply_formula_bar)
        self._comp_model = QStringListModel([], self)   # 公式栏/单元格共用候选模型
        self._completer = _FormulaCompleter(self.fx)
        self._completer.setModel(self._comp_model)
        self.fx.setCompleter(self._completer)
        fbar.addWidget(self.lbl_cell)
        fbar.addWidget(self.fx, 1)
        rv.addLayout(fbar)

        self.table = GridTable(0, 0)
        self._cell_completer = _FormulaCompleter(self.table)
        self._cell_completer.setModel(self._comp_model)
        self.table.setItemDelegate(_GridItemDelegate(self._cell_completer, self.table))
        self.table.raw_provider = self._raw_of
        self.table.paste_callback = self.paste_tsv
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.currentCellChanged.connect(self._on_current_cell)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.zoomRequested.connect(self._on_zoom)
        self.table.itemSelectionChanged.connect(self._update_stats)

        rv.addWidget(self.table, 1)
        # 基准行高（标准字号、缩放 100% 时），供缩放派生
        self._base_row = self.table.verticalHeader().defaultSectionSize()
        self._zoom = 1.0

        bottom = QHBoxLayout()
        self.lbl_hint = CaptionLabel("")
        bottom.addWidget(self.lbl_hint, 1)
        self.lbl_stat = CaptionLabel("")
        self.lbl_stat.setToolTip("当前选中区域的统计（类 Excel）")
        bottom.addWidget(self.lbl_stat)
        rv.addLayout(bottom)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 1000])

        # 左栏折叠状态记忆
        if _pref("calc_list_collapsed", False):
            self.left_widget.setVisible(False)
            self.btn_toggle_list.setText("≫")
            self.btn_toggle_list.setToolTip("展开左侧表格列表")

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

    def _refresh_completer(self) -> None:
        """刷新补全候选（公式栏与单元格编辑器共用同一模型）：内置函数 + 当前表参数片段。"""
        params = list((self.content.get("params") or {}).keys()) if self.content else []
        items = list(_KNOWN_FUNCS)
        for p in params:
            items.append(f'PARAM("{p}")')
        self._comp_model.setStringList(items)

    def _load_sheet(self) -> None:
        self.fx.clear()
        self.lbl_cell.clear()
        if self.sheet_id is None:
            self.content = {}
            self._refresh_completer()
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
        self._refresh_completer()
        self.lbl_hint.setText(
            f"最后编辑：{rec.get('updated_by') or '—'}  {rec.get('updated_at') or ''}"
            "　（数据库经 Seafile 多机同步：编辑前请确认其他电脑未同时编辑本表，后保存者会覆盖）")
        # 缩放按表记忆（仅本机 prefs，不写库）：先套字号/行高再填，
        # 列宽自适应用缩放后的字体测量，宽度自然带缩放
        self._zoom = 1.0
        self._set_zoom(self._load_zoom_pref() or 1.0, rescale_cols=False)
        self._fill()
        self._auto_fit_columns()

    # ------------------------------------------------------------------ #
    # 左栏折叠 / 网格缩放 / 冻结首行 / 选中统计
    # ------------------------------------------------------------------ #
    def _toggle_list(self) -> None:
        vis = not self.left_widget.isVisible()
        self.left_widget.setVisible(vis)
        self.btn_toggle_list.setText("≪" if vis else "≫")
        self.btn_toggle_list.setToolTip(
            "收起左侧表格列表，把空间让给网格" if vis else "展开左侧表格列表")
        _pref_set("calc_list_collapsed", not vis)
        if vis:
            self.split.setSizes([220, 1000])

    def _on_zoom(self, direction: int) -> None:
        self._set_zoom(round(self._zoom * (_ZOOM_STEP if direction > 0 else 1 / _ZOOM_STEP), 2))

    def _reset_zoom(self) -> None:
        self._set_zoom(1.0)

    def _set_zoom(self, z: float, rescale_cols: bool = True) -> None:
        """应用缩放：字号 + 行高 + 列宽同比缩放（60%~200%）。"""
        z = max(_ZOOM_MIN, min(_ZOOM_MAX, round(float(z), 2)))
        old = self._zoom
        px = max(8, round(scale.px(13) * z))
        qss = f"QTableWidget {{ font-size: {px}px; }}"
        self.table.setStyleSheet(qss)
        row_h = max(18, round(self._base_row * z))
        self.table.verticalHeader().setDefaultSectionSize(row_h)
        if rescale_cols and abs(z - old) > 1e-9 and self.table.columnCount():
            ratio = z / old
            for c in range(self.table.columnCount()):
                self.table.setColumnWidth(
                    c, max(24, round(self.table.columnWidth(c) * ratio)))
        self._zoom = z
        self.lbl_zoom.setText(f"{int(round(z * 100))}%")
        self._save_zoom_pref(z)

    def _load_zoom_pref(self) -> float | None:
        z = _pref("calc_zoom", {}).get(str(self.sheet_id))
        try:
            z = float(z)
        except (TypeError, ValueError):
            return None
        return z if _ZOOM_MIN <= z <= _ZOOM_MAX else None

    def _save_zoom_pref(self, z: float) -> None:
        if self.sheet_id is None:
            return
        m = _pref("calc_zoom", {})
        if not isinstance(m, dict):
            m = {}
        m[str(self.sheet_id)] = z
        _pref_set("calc_zoom", m)

    def _auto_fit_columns(self) -> None:
        """打开表时列宽按内容自适应（设上下限）；行高由全局字号档与缩放决定。"""
        self.table.resizeColumnsToContents()
        for c in range(self.table.columnCount()):
            self.table.setColumnWidth(
                c, max(48, min(self.table.columnWidth(c), 280)))

    def _update_stats(self) -> None:
        """选中区域统计（类 Excel）：计数 / 数值 / 求和 / 均值。"""
        items = self.table.selectedItems()
        if not items:
            self.lbl_stat.setText("")
            return
        vals = []
        for it in items:
            try:
                vals.append(float(str(it.text()).replace(",", "")))
            except ValueError:
                continue
        parts = [f"计数 {len(items)}"]
        if vals:
            s = sum(vals)
            parts.append(f"数值 {len(vals)}")
            parts.append(f"求和 {s:,.2f}" if s != int(s) else f"求和 {s:,.0f}")
            if vals:
                parts.append(f"均值 {s / len(vals):,.2f}")
        self.lbl_stat.setText("　".join(parts))

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
                self.table.setVerticalHeaderItem(r, QTableWidgetItem(str(r + 1)))
            if self.sheet_id is not None:
                ev = CalcEvaluator(cur_sheet_id=self.sheet_id)
                name = ev.cur_sheet()
                for r in range(rows):
                    for c in range(cols):
                        self.table.setItem(r, c, self._make_item(ev, name, r, c))
                ev.close()
        finally:
            self._filling = False
        self._update_stats()
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
        self._refresh_completer()
        self._recalc_fill()

    def _on_indicators(self) -> None:
        dlg = IndicatorManagerDialog(self)
        dlg.exec()
        if dlg.changed:
            self._load_sheet()   # 重建求值器，新指标即时生效

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #
    def _on_export(self) -> None:
        if self.sheet_id is None:
            return
        choices = ["带公式（DATA/跨表引用填值）", "不带公式（仅计算值）"]
        mode, ok = QInputDialog.getItem(
            self, "导出 Excel", "导出方式：", choices, 0, False)
        if not ok:
            return
        with_formula = mode == choices[0]
        rec = cs.get_sheet(self.sheet_id)
        default = suggest_filename(rec["name"] if rec else "计算表", with_formula)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel", default, "Excel 文件 (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_sheet(self.sheet_id, path, with_formula=with_formula)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已导出：\n{path}")

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
