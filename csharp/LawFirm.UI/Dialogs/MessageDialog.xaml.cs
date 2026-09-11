using System.Windows;
using System.Windows.Input;

namespace LawFirm.UI.Dialogs;

/// <summary>
/// Notion 风消息对话框（T5.4-④）：替代 QMessageBox.information / critical。
/// 用法：MessageDialog.Info("生成完成", "已生成 42 份…") / MessageDialog.Error("生成失败", msg)。
/// </summary>
public partial class MessageDialog : Window
{
    public MessageDialog()
    {
        InitializeComponent();
        Owner = Application.Current.MainWindow;
        WindowStartupLocation = Owner is null
            ? WindowStartupLocation.CenterScreen
            : WindowStartupLocation.CenterOwner;
    }

    public static void Info(string title, string message) => ShowDialog(title, message, false);

    public static void Error(string title, string message) => ShowDialog(title, message, true);

    private static void ShowDialog(string title, string message, bool isError)
    {
        var dlg = new MessageDialog
        {
            DialogTitle = title,
            Body = message,
        };
        dlg.TitleText.Foreground = isError
            ? dlg.TryFindResource("Red") as System.Windows.Media.Brush
              ?? System.Windows.Media.Brushes.IndianRed
            : dlg.TryFindResource("Text") as System.Windows.Media.Brush
              ?? System.Windows.Media.Brushes.Black;
        dlg.ShowDialog();
    }

    /// <summary>对话框标题。</summary>
    public string DialogTitle
    {
        get => TitleText.Text;
        set => TitleText.Text = value;
    }

    /// <summary>对话框正文。</summary>
    public string Body
    {
        get => BodyText.Text;
        set => BodyText.Text = value;
    }

    private void OnOk(object sender, RoutedEventArgs e) => Close();

    /// <summary>允许 ESC / 回车关闭。</summary>
    protected override void OnPreviewKeyDown(KeyEventArgs e)
    {
        base.OnPreviewKeyDown(e);
        if (e.Key is Key.Escape or Key.Enter) Close();
    }
}
