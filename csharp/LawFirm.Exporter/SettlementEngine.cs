using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Data.Sqlite;

namespace LawFirm.Exporter;

/// <summary>
/// 结算引擎（Pilot T2，C# 端口）。
///
/// 逐行镜像 Python 侧 app/engine/person_settlement.py 的 _compute / allocate_receipt /
/// _allocated_total / _staff_type，读取同一份 data/lawfirm.db（经 LawFirm.Data 只读打开）。
/// 金额全部使用 double（与 Python float 同为 IEEE-754 double），保证两侧算术一致、
/// diff_xlsx.py 的 1e-6 容差下数值可逐格对齐。仅在导出层格式化时 round 到 2 位。
///
/// 公共 API：Build(conn, year) / Build(conn, year, person, personType)。
/// </summary>
public static class SettlementEngine
{
    private static readonly int[] Months = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 };

    // ---- 公共 API ----
    public static Dictionary<string, PersonSettlement> Build(
        SqliteConnection conn, int year, string? person = null, string? personType = null)
        => Compute(conn, year, person, personType);

    // ------------------------------------------------------------------
    // 计算核心（镜像 _compute）
    // ------------------------------------------------------------------
    private static Dictionary<string, PersonSettlement> Compute(
        SqliteConnection conn, int year, string? person, string? personType)
    {
        var result = new Dictionary<string, PersonSettlement>();

        // ============ 收款分摊（按经办人）============
        var ipb = new Pb();
        var invClauses = new List<string>();
        if (person != null)
            invClauses.Add($"EXISTS (SELECT 1 FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no AND cd.person_name = {ipb.Add(person)})");
        if (personType != null)
            invClauses.Add($"EXISTS (SELECT 1 FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no AND cd.person_type = {ipb.Add(personType)})");
        string invWhere = invClauses.Count > 0 ? string.Join(" AND ", invClauses) : "1=1";
        var invoices = Q(conn,
            $"SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no " +
            $"FROM invoice i WHERE i.total_amount >= 0 AND {invWhere} ORDER BY i.invoice_date, i.invoice_no",
            ipb.Values);

        var rdpb = new Pb();
        var rdClauses = new List<string>();
        if (person != null)
            rdClauses.Add($"EXISTS (SELECT 1 FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no AND cd.person_name = {rdpb.Add(person)})");
        if (personType != null)
            rdClauses.Add($"EXISTS (SELECT 1 FROM charge_detail cd WHERE cd.invoice_no = i.invoice_no AND cd.person_type = {rdpb.Add(personType)})");
        string rdWhere = rdClauses.Count > 0 ? string.Join(" AND ", rdClauses) : "1=1";
        var reds = Q(conn,
            $"SELECT i.invoice_no, i.invoice_date, i.total_amount, i.orig_invoice_no " +
            $"FROM invoice i WHERE i.total_amount < 0 AND {rdWhere} ORDER BY i.invoice_date",
            rdpb.Values);

        // 红冲映射：orig_invoice_no -> {person: 红冲计费额}
        var redByOrig = new Dictionary<string, Dictionary<string, double>>();
        foreach (var red in reds)
        {
            if (YearOf(Str(red["invoice_date"])) != year) continue;
            string? orig = Str(red["orig_invoice_no"]);
            if (string.IsNullOrEmpty(orig)) continue;
            var rpb = new Pb();
            var rsql = "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no="
                       + rpb.Add(Str(red["invoice_no"]))
                       + (personType != null ? " AND person_type=" + rpb.Add(personType) : "")
                       + " ORDER BY id";
            var cdsR = Q(conn, rsql, rpb.Values);
            foreach (var cdR in cdsR)
            {
                string pname = Str(cdR["person_name"])!;
                double billing = Math.Abs(Num(cdR["billing_amount"]));
                if (!redByOrig.ContainsKey(orig!)) redByOrig[orig!] = new Dictionary<string, double>();
                var d = redByOrig[orig!];
                d[pname] = d.GetValueOrDefault(pname, 0.0) + billing;
            }
        }

        var receiptsByInv = new Dictionary<string, List<Dictionary<string, object?>>>();
        foreach (var inv in invoices)
        {
            string no = Str(inv["invoice_no"])!;
            receiptsByInv[no] = Q(conn,
                "SELECT amount, receipt_date, person_name FROM collection WHERE invoice_no=@p0 ORDER BY id", no);
        }

        var cdsByInv = new Dictionary<string, List<Dictionary<string, object?>>>();
        foreach (var inv in invoices)
        {
            string no = Str(inv["invoice_no"])!;
            var cpb = new Pb();
            var csql = "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=" + cpb.Add(no)
                       + (personType != null ? " AND person_type=" + cpb.Add(personType) : "") + " ORDER BY id";
            cdsByInv[no] = Q(conn, csql, cpb.Values);
        }

        foreach (var inv in invoices)
        {
            string no = Str(inv["invoice_no"])!;
            int invYear = YearOf(Str(inv["invoice_date"]));
            int invMonth = MonthOf(Str(inv["invoice_date"]));
            var cds = cdsByInv.GetValueOrDefault(no, new List<Dictionary<string, object?>>());
            if (cds.Count == 0) continue;

            var remaining = new Dictionary<string, double>();
            foreach (var cd in cds) remaining[Str(cd["person_name"])!] = Num(cd["billing_amount"]);

            // 逐笔收款分摊
            foreach (var rec in receiptsByInv.GetValueOrDefault(no, new List<Dictionary<string, object?>>()))
            {
                double amount = Num(rec["amount"]);
                string? pname = Str(rec["person_name"]);
                Dictionary<string, double> got;
                if (!string.IsNullOrEmpty(pname) && remaining.ContainsKey(pname))
                {
                    got = amount > 0 ? new Dictionary<string, double> { [pname] = amount } : new Dictionary<string, double>();
                    remaining[pname] = Math.Max(remaining[pname] - amount, 0.0);
                }
                else
                {
                    got = AllocateReceipt(remaining, amount);
                }
                int recYear = YearOf(Str(rec["receipt_date"]));
                int recMonth = MonthOf(Str(rec["receipt_date"]));
                if (recYear != year) continue;
                foreach (var kv in got)
                {
                    if (kv.Value == 0) continue;
                    var st = GetOrCreate(result, conn, kv.Key, personType);
                    var m = st.Months[recMonth];
                    if (recYear == invYear && recMonth == invMonth)
                    {
                        // ①由开票循环统一计算（=本月开票已收，含预收）
                    }
                    else if (recYear == year && invYear == year && recMonth > invMonth)
                        m.RecCurYear += kv.Value;        // ②收本年
                    else if (invYear < year)
                        m.RecPrevYear += kv.Value;       // ③收上年
                    else
                        m.RecCurYear += kv.Value;        // 兜底
                }
            }

            // 开票归类（含未收）
            foreach (var cd in cds)
            {
                if (invYear != year) continue;
                string name = Str(cd["person_name"])!;
                double billing = Num(cd["billing_amount"]);
                var st = GetOrCreate(result, conn, name, personType);
                var m = st.Months[invMonth];
                double gotTotal = AllocatedTotal(cds, name,
                    receiptsByInv.GetValueOrDefault(no, new List<Dictionary<string, object?>>())).Sum();
                double redCut = redByOrig.GetValueOrDefault(no, new Dictionary<string, double>())
                                      .GetValueOrDefault(name, 0.0);
                double billingEff = Math.Max(billing - redCut, 0.0);
                double uncollected = Math.Round(billingEff - gotTotal, 2);
                if (uncollected > 0.01)
                {
                    m.InvOpenUncollected += uncollected;
                    st.UncollectedMonth[invMonth] += uncollected;
                    st.UncollectedAcc += uncollected;
                }
                double received = Math.Round(billingEff - Math.Max(uncollected, 0.0), 2);
                m.RecOpenCur += received;       // ①
                m.InvOpenReceived += received;  // ⑥
                m.InvTotal += billing;
            }
        }

        // ============ 红字发票（三⑧⑨）============
        foreach (var red in reds)
        {
            int redYear = YearOf(Str(red["invoice_date"]));
            int redMonth = MonthOf(Str(red["invoice_date"]));
            if (redYear != year) continue;
            var rpb = new Pb();
            var rsql = "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=" + rpb.Add(Str(red["invoice_no"]))
                       + (personType != null ? " AND person_type=" + rpb.Add(personType) : "") + " ORDER BY id";
            var cds = Q(conn, rsql, rpb.Values);
            if (cds.Count == 0) continue;
            int? origYear = null, origMonth = null;
            string? oi = Str(red["orig_invoice_no"]);
            if (!string.IsNullOrEmpty(oi))
            {
                var orow = Q1(conn, "SELECT invoice_date FROM invoice WHERE invoice_no=@p0", oi);
                if (orow != null) { origYear = YearOf(Str(orow["invoice_date"])); origMonth = MonthOf(Str(orow["invoice_date"])); }
            }
            foreach (var cd in cds)
            {
                string name = Str(cd["person_name"])!;
                var st = GetOrCreate(result, conn, name, personType);
                var m = st.Months[redMonth];
                double val = Num(cd["billing_amount"]); // 负数
                m.InvTotal += val;
                if (origYear != null && origYear < year) m.InvRedPrev += val;            // ⑨红冲上年
                else if (origYear == year && origMonth != null && origMonth < redMonth) m.InvRedCur += val; // ⑧红冲本年
            }
        }

        // ============ 退款（二④⑤，按红字经办人比例分摊）============
        var refunds = Q(conn, "SELECT red_invoice_no, refund_amount, refund_date FROM refund ORDER BY refund_date");
        foreach (var rf in refunds)
        {
            string redNo = Str(rf["red_invoice_no"])!;
            var redRow = Q1(conn, "SELECT invoice_date FROM invoice WHERE invoice_no=@p0", redNo);
            if (redRow == null) continue;
            var rpb = new Pb();
            var rsql = "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=" + rpb.Add(redNo)
                       + (personType != null ? " AND person_type=" + rpb.Add(personType) : "") + " ORDER BY id";
            var cds = Q(conn, rsql, rpb.Values);
            if (cds.Count == 0) continue;
            double totalAbs = cds.Sum(c => Math.Abs(Num(c["billing_amount"])));
            if (totalAbs == 0) totalAbs = 1.0;
            int refundYear = YearOf(Str(rf["refund_date"]));
            if (refundYear != year) continue;
            int refundMonth = MonthOf(Str(rf["refund_date"]));
            int? origYear = null;
            var ri = Q1(conn, "SELECT orig_invoice_no FROM invoice WHERE invoice_no=@p0", redNo);
            if (ri != null)
            {
                string? oi2 = Str(ri["orig_invoice_no"]);
                if (!string.IsNullOrEmpty(oi2))
                {
                    var orow2 = Q1(conn, "SELECT invoice_date FROM invoice WHERE invoice_no=@p0", oi2);
                    if (orow2 != null) origYear = YearOf(Str(orow2["invoice_date"]));
                }
            }
            foreach (var cd in cds)
            {
                string name = Str(cd["person_name"])!;
                if (person != null && name != person) continue;
                double share = Num(rf["refund_amount"]) * Math.Abs(Num(cd["billing_amount"])) / totalAbs;
                var st = GetOrCreate(result, conn, name, personType);
                var m = st.Months[refundMonth];
                if (origYear != null && origYear < year) m.RecRefundPrev -= share;  // ⑤退上年
                else m.RecRefundCur -= share;                                       // ④退本年
            }
        }

        // ============ 业务收入 ============
        foreach (var kv in result)
        {
            var st = kv.Value;
            foreach (int mo in Months)
            {
                var m = st.Months[mo];
                double recNet = m.RecOpenCur + m.RecCurYear + m.RecPrevYear + m.RecRefundCur + m.RecRefundPrev;
                if (st.StaffType == "合伙")
                    m.Income = Math.Round(m.InvTotal, 2);
                else if (st.StaffType == "聘用" || st.StaffType == "兼职")
                    m.Income = Math.Round(recNet, 2);
                else
                    m.Income = 0.0;
            }
            st.UncollectedTotal = Math.Round(st.UncollectedAcc, 2);
        }

        // ============ 费用（六，逐类）============
        List<Dictionary<string, object?>> expRows;
        if (person == null)
        {
            var epb = new Pb();
            var esql = "SELECT period, actual_handler, expense_type, expense_amount FROM expense_ledger"
                       + (personType != null ? " WHERE person_type=" + epb.Add(personType) : "");
            expRows = Q(conn, esql, epb.Values);
        }
        else
        {
            var epb = new Pb();
            var esql = "SELECT period, actual_handler, expense_type, expense_amount FROM expense_ledger WHERE actual_handler=" + epb.Add(person);
            if (personType != null) esql += " AND person_type=" + epb.Add(personType);
            expRows = Q(conn, esql, epb.Values);
        }
        foreach (var er in expRows)
        {
            string? name = Str(er["actual_handler"]);
            if (string.IsNullOrEmpty(name)) continue;
            string period = Str(er["period"]) ?? "";
            int expMonth = MonthOf(period);
            if (!string.IsNullOrEmpty(period) && YearOf(period) != year) continue;
            string etype = Str(er["expense_type"]) ?? "其他费用";
            if (!result.ContainsKey(name!))
                result[name!] = new PersonSettlement { StaffType = StaffTypeOrig(conn, name!) };
            var st = result[name!];
            if (!st.Expenses.ContainsKey(etype)) st.Expenses[etype] = new Dictionary<int, double>();
            if (!st.Expenses[etype].ContainsKey(expMonth)) st.Expenses[etype][expMonth] = 0.0;
            st.Expenses[etype][expMonth] += Num(er["expense_amount"]);
        }

        return result;
    }

    // ------------------------------------------------------------------
    // 收款分摊（镜像 split.allocate_receipt / _allocated_total）
    // ------------------------------------------------------------------
    private static Dictionary<string, double> AllocateReceipt(Dictionary<string, double> remaining, double amount)
    {
        var result = new Dictionary<string, double>();
        if (amount <= 0) return result;
        var pool = remaining.Where(kv => kv.Value > 0.01).Select(kv => kv.Key).ToList();
        while (amount > 0.01 && pool.Count > 0)
        {
            double share = amount / pool.Count;
            double consumed = 0.0;
            var nextPool = new List<string>();
            foreach (var n in pool)
            {
                if (remaining[n] <= share + 0.01)
                {
                    double give = remaining[n];
                    result[n] = result.GetValueOrDefault(n, 0.0) + give;
                    remaining[n] = 0.0;
                    consumed += give;
                }
                else
                {
                    double give = share;
                    result[n] = result.GetValueOrDefault(n, 0.0) + give;
                    remaining[n] -= give;
                    consumed += give;
                    nextPool.Add(n);
                }
            }
            amount -= consumed;
            pool = nextPool;
        }
        return result;
    }

    private static List<double> AllocatedTotal(
        List<Dictionary<string, object?>> cds, string name,
        List<Dictionary<string, object?>> receipts)
    {
        var rem = new Dictionary<string, double>();
        foreach (var cd in cds) rem[Str(cd["person_name"])!] = Num(cd["billing_amount"]);
        var outList = new List<double>();
        foreach (var rec in receipts)
        {
            string? pname = Str(rec["person_name"]);
            double amount = Num(rec["amount"]);
            Dictionary<string, double> got;
            if (!string.IsNullOrEmpty(pname) && rem.ContainsKey(pname))
            {
                got = amount > 0 ? new Dictionary<string, double> { [pname] = amount } : new Dictionary<string, double>();
                rem[pname] = Math.Max(rem[pname] - amount, 0.0);
            }
            else
            {
                got = AllocateReceipt(rem, amount);
            }
            outList.Add(got.GetValueOrDefault(name, 0.0));
        }
        return outList;
    }

    // ------------------------------------------------------------------
    // 人员类型 / 人员结构
    // ------------------------------------------------------------------
    private static string StaffType(SqliteConnection conn, string name, string? overrideT)
        => !string.IsNullOrEmpty(overrideT) ? overrideT! : StaffTypeOrig(conn, name);

    private static string StaffTypeOrig(SqliteConnection conn, string name)
    {
        var r = Q1(conn, "SELECT staff_type FROM staff WHERE name=@p0 AND is_active=1", name);
        if (r == null) return "其他";
        string t = Str(r["staff_type"])?.Trim() ?? "";
        if (t.Contains("合伙")) return "合伙";
        if (t.Contains("兼职")) return "兼职";
        if (t.Contains("聘用")) return "聘用";
        return "其他";
    }

    private static PersonSettlement GetOrCreate(
        Dictionary<string, PersonSettlement> result, SqliteConnection conn, string name, string? personType)
    {
        if (!result.ContainsKey(name)) result[name] = new PersonSettlement { StaffType = StaffType(conn, name, personType) };
        return result[name];
    }

    // ------------------------------------------------------------------
    // 日期解析辅助
    // ------------------------------------------------------------------
    private static int YearOf(string? ym)
    {
        if (string.IsNullOrEmpty(ym)) return 0;
        var p = ym.Split('-');
        return p.Length >= 1 && int.TryParse(p[0], out var y) ? y : 0;
    }

    private static int MonthOf(string? ym)
    {
        if (string.IsNullOrEmpty(ym)) return 0;
        var p = ym.Split('-');
        return p.Length >= 2 && int.TryParse(p[1], out var m) ? m : 0;
    }

    // ------------------------------------------------------------------
    // SQLite 读取辅助
    // ------------------------------------------------------------------
    private static List<Dictionary<string, object?>> Q(SqliteConnection conn, string sql, params object?[] ps)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        for (int i = 0; i < ps.Length; i++)
            cmd.Parameters.AddWithValue("@p" + i, ps[i] ?? DBNull.Value);
        using var r = cmd.ExecuteReader();
        var rows = new List<Dictionary<string, object?>>();
        while (r.Read())
        {
            var d = new Dictionary<string, object?>();
            for (int c = 0; c < r.FieldCount; c++)
                d[r.GetName(c)] = r.IsDBNull(c) ? null : r.GetValue(c);
            rows.Add(d);
        }
        return rows;
    }

    private static Dictionary<string, object?>? Q1(SqliteConnection conn, string sql, params object?[] ps)
    {
        var rows = Q(conn, sql, ps);
        return rows.Count > 0 ? rows[0] : null;
    }

    private static double Num(object? v)
        => (v == null || v == DBNull.Value) ? 0.0 : Convert.ToDouble(v);

    private static string? Str(object? v)
        => (v == null || v == DBNull.Value) ? null : v.ToString();

    /// <summary>有序参数收集器：追加占位符并收集值，保证 @p{i} 与实参顺序一致。</summary>
    private sealed class Pb
    {
        private int _i;
        private readonly List<object?> _v = new();
        public string Add(object? val) { var n = "@p" + _i++; _v.Add(val); return n; }
        public object?[] Values => _v.ToArray();
    }
}

/// <summary>单人员结算结构（镜像 Python st 字典）。</summary>
public sealed class PersonSettlement
{
    public string StaffType { get; set; } = "其他";
    public Dictionary<int, MonthData> Months { get; } = new();
    public Dictionary<int, double> UncollectedMonth { get; } = new();
    public double UncollectedTotal;
    public Dictionary<string, Dictionary<int, double>> Expenses { get; } = new();
    public double UncollectedAcc;

    public PersonSettlement()
    {
        foreach (int m in new[] { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 })
        {
            Months[m] = new MonthData();
            UncollectedMonth[m] = 0.0;
        }
    }
}

/// <summary>单月 11 键数值（镜像 Python _empty_month）。</summary>
public sealed class MonthData
{
    public double RecOpenCur, RecCurYear, RecPrevYear, RecRefundCur, RecRefundPrev;
    public double InvOpenReceived, InvOpenUncollected, InvRedCur, InvRedPrev, InvTotal, Income;
}
