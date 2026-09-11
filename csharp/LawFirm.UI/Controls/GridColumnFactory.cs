using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;

namespace LawFirm.UI.Controls;

/// <summary>
/// 列工厂：按稳定 key 构造 DataGridTextColumn（T5.3 各表格页共用）。
///
/// - 绑定路径 = 字符串索引器 <c>[key]</c>（行模型暴露 string 索引器，见 ViewModels/GridRow.cs）。
/// - 数字列：右对齐 + StringFormat N2（zh-CN 强制见 App.xaml.cs）+ 负数红（DataTrigger 绑本列值）。
/// - 悬停全文（C6 退化方案，R8）：ElementStyle 的 ToolTip 绑同值，始终可看全文。
/// - 冻结列：CellStyle 换 FrozenCellStyle（灰底 #EAEAEA）。
/// </summary>
public static class GridColumnFactory
{
    private static readonly IsNegativeConverter Negative = new();

    /// <summary>构造一列。</summary>
    /// <param name="key">稳定列标识（列绑定路径与列持久化身份）。</param>
    /// <param name="numeric">true = 数值列（右对齐 N2 + 负数红）。</param>
    /// <param name="frozen">true = 冻结列灰底。</param>
    /// <param name="minWidth">最小宽。</param>
    /// <param name="header">表头显示文字（null = 用 key；两级表头子列需短标签时与 key 解耦）。</param>
    public static DataGridTextColumn TextColumn(string key, bool numeric, bool frozen = false,
        double minWidth = 48, string? header = null)
    {
        var col = new DataGridTextColumn
        {
            Header = header ?? key,
            MinWidth = minWidth,
            Binding = new System.Windows.Data.Binding($"[{key}]"),
        };
        if (numeric)
            col.Binding.StringFormat = "N2";

        // 单元格样式：基础 NotionCellStyle（或冻结灰底）
        var cellStyle = new Style(typeof(DataGridCell));
        var baseStyle = Application.Current?.TryFindResource(frozen ? "FrozenCellStyle" : "NotionCellStyle") as Style;
        if (baseStyle is not null) cellStyle.BasedOn = baseStyle;
        if (numeric) cellStyle.Setters.Add(new Setter(TextBlock.TextAlignmentProperty, TextAlignment.Right));
        col.CellStyle = cellStyle;

        // 元素样式：右对齐 + 负数红 + 悬停全文
        var elStyle = new Style(typeof(TextBlock));
        elStyle.Setters.Add(new Setter(TextBlock.VerticalAlignmentProperty, VerticalAlignment.Center));
        if (numeric)
        {
            elStyle.Setters.Add(new Setter(TextBlock.TextAlignmentProperty, TextAlignment.Right));
            var negTrigger = new System.Windows.DataTrigger
            {
                Binding = new System.Windows.Data.Binding($"[{key}]") { Converter = Negative },
                Value = true,
            };
            negTrigger.Setters.Add(new Setter(TextBlock.ForegroundProperty,
                Application.Current?.TryFindResource("Red") as Brush ?? Brushes.IndianRed));
            elStyle.Triggers.Add(negTrigger);
        }
        // C6 退化方案：始终挂 ToolTip（真机反馈视觉噪音再收紧为「仅压缩时」）
        elStyle.Setters.Add(new Setter(TextBlock.ToolTipProperty,
            new System.Windows.Data.Binding($"[{key}]")));
        col.ElementStyle = elStyle;
        return col;
    }

    /// <summary>整行加粗样式（行模型 IsBold=true 时用；Tab1 分类行）。</summary>
    public static Style BoldRowStyle()
    {
        var style = new Style(typeof(DataGridRow));
        var baseStyle = Application.Current?.TryFindResource("NotionRowStyle") as Style;
        if (baseStyle is not null) style.BasedOn = baseStyle;
        var trigger = new System.Windows.DataTrigger
        {
            Binding = new System.Windows.Data.Binding("IsBold"),
            Value = true,
        };
        trigger.Setters.Add(new Setter(TextElement.FontWeightProperty, FontWeights.Bold));
        style.Triggers.Add(trigger);
        return style;
    }

    /// <summary>合计行样式（C10：IsTotal=true → 加粗 + 灰底；table_view.py:291-306）。</summary>
    public static Style TotalRowStyle()
    {
        var style = new Style(typeof(DataGridRow));
        var baseStyle = Application.Current?.TryFindResource("NotionRowStyle") as Style;
        if (baseStyle is not null) style.BasedOn = baseStyle;
        var trigger = new System.Windows.DataTrigger
        {
            Binding = new System.Windows.Data.Binding("IsTotal"),
            Value = true,
        };
        trigger.Setters.Add(new Setter(TextElement.FontWeightProperty, FontWeights.Bold));
        trigger.Setters.Add(new Setter(BackgroundProperty,
            Application.Current?.TryFindResource("BgTable") as Brush ?? Brushes.WhiteSmoke));
        style.Triggers.Add(trigger);
        return style;
    }
}
