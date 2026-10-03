"""列表式报表的**常驻人员筛选控件**（勾选列表 + 全选 + 搜索），非弹窗对话框。

用在年度结算表 / 开票收入表两个 tab 的工具栏里：勾选后**预览与导出同时生效**
（两个导出按钮都按当前勾选），无需再点一次弹窗确认。

三处视觉区分（缺一不可，否则用户不知道自己在筛）：
  1. 按钮文案：`人员：全部（12）` / `人员：3/12 · 筛选中` / `人员：0/12 · 筛选中`
  2. 按钮高亮：筛选生效时 border:accent_blue + background:accent_blue_bg（走 style.palette）
  3. 「恢复全选」按钮：仅在筛选生效时可见

防递归（刷新名单 → selection_changed → 又刷新 → 又刷名单）：
  - 内部 `_syncing` 闸门 + blockSignals：重建勾选列表/回写勾选状态时**绝不自触发**回调；
  - set_persons 只在「有效勾选真的变了」时才发 selection_changed（保留勾选不刷屏）；
  - 宿主视图侧另有成对闸门 `_si_filter_syncing` / `_ii_filter_syncing`（见 settlement_view）。

勾选状态存 QSettings（不是 prefs.json —— 后者全应用共享、多处读-改-整写，并发易互相覆盖）。
空集不写 key（remove）：读不到 = 全选（fail-safe：宁不过滤也不静默出空表）。
落盘的是**勾选意图**（_intent，跨年月稳定）而不是当月交集——否则用户切到别的月份
看一眼，勾选连同落盘记录一起被抹掉，切回来也恢复不了（意图被当月事实吞掉）。

颜色一律走 style.palette()、几何一律走 scale.px()（tests/test_skin_contract.py 硬门禁）。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget,
)

from app.ui import scale, style
from app.ui.widgets import CaptionLabel, LineEdit, PushButton

# 面板尺寸（走 scale.px，跟字号档位缩放）
PANEL_WIDTH = 200
PANEL_MAX_HEIGHT = 260
ROW_HEIGHT = 26


def _active_btn_qss() -> str:
    """筛选生效时按钮的高亮样式（色号全来自调色板，模块内零 hex）。"""
    p = style.palette()
    return (
        "QPushButton { background: %(bg)s; color: %(fg)s;"
        " border: 1px solid %(bd)s; border-radius: 6px;"
        " padding: 5px 10px; min-height: 18px; }"
        % {"bg": p["accent_blue_bg"], "fg": p["accent_blue"], "bd": p["accent_blue"]}
    )


def _panel_qss() -> str:
    """勾选面板样式（白底 + 边框 + 圆角，视觉与既有 _combo_qss 同族）。"""
    p = style.palette()
    return (
        "QFrame { background: %(bg)s; border: 1px solid %(bd)s; border-radius: 8px; }"
        "QListWidget { background: %(bg)s; border: none; outline: none; }"
        "QListWidget::item { padding: 2px 4px; border-radius: 4px; }"
        "QListWidget::item:selected { background: %(sel)s; color: %(tx)s; }"
        "QLineEdit { background: %(bg)s; border: 1px solid %(bd)s; border-radius: 6px;"
        " padding: 4px 8px; }"
        % {"bg": p["bg"], "bd": p["border_2"], "sel": p["bg_hover"], "tx": p["text"]}
    )


class _PersonCheckPanel(QFrame):
    """勾选面板：搜索框 + 全选 + 勾选列表。浮在按钮下方，点外面自动关（Qt.Popup）。"""

    def __init__(self, owner: "PersonFilterWidget") -> None:
        super().__init__(None, Qt.WindowType.Popup)
        self._owner = owner
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(_panel_qss())
        lay = QVBoxLayout(self)
        m = scale.px(8)
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(scale.px(6))

        self.search = LineEdit()
        self.search.setPlaceholderText("搜索人员")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_search)
        lay.addWidget(self.search)

        self.select_all = QCheckBox("全选")
        self.select_all.toggled.connect(self._on_select_all)
        lay.addWidget(self.select_all)

        self.list = QListWidget()
        self.list.setFixedWidth(scale.px(PANEL_WIDTH))
        self.list.setMaximumHeight(scale.px(PANEL_MAX_HEIGHT))
        self.list.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.list)

        hint = CaptionLabel("勾选即时生效；预览与导出同步")
        lay.addWidget(hint)

    # ---- 名单装载（blockSignals 防止自触发回调）----
    def load(self, persons: list, checked: set) -> None:
        """重建勾选列表。persons 为当月名单（决定顺序），checked 为当前勾选集合。"""
        for w in (self.list, self.select_all):
            w.blockSignals(True)
        self.list.clear()
        for name in persons:
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            it.setCheckState(Qt.CheckState.Checked if name in checked
                             else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._sync_select_all()
        self.select_all.blockSignals(False)
        self._apply_search(self.search.text())

    def checked_names(self) -> list:
        """当前可见勾选（保持名单顺序）。"""
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def popup_under(self, anchor: QWidget) -> None:
        """在 anchor 下方弹出（宽度跟随按钮，但不低于面板最小宽）。"""
        self.adjustSize()
        w = max(self.width(), anchor.width())
        h = min(self.sizeHint().height(), self.height())
        screen = anchor.screen()
        geo = anchor.mapToGlobal(QPoint(0, anchor.height() + scale.px(4)))
        if screen is not None:
            avail = screen.availableGeometry()
            if geo.y() + h > avail.bottom():
                geo = anchor.mapToGlobal(QPoint(0, -h - scale.px(4)))
            geo.setX(min(geo.x(), avail.right() - w))
        self.setGeometry(geo.x(), geo.y(), w, h)
        self.show()
        self.search.setFocus()

    # ---- 内部槽 ----
    def _apply_search(self, text: str) -> None:
        """按搜索词隐藏不匹配行（不改勾选状态）。"""
        key = (text or "").strip()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(key) and key not in it.text())

    def _sync_select_all(self) -> None:
        """全选框勾选态 = 可见行是否全中（部分选中时保持未勾，避免「点一下全清」的陷阱）。"""
        visible = [self.list.item(i) for i in range(self.list.count())
                   if not self.list.item(i).isHidden()]
        all_checked = bool(visible) and all(
            it.checkState() == Qt.CheckState.Checked for it in visible)
        self.select_all.blockSignals(True)
        self.select_all.setChecked(all_checked)
        self.select_all.blockSignals(False)

    def _on_select_all(self, on: bool) -> None:
        """全选/全不选：只作用于**当前搜索可见**的行（隐藏行不动）。"""
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.isHidden():
                continue
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self._owner._commit(self.checked_names())

    def _on_item_changed(self, _item) -> None:
        self._sync_select_all()
        self._owner._commit(self.checked_names())


class PersonFilterWidget(QWidget):
    """工具栏常驻人员筛选：按钮（文案 + 高亮）+「恢复全选」+ 悬浮勾选面板。

    对外接口：
      set_persons(persons)      名单刷新（随年份/月份变）。**不改勾选意图**
      selected_persons()        -> list[str] | None（None = 未筛选 = 全选；空 list = 勾了 0 人）
      is_filtering()            -> bool（有效勾选 != 全选名单，含「0 人勾选」）
      reset_selection()         恢复全选（等价于点「恢复全选」按钮）
      selection_changed(list)   勾选变化信号（宿主据此立即刷新预览）

    状态分层（本控件最核心的不变量，勿混用）：
      _persons = 当月**事实上**有谁进报表（随年月变，含 hire_month 过滤）
      _intent  = 用户**想看**谁（跨年月稳定，唯一落盘的东西）
      _checked = _intent ∩ _persons（派生量，「这个月实际能显示谁」）
    被动刷新（切年月）只更新 _persons 与派生的 _checked；只有用户亲手勾选
    （_commit）或点「恢复全选」（reset_selection）才改写 _intent。
    """

    selection_changed = Signal(list)

    def __init__(self, settings_key: str, parent=None) -> None:
        super().__init__(parent)
        self._key = settings_key
        self._persons: list[str] = []
        self._intent: list[str] = []   # 用户意图（唯一落盘的东西，跨年月稳定）
        self._checked: list[str] = []   # 派生量 = _intent ∩ _persons
        self._initialized = False   # 是否已装载过名单（决定要不要读 QSettings）
        self._syncing = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(scale.px(8))

        self.btn = PushButton("人员：全部（0）")
        self.btn.setToolTip("点击勾选要显示/导出的人员；勾选即时作用于预览与导出")
        self.btn.clicked.connect(self._toggle_panel)
        lay.addWidget(self.btn)

        self.btn_reset = PushButton("恢复全选")
        self.btn_reset.setToolTip("清除人员勾选，恢复为全部人员")
        self.btn_reset.clicked.connect(self.reset_selection)
        lay.addWidget(self.btn_reset)

        self._panel = _PersonCheckPanel(self)
        self._refresh_visual()

    # ---- 名单与勾选 ----
    def set_persons(self, persons: list) -> None:
        """刷新名单（人员随年份/月份变化）。**只被动跟随，不改用户的勾选意图。**

        这里必须区分两个不同的东西：
        - ``_persons`` = 当月**事实上**有谁进报表（随年份/月份变，含 hire_month 过滤）；
        - ``_intent``  = 用户**想看**谁（跨年月稳定，是持久化的东西）。

        故本方法**永不修改 _intent**，只重算派生的有效勾选 `_checked`（= 意图 ∩ 当月名单）。
        早前实现在这里做交集回写 / 名单为空时清空，后果是用户只是切到别的月份
        「看看有没有人」，勾选就连同 QSettings key 一起被抹掉，切回来也恢复不了：

        - 「切到无人入职的年份」→ 名单为空 → 旧实现 ``_checked = []`` → 落盘丢失；
        - 「只勾 3 月入职的人 → 切到 1 月」→ 名单里没他 → 交集为空 → 意图被吞。

        两种场景现在都靠「意图留在 _intent 里」解决：切回有人的月份时勾选原样恢复。
        唯一会改写意图的是用户的显式操作（面板勾选 / 恢复全选，见 _commit）。
        """
        self._persons = [p for p in (persons or [])]
        if self._persons and not self._initialized:
            # 首次装载：还没读过QSettings，此时才做一次「全选 or 读回历史勾选」
            restored = self._load_setting()
            self._intent = list(self._persons) if restored is None else [
                p for p in self._persons if p in set(restored)]
        self._initialized = True
        self._sync_derived()
        self._panel.load(self._persons, set(self._checked))
        self._save_setting()
        self._refresh_visual()

    def _sync_derived(self) -> None:
        """_checked 由 _intent ∩ _persons 派生（名单/意图任一变化后都要调）。"""
        keep = set(self._intent)
        self._checked = [p for p in self._persons if p in keep]

    def selected_persons(self):
        """当前生效的勾选：None = 未筛选（全选）；list = 勾选人员（可能为空 = 0 人勾选）。

        名单为空（当月没人）时返回 None：此时无论勾选谁都得到空表，不存在过滤语义，
        且能让界面显示「全部（0）」而不是荒谬的「1/0 · 筛选中」。
        """
        if not self.is_filtering():
            return None
        return list(self._checked)

    def is_filtering(self) -> bool:
        """筛选是否生效：勾选集合与名单不一致（0 人勾选也算生效，必须显式表达）。

        名单为空时恒为 False——没东西可筛，不该显示成「筛选中」。
        """
        if not self._persons:
            return False
        return sorted(self._checked) != sorted(self._persons)

    def reset_selection(self) -> None:
        """恢复全选（= 把意图重置为当月全选；「恢复全选」按钮的语义）。"""
        if not self._persons:
            return
        if not self.is_filtering():
            self._intent = list(self._persons)
            self._sync_derived()
            self._save_setting()
            self._refresh_visual()
            return
        self._syncing = True
        try:
            self._intent = list(self._persons)
            self._sync_derived()
        finally:
            self._syncing = False
        self._panel.load(self._persons, set(self._checked))
        self._save_setting()
        self._refresh_visual()
        self.selection_changed.emit(list(self._checked))

    def button_text(self) -> str:
        return self.btn.text()

    def total_count(self) -> int:
        """名单总人数（当月报表口径）。"""
        return len(self._persons)

    def checked_count(self) -> int:
        """已勾选人数（= 0 时是「0 人勾选」，导出按钮须置灰）。"""
        return len(self._checked)

    def export_note(self) -> str:
        """导出日志/完成弹窗的附注文案；未筛选时返回空串。"""
        if not self.is_filtering():
            return ""
        return f"（按勾选 {self.checked_count()}/{self.total_count()} 人导出）"

    # ---- 内部 ----
    def _commit(self, checked: list) -> None:
        """面板勾选变化 → 改写**意图** + 存 QSettings + 发信号（重建名单期间一律忽略）。

        这是唯一会改写 _intent 的入口（reset_selection 是另一个）：用户在看得到人的
        面板上亲手勾选，意图就是「以这次勾选为准」，并**丢弃**那些当月不在名单里的
        旧意图（否则「切到 1 月→ 只勾甲 → 切回 3 月」会突然冒出乙，用户无法解释）。
        """
        if self._syncing:
            return
        if not self._persons:
            # 当月没人可勾：面板是空的，此时任何 _commit 都不该改写意图
            #（否则「只是切到空年份看看」就会把用户意图清掉，见 set_persons 注释）
            return
        checked = [c for c in (checked or []) if c in set(self._persons)]
        if sorted(checked) == sorted(self._checked):
            return
        self._intent = list(checked)
        self._sync_derived()
        self._save_setting()
        self._refresh_visual()
        self.selection_changed.emit(list(self._checked))

    def _toggle_panel(self) -> None:
        if self._panel.isVisible():
            self._panel.close()
        else:
            self._panel.load(self._persons, set(self._checked))
            self._panel.popup_under(self.btn)

    def _settings(self):
        from PySide6.QtCore import QSettings
        return QSettings("lawfirm_app", "lawfirm_app")

    def _load_setting(self):
        """读回勾选记录：读不到（无 key / 类型异常）→ None（调用方按全选处理）。"""
        try:
            v = self._settings().value(self._key)
        except Exception:  # noqa: BLE001
            return None
        if v is None:
            return None
        if isinstance(v, str):
            v = [v] if v else []
        if not isinstance(v, (list, tuple)):
            return None
        out = [str(x) for x in v if str(x)]
        return out or None

    def _save_setting(self) -> None:
        """落盘**意图**（不是当月派生出的有效勾选）。

        落盘的是 _intent：它是跨年月稳定的「用户想看谁」，与当月名单无关。
        - 「意图 = 空」或「意图 = 当月全选」→ remove（无筛选意图就不留 key，
          读不到 = 全选，fail-safe：宁不过滤也不静默出空表）。
        - 意图是当月名单的**真子集**（哪怕交集为空，如「只勾 3 月入职的人」看 1 月）
          → 照写不误。这正是修复「切到别的月份把勾选抹掉」的关键：写的是意图而非交集，
          切回 3 月时才能原样恢复。
        """
        s = self._settings()
        intent = list(self._intent)
        if intent and (not self._persons or sorted(intent) != sorted(self._persons)):
            s.setValue(self._key, intent)
        else:
            s.remove(self._key)

    def _refresh_visual(self) -> None:
        """三处视觉区分：文案 / 高亮 / 恢复全选按钮可见性。"""
        total = len(self._persons)
        n = len(self._checked)
        if self.is_filtering():
            self.btn.setText(f"人员：{n}/{total} · 筛选中")
            self.btn.setStyleSheet(_active_btn_qss())
        else:
            self.btn.setText(f"人员：全部（{total}）")
            self.btn.setStyleSheet("")
        self.btn.setToolTip(
            f"当前名单 {total} 人，已勾选 {n} 人。"
            "点击可勾选要显示/导出的人员；勾选即时作用于预览与导出")
        self.btn_reset.setVisible(self.is_filtering() and total > 0)
