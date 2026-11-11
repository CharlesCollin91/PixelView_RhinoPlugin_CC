using System;
using System.Diagnostics;
using System.Drawing;
using Rhino;
using Rhino.Commands;
using Rhino.Display;
using Rhino.Geometry;

namespace PixelViewCC
{
    /// <summary>Sheet as seen in a layout viewport: z = viewport pixels per paper mm, (Ox, Oy) = sheet top-left in client pixels.</summary>
    internal struct Mapping
    {
        public double Z, Ox, Oy;
        public int ViewW, ViewH;
        public Guid Viewport;
    }

    /// <summary>A rendered part of the sheet: image + where it sits, in sheet pixels at zoom Z.</summary>
    internal sealed class Tile : IDisposable
    {
        public DisplayBitmap Image;
        public double Z;
        public int X0, Y0, W, H;
        public double Ms, UsedPpi;
        public Guid Viewport;
        public bool Covers(int x0, int y0, int x1, int y1) => x0 >= X0 && y0 >= Y0 && x1 <= X0 + W && y1 <= Y0 + H;
        public void Dispose() { Image?.Dispose(); Image = null; }
    }

    internal enum MessageKind { Info, Success, Warning, Error }

    /// <summary>
    /// State and logic of PixelView: settings, PDF of the active layout, rendered tile, screen size.
    /// Work runs on Rhino's Idle event (UI thread). Document events only set flags.
    /// </summary>
    internal static class Controller
    {
        const double AutoQuietSeconds = 1.2;      // document quiet this long -> automatic refresh
        const double ViewSettleSeconds = 0.15;    // view still this long -> sharp render
        public const double ZoomMin = 0.05, ZoomMax = 64.0;

        static PixelViewPlugIn _plugin;
        static PixelConduit _conduit;

        // settings (kept by Rhino between sessions)
        public static bool Enabled { get; private set; }
        public static bool Auto { get; private set; } = true;
        public static ColorChoice Color { get; private set; } = ColorChoice.DisplayColor;
        public static double RasterPpi { get; private set; } = 600;   // like a Photoshop document resolution
        public static bool ManualScreen { get; private set; }
        public static double ManualPpi { get; private set; } = 96;

        // state
        static Pdfium.Document _pdf;
        static string _pdfKey;
        public static double PdfSeconds { get; private set; }
        public static double SheetWmm { get; private set; }
        public static double SheetHmm { get; private set; }
        public static Tile Current { get; private set; }
        public static ScreenInfo Screen { get; private set; }
        public static System.Collections.Generic.List<ScreenInfo> Monitors { get; private set; } = new System.Collections.Generic.List<ScreenInfo>();
        public static string ScreenProblem { get; private set; }
        public static string LayoutName { get; private set; } = "";
        public static double RealZoom { get; private set; }
        public static string Message { get; private set; } = "";
        public static MessageKind MessageKind { get; private set; }
        public static bool Busy { get; private set; }

        static DateTime _docDirtyAt = DateTime.MinValue;
        static DateTime _lastTick = DateTime.MinValue, _lastScreen = DateTime.MinValue;
        static Mapping _lastMapping;
        static DateTime _mappingChangedAt = DateTime.MinValue;
        static string _colorDefaultFor;

        public static event EventHandler StateChanged;

        // ---- lifecycle ----
        public static void Init(PixelViewPlugIn plugin)
        {
            _plugin = plugin;
            var s = plugin.Settings;
            Enabled = s.GetBool("Enabled", false);
            Auto = s.GetBool("Auto", true);
            RasterPpi = s.GetDouble("RasterPpi", 600);
            ManualScreen = s.GetBool("ManualScreen", false);
            ManualPpi = s.GetDouble("ManualPpi", 96);
            string c = s.GetString("Color", "");
            if (Enum.TryParse(c, out ColorChoice cc)) Color = cc; else _colorDefaultFor = "";   // first run: Rhino's default
            Log.Write("SETTING", $"Enabled={Enabled} Auto={Auto} Color={(c == "" ? "(Rhino default)" : c)} RasterPpi={RasterPpi} " +
                                 $"ManualScreen={ManualScreen} ManualPpi={ManualPpi}");

            _conduit = new PixelConduit { Enabled = true };
            RhinoApp.Idle += OnIdle;
            RhinoDoc.AddRhinoObject += OnDocChanged;
            RhinoDoc.DeleteRhinoObject += OnDocChanged;
            RhinoDoc.UndeleteRhinoObject += OnDocChanged;
            RhinoDoc.ReplaceRhinoObject += OnDocChanged;
            RhinoDoc.ModifyObjectAttributes += OnDocChanged;
            RhinoDoc.LayerTableEvent += OnDocChanged;
            RhinoDoc.DimensionStyleTableEvent += OnDocChanged;
            RhinoDoc.MaterialTableEvent += OnDocChanged;
            RhinoDoc.DocumentPropertiesChanged += OnDocChanged;
            RhinoDoc.EndOpenDocument += OnDocReset;
            RhinoDoc.NewDocument += OnDocReset;
            RhinoDoc.CloseDocument += OnDocReset;
            Command.EndCommand += (s2, e2) => RedrawActive();
            RefreshMonitors();
        }

