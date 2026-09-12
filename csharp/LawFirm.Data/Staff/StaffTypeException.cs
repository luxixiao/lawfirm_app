using System;

namespace LawFirm.Data.Staff;

/// <summary>
/// 员工类型 / 员工删除的业务错误（提示给 UI）。
/// 对齐 Python 侧 <c>app/engine/staff_type.py::StaffTypeError</c>。
/// </summary>
public class StaffTypeException : Exception
{
    /// <summary>用中文提示文案构造。</summary>
    /// <param name="message">面向用户的中文提示（原样展示给 UI）。</param>
    public StaffTypeException(string message) : base(message)
    {
    }
}

/// <summary>
/// 员工存在业务数据引用，禁止删除（提示改用「停用 / 启用」）。
/// 对齐 Python 侧 <c>app/engine/staff_type.py::StaffInUseError</c>。
/// </summary>
public sealed class StaffInUseException : StaffTypeException
{
    /// <summary>用中文提示文案构造。</summary>
    /// <param name="message">面向用户的中文提示（含分表明细）。</param>
    public StaffInUseException(string message) : base(message)
    {
    }
}
