import sys
import os

sys.path.insert(0, ".")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

import app.ui.style as style  # noqa: E402

style.PREFS_PATH = "tests/_prefs_ic.json"

app = QApplication([])
from app.ui.widgets import HelpIcon  # noqa: E402
from app.ui.invoice_collect_view import InvoiceCollectView  # noqa: E402
from app.ui.handler_collect_view import HandlerCollectView  # noqa: E402

for Cls in (InvoiceCollectView, HandlerCollectView):
    try:
        v = Cls()
        holder = QWidget()
        QVBoxLayout(holder)
        holder.layout().addWidget(v)
        holder.resize(1000, 400)
        holder.show()
        app.processEvents()
        ic = v.findChild(HelpIcon)
        print(Cls.__name__, "help_icon=", ic is not None,
              "visible=", ic.isVisible() if ic else None, flush=True)
        if ic is not None:
            px = ic.grab()
            print("  icon grab", px.width(), px.height(), flush=True)
        # 截图：页头顶部区域（offscreen 无中文字体，中文为豆腐块，仅验证布局/图标位置）
        if Cls is InvoiceCollectView:
            top = v.grab(v.rect().adjusted(0, 0, 0, -v.height() + 90))
            top.save("tests/_t_header.png")
            print("  header png saved", top.width(), top.height(), flush=True)
        print(Cls.__name__, "OK", flush=True)
    except Exception as e:  # noqa: BLE001
        print(Cls.__name__, "CONSTRUCT ERROR", repr(e), flush=True)
    finally:
        app.processEvents()
print("DONE", flush=True)
