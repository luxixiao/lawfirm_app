import sys
import os

sys.path.insert(0, ".")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

import app.ui.style as style  # noqa: E402

style.PREFS_PATH = "tests/_prefs_ic3.json"

app = QApplication([])
from app.ui.widgets import HelpIcon  # noqa: E402
from app.ui.import_view import ImportView  # noqa: E402
from app.ui.batch_view import BatchView  # noqa: E402
from app.ui.refund_view import RefundView  # noqa: E402
from app.ui.snapshot_view import SnapshotView  # noqa: E402

ok = 0
for Cls in (ImportView, BatchView, RefundView, SnapshotView):
    try:
        v = Cls()
        holder = QWidget()
        QVBoxLayout(holder)
        holder.layout().addWidget(v)
        holder.resize(1000, 400)
        holder.show()
        app.processEvents()
        ic = v.findChild(HelpIcon)
        present = ic is not None
        visible = ic.isVisible() if ic else None
        print(Cls.__name__, "help_icon=", present, "visible=", visible, flush=True)
        if present and visible:
            ok += 1
        print(Cls.__name__, "OK" if present else "NO ICON", flush=True)
    except Exception as e:  # noqa: BLE001
        print(Cls.__name__, "CONSTRUCT ERROR", repr(e), flush=True)
    finally:
        app.processEvents()
print("RESULT", ok, "/ 4", flush=True)
print("DONE", flush=True)
