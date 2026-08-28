"""列布局管理器：显示/隐藏、冻结(常驻保护列)、顺序、列宽 四态统一管理 + 严格填充算法。

存储：QSettings（HKCU\\Software\\lawfirm_app\\lawfirm_app），键 colstate/{page}/{name}。
- 列身份用「稳定标题字符串」作 key（各视图表头标题在代码中固定，可作唯一标识）。
- 冻结语义：常驻保护列——缩窗时优先不缩、永不被自动隐藏；冻结某列即冻结「该列及其左侧所有列」
  （冻结面板语义，不挪动列顺序）；冻结列铺偏灰底色以区分。不引入横向滚动。
- 填充算法（严格 D 版）：窗体变宽时优先把「未显全列」撑到内容宽（封顶 INVOICE_W），
  余量再按当前宽比例分给各列；窗体变窄时优先缩非冻结列，冻结列最后才缩（下限 MIN_W）。
- 列宽两态：auto（参与比例填充）/ fixed（用户手动拖过，锁定精确值，填充时排除它、其余吸收剩余）。

注：原 column_state.py（仅存列宽）已被本模块取代；table_view 不再 import 它。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QFontMetrics
from PySide6.QtWidgets import QHeaderView, QMenu, QTableWidget
from app.ui.table_features import (
    populate_filter_menu, set_frozen_columns, _FROZEN_BG,
    add_sort_actions, AccentHeaderView, _HEADER_SORT_BG, _HEADER_FROZEN_BG,
)

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
            # 仅缩小「未冻结且未手动定宽」的列；fixed（用户拖宽/双击拟合）列是用户显式设定，
            # 不应被自动缩回，否则双击自适应/expand 列宽后会被立即缩回，导致内容显示不全。
            nf = [c for c in vis if not c.get("frozen") and c.get("fixed") is None]
            nfW = sum(c["w"] for c in nf)
            if nf and deficit > 0:
                take = min(deficit, max(0.0, nfW - len(nf) * MIN_W))
                if take > 0:
                    for c in nf:
                        c["w"] = max(MIN_W, c["w"] - take * c["w"] / nfW)
                    deficit -= take
            if deficit > 0:
                # 兜底：仅剩 fixed 列也超宽时，才动 fixed 列（极少见，宁可出现横向滚动也不破坏用户设定）
                allc = [c for c in vis if c.get("fixed") is None]
                allW = sum(c["w"] for c in allc)
                if allW > 0:
                    take = min(deficit, max(0.0, allW - len(allc) * MIN_W))
                    for c in allc:
                        c["w"] = max(MIN_W, c["w"] - take * c["w"] / allW)
        return {c["key"]: int(round(c["w"])) for c in vis}

    # ---------- 测量 + 应用 ----------
    def _apply_frozen_style(self, table: QTableWidget, frozen_logical: set,
                            sorted_col: object = None) -> None:
        """统一表头底色协调器（自绘 AccentHeaderView）：

        - 排序列 → 强调色（最高优先，盖过冻结灰）
        - 冻结列 → 灰底
        - 其余   → 默认主题色

        单元格灰底仍由 TableBehaviorDelegate 绘制；本函数只管表头。
        sorted_col / frozen_logical 均为逻辑列号；循环按视觉列号遍历并换算。
        """
        set_frozen_columns(table, frozen_logical)
        hdr = table.horizontalHeader()
        if not isinstance(hdr, AccentHeaderView):
            return  # 非自绘表头：跳过底色高亮（不应发生，所有表格均已接入 AccentHeaderView）
        hdr.reset_all_colors()
        sort_l = sorted_col if isinstance(sorted_col, int) else None
        for c in range(table.columnCount()):
            logical = hdr.logicalIndex(c)
            if sort_l is not None and logical == sort_l:
                hdr.set_section_color(logical, _HEADER_SORT_BG)
            elif logical in frozen_logical:
                hdr.set_section_color(logical, _HEADER_FROZEN_BG)

    def measure_content_widths(self, table: QTableWidget, keys: List[str]) -> List[int]:
        """临时按内容撑开（屏蔽信号），返回每逻辑列的内容宽（封顶 INVOICE_W）。
        隐藏列会临时显示以便正确测量其理想宽，测量后恢复原状。
        """
        hdr = table.horizontalHeader()
        inv_w = invoice_width(hdr)
        hidden = [c for c in range(table.columnCount()) if table.isColumnHidden(c)]
        for c in hidden:
            table.setColumnHidden(c, False)
        hdr.blockSignals(True)
        table.resizeColumnsToContents()
        out = [min(table.columnWidth(c), inv_w) for c in range(table.columnCount())]
        hdr.blockSignals(False)
        for c in hidden:
            table.setColumnHidden(c, True)
        return out

    def apply(self, table: QTableWidget, keys: List[str], content_w: List[int],
              viewport_w: Optional[int] = None, state: Optional[dict] = None,
              reorder: bool = True, sorted_col: object = None) -> None:
        """重排(视觉序) + 显隐 + 定宽 + 填充。

        keys: 逻辑列顺序对应的稳定 key 列表（= 各视图 self.columns）。
        content_w: 与 keys 对齐的内容宽（已封顶 INVOICE_W）。
        reorder: False 时跳过视觉重排（用于内置冻结列的首列冻结表，避免打乱冻结顺序）。
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
            # 2) 重排视觉序（冻结列不再挪到最左，按 order 原样排列即可）
            if reorder:
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
            # 4) 冻结列视觉标记：表头铺灰底 + 委托灰底（偏灰）；排序列叠加强调色
            frozen_logical = {c for c, k in enumerate(keys) if state["frozen"].get(k, False)}
            self._apply_frozen_style(table, frozen_logical, sorted_col)
        finally:
            hdr.blockSignals(False)


