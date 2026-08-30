"""分成计算引擎 — 求值编排层（spec: calc_engine_spec.md §4）

把 calc_sheet（JSON 整表网格）、calc_formula（公式内核）、
calc_data（内置指标）+ calc_indicator（自定义指标）串成一体：

    CalcEvaluator(conn, cur_sheet_id)
        .cell_value(sheet_name, r, c)   → 单格值（float/str/ErrVal）
        .sheet_grid(sheet_name)         → 整表二维值（UI 查看/导出用）

- 自定义指标（B 层）：DATA(职工,"自定义指标",年[,月]) → 查 calc_indicator.definition，
  将 $职工/$年/$月 文本替换后作为公式在同一网格上下文中求值；
  指标嵌套指标支持，环 → CalcDataError（呈现为 #REF!）。
- 请求级缓存：实例内 网格值 / 指标值 各缓存一次，重开页面新实例自然刷新。
"""
from __future__ import annotations

import json
import re
from typing import Dict, Optional

from app.db import get_conn
from app.engine.calc_formula import (
    CellProvider, Engine, ErrVal, classify_cell,
)
from app.engine.calc_data import CalcData, CalcDataError

_PLACEHOLDER = re.compile(r"\$(职工|年|月)")


class CalcEvaluator:
    """一个求值批次一个实例（缓存 + 环检测都在实例内）。"""

    def __init__(self, conn=None, cur_sheet_id: Optional[int] = None):
        self._own = conn is None
        self.conn = conn if conn is not None else get_conn()
        self.sheets: Dict[str, dict] = self._load_sheets()
        self.calc_data = CalcData(self.conn)
        # 自定义指标名 → definition
        self.indicators: Dict[str, str] = {
            r["name"]: r["definition"]
            for r in self.conn.execute("SELECT name, definition FROM calc_indicator")
        }
        self._ind_active: set = set()   # 指标求值栈（环检测）
        self._ind_cache: Dict[tuple, object] = {}
        self._engine = Engine(_GridProvider(self), self._sheet_name(cur_sheet_id))

    # ---------- 载入 ----------
    def _load_sheets(self) -> Dict[str, dict]:
        out = {}
        for r in self.conn.execute("SELECT name, content FROM calc_sheet"):
            try:
                out[r["name"]] = json.loads(r["content"] or "{}")
            except json.JSONDecodeError:
                out[r["name"]] = {}
        return out

    def _sheet_name(self, sheet_id: Optional[int]) -> str:
        if sheet_id is None:
            return next(iter(self.sheets), "")
        row = self.conn.execute("SELECT name FROM calc_sheet WHERE id=?",
                                (sheet_id,)).fetchone()
        return row["name"] if row else next(iter(self.sheets), "")

    # ---------- 对外 ----------
    def cur_sheet(self) -> str:
        return self._engine.cur_sheet

    def cell_value(self, sheet: str, row0: int, col0: int):
        return self._engine.cell_value(sheet, row0, col0)

    def evaluate_in_cur(self, raw: str):
        return self._engine.evaluate(raw)

    def sheet_grid(self, sheet: Optional[str] = None):
        """整表二维值（显示用）：外层=行，内层=列；越界/空格→0 或 ''。"""
        name = sheet or self.cur_sheet()
        content = self.sheets.get(name) or {}
        rows, cols = int(content.get("rows", 0)), int(content.get("cols", 0))
        grid = []
        for r in range(rows):
            line = []
            for c in range(cols):
                v = self.cell_value(name, r, c)
                line.append(v)
            grid.append(line)
        return grid

    # ---------- 自定义指标求值（B 层） ----------
    def indicator_value(self, person: str, indicator: str, year: int,
                        month: Optional[int]) -> float:
        key = (person, indicator, year, month)
        if key in self._ind_cache:
            return self._ind_cache[key]
        if indicator in self._ind_active:
            raise CalcDataError(f"指标循环引用：{indicator}")
        definition = self.indicators.get(indicator)
        if definition is None:
            raise CalcDataError(f"未知指标：{indicator}")
        self._ind_active.add(indicator)
        try:
            formula = self._bind_definition(definition, person, year, month)
            v = self._engine.evaluate(formula)
            if isinstance(v, ErrVal):
                raise CalcDataError(f"指标 {indicator} 求值失败：{v.code} {v.msg}")
            num = float(v) if not isinstance(v, str) else _num_or_fail(v, indicator)
            self._ind_cache[key] = round(num, 2)
            return self._ind_cache[key]
        finally:
            self._ind_active.discard(indicator)

    @staticmethod
    def _bind_definition(definition: str, person: str, year: int,
                         month: Optional[int]) -> str:
        """$职工/$年/$月 占位替换为字面量（$月 缺省→"全年"）。"""
        person_lit = '"' + person.replace('"', '""') + '"'

        def rep(m):
            k = m.group(1)
            if k == "职工":
                return person_lit
            if k == "年":
                return str(int(year))
            return str(int(month)) if month is not None else '"全年"'

        return _PLACEHOLDER.sub(rep, definition)

    def close(self):
        if self._own:
            self.conn.close()


def _num_or_fail(v: str, indicator: str) -> float:
    try:
        return float(v)
    except ValueError:
        raise CalcDataError(f"指标 {indicator} 结果不是数值：{v!r}")


class _GridProvider(CellProvider):
    """CalcEvaluator 内部的 CellProvider 实现（self.owner 指回编排层）。"""

    def __init__(self, owner: CalcEvaluator):
        self.owner = owner

    # ---- 网格 ----
    def raw_cell(self, sheet: str, row0: int, col0: int):
        content = self.owner.sheets.get(sheet)
        if not content:
            return "", "blank"
        cell = (content.get("cells") or {}).get(f"{row0},{col0}")
        if not cell:
            return "", "blank"
        raw = cell.get("raw")
        kind = cell.get("kind") or classify_cell(raw)
        return raw, kind

    def has_sheet(self, sheet: str) -> bool:
        return sheet in self.owner.sheets

    # ---- A 层参数 ----
    def param(self, sheet: str, name: str):
        content = self.owner.sheets.get(sheet) or {}
        return (content.get("params") or {}).get(name)

    # ---- DATA：内置 → 自定义 ----
    def data(self, person: str, indicator: str, year: int, month: Optional[int]) -> float:
        if indicator in self.owner.indicators:
            return self.owner.indicator_value(person, indicator, year, month)
        return self.owner.calc_data.get(person, indicator, year, month)
