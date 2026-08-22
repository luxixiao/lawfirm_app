"""个人结算总表：按经办人+月份筛选预览，导出全部/指定人员（多选）"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from qfluentwidgets import (CaptionLabel, PrimaryPushButton, PushButton, SubtitleLabel)

from app.engine.person_settlement import build_settlement
from app.exporter.person_settlement_exporter import export_all, export_one

MONTH_LABELS = ["1月", "2月", "3月", "4月", "5月", "6月",
                "7月", "8月", "9月", "10月", "11月", "12月"]


class SettlementView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        self.tab_personal = QWidget()
        self._lay_p = QVBoxLayout(self.tab_personal)
        self._lay_p.setContentsMargins(8, 10, 8, 10)
        self._lay_p.setSpacing(12)
        lay = self._lay_p

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

        bar.addWidget(CaptionLabel("类型"))
        self.person_type = QComboBox()
        for t, v in [("汇总", None), ("合伙", "合伙"), ("聘用", "聘用"), ("兼职", "兼职")]:
            self.person_type.addItem(t, userData=v)
        self.person_type.currentIndexChanged.connect(lambda *_: self.refresh())
        bar.addWidget(self.person_type)
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
        for c in (self.year, self.person, self.month, self.person_type):
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
        self.btn_report = PushButton("导出月度结算表…")
        self.btn_report.clicked.connect(self.gen_report)
        self.lbl_summary = CaptionLabel("")
        self.lbl_summary.setStyleSheet("color:#8A8886;")
        bottom.addWidget(self.btn_all)
        bottom.addWidget(self.btn_selected)
        bottom.addWidget(self.btn_report)
        bottom.addStretch()
        bottom.addWidget(self.lbl_summary)
        lay.addLayout(bottom)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setPlaceholderText("生成日志…")
        lay.addWidget(self.log)

        # 默认选中最近有数据的年份（避免默认年无数据导致经办人为空）
        self.year.blockSignals(True)
        for i in range(self.year.count()):
            if self.year.itemData(i) == self._latest_data_year():
                self.year.setCurrentIndex(i)
                break
        self.year.blockSignals(False)
        self._reload_persons()

        # ---- 外层 Tab：个人结算总表 / 月度结算表 ----
        self.tabs.addTab(self.tab_personal, "个人结算总表")
        self.tabs.addTab(self._build_report_tab(), "月度结算表")
        self.tabs.addTab(self._build_staff_income_tab(), "年度聘用结算表")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1:
            self.refresh_report()
        elif idx == 2:
            self.refresh_staff_income()

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
        if hasattr(self, "r_person"):
            self.r_person.clear()
            self.r_person.addItem("请选择经办人", userData=None)
        for name in self._person_names:
            self.person.addItem(name, userData=name)
            if hasattr(self, "r_person"):
                self.r_person.addItem(name, userData=name)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reload_persons()
        self.refresh()

    # ---- 预览 ----
    @staticmethod
    def _has_any(st) -> bool:
        """该人员结构全年有任一数据"""
        if st is None:
            return False
        for mo in range(1, 13):
            m = st["months"][mo]
            for k in ("rec_open_cur", "rec_cur_year", "rec_prev_year", "rec_refund_cur",
                      "rec_refund_prev", "inv_open_received", "inv_open_uncollected",
                      "inv_red_cur", "inv_red_prev", "income"):
                if abs(m[k]) > 0.01:
                    return True
        if sum(st["expenses"].get(t, {}).get(mo, 0.0) for t in st["expenses"] for mo in range(1, 13)) > 0.01:
            return True
        return False

    def _sync_type_options(self, name: str) -> None:
        """类型下拉只显示该人实际拥有的身份；单身份自动选中"""
        year = self.year.currentData() or datetime.now().year
        available = []
        for pt in ("合伙", "聘用", "兼职"):
            st = build_settlement(year, person=name, person_type=pt).get(name)
            if self._has_any(st):
                available.append(pt)
        self.person_type.blockSignals(True)
        self.person_type.clear()
        self.person_type.addItem("汇总", userData=None)
        for pt in available:
            self.person_type.addItem(pt, userData=pt)
        # 单身份自动选中该身份；多身份默认汇总
        self.person_type.setCurrentIndex(1 if len(available) == 1 else 0)
        self.person_type.blockSignals(False)

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
        self._sync_type_options(name)
        ptype = self.person_type.currentData()
        data = build_settlement(year, person=name, person_type=ptype)
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
        type_txt = self.person_type.currentText()
        self.lbl_summary.setText(
            f"{name}（{type_txt}）{'·' + str(mo) + '月' if mo else '·全年'}")

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
        # 三（小计 = 本月开票总额，独立计算，含预收票）
        rows.append(row("三、本月开具发票金额", [round(m[mo_]["inv_total"], 2) for mo_ in range(1, 13)], True))
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
            from pathlib import Path
            files = []
            for n in selected:
                safe = n.replace("/", "_").replace("\\", "_").strip() or "未命名"
                files.append(export_one(n, Path(out) / f"个人结算总表_{safe}.xlsx", year))
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        for f in files:
            self.log.appendPlainText(f"✓ {f.name}")
        self.log.appendPlainText(f"\n完成：共 {len(files)} 份，输出目录：{out}")
        QMessageBox.information(self, "生成完成", f"已生成 {len(files)} 份\n输出目录：{out}")

    # ---- 导出月度结算表（多 sheet，仿模板）----
    @staticmethod
    def _prefs() -> dict:
        import json
        from pathlib import Path
        p = Path("data") / "prefs.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _save_prefs(prefs: dict) -> None:
        import json
        from pathlib import Path
        Path("data").mkdir(parents=True, exist_ok=True)
        (Path("data") / "prefs.json").write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")

    def gen_report(self) -> None:
        """选择月份 + 多选员工（默认上次选择），导出月度结算表"""
        from pathlib import Path
        from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem
        year = self.year.currentData() or datetime.now().year
        names = self._person_names
        prefs = self._prefs()
        last = prefs.get("report_persons", [])
        last = [n for n in last if n in names]

        dlg = QDialog(self)
        dlg.setWindowTitle("导出月度结算表")
        dlg.resize(400, 520)
        lay = QVBoxLayout(dlg)
        # 月份
        mbar = QHBoxLayout()
        mbar.addWidget(CaptionLabel("月份"))
        month_combo = QComboBox()
        for mo in range(1, 13):
            month_combo.addItem(f"{mo}月", userData=mo)
        mbar.addWidget(month_combo)
        mbar.addStretch()
        lay.addLayout(mbar)
        # 人员多选
        tip = CaptionLabel(f"勾选要导出的员工（共 {len(names)} 人，默认上次选择）")
        tip.setStyleSheet("color:#8A8886;")
        lay.addWidget(tip)
        lst = QListWidget()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if (last and n in last) else Qt.CheckState.Unchecked)
            lst.addItem(it)
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        check_all = QCheckBox("全选")
        btns.addWidget(check_all)
        btns.addStretch()
        okb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        okb.accepted.connect(dlg.accept); okb.rejected.connect(dlg.reject)
        btns.addWidget(okb)
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
            QMessageBox.information(self, "提示", "未选择任何员工")
            return
        month = month_combo.currentData()
        # 记忆上次选择
        prefs["report_persons"] = selected
        self._save_prefs(prefs)

        out = self._choose_dir()
        if not out:
            return
        from app.exporter.settlement_report_exporter import export_report
        self.log.clear()
        self.log.appendPlainText(f"正在生成 {year}年{month}月 结算表（{len(selected)} 人）…")
        try:
            f = export_report(Path(out) / f"{year}年{month}月结算表.xlsx", year, month, selected)
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        self.log.appendPlainText(f"✓ {f}")
        QMessageBox.information(self, "生成完成", f"已生成：{f}")

    # ---- 月度结算表 Tab（预览 + 导出）----
    def _build_report_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        v.setContentsMargins(8, 10, 8, 10)
        v.setSpacing(10)
        w.setLayout(v)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("年份"))
        self.r_year = QComboBox()
        cur = datetime.now().year
        for y in range(cur, cur - 3, -1):
            self.r_year.addItem(f"{y}年", userData=y)
        for i in range(self.r_year.count()):
            if self.r_year.itemData(i) == self._latest_data_year():
                self.r_year.setCurrentIndex(i)
                break
        self.r_year.currentIndexChanged.connect(lambda *_: self._reload_persons())
        bar.addWidget(self.r_year)
        bar.addWidget(CaptionLabel("月份"))
        self.r_month = QComboBox()
        for mo in range(1, 13):
            self.r_month.addItem(f"{mo}月", userData=mo)
        self.r_month.currentIndexChanged.connect(lambda *_: self.refresh_report())
        bar.addWidget(self.r_month)
        bar.addWidget(CaptionLabel("经办人"))
        self.r_person = QComboBox()
        self.r_person.setMinimumWidth(150)
        self.r_person.currentIndexChanged.connect(lambda *_: self.refresh_report())
        bar.addWidget(self.r_person)
        bar.addWidget(CaptionLabel("类型"))
        self.r_type = QComboBox()
        for t, vt in [("汇总", None), ("合伙", "合伙"), ("聘用", "聘用"), ("兼职", "兼职")]:
            self.r_type.addItem(t, userData=vt)
        self.r_type.currentIndexChanged.connect(lambda *_: self.refresh_report())
        bar.addWidget(self.r_type)
        bar.addStretch()
        v.addLayout(bar)
        self._reload_persons()  # 填充 r_person（r_year 已设默认）
        COMBO_QSS = """
        QComboBox { background: #FFFFFF; border: 1px solid #DADAD7; border-radius: 6px;
                    padding: 5px 10px; min-height: 18px; }
        QComboBox QAbstractItemView {
            background: #FFFFFF; color: #37352F;
            selection-background-color: #E9E9E7; selection-color: #37352F;
            border: 1px solid #DADAD7; outline: none;
        }
        """
        for c in (self.r_year, self.r_month, self.r_person, self.r_type):
            c.setStyleSheet(COMBO_QSS)
        self.r_table = QTableWidget(0, 5)
        self.r_table.setHorizontalHeaderLabels(["序号", "项目", "本期", "本年累计", "备注"])
        self.r_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.r_table.verticalHeader().setVisible(False)
        self.r_table.horizontalHeader().setStretchLastSection(True)
        for c, wd in enumerate([40, 200, 95, 95, 300]):
            self.r_table.setColumnWidth(c, wd)
        v.addWidget(self.r_table, 1)
        bbar = QHBoxLayout()
        self.btn_report = PushButton("导出月度结算表…")
        self.btn_report.clicked.connect(self.gen_report)
        self.r_summary = CaptionLabel("")
        self.r_summary.setStyleSheet("color:#8A8886;")
        bbar.addWidget(self.btn_report)
        bbar.addStretch()
        bbar.addWidget(self.r_summary)
        v.addLayout(bbar)
        return w

    def _sync_r_type_options(self, name: str) -> None:
        """月度结算表 Tab 类型下拉动态化"""
        year = self.r_year.currentData() or datetime.now().year
        available = []
        for pt in ("合伙", "聘用", "兼职"):
            st = build_settlement(year, person=name, person_type=pt).get(name)
            if self._has_any(st):
                available.append(pt)
        self.r_type.blockSignals(True)
        self.r_type.clear()
        self.r_type.addItem("汇总", userData=None)
        for pt in available:
            self.r_type.addItem(pt, userData=pt)
        self.r_type.setCurrentIndex(1 if len(available) == 1 else 0)
        self.r_type.blockSignals(False)

    def refresh_report(self) -> None:
        """月度结算表预览：选中经办人 + 月份 → 表格显示其结算表"""
        from app.exporter.settlement_report_exporter import build_report_rows
        if not hasattr(self, "r_table"):
            return
        year = self.r_year.currentData() or datetime.now().year
        month = self.r_month.currentData() or 1
        name = self.r_person.currentData()
        if not name:
            self.r_table.setRowCount(0)
            self.r_summary.setText("请选择经办人")
            return
        self._sync_r_type_options(name)
        ptype = self.r_type.currentData()
        data = build_settlement(year, person=name, person_type=ptype)
        st = data.get(name)
        if st is None:
            self.r_table.setRowCount(0)
            self.r_summary.setText(f"{name} 在 {year} 年无数据")
            return
        from app.exporter.settlement_report_exporter import report_note_rec, report_note_inv
        rows = build_report_rows(st, year, month)
        self.r_table.setRowCount(len(rows))
        from PySide6.QtGui import QFont
        note_rows = []
        for r, (seq, nm, cur, total, bold) in enumerate(rows):
            cells = [seq, nm, cur, total]
            for c, val in enumerate(cells):
                item = QTableWidgetItem("" if val is None else (f"{val:,.2f}" if isinstance(val, float) else str(val)))
                if isinstance(val, float):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if bold:
                    item.setFont(QFont(item.font().family(), item.font().pointSize(), QFont.Weight.Bold))
                self.r_table.setItem(r, c, item)
            # 备注按类别写在对应行（二收款 / 三开票）
            if nm == "本月收款金额":
                note = report_note_rec(st, month)
                if note:
                    it = QTableWidgetItem(note)
                    it.setForeground(Qt.GlobalColor.gray)
                    self.r_table.setItem(r, 4, it)
                    note_rows.append(r)
            elif nm == "本月开具发票金额":
                note = report_note_inv(st, month)
                if note:
                    it = QTableWidgetItem(note)
                    it.setForeground(Qt.GlobalColor.gray)
                    self.r_table.setItem(r, 4, it)
                    note_rows.append(r)
        for r in note_rows:
            self.r_table.resizeRowToContents(r)
        self.r_summary.setText(f"{name}（{self.r_type.currentText()}）· {year}年{month}月结算表预览（共{len(rows)}行，确认后导出）")

    # ---- 年度聘用结算表 Tab（预览 + 导出）----
    def _build_staff_income_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        v.setContentsMargins(8, 10, 8, 10)
        v.setSpacing(10)
        w.setLayout(v)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("年份"))
        self.si_year = QComboBox()
        cur = datetime.now().year
        for y in range(cur, cur - 3, -1):
            self.si_year.addItem(f"{y}年", userData=y)
        for i in range(self.si_year.count()):
            if self.si_year.itemData(i) == self._latest_data_year():
                self.si_year.setCurrentIndex(i)
                break
        self.si_year.currentIndexChanged.connect(lambda *_: self.refresh_staff_income())
        bar.addWidget(self.si_year)
        bar.addWidget(CaptionLabel("月份"))
        self.si_month = QComboBox()
        for mo in range(1, 13):
            self.si_month.addItem(f"{mo}月", userData=mo)
        self.si_month.setCurrentIndex(min(datetime.now().month, 12) - 1)
        self.si_month.currentIndexChanged.connect(lambda *_: self.refresh_staff_income())
        bar.addWidget(self.si_month)
        bar.addStretch()
        v.addLayout(bar)
        COMBO_QSS = """
        QComboBox { background: #FFFFFF; border: 1px solid #DADAD7; border-radius: 6px;
                    padding: 5px 10px; min-height: 18px; }
        QComboBox QAbstractItemView {
            background: #FFFFFF; color: #37352F;
            selection-background-color: #E9E9E7; selection-color: #37352F;
            border: 1px solid #DADAD7; outline: none;
        }
        """
        for c in (self.si_year, self.si_month):
            c.setStyleSheet(COMBO_QSS)
        # 12 列：序号/姓名/5 组(本月/累计)
        self.si_table = QTableWidget(0, 12)
        heads = ["序号", "姓名"]
        for lab in ["本年收入", "报酬发放", "住房公积金", "保险费", "汽油费"]:
            heads += [f"{lab}(本月)", f"{lab}(累计)"]
        self.si_table.setHorizontalHeaderLabels(heads)
        self.si_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.si_table.verticalHeader().setVisible(False)
        self.si_table.horizontalHeader().setStretchLastSection(True)
        self.si_table.setColumnWidth(0, 40)
        self.si_table.setColumnWidth(1, 90)
        for c in range(2, 12):
            self.si_table.setColumnWidth(c, 95)
        v.addWidget(self.si_table, 1)
        bbar = QHBoxLayout()
        self.btn_staff_income = PushButton("导出年度聘用结算表…")
        self.btn_staff_income.clicked.connect(self.gen_staff_income)
        self.si_summary = CaptionLabel("")
        self.si_summary.setStyleSheet("color:#8A8886;")
        bbar.addWidget(self.btn_staff_income)
        bbar.addStretch()
        bbar.addWidget(self.si_summary)
        v.addLayout(bbar)
        return w

    def refresh_staff_income(self) -> None:
        """年度聘用结算表预览：选年份+月份 → 表格显示该月完整内容（含合计行）"""
        from app.exporter.staff_income_exporter import build_report_rows
        if not hasattr(self, "si_table"):
            return
        year = self.si_year.currentData() or datetime.now().year
        month = self.si_month.currentData() or 1
        persons, rows, totals = build_report_rows(year, month)
        from PySide6.QtGui import QFont
        bold = QFont()
        bold.setBold(True)
        self.si_table.setRowCount(len(rows) + 1)
        for i, row in enumerate(rows):
            it0 = QTableWidgetItem(str(i + 1))
            it0.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.si_table.setItem(i, 0, it0)
            self.si_table.setItem(i, 1, QTableWidgetItem(row[0]))
            for j, v in enumerate(row[1:], 2):
                it = QTableWidgetItem(f"{v:,.2f}")
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.si_table.setItem(i, j, it)
        # 合计行
        last = len(rows)
        self.si_table.setItem(last, 0, QTableWidgetItem(""))
        it = QTableWidgetItem("合计")
        it.setFont(bold)
        self.si_table.setItem(last, 1, it)
        for j, v in enumerate(totals, 2):
            it = QTableWidgetItem(f"{v:,.2f}")
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setFont(bold)
            self.si_table.setItem(last, j, it)
        self.si_summary.setText(
            f"{year}年{month}月聘用律师业务收入结算表（{len(persons)} 人）· 预览共 {len(rows)} 行+合计，确认后导出")

    # ---- 导出年度聘用律师业务收入结算表 ----
    def gen_staff_income(self) -> None:
        from pathlib import Path
        year = self.si_year.currentData() or datetime.now().year
        month_to = self.si_month.currentData() or datetime.now().month
        out = self._choose_dir()
        if not out:
            return
        from app.exporter.staff_income_exporter import export_staff_income
        self.log.clear()
        self.log.appendPlainText(f"正在生成 {year}年度聘用律师业务收入结算表（1~{month_to}月）…")
        try:
            f = export_staff_income(Path(out) / f"{year}年度业务收入结算表（聘用律师）.xlsx", year, month_to)
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        self.log.appendPlainText(f"✓ {f}")
        QMessageBox.information(self, "生成完成", f"已生成：{f}")
