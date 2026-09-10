using System;
using System.ComponentModel.DataAnnotations.Schema;

namespace LawFirm.Data;

/// <summary>
/// invoice 表行 DTO（只读映射，与 app/db.py CREATE TABLE invoice 一致）。
///
/// 类型映射（按 T2 DTO 约定）：文本→string，金额→decimal，id→long/int。
/// 日期列（invoice_date / created_at）一律按 **string** 映射：源库为 TEXT，实际数据可能含
/// "YYYY-MM" 这类不完整 ISO 格式，.NET 强类型 DateTime 会抛 FormatException 导致 Sample()
/// 崩溃；T2 仅做只读展示，无需强类型日期。真正需要日期运算时由 T3 业务层显式解析。
///
/// 列名含下划线，用 [Column] 显式映射（Dapper 默认不剥离下划线）。
/// </summary>
public class InvoiceRow
{
    [Column("invoice_no")] public string InvoiceNo { get; set; } = "";

    [Column("invoice_date")] public string? InvoiceDate { get; set; }

    [Column("buyer")] public string? Buyer { get; set; }

    [Column("total_amount")] public decimal TotalAmount { get; set; }

    [Column("kind")] public string? Kind { get; set; }

    [Column("status")] public string? Status { get; set; }

    [Column("voucher_no")] public string? VoucherNo { get; set; }

    [Column("goods")] public string? Goods { get; set; }

    [Column("net_amount")] public decimal? NetAmount { get; set; }

    [Column("tax_rate")] public string? TaxRate { get; set; }

    [Column("tax")] public decimal? Tax { get; set; }

    [Column("orig_invoice_no")] public string? OrigInvoiceNo { get; set; }

    [Column("remark")] public string? Remark { get; set; }

    [Column("case_no")] public string? CaseNo { get; set; }

    [Column("source")] public string Source { get; set; } = "";

    [Column("import_batch_id")] public int? ImportBatchId { get; set; }

    [Column("created_at")] public string? CreatedAt { get; set; }
}
