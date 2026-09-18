"""补录发票弹窗 —— 复核页与「发票补录」页**共用**（阶段 3 B2f）。

本类从 `manual_entry_view._open_dialog()` 原样抽出，目的只有一个：
让导入复核页的行内「补录原票」按钮与「发票补录」页的「补录 / 编辑」走**同一个表单**，
避免两处字段口径（必填项 / 日期写法 / 明细表结构 / 校验文案）各自漂移。

用法：
    dlg = BackfillDialog(prefill, title="补录原始发票", parent=self,
                         validator=backfill_validator)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return                      # 取消 → 一行都不写
    data = dlg.data()               # 归一化：日期已转 YYYY-MM-DD、明细已聚合

三种模式：
- 新增（默认）：票号可改，空白或按 prefill 预填；
- 编辑（`locked_no=True`）：票号锁定（补录票以票号为主键，改号等于换票）；
- 查看（`readonly=True`）：全部只读 + 仅「关闭」按钮，用于「查看原票」。

字段级校验全部在弹窗内完成（校验不过**留在弹窗**，不关窗不丢输入）：
票号 / 开票日期必填、日期多写法归一、收款日期解析失败拦下、已收不超价税合计；
`validator` 额外挂一层写前校验（复核页用它跑 `build_backfill` → 花名册 / 票号冲突），
失败同样留在弹窗内改。两条路径与写库口径**同源**。

`soft_check`（阶段 6）= **软校验**：硬校验通过后调用 `soft_check(data)`，
返回非空字符串则弹一次「确认 / 取消」——
- **取消** → 留在弹窗继续改（与硬校验同一口径，**不丢输入**）；
- 确定 → 照常 accept。
用于「补录的蓝字原票与引用它的红字发票不一致」这类**可以放行但须留痕**的情形
（文案由 `red_mismatch_notice()` 统一生成，判定走 `import_confidence.red_orig_diff`）。
"""
from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QTableWidget, QVBoxLayout,
)

from app.ui import scale
from app.ui.date_input import DateInput, normalize_flex
from app.ui.widgets import DialogTitleLabel, CaptionLabel, PushButton


def backfill_validator(data: dict) -> str | None:
    """共用的写前校验：走 `build_backfill`（与入库同一口径）。通过返回 None。

    供「发票补录」页与导入复核页的行内补录按钮传入 `BackfillDialog(validator=...)`：
    校验不必等到写库才报错，用户留在弹窗里改。
    conn 取 `backfill_module.get_conn`（而非 app.db 直连），便于测试打桩。
    """
    from app.engine import backfill_module as bm
    conn = bm.get_conn()
    try:
        bm.build_backfill(conn, data)
        return None
    except ValueError as e:
        return str(e)
    finally:
        conn.close()


def red_mismatch_notice(diff) -> str | None:
    """`red_orig_diff` 结果 → 软提示正文；**一致（None / 空）返回 None**。

    阶段 6：复用于三个入口 —— 复核页行内补录（`soft_check`）、复核页红字行的
    「待确认」原因列、补录页补录/编辑（`soft_check`）。文案在此**单点**维护，
    避免三处各写一份而漂移。
    """
    if not diff:
        return None
    return (
        "补录的蓝字原票与引用它的红字发票不一致：\n\n"
        + str(diff.get("detail") or "")
        + "\n\n提示：红字金额为负、蓝字为正，符号相反不算不一致。\n"
        "若确属同一笔红冲业务，可继续保存"
        "（在导入复核页保存后该票会进入「待确认」，需再次确认）。\n\n"
        "是否继续保存？"
    )


