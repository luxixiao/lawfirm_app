using System;
using System.Collections.Generic;
using System.Linq;
using Dapper;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data.Expense;

/// <summary>
/// 费用类型维护（<c>app/engine/expense_cat.py</c> 的逐方法移植）。
///
/// <para>数据模型：</para>
/// <list type="bullet">
///   <item><c>expense_cat</c>（类型全集）：expense_type / category / sort_order（全局顺序）。</item>
///   <item><c>expense_category</c>（分类说明）：name / note / sort_order。</item>
/// </list>
///
/// <para>顺序口径：<b>全局顺序 = 分类顺序 + 类型在类内的顺序</b>（<see cref="SaveLayout"/> 一次性写回 sort_order）。
/// <see cref="OrderedTypes"/> 供结算表取数，故改动顺序即改导出列序。</para>
///
/// <para>连接约定（沿用 Batch 2a）：所有方法以调用方传入的 <see cref="SqliteConnection"/> 为第一参数，
/// <b>绝不自建连接</b>、<b>绝不自行 Commit</b>（事务归 <see cref="WriteGuard"/>）。
/// 读方法一律纯 SELECT（<see cref="EnsureCategories"/> 不得混进读路径）。</para>
///
/// <para>DTO 映射：SQL 用 <c>AS &lt;属性名&gt;</c> 别名——本项目 Dapper 只按「属性名 / 去下划线」匹配、
/// <b>不读</b> DataAnnotations 的 <c>[Column]</c>，故不依赖该特性。</para>
/// </summary>
public static class ExpenseCatService
{
    /// <summary>固定 5 个分类（不可增删；顺序敏感）。</summary>
    public static readonly string[] Categories = { "报酬发放", "住房公积金", "保险费", "汽油费", "其他" };

    /// <summary>兜底分类：脏数据（分类不在 <see cref="Categories"/> 内）一律并入。</summary>
    public const string FallbackCategory = "其他";

    /// <summary>预置默认归类规则（按类型名包含关键词，顺序敏感）。</summary>
    public static readonly (string Keyword, string Category)[] DefaultRule =
    {
        ("公积金", "住房公积金"),
        ("社保", "保险费"),
        ("保险", "保险费"),
        ("分成", "报酬发放"),
        ("报酬", "报酬发放"),
        ("汽油", "汽油费"),
        ("刷卡", "汽油费"),
        ("停车", "汽油费"),
        ("油", "汽油费"),
    };

    // ------------------------------------------------------------------ #
    // 分类（固定 5 类，说明可自定义）
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 建库 / 升级后补齐 5 个分类（缺哪个补哪个）。幂等维护写，经写闸门调用；不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void EnsureCategories(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        const string sql =
            "INSERT OR IGNORE INTO expense_category(name, note, sort_order) VALUES (@n, '', @so)";
        for (int i = 0; i < Categories.Length; i++)
            conn.Execute(sql, new { n = Categories[i], so = i + 1 });
    }

