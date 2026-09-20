"""分成计算 — 选择器与参数/指标管理对话框（spec: calc_engine_spec.md §5/§8）

- DataRefDialog：选择器单元格 → 选 职工+指标+账期，生成 =DATA(...) 原文写入当前格
  （与手写公式完全等价）。
- ParamDialog：本表命名参数（A 层）管理，编辑 content.params。
- IndicatorManagerDialog：自定义指标（B 层）calc_indicator 增删改；
  definition 保存前用公式内核做语法校验（占位符替换哑元后 parse）；
  删除前扫描全部计算表检测引用（JSON 全表扫描，表量小可接受）。
纯逻辑辅助函数（build_data_formula / validate_indicator_definition /
indicator_usage_count）独立导出，供无头测试。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.db import get_conn
from app.ui import style
from app.engine.calc_data import builtin_names
from app.engine.calc_formula import ParseError, Parser

# 内置函数名（spec §11.3：参数/指标名不得与其重名）
_RESERVED = {"SUM", "ROUND", "AVERAGE", "IF", "MIN", "MAX", "ABS",
             "DATA", "PARAM", "TRUE", "FALSE"}

_PLACEHOLDER = re.compile(r"\$(职工|年|月)")


# ---------------------------------------------------------------------------
# 纯逻辑辅助（无 GUI，可测）
# ---------------------------------------------------------------------------

def build_data_formula(person: str, indicator: str, year: int,
                       month: Optional[int]) -> str:
    p = person.replace('"', '""')
    i = indicator.replace('"', '""')
    if month is None:
        return f'=DATA("{p}","{i}",{int(year)})'
    return f'=DATA("{p}","{i}",{int(year)},{int(month)})'


def validate_indicator_name(name: str, exclude_self: str = "") -> Optional[str]:
    """合法返回 None；非法返回错误说明。"""
    name = (name or "").strip()
    if not name:
        return "指标名不能为空"
    if len(name) > 50:
        return "指标名过长（≤50 字符）"
    if name.upper() in {r.upper() for r in _RESERVED}:
        return f"指标名不能与内置函数重名：{name}"
    if exclude_self and name == exclude_self:
        return None
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM calc_indicator WHERE name=?", (name,)).fetchone()
        return None if not row else f"指标名已存在：{name}"
    finally:
        conn.close()


def validate_param_name(name: str) -> Optional[str]:
    """参数名校验（spec §11.3）：非空、≤50、不含引号、不与内置函数重名。"""
    name = (name or "").strip()
    if not name:
        return "参数名不能为空"
    if len(name) > 50:
        return "参数名过长（≤50 字符）"
    if '"' in name:
        return "参数名不能含双引号"
    if name.upper() in {r.upper() for r in _RESERVED}:
        return f"参数名不能与内置函数重名：{name}"
    return None


def validate_indicator_definition(definition: str) -> Optional[str]:
    """把 $职工/$年/$月 替换为哑元后用公式内核 parse，语法错返回说明。"""
    definition = (definition or "").strip()
    if not definition:
        return "定义不能为空"
    dummy = _PLACEHOLDER.sub(
        lambda m: {"职工": '"测试职工"', "年": "2025", "月": "1"}[m.group(1)],
        definition)
    if "$" in dummy:
        return "定义含未知占位符（仅支持 $职工/$年/$月）"
    try:
        Parser(dummy).parse()
    except ParseError as e:
        return f"定义语法错误：{e}"
    return None


def indicator_usage_count(name: str) -> int:
    """扫描全部计算表，统计引用了该指标的公式格数（删除前提示用）。"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT content FROM calc_sheet").fetchall()
    finally:
        conn.close()
    n = 0
    needle = f'"{name}"'
    for r in rows:
        try:
            content = json.loads(r["content"] or "{}")
        except json.JSONDecodeError:
            continue
        for cell in (content.get("cells") or {}).values():
            raw = str(cell.get("raw") or "")
            if raw.startswith("=") and needle in raw:
                n += 1
    return n


def list_years() -> List[str]:
    """可选账期年份：导入批次 + 发票日期的并集，降序。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT DISTINCT y FROM (
                 SELECT substr(period,1,4) AS y FROM import_batch WHERE IFNULL(period,'')!=''
                 UNION
                 SELECT strftime('%Y',invoice_date) AS y FROM invoice
                 WHERE invoice_date IS NOT NULL AND invoice_date != ''
               ) WHERE y != '' ORDER BY y DESC""").fetchall()
        return [r["y"] for r in rows]
    finally:
        conn.close()