class BackfillDialog(QDialog):
    """补录 / 编辑 / 查看一张原始发票。"""

    def __init__(self, prefill: dict | None = None, *,
                 title: str = "补录原始发票", locked_no: bool = False,
                 readonly: bool = False, validator=None, soft_check=None,
                 parent=None) -> None:
        super().__init__(parent)
        self._data: dict | None = None
        self._validate = validator
        self._soft_check = soft_check
        self._readonly = bool(readonly)
        prefill = prefill or {"invoice_no": "", "invoice_date": "", "buyer": "",
                              "total_amount": 0.0, "handlers": []}
        self.setWindowTitle(title)
        self.resize(620, 640)
        lay = QVBoxLayout(self)

        # ---- 抬头 4 项 ----
        form = QFormLayout()
        self.no_edit = QLineEdit(prefill.get("invoice_no") or "")
        self.no_edit.setReadOnly(locked_no)
        # 开票日期：自由文本 + 保存时归一（兼容 25.9.1 / 2025.9.01 / 2025-09-01 …）
        self.date_edit = QLineEdit(prefill.get("invoice_date") or "")
        self.date_edit.setPlaceholderText("如 25.9.1 / 2025-09-01")
        self.date_edit.setToolTip(
            "可输入：25.9.1 / 2025.9.1 / 2025.9.01 / 2025.09.01 / 25.09.1 / "
            "25.09.01 / 25.9.01 / 2025-09-01 / 2025年9月1日")
        self.buyer_edit = QLineEdit(prefill.get("buyer") or "")
        self.amt = QDoubleSpinBox()
        self.amt.setRange(-99999999, 99999999)
        self.amt.setDecimals(2)
        self.amt.setValue(float(prefill.get("total_amount") or 0))
        self.amt.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # 去上下箭头
        form.addRow("发票号码", self.no_edit)
        form.addRow("开票日期", self.date_edit)
        form.addRow("对方", self.buyer_edit)
        form.addRow("价税合计（开票总额）", self.amt)
        lay.addLayout(form)

        sub = DialogTitleLabel("经办人明细（可增删：经办人 / 开票金额 / 已收金额 / 收款日期）")
        lay.addWidget(sub)
        hint = CaptionLabel("台账已有的经办人与开票金额会自动预填；已收金额与收款日期按实际情况填写，留空表示尚未收款。")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # ---- 经办人明细表（可增删行，每行 4 个控件）----
        self.detail = QTableWidget(0, 4)
        self.detail.setHorizontalHeaderLabels(["经办人", "开票金额", "已收金额", "收款日期"])
        self.detail.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.detail.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.detail.verticalHeader().setVisible(False)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.detail)
        install_header_filter(self.detail)
        # 列宽均分填满窗体宽度
        self.detail.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # 行高固定且与内嵌输入框等高，使输入框四边框正好对齐单元格四框
        self.detail.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.detail.verticalHeader().setDefaultSectionSize(scale.px(36))
        self.detail.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 去掉单元格内边距，让输入框紧贴单元格四边
        self.detail.setStyleSheet("QTableWidget::item{padding:0px;margin:0px;}")
        lay.addWidget(self.detail, 1)

        for hd in prefill.get("handlers", []) or []:
            self._add_detail_row(hd.get("name", ""), float(hd.get("billing", 0) or 0),
                                 float(hd.get("received", 0) or 0), hd.get("date", ""))
        if self.detail.rowCount() == 0:
            self._add_detail_row()

        d_btns = QHBoxLayout()
        self.b_add = PushButton("增加一行")
        self.b_add.clicked.connect(lambda: self._add_detail_row())
        self.b_del = PushButton("删除所选行")
        self.b_del.clicked.connect(self._del_detail_row)
        d_btns.addWidget(self.b_add)
        d_btns.addWidget(self.b_del)
        d_btns.addStretch()
        lay.addLayout(d_btns)

        if self._readonly:
            for w in (self.no_edit, self.date_edit, self.buyer_edit, self.amt,
                      self.detail, self.b_add, self.b_del):
                w.setEnabled(False)
            self.b_add.hide()
            self.b_del.hide()
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            btns.rejected.connect(self.reject)
        else:
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                    | QDialogButtonBox.StandardButton.Cancel)
            btns.accepted.connect(self._on_accept)
            btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def data(self) -> dict | None:
        """通过校验后的归一化数据；未通过 / 查看模式返回 None。"""
        return self._data

    # ------------------------------------------------------------------ #
    # 经办人明细表
    # ------------------------------------------------------------------ #
    def _add_detail_row(self, name="", billing=0.0, received=0.0, date="") -> None:
        table = self.detail
        r = table.rowCount()
        table.insertRow(r)
        ne = QLineEdit(name)
        ne.setPlaceholderText("经办人")
        be = QDoubleSpinBox()
        be.setRange(0, 99999999)
        be.setDecimals(2)
        be.setValue(billing)
        be.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # 去上下箭头
        re_ = QDoubleSpinBox()
        re_.setRange(0, 99999999)
        re_.setDecimals(2)
        re_.setValue(received)
        re_.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # 去上下箭头
        # 日期：有值才放日期框；无值（或已收为 0）放空白 QLabel，
        # 绝不显示哨兵日期（旧版曾把 2000-01 当真实日期写入库）。
        has_date = bool(date) and received > 0.001
        if has_date:
            de = DateInput("month")
            de.set_text(date)
            de.setFixedHeight(scale.px(34))
            date_cell = de
        else:
            date_cell = QLabel("")
        # 已收金额 > 0 但日期为空时，自动补出日期框（默认当月）
        re_.valueChanged.connect(lambda v, _r=r: self._ensure_date_edit(_r, v))
        # 让输入框四边框对齐单元格：固定高度 + 紧贴四边
        for w in (ne, be, re_):
            w.setFixedHeight(scale.px(34))
        table.setCellWidget(r, 0, ne)
        table.setCellWidget(r, 1, be)
        table.setCellWidget(r, 2, re_)
        table.setCellWidget(r, 3, date_cell)

    def _ensure_date_edit(self, row: int, received: float) -> None:
        """已收金额变为 > 0 时，若收款日期列还是空白占位，则补出日期框（默认当月）。"""
        if received <= 0.001:
            return
        cell = self.detail.cellWidget(row, 3)
        if isinstance(cell, DateInput):
            return
        de = DateInput("month")
        de.set_text(QDate.currentDate().toString("yyyy-MM"))
        de.setFixedHeight(scale.px(34))
        self.detail.setCellWidget(row, 3, de)

    def _del_detail_row(self) -> None:
        r = self.detail.currentRow()
        if r >= 0:
            self.detail.removeRow(r)

    def read_detail(self) -> list:
        """明细表 → handlers 列表（`date_raw` 供「填了但解析不出」的拦截判定）。"""
        handlers = []
        for r in range(self.detail.rowCount()):
            ne = self.detail.cellWidget(r, 0)
            be = self.detail.cellWidget(r, 1)
            re_ = self.detail.cellWidget(r, 2)
            de = self.detail.cellWidget(r, 3)
            if not ne:
                continue
            name = ne.text().strip()
            if not name:
                continue
            handlers.append({
                "name": name,
                "billing": be.value() if be else 0.0,
                "received": re_.value() if re_ else 0.0,
                # 仅日期框且能解析出月份才视为有收款日期，空白占位 → 回空
                "date": de.text() if isinstance(de, DateInput) else "",
                "date_raw": de.raw() if isinstance(de, DateInput) else "",
            })
        return handlers

    # ------------------------------------------------------------------ #
    # 确定
    # ------------------------------------------------------------------ #
    def _on_accept(self) -> None:
        """字段级校验 → 归一化 → validator（硬）→ soft_check（软）→ 通过才 accept。

        硬校验不过 → 直接留在弹窗改；软校验（红蓝不一致）点「取消」→ 同样留在弹窗，
        不关窗不丢输入。两条路径都不写库。
        """
        no = self.no_edit.text().strip()
        if not no:
            QMessageBox.warning(self, "提示", "发票号码不能为空")
            return
        # 开票日期：必填 + 兼容多种写法，统一归一成 YYYY-MM-DD 再入库
        raw_date = self.date_edit.text().strip()
        if not raw_date:
            QMessageBox.warning(self, "提示", "开票日期为必填项")
            return
        try:
            invoice_date = normalize_flex(raw_date, "day")
        except Exception:  # noqa: BLE001 - 解析失败只是提示，不落库
            QMessageBox.warning(
                self, "提示",
                f"开票日期无法识别：{raw_date}\n"
                "可输入 25.9.1 / 2025.9.1 / 2025.09.01 / 2025-09-01 / 2025年9月1日 等写法。")
            return
        handlers = self.read_detail()
        # 收款日期填了但解析不出来 → 拦下（否则会被静默丢成"未收款"）
        for h in handlers:
            if h.get("date_raw") and not h.get("date"):
                QMessageBox.warning(
                    self, "提示",
                    f"「{h['name']}」的收款日期无法识别：{h['date_raw']}\n"
                    "可输入 25.9 / 2025.9 / 2025-09 / 25.9.1 / 2025-09-01 等写法，留空表示尚未收款。")
                return
        total = self.amt.value()
        sum_rec = sum(h["received"] for h in handlers)
        if total > 0 and sum_rec > total + 0.01:
            QMessageBox.warning(self, "提示",
                                f"已收金额合计({sum_rec:,.2f})超过价税合计({total:,.2f})")
            return
        data = {
            "invoice_no": no,
            "invoice_date": invoice_date,
            "buyer": self.buyer_edit.text().strip(),
            "total_amount": total,
            "handlers": handlers,
        }
        if self._validate is not None:
            err = self._validate(data)
            if err:
                QMessageBox.warning(self, "无法保存", str(err))
                return
        # 软校验（阶段 6）：可放行但须留痕的疑点 —— 弹「确认 / 取消」，
        # 取消则留在弹窗继续改（不丢输入），确定才保存。
        if self._soft_check is not None:
            notice = self._soft_check(data)
            if notice:
                if QMessageBox.question(
                    self, "提示", str(notice),
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                ) != QMessageBox.StandardButton.Ok:
                    return
        self._data = data
        self.accept()
