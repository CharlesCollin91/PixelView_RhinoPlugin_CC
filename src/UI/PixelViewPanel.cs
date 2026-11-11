using System;
using System.Linq;
using System.Runtime.InteropServices;
using Eto.Drawing;
using Eto.Forms;

namespace PixelViewCC
{
    /// <summary>
    /// Dockable panel "PixelView_CC" (like Layouts / Layers). Built from Rhino's native Eto controls only, with no
    /// font or colour set in code, so Rhino styles it with the user's own theme, font and size. The two drawn
    /// controls (Enable, refresh icon) take their colours and font from a native button at paint time.
    /// </summary>
    [Guid("84e8540c-33c6-4947-be0a-ae902b611a77")]
    public class PixelViewPanel : Panel
    {
        static readonly Color EnabledGreen = Color.FromArgb(78, 154, 81);

        readonly PillButton _enable;
        readonly IconButton _refresh;
        readonly CheckBox _auto = new CheckBox { Text = "Auto" };
        readonly LinkButton _info = new LinkButton { Text = "Info" };
        readonly Button _fit = new Button { Text = "Fit" };
        readonly Button _real = new Button { Text = "1:1" };
        readonly Button _out = new Button { Text = "-" };
        readonly Button _in = new Button { Text = "+" };
        readonly Label _zoom = new Label { TextAlignment = TextAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Width = 56 };
        readonly DropDown _color = new DropDown();
        readonly NumericStepper _ppi = new NumericStepper { MinValue = 150, MaxValue = 1200, Increment = 50, DecimalPlaces = 0, Width = 80 };
        readonly RadioButtonList _screenMode = new RadioButtonList { Orientation = Orientation.Vertical, Spacing = new Size(0, 4) };
        readonly NumericStepper _manualPpi = new NumericStepper { MinValue = 40, MaxValue = 600, Increment = 1, DecimalPlaces = 2, Width = 80 };
        readonly StackLayout _monitors = new StackLayout { Orientation = Orientation.Vertical, Spacing = 4 };
        string _monitorsShown = "";
        bool _updating;

        public PixelViewPanel(uint documentSerialNumber)
        {
            _enable = new PillButton("Enable", () => _fit);
            _refresh = new IconButton(() => _fit);
            foreach (var c in new[] { "Display color", "Print color", "Black & white" }) _color.Items.Add(c);
            _screenMode.Items.Add("Auto detect monitor size");
            _screenMode.Items.Add("Manual input PPI");

            _enable.ToolTip = "Show the active layout as pixels, exactly like Rhino's PDF placed in Photoshop: lines thinner " +
                              "than a pixel become light grey, so lineweights look as they will print. Hidden while a command " +
                              "runs or objects are selected.";
            _refresh.ToolTip = "Refresh: print the layout again and redraw (needed after zooming or panning inside a detail).";
            _auto.ToolTip = "Refresh by itself about 1 s after the document changes. Never during a command.";
            _info.ToolTip = "Open the PixelView_CC page in your browser: how it works, help, updates, bug reports.\n" +
                            PixelViewPlugIn.InfoUrl + "  (version " + PixelViewPlugIn.PackageVersion + ")";
            _fit.ToolTip = "Zoom the layout to the whole sheet.";
            _real.ToolTip = "Real size: 10 mm on the sheet = 10 mm on your screen.";
            _out.ToolTip = "Zoom out.";
            _in.ToolTip = "Zoom in.";
            _zoom.ToolTip = "Real zoom: 100% = real size on this monitor.";
            _color.ToolTip = "Colours of the PDF, as in Rhino's Print dialog.";
            _ppi.ToolTip = "Raster resolution, like a Photoshop document: Rhino's PDF is rasterised at this resolution, then " +
                           "shown at your zoom (shaded details are printed at it too). 600 by default; higher is slower.";
            _screenMode.ToolTip = "1:1 needs the real size of your monitor. Auto reads it from the monitor itself (EDID). " +
                                  "Manual: pixels per inch = horizontal pixels / screen width in inches.";
            _manualPpi.ToolTip = "Pixels per inch of your monitor. Check: at 1:1, a 100 mm line measures 100 mm with a ruler.";

            _enable.Click += Safe("Enable", () => Controller.SetEnabled(!Controller.Enabled));
            _refresh.Click += Safe("Refresh", () => Controller.RequestRefresh("refresh button"));
            _auto.CheckedChanged += Safe("Auto", () => Controller.SetAuto(_auto.Checked == true));
            _info.Click += Safe("Info", () => System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo { FileName = PixelViewPlugIn.InfoUrl, UseShellExecute = true }));
            _fit.Click += Safe("Fit", Controller.ZoomFit);
            _real.Click += Safe("1:1", () => Controller.ZoomReal(1.0));
            _out.Click += Safe("Zoom out", () => Controller.ZoomStep(1 / 1.25));
            _in.Click += Safe("Zoom in", () => Controller.ZoomStep(1.25));
            _color.SelectedIndexChanged += Safe("Color", () => Controller.SetColor((ColorChoice)Math.Max(0, _color.SelectedIndex)));
            _ppi.ValueChanged += Safe("Raster ppi", () => Controller.SetRasterPpi(_ppi.Value));
            _screenMode.SelectedIndexChanged += Safe("Screen", ApplyScreen);
            _manualPpi.ValueChanged += Safe("Manual ppi", ApplyScreen);

