using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using LawFirm.UI.Controls;
using LawFirm.UI.Services;
using LawFirm.UI.ViewModels;

namespace LawFirm.UI.Views.Settlement;

/// <summary>
/// Tab1 个人结算总表：动态列重建（月份模式 14 列 ↔ 1~mo 月列模式）
/// + 列布局持久化（StatePage=settlement / StateName=personal）。
///
/// 持久化时机（对齐 Python column_layout.py 语义）：
/// - VM 列集合变化 → 重建列 → **读取**存档并应用（列集合不一致时 Store 自动丢弃回退默认）；
/// - 用户手动拖宽 / 重排 → **写入**存档。程序化重建不写存档（防默认布局覆盖用户偏好）。
/// </summary>
public partial class PersonalSettlementTab : UserControl
{
    private PersonalSettlementViewModel? _vm;
    private readonly ColumnStateStore _store = new();
    private bool _applying;   // 程序化改列宽期间屏蔽写存档（对应 Python _applying）

    public PersonalSettlementTab()
    {
        InitializeComponent();
        DataContextChanged += OnDataContextChanged;
        Grid.RowStyle = GridColumnFactory.BoldRowStyle();
        Grid.ColumnWidthChanged += (_, _) => SaveColumnState();
        Grid.ColumnReordered += (_, _) => SaveColumnState();
    }

    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (_vm is not null) _vm.ColumnsChanged -= OnColumnsChanged;
        _vm = e.NewValue as PersonalSettlementViewModel;
        if (_vm is not null) _vm.ColumnsChanged += OnColumnsChanged;
    }

    private void OnColumnsChanged(IReadOnlyList<string> keys)
    {
        _applying = true;
        try
        {
            RebuildColumns(keys);
            LoadColumnState();
        }
        finally
        {
            _applying = false;
        }
    }

    private void RebuildColumns(IReadOnlyList<string> keys)
    {
        Grid.Columns.Clear();
        foreach (var key in keys)
        {
            bool numeric = key != "项目";
            // 冻结列（项目）灰底；数字列右对齐 N2 + 负数红（GridColumnFactory）
            Grid.Columns.Add(GridColumnFactory.TextColumn(key, numeric, frozen: key == "项目"));
        }
    }

    // ---- 列布局持久化（C3） ----

    private void LoadColumnState()
    {
        var keys = Grid.ColumnKeys();
        if (keys.Count == 0) return;
        var state = _store.Load("settlement", "personal", keys);
        Grid.ApplyColumnState(state);
    }

    private void SaveColumnState()
    {
        if (_applying) return;   // 重建触发的 resize 不是用户行为，不写存档
        _store.Save("settlement", "personal", Grid.CaptureColumnState());
    }
}
