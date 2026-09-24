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

import copy
import json
import os
import re

from PySide6.QtCore import Qt, Signal, QStringListModel, QRect, QMimeData
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCompleter, QDialog, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QSplitter, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.engine import calc_sheet as cs
from app.engine.calc_eval import CalcEvaluator
from app.engine.calc_formula import ErrVal, classify_cell
from app.engine.calc_ref_rewrite import translate_refs
from app.engine.calc_nav import jump_to_boundary, nav_step
from app.engine.calc_undo import (
    CommandStack, EditCellCommand, ParamCommand, BulkCommand, snapshot)
from app.exporter.calc_export import export_sheet, suggest_filename
from app.ui.calc_dialogs import DataRefDialog, IndicatorManagerDialog, ParamDialog
from app.ui import scale, style
from app.ui.scale import PREFS_PATH
from app.ui.widgets import CaptionLabel, PageHeader

# 公式错误值 / 文本结果的前景色见 style.PALETTES：neg_fg / pos_fg（「用时」取）。

# 缩放范围与步进（Ctrl+滚轮，按表记忆到本机 prefs，不写库）
_ZOOM_MIN, _ZOOM_MAX, _ZOOM_STEP = 0.6, 2.0, 1.1

# 公式栏自动补全候选：内置函数 + 当前表参数片段
_KNOWN_FUNCS = ["SUM", "ROUND", "AVERAGE", "IF", "MIN", "MAX", "ABS", "DATA", "PARAM"]

