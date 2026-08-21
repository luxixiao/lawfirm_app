"""个人结算总表：按模板生成每人一份 Excel"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QVBoxLayout, QWidget,
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
            self.year.addItem(f"{y}年", y)
        self.year.setCurrentIndex(0)
        bar.addWidget(self.year)

        self.btn_all = PrimaryPushButton("生成全部员工")
        self.btn_all.clicked.connect(self.gen_all)
        bar.addWidget(self.btn_all)

        bar.addWidget(CaptionLabel("单个员工"))
        self.person = ComboBox()
        self._reload_persons()
        bar.addWidget(self.person)
        self.btn_one = PushButton("生成该员工")
        self.btn_one.clicked.connect(self.gen_one)
        bar.addWidget(self.btn_one)
        bar.addStretch()
        lay.addLayout(bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("生成日志…")
        lay.addWidget(self.log, 1)

    def _reload_persons(self) -> None:
        data = build_settlement(datetime.now().year)
        self.person.clear()
        self.person.addItem("请选择员工", None)
        for name in sorted(data.keys()):
            self.person.addItem(name, name)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reload_persons()

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