            foreach (var b in new[] { _fit, _real }) b.MinimumSize = new Size(44, -1);
            foreach (var b in new[] { _out, _in }) b.MinimumSize = new Size(28, -1);

            var top = new TableLayout { Spacing = new Size(6, 0) };
            top.Rows.Add(new TableRow(_enable.Control, _refresh.Control, _auto, new TableCell(null, true), _info));

            var view = new DynamicLayout { Spacing = new Size(4, 6), Padding = new Padding(4, 6, 0, 6) };
            view.AddRow(_fit, _real, null, _out, _zoom, _in);

            var output = new DynamicLayout { Spacing = new Size(6, 6), Padding = new Padding(4, 6, 0, 6) };
            output.AddRow(new Label { Text = "Color", VerticalAlignment = VerticalAlignment.Center }, _color);
            output.AddRow(new Label { Text = "Raster", VerticalAlignment = VerticalAlignment.Center },
                          Row(_ppi, new Label { Text = "ppi", VerticalAlignment = VerticalAlignment.Center }));

            var monitor = new DynamicLayout { Spacing = new Size(6, 6), Padding = new Padding(4, 6, 0, 6) };
            monitor.AddRow(_screenMode);
            monitor.AddRow(Row(new Label { Text = "PPI", VerticalAlignment = VerticalAlignment.Center }, _manualPpi));
            monitor.AddRow(_monitors);

            var layout = new DynamicLayout { Padding = new Padding(8), Spacing = new Size(6, 8) };
            layout.AddRow(top);
            layout.AddRow(Section("View", view));
            layout.AddRow(Section("Output", output));
            layout.AddRow(Section("Monitor (Screen)", monitor));
            layout.Add(null);
            Content = new Scrollable { Content = layout, Border = BorderType.None };

            Controller.StateChanged += OnState;
            OnState(null, EventArgs.Empty);
        }

        protected override void Dispose(bool disposing)
        {
            Controller.StateChanged -= OnState;
            base.Dispose(disposing);
        }

        static Control Section(string title, Control content) =>
            new Expander { Header = new Label { Text = title }, Content = content, Expanded = true };

        static StackLayout Row(params Control[] items)
        {
            var st = new StackLayout { Orientation = Orientation.Horizontal, Spacing = 6, VerticalContentAlignment = VerticalAlignment.Center };
            foreach (var c in items) st.Items.Add(new StackLayoutItem(c, false));
            return st;
        }

        void ApplyScreen()
        {
            bool manual = _screenMode.SelectedIndex == 1;
            _manualPpi.Enabled = manual;
            Controller.SetScreen(manual, _manualPpi.Value);
        }

        EventHandler<EventArgs> Safe(string name, Action a) => (s, e) =>
        {
            if (_updating) return;
            try { a(); }
            catch (Exception ex)
            {
                Log.Error(name, ex);
                Rhino.RhinoApp.WriteLine($"PixelView_CC: error in {name}: {ex.Message}");
            }
        };

        void OnState(object sender, EventArgs e)
        {
            _updating = true;
            try
            {
                _enable.On = Controller.Enabled;
                _refresh.Enabled = !Controller.Busy;
                _auto.Checked = Controller.Auto;
                double r = Controller.RealZoom;
                _zoom.Text = r <= 0 ? "-" : (r >= 0.1 ? $"{r * 100:0}%" : $"{r * 100:0.0}%");
                _color.SelectedIndex = (int)Controller.Color;
                _ppi.Value = Controller.RasterPpi;
                _screenMode.SelectedIndex = Controller.ManualScreen ? 1 : 0;
                _manualPpi.Enabled = Controller.ManualScreen;
                var s = Controller.Screen;
                if (!Controller.ManualScreen && s != null && Controller.ScreenProblem == null) _manualPpi.Value = Math.Round(s.Ppi, 2);
                else if (Controller.ManualScreen) _manualPpi.Value = Controller.ManualPpi;
                UpdateMonitors(s);
            }
            catch (Exception ex) { Log.Error("panel refresh", ex); }
            finally { _updating = false; }
        }

        void UpdateMonitors(ScreenInfo inUse)
        {
            var lines = Controller.Monitors.Select((m, i) =>
                $"Monitor {i + 1}: {m.Describe()}" + (inUse != null && m.Device == inUse.Device && m.Device != "" ? "  (in use)" : "")).ToList();
            string key = string.Join("\n", lines);
            if (key == _monitorsShown) return;
            _monitorsShown = key;
            _monitors.Items.Clear();
            foreach (var t in lines) _monitors.Items.Add(new StackLayoutItem(new Label { Text = t, Wrap = WrapMode.Word }, true));
        }

