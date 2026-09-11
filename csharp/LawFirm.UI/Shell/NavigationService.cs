using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Media;

namespace LawFirm.UI.Shell;

/// <summary>侧栏导航子项（对应 main_window.py NAV_GROUPS 的 (key, title)）。</summary>
public sealed class NavItem : INotifyPropertyChanged
{
    private bool _isSelected;

    public NavItem(string key, string title) { Key = key; Title = title; }
    public string Key { get; }
    public string Title { get; }

    /// <summary>选中态（侧栏选中底 #e3e1db + 字重 600，V6）。由 NavigationService.Select 维护。</summary>
    public bool IsSelected
    {
        get => _isSelected;
        set { if (_isSelected != value) { _isSelected = value; OnPropertyChanged(); } }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged([CallerMemberName] string? name = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name!));
}

/// <summary>侧栏分组（可折叠；icon 直接持有 Geometry，避免 XAML 里 string→Geometry 转换）。</summary>
public sealed class NavGroup : INotifyPropertyChanged
{
    private bool _isExpanded;

    public NavGroup(string title, Geometry icon, IReadOnlyList<NavItem> items)
    {
        Title = title;
        Icon = icon;
        Items = items;
        _isExpanded = true;
    }

    public string Title { get; }
    public Geometry Icon { get; }
    public IReadOnlyList<NavItem> Items { get; }

    /// <summary>分组展开态（点击组头切换；组面板用 MaxHeight 动画，规格 §3.3）。</summary>
    public bool IsExpanded
    {
        get => _isExpanded;
        set { if (_isExpanded != value) { _isExpanded = value; OnPropertyChanged(); } }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged([CallerMemberName] string? name = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name!));
}

/// <summary>
/// 导航注册表 + 页面选择（G3，对应 main_window.py:56-93 NAV_GROUPS 与 :198-221 select）。
///
/// T5 Pilot 只实现「各类报表(settlement)」1 页；其余 21 项渲染为占位页（PlaceholderView，U11）。
/// Select(key) 更新 CurrentPageTitle（标题栏页名）、维护 NavItem.IsSelected，
/// 并触发 NavigateRequested（MainShellWindow 换页）。
/// </summary>
public sealed class NavigationService : INotifyPropertyChanged
{
    private static readonly Geometry IconImport = NavIcons.Import;
    private static readonly Geometry IconLedger = NavIcons.Ledger;
    private static readonly Geometry IconBusiness = NavIcons.Business;
    private static readonly Geometry IconSalary = NavIcons.Salary;
    private static readonly Geometry IconCalc = NavIcons.Calc;
    private static readonly Geometry IconReport = NavIcons.Report;
    private static readonly Geometry IconMaintenance = NavIcons.Maintenance;

    /// <summary>NAV_GROUPS 逐字对齐 main_window.py:56-93（组名 + 图标 + (key, 显示名)）。</summary>
    public static ReadOnlyCollection<NavGroup> BuildDefaultGroups()
    {
        var groups = new List<NavGroup>
        {
            new("数据导入", IconImport, new[]
            {
                new NavItem("import", "导入台账"),
                new NavItem("batch", "导入记录"),
                new NavItem("review", "导入复核"),
                new NavItem("audit", "修改记录"),
                new NavItem("manual", "发票补录"),
            }),
            new("台账查看", IconLedger, new[]
            {
                new NavItem("invoice_ledger", "销项发票"),
                new NavItem("ledger_doc", "发票台账"),
                new NavItem("expense_ledger", "费用台账"),
                new NavItem("salary_ledger", "工资表"),
            }),
            new("业务数据", IconBusiness, new[]
            {
                new NavItem("invoice", "发票收款情况"),
                new NavItem("handler_all", "经办人发票收款情况"),
                new NavItem("prepayment", "预收款"),
                new NavItem("refund", "退款"),
            }),
            new("工资个税", IconSalary, new[]
            {
                new NavItem("salary_summary", "工资累计"),
                new NavItem("tax_declaration", "1-11月个税申报"),
                new NavItem("tax_deduction", "费用扣除"),
            }),
            new("分成计算", IconCalc, new[]
            {
                new NavItem("calc", "计算表"),
            }),
            new("各类报表", IconReport, new[]
            {
                new NavItem("settlement", "各类报表"),
            }),
            new("数据维护", IconMaintenance, new[]
            {
                new NavItem("staff", "员工管理"),
                new NavItem("expense_cat", "费用类型"),
                new NavItem("data_clear", "数据情况"),
                new NavItem("snapshot", "快照"),
            }),
        };
        return groups.AsReadOnly();
    }

    private readonly Dictionary<string, Func<object>> _pageFactories = new();
    private readonly Dictionary<string, object> _pageCache = new();
    private string _currentPageTitle = "导入台账";
    private string? _selectedKey;

    public NavigationService()
    {
        Groups = BuildDefaultGroups();
        // 页面工厂：settlement 有真实页，其余走占位（U11）
        RegisterPage("settlement", () => new Views.Settlement.SettlementView());
        // 默认选中「导入台账」（main_window.py 默认页）
        MarkSelected("import");
    }

    public IReadOnlyList<NavGroup> Groups { get; }

    /// <summary>标题栏页名（= 当前选中子项显示名，main_window.py:138-148）。</summary>
    public string CurrentPageTitle
    {
        get => _currentPageTitle;
        private set { if (_currentPageTitle != value) { _currentPageTitle = value; OnPropertyChanged(); } }
    }

    public string? SelectedKey
    {
        get => _selectedKey;
        private set { _selectedKey = value; OnPropertyChanged(); }
    }

    /// <summary>请求换页（MainShellWindow 订阅；参数 = nav key）。</summary>
    public event EventHandler<string>? NavigateRequested;

    /// <summary>注册页面工厂（同程序集内注册真实页；未注册 key → 占位页）。</summary>
    public void RegisterPage(string key, Func<object> factory)
        => _pageFactories[key] = factory;

    /// <summary>选中某导航项：更新页名 + 选中态 + 请求换页（对应 Python select(key)）。</summary>
    public void Select(string key)
    {
        MarkSelected(key);
        NavigateRequested?.Invoke(this, key);
    }

    private void MarkSelected(string key)
    {
        SelectedKey = key;
        foreach (var g in Groups)
        {
            foreach (var it in g.Items)
            {
                it.IsSelected = it.Key == key;
                if (it.Key == key)
                    CurrentPageTitle = it.Title;
            }
        }
    }

    /// <summary>取（或创建）key 对应的页面内容；未注册的 key → 占位页（缓存复用）。</summary>
    public object GetOrCreatePage(string key)
    {
        if (_pageCache.TryGetValue(key, out var cached))
            return cached;
        object page = _pageFactories.TryGetValue(key, out var f)
            ? f()
            : new Views.PlaceholderView(key);
        _pageCache[key] = page;
        return page;
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged([CallerMemberName] string? name = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name!));
}
