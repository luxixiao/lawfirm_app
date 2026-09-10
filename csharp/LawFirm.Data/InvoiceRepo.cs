using System.Collections.Generic;
using Dapper;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data;

/// <summary>
/// invoice 表只读仓储（Pilot T2）。
///
/// 仅提供聚合计数与抽样读取，供 C# 产物与 Python 版行数/抽样数值比对。
/// 不持有连接所有权——连接由调用方（MainWindow）以 using 管理并只读打开。
/// </summary>
public class InvoiceRepo
{
    private readonly SqliteConnection _conn;

    public InvoiceRepo(SqliteConnection conn) => _conn = conn;

    /// <summary>invoice 表总行数（与 Python 侧 SELECT COUNT(*) 一致）。</summary>
    public int CountAll()
    {
        return _conn.ExecuteScalar<int>("SELECT COUNT(*) FROM invoice;");
    }

    /// <summary>按 invoice_no 排序取前 n 行样例（默认 5），用于 UI 预览与 golden 抽样比对。</summary>
    public IEnumerable<InvoiceRow> Sample(int n = 5)
    {
        const string sql =
            "SELECT invoice_no, invoice_date, buyer, total_amount, kind, status, " +
            "       voucher_no, goods, net_amount, tax_rate, tax, orig_invoice_no, remark, " +
            "       case_no, source, import_batch_id, created_at " +
            "FROM invoice ORDER BY invoice_no LIMIT @n;";
        // Dapper 默认 buffered:true，返回已物化的 List，连接释放后仍可安全枚举。
        return _conn.Query<InvoiceRow>(sql, new { n });
    }
}
