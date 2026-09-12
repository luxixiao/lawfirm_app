using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using Dapper;
using LawFirm.Data;
using LawFirm.Data.Expense;
using LawFirm.Data.Staff;
using LawFirm.Exporter;
using Microsoft.Data.Sqlite;
using NPOI.HSSF.UserModel;
using NPOI.SS.UserModel;
using NPOI.XSSF.UserModel;

namespace LawFirm.Cli;

/// <summary>
/// 命令行入口：只读打开 data/lawfirm.db，复算结算引擎并用 NPOI 导出 xlsx，
/// 供 scripts/diff_xlsx.py 与 Python 应用导出的 golden 逐格比对（回归安全网）。
///
/// 用法（在仓库根 lawfirm_app/ 下，先 dotnet build）：
///   # 月度结算表（默认 --report month，行为与 T2 完全一致）
///   dotnet run --project csharp/LawFirm.Cli -- ^
///       --year 2026 --month 12 --out csharp/out/settlement_report_csharp.xlsx
///
///   # 个人结算总表（T3，每人一文件）
///   dotnet run --project csharp/LawFirm.Cli -- ^
///       --report person --year 2026 --out-dir csharp/out/person_settlement
///   # 只导指定人（可多次 --person）
///   dotnet run --project csharp/LawFirm.Cli -- ^
///       --report person --year 2026 --out-dir csharp/out/person_settlement --person 丁祥锋
/// </summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        int year = DateTime.Today.Year;
        int month = 12;
        string outPath = "csharp/out/settlement_report_csharp.xlsx";
        // T3 新增：报告类型选择器，默认 "month"（= 现有月度结算表路径，行为不变）
        string report = "month";
        string outDir = "csharp/out/person_settlement";
        var onlyPersons = new List<string>();

        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--year" when i + 1 < args.Length: year = int.Parse(args[++i]); break;
                case "--month" when i + 1 < args.Length: month = int.Parse(args[++i]); break;
                case "--out" when i + 1 < args.Length: outPath = args[++i]; break;
                case "--report" when i + 1 < args.Length: report = args[++i]; break;
                case "--out-dir" when i + 1 < args.Length: outDir = args[++i]; break;
                case "--person" when i + 1 < args.Length: onlyPersons.Add(args[++i]); break;
                case "--selftest-ui" when i + 1 == args.Length: return RunUiSelfTest();
                case "--diag-db" when i + 1 == args.Length: return RunDiagDb();
                case "--diag-shell" when i + 1 == args.Length: return RunDiagShell();
                case "--diag-sidebar" when i + 1 == args.Length: return RunDiagSidebar();
                case "--diag-persons" when i + 1 == args.Length: return RunDiagPersons();
                case "--diag-switch" when i + 1 == args.Length: return RunDiagSwitch();
                case "--diag-switch-monthly" when i + 1 == args.Length: return RunDiagSwitchMonthly();
                case "--diag-write-selftest" when i + 1 == args.Length: return RunWriteSelfTest();
                case "--diag-base-data" when i + 1 == args.Length: return RunDiagBaseData();
                case "--diag-expense-cat" when i + 1 == args.Length: return RunDiagExpenseCat();
                case "--diag-staff-import" when i + 1 == args.Length: return RunDiagStaffImport();
                default:
                    Console.Error.WriteLine($"未知参数或缺少取值: {args[i]}");
                    return 2;
            }
        }

        try
        {
            string dbPath = DbConnection.FindDatabase();
            Console.WriteLine($"[INFO] 只读打开数据库: {dbPath}");

            using SqliteConnection conn = DbConnection.OpenReadOnly(dbPath);

            // ---- T3：个人结算总表分支 ----
            if (report == "person")
            {
                if (onlyPersons.Count == 0)
                {
                    var files = PersonSettlementExporter.ExportAll(conn, outDir, year);
                    if (files.Count == 0)
                    {
                        Console.WriteLine($"[OK] 无结算人员数据，未生成任何文件（{year}年）。");
                    }
                    else
                    {
                        Console.WriteLine($"[OK] 已导出台数 {files.Count}，目录: {outDir}");
                        foreach (var f in files) Console.WriteLine($"  - {f}");
                    }
                }
                else
                {
                    foreach (var p in onlyPersons)
                    {
                        var safe = p.Replace("/", "_").Replace("\\", "_").Trim();
                        if (string.IsNullOrEmpty(safe)) safe = "未命名";
                        string pPath = Path.Combine(outDir, $"个人结算总表_{safe}.xlsx");
                        string generated = PersonSettlementExporter.ExportOne(conn, p, pPath, year);
                        Console.WriteLine($"[OK] 已导出: {generated}");
                    }
                }
                Console.WriteLine("[NEXT] 生成 golden 并逐格比对，请运行：");
                Console.WriteLine($"  scripts\\t3_verify.bat {year}");
                return 0;
            }

            // ---- 默认：月度结算表（行为与 T2 逐字一致） ----
            // 名单 = 全选（与 Python dump_golden 的 sorted(build_settlement(year).keys()) 一致；
            // StringComparer.Ordinal 对应 Python 默认 codepoint 排序，保证 sheet 顺序一致）
            var persons = SettlementEngine.Build(conn, year)
                .Keys.OrderBy(k => k, StringComparer.Ordinal).ToList();

            string generatedMonth = SettlementReportExporter.ExportReport(conn, outPath, year, month, persons);
            Console.WriteLine($"[OK] 已导出: {generatedMonth}");
            // 注意：不要在控制台打印「可直接复制的 python 中文路径命令」——
            // cmd.exe 用 GBK 代码页传中文文件名给 python 会乱码（历史坑），且多行
            // 文本粘进 cmd 会被当成多条命令。统一引导到一键脚本。
            Console.WriteLine("[NEXT] 生成 golden 并逐格比对，请运行：");
            Console.WriteLine($"  scripts\\t2_verify.bat {year} {month}");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[ERROR] 导出失败: {ex.Message}");
            return 1;
        }
    }

    /// <summary>
    /// T5 自测：ComputeFill 4 个单测（ColumnLayoutManagerTests，规格 T5.2 验收 ⑦）。
    /// 不触数据库、不触 UI 线程；退出码 0=全绿，1=有失败。
    /// </summary>
    private static int RunUiSelfTest()
    {
        Console.WriteLine("[INFO] T5 ComputeFill self-test...");
        var failures = LawFirm.UI.Controls.ColumnLayoutManagerTests.RunAll();
        if (failures.Count == 0)
        {
            Console.WriteLine($"[OK] {5} cases passed.");
            return 0;
        }
        foreach (var f in failures) Console.Error.WriteLine($"[FAIL] {f}");
        return 1;
    }

    /// <summary>
    /// 诊断：主窗 XAML 解析冒烟（不 Show，纯构造 + 关窗）。
    /// 侧栏底部新增了数据库状态区（x:Static 绑定），一旦 StaticResource/类型名写错，
    /// 这里会直接 XamlParseException 失败，避免"编译过了但一启动白屏"。
    /// 退出码 0=通过，1=失败。
    /// </summary>
    private static int RunDiagShell()
    {
        Exception? failure = null;
        string info = string.Empty;

        var t = new Thread(() =>
        {
            try
            {
                var db = LawFirm.UI.Services.DbStatusService.Current;
                info = $"IsOk={db.IsOk}; Display={db.Display}; Source={db.SourceText}; Path={db.FullPath}";

                var app = CreateAppWithResources();
                var w = new LawFirm.UI.Shell.MainShellWindow();
                info += $"; Title={w.Title}";
                w.Close();
                app.Shutdown();
            }
            catch (Exception ex)
            {
                failure = ex;
            }
            finally
            {
                System.Windows.Threading.Dispatcher.CurrentDispatcher.InvokeShutdown();
            }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.IsBackground = true;
        t.Start();

        if (!t.Join(TimeSpan.FromSeconds(30)))
        {
            Console.WriteLine("[FAIL] 主窗构造超时（30s）");
            return 1;
        }
        if (failure is not null)
        {
            Console.WriteLine($"[FAIL] {failure.GetType().Name}: {failure.Message}");
            Console.WriteLine(failure.StackTrace);
            return 1;
        }
        Console.WriteLine("[OK] 主窗 XAML 解析通过（含侧栏数据库状态区）");
        Console.WriteLine($"[INFO] {info}");
        return 0;
    }

    /// <summary>
    /// 按 App.xaml 的顺序合并主题字典。无 Application 时 StaticResource 无处可查，
    /// 会在 InitializeComponent 里抛 XamlParseException（假失败），必须先补上。
    /// </summary>
    private static System.Windows.Application CreateAppWithResources()
    {
        var app = new System.Windows.Application();
        foreach (string theme in new[] { "Palette", "Typography", "NotionControls", "Generic" })
        {
            app.Resources.MergedDictionaries.Add(new System.Windows.ResourceDictionary
            {
                Source = new Uri($"pack://application:,,,/LawFirm.UI;component/Themes/{theme}.xaml", UriKind.Absolute),
            });
        }
        return app;
    }

    /// <summary>离屏跑一轮布局（Measure/Arrange 两遍，让嵌套 ItemsControl 生成容器）。</summary>
    private static void OffscreenLayout(System.Windows.FrameworkElement root, double w, double h)
    {
        var size = new System.Windows.Size(w, h);
        for (int pass = 0; pass < 2; pass++)
        {
            root.Measure(size);
            root.Arrange(new System.Windows.Rect(0, 0, w, h));
            root.UpdateLayout();
        }
    }

    private static IEnumerable<System.Windows.DependencyObject> VisualDescendants(System.Windows.DependencyObject root)
    {
        int n = System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);
        for (int i = 0; i < n; i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
            yield return child;
            foreach (var d in VisualDescendants(child)) yield return d;
        }
    }

    /// <summary>侧栏子项面板（DataTemplate 里 x:Name="GroupPanel" 的 ItemsControl）中可见的个数。</summary>
    private static List<System.Windows.Controls.ItemsControl> GroupPanels(System.Windows.DependencyObject side)
        => VisualDescendants(side)
            .OfType<System.Windows.Controls.ItemsControl>()
            .Where(ic => ic.Name == "GroupPanel")
            .ToList();

    /// <summary>
    /// 沿视觉树向上逐级检查 Visibility —— WPF 的"可见"是祖先链继承的：
    /// 面板设成 Collapsed 后，子元素自己的 Visibility 属性仍是 Visible，
    /// 只看自身属性会误判（离线树里也没法用 IsVisible，它要求有呈现源）。
    /// </summary>
    private static bool ChainVisible(System.Windows.DependencyObject d)
    {
        var cur = d;
        while (cur is not null)
        {
            if (cur is System.Windows.UIElement ui && ui.Visibility != System.Windows.Visibility.Visible)
                return false;
            cur = System.Windows.Media.VisualTreeHelper.GetParent(cur);
        }
        return true;
    }

    private static int CountVisiblePanels(System.Windows.DependencyObject side)
        => GroupPanels(side).Count(p => ChainVisible(p));

    /// <summary>子项面板内**视觉上真能看见**的子项文字个数（收起态必须为 0）。</summary>
    private static int CountVisibleItemTexts(System.Windows.DependencyObject side)
    {
        int n = 0;
        foreach (var panel in GroupPanels(side))
        {
            foreach (var d in VisualDescendants(panel))
            {
                if (d is System.Windows.Controls.TextBlock tb
                    && !string.IsNullOrEmpty(tb.Text)
                    && ChainVisible(tb))
                {
                    n++;
                }
            }
        }
        return n;
    }

    /// <summary>
    /// 诊断：侧栏收起态行为（不弹窗，离屏布局后检查视觉树）。
    ///
    /// 覆盖 T5.1 折叠语义：收起时**子项面板整块收起**。只隐藏组名不够——
    /// 子项行会继续渲染，窄宽度下文字被压成单字（"导…/导…/修…"），
    /// 且撑高内容触发竖向滚动条，滚动条再吃掉 17px 宽把文字彻底裁掉。
    /// 退出码 0=通过，1=失败。
    /// </summary>
    private static int RunDiagSidebar()
    {
        var failures = new List<string>();
        string info = string.Empty;

        var t = new Thread(() =>
        {
            try
            {
                var app = CreateAppWithResources();
                var nav = new LawFirm.UI.Shell.NavigationService();
                var side = new LawFirm.UI.Shell.Sidebar { DataContext = nav };
                var host = new System.Windows.Controls.Grid();
                host.Children.Add(side);

                // 展开态基线：必须有真实的子项面板与文字，否则是"测试夹具没跑起来"，不是功能通过
                OffscreenLayout(host, 240, 900);
                int expPanels = CountVisiblePanels(side);
                int expTexts = CountVisibleItemTexts(side);
                info = $"展开：可见面板 {expPanels}，可见子项文字 {expTexts}";
                if (expPanels == 0) failures.Add("展开态应至少有一个可见子项面板，实际 0（夹具可能没生效）");
                if (expTexts == 0) failures.Add("展开态应至少有一条可见子项文字，实际 0（夹具可能没生效）");

                // 收起态：面板必须全部收起、子项文字一条都不许可见
                side.IsCollapsed = true;
                OffscreenLayout(host, 60, 900);
                int colPanels = CountVisiblePanels(side);
                int colTexts = CountVisibleItemTexts(side);
                info += $"；收起：可见面板 {colPanels}，可见子项文字 {colTexts}";
                if (colPanels != 0)
                    failures.Add($"收起态子项面板应全部收起，实际仍有 {colPanels} 个可见（单字溢出/滚动条根因）");
                if (colTexts != 0)
                    failures.Add($"收起态不应有可见子项文字，实际 {colTexts} 条");

                // 再展开：面板要能回来（触发器是可逆的）
                side.IsCollapsed = false;
                OffscreenLayout(host, 240, 900);
                int rePanels = CountVisiblePanels(side);
                info += $"；再展开：可见面板 {rePanels}";
                if (rePanels != expPanels)
                    failures.Add($"再展开后可见面板数应回到 {expPanels}，实际 {rePanels}（收起不可逆）");

                host.Children.Clear();
                app.Shutdown();
            }
            catch (Exception ex)
            {
                failures.Add($"{ex.GetType().Name}: {ex.Message}");
            }
            finally
            {
                System.Windows.Threading.Dispatcher.CurrentDispatcher.InvokeShutdown();
            }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.IsBackground = true;
        t.Start();

        if (!t.Join(TimeSpan.FromSeconds(60)))
        {
            Console.WriteLine("[FAIL] 侧栏冒烟超时（60s）");
            return 1;
        }

        Console.WriteLine($"[INFO] {info}");
        if (failures.Count == 0)
        {
            Console.WriteLine("[OK] 侧栏收起态语义通过（收起不渲染子项）");
            return 0;
        }
        foreach (string f in failures) Console.Error.WriteLine($"[FAIL] {f}");
        Console.WriteLine($"[FAIL] {failures.Count} 项未通过");
        return 1;
    }

    /// <summary>
    /// 诊断：数据库定位链路（R-M2 / R-M4）。打印环境变量、命中来源、最终路径与逐条查找过程。
    /// 三机实测报障时先跑这个：一眼看出是不是残留 LAWFIRM_DB 或 Seafile 同步导致的空/半截文件。
    /// 退出码 0=找到，1=未找到。
    /// </summary>
    private static int RunDiagDb()
    {
        var r = DbConnection.ResolveDatabase();
        Console.WriteLine($"[INFO] LAWFIRM_DB = {(string.IsNullOrWhiteSpace(r.EnvValue) ? "（未设置）" : r.EnvValue)}"
            + (r.EnvIgnored ? "  ← 已设置但不可用，本次已忽略" : ""));
        Console.WriteLine($"[INFO] 来源 = {r.SourceText}");
        if (r.Found)
        {
            var fi = new FileInfo(r.Path!);
            Console.WriteLine($"[OK] DB = {r.Path}");
            Console.WriteLine($"[OK] 大小 = {fi.Length} 字节，最后修改 = {fi.LastWriteTime:yyyy-MM-dd HH:mm:ss}");
            return 0;
        }

        Console.WriteLine("[FAIL] 未找到可用的 lawfirm.db，查找过程：");
        foreach (string t in r.Tried) Console.WriteLine("  " + t);
        return 1;
    }

    /// <summary>
    /// 诊断：「各类报表」页数据路径（LatestDataYear → PersonNames）。
    /// 复现 UI 初始化链路但不进 WPF；异常全捕获打印，用于排查经办人下拉为空。
    /// 退出码 0=正常，1=抛异常。
    /// </summary>
    private static int RunDiagPersons()
    {
        try
        {
            Console.WriteLine($"[INFO] DB = {DbConnection.FindDatabase()}");
            int latest = LawFirm.UI.Services.SettlementQueryService.LatestDataYear();
            Console.WriteLine($"[OK] LatestDataYear() = {latest}");
            for (int y = latest; y >= latest - 3; y--)
            {
                var names = LawFirm.UI.Services.SettlementQueryService.PersonNames(y);
                string preview = names.Count > 0
                    ? "：" + string.Join("、", names.Take(8)) + (names.Count > 8 ? " …" : "")
                    : "（空）";
                Console.WriteLine($"[OK] PersonNames({y}) -> {names.Count} 人{preview}");
            }
            return 0;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[FAIL] {ex.GetType().Name}: {ex.Message}");
            Console.WriteLine(ex.StackTrace);
            return 1;
        }
    }

    /// <summary>
    /// 诊断：「切换经办人」重复全量刷新量化（性能回归门）。
    ///
    /// 背景：PersonalSettlementViewModel.OnPersonTypeChanged 会在 SyncTypesAndRefreshAsync
    /// 设 PersonType 时被自触发，导致一次切人跑两遍 RefreshAsync（引擎两遍 + ColumnsChanged 两遍）。
    /// 本命令用 SettlementQueryService.BuildCallCount 与 VM 的 Rows/SummaryText 变更事件量化：
    ///   - 修复前：一次切换 = 2 遍刷新 / 5 次 Build
    ///   - 修复后：一次切换 = 1 遍刷新 / 4 次 Build
    ///
    /// 完成信号：设 Person 后等 400ms 无新 Rows/SummaryText 事件（静默判定），单次与整体均 30s 超时。
    /// 退出码 0=全部切换完成，1=超时/初始化失败。
    /// </summary>
    private static int RunDiagSwitch()
    {
        Console.WriteLine("[INFO] 「切换经办人」重复刷新量化诊断（--diag-switch）");
        Console.WriteLine($"[INFO] DB = {DbConnection.FindDatabase()}");

        LawFirm.UI.ViewModels.PersonalSettlementViewModel vm;
        try
        {
            vm = new LawFirm.UI.ViewModels.PersonalSettlementViewModel();
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[FAIL] VM 构造失败: {ex.GetType().Name}: {ex.Message}");
            return 1;
        }

        var probe = new SwitchProbe();
        vm.PropertyChanged += probe.OnPropertyChanged;

        try
        {
            // 1. 初始化 + 轮询等人员列表就绪
            vm.InitializeOnce();
            var readySw = System.Diagnostics.Stopwatch.StartNew();
            while (vm.Persons.Count <= 1 && readySw.ElapsedMilliseconds < 30_000)
                Thread.Sleep(50);
            if (vm.Persons.Count <= 1)
            {
                Console.Error.WriteLine($"[FAIL] 人员列表未就绪（30s 超时，Persons={vm.Persons.Count}）");
                Console.Error.WriteLine($"[INFO] LogText={vm.LogText}");
                return 1;
            }
            Console.WriteLine($"[INFO] 人员列表就绪：共 {vm.Persons.Count} 项（首项为占位）");
            if (vm.Persons.Count < 3)
            {
                Console.Error.WriteLine($"[FAIL] 有效人员不足（需 >= 3 项，实际 {vm.Persons.Count}）");
                return 1;
            }

            int year = vm.Year?.Value ?? DateTime.Now.Year;

            // 「自动选中身份」= 恰好单一身份时选中该身份，否则「汇总」
            //（对应 SyncTypesAndRefreshAsync: types.Count == 2 ? types[1] : types[0]）。
            string AutoType(LawFirm.UI.ViewModels.PersonalSettlementViewModel.PersonOption p)
            {
                if (p.Name is null) return "占位";
                var av = LawFirm.UI.Services.SettlementQueryService.AvailableTypes(year, p.Name);
                return av.Count == 1 ? av[0] : "汇总";
            }

            // 默认按任务说明取 Persons[1]/[2]；但只有当两人「自动选中身份」不同时，
            // 切人才会改变 PersonType 从而触发被修复的那次自刷新。若二者相同，
            // 自动改取一对「自动身份不同」的人做确定性复现（并打印说明）。
            var pa = vm.Persons[1];
            var pb = vm.Persons[2];
            string ta = AutoType(pa), tb = AutoType(pb);
            bool switchedPair = false;
            if (ta == tb)
            {
                for (int i = 1; i < vm.Persons.Count; i++)
                {
                    if (vm.Persons[i].Name is null) continue;
                    string ti = AutoType(vm.Persons[i]);
                    if (ti == ta) continue;
                    pa = vm.Persons[1];
                    pb = vm.Persons[i];
                    tb = ti;
                    switchedPair = true;
                    break;
                }
            }
            Console.WriteLine($"[INFO] 交替对象：A = {pa.Name}（自动身份「{ta}」），B = {pb.Name}（自动身份「{tb}」）"
                + (switchedPair ? "  [注：Persons[1]/[2] 自动身份相同，已改取差异对以确保复现]" : "  [取 Persons[1]/[2]]"));
            if (ta == tb)
                Console.WriteLine("[WARN] 未找到自动身份不同的一对；本数据下切人不会改变 PersonType，基线与修复后可能相同");

            Console.WriteLine("[INFO] 计时口径：净耗时 = 「设 Person」→「最后一次 Rows/SummaryText 事件」；"
                + "400ms 静默窗口仅用于结束判定，不计入耗时。");

            // 结束判定：设 Person 后，等 Rows 事件出现且连续 400ms 无新事件（总超时 30s）；
            // 计时只取「设 Person」到「最后一次事件」的净耗时，静默尾部不计入。
            (bool Ok, long NetMs, int Builds, int Passes) SwitchTo(
                LawFirm.UI.ViewModels.PersonalSettlementViewModel.PersonOption target)
            {
                probe.Reset();
                LawFirm.UI.Services.SettlementQueryService.BuildCallCount = 0;
                long t0 = System.Diagnostics.Stopwatch.GetTimestamp();   // 计时起点（高精度）：设 Person 的那一刻
                vm.Person = target;   // 触发 OnPersonChanged → SyncTypesAndRefreshAsync（后台接续）
                var sw = System.Diagnostics.Stopwatch.StartNew();
                while (sw.ElapsedMilliseconds < 30_000)
                {
                    if (probe.RowsCount >= 1 && probe.MillisSinceLastEvent >= 400)
                        break;
                    Thread.Sleep(10);
                }
                bool ok = sw.ElapsedMilliseconds < 30_000;
                double netMsD = SwitchProbe.TicksToMs(probe.LastEventTimestamp - t0);   // 计时终点：最后一次事件时刻
                long netMs = netMsD < 0 ? 0 : (long)Math.Round(netMsD);
                return (ok, netMs, LawFirm.UI.Services.SettlementQueryService.BuildCallCount, probe.RowsCount);
            }

            // 预热一次（不计入统计），让 PersonType / Types 达到稳定基线。
            var warm = SwitchTo(pa);
            Console.WriteLine($"[INFO] 预热切至 {pa.Name}：净耗时 {warm.NetMs} ms，Build {warm.Builds}，刷新 {warm.Passes} 遍");

            // 2. 交替切换 10 次并统计
            var msList = new List<long>();
            var buildList = new List<int>();
            var passList = new List<int>();
            bool allOk = true;
            for (int k = 0; k < 10; k++)
            {
                var target = (k % 2 == 0) ? pb : pa;
                var r = SwitchTo(target);
                if (!r.Ok) allOk = false;
                msList.Add(r.NetMs);
                buildList.Add(r.Builds);
                passList.Add(r.Passes);
                Console.WriteLine($"  第 {k + 1,2} 次 → {target.Name,-8}：净耗时 {r.NetMs,5} ms   Build {r.Builds}   刷新 {r.Passes} 遍");
            }

            long medMs = Median(msList);
            int medBuild = (int)Median(buildList.Select(x => (long)x).ToList());
            int medPass = (int)Median(passList.Select(x => (long)x).ToList());

            Console.WriteLine($"[RESULT] 中位净耗时 = {medMs} ms（不含 400ms 静默窗口），中位 Build = {medBuild}，中位刷新 = {medPass} 遍");
            Console.WriteLine($"[RESULT] 净耗时分布 = [{string.Join(",", msList)}] ms");
            Console.WriteLine($"[RESULT] Build 次数分布 = [{string.Join(",", buildList)}]");
            Console.WriteLine($"[RESULT] 刷新遍数分布 = [{string.Join(",", passList)}]");
            Console.WriteLine(medPass == 1 && medBuild == 4
                ? "[OK] 与修复后预期一致（1 遍刷新 / 4 次 Build）"
                : medPass == 2 && medBuild == 5
                    ? "[WARN] 与修复前基线一致（2 遍刷新 / 5 次 Build）——可能未应用修复"
                    : "[INFO] 数值不在 1遍/4Build 与 2遍/5Build 两种预期内，请人工核对");

            return allOk ? 0 : 1;
        }
        finally
        {
            vm.PropertyChanged -= probe.OnPropertyChanged;
        }
    }

    /// <summary>
    /// 诊断：Tab2「月度结算表」重复全量刷新量化（--diag-switch-monthly）。
    ///
    /// 同一根因的第二处：MonthlyReportViewModel.RefreshAsync 内程序化设置 PersonType
    /// （仅当当前类型不在该人身份集合内时）会触发 OnPersonTypeChanged → 再跑一遍 RefreshAsync。
    /// 每次 RefreshAsync = AvailableTypes(3 次 Build) + BuildMonthlyRows(1 次 Build) = 4 次 Build，
    /// 故重复一遍后为 8 次 Build / 2 遍刷新；修复后应为 4 次 Build / 1 遍刷新。
    /// 计时口径同个人表：净耗时 = 设 Person → 最后一次 Rows/SummaryText 事件，400ms 静默不计入。
    /// </summary>
    private static int RunDiagSwitchMonthly()
    {
        Console.WriteLine("[INFO] 「月度结算表」重复刷新量化诊断（--diag-switch-monthly）");
        Console.WriteLine($"[INFO] DB = {DbConnection.FindDatabase()}");

        LawFirm.UI.ViewModels.PersonalSettlementViewModel personal;
        try
        {
            personal = new LawFirm.UI.ViewModels.PersonalSettlementViewModel();
            personal.InitializeOnce();
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[FAIL] Personal VM 构造失败: {ex.GetType().Name}: {ex.Message}");
            return 1;
        }

        var readySw = System.Diagnostics.Stopwatch.StartNew();
        while (personal.Persons.Count <= 1 && readySw.ElapsedMilliseconds < 30_000)
            Thread.Sleep(50);
        if (personal.Persons.Count <= 1)
        {
            Console.Error.WriteLine($"[FAIL] 人员列表未就绪（30s 超时，Persons={personal.Persons.Count}）");
            return 1;
        }
        if (personal.Persons.Count < 3)
        {
            Console.Error.WriteLine($"[FAIL] 有效人员不足（需 >= 3 项，实际 {personal.Persons.Count}）");
            return 1;
        }
        Console.WriteLine($"[INFO] 人员列表就绪：共 {personal.Persons.Count} 项（首项为占位）");

        var probe = new SwitchProbe();
        LawFirm.UI.ViewModels.MonthlyReportViewModel vm;
        try
        {
            vm = new LawFirm.UI.ViewModels.MonthlyReportViewModel(personal);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[FAIL] Monthly VM 构造失败: {ex.GetType().Name}: {ex.Message}");
            return 1;
        }
        vm.PropertyChanged += probe.OnPropertyChanged;

        try
        {
            int year = personal.Year?.Value ?? DateTime.Now.Year;
            string AutoType(LawFirm.UI.ViewModels.PersonalSettlementViewModel.PersonOption p)
            {
                if (p.Name is null) return "占位";
                var av = LawFirm.UI.Services.SettlementQueryService.AvailableTypes(year, p.Name);
                return av.Count == 1 ? av[0] : "汇总";
            }

            var pa = personal.Persons[1];
            var pb = personal.Persons[2];
            string ta = AutoType(pa), tb = AutoType(pb);
            bool switchedPair = false;
            if (ta == tb)
            {
                for (int i = 1; i < personal.Persons.Count; i++)
                {
                    if (personal.Persons[i].Name is null) continue;
                    string ti = AutoType(personal.Persons[i]);
                    if (ti == ta) continue;
                    pa = personal.Persons[1];
                    pb = personal.Persons[i];
                    tb = ti;
                    switchedPair = true;
                    break;
                }
            }
            Console.WriteLine($"[INFO] 交替对象：A = {pa.Name}（自动身份「{ta}」），B = {pb.Name}（自动身份「{tb}」）"
                + (switchedPair ? "  [Persons[1]/[2] 自动身份相同，已改取差异对]" : "  [取 Persons[1]/[2]]"));
            Console.WriteLine("[INFO] 计时口径：净耗时 = 「设 Person」→「最后一次 Rows/SummaryText 事件」；400ms 静默窗口仅用于结束判定，不计入。");

            // 结束判定：设 Person 后等 Rows 事件出现且连续 400ms 无新事件（总超时 30s）；净耗时只取到末事件。
            (bool Ok, long NetMs, int Builds, int Passes) SwitchTo(
                LawFirm.UI.ViewModels.PersonalSettlementViewModel.PersonOption target)
            {
                probe.Reset();
                LawFirm.UI.Services.SettlementQueryService.BuildCallCount = 0;
                long t0 = System.Diagnostics.Stopwatch.GetTimestamp();   // 计时起点（高精度）
                vm.Person = target;   // 触发 OnPersonChanged → RefreshAsync（后台接续）
                var sw = System.Diagnostics.Stopwatch.StartNew();
                while (sw.ElapsedMilliseconds < 30_000)
                {
                    if (probe.RowsCount >= 1 && probe.MillisSinceLastEvent >= 400)
                        break;
                    Thread.Sleep(10);
                }
                bool ok = sw.ElapsedMilliseconds < 30_000;
                double netMsD = SwitchProbe.TicksToMs(probe.LastEventTimestamp - t0);   // 计时终点：最后一次事件时刻
                long netMs = netMsD < 0 ? 0 : (long)Math.Round(netMsD);
                return (ok, netMs, LawFirm.UI.Services.SettlementQueryService.BuildCallCount, probe.RowsCount);
            }

            var warm = SwitchTo(pa);
            Console.WriteLine($"[INFO] 预热切至 {pa.Name}：净耗时 {warm.NetMs} ms，Build {warm.Builds}，刷新 {warm.Passes} 遍");

            var msList = new List<long>();
            var buildList = new List<int>();
            var passList = new List<int>();
            bool allOk = true;
            for (int k = 0; k < 10; k++)
            {
                var target = (k % 2 == 0) ? pb : pa;
                var r = SwitchTo(target);
                if (!r.Ok) allOk = false;
                msList.Add(r.NetMs);
                buildList.Add(r.Builds);
                passList.Add(r.Passes);
                Console.WriteLine($"  第 {k + 1,2} 次 → {target.Name,-8}：净耗时 {r.NetMs,5} ms   Build {r.Builds}   刷新 {r.Passes} 遍");
            }

            long medMs = Median(msList);
            int medBuild = (int)Median(buildList.Select(x => (long)x).ToList());
            int medPass = (int)Median(passList.Select(x => (long)x).ToList());

            Console.WriteLine($"[RESULT] 中位净耗时 = {medMs} ms（不含 400ms 静默窗口），中位 Build = {medBuild}，中位刷新 = {medPass} 遍");
            Console.WriteLine($"[RESULT] 净耗时分布 = [{string.Join(",", msList)}] ms");
            Console.WriteLine($"[RESULT] Build 次数分布 = [{string.Join(",", buildList)}]");
            Console.WriteLine($"[RESULT] 刷新遍数分布 = [{string.Join(",", passList)}]");
            Console.WriteLine(medPass == 1 && medBuild == 4
                ? "[OK] 与修复后预期一致（月度表 1 遍刷新 / 4 次 Build）"
                : medPass == 2 && medBuild == 8
                    ? "[WARN] 与修复前基线一致（月度表 2 遍刷新 / 8 次 Build）——可能未应用修复"
                    : "[INFO] 数值不在 1遍/4Build 与 2遍/8Build 两种预期内，请人工核对");

            return allOk ? 0 : 1;
        }
        finally
        {
            vm.PropertyChanged -= probe.OnPropertyChanged;
        }
    }

    /// <summary>
    /// 诊断：写入通道自测（--diag-write-selftest，Batch 1「A + 三保险」验收）。
    ///
    /// **绝不触碰真实 data/lawfirm.db**：先把真实库连同 -wal/-shm 复制到 %TEMP%，
    /// 所有写入只发生在临时副本上，跑完连同临时备份目录一起删除。
    /// 逐条打印 PASS/FAIL，全部通过才返回 0。
    /// </summary>
    private static int RunWriteSelfTest()
    {
        var results = new List<(string Name, bool Ok, string Detail)>();
        void Check(string name, bool ok, string detail = "") => results.Add((name, ok, detail));

        var res = DbConnection.ResolveDatabase();
        if (!res.Found)
        {
            Console.Error.WriteLine("[FAIL] 未找到可用的 lawfirm.db，查找过程：");
            foreach (string t in res.Tried) Console.Error.WriteLine("  " + t);
            return 1;
        }

        string realDb = res.Path!;
        DateTime realBefore = new FileInfo(realDb).LastWriteTime;
        Console.WriteLine("[INFO] 写入通道自测（--diag-write-selftest）");
        Console.WriteLine($"[INFO] 真实库 = {realDb}");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试前）= {realBefore:yyyy-MM-dd HH:mm:ss.fff}");

        string tempDir = Path.Combine(Path.GetTempPath(),
            "lawfirm-selftest-" + DateTime.Now.ToString("yyyyMMddHHmmss"));
        string copyDb = Path.Combine(tempDir, "lawfirm.db");

        try
        {
            Directory.CreateDirectory(tempDir);
            File.Copy(realDb, copyDb, overwrite: true);
            if (File.Exists(realDb + "-wal")) File.Copy(realDb + "-wal", copyDb + "-wal", overwrite: true);
            if (File.Exists(realDb + "-shm")) File.Copy(realDb + "-shm", copyDb + "-shm", overwrite: true);
            Console.WriteLine($"[INFO] 临时副本 = {copyDb}（-wal/-shm 若存在已一并复制）");

            string backupDir = WriteGuard.BackupDirFor(copyDb);
            Console.WriteLine($"[INFO] 副本备份目录 = {backupDir}");

            // ========== 断言 1 & 2：一次写入 → staff 探针行 + change_log 恰好 +1 ==========
            long changeBefore = CountRows(copyDb, "SELECT COUNT(*) FROM change_log;");
            long probeId = WriteGuard.Execute<long>(copyDb, "写通道自测-新增员工", (conn, tx) =>
            {
                using (var ins = conn.CreateCommand())
                {
                    ins.Transaction = tx;
                    ins.CommandText =
                        "INSERT INTO staff (name, staff_type, is_active, note, source) " +
                        "VALUES ($n, $t, 1, '', 'selftest');";
                    ins.Parameters.AddWithValue("$n", "__selftest__");
                    ins.Parameters.AddWithValue("$t", "聘用");
                    ins.ExecuteNonQuery();
                }
                long id;
                using (var q = conn.CreateCommand())
                {
                    q.Transaction = tx;
                    q.CommandText = "SELECT last_insert_rowid();";
                    id = Convert.ToInt64(q.ExecuteScalar());
                }

                // 同一事务内追加一条修改记录，与上面的 INSERT 一起提交（验证 ChangeLog 端口）。
                ChangeLog.Log(conn, "staff", id.ToString(), "create", null, "__selftest__",
                    note: "写通道自测", friendlyTable: "员工");
                return id;
            });

            long staffRows = CountRows(copyDb, "SELECT COUNT(*) FROM staff WHERE name = '__selftest__';");
            Check("1. staff 探针行已提交", staffRows == 1, $"name='__selftest__' 命中 {staffRows} 行（id={probeId}）");

            long changeAfter = CountRows(copyDb, "SELECT COUNT(*) FROM change_log;");
            long delta = changeAfter - changeBefore;
            string[] logRow = ReadRow(copyDb,
                "SELECT table_name, field, old_value, new_value FROM change_log ORDER BY id DESC LIMIT 1;");
            bool logOk = delta == 1
                && logRow.Length == 4
                && logRow[0] == "staff"
                && logRow[1] == "create"
                && logRow[2] == ""
                && logRow[3] == "__selftest__";
            Check("2. change_log 恰好 +1 行且内容正确", logOk,
                $"delta={delta}, table_name={AtStr(logRow, 0)}, field={AtStr(logRow, 1)}, " +
                $"old_value='{AtStr(logRow, 2)}', new_value='{AtStr(logRow, 3)}'");

            // ========== 断言 3：备份恰好 1 个且为真 SQLite 文件 ==========
            string[] backups = FileList(backupDir);
            int backupCount = WriteGuard.BackupCount(copyDb);
            bool headerOk = backups.Length == 1 && HasSqliteHeader(backups[0]);
            Check("3. 备份目录恰好 1 个 lawfirm-*.db 且为真 SQLite 文件", backupCount == 1 && headerOk,
                $"BackupCount={backupCount}, 实际文件={backups.Length}, SQLite 头={(backups.Length == 1 ? HasSqliteHeader(backups[0]).ToString() : "n/a")}");

            // ========== 断言 4：滚动保留 = 10（删最旧、留最新） ==========
            // 已产生 1 个备份；再写 15 次 → 共 16 个 → 应只保留最新 10 个。
            var createdOrder = new List<string>();
            if (backups.Length == 1) createdOrder.Add(backups[0]);
            for (int k = 0; k < 15; k++)
            {
                int kk = k;
                var before = new HashSet<string>(FileList(backupDir), StringComparer.OrdinalIgnoreCase);
                WriteGuard.Execute(copyDb, $"写通道自测-保留策略 {kk + 1}", (conn, tx) =>
                {
                    using var cmd = conn.CreateCommand();
                    cmd.Transaction = tx;
                    cmd.CommandText = "UPDATE staff SET note = $n WHERE name = '__selftest__';";
                    cmd.Parameters.AddWithValue("$n", "keep-" + kk);
                    cmd.ExecuteNonQuery();
                });
                foreach (string f in FileList(backupDir))
                    if (!before.Contains(f)) createdOrder.Add(f);
            }

            string[] survivors = FileList(backupDir);
            int totalCreated = createdOrder.Count;
            var expectedKept = new HashSet<string>(
                createdOrder.Skip(Math.Max(0, totalCreated - WriteGuard.RetentionCount)),
                StringComparer.OrdinalIgnoreCase);
            var survivorSet = new HashSet<string>(survivors, StringComparer.OrdinalIgnoreCase);
            bool retentionOk = survivors.Length == WriteGuard.RetentionCount && survivorSet.SetEquals(expectedKept);
            Check($"4. 滚动保留 = {WriteGuard.RetentionCount}（删最旧、留最新）", retentionOk,
                $"共创建 {totalCreated} 个，现存 {survivors.Length} 个，" +
                (retentionOk ? "保留集合 == 最新 10 个" : "保留集合 != 最新 10 个"));

            // ========== 断言 5：忙错误翻译 ==========
            // 真实锁会与 busy_timeout(5s) 纠缠且受平台影响，故**直接**构造 SqliteException(...,5/6)
            // 走与 Execute 完全相同的 TranslateBusy 分支来验证（明确说明：非真实锁）。
            var fakeBusy = new SqliteException("database is locked", 5);
            var fakeLocked = new SqliteException("database table is locked", 6);
            var translated5 = WriteGuard.TranslateBusy(fakeBusy, "写通道自测");
            var translated6 = WriteGuard.TranslateBusy(fakeLocked, "写通道自测");
            var untouched = WriteGuard.TranslateBusy(new InvalidOperationException("无关错误"), "写通道自测");
            bool busyOk =
                translated5 is DbBusyException b5 && b5.Message == WriteGuard.BusyMessage && ReferenceEquals(b5.InnerException, fakeBusy) &&
                translated6 is DbBusyException b6 && b6.Message == WriteGuard.BusyMessage && ReferenceEquals(b6.InnerException, fakeLocked) &&
                untouched is InvalidOperationException;
            Check("5. 忙错误翻译为 DbBusyException（直接构造 SqliteException(...,5/6) 走翻译分支）", busyOk,
                $"code5->{translated5.GetType().Name}, code6->{translated6.GetType().Name}, " +
                $"文案一致={(translated5 is DbBusyException m && m.Message == WriteGuard.BusyMessage)}");

            // ========== 断言 6：真实写锁 → DbBusyException，且等待 ≈ DefaultTimeout(5s)，解锁后可写 ==========
            // 复刻 verifier 的 T3（原手工构造异常的测法覆盖不到此场景）：
            // WAL 下必须用裸 SQL 的 BEGIN EXCLUSIVE —— 默认 BeginTransaction 是 deferred、不加锁，测不出来。
            // 锁连接全程保持打开；另起线程跑 WriteGuard.Execute，测量到异常的真实耗时。
            // 下界 4s 证明「确实按 ~5s 在等」（而非立刻失败）；上界 12s 远低于修复前的 ~34s，
            // 故一旦 DefaultTimeout 被回退，本断言必然抓得住。
            var lockerBuilder = new SqliteConnectionStringBuilder
            {
                DataSource = copyDb,
                Mode = SqliteOpenMode.ReadWrite,
                Pooling = false,
            };
            DbBusyException? lockedBusy = null;
            long lockedMs = -1;
            using (var locker = new SqliteConnection(lockerBuilder.ConnectionString))
            {
                locker.Open();
                using (var begin = locker.CreateCommand())
                {
                    begin.CommandText = "BEGIN EXCLUSIVE;";
                    begin.ExecuteNonQuery();
                }

                var worker = new Thread(() =>
                {
                    var sw = System.Diagnostics.Stopwatch.StartNew();
                    try
                    {
                        WriteGuard.Execute(copyDb, "写通道自测-真实锁", (conn, tx) =>
                        {
                            using var cmd = conn.CreateCommand();
                            cmd.Transaction = tx;
                            cmd.CommandText = "UPDATE staff SET note = 'locked' WHERE name = '__selftest__';";
                            cmd.ExecuteNonQuery();
                        });
                    }
                    catch (DbBusyException ex)
                    {
                        lockedBusy = ex;
                    }
                    catch
                    {
                        // 非 DbBusyException：保持 lockedBusy=null，下方断言自然失败
                    }
                    finally
                    {
                        lockedMs = sw.ElapsedMilliseconds;
                    }
                });
                worker.IsBackground = true;
                worker.Start();
                worker.Join(TimeSpan.FromSeconds(30));

                // 释放写锁
                using (var rollback = locker.CreateCommand())
                {
                    rollback.CommandText = "ROLLBACK;";
                    rollback.ExecuteNonQuery();
                }
            }

            // 解锁后再写一次，必须成功
            bool postUnlockOk = false;
            try
            {
                WriteGuard.Execute(copyDb, "写通道自测-解锁后写入", (conn, tx) =>
                {
                    using var cmd = conn.CreateCommand();
                    cmd.Transaction = tx;
                    cmd.CommandText = "UPDATE staff SET note = 'unlocked' WHERE name = '__selftest__';";
                    cmd.ExecuteNonQuery();
                });
                postUnlockOk = CountRows(copyDb,
                    "SELECT COUNT(*) FROM staff WHERE name = '__selftest__' AND note = 'unlocked';") == 1;
            }
            catch
            {
                postUnlockOk = false;
            }

            bool realLockOk = lockedBusy is not null
                && lockedBusy.Message == WriteGuard.BusyMessage
                && lockedMs >= 4000 && lockedMs <= 12000
                && postUnlockOk;
            Check("6. 真实 EXCLUSIVE 锁 → DbBusyException，等待≈5s（4–12s），解锁后可写", realLockOk,
                $"耗时={lockedMs} ms, 类型={(lockedBusy?.GetType().Name ?? "(未捕获)")}, " +
                $"文案一致={(lockedBusy?.Message == WriteGuard.BusyMessage)}, 解锁后可写={postUnlockOk}");
        }
        catch (Exception ex)
        {
            Check("! 自测过程异常", false, $"{ex.GetType().Name}: {ex.Message}");
            Console.Error.WriteLine(ex.ToString());
        }
        finally
        {
            // 默认连接池会留驻已 Dispose 的只读连接（文件句柄不释放），先清空连接池再删临时目录。
            try { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); } catch { /* 忽略 */ }
            try { if (Directory.Exists(tempDir)) Directory.Delete(tempDir, recursive: true); }
            catch { /* 临时目录清理失败不影响结论 */ }
        }

        Console.WriteLine();
        foreach (var r in results)
            Console.WriteLine($"  [{(r.Ok ? "PASS" : "FAIL")}] {r.Name}"
                + (string.IsNullOrEmpty(r.Detail) ? "" : $"  —— {r.Detail}"));

        bool allPass = results.Count > 0 && results.TrueForAll(r => r.Ok);
        Console.WriteLine();
        Console.WriteLine($"[{(allPass ? "OK" : "FAIL")}] 写入通道自测：{results.FindAll(r => r.Ok).Count}/{results.Count} 项通过");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试后）= {new FileInfo(realDb).LastWriteTime:yyyy-MM-dd HH:mm:ss.fff}（应与测试前一致）");
        return allPass ? 0 : 1;
    }

    /// <summary>
    /// 诊断：基础数据引擎自测（--diag-base-data，Batch 2a「员工类型 / 员工 CRUD」验收）。
    ///
    /// <b>绝不触碰真实 data/lawfirm.db</b>：先把真实库连同 -wal/-shm 复制到 %TEMP%，
    /// 所有写入只发生在临时副本上（走 <see cref="WriteGuard"/> 写闸门），跑完连同临时备份目录一起删除。
    /// 逐条打印 PASS/FAIL，全部通过才返回 0。
    /// </summary>
    private static int RunDiagBaseData()
    {
        var results = new List<(string Name, bool Ok, string Detail)>();
        void Check(string name, bool ok, string detail = "") => results.Add((name, ok, detail));

        var res = DbConnection.ResolveDatabase();
        if (!res.Found)
        {
            Console.Error.WriteLine("[FAIL] 未找到可用的 lawfirm.db，查找过程：");
            foreach (string t in res.Tried) Console.Error.WriteLine("  " + t);
            return 1;
        }

        string realDb = res.Path!;
        DateTime realBefore = new FileInfo(realDb).LastWriteTime;
        DateTime? realWalBefore = File.Exists(realDb + "-wal") ? new FileInfo(realDb + "-wal").LastWriteTime : null;
        DateTime? realShmBefore = File.Exists(realDb + "-shm") ? new FileInfo(realDb + "-shm").LastWriteTime : null;
        string realBackupDir = WriteGuard.BackupDirFor(realDb);
        bool realBackupDirBefore = Directory.Exists(realBackupDir);
        int realBackupCountBefore = WriteGuard.BackupCount(realDb);

        Console.WriteLine("[INFO] 基础数据引擎自测（--diag-base-data）");
        Console.WriteLine($"[INFO] 真实库 = {realDb}");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试前）= {realBefore:yyyy-MM-dd HH:mm:ss.fff}");
        Console.WriteLine($"[INFO] 真实备份目录 = {realBackupDir}（存在={realBackupDirBefore}，现有 {realBackupCountBefore} 份）");

        string tempDir = Path.Combine(Path.GetTempPath(),
            "lawfirm-basedata-" + DateTime.Now.ToString("yyyyMMddHHmmss"));
        string copyDb = Path.Combine(tempDir, "lawfirm.db");
        string backupDir = WriteGuard.BackupDirFor(copyDb);

        int gateCalls = 0;       // 用户变更（含校验失败）经过写闸门的次数
        int backupsCreated = 0;  // 观测到的新增备份文件数

        try
        {
            Directory.CreateDirectory(tempDir);
            File.Copy(realDb, copyDb, overwrite: true);
            if (File.Exists(realDb + "-wal")) File.Copy(realDb + "-wal", copyDb + "-wal", overwrite: true);
            if (File.Exists(realDb + "-shm")) File.Copy(realDb + "-shm", copyDb + "-shm", overwrite: true);
            Console.WriteLine($"[INFO] 临时副本 = {copyDb}（-wal/-shm 若存在已一并复制）");
            Console.WriteLine($"[INFO] 副本备份目录 = {backupDir}");

            // 每次经闸门的变更都统计「新建了几个备份文件」：滚动保留会删除最旧的，
            // 故不能只看总数，必须用「调用前后文件集差集」观测（最新的那份永不被删）。
            Exception? TryGate(string reason, Action<SqliteConnection, SqliteTransaction> work)
            {
                var before = new HashSet<string>(FileList(backupDir), StringComparer.OrdinalIgnoreCase);
                gateCalls++;
                try
                {
                    WriteGuard.Execute(copyDb, reason, work);
                }
                catch (Exception ex)
                {
                    backupsCreated += CountNewBackups(backupDir, before);
                    return ex;
                }
                backupsCreated += CountNewBackups(backupDir, before);
                return null;
            }

            // ---- 断言 0：引导补齐（幂等维护写；不计入用户变更基线） ----
            Exception? boot = TryGate("初始化员工类型（幂等补齐）",
                (c, t) => StaffTypeService.EnsureDefaults(c));
            Check("0. 引导 EnsureDefaults 幂等执行成功", boot is null, boot is null ? "" : boot.Message);
            int backupBaseline = WriteGuard.BackupCount(copyDb);
            gateCalls = 0; backupsCreated = 0;   // 重置：其后只统计「用户变更」

            // ---- 断言 1：内置类型 ----
            List<StaffTypeRow> types;
            using (var ro = DbConnection.OpenReadOnly(copyDb)) types = StaffTypeService.ListTypes(ro);
            bool builtinOk = Array.TrueForAll(StaffTypeService.BuiltinTypes, bn =>
            {
                var t = types.FirstOrDefault(x => x.Name == bn);
                return t is not null && t.IsBuiltin && StaffTypeService.IsComputable(bn);
            });
            bool extraOk = Array.TrueForAll(StaffTypeService.DefaultExtra, en =>
            {
                var t = types.FirstOrDefault(x => x.Name == en);
                return t is not null && !t.IsBuiltin && !StaffTypeService.IsComputable(en);
            });
            Check("1. 内置三类 is_builtin=1 且参与计算；挂靠/其他 is_builtin=0 且不参与",
                builtinOk && extraOk,
                "类型=" + string.Join("、", types.Select(t =>
                    $"{t.Name}(builtin={(t.IsBuiltin ? 1 : 0)},calc={(StaffTypeService.IsComputable(t.Name) ? 1 : 0)},n={t.StaffCount})")));

            // ---- 断言 2：IsComputable 真值表 ----
            var truth = new (string? In, bool Exp)[]
            {
                ("合伙", true), ("聘用", true), ("兼职", true),
                ("挂靠", false), ("其他", false), ("顾问", false),
                ("合伙人助理", true), ("", false), (null, false), (" 聘用 ", true),
            };
            var truthBad = truth.Where(x => StaffTypeService.IsComputable(x.In) != x.Exp).ToList();
            Check("2. IsComputable 真值表（含「合伙人助理」→true、null/空白→false、去空白）",
                truthBad.Count == 0,
                string.Join("；", truth.Select(x =>
                    $"{(x.In is null ? "null" : $"「{x.In}」")}→{(StaffTypeService.IsComputable(x.In) ? "true" : "false")}")));

            // ---- 断言 3：AddType 成功 / 重复 / 超长 ----
            Exception? ex3add = TryGate("新增员工类型", (c, t) => StaffTypeService.AddType(c, "顾问", ""));
            using (var ro = DbConnection.OpenReadOnly(copyDb)) types = StaffTypeService.ListTypes(ro);
            bool visible3 = types.Any(x => x.Name == "顾问");
            Exception? ex3dup = TryGate("新增员工类型", (c, t) => StaffTypeService.AddType(c, "顾问", ""));
            Exception? ex3len = TryGate("新增员工类型", (c, t) => StaffTypeService.AddType(c, new string('测', 21), ""));
            string dupMsg = ex3dup?.Message ?? "";
            string lenMsg = ex3len?.Message ?? "";
            Check("3. AddType 成功可见 / 重复报「类型已存在：顾问」/ 21 字报「类型名过长（≤20 字符）」",
                ex3add is null && visible3
                    && ex3dup is StaffTypeException && dupMsg == "类型已存在：顾问"
                    && ex3len is StaffTypeException && lenMsg == "类型名过长（≤20 字符）",
                $"add={(ex3add?.Message ?? "ok")},visible={visible3},dup='{dupMsg}',len='{lenMsg}'");

            // ---- 断言 4：RenameType 同步更新 staff_type_def 与 staff.staff_type ----
            bool ins4 = false;
            Exception? ex4ins = TryGate("新增员工",
                (c, t) => ins4 = StaffService.AddManual(c, "__basedata_probe__", "顾问", "", ""));
            Exception? ex4ren = TryGate("改名员工类型",
                (c, t) => StaffTypeService.RenameType(c, "顾问", "高级顾问"));
            bool defFollowed4, staffFollowed4;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            {
                defFollowed4 = StaffTypeService.GetType(ro, "高级顾问") is not null
                               && StaffTypeService.GetType(ro, "顾问") is null;
                var probe = StaffService.ListStaff(ro).FirstOrDefault(s => s.Name == "__basedata_probe__");
                staffFollowed4 = probe is not null && probe.StaffType == "高级顾问";
            }
            Check("4. RenameType 同步更新 staff_type_def 与 staff.staff_type（自定义类型跟随改名）",
                ex4ins is null && ins4 && ex4ren is null && defFollowed4 && staffFollowed4,
                $"insert={ins4},rename={(ex4ren?.Message ?? "ok")},defFollowed={defFollowed4},staffFollowed={staffFollowed4}");

            // ---- 断言 5：内置类型禁止改名 / 删除 ----
            Exception? ex5ren = TryGate("改名员工类型", (c, t) => StaffTypeService.RenameType(c, "合伙", "合伙X"));
            Exception? ex5del = TryGate("删除员工类型", (c, t) => StaffTypeService.DeleteType(c, "合伙"));
            Check("5. 内置类型禁止改名（断结算口径）/ 禁止删除",
                ex5ren is StaffTypeException
                    && ex5ren.Message == "「合伙」是内置结算类型，禁止改名（改名会断结算口径）"
                    && ex5del is StaffTypeException
                    && ex5del.Message == "「合伙」是内置结算类型，禁止删除",
                $"rename='{ex5ren?.Message}',delete='{ex5del?.Message}'");

            // ---- 断言 6：在用类型禁止删除（精确文案）/ 未用类型删除成功 ----
            const string expect6 = "还有 1 名员工属于该类型，请先把他们改成别的类型再删除";
            Exception? ex6use = TryGate("删除员工类型", (c, t) => StaffTypeService.DeleteType(c, "高级顾问"));
            Exception? ex6upd = TryGate("修改员工",
                (c, t) => StaffService.Update(c, "__basedata_probe__", "聘用", "", ""));
            Exception? ex6del = TryGate("删除员工类型", (c, t) => StaffTypeService.DeleteType(c, "高级顾问"));
            bool gone6;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
                gone6 = StaffTypeService.GetType(ro, "高级顾问") is null;
            Check("6. 在用类型禁止删除（精确「还有 1 名员工…」文案）/ 未用类型删除成功",
                ex6use is StaffTypeException && ex6use.Message == expect6
                    && ex6upd is null && ex6del is null && gone6,
                $"inUse='{ex6use?.Message}',update={(ex6upd?.Message ?? "ok")},delUnused={(ex6del?.Message ?? "ok")},gone={gone6}");

            // ---- 断言 7：MoveType 上移生效且 sort_order 重写为连续 1..N ----
            List<string> orderBefore;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
                orderBefore = StaffTypeService.ListTypes(ro).Select(x => x.Name).ToList();
            bool moved7 = false;
            string moveDetail7 = $"类型过少（{orderBefore.Count}），跳过";
            if (orderBefore.Count >= 2)
            {
                string moveTarget = orderBefore[1];
                Exception? ex7 = TryGate("调整类型顺序", (c, t) => StaffTypeService.MoveType(c, moveTarget, -1));
                List<StaffTypeRow> after7;
                using (var ro = DbConnection.OpenReadOnly(copyDb)) after7 = StaffTypeService.ListTypes(ro);
                List<string> namesAfter = after7.Select(x => x.Name).ToList();
                bool contiguous = after7.Select((x, i) => x.SortOrder == i + 1).All(b => b);
                var expected = new List<string>(orderBefore);
                (expected[1], expected[0]) = (expected[0], expected[1]);
                moved7 = ex7 is null && contiguous && namesAfter.SequenceEqual(expected);
                moveDetail7 = $"before=[{string.Join(",", orderBefore)}], after=[{string.Join(",", namesAfter)}], " +
                    $"sort=[{string.Join(",", after7.Select(x => x.SortOrder))}], contiguous={contiguous}";
            }
            Check("7. MoveType 上移生效且 sort_order 重写为连续 1..N", moved7, moveDetail7);

            // ---- 断言 8：ListTypes / ListStaff 为纯读（备份数不变） ----
            int bk8a = WriteGuard.BackupCount(copyDb);
            using (var ro = DbConnection.OpenReadOnly(copyDb)) _ = StaffTypeService.ListTypes(ro);
            using (var ro = DbConnection.OpenReadOnly(copyDb)) _ = StaffService.ListStaff(ro);
            int bk8b = WriteGuard.BackupCount(copyDb);
            Check("8. ListTypes / ListStaff 为纯读（不触发备份，备份数不变）", bk8b == bk8a, $"{bk8a} -> {bk8b}");

            // ---- 断言 9：引用计数抽样 + 删除行为 ----
            List<string> staffNames;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
                staffNames = StaffService.ListStaff(ro).Select(s => s.Name).ToList();
            var samples9 = new List<string>();
            string? zeroRef9 = null, inUse9 = null;
            StaffReferenceCount? inUseRefs9 = null;
            foreach (string nm in staffNames)
            {
                StaffReferenceCount r;
                using (var ro = DbConnection.OpenReadOnly(copyDb)) r = StaffTypeService.StaffReferenceCount(ro, nm);
                if (samples9.Count < 8)
                    samples9.Add($"{nm}[cd{r.ChargeDetail},el{r.ExpenseLedger},rs{r.RawSalary},col{r.Collection},tot{r.Total}]");
                if (r.Total == 0 && zeroRef9 is null) zeroRef9 = nm;
                if (r.Total > 0 && inUse9 is null) { inUse9 = nm; inUseRefs9 = r; }
            }
            Check("9a. StaffReferenceCount 抽样（真实引用条数）", samples9.Count > 0, string.Join("；", samples9));

            bool delZeroOk = false;
            string delZeroDetail = "无 0 引用员工可测";
            if (zeroRef9 is not null)
            {
                Exception? exd = TryGate("删除员工", (c, t) => StaffService.Delete(c, zeroRef9));
                bool goneZ;
                using (var ro = DbConnection.OpenReadOnly(copyDb))
                    goneZ = StaffService.ListStaff(ro).All(s => s.Name != zeroRef9);
                delZeroOk = exd is null && goneZ;
                delZeroDetail = $"删除「{zeroRef9}」-> {(exd is null ? "成功" : exd.Message)}，已消失={goneZ}";
            }

            bool delUseOk = false;
            string delUseDetail = "无有引用员工可测";
            if (inUse9 is not null && inUseRefs9 is not null)
            {
                string detail = string.Join("、",
                    inUseRefs9.NonZeroInOrder().Select(x => $"{x.Table} {x.Count} 条"));
                string expect9 =
                    $"「{inUse9}」在业务数据中有引用（{detail}）。\n"
                    + "直接删除会让结算表查不到其身份、业务收入按 0 计。\n"
                    + "如该员工已离职，请改用「停用 / 启用」。";
                Exception? exu = TryGate("删除员工", (c, t) => StaffService.Delete(c, inUse9));
                bool stillThere;
                using (var ro = DbConnection.OpenReadOnly(copyDb))
                    stillThere = StaffService.ListStaff(ro).Any(s => s.Name == inUse9);
                delUseOk = exu is StaffInUseException && exu.Message == expect9 && stillThere;
                delUseDetail = $"「{inUse9}」refs=[{detail}] -> {(exu is null ? "未抛出(异常!)" : exu.GetType().Name)}，仍在册={stillThere}";
            }
            Check("9b. 0 引用员工可删 / 有引用员工抛 StaffInUseException（含分表明细）",
                delZeroOk && delUseOk, delZeroDetail + "；" + delUseDetail);

            // ---- 断言 10：备份新增 = 用户变更次数 ----
            int bkFinal = WriteGuard.BackupCount(copyDb);
            int expectedSurvive = Math.Min(backupBaseline + gateCalls, WriteGuard.RetentionCount);
            Check("10. 备份新增份数 = 用户变更经闸门次数（每次恰 1 份）",
                backupsCreated == gateCalls && bkFinal == expectedSurvive,
                $"用户变更经闸门 {gateCalls} 次，观测新建备份 {backupsCreated} 份；" +
                $"基线 {backupBaseline} → 现存 {bkFinal}（保留上限 {WriteGuard.RetentionCount}，预期 {expectedSurvive}）");

            // ---- 断言 11：真实库未被触碰 ----
            DateTime realAfter = new FileInfo(realDb).LastWriteTime;
            DateTime? realWalAfter = File.Exists(realDb + "-wal") ? new FileInfo(realDb + "-wal").LastWriteTime : null;
            DateTime? realShmAfter = File.Exists(realDb + "-shm") ? new FileInfo(realDb + "-shm").LastWriteTime : null;
            bool dbSame = realAfter == realBefore;
            bool walSame = Nullable.Equals(realWalAfter, realWalBefore);
            bool shmSame = Nullable.Equals(realShmAfter, realShmBefore);
            bool backupsUntouched = realBackupDirBefore
                ? WriteGuard.BackupCount(realDb) == realBackupCountBefore
                : !Directory.Exists(realBackupDir);
            Check("11. 真实库未被触碰（文件时间一致 / 未创建 data\\backups）",
                dbSame && walSame && shmSame && backupsUntouched,
                $"db {realBefore:HH:mm:ss.fff}->{realAfter:HH:mm:ss.fff}, wal同={walSame}, shm同={shmSame}, " +
                $"备份目录存在={Directory.Exists(realBackupDir)}");
        }
        catch (Exception ex)
        {
            Check("! 自测过程异常", false, $"{ex.GetType().Name}: {ex.Message}");
            Console.Error.WriteLine(ex.ToString());
        }
        finally
        {
            try { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); } catch { /* 忽略 */ }
            try { if (Directory.Exists(tempDir)) Directory.Delete(tempDir, recursive: true); }
            catch { /* 临时目录清理失败不影响结论 */ }
        }

        Console.WriteLine();
        foreach (var r in results)
            Console.WriteLine($"  [{(r.Ok ? "PASS" : "FAIL")}] {r.Name}"
                + (string.IsNullOrEmpty(r.Detail) ? "" : $"  —— {r.Detail}"));

        bool allPass = results.Count > 0 && results.TrueForAll(r => r.Ok);
        Console.WriteLine();
        Console.WriteLine($"[{(allPass ? "OK" : "FAIL")}] 基础数据引擎自测：{results.FindAll(r => r.Ok).Count}/{results.Count} 项通过");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试后）= {new FileInfo(realDb).LastWriteTime:yyyy-MM-dd HH:mm:ss.fff}（应与测试前一致）");
        return allPass ? 0 : 1;
    }

    /// <summary>把真实库（含 -wal/-shm 若存在）复制到临时目录的 copyDb，供诊断在副本上跑。</summary>
    private static void CopyDbToTemp(string realDb, string tempDir, string copyDb)
    {
        Directory.CreateDirectory(tempDir);
        File.Copy(realDb, copyDb, overwrite: true);
        if (File.Exists(realDb + "-wal")) File.Copy(realDb + "-wal", copyDb + "-wal", overwrite: true);
        if (File.Exists(realDb + "-shm")) File.Copy(realDb + "-shm", copyDb + "-shm", overwrite: true);
    }

    /// <summary>
    /// 诊断：费用类型引擎自测（--diag-expense-cat，Batch 2b）。
    /// 只在 %TEMP% 副本上写；最高优先级断言：<see cref="ExpenseCatService.OrderedTypes"/>
    /// 与 <see cref="ExpenseCatHelper.OrderedTypes"/> 逐元素相等。退出码 0=全通过。
    /// </summary>
    private static int RunDiagExpenseCat()
    {
        var results = new List<(string Name, bool Ok, string Detail)>();
        void Check(string name, bool ok, string detail = "") => results.Add((name, ok, detail));

        var res = DbConnection.ResolveDatabase();
        if (!res.Found)
        {
            Console.Error.WriteLine("[FAIL] 未找到可用的 lawfirm.db，查找过程：");
            foreach (string t in res.Tried) Console.Error.WriteLine("  " + t);
            return 1;
        }

        string realDb = res.Path!;
        DateTime realBefore = new FileInfo(realDb).LastWriteTime;
        DateTime? realWalBefore = File.Exists(realDb + "-wal") ? new FileInfo(realDb + "-wal").LastWriteTime : null;
        DateTime? realShmBefore = File.Exists(realDb + "-shm") ? new FileInfo(realDb + "-shm").LastWriteTime : null;
        string realBackupDir = WriteGuard.BackupDirFor(realDb);
        bool realBackupDirBefore = Directory.Exists(realBackupDir);
        int realBackupCountBefore = WriteGuard.BackupCount(realDb);

        Console.WriteLine("[INFO] 费用类型引擎自测（--diag-expense-cat）");
        Console.WriteLine($"[INFO] 真实库 = {realDb}");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试前）= {realBefore:yyyy-MM-dd HH:mm:ss.fff}");

        string tempDir = Path.Combine(Path.GetTempPath(),
            "lawfirm-expense-" + DateTime.Now.ToString("yyyyMMddHHmmss"));
        string copyDb = Path.Combine(tempDir, "lawfirm.db");
        string backupDir = WriteGuard.BackupDirFor(copyDb);

        int gateCalls = 0, backupsCreated = 0;

        try
        {
            CopyDbToTemp(realDb, tempDir, copyDb);
            Console.WriteLine($"[INFO] 临时副本 = {copyDb}");

            Exception? TryGate(string reason, Action<SqliteConnection, SqliteTransaction> work)
            {
                var before = new HashSet<string>(FileList(backupDir), StringComparer.OrdinalIgnoreCase);
                gateCalls++;
                try { WriteGuard.Execute(copyDb, reason, work); }
                catch (Exception ex) { backupsCreated += CountNewBackups(backupDir, before); return ex; }
                backupsCreated += CountNewBackups(backupDir, before);
                return null;
            }

            // 引导：补齐 5 分类（幂等维护写，不计入用户变更基线）
            Exception? boot = TryGate("初始化费用分类（幂等补齐）",
                (c, t) => ExpenseCatService.EnsureCategories(c));
            Check("0. 引导 EnsureCategories 幂等执行成功", boot is null, boot is null ? "" : boot.Message);

            // ---- 1. ListCategories ----
            List<ExpenseCategoryRow> cats;
            using (var ro = DbConnection.OpenReadOnly(copyDb)) cats = ExpenseCatService.ListCategories(ro);
            bool order1 = cats.Select(x => x.Name).SequenceEqual(ExpenseCatService.Categories);
            var countsReal = new Dictionary<string, int>();
            foreach (string c in ExpenseCatService.Categories) countsReal[c] = 0;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            using (var cmd = ro.CreateCommand())
            {
                cmd.CommandText = "SELECT category FROM expense_cat";
                using var r = cmd.ExecuteReader();
                while (r.Read())
                {
                    string raw = r.IsDBNull(0) ? "" : r.GetString(0);
                    countsReal[ExpenseCatService.NormCat(raw)]++;
                }
            }
            bool counts1 = cats.All(x => countsReal.TryGetValue(x.Name, out int cc) && cc == x.Count);
            Check("1. ListCategories 恰 5 类、顺序==固定分类、计数与库内一致",
                cats.Count == 5 && order1 && counts1,
                string.Join("；", cats.Select(x => $"{x.Name}={x.Count}")));

            // ---- 2. OrderedTypes 等价（最高优先级） ----
            List<string> svc2, helper2;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            {
                svc2 = ExpenseCatService.OrderedTypes(ro);
                helper2 = ExpenseCatHelper.OrderedTypes(ro);
            }
            bool eq2 = svc2.SequenceEqual(helper2);
            Check("2. ExpenseCatService.OrderedTypes == ExpenseCatHelper.OrderedTypes（逐元素）", eq2,
                $"service({svc2.Count})=[{string.Join(",", svc2)}] || helper({helper2.Count})=[{string.Join(",", helper2)}]");

            // ---- 3. AddType 非法分类归一 + 重名 ----
            Exception? add3 = TryGate("新增费用类型", (c, t) => ExpenseCatService.AddType(c, "费用测试类型", "垃圾"));
            Exception? dup3 = TryGate("新增费用类型", (c, t) => ExpenseCatService.AddType(c, "费用测试类型", "其他"));
            bool norm3;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
                norm3 = ExpenseCatService.TypesByCategory(ro).TryGetValue("其他", out var l3) && l3.Contains("费用测试类型");
            Check("3. AddType 非法分类归一为「其他」/ 重名抛「费用类型已存在：费用测试类型」",
                add3 is null && norm3 && dup3 is ExpenseCatException && dup3.Message == "费用类型已存在：费用测试类型",
                $"add={(add3?.Message ?? "ok")},归一其他={norm3},dup='{dup3?.Message}'");

            // ---- 4. RenameType 同步 expense_cat + expense_ledger ----
            Exception? ledger4 = TryGate("新增费用台账探针", (c, t) =>
            {
                using var cmd = c.CreateCommand();
                cmd.Transaction = t;
                cmd.CommandText = "INSERT INTO expense_ledger (period, expense_type) VALUES ('0000', '费用测试类型')";
                cmd.ExecuteNonQuery();
            });
            Exception? ren4 = TryGate("改名费用类型",
                (c, t) => ExpenseCatService.RenameType(c, "费用测试类型", "费用测试类型2"));
            bool cat4, led4;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            {
                Dictionary<string, string> m4 = ExpenseCatService.GetMap(ro);
                cat4 = m4.ContainsKey("费用测试类型2") && !m4.ContainsKey("费用测试类型");
                led4 = ExpenseCatService.TypeReferenceCount(ro, "费用测试类型2") >= 1
                       && ExpenseCatService.TypeReferenceCount(ro, "费用测试类型") == 0;
            }
            Check("4. RenameType 同时改 expense_cat 与 expense_ledger.expense_type",
                ledger4 is null && ren4 is null && cat4 && led4,
                $"cat同步={cat4},ledger同步={led4}");

            // ---- 5. DeleteType 只删配置 ----
            int ledBefore5;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
                ledBefore5 = ExpenseCatService.TypeReferenceCount(ro, "费用测试类型2");
            Exception? del5 = TryGate("删除费用类型", (c, t) => ExpenseCatService.DeleteType(c, "费用测试类型2"));
            bool goneCfg5; int ledAfter5;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            {
                goneCfg5 = !ExpenseCatService.GetMap(ro).ContainsKey("费用测试类型2");
                ledAfter5 = ExpenseCatService.TypeReferenceCount(ro, "费用测试类型2");
            }
            Check("5. DeleteType 仅删配置，历史台账行保留",
                del5 is null && goneCfg5 && ledAfter5 == ledBefore5 && ledAfter5 >= 1,
                $"配置已删={goneCfg5},台账 前={ledBefore5} 后={ledAfter5}");

            // ---- 6. 脏数据归类 ----
            Exception? dirty6 = TryGate("新增脏分类探针", (c, t) =>
            {
                using var cmd = c.CreateCommand();
                cmd.Transaction = t;
                cmd.CommandText = "INSERT INTO expense_cat (expense_type, category, sort_order) " +
                    "VALUES ('脏类型X', '垃圾', COALESCE((SELECT MAX(sort_order) FROM expense_cat),0)+1)";
                cmd.ExecuteNonQuery();
            });
            bool dirtied6;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
                dirtied6 = ExpenseCatService.TypesByCategory(ro).TryGetValue("其他", out var l6) && l6.Contains("脏类型X");
            Check("6. 脏分类（'垃圾'）归入兜底「其他」", dirty6 is null && dirtied6,
                $"入库={dirty6 is null},归其他={dirtied6}");

            // ---- 7. MoveInCategory / 边界 / SaveLayout ----
            bool move7 = false; string moveDetail7 = "";
            List<string> catCandidates;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
                catCandidates = ExpenseCatService.TypesByCategory(ro)
                    .Where(kv => kv.Value.Count >= 2).Select(kv => kv.Key).ToList();
            if (catCandidates.Count > 0)
            {
                string cat7 = catCandidates[0];
                List<string> before7, after7;
                using (var ro = DbConnection.OpenReadOnly(copyDb)) before7 = ExpenseCatService.GetByCategory(ro, cat7);
                string second7 = before7[1];
                Exception? mv = TryGate("类内上移费用类型", (c, t) => ExpenseCatService.MoveInCategory(c, second7, -1));
                using (var ro = DbConnection.OpenReadOnly(copyDb)) after7 = ExpenseCatService.GetByCategory(ro, cat7);
                var exp7 = new List<string>(before7);
                (exp7[0], exp7[1]) = (exp7[1], exp7[0]);
                move7 = mv is null && after7.SequenceEqual(exp7);
                moveDetail7 = $"[{cat7}] {string.Join(",", before7)} -> {string.Join(",", after7)}";
            }
            Exception? boundGate = TryGate("类内上移费用类型（边界）", (c, t) =>
            {
                var byCat = ExpenseCatService.TypesByCategory(c);
                string first = byCat.First(kv => kv.Value.Count >= 1).Value[0];
                if (ExpenseCatService.MoveInCategory(c, first, -1))
                    throw new InvalidOperationException("越界应返回 false");
                if (ExpenseCatService.MoveInCategory(c, "不存在的类型ZZZ", -1))
                    throw new InvalidOperationException("不存在应返回 false");
            });
            Exception? layout7 = TryGate("保存费用类型布局", (c, t) =>
            {
                var byCat = ExpenseCatService.TypesByCategory(c);
                ExpenseCatService.SaveLayout(c, byCat);
            });
            bool contig7; string contigDetail7;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            using (var cmd = ro.CreateCommand())
            {
                cmd.CommandText = "SELECT expense_type, category, sort_order FROM expense_cat ORDER BY sort_order";
                using var r = cmd.ExecuteReader();
                int expected = 1; bool ok = true; int lastCatIdx = -1;
                var parts = new List<string>();
                while (r.Read())
                {
                    string cat = r.IsDBNull(1) ? "" : r.GetString(1);
                    int so = r.GetInt32(2);
                    if (so != expected) ok = false;
                    expected++;
                    int ci = Array.IndexOf(ExpenseCatService.Categories, ExpenseCatService.NormCat(cat));
                    if (ci < lastCatIdx) ok = false;
                    lastCatIdx = ci;
                    if (parts.Count < 60) parts.Add($"{so}:{cat}");
                }
                contig7 = ok;
                contigDetail7 = string.Join(" ", parts);
            }
            Check("7. 类内上移生效 + 越界/不存在返回 false 不写；SaveLayout 后全局 sort_order 连续 1..N 且按分类顺序",
                move7 && boundGate is null && layout7 is null && contig7,
                $"上移={move7}({moveDetail7})；边界false={boundGate is null}；layout={(layout7?.Message ?? "ok")}；连续1..N={contig7} [{contigDetail7}]");

            // ---- 8. 读方法纯读 ----
            int b8a = WriteGuard.BackupCount(copyDb);
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            {
                _ = ExpenseCatService.ListCategories(ro);
                _ = ExpenseCatService.OrderedTypes(ro);
                _ = ExpenseCatService.TypesByCategory(ro);
            }
            int b8b = WriteGuard.BackupCount(copyDb);
            Check("8. 读方法纯读（ListCategories/OrderedTypes/TypesByCategory 不产生备份）",
                b8b == b8a, $"{b8a} -> {b8b}（用户变更经闸门 {gateCalls} 次，观测新建备份 {backupsCreated} 份）");

            // ---- 9. 真实库未触碰 ----
            DateTime realAfter = new FileInfo(realDb).LastWriteTime;
            DateTime? realWalAfter = File.Exists(realDb + "-wal") ? new FileInfo(realDb + "-wal").LastWriteTime : null;
            DateTime? realShmAfter = File.Exists(realDb + "-shm") ? new FileInfo(realDb + "-shm").LastWriteTime : null;
            bool dbSame = realAfter == realBefore;
            bool walSame = Nullable.Equals(realWalAfter, realWalBefore);
            bool shmSame = Nullable.Equals(realShmAfter, realShmBefore);
            bool backupsUntouched = realBackupDirBefore
                ? WriteGuard.BackupCount(realDb) == realBackupCountBefore
                : !Directory.Exists(realBackupDir);
            Check("9. 真实库未被触碰（文件时间一致 / 未创建 data\\backups）",
                dbSame && walSame && shmSame && backupsUntouched,
                $"db {realBefore:HH:mm:ss.fff}->{realAfter:HH:mm:ss.fff}, wal同={walSame}, shm同={shmSame}, 备份目录存在={Directory.Exists(realBackupDir)}");
        }
        catch (Exception ex)
        {
            Check("! 自测过程异常", false, $"{ex.GetType().Name}: {ex.Message}");
            Console.Error.WriteLine(ex.ToString());
        }
        finally
        {
            try { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); } catch { /* 忽略 */ }
            try { if (Directory.Exists(tempDir)) Directory.Delete(tempDir, recursive: true); }
            catch { /* 临时目录清理失败不影响结论 */ }
        }

        Console.WriteLine();
        foreach (var r in results)
            Console.WriteLine($"  [{(r.Ok ? "PASS" : "FAIL")}] {r.Name}"
                + (string.IsNullOrEmpty(r.Detail) ? "" : $"  —— {r.Detail}"));

        bool allPass = results.Count > 0 && results.TrueForAll(r => r.Ok);
        Console.WriteLine();
        Console.WriteLine($"[{(allPass ? "OK" : "FAIL")}] 费用类型引擎自测：{results.FindAll(r => r.Ok).Count}/{results.Count} 项通过");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试后）= {new FileInfo(realDb).LastWriteTime:yyyy-MM-dd HH:mm:ss.fff}（应与测试前一致）");
        return allPass ? 0 : 1;
    }

    /// <summary>用 NPOI 生成一份职工清单测试文件（xls=true 用 HSSF，否则 XSSF；.xlsm 也用 XSSF 写）。</summary>
    private static void WriteStaffWorkbook(string path, bool xls, List<object?[]> rows)
    {
        IWorkbook wb = xls ? new HSSFWorkbook() : new XSSFWorkbook();
        ISheet ws = wb.CreateSheet("职工");
        for (int ri = 0; ri < rows.Count; ri++)
        {
            IRow row = ws.CreateRow(ri);
            object?[] cells = rows[ri];
            for (int ci = 0; ci < cells.Length; ci++)
            {
                object? v = cells[ci];
                if (v is null) continue;
                ICell cell = row.CreateCell(ci);
                if (v is double dd) cell.SetCellValue(dd);
                else if (v is bool bb) cell.SetCellValue(bb);
                else cell.SetCellValue(v.ToString());
            }
        }
        using var fs = new FileStream(path, FileMode.Create, FileAccess.Write);
        wb.Write(fs);
    }

    /// <summary>
    /// 诊断：职工清单导入解析 + 导入写流程（--diag-staff-import，Batch 2b）。
    /// 现场用 NPOI 生成 .xlsx/.xls/.xlsm 三份临时文件解析比对；导入写流程跑在 %TEMP% 副本上。
    /// 退出码 0=全通过。
    /// </summary>
    private static int RunDiagStaffImport()
    {
        var results = new List<(string Name, bool Ok, string Detail)>();
        void Check(string name, bool ok, string detail = "") => results.Add((name, ok, detail));

        var res = DbConnection.ResolveDatabase();
        if (!res.Found)
        {
            Console.Error.WriteLine("[FAIL] 未找到可用的 lawfirm.db，查找过程：");
            foreach (string t in res.Tried) Console.Error.WriteLine("  " + t);
            return 1;
        }

        string realDb = res.Path!;
        DateTime realBefore = new FileInfo(realDb).LastWriteTime;
        DateTime? realWalBefore = File.Exists(realDb + "-wal") ? new FileInfo(realDb + "-wal").LastWriteTime : null;
        DateTime? realShmBefore = File.Exists(realDb + "-shm") ? new FileInfo(realDb + "-shm").LastWriteTime : null;
        string realBackupDir = WriteGuard.BackupDirFor(realDb);
        bool realBackupDirBefore = Directory.Exists(realBackupDir);
        int realBackupCountBefore = WriteGuard.BackupCount(realDb);

        Console.WriteLine("[INFO] 职工清单导入自测（--diag-staff-import）");
        Console.WriteLine($"[INFO] 真实库 = {realDb}");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试前）= {realBefore:yyyy-MM-dd HH:mm:ss.fff}");

        string tempDir = Path.Combine(Path.GetTempPath(),
            "lawfirm-staffimport-" + DateTime.Now.ToString("yyyyMMddHHmmss"));
        string copyDb = Path.Combine(tempDir, "lawfirm.db");
        string backupDir = WriteGuard.BackupDirFor(copyDb);

        int gateCalls = 0, backupsCreated = 0;

        try
        {
            CopyDbToTemp(realDb, tempDir, copyDb);
            Console.WriteLine($"[INFO] 临时副本 = {copyDb}");

            Exception? TryGate(string reason, Action<SqliteConnection, SqliteTransaction> work)
            {
                var before = new HashSet<string>(FileList(backupDir), StringComparer.OrdinalIgnoreCase);
                gateCalls++;
                try { WriteGuard.Execute(copyDb, reason, work); }
                catch (Exception ex) { backupsCreated += CountNewBackups(backupDir, before); return ex; }
                backupsCreated += CountNewBackups(backupDir, before);
                return null;
            }

            // 测试数据：说明行在最前（表头不在第 0 行）、一行空姓名（跳过）、一行空类型（导入兜底聘用）、
            // 一个数值备注（2025.0 → "2025"）、一个全新类型（导入应自动补入）。
            var dataRows = new List<object?[]>
            {
                new object?[] { "职工清单（模板说明行，非表头）" },
                new object?[] { "姓名", "类型", "备注" },
                new object?[] { "张三", "聘用", "A" },
                new object?[] { "", "兼职", "空姓名应被跳过" },
                new object?[] { "李四", "", "空类型应兜底聘用" },
                new object?[] { "王五", "汽油费", 2025.0 },
                new object?[] { "赵六", "合伙", "" },
                new object?[] { "孙七", "测试新类型", "新类型应补入" },
            };

            string xlsx = Path.Combine(tempDir, "职工清单.xlsx");
            string xls = Path.Combine(tempDir, "职工清单.xls");
            string xlsm = Path.Combine(tempDir, "职工清单.xlsm");
            WriteStaffWorkbook(xlsx, xls: false, dataRows);
            WriteStaffWorkbook(xls, xls: true, dataRows);
            WriteStaffWorkbook(xlsm, xls: false, dataRows);

            // ---- 1. 三种格式解析成功且结果一致 ----
            static string Signature(List<StaffImportRow> rows)
                => string.Join("|", rows.Select(r => $"{r.Name}/{r.StaffType}/{r.Note}"));
            var pXlsx = StaffImportParser.ParseStaffFile(xlsx);
            var pXls = StaffImportParser.ParseStaffFile(xls);
            var pXlsm = StaffImportParser.ParseStaffFile(xlsm);
            string sXlsx = Signature(pXlsx.Staff), sXls = Signature(pXls.Staff), sXlsm = Signature(pXlsm.Staff);
            bool skip1 = pXlsx.Staff.All(r => r.Name.Length > 0);
            bool emptyType1 = pXlsx.Staff.Any(r => r.Name == "李四" && r.StaffType == "");
            bool numeric1 = pXlsx.Staff.Any(r => r.Name == "王五" && r.Note == "2025");
            Check("1. .xlsx/.xls/.xlsm 均解析成功且结果一致；空姓名跳过；数值备注→整数文本",
                sXlsx == sXls && sXls == sXlsm && pXlsx.Staff.Count == 5 && skip1 && emptyType1 && numeric1,
                $"[{sXlsx}]（xls/xlsm 与 xlsx {(sXlsx == sXls && sXls == sXlsm ? "一致" : "不一致")}）");

            // ---- 2. 表头不在第 0 行仍能定位 ----
            Check("2. 表头行不在第 0 行仍能定位（说明行在前）",
                pXlsx.Staff.Count == 5 && sXlsx.Contains("张三/聘用/A"),
                $"解析 {pXlsx.Staff.Count} 行，首行={pXlsx.Staff.FirstOrDefault()?.Name}");

            // ---- 3. 错误路径 ----
            string noName = Path.Combine(tempDir, "无姓名列.xlsx");
            WriteStaffWorkbook(noName, false, new List<object?[]>
            {
                new object?[] { "名字", "类型", "备注" },
                new object?[] { "张三", "聘用", "A" },
            });
            string headerOnly = Path.Combine(tempDir, "仅表头.xlsx");
            WriteStaffWorkbook(headerOnly, false, new List<object?[]>
            {
                new object?[] { "姓名", "类型", "备注" },
            });
            string csv = Path.Combine(tempDir, "职工清单.csv");
            File.WriteAllText(csv, "姓名,类型\n张三,聘用\n", new System.Text.UTF8Encoding(false));
            string missing = Path.Combine(tempDir, "不存在.xlsx");

            string e1 = TryParseError(noName);
            string e2 = TryParseError(headerOnly);
            string e3 = TryParseError(csv);
            string e4 = TryParseError(missing);
            Check("3. 错误路径文案精确（无姓名列 / 仅表头 / .csv / 文件不存在）",
                e1 == "未找到表头（需包含'姓名'和'类型'列）"
                    && e2 == "文件中没有有效的职工数据"
                    && e3 == "不支持的文件格式: .csv（支持 .xls/.xlsx/.xlsm）"
                    && e4 == $"文件不存在: {missing}",
                $"noName='{e1}'; headerOnly='{e2}'; csv='{e3}'; missing='{e4}'");

            // ---- 4. file_hash == 独立 MD5 ----
            string expectHash = Convert.ToHexString(
                System.Security.Cryptography.MD5.HashData(File.ReadAllBytes(xlsx))).ToLowerInvariant();
            Check("4. file_hash == 独立计算的 MD5", pXlsx.FileHash == expectHash,
                $"parser={pXlsx.FileHash}, 独立={expectHash}");

            // ---- 5. 导入写流程 ----
            long batchBefore = CountRows(copyDb, "SELECT COUNT(*) FROM import_batch;");
            long staffBefore = CountRows(copyDb, "SELECT COUNT(*) FROM staff;");
            (int NewCount, int UpdatedCount) imp1 = (0, 0);
            Exception? impEx1 = TryGate("导入职工清单", (c, t) =>
                imp1 = StaffService.ImportStaff(c, pXlsx.Staff, "职工清单.xlsx", pXlsx.FileHash));
            long batchAfter = CountRows(copyDb, "SELECT COUNT(*) FROM import_batch;");
            long staffAfter = CountRows(copyDb, "SELECT COUNT(*) FROM staff;");
            long newType;
            string liSiType, wangWuNote;
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            {
                newType = ro.ExecuteScalar<long>(
                    "SELECT COUNT(*) FROM staff_type_def WHERE name = '测试新类型'");
                liSiType = ro.ExecuteScalar<string?>(
                    "SELECT staff_type FROM staff WHERE name = '李四'") ?? "(缺失)";
                wangWuNote = ro.ExecuteScalar<string?>(
                    "SELECT note FROM staff WHERE name = '王五'") ?? "(缺失)";
            }
            bool impOk1 = impEx1 is null
                && batchAfter - batchBefore == 1
                && imp1.NewCount + imp1.UpdatedCount == pXlsx.Staff.Count
                && staffAfter - staffBefore == imp1.NewCount
                && newType == 1
                && liSiType == "聘用"
                && wangWuNote == "2025";
            Check("5. 导入：import_batch +1；(新增,更新) 与库内一致；未知类型自动补入；空类型兜底聘用",
                impOk1,
                $"batch {batchBefore}->{batchAfter}；新增={imp1.NewCount} 更新={imp1.UpdatedCount}；staff {staffBefore}->{staffAfter}；新类型补入={newType}；李四型='{liSiType}'；王五备注='{wangWuNote}'；err={impEx1?.Message ?? "-"}");

            // ---- 6. 重跑同一份文件：全部更新、不新增 ----
            (int NewCount, int UpdatedCount) imp2 = (0, 0);
            Exception? impEx2 = TryGate("导入职工清单", (c, t) =>
                imp2 = StaffService.ImportStaff(c, pXlsx.Staff, "职工清单.xlsx", pXlsx.FileHash));
            long staffAfter2 = CountRows(copyDb, "SELECT COUNT(*) FROM staff;");
            Check("6. 重跑同一文件：全为更新、0 新增、staff 行数不变",
                impEx2 is null && imp2.NewCount == 0 && imp2.UpdatedCount == pXlsx.Staff.Count
                    && staffAfter2 == staffAfter,
                $"新增={imp2.NewCount} 更新={imp2.UpdatedCount}；staff {staffAfter}->{staffAfter2}；err={impEx2?.Message ?? "-"}");

            // ---- 7. 读方法纯读 ----
            int b7a = WriteGuard.BackupCount(copyDb);
            using (var ro = DbConnection.OpenReadOnly(copyDb))
            {
                _ = StaffService.ListStaff(ro);
                _ = StaffTypeService.ListTypes(ro);
            }
            int b7b = WriteGuard.BackupCount(copyDb);
            Check("7. 读方法纯读（ListStaff/ListTypes 不产生备份）", b7b == b7a,
                $"{b7a} -> {b7b}（用户变更经闸门 {gateCalls} 次，观测新建备份 {backupsCreated} 份）");

            // ---- 8. 真实库未触碰 ----
            DateTime realAfter = new FileInfo(realDb).LastWriteTime;
            DateTime? realWalAfter = File.Exists(realDb + "-wal") ? new FileInfo(realDb + "-wal").LastWriteTime : null;
            DateTime? realShmAfter = File.Exists(realDb + "-shm") ? new FileInfo(realDb + "-shm").LastWriteTime : null;
            bool dbSame = realAfter == realBefore;
            bool walSame = Nullable.Equals(realWalAfter, realWalBefore);
            bool shmSame = Nullable.Equals(realShmAfter, realShmBefore);
            bool backupsUntouched = realBackupDirBefore
                ? WriteGuard.BackupCount(realDb) == realBackupCountBefore
                : !Directory.Exists(realBackupDir);
            Check("8. 真实库未被触碰（文件时间一致 / 未创建 data\\backups）",
                dbSame && walSame && shmSame && backupsUntouched,
                $"db {realBefore:HH:mm:ss.fff}->{realAfter:HH:mm:ss.fff}, wal同={walSame}, shm同={shmSame}, 备份目录存在={Directory.Exists(realBackupDir)}");
        }
        catch (Exception ex)
        {
            Check("! 自测过程异常", false, $"{ex.GetType().Name}: {ex.Message}");
            Console.Error.WriteLine(ex.ToString());
        }
        finally
        {
            try { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); } catch { /* 忽略 */ }
            try { if (Directory.Exists(tempDir)) Directory.Delete(tempDir, recursive: true); }
            catch { /* 临时目录清理失败不影响结论 */ }
        }

        Console.WriteLine();
        foreach (var r in results)
            Console.WriteLine($"  [{(r.Ok ? "PASS" : "FAIL")}] {r.Name}"
                + (string.IsNullOrEmpty(r.Detail) ? "" : $"  —— {r.Detail}"));

        bool allPass = results.Count > 0 && results.TrueForAll(r => r.Ok);
        Console.WriteLine();
        Console.WriteLine($"[{(allPass ? "OK" : "FAIL")}] 职工清单导入自测：{results.FindAll(r => r.Ok).Count}/{results.Count} 项通过");
        Console.WriteLine($"[INFO] 真实库 LastWriteTime（测试后）= {new FileInfo(realDb).LastWriteTime:yyyy-MM-dd HH:mm:ss.fff}（应与测试前一致）");
        return allPass ? 0 : 1;
    }

    /// <summary>尝试解析并返回异常消息；未抛异常返回 "(未抛出)"（供错误路径断言）。</summary>
    private static string TryParseError(string path)
    {
        try
        {
            StaffImportParser.ParseStaffFile(path);
            return "(未抛出)";
        }
        catch (StaffImportException ex)
        {
            return ex.Message;
        }
        catch (Exception ex)
        {
            return ex.GetType().Name + ": " + ex.Message;
        }
    }

    /// <summary>备份目录中「不在 before 集合里」的新文件个数（用于观测每次写入新建的快照）。</summary>
    private static int CountNewBackups(string dir, HashSet<string> before)
        => FileList(dir).Count(f => !before.Contains(f));

    /// <summary>只读连接执行 SELECT，返回单个整数（COUNT 等）。</summary>
    private static long CountRows(string dbPath, string sql)
    {
        using var conn = DbConnection.OpenReadOnly(dbPath);
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        object? v = cmd.ExecuteScalar();
        return v is null || v is DBNull ? 0L : Convert.ToInt64(v, System.Globalization.CultureInfo.InvariantCulture);
    }

    /// <summary>只读连接执行 SELECT，取第一行并按列转字符串（NULL -> ""）。</summary>
    private static string[] ReadRow(string dbPath, string sql)
    {
        using var conn = DbConnection.OpenReadOnly(dbPath);
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        using var r = cmd.ExecuteReader();
        if (!r.Read()) return Array.Empty<string>();
        var vals = new string[r.FieldCount];
        for (int i = 0; i < r.FieldCount; i++)
            vals[i] = r.IsDBNull(i) ? "" : r.GetValue(i)?.ToString() ?? "";
        return vals;
    }

    /// <summary>备份目录中的 lawfirm-*.db 列表（目录不存在返回空）。</summary>
    private static string[] FileList(string dir)
        => Directory.Exists(dir) ? Directory.GetFiles(dir, "lawfirm-*.db") : Array.Empty<string>();

    /// <summary>文件头 16 字节是否为 SQLite 魔数「SQLite format 3\0」。</summary>
    private static bool HasSqliteHeader(string path)
    {
        try
        {
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            var head = new byte[16];
            if (fs.Read(head, 0, 16) != 16) return false;
            return System.Text.Encoding.ASCII.GetString(head, 0, 16) == "SQLite format 3\0";
        }
        catch
        {
            return false;
        }
    }

    /// <summary>取数组第 i 项，越界返回 "(缺失)"（用于安全拼诊断文本）。</summary>
    private static string AtStr(string[] arr, int i) => i < arr.Length ? arr[i] : "(缺失)";

    /// <summary>中位数（偶数个取中间两数平均，向下取整）。空集合返回 0。</summary>
    private static long Median(List<long> values)
    {
        if (values.Count == 0) return 0;
        var sorted = new List<long>(values);
        sorted.Sort();
        int mid = sorted.Count / 2;
        return sorted.Count % 2 == 1
            ? sorted[mid]
            : (sorted[mid - 1] + sorted[mid]) / 2;
    }

    /// <summary>
    /// 订阅 PersonalSettlementViewModel.PropertyChanged，对 Rows / SummaryText 变更计数，
    /// 并记录最近一次事件的时刻（供 400ms 静默判定）。事件在后台线程触发，故一律用 Interlocked/Volatile。
    /// </summary>
    private sealed class SwitchProbe
    {
        private int _rows;
        private int _summary;
        private long _lastTs = System.Diagnostics.Stopwatch.GetTimestamp();

        public void Reset()
        {
            Interlocked.Exchange(ref _rows, 0);
            Interlocked.Exchange(ref _summary, 0);
            Interlocked.Exchange(ref _lastTs, System.Diagnostics.Stopwatch.GetTimestamp());
        }

        public void OnPropertyChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
        {
            if (e.PropertyName == "Rows")
            {
                Interlocked.Increment(ref _rows);
                Interlocked.Exchange(ref _lastTs, System.Diagnostics.Stopwatch.GetTimestamp());
            }
            else if (e.PropertyName == "SummaryText")
            {
                Interlocked.Increment(ref _summary);
                Interlocked.Exchange(ref _lastTs, System.Diagnostics.Stopwatch.GetTimestamp());
            }
        }

        public int RowsCount => Volatile.Read(ref _rows);
        public int SummaryCount => Volatile.Read(ref _summary);

        /// <summary>距最后一次事件的高精度毫秒数（供 400ms 静默判定）。</summary>
        public double MillisSinceLastEvent
            => TicksToMs(System.Diagnostics.Stopwatch.GetTimestamp() - Interlocked.Read(ref _lastTs));

        /// <summary>最近一次 Rows/SummaryText 事件的绝对高精度时刻戳；净耗时 = 末事件戳 − 设 Person 戳。</summary>
        public long LastEventTimestamp => Interlocked.Read(ref _lastTs);

        /// <summary>Stopwatch 刻度 → 毫秒。</summary>
        public static double TicksToMs(long ticks) => ticks * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
    }
}
