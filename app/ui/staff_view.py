"""员工管理页：员工名单（增删改/停用/导入）+ 员工类型（自定义增删改查）

- Tab1「员工名单」：花名册，导入 / 手动添加 / 修改 / 删除 / 停用启用。
  删除前检查业务数据引用：有引用禁止删除并提示改用停用（避免结算口径丢失）。
- Tab2「员工类型」：类型可自定义。合伙/聘用/兼职为内置结算类型，
  禁止删除与改名（改名会断结算口径），说明可改；自定义类型可自由增删改名。
- 口径：只有 合伙 / 聘用 / 兼职 参与业务收入计算，其余类型仅作身份标签。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import staff_type as st
from app.importer.staff_import import ImportError_, parse_staff_file
from app.ui import scale
from app.ui.column_layout import install_column_layout
from app.ui.table_features import install_common_features, install_header_filter
from app.ui.widgets import (
    CaptionLabel, PrimaryPushButton, PushButton,
)


class StaffView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        hint = QLabel("职工花名册（基础数据）：台账导入时校验经办人是否在此名单中。"
                      "类型中只有「合伙 / 聘用 / 兼职」参与业务收入计算。")
        hint.setObjectName("pageHint")
        lay.addWidget(hint)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setObjectName("pageTitleBar")
        self.tabs.addTab(self._build_staff_tab(), "员工名单")
        self.tabs.addTab(self._build_type_tab(), "员工类型")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        lay.addWidget(self.tabs, 1)

        self.refresh()

    # ================================================================== #
    # Tab1：员工名单
    # ================================================================== #
    def _build_staff_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)

        btns = QHBoxLayout()
        self.btn_import = QPushButton("导入职工清单（模板）")
        self.btn_import.setObjectName("primary")
        self.btn_import.clicked.connect(self.import_staff)
        self.btn_add = QPushButton("手动添加")
        self.btn_add.clicked.connect(self.add_manual)
        self.btn_edit = QPushButton("修改")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_toggle = QPushButton("停用 / 启用")
        self.btn_toggle.clicked.connect(self.toggle_active)
        for b in (self.btn_import, self.btn_add, self.btn_edit,
                  self.btn_delete, self.btn_toggle):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["姓名", "类型", "状态", "入职月份", "备注"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        install_common_features(self.table)
        install_header_filter(self.table)
        self._col = install_column_layout(self.table, "staff", "main")
        lay.addWidget(self.table, 1)
        return w

    # ================================================================== #
    # Tab2：员工类型
    # ================================================================== #
    def _build_type_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addWidget(CaptionLabel(
            "自定义员工类型。合伙 / 聘用 / 兼职 为内置结算类型：禁止删除与改名，说明可改；"
            "其余类型仅作身份标签，不参与业务收入计算。"))

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

        self.type_table = QTableWidget(0, 4)
        self.type_table.setHorizontalHeaderLabels(["类型", "参与结算", "说明", "人数"])
        self.type_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.type_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.type_table.verticalHeader().setVisible(False)
        self.type_table.setColumnWidth(0, scale.px(120))
        self.type_table.setColumnWidth(1, scale.px(80))
        self.type_table.setColumnWidth(3, scale.px(60))
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
        self._refresh_staff()
        self._refresh_types()

    # ---- 员工名单 ----
    def _refresh_staff(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT name, staff_type, is_active, hire_month, note FROM staff "
                "ORDER BY is_active DESC, name").fetchall()
        finally:
            conn.close()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(row["name"]))
            self.table.setItem(r, 1, QTableWidgetItem(row["staff_type"] or ""))
            item = QTableWidgetItem("在职" if row["is_active"] else "停用")
            item.setForeground(Qt.GlobalColor.gray if not row["is_active"]
                               else Qt.GlobalColor.black)
            self.table.setItem(r, 2, item)
            self.table.setItem(r, 3, QTableWidgetItem(row["hire_month"] or ""))
            self.table.setItem(r, 4, QTableWidgetItem(row["note"] or ""))
        self._col.apply()

    # ---- 员工类型 ----
    def _refresh_types(self) -> None:
        rows = st.list_types()
        self.type_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            name_item = QTableWidgetItem(row["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, row["name"])
            if row["is_builtin"]:
                f = name_item.font()
                f.setBold(True)
                name_item.setFont(f)
            self.type_table.setItem(r, 0, name_item)
            calc = "是" if st.is_computable(row["name"]) else "—"
            c_item = QTableWidgetItem(calc)
            c_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter
                                    | Qt.AlignmentFlag.AlignVCenter)
            self.type_table.setItem(r, 1, c_item)
            self.type_table.setItem(r, 2, QTableWidgetItem(row["note"] or ""))
            n_item = QTableWidgetItem(str(row["staff_count"]))
            n_item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
            self.type_table.setItem(r, 3, n_item)
        self._type_col.apply()

    def _current_type(self):
        r = self.type_table.currentRow()
        if r < 0:
            return None
        item = self.type_table.item(r, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ================================================================== #
    # 类型 CRUD
    # ================================================================== #
    def type_add(self) -> None:
        name, ok = QInputDialog.getText(self, "新增员工类型", "类型名称（≤20 字符）：")
        if not ok or not name.strip():
            return
        note, ok2 = QInputDialog.getText(
            self, "新增员工类型",
            "说明（可留空；不含「合伙 / 聘用 / 兼职」则不参与结算计算）：")
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

    # ================================================================== #
    # 员工 CRUD
    # ================================================================== #
    def _type_combo(self, current: str = "") -> QComboBox:
        combo = QComboBox()
        for t in st.list_types():
            combo.addItem(t["name"], userData=t["name"])
        if current:
            idx = combo.findData(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        return combo

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

        conn = get_conn()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "INSERT INTO import_batch (batch_type, period, file_name, file_hash, imported_at)"
                " VALUES (?,?,?,?,?)",
                ("staff", "0000", path.replace("\\", "/").split("/")[-1], file_hash, now))
            batch_id = cur.lastrowid
            # 导入的类型若不在类型表中，自动补入（is_builtin=0）
            st.ensure_types(conn, [s[1] for s in staff if s[1]] or ["聘用"])
            n_new, n_dup = 0, 0
            for name, stype, note in staff:
                if not stype:
                    stype = "聘用"
                r = conn.execute("SELECT id FROM staff WHERE name=?", (name,)).fetchone()
                if r:
                    conn.execute(
                        "UPDATE staff SET staff_type=?, note=?, is_active=1 WHERE id=?",
                        (stype, note, r["id"]))
                    n_dup += 1
                else:
                    conn.execute(
                        "INSERT INTO staff (name, staff_type, is_active, note, source,"
                        " import_batch_id) VALUES (?,?,1,?,?,?)",
                        (name, stype, note, "import", batch_id))
                    n_new += 1
            conn.commit()
            QMessageBox.information(self, "导入完成", f"新增 {n_new} 人，更新 {n_dup} 人。")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "导入失败", str(e))
        finally:
            conn.close()
        self.refresh()

    def add_manual(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("手动添加职工")
        form = QFormLayout(dlg)
        name_edit = QLineEdit()
        type_combo = self._type_combo()
        hire_edit = QLineEdit()
        hire_edit.setPlaceholderText("如 2025-04，留空=始终在名单")
        note_edit = QLineEdit()
        form.addRow("姓名", name_edit)
        form.addRow("类型", type_combo)
        form.addRow("入职月份", hire_edit)
        form.addRow("备注", note_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "姓名不能为空")
            return
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO staff (name, staff_type, is_active, hire_month, note,"
                " source) VALUES (?,?,1,?,?,?)",
                (name, type_combo.currentData(), hire_edit.text().strip(),
                 note_edit.text().strip(), "manual"))
            conn.commit()
            if cur.rowcount == 0:
                QMessageBox.information(self, "提示", f"职工「{name}」已存在，未重复添加")
        finally:
            conn.close()
        self.refresh()

    def edit_selected(self) -> None:
        """编辑选中员工：类型/入职月份/备注"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一名职工（或双击）")
            return
        name = self.table.item(row, 0).text()
        conn = get_conn()
        try:
            s = conn.execute("SELECT * FROM staff WHERE name=?", (name,)).fetchone()
        finally:
            conn.close()
        if s is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑员工：{name}")
        form = QFormLayout(dlg)
        type_combo = self._type_combo(s["staff_type"] or "聘用")
        hire_edit = QLineEdit(s["hire_month"] or "")
        hire_edit.setPlaceholderText("如 2025-04，留空=始终在名单")
        note_edit = QLineEdit(s["note"] or "")
        form.addRow("类型", type_combo)
        form.addRow("入职月份", hire_edit)
        form.addRow("备注", note_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_type = type_combo.currentData()
        if (s["staff_type"] or "") != new_type:
            ret = QMessageBox.question(
                self, "确认修改类型",
                f"将 {name} 的人员类型从「{s['staff_type']}」改为「{new_type}」？\n"
                f"注意：类型决定业务收入口径（合伙=按开票，聘用/兼职=按收款，其余=0）；"
                f"历史数据的身份不受影响。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE staff SET staff_type=?, hire_month=?, note=? WHERE name=?",
                (new_type, hire_edit.text().strip() or "", note_edit.text().strip(), name))
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def delete_selected(self) -> None:
        """删除员工：有业务数据引用时拒绝，提示改用停用。"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一名职工")
            return
        name = self.table.item(row, 0).text()
        ret = QMessageBox.question(
            self, "确认删除", f"确定从花名册中删除「{name}」？\n（该操作不可恢复）")
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            st.delete_staff(name)
        except st.StaffInUseError as e:
            QMessageBox.warning(self, "无法删除", str(e))
            return
        except st.StaffTypeError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.refresh()

    def toggle_active(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一名职工")
            return
        name = self.table.item(row, 0).text()
        conn = get_conn()
        try:
            conn.execute("UPDATE staff SET is_active = 1 - is_active WHERE name=?", (name,))
            conn.commit()
        finally:
            conn.close()
        self.refresh()
