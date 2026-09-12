using System;
using System.Collections.Generic;
using System.Linq;
using Dapper;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data.Staff;

/// <summary>
/// 员工花名册 CRUD（<c>app/ui/staff_view.py</c> 中员工名单相关 SQL 的移植；Python 无 <c>app/engine/staff.py</c>）。
///
/// <para>连接约定：所有方法以调用方传入的 <see cref="SqliteConnection"/> 为第一参数，<b>绝不自建连接</b>；
/// 写操作须运行在 <see cref="WriteGuard.Execute"/> 的事务内（本类不 Commit）。
/// 删除委托给 <see cref="StaffTypeService.DeleteStaff"/>（含业务数据引用检查）。</para>
///
/// <para>与 Python 的差异：Python 在 UI 层先 <c>.strip()</c> 再落库；此处把去空白下沉到本层，
/// 使落库结果与 Python 完全一致（name / hire_month / note 去空白；staff_type 原样）。</para>
/// </summary>
public static class StaffService
{
    /// <summary>
    /// 员工名单：姓名 / 类型 / 状态 / 入职月份 / 备注，按 is_active DESC, name 排序。
    /// <b>纯 SELECT</b>。
    /// </summary>
    /// <param name="conn">调用方持有的连接（可为只读）。</param>
    /// <returns>员工行列表。</returns>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static List<StaffRow> ListStaff(SqliteConnection conn)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        const string sql =
            "SELECT name AS Name, staff_type AS StaffType, is_active AS IsActive, " +
            "       hire_month AS HireMonth, note AS Note FROM staff " +
            "ORDER BY is_active DESC, name";
        return conn.Query<StaffRow>(sql).ToList();
    }

    /// <summary>
    /// 手动添加员工：<c>INSERT OR IGNORE</c>（重名不报错、不改动既有行）。
    /// name / hireMonth / note 去空白；staff_type 原样；is_active=1、source='manual'。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">姓名（去空白）。</param>
    /// <param name="staffType">员工类型（原样）。</param>
    /// <param name="hireMonth">入职月份（去空白，可空）。</param>
    /// <param name="note">备注（去空白，可空）。</param>
    /// <returns>确实插入了新行返回 true；重名被忽略（rowcount=0）返回 false。</returns>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static bool AddManual(SqliteConnection conn, string name, string staffType,
        string hireMonth, string note)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        int rows = conn.Execute(
            "INSERT OR IGNORE INTO staff (name, staff_type, is_active, hire_month, note, source) " +
            "VALUES (@name, @staffType, 1, @hireMonth, @note, 'manual')",
            new
            {
                name = (name ?? "").Trim(),
                staffType,
                hireMonth = (hireMonth ?? "").Trim(),
                note = (note ?? "").Trim(),
            });
        return rows > 0;
    }

    /// <summary>
    /// 编辑员工：更新类型 / 入职月份 / 备注。hire_month 与 note 去空白且空值归 ""（对应 Python
    /// <c>text().strip() or ""</c>）。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">姓名（定位条件，原样）。</param>
    /// <param name="staffType">新员工类型（原样）。</param>
    /// <param name="hireMonth">新入职月份（去空白，空归 ""）。</param>
    /// <param name="note">新备注（去空白）。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void Update(SqliteConnection conn, string name, string staffType,
        string hireMonth, string note)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        string hire = (hireMonth ?? "").Trim();
        conn.Execute(
            "UPDATE staff SET staff_type = @staffType, hire_month = @hireMonth, note = @note " +
            "WHERE name = @name",
            new { staffType, hireMonth = hire, note = (note ?? "").Trim(), name });
    }

    /// <summary>
    /// 停用 / 启用切换：<c>is_active = 1 - is_active</c>。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">姓名。</param>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void ToggleActive(SqliteConnection conn, string name)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        conn.Execute("UPDATE staff SET is_active = 1 - is_active WHERE name = @name", new { name });
    }

    /// <summary>
    /// 删除员工：委托给 <see cref="StaffTypeService.DeleteStaff"/>（存在业务数据引用时抛
    /// <see cref="StaffInUseException"/>）。本方法不 Commit。
    /// </summary>
    /// <param name="conn">调用方持有的可写连接（应为写闸门内的事务连接）。</param>
    /// <param name="name">姓名。</param>
    /// <exception cref="StaffInUseException">存在业务数据引用，禁止删除。</exception>
    /// <exception cref="StaffTypeException">员工不存在。</exception>
    /// <exception cref="ArgumentNullException">conn 为 null。</exception>
    public static void Delete(SqliteConnection conn, string name)
    {
        if (conn is null) throw new ArgumentNullException(nameof(conn));

        StaffTypeService.DeleteStaff(conn, name);
    }
}
