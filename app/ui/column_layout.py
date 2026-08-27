"""列布局管理器：显示/隐藏、冻结(常驻保护列)、顺序、列宽 四态统一管理 + 严格填充算法。

存储：QSettings（HKCU\\Software\\lawfirm_app\\lawfirm_app），键 colstate/{page}/{name}。
- 列身份用「稳定标题字符串」作 key（各视图表头标题在代码中固定，可作唯一标识）。
- 冻结语义：常驻保护列——置最左、缩窗时优先不缩、永不被自动隐藏。不引入横向滚动。
- 填充算法（严格 D 版）：窗体变宽时优先把「未显全列」撑到内容宽（封顶 INVOICE_W），
  余量再按当前宽比例分给各列；窗体变窄时优先缩非冻结列，冻结列最后才缩（下限 MIN_W）。
- 列宽两态：auto（参与比例填充）/ fixed（用户手动拖过，锁定精确值，填充时排除它、其余吸收剩余）。

注：原 column_state.py（仅存列宽）已被本模块取代；table_view 不再 import 它。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHeaderView, QTableWidget

_ORG = "lawfirm_app"
_APP = "lawfirm_app"
INVOICE_SAMPLE = "25332000000012014331"   # 20 位发票号
MIN_W = 48          # 单列最小宽（逻辑像素）
SPIN_MAX = 1600     # 手动宽上限


def invoice_width(header: QHeaderView) -> int:
    """最长列宽上限 = 一个完整发票号码的显示宽度（+ 左右内边距）。"""
    fm = QFontMetrics(header.font())
    return fm.horizontalAdvance(INVOICE_SAMPLE) + 16


class ColumnLayoutManager:
    def __init__(self, page: str, name: str = "main") -> None:
        self.page = page
        self.name = name
        self._s = QSettings(_ORG, _APP)

    # ---------- 存储 ----------
    def _key(self) -> str:
        return f"colstate/{self.page}/{self.name}"

    def load(self, default_keys: List[str]) -> dict:
        """读取状态；无存档或结构不符则返回默认。"""
        state = self._default(default_keys)
        raw = self._s.value(self._key())
        if not raw:
            return state
        try:
            saved = json.loads(raw)
        except Exception:  # noqa: BLE001
            return state
        saved_order = [k for k in saved.get("order", []) if k in default_keys]
        for k in default_keys:
            if k not in saved_order:
                saved_order.append(k)
        state["order"] = saved_order
        sv = saved.get("visible", {})
        sf = saved.get("frozen", {})
        sw = saved.get("widths", {})
        for k in default_keys:
            if k in sv:
                state["visible"][k] = bool(sv[k])
            if k in sf:
                state["frozen"][k] = bool(sf[k])
            if k in sw and isinstance(sw[k], (int, float)) and sw[k] > 0:
                state["widths"][k] = int(sw[k])
        return state

    @staticmethod
    def _default(keys: List[str]) -> dict:
        return {
            "order": list(keys),
            "visible": {k: True for k in keys},
            "frozen": {k: False for k in keys},
            "widths": {},
        }

    def save(self, state: dict) -> None:
        self._s.setValue(self._key(), json.dumps(state, ensure_ascii=False))

    def reset(self) -> None:
        self._s.remove(self._key())

    # ---------- 填充算法（严格 D 版） ----------
    @staticmethod
    def compute_fill(viewport_w: int, cols: List[dict]) -> Dict[str, int]:
        """cols: [{key, content_w, frozen, fixed(None|px), visible}]
        返回 {key: 最终宽}（仅 visible 列）。"""
        vis = [c for c in cols if c.get("visible", True)]
        if not vis:
            return {}
        for c in vis:
            c["w"] = c["fixed"] if c.get("fixed") is not None else c["content_w"]
        auto = [c for c in vis if c.get("fixed") is None]
        W = sum(c["w"] for c in vis)

        if viewport_w >= W:
            extra = float(viewport_w - W)
            # Phase1：优先喂未显全列（w < content_w）
            guard = 0
            while extra > 0.5 and guard < 8:
                guard += 1
                trunc = [c for c in auto if c["w"] < c["content_w"] - 0.5]
                if not trunc:
                    break
                deficit = sum(c["content_w"] - c["w"] for c in trunc)
                if deficit <= 0:
                    break
                give = min(extra, deficit)
                for c in trunc:
                    c["w"] += give * (c["content_w"] - c["w"]) / deficit
                extra -= give
            # Phase2：余量按当前宽比例分给 auto
            if extra > 0.5 and auto:
                tot = sum(c["w"] for c in auto) or 1
                for c in auto:
                    c["w"] += extra * c["w"] / tot
        else:
            deficit = float(W - viewport_w)
            nf = [c for c in vis if not c.get("frozen")]
            nfW = sum(c["w"] for c in nf)
            if nf and deficit > 0:
                take = min(deficit, max(0.0, nfW - len(nf) * MIN_W))
                if take > 0:
                    for c in nf:
                        c["w"] = max(MIN_W, c["w"] - take * c["w"] / nfW)
                    deficit -= take
            if deficit > 0:
                allc = vis
                allW = sum(c["w"] for c in allc)
                if allW > 0:
                    take = min(deficit, max(0.0, allW - len(allc) * MIN_W))
                    for c in allc:
                        c["w"] = max(MIN_W, c["w"] - take * c["w"] / allW)
        return {c["key"]: int(round(c["w"])) for c in vis}

    # ---------- 测量 + 应用 ----------
    def measure_content_widths(self, table: QTableWidget, keys: List[str]) -> List[int]:
        """临时按内容撑开（屏蔽信号），返回每逻辑列的内容宽（封顶 INVOICE_W）。"""
        hdr = table.horizontalHeader()
        inv_w = invoice_width(hdr)
        hdr.blockSignals(True)
        table.resizeColumnsToContents()
        out = [min(table.columnWidth(c), inv_w) for c in range(table.columnCount())]
        hdr.blockSignals(False)
        return out

    def apply(self, table: QTableWidget, keys: List[str], content_w: List[int],
              viewport_w: Optional[int] = None, state: Optional[dict] = None) -> None:
        """重排(视觉序) + 显隐 + 定宽 + 填充。

        keys: 逻辑列顺序对应的稳定 key 列表（= 各视图 self.columns）。
        content_w: 与 keys 对齐的内容宽（已封顶 INVOICE_W）。
        """
        if table.columnCount() == 0:
            return
        if state is None:
            state = self.load(keys)
        hdr = table.horizontalHeader()
        inv_w = invoice_width(hdr)

        hdr.blockSignals(True)
        try:
            # 1) 显隐（按逻辑列；setColumnHidden 属于 QTableWidget，非 QHeaderView）
            for c, k in enumerate(keys):
                table.setColumnHidden(c, not state["visible"].get(k, True))
            # 2) 重排视觉序（冻结列已在前，由对话框保证；此处直接用 order）
            desired = [k for k in state["order"] if k in keys]
            for k in keys:
                if k not in desired:
                    desired.append(k)

            def visual_of(k: str) -> int:
                for v in range(table.columnCount()):
                    if keys[hdr.logicalIndex(v)] == k:
                        return v
                return -1

            for _ in range(table.columnCount() + 2):
                ok = True
                for pos, k in enumerate(desired):
                    if visual_of(k) != pos:
                        cur = visual_of(k)
                        if cur >= 0:
                            hdr.moveSection(cur, pos)
                        ok = False
                        break
                if ok:
                    break
            # 3) 填充宽度
            if viewport_w is None:
                viewport_w = table.viewport().width()
            if viewport_w and viewport_w > 0:
                cols = []
                for c, k in enumerate(keys):
                    if not state["visible"].get(k, True):
                        continue
                    cols.append({
                        "key": k,
                        "content_w": min(content_w[c], inv_w),
                        "frozen": state["frozen"].get(k, False),
                        "fixed": state["widths"].get(k),
                        "visible": True,
                    })
                widths = self.compute_fill(viewport_w, cols)
                for c, k in enumerate(keys):
                    if k in widths:
                        table.setColumnWidth(c, widths[k])
                hdr.setStretchLastSection(False)
        finally:
            hdr.blockSignals(False)
