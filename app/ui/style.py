"""Notion-like 主题（浅色 / 深色）—— 全局 QSS + 多皮肤注册表

设计要点：
- 视觉基线 = 类似 Notion 的极简风：暖灰侧栏、发丝线、无阴影、留白多、字体克制。
- 颜色全部参数化到 palette 字典，build_qss() 按 palette 生成 QSS，便于新增皮肤。
- SKINS 是皮肤注册表；apply_skin() 在运行时切换；当前皮肤持久化到 data/prefs.json。
- 第二批皮肤（如品牌色、午夜蓝）只需往 PALETTES / SKINS 追加一项，无需改其他代码。
"""
from __future__ import annotations

import json
from pathlib import Path

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
def build_qss(p: dict) -> str:
    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {p['text']};
}}
QMainWindow, QWidget#pageArea {{ background: {p['bg']}; }}
QToolTip {{
    background: {p['bg_table']};
    color: {p['text']};
    border: 1px solid {p['border_2']};
    padding: 5px 9px;
    border-radius: 6px;
}}
QLabel#cellTip {{
    background: {p['bg_table']};
    color: {p['text']};
    border: 1px solid {p['border_2']};
    padding: 5px 9px;
    border-radius: 6px;
    font-size: 12px;
}}

/* ===== 侧边栏 ===== */
#sidebar {{ background: {p['bg_side']}; border-right: 1px solid {p['border']}; }}
#sidebar QLabel#groupHeader {{
    color: {p['text_faint']}; font-size: 11px; font-weight: 700;
    padding: 16px 16px 6px 16px;
}}
#sidebar QPushButton#navItem {{
    background: transparent; border: none; border-radius: 6px;
    text-align: left; padding: 0 8px 0 36px; min-height: 31px;
    color: {p['text_mute']}; font-size: 13px;
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
#sidebar QLabel#skinLabel {{ color: {p['text_faint']}; font-size: 11px; padding: 0 0 4px 0; }}

/* ===== 方案 A 折叠侧栏（单列分组 + 收起为图标列） ===== */
/* 大类行由 NavHeaderButton 自绘（图标/文字/chevron/hover/press 全自绘），
   这里只声明透明底无边框，避免套用全局 QPushButton 样式。
   注意：不要给 #groupHeaderBtn 加 :hover —— 会与自绘的 hover 插值打架。 */
#sidebar QPushButton#groupHeaderBtn {{
    background: transparent; border: none; border-radius: 6px;
    text-align: left; padding: 0; color: {p['text']}; font-size: 13px;
}}
#sidebar QPushButton#groupToggle {{
    background: transparent; border: none; border-radius: 8px;
    color: {p['text_faint']}; font-size: 16px; padding: 6px 10px;
}}
#sidebar QPushButton#groupToggle:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}
#sidebar QPushButton#groupPin {{
    background: transparent; border: 1px solid {p['border']}; border-radius: 7px;
    color: {p['text_mute']}; font-size: 12px; padding: 4px 10px;
}}
#sidebar QPushButton#groupPin:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}
#sidebar QPushButton#groupPin:checked {{
    background: {p['bg_select']}; color: {p['text']}; font-weight: 600;
    border-color: {p['text_faint']};
}}
#sidebar QScrollArea {{ background: transparent; border: none; }}
#sidebar QWidget#scrollContent {{ background: transparent; }}

/* ===== 页面标题（视图自带，保留选择器兼容） ===== */
#pageTitle {{ font-size: 20px; font-weight: 700; color: {p['text']}; }}
#pageHint  {{ color: {p['text_mute']}; font-size: 12px; }}
#placeholder {{ color: {p['text_faint']}; font-size: 14px; padding: 40px; }}

