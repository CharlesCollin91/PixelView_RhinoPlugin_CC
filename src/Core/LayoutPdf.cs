using System;
using System.Diagnostics;
using System.IO;
using Rhino;
using Rhino.Display;
using Rhino.FileIO;

namespace PixelViewCC
{
    internal enum ColorChoice { DisplayColor, PrintColor, BlackAndWhite }

    /// <summary>Prints one layout to a vector PDF in memory, with Rhino's own PDF engine (as Print > Rhino PDF).</summary>
    internal static class LayoutPdf
    {
        public static byte[] Print(RhinoPageView page, ColorChoice color, double dpi, out double seconds)
        {
            var sw = Stopwatch.StartNew();
            var settings = new ViewCaptureSettings(page, dpi);
            if (!settings.IsValid)
                throw new ViewException($"Rhino refused to print layout '{page.PageName}'.");
            settings.RasterMode = false;   // vector, like Rhino's Print default
            switch (color)
            {
                case ColorChoice.PrintColor: settings.OutputColor = ViewCaptureSettings.ColorMode.PrintColor; break;
                case ColorChoice.BlackAndWhite: settings.OutputColor = ViewCaptureSettings.ColorMode.BlackAndWhite; break;
                default: settings.OutputColor = ViewCaptureSettings.ColorMode.DisplayColor; break;
            }
            Log.Write("API", $"capture '{page.PageName}': OutputColor={settings.OutputColor} RasterMode={settings.RasterMode} " +
                             $"UsePrintWidths={settings.UsePrintWidths} Resolution={settings.Resolution} MediaSize={settings.MediaSize}");
            var pdf = FilePdf.Create();
            if (pdf == null)
                throw new ViewException("Rhino's PDF engine is not available (Rhino PDF plug-in disabled?).");
            int index = pdf.AddPage(settings);
            byte[] bytes;
            using (var ms = new MemoryStream())
            {
                pdf.Write(ms);
                bytes = ms.ToArray();
            }
            if (bytes.Length < 8 || bytes[0] != (byte)'%' || bytes[1] != (byte)'P')
                throw new ViewException("Rhino wrote an empty PDF.");
            seconds = sw.Elapsed.TotalSeconds;
            Log.Write("DONE", $"PDF of '{page.PageName}' (page {index}) built in {seconds:0.00} s, {bytes.Length / 1024} KB");
            return bytes;
        }

        public static ColorChoice RhinoDefault(RhinoPageView page)
        {
            try
            {
                var c = new ViewCaptureSettings(page, 300).OutputColor;
                if (c == ViewCaptureSettings.ColorMode.PrintColor) return ColorChoice.PrintColor;
                if (c == ViewCaptureSettings.ColorMode.BlackAndWhite) return ColorChoice.BlackAndWhite;
            }
            catch (Exception ex) { Log.Error("default OutputColor", ex); }
            return ColorChoice.DisplayColor;
        }
    }
}
