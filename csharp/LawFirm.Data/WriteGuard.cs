using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data;

/// <summary>
/// 写库忙异常：SQLITE_BUSY / SQLITE_LOCKED 被翻译后的对外异常。
///
/// Message 为固定中文文案（用户可直接照着做），原始 <see cref="SqliteException"/> 保留为 InnerException，
/// 便于排障时仍能看到底层错误码。
/// </summary>
public sealed class DbBusyException : Exception
{
    /// <summary>用固定文案与原始异常构造。</summary>
    /// <param name="message">对外展示的固定文案。</param>
    /// <param name="inner">被翻译的底层 SQLite 异常。</param>
    public DbBusyException(string message, Exception inner) : base(message, inner)
    {
    }
}

/// <summary>
/// 写入通道守卫（Batch 1：基础数据维护的写路径，**不含 UI**）。
///
/// 用户批准的「A + 三保险」方案——所有写入必须经由本类，绝不裸写：
///   S1 备份：每次写操作前用 SQLite **在线备份 API** 生成快照；备份失败即中止本次写入。
///   S2 串行：静态信号量把「备份 → 打开 → 开事务 → 写 → 提交 → 关闭」整段串起来，杜绝交错。
///   S3 忙处理：把 SQLITE_BUSY(5)/SQLITE_LOCKED(6) 翻译成一条人话异常，指导用户稍后重试。
///
/// 本类不改变 <see cref="DbConnection"/> 的任何既有行为，只在「已定位到真实 db」之后负责安全地写。
/// </summary>
public static class WriteGuard
{
    /// <summary>忙异常的固定文案（对外可见，供测试/CLI 断言与 UI 复用）。</summary>
    public const string BusyMessage =
        "数据库被占用（可能有另一台电脑或另一个程序正在写入）。请稍后重试；若持续出现，请确认没有其它程序打开 lawfirm.db。";

    /// <summary>滚动备份保留份数：超过即删除最旧的（按最后写入时间）。</summary>
    public const int RetentionCount = 10;

    // S2：全局写入闸门。WPF 的 async 续体可能在任意线程继续执行，若不串行化，
    // 两次「备份 + 写」会互相交错，出现「基于 A 的快照却提交了 B 的写入」这类脏状态。
    // 同时它也让「备份」与「写」之间不存在第二个写者，快照与提交彼此一致。
    private static readonly SemaphoreSlim Gate = new(1, 1);

    /// <summary>副本/真实库的备份目录：与 db 同级的 backups 子目录。</summary>
    /// <param name="dbPath">lawfirm.db 的路径。</param>
    /// <returns>备份目录绝对/相对路径（不保证已存在）。</returns>
    /// <exception cref="ArgumentException">dbPath 为空。</exception>
    public static string BackupDirFor(string dbPath)
    {
        if (string.IsNullOrWhiteSpace(dbPath))
            throw new ArgumentException("dbPath 不能为空", nameof(dbPath));
        string? dir = Path.GetDirectoryName(dbPath);
        if (string.IsNullOrEmpty(dir)) dir = ".";
        return Path.Combine(dir, "backups");
    }

    /// <summary>当前备份目录中 lawfirm-*.db 的份数（供测试/CLI 断言）。目录不存在返回 0。</summary>
    /// <param name="dbPath">lawfirm.db 的路径。</param>
    public static int BackupCount(string dbPath)
    {
        string dir = BackupDirFor(dbPath);
        return Directory.Exists(dir) ? Directory.GetFiles(dir, "lawfirm-*.db").Length : 0;
    }

    /// <summary>
    /// 执行一次读写操作（Action 版，无返回值）。
    /// 语义与 <see cref="Execute{T}"/> 完全一致，仅为无返回值的调用提供便利重载。
    /// </summary>
    public static void Execute(string dbPath, string reason, Action<SqliteConnection, SqliteTransaction> work)
    {
        Execute<object?>(dbPath, reason, (conn, tx) =>
        {
            work(conn, tx);
            return null;
        });
    }

