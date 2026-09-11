using System;
using System.Collections.Generic;

namespace LawFirm.UI.Controls;

/// <summary>
/// 严格填充算法（C4，纯函数移植，规格 §4.4）。
///
/// 逐行镜像 app/ui/column_layout.py:98-151 的 compute_fill（严格 D 版）：
/// - 变宽：Phase1 优先把「未显全列」（w &lt; content_w）按缺口比例补足（终止条件 extra &gt; 0.5 / guard &lt; 8），
///   Phase2 余量按当前宽比例分给 auto 列；
/// - 变窄：只缩「非冻结且非 fixed」列（下限 MIN_W=48）；仍不够才兜底缩 auto 列；
///   fixed（用户手动拖宽）列绝不被自动缩回。
/// 输出仅含 visible 列，宽度 = Math.Round(w)。
/// </summary>
public sealed class ColumnLayoutManager
{
    /// <summary>单列最小宽（逻辑像素）。column_layout.py:29。</summary>
    public const int MinW = 48;

    /// <summary>20 位发票号样本（column_layout.py:28），列宽上限基准。</summary>
    public const string InvoiceSample = "25332000000012014331";

    /// <summary>填充算法输入列。</summary>
    public sealed record FillColumn(string Key, double ContentW, bool Frozen = false, double? Fixed = null, bool Visible = true);

    /// <summary>
    /// 计算各列最终宽度。对应 column_layout.py:99-151。
    /// </summary>
    /// <param name="viewportW">视口宽（逻辑像素）。</param>
    /// <param name="cols">列集合（含隐藏列；隐藏列不参与分配）。</param>
    /// <returns>{key: 最终宽}（仅 visible 列）。</returns>
    public static Dictionary<string, int> ComputeFill(int viewportW, IReadOnlyList<FillColumn> cols)
    {
        // vis = [c for c in cols if c.get("visible", True)]
        var vis = new List<WorkCol>();
        foreach (var c in cols)
        {
            if (c.Visible) vis.Add(new WorkCol(c.Key, c.ContentW, c.Frozen, c.Fixed));
        }
        if (vis.Count == 0) return new Dictionary<string, int>();

        // w = fixed ?? content_w；auto = fixed 为空的列
        var auto = new List<WorkCol>();
        double wTotal = 0;
        foreach (var c in vis)
        {
            c.W = c.Fixed ?? c.ContentW;
            wTotal += c.W;
            if (c.Fixed is null) auto.Add(c);
        }

        if (viewportW >= wTotal)
        {
            double extra = viewportW - wTotal;

            // Phase1：优先喂未显全列（w < content_w - 0.5）
            int guard = 0;
            while (extra > 0.5 && guard < 8)
            {
                guard++;
                var trunc = new List<WorkCol>();
                double deficit = 0;
                foreach (var c in auto)
                {
                    if (c.W < c.ContentW - 0.5)
                    {
                        trunc.Add(c);
                        deficit += c.ContentW - c.W;
                    }
                }
                if (trunc.Count == 0 || deficit <= 0) break;
                double give = Math.Min(extra, deficit);
                foreach (var c in trunc)
                    c.W += give * (c.ContentW - c.W) / deficit;
                extra -= give;
            }

            // Phase2：余量按当前宽比例分给 auto
            if (extra > 0.5 && auto.Count > 0)
            {
                double tot = 0;
                foreach (var c in auto) tot += c.W;
                if (tot <= 0) tot = 1;
                foreach (var c in auto)
                    c.W += extra * c.W / tot;
            }
        }
        else
        {
            double deficit = wTotal - viewportW;

            // 仅缩小「未冻结且未手动定宽」的列（fixed 列是用户显式设定，不自动缩回）
            var nf = new List<WorkCol>();
            double nfW = 0;
            foreach (var c in vis)
            {
                if (!c.Frozen && c.Fixed is null) { nf.Add(c); nfW += c.W; }
            }
            if (nf.Count > 0 && deficit > 0)
            {
                double take = Math.Min(deficit, Math.Max(0.0, nfW - nf.Count * MinW));
                if (take > 0)
                {
                    foreach (var c in nf)
                        c.W = Math.Max(MinW, c.W - take * c.W / nfW);
                    deficit -= take;
                }
            }
            if (deficit > 0)
            {
                // 兜底：仅剩 auto 列（含冻结）也超宽时才动冻结列；fixed 列仍不动
                var allc = new List<WorkCol>();
                double allW = 0;
                foreach (var c in vis)
                {
                    if (c.Fixed is null) { allc.Add(c); allW += c.W; }
                }
                if (allW > 0)
                {
                    double take = Math.Min(deficit, Math.Max(0.0, allW - allc.Count * MinW));
                    foreach (var c in allc)
                        c.W = Math.Max(MinW, c.W - take * c.W / allW);
                }
            }
        }

        var result = new Dictionary<string, int>();
        foreach (var c in vis)
            result[c.Key] = (int)Math.Round(c.W);
        return result;
    }

    private sealed class WorkCol
    {
        public WorkCol(string key, double contentW, bool frozen, double? fixedW)
        { Key = key; ContentW = contentW; Frozen = frozen; Fixed = fixedW; W = 0; }
        public string Key;
        public double ContentW;
        public bool Frozen;
        public double? Fixed;
        public double W;
    }
}
