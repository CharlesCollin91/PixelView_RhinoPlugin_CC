# Architecture — PixelView_CC

PixelView_CC is a RhinoCommon plug-in written in C# (`.rhp`), shipped as a Rhino Package Manager package (`.yak`).
This page explains the design decisions, how the project is laid out and where each step lives.

## Design decisions

- **Rasterise like Photoshop, not like a vector viewer.** Vector viewers draw every line at least one full pixel
  wide, so thin lineweights all look the same. PixelView does what Photoshop does with a placed PDF: rasterise the
  page once at a fixed resolution (Raster ppi, 600 by default), then resample that raster to the zoom. Zoomed out,
  pixels are **area-averaged**, so a line thinner than a screen pixel becomes a light grey pixel. Zoomed in past
  the raster resolution, raster pixels are enlarged (nearest pixel).
- **Rhino prints the PDF itself.** The layout goes through Rhino's own PDF engine (`ViewCaptureSettings` +
  `FilePdf`), in memory, so what you see is what Print > Rhino PDF would produce: lineweights, print colours,
  hatches, shaded details.
- **PDFium rasterises.** PDFium (the PDF engine of Chrome, BSD-3-Clause / Apache-2.0) is called through P/Invoke;
  `pdfium.dll` (x64) is shipped next to the `.rhp`. It only does the rasterisation; the resampling to the zoom is
  done by the plug-in, so the method stays identical whatever the rasteriser.
- **True 1:1 from the monitor itself.** The physical size of the monitor is read from its EDID (Windows registry,
  read only), so 1:1 means real millimetres on that screen. A manual pixels-per-inch value covers monitors without
  a usable EDID.
- **Drawn inside the layout, not in a separate window.** A `DisplayConduit` draws the image over the layout in
  `DrawForeground`. Whenever Rhino's own display matters (a command running, objects selected, a detail active,
  the view still moving), the conduit draws nothing and Rhino's normal display shows.
- **Everything on Rhino's UI thread.** Rhino's API is not thread-safe, so the work is driven by `RhinoApp.Idle`
  (about every 100 ms); only the pixel resampling runs in parallel (`Parallel.For`).
- **One target framework: `net48`.** A `net48` plug-in loads in Rhino 8 under both runtimes (.NET Core and
  .NET Framework). It is built against the oldest Rhino 8 SDK, so it loads in every Rhino 8 service release.
- **Native Rhino look.** The panel uses Rhino's native Eto controls with no font or colour set in code. The two
  drawn controls (Enable, refresh icon) take their colours and font from a native button at paint time.
- **Settings and logs in the user's folders.** Settings are kept by Rhino (`PlugIn.Settings`); logs go to the
  plug-in's settings folder. Nothing is written in the package folder, which is replaced at every update.

## Project layout

```
src/
  PixelView_CC.csproj        net48, x64, output .rhp to ..\dist\, copies pdfium.dll next to it
  PixelViewPlugIn.cs         plug-in entry: loads PDFium, registers the panel, starts the Controller
  Commands.cs                PixelView (opens the panel, turns the view on), PixelViewLogFolder (hidden)
  Core/
    LayoutPdf.cs             prints one layout to a vector PDF in memory (Rhino's PDF engine)
    Pdfium.cs                PDFium binding, raster tiles and cache, resampling to the zoom
    ScreenInfo.cs            monitors and their physical size (EDID)
    Log.cs                   one log file per Rhino session
  Display/
    Controller.cs            state, settings, idle loop, view geometry, zoom, and the display conduit
  UI/
    PixelViewPanel.cs        the dockable PixelView_CC panel
  Resources/PixelView.ico    panel icon (embedded)
build.cmd                    compiles the .rhp into dist\ (writes logs\build_<date>.log)
pack.cmd                     makes the .yak package from dist\ with Rhino's Yak.exe
publish.cmd                  sends the .yak to McNeel's test server, then to the public Package Manager
manifest.yml                 package description for the Package Manager
nuget.config                 package source for the build (nuget.org only)
```

## Pipeline

```
Enable / Refresh / Auto / layout or setting changed
        │
        ▼
Controller.Tick ──► Controller.BuildPdf ──► LayoutPdf.Print ──► PDF bytes (memory)
        │                                                          │
        │                                                          ▼
        │                                             new Pdfium.Document(bytes)
        ▼
view settled (0.15 s) ──► Controller.TryMapping ──► Controller.Visible ──► Controller.Render
                                                                              │
                                                                              ▼
                                         Pdfium.Document.Render(zoom, visible rect, Raster ppi)
                                            raster tiles 512 x 512 at Raster ppi (made once, cached)
                                            ──► Downsample (area average) or Enlarge (nearest pixel)
                                                                              │
                                                                              ▼
                                                     Tile (DisplayBitmap) = Controller.Current
                                                                              │
                                                                              ▼
                       PixelConduit.DrawForeground ──► Controller.TileFor ──► DrawBitmap at 1:1 pixels
```

