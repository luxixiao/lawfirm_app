"""个人结算总表：按经办人+月份筛选预览，导出全部/指定人员（多选）"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui import style
from app.ui.column_layout import install_column_layout
from app.ui.table_features import install_two_tier_header
from app.ui.widgets import (
    CaptionLabel, FrozenTableWidget, PrimaryPushButton, PushButton, tab_help_corner,
)

from app.engine.person_settlement import build_settlement
from app.exporter.person_settlement_exporter import export_all, export_one

MONTH_LABELS = ["1月", "2月", "3月", "4月", "5月", "6月",
                "7月", "8月", "9月", "10月", "11月", "12月"]

_CN_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def _combo_qss(full: bool = False) -> str:
    """筛选栏下拉的显式样式（跟随调色板）。

    原先本文件把同一段硬编码 QSS 复制了 4 份（各自带十六进制色号），
    加第二个皮肤时必漏——现收口到这里，色号全部来自 style.palette()。
    full=True 时额外带 drop-down / popup item 两条（筛选栏那组用）。
    """
    p = style.palette()
    css = (
        "QComboBox { background: %(bg)s; border: 1px solid %(bd)s; border-radius: 6px;"
        " padding: 5px 10px; min-height: 18px; }"
        "QComboBox QAbstractItemView { background: %(bg)s; color: %(tx)s;"
        " selection-background-color: %(sel)s; selection-color: %(tx)s;"
        " border: 1px solid %(bd)s; outline: none; }"
    )
    if full:
        css += ("QComboBox::drop-down { border: none; width: 22px; }"
                "QComboBox QAbstractItemView::item { padding: 6px 10px; min-height: 22px; }")
    return css % {"bg": p["btn_bg"], "bd": p["border_2"],
                  "tx": p["text"], "sel": p["border"]}


def _merge_seq_name(seq, name) -> str:
    """把「序号 + 项目」合并为一个项目名，写法对齐个人结算总表。

    中文序号（一/二/三…）用「、」连接，数字序号（1/2/3…）用「.」连接；
    缺任一侧时返回另一侧原文。
    """
    s = "" if seq is None else str(seq).strip()
    n = "" if name is None else str(name).strip()
    if not s:
        return n
    if not n:
        return s
    sep = "、" if any(ch in _CN_NUM for ch in s) else "."
    return f"{s}{sep}{n}"


class SettlementView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setObjectName("pageTitleBar")
        outer.addWidget(self.tabs)
        self.tab_personal = QWidget()
        self._lay_p = QVBoxLayout(self.tab_personal)
        self._lay_p.setContentsMargins(8, 10, 8, 10)
        self._lay_p.setSpacing(12)
        lay = self._lay_p

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
        COMBO_QSS = _combo_qss(full=True)
        for c in (self.year, self.person, self.month, self.person_type):
            c.setStyleSheet(COMBO_QSS)
            v = c.view()
            if v is not None:
                v.setMinimumWidth(180)

        # ---- 结算总表表格（项目 × 月）----
        self.table = FrozenTableWidget(0, 14, frozen=1)
        self.table.setFrozenDividerVisible(False)
        self.table.setHorizontalHeaderLabels(["项目"] + MONTH_LABELS + ["合计"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table._col = install_column_layout(self.table, "settlement", "personal", movable=False)
        self.table.horizontalHeader().set_sort_marker_visible(False)  # 纯展示表，关排序三角
        lay.addWidget(self.table, 1)

        # ---- 底部：导出 + 摘要 ----
        bottom = QHBoxLayout()
        self.btn_all = PrimaryPushButton("导出全部员工")
        self.btn_all.clicked.connect(self.gen_all)
        self.btn_selected = PushButton("导出指定人员")
        self.btn_selected.clicked.connect(self.gen_selected)
        self.btn_report = PushButton("导出月度结算表")
        self.btn_report.clicked.connect(self.gen_report)
        self.lbl_summary = CaptionLabel("")
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
        self.tabs.addTab(self._build_invoice_income_tab(), "开票收入表")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.setCornerWidget(tab_help_corner(
            "个人结算总表：选择经办人查看结算总表；可导出全部或指定经办人（支持多选）。"
            "口径：收款/开票/未收/业务收入/费用。"
        ), Qt.Corner.TopRightCorner)

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1:
            self.refresh_report()
        elif idx == 2:
            self.refresh_staff_income()
        elif idx == 3:
            self.refresh_invoice_income()

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
        # 按月份模式调整表格列：选某月时显示「1~该月」各月列 + 累计合计（非仅该月）
        if mo:
            self.table.setColumnCount(mo + 2)
            self.table.setHorizontalHeaderLabels(["项目"] + MONTH_LABELS[:mo] + ["合计"])
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
        bold = QFont()
        bold.setBold(True)
        for r, row in enumerate(rows):
            label = str(row[0]) if row and row[0] is not None else ""
            is_cat = (label.startswith(("一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、"))
                      or label.startswith("▶"))
            for c, v in enumerate(row):
                item = QTableWidgetItem("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if isinstance(v, float) and c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if is_cat:
                    # 大分类（一、二、三…）整行加粗，首列显式左对齐
                    item.setFont(bold)
                    if c == 0:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table._col.apply()
        type_txt = self.person_type.currentText()
        self.lbl_summary.setText(
            f"{name}（{type_txt}）{'·1-' + str(mo) + '月累计' if mo else '·全年'}")

    def _build_rows(self, st: dict, year: int) -> list:
        """结算总表行（项目 × 月份 + 合计）。

        选某月 mo 时：显示 1~mo 月各月列，合计 = 1~mo 月累计（与月度结算表「本年累计」口径一致）；
        未选月份（全年）时：显示 1~12 月，合计 = 全年累计。
        """
        m = st["months"]
        mo = self.month.currentData()
        if mo:
            idxs = list(range(mo))          # 1~mo 月
            header_extra = mo + 1           # 项目 + mo 个月 + 合计
        else:
            idxs = list(range(12))
            header_extra = 13
        rows = []
        def row(label: str, vals12: list, is_sub=False):
            vals = [vals12[i] for i in idxs]
            # 合计 = 当前显示各月之和：全年模式=全年累计，选月模式=1~mo 月累计
            total = round(sum(vals), 2)
            prefix = "　" if is_sub else ""
            return [prefix + label] + vals + [total]
        rows.append([f"▶ {st['staff_type']}"] + [None] * header_extra)
        rows.append(["一、上年结余结转"] + [None] * header_extra)
        # 二
        rec_keys = ["rec_open_cur", "rec_cur_year", "rec_prev_year", "rec_refund_cur", "rec_refund_prev"]
        rows.append(row("二、本月收款金额", [round(sum(m[mo_][k] for k in rec_keys), 2) for mo_ in range(1, 13)], False))
        for key, label in [("rec_open_cur", "1.本月开收"), ("rec_cur_year", "2.收本年"),
                           ("rec_prev_year", "3.收上年"), ("rec_refund_cur", "4.退本年"),
                           ("rec_refund_prev", "5.退上年")]:
            rows.append(row(label, [round(m[mo_][key], 2) for mo_ in range(1, 13)], True))
        # 三（小计 = 本月开票总额，独立计算，含预收票）
        rows.append(row("三、本月开具发票金额", [round(m[mo_]["inv_total"], 2) for mo_ in range(1, 13)], False))
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
                        [round(sum(exp.get(t, {}).get(mo_, 0.0) for t in exp), 2) for mo_ in range(1, 13)], False))
        for i, etype in enumerate(self._sorted_expense_types(exp), 1):
            rows.append(row(f"{i}.{etype}", [round(exp[etype].get(mo_, 0.0), 2) for mo_ in range(1, 13)], True))
        # 修正合计列：未收款金额为存量口径（各月本月未收的累计余额）
        #   全年 → uncollected_total（本年累计未收）；选 mo 月 → 1~mo 月累计未收
        uncollected_cum = (st["uncollected_total"] if not mo
                           else round(sum(st["uncollected_month"][x] for x in range(1, mo + 1)), 2))
        for rr in rows:
            if rr and rr[0] == "四、未收款金额":
                rr[-1] = round(uncollected_cum, 2)
                break
        return rows

    @staticmethod
    def _sorted_expense_types(exp: dict) -> list:
        """费用类型按维护顺序（expense_cat.sort_order）排序，未维护的按名称兜底"""
        from app.engine.expense_cat import ordered_types
        order = {t: i for i, t in enumerate(ordered_types())}
        return sorted(exp.keys(), key=lambda t: (order.get(t, 999), t))

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
        COMBO_QSS = _combo_qss()
        for c in (self.r_year, self.r_month, self.r_person, self.r_type):
            c.setStyleSheet(COMBO_QSS)
        # 4 列：项目(序号+项目合并，对齐个人结算总表写法) / 本期 / 本年累计 / 备注
        self.r_table = FrozenTableWidget(0, 4, frozen=1)
        self.r_table.setFrozenDividerVisible(False)
        self.r_table.setHorizontalHeaderLabels(["项目", "本期", "本年累计", "备注"])
        self.r_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.r_table.setSortingEnabled(False)  # 月度结算表不提供排序功能，首列(项目)始终冻结
        self.r_table.verticalHeader().setVisible(False)
        self.r_table.horizontalHeader().setStretchLastSection(True)
        self.r_table._col = install_column_layout(self.r_table, "settlement", "report", movable=False)
        self.r_table.horizontalHeader().set_sort_marker_visible(False)  # 纯展示表，关排序三角
        v.addWidget(self.r_table, 1)
        bbar = QHBoxLayout()
        self.btn_report = PushButton("导出月度结算表")
        self.btn_report.clicked.connect(self.gen_report)
        self.r_summary = CaptionLabel("")
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
            # 序号与项目合并为一列，写法对齐个人结算总表：中文序号用「、」，数字序号用「.」
            label = _merge_seq_name(seq, nm)
            cells = [label, cur, total]
            for c, val in enumerate(cells):
                item = QTableWidgetItem("" if val is None else (f"{val:,.2f}" if isinstance(val, float) else str(val)))
                if isinstance(val, float):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if bold:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.r_table.setItem(r, c, item)
            # 备注按类别写在对应行（二收款 / 三开票）
            if nm == "本月收款金额":
                note = report_note_rec(st, month)
                if note:
                    it = QTableWidgetItem(note)
                    it.setForeground(Qt.GlobalColor.gray)
                    self.r_table.setItem(r, 3, it)
                    note_rows.append(r)
            elif nm == "本月开具发票金额":
                note = report_note_inv(st, month)
                if note:
                    it = QTableWidgetItem(note)
                    it.setForeground(Qt.GlobalColor.gray)
                    self.r_table.setItem(r, 3, it)
                    note_rows.append(r)
        for r in note_rows:
            self.r_table.resizeRowToContents(r)
        self.r_table._col.apply()
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
        COMBO_QSS = _combo_qss()
        for c in (self.si_year, self.si_month):
            c.setStyleSheet(COMBO_QSS)
        # 12 列：序号/姓名/5 组(本月/累计)；表头两行（第一行大类跨列合并，第二行本月/累计）
        self.si_table = FrozenTableWidget(0, 12, frozen=1)
        self.si_table.setFrozenDividerVisible(False)
        install_two_tier_header(
            self.si_table,
            lead_labels=["序号", "姓名"],
            groups=[(lab, ["本月", "累计"]) for lab in
                    ["本年收入", "报酬发放", "住房公积金", "保险费", "汽油费"]],
        )
        self.si_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.si_table.verticalHeader().setVisible(False)
        self.si_table.horizontalHeader().setStretchLastSection(True)
        self.si_table._col = install_column_layout(self.si_table, "settlement", "staff_income", movable=False)
        self.si_table.horizontalHeader().set_sort_marker_visible(False)  # 纯展示表，关排序三角
        v.addWidget(self.si_table, 1)
        bbar = QHBoxLayout()
        self.btn_staff_income = PushButton("导出当月")
        self.btn_staff_income.setToolTip("仅导出当前预览月份的单个 sheet")
        self.btn_staff_income.clicked.connect(self.gen_staff_income)
        self.btn_staff_income_full = PushButton("导出模板表")
        self.btn_staff_income_full.setToolTip("导出 1~选中月 全部月份 sheet（从新到旧排序，仿模板文件形态）")
        self.btn_staff_income_full.clicked.connect(self.gen_staff_income_full)
        self.si_summary = CaptionLabel("")
        bbar.addWidget(self.btn_staff_income)
        bbar.addWidget(self.btn_staff_income_full)
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
        self.si_table._col.apply()
        self.si_summary.setText(
            f"{year}年{month}月聘用律师业务收入结算表（{len(persons)} 人）· 预览共 {len(rows)} 行+合计，确认后导出")

    # ---- 导出年度聘用律师业务收入结算表 ----
    def gen_staff_income(self) -> None:
        from pathlib import Path
        year = self.si_year.currentData() or datetime.now().year
        month = self.si_month.currentData() or datetime.now().month
        out = self._choose_dir()
        if not out:
            return
        from app.exporter.staff_income_exporter import export_staff_income_month
        self.log.clear()
        self.log.appendPlainText(f"正在生成 {year}年{month}月聘用律师业务收入结算表…")
        try:
            f = export_staff_income_month(
                Path(out) / f"{year}年度业务收入结算表（聘用律师）_{year}{month:02d}.xlsx",
                year, month)
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        self.log.appendPlainText(f"✓ {f}")
        QMessageBox.information(self, "生成完成", f"已生成：{f}")

    # ---- 导出模板表（1~选中月全部 sheet，从新到旧）----
    def gen_staff_income_full(self) -> None:
        from pathlib import Path
        year = self.si_year.currentData() or datetime.now().year
        month_to = self.si_month.currentData() or datetime.now().month
        out = self._choose_dir()
        if not out:
            return
        from app.exporter.staff_income_exporter import export_staff_income
        self.log.clear()
        self.log.appendPlainText(f"正在生成 {year}年度聘用律师业务收入结算表模板表（1~{month_to}月，从新到旧）…")
        try:
            f = export_staff_income(
                Path(out) / f"{year}年度业务收入结算表（聘用律师）.xlsx", year, month_to)
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        self.log.appendPlainText(f"✓ {f}")
        QMessageBox.information(self, "生成完成", f"已生成：{f}")

    # ---- 开票收入表 Tab（预览 + 导出）----
    def _build_invoice_income_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout()
        v.setContentsMargins(8, 10, 8, 10)
        v.setSpacing(10)
        w.setLayout(v)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("年份"))
        self.ii_year = QComboBox()
        cur = datetime.now().year
        for y in range(cur, cur - 3, -1):
            self.ii_year.addItem(f"{y}年", userData=y)
        for i in range(self.ii_year.count()):
            if self.ii_year.itemData(i) == self._latest_data_year():
                self.ii_year.setCurrentIndex(i)
                break
        self.ii_year.currentIndexChanged.connect(lambda *_: self.refresh_invoice_income())
        bar.addWidget(self.ii_year)
        bar.addWidget(CaptionLabel("月份"))
        self.ii_month = QComboBox()
        for mo in range(1, 13):
            self.ii_month.addItem(f"{mo}月", userData=mo)
        self.ii_month.setCurrentIndex(min(datetime.now().month, 12) - 1)
        self.ii_month.currentIndexChanged.connect(lambda *_: self.refresh_invoice_income())
        bar.addWidget(self.ii_month)
        bar.addStretch()
        v.addLayout(bar)
        COMBO_QSS = _combo_qss()
        for c in (self.ii_year, self.ii_month):
            c.setStyleSheet(COMBO_QSS)
        # 8 列：序号/姓名/收入本月/收入累计/期末未收/开票已收/收回以前/合计收款
        self.ii_table = FrozenTableWidget(0, 8, frozen=1)
        self.ii_table.setFrozenDividerVisible(False)
        self.ii_table.setHorizontalHeaderLabels(
            ["序号", "姓名", "收入本月", "收入累计", "期末未收",
             "本月开票本月收回", "本月收回以前应收款", "本月合计收款"])
        self.ii_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ii_table.verticalHeader().setVisible(False)
        self.ii_table.horizontalHeader().setStretchLastSection(True)
        self.ii_table._col = install_column_layout(self.ii_table, "settlement", "invoice_income", movable=False)
        self.ii_table.horizontalHeader().set_sort_marker_visible(False)  # 纯展示表，关排序三角
        v.addWidget(self.ii_table, 1)
        bbar = QHBoxLayout()
        self.btn_ii_month = PushButton("导出当月")
        self.btn_ii_month.setToolTip("仅导出当前预览月份主表 + 未收款明细")
        self.btn_ii_month.clicked.connect(self.gen_invoice_income)
        self.btn_ii_full = PushButton("导出模板表")
        self.btn_ii_full.setToolTip("导出 1~选中月 主表（从新到旧）+ 未收款明细（当年+历史年度）")
        self.btn_ii_full.clicked.connect(self.gen_invoice_income_full)
        self.ii_summary = CaptionLabel("")
        bbar.addWidget(self.btn_ii_month)
        bbar.addWidget(self.btn_ii_full)
        bbar.addStretch()
        bbar.addWidget(self.ii_summary)
        v.addLayout(bbar)
        return w

    def refresh_invoice_income(self) -> None:
        """开票收入表预览：主表（选中月，8 列 + 合计行）"""
        from app.exporter.invoice_income_exporter import build_main_preview
        if not hasattr(self, "ii_table"):
            return
        year = self.ii_year.currentData() or datetime.now().year
        month = self.ii_month.currentData() or 1
        persons, rows, totals = build_main_preview(year, month)
        from PySide6.QtGui import QFont
        bold = QFont()
        bold.setBold(True)
        self.ii_table.setRowCount(len(rows) + 1)
        for i, row in enumerate(rows):
            it0 = QTableWidgetItem(str(i + 1))
            it0.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.ii_table.setItem(i, 0, it0)
            self.ii_table.setItem(i, 1, QTableWidgetItem(row[0]))
            for j, v in enumerate(row[1:], 2):
                it = QTableWidgetItem(f"{v:,.2f}")
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.ii_table.setItem(i, j, it)
        # 合计行
        last = len(rows)
        self.ii_table.setItem(last, 0, QTableWidgetItem(""))
        it = QTableWidgetItem("合计")
        it.setFont(bold)
        self.ii_table.setItem(last, 1, it)
        for j, v in enumerate(totals, 2):
            it = QTableWidgetItem(f"{v:,.2f}")
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setFont(bold)
            self.ii_table.setItem(last, j, it)
        self.ii_table._col.apply()
        self.ii_summary.setText(
            f"{year}年{month}月律师收费情况表（开票收入）· {len(persons)} 人 + 合计，确认后导出")

    # ---- 导出开票收入表 ----
    def gen_invoice_income(self) -> None:
        from pathlib import Path
        year = self.ii_year.currentData() or datetime.now().year
        month = self.ii_month.currentData() or datetime.now().month
        out = self._choose_dir()
        if not out:
            return
        from app.exporter.invoice_income_exporter import export_invoice_income_month
        self.log.clear()
        self.log.appendPlainText(f"正在生成 {year}年{month}月开票收入表…")
        try:
            f = export_invoice_income_month(
                Path(out) / f"{year}年度开票收入_{year}{month:02d}.xlsx", year, month)
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        self.log.appendPlainText(f"✓ {f}")
        QMessageBox.information(self, "生成完成", f"已生成：{f}")

    def gen_invoice_income_full(self) -> None:
        from pathlib import Path
        year = self.ii_year.currentData() or datetime.now().year
        month_to = self.ii_month.currentData() or datetime.now().month
        out = self._choose_dir()
        if not out:
            return
        from app.exporter.invoice_income_exporter import export_invoice_income
        self.log.clear()
        self.log.appendPlainText(f"正在生成 {year}年度开票收入表模板表（1~{month_to}月，从新到旧 + 未收款明细）…")
        try:
            f = export_invoice_income(
                Path(out) / f"{year}年度开票收入.xlsx", year, month_to)
        except Exception as e:  # noqa: BLE001
            self.log.appendPlainText(f"✗ 生成失败: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
            return
        self.log.appendPlainText(f"✓ {f}")
        QMessageBox.information(self, "生成完成", f"已生成：{f}")
