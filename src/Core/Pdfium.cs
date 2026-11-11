using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;

namespace PixelViewCC
{
    /// <summary>
    /// Rasterises Rhino's PDF exactly as Photoshop does when a PDF is placed in a document:
    /// 1. the page is rasterised at a fixed resolution (Raster ppi, 600 by default), anti-aliased;
    /// 2. that raster is shown on screen resampled to the zoom: area-averaged when zoomed out (a 0.05 mm
    ///    line becomes a light grey pixel), nearest pixel when zoomed past the raster resolution (pixels visible).
    /// PDFium (the PDF engine of Chrome; BSD-3-Clause / Apache-2.0) only does step 1.
    /// </summary>
    internal static class Pdfium
    {
        const string Dll = "pdfium";
        public const double PtPerMm = 72.0 / 25.4;
        const long MaxTargetPixels = 60L * 1000 * 1000;
        const long MaxRasterPixels = 150L * 1000 * 1000;   // above: the raster ppi is lowered for this zoom only

        [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Unicode)] static extern IntPtr LoadLibrary(string path);
        [DllImport(Dll)] static extern void FPDF_InitLibrary();
        [DllImport(Dll)] static extern IntPtr FPDF_LoadMemDocument(IntPtr data, int size, [MarshalAs(UnmanagedType.LPStr)] string password);
        [DllImport(Dll)] static extern void FPDF_CloseDocument(IntPtr doc);
        [DllImport(Dll)] static extern int FPDF_GetPageCount(IntPtr doc);
        [DllImport(Dll)] static extern IntPtr FPDF_LoadPage(IntPtr doc, int index);
        [DllImport(Dll)] static extern void FPDF_ClosePage(IntPtr page);
        [DllImport(Dll)] static extern double FPDF_GetPageWidth(IntPtr page);
        [DllImport(Dll)] static extern double FPDF_GetPageHeight(IntPtr page);
        [DllImport(Dll)] static extern IntPtr FPDFBitmap_Create(int width, int height, int alpha);
        [DllImport(Dll)] static extern int FPDFBitmap_FillRect(IntPtr bitmap, int left, int top, int width, int height, uint color);
        [DllImport(Dll)] static extern IntPtr FPDFBitmap_GetBuffer(IntPtr bitmap);
        [DllImport(Dll)] static extern int FPDFBitmap_GetStride(IntPtr bitmap);
        [DllImport(Dll)] static extern void FPDFBitmap_Destroy(IntPtr bitmap);
        [DllImport(Dll)] static extern void FPDF_RenderPageBitmap(IntPtr bitmap, IntPtr page, int startX, int startY, int sizeX, int sizeY, int rotate, int flags);
        [DllImport(Dll)] static extern uint FPDF_GetLastError();

        static bool _ready;

        /// <summary>Loads pdfium.dll from the plug-in folder. Returns null, or a user-facing error.</summary>
        public static string Load(string folder)
        {
            if (_ready) return null;
            string path = Path.Combine(folder ?? "", "pdfium.dll");
            if (!File.Exists(path))
                return $"pdfium.dll is missing next to PixelView_CC.rhp ({folder}).";
            if (LoadLibrary(path) == IntPtr.Zero)
                return $"pdfium.dll could not be loaded (Windows error {Marshal.GetLastWin32Error()}).";
            FPDF_InitLibrary();
            _ready = true;
            Log.Write("API", "PDFium loaded from " + path);
            return null;
        }

        /// <summary>
        /// A PDF kept in unmanaged memory with its first page loaded, and its raster cache: like a Photoshop
        /// document, the sheet is rasterised once at the raster ppi (in 512 x 512 tiles, made when first seen)
        /// and every zoom only resamples those pixels.
        /// </summary>
        public sealed class Document : IDisposable
        {
            const int Tile = 512;
            const long CacheBudgetBytes = 1L << 30;              // 1 GB of raster tiles, least recently used dropped first

            IntPtr _mem, _doc, _page;
            readonly double _wPt, _hPt;
            public double WidthMm { get; }
            public double HeightMm { get; }

            sealed class RasterTile { public byte[] Px; public int W, H; public LinkedListNode<long> Node; }
            readonly Dictionary<long, RasterTile> _tiles = new Dictionary<long, RasterTile>();
            readonly LinkedList<long> _lru = new LinkedList<long>();
            long _cacheBytes;
            public int TilesMade { get; private set; }

