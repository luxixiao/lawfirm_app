"""发票台账导入 - 问题行修正对话框（方案 A：汇总处理）

解析失败的问题行统一弹一个对话框：左侧表格列出全部问题行，
右侧表单对选中行手动修正（发票行：开票日期/开票金额/经办人及金额/收款金额/收款时间；
预收款行：收到时间/金额/经办人）。每行可「保存修改」或「跳过」，
确认后由调用方把修正数据合并进导入。
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_
from app.importer.parse_handler import parse_handler_column
from app.ui.widgets import CaptionLabel, PushButton, SubtitleLabel

_TYPES = {"invoice": "发票", "prepayment": "预收款"}


def _norm_dt(s: str) -> str | None:
    """规范化日期：2 段（YYYY-MM）保留月粒度，3 段（YYYY-MM-DD）保留日粒度；空返回 None，失败抛 ImportError_"""
    s = (s or "").strip()
    if not s:
        return None
    parts = [int(x) for x in re.findall(r"\d+", s)]
    if len(parts) == 2:  # 年月（与导入主流程收款日期语义一致）
        y, mo = parts
        if y < 100:
            y += 2000
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}"
        raise ImportError_(f"日期无法解析: 「{s}」")
    return normalize_date(s)


class ProblemDialog(QDialog):
    def __init__(self, problems: list, staff_names: list, period: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("发票台账导入 - 问题行修正")
        self.resize(920, 560)
        self._problems = problems
        self._staff = set(staff_names)
        self._year = int(period.split("-")[0]) if "-" in period else None
        # self._fix[i] = 已保存的修正 data 或 None；self._skip = set(index)
        self._fix: dict = {}
        self._skip: set = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        t = SubtitleLabel(f"发现 {len(problems)} 个问题行")
        root.addWidget(t)
        h = CaptionLabel(
            "以下行在解析时失败，请在右侧修正后保存；无法修正的可跳过（不入库）。"
            "经办人须为职工花名册中的姓名；收款时间支持 2025-02 或 2025-02-13 格式。"
        )
        root.addWidget(h)

        # ---- 中部：表格 + 表单 ----
        mid = QHBoxLayout()
        mid.setSpacing(12)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "类型", "行号", "发票号", "购方", "金额", "原因", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate([40, 60, 50, 150, 110, 90, 200, 70]):
            self.table.setColumnWidth(i, w)
        self._fill_table()
        self.table.itemSelectionChanged.connect(self._load_form)
        mid.addWidget(self.table, 3)

        form_box = QWidget()
        form_box.setObjectName("problemForm")
        fv = QVBoxLayout(form_box)
        fv.setContentsMargins(12, 12, 12, 12)
        fv.setSpacing(8)
        self._form_fields: dict = {}   # key -> QLineEdit/QPlainTextEdit
        self._form_labels: dict = {}   # key -> QLabel

        def field(key: str, label: str, *, multiline: bool = False, hint: str = "") -> None:
            lbl = QLabel(label)
            if multiline:
                w = QPlainTextEdit()
                w.setFixedHeight(72)
            else:
                w = QLineEdit()
            if hint:
                w.setPlaceholderText(hint)
            fv.addWidget(lbl)
            fv.addWidget(w)
            self._form_fields[key] = w
            self._form_labels[key] = lbl

        # 发票行字段
        field("inv_date", "开票日期", hint="如 2025-02-13，可空")
        field("inv_amount", "开票金额", hint="必填，数字")
        field("handlers", "经办人及金额", multiline=True, hint="如：徐志庆4500、柳立中4500；无金额=平摊")
        field("recv_amt", "收款金额", hint="可空；填了必须给收款时间")
        field("recv_time", "收款时间", hint="如 2025-02 或 2025-02-13")
        # 预收款行字段
        field("pp_date", "收到时间", hint="如 2025-02-13，可空")
        field("pp_amount", "金额", hint="必填，数字")
        field("pp_person", "经办人", hint="可空")
        self._btn_save = PushButton("保存修改")
        self._btn_save.clicked.connect(self._save_fix)
        self._btn_skip = PushButton("跳过此行")
        self._btn_skip.clicked.connect(self._skip_row)
        row = QHBoxLayout()
        row.addWidget(self._btn_save)
        row.addWidget(self._btn_skip)
        row.addStretch()
        fv.addLayout(row)
        fv.addStretch()
        mid.addWidget(form_box, 2)
        root.addLayout(mid, 1)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        bottom.addStretch()
        self._btn_ok = PushButton("确认导入（未处理行将跳过）")
        self._btn_ok.setObjectName("primary")
        self._btn_ok.clicked.connect(self._confirm)
        bottom.addWidget(self._btn_ok)
        root.addLayout(bottom)

        if self.table.rowCount():
            self.table.selectRow(0)

    # ---- 表格 ----
    def _fill_table(self) -> None:
        for i, p in enumerate(self._problems):
            vals = [
                str(i + 1), _TYPES.get(p["kind"], p["kind"]), str(p.get("row_no", "")),
                p.get("invoice_no", "") or "", p.get("buyer", "") or "",
                p.get("total_amount", "") or p.get("amount_text", "") or "",
                p.get("reason", ""), "待处理",
            ]
            self.table.insertRow(i)
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem(v))
        self._set_status_all()

    def _set_status_all(self) -> None:
        for i in range(self.table.rowCount()):
            self._set_status(i)

    def _set_status(self, i: int) -> None:
        if i in self._skip:
            st = "已跳过"
        elif i in self._fix:
            st = "已修改"
        else:
            st = "待处理"
        self.table.item(i, 7).setText(st)

    # ---- 表单 ----
    def _current_idx(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _show_fields(self, keys: list) -> None:
        for k, w in self._form_fields.items():
            visible = k in keys
            w.setVisible(visible)
            self._form_labels[k].setVisible(visible)

    def _load_form(self) -> None:
        idx = self._current_idx()
        if idx is None:
            return
        p = self._problems[idx]
        f = self._form_fields
        fix = self._fix.get(idx)
        if p["kind"] == "prepayment":
            self._show_fields(["pp_date", "pp_amount", "pp_person"])
            f["pp_date"].setText((fix or {}).get("received_date") or p.get("date_text", "") or "")
            f["pp_amount"].setText((fix or {}).get("amount_text") or p.get("amount_text", "") or "")
            f["pp_person"].setText((fix or {}).get("person_text") or p.get("person_text", "") or "")
        else:
            self._show_fields(["inv_date", "inv_amount", "handlers", "recv_amt", "recv_time"])
            f["inv_date"].setText((fix or {}).get("invoice_date") or p.get("date_text", "") or "")
            f["inv_amount"].setText((fix or {}).get("total_amount_text") or p.get("total_amount", "") or "")
            f["handlers"].setPlainText((fix or {}).get("handler_text") or p.get("handler_text", "") or "")
            f["recv_amt"].setText((fix or {}).get("recv_amount_text") or "")
            f["recv_time"].setText((fix or {}).get("recv_time_text") or "")

    def _save_fix(self) -> None:
        """校验并保存当前行的修正；失败弹提示不保存"""
        idx = self._current_idx()
        if idx is None:
            QMessageBox.information(self, "提示", "请先在左侧选择一行")
            return
        p = self._problems[idx]
        f = self._form_fields
        try:
            if p["kind"] == "invoice":
                data = self._validate_invoice(p, f)
            else:
                data = self._validate_prepayment(p, f)
        except ImportError_ as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        self._fix[idx] = data
        self._skip.discard(idx)
        self._set_status(idx)

    def _validate_invoice(self, p: dict, f: dict) -> dict:
        inv_date = _norm_dt(f["inv_date"].text())
        total_txt = f["inv_amount"].text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            raise ImportError_(f"开票金额无法解析: 「{total_txt}」") from None

        handler_text = f["handlers"].toPlainText().strip()
        handlers = parse_handler_column(handler_text, total, p.get("invoice_no", ""))
        missing = [n for n, _ in handlers if n not in self._staff]
        if missing:
            raise ImportError_(f"经办人不在职工花名册中: {', '.join(sorted(set(missing)))}")

        recv_amt_txt = f["recv_amt"].text().strip()
        recv_time_txt = f["recv_time"].text().strip()
        receipts: list = []
        if recv_amt_txt or recv_time_txt:
            if not recv_time_txt:
                raise ImportError_("填写了收款金额，必须同时填写收款时间")
            try:
                amt = float(recv_amt_txt.replace(",", "")) if recv_amt_txt else 0.0
            except ValueError:
                raise ImportError_(f"收款金额无法解析: 「{recv_amt_txt}」") from None
            if amt == 0:
                raise ImportError_("收款金额不能为 0")
            ym = _norm_dt(recv_time_txt)
            if ym is None:
                raise ImportError_("收款时间无法解析")
            receipts.append((ym, amt))

        return {
            "kind": "invoice",
            "invoice_no": p.get("invoice_no", ""),
            "invoice_date": inv_date or "",
            "total_amount": total,
            "total_amount_text": total_txt,
            "handlers": handlers,
            "handler_text": handler_text,
            "receipts": receipts,
            "recv_amount_text": recv_amt_txt,
            "recv_time_text": recv_time_txt,
        }

    def _validate_prepayment(self, p: dict, f: dict) -> dict:
        received_date = _norm_dt(f["pp_date"].text())
        amt_txt = f["pp_amount"].text().strip()
        try:
            amount = float(amt_txt.replace(",", "")) if amt_txt else 0.0
        except ValueError:
            raise ImportError_(f"金额无法解析: 「{amt_txt}」") from None
        return {
            "kind": "prepayment",
            "received_date": received_date or "",
            "amount": amount,
            "amount_text": amt_txt,
            "person_text": f["pp_person"].text().strip(),
        }

    def _skip_row(self) -> None:
        idx = self._current_idx()
        if idx is None:
            QMessageBox.information(self, "提示", "请先在左侧选择一行")
            return
        self._skip.add(idx)
        self._fix.pop(idx, None)
        self._set_status(idx)

    def _confirm(self) -> None:
        """确认：未处理行按跳过处理，返回 resolved 列表并关闭"""
        resolved = []
        for i, p in enumerate(self._problems):
            if i in self._skip:
                resolved.append({"index": i, "action": "skip", "data": None})
            elif i in self._fix:
                resolved.append({"index": i, "action": "fix", "data": self._fix[i]})
            else:
                resolved.append({"index": i, "action": "skip", "data": None})
        self._resolved = resolved
        self.accept()

    def resolved(self) -> list:
        """返回 [{index, action: fix|skip, data}]"""
        return getattr(self, "_resolved", [])
