using System;
using System.Collections.Generic;
using System.Linq;
using Dapper;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data.Staff;

/// <summary>
/// 员工类型维护 + 员工删除引用检查（<c>app/engine/staff_type.py</c> 的逐个方法移植）。
///
/// <para>口径（已与需求方确认，与 Python 完全一致）：</para>
/// <list type="bullet">
///   <item>只有 合伙 / 聘用 / 兼职 三类参与业务收入计算，故为内置（is_builtin=1）：禁止删除、禁止改名（改名会断结算口径），说明可改。</item>
///   <item>自定义类型仅作身份标签，不参与业务收入计算，可自由增删改名。</item>
///   <item>自定义类型名若含「合伙 / 聘用 / 兼职」也会被判定参与计算（<see cref="IsComputable"/> 用子串匹配），属预期行为。</item>
/// </list>
///
/// <para>连接约定（与 Python 一致，见 <see cref="DbConnection"/>）：</para>
/// <list type="bullet">
///   <item>所有方法以调用方传入的 <see cref="SqliteConnection"/> 为第一参数，<b>绝不自建连接</b>；写操作必须运行在
///     <see cref="WriteGuard.Execute"/> 的事务内（本类不自行 Commit——事务由写闸门提交）。</item>
///   <item><b>读/写分离（本次决策）</b>：<see cref="ListTypes"/> / <see cref="GetType"/> 为纯 SELECT，
///     <b>绝不</b>调用 <see cref="EnsureDefaults"/>（Python 的 list_types 会补齐内置类型，此处刻意不复刻）。</item>
///   <item><see cref="EnsureDefaults"/> / <see cref="EnsureTypes"/> 为显式的幂等维护写，由引导流程调用一次，
///     仍需经写闸门串行化。</item>
/// </list>
/// </summary>
public static class StaffTypeService
{
    /// <summary>内置三类：参与结算计算，锁定删除与改名。</summary>
    public static readonly string[] BuiltinTypes = { "合伙", "聘用", "兼职" };

    /// <summary>预置但可删可改名的常见类型。</summary>
    public static readonly string[] DefaultExtra = { "挂靠", "其他" };

    /// <summary>与 person_settlement._staff_type_orig 保持一致的口径关键词（顺序敏感）。</summary>
    public static readonly string[] ComputeKeywords = { "合伙", "兼职", "聘用" };

    // ------------------------------------------------------------------ #
    // 引导 / 幂等维护写
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 建库 / 升级后补齐：内置三类 + 预置的挂靠 / 其他（缺哪个补哪个）。
    /// 幂等维护写，经写闸门调用；本方法不 Commit（由 <see cref="WriteGuard"/> 事务提交）。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void EnsureDefaults(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        const string sql =
            "INSERT OR IGNORE INTO staff_type_def(name, is_builtin, note, sort_order) " +
            "VALUES (@name, @builtin, @note, @sortOrder)";

        for (int i = 0; i < BuiltinTypes.Length; i++)
        {
            conn.Execute(sql, new { name = BuiltinTypes[i], builtin = 1, note = "", sortOrder = i + 1 });
        }

