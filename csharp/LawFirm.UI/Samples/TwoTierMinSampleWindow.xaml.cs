using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using LawFirm.UI.Controls;

namespace LawFirm.UI.Samples;

/// <summary>
/// T5.2 首日最小验证窗（U5/R1）：程序化建列，验证
/// 两级表头（GroupLabel/IsLead/宽度同步）+ FrozenColumnCount=1 + ScrollUnit=Pixel 共存。
/// </summary>
public partial class TwoTierMinSampleWindow : Window
{
    public TwoTierMinSampleWindow()
    {
        InitializeComponent();
        BuildColumns();
        SampleGrid.ItemsSource = MakeRows();
    }

    private void BuildColumns()
    {
        // 1) 整高列「序号」（冻结）
        var idx = GridColumnFactory.TextColumn("序号", numeric: false, frozen: true, minWidth: 60);
        idx.Width = new DataGridLength(60);
        TwoTierHeaderGrid.SetIsLead(idx, true);
        SampleGrid.Columns.Add(idx);

        // 2) 两级列组「本年收入」= 本月 + 累计
        var cur = GridColumnFactory.TextColumn("本月", numeric: true, minWidth: 110);
        TwoTierHeaderGrid.SetGroupLabel(cur, "本年收入");
        SampleGrid.Columns.Add(cur);
        var tot = GridColumnFactory.TextColumn("累计", numeric: true, minWidth: 110);
        SampleGrid.Columns.Add(tot);

        // 3) 一条普通列（验证普通列与两级列共存 + 横向滚动出现）
        SampleGrid.Columns.Add(GridColumnFactory.TextColumn("备注", numeric: false, minWidth: 400));

        // 开启两级表头宽度同步 + 表头高 44（§4.3 第 1 步）
        TwoTierHeaderGrid.SetIsEnabled(SampleGrid, true);
        SampleGrid.ColumnHeaderHeight = 44;
        SampleGrid.FrozenColumnCount = 1;
        SampleGrid.RowStyle = GridColumnFactory.TotalRowStyle();
    }

    private static List<ViewModels.GridRow> MakeRows()
    {
        var rows = new List<ViewModels.GridRow>();
        for (int i = 1; i <= 12; i++)
        {
            double v = i % 3 == 0 ? -1234.56 * i : 1234.56 * i;   // 含负数（验证红字）
            rows.Add(new ViewModels.GridRow(new Dictionary<string, object?>
            {
                ["序号"] = i.ToString(),
                ["本月"] = v,
                ["累计"] = v * 3,
                ["备注"] = $"样本行 {i}：备注列用于制造横向滚动……",
            }));
        }
        rows.Add(new ViewModels.GridRow(new Dictionary<string, object?>
        {
            ["序号"] = "",
            ["本月"] = 0.0,
            ["累计"] = 0.0,
            ["备注"] = "合计",
        }, isTotal: true));
        return rows;
    }
}