def list_staff() -> List[str]:
    """花名册全部姓名，按姓名排序。（「停用」已取消，一律可选，按数据年月取数。）"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT name FROM staff ORDER BY name").fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()


def list_indicator_names() -> List[str]:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT name FROM calc_indicator ORDER BY name").fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 选择器单元格
# ---------------------------------------------------------------------------

class DataRefDialog(QDialog):
    """选 职工+指标+账期 → 生成 =DATA(...) 公式。formula 属性取结果。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("插入数据引用")
        self.formula: str = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(8)

        self.f_person = QComboBox()
        self.f_person.setEditable(True)
        for name in list_staff():
            self.f_person.addItem(name, name)
        form.addRow("职工：", self.f_person)

        self.f_indicator = QComboBox()
        for name in builtin_names():
            self.f_indicator.addItem(name, name)
        custom = list_indicator_names()
        if custom:
            self.f_indicator.insertSeparator(self.f_indicator.count())
            for name in custom:
                self.f_indicator.addItem(f"{name}（自定义）", name)
        self.f_indicator.setCurrentIndex(self.f_indicator.findData("业务收入"))
        form.addRow("指标：", self.f_indicator)

        self.f_year = QComboBox()
        self.f_year.setEditable(True)
        for y in list_years():
            self.f_year.addItem(y, y)
        form.addRow("年份：", self.f_year)

        self.f_month = QComboBox()
        self.f_month.addItem("全年累计", None)
        for m in range(1, 13):
            self.f_month.addItem(f"{m} 月", m)
        form.addRow("月份：", self.f_month)

        lay.addLayout(form)
        self.lbl_preview = QLabel("")
        lay.addWidget(self.lbl_preview)
        for w in (self.f_person, self.f_indicator, self.f_year, self.f_month):
            w.currentIndexChanged.connect(self._preview)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._preview()

    def _current_formula(self) -> str:
        person = self.f_person.currentData()
        indicator = self.f_indicator.currentData()
        year = self.f_year.currentText().strip()
        month = self.f_month.currentData()
        if not person or not indicator or not year.isdigit():
            return ""
        return build_data_formula(person, indicator, int(year), month)

    def _preview(self) -> None:
        f = self._current_formula()
        self.lbl_preview.setText(f"将插入：{f}" if f else "请选择 职工/指标/年份")
        self.lbl_preview.setStyleSheet(f"color:{style.palette()['text_mute']};")

    def _accept(self) -> None:
        f = self._current_formula()
        if not f:
            QMessageBox.warning(self, "信息不全", "请选择 职工、指标与年份。")
            return
        self.formula = f
        self.accept()


# ---------------------------------------------------------------------------
# 本表命名参数（A 层）
# ---------------------------------------------------------------------------

