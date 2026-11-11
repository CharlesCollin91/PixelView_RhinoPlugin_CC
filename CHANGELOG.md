# Changelog

All notable changes to PixelView_CC are listed here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow the Rhino Package Manager (three numbers).

## [1.1.1] - 2026-11-11
First public release on GitHub and on the Rhino Package Manager.

### Changed
- Panel rebuilt like Rhino's own panels: native controls only (Rhino theme, font and size), title **PixelView_CC**,
  rounded **Enable** button (green when on), refresh icon and **Auto** on the first row, then the sections
  **View**, **Output** and **Monitor (Screen)**.
- Every connected monitor is listed in **Monitor (Screen)**, the one showing the layout marked "(in use)".
- **Info** link to this page.
- Warnings go to the command line instead of status lines in the panel.
- The sheet is rasterised once at the raster ppi and kept in memory (like a Photoshop document): zooming only
  resamples it, on all processor cores.

### Fixed
- The monitor size is read again only when it changes.

## [1.1.0] - 2026-11-10
### Added
- First version as a Rhino plug-in: command `PixelView`, dockable panel with every setting, pixel view drawn inside
  the active layout tab.
- The layout is printed to a vector PDF in memory with Rhino's PDF engine, rasterised at a fixed resolution
  (Raster ppi, 600 by default) with PDFium, then shown at the zoom by exact area averaging (nearest pixel past the
  raster resolution).
- True 1:1 zoom from the monitor's real size (EDID), or a manual pixels-per-inch value.
- Pixel view hidden while a command runs or objects are selected.
- Settings kept by Rhino between sessions.
