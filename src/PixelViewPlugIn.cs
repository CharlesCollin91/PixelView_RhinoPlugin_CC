// PixelView_CC - Rhino 8 plug-in (Windows). GPL-3.0.
// Shows the active layout as pixels, like a PDF placed in Photoshop: lines thinner than a pixel become
// light grey instead of 1 black pixel, so lineweights look as they will print. True 1:1 zoom on screen.
using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using Rhino;
using Rhino.PlugIns;
using Rhino.UI;

[assembly: Guid("83c94a57-96c2-4eaf-bf51-9d1f35dd5b21")]
[assembly: PlugInDescription(DescriptionType.Organization, "Charles Collin Architecte")]
[assembly: PlugInDescription(DescriptionType.WebSite, "https://github.com/CharlesCollin91/PixelView_RhinoPlugin_CC")]
[assembly: PlugInDescription(DescriptionType.Country, "Canada")]

namespace PixelViewCC
{
    public class PixelViewPlugIn : PlugIn
    {
        public static PixelViewPlugIn Instance { get; private set; }
        /// <summary>Public page of the plug-in (GitHub): what it does, how to use it, updates, bug reports.</summary>
        public const string InfoUrl = "https://github.com/CharlesCollin91/PixelView_RhinoPlugin_CC";

        public static string PackageVersion => typeof(PixelViewPlugIn).Assembly.GetName().Version.ToString(3);

        public PixelViewPlugIn()
        {
            Instance = this;
        }

        // Loaded with Rhino so the panel and the layout display are back after a restart.
        public override PlugInLoadTime LoadTime => PlugInLoadTime.AtStartup;

        protected override LoadReturnCode OnLoad(ref string errorMessage)
        {
            Log.Start(Path.Combine(SettingsDirectory, "logs"));
            Log.Write("START", $"PixelView_CC v{PackageVersion} | Rhino {RhinoApp.Version} | CLR {Environment.Version} | {Environment.OSVersion}");
            if (!Rhino.Runtime.HostUtils.RunningOnWindows)
            {
                errorMessage = "PixelView_CC runs on Windows only.";
                Log.Write("ERROR", errorMessage);
                return LoadReturnCode.ErrorShowDialog;
            }
            string asmDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string pdfErr = Pdfium.Load(asmDir);
            if (pdfErr != null)
            {
                errorMessage = "PixelView_CC cannot start: " + pdfErr +
                               "\nReinstall PixelView_CC from the Package Manager (Tools > Package Manager).";
                Log.Write("ERROR", errorMessage);
                return LoadReturnCode.ErrorShowDialog;
            }
            System.Drawing.Icon icon = null;
            try
            {
                using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("PixelViewCC.PixelView.ico"))
                    icon = s != null ? new System.Drawing.Icon(s, 32, 32) : null;
            }
            catch (Exception ex) { Log.Write("ERROR", "panel icon: " + ex.Message); }
            Panels.RegisterPanel(this, typeof(PixelViewPanel), "PixelView_CC", icon);
            Controller.Init(this);
            Log.Write("START", "panel registered, controller ready");
            return LoadReturnCode.Success;
        }

        protected override void OnShutdown()
        {
            Controller.Shutdown();
            Log.Write("CLOSE", "Rhino closing");
            base.OnShutdown();
        }
    }
}
