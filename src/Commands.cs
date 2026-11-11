using System.Runtime.InteropServices;
using Rhino;
using Rhino.Commands;
using Rhino.UI;

namespace PixelViewCC
{
    /// <summary>PixelView: opens the panel and turns the pixel view on.</summary>
    [Guid("b743ff75-aab6-4473-b794-6cf1a29f9165")]
    public class PixelViewCommand : Command
    {
        public override string EnglishName => "PixelView";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            Panels.OpenPanel(typeof(PixelViewPanel).GUID);
            Controller.SetEnabled(true);
            if (!(doc.Views.ActiveView is Rhino.Display.RhinoPageView))
                RhinoApp.WriteLine("PixelView_CC: open a layout tab to see it in pixels.");
            Log.Write("RUN", "PixelView command");
            return Result.Success;
        }
    }

    /// <summary>PixelViewLogFolder (hidden): opens the folder of the PixelView_CC logs.</summary>
    [Guid("e2a4c1d8-7b3f-4c62-9f15-0d8a6e3b5c71")]
    [CommandStyle(Style.Hidden)]
    public class PixelViewLogFolderCommand : Command
    {
        public override string EnglishName => "PixelViewLogFolder";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            if (string.IsNullOrEmpty(Log.Folder)) { RhinoApp.WriteLine("PixelView_CC: no log folder."); return Result.Failure; }
            RhinoApp.WriteLine("PixelView_CC logs: " + Log.Folder);
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo { FileName = Log.Folder, UseShellExecute = true });
            return Result.Success;
        }
    }
}
