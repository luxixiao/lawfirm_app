using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using LawFirm.Data;
using LawFirm.Exporter;
using LawFirm.UI.ViewModels;
using Microsoft.Data.Sqlite;

namespace LawFirm.UI.Services;

/// <summary>
/// 结算查询服务（G6/T5.3）：包 SettlementEngine，产出可直接绑定的行模型。
///
/// - 全部方法**同步**实现；VM 经 Task.Run 在后台线程调用。
/// - **只读纪律**：连接在服务方法内经 <see cref="DbConnection.OpenReadOnly"/> 自建
///   （query_only + ReadOnly），UI 层无任何写库调用（R11）。
/// - 行构建逻辑逐行移植 settlement_view.py / settlement_report_exporter.py /
///   staff_income_exporter.py / invoice_income_exporter.py 的预览函数。
/// </summary>
public static class SettlementQueryService
{
    public static readonly string[] MonthLabels =
    {
        "1月", "2月", "3月", "4月", "5月", "6月",
        "7月", "8月", "9月", "10月", "11月", "12月",
    };

    private static readonly string[] CnNum = { "一", "二", "三", "四", "五", "六", "七", "八", "九", "十" };
    private static readonly string[] PersonTypes = { "合伙", "聘用", "兼职" };

    private static SqliteConnection OpenConn() => DbConnection.OpenReadOnly(DbConnection.FindDatabase());

    private static double R2(double v) => Math.Round(v, 2);

    // ------------------------------------------------------------------
    // 通用
    // ------------------------------------------------------------------

    /// <summary>最近一个有数据的年份（从今年往前找 4 年，settlement_view.py:172-179）。</summary>
    public static int LatestDataYear()
    {
        int cur = DateTime.Now.Year;
        for (int y = cur; y > cur - 4; y--)
        {
            using var conn = OpenConn();
            if (SettlementEngine.Build(conn, y).Count > 0) return y;
        }
        return cur;
    }

    /// <summary>人员列表 = Build(year) 的 key 按 StringComparer.Ordinal 排序（验收 T5.3-⑦）。</summary>
    public static List<string> PersonNames(int year)
    {
        using var conn = OpenConn();
        return SettlementEngine.Build(conn, year).Keys
            .OrderBy(k => k, StringComparer.Ordinal).ToList();
    }

    /// <summary>该人员实际拥有的身份（_has_any 过滤；settlement_view.py:217-232）。</summary>
    public static List<string> AvailableTypes(int year, string person)
    {
        var available = new List<string>();
        using var conn = OpenConn();
        foreach (var pt in PersonTypes)
        {
            var st = SettlementEngine.Build(conn, year, person, pt).GetValueOrDefault(person);
            if (HasAny(st)) available.Add(pt);
        }
        return available;
    }

    /// <summary>该人员结构全年有任一数据（逐行移植 settlement_view.py:202-215）。</summary>
    private static bool HasAny(PersonSettlement? st)
    {
        if (st is null) return false;
        for (int mo = 1; mo <= 12; mo++)
        {
            MonthData m = st.Months[mo];
            if (Math.Abs(m.RecOpenCur) > 0.01 || Math.Abs(m.RecCurYear) > 0.01
                || Math.Abs(m.RecPrevYear) > 0.01 || Math.Abs(m.RecRefundCur) > 0.01
                || Math.Abs(m.RecRefundPrev) > 0.01 || Math.Abs(m.InvOpenReceived) > 0.01
                || Math.Abs(m.InvOpenUncollected) > 0.01 || Math.Abs(m.InvRedCur) > 0.01
                || Math.Abs(m.InvRedPrev) > 0.01 || Math.Abs(m.Income) > 0.01)
            {
                return true;
            }
        }
        double expSum = 0;
        foreach (var kv in st.Expenses)
        {
            for (int mo = 1; mo <= 12; mo++)
                expSum += kv.Value.GetValueOrDefault(mo, 0.0);
        }
        return expSum > 0.01;
    }