        int baseN = BuiltinTypes.Length;
        for (int j = 0; j < DefaultExtra.Length; j++)
        {
            conn.Execute(sql, new { name = DefaultExtra[j], builtin = 0, note = "", sortOrder = baseN + j + 1 });
        }
    }

    /// <summary>
    /// 导入职工清单时调用：把未知类型自动入库（is_builtin=0）。
    /// 空名单直接返回；每个名字去空白，空名跳过。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="names">待补齐的类型名集合；null 视为空。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void EnsureTypes(SqliteConnection conn, IEnumerable<string>? names)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));
        if (names is null) return;

        const string sql =
            "INSERT OR IGNORE INTO staff_type_def(name, is_builtin, note) VALUES (@name, 0, '')";
        foreach (string raw in names)
        {
            string name = (raw ?? "").Trim();
            if (name.Length == 0) continue;
            conn.Execute(sql, new { name });
        }
    }

    // ------------------------------------------------------------------ #
    // 类型查询（纯读）
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 类型清单：名称 / 是否内置 / 说明 / 排序 / 引用人数。
    /// <b>纯 SELECT</b>——不触发任何写入，也不补齐内置类型（交付约定：读路径绝无写入）。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <returns>按 sort_order, name 排序的类型列表。</returns>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static List<StaffTypeRow> ListTypes(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        const string sql =
            "SELECT t.name AS Name, t.is_builtin AS IsBuiltin, t.note AS Note, " +
            "       t.sort_order AS SortOrder, " +
            "       (SELECT COUNT(*) FROM staff s WHERE s.staff_type = t.name) AS StaffCount " +
            "FROM staff_type_def t " +
            "ORDER BY t.sort_order, t.name";
        return conn.Query<StaffTypeRow>(sql).ToList();
    }

    /// <summary>
    /// 取单个类型定义（<c>SELECT *</c>）；不存在返回 null。纯 SELECT。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <param name="name">类型名。</param>
    /// <returns>命中的行；无则 null。</returns>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static StaffTypeRow? GetType(SqliteConnection conn, string name)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        const string sql =
            "SELECT name AS Name, is_builtin AS IsBuiltin, note AS Note, sort_order AS SortOrder " +
            "FROM staff_type_def WHERE name = @name";
        return conn.QueryFirstOrDefault<StaffTypeRow>(sql, new { name });
    }

    // ------------------------------------------------------------------ #
    // 口径判定（无 db）
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 该类型是否参与业务收入计算（口径同 person_settlement）：名字去空白后，
    /// 只要包含「合伙 / 兼职 / 聘用」任一关键词即参与（故「合伙人助理」也参与）。
    /// </summary>
    /// <param name="name">类型名；null / 空白按不参与处理。</param>
    /// <returns>参与计算返回 true。</returns>
    public static bool IsComputable(string? name)
    {
        string n = (name ?? "").Trim();
        foreach (string k in ComputeKeywords)
        {
            if (n.Contains(k, StringComparison.Ordinal)) return true;
        }
        return false;
    }

    // ------------------------------------------------------------------ #
    // 类型增删改（写；须经写闸门）
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 新增自定义类型。校验顺序与文案与 Python 完全一致：
    /// 去空白 → 空名报错 → 超长（&gt;20 字符）报错 → 重名报错 → insert（is_builtin=0，
    /// sort_order = COALESCE(MAX(sort_order),0)+1）。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">类型名（内部去空白）。</param>
    /// <param name="note">说明（内部去空白）。</param>
    /// <exception cref="StaffTypeException">名非法 / 超长 / 重名。</exception>
    public static void AddType(SqliteConnection conn, string name, string note = "")
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        name = (name ?? "").Trim();
        if (name.Length == 0) throw new StaffTypeException("类型名不能为空");
        if (CountCodePoints(name) > 20) throw new StaffTypeException("类型名过长（≤20 字符）");

        bool dup = conn.ExecuteScalar<long>(
            "SELECT COUNT(1) FROM staff_type_def WHERE name = @name", new { name }) > 0;
        if (dup) throw new StaffTypeException($"类型已存在：{name}");

        long next = conn.ExecuteScalar<long>(
            "SELECT COALESCE(MAX(sort_order),0)+1 FROM staff_type_def");

        conn.Execute(
            "INSERT INTO staff_type_def(name, is_builtin, note, sort_order) " +
            "VALUES (@name, 0, @note, @sortOrder)",
            new { name, note = (note ?? "").Trim(), sortOrder = next });
    }

    /// <summary>
    /// 改名：内置三类禁止；新名重复则报错；同步更新 staff_type_def.name <b>与</b> staff.staff_type。
    /// 校验顺序 / 文案与 Python 一致（仅对新名去空白，旧名原样）。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="oldName">原类型名。</param>
    /// <param name="newName">新类型名（内部去空白）。</param>
    /// <exception cref="StaffTypeException">新名非法 / 旧名不存在 / 内置禁改 / 新名重复。</exception>
    public static void RenameType(SqliteConnection conn, string oldName, string newName)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        newName = (newName ?? "").Trim();
        if (newName.Length == 0) throw new StaffTypeException("类型名不能为空");

        int? builtin = conn.ExecuteScalar<int?>(
            "SELECT is_builtin FROM staff_type_def WHERE name = @oldName", new { oldName });
        if (builtin is null) throw new StaffTypeException($"类型不存在：{oldName}");
        if (builtin.Value != 0)
            throw new StaffTypeException($"「{oldName}」是内置结算类型，禁止改名（改名会断结算口径）");

        bool dup = conn.ExecuteScalar<long>(
            "SELECT COUNT(1) FROM staff_type_def WHERE name = @newName", new { newName }) > 0;
        if (dup) throw new StaffTypeException($"类型已存在：{newName}");

        conn.Execute("UPDATE staff_type_def SET name = @newName WHERE name = @oldName",
            new { newName, oldName });
        conn.Execute("UPDATE staff SET staff_type = @newName WHERE staff_type = @oldName",
            new { newName, oldName });
    }

    /// <summary>
    /// 更新类型说明（说明去空白）。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">类型名。</param>
    /// <param name="note">新说明（内部去空白）。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void SetNote(SqliteConnection conn, string name, string note)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        conn.Execute("UPDATE staff_type_def SET note = @note WHERE name = @name",
            new { note = (note ?? "").Trim(), name });
    }

    /// <summary>
    /// 删除类型：内置三类禁止；有员工在用则禁止（避免员工身份丢失）。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">类型名。</param>
    /// <exception cref="StaffTypeException">不存在 / 内置禁删 / 仍有员工在用。</exception>
    public static void DeleteType(SqliteConnection conn, string name)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        int? builtin = conn.ExecuteScalar<int?>(
            "SELECT is_builtin FROM staff_type_def WHERE name = @name", new { name });
        if (builtin is null) throw new StaffTypeException($"类型不存在：{name}");
        if (builtin.Value != 0) throw new StaffTypeException($"「{name}」是内置结算类型，禁止删除");

        long inUse = conn.ExecuteScalar<long>(
            "SELECT COUNT(*) FROM staff WHERE staff_type = @name", new { name });
        if (inUse > 0)
            throw new StaffTypeException($"还有 {inUse} 名员工属于该类型，请先把他们改成别的类型再删除");

        conn.Execute("DELETE FROM staff_type_def WHERE name = @name", new { name });
    }

    /// <summary>
    /// 上移(-1) / 下移(+1)：交换相邻两项后重写<b>全部</b> sort_order（= 序号+1），保证顺序稳定。
    /// 名字不存在或越界时静默无操作。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">要移动的类型名。</param>
    /// <param name="direction">-1 上移，+1 下移。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void MoveType(SqliteConnection conn, string name, int direction)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        List<string> names = conn.Query<string>(
            "SELECT name FROM staff_type_def ORDER BY sort_order, name").ToList();
        int i = names.IndexOf(name);
        if (i < 0) return;
        int j = i + direction;
        if (j < 0 || j >= names.Count) return;

        (names[i], names[j]) = (names[j], names[i]);
        for (int idx = 0; idx < names.Count; idx++)
        {
            conn.Execute("UPDATE staff_type_def SET sort_order = @so WHERE name = @name",
                new { so = idx + 1, name = names[idx] });
        }
    }

    // ------------------------------------------------------------------ #
    // 员工删除与引用检查
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 该员工在各业务表中的引用条数（collection 含未归因的分摊收款——经发票关联 charge_detail）。
    /// 纯 SELECT。表 / 列名均为常量（无注入面）。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <param name="name">员工姓名。</param>
    /// <returns>各表引用数与总数。</returns>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static StaffReferenceCount StaffReferenceCount(SqliteConnection conn, string name)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        int chargeDetail = conn.ExecuteScalar<int>(
            "SELECT COUNT(*) FROM charge_detail WHERE person_name = @name", new { name });
        int expenseLedger = conn.ExecuteScalar<int>(
            "SELECT COUNT(*) FROM expense_ledger WHERE actual_handler = @name", new { name });
        int rawSalary = conn.ExecuteScalar<int>(
            "SELECT COUNT(*) FROM raw_salary WHERE staff_name = @name", new { name });
        // collection.person_name 留空表示「未归因收款」，须经发票关联 charge_detail 才反映真实引用。
        int collection = conn.ExecuteScalar<int>(
            "SELECT COUNT(*) FROM collection c " +
            "JOIN charge_detail cd ON cd.invoice_no = c.invoice_no " +
            "WHERE cd.person_name = @name", new { name });

        return new StaffReferenceCount(chargeDetail, expenseLedger, rawSalary, collection);
    }

    /// <summary>
    /// 删除员工；有业务数据引用则抛 <see cref="StaffInUseException"/>（建议改用停用）；
    /// 无引用但员工不存在则抛 <see cref="StaffTypeException"/>。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">员工姓名。</param>
    /// <exception cref="StaffInUseException">存在业务数据引用，禁止删除。</exception>
    /// <exception cref="StaffTypeException">员工不存在。</exception>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void DeleteStaff(SqliteConnection conn, string name)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        StaffReferenceCount refs = StaffReferenceCount(conn, name);
        if (refs.Total > 0)
        {
            string detail = string.Join("、",
                refs.NonZeroInOrder().Select(x => $"{x.Table} {x.Count} 条"));
            throw new StaffInUseException(
                $"「{name}」在业务数据中有引用（{detail}）。\n"
                + "直接删除会让结算表查不到其身份、业务收入按 0 计。\n"
                + "如该员工已离职，请改用「停用 / 启用」。");
        }

        int rows = conn.Execute("DELETE FROM staff WHERE name = @name", new { name });
        if (rows == 0) throw new StaffTypeException($"员工不存在：{name}");
    }

    // ------------------------------------------------------------------ #
    // 内部工具
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 按 Unicode 码点计长度（等价于 Python <c>len()</c>，而非 UTF-16 码元数）：
    /// 代理对（BMP 外字符）按 1 计，避免与 Python 的 ≤20 校验口径不一致。
    /// </summary>
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
