"""发票台账导入 — 统一确认对话框（问题修正 + 预览确认 合二为一）

背景：原先是两个独立弹窗
    1) 问题行修正 ProblemDialog  → 只能修，看不到源文件备注/经办人原文/收款认定
    2) 导入预览确认 PreviewDialog → 只能看与改已收，解析失败的行已被处理掉、无法回头改
本对话框把两者合并为「一张表 + 右侧就地修正」：

- 一张表承载全部行，状态分三类：待确认（解析失败 + 低置信，合并）/ 高置信 / 已跳过
- 顶部筛选胶囊（默认「待确认」）：待确认 / 已确认 / 高置信 / 已跳过 / 全部
  - 「已确认」与「高置信」重叠：已修正、点「确认」、或编辑过的发票均属高置信且归入已确认
- 选中「待确认」行（解析失败或低置信）→ 右侧内嵌 ProblemFixPanel 就地修正/编辑；
  保存或点「确认」后立即合并进工作副本、重算置信度并刷新，该行归入「已确认 / 高置信」
- 双击任意行 → 查看原始台账行（问题行也能看，依赖 problem 携带 header/raw_row）
- 待确认行：右侧可编辑「各经办人已收」，也可直接点「确认」认可系统默认收款口径
  （压制 sheet1/2/3 的三类兜底判定疑问，移出「需处理」）；若另有硬疑问仍需先修正数据
- 高置信行：默认只读（防随手改坏），点「编辑」才解锁表单
- 确认入库：未处理的问题行自动跳过；已收覆盖值按发票下标回写真实数据

数据流：真实 data 全程不被修改，修正只作用于工作副本；点「确认入库」时才一次性
把 resolved 合并进真实 data。点「取消」真实 data 保持原样。
"""
from __future__ import annotations

import copy
from typing import Dict, List

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
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
from app.ui.preview_dialog import _fmt_money
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

FILTERS = ["待确认", "已确认", "高置信", "已跳过", "全部"]
_PRIO = {"待确认": 0, "高置信": 1, "已跳过": 2}


