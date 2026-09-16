"""日期输入兼容层 —— 所有日期输入框都接受台账里常见的多种写法。

支持（解析口径复用 app.importer.date_utils.normalize_date，与导入完全一致）：
    2025-09-01 / 2025-9-1 / 2025/9/1 / 2025.9.1 / 2025.9.01 / 2025.09.01
    25.9.1 / 25.09.1 / 25.9.01 / 2025年9月1日   → 日粒度（YYYY-MM-DD）
    2025.9 / 25.9 / 2025-09 / 2025年9月          → 月粒度（YYYY-MM）
    空串 / 空白                                   → 视为"未填"

对外两个东西：
- parse_flex_date(text, default_year=None) -> (值, 粒度)
    粒度为 "day" / "month"，值分别为 "YYYY-MM-DD" / "YYYY-MM"；空文本 → ("", "day")；
    无法解析 → 抛 ImportError_（由调用方决定怎么提示）。
- DateInput —— 可直接键入上述任意写法的输入框，右侧「▾」弹出日历（可选到日，
  月粒度取值自动截到月）。原来用 QDateEdit 的两处（补录明细收款日期、退款日期）
  换成它：QDateEdit 的段式校验会直接吞掉 "25.9.1" 这类输入（实测整串被拒），
  改不了，只能换控件。
"""
from __future__ import annotations

import re

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QCalendarWidget, QDialog, QHBoxLayout, QLineEdit, QToolButton, QVBoxLayout, QWidget,
)

from app.importer.date_utils import normalize_date

UNIT_DAY = "day"
UNIT_MONTH = "month"

# 只有「年 + 月」没有「日」的写法 → 保留月粒度；其余交给 normalize_date 补日
_MONTH_ONLY = re.compile(r"^(?:\d{4}|\d{2})[年./\-]\d{1,2}月?$")


def parse_flex_date(text, default_year: int | None = None):
    """→ (归一值, 粒度)。空文本 ("", "day")；解析失败抛 ImportError_。"""
    s = "" if text is None else str(text).strip()
    if not s:
        return "", UNIT_DAY
    unit = UNIT_MONTH if _MONTH_ONLY.match(s) else UNIT_DAY
    norm = normalize_date(s, default_year)          # 失败抛 ImportError_
    return (norm[:7] if unit == UNIT_MONTH else norm), unit


def normalize_flex(text, unit: str = UNIT_DAY, default_year: int | None = None) -> str:
    """按指定粒度归一（unit=month 时截到月）。空文本 → ""。失败抛 ImportError_。"""
    value, _unit = parse_flex_date(text, default_year)
    if not value:
        return ""
    return value[:7] if unit == UNIT_MONTH else value


class DateInput(QWidget):
    """日期输入：自由手输 + 可选日历。

    text() 返回归一后的 "YYYY-MM"（月粒度，默认）或 "YYYY-MM-DD"（日粒度）；
    未填返回 ""；输入非法时 text() 也返回 ""，可用 is_valid() 区分并取 error() 说明。
    """

    textChanged = Signal(str)

    def __init__(self, unit: str = UNIT_MONTH, parent=None,
                 placeholder: str = "") -> None:
        super().__init__(parent)
        self._unit = unit
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(
            placeholder or ("如 25.9 / 2025-09" if unit == UNIT_MONTH else "如 25.9.1 / 2025-09-01"))
        self.edit.setToolTip(
            "可直接输入：25.9.1 / 2025.9.1 / 2025.9.01 / 2025.09.01 / 25.09.1 / "
            "25.09.01 / 25.9.01 / 2025-09-01 / 2025年9月1日"
            + ("；只填到月也可以（如 2025.9）" if unit == UNIT_MONTH else ""))
        self.btn = QToolButton()
        self.btn.setObjectName("datePickBtn")
        self.btn.setText("▾")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setToolTip("从日历选择")
        self.btn.clicked.connect(self._popup_calendar)

        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn, 0)
        self.edit.textChanged.connect(self.textChanged.emit)

    # ------------------------------------------------------------------ #
    # 取值
    # ------------------------------------------------------------------ #
    def raw(self) -> str:
        return self.edit.text().strip()

    def value(self) -> str:
        """归一值；未填或非法 → ""。"""
        try:
            return normalize_flex(self.raw(), self._unit)
        except Exception:  # noqa: BLE001 - 解析失败一律按"无效"处理
            return ""

    def text(self) -> str:
        return self.value()

    def is_empty(self) -> bool:
        return not self.raw()

    def is_valid(self) -> bool:
        return self.is_empty() or bool(self.value())

    def error(self) -> str:
        """非法时返回提示文案（合法/未填返回 ""）。"""
        if self.is_valid():
            return ""
        return f"日期无法解析：{self.raw()}"

    def set_text(self, text: str | None) -> None:
        self.edit.setText("" if text is None else str(text))

    def set_value(self, text: str | None) -> None:
        self.set_text(text)

    def clear(self) -> None:
        self.edit.clear()

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        self.edit.setEnabled(on)
        self.btn.setEnabled(on)

    # ------------------------------------------------------------------ #
    def _popup_calendar(self) -> None:
        dlg = QDialog(self, Qt.WindowType.Popup)
        cal = QCalendarWidget(dlg)
        cal.setGridVisible(True)
        cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        cur = self.value()
        if cur:
            cal.setSelectedDate(QDate.fromString(cur, "yyyy-MM-dd"))
        box = QVBoxLayout(dlg)
        box.setContentsMargins(4, 4, 4, 4)
        box.addWidget(cal)

        def _pick(d: QDate) -> None:
            self.set_text(d.toString("yyyy-MM-dd"))
            dlg.accept()

        cal.clicked.connect(_pick)
        dlg.move(self.mapToGlobal(self.rect().bottomLeft()))
        dlg.exec()
