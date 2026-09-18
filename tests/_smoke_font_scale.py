"""offscreen 冒烟：全局字号 5 档缩放（scale 底座 + QSS/标题栏/侧栏/表格派生 + 广播）。

运行：QT_QPA_PLATFORM=offscreen python tests/_smoke_font_scale.py

覆盖：
- scale 底座：档位表、倍率、px 派生、prefs 持久化 roundtrip（临时文件，不污染真实 prefs）
- QSS 按档位生成：标准档零回归抽查 + 各档字号变化
- AppTitleBar 高度/字号派生
- SidebarWidget.reapply_metrics 行高重算
- 表格行高派生（以统一导入对话框为例）
- 广播链 apply_font_step 的各环节单元验证（MainWindow 不在沙箱构造）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="font_scale_smoke_"))
import app.ui.scale as scale  # noqa: E402
scale.PREFS_PATH = TMP / "prefs.json"   # 持久化写临时文件，不污染真实 prefs.json
import app.ui.style as style  # noqa: E402
style.PREFS_PATH = TMP / "prefs.json"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
style.apply_skin(app, "notion_light")

from app.ui.main_window import AppTitleBar  # noqa: E402
from app.ui import sidebar as sb  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")


def qss_at(step: int) -> str:
    scale.set_step(step)
    return style.qss_for("notion_light")


# ------------------------------------------------- 1) scale 底座
# 隔离用户偏好：scale 导入时读 prefs.json 的 font_step（用户可能停在非默认档），
# 先归位默认档再断言「默认档」语义（set_step 仅改内存，不写 prefs.json）。
scale.set_step(scale.DEFAULT_STEP)
check("5 档定义", scale.STEPS == (11, 12, 13, 14, 15) and len(scale.LABELS) == 5,
      f"got={scale.STEPS}")
check("默认档 = 标准(13px)", scale.DEFAULT_STEP == 2 and scale.base_size() == 13,
      f"got={scale.base_size()}")
for i, expect in enumerate([round(11 / 13, 4), round(12 / 13, 4), 1.0,
                            round(14 / 13, 4), round(15 / 13, 4)]):
    scale.set_step(i)
    check(f"档{i} 倍率", abs(scale.ratio() - expect) < 1e-4, f"got={scale.ratio():.4f}")
scale.set_step(2)
check("标准档 px 恒等", scale.px(34) == 34 and scale.px(9) == 9 and scale.px(1) == 1)
scale.set_step(4)
check("大两号 px(34)=39", scale.px(34) == 39, f"got={scale.px(34)}")
check("px 最小 1 兜底", scale.px(0.2) == 1, f"got={scale.px(0.2)}")
scale.set_step(0)
check("小两号 px(34)=29", scale.px(34) == 29, f"got={scale.px(34)}")

# 持久化 roundtrip（已指向临时 prefs）
scale.set_step(4)
scale.save_step(4)
raw = json.loads(scale.PREFS_PATH.read_text(encoding="utf-8"))
check("save_step 落盘 font_step=4", raw.get("font_step") == 4, f"got={raw}")
scale.set_step(0)   # 内存与磁盘不一致，模拟下次启动
check("load_step 回读=4", scale.load_step() == 4, f"got={scale.load_step()}")
scale.save_step(2)
scale.set_step(2)

# ------------------------------------------------- 2) QSS 按档位生成
std = qss_at(2)
check("标准档 QSS：全局 13px", "font-size: 13px" in std)
check("标准档 QSS 零回归抽查：navItem 31px", "min-height: 31px" in std)
check("标准档 QSS 零回归抽查：pageTitle 20px", "font-size: 20px" in std)
big = qss_at(4)
import re as _re  # noqa: E402
_m = _re.search(r"\*\s*\{[^}]*font-size:\s*(\d+)px", big)
check("大两号 QSS：全局规则 15px", _m is not None and _m.group(1) == "15",
      f"got={_m.group(1) if _m else None}")
check("大两号 QSS：navItem 36px", "min-height: 36px" in big, )
small = qss_at(0)
check("小两号 QSS：全局 11px", "font-size: 11px" in small)

# ------------------------------------------------- 3) AppTitleBar 派生
scale.set_step(2)
tb_std = AppTitleBar()
check("标准档标题栏高 36", tb_std.height() == 36, f"got={tb_std.height()}")
scale.set_step(4)
tb_big = AppTitleBar()
check("大两号标题栏高 42", tb_big.height() == scale.px(36) == 42, f"got={tb_big.height()}")
tb_big.set_page_title("导入台账")
check("大两号标题栏字号 15px", "font:15px" in tb_big.titleLabel.styleSheet(),
      tb_big.titleLabel.styleSheet()[:60])

# ------------------------------------------------- 4) 侧栏 reapply_metrics
scale.set_step(2)
GROUPS = [("数据导入", [("import", "导入台账")]), ("数据维护", [("staff", "员工管理")])]
w = sb.SidebarWidget(GROUPS)
w.show()
app.processEvents()
hdr = next(iter(w._header_buttons.values()))
check("标准档侧栏大类行高 34", hdr.height() == 34, f"got={hdr.height()}")
scale.set_step(4)
w.reapply_metrics()
check("大两号侧栏行高 39", hdr.height() == 39, f"got={hdr.height()}")
check("侧栏展开宽派生 277", sb._px(sb.BASE_W_EXPAND) == 277, f"got={sb._px(sb.BASE_W_EXPAND)}")
scale.set_step(2)
w.reapply_metrics()
check("回标准档行高复原 34", hdr.height() == 34, f"got={hdr.height()}")

# ------------------------------------------------- 5) 表格行高派生（统一导入对话框）
from app.ui.unified_import_dialog import FILTER_ALL, UnifiedImportDialog  # noqa: E402

data = {
    "period": "2025-01",
    "invoices": [{
        "sheet": "sheet1", "sheet_name": "已开票已入账", "row_no": 10,
        "header": ["发票号", "购方", "价税合计", "经办人", "备注"],
        "raw_row": ["INV-1", "某公司", "1000", "周立生", ""],
        "invoice_no": "INV-1", "invoice_date": "2025-01-15", "buyer": "某公司",
        "total_amount": 1000.0, "handlers": [("周立生", 1000.0)], "handler_text": "周立生",
        "remark_raw": "", "remark": {"receipts": [], "remaining": None,
                                     "pure_date": None},
        "case_no": "", "is_red": False, "split_receipts": [],
    }],
    "prepayments": [],
    "problems": [],
    "sheet_totals": {"sheet1": 1000.0},
    "sheet12_total": 1000.0,
}
scale.set_step(2)
dlg = UnifiedImportDialog(data, "2025-01", ["周立生"])
# 阶段 4-2 起默认筛选 = 待补录，而本用例只有普通发票行（待确认）→ 不切「全部」表格为空、
# rowHeight(0) 会返回 0。切「全部」后行高才可测。
# 阶段 5 起筛选栏插入「已补录」→「全部」下标后移，故用 FILTER_ALL 而非写死数字。
dlg._grp.button(FILTER_ALL).setChecked(True)
dlg._render()
check("标准档表格行高 34", dlg.table.rowHeight(0) == 34, f"got={dlg.table.rowHeight(0)}")
scale.set_step(4)
dlg._render()
check("大两号表格行高 39", dlg.table.rowHeight(0) == 39, f"got={dlg.table.rowHeight(0)}")
check("大两号经办人列宽派生 265", dlg.table.columnWidth(6) == scale.px(230),
      f"got={dlg.table.columnWidth(6)}")

# ------------------------------------------------- 6) 收尾：恢复标准档
scale.set_step(scale.DEFAULT_STEP)
scale.save_step(scale.DEFAULT_STEP)
check("恢复标准档", scale.step() == 2)

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