        public static void Shutdown()
        {
            try
            {
                RhinoApp.Idle -= OnIdle;
                if (_conduit != null) _conduit.Enabled = false;
                Current?.Dispose();
                Current = null;
                _pdf?.Dispose();
                _pdf = null;
            }
            catch (Exception ex) { Log.Error("shutdown", ex); }
        }

        static void OnDocChanged(object sender, EventArgs e)
        {
            if (!Busy) _docDirtyAt = DateTime.Now;          // our own printing must not trigger a refresh
        }

        static void OnDocReset(object sender, EventArgs e)
        {
            _pdfKey = null;
            Current?.Dispose();
            Current = null;
        }

        // ---- settings from the panel ----
        public static void SetEnabled(bool on)
        {
            if (Enabled == on) return;
            Enabled = on;
            _plugin.Settings.SetBool("Enabled", on);
            Log.Write("SETTING", $"Enabled = {on}");
            RedrawActive();
            Notify();
        }

        public static void SetAuto(bool on) { Auto = on; _plugin.Settings.SetBool("Auto", on); Log.Write("SETTING", $"Auto = {on}"); }

        public static void SetColor(ColorChoice c)
        {
            if (Color == c) return;
            Color = c; _plugin.Settings.SetString("Color", c.ToString());
            Log.Write("SETTING", $"Color = {c}");
        }

        public static void SetRasterPpi(double ppi)
        {
            RasterPpi = Math.Max(150, Math.Min(1200, ppi)); _plugin.Settings.SetDouble("RasterPpi", RasterPpi);
            Log.Write("SETTING", $"RasterPpi = {RasterPpi}");
        }

        public static void SetScreen(bool manual, double ppi)
        {
            ManualScreen = manual; ManualPpi = Math.Max(40, Math.Min(600, ppi));
            _plugin.Settings.SetBool("ManualScreen", manual); _plugin.Settings.SetDouble("ManualPpi", ManualPpi);
            Log.Write("SETTING", $"Screen manual={manual} ppi={ManualPpi}");
            _lastScreen = DateTime.MinValue;
        }

        public static void RequestRefresh(string why)
        {
            Log.Write("RUN", "refresh asked: " + why);
            _pdfKey = null;
        }

        // ---- idle loop ----
        static void OnIdle(object sender, EventArgs e)
        {
            var now = DateTime.Now;
            if ((now - _lastTick).TotalMilliseconds < 100) return;
            _lastTick = now;
            try { Tick(now); }
            catch (Exception ex) { Log.Error("idle", ex); Say("unexpected error: " + ex.Message + " (command PixelViewLogFolder opens the log).", MessageKind.Error); }
        }

        static RhinoPageView ActiveLayout(out RhinoDoc doc)
        {
            doc = RhinoDoc.ActiveDoc;
            return doc?.Views.ActiveView as RhinoPageView;
        }

