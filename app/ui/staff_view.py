"""员工管理页（批1基础，三 tab）

- Tab1「花名册」：人员身份主数据（编号/姓名/身份证号/手机号/入职月份/离职月份/备注）。
  编号与身份证号非空时唯一。导入职工清单也落到这里。
- Tab2「员工类型」：人 × 类型 网格（一人可挂多个类型）；人员只能选自花名册。
  这是把原「员工名单」改名并拆出"类型"后的新形态——"人"归花名册，"类型"在此关联。
- Tab3「类型设置」：类型定义（开票 / 报销 / 业务金额方式 / 说明 / 人数），原「员工类型」改名。
- 口径（去身份）：是否进报表/能否承担费用由 staff_type_def 的 is_invoice / can_expense 决定；
  报表分组键=类型名本身（person_type 快照），不再使用身份/角色（角色列已移除）。
- 过渡镜像：staff 表仍被下游结算/导入读取，引擎函数会同步维护它（批2 再移除）。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import staff_type as st
from app.importer.staff_import import ImportError_, parse_staff_file
from app.ui import scale
from app.ui.column_layout import install_column_layout
from app.ui.table_features import install_common_features, install_header_filter
from app.ui.widgets import (
    CaptionLabel, PrimaryPushButton, PushButton, tab_help_corner,
)

# 业务金额方式（下拉：库里存的 net_basis 取值，非表头文案）未存口径时的显示默认值
BASIS_FALLBACK = "收款净额"


class StaffView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        # 「先建类型并勾选参与结算、再导入职工清单」的提示是否已在本次使用中出现过
        self._settle_order_hinted = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setObjectName("pageTitleBar")
        self.tabs.addTab(self._build_roster_tab(), "花名册")
        self.tabs.addTab(self._build_staff_type_tab(), "员工类型")
        self.tabs.addTab(self._build_type_settings_tab(), "类型设置")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.setCornerWidget(tab_help_corner(
            "花名册是人员身份主数据（基础数据）：台账导入时校验经办人是否在此名单中。"
            "「员工类型」页把人员关联到类型（一人可多类型）；能否参与结算，看该类型在"
            "「类型设置」页有没有勾选「参与结算」。"
        ), Qt.Corner.TopRightCorner)
        lay.addWidget(self.tabs, 1)

        self.refresh()

    # ================================================================== #
    # Tab1：花名册
    # ================================================================== #
    def _build_roster_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addWidget(CaptionLabel(
            "人员身份主数据：编号、姓名、身份证号、手机号、入职月份、离职月份、备注。"
            "编号与身份证号非空时不可重复；离职人员保留在此（离职月份填月份即可）。"))

        btns = QHBoxLayout()
        self.btn_import = QPushButton("导入职工清单（模板）")
        self.btn_import.setObjectName("primary")
        self.btn_import.clicked.connect(self.import_staff)
        self.btn_r_add = QPushButton("新增")
        self.btn_r_add.clicked.connect(self.add_roster)
        self.btn_r_edit = QPushButton("修改")
        self.btn_r_edit.clicked.connect(self.edit_roster)
        self.btn_r_del = QPushButton("删除")
        self.btn_r_del.clicked.connect(self.delete_roster)
        self.btn_r_export = QPushButton("导出")
        self.btn_r_export.setObjectName("accent")
        self.btn_r_export.clicked.connect(self.export_roster)
        for b in (self.btn_import, self.btn_r_add, self.btn_r_edit,
                  self.btn_r_del, self.btn_r_export):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)

        self.roster_table = QTableWidget(0, 7)
        self.roster_table.setHorizontalHeaderLabels(
            ["编号", "姓名", "身份证号", "手机号", "入职月份", "离职月份", "备注"])
        self.roster_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.roster_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.roster_table.cellDoubleClicked.connect(lambda *_: self.edit_roster())
        install_common_features(self.roster_table)
        install_header_filter(self.roster_table)
        self._roster_col = install_column_layout(self.roster_table, "staff_roster", "main")
        lay.addWidget(self.roster_table, 1)
        return w

    # ================================================================== #
    # Tab2：员工类型（人 × 类型）
    # ================================================================== #
    def _build_staff_type_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addWidget(CaptionLabel(
            "把人员关联到类型（一人可挂多个类型）。人员只能选自「花名册」；"
            "类型来自「类型设置」。每行一个人-类型组合，删除即解除该关联。"))

        btns = QHBoxLayout()
        self.btn_st_add = QPushButton("新增关联")
        self.btn_st_add.clicked.connect(self.add_staff_type)
        self.btn_st_del = QPushButton("删除关联")
        self.btn_st_del.clicked.connect(self.delete_staff_type)
        self.btn_st_export = QPushButton("导出")
        self.btn_st_export.setObjectName("accent")
        self.btn_st_export.clicked.connect(self.export_staff_types)
        for b in (self.btn_st_add, self.btn_st_del, self.btn_st_export):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)

        self.staff_type_table = QTableWidget(0, 2)
        self.staff_type_table.setHorizontalHeaderLabels(["姓名", "类型"])
        self.staff_type_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.staff_type_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        install_common_features(self.staff_type_table)
        install_header_filter(self.staff_type_table)
        self._st_col = install_column_layout(self.staff_type_table, "staff_type_map", "main")
        lay.addWidget(self.staff_type_table, 1)
        return w

    # ================================================================== #
    # Tab3：类型设置（原「员工类型」）
    # ================================================================== #
    def _build_type_settings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addWidget(CaptionLabel(
            "自定义员工类型，可自由增删改名。"
            "类型（开票/报销）决定它是否计入收入报表、能否承担费用；"
            "业务金额方式按类型各自设置；报表分组键=类型名本身，不再使用身份/角色。"))

        btns = QHBoxLayout()
        self.btn_t_add = QPushButton("新增类型")
        self.btn_t_rename = QPushButton("改名")
        self.btn_t_note = QPushButton("编辑说明")
        self.btn_t_del = QPushButton("删除类型")
        self.btn_t_up = QPushButton("上移")
        self.btn_t_down = QPushButton("下移")
        self.btn_t_add.clicked.connect(self.type_add)
        self.btn_t_rename.clicked.connect(self.type_rename)
        self.btn_t_note.clicked.connect(self.type_note)
        self.btn_t_del.clicked.connect(self.type_delete)
        self.btn_t_up.clicked.connect(lambda: self.type_move(-1))
        self.btn_t_down.clicked.connect(lambda: self.type_move(1))
        for b in (self.btn_t_add, self.btn_t_rename, self.btn_t_note,
                  self.btn_t_del, self.btn_t_up, self.btn_t_down):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)

        self.type_table = QTableWidget(0, 7)
        self.type_table.setHorizontalHeaderLabels(
            ["类型", "开票", "报销", "业务金额方式", "说明", "人数"])
        self.type_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.type_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.type_table.verticalHeader().setVisible(False)
        self.type_table.setColumnWidth(0, scale.px(120))
        self.type_table.setColumnWidth(1, scale.px(70))
        self.type_table.setColumnWidth(2, scale.px(70))
        self.type_table.setColumnWidth(3, scale.px(100))
        self.type_table.setColumnWidth(5, scale.px(60))
        self.type_table.setColumnWidth(6, scale.px(90))
        self.type_table.itemChanged.connect(self._on_line_changed)
        install_common_features(self.type_table)
        self._type_col = install_column_layout(self.type_table, "staff_type", "main")
        lay.addWidget(self.type_table, 1)
        return w

    # ================================================================== #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _on_tab_changed(self, _idx: int) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._refresh_roster()
        self._refresh_staff_types()
        self._refresh_types()

    # ---- 花名册 ----
    def _refresh_roster(self) -> None:
        rows = st.list_roster()
        self.roster_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.roster_table.setItem(r, 0, QTableWidgetItem(row["code"] or ""))
            self.roster_table.setItem(r, 1, QTableWidgetItem(row["name"]))
            self.roster_table.setItem(r, 2, QTableWidgetItem(row["id_card"] or ""))
            self.roster_table.setItem(r, 3, QTableWidgetItem(row["phone"] or ""))
            self.roster_table.setItem(r, 4, QTableWidgetItem(row["hire_month"] or ""))
            self.roster_table.setItem(r, 5, QTableWidgetItem(row["leave_month"] or ""))
            self.roster_table.setItem(r, 6, QTableWidgetItem(row["note"] or ""))
        self._roster_col.apply()

    # ---- 员工类型（人 × 类型）----
    def _refresh_staff_types(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT m.name AS name, m.type_name AS type_name FROM staff_type_map m "
                "JOIN staff_roster r ON r.name = m.name "
                "ORDER BY m.name, m.is_primary DESC, m.type_name").fetchall()
        finally:
            conn.close()
        self.staff_type_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.staff_type_table.setItem(r, 0, QTableWidgetItem(row["name"]))
            self.staff_type_table.setItem(r, 1, QTableWidgetItem(row["type_name"]))
        self._st_col.apply()

    # ---- 类型设置 ----
    def _refresh_types(self) -> None:
        rows = st.list_types()
        self._loading = True
        self.type_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            name = row["name"]
            invoice = bool(row["is_invoice"])
            expense = bool(row["can_expense"])
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, name)
            # 内置且进任一业务线 → 类型名加粗；取消后刷新即恢复普通字体
            if row["is_builtin"] and (invoice or expense):
                f = name_item.font()
                f.setBold(True)
                name_item.setFont(f)
            self.type_table.setItem(r, 0, name_item)

            # 开票（业务线）：可点击开关，点击即写库
            inv_item = QTableWidgetItem()
            inv_item.setFlags(inv_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            inv_item.setCheckState(Qt.CheckState.Checked if invoice
                                   else Qt.CheckState.Unchecked)
            inv_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter
                                      | Qt.AlignmentFlag.AlignVCenter)
            inv_item.setData(Qt.ItemDataRole.UserRole, name)
            self.type_table.setItem(r, 1, inv_item)

            # 报销（费用线）：可点击开关，点击即写库
            exp_item = QTableWidgetItem()
            exp_item.setFlags(exp_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            exp_item.setCheckState(Qt.CheckState.Checked if expense
                                   else Qt.CheckState.Unchecked)
            exp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter
                                      | Qt.AlignmentFlag.AlignVCenter)
            exp_item.setData(Qt.ItemDataRole.UserRole, name)
            self.type_table.setItem(r, 2, exp_item)

            # 净额口径：开票时可下拉选择，未开票时灰显且留空（设计点2）
            combo = QComboBox()
            combo.addItems(["开票净额", "收款净额"])
            if invoice:
                combo.setCurrentText(row.get("net_basis") or BASIS_FALLBACK)
            else:
                combo.setCurrentIndex(-1)   # 未开票 → 显示空白
            combo.setEnabled(invoice)
            combo.currentTextChanged.connect(
                lambda txt, n=name: self._on_basis_changed(n, txt))
            self.type_table.setCellWidget(r, 3, combo)

            self.type_table.setItem(r, 4, QTableWidgetItem(row["note"] or ""))
            n_item = QTableWidgetItem(str(row["staff_count"]))
            n_item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
            self.type_table.setItem(r, 5, n_item)

            # 去身份：角色列已移除 —— 报表分组键=类型名本身，分类由开票/报销勾选决定
        self._loading = False
        self._type_col.apply()

    def _on_line_changed(self, item: QTableWidgetItem) -> None:
        """开票 / 报销 开关被点选 → 写库 + 同步本行业务金额方式下拉与加粗态。"""
        if self._loading or item.column() not in (1, 2):
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        flag = item.checkState() == Qt.CheckState.Checked
        # 两开关各自只写自己的列，互不牵连（业务线不碰费用线）
        if item.column() == 1:
            st.set_invoice(name, flag)
        else:
            st.set_can_expense(name, flag)
        # 仅「开票」开关变化影响净额口径下拉的可用态（报销不影响）
        if item.column() == 1:
            combo = self.type_table.cellWidget(item.row(), 3)
            if isinstance(combo, QComboBox):
                # 程序化改动下拉会触发 currentTextChanged，这里必须屏蔽：
                # 否则「取消勾选时清空显示」会被当成一次口径变更写进库里，等于又把口径清掉。
                combo.blockSignals(True)
                combo.setEnabled(flag)
                if flag:
                    # 重新勾选：回填库中保留的口径；罕见脏空值只作显示默认值，不写库
                    combo.setCurrentText((st.get_type(name) or {}).get("net_basis")
                                         or BASIS_FALLBACK)
                else:
                    # 取消勾选：仅清空页面显示，库里的口径不动（回来时按原口径恢复）
                    combo.setCurrentIndex(-1)
                combo.blockSignals(False)
        self._apply_builtin_bold(name)

    def _apply_builtin_bold(self, name: str) -> None:
        """内置类型：进任一业务线时类型名加粗，关掉后恢复普通字体（立即生效）。"""
        t = st.get_type(name)
        if t is None or not t["is_builtin"]:
            return
        bold = bool(t.get("is_invoice") or t.get("can_expense"))
        for r in range(self.type_table.rowCount()):
            it = self.type_table.item(r, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == name:
                f = it.font()
                f.setBold(bold)
                it.setFont(f)
                return

    def _on_basis_changed(self, name: str, basis: str) -> None:
        """净额口径下拉变更 → 写库。

        空串不写：口径只有「开票净额 / 收款净额」两个合法值，空不是口径，
        写进去只会丢配置（下拉留空显示 ≠ 口径为空值）。
        """
        if self._loading or not basis:
            return
        st.set_net_basis(name, basis)

    def _current_type(self):
        r = self.type_table.currentRow()
        if r < 0:
            return None
        item = self.type_table.item(r, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ================================================================== #
    # 花名册 CRUD
    # ================================================================== #
    def _roster_dialog(self, person: dict = None) -> bool:
        """新增/修改共用对话框。person 为已有行（dict）时进入修改模式。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("新增人员" if person is None else f"修改人员：{person['name']}")
        form = QFormLayout(dlg)
        code_edit = QLineEdit(person["code"] if person else "")
        code_edit.setPlaceholderText("可空；非空须唯一")
        name_edit = QLineEdit(person["name"] if person else "")
        name_edit.setPlaceholderText("姓名即主键")
        id_edit = QLineEdit(person["id_card"] if person else "")
        id_edit.setPlaceholderText("身份证号，可空；非空须唯一")
        phone_edit = QLineEdit(person["phone"] if person else "")
        hire_edit = QLineEdit(person["hire_month"] if person else "")
        hire_edit.setPlaceholderText("如 2025-04，留空=始终在名单")
        leave_edit = QLineEdit(person["leave_month"] if person else "")
        leave_edit.setPlaceholderText("离职月份，如 2026-03；在职留空")
        note_edit = QLineEdit(person["note"] if person else "")
        if person is not None:
            name_edit.setReadOnly(True)  # 姓名主键不可改，改名走删除重建
        form.addRow("编号", code_edit)
        form.addRow("姓名", name_edit)
        form.addRow("身份证号", id_edit)
        form.addRow("手机号", phone_edit)
        form.addRow("入职月份", hire_edit)
        form.addRow("离职月份", leave_edit)
        form.addRow("备注", note_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "姓名不能为空")
            return False
        code = code_edit.text().strip()
        id_card = id_edit.text().strip()
        # 唯一性预检（部分唯一索引：空值不冲突，非空才唯一）
        conn = get_conn()
        try:
            if code:
                dup = conn.execute(
                    "SELECT 1 FROM staff_roster WHERE code=? AND name<>?", (code, name)).fetchone()
                if dup:
                    QMessageBox.warning(self, "编号重复", f"编号「{code}」已被他人使用")
                    return False
            if id_card:
                dup = conn.execute(
                    "SELECT 1 FROM staff_roster WHERE id_card=? AND name<>?",
                    (id_card, name)).fetchone()
                if dup:
                    QMessageBox.warning(self, "身份证号重复",
                                        f"身份证号「{id_card}」已被他人使用")
                    return False
        finally:
            conn.close()
        if person is None:
            try:
                st.add_roster_person(code, name, id_card, phone_edit.text().strip(),
                                     hire_edit.text().strip(), leave_edit.text().strip(),
                                     note_edit.text().strip())
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "新增失败", str(e))
                return False
        else:
            try:
                st.update_roster_person(code, name, id_card, phone_edit.text().strip(),
                                        hire_edit.text().strip(), leave_edit.text().strip(),
                                        note_edit.text().strip())
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "修改失败", str(e))
                return False
        return True

    def add_roster(self) -> None:
        if self._roster_dialog():
            self.refresh()

    def edit_roster(self) -> None:
        row = self.roster_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一名人员（或双击）")
            return
        name = self.roster_table.item(row, 1).text()
        person = st.get_roster_person(name)
        if person is None:
            return
        if self._roster_dialog(person):
            self.refresh()

    def delete_roster(self) -> None:
        row = self.roster_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一名人员")
            return
        name = self.roster_table.item(row, 1).text()
        ret = QMessageBox.question(
            self, "确认删除", f"从花名册中删除「{name}」？\n（有业务数据引用则禁止删除）")
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            st.delete_roster_person(name)
        except st.StaffInUseError as e:
            QMessageBox.warning(self, "无法删除", str(e))
            return
        except st.StaffTypeError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.refresh()

    def import_staff(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择职工清单", "", "Excel 文件 (*.xls *.xlsx *.xlsm)")
        if not path:
            return
        try:
            staff, file_hash = parse_staff_file(path)
        except ImportError_ as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return

        # ===== 空类型硬拦（方案 B，需求方 2026-09-25 拍板）=====
        missing = [name for name, stype, _note in staff if not (stype or "").strip()]
        if missing:
            shown = missing[:10]
            tail = "" if len(missing) <= 10 else f" 等 {len(missing) - 10} 人"
            QMessageBox.warning(
                self, "导入被拦下",
                f"职工清单里有 {len(missing)} 人未填写员工类型：\n"
                "　" + "、".join(shown) + tail + "\n\n"
                "未填类型的员工一律判为不参与结算，导入后导入费用台账时也会被逐行"
                "拒绝；故本次整批未导入（改好清单可重新导入）。\n"
                "需要的类型请到「类型设置」页新建，或在清单里补齐类型列。")
            return

        self._maybe_warn_settle_order()

        conn = get_conn()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "INSERT INTO import_batch (batch_type, period, file_name, file_hash, imported_at)"
                " VALUES (?,?,?,?,?)",
                ("staff", "0000", path.replace("\\", "/").split("/")[-1], file_hash, now))
            batch_id = cur.lastrowid
            st.ensure_types(conn, [s[1] for s in staff if s[1]])
            n_new, n_dup = 0, 0
            for name, stype, note in staff:
                exists = conn.execute(
                    "SELECT 1 FROM staff_roster WHERE name=?", (name,)).fetchone()
                st.sync_imported_staff(conn, name, stype, note)
                if exists:
                    n_dup += 1
                else:
                    n_new += 1
            conn.commit()
            QMessageBox.information(self, "导入完成", f"新增 {n_new} 人，更新 {n_dup} 人。")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "导入失败", str(e))
        finally:
            conn.close()
        self.refresh()

    def export_roster(self) -> None:
        """导出花名册为 Excel。"""
        rows = st.list_roster()
        if not rows:
            QMessageBox.information(self, "提示", "当前没有可导出的人员数据")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出花名册", "花名册.xlsx", "Excel 文件 (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "花名册"
            ws.append(["编号", "姓名", "身份证号", "手机号", "入职月份", "离职月份", "备注"])
            for r in rows:
                ws.append([r["code"] or "", r["name"], r["id_card"] or "", r["phone"] or "",
                           r["hire_month"] or "", r["leave_month"] or "", r["note"] or ""])
            wb.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成",
                                f"已导出 {len(rows)} 名人员到：\n{path}")

    # ================================================================== #
    # 员工类型（人 × 类型）CRUD
    # ================================================================== #
    def _staff_type_dialog(self) -> bool:
        """新增人-类型关联：人员选自花名册，类型选自类型设置。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("新增人员-类型关联")
        form = QFormLayout(dlg)
        person_combo = QComboBox()
        for p in st.list_roster():
            person_combo.addItem(p["name"], userData=p["name"])
        if person_combo.count() == 0:
            QMessageBox.warning(self, "无人员",
                                 "「花名册」里还没有人员，请先到花名册页添加或导入人员。")
            return False
        type_combo = self._type_combo()
        if type_combo.count() == 0:
            QMessageBox.warning(self, "无类型",
                                 "「类型设置」里还没有类型，请先新建类型。")
            return False
        form.addRow("人员（来自花名册）", person_combo)
        form.addRow("类型", type_combo)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        name = person_combo.currentData()
        tname = type_combo.currentData()
        if not name or not tname:
            QMessageBox.warning(self, "提示", "请选择人员和类型")
            return False
        try:
            st.add_person_type(name, tname)
        except st.StaffTypeError as e:
            QMessageBox.warning(self, "添加失败", str(e))
            return False
        return True

    def add_staff_type(self) -> None:
        if self._staff_type_dialog():
            self.refresh()

    def delete_staff_type(self) -> None:
        row = self.staff_type_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条人员-类型关联")
            return
        name = self.staff_type_table.item(row, 0).text()
        tname = self.staff_type_table.item(row, 1).text()
        ret = QMessageBox.question(
            self, "确认删除", f"解除「{name}」的「{tname}」类型关联？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            st.remove_person_type(name, tname)
        except st.StaffTypeError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.refresh()

    def export_staff_types(self) -> None:
        """导出人-类型关联为 Excel。"""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT m.name AS name, m.type_name AS type_name FROM staff_type_map m "
                "JOIN staff_roster r ON r.name = m.name ORDER BY m.name, m.type_name").fetchall()
        finally:
            conn.close()
        if not rows:
            QMessageBox.information(self, "提示", "当前没有可导出的关联数据")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出员工类型", "员工类型.xlsx", "Excel 文件 (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "员工类型"
            ws.append(["姓名", "类型"])
            for r in rows:
                ws.append([r["name"], r["type_name"]])
            wb.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成",
                                f"已导出 {len(rows)} 条关联到：\n{path}")

    # ================================================================== #
    # 类型设置 CRUD
    # ================================================================== #
    def type_add(self) -> None:
        name, ok = QInputDialog.getText(self, "新增员工类型", "类型名称（≤20 字符）：")
        if not ok or not name.strip():
            return
        note, ok2 = QInputDialog.getText(
            self, "新增员工类型",
            "说明（可留空；仅作身份标签，与是否参与结算无关）：")
        if not ok2:
            return
        try:
            st.add_type(name, note)
        except st.StaffTypeError as e:
            QMessageBox.warning(self, "新增失败", str(e))
            return
        self._refresh_types()

    def type_rename(self) -> None:
        name = self._current_type()
        if not name:
            QMessageBox.information(self, "提示", "请先选择一个类型")
            return
        new, ok = QInputDialog.getText(self, "改名", "新类型名称：", text=name)
        if not ok or not new.strip() or new.strip() == name:
            return
        try:
            st.rename_type(name, new)
        except st.StaffTypeError as e:
            QMessageBox.warning(self, "改名失败", str(e))
            return
        self.refresh()

    def type_note(self) -> None:
        name = self._current_type()
        if not name:
            QMessageBox.information(self, "提示", "请先选择一个类型")
            return
        cur = st.get_type(name) or {}
        note, ok = QInputDialog.getText(self, "编辑说明", f"「{name}」的说明：",
                                        text=cur.get("note") or "")
        if not ok:
            return
        st.set_note(name, note)
        self._refresh_types()

    def type_delete(self) -> None:
        name = self._current_type()
        if not name:
            QMessageBox.information(self, "提示", "请先选择一个类型")
            return
        ret = QMessageBox.question(self, "确认删除", f"删除员工类型「{name}」？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            st.delete_type(name)
        except st.StaffTypeError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self._refresh_types()

    def type_move(self, direction: int) -> None:
        name = self._current_type()
        if not name:
            return
        st.move_type(name, direction)
        self._refresh_types()
        self._reselect_type(name)

    def _reselect_type(self, name: str) -> None:
        """移动后让选中行跟随该类型（按类型名定位新行）。"""
        for r in range(self.type_table.rowCount()):
            item = self.type_table.item(r, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == name:
                self.type_table.selectRow(r)
                self.type_table.setCurrentCell(r, 0)
                return

    def _type_combo(self, current: str = "") -> QComboBox:
        """员工类型下拉（类型设置里的类型名）。不带初值时不预选。"""
        combo = QComboBox()
        for t in st.list_types():
            combo.addItem(t["name"], userData=t["name"])
        if current:
            idx = combo.findData(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        elif combo.count():
            combo.setCurrentIndex(-1)
        return combo

    def _maybe_warn_settle_order(self) -> None:
        """导入职工清单前的顺序指引：类型表为空时提示一次（只提醒，不代办）。"""
        if self._settle_order_hinted:
            return
        # 先置位再查询：无论本次是否真的弹窗，同一实例都不再重复打扰
        self._settle_order_hinted = True
        try:
            if len(st.list_types()) > 0:
                return
        except Exception:  # noqa: BLE001 读类型表失败 → 不提示，导入照旧
            return
        QMessageBox.information(
            self, "导入顺序提示",
            "建议先到「类型设置」页建立类型并勾选「参与结算」，再导入职工清单；"
            "否则后续导入费用台账会因经办人未参与结算被逐行拒绝。")
