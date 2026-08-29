"""导入页：选文件 → 自动识别类型与账期 → 导入"""
from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from app.ui.widgets import (SubtitleLabel, CaptionLabel, PrimaryPushButton, PushButton)
from app.importer.importer import (
    import_expense_file, import_invoice_file, import_ledger_file,
    import_salary_file,
)
from app.importer.staff_import import parse_staff_file
from app.db import get_conn


def guess_period(filename: str) -> str | None:
    """从文件名解析账期：2025.1 / 2025-01 / 202501 / 2025年1月 → 2025-01

    另支持 2 位年（工资表命名习惯，如 25.1 / 25-1）→ 2025-01。
    仅在 4 位年匹配失败后才尝试 2 位年，对既有导入完全向后兼容。
    """
    m = re.search(r"(\d{4})\s*[.\-年]\s*(\d{1,2})", filename)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{4})(\d{1,2})", filename)
    if m and int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    # 2 位年：25.1 / 25-1 -> 2025-01
    m = re.search(r"(\d{2})\s*[.\-年]\s*(\d{1,2})", filename)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{2000 + int(m.group(1)):04d}-{int(m.group(2)):02d}"
    return None


def guess_type(filename: str) -> str:
    """从文件名识别类型：invoice/ledger/expense/staff/salary"""
    if "工资" in filename or "薪酬" in filename:
        return "salary"
    if "费用" in filename or "支出" in filename:
        return "expense"
    if "销项" in filename or "开票" in filename:
        return "invoice"
    if "职工" in filename or "花名册" in filename or "清单" in filename:
        return "staff"
    if "台账" in filename:
        return "ledger"
    return "ledger"


