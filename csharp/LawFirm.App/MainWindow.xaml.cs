using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Media;
using LawFirm.Data;

namespace LawFirm.App;

/// <summary>
/// 主窗口（Pilot T2 只读验证）。
///
/// Loaded 时：经 DbConnection 只读打开 lawfirm.db → 调两个 CountAll() 显示总行数
/// → 取各表前 5 行样例在 DataGrid 展示。全程只读，任何异常都捕获并显示到状态栏，
/// 不中断应用、不触碰写操作。
/// </summary>
public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        try
        {
            string dbPath = DbConnection.FindDatabase();

            // using：连接随方法结束释放；OpenReadOnly 已设 query_only + ReadOnly，写被双重拒绝。
            using var conn = DbConnection.OpenReadOnly(dbPath);

            var invoiceRepo = new InvoiceRepo(conn);
            int invoiceCount = invoiceRepo.CountAll();
            IEnumerable<InvoiceRow> invoices = invoiceRepo.Sample(5);

            var collectionRepo = new CollectionRepo(conn);
            int collectionCount = collectionRepo.CountAll();
            IEnumerable<CollectionRow> collections = collectionRepo.Sample(5);

            InvoiceCountText.Text = $"发票表(invoice) 总行数: {invoiceCount}";
            InvoiceGrid.ItemsSource = invoices;

            CollectionCountText.Text = $"收款明细表(collection) 总行数: {collectionCount}";
            CollectionGrid.ItemsSource = collections;

            StatusText.Text = $"已只读打开数据库：{dbPath}";
            StatusText.Foreground = Brushes.DarkGreen;
        }
        catch (Exception ex)
        {
            StatusText.Text = $"打开/读取数据库失败：{ex.Message}";
            StatusText.Foreground = Brushes.Crimson;
        }
    }
}
