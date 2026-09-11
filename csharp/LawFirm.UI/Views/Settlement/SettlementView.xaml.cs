using System.Windows.Controls;

namespace LawFirm.UI.Views.Settlement;

/// <summary>「各类报表」页宿主：DataContext = SettlementViewModel。</summary>
public partial class SettlementView : UserControl
{
    public SettlementView()
    {
        InitializeComponent();
        // 缺失会导致整页 DataContext 为空：4 个下拉全空、按钮 Command 绑定落空（点击无反应）
        DataContext = new LawFirm.UI.ViewModels.SettlementViewModel();
    }
}