class ImportView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("导入台账")
        lay.addWidget(t)
        h = QLabel("选择台账文件，系统自动识别类型与账期。每月导入顺序："
                   "销项 → 发票台账 → 费用台账 → 工资表（职工清单首次导入一次即可）。\n"
                   "命名示例：工资表写作「工资25.1」= 工资表 2025 年 1 月；"
                   "账期一律以文件名为准，导入日志会持久保存、关闭程序也不丢失。")
        lay.addWidget(h)

        btns = QHBoxLayout()
        self.btn_file = QPushButton("选择文件导入")
        self.btn_file.setObjectName("primary")
        self.btn_file.clicked.connect(self.import_file)
        self.btn_folder = QPushButton("选择文件夹批量导入")
        self.btn_folder.clicked.connect(self.import_folder)
        btns.addWidget(self.btn_file)
        btns.addWidget(self.btn_folder)
        btns.addStretch()
        lay.addLayout(btns)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("导入日志…")
        lay.addWidget(self.log, 1)
        self._history_loaded = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 首次显示时回读历史日志（持久化在 import_log 表），程序关闭后不会丢
        if not self._history_loaded:
            self._history_loaded = True
            self._load_history()

    def _load_history(self) -> None:
        try:
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT imported_at, message FROM import_log "
                    "ORDER BY id DESC LIMIT 300").fetchall()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - 历史读取失败不影响导入功能
            return
        for r in reversed(rows):
            self.log.appendPlainText(f"[{r['imported_at']}] {r['message']}")
        if rows:
            self.log.appendPlainText("")  # 与本次会话日志之间留一行分隔

    def _log(self, msg: str, *, ok: bool = True, file_name: str = "",
             batch_type: str = "", period: str = "") -> None:
        """追加一行日志，并持久化到 import_log 表（关闭程序后仍可回看）。"""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log.appendPlainText(f"[{stamp}] {msg}")
        try:
            conn = get_conn()
            try:
                conn.execute(
                    "INSERT INTO import_log (imported_at, file_name, batch_type, period, ok, message) "
                    "VALUES (?,?,?,?,?,?)",
                    (stamp, file_name, batch_type, period, 1 if ok else 0, msg),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - 日志落库失败绝不影响导入主流程
            pass

    # ---- 单文件 ----
    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择台账文件", "", "Excel 文件 (*.xls *.xlsx *.xlsm)"
        )
        if path:
            self._do_import(path)

    # ---- 文件夹批量 ----
    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择台账文件夹")
        if not folder:
            return
        import glob, os
        files = sorted(glob.glob(os.path.join(folder, "*.xls*")))
        if not files:
            QMessageBox.information(self, "提示", "该文件夹没有 Excel 文件")
            return
        ok, fail = 0, 0
        for f in files:
            try:
                self._do_import(f, quiet=True)
                ok += 1
            except Exception as e:  # noqa: BLE001
                self._log(f"✗ {os.path.basename(f)}: {e}", ok=False,
                          file_name=os.path.basename(f))
                fail += 1
        self._log(f"批量导入完成: 成功 {ok}，失败 {fail}", batch_type="batch")
        QMessageBox.information(self, "批量导入", f"成功 {ok} 个，失败 {fail} 个（详见日志）")

    # ---- 执行 ----
    def _do_import(self, path: str, quiet: bool = False) -> None:
        fname = path.replace("\\", "/").split("/")[-1]
        ftype = guess_type(fname)
        period = guess_period(fname)
        try:
            if ftype == "staff":
                staff, _ = parse_staff_file(path)
                conn = get_conn()
                try:
                    from datetime import datetime
                    cur = conn.execute(
                        "INSERT INTO import_batch (batch_type, period, file_name, imported_at) VALUES (?,?,?,?)",
                        ("staff", "0000", fname, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    )
                    batch_id = cur.lastrowid
                    n_new, n_upd = 0, 0
                    for name, stype, note in staff:
                        stype = stype or "聘用"
                        r = conn.execute("SELECT id FROM staff WHERE name=?", (name,)).fetchone()
                        if r:
                            conn.execute("UPDATE staff SET staff_type=?, note=?, is_active=1 WHERE id=?", (stype, note, r["id"]))
                            n_upd += 1
                        else:
                            conn.execute(
                                "INSERT INTO staff (name, staff_type, is_active, note, source, import_batch_id) VALUES (?,?,1,?,?,?)",
                                (name, stype, note, "import", batch_id),
                            )
                            n_new += 1
                    conn.commit()
                    msg = f"✓ 职工花名册: 新增 {n_new}，更新 {n_upd}"
                finally:
                    conn.close()
            else:
                if not period:
                    raise Exception("无法从文件名识别账期（如 2025.1 / 202501），请把文件命名为类似「2025.1台账.xlsx」")
                if ftype == "invoice":
                    r = import_invoice_file(path, period)
                    msg = f"✓ 销项文档 {period}: {r['count']} 张发票"
                elif ftype == "ledger":
                    r = import_ledger_file(
                        path, period,
                        on_problems=lambda probs: self._resolve_problems(probs, period),
                        on_preview=lambda data: self._preview_ledger(data, period, path),
                    )
                    msg = (f"✓ 发票台账 {period}: {r['invoice_count']} 张发票, "
                           f"{r['prepayment_count']} 条预收款")
                elif ftype == "salary":
                    r = import_salary_file(path, period)
                    msg = (f"✓ 工资表 {period}: {r['count']} 行"
                           f"（{r['sheet_count']} 个工作表）")
                else:
                    r = import_expense_file(path, period)
                    msg = f"✓ 费用台账 {period}: {r['count']} 条费用"
        except Exception as e:  # noqa: BLE001
            self._log(f"✗ {fname}: {e}", ok=False, file_name=fname,
                      batch_type=ftype, period=period or "")
            if not quiet:
                QMessageBox.warning(self, "导入失败", str(e))
            return
        self._log(msg, ok=True, file_name=fname, batch_type=ftype,
                  period=period or "")
        if not quiet:
            QMessageBox.information(self, "导入成功", msg)

    def _resolve_problems(self, problems: list, period: str):
        """问题行修正回调：弹汇总对话框，返回 resolved；取消返回 None"""
        from PySide6.QtWidgets import QDialog
        from app.ui.problem_dialog import ProblemDialog
        conn = get_conn()
        try:
            staff_names = [r["name"] for r in conn.execute("SELECT name FROM staff ORDER BY name")]
        finally:
            conn.close()
        dlg = ProblemDialog(problems, staff_names, period, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.resolved()

    def _preview_ledger(self, data: dict, period: str, path: str):
        """写前预览回调：弹对照确认框；确认返回 data，取消返回 None（中止导入）"""
        from PySide6.QtWidgets import QDialog
        from app.ui.preview_dialog import PreviewDialog
        dlg = PreviewDialog(data, period, self, path=path)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return data
