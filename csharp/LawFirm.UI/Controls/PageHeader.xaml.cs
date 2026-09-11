using System.Windows;
using System.Windows.Controls;

namespace LawFirm.UI.Controls;

/// <summary>页头：标题 + 「?」帮助（对应 Python tab_help_corner）。帮助文字经 ToolTip 展示。</summary>
public partial class PageHeader : UserControl
{
    public PageHeader()
    {
        InitializeComponent();
        DataContext = this;
    }

    public static readonly DependencyProperty TitleProperty =
        DependencyProperty.Register(nameof(Title), typeof(string), typeof(PageHeader),
            new PropertyMetadata(string.Empty));

    public string Title
    {
        get => (string)GetValue(TitleProperty);
        set => SetValue(TitleProperty, value);
    }

    public static readonly DependencyProperty HelpTextProperty =
        DependencyProperty.Register(nameof(HelpText), typeof(string), typeof(PageHeader),
            new PropertyMetadata(string.Empty));

    public string HelpText
    {
        get => (string)GetValue(HelpTextProperty);
        set => SetValue(HelpTextProperty, value);
    }
}
