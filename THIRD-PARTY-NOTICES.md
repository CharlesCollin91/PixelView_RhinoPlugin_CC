# Third-party components

PixelView_CC is licensed under the GNU General Public License v3.0 or later (see the LICENSE file).
It ships the following component, which keeps its own license:

| Component | Use | License | Source |
|---|---|---|---|
| PDFium (`pdfium.dll`, x64) | rasterises the PDF that Rhino prints of the layout (600 ppi by default) | BSD-3-Clause and Apache-2.0 | https://pdfium.googlesource.com/pdfium/ — binaries: https://github.com/bblanchon/pdfium-binaries |

The Python prototype (`python/PixelView_CC.py`, not part of the plug-in) uses PyMuPDF (AGPL-3.0,
https://github.com/pymupdf/PyMuPDF), which it downloads on first launch, after asking; it is not included here.
