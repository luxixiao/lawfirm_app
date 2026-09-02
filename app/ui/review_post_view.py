"""导入后复核页（阶段 2）：镜表 ↔ 业务表 融合比对单表。

替换原「导入校验」页的三维度切换（发票信息/经办人分摊/已收认定）——
融合为一张表：每行一张发票，源/库对照做进同一格（仅不符时展开为
「源 ⇄ 库」红字，一致时只显单值），状态列给融合取值（spec §4）。

比对引擎：app/engine/review_compare.py（raw_ledger 镜表为基准）。
已确认异常：anomaly_note 新写 dim='merged'，读取回退聚合旧维度（spec §6.1）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import raw_ledger as rl
from app.engine import review_compare as rc
from app.engine.review_writeback import apply_edit, restore_from_archive
from app.ui import scale
from app.ui.column_layout import install_column_layout
from app.ui.widgets import CaptionLabel, PushButton, TableWidget

RED = QColor("#C0392B")
GREEN = QColor("#1E8449")
CONFIRMED_BG = QColor("#EAF2FB")
CONFIRMED_FG = QColor("#1F6FB2")
DIFF_BG = QColor("#FDF1F0")

FILTERS = ["全部", "差异", "一致", "已确认异常"]
HEADERS = ["发票号", "来源", "购方", "金额", "经办人分摊", "已收认定", "状态", "说明"]

_DIFF_STATUS = ("不符", "仅源有", "仅库有")


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"


class ReviewPostView(QWidget):
    """导入后模式：账期由顶部下拉驱动（set_period），本页负责筛选/比对/标记。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 12, 24, 16)
        lay.setSpacing(10)

        # ---- 筛选胶囊 + 标记按钮 ----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._grp = QButtonGroup(self)
        self._grp.setExclusive(True)
        self.chips: Dict[str, PushButton] = {}
        for i, name in enumerate(FILTERS):
            b = PushButton(name)
            b.setObjectName("chipBtn")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self._grp.addButton(b, i)
            b.clicked.connect(self._render)
            bar.addWidget(b)
            self.chips[name] = b
        bar.addStretch()
        self.btn_edit = PushButton("编辑回写")
        self.btn_edit.setEnabled(False)
        self.btn_edit.clicked.connect(self._edit_row)
        bar.addWidget(self.btn_edit)
        self.btn_restore = PushButton("还原为原件")
        self.btn_restore.setEnabled(False)
        self.btn_restore.clicked.connect(self._restore_row)
        bar.addWidget(self.btn_restore)
        self.btn_confirm = PushButton("标记已确认异常")
        self.btn_confirm.clicked.connect(self._mark_confirmed)
        bar.addWidget(self.btn_confirm)
        lay.addLayout(bar)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        # ---- 表格 ----
        self.table = TableWidget(self)
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
        self.table.itemSelectionChanged.connect(self._on_sel)
        lay.addWidget(self.table, 1)
        self.table._col = install_column_layout(self.table, "import_review", "main")

        lay.addWidget(CaptionLabel(
            "双击任意行可查看该记录在原台账中的信息；「源 ⇄ 库」红字表示两侧不一致。"))

        self._rows: List[Dict] = []
        self._period = ""
        self._batch = None
        self._need_fit = True

    # ------------------------------------------------------------------ #
    # 数据载入（由父页账期下拉驱动）
    # ------------------------------------------------------------------ #
    def set_period(self, period: str) -> None:
        self._period = period or ""
        self._load()

    def _load(self) -> None:
        if not self._period:
            self._rows, self._batch = [], None
        else:
            self._batch, self._rows = rc.build_review_rows(self._period)
        self._need_fit = True
        self._render()

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        mode = self._grp.checkedId()
        show = {
            0: lambda r: True,
            1: lambda r: r["status"] in _DIFF_STATUS,
            2: lambda r: r["status"] == "一致",
            3: lambda r: bool(r["confirmed_note"]),
        }.get(mode, lambda r: True)

        rows = [r for r in self._rows if show(r)]
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for ri, row in enumerate(rows):
            confirmed = bool(row["confirmed_note"])
            duals: List[Tuple[str, bool, str, str]] = [
                self._dual(row["invoice_no"], row["invoice_no"]),
                self._dual(row["source"], row["source"]),
                self._dual(row["buyer_src"], row["buyer_db"], str),
                self._dual(row["amount_src"], row["amount_db"], _money),
                self._dual(row["handlers_src"], row["handlers_db"], str),
                self._dual(row["recv_src"], row["recv_db"], str),
            ]
            diff_like = row["status"] in _DIFF_STATUS
            if confirmed:
                note = row["confirmed_note"] or ""
                status_text = "✓ 已确认异常：" + (note if len(note) <= 40 else note[:39] + "…")
            else:
                status_text = row["status"] if row["status"] != "一致" else "✓ 一致"
            cells = [(t, mm, s, d) for t, mm, s, d in duals]
            cells.append((status_text, False, status_text, status_text))
            detail = row["detail"]
            if confirmed and detail:
                detail = f"{note}　|　{detail}"
            elif confirmed:
                detail = note
            if not row.get("synced", True):
                # 已手工修订：与 Excel 原件不一致（可「编辑回写」里继续改或还原）
                detail = f"〔已手工修订〕{detail}"
            cells.append((detail, False, detail, detail))

            for c, (text, mismatch, s_plain, d_plain) in enumerate(cells):
                item = self._make_item(text, c)
                if mismatch:
                    item.setForeground(RED)
                    item.setToolTip(f"源：{s_plain}\n库：{d_plain}")
                if c == 6:
                    if row["status"] == "一致":
                        item.setForeground(GREEN)
                    elif confirmed:
                        item.setForeground(CONFIRMED_FG)
                    else:
                        item.setForeground(RED)
                if confirmed:
                    item.setBackground(CONFIRMED_BG)
                elif diff_like:
                    item.setBackground(DIFF_BG)
                self.table.setItem(ri, c, item)
            self.table.setRowHeight(ri, scale.px(32))
            self.table.item(ri, 0).setData(Qt.ItemDataRole.UserRole, row["invoice_no"])
        if self._need_fit:
            self.table._col.apply()
            self._need_fit = False
        else:
            self.table._col.apply(remeasure=False)

        diff_n = sum(1 for r in self._rows if r["status"] in _DIFF_STATUS)
        confirmed_n = sum(1 for r in self._rows if r["confirmed_note"])
        self.lbl_stat.setText(
            f"共 {len(self._rows)} 张发票，差异 {diff_n} 张，已确认异常 {confirmed_n} 张"
            + ("" if self._batch else "　（该账期没有已导入的发票台账批次）"))

    @staticmethod
    def _dual(src, db, fmt=str) -> Tuple[str, bool, str, str]:
        """同格双值：一致 → 单值；不符 → 「源 ⇄ 库」。返回 (文本, 是否不符, 源, 库)。"""
        s = fmt(src)
        d = fmt(db)
        if s == d:
            return s, False, s, d
        return f"{s} ⇄ {d}", True, s, d

    def _make_item(self, v, c: int):
        from PySide6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem("" if v is None else str(v))
        if c == 3:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _current_row(self) -> Dict | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        item = self.table.item(r, 0)
        if item is None:
            return None
        no = item.data(Qt.ItemDataRole.UserRole)
        for row in self._rows:
            if row["invoice_no"] == no:
                return row
        return None

    def _cell_double_clicked(self, r: int, _c: int) -> None:
        row = self._current_row()
        if row is None:
            return
        if (self._batch or {}).get("archive_path"):
            from app.ui.ledger_source import show_source_for_invoice
            show_source_for_invoice(self, row["invoice_no"])
        else:
            QMessageBox.information(
                self, "无存档文件",
                f"该账期没有存档原件，无法查看 Excel 原文。\n镜表来源：{row['source']}")

    # ------------------------------------------------------------------ #
    # 已确认异常（anomaly_note，dim='merged'）
    # ------------------------------------------------------------------ #
    def _mark_confirmed(self) -> None:
        row = self._current_row()
        if row is None:
            QMessageBox.information(self, "提示", "请先在表格中选中要标记的行。")
            return
        dlg = AnomalyConfirmDialog(self, row["invoice_no"], self._period,
                                   row["detail"], row["confirmed_note"])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load()

    # ------------------------------------------------------------------ #
    # 编辑回写（阶段 3）
    # ------------------------------------------------------------------ #
    def _on_sel(self) -> None:
        row = self._current_row()
        has_raw = bool(row and row.get("raw_id"))
        self.btn_edit.setEnabled(has_raw)
        # 还原：仅「已手工修订」(synced=0) 且账期有存档文件
        self.btn_restore.setEnabled(bool(
            has_raw and not row.get("synced", True)
            and (self._batch or {}).get("archive_path")))

    def _restore_row(self) -> None:
        """单行还原为 Excel 原件（spec §5.3）。"""
        row = self._current_row()
        if row is None or not row.get("raw_id"):
            QMessageBox.information(self, "提示", "请先选中一行。")
            return
        archive = (self._batch or {}).get("archive_path")
        if not archive:
            QMessageBox.information(self, "提示", "该账期无存档文件，无法还原原件。")
            return
        ret = QMessageBox.question(
            self, "还原为原件",
            f"将把发票 {row['invoice_no']} 恢复为 {self._period} 台账 Excel 原件"
            "（覆盖当前手工修改，同步业务表并留痕）。\n\n确认还原？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            restore_from_archive(self._period, row["raw_id"], archive)
        except ValueError as e:
            QMessageBox.warning(self, "无法还原", str(e))
            return
        self._load()
        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.success("", "已还原为 Excel 原件（修改记录可查）",
                        parent=self, position=InfoBarPosition.TOP_RIGHT,
                        duration=2500)

    def _edit_row(self) -> None:
        row = self._current_row()
        if row is None or not row.get("raw_id"):
            QMessageBox.information(self, "提示", "请先选中一张源侧存在的发票行。")
            return
        raw = rl.get_row(row["raw_id"])
        if raw is None:
            QMessageBox.information(self, "提示", "镜表行已不存在，请刷新后重试。")
            return
        dlg = WritebackDialog(self, self._period, row["invoice_no"], raw, row["raw_id"])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load()


class WritebackDialog(QDialog):
    """导入后回写对话框：编辑镜表原始文本字段 + 收款明细，修改原因必填。"""

    _EDIT_FIELDS = [
        ("invoice_date_raw", "开票日期"),
        ("invoice_no", "发票号码"),
        ("buyer", "对方"),
        ("amount_raw", "金额"),
        ("case_no", "案号"),
        ("remark", "备注"),
        ("handler_text", "经办人"),
    ]

    def __init__(self, parent, period: str, invoice_no: str, raw: Dict,
                 raw_id: int) -> None:
        super().__init__(parent)
        self._period = period
        self._raw_id = raw_id
        self.setWindowTitle(f"编辑回写 — {invoice_no}")
        self.resize(620, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        root.addWidget(CaptionLabel(
            f"账期 {period}　·　修改将写入镜表并同步业务表（invoice/经办人分摊/收款），"
            "全程留痕。改后该行标记「已手工修订」。"))

        form = QFormLayout()
        form.setSpacing(8)
        self.edits: Dict[str, QLineEdit] = {}
        for key, label in self._EDIT_FIELDS:
            le = QLineEdit(str(raw.get(key) or ""))
            self.edits[key] = le
            form.addRow(label, le)
        root.addLayout(form)

        root.addWidget(CaptionLabel("收款明细（留空=按备注自动推导；填写=显式覆盖）："))
        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["收款年月(YYYY-MM)", "金额", "经办人"])
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.verticalHeader().setVisible(False)
        root.addWidget(self.tbl, 1)
        btns = QHBoxLayout()
        btn_add = QPushButton("添加收款行")
        btn_add.clicked.connect(self._add_row)
        btn_del = QPushButton("删除所选行")
        btn_del.clicked.connect(self._del_row)
        btns.addWidget(btn_add)
        btns.addWidget(btn_del)
        btns.addStretch()
        root.addLayout(btns)
        self._load_receipts(raw.get("invoice_no") or "")

        root.addWidget(CaptionLabel("修改原因（必填）："))
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("例如：经核对实际收款日期为 2025-12-10，备注修正")
        root.addWidget(self.note_edit)

        box = QDialogButtonBox()
        self.btn_ok = box.addButton("确定回写", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel = box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._on_ok)
        root.addWidget(box)

    def _load_receipts(self, invoice_no: str) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT receipt_date, amount, person_name FROM collection "
                "WHERE invoice_no=? AND source='import' ORDER BY id",
                (invoice_no,),
            ).fetchall()
        finally:
            conn.close()
        self.tbl.setRowCount(0)
        for r in rows:
            self._append_row((r["receipt_date"] or "")[:7],
                             _money(r["amount"]), r["person_name"] or "")

    def _append_row(self, ym: str, amount: str, person: str) -> None:
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        for c, v in enumerate([ym, amount, person]):
            it = QTableWidgetItem(v)
            if c == 1:
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
            self.tbl.setItem(r, c, it)

    def _add_row(self) -> None:
        self._append_row("", "", "")

    def _del_row(self) -> None:
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)

    def _receipts(self) -> list:
        """收集收款明细 → [(person_name, amount, ym)]；无行返回 []。"""
        out = []
        for r in range(self.tbl.rowCount()):
            def _txt(c):
                it = self.tbl.item(r, c)
                return (it.text() if it else "").strip()
            ym, amt, person = _txt(0), _txt(1), _txt(2)
            if not ym and not amt and not person:
                continue
            try:
                amt_f = float(amt.replace(",", "")) if amt else 0.0
            except ValueError:
                raise ValueError(f"第 {r + 1} 行收款金额无法解析：{amt!r}")
            if not ym:
                raise ValueError(f"第 {r + 1} 行请填写收款年月（YYYY-MM）")
            out.append((person, amt_f, ym[:7]))
        return out

    def _on_ok(self) -> None:
        note = self.note_edit.text().strip()
        if not note:
            QMessageBox.warning(self, "需填写修改原因", "修改原因必填，便于日后审计追溯。")
            return
        patch = {}
        for key, _ in self._EDIT_FIELDS:
            patch[key] = self.edits[key].text().strip()
        try:
            patch["receipts"] = self._receipts()
        except ValueError as e:
            QMessageBox.warning(self, "收款明细有误", str(e))
            return
        try:
            res = apply_edit(self._period, self._raw_id, patch, note)
        except ValueError as e:
            QMessageBox.warning(self, "无法回写", str(e))
            return
        super().accept()


