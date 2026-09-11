using System.Windows;
using System.Windows.Input;

namespace LawFirm.UI.Dialogs;

/// <summary>
/// 异步进度窗（规格 §6.3）：非模态（Show + Owner）。
/// 取消链路（QA M2）：取消按钮 → DataContext(ProgressViewModel).CancelCommand
/// → CancelRequested 事件 → 发起导出的 VM（PersonalSettlementViewModel.RunExportAsync）
/// 订阅并调 _cts.Cancel() —— 窗口本身不处理取消，只负责承载。
/// </summary>
public partial class ProgressWindow : Window
{
    public ProgressWindow()
    {
        InitializeComponent();
    }

    /// <summary>拖动窗口（WindowStyle=None 时 WindowChrome 不生效于子窗，这里手动补）。</summary>
    protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonDown(e);
        if (e.ButtonState == MouseButtonState.Pressed) DragMove();
    }
}
