"""发票台账导入 — 统一确认对话框（问题修正 + 预览确认 合二为一）

背景：原先是两个独立弹窗
    1) 问题行修正 ProblemDialog  → 只能修，看不到源文件备注/经办人原文/收款认定
    2) 导入预览确认 PreviewDialog → 只能看与改已收，解析失败的行已被处理掉、无法回头改
本对话框把两者合并为「一张表 + 右侧就地修正」：

- 一张表承载全部行，状态分四类：待修正（解析失败）/ 待确认（低置信）/ 高置信 / 已跳过
- 顶部筛选胶囊：需处理（默认，待修正+待确认）/ 全部 / 待修正 / 待确认 / 高置信
- 选中「待修正」行 → 右侧内嵌 ProblemFixPanel 就地修正；保存后立即合并进工作副本、
  重算置信度并刷新表格，该行当场变为「待确认 / 高置信」，可反复修改
- 选中「已修正」行 → 右侧仍可「重新修正」
- 双击任意行 → 查看原始台账行（问题行也能看，依赖 problem 携带 header/raw_row）
- 待确认行可编辑「各经办人已收」（双击该列或右侧按钮）
- 确认入库：未处理的问题行自动跳过；已收覆盖值按发票下标回写真实数据

数据流：真实 data 全程不被修改，修正只作用于工作副本；点「确认入库」时才一次性
把 resolved 合并进真实 data。点「取消」真实 data 保持原样。
"""
from __future__ import annotations

import copy
from typing import Dict, List

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.engine.import_confidence import SHEET_LABEL, evaluate
from app.importer.excel_reader import ImportError_
from app.ui import scale
from app.ui.ledger_source import show_ledger_source
from app.ui.preview_dialog import HandlerReceivedDialog, _fmt_money
from app.ui.problem_fix_panel import ProblemFixPanel
from app.ui.table_view import auto_fit_columns
from app.ui.widgets import CaptionLabel, PrimaryPushButton, PushButton, TableWidget

RED = QColor("#C0392B")
AMBER = QColor("#B7791F")
GREEN = QColor("#1E8449")
GRAY = QColor("#8A8A85")
DIFF_BG = QColor("#FDF1F0")

HEADERS = [
    "状态", "类型", "来源", "发票号", "购方", "金额",
    "经办人分摊", "各经办人已收(双击编辑)", "收款认定", "源文件备注", "原因 / 疑问",
]
COL_STATUS, COL_KIND, COL_SRC, COL_NO, COL_BUYER, COL_AMT, COL_HANDLER, \
    COL_RECV, COL_RECEIPT, COL_REMARK, COL_REASON = range(11)

FILTERS = ["需处理", "全部", "待修正", "待确认", "高置信"]
_PRIO = {"待修正": 0, "待确认": 1, "已修正": 2, "已跳过": 3, "高置信": 4}


