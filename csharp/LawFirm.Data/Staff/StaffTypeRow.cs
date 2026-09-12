namespace LawFirm.Data.Staff;

/// <summary>
/// staff_type_def 行 DTO（对齐 Python <c>list_types()</c> 的返回字典）。
///
/// 映射约定：列名含下划线，故 <see cref="StaffTypeService.ListTypes"/> 的 SQL
/// 用 <c>AS</c> 把列别名到与本类属性完全同名（如 <c>sort_order AS SortOrder</c>），
/// 由 Dapper 按属性名匹配，不依赖 <c>[Column]</c> 特性（其在本项目 Dapper 版本下不生效）。
/// </summary>
public class StaffTypeRow
{
    /// <summary>类型名（staff_type_def.name）。</summary>
    public string Name { get; set; } = "";

    /// <summary>是否内置（合伙/聘用/兼职；DB 存 0/1，映射为 bool）。</summary>
    public bool IsBuiltin { get; set; }

    /// <summary>说明（仅内置类型可改）。</summary>
    public string? Note { get; set; }

    /// <summary>排序权重。</summary>
    public int SortOrder { get; set; }

    /// <summary>引用人数（相关子查询 staff_count；仅 <see cref="StaffTypeService.ListTypes"/> 返回时有效）。</summary>
    public int StaffCount { get; set; }
}