class TableColumnLayout(QObject):
    """通用列布局控制器：把四态布局应用到任意 QTableWidget 显示表格。

    用法（在视图构造中，数据表格建好、install_common_features/install_header_filter 之后）：
        from app.ui.column_layout import install_column_layout
        self._col = install_column_layout(self.table, "staff", "main")
    在每次渲染数据后调用：
        self._col.apply()
    右键表头即可「列设置…」（若已装按列筛选，则同一菜单含「按列筛选…」，不冲突）。
    """

    def __init__(self, table: QTableWidget, page: str, name: str = "main",
                 movable: bool = True) -> None:
        super().__init__(table)
        self.table = table
        self.mgr = ColumnLayoutManager(page, name)
        self.content_w: Optional[List[int]] = None
        self._filter_slot = None
        self._tf_filter = None
        self._applying = False
        self._movable = movable
        self._win = None  # 顶层窗口（用于监听最大化/还原，触发比例重填）
        self._sort_cb = None  # 右键「升序/降序」排序回调（由视图注册）
        self._sort_col = -1   # 当前排序列（逻辑列号，-1=未排序），供表头底色高亮

    # ---------- 入口 ----------
    def install(self) -> "TableColumnLayout":
        hdr = self.table.horizontalHeader()
        if self._movable:
            hdr.setSectionsMovable(True)
            hdr.sectionMoved.connect(self._on_moved)
        hdr.sectionResized.connect(self._on_resized)
        self.table.installEventFilter(self)
        # 合并右键菜单：若已装按列筛选，则接管其连接，并入同一菜单（避免两个弹窗）
        slot = getattr(hdr, "_tf_filter_slot", None)
        if slot is not None:
            try:
                hdr.customContextMenuRequested.disconnect(slot)
            except Exception:  # noqa: BLE001
                pass
            hdr._tf_filter_installed = False
            self._filter_slot = slot
            self._tf_filter = getattr(hdr, "_tf_filter_tf", None)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._on_context_menu)
        return self

    def _keys(self) -> List[str]:
        out = []
        for c in range(self.table.columnCount()):
            it = self.table.horizontalHeaderItem(c)
            t = it.text() if it else f"col{c}"
            # 剥离排序箭头后缀，避免「 ▲/▼」被当作列 key 污染列顺序/宽度状态
            t = t.replace(" ▲", "").replace(" ▼", "")
            out.append(t)
        return out

    def set_sort_callback(self, cb) -> None:
        """注册表头右键「升序/降序」排序回调 on_sort(logical, asc)。不注册则右键菜单不出现排序项。"""
        self._sort_cb = cb

    def set_sort_col(self, col: int) -> None:
        """记录当前排序列（逻辑列号），表头协调器据此铺强调色；-1 表示未排序。"""
        self._sort_col = col

    def apply(self, remeasure: bool = True) -> None:
        """渲染后调用：测量(可选)并应用布局。remeasure=False 时仅用缓存内容宽重填（拖宽/缩放时用）。"""
        self._ensure_win()
        if self.table.columnCount() == 0:
            return
        keys = self._keys()
        if remeasure or self.content_w is None or len(self.content_w) != len(keys):
            self.content_w = self.mgr.measure_content_widths(self.table, keys)
        self._applying = True
        try:
            self.mgr.apply(self.table, keys, self.content_w, sorted_col=self._sort_col)
        finally:
            self._applying = False

    def open_settings(self) -> None:
        from app.ui.column_settings_dialog import open_column_settings  # 延迟导入，避免与 column_layout 的循环依赖
        keys = self._keys()
        if self.content_w is None or len(self.content_w) != len(keys):
            self.content_w = self.mgr.measure_content_widths(self.table, keys)
        state = self.mgr.load(keys)
        hdr = self.table.horizontalHeader()
        state["order"] = [keys[hdr.logicalIndex(v)] for v in range(self.table.columnCount())]
        parent = self.table.window()
        if open_column_settings(parent, self.mgr, keys, state, self.content_w, movable=self._movable):
            # 应用后重新测量（显隐/顺序/宽度变了），再填充
            self.content_w = self.mgr.measure_content_widths(self.table, keys)
            self._applying = True
            try:
                self.mgr.apply(self.table, keys, self.content_w, reorder=self._movable)
            finally:
                self._applying = False

    # ---------- 信号 ----------
    def _on_moved(self, _logical: int, _old: int, _new: int) -> None:
        keys = self._keys()
        if not keys:
            return
        hdr = self.table.horizontalHeader()
        order = [keys[hdr.logicalIndex(v)] for v in range(self.table.columnCount())]
        state = self.mgr.load(keys)
        state["order"] = order
        self.mgr.save(state)

    def _on_resized(self, logical: int, _old: int, new_w: int) -> None:
        """用户手动调列宽：记为 fixed 宽度（程序化重填触发的 resize 由 _applying 屏蔽，不重复标记）。"""
        if getattr(self, "_applying", False):
            return
        if new_w <= 0 or self.content_w is None:
            return
        keys = self._keys()
        if 0 <= logical < len(keys):
            state = self.mgr.load(keys)
            state["widths"][keys[logical]] = int(new_w)
            self.mgr.save(state)
            self.apply(remeasure=False)

    def _on_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self.table)
        act_settings = menu.addAction("列设置…")
        # 按列筛选：直接把 3 个操作摊平进同一菜单（不再嵌套「按列筛选…」子菜单），
        # 并按条件显隐（该列未筛选则不显示「清除此列筛选」；无任何列筛选则不显示「清除全部筛选」）。
        if self._tf_filter is not None:
            menu.addSeparator()
            populate_filter_menu(menu, self._tf_filter, self.table, pos)
        hdr = self.table.horizontalHeader()
        logical = hdr.logicalIndexAt(pos)
        keys = self._keys()
        act_freeze = None
        if self._movable and 0 <= logical < len(keys):
            state = self.mgr.load(keys)
            frozen = state["frozen"].get(keys[logical], False)
            menu.addSeparator()
            act_freeze = menu.addAction("解冻此列" if frozen else "冻结此列")
        if self._sort_cb is not None and 0 <= logical < len(keys):
            add_sort_actions(menu, self.table, pos, self._sort_cb)
        action = menu.exec(self.table.mapToGlobal(pos))
        if action is None:
            return
        if action == act_settings:
            self.open_settings()
        elif action is act_freeze and act_freeze is not None:
            self._toggle_freeze(keys, logical)

    def _toggle_freeze(self, keys: List[str], logical: int) -> None:
        """右键冻结/解冻（冻结面板语义）：

        - 冻结某列：该列及其「左侧所有列」（当前视觉序）一并冻结；
        - 解冻某列：仅解除该列自身的冻结（其余列保持原冻结状态）。
        不再把列挪到最左（满足「冻结列留在原位」的需求）。
        """
        key = keys[logical]
        state = self.mgr.load(keys)
        now = not state["frozen"].get(key, False)
        hdr = self.table.horizontalHeader()
        visual_order = [keys[hdr.logicalIndex(v)] for v in range(self.table.columnCount())]
        try:
            idx = visual_order.index(key)
        except ValueError:
            idx = -1
        if now:
            for k in visual_order[:idx + 1]:
                state["frozen"][k] = True
        else:
            state["frozen"][key] = False
        self.mgr.save(state)
        self.content_w = self.mgr.measure_content_widths(self.table, keys)
        self._applying = True
        try:
            self.mgr.apply(self.table, keys, self.content_w, reorder=self._movable)
        finally:
            self._applying = False

    # ---------- 事件过滤（窗体缩放 / 最大化还原时重填） ----------
    def _ensure_win(self) -> None:
        """懒绑定顶层窗口，监听其状态变化（最大化/还原）以触发比例重填。"""
        if self._win is None:
            win = self.table.window()
            if win is not None:
                self._win = win
                win.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        self._ensure_win()  # 视图构造早于入窗，事件触发时再补绑窗口
        if obj is self.table and event.type() == QEvent.Type.Resize:
            if self.content_w is not None and self.table.columnCount():
                self.apply(remeasure=False)
        elif obj is self._win and event.type() == QEvent.Type.WindowStateChange:
            # 最大化/还原：延迟一帧，等布局稳定后再按比例重填（已显全列除外）
            if self.content_w is not None and self.table.columnCount():
                QTimer.singleShot(0, lambda: self.apply(remeasure=False))
        return super().eventFilter(obj, event)


def install_column_layout(table: QTableWidget, page: str, name: str = "main",
                          movable: bool = True) -> TableColumnLayout:
    """便捷入口：创建并安装列布局控制器（须在 install_header_filter 之后调用）。

    movable=False 时禁用列重排（用于内置冻结列的首列冻结表，避免拖动打乱冻结顺序）。
    """
    return TableColumnLayout(table, page, name, movable=movable).install()
