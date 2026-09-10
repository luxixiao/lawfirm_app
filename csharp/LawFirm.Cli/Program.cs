using System;
using System.Collections.Generic;
using System.Linq;
using LawFirm.Data;
using LawFirm.Exporter;
using Microsoft.Data.Sqlite;

namespace LawFirm.Cli;

/// <summary>
/// T2 命令行入口：只读打开 data/lawfirm.db，复算结算引擎并用 NPOI 导出月度结算表 xlsx，
/// 供 scripts/diff_xlsx.py 与 Python 应用导出的 golden 逐格比对（回归安全网）。
///
/// 用法（在仓库根 lawfirm_app/ 下，先 dotnet build）：
///   dotnet run --project csharp/LawFirm.Cli -- \
///       --year 2026 --month 12 --out csharp/out/settlement_report_csharp.xlsx
///
/// 导出后比对：
///   python scripts/diff_xlsx.py ^
///       tests/golden/settlement_report_exporter/2026年12月结算表.xlsx ^
///       csharp/out/settlement_report_csharp.xlsx --verbose
/// </summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        int year = DateTime.Today.Year;
        int month = 12;
        string outPath = "csharp/out/settlement_report_csharp.xlsx";

        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--year" when i + 1 < args.Length: year = int.Parse(args[++i]); break;
                case "--month" when i + 1 < args.Length: month = int.Parse(args[++i]); break;
                case "--out" when i + 1 < args.Length: outPath = args[++i]; break;
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

            // 名单 = 全选（与 Python dump_golden 的 sorted(build_settlement(year).keys()) 一致；
            // StringComparer.Ordinal 对应 Python 默认 codepoint 排序，保证 sheet 顺序一致）
            var persons = SettlementEngine.Build(conn, year)
                .Keys.OrderBy(k => k, StringComparer.Ordinal).ToList();

            string generated = SettlementReportExporter.ExportReport(conn, outPath, year, month, persons);
            Console.WriteLine($"[OK] 已导出: {generated}");
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
}
