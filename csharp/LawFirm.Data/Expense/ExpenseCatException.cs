using System;

namespace LawFirm.Data.Expense;

/// <summary>
/// 费用类型维护的业务错误（提示给 UI）。
/// 对齐 Python 侧 <c>app/engine/expense_cat.py::ExpenseCatError</c>。
/// </summary>
public class ExpenseCatException : Exception
{
    /// <summary>用中文提示文案构造。</summary>
    /// <param name="message">面向用户的中文提示（原样展示给 UI）。</param>
    public ExpenseCatException(string message) : base(message)
    {
    }
}