    // ------------------------------------------------------------------
    // Tab1 个人结算总表（_build_rows，settlement_view.py:280-333 逐行移植）
    // ------------------------------------------------------------------

    /// <summary>Tab1 行结果：行集合 + 列 key 集合（月模式列数会变）。</summary>
    public sealed record PersonalRowsResult(IReadOnlyList<GridRow> Rows, IReadOnlyList<string> ColumnKeys);

    /// <summary>构建 Tab1 行。month=0 全年（1~12 月列）；month=m 显示 1~m 月列。</summary>
    public static PersonalRowsResult BuildPersonalRows(int year, string person, string? personType, int month)
    {
        using var conn = OpenConn();
        var data = SettlementEngine.Build(conn, year, person, personType);
        var st = data.GetValueOrDefault(person);
        if (st is null)
            return new PersonalRowsResult(Array.Empty<GridRow>(), DefaultPersonalKeys(0));

        var m = st.Months;
        int lastMonth = month > 0 ? month : 12;          // 显示 1~lastMonth 月
        var colKeys = new List<string> { "项目" };
        for (int i = 0; i < lastMonth; i++) colKeys.Add(MonthLabels[i]);
        colKeys.Add("合计");

        double?[] Val(Func<MonthData, double> key)
        {
            var arr = new double?[12];
            for (int mo = 1; mo <= 12; mo++) arr[mo - 1] = R2(key(m[mo]));
            return arr;
        }
        double?[] ValSum(Func<MonthData, double>[] keys)
        {
            var arr = new double?[12];
            for (int mo = 1; mo <= 12; mo++) arr[mo - 1] = R2(keys.Sum(k => k(m[mo])));
            return arr;
        }
        double?[] Exp(string type)
        {
            var arr = new double?[12];
            var byMonth = st.Expenses.GetValueOrDefault(type);
            for (int mo = 1; mo <= 12; mo++) arr[mo - 1] = R2(byMonth?.GetValueOrDefault(mo, 0.0) ?? 0.0);
            return arr;
        }

        GridRow Row(string label, double?[] vals12, bool isSub, double? totalOverride = null)
        {
            var cells = new Dictionary<string, object?> { ["项目"] = (isSub ? "　" : "") + label };
            double picked = 0;
            bool any = false;
            for (int i = 0; i < lastMonth; i++)
            {
                cells[colKeys[i + 1]] = vals12[i];
                if (vals12[i] is double v) { picked += v; any = true; }
            }
            // 合计 = 当前显示各月之和（全年=全年累计；选月=1~mo 累计）；「四」行用存量口径覆盖
            cells["合计"] = totalOverride ?? (any ? R2(picked) : (double?)null);
            return new GridRow(cells, isBold: IsCategoryLabel(label));
        }

        GridRow NoneRow(string label)
        {
            var cells = new Dictionary<string, object?> { ["项目"] = label };
            for (int i = 0; i < lastMonth; i++) cells[colKeys[i + 1]] = null;
            cells["合计"] = null;
            return new GridRow(cells, isBold: true);
        }

        static Func<MonthData, double> F(Func<MonthData, double> f) => f;   // 数组字面量类型推断辅助

        var rows = new List<GridRow>();
        rows.Add(NoneRow($"▶ {st.StaffType}"));
        rows.Add(NoneRow("一、上年结余结转"));

        // 二
        rows.Add(Row("二、本月收款金额", ValSum(new[]
        {
            F(x => x.RecOpenCur), F(x => x.RecCurYear), F(x => x.RecPrevYear),
            F(x => x.RecRefundCur), F(x => x.RecRefundPrev),
        }), isSub: false));
        rows.Add(Row("1.本月开收", Val(x => x.RecOpenCur), true));
        rows.Add(Row("2.收本年", Val(x => x.RecCurYear), true));
        rows.Add(Row("3.收上年", Val(x => x.RecPrevYear), true));
        rows.Add(Row("4.退本年", Val(x => x.RecRefundCur), true));
        rows.Add(Row("5.退上年", Val(x => x.RecRefundPrev), true));
        // 三（小计 = 本月开票总额，独立计算，含预收票）
        rows.Add(Row("三、本月开具发票金额", Val(x => x.InvTotal), false));
        rows.Add(Row("1.本月开收", Val(x => x.InvOpenReceived), true));
        rows.Add(Row("2.本月未收", Val(x => x.InvOpenUncollected), true));
        rows.Add(Row("3.红冲本年", Val(x => x.InvRedCur), true));
        rows.Add(Row("4.红冲上年", Val(x => x.InvRedPrev), true));
        // 四（合计列 = 存量口径：全年 → UncollectedTotal；选月 → 1~mo 累计未收）
        double uncollectedCum = month > 0
            ? R2(Enumerable.Range(1, month).Sum(x => st.UncollectedMonth.GetValueOrDefault(x, 0.0)))
            : R2(st.UncollectedTotal);
        rows.Add(Row("四、未收款金额",
            Val(mo => st.UncollectedMonth.GetValueOrDefault(mo, 0.0)), false, uncollectedCum));
        // 五
        rows.Add(Row("五、业务收入", Val(x => x.Income), false));
        // 六（Σ 全部费用类型逐月）
        {
            var cells6 = new Dictionary<string, object?> { ["项目"] = "六、减：分成报酬及费用" };
            double sum6 = 0;
            for (int i = 0; i < lastMonth; i++)
            {
                double s = 0;
                foreach (var t in st.Expenses) s += st.Expenses[t].GetValueOrDefault(i + 1, 0.0);
                s = R2(s);
                cells6[colKeys[i + 1]] = s;
                sum6 += s;
            }
            cells6["合计"] = R2(sum6);
            rows.Add(new GridRow(cells6, isBold: true));
        }
        var expenseTypes = SortedExpenseTypes(conn, st.Expenses.Keys);
        for (int i = 0; i < expenseTypes.Count; i++)
        {
            rows.Add(Row($"{i + 1}.{expenseTypes[i]}", Exp(expenseTypes[i]), true));
        }

        return new PersonalRowsResult(rows, colKeys);
    }

