using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Data.Sqlite;

namespace LawFirm.Exporter;

/// <summary>
/// 费用类型顺序（Pilot T2，C# 端口）。
/// 镜像 app/engine/expense_cat.py 的 ordered_types()：按维护顺序
/// （分类顺序 CATEGORIES + 类内 sort_order, expense_type）返回费用类型列表。
/// 结算表「六、减：分成报酬及费用」的费用行顺序依赖此序，必须与 Python 完全一致。
/// </summary>
public static class ExpenseCatHelper
{
    private static readonly string[] Categories = { "报酬发放", "住房公积金", "保险费", "汽油费", "其他" };

    public static List<string> OrderedTypes(SqliteConnection conn)
    {
        var rows = new List<(string Type, string Cat)>();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT expense_type, category FROM expense_cat ORDER BY sort_order, expense_type";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            string type = r.IsDBNull(0) ? "" : r.GetString(0);
            string cat = r.IsDBNull(1) ? "" : r.GetString(1);
            rows.Add((type, Normalize(cat)));
        }
        var byCat = Categories.ToDictionary(c => c, _ => new List<string>());
        foreach (var (type, cat) in rows) byCat[cat].Add(type);
        var result = new List<string>();
        foreach (var c in Categories) result.AddRange(byCat[c]);
        return result;
    }

    private static string Normalize(string? c)
        => (c != null && Categories.Contains(c)) ? c : "其他";
}
