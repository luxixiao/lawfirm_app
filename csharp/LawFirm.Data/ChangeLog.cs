using System;
using System.Collections.Generic;
using System.Globalization;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data;

/// <summary>
/// 修改记录写入（对齐 Python 侧 <c>app/engine/change_log.py::log_change</c>）。
///
/// 约定：任何对导入台账数据的修改，都应在**同一事务内**追加一条 change_log，
/// 与业务 UPDATE 一起提交，保证「改动」与「留痕」原子一致。
/// 本类只写、不读、不建连接——连接与事务由调用方提供（与 Python 保持一致）。
/// </summary>
public static class ChangeLog
{
    // 11 列 INSERT，列顺序与 Python 完全一致。
    private const string InsertSql =
        "INSERT INTO change_log " +
        "(table_name, record_id, field, old_value, new_value, note, " +
        " friendly_table, invoice_no, buyer, amount, handlers) " +
        "VALUES ($table_name, $record_id, $field, $old_value, $new_value, $note, " +
        " $friendly_table, $invoice_no, $buyer, $amount, $handlers)";

    /// <summary>
    /// 写一条修改记录（应在事务内调用，与业务 UPDATE 一起提交）。
    ///
    /// friendlyTable/invoiceNo/buyer/amount/handlers 为「修改前整行快照」，
    /// 供修改记录页展示发票号码/对方/金额/经办人/修改表名（均为未修改前数据）。
    /// 调用方不传时留空（如批量同步、旧代码路径）。
    /// </summary>
    /// <param name="conn">调用方持有的连接；若其上有活动事务，命令会自动并入该事务。</param>
    /// <param name="tableName">表名。</param>
    /// <param name="recordId">记录 id（字符串）。</param>
    /// <param name="field">被修改字段名。</param>
    /// <param name="oldValue">旧值；null 记为 ""。</param>
    /// <param name="newValue">新值；null 记为 ""。</param>
    /// <param name="note">备注。</param>
    /// <param name="friendlyTable">友好表名（快照）。</param>
    /// <param name="invoiceNo">发票号码（快照）。</param>
    /// <param name="buyer">对方（快照）。</param>
    /// <param name="amount">金额（快照）。</param>
    /// <param name="handlers">经办人（快照）。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void Log(SqliteConnection conn, string tableName, string recordId, string field,
        object? oldValue, object? newValue, string note = "",
        string friendlyTable = "", string invoiceNo = "", string buyer = "",
        string amount = "", string handlers = "")
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        // 用 conn.CreateCommand()（而非 new SqliteCommand）：它会自动带上连接当前的活动事务，
        // 因此本方法在 WriteGuard 的事务内被调用时，这条 INSERT 会自动并入同一事务一起提交。
        using var cmd = conn.CreateCommand();
        cmd.CommandText = InsertSql;

        // 与 Python 一致：None -> ""，其余走 str() 语义；全部按 TEXT 列写入。
        cmd.Parameters.AddWithValue("$table_name", tableName ?? "");
        cmd.Parameters.AddWithValue("$record_id", recordId ?? "");
        cmd.Parameters.AddWithValue("$field", field ?? "");
        cmd.Parameters.AddWithValue("$old_value", ToStr(oldValue));
        cmd.Parameters.AddWithValue("$new_value", ToStr(newValue));
        cmd.Parameters.AddWithValue("$note", note ?? "");
        cmd.Parameters.AddWithValue("$friendly_table", friendlyTable ?? "");
        cmd.Parameters.AddWithValue("$invoice_no", invoiceNo ?? "");
        cmd.Parameters.AddWithValue("$buyer", buyer ?? "");
        cmd.Parameters.AddWithValue("$amount", amount ?? "");
        cmd.Parameters.AddWithValue("$handlers", handlers ?? "");

        cmd.ExecuteNonQuery();
    }

    /// <summary>写多条修改记录；每条对应一个 (字段, 旧值, 新值) 三元组。</summary>
    /// <param name="conn">调用方持有的连接（含活动事务即可）。</param>
    /// <param name="tableName">表名。</param>
    /// <param name="recordId">记录 id。</param>
    /// <param name="changes">要记录的变更集合。</param>
    /// <param name="note">统一备注。</param>
    public static void LogChanges(SqliteConnection conn, string tableName, string recordId,
        IEnumerable<(string Field, object? Old, object? New)> changes, string note = "")
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        if (changes is null) return;

        foreach (var (field, oldValue, newValue) in changes)
            Log(conn, tableName, recordId, field, oldValue, newValue, note);
    }

    /// <summary>
    /// 复刻 Python <c>"" if x is None else str(x)</c>：null 记为 ""，其余转字符串。
    /// 布尔转 True/False（与 Python str(bool) 一致）；数值用不变区域格式。
    /// </summary>
    private static string ToStr(object? value)
    {
        if (value is null) return "";
        if (value is string s) return s;
        if (value is bool b) return b ? "True" : "False";
        if (value is IFormattable f) return f.ToString(null, CultureInfo.InvariantCulture) ?? "";
        return value.ToString() ?? "";
    }
}
