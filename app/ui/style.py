"""Notion-like 主题（浅色）——全局 QSS"""

# 色板
BG        = "#FFFFFF"   # 主背景
BG_SIDE   = "#F7F7F5"   # 侧栏
BG_HOVER  = "#EFEFEC"   # 悬停
BG_SELECT = "#E9E9E7"   # 选中
BG_TABLE  = "#FAFAF9"   # 表格斑马
BORDER    = "#E9E9E7"   # 边框
BORDER_2  = "#DADAD7"   # 控件边框
TEXT      = "#37352F"   # 主文字
TEXT_MUTE = "#787774"   # 次要文字
TEXT_FAINT= "#B3B1AD"   # 弱文字
ACCENT    = "#37352F"   # 强调（深）
RED       = "#C0392B"   # 负数/红字
GREEN     = "#1E8449"   # 已收/正数
WHITE     = "#FFFFFF"

QSS = f"""
* {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QWidget#pageArea {{ background: {BG}; }}
QToolTip {{ background: {TEXT}; color: {WHITE}; border: none; padding: 6px 10px; border-radius: 6px; }}

/* ===== 侧边栏 ===== */
#sidebar {{
    background: {BG_SIDE};
    border-right: 1px solid {BORDER};
}}
#sidebar QLabel#app_title {{
    font-size: 15px; font-weight: 700; color: {TEXT};
    padding: 18px 16px 10px 16px;
}}
#sidebar QLabel#app_sub {{ color: {TEXT_FAINT}; font-size: 11px; padding: 0 16px 12px 16px; }}
#sidebar QListWidget {{
    background: transparent; border: none; outline: none;
    padding: 4px 10px;
}}
#sidebar QListWidget::item {{
    padding: 9px 12px; border-radius: 7px; color: {TEXT};
    margin: 1px 0;
}}
#sidebar QListWidget::item:hover {{ background: {BG_HOVER}; }}
#sidebar QListWidget::item:selected {{
    background: {BG_SELECT}; color: {TEXT}; font-weight: 600;
}}
#sidebar QListWidget::item:selected:hover {{ background: {BG_SELECT}; }}

/* ===== 页面标题 ===== */
#pageTitle {{ font-size: 20px; font-weight: 700; color: {TEXT}; }}
#pageHint  {{ color: {TEXT_MUTE}; font-size: 12px; }}
#placeholder {{ color: {TEXT_FAINT}; font-size: 14px; padding: 40px; }}

/* ===== 按钮 ===== */
QPushButton {{
    background: {WHITE};
    border: 1px solid {BORDER_2};
    border-radius: 8px;
    padding: 7px 16px;
    color: {TEXT};
}}
QPushButton:hover {{ background: {BG_HOVER}; border-color: {BORDER_2}; }}
QPushButton:pressed {{ background: {BG_SELECT}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {BG_TABLE}; }}
QPushButton#primary {{
    background: {ACCENT}; color: {WHITE}; border: none; font-weight: 600;
}}
QPushButton#primary:hover {{ background: #4F4D49; }}
QPushButton#primary:pressed {{ background: #2A2823; }}

/* ===== 输入控件 ===== */
QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox {{
    background: {WHITE};
    border: 1px solid {BORDER_2};
    border-radius: 7px;
    padding: 6px 10px;
    min-height: 18px;
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus {{
    border-color: #9E9C98;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {WHITE}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 4px; selection-background-color: {BG_SELECT};
    selection-color: {TEXT};
}}

/* ===== 表格 ===== */
QTableWidget {{
    background: {WHITE};
    alternate-background-color: {BG_TABLE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: #F1F1EF;
    selection-background-color: #EDEDEA;
    selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px 10px; border: none; }}
QTableWidget::item:selected {{ background: #EDEDEA; color: {TEXT}; }}
QHeaderView::section {{
    background: {BG_TABLE};
    color: {TEXT_MUTE};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid #F3F3F1;
    padding: 9px 10px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {TEXT}; }}
QTableCornerButton::section {{ background: {BG_TABLE}; border: none; }}

/* ===== 滚动条 ===== */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D3D1CB; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #B9B7B1; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #D3D1CB; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ===== Tab ===== */
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {WHITE}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_MUTE};
    padding: 8px 18px; border: none; border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}

/* ===== 标签 ===== */
QLabel {{ color: {TEXT}; }}
QLabel#pageHint {{ color: {TEXT_MUTE}; }}

/* ===== 消息框/弹窗 ===== */
QMessageBox, QDialog {{ background: {WHITE}; }}
QMessageBox QLabel {{ font-size: 13px; }}
"""
