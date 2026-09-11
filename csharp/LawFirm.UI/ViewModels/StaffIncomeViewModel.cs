using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LawFirm.UI.Dialogs;
using LawFirm.UI.Services;

namespace LawFirm.UI.ViewModels;

/// <summary>
/// Tab3「年度聘用结算表」VM（_build_staff_income_tab / refresh_staff_income，
/// settlement_view.py:666-769 对齐）：年份 + 月份筛选，两级表头 12 列预览（含合计行）。
/// </summary>
public partial class StaffIncomeViewModel : ViewModelBase
{
    private readonly PersonalSettlementViewModel _personal;

    public StaffIncomeViewModel(PersonalSettlementViewModel personal)
    {
        _personal = personal;
    }

    /// <summary>共享 Tab1 的年份状态（XAML 绑定 Personal.YearItems）。</summary>
    public PersonalSettlementViewModel Personal => _personal;

    /// <summary>月份选项 1~12。</summary>
    public IReadOnlyList<PersonalSettlementViewModel.MonthOption> MonthChoices { get; } =
        Enumerable.Range(1, 12)
            .Select(mo => new PersonalSettlementViewModel.MonthOption(mo, $"{mo}月")).ToList();

    [ObservableProperty] private int _month = Math.Min(DateTime.Now.Month, 12);   // 默认当前月（Python:689）
    [ObservableProperty] private IReadOnlyList<GridRow> _rows = new List<GridRow>();
    [ObservableProperty] private string _summaryText = "";

    partial void OnMonthChanged(int value) => _ = RefreshAsync();

    /// <summary>列 key 顺序（code-behind 建列用）。</summary>
    public static readonly string[] ColumnKeys =
    {
        "序号", "姓名",
        "收入本月", "收入累计", "报酬本月", "报酬累计",
        "公积金本月", "公积金累计", "保险本月", "保险累计", "汽油本月", "汽油累计",
    };

    /// <summary>切换到本 tab 时刷新（_on_tab_changed）。</summary>
    [RelayCommand]
    private async Task RefreshAsync()
    {
        int year = _personal.Year?.Value ?? DateTime.Now.Year;
        var result = await Task.Run(() =>
            SettlementQueryService.BuildStaffIncomeRows(year, Month));
        var rows = new List<GridRow>();
        int idx = 1;
        foreach (var r in result.Rows)
        {
            if (r.IsTotal)
            {
                rows.Add(TotalRow(r));
                break;
            }
            rows.Add(new GridRow(new Dictionary<string, object?>
            {
                ["序号"] = (idx++).ToString(),
                ["姓名"] = r.Name,
                ["收入本月"] = r.IncomeCur, ["收入累计"] = r.IncomeTot,
                ["报酬本月"] = r.PayCur, ["报酬累计"] = r.PayTot,
                ["公积金本月"] = r.FundCur, ["公积金累计"] = r.FundTot,
                ["保险本月"] = r.InsCur, ["保险累计"] = r.InsTot,
                ["汽油本月"] = r.FuelCur, ["汽油累计"] = r.FuelTot,
            }));
        }
        Rows = rows;
        SummaryText = $"{year}年{Month}月聘用律师业务收入结算表（{result.PersonCount} 人）· 预览共 {Math.Max(0, result.Rows.Count - 1)} 行+合计，确认后导出";
    }

    private static GridRow TotalRow(Services.SettlementQueryService.StaffIncomeRow r)
        => new(new Dictionary<string, object?>
        {
            ["序号"] = "",
            ["姓名"] = "合计",
            ["收入本月"] = r.IncomeCur, ["收入累计"] = r.IncomeTot,
            ["报酬本月"] = r.PayCur, ["报酬累计"] = r.PayTot,
            ["公积金本月"] = r.FundCur, ["公积金累计"] = r.FundTot,
            ["保险本月"] = r.InsCur, ["保险累计"] = r.InsTot,
            ["汽油本月"] = r.FuelCur, ["汽油累计"] = r.FuelTot,
        }, isTotal: true);

    /// <summary>
    /// 导出当月（gen_staff_income）。⚠️ staff_income 导出器尚未移植到 C#（属导出器移植工作包，
    /// 见规格 §1.1 已有资产清单），先给出明确提示，不静默失败。
    /// TODO(exporter-port): 移植 app/exporter/staff_income_exporter.py 后接线。
    /// </summary>
    [RelayCommand]
    private Task ExportCurrentMonthAsync()
    {
        MessageDialog.Info("提示",
            "「年度聘用结算表」导出器尚未移植到 C#（导出器移植工作包），预览功能已可用。");
        return Task.CompletedTask;
    }

    /// <summary>导出模板表（gen_staff_income_full）。同上：待导出器移植。</summary>
    [RelayCommand]
    private Task ExportTemplateAsync()
    {
        MessageDialog.Info("提示",
            "「年度聘用结算表」导出器尚未移植到 C#（导出器移植工作包），预览功能已可用。");
        return Task.CompletedTask;
    }
}
