"""个人结算总表：按经办人+月份筛选预览，导出全部/指定人员（多选）"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from qfluentwidgets import (CaptionLabel, PrimaryPushButton, PushButton, SubtitleLabel)

from app.engine.person_settlement import build_settlement
from app.exporter.person_settlement_exporter import export_all, export_one

MONTH_LABELS = ["1月", "2月", "3月", "4月", "5月", "6月",
                "7月", "8月", "9月", "10月", "11月", "12月"]


class SettlementView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("个人结算总表")
        lay.addWidget(t)
        h = CaptionLabel("选择经办人查看结算总表；可导出全部或指定经办人（支持多选）。口径：收款/开票/未收/业务收入/费用。")
        h.setStyleSheet("color:#8A8886;")
        lay.addWidget(h)

        # ---- 筛选栏（经办人 + 月份 + 年份）----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("年份"))
        self.year = QComboBox()
        cur = datetime.now().year
        for y in range(cur, cur - 3, -1):
            self.year.addItem(f"{y}年", userData=y)
        self.year.currentIndexChanged.connect(lambda *_: self._reload_persons())
        # 默认选中最近有数据的年份（避免默认年无数据导致经办人为空）
        for i in range(self.year.count()):
            if self.year.itemData(i) == self._latest_data_year():
                self.year.setCurrentIndex(i)
                break
        bar.addWidget(self.year)

        bar.addWidget(CaptionLabel("经办人"))
        self.person = QComboBox()
        self.person.setMinimumWidth(150)
        self.person.currentIndexChanged.connect(lambda *_: self.refresh())
        bar.addWidget(self.person)

        bar.addWidget(CaptionLabel("月份"))
        self.month = QComboBox()
        self.month.addItem("全部月份", userData=0)
        for mo in range(1, 13):
            self.month.addItem(f"{mo}月", userData=mo)
        self.month.currentIndexChanged.connect(lambda *_: self.refresh())
        bar.addWidget(self.month)
        bar.addStretch()
        lay.addLayout(bar)
        # 下拉列表显式样式：白底黑字 + 选中高亮（避免 qfluentwidgets 主题影响不可见）
        COMBO_QSS = """
        QComboBox { background: #FFFFFF; border: 1px solid #DADAD7; border-radius: 6px;
                    padding: 5px 10px; min-height: 18px; }
        QComboBox::drop-down { border: none; width: 22px; }
        QComboBox QAbstractItemView {
            background: #FFFFFF; color: #37352F;
            selection-background-color: #E9E9E7; selection-color: #37352F;
            border: 1px solid #DADAD7; outline: none;
        }
        QComboBox QAbstractItemView::item { padding: 6px 10px; min-height: 22px; }
        """
        for c in (self.year, self.person, self.month):
            c.setStyleSheet(COMBO_QSS)
            v = c.view()
            if v is not None:
                v.setMinimumWidth(180)

        # ---- 结算总表表格（项目 × 月）----
        self.table = QTableWidget(0, 14)
        self.table.setHorizontalHeaderLabels(["项目"] + MONTH_LABELS + ["合计"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        # ---- 底部：导出 + 摘要 ----
        bottom = QHBoxLayout()
        self.btn_all = PrimaryPushButton("导出全部员工")
        self.btn_all.clicked.connect(self.gen_all)
        self.btn_selected = PushButton("导出指定人员…")
        self.btn_selected.clicked.connect(self.gen_selected)
        self.lbl_summary = CaptionLabel("")
        self.lbl_summary.setStyleSheet("color:#8A8886;")
        bottom.addWidget(self.btn_all)
        bottom.addWidget(self.btn_selected)
        bottom.addStretch()
        bottom.addWidget(self.lbl_summary)
        lay.addLayout(bottom)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setPlaceholderText("生成日志…")
        lay.addWidget(self.log)

        self._reload_persons()

    # ---- 人员列表 ----
    @staticmethod
    def _latest_data_year() -> int:
        """最近一个有数据的年份（从今年往前找）"""
        cur = datetime.now().year
        for y in range(cur, cur - 4, -1):
            if build_settlement(y):
                return y
        return cur

    def _reload_persons(self) -> None:
        year = self.year.currentData() or datetime.now().year
        data = build_settlement(year)
        self._person_names = sorted(data.keys())
        self.person.clear()
        self.person.addItem("请选择经办人", userData=None)
        for name in self._person_names:
            self.person.addItem(name, userData=name)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reload_persons()
        self.refresh()

    # ---- 预览 ----
    def refresh(self) -> None:
        year = self.year.currentData() or datetime.now().year
        name = self.person.currentData()
        mo = self.month.currentData()
        # 按月份模式调整表格列
        if mo:
            self.table.setColumnCount(3)
            self.table.setHorizontalHeaderLabels(["项目", f"{mo}月", "合计"])
        else:
            self.table.setColumnCount(14)
            self.table.setHorizontalHeaderLabels(["项目"] + MONTH_LABELS + ["合计"])
        if not name:
            self.table.setRowCount(0)
            self.lbl_summary.setText("请选择经办人")
            return
        data = build_settlement(year, person=name)
        st = data.get(name)
        if st is None:
            self.table.setRowCount(0)
            self.lbl_summary.setText(f"{name} 在 {year} 年无数据")
            return
        rows = self._build_rows(st, year)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                item = QTableWidgetItem("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if isinstance(v, float) and c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
        self.lbl_summary.setText(
            f"{name}（{st['staff_type']}）{'·' + str(mo) + '月' if mo else '·全年'}")

    def _build_rows(self, st: dict, year: int) -> list:
        """结算总表行（项目 × 1-12月 + 合计）；月份筛选时只保留该月列"""
        m = st["months"]
        mo = self.month.currentData()
        if mo:
            idxs = [mo - 1]
            header_extra = 1  # 项目 + 1个月
        else:
            idxs = list(range(12))
            header_extra = 13
        rows = []
        def row(label: str, vals12: list, is_sub=False):
            vals = [vals12[i] for i in idxs]
            # 合计列 = 全年累计（单月模式也显示全年累计便于对照）
            total = round(sum(vals12), 2)
            prefix = "　" if is_sub else ""
            return [prefix + label] + vals + [total]
        rows.append([f"▶ {st['staff_type']}"] + [None] * header_extra)
        rows.append(["一、上年结余结转"] + [None] * header_extra)
        # 二
        rec_keys = ["rec_open_cur", "rec_cur_year", "rec_prev_year", "rec_refund_cur", "rec_refund_prev"]
        rows.append(row("二、本月收款金额", [round(sum(m[mo_][k] for k in rec_keys), 2) for mo_ in range(1, 13)], True))
        for key, label in [("rec_open_cur", "1.本月开收"), ("rec_cur_year", "2.收本年"),
                           ("rec_prev_year", "3.收上年"), ("rec_refund_cur", "4.退本年"),
                           ("rec_refund_prev", "5.退上年")]:
            rows.append(row(label, [round(m[mo_][key], 2) for mo_ in range(1, 13)], True))
        # 三
        inv_keys = ["inv_open_received", "inv_open_uncollected", "inv_red_cur", "inv_red_prev"]
        rows.append(row("三、本月开具发票金额", [round(sum(m[mo_][k] for k in inv_keys), 2) for mo_ in range(1, 13)], True))
        for key, label in [("inv_open_received", "1.本月开收"), ("inv_open_uncollected", "2.本月未收"),
                           ("inv_red_cur", "3.红冲本年"), ("inv_red_prev", "4.红冲上年")]:
            rows.append(row(label, [round(m[mo_][key], 2) for mo_ in range(1, 13)], True))
        # 四
        rows.append(row("四、未收款金额", [round(st["uncollected_month"][mo_], 2) for mo_ in range(1, 13)]))
        # 五
        rows.append(row("五、业务收入", [round(m[mo_]["income"], 2) for mo_ in range(1, 13)]))
        # 六
        exp = st["expenses"]
        rows.append(row("六、减：分成报酬及费用",
                        [round(sum(exp.get(t, {}).get(mo_, 0.0) for t in exp), 2) for mo_ in range(1, 13)], True))
        for i, etype in enumerate(sorted(exp.keys()), 1):
            rows.append(row(f"{i}.{etype}", [round(exp[etype].get(mo_, 0.0), 2) for mo_ in range(1, 13)], True))
        # 修正合计列：未收款金额的合计=本年累计未收（非各月之和）
        for rr in rows:
            if rr and rr[0] == "四、未收款金额":
                rr[-1] = round(st["uncollected_total"], 2)
                break
        return rows

    # ---- 导出 ----
    def _choose_dir(self) -> str:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        return d or ""

    def gen_all(self) -> None:
        out = self._choose_dir()
        if not out:
            return
        year = self.year.currentData() or datetime.now().year
        self.log.clear()
        self.log.appendPlainText(f"正在生成 {year} 年个人结算总表…")
        try:
            files = export_all(out, year)
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        for f in files:
            self.log.appendPlainText(f"✓ {f.name}")
        self.log.appendPlainText(f"\n完成：共 {len(files)} 份，输出目录：{out}")
        QMessageBox.information(self, "生成完成", f"已生成 {len(files)} 份个人结算总表\n输出目录：{out}")

    def gen_selected(self) -> None:
        """弹窗多选经办人（支持全选），导出选中人员"""
        names = self._person_names
        if not names:
            QMessageBox.information(self, "提示", "当前年份没有可导出的经办人")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("选择要导出的经办人")
        dlg.resize(360, 480)
        lay = QVBoxLayout(dlg)
        tip = CaptionLabel(f"共 {len(names)} 人，勾选要导出的（可多选）：")
        tip.setStyleSheet("color:#8A8886;")
        lay.addWidget(tip)
        lst = QListWidget()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            lst.addItem(it)
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        check_all = QCheckBox("全选")
        btns.addWidget(check_all)
        btns.addStretch()
        ok_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn.accepted.connect(dlg.accept)
        ok_btn.rejected.connect(dlg.reject)
        btns.addWidget(ok_btn)
        lay.addLayout(btns)

        def toggle_all(state: int) -> None:
            for i in range(lst.count()):
                lst.item(i).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        check_all.stateChanged.connect(toggle_all)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [lst.item(i).text() for i in range(lst.count())
                    if lst.item(i).checkState() == Qt.CheckState.Checked]
        if not selected:
            QMessageBox.information(self, "提示", "未选择任何经办人")
            return
        out = self._choose_dir()
        if not out:
            return
        year = self.year.currentData() or datetime.now().year
        self.log.clear()
        self.log.appendPlainText(f"正在为 {len(selected)} 人生成 {year} 年结算总表…")
        try:
            data = build_settlement(year)
            from pathlib import Path
            files = []
            for n in selected:
                st = data.get(n)
                if st is None:
                    continue
                safe = n.replace("/", "_").replace("\\", "_").strip() or "未命名"
                files.append(export_one(st, n, Path(out) / f"个人结算总表_{safe}.xlsx", year))
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        for f in files:
            self.log.appendPlainText(f"✓ {f.name}")
        self.log.appendPlainText(f"\n完成：共 {len(files)} 份，输出目录：{out}")
        QMessageBox.information(self, "生成完成", f"已生成 {len(files)} 份\n输出目录：{out}")
