import sys
import os

sys.path.insert(0, ".")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

import app.ui.style as style  # noqa: E402

style.PREFS_PATH = "tests/_prefs_ic4.json"

app = QApplication([])
from app.ui.widgets import HelpIcon  # noqa: E402
from app.ui.invoice_ledger_view import InvoiceLedgerView  # noqa: E402
from app.ui.invoice_ledger_doc_view import InvoiceLedgerDocView  # noqa: E402
from app.ui.expense_ledger_view import ExpenseLedgerView  # noqa: E402
from app.ui.salary_ledger_view import SalaryLedgerView  # noqa: E402
from app.ui.salary_summary_view import SalarySummaryView  # noqa: E402
from app.ui.tax_declaration_view import TaxDeclarationView  # noqa: E402
from app.ui.tax_deduction_view import TaxDeductionView  # noqa: E402
from app.ui.calc_sheet_view import CalcSheetView  # noqa: E402
from app.ui.staff_view import StaffView  # noqa: E402
from app.ui.expense_cat_view import ExpenseCatView  # noqa: E402
from app.ui.data_clear_view import DataClearView  # noqa: E402
from app.ui.manual_entry_view import ManualEntryView  # noqa: E402
from app.ui.settlement_view import SettlementView  # noqa: E402

CLASSES = [
    InvoiceLedgerView, InvoiceLedgerDocView, ExpenseLedgerView,
    SalaryLedgerView, SalarySummaryView, TaxDeclarationView,
    TaxDeductionView, CalcSheetView, StaffView, ExpenseCatView,
    DataClearView, ManualEntryView, SettlementView,
]

ok = 0
for Cls in CLASSES:
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
print("RESULT", ok, "/", len(CLASSES), flush=True)
print("DONE", flush=True)