    /// <summary>
    /// 执行一次读写操作：**先备份、全程串行、事务提交**，任一步失败即回滚并抛出。
    ///
    /// 顺序（S2 保证整段独占）：获取闸门 → S1 备份 → 打开(ReadWrite) → 开事务 →
    /// 执行 work → 提交 → 关闭连接 → 释放闸门。
    /// </summary>
    /// <typeparam name="T">work 的返回类型（提交成功后返回）。</typeparam>
    /// <param name="dbPath">lawfirm.db 的路径；文件必须已存在。</param>
    /// <param name="reason">本次写入的简短人类可读标签（如「新增员工类型」），用于诊断。</param>
    /// <param name="work">在事务内执行的写入逻辑；请使用传入的 conn/tx，不要自建连接。</param>
    /// <returns>work 的返回值（仅在提交成功后）。</returns>
    /// <exception cref="ArgumentException">dbPath 为空。</exception>
    /// <exception cref="FileNotFoundException">dbPath 不存在。</exception>
    /// <exception cref="DbBusyException">数据库被占用（SQLITE_BUSY/SQLITE_LOCKED）。</exception>
    public static T Execute<T>(string dbPath, string reason, Func<SqliteConnection, SqliteTransaction, T> work)
    {
        if (string.IsNullOrWhiteSpace(dbPath))
            throw new ArgumentException("dbPath 不能为空", nameof(dbPath));
        if (!File.Exists(dbPath))
            throw new FileNotFoundException($"数据库文件不存在: {dbPath}", dbPath);
        if (work is null) throw new ArgumentNullException(nameof(work));

        Gate.Wait();
        try
        {
            // S1：先落一份快照；备份失败直接抛，绝不带病写入。
            CreateBackup(dbPath);

            try
            {
                return RunInTransaction(dbPath, work);
            }
            catch (Exception ex)
            {
                // S3：把忙错误翻译成人话后再抛出（其它异常原样抛，附加 reason 供诊断）。
                throw TranslateBusy(ex, reason);
            }
        }
        catch (Exception ex) when (ex is not DbBusyException)
        {
            // 备份阶段（或上面 try 之外）的异常：同样做一次忙翻译。
            throw TranslateBusy(ex, reason);
        }
        finally
        {
            Gate.Release();
        }
    }

    /// <summary>打开连接、开事务、跑 work、提交；异常时回滚后原样上抛。</summary>
    private static T RunInTransaction<T>(string dbPath, Func<SqliteConnection, SqliteTransaction, T> work)
    {
        using var conn = DbConnection.OpenReadWrite(dbPath);
        using var tx = conn.BeginTransaction();
        try
        {
            T result = work(conn, tx);
            tx.Commit();
            return result;
        }
        catch
        {
            // 回滚本身可能因「事务已提交/连接已失效」而抛，不应掩盖真正的失败原因。
            try { tx.Rollback(); } catch { /* 忽略回滚失败 */ }
            throw;
        }
    }

