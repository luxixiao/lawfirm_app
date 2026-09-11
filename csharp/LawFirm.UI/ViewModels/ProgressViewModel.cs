using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LawFirm.UI.Services;

namespace LawFirm.UI.ViewModels;

/// <summary>
/// 进度窗 VM（规格 §6.3）：进度值 / 状态文本 / 取消命令。
/// 由 PersonalSettlementViewModel 等在导出期间注入 ProgressWindow.DataContext。
/// </summary>
public partial class ProgressViewModel : ViewModelBase
{
    [ObservableProperty] private double _progressValue;
    [ObservableProperty] private string _statusText = "正在准备…";

    /// <summary>进度上报回调（Progress&lt;T&gt; 保证在 UI 线程回调）。</summary>
    public void Update(ProgressReport r)
    {
        ProgressValue = r.Percent;
        StatusText = r.Done >= r.Total && r.Total > 0
            ? "完成"
            : $"正在导出 {r.CurrentItem}（{r.Done}/{r.Total}）…";
    }

    /// <summary>取消（导出 VM 的 _cts.Cancel()）。</summary>
    public event EventHandler? CancelRequested;

    [RelayCommand]
    private void Cancel() => CancelRequested?.Invoke(this, EventArgs.Empty);
}