    /// <summary>
    /// 分类清单：名称 / 说明 / 排序 / 该类的类型数。
    /// <b>纯 SELECT</b>——不触发 <see cref="EnsureCategories"/>（缺的分类运行时补为 0 条，不落库）。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <returns>分类行（顺序 == 库内维护顺序，末尾补齐缺失的固定分类）。</returns>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static List<ExpenseCategoryRow> ListCategories(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        var defs = new List<(string Name, string? Note, int SortOrder)>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = "SELECT name, note, sort_order FROM expense_category ORDER BY sort_order, name";
            using var r = cmd.ExecuteReader();
            while (r.Read())
                defs.Add((r.IsDBNull(0) ? "" : r.GetString(0),
                          r.IsDBNull(1) ? null : r.GetString(1),
                          r.IsDBNull(2) ? 0 : r.GetInt32(2)));
        }

        Dictionary<string, int> counts = CountByCategory(conn);
        var outl = new List<ExpenseCategoryRow>();
        var have = new HashSet<string>();
        foreach (var (name, note, sortOrder) in defs)
        {
            outl.Add(new ExpenseCategoryRow(name, note, sortOrder,
                counts.TryGetValue(name, out int c) ? c : 0));
            have.Add(name);
        }
        // 分类表里若缺某个固定分类（被外部删过）→ 运行时补齐为 0 条（不写库）。
        for (int i = 0; i < Categories.Length; i++)
        {
            if (!have.Contains(Categories[i]))
                outl.Add(new ExpenseCategoryRow(Categories[i], "", i + 1, 0));
        }
        return outl;
    }

    /// <summary>各类类型数（脏数据归类并入兜底类）。纯 SELECT。</summary>
    private static Dictionary<string, int> CountByCategory(SqliteConnection conn)
    {
        Dictionary<string, string> m = Normalize(conn);
        var outd = new Dictionary<string, int>();
        foreach (string c in Categories) outd[c] = 0;

        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT category FROM expense_cat";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            string raw = r.IsDBNull(0) ? "" : r.GetString(0);
            string key = m.TryGetValue(raw, out string? n) ? n : FallbackCategory;
            outd[key] += 1;
        }
        return outd;
    }

    /// <summary>脏数据归类：返回「原始 category 值 → 规范分类」的映射（不在 5 类内 → 兜底类）。纯 SELECT。</summary>
    public static Dictionary<string, string> Normalize(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        var outd = new Dictionary<string, string>();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT DISTINCT category FROM expense_cat";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            string raw = r.IsDBNull(0) ? "" : r.GetString(0);
            outd[raw] = NormCat(raw);
        }
        return outd;
    }

    /// <summary>取分类说明；分类不存在返回 ""。纯 SELECT。</summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <param name="name">分类名。</param>
    /// <returns>说明文本（null 视为 ""）。</returns>
    public static string GetCategoryNote(SqliteConnection conn, string name)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        string? note = conn.ExecuteScalar<string?>(
            "SELECT note FROM expense_category WHERE name = @n", new { n = name });
        return note ?? "";
    }

    /// <summary>设置分类说明（去空白）。写方法；先补齐 5 类，再更新。不 Commit。</summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">分类名。</param>
    /// <param name="note">新说明（去空白）。</param>
    public static void SetCategoryNote(SqliteConnection conn, string name, string note)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        EnsureCategories(conn);
        conn.Execute("UPDATE expense_category SET note = @note WHERE name = @n",
            new { note = (note ?? "").Trim(), n = name });
    }

    // ------------------------------------------------------------------ #
    // 类型全集
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 确保费用类型在 expense_cat 中；缺失的按默认规则（<see cref="DefaultRule"/>）自动归类，
    /// 无规则命中则落兜底类。不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    /// <param name="types">待补齐的类型名集合（空串跳过；<b>不 trim</b>，与 Python 一致）。</param>
    public static void EnsureTypes(SqliteConnection conn, IEnumerable<string>? types)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        if (types is null) return;

        foreach (string raw in types)
        {
            if (string.IsNullOrEmpty(raw)) continue;
            if (conn.ExecuteScalar<long>(
                    "SELECT COUNT(1) FROM expense_cat WHERE expense_type = @t", new { t = raw }) > 0)
                continue;

            string cat = FallbackCategory;
            foreach (var (kw, c) in DefaultRule)
            {
                if (raw.Contains(kw, StringComparison.Ordinal)) { cat = c; break; }
            }
            long nxt = conn.ExecuteScalar<long>("SELECT COALESCE(MAX(sort_order),0)+1 FROM expense_cat");
            conn.Execute("INSERT INTO expense_cat (expense_type, category, sort_order) VALUES (@t, @c, @so)",
                new { t = raw, c = cat, so = nxt });
        }
    }

    /// <summary>返回不在 expense_cat 名单中的费用类型（用于导入报错，保持输入顺序）。纯 SELECT。</summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <param name="types">待检查类型集合。</param>
    /// <returns>未知类型（非空且不在名单）。</returns>
    public static List<string> CheckUnknown(SqliteConnection conn, IEnumerable<string>? types)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        var known = new HashSet<string>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = "SELECT expense_type FROM expense_cat";
            using var r = cmd.ExecuteReader();
            while (r.Read()) known.Add(r.IsDBNull(0) ? "" : r.GetString(0));
        }
        var outl = new List<string>();
        if (types is null) return outl;
        foreach (string t in types)
            if (!string.IsNullOrEmpty(t) && !known.Contains(t)) outl.Add(t);
        return outl;
    }

    /// <summary>类型 → 原始 category 映射（不做归一化，与 Python get_map 一致）。纯 SELECT。</summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <returns>expense_type → category。</returns>
    public static Dictionary<string, string> GetMap(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        var outd = new Dictionary<string, string>();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT expense_type, category FROM expense_cat";
        using var r = cmd.ExecuteReader();
        while (r.Read())
            outd[r.IsDBNull(0) ? "" : r.GetString(0)] =
                r.IsDBNull(1) ? FallbackCategory : r.GetString(1);
        return outd;
    }

    /// <summary>
    /// 该类下的费用类型（按类内维护顺序）。脏数据归类并入兜底类。
    /// 纯 SELECT（内部只读 <see cref="Normalize"/>）。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <param name="category">目标分类。</param>
    /// <returns>该类下的类型名列表（category 非法则空）。</returns>
    public static List<string> GetByCategory(SqliteConnection conn, string category)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        Dictionary<string, string> m = Normalize(conn);
        List<string> cats = m.Where(kv => kv.Value == category).Select(kv => kv.Key).ToList();
        if (cats.Count == 0) return new List<string>();

        var p = new DynamicParameters();
        var holders = new string[cats.Count];
        for (int k = 0; k < cats.Count; k++)
        {
            holders[k] = "@p" + k;
            p.Add("@p" + k, cats[k]);
        }
        string sql = "SELECT expense_type FROM expense_cat WHERE category IN (" +
                     string.Join(",", holders) + ") ORDER BY sort_order, expense_type";
        return conn.Query<string>(sql, p).ToList();
    }

    /// <summary>
    /// {分类: [类型...]}，类内按维护顺序；保证 5 个分类都有键。
    /// 纯 SELECT（不调 <see cref="EnsureCategories"/>）。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <returns>分类到类型列表的字典（键为 5 个固定分类）。</returns>
    public static Dictionary<string, List<string>> TypesByCategory(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        var outd = new Dictionary<string, List<string>>();
        foreach (string c in Categories) outd[c] = new List<string>();

        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT expense_type, category FROM expense_cat ORDER BY sort_order, expense_type";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            string type = r.IsDBNull(0) ? "" : r.GetString(0);
            string cat = r.IsDBNull(1) ? "" : r.GetString(1);
            outd[NormCat(cat)].Add(type);
        }
        return outd;
    }

    /// <summary>
    /// 按维护顺序（分类顺序 + 类内顺序）返回费用类型列表。纯 SELECT。
    /// 必须与 <c>LawFirm.Exporter.ExpenseCatHelper.OrderedTypes</c> 逐元素相同。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <returns>费用类型列表。</returns>
    public static List<string> OrderedTypes(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        Dictionary<string, List<string>> byCat = TypesByCategory(conn);
        var outl = new List<string>();
        foreach (string c in Categories)
        {
            if (byCat.TryGetValue(c, out List<string>? list)) outl.AddRange(list);
        }
        return outl;
    }

    /// <summary>分类归一化：在 5 类内则原样，否则落兜底类。</summary>
    /// <param name="category">原始分类值（可为 null）。</param>
    /// <returns>规范分类。</returns>
    public static string NormCat(string? category)
        => category is not null && Array.IndexOf(Categories, category) >= 0 ? category : FallbackCategory;

    // ------------------------------------------------------------------ #
    // 类型增删改
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 新增类型；重名报错，新类型追加到该类末尾（类别非法则落兜底类）。不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    /// <param name="expenseType">类型名（去空白）。</param>
    /// <param name="category">目标分类（去归一化）。</param>
    /// <exception cref="ExpenseCatException">名非法 / 超长 / 重名。</exception>
    public static void AddType(SqliteConnection conn, string expenseType, string category = FallbackCategory)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        string name = (expenseType ?? "").Trim();
        if (name.Length == 0) throw new ExpenseCatException("费用类型名称不能为空");
        if (CountCodePoints(name) > 30) throw new ExpenseCatException("费用类型名称过长（≤30 字符）");
        string cat = NormCat(category);

        if (conn.ExecuteScalar<long>(
                "SELECT COUNT(1) FROM expense_cat WHERE expense_type = @t", new { t = name }) > 0)
            throw new ExpenseCatException($"费用类型已存在：{name}");

        long nxt = conn.ExecuteScalar<long>("SELECT COALESCE(MAX(sort_order),0)+1 FROM expense_cat");
        conn.Execute("INSERT INTO expense_cat (expense_type, category, sort_order) VALUES (@t, @c, @so)",
            new { t = name, c = cat, so = nxt });
    }

    /// <summary>
    /// 改名：同步更新 expense_cat <b>与</b> expense_ledger.expense_type（避免历史数据归类丢失）。不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    /// <param name="oldName">原类型名。</param>
    /// <param name="newName">新类型名（去空白）。</param>
    /// <exception cref="ExpenseCatException">新名非法 / 超长 / 原类型不存在 / 新名重复。</exception>
    public static void RenameType(SqliteConnection conn, string oldName, string newName)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        string nn = (newName ?? "").Trim();
        if (nn.Length == 0) throw new ExpenseCatException("费用类型名称不能为空");
        if (CountCodePoints(nn) > 30) throw new ExpenseCatException("费用类型名称过长（≤30 字符）");

        if (conn.ExecuteScalar<long>(
                "SELECT COUNT(1) FROM expense_cat WHERE expense_type = @t", new { t = oldName }) == 0)
            throw new ExpenseCatException($"费用类型不存在：{oldName}");

        if (nn != oldName && conn.ExecuteScalar<long>(
                "SELECT COUNT(1) FROM expense_cat WHERE expense_type = @t", new { t = nn }) > 0)
            throw new ExpenseCatException($"费用类型已存在：{nn}");

        conn.Execute("UPDATE expense_cat SET expense_type = @n WHERE expense_type = @o",
            new { n = nn, o = oldName });
        conn.Execute("UPDATE expense_ledger SET expense_type = @n WHERE expense_type = @o",
            new { n = nn, o = oldName });
    }

    /// <summary>改归类（新分类非法时并入兜底类）。不 Commit。</summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    /// <param name="expenseType">类型名。</param>
    /// <param name="category">新分类。</param>
    public static void SetCategory(SqliteConnection conn, string expenseType, string category)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        conn.Execute("UPDATE expense_cat SET category = @c WHERE expense_type = @t",
            new { c = NormCat(category), t = expenseType });
    }

    /// <summary>该类型在费用台账中的使用条数（删除前提示用）。纯 SELECT。</summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <param name="expenseType">类型名。</param>
    /// <returns>expense_ledger 中该类型的行数。</returns>
    public static int TypeReferenceCount(SqliteConnection conn, string expenseType)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        return conn.ExecuteScalar<int>(
            "SELECT COUNT(*) FROM expense_ledger WHERE expense_type = @t", new { t = expenseType });
    }

    /// <summary>
    /// 删除类型（<b>仅删配置，不动历史台账</b>）；不存在则报错。不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    /// <param name="expenseType">类型名。</param>
    /// <exception cref="ExpenseCatException">类型不存在。</exception>
    public static void DeleteType(SqliteConnection conn, string expenseType)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        int rows = conn.Execute("DELETE FROM expense_cat WHERE expense_type = @t", new { t = expenseType });
        if (rows == 0) throw new ExpenseCatException($"费用类型不存在：{expenseType}");
    }

    /// <summary>从费用台账同步全部类型到 expense_cat（按默认规则自动归类）。不 Commit。</summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    public static void SyncFromLedger(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        var types = new List<string>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText =
                "SELECT DISTINCT expense_type FROM expense_ledger WHERE expense_type IS NOT NULL";
            using var r = cmd.ExecuteReader();
            while (r.Read()) types.Add(r.IsDBNull(0) ? "" : r.GetString(0));
        }
        EnsureTypes(conn, types);
    }

    // ------------------------------------------------------------------ #
    // 顺序：类内上移/下移、拖拽落库
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 类内上移(-1) / 下移(+1)：只在同分类内交换，跨分类不动。越界或不存在返回 false 且不写。
    /// 写方法；不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    /// <param name="expenseType">要移动的类型名。</param>
    /// <param name="direction">-1 上移，+1 下移。</param>
    /// <returns>确实交换了返回 true。</returns>
    public static bool MoveInCategory(SqliteConnection conn, string expenseType, int direction)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        Dictionary<string, List<string>> byCat = TypesByCategory(conn);
        string cat = CurrentCategory(conn, expenseType);
        if (!byCat.TryGetValue(cat, out List<string>? names)) return false;
        int i = names.IndexOf(expenseType);
        if (i < 0) return false;
        int j = i + direction;
        if (j < 0 || j >= names.Count) return false;

        (names[i], names[j]) = (names[j], names[i]);
        var layout = new Dictionary<string, List<string>>();
        foreach (var kv in byCat) layout[kv.Key] = kv.Value;
        layout[cat] = names;
        WriteLayout(conn, layout);
        return true;
    }

    /// <summary>当前类型所属分类（归一化）；不存在返回兜底类。纯 SELECT。</summary>
    private static string CurrentCategory(SqliteConnection conn, string expenseType)
    {
        string? raw = conn.ExecuteScalar<string?>(
            "SELECT category FROM expense_cat WHERE expense_type = @t", new { t = expenseType });
        return raw is null ? FallbackCategory : NormCat(raw);
    }

    /// <summary>
    /// 拖拽 / 排序后一次性落库：归类 + 类内顺序（分类顺序固定为 <see cref="Categories"/>）。不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接。</param>
    /// <param name="layout">{分类: [类型...]}；未覆盖的类型保持原归类、接到末尾。</param>
    public static void SaveLayout(SqliteConnection conn, IReadOnlyDictionary<string, List<string>> layout)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        WriteLayout(conn, layout);
    }

    /// <summary>按 {分类: [类型...]} 写回 category 与全局 sort_order（从 1 递增）。</summary>
    private static void WriteLayout(SqliteConnection conn, IReadOnlyDictionary<string, List<string>> layout)
    {
        int seq = 0;
        foreach (string cat in Categories)
        {
            if (!layout.TryGetValue(cat, out List<string>? list)) continue;
            foreach (string t in list)
            {
                seq++;
                conn.Execute(
                    "UPDATE expense_cat SET category = @c, sort_order = @so WHERE expense_type = @t",
                    new { c = cat, so = seq, t });
            }
        }

        // 漏网（layout 未覆盖的类型）保持原归类，接到末尾。
        // 先物化查询结果（快照），与 Python 单条 SELECT 游标在事务内一次定序语义一致。
        var rows = new List<string>();
        using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = "SELECT expense_type FROM expense_cat ORDER BY sort_order, expense_type";
            using var r = cmd.ExecuteReader();
            while (r.Read()) rows.Add(r.IsDBNull(0) ? "" : r.GetString(0));
        }
        var covered = new HashSet<string>();
        foreach (List<string> v in layout.Values) foreach (string t in v) covered.Add(t);
        foreach (string t in rows)
        {
            if (covered.Contains(t)) continue;
            seq++;
            conn.Execute("UPDATE expense_cat SET sort_order = @so WHERE expense_type = @t",
                new { so = seq, t });
        }
    }

    // ------------------------------------------------------------------ #
    // 内部工具
    // ------------------------------------------------------------------ #

    /// <summary>按 Unicode 码点计长度（等价 Python <c>len()</c>）。</summary>
    private static int CountCodePoints(string s)
    {
        int count = 0;
        for (int i = 0; i < s.Length; i++)
        {
            if (!char.IsLowSurrogate(s[i])) count++;
        }
        return count;
    }
}
