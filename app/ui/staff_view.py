"""员工管理页：职工花名册展示 + 导入/增删/停用"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.widgets import (SubtitleLabel, CaptionLabel, PrimaryPushButton, PushButton)
from app.ui.column_layout import install_column_layout
from app.db import get_conn
from app.importer.staff_import import ImportError_, parse_staff_file


class StaffView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = SubtitleLabel("员工管理")
        lay.addWidget(title)
        hint = QLabel("职工花名册（基础数据）：台账导入时校验经办人是否在此名单中。")
        hint.setObjectName("pageHint")
        lay.addWidget(hint)

        btns = QHBoxLayout()
        self.btn_import = QPushButton("导入职工清单（模板）")
        self.btn_import.setObjectName("primary")
        self.btn_import.clicked.connect(self.import_staff)
        self.btn_add = QPushButton("手动添加")
        self.btn_add.clicked.connect(self.add_manual)
        self.btn_toggle = QPushButton("停用 / 启用")
        self.btn_toggle.clicked.connect(self.toggle_active)
        btns.addWidget(self.btn_import)
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_toggle)
        btns.addStretch()
        lay.addLayout(btns)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["姓名", "类型", "状态", "入职月份", "备注"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.table)
        install_header_filter(self.table)
        self._col = install_column_layout(self.table, "staff", "main")
        lay.addWidget(self.table)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        """切换到本页时自动刷新数据"""
        super().showEvent(event)
        self.refresh()

    # ---- 数据 ----
    def refresh(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT name, staff_type, is_active, hire_month, note, source FROM staff ORDER BY is_active DESC, name"
            ).fetchall()
        finally:
            conn.close()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(row["name"]))
            self.table.setItem(r, 1, QTableWidgetItem(row["staff_type"]))
            status = "在职" if row["is_active"] else "停用"
            item = QTableWidgetItem(status)
            item.setForeground(Qt.GlobalColor.gray if not row["is_active"] else Qt.GlobalColor.black)
            self.table.setItem(r, 2, item)
            self.table.setItem(r, 3, QTableWidgetItem(row["hire_month"] or ""))
            self.table.setItem(r, 4, QTableWidgetItem(row["note"] or ""))
        self._col.apply()

    # ---- 动作 ----
    def import_staff(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择职工清单", "", "Excel 文件 (*.xls *.xlsx *.xlsm)")
        if not path:
            return
        try:
            staff, file_hash = parse_staff_file(path)
        except ImportError_ as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return

        conn = get_conn()
        try:
            # 记录导入批次
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "INSERT INTO import_batch (batch_type, period, file_name, file_hash, imported_at) VALUES (?,?,?,?,?)",
                ("staff", "0000", path.split("/")[-1].split("\\")[-1], file_hash, now),
            )
            batch_id = cur.lastrowid
            n_new, n_dup = 0, 0
            for name, stype, note in staff:
                if not stype:
                    stype = "聘用"
                r = conn.execute("SELECT id FROM staff WHERE name=?", (name,)).fetchone()
                if r:
                    conn.execute(
                        "UPDATE staff SET staff_type=?, note=?, is_active=1 WHERE id=?",
                        (stype, note, r["id"]),
                    )
                    n_dup += 1
                else:
                    conn.execute(
                        "INSERT INTO staff (name, staff_type, is_active, note, source, import_batch_id) VALUES (?,?,1,?,?,?)",
                        (name, stype, note, "import", batch_id),
                    )
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
        from PySide6.QtWidgets import QDialog, QLineEdit, QComboBox, QDialogButtonBox, QFormLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("手动添加职工")
        form = QFormLayout(dlg)
        name_edit = QLineEdit()
        type_combo = QComboBox()
        type_combo.addItems(["合伙", "聘用", "兼职", "挂靠"])
        note_edit = QLineEdit()
        hire_edit = QLineEdit()
        hire_edit.setPlaceholderText("如 2025-04，留空=始终在名单")
        form.addRow("姓名", name_edit)
        form.addRow("类型", type_combo)
        form.addRow("入职月份", hire_edit)
        form.addRow("备注", note_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
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
            conn.execute(
                "INSERT OR IGNORE INTO staff (name, staff_type, is_active, hire_month, note, source) VALUES (?,?,1,?,?,?)",
                (name, type_combo.currentText(), hire_edit.text().strip(), note_edit.text().strip(), "manual"),
            )
            conn.commit()
        finally:
            conn.close()
        self.refresh()

    def edit_selected(self) -> None:
        """编辑选中员工：类型/入职月份/备注"""
        from PySide6.QtWidgets import QDialog, QLineEdit, QComboBox, QDialogButtonBox, QFormLayout
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
        type_combo = QComboBox()
        for t in ["合伙", "聘用", "兼职", "挂靠", "其他"]:
            type_combo.addItem(t, userData=t)
        type_combo.setCurrentText(s["staff_type"] or "聘用")
        hire_edit = QLineEdit(s["hire_month"] or "")
        hire_edit.setPlaceholderText("如 2025-04，留空=始终在名单")
        note_edit = QLineEdit(s["note"] or "")
        form.addRow("类型", type_combo)
        form.addRow("入职月份", hire_edit)
        form.addRow("备注", note_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # 人员类型修改二次确认（防误操作）
        new_type = type_combo.currentData()
        if (s["staff_type"] or "") != new_type:
            ret = QMessageBox.question(
                self, "确认修改类型",
                f"将 {name} 的人员类型从「{s['staff_type']}」改为「{new_type}」？\n"
                f"注意：类型只作默认身份，历史数据的身份不受影响；如身份不符请到「台账查看 → 费用台账」修改。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        conn = get_conn()
        try:
            changes = []
            for f, o, n in [("staff_type", s["staff_type"], type_combo.currentData()),
                            ("hire_month", s["hire_month"], hire_edit.text().strip() or ""),
                            ("note", s["note"], note_edit.text().strip())]:
                if str(o or "") != str(n or ""):
                    changes.append((f, o, n))
            if changes:
                conn.execute("UPDATE staff SET staff_type=?, hire_month=?, note=? WHERE name=?",
                             (type_combo.currentData(), hire_edit.text().strip() or "",
                              note_edit.text().strip(), name))
                conn.commit()
        finally:
            conn.close()
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
