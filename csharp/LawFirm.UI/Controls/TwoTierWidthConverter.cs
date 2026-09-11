using System;
using System.Globalization;
using System.Windows.Data;

namespace LawFirm.UI.Controls;

/// <summary>
/// 两级表头跨列宽度同步（C2，规格 §4.3 第 3 步）。
///
/// MultiBinding：[本列 SelfWidth, 右邻列 NextColumnWidth（均由 TwoTierHeaderGrid
/// 经 LayoutUpdated 维护的 attached DP，拖宽时有变更通知，QA M3）]
/// → 输出 Sum + 2（补偿 header 间 1px 分隔线 ×1 的累计误差；U4：+2 需真机微调）。
/// 对应 Python TwoTierHeaderView.paintSection 的跨列 drawText 宽度 = w1 + w2（含分隔线）。
/// </summary>
public sealed class TwoTierWidthConverter : IMultiValueConverter
{
    /// <summary>分隔线补偿（U4，真机微调项）。</summary>
    public const double SeparatorCompensation = 2.0;

    public object Convert(object?[]? values, Type targetType, object? parameter, CultureInfo culture)
    {
        double sum = 0;
        if (values is not null)
        {
            foreach (var v in values)
            {
                if (v is double d && !double.IsNaN(d) && !double.IsInfinity(d))
                    sum += d;
            }
        }
        return sum + SeparatorCompensation;
    }

    public object[] ConvertBack(object? value, Type[] targetTypes, object? parameter, CultureInfo culture)
        => throw new NotSupportedException("TwoTierWidthConverter 仅单向。");
}
