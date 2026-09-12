namespace LawFirm.Data.Expense;

/// <summary>
/// expense_category 分类行 DTO（对齐 Python <c>list_categories()</c> 的返回字典）。
///
/// 映射约定：SQL 用 <c>AS &lt;属性名&gt;</c> 别名（本项目 Dapper 不读 DataAnnotations 的
/// <c>[Column]</c>，见 ExpenseCatService 注释），故本类不使用特性。
/// </summary>
public class ExpenseCategoryRow
{
    /// <summary>分类名（固定 5 类之一）。</summary>
    public string Name { get; set; } = "";

    /// <summary>分类说明（可自定义）。</summary>
    public string? Note { get; set; }

    /// <summary>分类排序权重。</summary>
    public int SortOrder { get; set; }

    /// <summary>该分类下的费用类型数（脏数据已并入兜底分类）。</summary>
    public int Count { get; set; }

    /// <summary>构造。</summary>
    /// <param name="name">分类名。</param>
    /// <param name="note">说明。</param>
    /// <param name="sortOrder">排序权重。</param>
    /// <param name="count">该类类型数。</param>
    public ExpenseCategoryRow(string name, string? note, int sortOrder, int count)
    {
        Name = name;
        Note = note;
        SortOrder = sortOrder;
        Count = count;
    }
}
