"""导入页：选文件 → 自动识别类型与账期 → 导入

发票台账导入流程（2026-09-02 起，导入复核页改造阶段 1）：
解析成功后通过 ledger_pending 信号交给「导入复核」页（ImportReviewView）
的导入前模式就地确认/修正，「确认入库」后才写库。本页只负责选文件、
执行导入与日志；不再内嵌确认面板（原 QTabWidget 的「导入确认」tab 已移除）。

批量（2026-09-15 起，B 方案待确认队列）：
- 台账类文件按**解析出的账期**排序（不再按文件名——否则 2025.10 会排在
  2025.2 之前），保证复核队列里的月份顺序正确。
- **同类型同账期出现多份文件 → 整批导入直接中止**（不写库、不入队），并列出
  冲突文件要求改正后重导：覆盖式导入会让同账期文件互相覆盖，属数据风险。
- 每个台账文件解析后照旧 emit ledger_pending（复核页按账期追加进待确认
  队列，不覆盖）；全部处理完后 emit 一次 ledger_queue_ready 才切页，
  避免批量时逐文件反复切走当前页。

队列守卫（阶段 2-3，A7/A8）——由主窗口注入两个 callable，本页只消费、不查库：
- `pending_count_fn() -> int`：顶部提示条「还有 N 个账期待确认入库 → 去处理」；
- `ledger_guard() -> str | None`：返回文案即**拒绝本次「发票台账」导入**。
  边界写死：只拦台账类，销项 / 收款 / 费用 / 工资 / 职工清单照旧可导
  （否则会堵死「必须先导销项、再导台账」的顺序依赖）。批量导入时若文件夹里
  含台账文件 → **整批拒绝**（不处理任何文件，避免半成品批次）。
"""
from __future__ import annotations

import glob
import os
import re
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from app.ui.widgets import (CaptionLabel, PageHeader, PrimaryPushButton, PushButton)
from app.importer.importer import (
    import_expense_file, import_invoice_file, import_salary_file, parse_ledger_file,
)
from app.importer.staff_import import parse_staff_file
from app.db import get_conn
from app.diag import get_logger


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


# 类型的中文名（用于「同账期多文件」拦截提示）
_TYPE_LABEL = {
    "ledger": "发票台账", "invoice": "销项文档", "expense": "费用台账",
    "salary": "工资表", "staff": "职工清单",
}