class UnifiedImportDialog(QDialog):
    """发票台账导入统一确认对话框。

    exec() == Accepted 表示确认入库：结果已合并进 self._data
    （invoices/prepayments 含修正行、received_overrides 已回写、problems 清空）。
    """

    def __init__(self, data: Dict, period: str, staff_names: List[str],
                 parent=None, path: str = "", validator=None) -> None:
        """validator: 可选 callable(data) -> str | None。

        在「确认入库」时先对合并后的结果做写库前校验（sheet1+sheet2 合计 vs
        销项合计、经办人是否在花名册）；返回错误文案则留在对话框内继续修改，
        避免像旧流程那样「改完一堆才报错、修改全丢」。
        """
        super().__init__(parent)
        self._data = data
        self._period = period
        self._path = path
        self._validate = validator
        self._file_name = path.replace("\\", "/").split("/")[-1] if path else "导入文件"
        self._staff_set = set(staff_names)
        self._orig_inv_len = len(data.get("invoices", []))

        self._fix: Dict[int, dict] = {}          # problem index -> 修正数据
        self._skip: set = set()                  # problem index -> 跳过
        self._ov: Dict[int, Dict[str, float]] = {}  # invoice 下标 -> {经办人: 已收}
        self._idx_to_p: Dict[int, int] = {}      # work invoices 下标 -> problem index
        self._rows: List[Dict] = []

        self.setWindowTitle(f"发票台账导入确认 — {period}")
        self.resize(1240, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        t = QLabel(f"发票台账导入确认（{period}）")
        t.setObjectName("pageTitle")
        root.addWidget(t)
        root.addWidget(CaptionLabel(
            "解析失败的行与系统判定有疑问的行集中在同一张表：左侧筛选，右侧就地修正。"
            "「待修正」保存后立即重算并刷新；未处理的行在确认入库时自动跳过。"
            "双击任意行可对照原始台账行。"
        ))

        # ---- 筛选栏 ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._grp = QButtonGroup(self)
        self._grp.setExclusive(True)
        for i, name in enumerate(FILTERS):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setObjectName("chipBtn")
            b.setFixedHeight(26)
            self._grp.addButton(b, i)
            bar.addWidget(b)
        self._grp.button(0).setChecked(True)
        self._grp.idClicked.connect(lambda _i: self._render())
        bar.addSpacing(14)
        self.lbl_stat = CaptionLabel("")
        bar.addWidget(self.lbl_stat)
        bar.addStretch()
        root.addLayout(bar)

        # ---- 主体：左表 + 右面板 ----
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        self.table = TableWidget(self)
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setMinimumWidth(560)
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
        self.table.itemSelectionChanged.connect(self._load_right)
        self.splitter.addWidget(self.table)

        self._build_right_panel()
        self.splitter.addWidget(self.right)
        self.splitter.setHandleWidth(8)
        self.splitter.setSizes([820, 420])
        root.addWidget(self.splitter, 1)

        # ---- 底部 ----
        self.lbl_summary = CaptionLabel("")
        root.addWidget(self.lbl_summary)

        btns = QHBoxLayout()
        self.btn_confirm = PrimaryPushButton("确认入库")
        self.btn_confirm.clicked.connect(self.accept)
        b_cancel = PushButton("取消")
        b_cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(b_cancel)
        btns.addWidget(self.btn_confirm)
        root.addLayout(btns)

        self._init_cell_tooltip()
        self._rebuild()

    # ------------------------------------------------------------------ #
    # 右侧面板
    # ------------------------------------------------------------------ #
    def _build_right_panel(self) -> None:
        self.right = QWidget()
        self.right.setMinimumWidth(400)
        rv = QVBoxLayout(self.right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)

        card = QWidget()
        card.setObjectName("infoCard")
        cg = QGridLayout(card)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setSpacing(8)
        self._info: Dict[str, QLabel] = {}
        fields = [("来源", "src"), ("发票号", "no"), ("购方", "buyer"),
                  ("金额", "amt"), ("收款认定", "receipt"), ("备注", "remark")]
        for r, (label, key) in enumerate(fields):
            k = QLabel(label); k.setObjectName("infoKey")
            v = QLabel("—")
            v.setWordWrap(True)
            cg.addWidget(k, r, 0)
            cg.addWidget(v, r, 1)
            self._info[key] = v
        rv.addWidget(card)

        row_btn = QHBoxLayout()
        self.btn_source = PushButton("查看原始台账行")
        self.btn_source.clicked.connect(self._show_source)
        self.btn_edit_recv = PushButton("编辑各经办人已收")
        self.btn_edit_recv.clicked.connect(self._edit_received)
        row_btn.addWidget(self.btn_source)
        row_btn.addWidget(self.btn_edit_recv)
        row_btn.addStretch()
        rv.addLayout(row_btn)

        sep = QFrame(); sep.setObjectName("sep")
        sep.setFrameShape(QFrame.Shape.HLine); sep.setFixedHeight(1)
        rv.addWidget(sep)

        self.fix_panel = ProblemFixPanel(sorted(self._staff_set), self._period, self)
        rv.addWidget(self.fix_panel, 1)

        ab = QHBoxLayout()
        ab.setSpacing(8)
        self.btn_save = PushButton("保存修改")
        self.btn_save.clicked.connect(self._save_fix)
        self.btn_refix = PushButton("重新修正")
        self.btn_refix.clicked.connect(self._refix)
        self.btn_skiprow = PushButton("跳过此行")
        self.btn_skiprow.clicked.connect(self._skip_row)
        ab.addWidget(self.btn_save)
        ab.addWidget(self.btn_refix)
        ab.addWidget(self.btn_skiprow)
        ab.addStretch()
        rv.addLayout(ab)

    # ------------------------------------------------------------------ #
    # 行模型
    # ------------------------------------------------------------------ #
    def _rebuild_work(self) -> None:
        """按当前修正结果重建工作副本（真实 data 始终不被修改）。"""
        self._work = copy.deepcopy(self._data)
        if self._fix:
            from app.importer.importer import _apply_resolved
            _apply_resolved(
                self._work,
                [{"index": i, "action": "fix", "data": d} for i, d in sorted(self._fix.items())],
            )
        self._idx_to_p = {
            self._orig_inv_len + k: p_idx
            for k, p_idx in enumerate(sorted(self._fix))
        }

    def _rebuild(self) -> None:
        self._rebuild_work()
        evs = evaluate(self._work, self._staff_set)
        rows: List[Dict] = []
        for i, ev in enumerate(evs):
            inv_idx = i if i < self._orig_inv_len else None
            rows.append({
                "kind": "invoice",
                "status": "待确认" if ev["conf"] == "low" else "高置信",
                "p_index": self._idx_to_p.get(i),
                "inv_idx": inv_idx,
                "ev": ev,
                "problem": None,
            })
        for i, p in enumerate(self._data.get("problems", [])):
            if i in self._fix:
                # 发票类修正行已由 evaluate 产出，这里只补预收款类
                if p.get("kind") == "prepayment":
                    rows.append({"kind": "problem", "status": "已修正",
                                 "p_index": i, "inv_idx": None, "ev": None, "problem": p})
                continue
            rows.append({
                "kind": "problem",
                "status": "已跳过" if i in self._skip else "待修正",
                "p_index": i, "inv_idx": None, "ev": None, "problem": p,
            })
        rows.sort(key=lambda r: (_PRIO.get(r["status"], 9), r["p_index"] if r["p_index"] is not None else 1 << 30))
        self._rows = rows
        self._render()

    def _row_field(self, r: Dict, key: str) -> str:
        if r["kind"] == "invoice":
            ev = r["ev"]
            if key == "src":
                return f"{ev['sheet_label']} · 第{ev['row_no']}行"
            if key == "no":
                return ev["invoice_no"]
            if key == "buyer":
                return ev["buyer"] or "—"
            if key == "amt":
                return _fmt_money(ev["total_amount"])
            if key == "receipt":
                return ev["receipt_text"]
            if key == "remark":
                return (ev["_inv"].get("remark_raw") or "").replace("\n", " ").strip() or "—"
            if key == "handler":
                parsed = "、".join(f"{n} {_fmt_money(b)}" for n, b in ev["handlers"]) or "—"
                src = (ev["_inv"].get("handler_text") or "").replace("\n", " ").strip()
                return f"{parsed}　〔源填写〕{src}" if src else parsed
            if key == "reason":
                return "、".join(ev["reasons"]) if ev["reasons"] else "✓ 系统判定无疑问"
            if key == "kind":
                return "红字发票" if ev["is_red"] else "发票"
        p = r["problem"] or {}
        if key == "src":
            return f"{SHEET_LABEL.get(p.get('sheet'), p.get('sheet') or '—')} · 第{p.get('row_no', '')}行"
        if key == "no":
            return p.get("invoice_no") or "—"
        if key == "buyer":
            return p.get("buyer") or "—"
        if key == "amt":
            return p.get("total_amount") or p.get("amount_text") or "—"
        if key == "receipt":
            return "—"
        if key == "remark":
            return (p.get("remark_raw") or "").replace("\n", " ").strip() or "—"
        if key == "handler":
            return (p.get("handler_text") or "").replace("\n", " ").strip() or "—"
        if key == "reason":
            return p.get("reason") or "—"
        if key == "kind":
            return "预收款" if p.get("kind") == "prepayment" else "发票"
        return ""

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        filt = FILTERS[self._grp.checkedId()]
        rows = [r for r in self._rows if self._match(r, filt)]
        sel_key = self._selected_key()

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r_i, r in enumerate(rows):
            vals = [
                r["status"], self._row_field(r, "kind"), self._row_field(r, "src"),
                self._row_field(r, "no"), self._row_field(r, "buyer"),
                self._row_field(r, "amt"), self._row_field(r, "handler"),
                self._recv_text(r), self._row_field(r, "receipt"),
                self._row_field(r, "remark"), self._row_field(r, "reason"),
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 5:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == COL_STATUS:
                    fg = {"待修正": RED, "待确认": AMBER, "高置信": GREEN}.get(r["status"], GRAY)
                    item.setForeground(fg)
                if c == COL_REASON and r["kind"] == "invoice":
                    item.setForeground(GREEN if not r["ev"]["reasons"] else AMBER)
                if r["status"] == "待修正":
                    item.setBackground(DIFF_BG)
                if r["status"] == "已跳过":
                    item.setForeground(GRAY)
                self.table.setItem(r_i, c, item)
            self.table.setRowHeight(r_i, scale.px(34))
            self.table.item(r_i, 0).setData(Qt.ItemDataRole.UserRole, id(r))
        self.table.blockSignals(False)

        auto_fit_columns(self.table, max_width=210)
        self.table.setColumnWidth(COL_HANDLER, scale.px(230))
        self.table.setColumnWidth(COL_RECV, scale.px(220))
        self.table.setColumnWidth(COL_REMARK, scale.px(170))
        self._refresh_tip_data()

        n_fix = sum(1 for r in self._rows if r["status"] == "待修正")
        n_low = sum(1 for r in self._rows if r["status"] == "待确认")
        n_high = sum(1 for r in self._rows if r["status"] == "高置信")
        n_skip = sum(1 for r in self._rows if r["status"] == "已跳过")
        n_done = sum(1 for r in self._rows if r["status"] == "已修正")
        self.lbl_stat.setText(
            f"待修正 {n_fix}　待确认 {n_low}　高置信 {n_high}"
            + (f"　已修正 {n_done}" if n_done else "")
            + (f"　已跳过 {n_skip}" if n_skip else "")
        )

        inv_total = sum(r["ev"]["total_amount"] for r in self._rows if r["kind"] == "invoice")
        pp = self._work.get("prepayments", [])
        pp_total = sum(x.get("amount", 0.0) for x in pp)
        self.lbl_summary.setText(
            f"发票 {n_high + n_low + n_done} 张，合计 ¥{inv_total:,.2f}　"
            f"预收款 {len(pp)} 条，合计 ¥{pp_total:,.2f}"
        )

        if sel_key is not None:
            for i, r in enumerate(rows):
                if id(r) == sel_key:
                    self.table.selectRow(i)
                    break
        if self.table.rowCount() and self.table.currentRow() < 0:
            self.table.selectRow(0)
        self._load_right()

    @staticmethod
    def _match(r: Dict, filt: str) -> bool:
        if filt == "全部":
            return True
        if filt == "需处理":
            return r["status"] in ("待修正", "待确认")
        return r["status"] == filt

    def _selected_key(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _recv_text(self, r: Dict) -> str:
        if r["kind"] != "invoice":
            return "—（修正后生成）"
        ev = r["ev"]
        ov = self._ov.get(r["inv_idx"], {}) if r["inv_idx"] is not None else {}
        parts = []
        for name, _b in ev.get("handlers", []):
            amt = ov.get(name, ev.get("system_received", {}).get(name, 0.0))
            parts.append(f"{name} {_fmt_money(amt)}")
        return "、".join(parts) or "—"

    # ---- 单元格 tooltip（内容被列宽裁剪时悬停显示全文） ----
    _TIP_ROLE = Qt.ItemDataRole.UserRole + 1

    def _init_cell_tooltip(self) -> None:
        self._tip = QLabel(self)
        self._tip.setObjectName("cellTip")
        self._tip.setWindowFlag(Qt.WindowType.ToolTip, True)
        self._tip.hide()
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(self._pop_tip)
        self.table.setMouseTracking(True)
        self.table.cellEntered.connect(self._on_cell_entered)
        self.table.viewportEntered.connect(self._hide_tip)

    def _refresh_tip_data(self) -> None:
        fm = self.table.fontMetrics()
        pad = 14
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item is None:
                    continue
                text = item.text()
                if text and fm.horizontalAdvance(text) > self.table.columnWidth(c) - pad:
                    item.setData(self._TIP_ROLE, text)

    def _on_cell_entered(self, row: int, col: int) -> None:
        self._tip_timer.stop()
        item = self.table.item(row, col)
        if item and item.data(self._TIP_ROLE):
            self._tip_row, self._tip_col = row, col
            self._tip_timer.start(120)
        else:
            self._hide_tip()

    def _pop_tip(self) -> None:
        item = self.table.item(getattr(self, "_tip_row", -1), getattr(self, "_tip_col", -1))
        text = item.data(self._TIP_ROLE) if item else None
        if not text:
            return
        self._tip.setText(text)
        self._tip.adjustSize()
        self._tip.move(QCursor.pos() + QPoint(14, 18))
        self._tip.show()

    def _hide_tip(self) -> None:
        self._tip_timer.stop()
        self._tip.hide()

    # ------------------------------------------------------------------ #
    # 右侧交互
    # ------------------------------------------------------------------ #
    def _current_row(self) -> Dict | None:
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        for r in self._rows:
            if id(r) == key:
                return r
        return None

    def _load_right(self) -> None:
        r = self._current_row()
        if r is None:
            self.fix_panel.set_problem(None)
            self._set_actions(False, False)
            return
        for key in ("src", "no", "buyer", "amt", "receipt", "remark"):
            self._info[key].setText(self._row_field(r, key))

        can_fix = r["p_index"] is not None and r["kind"] == "problem"
        is_fixed_invoice = r["p_index"] is not None and r["kind"] == "invoice"
        self.fix_panel.setVisible(can_fix)
        self._set_actions(can_fix, is_fixed_invoice)

        if can_fix:
            self.fix_panel.set_problem(r["problem"])
        else:
            self.fix_panel.set_problem(None)
        self.btn_edit_recv.setVisible(r["kind"] == "invoice" and r["inv_idx"] is not None)

    def _set_actions(self, fixing: bool, refix: bool) -> None:
        self.btn_save.setVisible(fixing)
        self.btn_skiprow.setVisible(fixing)
        self.btn_refix.setVisible(refix)

    def _show_source(self) -> None:
        r = self._current_row()
        if r is None:
            return
        if r["kind"] == "invoice":
            inv = r["ev"]["_inv"]
            header = inv.get("header") or []
            raw_row = inv.get("raw_row") or []
            sheet_name = inv.get("sheet_name") or "—"
            row_no = inv.get("row_no") or 0
        else:
            p = r["problem"] or {}
            header = p.get("header") or []
            raw_row = p.get("raw_row") or []
            sheet_name = SHEET_LABEL.get(p.get("sheet"), p.get("sheet") or "—")
            row_no = p.get("row_no") or 0
        if not raw_row:
            QMessageBox.information(self, "无原始行", "该行没有对应的原始台账行数据。")
            return
        show_ledger_source(
            self, header=header, raw_row=raw_row, sheet_name=sheet_name,
            row_no=row_no, archive_path=self._path, file_name=self._file_name,
        )

    def _cell_double_clicked(self, r_i: int, c: int) -> None:
        row = self._rows and self._current_row()
        if row is None:
            return
        if c == COL_RECV and row["kind"] == "invoice" and row["inv_idx"] is not None:
            self._edit_received()
            return
        self._show_source()

    def _edit_received(self) -> None:
        r = self._current_row()
        if r is None or r["kind"] != "invoice" or r["inv_idx"] is None:
            return
        ev = r["ev"]
        no = ev["invoice_no"]
        tmp = {no: dict(self._ov.get(r["inv_idx"], {}))}
        dlg = HandlerReceivedDialog(ev, tmp, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._ov[r["inv_idx"]] = tmp.get(no, {})
            self._render()

    def _save_fix(self) -> None:
        r = self._current_row()
        if r is None or r["p_index"] is None:
            return
        try:
            data = self.fix_panel.read_fix()
        except ImportError_ as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        self._fix[r["p_index"]] = data
        self._skip.discard(r["p_index"])
        self._rebuild()

    def _refix(self) -> None:
        """已修正的发票行：回到修正形态继续编辑。"""
        r = self._current_row()
        if r is None or r["p_index"] is None:
            return
        p = self._data["problems"][r["p_index"]]
        self.fix_panel.setVisible(True)
        self._set_actions(True, False)
        self.fix_panel.set_problem(p)

    def _skip_row(self) -> None:
        r = self._current_row()
        if r is None or r["p_index"] is None:
            return
        self._skip.add(r["p_index"])
        self._fix.pop(r["p_index"], None)
        self._rebuild()

    # ------------------------------------------------------------------ #
    # 确认入库
    # ------------------------------------------------------------------ #
    def _merged_data(self) -> Dict:
        """把修正结果与已收覆盖值合并进 data 的副本（真实 data 不受影响）。"""
        merged = copy.deepcopy(self._data)
        from app.importer.importer import _apply_resolved
        problems = merged.get("problems", [])
        if problems:
            resolved = [
                {"index": i, "action": "fix", "data": self._fix[i]}
                if i in self._fix else {"index": i, "action": "skip", "data": None}
                for i in range(len(problems))
            ]
            _apply_resolved(merged, resolved)
        merged["problems"] = []

        # 已收覆盖值按「原始解析下标」回写（修正行的收款已包含在修正数据里）
        invoices = merged.get("invoices", [])
        for inv_idx, ov in self._ov.items():
            if inv_idx is None or inv_idx >= len(invoices):
                continue
            ev = next((r["ev"] for r in self._rows
                       if r["kind"] == "invoice" and r["inv_idx"] == inv_idx), None)
            if ev is None:
                continue
            invoices[inv_idx]["received_overrides"] = {
                name: v for name, v in ov.items()
                if abs(v - ev["system_received"].get(name, 0.0)) > 0.005
            }
        return merged

    def accept(self) -> None:
        todo = [r for r in self._rows if r["status"] == "待修正"]
        if todo:
            msg = (f"还有 {len(todo)} 行未处理，确认入库时这些行将被跳过（不入库）。\n\n"
                   "是否继续？")
            if QMessageBox.question(
                self, "存在未处理的问题行", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return

        merged = self._merged_data()
        if self._validate is not None:
            err = self._validate(merged)
            if err:
                QMessageBox.warning(self, "校验未通过", str(err))
                return

        # 校验通过才提交：原地替换调用方持有的同一个 dict
        self._data.clear()
        self._data.update(merged)
        super().accept()
