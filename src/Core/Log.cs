using System;
using System.IO;
using System.Linq;

namespace PixelViewCC
{
    /// <summary>One log file per Rhino session, in the plug-in settings folder (never in the package folder).</summary>
    internal static class Log
    {
        const int KeepFiles = 30;
        static string _path;
        static readonly object Gate = new object();

        public static string Folder { get; private set; }
        public static string FilePath => _path;

        public static void Start(string folder)
        {
            try
            {
                Folder = folder;
                Directory.CreateDirectory(folder);
                _path = Path.Combine(folder, $"PixelView_CC_{DateTime.Now:yyyy-MM-dd_HH-mm-ss}.log");
                var old = new DirectoryInfo(folder).GetFiles("PixelView_CC_*.log")
                    .OrderByDescending(f => f.LastWriteTimeUtc).Skip(KeepFiles).ToList();
                foreach (var f in old)
                {
                    try { f.Delete(); } catch { /* keep going */ }
                }
            }
            catch
            {
                _path = null;   // logging never blocks the plug-in
            }
        }

        public static void Write(string tag, string message)
        {
            if (_path == null) return;
            try
            {
                lock (Gate)
                    File.AppendAllText(_path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} | {tag,-6} | {message}{Environment.NewLine}");
            }
            catch { /* ignore */ }
        }

        public static void Error(string where, Exception ex) => Write("ERROR", $"{where}: {ex}");
    }
}
