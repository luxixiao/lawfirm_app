using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace LawFirm.UI.Controls;

/// <summary>
/// 两级表头基础设施（C2，规格 §4.3）。
///
/// 方案 = 「单表头单元格内嵌 2 行 Grid + ColumnSpan」（社区成熟模式，非 Plan B）：
/// - <see cref="GroupLabelProperty"/> / <see cref="IsLeadProperty"/> 挂在 DataGridColumn 上（声明两级结构）；
/// - <see cref="SelfWidthProperty"/> / <see cref="NextColumnWidthProperty"/> 挂在 DataGridColumn 上，
///   由本类监听 DataGrid.LayoutUpdated 实时维护（ActualWidth 本身无变更通知，
///   这两个 attached DP 是模板 MultiBinding 的通知源，QA M3）；
/// - 表头 ControlTemplate（Themes/Generic.xaml TwoTierColumnHeaderStyle）内用
///   MultiBinding(TwoTierWidthConverter) 把 [本列宽 + 右邻列宽 + 2] 赋给大类 TextBlock.Width；
/// - 样式挂接（QA M1）：IsEnabled=true 时自动把 TwoTierColumnHeaderStyle 应用到全部
///   未自定义 HeaderStyle 的列（OnLayoutUpdated 里对后加列补挂），容器层切换、零死代码。
///
/// ⚠️ 未真机验证（沙箱无 WPF 运行时）：U5 = 两级表头 + FrozenColumnCount + ScrollUnit=Pixel
/// 三者共存需用户本机首日跑 Samples/TwoTierMinSampleWindow 验证；失败走 §4.3 Plan B。
/// </summary>
public static class TwoTierHeaderGrid
{
    // ---- 附加属性：挂在 DataGrid 上，开启两级表头宽度同步 ----

    public static readonly DependencyProperty IsEnabledProperty =
        DependencyProperty.RegisterAttached(
            "IsEnabled", typeof(bool), typeof(TwoTierHeaderGrid),
            new PropertyMetadata(false, OnIsEnabledChanged));

    public static bool GetIsEnabled(DependencyObject obj)
        => (bool)obj.GetValue(IsEnabledProperty);

    public static void SetIsEnabled(DependencyObject obj, bool value)
        => obj.SetValue(IsEnabledProperty, value);

