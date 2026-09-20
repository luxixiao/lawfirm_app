"""发票台账导入 — 统一确认对话框（问题修正 + 预览确认 合二为一）

背景：原先是两个独立弹窗
    1) 问题行修正 ProblemDialog  → 只能修，看不到源文件备注/经办人原文/收款认定
    2) 导入预览确认 PreviewDialog → 只能看与改已收，解析失败的行已被处理掉、无法回头改
本对话框把两者合并为「一张表 + 右侧就地修正」：

- 一张表承载全部行，状态分三类：**待补录**（要补的原票不在库且本次未补录 —— 来源有二：
  应收账款 sheet3 行自身缺号、红字行引用的蓝字原票缺号）/ 待确认
  （解析失败 + 低置信 + 应收账款待确认收款）/ 高置信
- 顶部筛选胶囊（**默认「待补录」**，阶段 4-2 B 甲；阶段 5 加入「已补录」）：待补录 /
  待确认 / 已补录 / 已确认 / 高置信 / 全部。排序按「待补录 → 待确认 → 高置信」，
  故待补录排最前（C 甲）
- **不允许跳过**（2026-09-18 用户拍板）：删除「跳过此行」按钮；`accept()` 只要还存在任一
  「待补录 / 待确认」行即**硬拦**（不再问「是否继续」）。未修正的问题行同为「待确认」，
  故一并被拦 —— **入库 ≡ 全部处理完毕**。放弃本次导入请点「取消」。
  - 「已确认」与「高置信」重叠：已修正、点「确认」、或编辑过的发票均属高置信且归入已确认
  - **「已补录」是叠加视图**（阶段 5 A 甲，用户 2026-09-18）：判据 `is_backfilled`，
    与行自己的状态**互不影响** —— 本次填过补录的行既出现在「已补录」，也照旧留在
    「高置信 / 待确认」里（与「已确认」同一模式）。
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
- **红字行（阶段 5，B 乙，用户 2026-09-18 拍板）**：红字发票**本身没问题**（照常高置信），
  缺的是它引用的**蓝字原票** —— 故让**红字行自己**落「待补录」，原因列写明
  `需补录原票（红字引用 <原票号>）`，本行自身的疑问照旧拼在后面（绝不隐藏）：
  · 待补录时**不给「确认」**（确认写不出任何东西，点了状态也不变 → 只提示先补录）；
  · 填完补录 → `orig in self._backfills` 成立 → 判定自动失效 → 状态**回落到它本来的**
    「高置信 / 待确认」，无需另写回落逻辑；同时挂上「已补录」标记（叠加视图可见）；
  · 原票**在本批**（sheet1/2 会随台账入库，或 sheet3 行自己就是待补录行）→
    **不给入口**（`_row_backfill_target` 返回 None）：前者强行补录会撞
    `_validate_backfills` 校验③ 而卡住整批，后者会造成同一票号两行待办。

数据流：真实 data 全程不被修改，修正只作用于工作副本；点「确认入库」时才一次性
把 resolved 合并进真实 data。点「取消」真实 data 保持原样。

双模式（批 1b）
---------------
- `mode="pre"`（默认，**导入前**）：数据由调用方解析源文件后经 `load_data(...)` 传入；
  「确认入库」写库、「取消」零副作用（以上整段说明均指本模式）。
- `mode="post"`（**导入后**）：数据由 `load_period(period)` 从
  `app.engine.review_rebuild` **反向重建**（该账期 active 台账批次的 `raw_ledger` 镜像
  + 本批 `collection`），与解析结果同构，故四态/右栏/筛选全部复用。**只读查看**：
  · 底部只有「关闭」（无「确认入库」）；
  · 右栏表单恒只读；行内写操作按钮（保存修改 / 重新修正 / 确认 / 编辑 / 补录原票）全部隐藏；
  · 左侧表格照旧可用：筛选、单元格 tooltip、双击「查看原始台账行」
    （镜表不存原始行 → 由存档文件按行号读回，见 `review_rebuild.read_ledger_row`）；
  · 「待补录」**仍是有效待办**（去「发票补录」页处理），故保留该状态与原因文案；
    而 sheet3「已在库」行在导入后**没有待办**（收款已于入库时按 A10 处理完毕）→
    归入「高置信」而非「待确认」（见 `_deferred_status`）。
- **「导入时留痕」三类**（批 3-1 / 3-2b / 3-2c，落 `anomaly_note` 三个独立 dim）：
  post 的**状态**是「读库重建 + 重跑 `evaluate`」现算的，而「已确认 / 已补录」两个
  **叠加视图**原本依赖只在导入会话内存里的集合（`_confirmed` / `_deferred_confirmed` /
  `_inv_edits` / `_deferred_edits` / `_backfills`）—— `load_data` 每次把它们清空重建
  ⇒ 关掉导入页再从「导入复核」进来，这些集合全空，用户当时处理过的行**只剩「高置信」
  一档**。故三件事各落一条留痕，且**只在 post 读**（pre 的确认/修改/补录都是实时的，
  读留痕会让「覆盖式重导同账期」时上一批的旧痕迹冒充本批的）：
  1. **点了「确认」**（发票行 / sheet3 确认收款）→ `import_confirm` → post 里
     不再重报「待确认」并计入「已确认」（批 3-1 + 甲）；
  2. **就地改过字段** → `import_edit` → post 原因列标注「导入时已修改：字段…」，
     并计入「已确认」（批 3-2b + 乙 —— 与 pre 同口径：pre 的 `_inv_edits`/
     `_deferred_edits` 本就计进 `is_confirmed`）；
  3. **填过补录** → `import_backfill` → post 里「已补录」筛选不再是空表（批 3-2c）。
编辑回写属批 3（`review_writeback.apply_edit`），本批不实现。
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Dict, List

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import raw_ledger as rl
from app.engine.backfill_module import load_invoice_detail, prefill_red_original
from app.engine.import_confidence import (  # noqa: F401  （REASON_LIB_DIFF 供测试断言）
    REASON_LIB_DIFF, SHEET_LABEL, evaluate, receipt_summary, red_orig_diff,
)
from app.engine.raw_ledger import expand_receipts
from app.importer.excel_reader import ImportError_
from app.ui import scale, style
from app.ui.backfill_dialog import BackfillDialog, backfill_validator, red_mismatch_notice
from app.ui.ledger_source import show_ledger_source
from app.ui.preview_dialog import _fmt_money
from app.ui.problem_fix_panel import ProblemFixPanel
from app.engine.review_writeback import restore_from_archive
from app.ui.table_view import auto_fit_columns
from app.ui.widgets import CaptionLabel, PrimaryPushButton, PushButton, TableWidget
from app.ui.writeback_dialog import WritebackDialog

# 状态列的语义色一律「用时」取 style.qcolor(...)：neg_fg（待补录）/ amber_fg（待确认）
# / pos_fg（高置信）/ info_fg（信息性）/ text_mute（其它）/ accent_red_bg（待处理行底色）。

# A2：应收账款(sheet3)行的「原因 / 疑问」三态文案（阶段 4-2 起）
REASON_BACKFILL = "需补录原票"
REASON_BACKFILLED = "已补录，请确认收款"
REASON_IN_LIBRARY = "已入库，请确认收款"
REASON_NO_DOUBT = "✓ 系统判定无疑问"
# 阶段 5（源 A 红字引用）：红字行的待补录文案 —— 补的是**它引用的蓝字原票**，
# 故把原票号写在原因列，用户一眼能对上补的是哪张（B 乙：红字行自己落「待补录」）。
REASON_BACKFILL_RED = "需补录原票（红字引用 {}）"
# 阶段 6（红字 ⇄ 蓝字一致性）：蓝字原票金额/经办人与引用的红字发票不符 → 该红字行落
# 「待确认」，原因列写明**哪几项**不符（字段名来自 `red_orig_diff`），确认后才回高置信。
REASON_RED_MISMATCH = "红字与蓝字原票不一致（{}），请确认"
# 批 3-1：该行的疑问在**入库那一刻已由人点「确认」消掉**（留痕见 `engine.import_confirm`）。
# 「导入后」模式据此不再把它重报成「待确认」（post 隐藏了「确认」按钮，重报就是点不掉的
# 假待办）；疑问原文照旧展示，只在末尾加本标注 —— 绝不隐藏。
REASON_IMPORT_CONFIRMED = "导入时已确认"
# 批 3-2b：导入时就地修改过字段的提示（post 页原因列标注「导入时已修改：字段…」）。
# sheet3 在库行的文本字段库内零落点，post 从镜表（原文）重建会无声显示旧值 → 需提示。
REASON_IMPORT_EDIT = "导入时已修改"

HEADERS = [
    "状态", "类型", "来源", "发票号", "购方", "金额",
    "经办人分摊", "各经办人已收(双击编辑)", "收款认定", "源文件备注", "原因 / 疑问",
]
COL_STATUS, COL_KIND, COL_SRC, COL_NO, COL_BUYER, COL_AMT, COL_HANDLER, \
    COL_RECV, COL_RECEIPT, COL_REMARK, COL_REASON = range(11)

# 阶段 4-2：筛选栏胶囊 —— 「待补录」排在「待确认」之前（C 甲），默认落在「待补录」（B 甲）
# 阶段 5：新增「已补录」（放在待确认与已确认之间，A 甲）。它是**叠加视图**而非状态：
# 一张票填过补录后进入本筛，同时**不受影响地**留在自己的状态里（高置信 / 待确认…），
# 与既有「已确认」和「高置信」重叠同一模式（见 `_match` / `_row_backfilled`）。
FILTERS = ["待补录", "待确认", "已补录", "已确认", "高置信", "全部"]
_PRIO = {"待补录": 0, "待确认": 1, "高置信": 2}
# 「全部」在筛选栏中的下标 —— 测试与其它模块按它取「全部」按钮（改 FILTERS 必须同步）
FILTER_ALL = FILTERS.index("全部")


class UnifiedImportDialog(QWidget):
    """发票台账导入统一确认面板（可作为独立对话框，也可嵌入导入页的 tab）。

    confirmed 信号：用户点「确认入库」且校验通过后发出（self._data 已被合并为最终结果）。
    cancelled 信号：用户点「取消」发出。
    也可直接调用 accept() 同步拿到合并结果（保留旧调用方式，便于测试）。
    """

    confirmed = Signal()
    cancelled = Signal()

    def __init__(self, data=None, period="", staff_names=None,
                 parent=None, path: str = "", validator=None,
                 mode: str = "pre") -> None:
        """validator: 可选 callable(data) -> str | None（确认入库时先做写前校验）。

        作为对话框：直接传 data/staff_names 即可使用。
        作为嵌入面板：可只传 parent，随后调用 load_data(...) 载入待确认数据。
        mode: `"pre"`（导入前，默认，可改可确认入库）/ `"post"`（导入后，只读）；
              post 用 `load_period(period)` 从库反向重建，见模块 docstring「双模式」。
        """
        super().__init__(parent)
        # 数据相关的状态在 load_data 中初始化；这里给占位默认值以便无数据时也能构造
        self._data: Dict = {}
        # 批 1b：pre=导入前（可改可入库）/ post=导入后（只读查看，数据由 load_period 重建）
        self._mode = mode if mode in ("pre", "post") else "pre"
        # post 模式的溯源三要素（批次存档路径 / 文件名 / 批次号），load_period 时填
        self._post_meta: Dict = {"batch_id": None, "path": "", "file_name": ""}
        # 批 2：「台账 ⇄ 库」比对的库侧上下文（仅导入前模式构建，见 load_data）。
        # None = 不比对（post 模式 / 无账期 / 读库失败 —— 比对是增强项，绝不卡导入）。
        self._lib_ctx: Dict | None = None
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
        # 阶段 5：本批工作副本里出现过的票号缓存（`_rebuild` 每轮重置；纯内存，不查库）
        self._batch_nos_cache: set | None = None
        # 阶段 6：库中蓝字原票的取数缓存（`_rebuild` 每轮重置；键 = 原票号）
        self._blue_cache: dict | None = None
        # 批 3-2c：本账期「导入时填过补录」的票号集合缓存（`_rebuild` 每轮重置；
        # 只在 post 模式有值 —— pre 用实时集合 `_backfills`，不读历史留痕）
        self._imp_bf_nos_cache: set | None = None
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
        self.btn_cancel = PushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_confirm)
        root.addLayout(btns)
        self._apply_mode_chrome()

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
        # 批 2：导入前模式才建「台账 ⇄ 库」比对上下文（post 刻意不建 —— 导入后镜表
        # 原文与库的差异正是导入时人工修正的痕迹，重报 = 毛病 1 复活成假待办）。
        # 一次读库、缓存到本次载入；失败 → None（比对整体跳过，绝不卡导入）。
        self._lib_ctx = None
        if self._mode == "pre" and period:
            try:
                from app.engine.review_compare import build_lib_context
                self._lib_ctx = build_lib_context(period)
            except Exception:  # noqa: BLE001
                self._lib_ctx = None
        self.setWindowTitle(f"发票台账导入确认 — {period}")
        self._rebuild_fix_panel()
        self._rebuild()

    # ------------------------------------------------------------------ #
    # 双模式（批 1b）：导入前可改可入库 / 导入后只读查看
    # ------------------------------------------------------------------ #
    @property
    def mode(self) -> str:
        """`"pre"`（导入前）/ `"post"`（导入后，只读）。"""
        return self._mode

    def _apply_mode_chrome(self) -> None:
        """按模式调整**底部按钮**（右侧行内按钮由 `_set_actions` 负责）。

        post（导入后）**不写库**：隐藏「确认入库」，把「取消」改成「关闭」。
        关闭仍走 `reject()` → `cancelled` 信号，由宿主决定回哪个页面。
        """
        post = self._mode == "post"
        self.btn_confirm.setVisible(not post)
        self.btn_cancel.setText("关闭" if post else "取消")
        # 批 3-3：post 的行级修改走「编辑回写 / 还原为原件」（单事务写库+留痕）；
        # pre 隐藏 —— 导入前数据还没落库，没有镜表锚点可改。
        # 初始禁用：选中行后由 `_set_writeback_buttons` 按行解锁（空表/无选中
        # 时 itemSelectionChanged 不触发，按钮必须停在禁用态防误点）。
        self.btn_writeback.setVisible(post)
        self.btn_restore.setVisible(post)
        self.btn_writeback.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self._lock_panel_for_mode()

    def _lock_panel_for_mode(self) -> None:
        """post 模式下**建完面板即锁死**，不依赖「有没有行被选中」。

        `_load_right` 里也有一次 `set_readonly(True)`，但那要求有当前行；
        默认筛选为「待补录」而本期没有待补录行时（空表）没有任何行被选中，
        右栏不会被走到 → 面板会停在可编辑态（虽然此时它是隐藏的）。
        这里补一道与选中无关的保证，避免以后有人让面板常显而漏锁。
        """
        if self._mode == "post" and self.fix_panel is not None:
            self.fix_panel.set_readonly(True)

    def load_period(self, period: str) -> None:
        """（导入后模式）按账期从库中**反向重建**并载入，只读展示。

        数据源：`app.engine.review_rebuild.rebuild_period_data` —— 该账期 active 台账
        批次的 `raw_ledger` 镜像 + 本批 `collection`，产出与源文件解析**同构**的 data，
        故四态/筛选/右栏全部复用导入前的那一套。
        库无该期 active 批次（被清空 / 没导过）→ 载入空骨架，界面显示空表。

        只读约束（见模块 docstring「双模式」）：不校验、不写库；`validator` 传 None。
        """
        from app.engine.review_rebuild import rebuild_period_data
        data = rebuild_period_data(period)
        self._post_meta = {
            "batch_id": data.get("batch_id"),
            "path": data.get("path") or "",
            "file_name": data.get("file_name") or "",
        }
        staff = self._staff_names_from_db()
        # path 传存档路径 → 「查看原始台账行」在 post 模式直接拿它读存档文件
        self.load_data(data, period, staff, self._post_meta["path"], None)

    def _staff_names_from_db(self) -> list:
        """花名册（与写库侧同一口径 `importer._staff_names`），供右侧下拉与置信度判定。

        查库失败不阻断显示（返回空集合 → 经办人一律报「不在花名册」，属保守提示）。
        """
        conn = get_conn()
        try:
            from app.engine.backfill import all_staff_names
            return sorted(all_staff_names(conn))
        except Exception:  # noqa: BLE001
            return []
        finally:
            conn.close()

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
        self._lock_panel_for_mode()

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
        # 批 3-3：导入后「编辑回写 / 还原为原件」—— 只在 post 模式可见（见
        # `_apply_mode_chrome`）；pre 的修改走右栏就地面板，不与这两条路径混用。
        self.btn_writeback = PushButton("编辑回写")
        self.btn_writeback.clicked.connect(self._writeback_row)
        self.btn_writeback.setVisible(False)
        row_btn.addWidget(self.btn_writeback)
        self.btn_restore = PushButton("还原为原件")
        self.btn_restore.clicked.connect(self._restore_row)
        self.btn_restore.setVisible(False)
        row_btn.addWidget(self.btn_restore)
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
        ab.addWidget(self.btn_save)
        ab.addWidget(self.btn_refix)
        ab.addWidget(self.btn_confirm_row)
        ab.addWidget(self.btn_edit)
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

    @staticmethod
    def _edit_changed_fields(orig: dict, ed: dict) -> List[str]:
        """右侧表单编辑结果 vs 行原值 → 被改动的字段中文名列表（批 3-2b）。

        只比 `_apply_invoice_edit` 实际覆盖的 7 个字段；比较前做同款归一化
        （金额取两位小数、handlers/splits 排序），避免顺序差异造成假报。
        """
        changed: List[str] = []
        if (ed.get("invoice_date") or "") != (orig.get("invoice_date") or ""):
            changed.append("开票日期")
        if abs(float(ed.get("total_amount") or 0.0)
               - float(orig.get("total_amount") or 0.0)) > 0.005:
            changed.append("总金额")
        if sorted((str(n or "").strip(), round(float(b or 0.0), 2))
                  for n, b in (ed.get("handlers") or [])) != sorted(
                (str(n or "").strip(), round(float(b or 0.0), 2))
                for n, b in (orig.get("handlers") or [])):
            changed.append("经办人分摊")
        if (ed.get("handler_text") or "") != (orig.get("handler_text") or ""):
            changed.append("经办人原文")
        if sorted((str(n or "").strip(), round(float(a or 0.0), 2), str(ym or "")[:10])
                  for n, a, ym in (ed.get("split_receipts") or [])) != sorted(
                (str(n or "").strip(), round(float(a or 0.0), 2), str(ym or "")[:10])
                for n, a, ym in (orig.get("split_receipts") or [])):
            changed.append("收款明细")
        if ed.get("buyer") is not None and (ed.get("buyer") or "") != (orig.get("buyer") or ""):
            changed.append("购方")
        if ed.get("case_no") is not None and (ed.get("case_no") or "") != (orig.get("case_no") or ""):
            changed.append("案号")
        return changed

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

    @staticmethod
    def _row_invoice_no(r: Dict) -> str:
        """行 → 发票号码（发票行取 `ev`、应收账款行取 `deferred`；问题行无号）。"""
        if r["kind"] == "invoice":
            return str(((r.get("ev") or {}).get("invoice_no")) or "").strip()
        if r["kind"] == "deferred":
            return str(((r.get("deferred") or {}).get("invoice_no")) or "").strip()
        return ""

    def _import_confirmation(self, r: Dict) -> str:
        """该行的「导入时确认」留痕备注（批 3-1）；无留痕返回空串。

        **只在 post 模式生效**：pre 模式的确认是**实时**的（`_confirmed` 下标集合），
        若也读留痕，「覆盖式重导同一账期」时上一批的旧确认会提前吞掉本批的疑问。
        """
        if self._mode != "post":
            return ""
        no = self._row_invoice_no(r)
        if not no:
            return ""
        return (self._data.get("confirmations") or {}).get(no, "")

    def _import_edit_hint(self, r: Dict) -> str:
        """该行的「导入时已修改」字段提示（批 3-2b）；无提示返回空串。

        **只在 post 模式生效**：pre 模式的修改实时显示在右栏表单里，无需提示；
        若 pre 也读留痕，「覆盖式重导同一账期」时上一批的旧提示会提前混进本批
        （与 `_import_confirmation` 同款约束）。
        """
        if self._mode != "post":
            return ""
        no = self._row_invoice_no(r)
        if not no:
            return ""
        return (self._data.get("edit_hints") or {}).get(no, "")

    def _deferred_status(self, d: dict, d_index: int) -> str:
        """应收账款(sheet3)行的状态（阶段 4-2 四态；批 1b 起随模式微调）。

        - 已确认过 → `高置信`；
        - 需补录原票、且本次**还没填过**补录 → **`待补录`**（要去点「补录原票」）；
        - 其余（已在库 / 已填过补录）→ **`待确认`**（去确认收款信息）。

        判据取**工作副本**（`_rebuild_work` 已用最新库票号集合重判 `need_backfill`），
        故显示的「待补录 / 待确认」始终对应当前库状态与本次已填的补录。

        批 1b（导入后模式）唯一差异：**「已在库」行不再算待确认** —— 收款已在入库那一刻
        按 A10 处理完毕（`receipt_confirmed` 由 `_merged_data` 写入），导入后没有待办；
        仍按「待确认」显示会让用户以为还有活要干。故名次落在「高置信」。
        注意「**待补录」在导入后照样是有效待办**（去「发票补录」页逐张补），故不降级。
        """
        if d_index in self._deferred_confirmed:
            return "高置信"
        no = (d.get("invoice_no") or "").strip()
        if d.get("need_backfill") and no not in self._backfills:
            return "待补录"
        if self._mode == "post":
            return "高置信"
        return "待确认"

    def _rebuild(self) -> None:
        anchor = self._current_anchor()
        self._rebuild_work()
        # 每轮重建都重算「本批票号」缓存（工作副本刚被重建，缓存必须失效）
        self._batch_nos_cache = None
        # 阶段 6：同轮内「库中蓝字原票」取数只查一次（`_lib_blue_amounts` 的缓存）
        self._blue_cache = None
        # 批 3-2c：同上 —— 「导入时填过补录」票号集合（`_import_backfilled_nos` 的缓存）
        self._imp_bf_nos_cache = None
        # 批 2：「台账 ⇄ 库」比对只在导入前模式启用（lib=None → evaluate 完全跳过）
        evs = evaluate(self._work, self._staff_set, self._confirmed, self._period,
                       lib=(self._lib_ctx if self._mode == "pre" else None))
        rows: List[Dict] = []
        for i, ev in enumerate(evs):
            inv_idx = i if i < self._orig_inv_len else None
            r = {
                "kind": "invoice",
                "status": "待确认" if ev["conf"] == "low" else "高置信",
                "p_index": self._idx_to_p.get(i),
                "inv_idx": inv_idx,
                "work_idx": i,
                "ev": ev,
                "problem": None,
                "bf_orig": "",
                "red_diff": None,
                "red_diff_src": "",
            }
            # 阶段 5（B 乙，用户 2026-09-18 拍板）：红字行引用的**蓝字原票**缺失
            # → 本红字行落「待补录」（补的是那张原票，票号写进「原因」列）。
            # 「补完自动回落」不需要额外代码：`orig in self._backfills` 一旦成立，
            # 本判定即不再成立 → 状态回到 `evaluate` 的自然判定（高置信 / 待确认）。
            orig = self._red_backfill_orig(r)
            if orig:
                r["bf_orig"] = orig
                r["status"] = "待补录"
            # 阶段 6：红字 ⇄ 蓝字一致性（用户 2026-09-18 第 1 点）。两种情形都收口在
            # `_red_consistency`：①库中已有蓝字原票 a；②本次补录填的蓝字原票 d。
            # 不一致 → 该行落「待确认」，**点「确认」后才回高置信**（`_confirmed` 一进
            # 就不再压制，与 `evaluate` 的兜底确认同一机制，无需另设状态）。
            diff, src = self._red_consistency(r)
            r["red_diff"], r["red_diff_src"] = diff, src
            if diff and r["status"] != "待补录" and r["inv_idx"] not in self._confirmed:
                r["status"] = "待确认"
            rows.append(r)
        for i, p in enumerate(self._data.get("problems", [])):
            if i in self._fix:
                # 已修正：归入高置信，并标记「已确认」
                rows.append({"kind": "problem", "status": "高置信",
                             "p_index": i, "inv_idx": None, "work_idx": None,
                             "ev": None, "problem": p})
                continue
            rows.append({
                "kind": "problem",
                "status": "待确认",
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
        # 阶段 5（A 甲）：同时标记「已补录」—— 它是**叠加视图**而非状态，
        # 故与 is_confirmed 并列成一个独立布尔，不参与 `_PRIO` 排序。
        for r in rows:
            confirmed = False
            if r["kind"] == "problem" and r["p_index"] in self._fix:
                confirmed = True
            elif r["kind"] == "invoice" and r["inv_idx"] is not None and (
                    r["inv_idx"] in self._confirmed or r["inv_idx"] in self._inv_edits):
                confirmed = True
            elif r["kind"] == "deferred" and r["d_index"] in self._deferred_confirmed:
                confirmed = True
            # 批 3-1：导入时点过「确认」的行（有留痕）→ 导入后不再重报。
            # **只降「待确认」**：「待补录」是真待办（票不在库，得去补录），留痕绝不覆盖它。
            note = self._import_confirmation(r)
            r["import_confirm_note"] = note
            r["is_import_confirmed"] = bool(note)
            # 批 3-2b：导入时就地修改过字段的提示（仅 post 生效；只标注原因列，不改状态）
            r["import_edit_hint"] = self._import_edit_hint(r)
            if note:
                if r["status"] == "待确认":
                    r["status"] = "高置信"
                confirmed = True
            # 批 3-2c（乙）：post 里「导入时已修改」也算「已确认」，与 pre 同口径。
            # pre 的修改实时记在 `_inv_edits` / `_deferred_edits` 两个内存集合里，而它们
            # **已计入 `is_confirmed`**（上面第二个 `elif`）⇒ 同一个用户动作（改完这行）
            # 在 pre 是「已确认」，到 post 却只剩「高置信」，筛选点开是空表。
            # 只补 `is_confirmed`，**不动状态**（与 3-2b「只标注不改状态」的定论一致）：
            # 已修改的行仍可能是「待确认」（还有改不掉的硬疑问），这与 pre 完全一样。
            if r.get("import_edit_hint"):
                confirmed = True
            r["is_confirmed"] = confirmed
            r["is_backfilled"] = self._row_backfilled(r)
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
                # 阶段 5（B 乙）待补录 + 阶段 6 红蓝不一致：两类疑点都写进原因列，
                # 本行自身的疑问（如经办人不全）仍照旧拼在后面 —— 绝不因此隐藏。
                parts: List[str] = []
                if r["status"] == "待补录" and r.get("bf_orig"):
                    parts.append(REASON_BACKFILL_RED.format(r["bf_orig"]))
                if r.get("red_diff") and r["inv_idx"] not in self._confirmed:
                    head = REASON_RED_MISMATCH.format(
                        "、".join(r["red_diff"].get("fields") or []))
                    if r.get("red_diff_src"):
                        head += f"〔对照：{r['red_diff_src']}〕"
                    parts.append(head)
                if parts:
                    if ev["reasons"]:
                        parts.append("、".join(ev["reasons"]))
                    text = "；".join(parts)
                else:
                    text = "、".join(ev["reasons"]) if ev["reasons"] else REASON_NO_DOUBT
                # 批 3-1：导入时已由人确认过的行 → 在其后标注（疑问原文照旧全展示，不隐藏）
                if r.get("is_import_confirmed"):
                    text += f"〔{REASON_IMPORT_CONFIRMED}〕"
                # 批 3-2b：导入时就地修改过字段 → 标注改了哪些（post 从镜表重建会显示
                # 原文旧值，此提示说明改动仍在、可用右侧「编辑回写」就地修正）
                if r.get("import_edit_hint"):
                    text += f"〔{REASON_IMPORT_EDIT}：{r['import_edit_hint']}〕"
                return text
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
                    text = REASON_BACKFILLED
                elif d.get("need_backfill"):
                    text = REASON_BACKFILL
                else:
                    # 批 1b：导入后「已在库」行无待办（收款已于入库时按 A10 处理）→ 无疑问
                    text = REASON_NO_DOUBT if self._mode == "post" else REASON_IN_LIBRARY
                # 批 3-2c（甲）：该行的收款在导入那一刻已由人点「确认」→ 与发票行同款标注
                # （留痕见 `_merged_data` 的 deferred 分支；绝不动状态文案本身）
                if r.get("is_import_confirmed"):
                    text += f"〔{REASON_IMPORT_CONFIRMED}〕"
                # 批 3-2b：导入时就地修改过字段（库内零落点）→ 标注改了哪些。
                # 「待补录」行的修改随补录条目落库（bf["handlers"]/buyer/…），生成端已
                # 过滤不掉它们；这里对拿得到的提示照常标注。
                if r.get("import_edit_hint"):
                    text += f"〔{REASON_IMPORT_EDIT}：{r['import_edit_hint']}〕"
                return text
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
                    fg = {"待补录": style.qcolor("neg_fg"),
                          "待确认": style.qcolor("amber_fg"),
                          "高置信": style.qcolor("pos_fg")}.get(
                              r["status"], style.qcolor("text_mute"))
                    item.setForeground(fg)
                if c == COL_REASON and r["kind"] == "invoice":
                    # 阶段 5：待补录（红字引用缺原票）与 deferred 的「需补录原票」同色（琥珀）
                    item.setForeground(
                        style.qcolor("amber_fg")
                        if (r["status"] == "待补录" or r["ev"]["reasons"])
                        else style.qcolor("pos_fg"))
                if c == COL_REASON and r["kind"] == "deferred":
                    # 需补录原票（要去补录）用琥珀；「请确认收款」两类用蓝；
                    # 批 1b：导入后「已在库」行无待办 → 用绿（与 REASON_NO_DOUBT 同色）
                    _dno = ((r["deferred"] or {}).get("invoice_no") or "").strip()
                    _need_bf = bool((r["deferred"] or {}).get("need_backfill"))
                    if _dno in self._backfills:
                        item.setForeground(style.qcolor("info_fg"))
                    elif _need_bf:
                        item.setForeground(style.qcolor("amber_fg"))
                    elif self._mode == "post":
                        item.setForeground(style.qcolor("pos_fg"))
                    else:
                        item.setForeground(style.qcolor("info_fg"))
                if r["status"] in ("待补录", "待确认"):
                    item.setBackground(style.qcolor("accent_red_bg"))
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
        n_confirmed = sum(1 for r in self._rows if r.get("is_confirmed"))
        n_bfed = sum(1 for r in self._rows if r.get("is_backfilled"))
        self.lbl_stat.setText(
            f"待补录 {n_backfill}　待确认 {n_pending}　高置信 {n_high}"
            + (f"　已补录 {n_bfed}" if n_bfed else "")
            + (f"　已确认 {n_confirmed}" if n_confirmed else "")
        )

        inv_total = sum(r["ev"]["total_amount"] for r in self._rows if r["kind"] == "invoice")
        n_inv = sum(1 for r in self._rows if r["kind"] == "invoice")
        pp = self._work.get("prepayments", [])
        pp_total = sum(x.get("amount", 0.0) for x in pp)
        # 应收账款(sheet3)行：本次不落 invoice/分摊；「已入库」行确认后只追加收款（A10），
        # 「需补录」行由「补录原票」逐张确认（方案 A）。阶段 4-2 起拆三类计数摊到汇总可见：
        # 还需补录 / 已补录待确认收款 / 已入库待确认收款。
        n_need = n_backfill
        # 阶段 5：待补录现在有两个来源，汇总按来源拆开（否则会全被说成「应收账款」）
        n_need_def = sum(1 for r in self._rows
                         if r["status"] == "待补录" and r["kind"] == "deferred")
        n_need_red = sum(1 for r in self._rows
                         if r["status"] == "待补录" and r["kind"] == "invoice")
        n_bf_rows = sum(1 for r in self._rows if r["kind"] == "deferred"
                        and r["status"] == "待确认"
                        and ((r["deferred"] or {}).get("invoice_no") or "").strip()
                        in self._backfills)
        n_inlib = sum(1 for r in self._rows if r["kind"] == "deferred"
                      and not (r["deferred"] or {}).get("need_backfill"))
        n_bf = len(self._backfills)   # B2h：底部计数随行内补录同步
        # 阶段 6：红字 ⇄ 蓝字不一致的行数（已点「确认」的不再计 —— 用户已放行）
        n_red_diff = sum(1 for r in self._rows
                         if r.get("red_diff") and r["inv_idx"] not in self._confirmed)
        self.lbl_summary.setText(
            f"发票 {n_inv} 张，合计 ¥{inv_total:,.2f}　"
            f"预收款 {len(pp)} 条，合计 ¥{pp_total:,.2f}"
            + (f"　·　需补录原票 {n_need} 张"
               f"（应收账款 {n_need_def} · 红字引用 {n_need_red}）" if n_need else "")
            + (f"　·　红字与蓝字原票不一致 {n_red_diff} 张（待确认）" if n_red_diff else "")
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
        # 阶段 5（A 甲）：叠加视图 —— 只看「本次填过补录」，与行自己的状态无关，
        # 故同一行可同时出现在「已补录」和「高置信 / 待确认」里（与「已确认」同模式）。
        if filt == "已补录":
            return bool(r.get("is_backfilled"))
        if filt == "已确认":
            return bool(r.get("is_confirmed"))
        if filt == "高置信":
            return r["status"] == "高置信"
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
            self._set_writeback_buttons(None)
            # ⚠️ `set_problem(None)` 内部会把面板**重置为可编辑**（它服务于问题行修正路径）
            # → post 模式下必须重新锁一次，否则「无行选中」时面板停在可编辑态
            self._lock_panel_for_mode()
            return
        for key in ("src", "no", "buyer", "amt", "receipt", "remark", "reason"):
            self._info[key].setText(self._row_field(r, key))

        if r["kind"] == "invoice":
            # 所有发票行（高/低/已修正）均在右侧就地编辑（方案 A：统一右栏）
            # 阶段 5（用户 2026-09-18）：红字行落「待补录」时**不给「确认」** ——
            # 原票不在库，确认写不出任何东西、状态也不会变（点了没反应会让人困惑），
            # 与 sheet3 待补录行同一口径；唯一出路是右侧「补录原票」。
            pending_bf = r["status"] == "待补录"
            self.fix_panel.setVisible(True)
            self.fix_panel.set_invoice(r["ev"]["_inv"], r["ev"])
            if r["ev"]["conf"] == "high" and r["status"] != "待确认":
                # 高置信：默认只读，防止被随手改坏；点「编辑」才解锁
                self.fix_panel.set_readonly(True)
                self._set_actions(edit=True)
            elif r["ev"]["conf"] == "high":
                # 阶段 6：本身高置信，但**红字与蓝字原票不一致**被抬到「待确认」
                # → 仍默认只读（防误改），但必须给「确认」：确认后才回高置信
                # （要求 1「不一致则需要提示，确认后才能保存」）。
                self.fix_panel.set_readonly(True)
                self._set_actions(edit=True, confirm=True)
            else:
                # 低置信（待确认）：可保存修改，也可点「确认」认可系统默认口径
                self.fix_panel.set_readonly(False)
                self._set_actions(save=True, confirm=not pending_bf)
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
            # 未修正的问题行走 set_problem 修正路径（始终可编辑）。
            # ⚠️ 没有「跳过」出口（2026-09-18 用户拍板）→ 必须修正后才能入库。
            self.fix_panel.setVisible(True)
            self.fix_panel.set_problem(r["problem"])
            self._set_actions(save=True)
        # 批 1b：导入后模式**只读**（编辑回写属批 3）；右栏表单一律锁死
        if self._mode == "post":
            self.fix_panel.set_readonly(True)
        # 阶段 3（B2g/B2h）：行内补录按钮的可见性与文案（随行类型/票号是否在库切换）
        self._set_backfill_button(r)
        # 批 3-3：post 的「编辑回写 / 还原为原件」可用性随行切换
        self._set_writeback_buttons(r)

    def _set_actions(self, *, save: bool = False,
                     refix: bool = False, confirm: bool = False,
                     edit: bool = False) -> None:
        """按当前行切换右侧行内动作按钮。

        批 1b：`post`（导入后）一律全隐藏 —— 本模式不写库（编辑回写属批 3）。
        """
        if self._mode == "post":
            save = refix = confirm = edit = False
        self.btn_save.setVisible(save)
        self.btn_refix.setVisible(refix)
        self.btn_confirm_row.setVisible(confirm)
        self.btn_edit.setVisible(edit)

    # ------------------------------------------------------------------ #
    # 导入后编辑回写 / 还原原件（批 3-3）
    # ------------------------------------------------------------------ #
    def _row_raw_id(self, r: Dict | None):
        """行 → 镜表行 id（raw_ledger.id）；无锚点返回 None。

        invoice 行在 `ev["_inv"]`（`rebuild_period_data` 产物，批 3-3 起带
        raw_id/synced）；deferred（sheet3）行由 `split_deferred` 原样搬 item，
        同样带锚点。pre 模式解析侧没有 raw_id → 恒 None，编辑入口天然只在 post。
        """
        if r is None:
            return None
        if r["kind"] == "invoice":
            return (r["ev"].get("_inv") or {}).get("raw_id")
        if r["kind"] == "deferred":
            return (r.get("deferred") or {}).get("raw_id")
        return None

    def _row_synced(self, r: Dict) -> bool:
        """行是否与 Excel 原件一致（默认 True：无标记按未修订处理，不给还原）。"""
        if r["kind"] == "invoice":
            return bool((r["ev"].get("_inv") or {}).get("synced", True))
        if r["kind"] == "deferred":
            return bool((r.get("deferred") or {}).get("synced", True))
        return True

    def _row_invoice_no(self, r: Dict) -> str:
        if r["kind"] == "invoice":
            return r["ev"].get("invoice_no") or ""
        if r["kind"] == "deferred":
            return (r.get("deferred") or {}).get("invoice_no") or ""
        return ""

    def _set_writeback_buttons(self, r: Dict | None) -> None:
        """按当前行切换「编辑回写 / 还原为原件」可用性（可见性在 _apply_mode_chrome）。

        编辑：行有镜表锚点（raw_id）即可 —— 与旧页 `_on_sel` 同口径。
        还原：还须「已手工修订」（synced=0）+ 账期有存档文件（还原要对原件重解析）。
        """
        raw_id = self._row_raw_id(r)
        has_archive = bool(self._post_meta.get("path"))
        self.btn_writeback.setEnabled(raw_id is not None)
        self.btn_restore.setEnabled(
            raw_id is not None and not self._row_synced(r) and has_archive)

    def _reload_post(self) -> None:
        """回写 / 还原成功后的重载：按账期从库重建（行值、四态、汇总、按钮全刷新）。"""
        self.load_period(self._period)

    def _writeback_row(self) -> None:
        """导入后编辑回写：WritebackDialog → `apply_edit`（单事务）→ 重载本账期。"""
        r = self._current_row()
        raw_id = self._row_raw_id(r)
        if raw_id is None:
            QMessageBox.information(self, "提示", "请先选中一行。")
            return
        raw = rl.get_row(raw_id)
        if raw is None:
            QMessageBox.information(self, "提示", "镜表行已不存在，请刷新后重试。")
            return
        dlg = WritebackDialog(self, self._period, self._row_invoice_no(r), raw, raw_id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._reload_post()
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.success("", "已回写（修改记录可查）", parent=self.window(),
                            position=InfoBarPosition.TOP_RIGHT, duration=2500)

    def _restore_row(self) -> None:
        """单行还原为 Excel 原件（spec §5.3）：确认 → restore_from_archive → 重载。"""
        r = self._current_row()
        raw_id = self._row_raw_id(r)
        if raw_id is None:
            QMessageBox.information(self, "提示", "请先选中一行。")
            return
        archive = self._post_meta.get("path") or ""
        if not archive:
            QMessageBox.information(self, "提示", "该账期无存档文件，无法还原原件。")
            return
        ret = QMessageBox.question(
            self, "还原为原件",
            f"将把发票 {self._row_invoice_no(r)} 恢复为 {self._period} 台账 Excel 原件"
            "（覆盖当前手工修改，同步业务表并留痕）。\n\n确认还原？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            restore_from_archive(self._period, raw_id, archive)
        except ValueError as e:
            QMessageBox.warning(self, "无法还原", str(e))
            return
        self._reload_post()
        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.success("", "已还原为 Excel 原件（修改记录可查）", parent=self.window(),
                        position=InfoBarPosition.TOP_RIGHT, duration=2500)

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

    def _batch_nos(self) -> set:
        """本批**工作副本**里出现过的票号（sheet1/2 + sheet3/deferred）。

        用途（阶段 5）：排除「原票会随本次台账入库」这类**假阳性**，两种情形都不该
        要求补录，也绝不能让用户点进补录弹窗 ——
        · 原票在本批 sheet1/2 → 确认入库时即被创建，无需补录。若强行填了补录，
          `importer._validate_backfills` 校验③ 会命中「与本次台账 sheet1/2 同号」
          → **整批入库中止**（用户 2026-09-18 明确要求避免这种卡死）；
        · 原票在本批 sheet3（deferred）→ 该行**自己**就是待补录行（票号相同），
          不必让红字行再指一次，否则同一票号会出现两行待办。

        结果按工作副本缓存（`_rebuild` 每轮置空），无 DB 查询。
        """
        if self._batch_nos_cache is None:
            nos: set = set()
            for inv in (self._work.get("invoices") or []):
                no = (inv.get("invoice_no") or "").strip()
                if no:
                    nos.add(no)
            for d in (self._work.get("deferred") or []):
                no = (d.get("invoice_no") or "").strip()
                if no:
                    nos.add(no)
            self._batch_nos_cache = nos
        return self._batch_nos_cache

    def _red_backfill_orig(self, r: Dict) -> str:
        """红字行 → 待补录的**蓝字原票号**；不适用（无需补录）返回空串。

        判据（阶段 5，B 乙）：本行是红字 + 反查到原票 + 原票不在库、不在本批、
        本次也没填过补录。四条缺一不可，全部收口在 `_row_backfill_target`（单一口径）。
        """
        if r["kind"] != "invoice":
            return ""
        tgt = self._row_backfill_target(r)
        if tgt is None:
            return ""
        no, in_lib = tgt
        if in_lib or no in self._backfills:
            return ""
        return no

    def _import_backfilled_nos(self) -> set:
        """post：本账期**导入时填过补录**的票号集合（批 3-2c 留痕）；pre 恒空集。

        与 `_import_confirmation` / `_import_edit_hint` 同为「只在 post 生效」：
        pre 的补录是**实时**的（`self._backfills`），若也读留痕，「覆盖式重导同一账期」
        时上一批的旧补录会冒充本批的（同款约束见那两个方法）。按轮缓存
        （`_rebuild` 每轮重置；集合来自 `review_rebuild` 已读好的 `data["backfilled"]`，
        本方法不查库）。
        """
        if self._mode != "post":
            return set()
        if self._imp_bf_nos_cache is None:
            self._imp_bf_nos_cache = set(self._data.get("backfilled") or ())
        return self._imp_bf_nos_cache

    def _row_backfilled(self, r: Dict) -> bool:
        """该行**是否填过补录**（「已补录」叠加视图的判据，与状态无关）。

        · 应收账款(deferred)行：行上票号即要补的票 → 看它有没有被补过；
        · 红字(invoice)行：要补的是**它引用的蓝字原票** → 看原票号有没有被补过
          （补录完成那刻 `status` 已回落，故不能只看 `bf_orig`，必须现算一次）；
        · 其余行：否。

        **pre 看本批**（`self._backfills` = 用户刚刚填的）；**post 看留痕**
        （`_import_backfilled_nos()`，批 3-2c）—— post 的 `backfills` 恒空
        （内容已入 `invoice`/`charge_detail`/`collection`），不读留痕的话「已补录」
        筛选点开**永远是空表**（用户以为自己上次白补了）。两模式互补，绝不同时生效。
        """
        hist = self._import_backfilled_nos()
        if r["kind"] == "deferred":
            no = ((r.get("deferred") or {}).get("invoice_no") or "").strip()
            return bool(no) and (no in self._backfills or no in hist)
        if r["kind"] == "invoice":
            tgt = self._row_backfill_target(r)
            if tgt and tgt[0] in self._backfills:
                return True
            if not hist:
                return False      # pre：不查库、不反查原票号（留痕恒空 → 直接 False）
            orig = self._red_orig_no((r.get("ev") or {}).get("invoice_no") or "")
            return bool(orig) and orig in hist
        return False

    def _row_backfill_target(self, r: Dict) -> tuple | None:
        """当前行是否有补录入口 → (要补/要看的票号, 该票是否已在库)；不适用返回 None。

        语义差异（§8 第 3 点）：
        - 应收账款(deferred)行：补**它自己**（行上票号即待补原票；
          已在库 = `need_backfill` 为假）；
        - 红字发票(invoice)行：补**它引用的原票**（票号经 `_red_orig_no` 反查），
          取不到原票号 → 无入口（可接受）。

        阶段 5 收紧：红字行引用的原票**在本批**（见 `_batch_nos`）且不在库时，
        返回 None（不给入口）—— 该票本次会随台账入库，强行补录会在写前校验被拦下、
        卡住整批（`_validate_backfills` 校验③）。
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
            if self._invoice_in_library(orig):
                return (orig, True)      # 已在库 → 「查看原票」（只读）
            if orig in self._batch_nos():
                return None              # 本次随台账入库 → 无需补录，也不给入口
            return (orig, False)
        return None

    # ------------------------------------------------------------------ #
    # 阶段 6：红字 ⇄ 蓝字 一致性
    # ------------------------------------------------------------------ #
    def _lib_blue_amounts(self, invoice_no: str) -> dict | None:
        """库中一张**蓝字原票**的 `{total_amount, handlers, persons_known}`；不在库 → None。

        取数走 `load_invoice_detail`（与补录页「编辑」同一口径），不另写 SQL；
        同轮 `_rebuild` 内按票号缓存（`_blue_cache` 每轮重置）。

        `persons_known`（阶段 6-2b）= 该票**有没有** `charge_detail` 分摊数据。
        没有 → 比对时**只比总金额**，绝不把「缺数据」当「不一致」
        （票在库 ⟹ 它那期台账已导入 ⟹ 分摊必在；此分支是零成本防御）。
        """
        no = (invoice_no or "").strip()
        if not no:
            return None
        if self._blue_cache is None:
            self._blue_cache = {}
        if no in self._blue_cache:
            return self._blue_cache[no]
        try:
            detail = load_invoice_detail(no) or {}
        except Exception:  # noqa: BLE001 查库失败只影响「提示」，绝不影响导入
            detail = {}
        out = None
        if detail:
            raw = [h for h in (detail.get("handlers") or []) if (h.get("name") or "").strip()]
            out = {
                "total_amount": detail.get("total_amount"),
                "handlers": [(h.get("name") or "", h.get("billing") or 0.0) for h in raw],
                "persons_known": bool(raw),
            }
        self._blue_cache[no] = out
        return out

    def _red_consistency(self, r: Dict) -> tuple:
        """红字行 → `(red_orig_diff 结果 | None, 蓝字侧来源文案)`；非红字行 → `(None, "")`。

        「蓝字侧」优先级（用户 2026-09-18 第 1 点两种情形）：
        1. **本次补录**（`self._backfills`）—— 情形 b：原票不在库，用户刚在弹窗里填的蓝字 d；
        2. **库中已有蓝字原票** —— 情形 a：`invoice` 表里已有的蓝字 a。
        两者都没有（= 还在「待补录」等补录）→ `(None, "")`：**不比对、不误报**。

        「红字侧」一律取**本行自己**（台账解析出的红字金额/经办人），不查库：
        复核页在确认入库前，红字票的 charge_detail 尚未写入（销项导入只建 `invoice`），
        查库会拿到空经办人 → 比对失真（这正是要求 2 预填缺口的同一个根因）。

        金额一律取绝对值（红字在库是负数）—— 由 `red_orig_diff` 负责，符号相反算一致。

        阶段 6-2b：情形 a 若查不到蓝字的 `charge_detail`（分摊数据）→ **只比总金额**，
        不判「经办人 / 经办人金额」（缺数据 ≠ 不一致；正常流程下走不到这条分支）。
        """
        if r["kind"] != "invoice":
            return (None, "")
        ev = r.get("ev") or {}
        if not ev.get("is_red"):
            return (None, "")
        tgt = self._row_backfill_target(r)
        if tgt is None:
            return (None, "")            # 无补录入口（本批/取不到原票号）→ 不比对
        orig = tgt[0]
        rt, rh = ev.get("total_amount"), ev.get("handlers")
        if orig in self._backfills:
            item = self._backfills.get(orig) or {}
            return (red_orig_diff(rt, rh, item.get("total_amount"), item.get("handlers")),
                    "本次补录")
        blue = self._lib_blue_amounts(orig)
        if blue is None:
            return (None, "")
        # 阶段 6-2b：蓝字侧没有分摊数据 → 只比总金额（缺数据 ≠ 不一致）
        return (red_orig_diff(rt, rh, blue["total_amount"], blue["handlers"],
                              orig_persons_known=blue.get("persons_known", True)),
                "库中蓝字原票")

    def _red_prefill_diff(self, r: Dict, data: dict):
        """补录**弹窗内容** vs 本行红字发票 → `red_orig_diff`（一致 → None）。

        供 `soft_check` 用：保存那一刻就拦住「蓝字与红字不一致」并让用户确认。

        ⚠️ **只对「本行真的是红字发票」比对**（2026-09-18 用户报告的误报）：
        sheet3 应收账款行是**蓝字**、且 deferred 行的 `ev` 恒为 None —— 那里根本没有
        红字发票可比；旧写法拿空 ev 当红字侧（0 元 / 无经办人）→ 弹窗填什么都报
        「不一致」。红字 ⇄ 蓝字比对只属于 sheet1/2 销项里的红字行落「待补录」的场景
        （`ev["is_red"]`）；sheet3 行的补录不挂本比对（补录页 `_red_soft_check` 走
        查库反查 `load_red_reference`，查不到红字返回 None，那条路径本来就对）。
        """
        ev = (r or {}).get("ev") or {}
        if not ev.get("is_red"):
            return None
        return red_orig_diff(ev.get("total_amount"), ev.get("handlers"),
                             (data or {}).get("total_amount"), (data or {}).get("handlers"))

    def _set_backfill_button(self, r: Dict | None) -> None:
        """按当前行切换「补录原票 / 查看原票」（B2g 文案 + B2h 刷新）。

        批 1b：`post`（导入后）隐藏入口 —— 补录/改补录都会写库，只读模式不给入口；
        需要补录的票在导入后去「发票补录」页处理（本表仍照实显示「待补录」）。
        """
        if self._mode == "post":
            self.btn_backfill.setVisible(False)
            return
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

        红字行（阶段 6）：预填=**本行红字发票**的金额/经办人（取绝对值），不查库（见下）。
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
        # 红字行：补的是**它引用的蓝字原票**。原票开票日期不可知（留空手填）；
        # 购方/金额/经办人**取本行红字发票**（用户 2026-09-18 第 2 点：默认带入红字发票的
        # 经办人及分摊金额；红字 -2000/张三-2000 → 弹窗默认 2000/张三/开票 2000）。
        # ⚠️ 不能查库（旧写法 `prefill_red_original(conn, no)`）：销项导入只建 `invoice`，
        # 红字票的 `charge_detail` 要等**本次台账**入库才写 → 复核页此刻查库**经办人必为空**。
        ev = r.get("ev") or {}
        handlers = [
            {"name": (n or "").strip(), "billing": abs(float(b or 0.0)),
             "received": 0.0, "date": ""}
            for n, b in (ev.get("handlers") or []) if (n or "").strip()
        ]
        total = abs(float(ev.get("total_amount") or 0.0))
        if handlers or total:
            return {
                "invoice_no": no,
                "invoice_date": "",          # 原票开票日期不可知，留空手填
                "buyer": ev.get("buyer") or "",
                "total_amount": total,
                "handlers": handlers,
            }
        # 兜底：本行连金额/经办人都解析不出来 → 退回查库取红字票（能拿到多少算多少）
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
            # 阶段 6 要求 3：票号已确定（= 红字反查出的蓝字原票号）→ **锁定不可编辑**
            locked_no=True,
            validator=lambda d, _k=no: self._validate_backfill(d, _k),
            # 阶段 6 要求 1(b)：保存那刻做软校验 —— 蓝字原票与红字发票不一致则弹提示，
            # 「取消」留在弹窗继续改（不丢输入），确定则照常收集。
            soft_check=(lambda d, _r=r: red_mismatch_notice(self._red_prefill_diff(_r, d))),
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
        """双击 → 查看该行的原始台账行。

        批 1b（post）：镜表**不存**原始行（`raw_ledger` 是归一化镜像）→ 按批次存档文件
        + `sheet_name` / `row_no` 读回（`review_rebuild.read_ledger_row`），逐列原文照旧可对照。
        """
        r = self._current_row()
        if r is None:
            return
        if r["kind"] == "invoice":
            inv = r["ev"]["_inv"]
            header = inv.get("header") or []
            raw_row = inv.get("raw_row") or []
            raw_sheet = inv.get("sheet_name") or ""
            sheet_name = raw_sheet or "—"
            row_no = inv.get("row_no") or 0
        elif r["kind"] == "deferred":
            d = r["deferred"] or {}
            header = d.get("header") or []
            raw_row = d.get("raw_row") or []
            raw_sheet = d.get("sheet_name") or ""
            sheet_name = raw_sheet or SHEET_LABEL.get(d.get("sheet"), d.get("sheet") or "—")
            row_no = d.get("row_no") or 0
        else:
            p = r["problem"] or {}
            header = p.get("header") or []
            raw_row = p.get("raw_row") or []
            raw_sheet = p.get("sheet_name") or ""
            sheet_name = raw_sheet or SHEET_LABEL.get(p.get("sheet"), p.get("sheet") or "—")
            row_no = p.get("row_no") or 0
        if not raw_row and self._mode == "post":
            # 导入后：镜表无原始行 → 从该批次存档文件按同一行号读回
            from app.engine.review_rebuild import read_ledger_row
            header, raw_row = read_ledger_row(raw_sheet, row_no, self._path)
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

        红字(invoice)行（阶段 5）：该行引用了一张**不在库**的蓝字原票时，本行落「待补录」，
        确认同样不生效（要补的是那张原票，与本行收款口径无关），须先点「补录原票」。
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
        # 阶段 5（B 乙）：红字行落「待补录」时确认同样不生效 —— 它要补的是**引用的
        # 蓝字原票**，与确认本行收款口径无关；只提示先去补录（UI 上按钮已隐藏，此处兜底）。
        if r["status"] == "待补录":
            QMessageBox.information(
                self, "请先补录原票",
                f"该红字发票引用的蓝字原票 {r.get('bf_orig') or '—'} 不在库中，"
                "需先补录原票。\n\n"
                "请点右侧「补录原票」就地填写（随本次台账一起入库）；"
                "补录后本行会回到「高置信」。\n\n"
                "本次点「确认」不会写入任何数据。")
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

        # 批 3-1：本次点过「确认」的发票行 → 随台账**同一事务**落一条留痕
        # （`anomaly_note` 独立 dim，由 `commit_ledger_import` 写）。
        # 为什么必须落库：确认只活在内存集合 `_confirmed` 里 → 不落库，「导入后」模式重推
        # 四态时会把这些疑问重新报成「待确认」，而 post 隐藏了「确认」按钮 ⇒ 点不掉的假待办。
        confs: List[Dict] = []
        for r in self._rows:
            if r["kind"] == "invoice" and r.get("work_idx") in self._confirmed:
                ev = r.get("ev") or {}
                _no = str(ev.get("invoice_no") or "").strip()
                if not _no:
                    continue
                parts = list(ev.get("reasons") or [])
                if r.get("red_diff"):
                    parts.insert(0, "红字与蓝字原票不一致（"
                                 + "、".join(r["red_diff"].get("fields") or []) + "）")
                confs.append({
                    "invoice_no": _no,
                    "note": "；".join(parts) or "兜底判定（系统口径不确定，已人工过目）",
                })
            elif r["kind"] == "deferred" and r.get("d_index") in self._deferred_confirmed:
                # 批 3-2c（甲）：sheet3「确认收款」与发票行的「确认」**同性质** —— 都只
                # 活在内存集合（`_deferred_confirmed` / `_confirmed`）里，库里零痕迹。
                # 不落留痕 ⇒ post 里 `is_confirmed` 恒 False，而状态又被 `_deferred_status`
                # 一律降成「高置信」⇒ 全部沉进「高置信」，用户看不到自己确认过哪些行。
                # 状态为「待补录」的行进不来（`_confirm_row` 直接 return）；但
                # `need_backfill=True` 而**本批已补录**、随后又点了确认的行**会**进来 ——
                # 它的收款由补录条目写（A10 里对补录票号跳过），本留痕只回答
                # 「这一行人工过目过吗」，与收款怎么写的无关。
                d = r.get("deferred") or {}
                _no = str(d.get("invoice_no") or "").strip()
                if not _no:
                    continue
                confs.append({
                    "invoice_no": _no,
                    "note": ("已确认收款口径（应收账款：本次已补录原票）"
                             if d.get("need_backfill")
                             else "已确认收款口径（应收账款：已在库，采纳台账收款）"),
                })
        merged["confirmations"] = confs

        # 批 3-2b：本次就地修改过字段的行 → 随台账**同一事务**落「修改字段」留痕
        # （`anomaly_note` 独立 dim=import_edit，由 `commit_ledger_import` 写）。
        # 为什么必须落库：sheet3 在库行的文本修改（购方/经办人分摊/案号）库内零落点，
        # 「导入后」模式从 raw_ledger（原文镜表）重建 → 会无声显示旧值；留痕让 post 页
        # 原因列标注「导入时已修改：字段…」。「待补录」行的修改随补录条目落库 → 不过滤
        # 会重复提示，故 need_backfill 行跳过。发票行的修改虽随本批写库，但 post 页同样
        # 只显示镜表原文（批 3-2 只回填经办人分摊）→ 一并留痕。
        edit_fields: Dict[str, List[str]] = {}

        # 已存在发票的右侧就地编辑：原地覆盖
        for inv_idx, ed in self._inv_edits.items():
            invs = merged.get("invoices", [])
            if 0 <= inv_idx < len(invs):
                inv = invs[inv_idx]
                _no = str(inv.get("invoice_no") or "").strip()
                _fields = self._edit_changed_fields(inv, ed)
                if _no and _fields:
                    edit_fields.setdefault(_no, []).extend(_fields)
                self._apply_invoice_edit(inv, ed)

        # A3 + A10：应收账款(sheet3)行的就地编辑与「确认收款」结果回写 data["deferred"]。
        # 下标对应 data["deferred"] 顺序（split_deferred 只追加、不重排）。
        deferred = merged.get("deferred") or []
        for d_idx, ed in sorted(self._deferred_edits.items()):
            if 0 <= d_idx < len(deferred):
                d = deferred[d_idx]
                if not d.get("need_backfill"):
                    _no = str(d.get("invoice_no") or "").strip()
                    _fields = self._edit_changed_fields(d, ed)
                    if _no and _fields:
                        edit_fields.setdefault(_no, []).extend(_fields)
                self._apply_invoice_edit(d, ed)
        merged["edit_hints"] = [
            {"invoice_no": no, "note": "、".join(dict.fromkeys(fields))}
            for no, fields in edit_fields.items()
        ]
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
        # 批 3-2c：本批**补过哪些票号** → 随台账同一事务落一条独立留痕
        # （`anomaly_note` dim=import_backfill，由 `commit_ledger_import` 写）。
        # 与上面 `backfills` 的**内容**分开：内容随本事务写进
        # `invoice`/`charge_detail`/`collection`，入库后就不再需要；
        # 但「已补录」筛选（`_row_backfilled`）在 post 里需要知道**本批补过哪几张票**
        # —— 不落痕，那个筛选按钮点开永远是空表。
        merged["backfilled"] = [no for no in bf_map if no]

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
        """「确认入库」——**全部处理完毕**才放行（2026-09-18 用户拍板）。

        硬拦条件（不再问「是否继续」）：只要还存在任一「待补录 / 待确认」行即拒绝入库。
        该单一条件天然覆盖全部三类未处理行：
          · 未修正的问题行 —— 解析失败一律落「待确认」，且已无「跳过」出口；
          · 低置信发票行 / 红字与蓝字原票不一致的行（确认后才回高置信）；
          · 应收账款 sheet3 行 —— 「待补录」需先用右侧「补录原票」补录，
            「待确认」需点「确认」采纳收款口径。
        放弃本次导入请点「取消」（`reject`），真实 data 一行不动。

        批 1b：`post`（导入后）模式**不写库** —— 本方法直接返回（`_data` 一行不改），
        也不做硬拦检查（无待办概念，按钮已隐藏）。
        """
        if self._mode == "post":
            return
        n_backfill = sum(1 for r in self._rows if r["status"] == "待补录")
        n_pending = sum(1 for r in self._rows if r["status"] == "待确认")
        if n_backfill or n_pending:
            n_prob = sum(1 for r in self._rows
                         if r["kind"] == "problem" and r["status"] == "待确认")
            n_inlib = sum(1 for r in self._rows
                          if r["kind"] == "deferred" and r["status"] == "待确认"
                          and not (r["deferred"] or {}).get("need_backfill"))
            n_red = sum(1 for r in self._rows if r.get("red_diff")
                        and r["inv_idx"] not in self._confirmed)
            msg = f"还有 {n_backfill + n_pending} 行未处理，无法入库：\n"
            if n_backfill:
                msg += (f"\n· 「待补录」{n_backfill} 行：引用的原票不在库，"
                        "请用右侧「补录原票」逐张补录（补完即离开本状态）。\n")
            if n_pending:
                msg += (f"\n· 「待确认」{n_pending} 行：请逐行核对后点右侧「确认」，"
                        "或先「保存修改」修正数据。")
                if n_prob:
                    msg += f"（其中 {n_prob} 行是解析失败的问题行，必须先修正）"
                if n_red:
                    msg += (f"（其中 {n_red} 行是「红字与蓝字原票不一致」，"
                            "请核对金额 / 经办人 / 经办人金额）")
                if n_inlib:
                    msg += (f"（其中 {n_inlib} 行是 sheet3「已入库，请确认收款」，"
                            "确认后本次会追加该票收款）")
                msg += "\n"
            msg += "\n全部处理完毕后才能入库。若要放弃本次导入，请点「取消」。"
            QMessageBox.warning(self, "尚有未处理的行", msg)
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
