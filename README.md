# PixelView_CC

<br>


**The plugin** : See your Rhino layouts on screen the way they will print: true lineweights, at true real-world size.
<img width="3837" height="2060" alt="PLugin1" src="https://github.com/user-attachments/assets/b3e0ca6f-93ca-41a9-9572-6f153e458d7d" />
<br>
<!-- Header images: the panel (left) and a layout seen with PixelView (right), side by side. -->

**What it does** :
- CAD and vector programs draw your linework with an OpenGL display engine: whatever the zoom, every line keeps at least one full pixel, so what you see is not an accurate picture of your drawing. With precise line drawings, that matters.
- The usual workaround is to print to PDF, then open the PDF in a raster program such as Photoshop to see the real lineweights. PixelView shows your layout that way directly in Rhino, as close to the printed sheet as a screen can get.
- The **1:1** button shows the drawing at its exact real-world size, as printed: the plugin detects your monitor's size and resolution and does the math.

**Requirements** : Windows, Rhino 8 <br>
**Actual Plugin Version** : 1.1.1 <br>
**License** : [GPL-3.0-or-later](LICENSE) — you may use, study, modify and share it; modified versions stay under the same license. Includes PDFium (BSD-3-Clause / Apache-2.0), see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Install
1. In Rhino: **Tools > Package Manager**, search **PixelView_CC**, click **Install**, then restart Rhino.
2. Optional toolbar button, with this macro:
   ```
   ! _PixelView
   ```
3. Run `PixelView` (or click the button): the **PixelView_CC** panel opens. Open a layout tab.

## Interface
- **Inputs** : the active layout tab (model viewports have no paper size).
- **Enable** : turns the pixel view on or off (green when on). It hides itself while a command runs or objects are selected.
- **Refresh** (icon) : prints the layout again; needed after zooming or panning inside a detail.
- **Auto** : refreshes by itself about 1 s after the document changes.
- **Info** : opens this page.
- **View — Fit · 1:1 · - · +** : zoom to the whole sheet, to real size, out and in; the label shows the real zoom (100% = real size).
- **Output — Color** : Display color, Print color or Black & white, as in Rhino's Print dialog.
- **Output — Raster ppi** : resolution of the raster, like a Photoshop document (600 by default, 150 to 1200).
- **Monitor (Screen)** : **Auto detect monitor size** or **Manual input PPI** (horizontal pixels / screen width in inches). Every connected monitor is listed; "(in use)" marks the one showing the layout.

## Contributing
Improvements are welcome. Start with [ARCHITECTURE.md](ARCHITECTURE.md), which explains how the C# project is organized and where each step lives. Version history: [CHANGELOG.md](CHANGELOG.md).

## What the plugin does (DETAILED)
- Prints the active layout to a vector PDF in memory with Rhino's own PDF engine, as Print > Rhino PDF does.
- Rasterises that PDF at a fixed resolution (Raster ppi, 600 by default) with PDFium, like a PDF placed in Photoshop.
- Draws the raster inside the layout tab, area-averaged to the zoom, so lines thinner than a pixel read as light grey.
- Shows the raster pixels themselves when you zoom in past the raster resolution.
- Reads each monitor's physical size from the monitor itself (EDID) for a true 1:1 zoom.
- Accepts a manual pixels-per-inch value when a monitor's size cannot be read.
- Zooms the layout with Fit, 1:1, - and +, and shows the real zoom in percent.
- Rasterises the sheet once and keeps it in memory, so zooming and panning only resample it.
- Lowers the raster resolution for very far zoom-outs to keep memory use reasonable.
- Gives back Rhino's normal display while a command runs, objects are selected or a detail is active.
- Refreshes automatically after the document changes (Auto), or on demand with Refresh.
- Offers the colour modes of Rhino's Print dialog, starting from Rhino's own default.
- Keeps every setting between Rhino sessions.
- Lives in a dockable panel built with Rhino's native controls, theme and font.
- Writes warnings and what to do about them in the command line.
