using System;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;

namespace PixelViewCC
{
    /// <summary>Physical size of the monitor showing a window: pixels per inch, from the monitor's EDID (read only).</summary>
    internal sealed class ScreenInfo
    {
        public double Ppi;
        public string Name = "unknown monitor";
        public string Source = "fallback";
        public double WidthMm;
        public int PixelsWide, PixelsHigh;
        public string Device = "";
        public string DisplayName = "";          // \\.\DISPLAY2
        public int Number;                       // 2 (Windows display number)
        public string Problem;                   // why the size is unknown, or null
        public double PpMm => Ppi / 25.4;

        public string Describe()
        {
            if (Problem != null) return $"{Name} - size unknown ({Problem})";
            string size = WidthMm > 0 ? $"{WidthMm:0} mm wide, " : "";
            return $"{Name} | {PixelsWide}x{PixelsHigh} px | {size}{Ppi:0.00} ppi";
        }

        public string Key => $"{Device}|{Ppi:0.000}";

        // ---- Win32 ----
        [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct MONITORINFOEX
        {
            public int cbSize; public RECT rcMonitor; public RECT rcWork; public uint dwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string szDevice;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct DISPLAY_DEVICE
        {
            public int cb;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceString;
            public int StateFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceID;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string DeviceKey;
        }

        [DllImport("user32")] static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);
        [DllImport("user32", CharSet = CharSet.Unicode)] static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFOEX info);
        [DllImport("user32", CharSet = CharSet.Unicode)] static extern bool EnumDisplayDevices(string device, uint index, ref DISPLAY_DEVICE dd, uint flags);
        delegate bool MonitorEnumProc(IntPtr hMonitor, IntPtr hdc, ref RECT rect, IntPtr data);
        [DllImport("user32")] static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc proc, IntPtr data);

