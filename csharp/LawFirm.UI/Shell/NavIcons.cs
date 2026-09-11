using System;
using System.Windows;
using System.Windows.Media;

namespace LawFirm.UI.Shell;

/// <summary>
/// 侧栏导航图标（7 组 + chevron，规格 §3.3）。
///
/// 24 网格线性图标，1.5px 描边、Round cap/join（对应 app/ui/nav_icons.py:123-175）。
/// 按 U3 裁定：「数据导入」「台账查看」「各类报表」三枚精确复刻（首屏可见），
/// 其余 4 枚用简化等价图形，全量期补精确版。
/// 用法：&lt;Path Data="{x:Static shell:NavIcons.Import}" Stroke=... StrokeThickness="1.5"/&gt;
/// </summary>
public static class NavIcons
{
    private static Geometry? Parse(string data)
        => Geometry.Parse(data);

    /// <summary>数据导入：U 形托盘 + 下箭头（nav_icons.py 精确复刻）。</summary>
    public static readonly Geometry Import = Parse(
        "M3.5,14.5 V17.5 A2,2 0 0 0 5.5,19.5 H18.5 A2,2 0 0 0 20.5,17.5 V14.5 " +
        "M12,3 V14 M7.5,9.5 L12,14 L16.5,9.5");

    /// <summary>台账查看：圆角矩形 + 横线 + 3 条竖线（nav_icons.py 精确复刻）。</summary>
    public static readonly Geometry Ledger = Parse(
        "M5.5,3.5 H18.5 A1.5,1.5 0 0 1 20,5 V19 A1.5,1.5 0 0 1 18.5,20.5 H5.5 " +
        "A1.5,1.5 0 0 1 4,19 V5 A1.5,1.5 0 0 1 5.5,3.5 Z " +
        "M4,8.5 H20 M8.5,12 V16.5 M12,12 V16.5 M15.5,12 V16.5");

    /// <summary>业务数据：钱包（简化等价图形，U3）。</summary>
    public static readonly Geometry Business = Parse(
        "M4,9.5 V7 A1.5,1.5 0 0 1 5.5,5.5 H18.5 A1.5,1.5 0 0 1 20,7 V17 " +
        "A1.5,1.5 0 0 1 18.5,18.5 H5.5 A1.5,1.5 0 0 1 4,17 V9.5 A2,2 0 0 0 6,11.5 H20 " +
        "M16,14.25 H18");

    /// <summary>工资个税：人形 + 圆币（简化等价图形，U3）。</summary>
    public static readonly Geometry Salary = Parse(
        "M10,8.6 A3.4,3.4 0 1 1 3.2,8.6 A3.4,3.4 0 0 1 10,8.6 " +
        "M4.2,19.6 A5.8,5.8 0 0 1 10,13.8 M17.6,16.4 A3.8,3.8 0 1 1 10,16.4 " +
        "A3.8,3.8 0 0 1 17.6,16.4 M10.2,19 H20.5");

    /// <summary>分成计算：计算器（简化等价图形，U3）。</summary>
    public static readonly Geometry Calc = Parse(
        "M7.5,3.5 H16.5 A1.5,1.5 0 0 1 18,5 V19 A1.5,1.5 0 0 1 16.5,20.5 H7.5 " +
        "A1.5,1.5 0 0 1 6,19 V5 A1.5,1.5 0 0 1 7.5,3.5 Z " +
        "M8.5,6.5 H15.5 V10 H8.5 Z M9,13.5 H9.01 M12,13.5 H12.01 M15,13.5 H15.01 " +
        "M9,17 H9.01 M12,17 H12.01 M15,17 H15.01");

    /// <summary>各类报表：坐标轴 + 3 柱（nav_icons.py 精确复刻）。</summary>
    public static readonly Geometry Report = Parse(
        "M4,4 V20 H20 " +
        "M7.5,20 V13.5 H10.5 V20 " +
        "M12,20 V9.5 H15 V20 " +
        "M16.5,20 V15 H19.5 V20");

    /// <summary>数据维护：放射齿轮（简化等价图形，U3）。</summary>
    public static readonly Geometry Maintenance = Parse(
        "M15.6,12 A3.6,3.6 0 1 1 8.4,12 A3.6,3.6 0 0 1 15.6,12 " +
        "M12,3.5 V6.2 M12,17.8 V20.5 M20.5,12 H17.8 M6.2,12 H3.5 " +
        "M17.9,6.1 L16,8 M8,16 L6.1,17.9 M17.9,17.9 L16,16 M8,8 L6.1,6.1");

    /// <summary>分组折叠 chevron（sidebar.py：d = 0.28×min(w,h)，指向右侧）。</summary>
    public static readonly Geometry ChevronRight = Parse("M9,6 L15,12 L9,18");

    /// <summary>分组展开 chevron（旋转 90° = 指向下）。</summary>
    public static readonly Geometry ChevronDown = Parse("M6,9 L12,15 L18,9");

    /// <summary>侧栏折叠按钮（›）。</summary>
    public static readonly Geometry Collapse = ChevronRight;
}