    /// <summary>全年默认列 key。maxKeys&lt;=0 = 全部 12 月（无限制，QA B1 调用口径）；&gt;0 = 前 maxKeys 个月。</summary>
    private static List<string> DefaultPersonalKeys(int maxKeys = 0)
    {
        var keys = new List<string> { "项目" };
        keys.AddRange(maxKeys > 0 ? MonthLabels.Take(maxKeys) : MonthLabels);
        keys.Add("合计");
        return keys;
    }

    /// <summary>粗体判定：一、..十、 或 ▶ 开头（settlement_view.py:263-264）。</summary>
    public static bool IsCategoryLabel(string? label)
    {
        if (string.IsNullOrEmpty(label)) return false;
        if (label.StartsWith("▶")) return true;
        foreach (var cn in CnNum)
        {
            if (label.StartsWith(cn + "、")) return true;
        }
        return false;
    }

    /// <summary>费用类型按维护顺序排序，未维护的按名称兜底（settlement_view.py:336-340）。</summary>
    public static List<string> SortedExpenseTypes(SqliteConnection conn, IEnumerable<string> types)
    {
        var order = ExpenseCatHelper.OrderedTypes(conn)
            .Select((t, i) => (t, i))
            .ToDictionary(x => x.t, x => x.i);
        return types.OrderBy(t => (order.TryGetValue(t, out int i) ? i : 999, t), Comparer<(int, string)>.Default).ToList();
    }

    // ------------------------------------------------------------------
    // Tab2 月度结算表（build_report_rows / report_note_*，settlement_report_exporter.py:51-141）
    // ------------------------------------------------------------------

    /// <summary>Tab2 行模型（seq+name 合并成一列展示）。</summary>
    public sealed record ReportRow(string Label, double? Current, double? Total, bool Bold, string? Note);

    /// <summary>Tab2 行结果：行集合 + 摘要后缀（无数据时 Rows 空）。</summary>
    public sealed record MonthlyRowsResult(IReadOnlyList<ReportRow> Rows, PersonSettlement? St);