        static void Tick(DateTime now)
        {
            var pv = ActiveLayout(out RhinoDoc doc);
            string name = pv?.PageName ?? "";
            if (name != LayoutName) { LayoutName = name; Notify(); }
            if (!Enabled || pv == null || Busy) return;
            if (Command.InCommand()) return;

            if (_colorDefaultFor != null)
            {
                Color = LayoutPdf.RhinoDefault(pv);
                _colorDefaultFor = null;
                Log.Write("SETTING", $"Color = {Color} (Rhino's default)");
                Notify();
            }
            if ((now - _lastScreen).TotalSeconds > 0.5) UpdateScreen(pv, now);

            string key = $"{doc.RuntimeSerialNumber}|{pv.MainViewport.Id}|{Color}|{RasterPpi}";
            bool autoDue = Auto && _docDirtyAt != DateTime.MinValue && (now - _docDirtyAt).TotalSeconds > AutoQuietSeconds;
            if (key != _pdfKey || autoDue)          // _pdfKey keeps a failed key too: no retry loop on errors
            {
                BuildPdf(pv, key, _pdf == null ? "open" : key != _pdfKey ? "layout, settings or Refresh" : "document changed (Auto)");
                return;
            }
            if (_pdf == null) return;
            if (!pv.PageIsActive) return;          // working inside a detail: Rhino's own display
            if (!TryMapping(pv, out Mapping m)) return;
            if (!Same(m, _lastMapping)) { _lastMapping = m; _mappingChangedAt = now; UpdateZoomLabel(m); }
            if ((now - _mappingChangedAt).TotalSeconds < ViewSettleSeconds) return;
            var rect = Visible(m);
            if (rect == null) return;
            var t = Current;
            bool fresh = t != null && t.Viewport == m.Viewport && Math.Abs(t.Z - m.Z) <= 1e-9 * m.Z &&
                         t.Covers(rect.Value.X0, rect.Value.Y0, rect.Value.X1, rect.Value.Y1);
            if (!fresh) Render(pv, m, rect.Value);
        }

        static bool Same(Mapping a, Mapping b) =>
            a.Viewport == b.Viewport && Math.Abs(a.Z - b.Z) < 1e-9 && Math.Abs(a.Ox - b.Ox) < 0.01 && Math.Abs(a.Oy - b.Oy) < 0.01 &&
            a.ViewW == b.ViewW && a.ViewH == b.ViewH;

        // ---- PDF ----
        static void BuildPdf(RhinoPageView pv, string key, string why)
        {
            _docDirtyAt = DateTime.MinValue;        // a failure must not retry on every tick
            Busy = true;
            Notify();
            try
            {
                Log.Write("RUN", $"refresh ({why}): layout='{pv.PageName}' color={Color} raster={RasterPpi} ppi");
                byte[] bytes = LayoutPdf.Print(pv, Color, RasterPpi, out double sec);
                var doc = new Pdfium.Document(bytes);
                _pdf?.Dispose();
                _pdf = doc;
                _pdfKey = key;
                PdfSeconds = sec;
                SheetWmm = doc.WidthMm;
                SheetHmm = doc.HeightMm;
                Current?.Dispose();
                Current = null;
                _mappingChangedAt = DateTime.MinValue;
                Say($"{pv.PageName}: {SheetWmm:0} x {SheetHmm:0} mm, printed in {sec:0.0} s", MessageKind.Success);
            }
            catch (ViewException ex)
            {
                _pdfKey = key;                      // do not retry until something changes
                Log.Write("SKIP", ex.Message);
                Say(ex.Message, MessageKind.Error);
            }
            catch (Exception ex)
            {
                _pdfKey = key;
                Log.Error("BuildPdf", ex);
                Say("PDF failed: " + ex.Message + " (see log)", MessageKind.Error);
            }
            finally
            {
                Busy = false;
                Notify();
            }
        }

        // ---- geometry of the view ----
        static double MmToPage(RhinoDoc doc) => RhinoMath.UnitScale(UnitSystem.Millimeters, doc.PageUnitSystem);

        public static bool TryMapping(RhinoPageView pv, out Mapping m)
        {
            m = default;
            if (SheetWmm <= 0 || SheetHmm <= 0) return false;
            var vp = pv.MainViewport;
            double k = MmToPage(pv.Document);
            Point2d a = vp.WorldToClient(new Point3d(0, SheetHmm * k, 0));
            Point2d b = vp.WorldToClient(new Point3d(SheetWmm * k, SheetHmm * k, 0));
            double z = (b.X - a.X) / SheetWmm;
            if (!(z > 0) || double.IsInfinity(z)) return false;
            Size sz = vp.Size;
            m = new Mapping { Z = z, Ox = a.X, Oy = a.Y, ViewW = sz.Width, ViewH = sz.Height, Viewport = vp.Id };
            return true;
        }

