"""Notion-like 主题（浅色 / 深色）—— 全局 QSS + 多皮肤注册表

设计要点：
- 视觉基线 = 类似 Notion 的极简风：暖灰侧栏、发丝线、无阴影、留白多、字体克制。
- 颜色全部参数化到 palette 字典，build_qss() 按 palette 生成 QSS，便于新增皮肤。
- SKINS 是皮肤注册表；apply_skin() 在运行时切换；当前皮肤持久化到 data/prefs.json。
- 第二批皮肤（如品牌色、午夜蓝）只需往 PALETTES / SKINS 追加一项，无需改其他代码。

尺寸全部按 app.ui.scale 的档位倍率派生（build_qss 的 s 参数）：字号、内边距、
圆角、控件最小尺寸一律乘倍率，避免「字号变了框没变」导致文字被裁或溢出。
QSS 因此**不再在模块加载时预生成**，改由 qss_for() 按当前倍率即时生成。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.ui import scale

PREFS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "prefs.json"

DEFAULT_SKIN = "notion_light"


# ---------------------------------------------------------------------------
# 调色板
# ---------------------------------------------------------------------------
def _light() -> dict:
    return {
        "bg": "#FFFFFF", "bg_side": "#F7F7F5", "bg_hover": "#EFEFEC",
        "bg_select": "#E9E9E7", "bg_table": "#FAFAF9", "border": "#E9E9E7",
        "border_2": "#DADAD7", "text": "#37352F", "text_mute": "#787774",
        "text_faint": "#B3B1AD", "accent": "#37352F", "red": "#C0392B",
        "green": "#1E8449", "white": "#FFFFFF",
        "btn_bg": "#FFFFFF", "btn_border": "#DADAD7", "btn_hover": "#EFEFEC",
        "btn_press": "#E9E9E7", "btn_pri_bg": "#37352F", "btn_pri_fg": "#FFFFFF",
        "btn_pri_hover": "#4F4D49", "btn_pri_press": "#2A2823", "grid": "#F1F1EF",
        "warn_bg": "#FCEBEB", "warn_fg": "#C0392B",
    }


def _dark() -> dict:
    return {
        "bg": "#1F1F1E", "bg_side": "#262625", "bg_hover": "#2E2E2C",
        "bg_select": "#3A3A37", "bg_table": "#2A2A28", "border": "#343432",
        "border_2": "#45453F", "text": "#E9E9E7", "text_mute": "#A0A09C",
        "text_faint": "#6B6B66", "accent": "#E9E9E7", "red": "#E07A6B",
        "green": "#5CB98C", "white": "#FFFFFF",
        "btn_bg": "#2E2E2C", "btn_border": "#45453F", "btn_hover": "#3A3A37",
        "btn_press": "#45453F", "btn_pri_bg": "#E9E9E7", "btn_pri_fg": "#1F1F1E",
        "btn_pri_hover": "#FFFFFF", "btn_pri_press": "#CFCFCA", "grid": "#33332F",
        "warn_bg": "#3A2622", "warn_fg": "#E07A6B",
    }


PALETTES = {"notion_light": _light(), "notion_dark": _dark()}


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
QMainWindow, QWidget#pageArea {{ background: {p['bg']}; }}
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
#sidebar QLabel#skinLabel {{ color: {p['text_faint']}; font-size: {P(11)}; padding: 0 0 {P(4)} 0; }}

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

/* ===== 页面标题（视图自带，保留选择器兼容） ===== */
#pageTitle {{ font-size: {P(20)}; font-weight: 700; color: {p['text']}; }}
#pageHint  {{ color: {p['text_mute']}; font-size: {P(12)}; }}
#placeholder {{ color: {p['text_faint']}; font-size: {P(14)}; padding: {P(40)}; }}

/* ===== 按钮 ===== */
QPushButton {{
    background: {p['btn_bg']}; border: 1px solid {p['btn_border']};
    border-radius: {P(8)}; padding: {P(7)} {P(16)}; color: {p['text']};
}}
QPushButton:hover {{ background: {p['btn_hover']}; border-color: {p['btn_border']}; }}
QPushButton:pressed {{ background: {p['btn_press']}; }}
QPushButton:disabled {{ color: {p['text_faint']}; background: {p['bg_table']}; }}
QPushButton#primary {{
    background: {p['btn_pri_bg']}; color: {p['btn_pri_fg']}; border: none; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {p['btn_pri_hover']}; }}
QPushButton#primary:pressed {{ background: {p['btn_pri_press']}; }}

/* ===== 输入控件 ===== */
QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox {{
    background: {p['btn_bg']}; border: 1px solid {p['btn_border']};
    border-radius: {P(7)}; padding: {P(6)} {P(10)}; min-height: {P(18)}; color: {p['text']};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus {{ border-color: {p['text_faint']}; }}
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
QTableWidget::item {{ padding: {P(6)} {P(10)}; border: none; }}
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
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['accent']}; font-weight: 600; }}

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
QFrame#sep {{ background: {p['grid']}; border: none; }}

/* ===== 费用类型卡片内的操作按钮 ===== */
/* 全局 QPushButton padding 7px 16px 太宽，5 个一行时会被压到 sizeHint 以下导致文字被裁 */
QFrame#card QPushButton#cardBtn {{
    padding: {P(4)} {P(10)}; font-size: {P(12)}; border-radius: {P(6)};
    background: {p['btn_bg']}; border: 1px solid {p['btn_border']}; color: {p['text']};
}}
QFrame#card QPushButton#cardBtn:hover {{ background: {p['btn_hover']}; }}
QFrame#card QPushButton#cardBtn:pressed {{ background: {p['btn_press']}; }}

/* ===== 筛选胶囊（导入确认对话框的状态筛选）===== */
QPushButton#chipBtn {{
    padding: 0 {P(12)}; min-width: {P(54)}; font-size: {P(12)}; border-radius: {P(13)};
    background: {p['btn_bg']}; border: 1px solid {p['btn_border']}; color: {p['text_mute']};
}}
QPushButton#chipBtn:hover {{ background: {p['btn_hover']}; color: {p['text']}; }}
QPushButton#chipBtn:checked {{
    background: {p['accent']}; border-color: {p['accent']}; color: {p['bg']};
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
    "notion_dark": {"label": "Notion 深色", "palette": "notion_dark"},
}


def current_skin() -> str:
    return _current_skin


def palette() -> dict:
    """当前皮肤的调色板（自绘控件按状态取色用）。"""
    return PALETTES.get(_current_skin, PALETTES[DEFAULT_SKIN])


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
