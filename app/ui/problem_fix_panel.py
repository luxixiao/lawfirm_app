"""问题行修正表单（可复用组件）

从 problem_dialog.py 抽出的「逐经办人修正」表单，供统一导入对话框内嵌复用：
- 发票行：开票日期 / 开票总额 + 逐经办人子表（经办人下拉 | 开票金额 | 收款金额 | 收款日期）
  + 实时合计胶囊（开票额合计 vs 总额、收款合计）+ 添加/删除经办人
- 预收款行：收到时间 / 金额 / 经办人

对外接口：
    panel = ProblemFixPanel(staff_names, period)
    panel.set_problem(p)      # 载入一行（p=None 清空）
    data = panel.read_fix()   # 校验并返回修正数据；失败抛 ImportError_

校验规则与 problem_dialog.py 完全一致（经办人须在花名册、开票额合计须等于总额、
填了收款金额必须同时填收款日期、同名经办人合并前弹确认）。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QSizePolicy, QVBoxLayout, QWidget,
)

from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_
from app.importer.parse_handler import parse_handler_column
from app.ui import scale
from app.ui.widgets import (
    CaptionLabel, ComboBox, LineEdit, PushButton, TableWidget,
)


def _norm_dt(s: str) -> str | None:
    """规范化日期：2 段（YYYY-MM）保留月粒度，3 段（YYYY-MM-DD）保留日粒度；空返回 None"""
    s = (s or "").strip()
    if not s:
        return None
    parts = [int(x) for x in re.findall(r"\d+", s)]
    if len(parts) == 2:
        y, mo = parts
        if y < 100:
            y += 2000
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}"
        raise ImportError_(f"日期无法解析: 「{s}」")
    return normalize_date(s)


def _money(x: float) -> str:
    return f"{x:,.2f}"


class ProblemFixPanel(QWidget):
    """问题行修正表单（发票 / 预收款两种形态，按 set_problem 切换）。"""

    def __init__(self, staff_names: list, period: str, parent=None) -> None:
        super().__init__(parent)
        self._staff = set(staff_names)
        self._staff_list = sorted(staff_names)
        self._period = period
        self._problem: dict | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------- 发票行 ----------
        self.inv_box = QWidget()
        iv = QVBoxLayout(self.inv_box)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(10)

        card = QWidget()
        card.setObjectName("infoCard")
        cg = QGridLayout(card)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setSpacing(10)
        cg.setColumnStretch(0, 1)
        cg.setColumnStretch(2, 1)
        k1 = QLabel("开票日期"); k1.setObjectName("infoKey")
        self.inv_date = LineEdit(); self.inv_date.setPlaceholderText("如 2025-02-13，可空")
        k2 = QLabel("开票总额"); k2.setObjectName("infoKey")
        self.inv_amount = LineEdit(); self.inv_amount.setPlaceholderText("必填，数字")
        self.inv_amount.textChanged.connect(self._update_summary)
        sep = QFrame(); sep.setObjectName("sep"); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        cg.addWidget(k1, 0, 0); cg.addWidget(self.inv_date, 1, 0)
        cg.addWidget(sep, 0, 1, 2, 1)
        cg.addWidget(k2, 0, 2); cg.addWidget(self.inv_amount, 1, 2)
        iv.addWidget(card)

        tb = QHBoxLayout()
        tb.setSpacing(8)
        self.btn_add = PushButton("＋ 添加经办人")
        self.btn_del = PushButton("－ 删除选中")
        self.btn_add.clicked.connect(self._add_row)
        self.btn_del.clicked.connect(self._del_row)
        tb.addWidget(self.btn_add)
        tb.addWidget(self.btn_del)
        tb.addStretch()
        iv.addLayout(tb)

        self.htable = TableWidget(0, 4)
        self.htable.setHorizontalHeaderLabels(
            ["经办人", "开票金额", "收款金额", "收款日期"])
        self.htable.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.htable.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.htable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.htable.setColumnWidth(1, scale.px(130))
        self.htable.setColumnWidth(2, scale.px(130))
        self.htable.setColumnWidth(3, scale.px(140))
        # 至少露出 ~4 行，避免被右侧面板压成单行
        self.htable.setMinimumHeight(scale.px(34) * 4 + scale.px(30))
        self.htable.itemChanged.connect(self._on_item_changed)
        iv.addWidget(self.htable, 1)

        sb = QHBoxLayout()
        sb.setSpacing(10)
        self.chip_bill = QLabel(""); self.chip_bill.setObjectName("chip")
        self.chip_recv = QLabel(""); self.chip_recv.setObjectName("chip")
        sb.addWidget(self.chip_bill)
        sb.addWidget(self.chip_recv)
        sb.addStretch()
        iv.addLayout(sb)
        root.addWidget(self.inv_box)

        # ---------- 预收款行 ----------
        self.pp_box = QWidget()
        pv = QVBoxLayout(self.pp_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(10)

        def pp_field(key: str, label: str, hint: str = "") -> None:
            lbl = QLabel(label); lbl.setObjectName("infoKey")
            w = LineEdit()
            if hint:
                w.setPlaceholderText(hint)
            pv.addWidget(lbl)
            pv.addWidget(w)
            setattr(self, key, w)

        pp_field("pp_date", "收到时间", "如 2025-02-13，可空")
        pp_field("pp_amount", "金额", "必填，数字")
        pp_field("pp_person", "经办人", "可空")
        pv.addStretch()
        root.addWidget(self.pp_box)

        self.hint = CaptionLabel("")
        root.addWidget(self.hint)
        root.addStretch()

        self.set_problem(None)

    # ------------------------------------------------------------------ #
    # 载入
    # ------------------------------------------------------------------ #
    def set_problem(self, p: dict | None) -> None:
        self._problem = p
        if p is None:
            self.inv_box.setVisible(False)
            self.pp_box.setVisible(False)
            self.hint.setText("左侧选择一行后在此修正。")
            return
        if p.get("kind") == "prepayment":
            self.inv_box.setVisible(False)
            self.pp_box.setVisible(True)
            self.pp_date.setText(p.get("date_text", "") or "")
            self.pp_amount.setText(p.get("amount_text", "") or "")
            self.pp_person.setText(p.get("person_text", "") or "")
            self.hint.setText("预收款行：填写收到时间 / 金额 / 经办人后点「保存修改」。")
            return
        self.pp_box.setVisible(False)
        self.inv_box.setVisible(True)
        self.inv_date.setText(p.get("date_text", "") or "")
        self.inv_amount.setText(p.get("total_amount", "") or "")
        self._render_rows(self._prefill_rows(p))
        self.hint.setText(
            "发票行：经办人须在职工花名册中；开票金额合计须等于开票总额；"
            "填了收款金额须同时填收款日期（支持 2025-02 或 2025-02-13）。"
        )

    def current_problem(self) -> dict | None:
        return self._problem

    # ------------------------------------------------------------------ #
    # 表单渲染 / 读取
    # ------------------------------------------------------------------ #
    def _make_handler_combo(self, name: str = "") -> QComboBox:
        cb = ComboBox()
        cb.setObjectName("handlerCombo")
        cb.setEditable(False)
        items = list(self._staff_list)
        if name and name not in items:
            items = [name] + items
        cb.addItems(items)
        cb.setCurrentIndex(items.index(name) if name in items else 0)
        cb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        return cb

    def _render_rows(self, rows: list) -> None:
        self.htable.blockSignals(True)
        self.htable.setRowCount(0)
        name_cnt = Counter(r.get("name", "").strip() for r in rows if r.get("name", "").strip())
        dup = {n for n, c in name_cnt.items() if c > 1}
        for r in rows:
            i = self.htable.rowCount()
            self.htable.insertRow(i)
            self.htable.setCellWidget(i, 0, self._make_handler_combo(r.get("name", "")))
            self.htable.setItem(i, 1, self._item(str(r.get("bill", ""))))
            self.htable.setItem(i, 2, self._item(str(r.get("recv_amt", ""))))
            self.htable.setItem(i, 3, self._item(str(r.get("recv_date", ""))))
            if r.get("name", "").strip() in dup:
                for c in range(4):
                    item = self.htable.item(i, c)
                    if item is not None:
                        item.setBackground(QColor("#FFF3CD"))
        self.htable.blockSignals(False)
        self._update_summary()

    @staticmethod
    def _item(text: str):
        from PySide6.QtWidgets import QTableWidgetItem
        return QTableWidgetItem(text)

    def _read_rows(self) -> list:
        out = []
        for i in range(self.htable.rowCount()):
            cb = self.htable.cellWidget(i, 0)
            name = cb.currentText().strip() if cb else ""
            bill = self.htable.item(i, 1).text().strip() if self.htable.item(i, 1) else ""
            recv_amt = self.htable.item(i, 2).text().strip() if self.htable.item(i, 2) else ""
            recv_date = self.htable.item(i, 3).text().strip() if self.htable.item(i, 3) else ""
            out.append({"name": name, "bill": bill, "recv_amt": recv_amt, "recv_date": recv_date})
        return out

    def _on_item_changed(self, item) -> None:
        if item.column() in (1, 2, 3):
            self._update_summary()

    def _update_summary(self) -> None:
        rows = self._read_rows()
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            total = 0.0
        bill_sum = recv_sum = 0.0
        for r in rows:
            try:
                bill_sum += float(r["bill"].replace(",", "")) if r["bill"] else 0.0
            except ValueError:
                pass
            try:
                recv_sum += float(r["recv_amt"].replace(",", "")) if r["recv_amt"] else 0.0
            except ValueError:
                pass
        ok = abs(bill_sum - total) <= 0.01
        self._set_chip(
            self.chip_bill,
            f"开票额合计 <span style='font-family:Consolas,monospace'>{_money(bill_sum)}</span>"
            f" / 总额 <span style='font-family:Consolas,monospace'>{_money(total)}</span>",
            not ok,
        )
        self._set_chip(
            self.chip_recv,
            f"收款合计 <span style='font-family:Consolas,monospace'>{_money(recv_sum)}</span>",
            False,
        )

    def _set_chip(self, label: QLabel, html: str, warn: bool) -> None:
        label.setText(html)
        label.setObjectName("chipWarn" if warn else "chip")
        label.style().unpolish(label)
        label.style().polish(label)

    def _prefill_rows(self, p: dict) -> list:
        total_txt = (p.get("total_amount", "") or "").strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            total = 0.0
        handler_text = p.get("handler_text", "") or ""
        try:
            hs = parse_handler_column(handler_text, total, p.get("invoice_no", ""))
            return [{"name": n, "bill": (int(a) if a == int(a) else a),
                     "recv_amt": "", "recv_date": ""} for n, a in hs]
        except ImportError_:
            return [{"name": "", "bill": (int(total) if total == int(total) else total),
                     "recv_amt": "", "recv_date": ""}]

    # ------------------------------------------------------------------ #
    # 行操作
    # ------------------------------------------------------------------ #
    def _add_row(self) -> None:
        i = self.htable.rowCount()
        self.htable.insertRow(i)
        self.htable.setCellWidget(i, 0, self._make_handler_combo())
        self.htable.setItem(i, 1, self._item(""))
        self.htable.setItem(i, 2, self._item(""))
        self.htable.setItem(i, 3, self._item(""))
        self._update_summary()

    def _del_row(self) -> None:
        rows = self.htable.selectionModel().selectedRows()
        if not rows:
            return
        for r in sorted((x.row() for x in rows), reverse=True):
            self.htable.removeRow(r)
        self._update_summary()

    # ------------------------------------------------------------------ #
    # 校验 / 读取修正结果
    # ------------------------------------------------------------------ #
    def read_fix(self) -> dict:
        """校验并返回修正数据；失败抛 ImportError_（含用户可读信息）。"""
        p = self._problem
        if p is None:
            raise ImportError_("请先选择一行")
        if p.get("kind") == "prepayment":
            return self._validate_prepayment(p)
        return self._validate_invoice(p)

    @staticmethod
    def _detect_dup_handlers(rows: list) -> set:
        cnt = Counter(r.get("name", "").strip() for r in rows if r.get("name", "").strip())
        return {n for n, c in cnt.items() if c > 1}

    def _confirm_merge(self, rows: list, dup_names: set) -> bool:
        agg_bill = defaultdict(float)
        for r in rows:
            n = r.get("name", "").strip()
            if not n:
                continue
            try:
                b = float(r.get("bill", "").replace(",", "")) if r.get("bill") else 0.0
            except ValueError:
                b = 0.0
            agg_bill[n] += b
        per_name_recv = defaultdict(list)
        for r in rows:
            n = r.get("name", "").strip()
            if not n:
                continue
            if r.get("recv_amt", "").strip() or r.get("recv_date", "").strip():
                per_name_recv[n].append((r.get("recv_amt", "").strip(), r.get("recv_date", "").strip()))
        lines = []
        for n in sorted(dup_names):
            recv = per_name_recv.get(n, [])
            recv_desc = "、".join(f"{d}:{a}" for a, d in recv) if recv else "无收款"
            lines.append(f"• {n}：开票额合计 {_money(agg_bill[n])}；收款分 {len(recv)} 期（{recv_desc}）")
        msg = ("检测到以下经办人填写了多行，将按姓名合并开票额（开票分摊每人一行），"
               "收款按各期逐笔写入：\n\n" + "\n".join(lines) +
               "\n\n是否确认合并？点「否」可返回继续调整。")
        return QMessageBox.question(
            self, "确认合并同名经办人", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ) == QMessageBox.StandardButton.Yes

    @staticmethod
    def _aggregate_handlers(handlers: list) -> list:
        agg: dict = {}
        order: list = []
        for n, b in handlers:
            if n not in agg:
                agg[n] = 0.0
                order.append(n)
            agg[n] += b
        return [(n, agg[n]) for n in order]

    def _validate_invoice(self, p: dict) -> dict:
        inv_date = _norm_dt(self.inv_date.text())
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            raise ImportError_(f"开票总额无法解析: 「{total_txt}」") from None

        rows = self._read_rows()
        dup_names = self._detect_dup_handlers(rows)
        if dup_names and not self._confirm_merge(rows, dup_names):
            raise ImportError_("已取消合并（同名经办人未合并），返回继续编辑")

        handlers: list = []
        receipts: list = []
        bill_sum = 0.0
        for r in rows:
            name = r["name"].strip()
            if not name:
                raise ImportError_("经办人不能为空")
            if name not in self._staff:
                raise ImportError_(f"经办人不在职工花名册中: {name}")
            bill_txt = r["bill"].strip()
            try:
                bill = float(bill_txt.replace(",", "")) if bill_txt else 0.0
            except ValueError:
                raise ImportError_(f"开票金额无法解析: 「{bill_txt}」") from None
            if bill < 0:
                raise ImportError_("开票金额不能为负")
            bill_sum += bill
            handlers.append((name, bill))
            recv_amt = r["recv_amt"].strip()
            recv_date = r["recv_date"].strip()
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

        merged_handlers = self._aggregate_handlers(handlers)
        handler_text = "、".join(
            f"{n}{int(a) if a == int(a) else a}" for n, a in merged_handlers)
        return {
            "kind": "invoice",
            "invoice_no": p.get("invoice_no", ""),
            "invoice_date": inv_date or "",
            "total_amount": total,
            "total_amount_text": total_txt,
            "handlers": merged_handlers,
            "handler_text": handler_text,
            "split_receipts": receipts,
            "buyer": p.get("buyer", ""),
            "case_no": p.get("case_no", ""),
            "_rows": rows,
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
