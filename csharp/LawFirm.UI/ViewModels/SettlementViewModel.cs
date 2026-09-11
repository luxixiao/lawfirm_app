using System;
using System.Collections.Generic;
using CommunityToolkit.Mvvm.ComponentModel;

namespace LawFirm.UI.ViewModels;

/// <summary>
/// settlement 页总 VM（G6）：4 个子 tab（个人结算总表 / 月度结算表 / 年度聘用结算表 / 开票收入表）。
/// tab 名与顺序逐字对齐 settlement_view.py:153-156。
/// 切 tab 时懒加载对应子 VM 的首次刷新（_on_tab_changed）。
/// </summary>
public partial class SettlementViewModel : ViewModelBase
{
    public SettlementViewModel()
    {
        Personal = new PersonalSettlementViewModel();
        Monthly = new MonthlyReportViewModel(Personal);
        StaffIncome = new StaffIncomeViewModel(Personal);
        InvoiceIncome = new InvoiceIncomeViewModel(Personal);
        Personal.InitializeOnce();
    }

    public PersonalSettlementViewModel Personal { get; }
    public MonthlyReportViewModel Monthly { get; }
    public StaffIncomeViewModel StaffIncome { get; }
    public InvoiceIncomeViewModel InvoiceIncome { get; }

    [ObservableProperty] private int _selectedTabIndex;

    partial void OnSelectedTabIndexChanged(int value) => OnTabChanged(value);

    private bool _monthlyLoaded;
    private bool _staffIncomeLoaded;
    private bool _invoiceIncomeLoaded;

    private void OnTabChanged(int idx)
    {
        // 各子 VM 的刷新命令（RelayCommand 生成属性）；已加载过的不重复刷
        switch (idx)
        {
            case 1 when !_monthlyLoaded:
                _monthlyLoaded = true;
                Monthly.RefreshCommand.Execute(null);
                break;
            case 2 when !_staffIncomeLoaded:
                _staffIncomeLoaded = true;
                StaffIncome.RefreshCommand.Execute(null);
                break;
            case 3 when !_invoiceIncomeLoaded:
                _invoiceIncomeLoaded = true;
                InvoiceIncome.RefreshCommand.Execute(null);
                break;
        }
    }
}
