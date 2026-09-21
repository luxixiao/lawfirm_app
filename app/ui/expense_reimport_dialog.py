"""同账期重导确认弹窗（feature 3）。

费用台账「同账期二次导入」时，先比对本次解析行与库里 active 批次的字段级差异，
弹此窗让用户确认是否以新文件覆盖旧账期数据：

- 「覆盖（以新文件为准）」→ ``QDialog.Accepted``（importer 继续删除旧批次并写新行）；
- 「取消导入」→ ``QDialog.Rejected``（importer 中止导入，保留原账期数据）。

皮肤契约（硬要求）：
- 颜色一律经 ``style.qcolor`` 取语义 token（如 ``neg_fg`` / ``pos_fg`` / ``info_fg``），
  禁止十六进制色号字面量与 ``QColor(r,g,b)``；
- 固定尺寸（对话框宽高、按钮高、表格行高）一律 ``scale.px(基准值)``，禁止裸数字。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHeaderView, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from app.engine.expense_validation import ReimportDiff
from app.ui import scale, style
from app.ui.widgets import CaptionLabel, DialogTitleLabel


def _fmt(v) -> str:
    """差异值格式化：None → 空串；浮点保留两位；其余转字符串。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


class ExpenseReimportDialog(QDialog):
    """同账期重导的字段级差异确认弹窗。"""

    def __init__(self, diff: ReimportDiff, parent=None) -> None:
        super().__init__(parent)
        self._diff = diff

        period = diff.period or ""
        n_match = len(diff.matched)
        n_add = len(diff.added)
        n_del = len(diff.removed)
        total = n_match + n_add + n_del

        self.setWindowTitle("同账期重导确认")
        # 固定尺寸一律走 scale.px（随字号档位缩放，禁止裸数字）
        self.resize(scale.px(660), scale.px(560))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(scale.px(24), scale.px(20), scale.px(24), scale.px(20))
        lay.setSpacing(scale.px(12))

        # ---- 顶部摘要 ----
        title = DialogTitleLabel(
            f"同账期重导，检测到 {total} 处差异：新增 {n_add} / 删除 {n_del} / 修改 {n_match}")
        lay.addWidget(title)
        sub = CaptionLabel(
            f"账期 {period}：覆盖将以本次导入文件为准替换原账期全部费用数据；"
            "取消则保留原账期数据不变。")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        # ---- 差异表（只读）----
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["类型", "序号", "名称", "字段", "旧值(db)", "新值(parsed)"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(scale.px(34))
        # 固定列宽 + 拉伸「名称 / 旧值 / 新值」以填满宽度
        self.table.setColumnWidth(0, scale.px(56))
        self.table.setColumnWidth(1, scale.px(48))
        self.table.setColumnWidth(3, scale.px(104))
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)

        # 解析行 / 库行按 seq 索引，便于取出名称（added 取新文件，removed 取旧库）
        parsed_by_seq = {int(r.get("seq")): r for r in diff.parsed}
        db_by_seq = {int(r.get("seq")): r for r in diff.db}

        # 修改（matched）：每个 field_diff 一行
        for seq, fds in diff.matched:
            name = (parsed_by_seq.get(seq) or {}).get("name", "")
            for fd in fds:
                self._add_row("修改", seq, name, fd.get("field", ""),
                              fd.get("db"), fd.get("parsed"))
        # 新增（added，仅新文件有）：整行替换
        for seq in diff.added:
            name = (parsed_by_seq.get(seq) or {}).get("name", "")
            self._add_row("新增", seq, name, "—", None, "新行")
        # 删除（removed，仅旧库有）：旧行将消失
        for seq in diff.removed:
            name = (db_by_seq.get(seq) or {}).get("name", "")
            self._add_row("删除", seq, name, "—", "原行", None)

        # ---- 底部按钮 ----
        btns = QHBoxLayout()
        btns.addStretch()
        self.btn_cancel = QPushButton("取消导入")
        self.btn_cancel.setFixedHeight(scale.px(36))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_overwrite = QPushButton("覆盖（以新文件为准）")
        self.btn_overwrite.setObjectName("primary")
        self.btn_overwrite.setFixedHeight(scale.px(36))
        self.btn_overwrite.clicked.connect(self.accept)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_overwrite)
        lay.addLayout(btns)

    # ------------------------------------------------------------------ #
    def _add_row(self, kind: str, seq: int, name, field, old_val, new_val) -> None:
        """追加一行差异；按类型上色（背景浅底 + 文字色，全部走 style.qcolor）。"""
        row = self.table.rowCount()
        self.table.insertRow(row)

        type_item = QTableWidgetItem(kind)
        if kind == "修改":
            type_item.setForeground(style.qcolor("info_fg"))
        elif kind == "新增":
            type_item.setForeground(style.qcolor("pos_fg"))
        else:  # 删除
            type_item.setForeground(style.qcolor("neg_fg"))
        self.table.setItem(row, 0, type_item)
        self.table.setItem(row, 1, QTableWidgetItem(str(seq)))

        name_item = QTableWidgetItem(_fmt(name))
        name_item.setForeground(style.qcolor("text"))
        self.table.setItem(row, 2, name_item)

        field_item = QTableWidgetItem(_fmt(field))
        field_item.setForeground(style.qcolor("text"))
        self.table.setItem(row, 3, field_item)

        old_item = QTableWidgetItem(_fmt(old_val))
        old_item.setForeground(style.qcolor("neg_fg"))
        self.table.setItem(row, 4, old_item)

        new_item = QTableWidgetItem(_fmt(new_val))
        new_item.setForeground(style.qcolor("pos_fg"))
        self.table.setItem(row, 5, new_item)

        for c in range(6):
            it = self.table.item(row, c)
            if it is not None:
                it.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