class ParamDialog(QDialog):
    """编辑 content.params：名称 → 数值。params 属性返回新字典。"""

    def __init__(self, params: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本表参数")
        self.params: Dict = dict(params or {})

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        help_html = (
            "命名参数 = 本表内可复用的常量，在公式中通过 <b>PARAM(\"名称\")</b> 引用。<br>"
            "· 左侧填参数名，右侧填数值（或文本）。<br>"
            "· 例：名称 <b>提成比例</b>，值 <b>0.3</b>；公式中写 "
            "<b>=B2*PARAM(\"提成比例\")</b> 即按 0.3 计算。<br>"
            "· 命名规则：非空、≤50 字、不含引号，且不能与函数名"
            "（SUM/ROUND/AVERAGE/IF/MIN/MAX/ABS/DATA/PARAM）重名。<br>"
            "· 修改参数后，引用它的公式会自动重算。"
        )
        lbl_help = QLabel(help_html)
        lbl_help.setWordWrap(True)
        lbl_help.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lbl_help.setStyleSheet(f"color:{style.palette()['text_mute']};")
        lay.addWidget(lbl_help)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["名称", "值"])
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        btn_add = QPushButton("加参数")
        btn_del = QPushButton("删除选中")
        btn_add.clicked.connect(self._add)
        btn_del.clicked.connect(self._del)
        bar.addWidget(btn_add)
        bar.addWidget(btn_del)
        bar.addStretch(1)
        lay.addLayout(bar)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        for k, v in self.params.items():
            self._append_row(k, v)

    def _append_row(self, k: str, v) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(k)))
        self.table.setItem(r, 1, QTableWidgetItem(str(v)))

    def _add(self) -> None:
        self._append_row("新参数", 0)

    def _del(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _accept(self) -> None:
        out: Dict = {}
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            raw = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if not name and not raw:
                continue
            err = validate_param_name(name)
            if err:
                QMessageBox.warning(self, "参数校验失败", f"第 {r + 1} 行：{err}")
                return
            if name in out:
                QMessageBox.warning(self, "参数校验失败", f"第 {r + 1} 行：参数名重复：{name}")
                return
            try:
                out[name] = float(raw)
            except ValueError:
                out[name] = raw
        self.params = out
        self.accept()


# ---------------------------------------------------------------------------
# 自定义指标管理（B 层）
# ---------------------------------------------------------------------------

class _IndicatorEditDialog(QDialog):
    def __init__(self, name="", definition="", note="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义指标")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.f_name = QLineEdit(name)
        self.f_note = QLineEdit(note)
        form.addRow("指标名：", self.f_name)
        form.addRow("说明：", self.f_note)
        lay.addLayout(form)
        lay.addWidget(QLabel("定义（公式，$职工/$年/$月 为占位符）：\n"
                             "例：DATA($职工,\"业务收入\",$年,$月)*PARAM(\"提成比例\")"))
        self.f_def = QPlainTextEdit(definition)
        self.f_def.setMinimumHeight(90)
        lay.addWidget(self.f_def, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._check)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _check(self) -> None:
        err = validate_indicator_definition(self.f_def.toPlainText())
        if err:
            QMessageBox.warning(self, "定义校验失败", err)
            return
        self.accept()

    def values(self):
        return (self.f_name.text().strip(), self.f_def.toPlainText().strip(),
                self.f_note.text().strip())


class IndicatorManagerDialog(QDialog):
    """calc_indicator 增删改（全局，非本表）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义指标管理")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "自定义指标：定义里用 $职工/$年/$月 占位，保存后在 DATA() 的指标下拉中出现。"))

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["指标名", "定义"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        for text, fn in (("新增", self._add), ("编辑", self._edit), ("删除", self._del)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        lay.addLayout(bar)
        self._reload()
        self.changed = False

    def _reload(self) -> None:
        from app.db import get_conn
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT name, definition, note FROM calc_indicator ORDER BY name"
            ).fetchall()
        finally:
            conn.close()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r["name"]))
            it = QTableWidgetItem(r["definition"])
            it.setToolTip(r["note"] or "")
            self.table.setItem(i, 1, it)

    def _add(self) -> None:
        dlg = _IndicatorEditDialog(parent=self)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            name, definition, note = dlg.values()
            err = validate_indicator_name(name)
            if err:
                QMessageBox.warning(self, "名称校验失败", err)
                continue
            conn = get_conn()
            try:
                conn.execute(
                    "INSERT INTO calc_indicator(name, definition, note, updated_by) "
                    "VALUES(?,?,?,?)",
                    (name, definition, note, os.environ.get("USERNAME", "")))
                conn.commit()
            finally:
                conn.close()
            self.changed = True
            self._reload()
            return

    def _current_name(self) -> Optional[str]:
        r = self.table.currentRow()
        if r < 0 or not self.table.item(r, 0):
            return None
        return self.table.item(r, 0).text()

    def _edit(self) -> None:
        name = self._current_name()
        if not name:
            return
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT definition, note FROM calc_indicator WHERE name=?", (name,)
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return
        dlg = _IndicatorEditDialog(name, row["definition"], row["note"] or "", parent=self)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            new_name, definition, note = dlg.values()
            err = validate_indicator_name(new_name, exclude_self=name)
            if err:
                QMessageBox.warning(self, "名称校验失败", err)
                continue
            conn = get_conn()
            try:
                conn.execute(
                    "UPDATE calc_indicator SET name=?, definition=?, note=?, "
                    "updated_at=datetime('now','localtime') WHERE name=?",
                    (new_name, definition, note, name))
                conn.commit()
            finally:
                conn.close()
            self.changed = True
            self._reload()
            return

    def _del(self) -> None:
        name = self._current_name()
        if not name:
            return
        n = indicator_usage_count(name)
        tip = f"，当前被 {n} 个单元格引用（删除后这些格将显示 #REF!）" if n else ""
        ret = QMessageBox.question(self, "确认删除",
                                   f"删除自定义指标「{name}」{tip}？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        conn = get_conn()
        try:
            conn.execute("DELETE FROM calc_indicator WHERE name=?", (name,))
            conn.commit()
        finally:
            conn.close()
        self.changed = True
        self._reload()