        public struct PixRect { public int X0, Y0, X1, Y1; }

        static PixRect? Visible(Mapping m)
        {
            double pw = SheetWmm * m.Z, ph = SheetHmm * m.Z;
            double ox = Math.Round(m.Ox), oy = Math.Round(m.Oy);
            double x0 = Math.Max(0, -ox), y0 = Math.Max(0, -oy);
            double x1 = Math.Min(pw, m.ViewW - ox), y1 = Math.Min(ph, m.ViewH - oy);
            if (x1 <= x0 || y1 <= y0) return null;
            return new PixRect { X0 = (int)Math.Floor(x0), Y0 = (int)Math.Floor(y0), X1 = (int)Math.Ceiling(x1), Y1 = (int)Math.Ceiling(y1) };
        }

        static void Render(RhinoPageView pv, Mapping m, PixRect r)
        {
            var sw = Stopwatch.StartNew();
            try
            {
                int w = r.X1 - r.X0, h = r.Y1 - r.Y0;
                Bitmap bmp = _pdf.Render(m.Z, r.X0, r.Y0, w, h, RasterPpi, out double usedPpi);
                var tile = new Tile { Image = new DisplayBitmap(bmp), Z = m.Z, X0 = r.X0, Y0 = r.Y0, W = w, H = h,
                                      Viewport = m.Viewport, Ms = sw.Elapsed.TotalMilliseconds, UsedPpi = usedPpi };
                bmp.Dispose();
                Current?.Dispose();
                Current = tile;
                Log.Write("VIEW", $"render z={m.Z:0.0000} px/mm real={RealZoom * 100:0.0}% rect=({r.X0},{r.Y0},{r.X1},{r.Y1}) " +
                                  $"{w}x{h} px from {usedPpi:0} ppi raster in {tile.Ms:0} ms (raster tiles made so far: {_pdf.TilesMade})");
                pv.Redraw();
                Notify();
            }
            catch (ViewException ex) { Say(ex.Message, MessageKind.Warning); Log.Write("SKIP", "render: " + ex.Message); }
        }

        /// <summary>Called by the conduit: the tile to draw in this viewport and where, or null.</summary>
        public static Tile TileFor(DrawEventArgs e, out int left, out int top)
        {
            left = top = 0;
            if (!Enabled || Busy || Current == null) return null;
            if (e.Display.IsPrinting) return null;
            var vp = e.Viewport;
            if (vp.ViewportType != ViewportType.PageViewMainViewport || vp.Id != Current.Viewport) return null;
            if (!(e.Viewport.ParentView is RhinoPageView pv) || !pv.PageIsActive) return null;
            if (Command.InCommand()) return null;                       // editing: Rhino's own display (decision B)
            foreach (var _ in pv.Document.Objects.GetSelectedObjects(false, false)) return null;   // selection must stay visible
            if (!TryMapping(pv, out Mapping m) || Math.Abs(m.Z - Current.Z) > 1e-9 * m.Z) return null;  // zooming: shown again once sharp
            left = (int)Math.Round(m.Ox) + Current.X0;
            top = (int)Math.Round(m.Oy) + Current.Y0;
            return Current;
        }

        // ---- screen and zoom ----
        static void UpdateScreen(RhinoPageView pv, DateTime now)
        {
            _lastScreen = now;
            ScreenInfo s;
            string problem = null;
            try { s = ScreenInfo.Detect(pv.Handle); }
            catch (Exception ex)
            {
                problem = ex.Message;
                s = new ScreenInfo { Ppi = ManualPpi, Name = "unknown monitor", Source = "manual (auto failed)" };
            }
            if (ManualScreen) { s.Ppi = ManualPpi; s.Source = "manual"; }
            if (Screen == null || Screen.Key != s.Key || problem != ScreenProblem)
            {
                Screen = s;
                ScreenProblem = problem;
                RefreshMonitors();
                Log.Write("SCREEN", $"using {s.Describe()} ({s.Source})" + (problem != null ? " | auto failed: " + problem : ""));
                if (problem != null && !ManualScreen)
                    Say($"monitor size not found ({problem}). In the PixelView_CC panel, Monitor: choose Manual input PPI and type your monitor's PPI.", MessageKind.Warning);
                if (TryMapping(pv, out Mapping m)) UpdateZoomLabel(m);
                Notify();
            }
        }