    private static void OnIsEnabledChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is not DataGrid grid) return;
        if ((bool)e.NewValue)
        {
            grid.LayoutUpdated -= OnLayoutUpdated;
            grid.LayoutUpdated += OnLayoutUpdated;
            ApplyTwoTierStyle(grid);   // QA M1：启用即挂两级表头模板
        }
        else
        {
            grid.LayoutUpdated -= OnLayoutUpdated;
        }
    }

    private static Style? _twoTierStyle;

    /// <summary>
    /// QA M1：把 Generic.xaml 的 TwoTierColumnHeaderStyle 应用到未自定义 HeaderStyle 的列。
    /// 仅当 col.HeaderStyle 为空时接管（不覆盖调用方显式样式）；OnLayoutUpdated 会重复调用，
    /// 后加的列也能补挂 —— 调用方 SetIsEnabled 与建列的先后顺序因此无关紧要。
    /// </summary>
    private static void ApplyTwoTierStyle(DataGrid grid)
    {
        if (_twoTierStyle is null)
        {
            _twoTierStyle = Application.Current?.TryFindResource("TwoTierColumnHeaderStyle") as Style;
            if (_twoTierStyle is null) return;   // App.xaml 未合并 Generic.xaml 时静默跳过
        }
        foreach (var col in grid.Columns)
        {
            if (col.HeaderStyle is null) col.HeaderStyle = _twoTierStyle;
        }
    }

    // ---- 附加属性：挂在 DataGridColumn 上 ----

    /// <summary>大类标签（非空 = 本列是某大类的起始列，第一行显示该标签）。</summary>
    public static readonly DependencyProperty GroupLabelProperty =
        DependencyProperty.RegisterAttached(
            "GroupLabel", typeof(string), typeof(TwoTierHeaderGrid),
            new PropertyMetadata(string.Empty));

    public static string GetGroupLabel(DataGridColumn col)
        => (string)col.GetValue(GroupLabelProperty);

    public static void SetGroupLabel(DataGridColumn col, string value)
        => col.SetValue(GroupLabelProperty, value);

    /// <summary>整高列（序号/姓名）：内容跨两行居中。</summary>
    public static readonly DependencyProperty IsLeadProperty =
        DependencyProperty.RegisterAttached(
            "IsLead", typeof(bool), typeof(TwoTierHeaderGrid),
            new PropertyMetadata(false));

    public static bool GetIsLead(DataGridColumn col)
        => (bool)col.GetValue(IsLeadProperty);

    public static void SetIsLead(DataGridColumn col, bool value)
        => col.SetValue(IsLeadProperty, value);

    /// <summary>
    /// 本列当前宽（QA M3：ActualWidth 是普通只读属性、无变更通知，拖宽时模板不刷新；
    /// 这里由 OnLayoutUpdated 维护成 attached DP，拖宽 → LayoutUpdated → DP 变更 →
    /// MultiBinding 重新求值，大类文字实时跟随）。
    /// </summary>
    public static readonly DependencyProperty SelfWidthProperty =
        DependencyProperty.RegisterAttached(
            "SelfWidth", typeof(double), typeof(TwoTierHeaderGrid),
            new PropertyMetadata(0.0));

    public static double GetSelfWidth(DataGridColumn col)
        => (double)col.GetValue(SelfWidthProperty);

    public static void SetSelfWidth(DataGridColumn col, double value)
        => col.SetValue(SelfWidthProperty, value);

    /// <summary>右邻列宽（运行时维护；表头模板经 MultiBinding 读取）。</summary>
    public static readonly DependencyProperty NextColumnWidthProperty =
        DependencyProperty.RegisterAttached(
            "NextColumnWidth", typeof(double), typeof(TwoTierHeaderGrid),
            new PropertyMetadata(0.0));

    public static double GetNextColumnWidth(DataGridColumn col)
        => (double)col.GetValue(NextColumnWidthProperty);

    public static void SetNextColumnWidth(DataGridColumn col, double value)
        => col.SetValue(NextColumnWidthProperty, value);

    // ---- 宽度同步 ----

    private static void OnLayoutUpdated(object? sender, EventArgs e)
    {
        if (sender is not DataGrid grid) return;
        // QA M1：后建列补挂两级表头模板（内部有 HeaderStyle 空判断，幂等且廉价）
        ApplyTwoTierStyle(grid);
        var columns = grid.Columns;
        // 按 DisplayIndex（视觉序）遍历：大类跨列 = 视觉上「本列 + 右邻列」
        var sorted = new DataGridColumn?[columns.Count];
        foreach (var col in columns)
        {
            if (col.DisplayIndex >= 0 && col.DisplayIndex < sorted.Length)
                sorted[col.DisplayIndex] = col;
        }
        for (int i = 0; i < sorted.Length; i++)
        {
            var col = sorted[i];
            if (col is null) continue;
            // QA M3：本列 ActualWidth → SelfWidth attached DP（变更通知源）
            double self = col.ActualWidth;
            if (Math.Abs(GetSelfWidth(col) - self) > 0.01)
                SetSelfWidth(col, self);
            // 仅大类起始列需要跨列宽度；其余列同步清零避免残留
            double nextW = 0;
            if (!string.IsNullOrEmpty(GetGroupLabel(col)) && i + 1 < sorted.Length && sorted[i + 1] is not null)
                nextW = sorted[i + 1]!.ActualWidth;
            double cur = GetNextColumnWidth(col);
            if (Math.Abs(cur - nextW) > 0.01)   // LayoutUpdated 高频，仅变化时写
                SetNextColumnWidth(col, nextW);
        }
    }
}
