using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace LawFirm.UI.Services;

/// <summary>列状态（规格 §4.4；对应 column_layout.py 的 JSON 四键）。</summary>
public sealed class ColumnState
{
    [JsonPropertyName("order")]
    public List<string> Order { get; set; } = new();

    [JsonPropertyName("visible")]
    public Dictionary<string, bool> Visible { get; set; } = new();

    [JsonPropertyName("frozen")]
    public Dictionary<string, bool> Frozen { get; set; } = new();

    [JsonPropertyName("widths")]
    public Dictionary<string, double> Widths { get; set; } = new();
}

/// <summary>
/// 列布局持久化（C3，规格 §4.4）。
///
/// - 载体：%APPDATA%\LawFirm\ui-state.json（U6 已裁定：不进 Seafile、不进 git、不污染 db 目录；
///   Python 存注册表同样不同步）。
/// - 列身份 = 稳定标题字符串；**列集合与 saved.order 不一致 → 整份丢弃回退默认**
///   （column_layout.py:64-65，防「14 列↔3 列」串档）。
/// - 文件缺失/损坏 → 视为无存档。
/// </summary>
public sealed class ColumnStateStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private readonly string _path;

    public ColumnStateStore()
        : this(DefaultPath()) { }

    /// <summary>注入路径（单测用）。</summary>
    public ColumnStateStore(string path) => _path = path;

    public static string DefaultPath()
    {
        string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        return Path.Combine(appData, "LawFirm", "ui-state.json");
    }

    private static Dictionary<string, JsonElement> ReadRoot(string path)
    {
        if (!File.Exists(path)) return new Dictionary<string, JsonElement>();
        try
        {
            string json = File.ReadAllText(path);
            if (string.IsNullOrWhiteSpace(json)) return new Dictionary<string, JsonElement>();
            var root = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json, JsonOpts);
            return root ?? new Dictionary<string, JsonElement>();
        }
        catch (Exception)   // 损坏 → 视为无存档（column_layout.py:57-58 同精神）
        {
            return new Dictionary<string, JsonElement>();
        }
    }

    private static void WriteRoot(string path, Dictionary<string, JsonElement> root)
    {
        string? dir = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        File.WriteAllText(path, JsonSerializer.Serialize(root, JsonOpts));
    }

    private static string Key(string page, string name) => $"{page}/{name}";

    private static ColumnState Default(IReadOnlyList<string> defaultKeys)
    {
        var state = new ColumnState();
        foreach (var k in defaultKeys)
        {
            state.Order.Add(k);
            state.Visible[k] = true;
            state.Frozen[k] = false;
        }
        return state;
    }

    /// <summary>读取列状态；无存档或列集合不一致 → 默认（全部可见、未冻结、无固定宽）。</summary>
    public ColumnState Load(string page, string name, IReadOnlyList<string> defaultKeys)
    {
        var state = Default(defaultKeys);
        var root = ReadRoot(_path);
        if (!root.TryGetValue("columns", out var colsEl))
            return state;
        try
        {
            var cols = JsonSerializer.Deserialize<Dictionary<string, ColumnState>>(colsEl.GetRawText(), JsonOpts);
            if (cols is null || !cols.TryGetValue(Key(page, name), out var saved) || saved is null)
                return state;
            if (saved.Order is null)
                return state;
            // 列集合（身份集合）必须与当前完全一致（用完整存档集合比较，column_layout.py:64-65）
            var savedSet = new HashSet<string>(saved.Order);
            var defaultSet = new HashSet<string>(defaultKeys);
            if (savedSet.Count != defaultSet.Count || !savedSet.SetEquals(defaultSet))
                return state;   // 串档防护：整份丢弃
            // order：过滤后按存档序；缺失的追加（column_layout.py:59-68）
            var savedOrder = new List<string>();
            foreach (var k in saved.Order)
            {
                if (defaultSet.Contains(k)) savedOrder.Add(k);
            }
            foreach (var k in defaultKeys)
            {
                if (!savedOrder.Contains(k)) savedOrder.Add(k);
            }
            state.Order = savedOrder;
            foreach (var k in defaultKeys)
            {
                if (saved.Visible is not null && saved.Visible.TryGetValue(k, out var v))
                    state.Visible[k] = v;
                if (saved.Frozen is not null && saved.Frozen.TryGetValue(k, out var f))
                    state.Frozen[k] = f;
                if (saved.Widths is not null && saved.Widths.TryGetValue(k, out var w) && w > 0)
                    state.Widths[k] = w;
            }
            return state;
        }
        catch (Exception)
        {
            return state;
        }
    }

    /// <summary>写入列状态（整份覆盖该 key；其余 section 原样保留）。</summary>
    public void Save(string page, string name, ColumnState state)
    {
        var root = ReadRoot(_path);
        Dictionary<string, ColumnState> cols;
        if (root.TryGetValue("columns", out var colsEl))
        {
            cols = JsonSerializer.Deserialize<Dictionary<string, ColumnState>>(colsEl.GetRawText(), JsonOpts)
                   ?? new Dictionary<string, ColumnState>();
        }
        else
        {
            cols = new Dictionary<string, ColumnState>();
        }
        cols[Key(page, name)] = state;
        root["columns"] = JsonSerializer.SerializeToElement(cols, JsonOpts);
        WriteRoot(_path, root);
    }

    /// <summary>删除存档（「恢复默认」；对应 column_layout.py:94-95）。</summary>
    public void Reset(string page, string name)
    {
        var root = ReadRoot(_path);
        if (!root.TryGetValue("columns", out var colsEl)) return;
        var cols = JsonSerializer.Deserialize<Dictionary<string, ColumnState>>(colsEl.GetRawText(), JsonOpts);
        if (cols is null || !cols.Remove(Key(page, name))) return;
        root["columns"] = JsonSerializer.SerializeToElement(cols, JsonOpts);
        WriteRoot(_path, root);
    }
}

