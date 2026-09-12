using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using LawFirm.Data;
using LawFirm.Exporter;
using Microsoft.Data.Sqlite;

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
