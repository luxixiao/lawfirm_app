using System.Windows.Controls;

namespace LawFirm.UI.Views;

/// <summary>「功能待迁移」占位页（U11）。仅显示页面标识，无任何数据访问。</summary>
public partial class PlaceholderView : UserControl
{
    public PlaceholderView(string pageKey = "unknown")
    {
        PageKey = pageKey;
        InitializeComponent();
    }

    /// <summary>页面标识（= NAV_GROUPS 的 key，便于用户报告缺失页）。</summary>
    public string PageKey { get; }
}
