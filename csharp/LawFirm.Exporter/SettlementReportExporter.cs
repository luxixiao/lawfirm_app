using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Microsoft.Data.Sqlite;
using NPOI.SS.UserModel;
using NPOI.SS.Util;
using NPOI.XSSF.UserModel;

namespace LawFirm.Exporter;

/// <summary>
/// 月度结算表导出器（Pilot T2，C# / NPOI 端口）。
///
/// 逐格镜像 Python app/exporter/settlement_report_exporter.py 的 _write_sheet /
/// build_report_rows / report_note_*，写入 xlsx 供 diff_xlsx.py 逐格比对。
///
/// 对齐重点（对照 diff_xlsx.py 的 7 个维度）：
///  - sheet 名/顺序：每人 合伙/聘用/兼职(有数据才出) + 汇总 + 公共费用，与 Python 完全一致；
///  - 单元格值 + number_format：仅数值格设 "#,##0.00"，空值保持 General；
///  - 合并单元格：仅 A1:E1（标题）；
///  - 表头序列：第 3 行 = 序号|项目|本期|本年累计|备注；
///  - 汇总行：含「结余」关键词的行逐格对齐；
///  - 页面设置：orientation=portrait、fitToPage=1、四边 margins=0.25/0.25/0.3/0.3、
///    header/footer=0.5/0.5（fitToWidth/fitToHeight 因 NPOI DefaultValue(1) 不会落盘，
///    语义等价，详见 README_T2 第 5 节）。
/// （列宽/行高 diff 不比较，故不强制对齐，仅做基本可用性设置。）
/// </summary>
public static class SettlementReportExporter
{
    private static readonly int[] AllMonths = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 };
    private static readonly string[] Header = { "序号", "项目", "本期", "本年累计", "备注" };

    public static string ExportReport(
        SqliteConnection conn, string outPath, int year, int month, List<string> persons)
    {
        // 输出目录常常不存在（csharp/out 已被 .gitignore 忽略，克隆/同步后不会自带），
        // 不先建目录 FileStream 会抛 DirectoryNotFoundException（"找不到路径…的一部分"）。
        var outDir = Path.GetDirectoryName(Path.GetFullPath(outPath));
        if (!string.IsNullOrEmpty(outDir)) Directory.CreateDirectory(outDir);

        if (persons == null || persons.Count == 0)
        {
            // 与 Python 导出器一致：空名单兜底为合法占位 xlsx（>=1 sheet），避免无效 workbook。
            // 注意：此处变量名不能与外层作用域的 wb / fs 同名 —— C# 规则 CS0136 禁止在嵌套
            // 作用域声明与封闭作用域同名的局部变量（与声明先后顺序无关），故加 Empty 后缀。
            var wbEmpty = new XSSFWorkbook();
            var wsEmpty = wbEmpty.CreateSheet("无数据");
            wsEmpty.CreateRow(0).CreateCell(0).SetCellValue("无结算数据");
            wsEmpty.CreateRow(1).CreateCell(0).SetCellValue($"{year}年无结算人员数据，未生成结算表。");
            // 与 Python golden 对齐页边距：Python 的占位 sheet 没有走 _write_sheet，
            // 因此沿用 openpyxl 的默认页边距（left/right=0.75、top/bottom=1.0）；
            // 而 NPOI 的默认是 0.7/0.7/0.75/0.75，若不显式写出，逐格 diff 会在
            // page_setup 维度报 4 处不一致。此处显式写成 Python 侧的实际值。
            // （方向/缩放两边默认一致，已验证无差异，故不动以免引入新的不一致。）
            wsEmpty.SetMargin(MarginType.LeftMargin, 0.75f);
            wsEmpty.SetMargin(MarginType.RightMargin, 0.75f);
            wsEmpty.SetMargin(MarginType.TopMargin, 1.0f);
            wsEmpty.SetMargin(MarginType.BottomMargin, 1.0f);
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath))!);
            using var fsEmpty = new FileStream(outPath, FileMode.Create, FileAccess.Write);
            wbEmpty.Write(fsEmpty);
            return outPath;
        }
        var dataAll = SettlementEngine.Build(conn, year);
        var ordered = ExpenseCatHelper.OrderedTypes(conn);
        var wb = new XSSFWorkbook();
        foreach (var person in persons)
        {
            if (person == "公共费用")
            {
                var st = dataAll.TryGetValue("公共", out var g) ? g : new PersonSettlement();
                var ws = wb.CreateSheet("公共费用");
                WriteSheet(ws, "公共费用", st, year, month, ordered);
                continue;
            }
            bool anyData = false;
            foreach (var ptype in new[] { "合伙", "聘用", "兼职" })
            {
                var st = SettlementEngine.Build(conn, year, person, ptype).GetValueOrDefault(person);
                if (st != null && HasData(st, month))
                {
                    var ws = wb.CreateSheet(person + ptype);
                    WriteSheet(ws, person, st, year, month, ordered);
                    anyData = true;
                }
            }
            var stAll = dataAll.GetValueOrDefault(person);
            if (stAll != null && HasData(stAll, month))
            {
                var ws = wb.CreateSheet(person + "汇总");
                WriteSheet(ws, person, stAll, year, month, ordered);
                anyData = true;
            }
            if (!anyData)
            {
                var ws = wb.CreateSheet(person + "汇总");
                WriteSheet(ws, person, new PersonSettlement(), year, month, ordered);
            }
        }
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath))!);
        using var fs = new FileStream(outPath, FileMode.Create, FileAccess.Write);
        wb.Write(fs);
        return outPath;
    }

    // ------------------------------------------------------------------
    private static bool HasData(PersonSettlement st, int month)
    {
        var m = st.Months[month];
        double[] vals =
        {
            m.RecOpenCur, m.RecCurYear, m.RecPrevYear, m.RecRefundCur, m.RecRefundPrev,
            m.InvOpenReceived, m.InvOpenUncollected, m.InvRedCur, m.InvRedPrev, m.Income,
        };
        if (vals.Any(v => Math.Abs(v) > 0.01)) return true;
        if (st.UncollectedMonth.GetValueOrDefault(month, 0) > 0.01) return true;
        if (st.Expenses.Values.Sum(e => e.GetValueOrDefault(month, 0.0)) > 0.01) return true;
        return false;
    }

    private static void WriteSheet(
        ISheet ws, string person, PersonSettlement st, int year, int month, List<string> ordered)
    {
        var wbk = ws.Workbook;
        var thin = BorderStyle.Thin;

        ICellStyle Make(bool bold, HorizontalAlignment align, bool numeric)
        {
            var s = wbk.CreateCellStyle();
            s.BorderTop = thin; s.BorderBottom = thin; s.BorderLeft = thin; s.BorderRight = thin;
            s.Alignment = align;
            if (numeric) s.DataFormat = wbk.CreateDataFormat().GetFormat("#,##0.00");
            if (bold) { var f = wbk.CreateFont(); f.IsBold = true; s.SetFont(f); }
            return s;
        }
        var centerText = Make(false, HorizontalAlignment.Center, false);
        var centerTextBold = Make(true, HorizontalAlignment.Center, false);
        var leftText = Make(false, HorizontalAlignment.Left, false);
        var leftTextBold = Make(true, HorizontalAlignment.Left, false);
        var num = Make(false, HorizontalAlignment.Right, true);
        var numBold = Make(true, HorizontalAlignment.Right, true);

        // 标题（A1，合并 A1:E1）
        var r0 = ws.CreateRow(0);
        var cA1 = r0.CreateCell(0);
        cA1.SetCellValue("浙江震天律师事务所");
        var titleFont = wbk.CreateFont();
        titleFont.IsBold = true;
        titleFont.FontHeightInPoints = 13;
        var titleStyle = wbk.CreateCellStyle();
        titleStyle.SetFont(titleFont);
        titleStyle.Alignment = HorizontalAlignment.Center;
        cA1.CellStyle = titleStyle;
        ws.AddMergedRegion(new CellRangeAddress(0, 0, 0, 4));

        // 第二行：姓名 / 期间
        var r1 = ws.CreateRow(1);
        r1.CreateCell(1).SetCellValue($"姓名：{person}");
        r1.CreateCell(2).SetCellValue($"二〇{year % 100:00}年{ChineseTitle(month)}结算表");

        // 表头（第 3 行）
        var r2 = ws.CreateRow(2);
        for (int j = 0; j < 5; j++)
        {
            var c = r2.CreateCell(j);
            c.SetCellValue(Header[j]);
            c.CellStyle = centerTextBold;
        }

        // 数据行
        foreach (var (seq, name, cur, total, bold) in BuildReportRows(st, year, month, ordered))
        {
            int rr = ws.LastRowNum + 1;
            var row = ws.CreateRow(rr);
            var ca = row.CreateCell(0);
            // seq 可能为 ""（结余行）。openpyxl 写空串时落盘为「无值单元格」，
            // 而 NPOI 的 SetCellValue("") 会落盘成空字符串，diff 会读成 '' ≠ None。
            // 因此空 seq 只建单元格套样式、不写值，保持与 golden 一致。
            if (!string.IsNullOrEmpty(seq)) ca.SetCellValue(seq);
            ca.CellStyle = bold ? centerTextBold : centerText;
            var cb = row.CreateCell(1);
            cb.SetCellValue(name);
            cb.CellStyle = bold ? leftTextBold : leftText;
            if (cur.HasValue)
            {
                var cc = row.CreateCell(2);
                cc.SetCellValue(Math.Round(cur.Value, 2));
                cc.CellStyle = bold ? numBold : num;
            }
            if (total.HasValue)
            {
                var cd = row.CreateCell(3);
                cd.SetCellValue(Math.Round(total.Value, 2));
                cd.CellStyle = bold ? numBold : num;
            }
            if (name == "本月收款金额")
            {
                var note = ReportNoteRec(st, month);
                if (!string.IsNullOrEmpty(note)) row.CreateCell(4).SetCellValue(note);
            }
            else if (name == "本月开具发票金额")
            {
                var note = ReportNoteInv(st, month);
                if (!string.IsNullOrEmpty(note)) row.CreateCell(4).SetCellValue(note);
            }
        }

        // 页面设置（对照 diff_xlsx 第 6 维）
        ws.PrintSetup.Landscape = false;     // portrait
        ws.FitToPage = true;
        // NPOI 的 IPrintSetup 属性名为 FitWidth / FitHeight（对应 POI 的 setFitWidth/setFitHeight，
        // 类型为 short），不存在 FitToWidth / FitToHeight —— 后者是 openpyxl 的属性名。
        ws.PrintSetup.FitWidth = (short)1;
        ws.PrintSetup.FitHeight = (short)1;
        // NPOI 2.7.2 的 CT_PageSetup.fitToWidth/fitToHeight 带 [DefaultValue(1)]，
        // XmlSerializer 会在「等于默认值」时省略该属性 —— 与 openpyxl 显式写 1 语义等价，
        // diff_xlsx 侧按 schema 默认值归一（None 视为 1），此处无需再处理。
        // 页边距必须用 double 字面量：写 0.3f（float）会存成 0.30000001192092896。
        ws.SetMargin(MarginType.LeftMargin, 0.25);
        ws.SetMargin(MarginType.RightMargin, 0.25);
        ws.SetMargin(MarginType.TopMargin, 0.3);
        ws.SetMargin(MarginType.BottomMargin, 0.3);
        ws.SetMargin(MarginType.HeaderMargin, 0.5);
        ws.SetMargin(MarginType.FooterMargin, 0.5);
    }

    // ------------------------------------------------------------------
    // 行数据（镜像 build_report_rows）
    // ------------------------------------------------------------------
    private static List<(string seq, string name, double? cur, double? total, bool bold)> BuildReportRows(
        PersonSettlement st, int year, int month, List<string> ordered)
    {
        var m = st.Months[month];
        double Cum(Func<int, double> f) => Math.Round(AllMonths.Take(month).Sum(f), 2);
        var rows = new List<(string, string, double?, double?, bool)>();
        void Row(string seq, string name, double? cur, double? total, bool bold = false)
            => rows.Add((seq, name,
                cur is double c ? Math.Round(c, 2) : (double?)null,
                total is double t ? Math.Round(t, 2) : (double?)null,
                bold));

        Row("一", "上年结余结转", null, null);
        var recKeys = new Func<int, double>[]
        {
            mm => st.Months[mm].RecOpenCur, mm => st.Months[mm].RecCurYear, mm => st.Months[mm].RecPrevYear,
            mm => st.Months[mm].RecRefundCur, mm => st.Months[mm].RecRefundPrev,
        };
        double MonthTotal(Func<int, double>[] ks) => Math.Round(ks.Sum(k => k(month)), 2);
        double Cur2 = MonthTotal(recKeys);
        double Tot2 = Math.Round(AllMonths.Take(month).Sum(mm => recKeys.Sum(k => k(mm))), 2);
        Row("二", "本月收款金额", Cur2, Tot2, true);
        Row("1", "本月开票本月收款", m.RecOpenCur, Cum(mm => st.Months[mm].RecOpenCur));
        Row("2", "收回以前应收款", Math.Round(m.RecCurYear + m.RecPrevYear, 2),
            Cum(mm => st.Months[mm].RecCurYear + st.Months[mm].RecPrevYear));
        double Cur3 = m.InvTotal;
        double Tot3 = Math.Round(AllMonths.Take(month).Sum(mm => st.Months[mm].InvTotal), 2);
        Row("三", "本月开具发票金额", Cur3, Tot3, true);
        Row("1", "本月开票已收款", m.InvOpenReceived, Cum(mm => st.Months[mm].InvOpenReceived));
        Row("2", "本月开票未收款", m.InvOpenUncollected, Cum(mm => st.Months[mm].InvOpenUncollected));
        Row("四", "未收款金额", st.UncollectedMonth.GetValueOrDefault(month, 0), st.UncollectedTotal, true);
        Row("五", "业务收入", m.Income, Cum(mm => st.Months[mm].Income), true);

        // 六、减：分成报酬及费用（排除折旧/银行结息/城建税及附加）
        string[] exclude = { "折旧", "银行结息", "城建税", "教育附加" };
        var exp = st.Expenses
            .Where(kv => !exclude.Any(ex => kv.Key.Contains(ex)))
            .ToDictionary(kv => kv.Key, kv => kv.Value);
        double Cur6 = Math.Round(exp.Values.Sum(e => e.GetValueOrDefault(month, 0)), 2);
        double Tot6 = Math.Round(exp.Values.Sum(e => AllMonths.Take(month).Sum(mm => e.GetValueOrDefault(mm, 0))), 2);
        Row("六", "减：分成报酬及费用", Cur6, Tot6, true);

        var expOrder = ordered.Select((t, i) => (t, i)).ToDictionary(x => x.t, x => x.i);
        var expTypes = exp.Keys
            .OrderBy(t => expOrder.GetValueOrDefault(t, 999))
            .ThenBy(t => t, StringComparer.Ordinal)
            .ToList();
        int idx = 1;
        foreach (var etype in expTypes)
        {
            Row(idx.ToString(), etype,
                exp[etype].GetValueOrDefault(month, 0),
                Cum(mm => exp[etype].GetValueOrDefault(mm, 0)));
            idx++;
        }

        double taxCur = Tax(Math.Max(Cur3, 0));
        double taxTot = Tax(Math.Max(Tot3, 0));
        Row("七", "减：税金及会费", Math.Round(taxCur, 2), Math.Round(taxTot, 2), true);
        Row("1", "增值税、城建及附加", taxCur, taxTot);
        Row("2", "抵扣进项", null, null);

        double cur5 = m.Income - Cur6 - taxCur;
        double tot5 = Cum(mm => st.Months[mm].Income) - Tot6 - taxTot;
        Row("", "结余", Math.Round(cur5, 2), Math.Round(tot5, 2), true);
        return rows;
    }

    private static double Tax(double amount) => Math.Round(amount / 1.06 * 0.06 * 1.12, 2);

    private static string ChineseTitle(int month)
    {
        const string cn = "一二三四五六七八九十";
        if (month <= 10) return cn[month - 1] + "月";
        return "十" + (month > 11 ? cn[month - 11].ToString() : "") + "月";
    }

    private static string Fmt(double x) => x.ToString("#,##0.00", CultureInfo.InvariantCulture);

    private static string ReportNoteRec(PersonSettlement st, int month)
    {
        var m = st.Months[month];
        var notes = new List<string>();
        if (m.RecCurYear > 0.01) notes.Add($"本月收回本年应收款{Fmt(m.RecCurYear)}元");
        if (m.RecPrevYear > 0.01) notes.Add($"本月收回上年应收款{Fmt(m.RecPrevYear)}元");
        if (m.RecRefundCur < -0.01) notes.Add($"本月退本年应收款{Fmt(Math.Abs(m.RecRefundCur))}元");
        if (m.RecRefundPrev < -0.01) notes.Add($"本月退上年应收款{Fmt(Math.Abs(m.RecRefundPrev))}元");
        double cumPrevYear = AllMonths.Take(month).Sum(mm => st.Months[mm].RecPrevYear);
        double cumRefundPrev = AllMonths.Take(month).Sum(mm => st.Months[mm].RecRefundPrev);
        if (cumPrevYear > 0.01) notes.Add($"本年累计收回上年应收款{Fmt(cumPrevYear)}元");
        if (cumRefundPrev < -0.01) notes.Add($"本年累计退上年应收款{Fmt(Math.Abs(cumRefundPrev))}元");
        return string.Join("\n", notes);
    }

    private static string ReportNoteInv(PersonSettlement st, int month)
    {
        var m = st.Months[month];
        var notes = new List<string>();
        if (m.InvRedCur < -0.01) notes.Add($"本月红冲本年{Fmt(Math.Abs(m.InvRedCur))}元");
        if (m.InvRedPrev < -0.01) notes.Add($"本月红冲上年{Fmt(Math.Abs(m.InvRedPrev))}元");
        double cumRedPrev = AllMonths.Take(month).Sum(mm => st.Months[mm].InvRedPrev);
        if (cumRedPrev < -0.01) notes.Add($"本年累计红冲上年{Fmt(Math.Abs(cumRedPrev))}元");
        return string.Join("\n", notes);
    }
}
