using System.Windows;
using System.Windows.Controls;
using LawFirm.UI.Controls;
using LawFirm.UI.ViewModels;

namespace LawFirm.UI.Views.Settlement;

/// <summary>Tab2 月度结算表：静态 4 列（项目冻结灰底；备注灰字）。</summary>
public partial class MonthlyReportTab : UserControl
{
    public MonthlyReportTab()
    {
        InitializeComponent();
        Grid.Columns.Add(GridColumnFactory.TextColumn("项目", numeric: false, frozen: true));
        var cur = GridColumnFactory.TextColumn("本期", numeric: true);
        var tot = GridColumnFactory.TextColumn("本年累计", numeric: true);
        Grid.Columns.Add(cur);
        Grid.Columns.Add(tot);
        var note = GridColumnFactory.TextColumn("备注", numeric: false, minWidth: 220);
        // 备注灰字（settlement_view.py:650/657：setForeground(gray)）
        note.ElementStyle.Setters.Add(new Setter(
            TextBlock.ForegroundProperty,
            TryFindResource("TextMute") as System.Windows.Media.Brush
                ?? System.Windows.Media.Brushes.Gray));
        Grid.Columns.Add(note);
    }
}