/// <summary>
/// UI 轻量偏好（存同一份 %APPDATA%\LawFirm\ui-state.json）。
/// 目前只有「导出月度结算表」的上次人员选择（对应 Python data/prefs.json 的 report_persons，
/// 但**不写 data/prefs.json**——data/ 是 db 目录且被 Seafile 同步，UI 状态不入内，U6 同精神）。
/// </summary>
public static class UiPrefs
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private static Dictionary<string, JsonElement> ReadRoot()
    {
        try
        {
            string path = ColumnStateStore.DefaultPath();
            if (!File.Exists(path)) return new Dictionary<string, JsonElement>();
            string json = File.ReadAllText(path);
            var root = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json, JsonOpts);
            return root ?? new Dictionary<string, JsonElement>();
        }
        catch (Exception)
        {
            return new Dictionary<string, JsonElement>();
        }
    }

    private static void WriteRoot(Dictionary<string, JsonElement> root)
    {
        string path = ColumnStateStore.DefaultPath();
        string? dir = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        File.WriteAllText(path, JsonSerializer.Serialize(root, JsonOpts));
    }

    /// <summary>上次「导出月度结算表」勾选的员工（Python prefs.report_persons）。</summary>
    public static List<string> LoadReportPersons()
    {
        var root = ReadRoot();
        if (!root.TryGetValue("reportPersons", out var el)) return new List<string>();
        try
        {
            return JsonSerializer.Deserialize<List<string>>(el.GetRawText(), JsonOpts) ?? new List<string>();
        }
        catch (Exception)
        {
            return new List<string>();
        }
    }

    public static void SaveReportPersons(IReadOnlyList<string> persons)
    {
        var root = ReadRoot();
        root["reportPersons"] = JsonSerializer.SerializeToElement(persons, JsonOpts);
        WriteRoot(root);
    }
}
