# 标题栏设计方案（B 方案：动态页面标题）+ 综合检查

> 状态：已选 B，**已实现并推送**（commit `6aa0247`，含 P1/P2/P3 修复 + `tests/_smoke_titlebar.py` 11 项 offscreen 全过）。本文件为已落地的设计规格留档。
> 关联文件：`app/ui/main_window.py`（`AppTitleBar` 93–112、`select` 270–288、`_on_skin_changed` 248–257、`_build_layout` 183–215、`NAV_GROUPS` 53–90）
> 前置：无边框窗口已落地（commit `d6870cf`，基类 `FramelessWindow`，自绘 `AppTitleBar(TitleBar)`）。

---

## 0. 需求边界

- 去掉左上角品牌文字「律所开票收款统计」，**不放任何图标**（含 logo / 页面图标均不做）。
- 标题栏左侧改为**显示当前所在页面名**（如「导入台账」「员工管理」），随页面切换自动更新。
- 保留右侧最小 / 最大 / 关闭三按钮，交互继续由 `qframelesswindow` 库兜底（拖拽 / 双击最大化 / 边缘吸附）。
- 任务栏名称（`setWindowTitle`）本次**不改**，仍显示「律所开票收款统计」（如需短名见 §2.7）。

---

## 1. B 方案详细规格

### 1.1 数据源（现成，无需新增表）

页面中文名已写在 `NAV_GROUPS`：每个 `(key, 显示名)` 配对齐全。

```python
# MainWindow.__init__ 内，复用 _key_to_group 的遍历方式
self._key_to_title = {k: name for _, items in NAV_GROUPS for k, name in items}
```

### 1.2 Hook 点（唯一入口）

`select(key)` 是页面切换的**唯一入口**（`go_to_page` 也转调它），在此 hook 即可覆盖全部跳转（侧栏点击、视图内 `window().go_to_page(...)`、`showEvent` 兜底切「员工管理」等）。

```python
def select(self, key: str) -> None:
    page = self._pages.get(key)
    if page is None:
        return
    self.stack.setCurrentWidget(page)
    grp = self._key_to_group.get(key)
    if grp is not None:
        self.sidebar.activate(key, grp)
    # ★ 新增：同步标题栏
    if getattr(self, "titleBar", None) is not None:
        self.titleBar.set_page_title(self._key_to_title.get(key, ""))
```

### 1.3 `AppTitleBar` 改造

```python
class AppTitleBar(TitleBar):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self.titleLabel = QLabel("")          # 去掉固定品牌文字
        self.titleLabel.setObjectName("titleBarTitle")
        self.titleLabel.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.hBoxLayout.insertWidget(0, self.titleLabel, 1)  # stretch=1 占满左侧
        self.apply_skin()

    def set_page_title(self, name: str) -> None:
        self.titleLabel.setText(name)
        self._elide()                          # 见 1.4

    def _elide(self) -> None:
        # 预留接口：窗口 resize 时按可用宽度截断长标题（见 1.4 / §2.3）
        fm = self.titleLabel.fontMetrics()
        max_w = max(60, self.width() - 160)    # 160≈右侧三按钮+间距+padding
        self.titleLabel.setText(fm.elidedText(self.titleLabel.text(), Qt.ElideRight, max_w))
```

- 去掉第 100 行 `self.titleLabel = QLabel("律所开票收款统计")`。
- `set_page_title` 默认在 `__init__` 末或 `MainWindow.__init__` 首屏 `select("import")` 时被调用 → 首屏显示「导入台账」。

### 1.4 长标题截断（必须，见 §2.3）

- 用 `QFontMetrics.elidedText(text, Qt.ElideRight, max_w)` 做末尾省略号截断，避免窄窗口时标题压到右侧按钮。
- `max_w = 窗口宽 − 右侧按钮区(≈120) − 左 padding(12)`；在 `resizeEvent` 里重算（或 `titleLabel` 设 `setMaximumWidth` + `setElideMode`，由布局自动处理——二选一，推荐前者更可控）。
- 验收：窗口拉到 <900px 时，长名（如「经办人发票收款情况」）显示「经办人发票收…」且**不碰右侧按钮**。

### 1.5 配色（无需改）

`_on_skin_changed`（248–257）已调用 `self.titleBar.apply_skin()`；`apply_skin` 用 `style.palette()` 的 `bg`/`text`，动态标题文字同样跟随皮肤（含深色）。

---

## 2. 综合检查：发现的问题与设计方案

### 2.1 必须修（影响可用 / 美观）

