using System.Collections.Generic;
using System.Linq;
using System.Windows;
using LawFirm.UI.Services;

namespace LawFirm.UI.Dialogs;

/// <summary>
/// 导出月度结算表弹窗（gen_report 的 QDialog；settlement_view.py:455-498）。
/// 默认勾选 = 上次选择（UiPrefs.reportPersons，存 %APPDATA%，不写 data/prefs.json）。
/// 用法：var choice = MonthlyExportDialog.Pick(names);（null = 取消）
/// </summary>
public partial class MonthlyExportDialog : Window
{
    public sealed class Item
    {
        public Item(string name) { Name = name; }
        public string Name { get; }
        public bool IsChecked { get; set; }
    }

    public MonthlyExportDialog(IReadOnlyList<string> names)
    {
        InitializeComponent();
        Owner = Application.Current.MainWindow;
        WindowStartupLocation = Owner is null
            ? WindowStartupLocation.CenterScreen
            : WindowStartupLocation.CenterOwner;
        Tip = $"勾选要导出的员工（共 {names.Count} 人，默认上次选择）";
        MonthItems = Enumerable.Range(1, 12)
            .Select(mo => new PersonalSettlementViewModel.MonthOption(mo, $"{mo}月")).ToList();
        SelectedMonth = MonthItems[0];
        var last = UiPrefs.LoadReportPersons();
        Items = names.Select(n => new Item(n) { IsChecked = last.Count > 0 && last.Contains(n) }).ToList();
        DataContext = this;
    }

    public string Tip { get; }
    public List<PersonalSettlementViewModel.MonthOption> MonthItems { get; }
    public PersonalSettlementViewModel.MonthOption? SelectedMonth { get; set; }
    public List<Item> Items { get; }

    /// <summary>弹窗选择；返回 (月份, 选中名单)（null = 取消）。</summary>
    public static (int Month, List<string> Persons)? Pick(IReadOnlyList<string> names)
    {
        var dlg = new MonthlyExportDialog(names);
        if (dlg.ShowDialog() != true) return null;
        var selected = dlg.Items.Where(i => i.IsChecked).Select(i => i.Name).ToList();
        UiPrefs.SaveReportPersons(selected);   // 记忆上次选择（Python prefs.report_persons）
        return (dlg.SelectedMonth?.Value ?? 1, selected);
    }

    private void OnToggleAll(object sender, RoutedEventArgs e)
    {
        if (sender is System.Windows.Controls.CheckBox cb && cb.IsChecked.HasValue)
        {
            foreach (var item in Items) item.IsChecked = cb.IsChecked.Value;
        }
        PersonList.ItemsSource = null;
        PersonList.ItemsSource = Items;
    }

    private void OnOk(object sender, RoutedEventArgs e) => DialogResult = true;

    private void OnCancel(object sender, RoutedEventArgs e) => Close();
}
