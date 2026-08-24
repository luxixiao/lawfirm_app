"""发票台账导入 - 问题行修正对话框（方案 C：逐人分摊）

解析失败的问题行统一弹一个对话框：左侧表格列出全部问题行，右侧对选中行
手动修正。发票行采用「逐经办人」编辑（方案 C）：
  - 开票日期 / 开票总额
  - ② 开票分摊表：经办人(花名册下拉) | 开票金额
  - ③ 收款分摊表：经办人 | 收款金额 | 收款日期（与开票表经办人一一对应）
  - 分摊按钮：均分开票额 / 均分收款 / 按开票比例生成(全额,账期月)
预收款行保持原有简单字段。每行可「保存修改」或「跳过」，确认后由调用方合并入库。
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
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
        self.resize(1000, 600)
        self._problems = problems
        self._staff = set(staff_names)
        self._staff_list = sorted(staff_names)
        self._period = period  # "2025-03"
        # self._fix[i] = 已保存的修正 data 或 None；self._skip = set(index)
        self._fix: dict = {}
        self._skip: set = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        t = SubtitleLabel(f"发现 {len(problems)} 个问题行")
        root.addWidget(t)
        h = CaptionLabel(
            "以下行解析失败，请在右侧逐经办人修正后保存；无法修正可跳过（不入库）。"
            "经办人须为职工花名册中的姓名；收款日期支持 2025-02 或 2025-02-13。"
            "开票金额合计须等于开票总额；填了收款金额必须同时填收款日期。"
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

        # ---- 右侧表单 ----
        form_box = QWidget()
        form_box.setObjectName("problemForm")
        fv = QVBoxLayout(form_box)
        fv.setContentsMargins(12, 12, 12, 12)
        fv.setSpacing(8)

        # 发票行容器
        self.inv_box = QWidget()
        iv = QVBoxLayout(self.inv_box)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(6)

        def line_field(label: str, hint: str = "") -> QLineEdit:
            lbl = QLabel(label)
            w = QLineEdit()
            if hint:
                w.setPlaceholderText(hint)
            iv.addWidget(lbl)
            iv.addWidget(w)
            return w

        self.inv_date = line_field("开票日期", "如 2025-02-13，可空")
        self.inv_amount = line_field("开票总额", "必填，数字")

        iv.addWidget(QLabel("② 开票分摊（经办人及开票金额）"))
        self.bill_table = QTableWidget(0, 2)
        self.bill_table.setHorizontalHeaderLabels(["经办人", "开票金额"])
        self.bill_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bill_table.setColumnWidth(1, 110)
        iv.addWidget(self.bill_table)
        bw = QHBoxLayout()
        self._btn_add = PushButton("+ 加行")
        self._btn_del = PushButton("- 删行")
        self._btn_split_bill = PushButton("均分开票额")
        self._btn_add.clicked.connect(self._add_row)
        self._btn_del.clicked.connect(self._del_row)
        self._btn_split_bill.clicked.connect(self._split_bill_even)
        bw.addWidget(self._btn_add)
        bw.addWidget(self._btn_del)
        bw.addWidget(self._btn_split_bill)
        bw.addStretch()
        iv.addLayout(bw)

        iv.addWidget(QLabel("③ 收款分摊（可部分人收、不同日期）"))
        self.recv_table = QTableWidget(0, 3)
        self.recv_table.setHorizontalHeaderLabels(["经办人", "收款金额", "收款日期"])
        self.recv_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.recv_table.setColumnWidth(1, 100)
        self.recv_table.setColumnWidth(2, 110)
        iv.addWidget(self.recv_table)
        rw = QHBoxLayout()
        self._btn_split_recv = PushButton("均分收款")
        self._btn_gen_recv = PushButton("按开票比例生成(全额,账期月)")
        self._btn_split_recv.clicked.connect(self._split_recv_even)
        self._btn_gen_recv.clicked.connect(self._gen_recv_from_bill)
        rw.addWidget(self._btn_split_recv)
        rw.addWidget(self._btn_gen_recv)
        rw.addStretch()
        iv.addLayout(rw)
        fv.addWidget(self.inv_box)

        # 预收款行容器
        self.pp_box = QWidget()
        pv = QVBoxLayout(self.pp_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)

        def pp_field(key: str, label: str, hint: str = "") -> None:
            lbl = QLabel(label)
            w = QLineEdit()
            if hint:
                w.setPlaceholderText(hint)
            pv.addWidget(lbl)
            pv.addWidget(w)
            setattr(self, key, w)

        pp_field("pp_date", "收到时间", "如 2025-02-13，可空")
        pp_field("pp_amount", "金额", "必填，数字")
        pp_field("pp_person", "经办人", "可空")
        fv.addWidget(self.pp_box)

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

    def _make_handler_combo(self, name: str = "") -> QComboBox:
        cb = QComboBox()
        cb.setEditable(True)
        cb.addItems(self._staff_list)
        if name:
            cb.setCurrentText(name)
        return cb

    def _render_bill(self, rows: list) -> None:
        self.bill_table.setRowCount(0)
        for r in rows:
            i = self.bill_table.rowCount()
            self.bill_table.insertRow(i)
            self.bill_table.setCellWidget(i, 0, self._make_handler_combo(r.get("name", "")))
            self.bill_table.setItem(i, 1, QTableWidgetItem(str(r.get("bill", ""))))

    def _render_recv(self, rows: list) -> None:
        self.recv_table.setRowCount(0)
        for r in rows:
            i = self.recv_table.rowCount()
            self.recv_table.insertRow(i)
            lbl = QLabel(r.get("name", ""))
            lbl.setObjectName("recvName")
            self.recv_table.setCellWidget(i, 0, lbl)
            self.recv_table.setItem(i, 1, QTableWidgetItem(str(r.get("recv_amt", ""))))
            self.recv_table.setItem(i, 2, QTableWidgetItem(str(r.get("recv_date", ""))))

    def _read_bill_rows(self) -> list:
        out = []
        for i in range(self.bill_table.rowCount()):
            cb = self.bill_table.cellWidget(i, 0)
            name = cb.currentText().strip() if cb else ""
            amt = self.bill_table.item(i, 1).text().strip() if self.bill_table.item(i, 1) else ""
            out.append({"name": name, "bill": amt})
        return out

    def _read_recv_rows(self) -> list:
        out = []
        for i in range(self.recv_table.rowCount()):
            lbl = self.recv_table.cellWidget(i, 0)
            name = lbl.text().strip() if lbl else ""
            amt = self.recv_table.item(i, 1).text().strip() if self.recv_table.item(i, 1) else ""
            dt = self.recv_table.item(i, 2).text().strip() if self.recv_table.item(i, 2) else ""
            out.append({"name": name, "recv_amt": amt, "recv_date": dt})
        return out

    def _load_form(self) -> None:
        idx = self._current_idx()
        if idx is None:
            return
        p = self._problems[idx]
        fix = self._fix.get(idx)
        if p["kind"] == "prepayment":
            self.inv_box.setVisible(False)
            self.pp_box.setVisible(True)
            self.pp_date.setText((fix or {}).get("received_date") or p.get("date_text", "") or "")
            self.pp_amount.setText((fix or {}).get("amount_text") or p.get("amount_text", "") or "")
            self.pp_person.setText((fix or {}).get("person_text") or p.get("person_text", "") or "")
            return

        self.inv_box.setVisible(True)
        self.pp_box.setVisible(False)
        # 初始经办人行（优先用已保存修正，其次尝试用问题行的经办人列预填）
        if fix:
            rows = fix["_rows"]
        else:
            rows = self._prefill_rows(p)
        self.inv_date.setText(p.get("date_text", "") or "")
        self.inv_amount.setText(p.get("total_amount", "") or "")
        self._render_bill(rows)
        self._render_recv(rows)

    def _prefill_rows(self, p: dict) -> list:
        """尝试用问题行的经办人/金额预填；失败则给一行空行"""
        total_txt = (p.get("total_amount", "") or "").strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            total = 0.0
        handler_text = p.get("handler_text", "") or ""
        try:
            hs = parse_handler_column(handler_text, total, p.get("invoice_no", ""))
            return [{"name": n, "bill": (int(a) if a == int(a) else a), "recv_amt": "", "recv_date": ""}
                    for n, a in hs]
        except ImportError_:
            return [{"name": "", "bill": (int(total) if total == int(total) else total),
                     "recv_amt": "", "recv_date": ""}]

    # ---- 分摊按钮 ----
    def _add_row(self) -> None:
        i = self.bill_table.rowCount()
        self.bill_table.insertRow(i)
        self.bill_table.setCellWidget(i, 0, self._make_handler_combo())
        self.bill_table.setItem(i, 1, QTableWidgetItem(""))
        self._sync_recv()

    def _del_row(self) -> None:
        rows = self.bill_table.selectionModel().selectedRows()
        if not rows:
            return
        for r in sorted((x.row() for x in rows), reverse=True):
            self.bill_table.removeRow(r)
        self._sync_recv()

    def _sync_recv(self) -> None:
        """收款表经办人与开票表保持一致（保留同名者的收款额/日期）"""
        bill = self._read_bill_rows()
        existing = {r["name"]: r for r in self._read_recv_rows()}
        self._render_recv([
            {"name": b["name"],
             "recv_amt": existing.get(b["name"], {}).get("recv_amt", ""),
             "recv_date": existing.get(b["name"], {}).get("recv_date", "")}
            for b in bill
        ])

    def _split_bill_even(self) -> None:
        rows = self._read_bill_rows()
        if not rows:
            return
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            QMessageBox.warning(self, "无法均分", f"开票总额无法解析: 「{total_txt}」")
            return
        share = total / len(rows)
        # 末位吸收余数
        for i, r in enumerate(rows):
            val = share if i < len(rows) - 1 else total - share * (len(rows) - 1)
            r["bill"] = f"{round(val, 2):g}"
        self._render_bill(rows)
        self._sync_recv()

    def _split_recv_even(self) -> None:
        rows = self._read_bill_rows()
        if not rows:
            return
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            QMessageBox.warning(self, "无法均分", f"开票总额无法解析: 「{total_txt}」")
            return
        share = total / len(rows)
        recv = self._read_recv_rows()
        for i, r in enumerate(recv):
            val = share if i < len(recv) - 1 else total - share * (len(recv) - 1)
            r["recv_amt"] = f"{round(val, 2):g}"
            r["recv_date"] = self._period
        self._render_recv(recv)

    def _gen_recv_from_bill(self) -> None:
        """按各经办人开票比例生成收款：收款额=开票额、日期=账期月（即全额当月收讫）"""
        bill = self._read_bill_rows()
        recv = self._read_recv_rows()
        for b, r in zip(bill, recv):
            try:
                amt = float(b["bill"].replace(",", "")) if b["bill"] else 0.0
            except ValueError:
                amt = 0.0
            r["recv_amt"] = f"{round(amt, 2):g}"
            r["recv_date"] = self._period
        self._render_recv(recv)

    # ---- 保存 ----
    def _save_fix(self) -> None:
        idx = self._current_idx()
        if idx is None:
            QMessageBox.information(self, "提示", "请先在左侧选择一行")
            return
        p = self._problems[idx]
        try:
            if p["kind"] == "invoice":
                data = self._validate_invoice(p)
            else:
                data = self._validate_prepayment(p)
        except ImportError_ as e:
            QMessageBox.warning(self, "无法保存", str(e))
            return
        self._fix[idx] = data
        self._skip.discard(idx)
        self._set_status(idx)

    def _validate_invoice(self, p: dict) -> dict:
        inv_date = _norm_dt(self.inv_date.text())
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            raise ImportError_(f"开票总额无法解析: 「{total_txt}」") from None

        bill_rows = self._read_bill_rows()
        recv_rows = self._read_recv_rows()
        if len(bill_rows) != len(recv_rows):
            raise ImportError_("开票/收款经办人不一致，请重新操作")

        handlers: list = []
        receipts: list = []
        bill_sum = 0.0
        for b, rc in zip(bill_rows, recv_rows):
            name = b["name"].strip()
            bill_txt = b["bill"].strip()
            if not name:
                raise ImportError_("经办人不能为空")
            if name not in self._staff:
                raise ImportError_(f"经办人不在职工花名册中: {name}")
            try:
                bill = float(bill_txt.replace(",", "")) if bill_txt else 0.0
            except ValueError:
                raise ImportError_(f"开票金额无法解析: 「{bill_txt}」") from None
            if bill < 0:
                raise ImportError_("开票金额不能为负")
            bill_sum += bill
            handlers.append((name, bill))
            recv_amt = rc["recv_amt"].strip()
            recv_date = rc["recv_date"].strip()
            if recv_amt or recv_date:
                if not recv_date:
                    raise ImportError_("填了收款金额/日期，必须同时填写收款日期")
                try:
                    amt = float(recv_amt.replace(",", "")) if recv_amt else 0.0
                except ValueError:
                    raise ImportError_(f"收款金额无法解析: 「{recv_amt}」") from None
                if amt <= 0:
                    raise ImportError_("收款金额须为正")
                ym = _norm_dt(recv_date)
                if not ym:
                    raise ImportError_("收款日期无法解析")
                receipts.append((name, amt, ym))

        if abs(bill_sum - total) > 0.01:
            raise ImportError_(f"经办人开票金额合计({bill_sum:g}) ≠ 开票总额({total:g})，请调整")

        handler_text = "、".join(
            f"{n}{int(a) if a == int(a) else a}" for n, a in handlers)
        return {
            "kind": "invoice",
            "invoice_no": p.get("invoice_no", ""),
            "invoice_date": inv_date or "",
            "total_amount": total,
            "total_amount_text": total_txt,
            "handlers": handlers,
            "handler_text": handler_text,
            "split_receipts": receipts,
            "buyer": p.get("buyer", ""),
            "case_no": p.get("case_no", ""),
            "_rows": [{"name": b["name"], "bill": b["bill"],
                       "recv_amt": rc["recv_amt"], "recv_date": rc["recv_date"]}
                      for b, rc in zip(bill_rows, recv_rows)],
        }

    def _validate_prepayment(self, p: dict) -> dict:
        received_date = _norm_dt(self.pp_date.text())
        amt_txt = self.pp_amount.text().strip()
        try:
            amount = float(amt_txt.replace(",", "")) if amt_txt else 0.0
        except ValueError:
            raise ImportError_(f"金额无法解析: 「{amt_txt}」") from None
        return {
            "kind": "prepayment",
            "received_date": received_date or "",
            "amount": amount,
            "amount_text": amt_txt,
            "person_text": self.pp_person.text().strip(),
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
        return getattr(self, "_resolved", [])
