using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LawFirm.UI.Controls;
using LawFirm.UI.Dialogs;
using LawFirm.UI.Services;

namespace LawFirm.UI.ViewModels;

/// <summary>
/// Tab1「个人结算总表」VM（settlement_view.py:53-425 逐项对齐）：
/// 筛选栏（年份/经办人/月份/类型）+ 表格（动态列）+ 导出三按钮 + 摘要 + 日志。
/// 数据经 SettlementQueryService 在后台线程计算（UI 不冻结）；导出经 ExportService 异步。
/// </summary>
public partial class PersonalSettlementViewModel : ViewModelBase
{
    private readonly ExportService _exportService = new();
    private CancellationTokenSource? _cts;

    /// <summary>年份选项（今年往前 3 年）。</summary>
    public sealed record YearOption(int Value)
    {
        public string Label => $"{Value}年";
    }

    /// <summary>月份选项（0 = 全部月份）。</summary>
    public sealed record MonthOption(int Value, string Label);

    /// <summary>类型选项（null = 汇总）。</summary>
    public sealed record TypeOption(string? Value, string Label);

    /// <summary>经办人选项（null = 「请选择经办人」占位）。</summary>
    public sealed record PersonOption(string? Name)
    {
        public string Label => Name ?? "请选择经办人";
    }

    public PersonalSettlementViewModel()
    {
        int cur = DateTime.Now.Year;
        Years = new List<YearOption>(Enumerable.Range(0, 3).Select(i => new YearOption(cur - i)));
        Months = new List<MonthOption>
        {
            new MonthOption(0, "全部月份"),
        }.Concat(Enumerable.Range(1, 12).Select(mo => new MonthOption(mo, $"{mo}月"))).ToList();
        Types = new List<TypeOption>
        {
            new TypeOption(null, "汇总"), new TypeOption("合伙", "合伙"),
            new TypeOption("聘用", "聘用"), new TypeOption("兼职", "兼职"),
        };
    }

    /// <summary>首次显示时初始化：默认选「最近有数据的年份」+ 载人员列表（settlement_view.py:143-150）。</summary>
    public void InitializeOnce()
    {
        if (_initialized) return;
        _initialized = true;
        _ = ReloadPersonsAsync();
    }
    private bool _initialized;

    // ---- 筛选状态 ----
    [ObservableProperty] private List<YearOption> _years;
    [ObservableProperty] private YearOption? _year;
    [ObservableProperty] private List<PersonOption> _persons = new();
    [ObservableProperty] private PersonOption? _person;
    [ObservableProperty] private List<MonthOption> _months;
    [ObservableProperty] private MonthOption? _month;
    [ObservableProperty] private List<TypeOption> _types;
    [ObservableProperty] private TypeOption? _personType;

