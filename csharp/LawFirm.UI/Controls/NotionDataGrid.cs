using System;
using System.Collections.Specialized;
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using LawFirm.UI.Services;

namespace LawFirm.UI.Controls;

/// <summary>
/// Notion 风表格（规格 §4.5）：封装 C1 冻结列 + C6 悬停全文 + C7 选中保色
/// + C8 像素滚动 + C9 表头底色 + C10 合计行样式钩子。
///
/// 视觉样式经实例级 Style（Themes/NotionControls.xaml 的 NotionDataGridStyle）应用，
/// 行为属性在构造器锁定（不可在 XAML 漏项）。
/// 两级表头经 attached 属性 TwoTierHeaderGrid.IsEnabled/GroupLabel/IsLead 声明（§4.3）。
/// 列布局持久化经 StatePage/StateName + ColumnStateStore（§4.4）。
/// </summary>
public class NotionDataGrid : DataGrid
{
    public NotionDataGrid()
    {
        Columns.CollectionChanged += OnColumnsCollectionChanged;
        CanUserAddRows = false;
        CanUserDeleteRows = false;
        CanUserResizeRows = false;
        IsReadOnly = true;
        HeadersVisibility = DataGridHeadersVisibility.Column;
        AutoGenerateColumns = false;
        SelectionUnit = DataGridSelectionUnit.FullRow;      // setSelectionBehavior(SelectRows)
        SelectionMode = DataGridSelectionMode.Single;
        GridLinesVisibility = DataGridGridLinesVisibility.Horizontal;
        HorizontalGridLinesBrush = TryGetBrush("GridLine") ?? Brushes.LightGray;
        // ★ C8：像素滚动 + 虚拟化并存（不可 CanContentScroll=False，那会关闭虚拟化）
        SetValue(ScrollViewer.CanContentScrollProperty, true);
        VirtualizingStackPanel.SetScrollUnit(this, ScrollUnit.Pixel);
        VirtualizingStackPanel.SetIsVirtualizing(this, true);
        VirtualizingStackPanel.SetVirtualizationMode(this, VirtualizationMode.Recycling);
        EnableRowVirtualization = true;
        EnableColumnVirtualization = false;   // 列少；开启反而致 header 错位
        ColumnHeaderHeight = 34;              // 两级表头时设 44（§4.3）
        RowHeight = 34;                       // table_view.py:289
        FrozenColumnCount = 1;                // 默认冻结首列（C1）
    }

    private static Brush? TryGetBrush(string key)
        => Application.Current?.TryFindResource(key) as Brush;

    // ---- 列布局持久化身份（§4.4：键 colstate/{page}/{name} → ui-state.json 的 "{page}/{name}"） ----

    /// <summary>页面标识（如 "settlement"）；null = 不持久化。</summary>
    public string? StatePage { get; set; }

    /// <summary>表名（如 "personal"/"report"/"staff_income"/"invoice_income"）。</summary>
    public string StateName { get; set; } = "main";

    // ---- 列宽变化事件（QA B2） ----
    //
    // WPF DataGrid 没有 ColumnWidthChanged 事件，且 DataGridColumn 并不实现
    // INotifyPropertyChanged（CS1061 实测）。改用 DependencyPropertyDescriptor
    // 监听 WidthProperty（用户拖动/程序化设置 Width 都会走该 DP）；
    // 列增删时经 Columns.CollectionChanged 自动重新挂钩。

    /// <summary>任一列宽度变化时触发（用户拖宽 / 程序化设置 Width）。</summary>
    public event EventHandler? ColumnWidthChanged;

    private readonly HashSet<DataGridColumn> _widthHooked = new();

