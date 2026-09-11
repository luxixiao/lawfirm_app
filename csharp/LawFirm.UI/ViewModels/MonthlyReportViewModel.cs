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
/// Tab2「月度结算表」VM（_build_report_tab / refresh_report，settlement_view.py:520-663 对齐）：
/// 年份/月份/经办人/类型筛选 + 4 列预览（项目/本期/本年累计/备注）+ 导出按钮。
/// </summary>
public partial class MonthlyReportViewModel : ViewModelBase
{
    private readonly ExportService _exportService = new();

    public MonthlyReportViewModel(PersonalSettlementViewModel personal)
    {
        Personal = personal;
        // 人员列表与 Tab1 共享（Python r_person 与 person 同步填充）
        Personal.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(Personal.Persons))
            {
                PersonOptions = Personal.Persons;
                Person = PersonOptions.FirstOrDefault();
            }
        };
        PersonOptions = Personal.Persons;
        TypeOptions = Personal.Types;
    }

    public PersonalSettlementViewModel Personal { get; }

    public IReadOnlyList<PersonalSettlementViewModel.YearOption> YearItems => Personal.Years;
    public PersonalSettlementViewModel.YearOption? Year => Personal.Year;

    /// <summary>月份选项 1~12（与 Tab1 的 Months 不同：无「全部月份」）。</summary>
    public IReadOnlyList<PersonalSettlementViewModel.MonthOption> MonthChoices { get; } =
        System.Linq.Enumerable.Range(1, 12)
            .Select(mo => new PersonalSettlementViewModel.MonthOption(mo, $"{mo}月")).ToList();

    [ObservableProperty] private IReadOnlyList<PersonalSettlementViewModel.PersonOption> _personOptions;
    [ObservableProperty] private PersonalSettlementViewModel.PersonOption? _person;
    [ObservableProperty] private IReadOnlyList<PersonalSettlementViewModel.TypeOption> _typeOptions;
    [ObservableProperty] private PersonalSettlementViewModel.TypeOption? _personType;
    [ObservableProperty] private int _month = 1;
    [ObservableProperty] private IReadOnlyList<Services.SettlementQueryService.ReportRow> _rows =
        Array.Empty<SettlementQueryService.ReportRow>();
    [ObservableProperty] private string _summaryText = "请选择经办人";

    partial void OnPersonChanged(PersonalSettlementViewModel.PersonOption? value) => _ = RefreshAsync();
    partial void OnPersonTypeChanged(PersonalSettlementViewModel.TypeOption? value) => _ = RefreshAsync();
    partial void OnMonthChanged(int value) => _ = RefreshAsync();

    /// <summary>切换到本 tab 时刷新（_on_tab_changed）。</summary>
    [RelayCommand]
    private async Task RefreshAsync()
    {
        var name = Person?.Name;
        if (name is null)
        {
            Rows = Array.Empty<SettlementQueryService.ReportRow>();
            SummaryText = "请选择经办人";
            return;
        }
        // 类型下拉动态化（_sync_r_type_options）：复用 Tab1 的结果（同一人同一份身份集合）
        var available = await Task.Run(() =>
            SettlementQueryService.AvailableTypes(Personal.Year?.Value ?? DateTime.Now.Year, name));
        var types = new List<PersonalSettlementViewModel.TypeOption> { new(null, "汇总") };
        types.AddRange(available.Select(pt => new PersonalSettlementViewModel.TypeOption(pt, pt)));
        TypeOptions = types;
        if (PersonType is null || !types.Any(t => t.Value == PersonType.Value))
            PersonType = types.Count == 2 ? types[1] : types[0];

        var result = await Task.Run(() =>
            SettlementQueryService.BuildMonthlyRows(
                Personal.Year?.Value ?? DateTime.Now.Year, name, PersonType?.Value, Month));
        Rows = result.Rows.Select(r => new GridRow(new Dictionary<string, object?>
        {
            ["项目"] = r.Label,
            ["本期"] = r.Current,
            ["本年累计"] = r.Total,
            ["备注"] = r.Note,
        }, isBold: r.Bold)).ToList();
        SummaryText = result.Rows.Count == 0
            ? $"{name} 在 {Personal.Year?.Value} 年无数据"
            : $"{name}（{PersonType?.Label ?? "汇总"}）· {Personal.Year?.Value}年{Month}月结算表预览（共{result.Rows.Count}行，确认后导出）";
    }

    /// <summary>导出月度结算表（gen_report 同款弹窗 + ExportService）。</summary>
    [RelayCommand]
    private async Task ExportMonthlyAsync()
    {
        var names = PersonOptions.Where(p => p.Name is not null).Select(p => p.Name!).ToList();
        if (names.Count == 0)
        {
            MessageDialog.Info("提示", "当前年份没有可导出的员工");
            return;
        }
        var choice = MonthlyExportDialog.Pick(names);
        if (choice is null) return;
        var (month, selected) = choice.Value;
        if (selected.Count == 0)
        {
            MessageDialog.Info("提示", "未选择任何员工");
            return;
        }
        // QA B4：PickFolder 为 PersonalSettlementViewModel 的 public static，直接类名调用
        string? outDir = PersonalSettlementViewModel.PickFolder();
        if (outDir is null) return;
        int year = Personal.Year?.Value ?? DateTime.Now.Year;
        string outPath = System.IO.Path.Combine(outDir, $"{year}年{month}月结算表.xlsx");
        // TODO(minor)：Tab2 导出暂无进度窗（单文件秒级完成，进度收益低）；
        // 需要时复用 PersonalSettlementViewModel.RunExportAsync 模式（QAT 评审记录）
        await _exportService.ExportMonthlyAsync(outPath, year, month, selected, null, System.Threading.CancellationToken.None);
        MessageDialog.Info("生成完成", $"已生成：{outPath}");
    }
}
