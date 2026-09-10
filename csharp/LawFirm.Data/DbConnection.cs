using System;
using System.IO;
using Dapper;
using Microsoft.Data.Sqlite;

namespace LawFirm.Data;

/// <summary>
/// 只读数据库连接工厂（Pilot T2）。
///
/// 设计原则（计划 §5.2 数据层 / T2 验证项）：
/// - 复用现有 data/lawfirm.db，**零数据风险**。
/// - 纵深防御只读：连接串 Mode=ReadOnly（文件系统级拒绝写）+ 打开后
///   PRAGMA query_only=ON（连接级只接受 SELECT）+ PRAGMA foreign_keys=ON。
/// - 本类**绝不**执行 INSERT/UPDATE/DELETE，也**绝不**做任何迁移（无 EF、无建表）。
/// </summary>
public static class DbConnection
{
    /// <summary>
    /// 只读打开指定路径的 SQLite 数据库，返回已 Open 的连接。
    /// 调用方负责 using 释放。任何写操作都会被 query_only + ReadOnly 双重拒绝。
    /// </summary>
    /// <param name="dbPath">lawfirm.db 的绝对或相对路径。</param>
    /// <exception cref="ArgumentException">dbPath 为空。</exception>
    /// <exception cref="FileNotFoundException">文件不存在。</exception>
    public static SqliteConnection OpenReadOnly(string dbPath)
    {
        if (string.IsNullOrWhiteSpace(dbPath))
            throw new ArgumentException("dbPath 不能为空", nameof(dbPath));
        if (!File.Exists(dbPath))
            throw new FileNotFoundException($"数据库文件不存在: {dbPath}", dbPath);

        // 文件系统级只读：若文件被 Python 版以 WAL 占用，只读打开不会破坏 -wal/-shm。
        var builder = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            Mode = SqliteOpenMode.ReadOnly,
        };

        var conn = new SqliteConnection(builder.ConnectionString);
        conn.Open();

        // 连接级防御：只允许 SELECT；外键约束开启（每连接非持久，与 Python 侧 db.py:421 一致）。
        conn.Execute("PRAGMA query_only = ON;");
        conn.Execute("PRAGMA foreign_keys = ON;");
        return conn;
    }

    /// <summary>
    /// 在仓库中定位 data/lawfirm.db：
    /// 1) 环境变量 LAWFIRM_DB（最高优先，便于本机指向任意副本）；
    /// 2) 从程序输出目录向上逐级查找 data/lawfirm.db（与 data/ 在仓库根同级）；
    /// 3) 兜底到计划约定的相对输出路径。
    /// 不依赖当前工作目录，简单且稳健。
    /// </summary>
    public static string FindDatabase()
    {
        string? env = Environment.GetEnvironmentVariable("LAWFIRM_DB");
        if (!string.IsNullOrWhiteSpace(env) && File.Exists(env))
            return env;

        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir != null)
        {
            string candidate = Path.Combine(dir.FullName, "data", "lawfirm.db");
            if (File.Exists(candidate))
                return candidate;
            dir = dir.Parent;
        }

        // 兜底：从输出目录向上两级寻 data/（兼容 `dotnet run` 工作目录与计划描述）。
        return Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "data", "lawfirm.db"));
    }
}
