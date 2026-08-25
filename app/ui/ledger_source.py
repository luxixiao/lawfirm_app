"""台账溯源卡片（共享组件）

- show_ledger_source: 弹窗展示某条记录在「原台账」中的原始单元格，
  顶部面包屑精确到 文件 › sheet › 第 N 行，底部「打开原文件」按钮。
- show_source_for_invoice: 由发票号查 DB 溯源信息（src_sheet/src_row/批次存档），
  再调 show_ledger_source；供「经办人发票收款情况」右键调用。

风格沿用 D-Notion（style.py 的 #sourceCard 系列）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel,
    QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.importer.archive_helper import read_archive_row, resolve_archive
from app.ui.widgets import PrimaryPushButton, PushButton


def open_archive_file(archive_path: str) -> None:
    """用系统默认程序打开源文件（本机 Excel）。跨平台兼容。"""
    p = resolve_archive(archive_path)
    if not p.exists():
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]  Windows 专用
        elif sys.platform == "darwin":
            os.system(f'open "{p}"')
        else:
            os.system(f'xdg-open "{p}"')
    except Exception:  # noqa: BLE001 打开失败静默（用户可手动去存档目录找）
        pass


class LedgerSourceDialog(QDialog):
    """原台账行溯源弹窗。"""

    def __init__(self, parent, *, header: List[str], raw_row: List[str],
                 sheet_name: str, row_no: int, archive_path: str,
                 file_name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("查看台账信息")
        self.resize(560, 460)
        self._archive_path = archive_path

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        # 面包屑：文件 › sheet › 第 N 行
        crumb = QHBoxLayout()
        crumb.setSpacing(6)
        f = QLabel(file_name)
        f.setObjectName("crumbFile")
        crumb.addWidget(f)
        crumb.addWidget(QLabel("›"))
        crumb.addWidget(QLabel(sheet_name or "—"))
        crumb.addWidget(QLabel("›"))
        crumb.addWidget(QLabel(f"第 {row_no} 行"))
        crumb.addStretch()
        root.addLayout(crumb)

        # 原台账单元格网格
        card = _SourceCard(header, raw_row)
        root.addWidget(card, 1)

        # 底部按钮
        btns = QHBoxLayout()
        open_btn = PushButton("打开原文件")
        open_btn.clicked.connect(lambda: open_archive_file(self._archive_path))
        close_btn = PushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(open_btn)
        btns.addWidget(close_btn)
        root.addLayout(btns)


class _SourceCard(QWidget):
    """展示原台账单元格（字段名 | 原值）的卡片。"""

    def __init__(self, header: List[str], raw_row: List[str]) -> None:
        super().__init__()
        self.setObjectName("sourceCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        grid = QGridLayout()
        grid.setObjectName("sourceGrid")
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        layout.addLayout(grid)

        n = max(len(header), len(raw_row))
        if n == 0:
            empty = QLabel("（无原始凭证行信息）")
            empty.setObjectName("sourceEmpty")
            layout.addWidget(empty)
            return
        for i in range(n):
            key = header[i] if i < len(header) else f"列{i + 1}"
            val = raw_row[i] if i < len(raw_row) else ""
            k = QLabel(key)
            k.setObjectName("sourceKey")
            v = QLabel(val if val != "" else "—")
            v.setObjectName("sourceVal")
            v.setWordWrap(True)
            grid.addWidget(k, i, 0)
            grid.addWidget(v, i, 1)


def show_ledger_source(parent, *, header: List[str], raw_row: List[str],
                       sheet_name: str, row_no: int, archive_path: str,
                       file_name: str) -> None:
    """弹窗展示原台账行（供 B 校验中心 / 其它调用方复用）。"""
    dlg = LedgerSourceDialog(
        parent, header=header, raw_row=raw_row, sheet_name=sheet_name,
        row_no=row_no, archive_path=archive_path, file_name=file_name,
    )
    dlg.exec()


def read_invoice_source(invoice_no: str) -> Optional[dict]:
    """由发票号查溯源三要素（sheet/行号/存档文件）与原始行。

    返回 {header, raw_row, sheet_name, row_no, archive_path, file_name} 或 None。
    """
    conn = get_conn()
    try:
        inv = conn.execute(
            "SELECT src_sheet, src_row, import_batch_id FROM invoice WHERE invoice_no=?",
            (invoice_no,),
        ).fetchone()
        if inv is None:
            return None
        sheet_name = inv["src_sheet"] or ""
        row_no = inv["src_row"] or 0
        batch_id = inv["import_batch_id"]
        if not sheet_name or not row_no or not batch_id:
            return None
        batch = conn.execute(
            "SELECT file_name, archive_path FROM import_batch WHERE id=?", (batch_id,)
        ).fetchone()
        if batch is None:
            return None
        file_name = batch["file_name"] or ""
        archive_path = batch["archive_path"] or ""
        if not archive_path:
            return None
    finally:
        conn.close()
    res = read_archive_row(archive_path, sheet_name, row_no)
    if res is None:
        # 存档文件或行列读不到（如历史数据 sheet 名漂移）
        return {
            "header": [], "raw_row": [], "sheet_name": sheet_name,
            "row_no": row_no, "archive_path": archive_path, "file_name": file_name,
        }
    header, raw_row = res
    return {
        "header": header, "raw_row": raw_row, "sheet_name": sheet_name,
        "row_no": row_no, "archive_path": archive_path, "file_name": file_name,
    }


def show_source_for_invoice(parent, invoice_no: str) -> None:
    """右键溯源入口：查 DB → 显示原台账行；无溯源信息时给提示。"""
    src = read_invoice_source(invoice_no)
    if src is None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            parent, "无溯源信息",
            f"发票 {invoice_no} 没有溯源记录。\n可能来自手动补录，或导入于本次溯源功能上线之前。",
        )
        return
    if not src["raw_row"]:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            parent, "无法读取原行",
            f"发票 {invoice_no} 的存档文件或行列已不可读\n"
            f"（文件：{src['file_name']}，sheet：{src['sheet_name']}，行：{src['row_no']}）。\n"
            f"可点击「打开原文件」手动核对。",
        )
    show_ledger_source(
        parent, header=src["header"], raw_row=src["raw_row"],
        sheet_name=src["sheet_name"], row_no=src["row_no"],
        archive_path=src["archive_path"], file_name=src["file_name"],
    )
