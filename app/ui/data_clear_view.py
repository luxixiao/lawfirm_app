"""数据情况页（需求 1）：需输入「我确认清空数据」验证后才可清空

清空口径（2026-09-16 用户确认）：
- **保留**：员工管理（staff 员工名单 / staff_type_def 员工类型）、
  费用类型（expense_cat 类型清单 / expense_category 分类说明）、快照（snapshot）。
- **清空**：数据库里**除上述保留表之外的全部表**（不再硬编码白名单，
  运行时按 sqlite_master 求差集，新增业务表自动纳入清空范围）。
- 清空后执行 VACUUM 回收空间。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget,
)

from app.ui.widgets import CaptionLabel, PageHeader, PrimaryPushButton
from app.db import get_conn

_CONFIRM_TEXT = "我确认清空数据"

# 保留表（页面口径）：员工管理 + 费用类型 + 快照
_KEEP_TABLES = ["staff", "staff_type_def", "expense_cat", "expense_category", "snapshot"]
_KEEP_LABELS = {
    "staff": "员工管理 · 员工名单",
    "staff_type_def": "员工管理 · 员工类型",
    "expense_cat": "费用类型 · 类型清单",
    "expense_category": "费用类型 · 分类说明",
    "snapshot": "快照",
}

# 清空表的中文说明（未列出的表直接显示表名）
_CLEAR_LABELS = {
    "invoice": "发票",
    "raw_invoice": "销项发票镜表",
    "raw_ledger": "发票台账镜表",
    "raw_salary": "工资表镜表",
    "charge_detail": "经办人发票收款情况",
    "collection": "收款明细",
    "refund": "退款",
    "prepayment": "预收款",
    "prepayment_offset": "预收款核销",
    "expense_ledger": "费用台账",
    "import_batch": "导入批次",
    "import_log": "导入日志",
    "change_log": "修改记录",
    "received_snapshot": "收款认定快照",
    "anomaly_note": "已确认异常",
    "tax_declaration": "个税申报",
    "tax_deduction": "费用扣除",
    "calc_sheet": "分成计算 · 计算表",
    "calc_indicator": "分成计算 · 指标",
}


def _db_tables(conn) -> list:
    """当前库全部业务表（排除 sqlite 内部表）。"""
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _clear_tables(conn) -> list:
    """需要清空的表 = 全部表 − 保留表（运行时求差集，新增业务表自动纳入）。"""
    keep = set(_KEEP_TABLES)
    return [t for t in _db_tables(conn) if t not in keep]


def _label(tbl: str, mapping: dict) -> str:
    return mapping.get(tbl, tbl)


class DataClearView(QWidget):
    # 清空成功后发出。本页看不到复核页的**内存待确认队列**（它不在库里，
    # DELETE 清不掉），必须由主窗口据此重置，否则清空后复核页仍留着已失效的
    # 待确认数据（2026-09-17 用户报障）。
    data_cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(14)

        lay.addWidget(PageHeader(
            "数据情况",
            "清空全部业务数据（重新导入前使用）。员工管理 / 费用类型 / 快照将保留，"
            "详见下方说明。",
        ))

        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(150)
        lay.addWidget(self.info)

        bar = QHBoxLayout()
        bar.addWidget(CaptionLabel("请输入：「我确认清空数据」"))
        self.input = QLineEdit()
        self.input.setPlaceholderText(_CONFIRM_TEXT)
        self.input.textChanged.connect(self._on_text)
        bar.addWidget(self.input, 1)
        lay.addLayout(bar)

        self.btn_clear = PrimaryPushButton("清空数据")
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(self._do_clear)
        lay.addWidget(self.btn_clear)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.setPlaceholderText("操作结果…")
        lay.addWidget(self.log)
        lay.addStretch()

        self._refresh_info()

    # ------------------------------------------------------------------ #
    def _refresh_info(self) -> None:
        """按当前库实际表清单生成「将清空 / 将保留」说明。"""
        conn = get_conn()
        try:
            clears = _clear_tables(conn)
        finally:
            conn.close()
        clear_lines = [f"{t}（{_label(t, _CLEAR_LABELS)}）" for t in clears]
        keep_lines = [f"{_label(t, _KEEP_LABELS)}（{t}）" for t in _KEEP_TABLES]
        self.info.setPlainText(
            f"将清空（{len(clears)} 张表）：\n  · " + "\n  · ".join(clear_lines) +
            f"\n\n将保留（{len(_KEEP_TABLES)} 张表）：\n  · " + "\n  · ".join(keep_lines) +
            "\n\n清空操作不可撤销，请确认已导出或备份所需数据。")

    def _on_text(self, text: str) -> None:
        self.btn_clear.setEnabled(text.strip() == _CONFIRM_TEXT)

    def _do_clear(self) -> None:
        if self.input.text().strip() != _CONFIRM_TEXT:
            QMessageBox.warning(self, "验证失败", f"请输入准确字样：「{_CONFIRM_TEXT}」")
            return
        ret = QMessageBox.question(
            self, "最终确认",
            "即将清空全部业务数据（仅保留：员工管理 / 费用类型 / 快照），"
            "此操作不可撤销！\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        conn = get_conn()
        try:
            targets = _clear_tables(conn)
            counts = []
            for tbl in targets:
                n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
                conn.execute(f'DELETE FROM "{tbl}"')
                counts.append(f"{tbl}: {n} 行")
            conn.commit()
            conn.execute("VACUUM")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "清空失败", str(e))
            return
        finally:
            conn.close()
        self.log.setPlainText("已清空：\n  · " + "\n  · ".join(counts) +
                              "\n\n保留（未清空）：员工管理 / 费用类型 / 快照。\n"
                              "建议重新从「导入」页导入台账。")
        QMessageBox.information(self, "完成", "业务数据已清空。")
        # 库已空 → 通知主窗口重置复核页的内存待确认队列 + 刷新侧栏角标/导入页提示条。
        # 放在提示框之后：先让用户看到清空结果，再收拾其它页面的残留状态。
        self.data_cleared.emit()