    public static MonthlyRowsResult BuildMonthlyRows(int year, string person, string? personType, int month)
    {
        using var conn = OpenConn();
        var data = SettlementEngine.Build(conn, year, person, personType);
        var st = data.GetValueOrDefault(person);
        if (st is null)
            return new MonthlyRowsResult(Array.Empty<ReportRow>(), null);
        return new MonthlyRowsResult(BuildMonthlyRowsCore(st, month), st);
    }

    private static List<ReportRow> BuildMonthlyRowsCore(PersonSettlement st, int month)
    {
        var m = st.Months;
        var rows = new List<ReportRow>();
        double Cum(Func<int, double> monthValue) => R2(Enumerable.Range(1, month).Sum(monthValue));
        double MonthTotal(int mo, Func<MonthData, double>[] keys) => R2(keys.Sum(k => k(m[mo])));

        void Row(string seq, string name, double? cur, double? total, bool bold = false, string? note = null)
            => rows.Add(new ReportRow(MergeSeqName(seq, name),
                cur is null ? null : R2(cur.Value),
                total is null ? null : R2(total.Value), bold, note));

        var recKeys = new Func<MonthData, double>[]
        {
            x => x.RecOpenCur, x => x.RecCurYear, x => x.RecPrevYear,
            x => x.RecRefundCur, x => x.RecRefundPrev,
        };

        // 一
        Row("一", "上年结余结转", null, null);
        // 二
        double cur2 = MonthTotal(month, recKeys);
        double tot2 = R2(Enumerable.Range(1, month).Sum(mo => MonthTotal(mo, recKeys)));
        Row("二", "本月收款金额", cur2, tot2, bold: true, note: ReportNoteRec(st, month));
        Row("1", "本月开票本月收款", m[month].RecOpenCur, Cum(mo => m[mo].RecOpenCur));
        Row("2", "收回以前应收款", R2(m[month].RecCurYear + m[month].RecPrevYear),
            Cum(mo => R2(m[mo].RecCurYear + m[mo].RecPrevYear)));
        // 三
        double cur3 = m[month].InvTotal;
        double tot3 = R2(Enumerable.Range(1, month).Sum(mo => m[mo].InvTotal));
        Row("三", "本月开具发票金额", cur3, tot3, bold: true, note: ReportNoteInv(st, month));
        Row("1", "本月开票已收款", m[month].InvOpenReceived, Cum(mo => m[mo].InvOpenReceived));
        Row("2", "本月开票未收款", m[month].InvOpenUncollected, Cum(mo => m[mo].InvOpenUncollected));
        // 四
        Row("四", "未收款金额", st.UncollectedMonth.GetValueOrDefault(month, 0.0), st.UncollectedTotal, bold: true);
        // 五
        Row("五", "业务收入", m[month].Income, Cum(mo => m[mo].Income), bold: true);
        // 六（排除折旧/银行结息/城建税及附加——模板口径）
        var exclude = new[] { "折旧", "银行结息", "城建税", "教育附加" };
        var exp = st.Expenses
            .Where(kv => !exclude.Any(k => kv.Key.Contains(k, StringComparison.Ordinal)))
            .ToDictionary(kv => kv.Key, kv => kv.Value);
        double cur6 = R2(exp.Sum(t => t.Value.GetValueOrDefault(month, 0.0)));
        double tot6 = R2(exp.Sum(t => Enumerable.Range(1, month).Sum(mo => t.Value.GetValueOrDefault(mo, 0.0))));
        Row("六", "减：分成报酬及费用", cur6, tot6, bold: true);
        var expOrder = ExpenseOrder(exp.Keys);
        int i = 0;
        foreach (var etype in exp.Keys.OrderBy(t => (expOrder.TryGetValue(t, out int o) ? o : 999, t)))
        {
            i++;
            Row(i.ToString(), etype, exp[etype].GetValueOrDefault(month, 0.0),
                Cum(mo => exp[etype].GetValueOrDefault(mo, 0.0)));
        }
        // 七（增值税、城建及附加 = 开票净额 ÷ 1.06 × 6% × 1.12）
        double Tax(double amount) => R2(amount / 1.06 * 0.06 * 1.12);
        double taxCur = Tax(Math.Max(cur3, 0.0));
        double taxTot = Tax(Math.Max(tot3, 0.0));
        Row("七", "减：税金及会费", taxCur, taxTot, bold: true);
        Row("1", "增值税、城建及附加", taxCur, taxTot);
        Row("2", "抵扣进项", null, null);
        // 结余 = 五 - 六 - 七
        Row("", "结余", R2(m[month].Income - cur6 - taxCur),
            R2(Cum(mo => m[mo].Income) - tot6 - taxTot), bold: true);
        return rows;
    }

