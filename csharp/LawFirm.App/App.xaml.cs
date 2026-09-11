using System.Globalization;
using System.Windows;
using System.Windows.Markup;
using LawFirm.UI.Shell;

namespace LawFirm.App;

/// <summary>
/// WPF 应用入口（T5 起为产品壳启动器）。
///
/// - 显式 new MainShellWindow()（不用 StartupUri，规格 §2.2-3；T2 演示窗 MainWindow 已整体替换）。
/// - 强制 zh-CN（规格 §4.5 / R9：StringFormat N2 千分位受 CurrentCulture 影响，
///   防止系统区域导致 "1.234,56"）；同步 OverrideMetadata 使所有 FrameworkElement 默认语言一致。
/// </summary>
public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        // 1) 文化强制：数值格式 1,234.56（zh-CN）
        var zh = new CultureInfo("zh-CN");
        CultureInfo.CurrentCulture = zh;
        CultureInfo.CurrentUICulture = zh;
        FrameworkElement.LanguageProperty.OverrideMetadata(
            typeof(FrameworkElement),
            new FrameworkPropertyMetadata(XmlLanguage.GetLanguage("zh-CN")));

        // 2) 未处理异常兜底：应用不崩溃（T5.4 验收 ⑥ 精神），错误弹窗报告
        DispatcherUnhandledException += (_, args) =>
        {
            MessageBox.Show(
                $"发生未处理异常：{args.Exception.Message}\n\n{args.Exception.StackTrace}",
                "错误", MessageBoxButton.OK, MessageBoxImage.Error);
            args.Handled = true;
        };

        base.OnStartup(e);

        // 3) 主窗（无边框壳 + 侧栏 + 页面栈；T2 的 MainWindow 演示窗已整体替换，规格 §1.1）
        var window = new MainShellWindow();
        MainWindow = window;
        window.Show();
    }
}