| 编号 | 问题 | 设计对策 |
|---|---|---|
| **P1** | 窗口三按钮 **hover / 按下态不可见**：库默认 `TitleBarButton` 在自定义标题栏背景下 hover 反馈弱，关闭按钮 hover 无变红提示，用户易以为「不能关」。 | 在 `apply_skin` 或全局 QSS 给窗口按钮加态：`min/max` 按钮 `:hover` 浅灰底（≈ `rgba(128,128,128,0.12)`）、`:pressed` 更深；**关闭按钮 `:hover` 红底白字（`#E24B4A`）**。需先确认库默认是否已有——若无，用 `TitleBarButton` 的 objectName / `qfluentwidgets` 样式钩子自定义 QSS。 |
| **P2** | 标题栏与下方内容区**缺少分隔**，无边框下两者糊在一起，层次不清。 | 标题栏底部加 `0.5px` 分隔线：`self.setStyleSheet("...; border-bottom: 0.5px solid <palette border>")`，`border` 取自 `style.palette()` 的中性描边色（浅色≈ `#E3E1DB`，深色≈ `rgba(255,255,255,0.12)`）。 |
| **P3** | 标题过长与右侧按钮重叠（见 1.4）。 | 长标题 elide 截断，必须做。 |

### 2.2 建议修（体验 / 健壮性）

| 编号 | 问题 | 设计对策 |
|---|---|---|
| **P4** | 最大化按钮状态图标是否随「最大化 / 还原」正确切换。 | 真机验收项：库 `TitleBar` 默认应处理 `maxBtn` 图标切换；若不正确再定制。不改代码，先验收。 |
| **P5** | 深色皮肤下标题栏文字对比度。 | 校验 `style.palette()` 深色档 `text`/`bg` 对比度 **≥ 4.5:1（WCAG AA）**；标题栏文字在深色必须可读。若当前深色 token 不达标，在 `style.palette()` 修正。 |
| **P6** | `show_info` 用 `InfoBarPosition.TOP_RIGHT`（290–297），通知条可能**盖住右上角关闭按钮**。 | 真机验收：确认 `qfluentwidgets` 的 `InfoBar` 在 `FramelessWindow` 下 y 偏移是否避开标题栏（36px）。若重叠，改 `position=InfoBarPosition.TOP`（居中、不挡右上）或显式增大 offset。 |
| **P7** | 任务栏名称（待你决定）。 | 本次不改；如需短名（如「律所统计」）改 `setWindowTitle` 即可。 |
| **P8** | 可访问性。 | 验收项：三按钮 tooltip（最小化 / 最大化 / 关闭）、`Alt+F4` 关闭、`Win+↑/↓/←/→` 系统级可用；`titleLabel.setAccessibleName(当前页名)`。 |

### 2.3 与设计稿的对应关系

- 原四方案草图（A 纯留白 / B 动态页名 / C 汉堡 / D 搜索）中本次落地 **B**。
- B 的视觉：**左侧页名（13px、`Microsoft YaHei`、左对齐、padding-left 12px）+ 右侧三圆角方块按钮**，整体 36px 高、底部 0.5px 分隔线，配色随皮肤。

---

## 3. 工程师验收清单（自测 + 真机）

- [ ] 启动后标题栏显示「导入台账」（首屏 `select("import")`）。
- [ ] 点侧栏各页 / 视图内 `go_to_page` 跳转，标题栏页名实时跟随。
- [ ] 无员工首次启动（`showEvent` 兜底 `select("staff")`）标题显示「员工管理」。
- [ ] 窗口拉窄到 <900px，长页名 elide 省略号且**不压右侧按钮**。
- [ ] 切换皮肤，标题栏背景 / 文字同步；深色档文字清晰可读（对比度达标）。
- [ ] 三按钮 hover 反馈明显；**关闭按钮 hover 变红底白字**。
- [ ] 标题栏底部有 0.5px 分隔线，与内容区分层清晰。
- [ ] `show_info` 通知条**不遮挡**关闭按钮。
- [ ] 双击标题栏最大化、拖拽移动、Win 边缘吸附正常。

---

## 4. 改动文件清单（预估）

| 文件 | 改动 |
|---|---|
| `app/ui/main_window.py` | `AppTitleBar`：去固定文字 + `set_page_title` + `_elide` + 底部分隔线 + 按钮态 QSS（P1/P2/P3）；`MainWindow.__init__` 加 `_key_to_title`；`select` 加标题同步 hook；可选 `_on_skin_changed` 接按钮态。 |
| `app/ui/style.py` | 若 P5 深色对比度不达标，补 / 修正深色 `text`/`border` token。 |
| （无需） | 数据源、布局框架、皮肤切换流程均复用现有，无新增文件。 |

---

## 5. 风险与回退

- 本改动与无边框改造（`d6870cf`）同一文件，若 B 方案体验不佳：`git revert d6870cf` 会连同无边框整体回退（与方案 1→方案 2 回退路径一致）。若只想撤标题栏改动而保留无边框，用本 spec 的反向 diff 局部回退即可。
- 沙箱无法跑完整 `MainWindow`（依赖 qfluentwidgets），**所有视觉项以你本机 `run_app.bat` 真机验收为准**。