class UnifiedImportDialog(QWidget):
    """发票台账导入统一确认面板（可作为独立对话框，也可嵌入导入页的 tab）。

    confirmed 信号：用户点「确认入库」且校验通过后发出（self._data 已被合并为最终结果）。
    cancelled 信号：用户点「取消」发出。
    也可直接调用 accept() 同步拿到合并结果（保留旧调用方式，便于测试）。
    """

    confirmed = Signal()
    cancelled = Signal()

    def __init__(self, data=None, period="", staff_names=None,
                 parent=None, path: str = "", validator=None) -> None:
        """validator: 可选 callable(data) -> str | None（确认入库时先做写前校验）。

        作为对话框：直接传 data/staff_names 即可使用。
        作为嵌入面板：可只传 parent，随后调用 load_data(...) 载入待确认数据。
        """
        super().__init__(parent)
        # 数据相关的状态在 load_data 中初始化；这里给占位默认值以便无数据时也能构造
        self._data: Dict = {}
        # 载入时的原始快照（深拷贝）：accept() 会把 _data 原地替换为合并结果，
        # 之后 collect_import_fixes 若再读 _data 取"旧值"就会拿到新值（差异全部消失、
        # 留痕丢失）。故单独保留这份未被修改的原始数据供取旧值。
        self._orig_data: Dict = {}
        self._period = period
        self._path = path
        self._validate = validator
        self._file_name = path.replace("\\", "/").split("/")[-1] if path else "导入文件"
        self._staff_set: set = set(staff_names or [])
        self._orig_inv_len = 0
        self._fix: Dict[int, dict] = {}          # problem index -> 修正数据
        self._skip: set = set()                  # problem index -> 跳过
        self._ov: Dict[int, Dict[str, float]] = {}  # invoice 下标 -> {经办人: 已收}
        self._inv_edits: Dict[int, dict] = {}    # work invoice 下标 -> 表单编辑结果（原地更新）
        self._idx_to_p: Dict[int, int] = {}      # work invoices 下标 -> problem index
        self._confirmed: set = set()             # 已点「确认」的 work invoice 下标集合
        self._rows: List[Dict] = []
        self.fix_panel = None

        self.setWindowTitle(f"发票台账导入确认 — {period}")
        if self.isWindow():
            self.resize(1240, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        # 页面级标题与说明由宿主 ImportReviewView 的 PageHeader 提供（本面板仅嵌入使用），
        # 此处不再自带标题，避免双重页头；原说明文字已并入页头「?」tooltip。

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
        self.splitter.setSizes([760, 560])
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

        if data is not None:
            self.load_data(data, period, staff_names, path, validator)
        else:
            self._rebuild()

    # ------------------------------------------------------------------ #
    # 数据载入（嵌入面板可二次调用）
    # ------------------------------------------------------------------ #
    def load_data(self, data, period="", staff_names=None,
                  path="", validator=None) -> None:
        """（重新）载入待确认数据并重建界面。

        作为嵌入面板时，可先以空参 __init__ 构造，再多次调用本方法切换数据源。
        self._data 直接引用传入的 data（同一对象），便于 accept() 原地合并回写。
        """
        self._data = data
        # 原始快照：供 collect_import_fixes 取「旧值」（accept 会覆盖 _data，必须另存）
        self._orig_data = copy.deepcopy(data)
        self._period = period
        self._path = path
        self._validate = validator
        self._file_name = path.replace("\\", "/").split("/")[-1] if path else "导入文件"
        self._staff_set = set(staff_names or [])
        self._orig_inv_len = len(data.get("invoices", []))
        self._fix = {}
        self._skip = set()
        self._ov = {}
        self._inv_edits = {}
        self._idx_to_p = {}
        self._confirmed = set()
        self._rows = []
        self.setWindowTitle(f"发票台账导入确认 — {period}")
        self._rebuild_fix_panel()
        self._rebuild()

    def _rebuild_fix_panel(self) -> None:
        """按当前 staff_set 重建右侧修正表单（staff 变化时需重建下拉）。"""
        if self.fix_panel is not None:
            # 先隐藏 + 仅从布局移除，保持父子关系：切勿用 setParent(None)——那会把面板
            # 脱离成独立顶层窗口，导入时每重建一次就冒出一个游离弹窗（曾导致批量导入弹窗）。
            self.fix_panel.hide()
            self._fix_host_ly.removeWidget(self.fix_panel)
            self.fix_panel.deleteLater()
        self.fix_panel = ProblemFixPanel(sorted(self._staff_set), self._period, self)
        self._fix_host_ly.addWidget(self.fix_panel, 1)

    # ------------------------------------------------------------------ #
    # 右侧面板
    # ------------------------------------------------------------------ #
    def _build_right_panel(self) -> None:
        self.right = QWidget(self)
        self.right.setMinimumWidth(400)
        rv = QVBoxLayout(self.right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)

        card = QWidget(self.right)
        card.setObjectName("infoCard")
        cg = QGridLayout(card)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setSpacing(8)
        self._info: Dict[str, QLabel] = {}
        fields = [("来源", "src"), ("发票号", "no"), ("购方", "buyer"),
                  ("金额", "amt"), ("收款认定", "receipt"), ("备注", "remark"),
                  ("原因 / 疑问", "reason")]
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
        row_btn.addWidget(self.btn_source)
        row_btn.addStretch()
        rv.addLayout(row_btn)

        sep = QFrame(); sep.setObjectName("sep")
        sep.setFrameShape(QFrame.Shape.HLine); sep.setFixedHeight(1)
        rv.addWidget(sep)

        self._fix_host = QWidget(self.right)
        self._fix_host_ly = QVBoxLayout(self._fix_host)
        self._fix_host_ly.setContentsMargins(0, 0, 0, 0)
        self._fix_host_ly.setSpacing(0)
        self.fix_panel = ProblemFixPanel(sorted(self._staff_set), self._period, self)
        self._fix_host_ly.addWidget(self.fix_panel, 1)
        rv.addWidget(self._fix_host, 1)

        ab = QHBoxLayout()
        ab.setSpacing(8)
        self.btn_save = PushButton("保存修改")
        self.btn_save.clicked.connect(self._save_fix)
        self.btn_refix = PushButton("重新修正")
        self.btn_refix.clicked.connect(self._refix)
        self.btn_confirm_row = PushButton("确认")
        self.btn_confirm_row.clicked.connect(self._confirm_row)
        self.btn_edit = PushButton("编辑")
        self.btn_edit.clicked.connect(self._enter_edit_mode)
        self.btn_skiprow = PushButton("跳过此行")
        self.btn_skiprow.clicked.connect(self._skip_row)
        ab.addWidget(self.btn_save)
        ab.addWidget(self.btn_refix)
        ab.addWidget(self.btn_confirm_row)
        ab.addWidget(self.btn_edit)
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
                self._period,
            )
        # 已存在发票的右侧表单就地编辑（_inv_edits）原地覆盖，不追加
        for inv_idx, ed in self._inv_edits.items():
            invs = self._work.get("invoices", [])
            if 0 <= inv_idx < len(invs):
                self._apply_invoice_edit(invs[inv_idx], ed)
        self._idx_to_p = {
            self._orig_inv_len + k: p_idx
            for k, p_idx in enumerate(sorted(self._fix))
        }

    @staticmethod
    def _apply_invoice_edit(inv: dict, ed: dict) -> None:
        """把右侧表单的编辑结果原地写回一条已存在的发票。"""
        inv["invoice_date"] = ed.get("invoice_date") or ""
        inv["total_amount"] = ed.get("total_amount") or 0.0
        inv["handlers"] = list(ed.get("handlers") or [])
        inv["handler_text"] = ed.get("handler_text") or ""
        inv["split_receipts"] = list(ed.get("split_receipts") or [])
        if ed.get("buyer") is not None:
            inv["buyer"] = ed.get("buyer")
        if ed.get("case_no") is not None:
            inv["case_no"] = ed.get("case_no")
        # 收款已由 split_receipts 显式接管，清空旧的备注推导与覆盖值
        rem = inv.get("remark") or {}
        rem.pop("receipts", None)
        rem.pop("pure_date", None)
        inv["remark"] = rem
        inv.pop("received_overrides", None)

    def _rebuild(self) -> None:
        anchor = self._current_anchor()
        self._rebuild_work()
        evs = evaluate(self._work, self._staff_set, self._confirmed, self._period)
        rows: List[Dict] = []
        for i, ev in enumerate(evs):
            inv_idx = i if i < self._orig_inv_len else None
            rows.append({
                "kind": "invoice",
                "status": "待确认" if ev["conf"] == "low" else "高置信",
                "p_index": self._idx_to_p.get(i),
                "inv_idx": inv_idx,
                "work_idx": i,
                "ev": ev,
                "problem": None,
            })
        for i, p in enumerate(self._data.get("problems", [])):
            if i in self._fix:
                # 已修正：归入高置信，并标记「已确认」
                rows.append({"kind": "problem", "status": "高置信",
                             "p_index": i, "inv_idx": None, "work_idx": None,
                             "ev": None, "problem": p})
                continue
            rows.append({
                "kind": "problem",
                "status": "已跳过" if i in self._skip else "待确认",
                "p_index": i, "inv_idx": None, "work_idx": None,
                "ev": None, "problem": p,
            })
        # 标记「已确认」类别：已修正 / 点「确认」/ 编辑过的发票（均属高置信）
        for r in rows:
            confirmed = False
            if r["kind"] == "problem" and r["p_index"] in self._fix:
                confirmed = True
            elif r["kind"] == "invoice" and r["inv_idx"] is not None and (
                    r["inv_idx"] in self._confirmed or r["inv_idx"] in self._inv_edits):
                confirmed = True
            r["is_confirmed"] = confirmed
        rows.sort(key=lambda r: (_PRIO.get(r["status"], 9), r["p_index"] if r["p_index"] is not None else 1 << 30))
        self._rows = rows
        self._render(anchor)

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
    def _render(self, anchor: tuple | None = None) -> None:
        if anchor is None:
            anchor = self._current_anchor()
        filt = FILTERS[self._grp.checkedId()]
        rows = [r for r in self._rows if self._match(r, filt)]

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
                    fg = {"待确认": AMBER, "高置信": GREEN}.get(r["status"], GRAY)
                    item.setForeground(fg)
                if c == COL_REASON and r["kind"] == "invoice":
                    item.setForeground(GREEN if not r["ev"]["reasons"] else AMBER)
                if r["status"] == "待确认":
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

        n_pending = sum(1 for r in self._rows if r["status"] == "待确认")
        n_high = sum(1 for r in self._rows if r["status"] == "高置信")
        n_skip = sum(1 for r in self._rows if r["status"] == "已跳过")
        n_confirmed = sum(1 for r in self._rows if r.get("is_confirmed"))
        self.lbl_stat.setText(
            f"待确认 {n_pending}　高置信 {n_high}"
            + (f"　已确认 {n_confirmed}" if n_confirmed else "")
            + (f"　已跳过 {n_skip}" if n_skip else "")
        )

        inv_total = sum(r["ev"]["total_amount"] for r in self._rows if r["kind"] == "invoice")
        pp = self._work.get("prepayments", [])
        pp_total = sum(x.get("amount", 0.0) for x in pp)
        # sheet3 期外票：本次不落库，转入「补录原票」逐张确认（方案 A）
        n_def = len(self._work.get("deferred", []) or [])
        self.lbl_summary.setText(
            f"发票 {n_high + n_pending} 张，合计 ¥{inv_total:,.2f}　"
            f"预收款 {len(pp)} 条，合计 ¥{pp_total:,.2f}"
            + (f"　·　应收账款期外 {n_def} 张已转入补录原票" if n_def else "")
        )

        sel_row = -1
        if anchor is not None:
            for i, r in enumerate(rows):
                if r["kind"] == "invoice" and anchor[0] == "inv" and r.get("work_idx") == anchor[1]:
                    sel_row = i
                    break
                if r["kind"] == "problem" and anchor[0] == "prob" and r["p_index"] == anchor[1]:
                    sel_row = i
                    break
        self.table.blockSignals(True)
        if sel_row >= 0:
            self.table.selectRow(sel_row)
        elif self.table.rowCount() and self.table.currentRow() < 0:
            self.table.selectRow(0)
        self.table.blockSignals(False)
        self._load_right()

    @staticmethod
    def _match(r: Dict, filt: str) -> bool:
        if filt == "全部":
            return True
        if filt == "待确认":
            return r["status"] == "待确认"
        if filt == "已确认":
            return bool(r.get("is_confirmed"))
        if filt == "高置信":
            return r["status"] == "高置信"
        if filt == "已跳过":
            return r["status"] == "已跳过"
        return False

    def _current_anchor(self):
        r = self._current_row()
        if r is None:
            return None
        if r["kind"] == "invoice":
            return ("inv", r.get("work_idx"))
        return ("prob", r["p_index"])

    def _recv_text(self, r: Dict) -> str:
        if r["kind"] != "invoice":
            return "—（修正后生成）"
        ev = r["ev"]
        parts = []
        for name, _b in ev.get("handlers", []):
            amt = ev.get("system_received", {}).get(name, 0.0)
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
            self.fix_panel.setVisible(False)
            self._set_actions()
            return
        for key in ("src", "no", "buyer", "amt", "receipt", "remark", "reason"):
            self._info[key].setText(self._row_field(r, key))

        if r["kind"] == "invoice":
            # 所有发票行（高/低/已修正）均在右侧就地编辑（方案 A：统一右栏）
            self.fix_panel.setVisible(True)
            self.fix_panel.set_invoice(r["ev"]["_inv"], r["ev"])
            if r["ev"]["conf"] == "high":
                # 高置信：默认只读，防止被随手改坏；点「编辑」才解锁
                self.fix_panel.set_readonly(True)
                self._set_actions(edit=True)
            else:
                # 低置信（待确认）：可保存修改，也可点「确认」认可系统默认口径
                self.fix_panel.set_readonly(False)
                self._set_actions(save=True, confirm=True)
        else:
            # 待修正 / 已跳过 问题行仍走 set_problem 修正路径（始终可编辑）
            self.fix_panel.setVisible(True)
            self.fix_panel.set_problem(r["problem"])
            self._set_actions(save=True, skip=(r["status"] == "待确认"))

    def _set_actions(self, *, save: bool = False, skip: bool = False,
                     refix: bool = False, confirm: bool = False,
                     edit: bool = False) -> None:
        self.btn_save.setVisible(save)
        self.btn_skiprow.setVisible(skip)
        self.btn_refix.setVisible(refix)
        self.btn_confirm_row.setVisible(confirm)
        self.btn_edit.setVisible(edit)

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
        self._show_source()

    def _save_fix(self) -> None:
        r = self._current_row()
        if r is None:
            return
        try:
            data = self.fix_panel.read_fix()
        except ImportError_ as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        if r["kind"] == "invoice" and r["inv_idx"] is not None:
            # 已存在发票：右侧表单原地更新（方案 A），不追加、不覆盖整表
            self._inv_edits[r["inv_idx"]] = data
            self._rebuild()
        elif r["p_index"] is not None:
            # 待修正问题行 / 已修正行（源自问题修正）：走追加 / 覆盖修正路径
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
        self._set_actions(save=True)
        self.fix_panel.set_problem(p)

    def _enter_edit_mode(self) -> None:
        """高置信行点「编辑」后解锁表单（默认只读态）。"""
        r = self._current_row()
        if r is None or r["kind"] != "invoice":
            return
        self.fix_panel.set_readonly(False)
        self._set_actions(save=True)

    def _confirm_row(self) -> None:
        """待确认行点「确认」：认可系统默认收款口径，移出「需处理」。

        仅压制 sheet1/2/3 的三类「兜底判定」提示；若该行另有无法确认的硬疑问
        （如经办人不在花名册、分摊不平），确认无法消除，需先「保存修改」修正数据。
        """
        r = self._current_row()
        if r is None or r["kind"] != "invoice" or r["work_idx"] is None:
            return
        self._confirmed.add(r["work_idx"])
        self._rebuild()
        row = next((x for x in self._rows
                    if x["kind"] == "invoice" and x.get("work_idx") == r["work_idx"]), None)
        if row is not None and row["ev"]["conf"] == "low":
            residual = "、".join(row["ev"]["reasons"]) or "其它疑问"
            QMessageBox.information(
                self, "仍有未确认的疑问",
                f"该行的「兜底判定」已确认，但还残留以下硬疑问，确认无法消除：\n\n"
                f"　{residual}\n\n请点「保存修改」修正数据后再确认。")

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
            _apply_resolved(merged, resolved, self._period)
        merged["problems"] = []

        # 已存在发票的右侧就地编辑：原地覆盖
        for inv_idx, ed in self._inv_edits.items():
            invs = merged.get("invoices", [])
            if 0 <= inv_idx < len(invs):
                self._apply_invoice_edit(invs[inv_idx], ed)

        # 已收覆盖值按「原始解析下标」回写（历史弹窗路径，现已停用，保留兼容）
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
        todo = [r for r in self._rows if r["status"] == "待确认"]
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
        self.confirmed.emit()

    def reject(self) -> None:
        """用户点「取消」：真实 data 不被修改，发出 cancelled 信号。"""
        self.cancelled.emit()

    def collect_import_fixes(self) -> list:
        """把本次导入确认页的人工干预规范化为留痕条目（供 log_import_fixes）。

        每项 {"kind": "fix"|"edit", "invoice_no": str, "old": dict, "new": dict}
        - fix：修正的问题行（解析失败凭空填；old = 该行原始台账可读原文）
        - edit：右栏就地编辑的已解析行（old = 原解析值）

        旧值一律取自 `self._orig_data`（load_data 时的深拷贝）：accept() 已把
        `self._data` 原地替换为合并结果，若再读 _data 会拿到新值 → 逐字段差异被判
        「无变化」而不留痕（旧值缺失、新值只剩摘要）。故必须用未被修改的原始快照。
        """
        items = []
        oinv = (self._orig_data or {}).get("invoices", [])
        oprob = (self._orig_data or {}).get("problems", [])
        for p_idx, data in sorted(self._fix.items()):
            p = oprob[p_idx] if 0 <= p_idx < len(oprob) else {}
            items.append({
                "kind": "fix",
                "invoice_no": str(data.get("invoice_no")
                                  or p.get("invoice_no") or "").strip(),
                # 问题行原始台账原文（供修改记录页展示「旧值」）
                "old": {
                    "invoice_date": str(p.get("date_text") or "").strip(),
                    "total_amount": str(p.get("total_amount") or "").strip(),
                    "handler_text": str(p.get("handler_text") or "").strip(),
                    "buyer": str(p.get("buyer") or "").strip(),
                    "remark_raw": str(p.get("remark_raw") or "").strip(),
                },
                "new": data,
            })
        for inv_idx, data in sorted(self._inv_edits.items()):
            if not (0 <= inv_idx < len(oinv)):
                continue
            old = dict(oinv[inv_idx])
            no = str(data.get("invoice_no") or old.get("invoice_no") or "").strip()
            items.append({"kind": "edit", "invoice_no": no, "old": old, "new": data})
        return items
