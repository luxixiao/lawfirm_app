"""个人结算总表：按模板生成每人一份 Excel"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QTableWidget, QVBoxLayout, QWidget,
)

from qfluentwidgets import (CaptionLabel, ComboBox, PrimaryPushButton, PushButton, SubtitleLabel)

from app.engine.person_settlement import build_settlement
from app.exporter.person_settlement_exporter import export_all, export_one


class SettlementView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("个人结算总表")
        lay.addWidget(t)
        h = CaptionLabel("按模板生成每人一份「个人结算总表.xlsx」（1~12 月 + 合计），口径：收款/开票/未收/业务收入/费用。")
        h.setStyleSheet("color:#8A8886;")
        lay.addWidget(h)

        bar = QHBoxLayout()
        bar.addWidget(CaptionLabel("年份"))
        self.year = ComboBox()
        cur = datetime.now().year
        for y in range(cur, cur - 3, -1):
            self.year.addItem(f"{y}年", userData=y)
        self.year.setCurrentIndex(0)
        bar.addWidget(self.year)

        self.btn_all = PrimaryPushButton("生成全部员工")
        self.btn_all.clicked.connect(self.gen_all)
        bar.addWidget(self.btn_all)

        bar.addWidget(CaptionLabel("单个员工"))
        self.person = ComboBox()
        self._reload_persons()
        bar.addWidget(self.person)
        self.btn_preview = PushButton("在软件内查看")
        self.btn_preview.clicked.connect(self.preview)
        bar.addWidget(self.btn_preview)
        self.btn_one = PushButton("生成该员工")
        self.btn_one.clicked.connect(self.gen_one)
        bar.addWidget(self.btn_one)
        bar.addStretch()
        lay.addLayout(bar)
        # 年份切换时重新加载员工列表
        self.year.currentIndexChanged.connect(lambda *_: self._reload_persons())

        # 预览表格（软件内查看结算总表）
        self.preview_table = QTableWidget(0, 14)
        self.preview_table.setHorizontalHeaderLabels(
            ["项目", "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月", "合计"])
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.preview_table, 3)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("生成日志…")
        lay.addWidget(self.log, 1)

    def _reload_persons(self) -> None:
        year = self.year.currentData() or datetime.now().year
        data = build_settlement(year)
        self.person.clear()
        self.person.addItem("请选择员工", userData=None)
        for name in sorted(data.keys()):
            self.person.addItem(name, userData=name)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reload_persons()

    # ---- 软件内查看 ----
    def preview(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        year = self.year.currentData()
        name = self.person.currentData()
        data = build_settlement(year, person=name)
        if name:
            data = {name: data[name]} if name in data else {}
        if not data:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "提示", "该员工在当前年份没有数据")
            return
        rows = []
        for pname, st in sorted(data.items()):
            self._append_person_rows(rows, pname, st)
        self.preview_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                item = QTableWidgetItem("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if isinstance(v, float) and c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.preview_table.setItem(r, c, item)
        self.log.clear()
        self.log.appendPlainText(f"已加载 {year} 年结算数据：{'、'.join(data.keys())}")

    @staticmethod
    def _append_person_rows(rows: list, name: str, st: dict) -> None:
        """把一个人的结算数据转成表格行（与导出模板一致）"""
        m = st["months"]
        months = list(range(1, 13))
        # 姓名行
        rows.append([f"▶ {name}（{st['staff_type']}）"] + [None] * 13)
        rows.append(["一、上年结余结转"] + [None] * 13)
        # 二
        rec_keys = ["rec_open_cur", "rec_cur_year", "rec_prev_year", "rec_refund_cur", "rec_refund_prev"]
        sub = [round(sum(m[mo][k] for k in rec_keys), 2) for mo in months]
        rows.append(["二、本月收款金额"] + sub + [round(sum(sub), 2)])
        for key, label in [("rec_open_cur", "　1.本月开收"), ("rec_cur_year", "　2.收本年"),
                           ("rec_prev_year", "　3.收上年"), ("rec_refund_cur", "　4.退本年"),
                           ("rec_refund_prev", "　5.退上年")]:
            vals = [round(m[mo][key], 2) for mo in months]
            rows.append([label] + vals + [round(sum(vals), 2)])
        # 三
        inv_keys = ["inv_open_received", "inv_open_uncollected", "inv_red_cur", "inv_red_prev"]
        sub = [round(sum(m[mo][k] for k in inv_keys), 2) for mo in months]
        rows.append(["三、本月开具发票金额"] + sub + [round(sum(sub), 2)])
        for key, label in [("inv_open_received", "　1.本月开收"), ("inv_open_uncollected", "　2.本月未收"),
                           ("inv_red_cur", "　3.红冲本年"), ("inv_red_prev", "　4.红冲上年")]:
            vals = [round(m[mo][key], 2) for mo in months]
            rows.append([label] + vals + [round(sum(vals), 2)])
        # 四
        vals = [round(st["uncollected_month"][mo], 2) for mo in months]
        rows.append(["四、未收款金额"] + vals + [round(st["uncollected_total"], 2)])
        # 五
        vals = [round(m[mo]["income"], 2) for mo in months]
        rows.append(["五、业务收入"] + vals + [round(sum(vals), 2)])
        # 六
        exp = st["expenses"]
        sub = [round(sum(exp.get(t, {}).get(mo, 0.0) for t in exp), 2) for mo in months]
        rows.append(["六、减：分成报酬及费用"] + sub + [round(sum(sub), 2)])
        for i, etype in enumerate(sorted(exp.keys()), 1):
            vals = [round(exp[etype].get(mo, 0.0), 2) for mo in months]
            rows.append([f"　{i}.{etype}"] + vals + [round(sum(vals), 2)])

    def _choose_dir(self) -> str:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        return d or ""

    def gen_all(self) -> None:
        out = self._choose_dir()
        if not out:
            return
        year = self.year.currentData()
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

    def gen_one(self) -> None:
        name = self.person.currentData()
        if not name:
            QMessageBox.information(self, "提示", "请先选择员工")
            return
        out = self._choose_dir()
        if not out:
            return
        year = self.year.currentData()
        try:
            st = build_settlement(year, person=name).get(name)
            if st is None:
                raise RuntimeError(f"员工 {name} 无数据")
            from pathlib import Path
            f = export_one(st, name, Path(out) / f"个人结算总表_{name}.xlsx", year)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "生成失败", str(e))
            return
        self.log.clear()
        self.log.appendPlainText(f"✓ {f}")
        QMessageBox.information(self, "生成完成", f"已生成：{f}")
