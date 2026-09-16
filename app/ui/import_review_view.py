"""导入复核页（合并原「导入确认」tab2 与「导入校验」独立页）

双模式（顶部徽章只读展示，非手动切换开关）：
- 导入前：发票台账源文件解析完成后由导入页进入本页，复用 UnifiedImportDialog
  的就地修正/确认能力，点「确认入库」才写库。
  批量导入时走**待确认队列**（2026-09-15，B 方案）：导入页每解析完一个台账文件
  调用一次 open_pending，本页把该项追加进队列（同账期多份文件都保留，不静默
  丢弃）；首项到达时进入导入前模式并从队首开始，「确认入库」/「跳过当前」/
  「放弃全部」后自动载入下一个账期，直到队列清空才回到导入后模式并返回导入页。
  队列只存在内存中，未确认即关闭程序不会留下半成品数据——重跑一次文件夹导入
  即可恢复（同账期导入幂等，见 importer.commit_ledger_import 的覆盖式导入 +
  _auto_snapshot）。
- 导入后：默认模式。顶部账期下拉可切换（同账期多批次以最新为准），
  内容为 ReviewPostView 的「raw_ledger 镜表 ↔ 业务表」融合比对单表
  （阶段 2 起取代旧 ImportVerifyView 三维度切换，spec §2/§4/§10）。

数据流（导入前）：真实 data 全程不改，修正只作用于工作副本；「确认入库」时
commit_ledger_import 一次性写库；点「取消」零副作用。

待确认队列跑完后，若库中仍有待补录发票（源 A 红字/退款引用原票 + 源 B 库中
缺失的 sheet3 期外票），弹一次提示并可一跳直达「补录原票」页（唯一补录入口）。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QMessageBox, QStackedWidget, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.importer.importer import commit_ledger_import, validate_ledger_before_write
from app.ui.review_post_view import ReviewPostView
from app.ui.unified_import_dialog import UnifiedImportDialog
from app.ui.widgets import CaptionLabel, ComboBox, PageHeader
from app.diag import get_logger


def _fname(path: str) -> str:
    return (path or "").replace("\\", "/").split("/")[-1]


def _ledger_periods() -> list:
    conn = get_conn()
    try:
        return [r["period"] for r in conn.execute(
            "SELECT DISTINCT period FROM import_batch "
            "WHERE batch_type='ledger' AND status='active' ORDER BY period DESC")]
    finally:
        conn.close()


class ImportReviewView(QWidget):
    """导入复核：导入前逐账期确认/修正 + 导入后核对（双模式单页）。"""

    navigate_back = Signal()
    # 目标页面 key（如 "manual"）——确认入库后若仍有待补录发票，跳到补录原票页
    navigate_to = Signal(str)
    # file_name, batch_type, period, msg, ok
    import_finished = Signal(str, str, str, str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- 页头（本页 lay 是 0 边距全出血，页头自带缩进；说明收进「?」tooltip）----
        lay.addWidget(PageHeader(
            "导入复核",
            "导入前逐行确认/修正：解析失败与系统判定存疑的行集中在同一张表，左侧筛选、"
            "右侧就地修正，「待修正」保存后立即重算，未处理的行在确认入库时自动跳过，"
            "双击任意行可对照原始台账行。批量导入时按账期顺序逐个确认（可「跳过当前」"
            "或「放弃全部待确认」），确认入库后自动载入下一个账期。确认入库后若仍有"
            "待补录发票（红字/退款引用的原票缺失、应收账款期外票库中缺号），会提示"
            "并可一键跳到「补录原票」页；补录完成后回到本页，该行自动转为普通比对行。"
            "导入后可切换账期回写修改，全程留痕。",
            margins=(24, 16, 24, 0),
        ))

        # ---- 顶部模式条：徽章 + 账期下拉（导入后可切、导入前按队列顺序锁定）----
        bar = QHBoxLayout()
        bar.setContentsMargins(24, 14, 24, 0)
        bar.setSpacing(8)
        self.lbl_mode = CaptionLabel("")
        bar.addWidget(self.lbl_mode)
        bar.addWidget(CaptionLabel("账期"))
        self.combo_period = ComboBox()
        self.combo_period.setMinimumWidth(120)
        self.combo_period.currentIndexChanged.connect(self._on_period_changed)
        bar.addWidget(self.combo_period)
        bar.addStretch()
        lay.addLayout(bar)

        # ---- 内容堆栈：0=导入后(默认) 1=导入前 ----
        self.stack = QStackedWidget()
        self.page_post = ReviewPostView()
        self.page_pre = UnifiedImportDialog(parent=self)
        self.page_pre.confirmed.connect(self._on_confirmed)
        self.page_pre.cancelled.connect(self._on_cancelled)
        self.stack.addWidget(self.page_post)
        self.stack.addWidget(self.page_pre)
        lay.addWidget(self.stack, 1)

        # ---- 待确认队列（B 方案：按账期顺序逐个确认，不做跳序）----
        self._queue: list = []          # [{"data","period","staff_names","path"}]
        self._idx = 0                   # 当前账期在队列中的下标
        self._queue_active = False      # 是否正处于一次待确认队列流程中
        self._results: list = []        # [(period, file_name, ok, msg)] 本次队列结果
        self._fix_skipped = 0           # 累计未能定位镜表行的人工修改条数
        self._fix_log_failed = False    # 修改留痕写入是否失败过
        self._set_mode("post")

    # ------------------------------------------------------------------ #
    # 模式
    # ------------------------------------------------------------------ #
    def _set_mode(self, mode: str, period: str = "") -> None:
        if mode == "pre":
            self.lbl_mode.setText(f"导入前 · 待确认　{period}")
            self.combo_period.blockSignals(True)
            self.combo_period.clear()
            self.combo_period.addItem(period, userData=period)
            self.combo_period.setEnabled(False)  # 导入前按队列账期顺序锁定
            self.combo_period.blockSignals(False)
            self.stack.setCurrentWidget(self.page_pre)
        else:
            self.lbl_mode.setText("已导入")
            self.lbl_mode.setToolTip("")
            self.combo_period.setToolTip("")
            self.combo_period.setEnabled(True)
            self._reload_periods(prefer=period)
            self.stack.setCurrentWidget(self.page_post)

    def _reload_periods(self, prefer: str = "") -> None:
        periods = _ledger_periods()
        self.combo_period.blockSignals(True)
        self.combo_period.clear()
        for p in periods:
            self.combo_period.addItem(p, userData=p)
        if prefer and prefer in periods:
            self.combo_period.setCurrentIndex(periods.index(prefer))
        cur = self.combo_period.currentData() or ""
        self.combo_period.blockSignals(False)
        # 手动驱动一次（blockSignals 期间信号被抑制）
        self.page_post.set_period(cur)

    def _on_period_changed(self, *_):
        if self.stack.currentWidget() is self.page_post:
            self.page_post.set_period(self.combo_period.currentData() or "")

    # ------------------------------------------------------------------ #
    # 待确认队列（导入前模式）
    # ------------------------------------------------------------------ #
    def _refresh_queue_label(self) -> None:
        """刷新导入前模式的进度徽章与「待确认队列」提示。

        徽章带上当前文件名：同账期出现多份文件时能一眼看出正在确认哪一份。
        """
        n = len(self._queue)
        if not self._queue_active or n == 0:
            return
        if self.stack.currentWidget() is self.page_pre and self._idx < n:
            cur = self._queue[self._idx]
            self.lbl_mode.setText(
                f"导入前 · 待确认　{cur['period']}"
                f"　（第 {self._idx + 1}/{n} 个）　{_fname(cur['path'])}")
        tip = "待确认队列（{} 个，按账期顺序）：{}".format(
            n, "、".join(
                f"{q['period']}（{_fname(q['path'])}）" for q in self._queue))
        self.lbl_mode.setToolTip(tip)
        self.combo_period.setToolTip(tip)

    def open_pending(self, data: dict, period: str, staff_names, path: str) -> None:
        """把一份解析结果追加进待确认队列。

        首个到达时进入导入前模式并从队首开始；已在导入前模式时不打断当前账期，
        仅更新队列进度（批量导入时导入页会对每个文件调用一次本方法）。
        同账期的多份文件都保留在队列里——不静默丢弃任何一份；按队列顺序逐个
        确认，后确认的按「同账期覆盖式导入」生效（导入页已把最新改动的文件
        排在后面，故最后确认的是最新的那份）。
        """
        item = {"data": data, "period": period, "staff_names": staff_names,
                "path": path}
        self._queue.append(item)
        if self._queue_active:
            get_logger().info("REVIEW 队列追加 period=%s path=%s（共 %d 个）",
                              period, path, len(self._queue))
            self._refresh_queue_label()
            return
        get_logger().info("REVIEW 队列起始 period=%s path=%s", period, path)
        self._queue_active = True
        self._idx = 0
        self._results = []
        self._fix_skipped = 0
        self._fix_log_failed = False
        self._load_current()

    def _advance(self) -> None:
        """当前账期处理完毕（已确认 / 已跳过 / 载入失败）→ 处理队列下一个。"""
        self._idx += 1
        self._load_current()

    def _load_current(self) -> None:
        """载入队列当前账期；下标越界即视为队列处理完毕。"""
        if not (0 <= self._idx < len(self._queue)):
            self._finish_queue()
            return
        item = self._queue[self._idx]
        period, path = item["period"], item["path"]
        get_logger().info("REVIEW load queue=%d/%d period=%s path=%s",
                          self._idx + 1, len(self._queue), period, path)
        validator = lambda d, _p=period: validate_ledger_before_write(d, _p)  # noqa: E731
        try:
            self.page_pre.load_data(item["data"], period, item["staff_names"],
                                    path, validator)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", f"载入待确认数据失败：{e}")
            msg = f"✗ 载入待确认数据失败: {e}"
            self.import_finished.emit(_fname(path), "ledger", period, msg, False)
            self._results.append((period, _fname(path), False, msg))
            self._advance()
            return
        self._set_mode("pre", period)
        self._refresh_queue_label()

    def _finish_queue(self) -> None:
        """队列处理完毕：汇总结果 → 回到导入后模式 → 返回导入页。"""
        self._queue = []
        self._idx = 0
        self._queue_active = False
        results, self._results = self._results, []
        skipped = self._fix_skipped
        log_failed = self._fix_log_failed
        self._fix_skipped = 0
        self._fix_log_failed = False
        self._show_queue_summary(results, skipped, log_failed)
        # 优先定位到最后一个成功入库的账期供立即核对
        last_ok = next((p for p, _f, ok, _m in reversed(results) if ok), "")
        self._set_mode("post", last_ok)
        # 确认入库后仍有待补录发票 → 提示去补录原票；补录是唯一入口（方案 C：
        # 源 A 红字/退款引用原票 + 源 B 库中缺失的 sheet3 期外票，同一份清单）。
        # 选「去补录」则跳补录页，否则照旧返回导入页；本批一张都没入库时不提示
        # （放弃全部待确认的场景不该被拽去补录）。
        imported = any(ok for _p, _f, ok, _m in results)
        if imported and self._prompt_backfill():
            self.navigate_to.emit("manual")
        else:
            self.navigate_back.emit()

    def _prompt_backfill(self) -> bool:
        """确认入库后提示「存在需补录的发票」。返回 True = 用户选择「去补录」。

        名单 = `list_pending_backfill()`（与「补录原票」页同一个函数、同一份清单），
        故弹窗张数与补录页待办数始终一致，不会出现「说 3 张却看到 8 张」。
        查询失败不阻断导入结果（仅不提示）。
        """
        try:
            from app.engine.backfill_module import list_pending_backfill
            pending = list_pending_backfill()
        except Exception:  # noqa: BLE001 提示失败不影响已入库的导入结果
            return False
        if not pending:
            return False
        n_red = sum(1 for p in pending if p.get("source") == "红字引用")
        n_recv = sum(1 for p in pending if p.get("source") == "应收账款")
        box = QMessageBox(self)
        box.setWindowTitle("存在需补录的发票")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"存在需补录的发票 {len(pending)} 张，是否现在去补录？")
        box.setInformativeText(
            "需补录的发票有两类：\n"
            f"　· 红字 / 退款引用的原票（库中缺失）：{n_red} 张\n"
            f"　· 应收账款期外票（台账里有、库中没有）：{n_recv} 张\n\n"
            "「补录原票」是唯一入口；两类来源都已按台账已有信息预填，"
            "逐张核对后保存即可。补录完成后回本页，该行会转为普通比对行。")
        box.setDetailedText("\n".join(
            f"{p['invoice_no']}　{p.get('source') or ''}　"
            f"{p.get('invoice_date') or '（日期待填）'}"
            for p in pending))
        # setDetailedText 会自动挂一个「显示详情...」按钮，其文案来自 Qt 内置翻译
        # （未装 qtbase_zh_CN 时是英文 "Show Details..."）。这里按角色直接改成中文，
        # 不依赖翻译表是否装载。
        for b in box.buttons():
            if box.buttonRole(b) == QMessageBox.ButtonRole.ActionRole:
                b.setText("查看清单")
        b_go = box.addButton("去补录", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_go)
        box.exec()
        return box.clickedButton() is b_go

    def _show_queue_summary(self, results: list, fix_skipped: int,
                            fix_log_failed: bool) -> None:
        """单账期沿用原「导入成功」提示；批量汇总一次，避免逐账期弹窗。"""
        if not results:
            return
        if len(results) == 1:
            _period, _f, ok, msg = results[0]
            if ok:  # 取消/跳过的单账期保持原有的静默返回
                QMessageBox.information(self, "导入成功", msg)
            return
        ok_n = sum(1 for r in results if r[2])
        lines = [f"待确认队列已处理完：成功入库 {ok_n} 个账期，"
                 f"未入库 {len(results) - ok_n} 个"]
        lines.extend(f"  · {period}：{msg}" for period, _f, _ok, msg in results)
        if fix_skipped:
            lines.append(f"注：{fix_skipped} 条人工修改未能定位到镜表行，未留痕"
                         "（数据不受影响）")
        if fix_log_failed:
            lines.append("注：部分账期的修改留痕写入失败（数据不受影响）")
        QMessageBox.information(self, "导入复核", "\n".join(lines))

    # ------------------------------------------------------------------ #
    # 导入前确认 / 取消
    # ------------------------------------------------------------------ #
    def _on_confirmed(self) -> None:
        if not (self._queue_active and 0 <= self._idx < len(self._queue)):
            return
        item = self._queue[self._idx]
        period, path = item["period"], item["path"]
        fname = _fname(path)
        try:
            r = commit_ledger_import(self.page_pre._data, period, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            msg = f"✗ {fname}: {e}"
            self.import_finished.emit(fname, "ledger", period, msg, False)
            self._results.append((period, fname, False, msg))
            self._advance()
            return
        # 导入前留痕：确认页的人工修正/编辑补录 change_log + synced=0
        # （独立事务，失败不阻断已入库的导入结果；批量时累计到汇总里提示，
        #   不逐账期弹窗）
        try:
            from app.engine.import_fix_log import log_import_fixes
            fix_res = log_import_fixes(r.get("batch_id") or 0,
                                       self.page_pre.collect_import_fixes())
            self._fix_skipped += fix_res["skipped"]
        except Exception:  # noqa: BLE001
            self._fix_log_failed = True
        extra = (f", {r['deferred_count']} 张应收账款期外票转入补录原票"
                 if r.get("deferred_count") else "")
        msg = (f"✓ 发票台账 {period}: {r['invoice_count']} 张发票, "
               f"{r['prepayment_count']} 条预收款{extra}")
        self.import_finished.emit(fname, "ledger", period, msg, True)
        self._results.append((period, fname, True, msg))
        self._advance()

    def _on_cancelled(self) -> None:
        """取消当前账期：队列里还有其它账期时先问是跳过还是放弃全部。"""
        if not (self._queue_active and 0 <= self._idx < len(self._queue)):
            self._queue, self._idx, self._queue_active = [], 0, False
            self._set_mode("post")
            self.navigate_back.emit()
            return
        period = self._queue[self._idx]["period"]
        rest = len(self._queue) - self._idx - 1  # 当前账期之后还剩几个
        if rest > 0:
            choice = self._ask_cancel(period, rest)
            if choice == "back":
                return
            if choice == "all":
                for q in self._queue[self._idx:]:
                    self._results.append((q["period"], _fname(q["path"]),
                                          False, "已放弃（未入库）"))
                self._idx = len(self._queue)
                self._load_current()
                return
        self._results.append((period, _fname(self._queue[self._idx]["path"]),
                              False, "已取消（未入库）"))
        self._advance()

    def _ask_cancel(self, period: str, rest: int) -> str:
        """取消当前账期时的三选一：skip=跳过当前，all=放弃全部，back=返回。"""
        box = QMessageBox(self)
        box.setWindowTitle("取消导入")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"取消当前账期 {period} 的导入？")
        box.setInformativeText(f"该账期不会入库。队列里还有 {rest} 个待确认账期。")
        b_skip = box.addButton("跳过当前", QMessageBox.ButtonRole.AcceptRole)
        b_all = box.addButton("放弃全部待确认",
                              QMessageBox.ButtonRole.DestructiveRole)
        b_back = box.addButton("返回", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_back)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_skip:
            return "skip"
        if clicked is b_all:
            return "all"
        return "back"