        public static void RefreshMonitors()
        {
            try
            {
                Monitors = ScreenInfo.EnumerateAll();
                foreach (var mon in Monitors) Log.Write("SCREEN", $"monitor {mon.Number} ({mon.DisplayName}): {mon.Describe()}");
            }
            catch (Exception ex) { Log.Error("monitor list", ex); }
            Notify();
        }

        static void UpdateZoomLabel(Mapping m)
        {
            double r = Screen != null && Screen.PpMm > 0 ? m.Z / Screen.PpMm : 0;
            if (Math.Abs(r - RealZoom) > 1e-6) { RealZoom = r; Notify(); }
        }

        public static void ZoomFit()
        {
            var pv = ActiveLayout(out RhinoDoc doc);
            if (!CheckZoomTarget(pv)) return;
            double k = MmToPage(doc);
            var vp = pv.MainViewport;
            vp.ZoomBoundingBox(new BoundingBox(0, 0, 0, SheetWmm * k, SheetHmm * k, 0));
            pv.Redraw();
            Log.Write("VIEW", "fit");
        }

        public static void ZoomReal(double real) => ZoomTo(real, "zoom " + (real * 100).ToString("0") + "%");

        public static void ZoomStep(double factor) => ZoomTo(RealZoom * factor, "zoom x" + factor.ToString("0.00"));

        static void ZoomTo(double real, string why)
        {
            var pv = ActiveLayout(out RhinoDoc doc);
            if (!CheckZoomTarget(pv)) return;
            UpdateScreen(pv, DateTime.Now);
            real = Math.Max(ZoomMin, Math.Min(ZoomMax, real));
            var vp = pv.MainViewport;
            for (int i = 0; i < 3; i++)                               // Magnify about the view centre, then correct
            {
                if (!TryMapping(pv, out Mapping m)) return;
                double factor = real * Screen.PpMm / m.Z;
                if (Math.Abs(factor - 1) < 1e-6) break;
                vp.Magnify(factor, false);
            }
            pv.Redraw();
            if (TryMapping(pv, out Mapping done)) UpdateZoomLabel(done);
            Log.Write("VIEW", $"{why}: real zoom now {RealZoom * 100:0.00}%");
        }

        static bool CheckZoomTarget(RhinoPageView pv)
        {
            if (pv == null) { Say("Open a layout tab first.", MessageKind.Warning); return false; }
            if (SheetWmm <= 0) { Say("Click Enable in the PixelView_CC panel first.", MessageKind.Warning); return false; }
            if (!pv.PageIsActive) { Say("A detail is active: double-click outside it first.", MessageKind.Warning); return false; }
            return true;
        }

        // ---- helpers ----
        static string _lastSaid;

        /// <summary>Messages go where Rhino's own commands write them: the command line (warnings, errors) and the log.</summary>
        static void Say(string text, MessageKind kind)
        {
            Message = text;
            MessageKind = kind;
            Log.Write(kind == MessageKind.Error ? "ERROR" : kind == MessageKind.Warning ? "WARN" : "INFO", text);
            if ((kind == MessageKind.Error || kind == MessageKind.Warning) && text != _lastSaid)
                RhinoApp.WriteLine("PixelView_CC: " + text);
            _lastSaid = text;
            Notify();
        }

        static void Notify()
        {
            try { StateChanged?.Invoke(null, EventArgs.Empty); }
            catch (Exception ex) { Log.Error("panel update", ex); }
        }

        static void RedrawActive()
        {
            try { RhinoDoc.ActiveDoc?.Views.ActiveView?.Redraw(); } catch { /* ignore */ }
        }
    }

    internal sealed class PixelConduit : DisplayConduit
    {
        protected override void DrawForeground(DrawEventArgs e)
        {
            try
            {
                var t = Controller.TileFor(e, out int left, out int top);
                if (t?.Image != null) e.Display.DrawBitmap(t.Image, left, top);
            }
            catch (Exception ex) { Log.Error("conduit", ex); }
        }
    }
}
