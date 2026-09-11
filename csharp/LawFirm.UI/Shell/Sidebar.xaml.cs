using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media.Animation;

namespace LawFirm.UI.Shell;

/// <summary>
/// 分组折叠侧栏（规格 §3.3）。
///
/// - DataContext 必须是 <see cref="NavigationService"/>（ItemsSource 绑 Groups）。
/// - 收起/展开：RootBorder.Width 动画 240↔60（收 240ms OutQuart / 展 180ms OutCubic）。
/// - 收起态：组面板隐藏（模板 DataTrigger），点组头先展开侧栏。
/// - U2 已裁定：T5 不做 hover 自动展开/收回。
/// </summary>
public partial class Sidebar : UserControl
{
    private const double ExpandedWidth = 240;   // sidebar.py:33
    private const double CollapsedWidth = 60;   // sidebar.py:34

    /// <summary>侧栏收起态（模板内据此隐藏文字/子项）。</summary>
    public static readonly DependencyProperty IsCollapsedProperty =
        DependencyProperty.Register(
            nameof(IsCollapsed), typeof(bool), typeof(Sidebar),
            new PropertyMetadata(false));

    public bool IsCollapsed
    {
        get => (bool)GetValue(IsCollapsedProperty);
        set => SetValue(IsCollapsedProperty, value);
    }

    public Sidebar()
    {
        InitializeComponent();
        DataContextChanged += (_, _) => CollapseHint.Visibility = IsCollapsed ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>折叠/展开按钮：宽度动画 + 切换 IsCollapsed。</summary>
    private void OnToggleCollapse(object sender, RoutedEventArgs e)
    {
        bool toCollapsed = !IsCollapsed;
        var anim = new DoubleAnimation
        {
            To = toCollapsed ? CollapsedWidth : ExpandedWidth,
            Duration = TimeSpan.FromMilliseconds(toCollapsed ? 240 : 180),
            EasingFunction = toCollapsed
                ? new QuarticEase { EasingMode = EasingMode.EaseOut }    // rail_in
                : new CubicEase { EasingMode = EasingMode.EaseOut },     // rail_out
            FillBehavior = FillBehavior.Stop,
        };
        anim.Completed += (_, _) =>
        {
            // FillBehavior.Stop 后落回本地值，固定目标宽避免悬停抖动
            RootBorder.Width = toCollapsed ? CollapsedWidth : ExpandedWidth;
        };
        RootBorder.BeginAnimation(WidthProperty, anim);
        IsCollapsed = toCollapsed;
        CollapseHint.Visibility = toCollapsed ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>组头点击：收起态下先展开侧栏（子项面板才会出现）。</summary>
    private void OnGroupHeaderClick(object sender, RoutedEventArgs e)
    {
        if (IsCollapsed && ((ToggleButton)sender).IsChecked == true)
            OnToggleCollapse(sender, e);
    }

    /// <summary>子项选中 → NavigationService.Select（更新页名 + 换页）。</summary>
    private void OnItemChecked(object sender, RoutedEventArgs e)
    {
        if (sender is RadioButton { DataContext: NavItem item }
            && DataContext is NavigationService nav)
        {
            nav.Select(item.Key);
        }
    }
}
