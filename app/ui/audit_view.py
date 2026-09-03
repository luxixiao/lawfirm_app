"""统一审计中心组件（AuditView）

- 可复用 QWidget：渲染 change_log 修改记录，带多维过滤。
- 同一份代码被两处复用，避免重复：
  * 台账数据页「修改记录」Tab：无预设（默认下拉=发票相关）。
  * 发票台账页 / 销项发票页 右击「查看修改记录」：预设 table_name + record_id（锁定到某行）。
- change_log 是统一数据源（已按 (table_name, record_id) 建 key），本组件只读展示。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.engine.change_log import (
    INVOICE_RELATED, TABLE_OPTIONS, build_friendly_table, fetch_log,
)
from app.ui.column_layout import install_column_layout
from app.ui.table_features import install_accent_header
from app.ui.widgets import CaptionLabel, PushButton, page_header


class AuditView(QWidget):
    """修改记录统一视图。

    table_name / record_id 为预设过滤（右击某行查看其历史时传入）。
    预设 record_id 时，记录搜索框与表下拉会被锁定，仅展示该行历史。

    展示列：修改时间 | 修改表名 | 发票号码 | 对方 | 金额 | 经办人 | 旧值 | 新值 | 备注
    （发票号码/对方/金额/经办人为修改前快照，由编辑入口在写日志时一并存入）
    """

    _HEADERS = ["修改时间", "修改表名", "字段", "发票号码", "对方", "金额", "经办人", "旧值", "新值", "备注"]
    _KEYS = ["created_at", "friendly_table", "field", "invoice_no", "buyer", "amount",
             "handlers", "old_value", "new_value", "note"]

    def __init__(self, parent: QWidget | None = None, *,
                 table_name: str | None = None, record_id: str | None = None) -> None:
        super().__init__(parent)
        self._preset_table = table_name
        self._preset_record = record_id

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ---- 页头：标题 + ? 帮助图标 ----
        hdr = QWidget()
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(0, 14, 0, 0)
        hdr_lay.setSpacing(8)
        hdr_lay.addWidget(page_header(
            "修改记录",
            "系统所有数据修改的留痕中心。可按表、记录 ID、关键字筛选，查看每笔改动的字段、旧值/新值与备注。"
            "右击业务表行「查看修改记录」会自动定位到该行历史。"))
        lay.addWidget(hdr)

        # 筛选条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_table = QComboBox()
        for val, label in TABLE_OPTIONS:
            self.f_table.addItem(label, val)
        self.f_record = QLineEdit()
        self.f_record.setPlaceholderText("记录 ID")
        self.f_keyword = QLineEdit()
        self.f_keyword.setPlaceholderText("关键字（记录/字段/旧值/新值/备注）")
        self.btn_refresh = PushButton("刷新")
        bar.addWidget(QLabel("表："))
        bar.addWidget(self.f_table, 0)
        bar.addWidget(QLabel("记录："))
        bar.addWidget(self.f_record, 1)
        bar.addWidget(QLabel("关键字："))
        bar.addWidget(self.f_keyword, 2)
        bar.addWidget(self.btn_refresh, 0)
        lay.addLayout(bar)

        # 表格
        self.table = QTableWidget(0, len(self._HEADERS))
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels(self._HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.table)
        self._filter = install_header_filter(self.table)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        self._col = install_column_layout(self.table, "audit")
        self._col.set_sort_callback(self._do_sort)
        self._rows: list = []
        self._sort_col: int = -1
        self._sort_asc: bool = True

        # 信号
        self.f_table.currentIndexChanged.connect(lambda _: self.load())
        self.f_record.editingFinished.connect(self.load)
        self.f_keyword.editingFinished.connect(self.load)
        self.f_record.returnPressed.connect(self.load)
        self.f_keyword.returnPressed.connect(self.load)
        self.btn_refresh.clicked.connect(self.load)

        # 预设（右击查看某行历史）
        if self._preset_table:
            idx = self.f_table.findData(self._preset_table)
            if idx >= 0:
                self.f_table.setCurrentIndex(idx)
            self.f_table.setEnabled(False)
        if self._preset_record is not None:
            self.f_record.setText(str(self._preset_record))
            self.f_record.setEnabled(False)

        self.load()

    def load(self) -> None:
        table_name = self.f_table.currentData() or None
        # 预设优先（即使控件被锁定，也用预设值）
        if self._preset_table:
            table_name = self._preset_table
        record_id = (str(self._preset_record) if self._preset_record is not None
                     else (self.f_record.text().strip() or None))
        keyword = self.f_keyword.text().strip() or None

        rows = fetch_log(
            limit=2000,
            table_name=table_name,
            record_id=record_id,
            keyword=keyword,
        )
        self._rows = rows
        self._fill(rows)
        self.lbl_stat.setText(f"共 {len(rows)} 条修改记录"
                              + ("（已锁定到该行）" if self._preset_record is not None else ""))

    def _fill(self, rows) -> None:
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(self._KEYS):
                if key == "friendly_table" and not (row.get("friendly_table") or ""):
                    # 旧记录无快照：按 table_name 回退友好名
                    v = build_friendly_table(row.get("table_name") or "")
                else:
                    v = row.get(key) or ""
                item = QTableWidgetItem("" if v is None else str(v))
                if key in ("old_value", "new_value", "buyer", "amount", "handlers",
                           "invoice_no", "field"):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                          | Qt.AlignmentFlag.AlignVCenter)
                # 事件类字段（(导入修正)/(同步)/(新增)/(删除)…）淡蓝底纹，与真字段编辑分层
                if key == "field" and str(v or "").startswith("("):
                    item.setBackground(QColor("#EAF2FB"))
                self.table.setItem(r, c, item)
        self._col.apply()
        # 重新叠加右键「按列筛选」（与搜索/刷新 AND 组合）：_fill 重建表格会清除 setRowHidden 状态
        self._filter.apply_to_table(self.table)

    def _do_sort(self, logical: int, asc: bool) -> None:
        """表头右键「升序/降序」排序回调：按逻辑列号对本页行重排并加原生排序标识。"""
        self._sort_col = logical
        self._sort_asc = asc
        self._col.set_sort_col(logical)
        key = self._KEYS[logical]
        rows = sorted(self._rows,
                      key=lambda r, k=key: str(r.get(k) or ""),
                      reverse=not asc)
        self._fill(rows)
        hdr = self.table.horizontalHeader()
        hdr.setSortIndicator(logical,
                             Qt.SortOrder.DescendingOrder if not asc else Qt.SortOrder.AscendingOrder)
