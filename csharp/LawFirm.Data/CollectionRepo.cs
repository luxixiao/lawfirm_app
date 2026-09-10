using System.Collections.Generic;
using Dapper;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data;

/// <summary>
/// collection 表只读仓储（Pilot T2）。
///
/// 仅提供聚合计数与抽样读取。不持有连接所有权——连接由调用方以 using 管理并只读打开。
/// </summary>
public class CollectionRepo
{
    private readonly SqliteConnection _conn;

    public CollectionRepo(SqliteConnection conn) => _conn = conn;

    /// <summary>collection 表总行数（与 Python 侧 SELECT COUNT(*) 一致）。</summary>
    public int CountAll()
    {
        return _conn.ExecuteScalar<int>("SELECT COUNT(*) FROM collection;");
    }

    /// <summary>按 id 排序取前 n 行样例（默认 5），用于 UI 预览与 golden 抽样比对。</summary>
    public IEnumerable<CollectionRow> Sample(int n = 5)
    {
        const string sql =
            "SELECT id, invoice_no, amount, receipt_date, person_name, " +
            "       source, import_batch_id, note " +
            "FROM collection ORDER BY id LIMIT @n;";
        return _conn.Query<CollectionRow>(sql, new { n });
    }
}
