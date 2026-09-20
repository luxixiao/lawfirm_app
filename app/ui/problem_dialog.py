"""发票台账导入 - 问题行修正对话框（逐经办人单表）

解析失败的问题行统一弹一个对话框：左侧表格列出全部问题行，右侧对选中行
手动修正。发票行采用「逐经办人」单表编辑（每人一行，四列）：
    经办人（花名册下拉） | 开票金额 | 收款金额 | 收款日期
- 顶部信息卡：开票日期 / 开票总额（只读参考）
- 实时合计条：开票额合计 vs 总额、收款合计；不对齐自动转红胶囊
- 工具栏：＋添加经办人 / －删除选中 ｜ 均分开票额 / 均分收款 / 按开票比例生成收款
预收款行保持原有简单字段。每行可「保存修改」或「跳过」，确认后由调用方合并入库。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from PySide6.QtCore import Qt, QSettings, QTimer, QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.importer.date_utils import normalize_date
from app.importer.excel_reader import ImportError_
from app.importer.parse_handler import parse_handler_column
from app.ui import scale, style
from app.diag import get_logger
from app.ui.widgets import (
    CaptionLabel, ComboBox, LineEdit, PrimaryPushButton, PushButton,
    SubtitleLabel, TableWidget,
)

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


def _money(x: float) -> str:
    return f"{x:,.2f}"


class ProblemDialog(QDialog):
    def __init__(self, problems: list, staff_names: list, period: str, parent=None) -> None:
        super().__init__(parent)
        get_logger().warning("INSTANTIATE ProblemDialog (deprecated standalone import dialog)")
        self.setWindowTitle("发票台账导入 - 问题行修正")
        self.resize(1180, 620)
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
            "经办人须为职工花名册中的姓名；收款日期支持 2025-02 / 25.2.13 / 2025.2.13 / "
            "2025-02-13 等写法。"
            "开票金额合计须等于开票总额；填了收款金额必须同时填收款日期。"
        )
        root.addWidget(h)

        # ---- 中部：列表 + 表单（可拖拽宽度，状态记忆） ----
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        self.table = TableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "类型", "行号", "发票号", "购方", "金额", "原因", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setMinimumWidth(280)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate([36, 52, 48, 150, 84, 92, 200, 64]):
            self.table.setColumnWidth(i, scale.px(w))
        self._fill_table()
        self.table.itemSelectionChanged.connect(self._load_form)
        self.splitter.addWidget(self.table)
        self._init_cell_tooltip()

        # ---- 右侧表单 ----
        form_box = QWidget()
        form_box.setObjectName("problemForm")
        form_box.setMinimumWidth(420)
        fv = QVBoxLayout(form_box)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(10)

        # 发票行容器
        self.inv_box = QWidget()
        iv = QVBoxLayout(self.inv_box)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(10)

        # 信息卡：开票日期 / 开票总额
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
        cg.addWidget(sep, 0, 1, 2, 1)  # 跨两行居中分隔
        cg.addWidget(k2, 0, 2); cg.addWidget(self.inv_amount, 1, 2)
        iv.addWidget(card)

        # 工具栏：行操作（手工填写，去掉自动分摊按钮）
        tb = QHBoxLayout()
        tb.setSpacing(8)
        self._btn_add = PushButton("＋ 添加经办人")
        self._btn_del = PushButton("－ 删除选中")
        self._btn_add.clicked.connect(self._add_row)
        self._btn_del.clicked.connect(self._del_row)
        tb.addWidget(self._btn_add)
        tb.addWidget(self._btn_del)
        tb.addStretch()

        # 逐经办人单表：经办人 | 开票金额 | 收款金额 | 收款日期
        self.htable = TableWidget(0, 4)
        self.htable.setHorizontalHeaderLabels(
            ["经办人", "开票金额", "收款金额", "收款日期"])
        self.htable.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.htable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.htable.setColumnWidth(1, scale.px(120))
        self.htable.setColumnWidth(2, scale.px(120))
        self.htable.setColumnWidth(3, scale.px(130))
        self.htable.itemChanged.connect(self._on_item_changed)
        iv.addWidget(self.htable)

        # 实时合计条
        sb = QHBoxLayout()
        sb.setSpacing(10)
        self._chip_bill = QLabel(""); self._chip_bill.setObjectName("chip")
        self._chip_recv = QLabel(""); self._chip_recv.setObjectName("chip")
        sb.addWidget(self._chip_bill)
        sb.addWidget(self._chip_recv)
        sb.addStretch()
        iv.addLayout(sb)

        fv.addWidget(self.inv_box)

        # 预收款行容器
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
        fv.addWidget(self.pp_box)

        self._btn_save = PushButton("保存修改")
        self._btn_save.clicked.connect(self._save_fix)
        self._btn_skip = PushButton("跳过此行")
        self._btn_skip.clicked.connect(self._skip_row)

        # 统一底部按钮行：添加/删除（仅发票） | 保存/跳过
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_del)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_save)
        btn_row.addWidget(self._btn_skip)
        fv.addLayout(btn_row)
        fv.addStretch()
        self.splitter.addWidget(form_box)
        self.splitter.setHandleWidth(8)
        # 恢复上次布局（首次打开用默认 430/750）
        settings = QSettings("lawfirm_app", "problem_dialog")
        geo = settings.value("geometry")
        if isinstance(geo, (bytes, bytearray)):
            self.restoreGeometry(geo)
        state = settings.value("splitter_state")
        if isinstance(state, (bytes, bytearray)):
            self.splitter.restoreState(state)
        else:
            self.splitter.setSizes([430, 750])
        root.addWidget(self.splitter, 1)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        bottom.addStretch()
        self._btn_ok = PrimaryPushButton("确认导入（未处理行将跳过）")
        self._btn_ok.clicked.connect(self._confirm)
        bottom.addWidget(self._btn_ok)
        root.addLayout(bottom)

        if self.table.rowCount():
            self.table.selectRow(0)

    # ---- 列表 ----
    _TIP_ROLE = Qt.ItemDataRole.UserRole + 1  # 单元格悬停全文

    def _fill_table(self) -> None:
        tooltip_cols = {3, 4, 6}  # 发票号、购方、原因：悬停显示全文
        for i, p in enumerate(self._problems):
            vals = [
                str(i + 1), _TYPES.get(p["kind"], p["kind"]), str(p.get("row_no", "")),
                p.get("invoice_no", "") or "", p.get("buyer", "") or "",
                p.get("total_amount", "") or p.get("amount_text", "") or "",
                p.get("reason", ""), "待处理",
            ]
            self.table.insertRow(i)
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in tooltip_cols and isinstance(v, str) and v:
                    item.setData(self._TIP_ROLE, v)
                self.table.setItem(i, c, item)
        self._set_status_all()

    # ---- 自定义单元格 tooltip（Notion 浅灰卡片，替代原生黑底，避免闪烁） ----
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

    def _on_cell_entered(self, row: int, col: int) -> None:
        self._tip_timer.stop()
        item = self.table.item(row, col)
        if item and item.data(self._TIP_ROLE):
            self._tip_row = row
            self._tip_col = col
            self._tip_timer.start(120)   # 悬停 120ms 即弹出（比原生默认更快）
        else:
            self._hide_tip()

    def _pop_tip(self) -> None:
        row = getattr(self, "_tip_row", -1)
        col = getattr(self, "_tip_col", -1)
        item = self.table.item(row, col)
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
        cb = ComboBox()
        cb.setObjectName("handlerCombo")
        cb.setEditable(False)
        items = list(self._staff_list)
        if name and name not in items:   # 预填名不在花名册也先显示，保存时再校验
            items = [name] + items
        cb.addItems(items)
        cb.setCurrentIndex(items.index(name) if name in items else 0)
        # 关键：让下拉框宽高都跟随单元格，去掉边框后内嵌进表格
        cb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        return cb

    def _render_rows(self, rows: list) -> None:
        self.htable.blockSignals(True)
        self.htable.setRowCount(0)
        # A 方案：标记同名经办人行（浅黄底色），提示"保存时将被合并"
        name_cnt = Counter(r.get("name", "").strip() for r in rows if r.get("name", "").strip())
        dup = {n for n, c in name_cnt.items() if c > 1}
        for r in rows:
            i = self.htable.rowCount()
            self.htable.insertRow(i)
            self.htable.setCellWidget(i, 0, self._make_handler_combo(r.get("name", "")))
            self.htable.setItem(i, 1, QTableWidgetItem(str(r.get("bill", ""))))
            self.htable.setItem(i, 2, QTableWidgetItem(str(r.get("recv_amt", ""))))
            self.htable.setItem(i, 3, QTableWidgetItem(str(r.get("recv_date", ""))))
            if r.get("name", "").strip() in dup:
                for c in range(4):
                    item = self.htable.item(i, c)
                    if item is not None:
                        item.setBackground(style.qcolor("amber_bg"))
        self.htable.blockSignals(False)
        self._update_summary()

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

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() in (1, 2, 3):
            self._update_summary()

    def _update_summary(self) -> None:
        rows = self._read_rows()
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            total = 0.0
        bill_sum = 0.0
        recv_sum = 0.0
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
            self._chip_bill,
            f"开票额合计 <span style='font-family:Consolas,monospace'>{_money(bill_sum)}</span>"
            f" / 总额 <span style='font-family:Consolas,monospace'>{_money(total)}</span>",
            not ok,
        )
        self._set_chip(
            self._chip_recv,
            f"收款合计 <span style='font-family:Consolas,monospace'>{_money(recv_sum)}</span>",
            False,
        )

    def _set_chip(self, label: QLabel, html: str, warn: bool) -> None:
        label.setText(html)
        label.setObjectName("chipWarn" if warn else "chip")
        label.style().unpolish(label)
        label.style().polish(label)

    def _load_form(self) -> None:
        idx = self._current_idx()
        if idx is None:
            return
        p = self._problems[idx]
        fix = self._fix.get(idx)
        if p["kind"] == "prepayment":
            self.inv_box.setVisible(False)
            self.pp_box.setVisible(True)
            self._btn_add.setVisible(False)
            self._btn_del.setVisible(False)
            self.pp_date.setText((fix or {}).get("received_date") or p.get("date_text", "") or "")
            self.pp_amount.setText((fix or {}).get("amount_text") or p.get("amount_text", "") or "")
            self.pp_person.setText((fix or {}).get("person_text") or p.get("person_text", "") or "")
            return

        self.inv_box.setVisible(True)
        self.pp_box.setVisible(False)
        self._btn_add.setVisible(True)
        self._btn_del.setVisible(True)
        rows = fix["_rows"] if fix else self._prefill_rows(p)
        self.inv_date.setText(p.get("date_text", "") or "")
        self.inv_amount.setText(p.get("total_amount", "") or "")
        self._render_rows(rows)

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
            return [{"name": n, "bill": (int(a) if a == int(a) else a),
                     "recv_amt": "", "recv_date": ""} for n, a in hs]
        except ImportError_:
            return [{"name": "", "bill": (int(total) if total == int(total) else total),
                     "recv_amt": "", "recv_date": ""}]

    # ---- 工具栏 ----
    def _add_row(self) -> None:
        i = self.htable.rowCount()
        self.htable.insertRow(i)
        self.htable.setCellWidget(i, 0, self._make_handler_combo())
        self.htable.setItem(i, 1, QTableWidgetItem(""))
        self.htable.setItem(i, 2, QTableWidgetItem(""))
        self.htable.setItem(i, 3, QTableWidgetItem(""))
        self._update_summary()

    def _del_row(self) -> None:
        rows = self.htable.selectionModel().selectedRows()
        if not rows:
            return
        for r in sorted((x.row() for x in rows), reverse=True):
            self.htable.removeRow(r)
        self._update_summary()

    def _split_bill_even(self) -> None:
        rows = self._read_rows()
        if not rows:
            return
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            QMessageBox.warning(self, "无法均分", f"开票总额无法解析: 「{total_txt}」")
            return
        share = total / len(rows)
        for i, r in enumerate(rows):
            val = share if i < len(rows) - 1 else total - share * (len(rows) - 1)
            r["bill"] = f"{round(val, 2):g}"
        self._render_rows(rows)

    def _split_recv_even(self) -> None:
        rows = self._read_rows()
        if not rows:
            return
        total_txt = self.inv_amount.text().strip()
        try:
            total = float(total_txt.replace(",", "")) if total_txt else 0.0
        except ValueError:
            QMessageBox.warning(self, "无法均分", f"开票总额无法解析: 「{total_txt}」")
            return
        share = total / len(rows)
        for i, r in enumerate(rows):
            val = share if i < len(rows) - 1 else total - share * (len(rows) - 1)
            r["recv_amt"] = f"{round(val, 2):g}"
            r["recv_date"] = self._period
        self._render_rows(rows)

    def _gen_recv_from_bill(self) -> None:
        """按各经办人开票比例生成收款：收款额=开票额、日期=账期月（即全额当月收讫）"""
        rows = self._read_rows()
        for r in rows:
            try:
                amt = float(r["bill"].replace(",", "")) if r["bill"] else 0.0
            except ValueError:
                amt = 0.0
            r["recv_amt"] = f"{round(amt, 2):g}"
            r["recv_date"] = self._period
        self._render_rows(rows)

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

    # ---- A+D：同名经办人多行 -> 合并 ----
    def _detect_dup_handlers(self, rows: list) -> set:
        """返回出现 >1 次的经办人姓名集合（空表示无重名）。"""
        cnt = Counter(r.get("name", "").strip() for r in rows if r.get("name", "").strip())
        return {n for n, c in cnt.items() if c > 1}

    def _confirm_merge(self, rows: list, dup_names: set) -> bool:
        """D 方案：弹确认框，展示同名经办人将如何合并（开票额求和、收款分多期）。"""
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
        """按姓名聚合开票额，同名多行求和，保持首次出现顺序。"""
        agg = {}
        order = []
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
        # ---- A+D：同名经办人多行 -> 弹确认后按姓名聚合开票额 ----
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

        # ---- A：按姓名聚合开票额（同名多行合并为一行，避免 charge_detail 唯一约束冲突） ----
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

    def closeEvent(self, event) -> None:
        """关闭时记忆窗口几何与 splitter 状态"""
        settings = QSettings("lawfirm_app", "problem_dialog")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("splitter_state", self.splitter.saveState())
        super().closeEvent(event)
