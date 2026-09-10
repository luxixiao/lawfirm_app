using System;
using System.ComponentModel.DataAnnotations.Schema;

namespace LawFirm.Data;

/// <summary>
/// collection 表行 DTO（只读映射，与 app/db.py CREATE TABLE collection 一致）。
///
/// 类型映射：id→long（INTEGER AUTOINCREMENT），金额→decimal，文本→string。
/// 日期列（receipt_date）按 **string** 映射：源库为 TEXT，可能含 "YYYY-MM" 不完整格式，
/// 强类型 DateTime 会在 Sample() 抛 FormatException；T2 仅只读展示，无需强类型日期。
/// </summary>
public class CollectionRow
{
    [Column("id")] public long Id { get; set; }

    [Column("invoice_no")] public string InvoiceNo { get; set; } = "";

    [Column("amount")] public decimal Amount { get; set; }

    [Column("receipt_date")] public string? ReceiptDate { get; set; }

    [Column("person_name")] public string PersonName { get; set; } = "";

    [Column("source")] public string Source { get; set; } = "";

    [Column("import_batch_id")] public int? ImportBatchId { get; set; }

    [Column("note")] public string? Note { get; set; }
}
