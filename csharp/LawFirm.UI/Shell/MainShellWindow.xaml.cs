using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace LawFirm.UI.Shell;

/// <summary>
/// 无边框主窗（规格 §3.1）。
///
/// - WindowChrome 提供 36px 拖拽区 + 6px 缩放边，替代 Python FramelessWindow。
/// - U1 采纳 WM_GETMINMAXINFO 方案：最大化时不覆盖任务栏（WindowStyle=None 的经典缺陷补偿）。
/// - 导航：Sidebar 的 DataContext = NavigationService；NavigateRequested 换页（页面缓存复用）。
/// </summary>
public partial class MainShellWindow : Window
{
    private readonly NavigationService _nav = new();

    public MainShellWindow()
    {
        InitializeComponent();
        DataContext = _nav;   // TitleBar/Sidebar 经继承获得 DataContext
        _nav.NavigateRequested += OnNavigateRequested;

        SourceInitialized += (_, _) => ApplyMaximizeWorkAreaFix();
        Loaded += (_, _) =>
        {
            // 初始页：默认选中「导入台账」（main_window.py 默认页）→ 直接展示占位页
            ShowPage(_nav.SelectedKey ?? "import");
        };
        // T5.2 首日最小验证入口（U5/R1）：F12 打开最小样本窗
        PreviewKeyDown += OnPreviewKeyDown;
    }

    private void OnPreviewKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == System.Windows.Input.Key.F12)
        {
            new Samples.TwoTierMinSampleWindow { Owner = this }.Show();
            e.Handled = true;
        }
    }

    private void OnNavigateRequested(object? sender, string key) => ShowPage(key);

    private void ShowPage(string key)
    {
        PageHost.Content = _nav.GetOrCreatePage(key);
    }

    // ---------------------------------------------------------------
    // U1：最大化不覆盖任务栏（WM_GETMINMAXINFO）
    // ---------------------------------------------------------------
    protected override void OnStateChanged(EventArgs e)
    {
        base.OnStateChanged(e);
        // 还原时清掉补偿尺寸，交回 MinWidth/MinHeight/Width/Height
        if (WindowState == WindowState.Normal)
        {
            ClearValue(MaxHeightProperty);
            ClearValue(MaxWidthProperty);
        }
    }

    private void ApplyMaximizeWorkAreaFix()
    {
        var source = PresentationSource.FromVisual(this) as HwndSource;
        source?.AddHook(WndProc);
    }

    private static IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == WM_GETMINMAXINFO)
        {
            var mmi = Marshal.PtrToStructure(lParam, typeof(MINMAXINFO)) is MINMAXINFO v ? v : default;
            IntPtr monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
            if (monitor != IntPtr.Zero)
            {
                var info = new MONITORINFO();
                GetMonitorInfo(monitor, ref info);
                // 工作区 = 去掉任务栏后的区域；最大化时限制到工作区
                mmi.ptMaxPosition.x = Math.Abs(info.rcWork.left - info.rcMonitor.left);
                mmi.ptMaxPosition.y = Math.Abs(info.rcWork.top - info.rcMonitor.top);
                mmi.ptMaxSize.x = Math.Abs(info.rcWork.right - info.rcWork.left);
                mmi.ptMaxSize.y = Math.Abs(info.rcWork.bottom - info.rcWork.top);
            }
            Marshal.StructureToPtr(mmi, lParam, true);
            handled = true;
        }
        return IntPtr.Zero;
    }

    private const int WM_GETMINMAXINFO = 0x0024;
    private const IntPtr MONITOR_DEFAULTTONEAREST = new(2);

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int x; public int y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int left, top, right, bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MINMAXINFO
    {
        public POINT ptReserved;
        public POINT ptMaxSize;
        public POINT ptMaxPosition;
        public POINT ptMinTrackSize;
        public POINT ptMaxTrackSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MONITORINFO
    {
        public int cbSize;
        public RECT rcMonitor;
        public RECT rcWork;
        public uint dwFlags;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromWindow(IntPtr hwnd, IntPtr dwFlags);

    [DllImport("user32.dll")]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO lpmi);
}
