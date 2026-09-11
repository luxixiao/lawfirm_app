using System;
using System.Globalization;
using System.Windows.Data;

namespace LawFirm.UI.Controls;

/// <summary>
/// 数值负数判定（C7 负数标红，table_view.py:335-336）。
/// 用作 DataTrigger 的 Binding 转换器：&lt; 0 → true（红色 #eb5757）。
/// 接受 double / double? / 数值类型；无法解析 → false。
/// </summary>
public sealed class IsNegativeConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        double d;
        if (value is double dv) d = dv;
        else if (value is null) return false;
        else
        {
            try { d = System.Convert.ToDouble(value, CultureInfo.InvariantCulture); }
            catch (Exception) { return false; }
        }
        return d < 0;
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
        => throw new NotSupportedException("IsNegativeConverter 仅单向。");
}
