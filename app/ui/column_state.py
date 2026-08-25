"""表格列宽持久化：手动调整后的列宽记录到 QSettings，关闭软件 / 切换页面后不恢复默认。

- 保存：表格列宽变化（sectionResized）时记录。
- 恢复：渲染完成后按存档覆盖（仅覆盖已保存的列，未保存的列仍由自适应决定）。
- 所有写入列宽的动作都在 blockSignals 下进行，避免恢复/自适应时回写存档形成反馈环。
"""
from __future__ import annotations

import json
from typing import Tuple

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QTableWidget

_ORG = "lawfirm_app"
_APP = "lawfirm_app"


def _key(page: str, name: str) -> str:
    return f"colwidths/{page}/{name}"


def save_widths(table: QTableWidget, page: str, name: str) -> None:
    """记录当前所有列宽。"""
    if table.columnCount() == 0:
        return
    vals = [table.columnWidth(c) for c in range(table.columnCount())]
    QSettings(_ORG, _APP).setValue(_key(page, name), json.dumps(vals))


def restore_col_widths(table: QTableWidget, page: str, name: str) -> bool:
    """恢复存档列宽。成功返回 True（调用方应关闭末列拉伸），否则返回 False。"""
    raw = QSettings(_ORG, _APP).value(_key(page, name))
    if not raw:
        return False
    try:
        vals = json.loads(raw)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(vals, list) or len(vals) != table.columnCount():
        return False
    hdr = table.horizontalHeader()
    hdr.blockSignals(True)
    for c, w in enumerate(vals):
        table.setColumnWidth(c, int(w))
    hdr.blockSignals(False)
    return True


def attach_persistence(table: QTableWidget, page: str, name: str = "main") -> None:
    """连接列宽变化 -> 存档。在表格创建后、首次渲染前调用一次。"""
    table.setObjectName(name)
    hdr = table.horizontalHeader()
    hdr.sectionResized.connect(lambda *_: save_widths(table, page, name))


def auto_fit_then_restore(table: QTableWidget, page: str, name: str,
                          max_width: int = 320, min_width: int = 70) -> None:
    """自适应列宽（避免省略号），但若有存档列宽则优先用存档（覆盖自适应）。

    返回是否使用了存档（调用方可据此决定是否关闭末列拉伸）。
    """
    from app.ui.table_view import auto_fit_columns
    hdr = table.horizontalHeader()
    # 自适应阶段屏蔽信号，避免把自适应宽度误存为"手动"
    hdr.blockSignals(True)
    auto_fit_columns(table, max_width=max_width, min_width=min_width)
    hdr.blockSignals(False)
    return restore_col_widths(table, page, name)
