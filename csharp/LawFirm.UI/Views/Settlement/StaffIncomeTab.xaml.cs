using System.Windows.Controls;
using LawFirm.UI.Controls;
using LawFirm.UI.ViewModels;

namespace LawFirm.UI.Views.Settlement;

/// <summary>
/// Tab3 年度聘用结算表：两级表头 12 列（§4.3）——
/// 序号/姓名 整高（IsLead），本年收入/报酬发放/住房公积金/保险费/汽油费 各跨 2 列（GroupLabel），
/// 表头总高 44；合计行加粗灰底（C10）。
/// ⚠️ U5：两级表头 + FrozenColumnCount=1 + ScrollUnit=Pixel 共存需用户本机首日验证（F12 最小样本）。
/// </summary>
public partial class StaffIncomeTab : UserControl
{
    /// <summary>5 个大类标签（顺序 = Python settlement_view.py:711-712）。</summary>
    private static readonly string[] Groups =
    {
        "本年收入", "报酬发放", "住房公积金", "保险费", "汽油费",
    };

    public StaffIncomeTab()
    {
        InitializeComponent();
        BuildColumns();
        Grid.RowStyle = GridColumnFactory.TotalRowStyle();
    }

    private void BuildColumns()
    {
        // 序号（整高，冻结）
        var idx = GridColumnFactory.TextColumn("序号", numeric: false, frozen: true, minWidth: 56);
        TwoTierHeaderGrid.SetIsLead(idx, true);
        Grid.Columns.Add(idx);
        // 姓名（整高）
        var name = GridColumnFactory.TextColumn("姓名", numeric: false, minWidth: 90);
        TwoTierHeaderGrid.SetIsLead(name, true);
        Grid.Columns.Add(name);
        // 5 组 ×（本月/累计）：子列显示「本月/累计」，key 唯一（显示与持久化身份解耦）
        string[][] groupKeys =
        {
            new[] { "收入本月", "收入累计" },
            new[] { "报酬本月", "报酬累计" },
            new[] { "公积金本月", "公积金累计" },
            new[] { "保险本月", "保险累计" },
            new[] { "汽油本月", "汽油累计" },
        };
        for (int g = 0; g < Groups.Length; g++)
        {
            for (int k = 0; k < 2; k++)
            {
                var col = GridColumnFactory.TextColumn(
                    groupKeys[g][k], numeric: true, minWidth: 84,
                    header: k == 0 ? "本月" : "累计");
                if (k == 0)
                    TwoTierHeaderGrid.SetGroupLabel(col, Groups[g]);   // 第 1 行大类跨 2 列
                Grid.Columns.Add(col);
            }
        }
        // 两级表头开关（QA M1：容器层自动挂 TwoTierColumnHeaderStyle + LayoutUpdated 宽度同步）
        // + 表头总高 44（§4.3 第 1 步，两级各 22px；不改则 34px 装不下两行文字）
        TwoTierHeaderGrid.SetIsEnabled(Grid, true);
        Grid.ColumnHeaderHeight = 44;
    }
}
