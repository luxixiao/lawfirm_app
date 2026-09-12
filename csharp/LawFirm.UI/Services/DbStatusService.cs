using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows;
using LawFirm.Data;

namespace LawFirm.UI.Services;

/// <summary>
/// 数据库定位状态（R-M2 可观测性）：侧栏底部常驻显示「当前用的是哪个 lawfirm.db」，
/// 悬浮看完整路径与来源，点击复制路径到剪贴板。
///
/// 为什么需要：三机 Seafile 同步场景下，残留的 LAWFIRM_DB 环境变量会把程序
/// 指到一个旧副本上，界面照常显示数据（只是数据不对），用户完全无法察觉。
/// 有了这一行常驻信息 + 来源标注，一眼就能看出「读的是不是我以为的那个库」。
/// </summary>
public sealed class DbStatusService
{
    private static readonly DbStatusService _current = new();

    /// <summary>进程级单例（类型为只读，构造期即完成解析，无需 INotifyPropertyChanged）。</summary>
    public static DbStatusService Current => _current;

    private DbStatusService()
    {
        DbConnection.DatabaseResolution r;
        try
        {
            r = DbConnection.ResolveDatabase();
        }
        catch (Exception ex)
        {
            // 解析本身不该抛；兜底成「未找到」，保证 UI 一定能起来
            r = new DbConnection.DatabaseResolution { Source = "none", Tried = new[] { "解析失败：" + ex.Message } };
        }

        IsOk = r.Found;
        HasEnvResidue = r.EnvIgnored;
        SourceText = r.SourceText;
        FullPath = r.Path ?? string.Empty;
        ShortName = r.Found ? Path.GetFileName(r.Path!) : "未找到数据库";
        Display = r.Found ? "数据库：" + Path.GetFileName(r.Path!) : "⚠ 未找到数据库";

        var sb = new StringBuilder();
        sb.AppendLine(r.Found ? "当前数据库：" + r.Path : "未找到可用的 lawfirm.db");
        sb.AppendLine("来源：" + r.SourceText);
        sb.AppendLine("环境变量 LAWFIRM_DB：" + (string.IsNullOrWhiteSpace(r.EnvValue) ? "（未设置）" : r.EnvValue));
        if (r.EnvIgnored)
            sb.AppendLine("注意：LAWFIRM_DB 已设置但不可用，本次已忽略（残留变量会把程序指到旧副本）。");
        if (r.Tried.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine("查找过程：");
            foreach (string t in r.Tried.Take(20)) sb.AppendLine("  " + t);
        }
        sb.AppendLine();
        sb.AppendLine("（点击可复制完整路径）");
        Detail = sb.ToString().TrimEnd();
    }

    public bool IsOk { get; }
    public bool HasEnvResidue { get; }
    public string SourceText { get; }
    public string FullPath { get; }
    public string ShortName { get; }
    public string Display { get; }
    public string Detail { get; }

    /// <summary>把完整路径复制到剪贴板（侧栏底部点击）。失败静默返回 false。</summary>
    public bool CopyPathToClipboard()
    {
        if (!IsOk) return false;
        try
        {
            Clipboard.SetText(FullPath);
            return true;
        }
        catch
        {
            return false;
        }
    }
}
