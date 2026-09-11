using System.Collections.Generic;
using System.Linq;
using System.Windows;

namespace LawFirm.UI.Dialogs;

/// <summary>
/// 多选经办人弹窗（对应 gen_selected 的 QDialog；settlement_view.py:365-405）。
/// 用法：var selected = ExportScopeDialog.PickPersons(names);（null = 用户取消）
/// </summary>
public partial class ExportScopeDialog : Window
{
    public sealed class Item
    {
        public Item(string name) { Name = name; }
        public string Name { get; }
        public bool IsChecked { get; set; }
    }

    public ExportScopeDialog(IReadOnlyList<string> names)
    {
        InitializeComponent();
        Owner = Application.Current.MainWindow;
        WindowStartupLocation = Owner is null
            ? WindowStartupLocation.CenterScreen
            : WindowStartupLocation.CenterOwner;
        Tip = $"共 {names.Count} 人，勾选要导出的（可多选）：";
        Items = names.Select(n => new Item(n)).ToList();
        DataContext = this;
    }

    public string Tip { get; }
    public List<Item> Items { get; }

    /// <summary>弹窗选择；返回选中名单（null = 取消）。</summary>
    public static List<string>? PickPersons(IReadOnlyList<string> names)
    {
        var dlg = new ExportScopeDialog(names);
        return dlg.ShowDialog() == true
            ? dlg.Items.Where(i => i.IsChecked).Select(i => i.Name).ToList()
            : null;
    }

    private void OnToggleAll(object sender, RoutedEventArgs e)
    {
        if ((sender is System.Windows.Controls.CheckBox cb) && cb.IsChecked.HasValue)
        {
            foreach (var item in Items) item.IsChecked = cb.IsChecked.Value;
        }
        // 强制刷新列表勾选态（Item 非 INPC，简化处理：重绑）
        PersonList.ItemsSource = null;
        PersonList.ItemsSource = Items;
    }

    private void OnOk(object sender, RoutedEventArgs e) => DialogResult = true;

    private void OnCancel(object sender, RoutedEventArgs e) => Close();
}