            public Document(byte[] pdf)
            {
                _mem = Marshal.AllocHGlobal(pdf.Length);
                Marshal.Copy(pdf, 0, _mem, pdf.Length);
                _doc = FPDF_LoadMemDocument(_mem, pdf.Length, null);
                if (_doc == IntPtr.Zero)
                {
                    uint err = FPDF_GetLastError();
                    Dispose();
                    throw new ViewException($"the PDF printed by Rhino could not be read (PDFium error {err}).");
                }
                if (FPDF_GetPageCount(_doc) < 1)
                {
                    Dispose();
                    throw new ViewException("the PDF printed by Rhino has no page.");
                }
                _page = FPDF_LoadPage(_doc, 0);
                _wPt = FPDF_GetPageWidth(_page);
                _hPt = FPDF_GetPageHeight(_page);
                WidthMm = _wPt / PtPerMm;
                HeightMm = _hPt / PtPerMm;
            }

            /// <summary>
            /// Screen image of the sheet-pixel rectangle (x0, y0, w, h) of the sheet shown at z screen pixels per
            /// paper mm, from the sheet rasterised at rasterPpi. Returns a 32 bpp bitmap of w x h pixels.
            /// </summary>
            public Bitmap Render(double z, int x0, int y0, int w, int h, double rasterPpi, out double usedPpi)
            {
                if ((long)w * h > MaxTargetPixels) throw new ViewException("zoomed area too large to render.");
                // Far zoomed out, the raster of the visible sheet can exceed the memory budget: lower the ppi for
                // this zoom only (the screen image is then still averaged over several raster pixels).
                double needed = (double)w * h * Math.Pow(rasterPpi / 25.4 / z, 2);
                usedPpi = needed > MaxRasterPixels ? rasterPpi * Math.Sqrt(MaxRasterPixels / needed) : rasterPpi;
                usedPpi = Math.Round(usedPpi, 1);
                double rz = usedPpi / 25.4;                            // raster pixels per paper mm
                double f = rz / z;                                     // raster pixels per screen pixel
                double sPt = rz / PtPerMm;                             // raster pixels per PDF point
                int fullW = (int)Math.Round(_wPt * sPt), fullH = (int)Math.Round(_hPt * sPt);
                int sx0 = Math.Max(0, (int)Math.Floor(x0 * f)), sy0 = Math.Max(0, (int)Math.Floor(y0 * f));
                int sx1 = Math.Min(fullW, (int)Math.Ceiling((x0 + w) * f)), sy1 = Math.Min(fullH, (int)Math.Ceiling((y0 + h) * f));
                if (sx1 <= sx0 || sy1 <= sy0) throw new ViewException("nothing of the sheet is visible.");
                var raster = new Raster(this, (int)Math.Round(usedPpi * 10), fullW, fullH);
                return f >= 1 ? Downsample(raster, f, x0, y0, w, h, sx0, sy0, sx1, sy1)
                              : Enlarge(raster, f, x0, y0, w, h, sx0, sy0, sx1, sy1);
            }

            /// <summary>The sheet at one raster resolution, read through the tile cache.</summary>
            sealed class Raster
            {
                readonly Document _d;
                readonly int _ppiKey;
                public readonly int FullW, FullH;
                public Raster(Document d, int ppiKey, int fullW, int fullH) { _d = d; _ppiKey = ppiKey; FullW = fullW; FullH = fullH; }

                public RasterTile Get(int tx, int ty) => _d.GetTile(_ppiKey, tx, ty, FullW, FullH);

                /// <summary>Copies raster row r, columns [sx0, sx1), into dst (BGRx). Tiles must be cached (main thread).</summary>
                public void Row(int r, int sx0, int sx1, byte[] dst)
                {
                    int ty = r / Tile, ry = r - ty * Tile;
                    for (int c = sx0; c < sx1;)
                    {
                        int tx = c / Tile, cx = c - tx * Tile;
                        RasterTile t = _d.Peek(_ppiKey, tx, ty);
                        int n = Math.Min(sx1 - c, t.W - cx);
                        Buffer.BlockCopy(t.Px, (ry * t.W + cx) * 4, dst, (c - sx0) * 4, n * 4);
                        c += n;
                    }
                }
            }

            static long Key(int ppiKey, int tx, int ty) => ((long)ppiKey << 40) | ((long)tx << 20) | (uint)ty;

            RasterTile Peek(int ppiKey, int tx, int ty) => _tiles[Key(ppiKey, tx, ty)];

