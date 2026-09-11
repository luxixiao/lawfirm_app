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
/// Tab4「开票收入表」VM（_build_invoice_income_tab / refresh_invoice_income，
/// settlement_view.py:815-915 对齐）：年份 + 月份筛选，8 列预览（含合计行）。
/// </summary>
public partial class InvoiceIncomeViewModel : ViewModelBase
{
    private readonly PersonalSettlementViewModel _personal;

    public InvoiceIncomeViewModel(PersonalSettlementViewModel personal)
    {
        _personal = personal;
    }

    /// <summary>共享 Tab1 的年份状态（XAML 绑定 Personal.YearItems）。</summary>
    public PersonalSettlementViewModel Personal => _personal;

    /// <summary>月份选项 1~12。</summary>
    public IReadOnlyList<PersonalSettlementViewModel.MonthOption> MonthChoices { get; } =
        Enumerable.Range(1, 12)
            .Select(mo => new PersonalSettlementViewModel.MonthOption(mo, $"{mo}月")).ToList();

    [ObservableProperty] private int _month = Math.Min(DateTime.Now.Month, 12);   // 默认当前月（Python:838）
    [ObservableProperty] private IReadOnlyList<GridRow> _rows = new List<GridRow>();
    [ObservableProperty] private string _summaryText = "";

    partial void OnMonthChanged(int value) => _ = RefreshAsync();

    /// <summary>列 key 顺序（code-behind 建列用；表头文字与 Python 逐字一致）。</summary>
    public static readonly string[] ColumnKeys =
    {
        "序号", "姓名", "收入本月", "收入累计", "期末未收",
        "本月开票本月收回", "本月收回以前应收款", "本月合计收款",
    };

    /// <summary>切换到本 tab 时刷新（_on_tab_changed）。</summary>
    [RelayCommand]
    private async Task RefreshAsync()
    {
        int year = _personal.Year?.Value ?? DateTime.Now.Year;
        var result = await Task.Run(() =>
            SettlementQueryService.BuildInvoiceIncomeRows(year, Month));
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
                ["收入本月"] = r.InvCur, ["收入累计"] = r.InvTot,
                ["期末未收"] = r.Uncollected,
                ["本月开票本月收回"] = r.OpenRecv,
                ["本月收回以前应收款"] = r.PrevRecv,
                ["本月合计收款"] = r.TotalRecv,
            }));
        }
        Rows = rows;
        SummaryText = $"{year}年{Month}月律师收费情况表（开票收入）· {result.PersonCount} 人 + 合计，确认后导出";
    }

    private static GridRow TotalRow(Services.SettlementQueryService.InvoiceIncomeRow r)
        => new(new Dictionary<string, object?>
        {
            ["序号"] = "",
            ["姓名"] = "合计",
            ["收入本月"] = r.InvCur, ["收入累计"] = r.InvTot,
            ["期末未收"] = r.Uncollected,
            ["本月开票本月收回"] = r.OpenRecv,
            ["本月收回以前应收款"] = r.PrevRecv,
            ["本月合计收款"] = r.TotalRecv,
        }, isTotal: true);

    /// <summary>
    /// 导出当月（gen_invoice_income）。⚠️ invoice_income 导出器尚未移植到 C#（属导出器移植工作包）。
    /// TODO(exporter-port): 移植 app/exporter/invoice_income_exporter.py 后接线。
    /// </summary>
    [RelayCommand]
    private Task ExportCurrentMonthAsync()
    {
        MessageDialog.Info("提示",
            "「开票收入表」导出器尚未移植到 C#（导出器移植工作包），预览功能已可用。");
        return Task.CompletedTask;
    }

    /// <summary>导出模板表（gen_invoice_income_full）。同上：待导出器移植。</summary>
    [RelayCommand]
    private Task ExportTemplateAsync()
    {
        MessageDialog.Info("提示",
            "「开票收入表」导出器尚未移植到 C#（导出器移植工作包），预览功能已可用。");
        return Task.CompletedTask;
    }
}
