using System.Collections.Generic;

namespace LawFirm.UI.ViewModels;

/// <summary>
/// 通用表格行模型（T5.3 各 tab 共用）：字符串索引器供 DataGrid 列绑定 <c>[key]</c>，
/// IsBold / IsTotal 供行样式触发（分类行加粗；合计行加粗+灰底，C10）。
/// </summary>
public sealed class GridRow
{
    public GridRow(IReadOnlyDictionary<string, object?> cells, bool isBold = false, bool isTotal = false)
    {
        Cells = cells;
        IsBold = isBold;
        IsTotal = isTotal;
    }

    public bool IsBold { get; }
    public bool IsTotal { get; }
    public Dictionary<string, object?> Cells { get; }

    /// <summary>列值访问器（WPF 绑定路径 [key] 走这里）。</summary>
    public object? this[string key] => Cells.TryGetValue(key, out var v) ? v : null;
}