    /// <summary>序号+项目合并（settlement_view.py:29-42：中文序号「、」，数字序号「.」）。</summary>
    public static string MergeSeqName(string? seq, string? name)
    {
        string s = (seq ?? string.Empty).Trim();
        string n = (name ?? string.Empty).Trim();
        if (s.Length == 0) return n;
        if (n.Length == 0) return s;
        string sep = s.Any(ch => CnNum.Contains(ch)) ? "、" : ".";
        return $"{s}{sep}{n}";
    }

    /// <summary>收款类备注（settlement_report_exporter.py:51-69）。</summary>
    public static string? ReportNoteRec(PersonSettlement st, int month)
    {
        var m = st.Months;
        var notes = new List<string>();
        if (m[month].RecCurYear > 0.01)
            notes.Add($"本月收回本年应收款{m[month].RecCurYear:N2}元");
        if (m[month].RecPrevYear > 0.01)
            notes.Add($"本月收回上年应收款{m[month].RecPrevYear:N2}元");
        if (m[month].RecRefundCur < -0.01)
            notes.Add($"本月退本年应收款{Math.Abs(m[month].RecRefundCur):N2}元");
        if (m[month].RecRefundPrev < -0.01)
            notes.Add($"本月退上年应收款{Math.Abs(m[month].RecRefundPrev):N2}元");
        double cumPrevYear = Enumerable.Range(1, month).Sum(mo => m[mo].RecPrevYear);
        double cumRefundPrev = Enumerable.Range(1, month).Sum(mo => m[mo].RecRefundPrev);
        if (cumPrevYear > 0.01)
            notes.Add($"本年累计收回上年应收款{cumPrevYear:N2}元");
        if (cumRefundPrev < -0.01)
            notes.Add($"本年累计退上年应收款{Math.Abs(cumRefundPrev):N2}元");
        return notes.Count > 0 ? string.Join("\n", notes) : null;
    }

    /// <summary>开票类备注（settlement_report_exporter.py:72-83）。</summary>
    public static string? ReportNoteInv(PersonSettlement st, int month)
    {
        var m = st.Months;
        var notes = new List<string>();
        if (m[month].InvRedCur < -0.01)
            notes.Add($"本月红冲本年{Math.Abs(m[month].InvRedCur):N2}元");
        if (m[month].InvRedPrev < -0.01)
            notes.Add($"本月红冲上年{Math.Abs(m[month].InvRedPrev):N2}元");
        double cumRedPrev = Enumerable.Range(1, month).Sum(mo => m[mo].InvRedPrev);
        if (cumRedPrev < -0.01)
            notes.Add($"本年累计红冲上年{Math.Abs(cumRedPrev):N2}元");
        return notes.Count > 0 ? string.Join("\n", notes) : null;
    }

    /// <summary>费用类型维护序（expense_cat 表，未维护 = 999）。</summary>
    private static Dictionary<string, int> ExpenseOrder(IEnumerable<string> types)
    {
        using var conn = OpenConn();
        var order = ExpenseCatHelper.OrderedTypes(conn)
            .Select((t, i) => (t, i))
            .ToDictionary(x => x.t, x => x.i);
        var result = new Dictionary<string, int>();
        foreach (var t in types)
            result[t] = order.TryGetValue(t, out int i) ? i : 999;
        return result;
    }

