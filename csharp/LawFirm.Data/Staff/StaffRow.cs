namespace LawFirm.Data.Staff;

/// <summary>
/// staff 表行 DTO（对齐 Python <c>staff_view._refresh_staff</c> 的选列）。
///
/// 映射约定：列名含下划线，故 <see cref="StaffService.ListStaff"/> 的 SQL 用 <c>AS</c>
/// 把列别名到与本类属性完全同名（如 <c>staff_type AS StaffType</c>），由 Dapper 按属性名
/// 匹配，不依赖 <c>[Column]</c> 特性（其在本项目 Dapper 版本下不生效）。
/// 日期类文本（hire_month，形如 "2025-04"）按 string 映射，避免强类型 DateTime 解析异常。
/// </summary>
public class StaffRow
{
    /// <summary>姓名（staff.name）。</summary>
    public string Name { get; set; } = "";

    /// <summary>员工类型（staff.staff_type）。</summary>
    public string StaffType { get; set; } = "";

    /// <summary>是否在职（DB 存 0/1，映射为 bool）。</summary>
    public bool IsActive { get; set; }

    /// <summary>入职月份文本（staff.hire_month，如 "2025-04"；空表示始终在名单）。</summary>
    public string? HireMonth { get; set; }

    /// <summary>备注（staff.note）。</summary>
    public string? Note { get; set; }
}
