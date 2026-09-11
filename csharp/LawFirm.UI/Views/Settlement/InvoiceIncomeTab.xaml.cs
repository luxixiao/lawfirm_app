using System.Windows.Controls;
using LawFirm.UI.Controls;
using LawFirm.UI.ViewModels;

namespace LawFirm.UI.Views.Settlement;

/// <summary>Tab4 开票收入表：静态 8 列（表头逐字对齐 Python:857-859），首列冻结。</summary>
public partial class InvoiceIncomeTab : UserControl
{
    public InvoiceIncomeTab()
    {
        InitializeComponent();
        BuildColumns();
        Grid.RowStyle = GridColumnFactory.TotalRowStyle();
    }

    private void BuildColumns()
    {
        foreach (var key in InvoiceIncomeViewModel.ColumnKeys)
        {
            bool numeric = key != "序号" && key != "姓名";
            Grid.Columns.Add(GridColumnFactory.TextColumn(key, numeric, frozen: key == "序号",
                minWidth: key.StartsWith("本月", StringComparison.Ordinal) ? 110 : 72));
        }
    }
}
