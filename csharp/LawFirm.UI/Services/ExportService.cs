using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using LawFirm.Data;
using LawFirm.Exporter;
using Microsoft.Data.Sqlite;

namespace LawFirm.UI.Services;

/// <summary>进度上报（规格 §6.2）。</summary>
public sealed record ProgressReport(int Done, int Total, string CurrentItem)
{
    public double Percent => Total <= 0 ? 0 : (double)Done / Total * 100.0;
}

/// <summary>
/// 导出服务（G7/T5.4，规格 §6）：后台线程跑 NPOI 导出。
///
/// 数据层纪律（不可破，R6/R11）：
/// - SqliteConnection **不跨线程传递**：连接在本服务的后台线程内经
///   <see cref="DbConnection.OpenReadOnly"/> 自建（query_only + ReadOnly）；
/// - UI 线程零写库；导出只写用户选定的输出目录；
/// - 取消粒度 = 每人一个文件（ct 在每人间检查）；已落盘文件不删除（U7）。
/// </summary>
public sealed class ExportService
{
    /// <summary>导出全部员工（每人一文件）。对应 Python export_all（带逐人进度 + 取消）。</summary>
    public Task<IReadOnlyList<string>> ExportAllAsync(
        string outDir, int year, IProgress<ProgressReport>? progress, CancellationToken ct)
        => Task.Run<IReadOnlyList<string>>(() =>
        {
            Directory.CreateDirectory(outDir);
            var files = new List<string>();
            // 后台线程自建只读连接（禁止跨线程传 conn）
            using (var conn = DbConnection.OpenReadOnly(DbConnection.FindDatabase()))
            {
                var persons = SettlementEngine.Build(conn, year).Keys
                    .OrderBy(k => k, StringComparer.Ordinal).ToList();
                int total = persons.Count;
                for (int i = 0; i < total; i++)
                {
                    ct.ThrowIfCancellationRequested();
                    progress?.Report(new ProgressReport(i, total, persons[i]));
                    string path = Path.Combine(outDir, $"个人结算总表_{SafeFileName(persons[i])}.xlsx");
                    files.Add(PersonSettlementExporter.ExportOne(conn, persons[i], path, year));
                }
            }
            progress?.Report(new ProgressReport(files.Count, files.Count, string.Empty));
            return files;
        }, ct);

    /// <summary>导出指定人员（settlement_view.py:415-417 的 C# 版，带逐人进度 + 取消）。</summary>
    public Task<IReadOnlyList<string>> ExportSelectedAsync(
        string outDir, int year, IReadOnlyList<string> persons,
        IProgress<ProgressReport>? progress, CancellationToken ct)
        => Task.Run<IReadOnlyList<string>>(() =>
        {
            Directory.CreateDirectory(outDir);
            var files = new List<string>();
            using (var conn = DbConnection.OpenReadOnly(DbConnection.FindDatabase()))
            {
                int total = persons.Count;
                for (int i = 0; i < total; i++)
                {
                    ct.ThrowIfCancellationRequested();
                    progress?.Report(new ProgressReport(i, total, persons[i]));
                    string path = Path.Combine(outDir, $"个人结算总表_{SafeFileName(persons[i])}.xlsx");
                    files.Add(PersonSettlementExporter.ExportOne(conn, persons[i], path, year));
                }
            }
            progress?.Report(new ProgressReport(files.Count, files.Count, string.Empty));
            return files;
        }, ct);

    /// <summary>导出月度结算表（单人文件）。对应 Python gen_report → export_report。</summary>
    public Task<IReadOnlyList<string>> ExportMonthlyAsync(
        string outPath, int year, int month, IReadOnlyList<string> persons,
        IProgress<ProgressReport>? progress, CancellationToken ct)
        => Task.Run<IReadOnlyList<string>>(() =>
        {
            ct.ThrowIfCancellationRequested();
            progress?.Report(new ProgressReport(0, 1, $"{year}年{month}月结算表"));
            IReadOnlyList<string> files;
            // 后台线程自建只读连接（SettlementReportExporter 收外部连接）
            using (var conn = DbConnection.OpenReadOnly(DbConnection.FindDatabase()))
            {
                var path = SettlementReportExporter.ExportReport(
                    conn, outPath, year, month, persons.ToList());
                files = new List<string> { path };
            }
            progress?.Report(new ProgressReport(1, 1, outPath));
            return files;
        }, ct);

    /// <summary>
    /// 文件名安全化（逐字对齐 Python：person.replace("/","_").replace("\\","_").strip() or "未命名"）。
    /// 与 PersonSettlementExporter.SafeFileName 同规则；该处为 private，故在此复制（注释锚点：T3 移植报告）。
    /// </summary>
    private static string SafeFileName(string person)
    {
        string safe = person.Replace("/", "_").Replace("\\", "_").Trim();
        return safe.Length == 0 ? "未命名" : safe;
    }
}