    /// <summary>
    /// 生成一份在线备份并做滚动保留；返回备份文件路径。
    ///
    /// **必须**使用 SQLite 在线备份 API（<see cref="SqliteConnection.BackupDatabase(SqliteConnection)"/>），
    /// **绝不能**用 File.Copy：库处于 WAL 模式时，已提交事务可能仍停留在 -wal 文件中而尚未 checkpoint 回主库，
    /// 直接复制 .db 只会得到「落后于已提交状态」的脏快照——恢复时数据静默缺失却不报任何错。
    /// BackupDatabase 由 SQLite 内部按页复制，能正确并入 WAL 中已提交的内容。
    /// </summary>
    private static string CreateBackup(string dbPath)
    {
        string dir = BackupDirFor(dbPath);
        Directory.CreateDirectory(dir);

        // 文件名带**毫秒**时间戳（yyyyMMdd-HHmmss-fff）：此前只到秒，同一秒内连发多次备份时
        // 名称会复用（尤其滚动保留删掉带 -2 后缀者后，后续备份会重新占用 -2 这个名字），
        // 使「文件名 ⟺ 某一份快照」不再稳定、回溯时易认错。毫秒级命名让每个快照名字唯一。
        // 保留「存在则加 -2/-3… 后缀」循环作为极端并发下的兜底，绝不覆盖既有快照。
        string stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss-fff");
        string dest = Path.Combine(dir, $"lawfirm-{stamp}.db");
        int suffix = 2;
        while (File.Exists(dest))
        {
            dest = Path.Combine(dir, $"lawfirm-{stamp}-{suffix}.db");
            suffix++;
        }

        // 源用只读连接：快照只读不写，且绝不在真实库上触发 checkpoint / 改动 -wal。
        // Pooling=false 很关键：默认连接池会把「已 Dispose」的连接留在池里（文件句柄不释放），
        // 导致后续滚动保留 File.Delete 目标文件时被占用而失败（快照永远删不掉，越积越多）。
        var sourceBuilder = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            Mode = SqliteOpenMode.ReadOnly,
            Pooling = false,
        };
        using (var source = new SqliteConnection(sourceBuilder.ConnectionString))
        {
            source.Open();

            var destBuilder = new SqliteConnectionStringBuilder
            {
                DataSource = dest,
                Mode = SqliteOpenMode.ReadWriteCreate,
                Pooling = false,
            };
            using var destConn = new SqliteConnection(destBuilder.ConnectionString);
            destConn.Open();

            source.BackupDatabase(destConn);
        }

        TrimBackups(dir);
        return dest;
    }

    /// <summary>滚动保留：按最后写入时间由新到旧排序，删除第 <see cref="RetentionCount"/> 份之后的所有备份。</summary>
    private static void TrimBackups(string dir)
    {
        // 排序：LastWriteTimeUtc 降序为主（NTFS 时间戳精度高，同一秒内多次备份也可区分），
        // 名称降序为稳定兜底（名称内含时间戳，语义上「名字越大越新」）。
        var files = new DirectoryInfo(dir).GetFiles("lawfirm-*.db")
            .OrderByDescending(f => f.LastWriteTimeUtc)
            .ThenByDescending(f => f.Name, StringComparer.Ordinal)
            .ToList();

        for (int i = RetentionCount; i < files.Count; i++)
        {
            try
            {
                files[i].Delete();
            }
            catch (Exception ex)
            {
                // 备份清理失败不应阻断本次写入（下次写入会再尝试）；仅记录诊断。
                Debug.WriteLine($"[WriteGuard] 清理旧备份失败: {files[i].FullName}: {ex.Message}");
            }
        }
    }

    /// <summary>
    /// S3：把 SQLITE_BUSY(5) / SQLITE_LOCKED(6) 翻译为固定文案的 <see cref="DbBusyException"/>；
    /// 其它异常原样返回（调用方再抛）。公开以便 CLI/测试直接验证翻译分支。
    /// </summary>
    /// <param name="ex">原始异常。</param>
    /// <param name="reason">本次写入的标签，写入异常 Data["reason"] 供诊断。</param>
    /// <returns>忙错误返回新的 <see cref="DbBusyException"/>；否则返回入参 ex。</returns>
    public static Exception TranslateBusy(Exception ex, string reason)
    {
        if (ex is SqliteException se && (se.SqliteErrorCode == 5 || se.SqliteErrorCode == 6))
        {
            var busy = new DbBusyException(BusyMessage, se);
            if (!string.IsNullOrEmpty(reason)) busy.Data["reason"] = reason;
            Debug.WriteLine($"[WriteGuard] 忙错误({se.SqliteErrorCode}) 于「{reason}」");
            return busy;
        }

        if (!string.IsNullOrEmpty(reason)) ex.Data["reason"] = reason;
        return ex;
    }
}
