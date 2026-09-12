using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
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
    /// 读写打开指定路径的 SQLite 数据库，返回已 Open 的连接（基础数据维护的**首个写通道**）。
    /// 调用方负责 using 释放。本方法只负责「打开」，**不**负责备份/事务/串行化——
    /// 那些一律走 <see cref="WriteGuard"/>，绝不要在别处裸用本方法直接写。
    /// </summary>
    /// <param name="dbPath">lawfirm.db 的绝对或相对路径；文件必须已存在。</param>
    /// <exception cref="ArgumentException">dbPath 为空。</exception>
    /// <exception cref="FileNotFoundException">文件不存在。</exception>
    public static SqliteConnection OpenReadWrite(string dbPath)
    {
        if (string.IsNullOrWhiteSpace(dbPath))
            throw new ArgumentException("dbPath 不能为空", nameof(dbPath));
        if (!File.Exists(dbPath))
            throw new FileNotFoundException($"数据库文件不存在: {dbPath}", dbPath);

        // 读写模式：库必须已存在，故用 Mode=ReadWrite（**绝不**用 ReadWriteCreate）——
        // 拼错路径时不能凭空造出一个空库，那会让用户误以为「数据全没了」。
        // Pooling=false：写连接必须真正关闭（触发 checkpoint 并释放文件句柄），
        // 否则默认连接池会长期持有 db 文件锁，令 WAL checkpoint / 文件操作被占用阻塞。
        // DefaultTimeout=5：**这才是命令忙等（BUSY）等待上限的真正开关**——它决定本连接
        // 上所有命令（含 BeginTransaction 内部命令）的 CommandTimeout（默认 30 秒！）。
        // Microsoft.Data.Sqlite 在每次执行命令时用 CommandTimeout 去调 sqlite3_busy_timeout，
        // 会把连接级 PRAGMA busy_timeout 覆盖掉；故写锁下若只设 PRAGMA，实际会等满 ~30 秒。
        var builder = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            Mode = SqliteOpenMode.ReadWrite,
            Pooling = false,
            DefaultTimeout = 5,
        };

        var conn = new SqliteConnection(builder.ConnectionString);
        conn.Open();

        // 外键约束开启（每连接非持久，与 Python 侧一致）。
        // 关于忙等上限：**真正生效的是上面的 DefaultTimeout=5（→ 每条命令的 CommandTimeout）**，
        // Microsoft.Data.Sqlite 每次执行命令都会用它覆盖 sqlite3_busy_timeout；这里的
        // PRAGMA busy_timeout 仅对「不经 SqliteCommand 包裹的裸 SQL 路径」有个兜底意义，
        // 不构成命令级等待上限——不要误以为设了它等 5 秒就够了。
        conn.Execute("PRAGMA foreign_keys = ON;");
        conn.Execute("PRAGMA busy_timeout = 5000;");

        // 刻意的不对称：此处**不设** PRAGMA query_only=ON。
        // query_only 是只读通道的纵深防御（见 OpenReadOnly），写通道若也设上，
        // 任何 INSERT/UPDATE/DELETE 都会被连接级拒绝；两条通道的防御必须相反。
        return conn;
    }

    /// <summary>
    /// 数据库定位结果（R-M2：把「本次到底用了哪个 db、为什么」显式暴露给 UI / CLI）。
    /// 三机实测时，Seafile 同步 + 残留 LAWFIRM_DB 会导致「读到的不是我以为的那个库」，
    /// 静默兜底是最危险的——所以这里把来源与尝试过的候选全部带出来。
    /// </summary>
    public sealed class DatabaseResolution
    {
        /// <summary>命中的绝对路径；未命中为 null。</summary>
        public string? Path { get; init; }

        /// <summary>来源：env / walkup / cwd / none。</summary>
        public string Source { get; init; } = "none";

        /// <summary>环境变量 LAWFIRM_DB 的原始值（未设置则 null）。</summary>
        public string? EnvValue { get; init; }

        /// <summary>LAWFIRM_DB 设了但不可用（文件不存在 / 非 SQLite）→ 已被忽略。</summary>
        public bool EnvIgnored { get; init; }

        /// <summary>按顺序尝试过的候选（含不可用原因），用于报错与诊断输出。</summary>
        public IReadOnlyList<string> Tried { get; init; } = Array.Empty<string>();

        public bool Found => Path is not null;

        public string SourceText => Source switch
        {
            "env" => "环境变量 LAWFIRM_DB",
            "walkup" => "程序目录向上查找 data/lawfirm.db",
            "cwd" => "当前目录向上查找 data/lawfirm.db",
            _ => "未找到",
        };
    }

    /// <summary>
    /// 在仓库中定位 data/lawfirm.db（不抛异常版本，供 UI / 诊断使用）：
    /// 1) 环境变量 LAWFIRM_DB（最高优先，便于本机指向任意副本）；
    /// 2) 从程序输出目录逐级向上查找 data/lawfirm.db；
    /// 3) 从当前工作目录逐级向上查找 data/lawfirm.db。
    ///
    /// 每一步都做 **文件存在 + SQLite 头** 双重校验（R-M4）：
    /// 旧实现兜底直接拼出 bin\data\lawfirm.db（必然不存在）且不做 File.Exists，
    /// 结果是把「找不到」伪装成一个看起来正常的路径，错误被推到下游才炸。
    /// </summary>
    public static DatabaseResolution ResolveDatabase()
    {
        var tried = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        string? env = Environment.GetEnvironmentVariable("LAWFIRM_DB");
        bool envIgnored = false;
        if (!string.IsNullOrWhiteSpace(env))
        {
            if (IsUsableSqlite(env))
            {
                string full = Path.GetFullPath(env);
                tried.Add($"[命中] 环境变量 LAWFIRM_DB = {full}");
                return new DatabaseResolution
                {
                    Path = full, Source = "env", EnvValue = env, Tried = tried,
                };
            }
            envIgnored = true;
            tried.Add($"[忽略] 环境变量 LAWFIRM_DB = {env}（{ReasonNotUsable(env)}）");
        }

        // 起点 1：程序输出目录（bin\Debug\net10.0-windows\ → …\lawfirm_app\）
        var hit = WalkUp(AppContext.BaseDirectory, "walkup", tried, seen);
        if (hit is not null)
            return new DatabaseResolution { Path = hit.Path, Source = hit.Source, EnvValue = env, EnvIgnored = envIgnored, Tried = tried };

        // 起点 2：当前工作目录（dotnet run / 快捷方式的工作目录可能与程序目录不同）
        hit = WalkUp(Directory.GetCurrentDirectory(), "cwd", tried, seen);
        if (hit is not null)
            return new DatabaseResolution { Path = hit.Path, Source = hit.Source, EnvValue = env, EnvIgnored = envIgnored, Tried = tried };

        return new DatabaseResolution
        {
            Source = "none", EnvValue = env, EnvIgnored = envIgnored, Tried = tried,
        };
    }

    private static DatabaseResolution? WalkUp(string start, string source, List<string> tried, HashSet<string> seen)
    {
        var dir = new DirectoryInfo(start ?? ".");
        while (dir != null)
        {
            string candidate = Path.Combine(dir.FullName, "data", "lawfirm.db");
            if (seen.Add(candidate))
                tried.Add(IsUsableSqlite(candidate)
                    ? $"[命中] {candidate}"
                    : $"[跳过] {candidate}（{ReasonNotUsable(candidate)}）");
            if (IsUsableSqlite(candidate))
                return new DatabaseResolution { Path = candidate, Source = source };
            dir = dir.Parent;
        }
        return null;
    }

    /// <summary>
    /// 定位 data/lawfirm.db；找不到则抛出**列出全部候选**的 FileNotFoundException
    /// （旧实现返回一个不存在的路径，报错信息完全误导）。
    /// </summary>
    public static string FindDatabase()
    {
        var r = ResolveDatabase();
        if (r.Found) return r.Path!;

        var sb = new StringBuilder();
        sb.AppendLine("未找到可用的 data/lawfirm.db，已按顺序尝试：");
        foreach (string t in r.Tried) sb.AppendLine("  " + t);
        sb.Append("处理办法：设置环境变量 LAWFIRM_DB 指向 lawfirm.db，或把程序放到仓库目录下运行。");
        throw new FileNotFoundException(sb.ToString());
    }

    private static readonly byte[] SqliteMagic = "SQLite format 3\0"u8.ToArray();

    /// <summary>存在且是真正的 SQLite 文件（Seafile 同步中断可能产生 0 字节/半截文件）。</summary>
    private static bool IsUsableSqlite(string path)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path)) return false;
            var fi = new FileInfo(path);
            if (fi.Length < 100) return false;
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            var head = new byte[16];
            if (fs.Read(head, 0, 16) != 16) return false;
            for (int i = 0; i < 16; i++)
                if (head[i] != SqliteMagic[i]) return false;
            return true;
        }
        catch
        {
            // 只读探测：被占用 / 权限不足 / 路径非法 都按「不可用」处理，不抛给调用方
            return false;
        }
    }

    private static string ReasonNotUsable(string path)
    {
        try
        {
            if (!File.Exists(path)) return "文件不存在";
            var fi = new FileInfo(path);
            if (fi.Length < 100) return $"文件过小（{fi.Length} 字节，可能被同步截断）";
            return "不是 SQLite 文件";
        }
        catch (Exception ex)
        {
            return "无法读取：" + ex.Message;
        }
    }
}