/* ===== 按钮 ===== */
QPushButton {{
    background: {p['btn_bg']}; border: 1px solid {p['btn_border']};
    border-radius: 8px; padding: 7px 16px; color: {p['text']};
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
    border-radius: 7px; padding: 6px 10px; min-height: 18px; color: {p['text']};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus {{ border-color: {p['text_faint']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p['btn_bg']}; border: 1px solid {p['border']}; border-radius: 8px; padding: 4px;
    selection-background-color: {p['bg_select']}; selection-color: {p['text']};
}}

/* ===== 表格 ===== */
QTableWidget {{
    background: {p['bg']}; alternate-background-color: {p['bg_table']};
    border: 1px solid {p['border']}; border-radius: 10px; gridline-color: {p['grid']};
    selection-background-color: {p['bg_select']}; selection-color: {p['text']};
}}
QTableWidget::item {{ padding: 6px 10px; border: none; }}
QTableWidget::item:selected {{ background: {p['bg_select']}; }}
/* 注意：此处不要写 color。选中态文字色由单元格 setForeground + TableBehaviorDelegate
   的 initStyleOption 接管（红字/绿字选中仍保持原色）；在此写 color 会覆盖委托，
   导致选中行红字变回普通色。普通行选中文字色由上方 selection-color 保证可读。 */
QHeaderView::section {{
    background: {p['bg_table']}; color: {p['text_mute']}; border: none;
    border-bottom: 1px solid {p['border']}; border-right: 1px solid {p['grid']};
    padding: 9px 10px; font-weight: 600;
}}
QHeaderView::section:hover {{ color: {p['text']}; }}
QTableCornerButton::section {{ background: {p['bg_table']}; border: none; }}

/* ===== 滚动条 ===== */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['text_faint']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['text_mute']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['text_faint']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ===== Tab ===== */
QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 10px; background: {p['bg']}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {p['text_mute']}; padding: 8px 18px; border: none;
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
    border-radius: 10px; padding: 12px 16px;
}}
QLabel#infoKey {{ color: {p['text_mute']}; font-size: 12px; margin-top: 6px; margin-bottom: 2px; }}
QLabel#infoVal {{ color: {p['text']}; font-size: 15px; font-weight: 700; }}
QWidget#infoCard QLineEdit {{
    min-height: 28px; max-height: 28px;
}}
QComboBox#handlerCombo {{
    border: none; background: transparent; border-radius: 6px;
    padding: 2px 6px;
}}
QComboBox#handlerCombo:hover,
QComboBox#handlerCombo:focus {{
    background: {p['bg_hover']};
}}
QComboBox#handlerCombo::drop-down {{
    border: none; width: 18px;
}}

QLabel#chip {{
    background: {p['bg_table']}; border: 1px solid {p['border']};
    border-radius: 14px; padding: 6px 14px; color: {p['text_mute']}; font-size: 12px;
}}
QLabel#chipVal {{ color: {p['text']}; font-weight: 700; font-family: "JetBrains Mono", "Consolas", monospace; }}
QLabel#chipWarn {{
    background: {p['warn_bg']}; border: 1px solid {p['warn_bg']};
    border-radius: 14px; padding: 6px 14px; color: {p['warn_fg']}; font-size: 12px; font-weight: 600;
}}
QLabel#chipWarn QLabel#chipVal {{ color: {p['warn_fg']}; }}
QFrame#sep {{ background: {p['grid']}; border: none; }}

/* ===== 台账溯源卡片 ===== */
QWidget#sourceCard {{
    background: {p['bg_table']}; border: 1px solid {p['border']};
    border-radius: 10px; padding: 16px 18px;
}}
QLabel#sourceBreadcrumb {{
    color: {p['text_mute']}; font-size: 12px; padding: 0 0 10px 0;
}}
QLabel#sourceBreadcrumb QLabel#crumbFile {{ color: {p['text']}; font-weight: 600; }}
QWidget#sourceGrid {{ background: transparent; }}
QLabel#sourceKey {{
    color: {p['text_mute']}; font-size: 12px; padding: 5px 8px;
    border-right: 1px solid {p['border']};
}}
QLabel#sourceVal {{
    color: {p['text']}; font-size: 13px; padding: 5px 8px;
    font-family: "JetBrains Mono", "Consolas", monospace;
}}
QLabel#sourceEmpty {{ color: {p['text_faint']}; font-style: italic; }}

/* ===== 费用类型分类卡片（费用类型页） ===== */
QFrame#card {{
    background: {p['bg_table']}; border: 1px solid {p['border']};
    border-radius: 10px;
}}
QLabel#cardTitle {{ color: {p['text']}; font-size: 13px; font-weight: 700; }}
QFrame#card QListWidget {{
    background: {p['bg']}; border: 1px solid {p['border']};
    border-radius: 8px; padding: 4px; outline: none;
}}
QFrame#card QListWidget::item {{ padding: 5px 8px; border-radius: 5px; }}
QFrame#card QListWidget::item:hover {{ background: {p['bg_hover']}; }}
QFrame#card QListWidget::item:selected {{ background: {p['bg_select']}; color: {p['text']}; }}

/* ===== 消息框 / 弹窗 ===== */
QMessageBox, QDialog {{ background: {p['bg']}; }}
QMessageBox QLabel {{ font-size: 13px; }}
"""


# ---------------------------------------------------------------------------
# 皮肤注册表
# ---------------------------------------------------------------------------
SKINS = {
    "notion_light": {"label": "Notion 浅色", "qss": build_qss(PALETTES["notion_light"])},
    "notion_dark": {"label": "Notion 深色", "qss": build_qss(PALETTES["notion_dark"])},
}


def current_skin() -> str:
    return _current_skin


def palette() -> dict:
    """当前皮肤的调色板（自绘控件按状态取色用）。"""
    return PALETTES.get(_current_skin, PALETTES[DEFAULT_SKIN])


def apply_skin(app, name: str) -> None:
    """在运行时切换皮肤（app 为 QApplication 实例）。"""
    global _current_skin
    name = name if name in SKINS else DEFAULT_SKIN
    _current_skin = name
    app.setStyleSheet(SKINS[name]["qss"])


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