    /// <summary>列集合变化（Add/Remove/Reset）→ 重新挂钩逐列 PropertyChanged。</summary>
    /// <remarks>
    /// DataGrid 没有可重写的 OnColumnsChanged 虚方法（QA 后实测 CS0115）；
    /// Columns 是 ObservableCollection，构造器里订阅 CollectionChanged 等价实现。
    /// </remarks>
    private void OnColumnsCollectionChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        if (e.Action == NotifyCollectionChangedAction.Reset)
        {
            _widthHooked.Clear();
            foreach (var col in Columns) HookColumnWidth(col);
            return;
        }
        if (e.OldItems is not null)
        {
            foreach (var item in e.OldItems)
                if (item is DataGridColumn removed) UnhookColumnWidth(removed);
        }
        if (e.NewItems is not null)
        {
            foreach (var item in e.NewItems)
                if (item is DataGridColumn added) HookColumnWidth(added);
        }
    }

    private void HookColumnWidth(DataGridColumn col)
    {
        if (_widthHooked.Add(col))
        {
            var dpd = System.ComponentModel.DependencyPropertyDescriptor
                .FromProperty(DataGridColumn.WidthProperty, typeof(DataGridColumn));
            dpd.AddValueChanged(col, OnColumnWidthChanged);
        }
    }

    private void UnhookColumnWidth(DataGridColumn col)
    {
        if (_widthHooked.Remove(col))
        {
            var dpd = System.ComponentModel.DependencyPropertyDescriptor
                .FromProperty(DataGridColumn.WidthProperty, typeof(DataGridColumn));
            dpd.RemoveValueChanged(col, OnColumnWidthChanged);
        }
    }

    private void OnColumnWidthChanged(object? sender, EventArgs e)
        => ColumnWidthChanged?.Invoke(this, EventArgs.Empty);

    /// <summary>列身份 = 稳定标题字符串（列集合不一致即整份丢弃，column_layout.py:64-65）。</summary>
    public IReadOnlyList<string> ColumnKeys()
    {
        var keys = new string[Columns.Count];
        foreach (var col in Columns)
        {
            if (col.DisplayIndex >= 0 && col.DisplayIndex < keys.Length)
                keys[col.DisplayIndex] = col.Header as string ?? $"col{col.DisplayIndex}";
        }
        return keys;
    }

    /// <summary>应用列状态（顺序/显隐/冻结/宽度）。列身份以 Header 字符串匹配。</summary>
    public void ApplyColumnState(ColumnState state)
    {
        if (state is null) return;
        // 1) 顺序：按 state.Order 重排 DisplayIndex（未出现的列排在后面）
        var ordered = new List<DataGridColumn>();
        foreach (var key in state.Order)
        {
            var col = FindByKey(key);
            if (col is not null) ordered.Add(col);
        }
        foreach (var col in Columns)
        {
            if (!ordered.Contains(col)) ordered.Add(col);
        }
        for (int i = 0; i < ordered.Count; i++)
        {
            if (ordered[i].DisplayIndex != i) ordered[i].DisplayIndex = i;
        }
        // 2) 显隐 + 3) 冻结 + 4) 宽度
        foreach (var col in Columns)
        {
            string key = col.Header as string ?? string.Empty;
            if (state.Visible.TryGetValue(key, out bool visi)) col.Visibility = visi ? Visibility.Visible : Visibility.Collapsed;
            // 冻结语义（面板语义，column_layout.py:416-438）：DataGrid.FrozenColumnCount
            // 恒为「最左 N 列」，这里由调用方把 frozen 集合折算成 N。
            if (state.Widths.TryGetValue(key, out double w) && w > 0)
                col.Width = new DataGridLength(w, DataGridLengthUnitType.Pixel);
        }
        int frozenCount = 0;
        for (int i = 0; i < Columns.Count; i++)
        {
            var col = ColumnAtDisplayIndex(i);
            if (col is null) continue;
            // 无存档时默认冻结首列（NotionDataGrid 构造器语义：FrozenColumnCount=1）
            bool frozen = state.Frozen.TryGetValue(col.Header as string ?? string.Empty, out bool f)
                ? f : i == 0;
            if (frozen) frozenCount = i + 1;    // 冻结列必须是最左连续段
        }
        FrozenColumnCount = frozenCount;
    }

    /// <summary>捕获当前列状态（拖宽/重排/显隐/冻结后保存）。</summary>
    public ColumnState CaptureColumnState()
    {
        var state = new ColumnState
        {
            Order = new List<string>(),
            Visible = new Dictionary<string, bool>(),
            Frozen = new Dictionary<string, bool>(),
            Widths = new Dictionary<string, double>(),
        };
        for (int i = 0; i < Columns.Count; i++)
        {
            var col = ColumnAtDisplayIndex(i);
            if (col is null) continue;
            string key = col.Header as string ?? $"col{i}";
            state.Order.Add(key);
            state.Visible[key] = col.Visibility == Visibility.Visible;
            state.Widths[key] = col.ActualWidth > 0 ? Math.Round(col.ActualWidth) : 0;
        }
        for (int i = 0; i < FrozenColumnCount && i < Columns.Count; i++)
        {
            var col = ColumnAtDisplayIndex(i);
            if (col is not null) state.Frozen[col.Header as string ?? $"col{i}"] = true;
        }
        return state;
    }

    private DataGridColumn? ColumnAtDisplayIndex(int index)
    {
        foreach (var col in Columns)
        {
            if (col.DisplayIndex == index) return col;
        }
        return null;
    }

    private DataGridColumn? FindByKey(string key)
    {
        foreach (var col in Columns)
        {
            if ((col.Header as string) == key) return col;
        }
        return null;
    }
}