        /// <summary>Every monitor connected, in Windows display order. A monitor whose size is unknown has Problem set.</summary>
        public static System.Collections.Generic.List<ScreenInfo> EnumerateAll()
        {
            var handles = new System.Collections.Generic.List<IntPtr>();
            MonitorEnumProc proc = (IntPtr h, IntPtr dc, ref RECT r, IntPtr d) => { handles.Add(h); return true; };
            EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, proc, IntPtr.Zero);
            GC.KeepAlive(proc);
            var list = new System.Collections.Generic.List<ScreenInfo>();
            foreach (var h in handles)
            {
                try { list.Add(FromMonitor(h)); }
                catch (ViewException ex) { list.Add(new ScreenInfo { Name = "unknown monitor", Problem = ex.Message }); }
            }
            list.Sort((a, b) => a.Number.CompareTo(b.Number));
            return list;
        }

        /// <summary>Monitor showing hwnd. Throws ViewException with a user-facing reason if its size is unknown.</summary>
        public static ScreenInfo Detect(IntPtr hwnd) => FromMonitor(MonitorFromWindow(hwnd, 2));   // nearest

        static readonly System.Collections.Generic.Dictionary<string, Edid> EdidCache = new System.Collections.Generic.Dictionary<string, Edid>();

        static ScreenInfo FromMonitor(IntPtr mon)
        {
            var mi = new MONITORINFOEX { cbSize = Marshal.SizeOf(typeof(MONITORINFOEX)) };
            if (mon == IntPtr.Zero || !GetMonitorInfo(mon, ref mi))
                throw new ViewException("monitor of the view not found");
            int pxW = mi.rcMonitor.Right - mi.rcMonitor.Left, pxH = mi.rcMonitor.Bottom - mi.rcMonitor.Top;

            string id = null, label = "";
            var dd = new DISPLAY_DEVICE { cb = Marshal.SizeOf(typeof(DISPLAY_DEVICE)) };
            for (uint i = 0; EnumDisplayDevices(mi.szDevice, i, ref dd, 1); i++)   // EDD_GET_DEVICE_INTERFACE_NAME
            {
                if (id == null) { id = dd.DeviceID; label = dd.DeviceString; }
                if ((dd.StateFlags & 1) != 0) { id = dd.DeviceID; label = dd.DeviceString; break; }   // active
                dd.cb = Marshal.SizeOf(typeof(DISPLAY_DEVICE));
            }
            if (string.IsNullOrEmpty(id)) throw new ViewException($"no monitor id for {mi.szDevice}");

            if (!EdidCache.TryGetValue(id, out Edid edid)) { edid = ReadEdid(id); EdidCache[id] = edid; }
            if (edid.WidthMm <= 0) throw new ViewException($"monitor {edid.Name} does not report its size");
            int realW = pxW;
            // Pixels virtualised by Windows scaling: the EDID native mode gives the real count.
            if (edid.NativeW > 0 && Math.Abs(pxW - edid.NativeW) > 2 && pxW < edid.NativeW &&
                Math.Abs((double)edid.NativeW / pxW * pxH - edid.NativeH) < 3)
                realW = edid.NativeW;
            var s = new ScreenInfo
            {
                Ppi = realW / (edid.WidthMm / 25.4),
                Name = string.IsNullOrEmpty(edid.Name) ? (string.IsNullOrEmpty(label) ? mi.szDevice : label) : edid.Name,
                Source = edid.SizeSource,
                WidthMm = edid.WidthMm,
                PixelsWide = realW,
                PixelsHigh = pxH,
                Device = id,
                DisplayName = mi.szDevice,
                Number = DisplayNumber(mi.szDevice),
            };
            return s;
        }

        static int DisplayNumber(string device)
        {
            int i = device.Length;
            while (i > 0 && char.IsDigit(device[i - 1])) i--;
            return int.TryParse(device.Substring(i), out int n) ? n : 0;
        }

        sealed class Edid
        {
            public string Name = "";
            public double WidthMm, HeightMm;
            public int NativeW, NativeH;
            public string SizeSource = "";
        }

        static Edid ReadEdid(string deviceId)
        {
            // \\?\DISPLAY#ACI32A4#5&...#{guid} -> HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\ACI32A4\5&...\Device Parameters
            var parts = deviceId.Split('#');
            if (parts.Length < 3) throw new ViewException($"unexpected monitor id '{deviceId}'");
            string key = $@"SYSTEM\CurrentControlSet\Enum\DISPLAY\{parts[1]}\{parts[2]}\Device Parameters";
            byte[] e;
            using (var k = Registry.LocalMachine.OpenSubKey(key, false))
                e = k?.GetValue("EDID") as byte[];
            if (e == null) throw new ViewException("monitor EDID not found in the registry");
            return Parse(e);
        }

        static Edid Parse(byte[] e)
        {
            var hdr = new byte[] { 0, 255, 255, 255, 255, 255, 255, 0 };
            if (e.Length < 128) throw new ViewException("monitor EDID too short");
            for (int i = 0; i < 8; i++)
                if (e[i] != hdr[i]) throw new ViewException("monitor EDID unreadable");
            var info = new Edid();
            foreach (int off in new[] { 54, 72, 90, 108 })
            {
                if (e[off] == 0 && e[off + 1] == 0)
                {
                    if (e[off + 3] == 0xFC)
                    {
                        var sb = new StringBuilder();
                        for (int i = off + 5; i < off + 18 && e[i] != 0x0A; i++) sb.Append((char)e[i]);
                        info.Name = sb.ToString().Trim();
                    }
                }
                else if (info.NativeW == 0)
                {
                    int hw = e[off + 2] | ((e[off + 4] & 0xF0) << 4);
                    int vh = e[off + 5] | ((e[off + 7] & 0xF0) << 4);
                    int wmm = e[off + 12] | ((e[off + 14] & 0xF0) << 4);
                    int hmm = e[off + 13] | ((e[off + 14] & 0x0F) << 8);
                    if (hw > 0 && vh > 0)
                    {
                        info.NativeW = hw; info.NativeH = vh;
                        if (wmm > 0 && hmm > 0) { info.WidthMm = wmm; info.HeightMm = hmm; info.SizeSource = "EDID (1 mm)"; }
                    }
                }
            }
            if (info.WidthMm <= 0 && e[21] > 0 && e[22] > 0)
            {
                info.WidthMm = e[21] * 10.0; info.HeightMm = e[22] * 10.0; info.SizeSource = "EDID (1 cm)";
            }
            return info;
        }
    }
}