# 阶段4 G4：应用内公式块复制用的自定义 MIME（剪贴板），外部粘贴降级走 text/plain TSV
_CALC_MIME = "application/x-lawfirm-calc-cells"


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
            # F2/双击进入编辑时光标置末（插入式，不选中原内容），贴近 Excel F2 手感
            editor.setCursorPosition(len(editor.text()))
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
        self.paste_callback = None  # Ctrl+V → 块粘贴（自定义 MIME 优先）/ TSV 值粘贴
        # 阶段1 键盘导航（G3）注入点：由 CalcSheetView 在 __init__ 末尾赋值
        self.clear_callback = None       # 无参：清空当前/选中格（编辑模式内判断）
        self.undo_callback = None        # Ctrl+Z（导航态）
        self.redo_callback = None        # Ctrl+Y / Ctrl+Shift+Z（导航态）
        self.bounds_provider = None      # () -> (rows, cols)
        self.occupied_provider = None    # () -> set["r,c"] 有数据格
        # 阶段4 G4：复制/剪切/填充柄
        self.copy_callback = None   # Ctrl+C → 写入自定义 MIME + TSV
        self.cut_callback = None    # Ctrl+X → 复制后清空选中
        self.fill_callback = None   # 填充柄释放：fill_callback(source_rect, target_rect)
        self.fill_enabled = False   # 仅编辑模式置 True（查看模式不画/不拖）
        self._fill_dragging = False
        self._fill_src = None       # (r0, c0, h, w)
        self._fill_preview = None   # QRect（视口坐标），绘制目标预览

    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomRequested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 (Qt override)
        # 编辑态：文本编辑器的复制/粘贴/撤销交给 QLineEdit 自管（B3），不拦截
        if self.state() == QAbstractItemView.State.EditingState:
            super().keyPressEvent(event)
            return
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        key = event.key()
        # 阶段4 G4：Ctrl+C/X 复制/剪切；Ctrl+V 块粘贴（自定义 MIME 优先，降级 TSV）
        if ctrl and key == Qt.Key.Key_C and self.copy_callback is not None:
            self.copy_callback()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_X and self.cut_callback is not None:
            self.cut_callback()
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_V and self.paste_callback is not None:
            self.paste_callback()
            return
        # Ctrl+Z 撤销 / Ctrl+Y 或 Ctrl+Shift+Z 重做（仅导航态；编辑态交 QLineEdit 自管输入撤销，B3）
        if ctrl and key == Qt.Key.Key_Z and not shift:
            if self.undo_callback is not None:
                self.undo_callback()
            event.accept()
            return
        if ctrl and (key == Qt.Key.Key_Y or (key == Qt.Key.Key_Z and shift)):
            if self.redo_callback is not None:
                self.redo_callback()
            event.accept()
            return
        rows, cols = self._nav_bounds()
        # Del / Backspace：清空当前格或选中区（只读拒绝在 clear_callback 内判断）
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.clear_callback is not None:
                self.clear_callback()
            event.accept()
            return
        # Tab / Enter：导航态无提交，仅移动焦点
        if key == Qt.Key.Key_Tab:
            if rows > 0 and cols > 0:
                self._nav_move("tab", shift)
            event.accept()
            return
        if key in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            if rows > 0 and cols > 0:
                self._nav_move("enter", shift)
            event.accept()
            return
        # Ctrl+Home / Ctrl+End：整表首 / 尾
        if ctrl and key == Qt.Key.Key_Home and rows > 0 and cols > 0:
            self.setCurrentCell(0, 0)
            event.accept()
            return
        if ctrl and key == Qt.Key.Key_End and rows > 0 and cols > 0:
            self.setCurrentCell(rows - 1, cols - 1)
            event.accept()
            return
        # Home / End：行首 / 行尾
        if key == Qt.Key.Key_Home:
            r = self.currentRow()
            if r >= 0:
                self.setCurrentCell(r, 0)
            event.accept()
            return
        if key == Qt.Key.Key_End:
            r = self.currentRow()
            if r >= 0 and cols > 0:
                self.setCurrentCell(r, cols - 1)
            event.accept()
            return
        # Ctrl+方向：跳数据边界
        if ctrl and key in (Qt.Key.Key_Up, Qt.Key.Key_Down,
                            Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._nav_jump(key)
            event.accept()
            return
        # 其余（方向键、Shift+方向 扩展选区等）交 Qt 默认
        super().keyPressEvent(event)

    def _nav_bounds(self):
        if self.bounds_provider is not None:
            return self.bounds_provider()
        return (self.rowCount(), self.columnCount())

    def _nav_occupied(self):
        if self.occupied_provider is not None:
            return self.occupied_provider()
        return set()

    def _nav_move(self, key, shift):
        r, c = self.currentRow(), self.currentColumn()
        rows, cols = self._nav_bounds()
        nr, nc = nav_step(r, c, rows, cols, key, shift)
        if (nr, nc) != (r, c):
            self.setCurrentCell(nr, nc)

    def _nav_jump(self, key):
        r, c = self.currentRow(), self.currentColumn()
        if r < 0 or c < 0:
            return
        rows, cols = self._nav_bounds()
        dr, dc = {Qt.Key.Key_Up: (-1, 0), Qt.Key.Key_Down: (1, 0),
                  Qt.Key.Key_Left: (0, -1), Qt.Key.Key_Right: (0, 1)}[key]
        nr, nc = jump_to_boundary(self._nav_occupied(), rows, cols, r, c, dr, dc)
        self.setCurrentCell(nr, nc)

    def edit(self, index, trigger, event):  # noqa: N802 (Qt override)
        if self.raw_provider is not None:
            raw = self.raw_provider(index.row(), index.column())
            if raw not in (None, ""):
                it = self.item(index.row(), index.column())
                if it is not None:
                    it.setText(str(raw))
        return super().edit(index, trigger, event)

    # ------------------------------------------------------------------ #
    # 阶段4 G4：填充柄绘制 + 拖拽（复制式填充，序列识别不做）
    # 说明：QAbstractItemView 会把视口鼠标事件以「视口坐标」转发到本类 mouse*Event，
    # 因此 event.pos() 与 visualRect() 同坐标系，可直接比对。
    # ------------------------------------------------------------------ #
    def _sel_bbox(self):
        """当前选区的包围矩形：(r0, c0, h, w)，无选区返回 None。"""
        rngs = self.selectedRanges()
        if not rngs:
            return None
        r0 = min(rng.topRow() for rng in rngs)
        c0 = min(rng.leftColumn() for rng in rngs)
        r1 = max(rng.bottomRow() for rng in rngs)
        c1 = max(rng.rightColumn() for rng in rngs)
        if r1 < 0 or c1 < 0:
            return None
        return (r0, c0, r1 - r0 + 1, c1 - c0 + 1)

    def _fill_handle_rect(self):
        """选区右下角手柄的小方块（视口坐标）；无选区/越界返回 None。"""
        bbox = self._sel_bbox()
        if bbox is None:
            return None
        r1 = bbox[0] + bbox[2] - 1
        c1 = bbox[1] + bbox[3] - 1
        if r1 < 0 or c1 < 0:
            return None
        idx = self.model().index(r1, c1)
        if not idx.isValid():
            return None
        cell = self.visualRect(idx)
        if cell.isEmpty():
            return None
        return QRect(cell.right() - 6, cell.bottom() - 6, 8, 8)

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        super().paintEvent(event)
        if not self.fill_enabled:
            return
        # 拖拽中的目标预览
        if self._fill_preview is not None and not self._fill_preview.isEmpty():
            p = QPainter(self.viewport())
            p.setPen(style.qcolor("accent_blue"))
            p.setBrush(style.qcolor("accent_blue_bg"))
            p.drawRect(self._fill_preview)
            p.end()
        # 手柄小方块（蓝底白边，色号走皮肤 token）
        hr = self._fill_handle_rect()
        if hr is not None:
            p = QPainter(self.viewport())
            p.setPen(style.qcolor("white"))
            p.setBrush(style.qcolor("accent_blue"))
            p.drawRect(hr)
            p.end()

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        if self.fill_enabled and event.button() == Qt.MouseButton.LeftButton:
            hr = self._fill_handle_rect()
            if hr is not None and hr.adjusted(-3, -3, 3, 3).contains(event.pos()):
                bbox = self._sel_bbox()
                if bbox is not None:
                    self._fill_dragging = True
                    self._fill_src = bbox
                    self._fill_preview = None
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 (Qt override)
        if self._fill_dragging and self._fill_src is not None:
            idx = self.indexAt(event.pos())
            if idx.isValid():
                tr, tc = idx.row(), idx.column()
                sr0, sc0, sh, sw = self._fill_src
                r0 = max(0, min(sr0, tr))   # D2：负夹取
                c0 = max(0, min(sc0, tc))
                h = abs(tr - sr0) + 1
                w = abs(tc - sc0) + 1
                a = self.visualRect(self.model().index(r0, c0))
                b = self.visualRect(self.model().index(r0 + h - 1, c0 + w - 1))
                self._fill_preview = a.united(b)
                self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override)
        if self._fill_dragging and self._fill_src is not None:
            idx = self.indexAt(event.pos())
            self._fill_dragging = False
            self._fill_preview = None
            self.viewport().update()
            if idx.isValid() and self.fill_callback is not None:
                tr, tc = idx.row(), idx.column()
                sr0, sc0, sh, sw = self._fill_src
                r0 = max(0, min(sr0, tr))   # D2：负夹取
                c0 = max(0, min(sc0, tc))
                th = abs(tr - sr0) + 1
                tw = abs(tc - sc0) + 1
                self.fill_callback(self._fill_src, (r0, c0, th, tw))
            self._fill_src = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CalcSheetView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.sheet_id: int | None = None
        self.content: dict = {}
        self.edit_mode: bool = False
        self._filling = False
        self.stack = CommandStack()

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        root.addWidget(PageHeader(
            "分成计算",
            "内嵌类 Excel 计算表：引用软件内数据（=DATA）、跨表引用、命名参数。"
            "查看模式默认只读；切到编辑模式后双击或直接输入即可修改，改动自动保存。"
            "计算结果只读引用台账数据，绝不回写业务主表。",
        ))

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
        # 阶段5 G10：重命名**单独一行**——左栏最小宽 200px，4 个按钮挤一行会超出宽度、
        # 按钮文字被裁成空白（该类问题在左栏已有先例）。
        lb2 = QHBoxLayout()
        lb2.setSpacing(8)
        self.btn_rename = QPushButton("重命名")
        self.btn_rename.setToolTip(
            "修改表名；其他表中对本表的引用（=表名!A1）会同步改写，防止断链成 #REF!")
        lb2.addWidget(self.btn_rename)
        lb2.addStretch(1)
        lv.addLayout(lb2)
        self.btn_rename.clicked.connect(self._on_rename)
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
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.setToolTip("撤销上一步（Ctrl+Z）；切换表后清空")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self._on_undo)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.setToolTip("重做（Ctrl+Y / Ctrl+Shift+Z）；切换表后清空")
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self._on_redo)
        bar.addWidget(self.btn_undo)
        bar.addWidget(self.btn_redo)
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

        # ---------------- 结构变换栏（阶段3 G2：插/删行、列） ----------------
        sbar = QHBoxLayout()
        sbar.setSpacing(8)
        sbar.addWidget(QLabel("结构："))
        self.btn_ins_row_above = QPushButton("↑插入行")
        self.btn_ins_row_below = QPushButton("↓插入行")
        self.btn_del_row = QPushButton("删除行")
        self.btn_ins_col_left = QPushButton("←插入列")
        self.btn_ins_col_right = QPushButton("→插入列")
        self.btn_del_col = QPushButton("删除列")
        self.btn_ins_row_above.setToolTip(
            "在当前选中行上方插入一行；引用按 $/绝对/跨表规则真重写（扩张/收缩/#REF!）")
        self.btn_ins_row_below.setToolTip("在当前选中行下方插入一行")
        self.btn_del_row.setToolTip(
            "删除当前选中行；被删行上的引用变 #REF!，其下引用自动上移")
        self.btn_ins_col_left.setToolTip(
            "在当前选中列左侧插入一列；引用按规则真重写")
        self.btn_ins_col_right.setToolTip("在当前选中列右侧插入一列")
        self.btn_del_col.setToolTip(
            "删除当前选中列；被删列上的引用变 #REF!，其右引用自动左移")
        self.btn_ins_row_above.clicked.connect(
            lambda: self._struct_op("row", self.table.currentRow(), 1, True))
        self.btn_ins_row_below.clicked.connect(
            lambda: self._struct_op("row", self.table.currentRow() + 1, 1, True))
        self.btn_del_row.clicked.connect(
            lambda: self._struct_op("row", self.table.currentRow(), 1, False))
        self.btn_ins_col_left.clicked.connect(
            lambda: self._struct_op("col", self.table.currentColumn(), 1, True))
        self.btn_ins_col_right.clicked.connect(
            lambda: self._struct_op("col", self.table.currentColumn() + 1, 1, True))
        self.btn_del_col.clicked.connect(
            lambda: self._struct_op("col", self.table.currentColumn(), 1, False))
        for b in (self.btn_ins_row_above, self.btn_ins_row_below, self.btn_del_row,
                  self.btn_ins_col_left, self.btn_ins_col_right, self.btn_del_col):
            sbar.addWidget(b)
        sbar.addStretch(1)
        rv.addLayout(sbar)

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
        # 阶段4 G4：Ctrl+C/X/V + 填充柄回调（paste 优先识别自定义 MIME，降级 TSV）
        self.table.paste_callback = self._paste_cells
        self.table.copy_callback = self._copy_selection
        self.table.cut_callback = self._cut_selection
        self.table.fill_callback = self._do_fill
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.currentCellChanged.connect(self._on_current_cell)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.zoomRequested.connect(self._on_zoom)
        self.table.itemSelectionChanged.connect(self._update_stats)
        # 阶段1 键盘导航（G3）注入点
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.bounds_provider = lambda: (
            int(self.content.get("rows") or 0), int(self.content.get("cols") or 0))
        self.table.occupied_provider = lambda: set((self.content.get("cells") or {}).keys())
        self.table.clear_callback = self._clear_selected_cells
        self.table.undo_callback = self._on_undo
        self.table.redo_callback = self._on_redo

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
        self.stack.clear()
        self._update_undo_buttons()
        self.fx.clear()
        self.lbl_cell.clear()
        self._corrupt = False
        self.btn_edit_mode.setEnabled(True)
        self.btn_view_mode.setEnabled(True)
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
        # P0-4：库内 content 无法解析（Seafile 半写入 / 手工改动 / 跨版本结构）时，
        # get_sheet 已把原始串备份到库外并打 content_corrupt 标记。此处必须**锁只读** ——
        # 否则用户看到的是空表，若以为"打开错了表"随手改一格，空表就会被"编辑即存"写回，
        # 原内容永久丢失（改前连痕迹都不留）。
        self._corrupt = bool(rec.get("content_corrupt"))
        self.btn_edit_mode.setEnabled(not self._corrupt)
        self.btn_view_mode.setEnabled(not self._corrupt)
        self._refresh_completer()
        if self._corrupt:
            self._set_mode(False)
            self.lbl_hint.setText(
                "⚠️ 本表在库中的内容已损坏（无法解析），当前显示为空表，已锁定只读以防止覆盖。"
                f"原始内容备份：{rec.get('content_backup') or '（备份失败）'}"
                "　请先从备份恢复，再回来编辑。")
            QMessageBox.critical(
                self, "计算表内容损坏",
                f"表「{rec.get('name') or ''}」在库中的内容无法解析，已锁定为只读。\n\n"
                f"原始内容已备份至：\n{rec.get('content_backup') or '（备份失败）'}\n\n"
                "请勿在恢复前强行保存，否则会覆盖原始数据。")
        else:
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
        if getattr(self, "_corrupt", False):
            # P0-4：损坏表一律不写库（引擎侧还有 save_content 守卫兜底）
            QMessageBox.warning(
                self, "未保存",
                "本表内容已损坏且处于只读状态，本次改动未保存。请先从备份恢复后再编辑。")
            return
        try:
            cs.save_content(self.sheet_id, self.content, updated_by=_user())
        except cs.CalcSheetError as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
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
                try:
                    name = ev.cur_sheet()
                    for r in range(rows):
                        for c in range(cols):
                            self.table.setItem(r, c, self._make_item(ev, name, r, c))
                finally:
                    # P1-2：_make_item/求值抛异常也必须关闭，close 必须在 finally
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
                item.setForeground(QBrush(style.qcolor("neg_fg")))
            elif isinstance(v, (int, float)):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            else:
                item.setForeground(QBrush(style.qcolor("pos_fg")))  # 公式结果为文本
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
    # 撤销 / 重做（阶段2 命令栈，G1）
    # ------------------------------------------------------------------ #
    def _commit_command(self, cmd) -> None:
        """统一提交命令（B2）：apply 返回新 content → 保存重算 → 入栈 → 按钮刷新 → 焦点回锚点。"""
        if self.sheet_id is None or getattr(self, "_corrupt", False):
            return
        # B1：命令 apply 返回新 content，绝不在原 content 上就地改
        self.content = cmd.apply(self.content)
        self._recalc_fill()
        self.stack.push(cmd)
        self._update_undo_buttons()
        anchor = cmd.anchor()
        if anchor:
            r, c = anchor
            if 0 <= r < self.table.rowCount() and 0 <= c < self.table.columnCount():
                self.table.setCurrentCell(r, c)

    def _on_undo(self) -> None:
        if self.sheet_id is None:
            return
        cmd = self.stack.undo()
        if cmd is None:  # B7：空栈 no-op
            return
        self.content = cmd.revert(self.content)
        self._recalc_fill()
        self._update_undo_buttons()
        anchor = cmd.anchor()
        if anchor:
            r, c = anchor
            if 0 <= r < self.table.rowCount() and 0 <= c < self.table.columnCount():
                self.table.setCurrentCell(r, c)

    def _on_redo(self) -> None:
        if self.sheet_id is None:
            return
        cmd = self.stack.redo()
        if cmd is None:  # B7：空栈 no-op
            return
        self.content = cmd.apply(self.content)
        self._recalc_fill()
        self._update_undo_buttons()
        anchor = cmd.anchor()
        if anchor:
            r, c = anchor
            if 0 <= r < self.table.rowCount() and 0 <= c < self.table.columnCount():
                self.table.setCurrentCell(r, c)

    def _update_undo_buttons(self) -> None:
        if not hasattr(self, "btn_undo"):
            return
        self.btn_undo.setEnabled(self.stack.can_undo())
        self.btn_redo.setEnabled(self.stack.can_redo())

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
                | QAbstractItemView.EditTrigger.EditKeyPressed)
            self.fx.setReadOnly(False)
        else:
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.fx.setReadOnly(True)
        for b in (self.btn_add_row, self.btn_add_col, self.btn_ref, self.btn_param,
                  self.btn_ins_row_above, self.btn_ins_row_below, self.btn_del_row,
                  self.btn_ins_col_left, self.btn_ins_col_right, self.btn_del_col):
            b.setEnabled(edit)
        # 阶段4 G4：仅编辑模式显示/启用填充柄（查看模式不画、不拖）
        self.table.fill_enabled = edit
        if not edit:
            self.table._fill_dragging = False
            self.table._fill_preview = None

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
        before = copy.deepcopy(self.content.get("params") or {})
        dlg = ParamDialog(self.content.get("params") or {}, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        after = copy.deepcopy(dlg.params)
        if before == after:
            self._refresh_completer()
            return
        self._commit_command(ParamCommand(before=before, after=after))
        self._refresh_completer()

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
        """用户编辑提交：构造单格命令（B1 纯逆操作）→ 提交栈（不直接改 content）。"""
        if r < 0 or c < 0 or not self.edit_mode or self.sheet_id is None:
            return
        key = f"{r},{c}"
        text = (text or "").strip()
        cells = self.content.get("cells") or {}
        before = cells.get(key)
        before = copy.deepcopy(before) if before else None
        after = {"raw": text, "kind": classify_cell(text)} if text else None
        if before == after:  # 值无变化不记命令（避免无意义撤销项）
            return
        self._commit_command(EditCellCommand(key=key, before=before, after=after))

    def _clear_selected_cells(self) -> None:
        """Del/Backspace（导航态、编辑模式）：清空当前格或选中区，不进编辑。"""
        if not self.edit_mode or self.sheet_id is None:
            return
        targets = set()
        for rng in self.table.selectedRanges():
            for r in range(rng.topRow(), rng.bottomRow() + 1):
                for c in range(rng.leftColumn(), rng.rightColumn() + 1):
                    targets.add((r, c))
        if not targets:
            r, c = self.table.currentRow(), self.table.currentColumn()
            if r >= 0 and c >= 0:
                targets.add((r, c))
        if not targets:
            return
        before = snapshot(self.content)
        cells = self.content.setdefault("cells", {})
        for (r, c) in targets:
            cells.pop(f"{r},{c}", None)
        after = snapshot(self.content)
        if before == after:
            return
        r, c = self.table.currentRow(), self.table.currentColumn()
        anchor_key = f"{r},{c}" if r >= 0 and c >= 0 else None
        self._commit_command(BulkCommand(before=before, after=after, anchor_key=anchor_key))

    def _grow(self, d_rows: int, d_cols: int) -> None:
        if self.sheet_id is None or not self.edit_mode:
            return
        before = snapshot(self.content)
        self.content["rows"] = int(self.content.get("rows") or 0) + d_rows
        self.content["cols"] = int(self.content.get("cols") or 0) + d_cols
        after = snapshot(self.content)
        if before == after:
            return
        self._commit_command(BulkCommand(before=before, after=after))

    def _struct_op(self, axis: str, at: int, delta: int, insert: bool) -> None:
        """插入/删除行、列（阶段3 G2）：调引擎纯函数 → 压 BulkCommand → 走统一提交。

        复用阶段2 的 BulkCommand（整 content 快照），calc_undo.py 零改动。
        at/currentRow/Column 可能越界（-1 表示未选），先拦下提示用户选格。
        """
        if self.sheet_id is None or not self.edit_mode or getattr(self, "_corrupt", False):
            return
        if at < 0:
            self.lbl_hint.setText("请先点选一个单元格，再执行插/删行列。")
            return
        before = snapshot(self.content)
        if axis == "row":
            after = (cs.insert_row if insert else cs.delete_row)(self.content, at, delta)
        else:
            after = (cs.insert_col if insert else cs.delete_col)(self.content, at, delta)
        if before == after:
            # D1：已是最末 1 行/列无法再删，或 delta 被守卫夹成 0
            self.lbl_hint.setText("已是最末一行/列，无法继续删除。")
            return
        r, c = self.table.currentRow(), self.table.currentColumn()
        anchor_key = f"{r},{c}" if r >= 0 and c >= 0 else None
        self._commit_command(BulkCommand(before=before, after=after, anchor_key=anchor_key))

    # ------------------------------------------------------------------ #
    # 阶段4 G4：复制 / 剪切 / 块粘贴 / 填充柄
    # ------------------------------------------------------------------ #
    def _copy_selection(self) -> None:
        """Ctrl+C：把选中区 raw 写入剪贴板，带自定义 MIME（应用内公式块）+ TSV（外部兼容）。

        仅收集「有数据」的源格（D3：空白源格不入块，粘贴/填充时不误清目标）。
        查看模式也可复制（只读数据，无害）；没有数据可复制则静默返回。
        """
        if self.sheet_id is None:
            return
        rngs = self.table.selectedRanges()
        if not rngs:
            return
        cells = self.content.get("cells") or {}
        r0 = min(rng.topRow() for rng in rngs)
        c0 = min(rng.leftColumn() for rng in rngs)
        r1 = max(rng.bottomRow() for rng in rngs)
        c1 = max(rng.rightColumn() for rng in rngs)
        w, h = c1 - c0 + 1, r1 - r0 + 1
        block: Dict[str, Dict[str, str]] = {}
        tsv_rows: List[str] = []
        for r in range(r0, r1 + 1):
            row_vals: List[str] = []
            for c in range(c0, c1 + 1):
                cell = cells.get(f"{r},{c}")
                raw = (cell or {}).get("raw")
                row_vals.append(raw if raw is not None else "")
                if raw not in (None, ""):
                    block[f"{r},{c}"] = {
                        "raw": raw,
                        "kind": (cell or {}).get("kind") or classify_cell(raw),
                    }
            tsv_rows.append("\t".join(row_vals))
        if not block:
            return
        payload = json.dumps(
            {"origin": [r0, c0], "w": w, "h": h, "cells": block},
            ensure_ascii=False)
        mime = QMimeData()
        mime.setData(_CALC_MIME, payload.encode("utf-8"))
        mime.setText("\n".join(tsv_rows))
        QApplication.clipboard().setMimeData(mime)

    def _cut_selection(self) -> None:
        """Ctrl+X：复制后再清空选中区（压 BulkCommand）。仅编辑模式有效。"""
        if not self.edit_mode or self.sheet_id is None:
            return
        self._copy_selection()
        self._clear_selected_cells()

    def _paste_cells(self) -> None:
        """Ctrl+V：若剪贴板含自定义 MIME → 块粘贴（按位移 translate_refs 平移公式）；
        否则降级走 TSV 值粘贴（D7：自定义 MIME 优先）。

        块位移 = 目标原点 − 源原点（常量）；公式格走 translate_refs，绝对/跨表不动；
        kind 经 classify_cell 重算（D6）；越界目标自动增长网格（D1）。
        """
        if not self.edit_mode or self.sheet_id is None:
            return
        mime = QApplication.clipboard().mimeData()
        r0t, c0t = self.table.currentRow(), self.table.currentColumn()
        if r0t < 0 or c0t < 0:
            r0t, c0t = 0, 0
        if mime is not None and mime.hasFormat(_CALC_MIME):
            try:
                data = json.loads(bytes(mime.data(_CALC_MIME)).decode("utf-8"))
            except Exception:
                self.paste_tsv()
                return
            sr0, sc0 = data.get("origin", [0, 0])
            src_cells = data.get("cells", {})
            if not src_cells:
                return
            dr0, dc0 = r0t - sr0, c0t - sc0   # 整块常量位移
            before = snapshot(self.content)
            new_cells = dict(self.content.get("cells") or {})
            max_r = max_c = -1
            for key, cell in src_cells.items():
                sr, sc = (int(x) for x in key.split(","))
                tr, tc = sr + dr0, sc + dc0
                raw = cell.get("raw")
                if raw is None:
                    continue
                if str(raw).startswith("="):   # D5：仅公式平移
                    raw = translate_refs(raw, dr0, dc0)
                new_cells[f"{tr},{tc}"] = {
                    "raw": raw, "kind": classify_cell(raw)}   # D6：重算 kind
                max_r = max(max_r, tr)
                max_c = max(max_c, tc)
            new = dict(self.content)
            new["cells"] = new_cells
            rows = int(new.get("rows") or 0)
            cols = int(new.get("cols") or 0)
            if max_r + 1 > rows:   # D1：越界增长
                new["rows"] = max_r + 1
            if max_c + 1 > cols:
                new["cols"] = max_c + 1
            if before == new:
                return
            self._commit_command(
                BulkCommand(before=before, after=new, anchor_key=f"{r0t},{c0t}"))
        else:
            self.paste_tsv()

    def _do_fill(self, src_rect, tgt_rect) -> None:
        """填充柄释放：按 fill_cells 映射把源区复制到目标区（复制式，无序列识别）。

        src_rect/tgt_rect = (r0, c0, h, w)。单格源→逐格递增偏移；块源→整块常量偏移 +
        取模重复。D1 越界增长、D2 负夹取、D3 空白源格跳过、D5 仅平移公式、D6 重算 kind。
        """
        if not self.edit_mode or self.sheet_id is None or getattr(self, "_corrupt", False):
            return
        cells = self.content.get("cells") or {}
        out = cs.fill_cells(cells, src_rect, tgt_rect)   # 纯函数：{target_key: new_raw}
        if not out:
            return
        before = snapshot(self.content)
        new_cells = dict(cells)
        max_r = max_c = -1
        for key, raw in out.items():
            new_cells[key] = {"raw": raw, "kind": classify_cell(raw)}   # D6
            r, c = (int(x) for x in key.split(","))
            max_r = max(max_r, r)
            max_c = max(max_c, c)
        new = dict(self.content)
        new["cells"] = new_cells
        rows = int(new.get("rows") or 0)
        cols = int(new.get("cols") or 0)
        if max_r + 1 > rows:   # D1：越界增长
            new["rows"] = max_r + 1
        if max_c + 1 > cols:
            new["cols"] = max_c + 1
        if before == new:
            return
        tr0, tc0 = tgt_rect[0], tgt_rect[1]
        self._commit_command(
            BulkCommand(before=before, after=new, anchor_key=f"{tr0},{tc0}"))

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
        before = snapshot(self.content)
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
        after = snapshot(self.content)
        if before == after:
            return
        self._commit_command(BulkCommand(before=before, after=after, anchor_key=f"{r0},{c0}"))

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

    def _on_rename(self) -> None:
        """重命名表（阶段5 G10）：改名 + 同步重写全库跨表引用，防他表断链成 #REF!。"""
        if self.sheet_id is None:
            return
        rec = cs.get_sheet(self.sheet_id)
        old_name = rec["name"] if rec else ""
        name, ok = QInputDialog.getText(
            self, "重命名计算表",
            "新表名（禁空格/!、禁 A1 样式）。改名会同步更新其他表中对本表的引用，"
            "避免引用断链变成 #REF!：",
            text=old_name)
        if not ok:
            return
        name = (name or "").strip()
        if not name or name == old_name:
            return
        err = cs.validate_sheet_name(name)
        if err:
            QMessageBox.warning(self, "重命名失败", err)
            return
        try:
            changed = cs.rename_sheet(self.sheet_id, name, updated_by=_user())
        except cs.CalcSheetError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        self._reload_list()   # 按 id 保持选中，改名后仍停在这张表
        tip = f"已重命名为「{name}」"
        if changed:
            tip += f"，并同步更新了 {changed} 张表中的跨表引用"
        self.lbl_hint.setText(tip)

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