- `Controller.Tick` rebuilds the PDF when the key (document, layout, colour, raster ppi) changes, on Refresh, or,
  with Auto, 1.2 s after the last document change. A failed key is kept, so an error is not retried in a loop.
- `Controller.TryMapping` maps the sheet (millimetres) to the viewport's client pixels with
  `WorldToClient`; `Controller.Visible` keeps only the part of the sheet on screen.
- `Pdfium.Document.Render` rasterises only the 512 x 512 tiles it needs, keeps them in a least-recently-used cache
  (1 GB), and lowers the raster resolution for a far zoom-out when the whole raster would exceed 150 million pixels.
- `Controller.TileFor` returns nothing while a command runs, objects are selected, a detail is active, the zoom
  has changed since the last render, or Rhino is printing.

## Data objects

| Object | File | Holds |
|---|---|---|
| `ColorChoice` | `Core/LayoutPdf.cs` | Display color, Print color, Black & white (as in Rhino's Print dialog) |
| `Pdfium.Document` | `Core/Pdfium.cs` | the PDF in unmanaged memory, its first page, its raster tile cache |
| `ScreenInfo` | `Core/ScreenInfo.cs` | one monitor: device, number, pixels, width in mm, ppi, or the reason its size is unknown |
| `Mapping` | `Display/Controller.cs` | zoom (screen pixels per sheet mm), sheet origin on screen, viewport size and id |
| `Tile` | `Display/Controller.cs` | the rendered image, its zoom and pixel rectangle, the viewport it belongs to |

## Zoom

`Controller.ZoomFit` frames the whole sheet (`ZoomBoundingBox`). `Controller.ZoomReal` (1:1) and
`Controller.ZoomStep` (- and +) go through `Controller.ZoomTo`, which magnifies the layout about the view centre
(`Magnify`, corrected up to three times) until the sheet has exactly the requested number of screen pixels per
millimetre, using the monitor in use (`Controller.UpdateScreen`). The real zoom shown in the panel is screen
millimetres per sheet millimetre (100% = real size).

## Messages and logs

- Warnings and errors go to Rhino's command line, prefixed `PixelView_CC:` (`Controller.Say`), each with what to do.
- Log: one file per Rhino session in the plug-in's settings folder, under `logs`; the 30 most recent are kept.
  The hidden command `PixelViewLogFolder` opens that folder.
- Log tags: `START`, `SETTING`, `RUN`, `API`, `DONE`, `VIEW`, `SCREEN`, `INFO`, `WARN`, `SKIP`, `ERROR`, `CLOSE`.

## Build and package

1. Install the **.NET 8 SDK** (x64, free) once.
2. `build.cmd`: compiles `dist\PixelView_CC.rhp` and copies `dist\pdfium.dll` (NuGet packages are downloaded the
   first time). Close Rhino first if it has the `.rhp` loaded: the file is locked while Rhino runs.
3. Test: drag `dist\PixelView_CC.rhp` into Rhino, restart Rhino.
4. `pack.cmd`: builds the `.yak` package into `dist\` with Rhino 8's `Yak.exe`.
5. `publish.cmd`: sign in (`yak login`), send to McNeel's test server, check the install from there, then publish.

The version is set in `src/PixelView_CC.csproj` (`Version`, `AssemblyVersion`, `FileVersion`) and in
`manifest.yml`. A version published on the Package Manager can never be replaced: every change gets a new number.

## Where to start for…

| You want to… | Start in |
|---|---|
| change how the layout is printed (colours, raster mode) | `LayoutPdf.Print` |
| change the rasterisation or the resampling | `Pdfium.Document.Render`, `Downsample`, `Enlarge` |
| change when the PDF is rebuilt or the view redrawn | `Controller.Tick`, `Controller.BuildPdf` |
| change when the pixel view is hidden | `Controller.TileFor` |
| change the zoom buttons | `Controller.ZoomTo`, `Controller.CheckZoomTarget` |
| fix the monitor size detection | `ScreenInfo.FromMonitor`, `ScreenInfo.ReadEdid`, `ScreenInfo.Parse` |
| add a setting to the panel | `PixelViewPanel` (constructor and `OnState`), then a `Controller.Set…` method |
| change the panel's look | `PixelViewPanel.Theme`, `PillButton`, `IconButton` |
