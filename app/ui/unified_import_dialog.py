"""发票台账导入 — 统一确认对话框（问题修正 + 预览确认 合二为一）

背景：原先是两个独立弹窗
    1) 问题行修正 ProblemDialog  → 只能修，看不到源文件备注/经办人原文/收款认定
    2) 导入预览确认 PreviewDialog → 只能看与改已收，解析失败的行已被处理掉、无法回头改
本对话框把两者合并为「一张表 + 右侧就地修正」：

- 一张表承载全部行，状态分四类：**待补录**（应收账款原票不在库且本次未补录）/ 待确认
  （解析失败 + 低置信 + 应收账款待确认收款）/ 高置信 / 已跳过
- 顶部筛选胶囊（**默认「待补录」**，阶段 4-2 B 甲）：待补录 / 待确认 / 已确认 / 高置信 /
  已跳过 / 全部。排序按「待补录 → 待确认 → 高置信 → 已跳过」，故待补录排最前（C 甲）
  - 「已确认」与「高置信」重叠：已修正、点「确认」、或编辑过的发票均属高置信且归入已确认
- 选中「待确认」行（解析失败或低置信）→ 右侧内嵌 ProblemFixPanel 就地修正/编辑；
  保存或点「确认」后立即合并进工作副本、重算置信度并刷新，该行归入「已确认 / 高置信」
- 双击任意行 → 查看原始台账行（问题行也能看，依赖 problem 携带 header/raw_row）
- 待确认行：右侧可编辑「各经办人已收」，也可直接点「确认」认可系统默认收款口径
  （压制 sheet1/2/3 的三类兜底判定疑问，移出「需处理」）；若另有硬疑问仍需先修正数据
- 高置信行：默认只读（防随手改坏），点「编辑」才解锁表单
- **应收账款(sheet3)行**（阶段 2 A1/A2/A3 + 阶段 4-2）：`data["deferred"]` 的每一行也进本表，
  与发票行并列。状态/原因（阶段 4-2 起四态）：
  · **待补录**：票号不在库、且本次**还没填过**补录 → 原因 `需补录原票`（琥珀）；
  · **待确认**：票号已在库（原因 `已入库，请确认收款`）或已填过补录（原因
    `已补录，请确认收款`）—— 两者都要「确认收款信息」之后才离开；
  · **高置信**：已确认（或已就地编辑并确认）；
  · 普通发票行无疑问时原因列显示 `✓ 系统判定无疑问`。
  应收账款行复用同一 `ProblemFixPanel` 就地修改（A3，支持同名多行 = 多期收款；预填展开与
  补录页同源，见 `app.engine.raw_ledger.expand_receipts`），保存结果回写 `data["deferred"]`：
  · 已入库行 → 同时置 `receipt_confirmed`，入库时由 `commit_ledger_import`（A10）
    **追加**该票收款（仅本批，绝不删历史）；
  · 需补录行 → 只记录修正结果（原票入库属补录流程），不入库任何收款。
- 确认入库：未处理的问题行自动跳过；已收覆盖值按发票下标回写真实数据
- **行内「补录原票」按钮**（阶段 3 B2g/B2h；阶段 4-2 改三态文案）：右侧按钮行在
  「查看原始台账行」之后多一枚按钮，按当前行给出补录入口：
  · 未填过补录且原票不在库 → **「补录原票」**，弹 `BackfillDialog`（与「发票补录」页同一个类）；
  · **已填过补录 → 「修改补录」**（D 甲：点它确实是重新打开**可编辑**，而不是只读查看）；
  · 票已在库且未填过 → **「查看原票」**（只读查看，原票无需补录）。
  补录结果**不立即写库**，收集进 `data["backfills"]`（B2c），随台账**同一事务**入库
  （`commit_ledger_import`）；点「取消」一行都不写。
  **在补录弹窗里填的经办人/收款会写回该行**（`_deferred_edits`）→ 该行随即从「待补录」
  落到**「待确认」**，并在右栏与「各经办人已收」列**回显**刚填的内容（A 甲要求）；
  之后在「待确认」里再改，以**行的最终值**为准回写补录条目（`_merged_data`）。
  红字行的原票号用 `SELECT orig_invoice_no FROM invoice WHERE invoice_no=?` 反查
  （查 `invoice` 表 —— `raw_invoice` 无此列），取不到则按钮不出现。

数据流：真实 data 全程不被修改，修正只作用于工作副本；点「确认入库」时才一次性
把 resolved 合并进真实 data。点「取消」真实 data 保持原样。
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Dict, List

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine.backfill_module import load_invoice_detail, prefill_red_original
from app.engine.import_confidence import SHEET_LABEL, evaluate, receipt_summary
from app.engine.raw_ledger import expand_receipts
from app.importer.excel_reader import ImportError_
from app.ui import scale
from app.ui.backfill_dialog import BackfillDialog, backfill_validator
from app.ui.ledger_source import show_ledger_source
from app.ui.preview_dialog import _fmt_money
from app.ui.problem_fix_panel import ProblemFixPanel
from app.ui.table_view import auto_fit_columns
from app.ui.widgets import CaptionLabel, PrimaryPushButton, PushButton, TableWidget

RED = QColor("#C0392B")
AMBER = QColor("#B7791F")
GREEN = QColor("#1E8449")
BLUE = QColor("#2C6FBB")
GRAY = QColor("#8A8A85")
DIFF_BG = QColor("#FDF1F0")

# A2：应收账款(sheet3)行的「原因 / 疑问」三态文案（阶段 4-2 起）
REASON_BACKFILL = "需补录原票"
REASON_BACKFILLED = "已补录，请确认收款"
REASON_IN_LIBRARY = "已入库，请确认收款"
REASON_NO_DOUBT = "✓ 系统判定无疑问"

HEADERS = [
    "状态", "类型", "来源", "发票号", "购方", "金额",
    "经办人分摊", "各经办人已收(双击编辑)", "收款认定", "源文件备注", "原因 / 疑问",
]
COL_STATUS, COL_KIND, COL_SRC, COL_NO, COL_BUYER, COL_AMT, COL_HANDLER, \
    COL_RECV, COL_RECEIPT, COL_REMARK, COL_REASON = range(11)

# 阶段 4-2：筛选栏胶囊 —— 「待补录」排在「待确认」之前（C 甲），默认落在「待补录」（B 甲）
FILTERS = ["待补录", "待确认", "已确认", "高置信", "已跳过", "全部"]
_PRIO = {"待补录": 0, "待确认": 1, "高置信": 2, "已跳过": 3}


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
        # 应收账款(sheet3)行（阶段 2 A3）：deferred 下标 -> 表单编辑结果；
        # 以及用户「确认」过的 deferred 下标（已入库行据此置 receipt_confirmed → A10 写收款）
        self._deferred_edits: Dict[int, dict] = {}
        self._deferred_confirmed: set = set()
        # 阶段 3（B2c）：行内「补录原票」收集到的补录条目 —— 票号 → 条目
        # （不写库；随台账同一事务入库，点「取消」一行都不写）
        self._backfills: Dict[str, dict] = {}
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
        self._deferred_edits = {}
        self._deferred_confirmed = set()
        # B2c：以 data 里已有的 backfills 为起点（「入库不过 → 返回修改」会重载同一份
        # data，已填的补录不能丢；按钮态与「原因」列也据此还原）
        self._backfills = {
            (b.get("invoice_no") or "").strip(): b
            for b in (data.get("backfills") or [])
            if (b.get("invoice_no") or "").strip()
        }
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
        # 阶段 3（B2g）：行内补录入口 —— 文案随当前行在「补录原票 / 查看原票」间切换
        self.btn_backfill = PushButton("补录原票")
        self.btn_backfill.clicked.connect(self._open_backfill)
        self.btn_backfill.setVisible(False)
        row_btn.addWidget(self.btn_backfill)
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
    def _split_work(self, d: Dict) -> None:
        """保证 d["deferred"] 已就绪（幂等切分；与复核页 A5 载入刷新同源）。

        sheet3 行一律不进普通发票路径。正常流程里解析期与复核页载入时都已切过，
        这里兜底「直连构造」的调用方（测试 / 其它入口），使 A1 的行装配与 A3 的下标
        映射在两条路径上一致。切分失败不阻断界面（commit 前还会再切一次）。
        """
        from app.importer.ledger_import import split_deferred
        try:
            split_deferred(d, self._period)
        except Exception:  # noqa: BLE001
            pass

    def _rebuild_work(self) -> None:
        """按当前修正结果重建工作副本（真实 data 始终不被修改）。"""
        self._work = copy.deepcopy(self._data)
        from app.importer.importer import _apply_resolved
        if self._fix:
            _apply_resolved(
                self._work,
                [{"index": i, "action": "fix", "data": d} for i, d in sorted(self._fix.items())],
                self._period,
            )
        else:
            self._split_work(self._work)
        # 已存在发票的右侧表单就地编辑（_inv_edits）原地覆盖，不追加
        for inv_idx, ed in self._inv_edits.items():
            invs = self._work.get("invoices", [])
            if 0 <= inv_idx < len(invs):
                self._apply_invoice_edit(invs[inv_idx], ed)
        # 应收账款(sheet3)行的就地编辑（A3）：下标对应 data["deferred"] 顺序。
        # split_deferred 只「追加未见过的新行」、不重排既有行，故下标稳定。
        defs = self._work.get("deferred") or []
        for d_idx, ed in self._deferred_edits.items():
            if 0 <= d_idx < len(defs):
                self._apply_invoice_edit(defs[d_idx], ed)
        self._idx_to_p = {
            self._orig_inv_len + k: p_idx
            for k, p_idx in enumerate(sorted(self._fix))
        }

    @staticmethod
    def _apply_invoice_edit(inv: dict, ed: dict) -> None:
        """把右侧表单的编辑结果原地写回一条已存在的发票。

        应收账款(deferred)行结构与发票行同构（invoice_date / total_amount /
        handlers / handler_text / split_receipts / buyer / case_no / remark），
        故两者共用本函数；deferred 行独有的字段（sheet / row_no / header / raw_row /
        need_backfill …）不被触碰。
        """
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

    # ------------------------------------------------------------------ #
    # 应收账款(sheet3)行（deferred）：预填展开 / 收款明细
    # ------------------------------------------------------------------ #
    @staticmethod
    def _deferred_expand(d: dict):
        """应收账款行 → (面板预填行, 收款明细)。

        与补录页**同源**（`app.engine.raw_ledger.expand_receipts`）：支持多期收款
        展开成「同名多行」，开票额合计仍等于开票总额（D3 甲 / A11）。
        """
        hs = [tuple(h) for h in (d.get("handlers") or [])]
        dicts, split = expand_receipts(hs, d.get("remark") or {}, d.get("total_amount") or 0.0)
        rows = [
            {"name": h["name"], "bill": h["billing"],
             "recv_amt": ("" if not h["received"] else _fmt_money(h["received"])),
             "recv_date": h["date"]}
            for h in dicts
        ]
        return rows, split

    def _deferred_payload(self, d: dict):
        """应收账款行 → (面板预填行, 收款明细)。

        已显式编辑过的行（有 `split_receipts`）以显式值为准（remark 已被清空，
        再走备注推导会丢收款）；否则按台账备注展开。
        """
        split = list(d.get("split_receipts") or [])
        if split:
            from app.ui.problem_fix_panel import rows_from_handlers
            return rows_from_handlers(list(d.get("handlers") or []), split), split
        return self._deferred_expand(d)

    def _deferred_recv_text(self, d: dict) -> str:
        """应收账款行的「各经办人已收」列（按姓名合计多期收款）。"""
        _rows, split = self._deferred_payload(d)
        agg: Dict[str, float] = defaultdict(float)
        for name, amt, _ym in split:
            agg[name] += amt or 0.0
        return "、".join(f"{n} {_fmt_money(a)}" for n, a in agg.items()) or "—"

    def _deferred_status(self, d: dict, d_index: int) -> str:
        """应收账款(sheet3)行的状态（阶段 4-2 四态）。

        - 已确认过 → `高置信`；
        - 需补录原票、且本次**还没填过**补录 → **`待补录`**（要去点「补录原票」）；
        - 其余（已在库 / 已填过补录）→ **`待确认`**（去确认收款信息）。

        判据取**工作副本**（`_rebuild_work` 已用最新库票号集合重判 `need_backfill`），
        故显示的「待补录 / 待确认」始终对应当前库状态与本次已填的补录。
        """
        if d_index in self._deferred_confirmed:
            return "高置信"
        no = (d.get("invoice_no") or "").strip()
        if d.get("need_backfill") and no not in self._backfills:
            return "待补录"
        return "待确认"

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
        # A1：应收账款(sheet3)行进同一张表（data["deferred"] 每一行）。
        # 判据取**工作副本**（_rebuild_work 已用最新库票号集合重判 need_backfill），
        # 故显示的「需补录 / 已入库」始终对应当前库状态。
        for i, d in enumerate(self._work.get("deferred") or []):
            rows.append({
                "kind": "deferred",
                "status": self._deferred_status(d, i),
                "p_index": None, "inv_idx": None, "work_idx": None,
                "ev": None, "problem": None, "deferred": d, "d_index": i,
            })
        # 标记「已确认」类别：已修正 / 点「确认」/ 编辑过的发票（均属高置信）
        for r in rows:
            confirmed = False
            if r["kind"] == "problem" and r["p_index"] in self._fix:
                confirmed = True
            elif r["kind"] == "invoice" and r["inv_idx"] is not None and (
                    r["inv_idx"] in self._confirmed or r["inv_idx"] in self._inv_edits):
                confirmed = True
            elif r["kind"] == "deferred" and r["d_index"] in self._deferred_confirmed:
                confirmed = True
            r["is_confirmed"] = confirmed
        # 排序：先按状态优先级（阶段 4-2 起「待补录」= 0 排最前，见 `_PRIO`），
        # 同状态内再按 问题行 → 发票行 → 应收账款行（各自保持原顺序）。
        # 故「待补录」的应收账款行会排到待确认的问题行之前 —— 这是 C 甲要的效果，
        # 不再是「问题行恒排最前」。
        _kind_order = {"problem": 0, "invoice": 1, "deferred": 2}
        rows.sort(key=lambda r: (
            _PRIO.get(r["status"], 9),
            _kind_order.get(r["kind"], 9),
            r["d_index"] if r["kind"] == "deferred"
            else (r["p_index"] if r["p_index"] is not None else 1 << 30),
        ))
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
        if r["kind"] == "deferred":
            d = r["deferred"] or {}
            if key == "src":
                return f"{SHEET_LABEL.get(d.get('sheet'), d.get('sheet') or '—')} · 第{d.get('row_no', '')}行"
            if key == "no":
                return d.get("invoice_no") or "—"
            if key == "buyer":
                return d.get("buyer") or "—"
            if key == "amt":
                return _fmt_money(d.get("total_amount") or 0.0)
            if key == "receipt":
                return receipt_summary(d)
            if key == "remark":
                return (d.get("remark_raw") or "").replace("\n", " ").strip() or "—"
            if key == "handler":
                parsed = "、".join(f"{n} {_fmt_money(b)}" for n, b in (d.get("handlers") or [])) or "—"
                src = (d.get("handler_text") or "").replace("\n", " ").strip()
                return f"{parsed}　〔源填写〕{src}" if src else parsed
            if key == "reason":
                # A2 + 阶段 4-2：应收账款行三态 —— 需补录原票 / 已补录请确认收款 /
                # 已入库请确认收款（普通发票行的「✓ 系统判定无疑问」不在此分支）
                if (d.get("invoice_no") or "").strip() in self._backfills:
                    return REASON_BACKFILLED
                return REASON_BACKFILL if d.get("need_backfill") else REASON_IN_LIBRARY
            if key == "kind":
                return "应收账款"
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
                    # 阶段 4-2：待补录（要去补录）用红，待确认用琥珀，高置信用绿
                    fg = {"待补录": RED, "待确认": AMBER,
                          "高置信": GREEN}.get(r["status"], GRAY)
                    item.setForeground(fg)
                if c == COL_REASON and r["kind"] == "invoice":
                    item.setForeground(GREEN if not r["ev"]["reasons"] else AMBER)
                if c == COL_REASON and r["kind"] == "deferred":
                    # 需补录原票（要去补录）用琥珀；其余两类「请确认收款」都用蓝
                    _dno = ((r["deferred"] or {}).get("invoice_no") or "").strip()
                    if _dno in self._backfills:
                        item.setForeground(BLUE)
                    else:
                        item.setForeground(
                            AMBER if (r["deferred"] or {}).get("need_backfill") else BLUE)
                if r["status"] in ("待补录", "待确认"):
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

        n_backfill = sum(1 for r in self._rows if r["status"] == "待补录")
        n_pending = sum(1 for r in self._rows if r["status"] == "待确认")
        n_high = sum(1 for r in self._rows if r["status"] == "高置信")
        n_skip = sum(1 for r in self._rows if r["status"] == "已跳过")
        n_confirmed = sum(1 for r in self._rows if r.get("is_confirmed"))
        self.lbl_stat.setText(
            f"待补录 {n_backfill}　待确认 {n_pending}　高置信 {n_high}"
            + (f"　已确认 {n_confirmed}" if n_confirmed else "")
            + (f"　已跳过 {n_skip}" if n_skip else "")
        )

        inv_total = sum(r["ev"]["total_amount"] for r in self._rows if r["kind"] == "invoice")
        n_inv = sum(1 for r in self._rows if r["kind"] == "invoice")
        pp = self._work.get("prepayments", [])
        pp_total = sum(x.get("amount", 0.0) for x in pp)
        # 应收账款(sheet3)行：本次不落 invoice/分摊；「已入库」行确认后只追加收款（A10），
        # 「需补录」行由「补录原票」逐张确认（方案 A）。阶段 4-2 起拆三类计数摊到汇总可见：
        # 还需补录 / 已补录待确认收款 / 已入库待确认收款。
        n_need = n_backfill
        n_bf_rows = sum(1 for r in self._rows if r["kind"] == "deferred"
                        and r["status"] == "待确认"
                        and ((r["deferred"] or {}).get("invoice_no") or "").strip()
                        in self._backfills)
        n_inlib = sum(1 for r in self._rows if r["kind"] == "deferred"
                      and not (r["deferred"] or {}).get("need_backfill"))
        n_bf = len(self._backfills)   # B2h：底部计数随行内补录同步
        self.lbl_summary.setText(
            f"发票 {n_inv} 张，合计 ¥{inv_total:,.2f}　"
            f"预收款 {len(pp)} 条，合计 ¥{pp_total:,.2f}"
            + (f"　·　应收账款需补录原票 {n_need} 张" if n_need else "")
            + (f"　·　已补录待确认收款 {n_bf_rows} 张" if n_bf_rows else "")
            + (f"　·　应收账款已入库待确认收款 {n_inlib} 张" if n_inlib else "")
            + (f"　·　本次已填补录 {n_bf} 张（随本次台账入库）" if n_bf else "")
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
                if r["kind"] == "deferred" and anchor[0] == "def" and r["d_index"] == anchor[1]:
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
        if filt == "待补录":
            return r["status"] == "待补录"
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
        if r["kind"] == "deferred":
            return ("def", r["d_index"])
        return ("prob", r["p_index"])

    def _recv_text(self, r: Dict) -> str:
        if r["kind"] == "deferred":
            return self._deferred_recv_text(r["deferred"] or {})
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
            self._set_backfill_button(None)
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
        elif r["kind"] == "deferred":
            # A3：应收账款(sheet3)行复用同一就地编辑面板。预填由
            # raw_ledger.expand_receipts 展开（同名多行 = 多期收款，与补录页同源）。
            d = r["deferred"] or {}
            rows, _split = self._deferred_payload(d)
            self.fix_panel.setVisible(True)
            self.fix_panel.set_invoice(d, prefill_rows=rows)
            self.fix_panel.set_readonly(False)
            # 两种状态都可「保存修改」；「确认」= 采纳收款口径。
            # 阶段 4-2：**「待补录」行不给「确认」** —— 原票不在库，确认也写不出任何东西，
            # 必须先点「补录原票」；补录后该行落到「待确认」，那时才可确认收款信息。
            self._set_actions(save=True, confirm=(r["status"] != "待补录"))
        else:
            # 待修正 / 已跳过 问题行仍走 set_problem 修正路径（始终可编辑）
            self.fix_panel.setVisible(True)
            self.fix_panel.set_problem(r["problem"])
            self._set_actions(save=True, skip=(r["status"] == "待确认"))
        # 阶段 3（B2g/B2h）：行内补录按钮的可见性与文案（随行类型/票号是否在库切换）
        self._set_backfill_button(r)

    def _set_actions(self, *, save: bool = False, skip: bool = False,
                     refix: bool = False, confirm: bool = False,
                     edit: bool = False) -> None:
        self.btn_save.setVisible(save)
        self.btn_skiprow.setVisible(skip)
        self.btn_refix.setVisible(refix)
        self.btn_confirm_row.setVisible(confirm)
        self.btn_edit.setVisible(edit)

    # ------------------------------------------------------------------ #
    # 行内「补录原票」（阶段 3 B2g/B2h）
    # ------------------------------------------------------------------ #
    def _red_orig_no(self, red_invoice_no: str) -> str:
        """红字发票号 → 其引用的原票号（查 `invoice` 表，销项导入时已写入该列）。

        `raw_invoice` 镜像表**没有** `orig_invoice_no` 列（它是从 remark 现算的），
        故必须查 `invoice`。查不到（该红字票不在销项）返回空串。
        """
        no = (red_invoice_no or "").strip()
        if not no:
            return ""
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT orig_invoice_no FROM invoice WHERE invoice_no=?", (no,)).fetchone()
        except Exception:  # noqa: BLE001 反查失败只影响按钮是否出现，不影响导入
            return ""
        finally:
            conn.close()
        return ((row["orig_invoice_no"] if row else "") or "").strip()

    def _invoice_in_library(self, invoice_no: str) -> bool:
        no = (invoice_no or "").strip()
        if not no:
            return False
        conn = get_conn()
        try:
            return conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone() is not None
        except Exception:  # noqa: BLE001
            return False
        finally:
            conn.close()

    def _row_backfill_target(self, r: Dict) -> tuple | None:
        """当前行是否有补录入口 → (要补/要看的票号, 该票是否已在库)；不适用返回 None。

        语义差异（§8 第 3 点）：
        - 应收账款(deferred)行：补**它自己**（行上票号即待补原票；
          已在库 = `need_backfill` 为假）；
        - 红字发票(invoice)行：补**它引用的原票**（票号经 `_red_orig_no` 反查），
          取不到原票号 → 无入口（可接受）。
        """
        if r["kind"] == "deferred":
            d = r["deferred"] or {}
            no = (d.get("invoice_no") or "").strip()
            if not no:
                return None
            return (no, not d.get("need_backfill"))
        if r["kind"] == "invoice":
            ev = r.get("ev") or {}
            if not ev.get("is_red"):
                return None
            orig = self._red_orig_no(ev.get("invoice_no") or "")
            if not orig:
                return None
            return (orig, self._invoice_in_library(orig))
        return None

    def _set_backfill_button(self, r: Dict | None) -> None:
        """按当前行切换「补录原票 / 查看原票」（B2g 文案 + B2h 刷新）。"""
        tgt = self._row_backfill_target(r) if r is not None else None
        if tgt is None:
            self.btn_backfill.setVisible(False)
            return
        no, in_lib = tgt
        # 阶段 4-2（D 甲）：已填过补录 → 「修改补录」（点它确实重新打开**可编辑**）；
        # 票已在库且未填过 → 「查看原票」（只读）；否则 → 「补录原票」。
        if no in self._backfills:
            self.btn_backfill.setText("修改补录")
        elif in_lib:
            self.btn_backfill.setText("查看原票")
        else:
            self.btn_backfill.setText("补录原票")
        self.btn_backfill.setVisible(True)

    @staticmethod
    def _bf_handlers_from_row(d: dict, split: list) -> list:
        """应收账款行 → 补录弹窗口径的 handlers（`name/billing/received/date`）。

        与 `problem_fix_panel.rows_from_handlers` / `raw_ledger.expand_receipts` **同口径**：
        每人**首行**带开票分摊额、其余行 0 —— `build_backfill` 按姓名聚合开票额，
        故合计仍等于开票总额（多期收款因此可无损表达），而收款逐笔保留。

        用途有二（阶段 4-2）：① 预填补录弹窗；② 在「待确认」里改过收款后，
        `_merged_data` 以**行的最终值**回写补录条目（否则用户改的收款会被静默丢弃）。
        """
        agg: Dict[str, float] = {}
        order: List[str] = []
        for n, b in (d.get("handlers") or []):
            nme = (n or "").strip()
            if not nme:
                continue
            if nme not in agg:
                agg[nme] = 0.0
                order.append(nme)
            agg[nme] += float(b or 0.0)
        recs: Dict[str, list] = defaultdict(list)
        for n, amt, ym in (split or []):
            nme = (n or "").strip()
            if nme:
                recs[nme].append((float(amt or 0.0), (ym or "")[:10]))
        out: list = []
        for nme in order:
            rs = recs.pop(nme, [])
            if not rs:
                out.append({"name": nme, "billing": agg[nme], "received": 0.0, "date": ""})
                continue
            for k, (amt, ym) in enumerate(rs):
                out.append({"name": nme, "billing": (agg[nme] if k == 0 else 0.0),
                            "received": amt, "date": ym})
        # 只在收款里出现、不在分摊里的人（数据异常）也带上，绝不静默丢行
        for nme, rs in recs.items():
            for amt, ym in rs:
                out.append({"name": nme, "billing": 0.0, "received": amt, "date": ym})
        return out

    def _backfill_prefill(self, r: Dict, no: str) -> dict:
        """按行来源预填补录弹窗（与「发票补录」页 `open_backfill_pending` 同口径）。

        应收账款行（阶段 4-2）：预填**连同台账里已有的收款**（`_deferred_payload` 同源）——
        否则用户先在右侧改过收款、再打开补录弹窗时，弹窗里的空白收款会在确定后被写回该行，
        把刚改的收款抹掉。
        """
        if r["kind"] == "deferred":
            d = r["deferred"] or {}
            _rows, split = self._deferred_payload(d)
            return {
                "invoice_no": no,
                "invoice_date": d.get("invoice_date") or "",
                "buyer": d.get("buyer") or "",
                "total_amount": d.get("total_amount") or 0.0,
                "handlers": self._bf_handlers_from_row(d, split),
            }
        # 红字行：补的是它引用的原票 —— 原票开票日期不可知（留空手填），
        # 购方/金额/经办人取红字发票做参考（与补录页 prefill_red_original 同一函数）
        conn = get_conn()
        try:
            return prefill_red_original(conn, no)
        except Exception:  # noqa: BLE001
            return {"invoice_no": no, "invoice_date": "", "buyer": "",
                    "total_amount": None, "handlers": []}
        finally:
            conn.close()

    def _validate_backfill(self, data: dict, editing_no: str = "") -> str | None:
        """弹窗内的写前校验（B2d 第一次）：与写库同源 + 先拦「本次已填过同票号」。"""
        err = backfill_validator(data)
        if err:
            return err
        no = (data.get("invoice_no") or "").strip()
        if no and no != editing_no and no in self._backfills:
            return (f"票号 {no} 本次已填过补录，请只保留一份"
                    "（如需修改，请点该行的「修改补录」）。")
        return None

    def _open_backfill(self) -> None:
        """行内按钮：补录 / 查看原票。

        **不立即写库**（B2c）：确定后只把结果收集进 `self._backfills`；
        点「确认入库」时随台账同一事务入库，点「取消」一行都不写。
        """
        r = self._current_row()
        if r is None:
            return
        tgt = self._row_backfill_target(r)
        if tgt is None:
            return
        no, in_lib = tgt
        existing = self._backfills.get(no)
        # ① 票已在库且本次没填过 → 只读查看（原票无需补录）
        if in_lib and existing is None:
            dlg = BackfillDialog(load_invoice_detail(no), title="查看原票",
                                 readonly=True, parent=self)
            dlg.exec()
            return
        # ② 已填过 → 预填已填内容可继续改；③ 未填过 → 按行信息预填
        if existing is not None:
            prefill = existing
        else:
            prefill = self._backfill_prefill(r, no)
        dlg = BackfillDialog(
            prefill, title=("修改补录" if existing is not None else "补录原票"),
            validator=lambda d, _k=no: self._validate_backfill(d, _k),
            parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        item = dict(dlg.data() or {})
        if not item:
            return
        # 复核页当前只产出 create=True（已在库票走 ① 只读查看）。
        # create=False（票已在库、只追加收款）是引擎侧与 A10 共用的统一通道。
        item["create"] = True
        self._backfills[(item.get("invoice_no") or "").strip()] = item
        if r["kind"] == "deferred" and r.get("d_index") is not None:
            # 阶段 4-2（A 甲）：**不再**把该行直接标成已确认 —— 那正是「补录完直接变高置信」
            # 的根因。改为把补录里填的经办人/收款**写回该行**：该行随 `_deferred_status`
            # 从「待补录」落到**「待确认」**，右栏预填与「各经办人已收」列因此**回显**
            # 刚填的内容；之后在待确认里点「确认」才离开。
            self._deferred_edits[r["d_index"]] = self._row_edit_from_backfill(
                self._deferred_edits.get(r["d_index"]), item)
        # 刷新当前行（按钮切「修改补录」、状态/原因/底部计数同步）
        self._rebuild()

    @staticmethod
    def _row_edit_from_backfill(ed: dict | None, item: dict) -> dict:
        """补录弹窗结果 → 应收账款行的就地编辑结果（`_deferred_edits` 口径）。

        与 `ProblemFixPanel.read_fix()` 同结构（`_apply_invoice_edit` 消费）：
        `handlers` = [(姓名, 开票额)]（同名多行 = 多期）、
        `split_receipts` = [(姓名, 金额, 日期)]（只含已收>0 且有日期的行）。
        """
        handlers_in = list(item.get("handlers") or [])
        out = dict(ed or {})
        handlers = [
            ((h.get("name") or "").strip(), float(h.get("billing") or 0.0))
            for h in handlers_in if (h.get("name") or "").strip()
        ]
        out["invoice_date"] = item.get("invoice_date") or ""
        out["total_amount"] = float(item.get("total_amount") or 0.0)
        out["buyer"] = item.get("buyer")
        out["handlers"] = handlers
        out["handler_text"] = "、".join(f"{n} {_fmt_money(b)}" for n, b in handlers)
        out["split_receipts"] = [
            ((h.get("name") or "").strip(), float(h.get("received") or 0.0),
             (h.get("date") or "")[:10])
            for h in handlers_in
            if (h.get("name") or "").strip()
            and float(h.get("received") or 0.0) > 0.001
            and (h.get("date") or "").strip()
        ]
        return out

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
        elif r["kind"] == "deferred":
            d = r["deferred"] or {}
            header = d.get("header") or []
            raw_row = d.get("raw_row") or []
            sheet_name = SHEET_LABEL.get(d.get("sheet"), d.get("sheet") or "—")
            row_no = d.get("row_no") or 0
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
        elif r["kind"] == "deferred" and r["d_index"] is not None:
            # A3：应收账款行 → 回写 data["deferred"] 的对应条目。
            # 「已入库」行还表示用户已对这些收款做出判断 → 置 receipt_confirmed，
            # 由 commit_ledger_import（A10）在入库时**追加**该票收款（需补录行不出收款）。
            self._deferred_edits[r["d_index"]] = data
            if not (r["deferred"] or {}).get("need_backfill"):
                self._deferred_confirmed.add(r["d_index"])
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

        应收账款(sheet3)行（阶段 4-2 起三态各有所指）：
        - **「待补录」**（原因 `需补录原票`）→ 原票不在库，**确认不生效**：只提示先去补录原票，
          该行**留在「待补录」**（不记已确认，否则用户会以为处理完了）；
        - **「待确认」+ 已补录**（原因 `已补录，请确认收款`）→ 确认收款信息 → 归入高置信；
          收款由补录条目（`apply_backfill`）在入库时写入，与 A10 不重复计（阶段 4-1 去重）；
        - **「待确认」+ 已入库**（原因 `已入库，请确认收款`）→ 采纳台账收款口径，
          入库时由 A10 **追加**该票收款。
        """
        r = self._current_row()
        if r is None:
            return
        if r["kind"] == "deferred":
            d = r["deferred"] or {}
            no = (d.get("invoice_no") or "").strip()
            if r["status"] == "待补录":
                QMessageBox.information(
                    self, "请先补录原票",
                    f"发票 {no or '—'} 的原票不在库中，需先补录原票才能确认收款信息。\n\n"
                    "请点右侧「补录原票」就地填写（随本次台账一起入库），"
                    "或到「发票补录」页补录；补录后本行会转为「待确认」。\n\n"
                    "本次点「确认」不会写入任何数据。")
                return
            self._deferred_confirmed.add(r["d_index"])
            self._rebuild()
            return
        if r["kind"] != "invoice" or r["work_idx"] is None:
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
        else:
            # 与 _rebuild_work 同一步骤：保证 deferred 桶结构与工作副本一致，
            # A3 的 deferred 下标才不会错位（直连构造时数据尚未切分）。
            self._split_work(merged)
        merged["problems"] = []

        # 已存在发票的右侧就地编辑：原地覆盖
        for inv_idx, ed in self._inv_edits.items():
            invs = merged.get("invoices", [])
            if 0 <= inv_idx < len(invs):
                self._apply_invoice_edit(invs[inv_idx], ed)

        # A3 + A10：应收账款(sheet3)行的就地编辑与「确认收款」结果回写 data["deferred"]。
        # 下标对应 data["deferred"] 顺序（split_deferred 只追加、不重排）。
        deferred = merged.get("deferred") or []
        for d_idx, ed in sorted(self._deferred_edits.items()):
            if 0 <= d_idx < len(deferred):
                self._apply_invoice_edit(deferred[d_idx], ed)
        for d_idx in sorted(self._deferred_confirmed):
            if not (0 <= d_idx < len(deferred)):
                continue
            d = deferred[d_idx]
            if d.get("need_backfill"):
                continue  # 需补录原票 → 由补录流程写入，此处绝不出收款
            if not d.get("split_receipts"):
                # 只点了「确认」未编辑 → 采纳台账预填口径（无收款信息时为空，即不写）
                _rows, split = self._deferred_expand(d)
                d["split_receipts"] = split
            d["split_receipts"] = [
                ((n or "").strip(), float(a or 0.0), (ym or "")[:10])
                for n, a, ym in (d.get("split_receipts") or [])
                if (n or "").strip() and float(a or 0.0) > 0.001
            ]
            # 用户在复核页确认过收款 → commit_ledger_import（A10）据此**追加** collection
            d["receipt_confirmed"] = True

        # 阶段 3（B2c）：行内「补录原票」收集到的条目随 data 一起流转
        # （天然按账期隔离）→ 由 commit_ledger_import 在同一事务写库。
        # data 里已有的（如「入库不过 → 返回修改」重载同一份 data）先保留，
        # 面板内的（可能改过）覆盖之。
        bf_map = {
            (b.get("invoice_no") or "").strip(): b
            for b in (merged.get("backfills") or [])
            if (b.get("invoice_no") or "").strip()
        }
        bf_map.update(self._backfills)
        # 阶段 4-2：已补录的行若在「待确认」里又被改过（右栏保存 → `_deferred_edits`），
        # 以**行的最终值**回写补录条目 —— 补录条目才是写库依据（`apply_backfill`），
        # 不写回就会把用户在待确认里改的收款静默丢掉。
        for d in deferred:
            _no = (d.get("invoice_no") or "").strip()
            bf = bf_map.get(_no)
            if not bf or not bf.get("create", True):
                continue
            _rows, _split = self._deferred_payload(d)
            bf["handlers"] = self._bf_handlers_from_row(d, _split)
            if d.get("invoice_date"):
                bf["invoice_date"] = d["invoice_date"]
            if d.get("buyer") is not None:
                bf["buyer"] = d["buyer"]
            if d.get("total_amount"):
                bf["total_amount"] = float(d["total_amount"])
        merged["backfills"] = list(bf_map.values())

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
        # 阶段 4-2：未处理 = 「待补录」+「待确认」（待补录行本次不会建票，同样要提醒）
        todo = [r for r in self._rows if r["status"] in ("待补录", "待确认")]
        if todo:
            n_bf = sum(1 for r in todo if r["status"] == "待补录")
            n_inlib = sum(1 for r in todo if r["kind"] == "deferred"
                          and r["status"] == "待确认"
                          and not (r["deferred"] or {}).get("need_backfill"))
            msg = (f"还有 {len(todo)} 行未处理，确认入库时这些行将被跳过（不入库）。\n"
                   + (f"\n其中「待补录」{n_bf} 行：原票不在库，本次不会为它们建票，"
                      "请先用右侧「补录原票」补录。\n" if n_bf else "")
                   + (f"\n其中应收账款「已入库，请确认收款」{n_inlib} 行：本次不会写入这些收款"
                      "（该票已有收款不受影响；重新导入同一账期可再确认）。\n"
                      if n_inlib else "")
                   + "\n是否继续？")
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
        # 应收账款(sheet3)行的就地编辑（A3）：旧值取原始 deferred 行；
        # 若载入时 data 尚未切分（无 deferred 桶），按票号回退到 invoices 里找 sheet3 行。
        odef = (self._orig_data or {}).get("deferred") or []
        for d_idx, data in sorted(self._deferred_edits.items()):
            d = odef[d_idx] if 0 <= d_idx < len(odef) else {}
            no = str(data.get("invoice_no") or d.get("invoice_no") or "").strip()
            if not d and no:
                d = next((x for x in list(odef) + list(oinv)
                          if str(x.get("invoice_no") or "").strip() == no), {})
            items.append({"kind": "edit", "invoice_no": no, "old": dict(d), "new": data})
        return items