            RasterTile GetTile(int ppiKey, int tx, int ty, int fullW, int fullH)
            {
                long key = Key(ppiKey, tx, ty);
                if (_tiles.TryGetValue(key, out RasterTile t))
                {
                    _lru.Remove(t.Node);
                    _lru.AddFirst(t.Node);
                    return t;
                }
                int tw = Math.Min(Tile, fullW - tx * Tile), th = Math.Min(Tile, fullH - ty * Tile);
                IntPtr bmp = FPDFBitmap_Create(tw, th, 0);
                if (bmp == IntPtr.Zero) throw new ViewException("not enough memory to rasterise: lower Raster ppi.");
                var px = new byte[tw * th * 4];
                try
                {
                    FPDFBitmap_FillRect(bmp, 0, 0, tw, th, 0xFFFFFFFF);
                    FPDF_RenderPageBitmap(bmp, _page, -tx * Tile, -ty * Tile, fullW, fullH, 0, 0);
                    int stride = FPDFBitmap_GetStride(bmp);
                    IntPtr buf = FPDFBitmap_GetBuffer(bmp);
                    for (int y = 0; y < th; y++) Marshal.Copy(buf + y * stride, px, y * tw * 4, tw * 4);
                }
                finally { FPDFBitmap_Destroy(bmp); }
                t = new RasterTile { Px = px, W = tw, H = th, Node = new LinkedListNode<long>(key) };
                _tiles[key] = t;
                _lru.AddFirst(t.Node);
                _cacheBytes += px.Length;
                TilesMade++;
                while (_cacheBytes > CacheBudgetBytes && _lru.Count > 1)
                {
                    long old = _lru.Last.Value;
                    _lru.RemoveLast();
                    _cacheBytes -= _tiles[old].Px.Length;
                    _tiles.Remove(old);
                }
                return t;
            }

            /// <summary>Area average (box filter with exact fractional coverage), like viewing a placed PDF zoomed out.</summary>
            static Bitmap Downsample(Raster raster, double f, int x0, int y0, int w, int h, int sx0, int sy0, int sx1, int sy1)
            {
                int sw = sx1 - sx0;
                // column weights: target column x covers raster [(x0 + x) * f, (x0 + x + 1) * f)
                var colFirst = new int[w];
                var colCount = new int[w];
                var colStart = new int[w];
                var colSum = new float[w];
                var weights = new List<float>(w * ((int)Math.Ceiling(f) + 2));
                for (int x = 0; x < w; x++)
                {
                    double a = (x0 + x) * f, b = a + f;
                    int s0 = Math.Max(sx0, (int)Math.Floor(a)), s1 = Math.Min(sx1, (int)Math.Ceiling(b));
                    colFirst[x] = s0 - sx0;
                    colStart[x] = weights.Count;
                    colCount[x] = Math.Max(0, s1 - s0);
                    for (int s = s0; s < s1; s++)
                    {
                        float wt = (float)Math.Max(0, Math.Min(b, s + 1) - Math.Max(a, s));
                        weights.Add(wt);
                        colSum[x] += wt;
                    }
                }
                float[] cw = weights.ToArray();
                var rowSum = new float[h];
                var acc = new float[(long)w * h * 3];
                int ty0 = sy0 / Tile, ty1 = (sy1 - 1) / Tile, tx0 = sx0 / Tile, tx1 = (sx1 - 1) / Tile;
                for (int ty = ty0; ty <= ty1; ty++)
                {
                    for (int tx = tx0; tx <= tx1; tx++) raster.Get(tx, ty);            // PDFium on this thread only
                    int r0 = Math.Max(sy0, ty * Tile), r1 = Math.Min(sy1, (ty + 1) * Tile);
                    int rows = r1 - r0;
                    var rowOut = new float[rows][];
                    Parallel.For(0, rows, () => new byte[sw * 4], (i, state, src) =>     // horizontal pass, one raster row each
                    {
                        raster.Row(r0 + i, sx0, sx1, src);
                        var o = new float[w * 3];
                        for (int x = 0; x < w; x++)
                        {
                            float b = 0, g = 0, rr = 0;
                            int k = colStart[x], p = colFirst[x] * 4;
                            for (int n = 0; n < colCount[x]; n++, k++, p += 4)
                            {
                                float wt = cw[k];
                                b += wt * src[p]; g += wt * src[p + 1]; rr += wt * src[p + 2];
                            }
                            o[x * 3] = b; o[x * 3 + 1] = g; o[x * 3 + 2] = rr;
                        }
                        rowOut[i] = o;
                        return src;
                    }, _ => { });
                    for (int i = 0; i < rows; i++)                                          // vertical pass
                    {
                        int r = r0 + i;
                        int yA = Math.Max(0, (int)Math.Floor(r / f) - y0), yB = Math.Min(h - 1, (int)Math.Floor((r + 1) / f) - y0);
                        float[] o = rowOut[i];
                        for (int y = yA; y <= yB; y++)
                        {
                            double a = (y0 + y) * f;
                            float wy = (float)Math.Max(0, Math.Min(a + f, r + 1) - Math.Max(a, r));
                            if (wy <= 0) continue;
                            rowSum[y] += wy;
                            long baseIdx = (long)y * w * 3;
                            for (int j = 0; j < w * 3; j++) acc[baseIdx + j] += wy * o[j];
                        }
                    }
                }
                // Parts of a screen pixel outside the sheet (last row / column) count as white paper.
                float area = (float)(f * f), norm = 1f / area;
                var result = new Bitmap(w, h, PixelFormat.Format32bppArgb);
                var data = result.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb);
                try
                {
                    var outRow = new byte[w * 4];
                    for (int y = 0; y < h; y++)
                    {
                        long o = (long)y * w * 3;
                        for (int x = 0; x < w; x++)
                        {
                            float white = 255f * Math.Max(0f, area - colSum[x] * rowSum[y]);
                            outRow[x * 4] = Clamp((acc[o + x * 3] + white) * norm);
                            outRow[x * 4 + 1] = Clamp((acc[o + x * 3 + 1] + white) * norm);
                            outRow[x * 4 + 2] = Clamp((acc[o + x * 3 + 2] + white) * norm);
                            outRow[x * 4 + 3] = 255;
                        }
                        Marshal.Copy(outRow, 0, data.Scan0 + y * data.Stride, w * 4);
                    }
                }
                finally { result.UnlockBits(data); }
                return result;
            }

