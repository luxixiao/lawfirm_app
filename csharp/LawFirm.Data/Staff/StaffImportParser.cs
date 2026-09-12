using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using NPOI.SS.UserModel;

namespace LawFirm.Data.Staff;

/// <summary>
/// 职工花名册导入解析异常（带用户可读信息）。
/// 对齐 Python 侧 <c>app/importer/staff_import.py::ImportError_</c>。
/// </summary>
public sealed class StaffImportException : Exception
{
    /// <summary>用中文提示文案构造。</summary>
    /// <param name="message">面向用户的中文提示。</param>
    public StaffImportException(string message) : base(message)
    {
    }
}

/// <summary>
/// 职工清单解析（<c>app/importer/staff_import.py</c> 的移植，用 NPOI 读 Excel）。
///
/// <para>模板 = 职工清单.xlsx：姓名|类型|备注。语义与 Python 逐条对齐：</para>
/// <list type="bullet">
///   <item><c>_cell_text</c>：null → ""；数值且为整数 → 按整数文本（2025.0 → "2025"）；其余去空白。</item>
///   <item>读 .xlsx/.xls 取<b>缓存值</b>（对应 openpyxl 的 <c>data_only=True</c>），公式单元返回缓存结果而非公式文本。</item>
///   <item>表头定位：第一行同时含「姓名」与「类型」（子串）；找不到抛错。</item>
///   <item>列下标：姓名 / 类型必需、备注可选（缺则 -1）；同名取第一个匹配。</item>
///   <item>表头之后逐行取，姓名为空跳过；无有效数据抛错。</item>
///   <item><c>file_hash</c> = 文件字节的 MD5 十六进制（小写）。</item>
/// </list>
///
/// <para><b>有意修正</b>：Python 的 <c>parse_staff_file</c> 与错误文案都声称支持 <c>.xlsm</c>，
/// 但代码只在 <c>.xlsx</c> / <c>.xls</c> 上分支，<c>.xlsm</c> 实际会落到 else 抛「不支持的文件格式」。
/// NPOI 的 <c>WorkbookFactory.Create</c> 本就能读 .xlsm，故本端口<b>真的支持 .xlsm</b>。</para>
/// </summary>
public static class StaffImportParser
{
    // ------------------------------------------------------------------ #
    // 单元文本（对齐 Python _cell_text）
    // ------------------------------------------------------------------ #

    private static string CellText(ICell? cell)
    {
        if (cell is null) return "";

        CellType type = cell.CellType;
        // 公式单元：取缓存结果（data_only 语义），不返回公式字符串。
        if (type == CellType.Formula) type = cell.CachedFormulaResultType;

        switch (type)
        {
            case CellType.String:
                return (cell.StringCellValue ?? "").Trim();
            case CellType.Numeric:
                double d = cell.NumericCellValue;
                // 整数（且非 NaN/Inf）→ 整数文本：2025.0 → "2025"（对应 Python str(int(v))）。
                if (!double.IsNaN(d) && !double.IsInfinity(d)
                    && d == Math.Floor(d) && Math.Abs(d) < 1e15)
                {
                    return ((long)d).ToString(CultureInfo.InvariantCulture);
                }
                return d.ToString(CultureInfo.InvariantCulture);
            case CellType.Boolean:
                // 对应 Python str(bool) → "True"/"False"。
                return cell.BooleanCellValue ? "True" : "False";
            default:
                // Blank / Error / Unknown → ""（Python 的 None 亦归 ""）。
                return "";
        }
    }

    // ------------------------------------------------------------------ #
    // 读取整表（第一张 sheet）为字符串矩阵
    // ------------------------------------------------------------------ #

    private static List<List<string>> ReadRows(string path)
    {
        var rows = new List<List<string>>();
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        IWorkbook wb = WorkbookFactory.Create(fs);
        ISheet sheet = wb.GetSheetAt(0);
        for (int r = sheet.FirstRowNum; r <= sheet.LastRowNum; r++)
        {
            IRow? row = sheet.GetRow(r);
            var cells = new List<string>();
            if (row is not null)
            {
                int last = row.LastCellNum; // 1-based 计数；0 表示无单元
                for (int c = 0; c < last; c++)
                    cells.Add(CellText(row.GetCell(c)));
            }
            rows.Add(cells);
        }
        return rows;
    }

    // ------------------------------------------------------------------ #
    // 表头定位
    // ------------------------------------------------------------------ #

    /// <summary>定位表头行：同时含「姓名」与「类型」的行号；找不到返回 -1。</summary>
    private static int FindHeaderRow(List<List<string>> rows)
    {
        for (int i = 0; i < rows.Count; i++)
        {
            bool hasName = false, hasType = false;
            foreach (string n in rows[i])
            {
                if (n.Contains("姓名", StringComparison.Ordinal)) hasName = true;
                if (n.Contains("类型", StringComparison.Ordinal)) hasType = true;
            }
            if (hasName && hasType) return i;
        }
        return -1;
    }

    // ------------------------------------------------------------------ #
    // 解析入口
    // ------------------------------------------------------------------ #

    /// <summary>
    /// 解析职工清单文件，返回 ([(姓名, 类型, 备注)...], file_hash)。
    /// </summary>
    /// <param name="path">文件路径（.xls / .xlsx / .xlsm）。</param>
    /// <returns>职工行列表与文件 MD5（小写十六进制）。</returns>
    /// <exception cref="StaffImportException">文件不存在 / 格式不支持 / 未找到表头 / 无有效数据。</exception>
    public static (List<StaffImportRow> Staff, string FileHash) ParseStaffFile(string path)
    {
        if (!File.Exists(path))
            throw new StaffImportException($"文件不存在: {path}");

        string suffix = Path.GetExtension(path).ToLowerInvariant();
        if (suffix != ".xls" && suffix != ".xlsx" && suffix != ".xlsm")
            throw new StaffImportException($"不支持的文件格式: {suffix}（支持 .xls/.xlsx/.xlsm）");

        List<List<string>> rows = ReadRows(path);

        int hr = FindHeaderRow(rows);
        if (hr < 0)
            throw new StaffImportException("未找到表头（需包含'姓名'和'类型'列）");

        List<string> header = rows[hr];
        int idxName = FirstIndexOf(header, "姓名");
        int idxType = FirstIndexOf(header, "类型");
        int idxNote = FirstIndexOf(header, "备注"); // -1 表示无备注列

        var staff = new List<StaffImportRow>();
        for (int r = hr + 1; r < rows.Count; r++)
        {
            List<string> row = rows[r];
            string name = idxName >= 0 && idxName < row.Count ? row[idxName] : "";
            if (string.IsNullOrEmpty(name)) continue;
            string stype = idxType >= 0 && idxType < row.Count ? row[idxType] : "";
            string note = idxNote >= 0 && idxNote < row.Count ? row[idxNote] : "";
            staff.Add(new StaffImportRow(name, stype, note));
        }

        if (staff.Count == 0)
            throw new StaffImportException("文件中没有有效的职工数据");

        string fileHash = Convert.ToHexString(MD5.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
        return (staff, fileHash);
    }

    /// <summary>表头中第一个包含 <paramref name="needle"/> 的列下标；找不到返回 -1。</summary>
    private static int FirstIndexOf(List<string> header, string needle)
    {
        for (int i = 0; i < header.Count; i++)
        {
            if (header[i].Contains(needle, StringComparison.Ordinal)) return i;
        }
        return -1;
    }
}
