namespace LawFirm.Data.Staff;

/// <summary>
/// 职工清单导入行（对齐 Python <c>parse_staff_file</c> 的 (name, staff_type, note) 三元组）。
/// </summary>
/// <param name="Name">姓名（已去空白；解析保证非空）。</param>
/// <param name="StaffType">类型（可为 ""，导入时兜底为「聘用」）。</param>
/// <param name="Note">备注（可为 ""）。</param>
public sealed record StaffImportRow(string Name, string StaffType, string Note);