        static Color Mix(Color a, Color b, float t) =>
            Color.FromArgb((int)((a.R + (b.R - a.R) * t) * 255), (int)((a.G + (b.G - a.G) * t) * 255), (int)((a.B + (b.B - a.B) * t) * 255));

        // ---- drawn controls: colours and font read from a native button, so they follow Rhino's theme ----
        static (Color back, Color text, Font font) Theme(Func<Button> probe)
        {
            Color back = Colors.Transparent, text = Colors.Transparent;
            Font font = null;
            try
            {
                var b = probe();
                back = b.BackgroundColor;
                text = b.TextColor;
                font = b.Font;
            }
            catch { /* fall back below */ }
            bool dark = Rhino.Runtime.HostUtils.RunningInDarkMode;
            if (back.A < 0.05f) back = dark ? Color.FromArgb(62, 62, 66) : Color.FromArgb(225, 225, 225);
            if (text.A < 0.05f) text = dark ? Color.FromArgb(230, 230, 230) : Colors.Black;
            return (back, text, font ?? SystemFonts.Default());
        }

        /// <summary>Rounded "Enable" button: theme colours when off, green when on.</summary>
        sealed class PillButton
        {
            readonly string _text;
            readonly Func<Button> _probe;
            bool _hover, _on;
            public readonly Drawable Control = new Drawable { Size = new Size(84, 26) };
            public event EventHandler<EventArgs> Click;
            public string ToolTip { set => Control.ToolTip = value; }
            public bool On { get => _on; set { _on = value; Control.Invalidate(); } }

            public PillButton(string text, Func<Button> probe)
            {
                _text = text;
                _probe = probe;
                Control.Cursor = Cursors.Pointer;
                Control.MouseEnter += (s, e) => { _hover = true; Control.Invalidate(); };
                Control.MouseLeave += (s, e) => { _hover = false; Control.Invalidate(); };
                Control.MouseUp += (s, e) => Click?.Invoke(this, EventArgs.Empty);
                Control.Paint += (s, e) =>
                {
                    try
                    {
                        var (back, text2, font) = Theme(_probe);
                        var g = e.Graphics;
                        g.AntiAlias = true;
                        var rect = new RectangleF(0.5f, 0.5f, Control.Width - 1f, Control.Height - 1f);
                        var path = GraphicsPath.GetRoundRect(rect, rect.Height / 2f);
                        Color fill = _on ? EnabledGreen : back;
                        if (_hover) fill = Mix(fill, _on ? Colors.White : text2, 0.12f);
                        g.FillPath(fill, path);
                        g.DrawPath(new Pen(_on ? EnabledGreen : Mix(back, text2, 0.35f), 1f), path);
                        var sz = g.MeasureString(font, _text);
                        g.DrawText(font, _on ? Colors.White : text2, (Control.Width - sz.Width) / 2f, (Control.Height - sz.Height) / 2f, _text);
                    }
                    catch (Exception ex) { Log.Error("Enable paint", ex); }
                };
            }
        }

        /// <summary>Small round-arrow refresh icon.</summary>
        sealed class IconButton
        {
            readonly Func<Button> _probe;
            bool _hover, _enabled = true;
            public readonly Drawable Control = new Drawable { Size = new Size(26, 26) };
            public event EventHandler<EventArgs> Click;
            public string ToolTip { set => Control.ToolTip = value; }
            public bool Enabled { get => _enabled; set { _enabled = value; Control.Invalidate(); } }

            public IconButton(Func<Button> probe)
            {
                _probe = probe;
                Control.Cursor = Cursors.Pointer;
                Control.MouseEnter += (s, e) => { _hover = true; Control.Invalidate(); };
                Control.MouseLeave += (s, e) => { _hover = false; Control.Invalidate(); };
                Control.MouseUp += (s, e) => { if (_enabled) Click?.Invoke(this, EventArgs.Empty); };
                Control.Paint += (s, e) =>
                {
                    try
                    {
                        var (back, text, _) = Theme(_probe);
                        var g = e.Graphics;
                        g.AntiAlias = true;
                        float w = Control.Width, h = Control.Height;
                        if (_hover && _enabled)
                            g.FillPath(back, GraphicsPath.GetRoundRect(new RectangleF(0.5f, 0.5f, w - 1f, h - 1f), 4f));
                        Color c = _enabled ? text : Mix(back, text, 0.4f);
                        float cx = w / 2f, cy = h / 2f, r = 6.5f;
                        g.DrawArc(new Pen(c, 1.6f), cx - r, cy - r, 2 * r, 2 * r, -60f, 300f);
                        double a = -60.0 * Math.PI / 180.0;
                        float ax = cx + r * (float)Math.Cos(a), ay = cy + r * (float)Math.Sin(a);
                        g.FillPolygon(c, new PointF(ax + 3.5f, ay - 1.5f), new PointF(ax - 3f, ay - 3f), new PointF(ax + 0.5f, ay + 3.5f));
                    }
                    catch (Exception ex) { Log.Error("refresh icon paint", ex); }
                };
            }
        }
    }
}
