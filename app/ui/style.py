"""Notion-like 主题（浅色）—— 全局 QSS + 皮肤注册表

设计要点：
- 视觉基线 = 类似 Notion 的极简风：暖灰侧栏、发丝线、无阴影、留白多、字体克制。
- 颜色全部参数化到 palette 字典，build_qss() 按 palette 生成 QSS，便于新增皮肤。
- SKINS 是皮肤注册表；apply_skin() 在运行时切换；当前皮肤持久化到 data/prefs.json。
- ⚠️ 2026-09-20：深色（notion_dark）与侧栏「皮肤」下拉已移除，**只保留一套浅色皮肤**；
  但皮肤切换的**机制仍完整保留**（SKINS / PALETTES / apply_skin / refresh_qss /
  load_skin_pref / save_skin_pref / available_skins）。将来要加皮肤只需三步：
    ① PALETTES 里加一份与 notion_light **同一个 key 集合**的调色板；
    ② SKINS 里加一项（key + label）；
    ③ 把侧栏的皮肤下拉恢复（或挪进「设置」页）。
  取色一律经 palette()，**不要在各视图里硬编码 QColor**，否则加第二个皮肤必漏。

尺寸全部按 app.ui.scale 的档位倍率派生（build_qss 的 s 参数）：字号、内边距、
圆角、控件最小尺寸一律乘倍率，避免「字号变了框没变」导致文字被裁或溢出。
QSS 因此**不再在模块加载时预生成**，改由 qss_for() 按当前倍率即时生成。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QColor

from app.ui import scale

PREFS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "prefs.json"

DEFAULT_SKIN = "notion_light"


# ---------------------------------------------------------------------------
# 调色板
# ---------------------------------------------------------------------------
def _light() -> dict:
    return {
        "bg": "#FFFFFF", "canvas": "#f7f6f3", "bg_side": "#f7f6f3", "bg_hover": "#efedea",
        "bg_select": "#e3e1db", "bg_table": "#fbfbfa", "border": "#e9e9e7",
        "border_2": "#DADAD7", "text": "#37352F", "text_mute": "#787774",
        "text_faint": "#9b9a97", "white": "#FFFFFF",
        "btn_bg": "#FFFFFF", "btn_border": "#DADAD7", "btn_hover": "#efedea",
        "btn_press": "#e3e1db", "grid": "#F1F1EF",
        "warn_bg": "#fdeced", "warn_fg": "#eb5757",
        # 4 色强调（蓝/红/绿/黄）+ 浅底 —— Notion Style 升级（Batch 1b 起使用）
        "accent_blue": "#2eaadc", "accent_blue_bg": "#e8f4fb",
        "accent_blue_hover": "#2491c4", "accent_blue_press": "#1d7eab",
        "accent_red": "#eb5757", "accent_red_bg": "#fdeced",
        "accent_green": "#0f7b6c", "accent_green_bg": "#e6f4f0",
        "accent_yellow": "#dfab01", "accent_yellow_bg": "#fbf3db",
        # --- 语义色 / 结构色（2026-09-20：把散落在 18 个视图里的硬编码色收口）---
        # 为什么需要它们：accent_* 四色是「状态胶囊 / 高亮」用的鲜艳色，而表格正文里的
        # 红字/绿字要更沉、更像纸张上的墨——两套色号不同，不能混用。以前各视图直接写
        # 十六进制，结果「加第二个皮肤」时必然漏掉一批（实测 72 行）。
        # 语义前景（setForeground，表格正文）
        "neg_fg": "#C0392B",           # 负数 / 差异 / 缺失 / 错误
        "pos_fg": "#1E8449",           # 已收 / 校验一致
        "amber_fg": "#B7791F",         # 疑问 / 待核对
        "info_fg": "#2C6FBB",          # 信息性强调（非判定结果）
        # 语义浅底
        "amber_bg": "#FFF3CD",         # 疑问行底色（问题行列表 / 修正面板）
        "row_sum_bg": "#F2F2F0",       # 合计 / 汇总行底色
        # 提示条（「待补录」横幅那一组淡黄）
        "notice_bg": "#FFF8E6",
        "notice_border": "#F0DFA8",
        "notice_fg": "#7A5C00",
        "notice_fg_hover": "#8F6D00",
        # 表头（自绘 AccentHeaderView，与 QSS 的 QHeaderView::section 是两套绘制路径）
        "header_sort_bg": "#E3F2FD",   # 当前排序列底色
        "header_border": "#E0E0E0",    # 表头分隔线（比 border 深一档）
        "frozen_bg": "#EAEAEA",        # 冻结列高亮底色（表头与列体共用）
        "mark_blue": "#2D7DD2",        # 排序角标（实心直角三角）
    }


# 视图里**不允许**出现十六进制颜色字面量（style.py 之外），否则加皮肤必漏。
# 契约测试 tests/test_skin_contract.py 会强制这条；确需例外时把 (文件, 片段) 加进那里
# 的 ALLOWLIST 并写明理由。


PALETTES = {"notion_light": _light()}
# 新增皮肤：在此追加一项，key 集合必须与 _light() 完全一致（见 _light 的注释与契约测试）


# ---------------------------------------------------------------------------
# QSS 生成
# ---------------------------------------------------------------------------
def build_qss(p: dict, s: float = 1.0) -> str:
    """按调色板 p 与缩放倍率 s 生成全局 QSS。

    s 取自 app.ui.scale.ratio()。所有 px 值一律走 P() 派生——字号变了，
    内边距/行高/圆角/最小尺寸同步变，任何档位下都不会出现裁切或溢出。
    """

    def P(n: float) -> str:
        return f"{max(1, int(round(n * s)))}px"

    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: {P(13)};
    color: {p['text']};
}}
QMainWindow, QWidget#pageArea {{ background: {p['canvas']}; }}
QToolTip {{
    background: {p['bg_table']};
    color: {p['text']};
    border: 1px solid {p['border_2']};
    padding: {P(5)} {P(9)};
    border-radius: {P(6)};
}}
QLabel#cellTip {{
    background: {p['bg_table']};
    color: {p['text']};
    border: 1px solid {p['border_2']};
    padding: {P(5)} {P(9)};
    border-radius: {P(6)};
    font-size: {P(12)};
}}

/* ===== 侧边栏 ===== */
#sidebar {{ background: {p['bg_side']}; border-right: 1px solid {p['border']}; }}
#sidebar QLabel#groupHeader {{
    color: {p['text_faint']}; font-size: {P(11)}; font-weight: 700;
    padding: {P(16)} {P(16)} {P(6)} {P(16)};
}}
#sidebar QPushButton#navItem {{
    background: transparent; border: none; border-radius: {P(6)};
    text-align: left; padding: 0 {P(8)} 0 {P(36)}; min-height: {P(31)};
    color: {p['text_mute']}; font-size: {P(13)};
}}
#sidebar QPushButton#navItem:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}
#sidebar QPushButton#navItem:checked {{
    background: {p['bg_select']}; color: {p['text']}; font-weight: 600;
}}
/* 键盘焦点：Qt 无 :focus-visible，统一用浅底 + 描边，避免几何跳动 */
#sidebar QPushButton#navItem:focus,
#sidebar QPushButton#groupHeaderBtn:focus,
#sidebar QPushButton#groupToggle:focus,
#sidebar QPushButton#groupPin:focus {{
    outline: none; background: {p['bg_hover']}; color: {p['text']};
}}
#sidebar QLabel#prefLabel {{ color: {p['text_faint']}; font-size: {P(11)}; padding: 0 0 {P(4)} 0; }}

/* ===== 方案 A 折叠侧栏（单列分组 + 收起为图标列） ===== */
/* 大类行由 NavHeaderButton 自绘（图标/文字/chevron/hover/press 全自绘），
   这里只声明透明底无边框，避免套用全局 QPushButton 样式。
   注意：不要给 #groupHeaderBtn 加 :hover —— 会与自绘的 hover 插值打架。 */
#sidebar QPushButton#groupHeaderBtn {{
    background: transparent; border: none; border-radius: {P(6)};
    text-align: left; padding: 0; color: {p['text']}; font-size: {P(13)};
}}
#sidebar QPushButton#groupToggle {{
    background: transparent; border: none; border-radius: {P(8)};
    color: {p['text_faint']}; font-size: {P(16)}; padding: {P(6)} {P(10)};
}}
#sidebar QPushButton#groupToggle:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}
#sidebar QPushButton#groupPin {{
    background: transparent; border: 1px solid {p['border']}; border-radius: {P(7)};
    color: {p['text_mute']}; font-size: {P(12)}; padding: {P(4)} {P(10)};
}}
#sidebar QPushButton#groupPin:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}
#sidebar QPushButton#groupPin:checked {{
    background: {p['bg_select']}; color: {p['text']}; font-weight: 600;
    border-color: {p['text_faint']};
}}
#sidebar QScrollArea {{ background: transparent; border: none; }}
#sidebar QWidget#scrollContent {{ background: transparent; }}

/* ===== 页头「?」帮助按钮（实心圆底，hover 仅加深背景） ===== */
/* 注意：border-radius 不能写超大值（如 999px）——Qt QSS 会丢弃整条声明导致方角；
   半径取按钮尺寸(20px)的一半，随字号档位同步缩放保持正圆 */
QPushButton#helpBtn {{
    background: {p['bg_hover']}; border: none; border-radius: {P(10)};
    color: {p['text_mute']}; font-size: {P(12)}; font-weight: 700; padding: 0;
}}
QPushButton#helpBtn:hover {{ background: {p['bg_select']}; color: {p['text']}; }}
QPushButton#helpBtn:pressed {{ background: {p['border_2']}; }}

/* ===== 页面标题（视图自带，保留选择器兼容） ===== */
#pageTitle {{ font-size: {P(20)}; font-weight: 700; color: {p['text']}; }}
#pageHint  {{ color: {p['text_mute']}; font-size: {P(12)}; }}
#placeholder {{ color: {p['text_faint']}; font-size: {P(14)}; padding: {P(40)}; }}

/* ===== 弹窗内的小节标题 ===== */
/* 弹窗里的小节标题若沿用页面级 #pageTitle（20px），会比同弹窗的表单标签/正文
   大一大截，视觉上很割裂；这里与 #infoVal 对齐（15px 粗体）。 */
QLabel#dialogTitle {{ font-size: {P(15)}; font-weight: 700; color: {p['text']}; }}
QLabel#dialogInfo {{ font-size: {P(13)}; color: {p['text']}; }}
QLabel#dialogRef {{ font-size: {P(12)}; color: {p['text_mute']}; }}

/* ===== 按钮（次要：透明底 + 边框，hover 仅变背景；主按钮：强调蓝实底） ===== */
QPushButton {{
    background: transparent; border: 1px solid {p['btn_border']};
    border-radius: {P(8)}; padding: {P(7)} {P(16)}; color: {p['text']};
}}
QPushButton:hover {{ background: {p['bg_hover']}; border-color: {p['btn_border']}; }}
QPushButton:pressed {{ background: {p['btn_press']}; }}
QPushButton:disabled {{ color: {p['text_faint']}; background: transparent; border-color: {p['border']}; }}
QPushButton#primary {{
    background: {p['accent_blue']}; color: #FFFFFF; border: none; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {p['accent_blue_hover']}; }}
QPushButton#primary:pressed {{ background: {p['accent_blue_press']}; }}
QPushButton#primary:disabled {{ background: {p['btn_press']}; color: {p['text_faint']}; }}

/* ===== 输入控件 ===== */
QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox {{
    background: {p['btn_bg']}; border: 1px solid {p['btn_border']};
    border-radius: {P(7)}; padding: {P(6)} {P(10)}; min-height: {P(18)}; color: {p['text']};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus {{ border: 1px solid {p['accent_blue']}; }}
QComboBox::drop-down {{ border: none; width: {P(22)}; }}
QComboBox QAbstractItemView {{
    background: {p['btn_bg']}; border: 1px solid {p['border']}; border-radius: {P(8)}; padding: {P(4)};
    selection-background-color: {p['bg_select']}; selection-color: {p['text']};
}}

/* ===== 表格 ===== */
QTableWidget {{
    background: {p['bg']}; alternate-background-color: {p['bg_table']};
    border: 1px solid {p['border']}; border-radius: {P(10)}; gridline-color: {p['grid']};
    selection-background-color: {p['bg_select']}; selection-color: {p['text']};
}}
/* 纵向 padding 只留 1px：Qt 会把「单元格内嵌控件(setCellWidget)」的几何按本 padding
   内缩，6px 上下 padding 会让 30px 行高里只剩 17px 给按钮 → 按钮被压扁、文字贴边被裁
   （补录页「补录」按钮就是这么被裁的）。文字本身在行矩形内垂直居中绘制，去掉纵向
   padding 视觉上只少了原本被裁掉的那部分，不会让文字偏移。 */
QTableWidget::item {{ padding: {P(1)} {P(10)}; border: none; }}
QTableWidget::item:selected {{ background: {p['bg_select']}; }}
/* 注意：此处不要写 color。选中态文字色由单元格 setForeground + TableBehaviorDelegate
   的 initStyleOption 接管（红字/绿字选中仍保持原色）；在此写 color 会覆盖委托，
   导致选中行红字变回普通色。普通行选中文字色由上方 selection-color 保证可读。 */
QHeaderView::section {{
    background: {p['bg_table']}; color: {p['text_mute']}; border: none;
    border-bottom: 1px solid {p['border']}; border-right: 1px solid {p['grid']};
    padding: {P(9)} {P(10)}; font-weight: 600;
}}
QHeaderView::section:hover {{ color: {p['text']}; }}
QTableCornerButton::section {{ background: {p['bg_table']}; border: none; }}

/* ===== 滚动条 ===== */
QScrollBar:vertical {{ background: transparent; width: {P(10)}; margin: {P(2)}; }}
QScrollBar::handle:vertical {{ background: {p['text_faint']}; border-radius: {P(5)}; min-height: {P(30)}; }}
QScrollBar::handle:vertical:hover {{ background: {p['text_mute']}; }}
QScrollBar:horizontal {{ background: transparent; height: {P(10)}; margin: {P(2)}; }}
QScrollBar::handle:horizontal {{ background: {p['text_faint']}; border-radius: {P(5)}; min-width: {P(30)}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ===== Tab ===== */
QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: {P(10)}; background: {p['bg']}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {p['text_mute']}; padding: {P(8)} {P(18)}; border: none;
    border-bottom: 2px solid transparent; font-weight: 500;
}}
QTabBar::tab:hover {{ color: {p['text']}; }}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['accent_blue']}; font-weight: 600; }}

/* ===== 多 tab 页标题化 tab 栏（方案B）：字号/字重对齐页面标题 ===== */
/* 仅作用于 6 个多 tab 页（tabBar objectName=pageTitleBar），不影响弹窗/其它 QTabWidget */
QTabBar#pageTitleBar::tab {{
    background: transparent; color: {p['text_mute']}; border: none;
    border-bottom: 2px solid transparent; font-size: {P(20)}; font-weight: 700;
    padding: {P(8)} {P(20)};
}}
QTabBar#pageTitleBar::tab:hover {{ color: {p['text']}; }}
QTabBar#pageTitleBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['accent_blue']}; font-weight: 700; }}

/* ===== 标签 ===== */
QLabel {{ color: {p['text']}; }}
QLabel#pageHint {{ color: {p['text_mute']}; }}

/* ===== 问题行修正面板 ===== */
QWidget#infoCard {{
    background: {p['bg_table']}; border: 1px solid {p['border']};
    border-radius: {P(10)}; padding: {P(12)} {P(16)};
}}
QLabel#infoKey {{ color: {p['text_mute']}; font-size: {P(12)}; margin-top: {P(6)}; margin-bottom: {P(2)}; }}
QLabel#infoVal {{ color: {p['text']}; font-size: {P(15)}; font-weight: 700; }}
QWidget#infoCard QLineEdit {{
    min-height: {P(28)}; max-height: {P(28)};
}}
QComboBox#handlerCombo {{
    border: none; background: transparent; border-radius: {P(6)};
    padding: {P(2)} {P(6)};
}}
QComboBox#handlerCombo:hover,
QComboBox#handlerCombo:focus {{
    background: {p['bg_hover']};
}}
QComboBox#handlerCombo::drop-down {{
    border: none; width: {P(18)};
}}

QLabel#chip {{
    background: {p['bg_table']}; border: 1px solid {p['border']};
    border-radius: {P(14)}; padding: {P(6)} {P(14)}; color: {p['text_mute']}; font-size: {P(12)};
}}
QLabel#chipVal {{ color: {p['text']}; font-weight: 700; font-family: "JetBrains Mono", "Consolas", monospace; }}
QLabel#chipWarn {{
    background: {p['warn_bg']}; border: 1px solid {p['warn_bg']};
    border-radius: {P(14)}; padding: {P(6)} {P(14)}; color: {p['warn_fg']}; font-size: {P(12)}; font-weight: 600;
}}
QLabel#chipWarn QLabel#chipVal {{ color: {p['warn_fg']}; }}
/* ===== 状态 chip 四色（蓝/红/绿/黄 + 浅底） ===== */
QLabel#chipBlue {{ background: {p['accent_blue_bg']}; border: 1px solid {p['accent_blue_bg']}; border-radius: {P(14)}; padding: {P(6)} {P(14)}; color: {p['accent_blue']}; font-size: {P(12)}; }}
QLabel#chipRed {{ background: {p['accent_red_bg']}; border: 1px solid {p['accent_red_bg']}; border-radius: {P(14)}; padding: {P(6)} {P(14)}; color: {p['accent_red']}; font-size: {P(12)}; }}
QLabel#chipGreen {{ background: {p['accent_green_bg']}; border: 1px solid {p['accent_green_bg']}; border-radius: {P(14)}; padding: {P(6)} {P(14)}; color: {p['accent_green']}; font-size: {P(12)}; }}
QLabel#chipYellow {{ background: {p['accent_yellow_bg']}; border: 1px solid {p['accent_yellow_bg']}; border-radius: {P(14)}; padding: {P(6)} {P(14)}; color: {p['accent_yellow']}; font-size: {P(12)}; }}
QLabel#chipBlue QLabel#chipVal {{ color: {p['accent_blue']}; }}
QLabel#chipRed QLabel#chipVal {{ color: {p['accent_red']}; }}
QLabel#chipGreen QLabel#chipVal {{ color: {p['accent_green']}; }}
QLabel#chipYellow QLabel#chipVal {{ color: {p['accent_yellow']}; }}
QFrame#sep {{ background: {p['grid']}; border: none; }}

/* ===== 费用类型卡片内的操作按钮 ===== */
/* 全局 QPushButton padding 7px 16px 太宽，5 个一行时会被压到 sizeHint 以下导致文字被裁 */
QFrame#card QPushButton#cardBtn {{
    padding: {P(4)} {P(10)}; font-size: {P(12)}; border-radius: {P(6)};
    background: transparent; border: 1px solid {p['btn_border']}; color: {p['text']};
}}
QFrame#card QPushButton#cardBtn:hover {{ background: {p['btn_hover']}; }}
QFrame#card QPushButton#cardBtn:pressed {{ background: {p['btn_press']}; }}

/* ===== 表格行内操作按钮（补录 / 编辑 / 删除…） ===== */
/* 行内按钮受列宽与行高双重挤压，故用同一套紧凑内边距 + 小一号字：
   各页表格里的「操作」按钮外观统一，且任何字号档位下都不会被裁。 */
QPushButton#rowBtn {{
    padding: {P(2)} {P(10)}; font-size: {P(12)}; border-radius: {P(6)};
    background: transparent; border: 1px solid {p['btn_border']}; color: {p['text']};
}}
QPushButton#rowBtn:hover {{ background: {p['btn_hover']}; }}
QPushButton#rowBtn:pressed {{ background: {p['btn_press']}; }}

/* ===== 日期输入右侧的日历按钮 ===== */
QToolButton#datePickBtn {{
    background: transparent; border: none; color: {p['text_mute']};
    font-size: {P(11)}; padding: 0 {P(5)};
}}
QToolButton#datePickBtn:hover {{ color: {p['text']}; background: {p['bg_hover']}; border-radius: {P(5)}; }}

/* ===== 筛选胶囊（导入确认对话框的状态筛选）===== */
QPushButton#chipBtn {{
    padding: 0 {P(12)}; min-width: {P(54)}; font-size: {P(12)}; border-radius: {P(13)};
    background: {p['btn_bg']}; border: 1px solid {p['btn_border']}; color: {p['text_mute']};
}}
QPushButton#chipBtn:hover {{ background: {p['btn_hover']}; color: {p['text']}; }}
QPushButton#chipBtn:checked {{
    background: {p['accent_blue']}; border-color: {p['accent_blue']}; color: #FFFFFF;
}}

/* ===== 导入页顶部「待确认」提示条（A7：还有 N 个账期待确认入库 → 去处理）===== */
QPushButton#pendingBanner {{
    text-align: left; padding: {P(8)} {P(14)}; font-size: {P(12)};
    font-weight: 600; border-radius: {P(8)};
    background: {p['accent_blue_bg']}; border: 1px solid {p['accent_blue']};
    color: {p['accent_blue']};
}}
QPushButton#pendingBanner:hover {{
    background: {p['accent_blue']}; border-color: {p['accent_blue']}; color: #FFFFFF;
}}

/* ===== 台账溯源卡片 ===== */
QWidget#sourceCard {{
    background: {p['bg_table']}; border: 1px solid {p['border']};
    border-radius: {P(10)}; padding: {P(16)} {P(18)};
}}
QLabel#sourceBreadcrumb {{
    color: {p['text_mute']}; font-size: {P(12)}; padding: 0 0 {P(10)} 0;
}}
QLabel#sourceBreadcrumb QLabel#crumbFile {{ color: {p['text']}; font-weight: 600; }}
QWidget#sourceGrid {{ background: transparent; }}
QLabel#sourceKey {{
    color: {p['text_mute']}; font-size: {P(12)}; padding: {P(5)} {P(8)};
    border-right: 1px solid {p['border']};
}}
QLabel#sourceVal {{
    color: {p['text']}; font-size: {P(13)}; padding: {P(5)} {P(8)};
    font-family: "JetBrains Mono", "Consolas", monospace;
}}
QLabel#sourceEmpty {{ color: {p['text_faint']}; font-style: italic; }}

/* ===== 费用类型卡片内的别名 chip ===== */
/* 别名是「同义写法 → 规范类型」的可删除小标签，浅底胶囊 + × 表达 */
QFrame#chip {{
    background: {p['bg']}; border: 1px solid {p['border']};
    border-radius: {P(12)}; padding: {P(2)} {P(4)};
}}
QFrame#chip QLabel#chipText {{
    color: {p['text']}; font-size: {P(12)}; padding: 0 {P(2)};
}}
QLabel#chipType {{
    color: {p['text_mute']}; font-size: {P(12)}; font-weight: 600;
}}
QPushButton#chipX {{
    background: transparent; border: none; border-radius: {P(8)};
    color: {p['text_faint']}; font-size: {P(12)}; padding: 0; min-width: {P(16)}; max-width: {P(16)};
}}
QPushButton#chipX:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}
QPushButton#chipX:pressed {{ background: {p['btn_press']}; }}

/* ===== 费用类型分类卡片（费用类型页） ===== */
QFrame#card {{
    background: {p['bg_table']}; border: 1px solid {p['border']};
    border-radius: {P(10)};
}}
QLabel#cardTitle {{ color: {p['text']}; font-size: {P(13)}; font-weight: 700; }}
QFrame#card QListWidget {{
    background: {p['bg']}; border: 1px solid {p['border']};
    border-radius: {P(8)}; padding: {P(4)}; outline: none;
}}
QFrame#card QListWidget::item {{ padding: {P(5)} {P(8)}; border-radius: {P(5)}; }}
QFrame#card QListWidget::item:hover {{ background: {p['bg_hover']}; }}
QFrame#card QListWidget::item:selected {{ background: {p['bg_select']}; color: {p['text']}; }}

/* ===== 消息框 / 弹窗 ===== */
QMessageBox, QDialog {{ background: {p['bg']}; }}
QMessageBox QLabel {{ font-size: {P(13)}; }}
"""


