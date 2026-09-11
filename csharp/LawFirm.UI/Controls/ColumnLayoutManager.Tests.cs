using System.Collections.Generic;
using System.Linq;

namespace LawFirm.UI.Controls;

/// <summary>
/// ComputeFill 单测（规格 T5.2 验收 ⑦ / T5.5）：
/// 5 个用例，期望值逐项手算对齐 Python compute_fill（column_layout.py:98-151）。
/// assert 风格（沙箱无 xUnit 运行环境）；t5_smoke.bat 经 LawFirm.Cli --selftest-ui 执行。
/// </summary>
public static class ColumnLayoutManagerTests
{
    /// <summary>运行全部用例；返回失败描述列表（空 = 全绿）。</summary>
    public static List<string> RunAll()
    {
        var failures = new List<string>();
        Case1_WiderFillsFixedColumn(failures);
        Case2_NarrowerShrinksNonFrozen(failures);
        Case3_AllFixedTooWideUnchanged(failures);
        Case4_EmptyColumnSet(failures);
        Case5_WiderFillsAutoColumn(failures);
        return failures;
    }

    /// <summary>
    /// 用例 1 变宽（QA Minor 修正）：两列均 Fixed，auto 集为空 → Phase1/Phase2 无候选，
    /// fixed 列原样保留（Python 实跑 A=200,B=300；先前断言 A=500 是把 A 误当 auto 列手算）。
    /// </summary>
    private static void Case1_WiderFillsFixedColumn(List<string> failures)
    {
        var cols = new List<ColumnLayoutManager.FillColumn>
        {
            new("A", ContentW: 500, Frozen: false, Fixed: 200),
            new("B", ContentW: 300, Frozen: false, Fixed: 300),
        };
        var got = ColumnLayoutManager.ComputeFill(800, cols);
        // extra=300 但 auto=[]（Fixed 非 null 不进 auto）→ trunc 空 → 原宽返回
        Check(failures, "case1", got, new Dictionary<string, int> { ["A"] = 200, ["B"] = 300 });
    }

    /// <summary>用例 2 变窄：冻结列 A 不缩，非冻结 B/C 等比缩到 120（≥ MIN_W）。</summary>
    private static void Case2_NarrowerShrinksNonFrozen(List<string> failures)
    {
        var cols = new List<ColumnLayoutManager.FillColumn>
        {
            new("A", ContentW: 300, Frozen: true),
            new("B", ContentW: 300),
            new("C", ContentW: 300),
        };
        var got = ColumnLayoutManager.ComputeFill(540, cols);
        // deficit=360；nf=[B,C] nfW=600, take=min(360,600-96)=360 → B=C=max(48,300-180)=120
        Check(failures, "case2", got, new Dictionary<string, int> { ["A"] = 300, ["B"] = 120, ["C"] = 120 });
    }

    /// <summary>用例 3 全 fixed 超宽：fixed 列绝不被自动缩回（宁可横向滚动）。</summary>
    private static void Case3_AllFixedTooWideUnchanged(List<string> failures)
    {
        var cols = new List<ColumnLayoutManager.FillColumn>
        {
            new("A", ContentW: 400, Fixed: 400),
            new("B", ContentW: 400, Fixed: 400),
        };
        var got = ColumnLayoutManager.ComputeFill(500, cols);
        Check(failures, "case3", got, new Dictionary<string, int> { ["A"] = 400, ["B"] = 400 });
    }

    /// <summary>用例 4 空列集：返回空字典。</summary>
    private static void Case4_EmptyColumnSet(List<string> failures)
    {
        var got = ColumnLayoutManager.ComputeFill(100, new List<ColumnLayoutManager.FillColumn>());
        if (got.Count != 0)
            failures.Add($"case4: 期望空字典，实际 {string.Join(",", got.Select(kv => kv.Key + "=" + kv.Value))}");
    }

    private static void Check(List<string> failures, string caseName,
        Dictionary<string, int> got, Dictionary<string, int> expected)
    {
        foreach (var kv in expected)
        {
            if (!got.TryGetValue(kv.Key, out int actual) || actual != kv.Value)
                failures.Add($"{caseName}: {kv.Key} 期望 {kv.Value}，实际 {(got.TryGetValue(kv.Key, out int a) ? a.ToString() : "缺失")}");
        }
        foreach (var kv in got)
        {
            if (!expected.ContainsKey(kv.Key))
                failures.Add($"{caseName}: 多出列 {kv.Key}={kv.Value}");
        }
    }
}