class ImportView(QWidget):
    # 发票台账解析完成 → 交导入复核页确认（data, period, staff_names, path）
    ledger_pending = Signal(object, str, object, str)
    # 本批次台账已全部交给复核页 → 由主窗口切到「导入复核」页（批量只切一次）
    ledger_queue_ready = Signal()
    # 点顶部「待确认」提示条 → 主窗口切到「导入复核」页（A7）
    navigate_review = Signal()

    def __init__(self) -> None:
        super().__init__()
        # 由主窗口注入（A7/A8）；未接线（如单测直接构造）时降级为「无待确认、不拦」
        self.pending_count_fn = None   # callable() -> int  未确认入库的账期数
        self.ledger_guard = None       # callable() -> str | None  台账导入守卫文案
        self._build_import_tab(self)
        self._history_loaded = False

    def _build_import_tab(self, page: QWidget) -> None:
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        lay.addWidget(PageHeader(
            "导入台账",
            "选择台账文件，系统自动识别类型与账期。每月导入顺序："
            "销项 → 发票台账 → 费用台账 → 工资表（职工清单首次导入一次即可）。\n"
            "命名示例：工资表写作「工资25.1」= 工资表 2025 年 1 月；"
            "账期一律以文件名为准，导入日志会持久保存、关闭程序也不丢失。",
        ))

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

        # A7：待确认提示条。队列未跑完时显示「还有 N 个账期待确认入库 → 去处理」，
        # 点击直达「导入复核」页；无待确认时整条隐藏（不占位）。
        self.banner_pending = QPushButton("")
        self.banner_pending.setObjectName("pendingBanner")
        self.banner_pending.setCursor(Qt.CursorShape.PointingHandCursor)
        self.banner_pending.setVisible(False)
        self.banner_pending.clicked.connect(lambda: self.navigate_review.emit())
        lay.addWidget(self.banner_pending)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("导入日志…")
        lay.addWidget(self.log, 1)
        self._history_loaded = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # A7：每次回到本页都按最新队列状态刷新顶部提示条
        self._refresh_pending_banner()
        # 首次显示时回读历史日志（持久化在 import_log 表），程序关闭后不会丢
        if not self._history_loaded:
            self._history_loaded = True
            self._load_history()

    # ---- A7：待确认提示条 / A8：台账导入守卫（均由主窗口注入的 callable 驱动）----
    def set_pending_count(self, n: int) -> None:
        """A7：设置待确认账期数并刷新提示条（0 = 隐藏）。"""
        n = max(0, int(n or 0))
        if n > 0:
            self.banner_pending.setText(f"还有 {n} 个账期待确认入库 → 去处理")
            self.banner_pending.setToolTip(
                "这些账期已解析但尚未写入数据库，请到「导入复核」页逐个确认入库")
            self.banner_pending.setVisible(True)
        else:
            self.banner_pending.setText("")
            self.banner_pending.setToolTip("")
            self.banner_pending.setVisible(False)

    def _refresh_pending_banner(self) -> None:
        fn = self.pending_count_fn
        try:
            n = int(fn()) if fn is not None else 0
        except Exception:  # noqa: BLE001 提示条刷新失败不应影响导入功能
            n = 0
        self.set_pending_count(n)

    def _ledger_block_message(self) -> str | None:
        """A8：返回拦截文案 = 拒绝本次台账导入；None = 放行。

        守卫本身出错时**放行**（不因为一个提示功能把导入主流程堵死）。
        """
        fn = self.ledger_guard
        if fn is None:
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None

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
        if not path:
            return
        # A8：只拦「发票台账」——销项导出 / 收款 / 费用台账 / 工资表 / 职工清单
        # 照旧可导，否则会堵死既有的「必须先导销项、再导台账」顺序依赖。
        # 判定放在选文件之后（此处才能从文件名识别类型），但仍在**解析/入队之前**，
        # 故不会留下任何半成品数据。
        if guess_type(os.path.basename(path)) == "ledger":
            block = self._ledger_block_message()
            if block:
                self._log("✗ 已中止导入：" + block, ok=False,
                          file_name=os.path.basename(path), batch_type="ledger")
                QMessageBox.warning(self, "存在未确认入库的账期", block)
                return
        if self._do_import(path) == "ledger":
            self.ledger_queue_ready.emit()

    # ---- 文件夹批量 ----
    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择台账文件夹")
        if not folder:
            return
        # 按「解析出的账期」排序：按文件名排序会把 2025.10 排在 2025.2 之前，
        # 导致复核队列里的月份顺序错乱。
        def _key(p: str):
            name = os.path.basename(p)
            return (guess_period(name) or "9999-99", name)

        files = sorted(glob.glob(os.path.join(folder, "*.xls*")), key=_key)
        if not files:
            QMessageBox.information(self, "提示", "该文件夹没有 Excel 文件")
            return
        # A8：台账导入前置守卫 —— 队列未跑完时**整批拒绝**。
        # 必须在处理**任何**文件之前拦（否则会出现「销项已入库、台账被拦」的
        # 半成品批次）；只在文件夹里确实含台账类文件时才拦，纯销项 / 费用 /
        # 工资 / 职工清单的文件夹照旧可导（保住「先导销项、再导台账」顺序依赖）。
        if any(guess_type(os.path.basename(f)) == "ledger" for f in files):
            block = self._ledger_block_message()
            if block:
                msg = ("本次批量导入已中止（未写入任何数据）。\n\n" + block
                       + "\n\n（本文件夹含「发票台账」文件。销项导出、费用台账、"
                         "工资表、职工清单不受影响，可单独导入。）")
                self._log("✗ 批量导入已中止：" + block, ok=False, batch_type="batch")
                QMessageBox.warning(self, "存在未确认入库的账期", msg)
                return
        # 同类型同账期多份文件 → 直接中止整批导入（在写库/入队之前拦下，避免留下
        # 「一半已导入」的半成品）：覆盖式导入会让同账期文件互相覆盖，属数据风险。
        # 职工清单无账期语义（批次账期固定 0000，按姓名 upsert），不参与判定。
        groups: dict = {}
        for f in files:
            n = os.path.basename(f)
            ftype = guess_type(n)
            period = guess_period(n)
            if ftype == "staff" or not period:
                continue
            groups.setdefault((ftype, period), []).append(n)
        conflicts = {k: v for k, v in groups.items() if len(v) > 1}
        if conflicts:
            lines = [
                f"　· {period}（{_TYPE_LABEL.get(ftype, ftype)}）："
                f"{'、'.join(sorted(ns))}"
                for (ftype, period), ns in sorted(
                    conflicts.items(), key=lambda kv: (kv[0][1], kv[0][0]))
            ]
            msg = ("同一账期存在多份同类型文件，导入时会互相覆盖，"
                   "已中止本次批量导入（未写入任何数据）。\n\n"
                   "请把重复的文件改名以区分账期、或移出该文件夹后重新导入：\n"
                   + "\n".join(lines))
            self._log("✗ 批量导入已中止：同账期多份同类型文件\n" + msg,
                      ok=False, batch_type="batch")
            QMessageBox.critical(self, "批量导入已中止", msg)
            return

        ok_files, fail_files = [], []
        n_ledger = 0
        for f in files:
            name = os.path.basename(f)
            try:
                handed = self._do_import(f, quiet=True)
                # ledger 类型在 _do_import 内转入「导入复核」待确认队列，需用户
                # 逐账期确认后才真正入库；其余类型已在本轮直接写库。
                if handed == "ledger":
                    n_ledger += 1
                    ok_files.append((name, "已转入复核，待确认入库"))
                else:
                    ok_files.append((name, "导入成功"))
            except Exception as e:  # noqa: BLE001
                self._log(f"✗ {name}: {e}", ok=False, file_name=name)
                fail_files.append((name, str(e)))
        # 逐文件记录结果（成功也列出文件名，便于回看）
        for name, result in ok_files:
            self._log(f"✓ {name}: {result}", ok=True, file_name=name,
                      batch_type="batch")
        # 台账全部解析完成 → 一次性切到「导入复核」页（在汇总弹窗之前，切换
        # 在弹窗之下完成，关掉弹窗后即停在复核页待确认）
        if n_ledger:
            self.ledger_queue_ready.emit()
        # 汇总：枚举成功与失败的具体文件
        if ok_files or fail_files:
            lines = [f"批量导入完成：成功 {len(ok_files)} 个，"
                     f"失败 {len(fail_files)} 个"]
            if ok_files:
                lines.append("成功文件：")
                lines.extend(f"  · {n}（{r}）" for n, r in ok_files)
            if fail_files:
                lines.append("失败文件：")
                lines.extend(f"  · {n}：{e}" for n, e in fail_files)
            summary = "\n".join(lines)
            self._log(summary, batch_type="batch")
            QMessageBox.information(self, "批量导入", summary)
        else:
            self._log("批量导入完成：未处理任何文件", batch_type="batch")
            QMessageBox.information(self, "批量导入", "未处理任何文件（导入均被跳过）。")

    # ---- 执行 ----
    def _do_import(self, path: str, quiet: bool = False) -> str | None:
        """导入单个文件。台账类不写库，返回 "ledger" 交给「导入复核」待确认队列；
        其余类型直接写库，返回 None。"""
        fname = path.replace("\\", "/").split("/")[-1]
        ftype = guess_type(fname)
        period = guess_period(fname)
        get_logger().info("IMPORT _do_import fname=%s ftype=%s period=%s",
                          fname, ftype, period)
        try:
            if ftype == "staff":
                staff, _ = parse_staff_file(path)
                conn = get_conn()
                try:
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
                    # 解析 → 交由「导入复核」页的待确认队列（确认入库后才写库）
                    get_logger().info("IMPORT ledger branch → parse_ledger_file + emit ledger_pending")
                    data = parse_ledger_file(path, period)
                    # deferred（应收账款期外票）也算「识别到内容」：它们不落 invoice，
                    # 会转入「补录原票」，不能因此判成空文件。
                    if (not data.get("invoices") and not data.get("prepayments")
                            and not data.get("deferred")):
                        raise Exception(
                            "解析完成但未识别到任何发票或预收款行。\n"
                            "请检查台账文件的 sheet 名称与列名是否符合模板"
                            "（如「开票明细」「预收款」等）。")
                    conn = get_conn()
                    try:
                        staff_names = [rr["name"] for rr in
                                       conn.execute("SELECT name FROM staff ORDER BY name")]
                    finally:
                        conn.close()
                    self.ledger_pending.emit(data, period, staff_names, path)
                    return "ledger"
                elif ftype == "salary":
                    r = import_salary_file(path, period)
                    msg = (f"✓ 工资表 {period}: {r['count']} 行"
                           f"（{r['sheet_count']} 个工作表）")
                else:
                    # 同账期重导：传入确认回调，由弹窗决定覆盖 / 取消（feature 3）。
                    # 非 UI 调用方（脚本 / 单测）不传回调 → 静默覆盖，零回归。
                    r = import_expense_file(path, period, on_reimport_diff=self._confirm_reimport)
                    msg = f"✓ 费用台账 {period}: {r['count']} 条费用"
                    # P1-4：无/坏序号行已按无序号处理，显式告知（不静默）
                    for w in r.get("warnings") or []:
                        msg += f"\n⚠ {w}"
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

    # ---- 导入复核页回传的导入结果（写日志；弹窗由复核页负责） ----
    def log_result(self, file_name: str, batch_type: str, period: str,
                   msg: str, ok: bool) -> None:
        self._log(msg, ok=ok, file_name=file_name, batch_type=batch_type,
                  period=period or "")

    def _resolve_problems(self, problems: list, period: str):
        """旧路径：仅修正问题行的回调（保留备用，当前走嵌入确认 tab）。"""
        get_logger().warning("DEAD-PATH HIT: _resolve_problems (old ProblemDialog flow)")
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
        get_logger().warning("DEAD-PATH HIT: _preview_ledger (old PreviewDialog flow)")
        from PySide6.QtWidgets import QDialog
        from app.ui.preview_dialog import PreviewDialog
        dlg = PreviewDialog(data, period, self, path=path)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return data

    def _confirm_reimport(self, diff) -> str:
        """同账期重导确认回调（feature 3）。

        由 ``import_expense_file(on_reimport_diff=...)`` 在「检测到差异」时调用：
        弹 ``ExpenseReimportDialog`` 展示字段级差异，用户「覆盖」返回 "overwrite"、
        「取消导入」返回 "cancel"（importer 据此保留原账期数据）。
        """
        from PySide6.QtWidgets import QDialog
        from app.ui.expense_reimport_dialog import ExpenseReimportDialog
        dlg = ExpenseReimportDialog(diff, self)
        return "overwrite" if dlg.exec() == QDialog.Accepted else "cancel"