            /// <summary>Zoomed past the raster resolution: each raster pixel shown as a square, like Photoshop.</summary>
            static Bitmap Enlarge(Raster raster, double f, int x0, int y0, int w, int h, int sx0, int sy0, int sx1, int sy1)
            {
                int sw = sx1 - sx0;
                for (int ty = sy0 / Tile; ty <= (sy1 - 1) / Tile; ty++)
                    for (int tx = sx0 / Tile; tx <= (sx1 - 1) / Tile; tx++) raster.Get(tx, ty);
                var colSrc = new int[w];
                for (int x = 0; x < w; x++) colSrc[x] = Math.Min(sw - 1, Math.Max(0, (int)Math.Floor((x0 + x + 0.5) * f) - sx0));
                var result = new Bitmap(w, h, PixelFormat.Format32bppArgb);
                var data = result.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb);
                try
                {
                    var src = new byte[sw * 4];
                    var outRow = new byte[w * 4];
                    int lastRow = -1;
                    for (int y = 0; y < h; y++)
                    {
                        int r = Math.Min(sy1 - 1, Math.Max(sy0, (int)Math.Floor((y0 + y + 0.5) * f)));
                        if (r != lastRow) { raster.Row(r, sx0, sx1, src); lastRow = r; }
                        for (int x = 0; x < w; x++)
                        {
                            int p = colSrc[x] * 4;
                            outRow[x * 4] = src[p]; outRow[x * 4 + 1] = src[p + 1]; outRow[x * 4 + 2] = src[p + 2]; outRow[x * 4 + 3] = 255;
                        }
                        Marshal.Copy(outRow, 0, data.Scan0 + y * data.Stride, w * 4);
                    }
                }
                finally { result.UnlockBits(data); }
                return result;
            }

            static byte Clamp(float v) => (byte)(v <= 0 ? 0 : v >= 255 ? 255 : (int)(v + 0.5f));

            public void Dispose()
            {
                _tiles.Clear();
                _lru.Clear();
                _cacheBytes = 0;
                if (_page != IntPtr.Zero) { FPDF_ClosePage(_page); _page = IntPtr.Zero; }
                if (_doc != IntPtr.Zero) { FPDF_CloseDocument(_doc); _doc = IntPtr.Zero; }
                if (_mem != IntPtr.Zero) { Marshal.FreeHGlobal(_mem); _mem = IntPtr.Zero; }
            }
        }
    }

    /// <summary>Something the user must know; the message is user-facing (English).</summary>
    internal class ViewException : Exception
    {
        public ViewException(string message) : base(message) { }
    }
}
