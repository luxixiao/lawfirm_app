using System.Collections.Generic;

namespace LawFirm.Data.Staff;

/// <summary>
/// 员工在各业务表中的引用条数（对齐 Python <c>staff_reference_count</c> 的返回字典）。
///
/// <para>字段含义：</para>
/// <list type="bullet">
///   <item><see cref="ChargeDetail"/>：charge_detail.person_name 精确命中数。</item>
///   <item><see cref="ExpenseLedger"/>：expense_ledger.actual_handler 精确命中数。</item>
///   <item><see cref="RawSalary"/>：raw_salary.staff_name 精确命中数。</item>
///   <item><see cref="Collection"/>：经发票关联 charge_detail 反映的分摊收款条数
///     （collection.person_name 留空表示「未归因收款」，故不按人名直取）。</item>
///   <item><see cref="Total"/>：以上四项之和。</item>
/// </list>
/// </summary>
public sealed class StaffReferenceCount
{
    /// <summary>构造引用计数（total 自动求和）。</summary>
    /// <param name="chargeDetail">charge_detail 命中数。</param>
    /// <param name="expenseLedger">expense_ledger 命中数。</param>
    /// <param name="rawSalary">raw_salary 命中数。</param>
    /// <param name="collection">collection（经发票关联）命中数。</param>
    public StaffReferenceCount(int chargeDetail, int expenseLedger, int rawSalary, int collection)
    {
        ChargeDetail = chargeDetail;
        ExpenseLedger = expenseLedger;
        RawSalary = rawSalary;
        Collection = collection;
        Total = chargeDetail + expenseLedger + rawSalary + collection;
    }

    /// <summary>charge_detail 引用条数。</summary>
    public int ChargeDetail { get; }

    /// <summary>expense_ledger 引用条数。</summary>
    public int ExpenseLedger { get; }

    /// <summary>raw_salary 引用条数。</summary>
    public int RawSalary { get; }

    /// <summary>collection（经发票关联）引用条数。</summary>
    public int Collection { get; }

    /// <summary>引用总条数（四项之和）。</summary>
    public int Total { get; }

    /// <summary>
    /// 非零引用表，按 Python dict 的键序输出：
    /// charge_detail → expense_ledger → raw_salary → collection。
    /// 供 <see cref="StaffTypeService.DeleteStaff"/> 拼装「{表} {条数} 条」明细。
    /// </summary>
    /// <returns>非零项的 (表名, 条数) 有序列表。</returns>
    public IReadOnlyList<(string Table, int Count)> NonZeroInOrder()
    {
        var list = new List<(string Table, int Count)>(4);
        if (ChargeDetail > 0) list.Add(("charge_detail", ChargeDetail));
        if (ExpenseLedger > 0) list.Add(("expense_ledger", ExpenseLedger));
        if (RawSalary > 0) list.Add(("raw_salary", RawSalary));
        if (Collection > 0) list.Add(("collection", Collection));
        return list;
    }
}