    // ---- 表格 / 摘要 / 日志 ----
    [ObservableProperty] private List<GridRow> _rows = new();
    [ObservableProperty] private string _summaryText = "请选择经办人";
    [ObservableProperty] private string _logText = string.Empty;
    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(ExportAllCommand))]
    [NotifyCanExecuteChangedFor(nameof(ExportSelectedCommand))]
    [NotifyCanExecuteChangedFor(nameof(ExportMonthlyCommand))]
    private bool _isBusy;

    /// <summary>列 key 集合变化（月模式 14 列 ↔ 月列模式）→ View 重建列。</summary>
    public event Action<IReadOnlyList<string>>? ColumnsChanged;

    partial void OnYearChanged(YearOption? value) => _ = ReloadPersonsAsync();
    partial void OnPersonChanged(PersonOption? value) => _ = SyncTypesAndRefreshAsync();
    partial void OnMonthChanged(MonthOption? value) => _ = RefreshAsync();
    partial void OnPersonTypeChanged(TypeOption? value) => _ = RefreshAsync();

    private int YearValue => Year?.Value ?? DateTime.Now.Year;
    private int MonthValue => Month?.Value ?? 0;
    private string? PersonName => Person?.Name;

    /// <summary>人员列表重载（_reload_persons）：清空选择，等用户再选（Python 同款行为）。</summary>
    [RelayCommand]
    private async Task ReloadPersonsAsync()
    {
        int year = YearValue;
        var names = await Task.Run(() => SettlementQueryService.PersonNames(year));
        var list = new List<PersonOption> { new PersonOption(null) };
        list.AddRange(names.Select(n => new PersonOption(n)));
        Persons = list;
        Person = list[0];
        Rows = new List<GridRow>();
        SummaryText = "请选择经办人";
    }

    /// <summary>选人后：类型下拉按该人实际身份重建（_sync_type_options）+ 刷新。</summary>
    private async Task SyncTypesAndRefreshAsync()
    {
        var name = PersonName;
        if (name is null)
        {
            Rows = new List<GridRow>();
            SummaryText = "请选择经办人";
            return;
        }
        int year = YearValue;
        var available = await Task.Run(() => SettlementQueryService.AvailableTypes(year, name));
        var types = new List<TypeOption> { new TypeOption(null, "汇总") };
        types.AddRange(available.Select(pt => new TypeOption(pt, pt)));
        // 单身份自动选中该身份；多身份默认汇总（settlement_view.py:230-232）
        Types = types;
        PersonType = types.Count == 2 ? types[1] : types[0];
        await RefreshAsync();
    }

    /// <summary>刷新表格（refresh + _build_rows）。</summary>
    [RelayCommand]
    private async Task RefreshAsync()
    {
        var name = PersonName;
        if (name is null)
        {
            Rows = new List<GridRow>();
            SummaryText = "请选择经办人";
            return;
        }
        int year = YearValue;
        int month = MonthValue;
        string? ptype = PersonType?.Value;
        var result = await Task.Run(() =>
            SettlementQueryService.BuildPersonalRows(year, name, ptype, month));
        Rows = result.Rows.ToList();
        ColumnsChanged?.Invoke(result.ColumnKeys);
        if (result.Rows.Count == 0)
        {
            SummaryText = $"{name} 在 {year} 年无数据";
            return;
        }
        SummaryText = $"{name}（{PersonType?.Label ?? "汇总"}）{(month > 0 ? $"·1-{month}月累计" : "·全年")}";
    }

    // ---------------- 导出（T5.4 接线；CanExecute=!IsBusy 防重入，R7） ----------------

    [RelayCommand(CanExecute = nameof(CanExport))]
    private async Task ExportAllAsync()
    {
        string? outDir = PickFolder();
        if (outDir is null) return;                       // 用户取消 → 静默返回（Python:348-350）
        int year = YearValue;
        LogText = $"正在生成 {year} 年个人结算总表…";
        await RunExportAsync(
            // QA B3：lambda 在 RunExportAsync 内 _cts 赋值之后才执行，取 Token 安全
            () => _exportService.ExportAllAsync(outDir, year, ProgressSink(), _cts!.Token),
            files => $"已生成 {files.Count} 份个人结算总表\n输出目录：{outDir}",
            files => { foreach (var f in files) AppendLog($"✓ {Path.GetFileName(f)}"); });
    }

    [RelayCommand(CanExecute = nameof(CanExport))]
    private async Task ExportSelectedAsync()
    {
        var names = Persons.Where(p => p.Name is not null).Select(p => p.Name!).ToList();
        if (names.Count == 0)
        {
            MessageDialog.Info("提示", "当前年份没有可导出的经办人");
            return;
        }
        var selected = ExportScopeDialog.PickPersons(names);
        if (selected is null) return;                     // 取消
        if (selected.Count == 0)
        {
            MessageDialog.Info("提示", "未选择任何经办人");
            return;
        }
        string? outDir = PickFolder();
        if (outDir is null) return;
        int year = YearValue;
        LogText = $"正在为 {selected.Count} 人生成 {year} 年结算总表…";
        await RunExportAsync(
            () => _exportService.ExportSelectedAsync(outDir, year, selected, ProgressSink(), _cts!.Token),
            files => $"已生成 {files.Count} 份\n输出目录：{outDir}",
            files => { foreach (var f in files) AppendLog($"✓ {Path.GetFileName(f)}"); });
    }

    /// <summary>导出月度结算表（gen_report：月份 + 多选员工，记忆上次选择；Python:445-517）。</summary>
    [RelayCommand(CanExecute = nameof(CanExport))]
    private async Task ExportMonthlyAsync()
    {
        var names = Persons.Where(p => p.Name is not null).Select(p => p.Name!).ToList();
        if (names.Count == 0)
        {
            MessageDialog.Info("提示", "当前年份没有可导出的员工");
            return;
        }
        var choice = MonthlyExportDialog.Pick(names);
        if (choice is null) return;                       // 取消
        var (month, selected) = choice.Value;
        if (selected.Count == 0)
        {
            MessageDialog.Info("提示", "未选择任何员工");
            return;
        }
        string? outDir = PickFolder();
        if (outDir is null) return;
        int year = YearValue;
        string fileName = $"{year}年{month}月结算表.xlsx";
        string outPath = Path.Combine(outDir, fileName);
        LogText = $"正在生成 {year}年{month}月 结算表（{selected.Count} 人）…";
        await RunExportAsync(
            () => _exportService.ExportMonthlyAsync(outPath, year, month, selected, ProgressSink(), _cts!.Token),
            files => $"已生成：{files[0]}",
            files => AppendLog($"✓ {files[0]}"));
    }

    private bool CanExport() => !IsBusy;

    // ---------------- 内部 ----------------

    private IProgress<Services.ProgressReport> ProgressSink()
        => new Progress<Services.ProgressReport>(r => StatusTextUpdated?.Invoke(r));

    /// <summary>进度上报（ProgressWindow 订阅；Progress<T> 回调已在 UI 线程，规格 §6.3-2）。</summary>
    public event Action<Services.ProgressReport>? StatusTextUpdated;

    private async Task RunExportAsync(
        Func<Task<IReadOnlyList<string>>> export,
        Func<IReadOnlyList<string>, string> doneMessage,
        Action<IReadOnlyList<string>> logFiles)
    {
        var cts = new CancellationTokenSource();
        _cts = cts;   // 供导出 lambda 取 Token（QA B3）
        var win = new ProgressWindow
        {
            Owner = System.Windows.Application.Current.MainWindow,
            DataContext = new ProgressViewModel(),
        };
        EventHandler? cancelHandler = null;
        if (win.DataContext is ProgressViewModel pvm)
        {
            StatusTextUpdated += pvm.Update;
            // QA M2：取消链路打通 —— 进度窗「取消」→ CancelRequested → 本 VM 的 cts.Cancel()。
            // ExportService 在每个人/每文件边界检查 ct（ThrowIfCancellationRequested），
            // 故点取消最多再落盘 1 个文件即停止；已落盘文件不删（U7）。
            // 用局部快照 cts（finally 里 _cts 置 null 后仍可取消）；导出已结束时 Dispose 触发的
            // ObjectDisposedException 吞掉即可。
            cancelHandler = (_, _) =>
            {
                try { cts.Cancel(); }
                catch (ObjectDisposedException) { /* 导出已结束，忽略 */ }
            };
            pvm.CancelRequested += cancelHandler;
        }
        win.Show();
        IsBusy = true;
        try
        {
            var files = await export();
            AppendLog(string.Empty);
            AppendLog($"完成：共 {files.Count} 份");
            logFiles(files);
            MessageDialog.Info("生成完成", doneMessage(files));
        }
        catch (OperationCanceledException)
        {
            AppendLog("已取消。");
        }
        catch (Exception ex)
        {
            AppendLog($"✗ 生成失败: {ex.Message}");
            MessageDialog.Error("生成失败", ex.Message);
        }
        finally
        {
            IsBusy = false;
            if (win.DataContext is ProgressViewModel pvm2)
            {
                StatusTextUpdated -= pvm2.Update;
                if (cancelHandler is not null) pvm2.CancelRequested -= cancelHandler;
            }
            win.Close();
            cts.Dispose();
            _cts = null;
        }
    }

    private void AppendLog(string line) => LogText = LogText.Length == 0 ? line : LogText + "\n" + line;

    /// <summary>目录选择（.NET 8+ WPF OpenFolderDialog；各 tab 共用）。</summary>
    public static string? PickFolder()
    {
        // TODO(verify): Microsoft.Win32.OpenFolderDialog 为 .NET 8+ WPF API，net10 下应可用
        var dlg = new Microsoft.Win32.OpenFolderDialog { Title = "选择输出目录" };
        return dlg.ShowDialog() == true ? dlg.FolderName : null;
    }
}