# ---------------------------------------------------------------------------
# 皮肤注册表
# ---------------------------------------------------------------------------
# qss 不再预生成：字号档位会变，QSS 必须按当前倍率即时生成。
SKINS = {
    "notion_light": {"label": "Notion 浅色", "palette": "notion_light"},
}
# 当前只有一套皮肤、无切换 UI；此注册表保留是为了「日后加皮肤 = 加一项」


def current_skin() -> str:
    return _current_skin


def palette() -> dict:
    """当前皮肤的调色板（自绘控件按状态取色用）。"""
    return PALETTES.get(_current_skin, PALETTES[DEFAULT_SKIN])


def qcolor(name: str) -> QColor:
    """取当前皮肤的某个色号，返回 QColor。

    **必须在「用时」调用，不要提到模块导入期**（模块级 `RED = style.qcolor(...)` 会在
    apply_skin() 之前就把颜色定死；将来加皮肤切换时会停在旧色）。

    名字拼错会 **KeyError 直接炸**（而不是静默变黑）——见 tests/test_skin_contract.py
    会把源码里所有 qcolor("x") 的字面量扫出来，断言每个调色板都有 x。
    """
    return QColor(palette()[name])


def qss_for(name: str) -> str:
    """按皮肤名 + **当前字号档位**生成 QSS。"""
    key = SKINS.get(name, SKINS[DEFAULT_SKIN])["palette"]
    return build_qss(PALETTES[key], scale.ratio())