    // ------------------------------------------------------------------
    // Tab3 年度聘用结算表（staff_income_exporter.py:36-90 逐行移植）
    // ------------------------------------------------------------------

    /// <summary>Tab3 行模型（10 个数值列 + 姓名）。</summary>
    public sealed record StaffIncomeRow(
        string Name, double IncomeCur, double IncomeTot,
        double PayCur, double PayTot, double FundCur, double FundTot,
        double InsCur, double InsTot, double FuelCur, double FuelTot, bool IsTotal = false);

    public sealed record StaffIncomeResult(IReadOnlyList<StaffIncomeRow> Rows, int PersonCount);

    public static StaffIncomeResult BuildStaffIncomeRows(int year, int month)
    {
        using var conn = OpenConn();
        var data = SettlementEngine.Build(conn, year);
        var persons = StaffEmployees(conn, year, month);
        // 费用归类映射：expense_type → category（Python get_map()）
        var catMap = LoadExpenseCatMap(conn);
        var rows = new List<StaffIncomeRow>();
        foreach (var name in persons)
        {
            var st = data.GetValueOrDefault(name);
            if (st is null)
            {
                rows.Add(new StaffIncomeRow(name, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0));
                continue;
            }
            var m = st.Months;
            double incomeCur = m[month].Income;
            double incomeTot = R2(Enumerable.Range(1, month).Sum(mo => m[mo].Income));
            double[] fees = new double[8];
            var cols = new[]
            {
                (Cat: "报酬发放", Pay: 0), (Cat: "住房公积金", Pay: 2),
                (Cat: "保险费", Pay: 4), (Cat: "汽油费", Pay: 6),
            };
            foreach (var (cat, pay) in cols)
            {
                var types = catMap.Where(kv => kv.Value == cat).Select(kv => kv.Key).ToList();
                double cur = ExpSum(st, month, types);
                double tot = R2(Enumerable.Range(1, month).Sum(mo => ExpSum(st, mo, types)));
                fees[pay] = cur;
                fees[pay + 1] = tot;
            }
            rows.Add(new StaffIncomeRow(name, incomeCur, incomeTot,
                fees[0], fees[1], fees[2], fees[3], fees[4], fees[5], fees[6], fees[7]));
        }
        // 合计行（10 个数值列）
        var totals = SumTotals(rows, r => new[]
        {
            r.IncomeCur, r.IncomeTot, r.PayCur, r.PayTot, r.FundCur,
            r.FundTot, r.InsCur, r.InsTot, r.FuelCur, r.FuelTot,
        });
        rows.Add(new StaffIncomeRow("合计", totals[0], totals[1], totals[2], totals[3],
            totals[4], totals[5], totals[6], totals[7], totals[8], totals[9], IsTotal: true));
        return new StaffIncomeResult(rows, persons.Count);
    }

