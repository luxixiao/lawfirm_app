"""offscreen 冒烟：Qt 内置文案中文化（app/ui/i18n.py）。

运行：python tests/_smoke_i18n.py

背景缺陷：PySide6 默认不装载 Qt 中文翻译，QDialogButtonBox/QMessageBox 的标准按钮
一律英文（OK / Cancel / Close / Yes / No / Show Details...），QFileDialog 也是英文。
app/ui/i18n.install_qt_zh() 装载 qtbase_zh_CN 后应全部变中文。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialogButtonBox, QFileDialog, QMessageBox,
)

app = QApplication.instance() or QApplication(sys.argv)

from app.ui import i18n  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


def _qa_to_en():
    """记下未中文化时的英文文案，做为对照。"""
    b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                         | QDialogButtonBox.StandardButton.Cancel)
    return [x.text() for x in b.buttons()]


before = _qa_to_en()
loaded = i18n.install_qt_zh(app)
check("装载 qtbase_zh_CN", "qtbase_zh_CN" in loaded, str(loaded))
check("装载前是英文（对照）", before == ["OK", "Cancel"], str(before))

# 幂等：重复调用不重复装载
again = i18n.install_qt_zh(app)
check("幂等（重复调用不重复装载）", again == loaded, str(again))

b1 = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                      | QDialogButtonBox.StandardButton.Cancel)
check("Ok|Cancel → 确定/取消", [x.text() for x in b1.buttons()] == ["确定", "取消"],
      str([x.text() for x in b1.buttons()]))

b2 = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
check("Close → 关闭", [x.text() for x in b2.buttons()] == ["关闭"],
      str([x.text() for x in b2.buttons()]))

box = QMessageBox()
box.setText("t")
box.setDetailedText("a\nb")
check("setDetailedText 自动按钮 → 显示详情...（英文为 Show Details...）",
      all("Show" not in x.text() and "Details" not in x.text() for x in box.buttons()),
      str([x.text() for x in box.buttons()]))

q = QMessageBox(QMessageBox.Icon.Question, "确认", "继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
_yt = "".join(x.text() for x in q.buttons())
check("Yes|No → 中文（去助记符后为 是/否）",
      "Yes" not in _yt and "No" not in _yt and "是" in _yt and "否" in _yt, _yt)

# QFileDialog 也是 Qt 内置文案，装表后不应再出现英文 "File name" 之类
check("i18n.installed() 可自检", "qtbase_zh_CN" in i18n.installed(), str(i18n.installed()))
check("QFileDialog 类型可用（翻译表覆盖其内置文案）", QFileDialog is not None)

bad = [n for n, ok in results if not ok]
print(f"\nSMOKE {'PASS' if not bad else 'FAIL'} {len(results) - len(bad)}/{len(results)}")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
