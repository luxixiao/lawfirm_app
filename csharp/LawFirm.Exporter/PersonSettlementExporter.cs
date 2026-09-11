using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Microsoft.Data.Sqlite;
using NPOI.SS.UserModel;
using NPOI.SS.Util;
using NPOI.XSSF.UserModel;

namespace LawFirm.Exporter;

/// <summary>
/// 个人结算总表导出器（Pilot T3，C# / NPOI 端口）。
///
/// 逐格镜像 Python app/exporter/person_settlement_exporter.py 的
/// _write_ws / _row_values / _subtotal / _has_data / export_one / export_all。
/// 输出契约：每人一个 xlsx（sheet = 各身份 [仅有数据时输出] + 汇总 [恒输出]）。
///
/// 格式契约（对照 diff_xlsx.py 的 7 个维度）：
///  - sheet 名 / 顺序：身份 sheet（合伙/聘用/兼职，仅 HasDataYear 为真者）+ 汇总恒出；
///  - 合并单元格：仅 A1:O1（标题）；
///  - 表头序列：第 2 行 = 序号|项目|1月..12月|合计（共 15 列）；
///  - 数字格式：C..O 每格 "#,##0.00"（A/B 不设）；空数值格只套样式、不写值；
///  - 逐格值：所有 15 列逐格与 Python 对齐；
///  - 页面设置：**只写 4 边 margin（0.75/0.75/1.0/1.0，double 字面量）**，
///    🔴 **绝不触碰 orientation / Landscape / FitToPage / FitWidth / FitHeight**。
///    原因见规格书 §6.4.1 U1 定案：本导出器 golden 全部 80 个 sheet 无 &lt;pageSetup&gt;，
///    diff_xlsx.py 的 _compare_page_setup 对 orientation 不做归一化，一旦落盘
///    orientation="portrait" 就会 80 个 sheet 全红。（月度导出器 SettlementReportExporter.cs:211
///    写了 Landscape=false 能 PASS 只是因为它自己的 golden 恰好有 portrait，两契约不同，不可照抄。）
/// （列宽 / 行高 diff 不比较，仅为基本可用性设置。）
/// </summary>
public static class PersonSettlementExporter
{
    private static readonly int[] AllMonths = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 };
    private static readonly string[] Headers =
    {
        "序号", "项目", "1月", "2月", "3月", "4月", "5月", "6月",
        "7月", "8月", "9月", "10月", "11月", "12月", "合计",
    };
    private static readonly string[] PersonTypes = { "合伙", "聘用", "兼职" };

    // ==================================================================
    // 公开 API
    // ==================================================================

    /// <summary>
    /// 导出单个员工的个人结算总表（每人一文件、多 sheet）。
    /// 对应 Python export_one(person, path, year)。
    /// </summary>
    /// <param name="conn">只读数据库连接（用于读取费用类型顺序）</param>
    /// <param name="person">员工姓名（Build 返回字典的 key）</param>
    /// <param name="path">输出文件路径（父目录会自动创建）</param>
    /// <param name="year">结算年份</param>
    /// <returns>实际写出的文件路径（原样返回 path）</returns>
    public static string ExportOne(SqliteConnection conn, string person, string path, int year)
    {
        // 不带身份过滤的汇总数据（等价 Python build_settlement(year, person=person).get(person)）
        var stAll = SettlementEngine.Build(conn, year, person, null).GetValueOrDefault(person);

        // 费用类型顺序只需读取一次，供本文件内所有 sheet 复用（对齐 Python 的模块级 ordered_types）。
        var ordered = ExpenseCatHelper.OrderedTypes(conn);

        // NPOI 的 XSSFWorkbook 初始无 sheet，无需像 openpyxl 那样 wb.remove(wb.active)。
        var wb = new XSSFWorkbook();

        // 各身份 sheet（仅有数据才建），顺序 = 合伙 -> 聘用 -> 兼职
        foreach (var ptype in PersonTypes)
        {
            var st = SettlementEngine.Build(conn, year, person, ptype).GetValueOrDefault(person);
            if (HasDataYear(st))
            {
                var ws = wb.CreateSheet(person + ptype);
                WriteWs(ws, person, st!, year, ptype, ordered);
            }
        }

        // 汇总 sheet（恒建）
        var wsSum = wb.CreateSheet(person + "汇总");
        WriteWs(wsSum, person, stAll, year, "汇总", ordered);

        var outDir = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(outDir)) Directory.CreateDirectory(outDir);
        using var fs = new FileStream(path, FileMode.Create, FileAccess.Write);
        wb.Write(fs);
        return path;
    }

    /// <summary>
    /// 为全部员工生成个人结算总表（每人一文件），返回文件路径列表。
    /// 对应 Python export_all(outDir, year)。
    /// 人员集合 = SettlementEngine.Build(conn, year).Keys，按 StringComparer.Ordinal 排序
    /// （对应 Python sorted() 的 codepoint 序，保证与 golden 文件名集合一致）；
    /// 0 人时返回空列表、不产占位文件（对齐 Python export_all 行为）。
    /// </summary>
    /// <param name="conn">只读数据库连接</param>
    /// <param name="outDir">输出目录（不存在会自动创建）</param>
    /// <param name="year">结算年份</param>
    /// <returns>生成的文件路径列表（顺序 = 排序后的人员顺序）</returns>
    public static List<string> ExportAll(SqliteConnection conn, string outDir, int year)
    {
        var data = SettlementEngine.Build(conn, year);
        Directory.CreateDirectory(outDir);
        var files = new List<string>();
        foreach (var person in data.Keys.OrderBy(k => k, StringComparer.Ordinal))
        {
            var safe = SafeFileName(person);
            var path = Path.Combine(outDir, $"个人结算总表_{safe}.xlsx");
            files.Add(ExportOne(conn, person, path, year));
        }
        return files;
    }

    /// <summary>
    /// 文件安全化（逐字镜像 Python export_all 内的替换规则）：
    /// person.Replace("/", "_").Replace("\\", "_").Trim()，结果为空串时用 "未命名"。
    /// </summary>
    private static string SafeFileName(string person)
    {
        var safe = person.Replace("/", "_").Replace("\\", "_").Trim();
        return string.IsNullOrEmpty(safe) ? "未命名" : safe;
    }

    // ==================================================================
    // 全年有数据判定（镜像 Python _has_data）
    // ==================================================================
    private static bool HasDataYear(PersonSettlement? st)
    {
        if (st == null) return false;

        // 10 个键（与 MonthData 字段对应；注意：不含 InvTotal，也不含 UncollectedMonth）
        foreach (int mo in AllMonths)
        {
            var m = st.Months[mo];
            double[] vals =
            {
                m.RecOpenCur, m.RecCurYear, m.RecPrevYear, m.RecRefundCur, m.RecRefundPrev,
                m.InvOpenReceived, m.InvOpenUncollected, m.InvRedCur, m.InvRedPrev, m.Income,
            };
            if (vals.Any(v => Math.Abs(v) > 0.01)) return true;
        }

        // 全年费用合计 > 0.01
        double expSum = st.Expenses.Values.Sum(d => d.Values.Sum());
        if (expSum > 0.01) return true;

        return false;
    }

    // ==================================================================
    // 行值辅助（镜像 Python _row_values / _subtotal，均返回 13 个值）
    // ==================================================================
    private static double[] RowValues(PersonSettlement st, Func<MonthData, double> key)
    {
        var vals = new double[13];
        for (int i = 0; i < 12; i++)
            vals[i] = Math.Round(key(st.Months[AllMonths[i]]), 2);
        vals[12] = Math.Round(vals.Take(12).Sum(), 2);
        return vals;
    }

    private static double[] Subtotal(PersonSettlement st, Func<MonthData, double>[] keys)
    {
        var vals = new double[13];
        for (int i = 0; i < 12; i++)
        {
            var m = st.Months[AllMonths[i]];
            vals[i] = Math.Round(keys.Sum(k => k(m)), 2);
        }
        vals[12] = Math.Round(vals.Take(12).Sum(), 2);
        return vals;
    }

    // ==================================================================
    // 单个 sheet 写入（镜像 Python _write_ws）
    // ==================================================================
    private static void WriteWs(
        ISheet ws, string person, PersonSettlement? st, int year, string typeLabel, List<string> ordered)
    {
        var wbk = ws.Workbook;
        var thin = BorderStyle.Thin;

        // 单元格样式工厂（照抄 SettlementReportExporter.Make 的写法）
        ICellStyle Make(bool bold, HorizontalAlignment align, bool numeric)
        {
            var s = wbk.CreateCellStyle();
            s.BorderTop = thin; s.BorderBottom = thin; s.BorderLeft = thin; s.BorderRight = thin;
            s.Alignment = align;
            s.VerticalAlignment = VerticalAlignment.Center;
            if (numeric) s.DataFormat = wbk.CreateDataFormat().GetFormat("#,##0.00");
            if (bold) { var f = wbk.CreateFont(); f.IsBold = true; s.SetFont(f); }
            return s;
        }
        var centerText = Make(false, HorizontalAlignment.Center, false);   // A 列（不加粗）
        var leftText = Make(false, HorizontalAlignment.Left, false);       // B 列（不加粗）
        var leftTextBold = Make(true, HorizontalAlignment.Left, false);    // B 列（加粗）
        var num = Make(false, HorizontalAlignment.Right, true);            // C..O（不加粗）
        var numBold = Make(true, HorizontalAlignment.Right, true);         // C..O（加粗）

        // 表头样式 = 加粗 + 居中 + F2F2F2 实心底 + 边框（对齐 Python 的 BOLD + HEAD_FILL）。
        // 之前这里直接复用 centerText（bold=false 且无填充），而注释却写着「加粗+填充」，
        // 属于注释与实现不符的视觉回归；diff_xlsx.py 不比 font/fill，7 维全绿也看不出来。
        var headerText = Make(true, HorizontalAlignment.Center, false);
        if (headerText is XSSFCellStyle xsHeader)
            xsHeader.SetFillForegroundColor(new XSSFColor(new byte[] { 0xF2, 0xF2, 0xF2 }));
        headerText.FillPattern = FillPattern.SolidForeground;

        // ---- 标题行（第 1 行，合并 A1:O1）----
        var r0 = ws.CreateRow(0);
        r0.HeightInPoints = 28;
        var cA1 = r0.CreateCell(0);
        // 分隔符为全角空格 U+3000（\u3000），不是普通空格
        cA1.SetCellValue($"个人结算总表（{year}年）\u3000姓名：{person}\u3000类型：{typeLabel}");
        var titleFont = wbk.CreateFont();
        titleFont.IsBold = true;
        titleFont.FontHeightInPoints = 14;
        var titleStyle = wbk.CreateCellStyle();
        titleStyle.SetFont(titleFont);
        titleStyle.Alignment = HorizontalAlignment.Center;
        titleStyle.VerticalAlignment = VerticalAlignment.Center;
        cA1.CellStyle = titleStyle;
        ws.AddMergedRegion(new CellRangeAddress(0, 0, 0, 14));

        // ---- 表头行（第 2 行，15 列）----
        var r1 = ws.CreateRow(1);
        for (int j = 0; j < Headers.Length; j++)
        {
            var c = r1.CreateCell(j);
            c.SetCellValue(Headers[j]);
            c.CellStyle = headerText;   // 加粗 + 居中 + F2F2F2 填充 + 边框（Python BOLD + HEAD_FILL）
        }

        // ---- 数据行（第 3 行起）----
        // put()：A 列 seq（居中，永不加粗）、B 列 name（左对齐；bold 时加粗）、
        //         C..O 共 13 个数值格（#,##0.00、右对齐；bold 时加粗；值为 null 时只套样式不写值）。
        void Put(int rowIdx, string seq, string name, double?[] vals, bool bold = false)
        {
            var row = ws.CreateRow(rowIdx);

            var ca = row.CreateCell(0);
            // seq 可能为 ""（本表无此情形，但保持与 Python 一致：空串落盘为「无值单元格」）
            if (!string.IsNullOrEmpty(seq)) ca.SetCellValue(seq);
            ca.CellStyle = centerText;

            var cb = row.CreateCell(1);
            cb.SetCellValue(name);
            cb.CellStyle = bold ? leftTextBold : leftText;

            for (int k = 0; k < vals.Length; k++)
            {
                var cc = row.CreateCell(2 + k);
                // 空值只套样式、不 SetCellValue（否则会落盘 t="n" 无值/0，与 golden 的 None 不一致）
                if (vals[k].HasValue) cc.SetCellValue(vals[k]!.Value);
                cc.CellStyle = bold ? numBold : num;
            }
        }

        int r = 2;  // 0-based 行下标；r=2 即 Excel 第 3 行
        var empty13 = new double?[13];

        // 一、上年结余结转（留空占位）
        Put(r, "一", "上年结余结转", empty13); r++;

        // 二、本月收款金额（5 项小计）
        var recKeys = new Func<MonthData, double>[]
        {
            mm => mm.RecOpenCur, mm => mm.RecCurYear, mm => mm.RecPrevYear,
            mm => mm.RecRefundCur, mm => mm.RecRefundPrev,
        };
        Put(r, "二", "本月收款金额", ToNullable(Subtotal(st!, recKeys)), true); r++;
        Put(r, "1", "本月开收", ToNullable(RowValues(st!, mm => mm.RecOpenCur))); r++;
        Put(r, "2", "收本年", ToNullable(RowValues(st!, mm => mm.RecCurYear))); r++;
        Put(r, "3", "收上年", ToNullable(RowValues(st!, mm => mm.RecPrevYear))); r++;
        Put(r, "4", "退本年", ToNullable(RowValues(st!, mm => mm.RecRefundCur))); r++;
        Put(r, "5", "退上年", ToNullable(RowValues(st!, mm => mm.RecRefundPrev))); r++;

        // 三、本月开具发票金额（独立计算：读 InvTotal，含预收票）
        var invTotalVals = new double[13];
        for (int i = 0; i < 12; i++)
            invTotalVals[i] = Math.Round(st!.Months[AllMonths[i]].InvTotal, 2);
        invTotalVals[12] = Math.Round(invTotalVals.Take(12).Sum(), 2);
        Put(r, "三", "本月开具发票金额", ToNullable(invTotalVals), true); r++;
        Put(r, "1", "本月开收", ToNullable(RowValues(st!, mm => mm.InvOpenReceived))); r++;
        Put(r, "2", "本月未收", ToNullable(RowValues(st!, mm => mm.InvOpenUncollected))); r++;
        Put(r, "3", "红冲本年", ToNullable(RowValues(st!, mm => mm.InvRedCur))); r++;
        Put(r, "4", "红冲上年", ToNullable(RowValues(st!, mm => mm.InvRedPrev))); r++;

        // 四、未收款金额（各月 = UncollectedMonth；合计 = UncollectedTotal，不是 12 个月之和）
        var uncVals = new double?[13];
        for (int i = 0; i < 12; i++)
            uncVals[i] = Math.Round(st!.UncollectedMonth.GetValueOrDefault(AllMonths[i], 0.0), 2);
        uncVals[12] = Math.Round(st!.UncollectedTotal, 2);
        Put(r, "四", "未收款金额", uncVals, true); r++;

        // 五、业务收入
        Put(r, "五", "业务收入", ToNullable(RowValues(st!, mm => mm.Income)), true); r++;

        // 六、减：分成报酬及费用（本表**不排除任何费用类型**，与月度结算表不同）
        var exp = st!.Expenses;

        // 费用小计行（逐月对全部费用类型求和）
        var expSub = new double[13];
        for (int i = 0; i < 12; i++)
        {
            int mo = AllMonths[i];
            expSub[i] = Math.Round(exp.Values.Sum(d => d.GetValueOrDefault(mo, 0.0)), 2);
        }
        expSub[12] = Math.Round(expSub.Take(12).Sum(), 2);
        Put(r, "六", "减：分成报酬及费用", ToNullable(expSub), true); r++;

        // 费用类型排序：先按 ordered_types() 顺序，再按类型名（Ordinal = Python codepoint）
        var order = ordered.Select((t, i) => (t, i)).ToDictionary(x => x.t, x => x.i);
        var expTypes = exp.Keys
            .OrderBy(t => order.TryGetValue(t, out var idx) ? idx : 999)
            .ThenBy(t => t, StringComparer.Ordinal)
            .ToList();
        int seqIdx = 1;
        foreach (var etype in expTypes)
        {
            var vals = new double?[13];
            for (int i = 0; i < 12; i++)
                vals[i] = Math.Round(exp[etype].GetValueOrDefault(AllMonths[i], 0.0), 2);
            vals[12] = Math.Round(vals.Take(12).Sum(v => v ?? 0.0), 2);
            Put(r, seqIdx.ToString(), etype, vals); r++;
            seqIdx++;
        }

        // ---- 列宽（diff 不比较，仅基本可用性；照 Python A=6 / B=22 / C..O=11）----
        ws.SetColumnWidth(0, 6 * 256);   // A
        ws.SetColumnWidth(1, 22 * 256);  // B
        for (int j = 2; j <= 14; j++) ws.SetColumnWidth(j, 11 * 256);  // C..O

        // ---- 页面设置 ----
        // 🔴 只写 4 边 margin，绝不触碰 orientation / Landscape / FitToPage / FitWidth / FitHeight。
        //    必须用 double 字面量 0.75 / 1.0（float 会引入精度噪声）。
        //    top/bottom = 1.0（不是 0.75）。header/footer = 0.5（不参与 diff，照 golden 写）。
        ws.SetMargin(MarginType.LeftMargin, 0.75);
        ws.SetMargin(MarginType.RightMargin, 0.75);
        ws.SetMargin(MarginType.TopMargin, 1.0);
        ws.SetMargin(MarginType.BottomMargin, 1.0);
        ws.SetMargin(MarginType.HeaderMargin, 0.5);
        ws.SetMargin(MarginType.FooterMargin, 0.5);
    }

    // 将 double[] 转 double?[]
    private static double?[] ToNullable(double[] vals)
        => vals.Select(v => (double?)v).ToArray();
}