    /// <summary>聘用/兼职（视同聘用）名单；入职月份晚于当前月排除（staff_income_exporter.py:36-50）。</summary>
    private static List<string> StaffEmployees(SqliteConnection conn, int year, int month)
    {
        var result = new List<string>();
        string cur = $"{year:D4}-{month:D2}";
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT name, hire_month FROM staff "
            + "WHERE staff_type LIKE '%聘用%' OR staff_type LIKE '%兼职%' ORDER BY name";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            string name = r.IsDBNull(0) ? "" : r.GetString(0);
            string hm = r.IsDBNull(1) ? "" : r.GetString(1).Trim();
            if (hm.Length > 0 && string.CompareOrdinal(hm, cur) > 0) continue;   // 尚未入职
            result.Add(name);
        }
        return result;
    }

    private static double ExpSum(PersonSettlement st, int month, List<string> types)
        => R2(types.Sum(t => st.Expenses.GetValueOrDefault(t)?.GetValueOrDefault(month, 0.0) ?? 0.0));

    private static Dictionary<string, string> LoadExpenseCatMap(SqliteConnection conn)
    {
        var map = new Dictionary<string, string>();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT expense_type, category FROM expense_cat";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            if (!r.IsDBNull(0)) map[r.GetString(0)] = r.IsDBNull(1) ? "其他" : r.GetString(1);
        }
        return map;
    }

    // ------------------------------------------------------------------
    // Tab4 开票收入表（invoice_income_exporter.py:32-87 逐行移植）
    // ------------------------------------------------------------------

    /// <summary>Tab4 行模型（6 个数值列 + 姓名）。</summary>
    public sealed record InvoiceIncomeRow(
        string Name, double InvCur, double InvTot, double Uncollected,
        double OpenRecv, double PrevRecv, double TotalRecv, bool IsTotal = false);

    public sealed record InvoiceIncomeResult(IReadOnlyList<InvoiceIncomeRow> Rows, int PersonCount);

    public static InvoiceIncomeResult BuildInvoiceIncomeRows(int year, int month)
    {
        using var conn = OpenConn();
        var data = SettlementEngine.Build(conn, year);
        var persons = MainPersons(conn, year, month);
        var rows = new List<InvoiceIncomeRow>();
        // _build_main_rows 按 data.keys() 遍历（跳过「公共」），与 persons 名单解耦（Python 同款行为）
        foreach (var name in data.Keys.OrderBy(k => k, StringComparer.Ordinal))
        {
            if (name == "公共") continue;
            var st = data.GetValueOrDefault(name);
            if (st is null)
            {
                rows.Add(new InvoiceIncomeRow(name, 0, 0, 0, 0, 0, 0));
                continue;
            }
            var m = st.Months[month];
            double invCur = m.InvTotal;
            double invTot = R2(Enumerable.Range(1, month).Sum(mo => st.Months[mo].InvTotal));
            double uncol = R2(st.UncollectedTotal);
            double openRecv = m.InvOpenReceived;
            double prevRecv = R2(m.RecCurYear + m.RecPrevYear + m.RecRefundCur + m.RecRefundPrev);
            double totalRecv = R2(openRecv + prevRecv);
            rows.Add(new InvoiceIncomeRow(name, invCur, invTot, uncol, openRecv, prevRecv, totalRecv));
        }
        var totals = SumTotals(rows, r => new[]
        {
            r.InvCur, r.InvTot, r.Uncollected, r.OpenRecv, r.PrevRecv, r.TotalRecv,
        });
        rows.Add(new InvoiceIncomeRow("合计", totals[0], totals[1], totals[2],
            totals[3], totals[4], totals[5], IsTotal: true));
        return new InvoiceIncomeResult(rows, persons.Count);
    }

    /// <summary>主表名单：合伙/聘用/兼职 且在职；入职晚于当前月排除（invoice_income_exporter.py:32-45）。</summary>
    private static List<string> MainPersons(SqliteConnection conn, int year, int month)
    {
        var result = new List<string>();
        string cur = $"{year:D4}-{month:D2}";
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT name, hire_month FROM staff "
            + "WHERE staff_type IN ('合伙','聘用','兼职') AND is_active=1 ORDER BY name";
        using var r = cmd.ExecuteReader();
        while (r.Read())
        {
            string name = r.IsDBNull(0) ? "" : r.GetString(0);
            string hm = r.IsDBNull(1) ? "" : r.GetString(1).Trim();
            if (hm.Length > 0 && string.CompareOrdinal(hm, cur) > 0) continue;
            result.Add(name);
        }
        return result;
    }

    // ------------------------------------------------------------------
    // 共用
    // ------------------------------------------------------------------

    private static double[] SumTotals<T>(List<T> rows, Func<T, double[]> values)
    {
        int n = rows.Count > 0 ? values(rows[0]).Length : 0;
        var totals = new double[n];
        foreach (var row in rows)
        {
            var vs = values(row);
            for (int j = 0; j < n; j++) totals[j] += vs[j];
        }
        for (int j = 0; j < n; j++) totals[j] = R2(totals[j]);
        return totals;
    }
}
