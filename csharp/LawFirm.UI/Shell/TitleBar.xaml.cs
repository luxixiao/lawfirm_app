using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace LawFirm.UI.Shell;

/// <summary>
/// 自绘标题栏（规格 §3.2）。页名由 DataContext 的 PageTitle 提供；
/// 拖拽交给 WindowChrome，双击切换最大化，三按钮行为对应 main_window.py:155-170。
/// </summary>
public partial class TitleBar : UserControl
{
    public TitleBar()
    {
        InitializeComponent();
        MouseLeftButtonDown += OnTitleBarMouseLeftButtonDown;
    }

    /// <summary>页名（绑定 NavigationService.CurrentPageTitle）。</summary>
    public static readonly DependencyProperty PageTitleProperty =
        DependencyProperty.Register(
            nameof(PageTitle), typeof(string), typeof(TitleBar),
            new PropertyMetadata("导入台账"));

    public string PageTitle
    {
        get => (string)GetValue(PageTitleProperty);
        set => SetValue(PageTitleProperty, value);
    }

    private void OnTitleBarMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        // 双击最大化/还原（对齐 FramelessWindow 行为，规格 §3.1）
        if (e.ClickCount == 2 && Window.GetWindow(this) is { } win)
        {
            win.WindowState = win.WindowState == WindowState.Maximized
                ? WindowState.Normal
                : WindowState.Maximized;
            e.Handled = true;
        }
    }

    private void OnMinimize(object sender, RoutedEventArgs e)
    {
        if (Window.GetWindow(this) is { } win) win.WindowState = WindowState.Minimized;
    }

    private void OnMaximize(object sender, RoutedEventArgs e)
    {
        if (Window.GetWindow(this) is { } win)
        {
            win.WindowState = win.WindowState == WindowState.Maximized
                ? WindowState.Normal
                : WindowState.Maximized;
        }
    }

    private void OnClose(object sender, RoutedEventArgs e)
    {
        Window.GetWindow(this)?.Close();
    }
}
