using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
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
            Console.WriteLine("[OK] 4/4 cases passed.");
            return 0;
        }
        foreach (var f in failures) Console.Error.WriteLine($"[FAIL] {f}");
        return 1;
    }
}