class AnomalyConfirmDialog(QDialog):
    """标记 / 更新 / 撤销「已确认异常」备注（融合后 dim='merged'）。"""

    def __init__(self, parent, invoice_no: str, period: str,
                 detail: str, existing_note: str) -> None:
        super().__init__(parent)
        self._no = invoice_no
        self._period = period
        self.setWindowTitle(f"标记已确认异常 — {invoice_no}")
        self.resize(460, 290)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        root.addWidget(CaptionLabel(f"账期：{period}"))
        if detail:
            rlab = QLabel(f"当前差异：{detail}")
            rlab.setWordWrap(True)
            root.addWidget(rlab)
        else:
            root.addWidget(CaptionLabel("（当前无差异，仍可为该记录添加确认说明）"))

        root.addWidget(CaptionLabel("确认说明（必填，记录为什么手动修改 / 无需按源重算）："))
        self.edit = QPlainTextEdit(existing_note)
        self.edit.setPlaceholderText("例如：经办人信息已手动修正，与源台账不一致属正常")
        root.addWidget(self.edit, 1)

        box = QDialogButtonBox()
        self.btn_ok = box.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_revoke = box.addButton("撤销确认", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_cancel = box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_revoke.setEnabled(bool(existing_note))
        self.btn_revoke.clicked.connect(self._revoke)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        root.addWidget(box)

    def accept(self) -> None:
        note = self.edit.toPlainText().strip()
        if not note:
            QMessageBox.warning(self, "需填写说明", "请填写确认说明，避免日后误判。")
            return
        conn = get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO anomaly_note (invoice_no, dim, period, note) "
                "VALUES (?,?,?,?)",
                (self._no, "merged", self._period, note),
            )
            conn.commit()
        finally:
            conn.close()
        super().accept()

    def _revoke(self) -> None:
        conn = get_conn()
        try:
            conn.execute(
                "DELETE FROM anomaly_note WHERE invoice_no=? AND dim='merged' AND period=?",
                (self._no, self._period),
            )
            conn.commit()
        finally:
            conn.close()
        self.reject()