def apply_skin(app, name: str) -> None:
    """在运行时切换皮肤（app 为 QApplication 实例）。"""
    global _current_skin
    name = name if name in SKINS else DEFAULT_SKIN
    _current_skin = name
    app.setStyleSheet(qss_for(name))


def refresh_qss(app) -> None:
    """字号档位变化后重建 QSS（皮肤不变）。

    只换样式表不够：行高/列宽等代码侧尺寸需由 main_window 另行广播刷新。
    """
    app.setStyleSheet(qss_for(_current_skin))


def skin_label(name: str) -> str:
    return SKINS.get(name, SKINS[DEFAULT_SKIN])["label"]


def available_skins():
    """返回 [(key, label), ...]，供 UI 枚举。"""
    return [(k, v["label"]) for k, v in SKINS.items()]


def _load_prefs() -> dict:
    try:
        if PREFS_PATH.exists():
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def load_skin_pref() -> str:
    name = _load_prefs().get("skin")
    return name if name in SKINS else DEFAULT_SKIN


# ---------------------------------------------------------------------------
# 动效开关（reduced motion）
# 关闭后所有侧栏动画降为瞬时到位（保留颜色变化，去掉位移与缩放）。
# ---------------------------------------------------------------------------
def motion_enabled() -> bool:
    return bool(_load_prefs().get("motion", True))


def set_motion_enabled(enabled: bool) -> None:
    data = _load_prefs()
    data["motion"] = bool(enabled)
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:
        pass


# 模块加载即对齐持久化偏好：自绘控件在 apply_skin() 之前取色也不会错
_current_skin = load_skin_pref()


def save_skin_pref(name: str) -> None:
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if PREFS_PATH.exists():
            try:
                data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["skin"] = name
        PREFS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
