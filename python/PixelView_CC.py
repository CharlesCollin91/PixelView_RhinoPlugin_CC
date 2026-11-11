#! python 3
# PixelView_CC - Pixel viewer for Rhino layouts: shows lineweights as they really look (like Photoshop),
#                at true 1:1 size on screen.
# Requires: Rhino 8+ (Windows; Mac not tested), Python 3 (built into Rhino), PyMuPDF (installed on first launch,
#           after asking, inside the plugin folder).
# Run: ! _-RunPythonScript "C:\path\to\PixelView_CC\PixelView_CC.py"
# Copyright (C) 2026 Charles Collin. Licensed under GPL-3.0-or-later (see LICENSE).
#
# How it works: the chosen layout is printed to a VECTOR PDF in memory (same engine as Rhino PDF printing),
# then rasterised with PyMuPDF (anti-aliased, coverage-based: a 0.1 mm line becomes a light grey pixel,
# like Photoshop) at exactly the screen resolution of the current zoom. "1:1" uses the physical size
# of the monitor the window is on (Windows: read from the monitor EDID; otherwise typed once by the user),
# so 10 mm on paper = 10 mm on screen.
# Nothing in the document is changed.
__version__ = "1.1"
PLUGIN_NAME = "PixelView_CC"

# =============================================================================
# ENVIRONMENT GUARD - runs first, before any import that needs Rhino 8 / Python 3.
# Stops with a clear message instead of an unknown error. Keep it simple: no f-strings.
# =============================================================================
import os
import sys

MIN_PYTHON = (3, 9)
MIN_RHINO = 8


def _env_problem():
    """Return None if the environment is fine, else a short English explanation."""
    if sys.version_info[:2] < MIN_PYTHON:
        return ("Python %d.%d found, Python %d.%d+ needed.\n"
                "Line 1 of the file must be '#! python 3', and Rhino must be 8 or newer."
                % (sys.version_info[0], sys.version_info[1], MIN_PYTHON[0], MIN_PYTHON[1]))
    try:
        import Rhino
    except ImportError:
        return ("Rhino was not found. Run this file inside Rhino 8+ "
                "(toolbar button or ScriptEditor), not in another Python.")
    if Rhino.RhinoApp.ExeVersion < MIN_RHINO:
        return "Rhino %s found, Rhino %d+ needed." % (Rhino.RhinoApp.Version, MIN_RHINO)
    for module in ("clr", "scriptcontext", "Eto.Forms", "Eto.Drawing"):
        try:
            __import__(module)
        except ImportError as e:
            return "Module '%s' is missing in this Rhino Python (%s)." % (module, e)
    return None


def _env_report(problem):
    msg = ("%s v%s cannot start.\n\n%s\n\n"
           "Requires: Rhino 8+ for Windows, Python 3 (built into Rhino 8)."
           % (PLUGIN_NAME, __version__, problem))
    try:  # leave a trace for debugging, next to the normal logs
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        import time
        with open(os.path.join(log_dir, "ENV_ERROR.log"), "a") as f:
            f.write("%s | ENV | %s | python %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                     problem.replace("\n", " "), sys.version.split()[0]))
    except Exception:
        pass
    try:
        import Rhino
        Rhino.RhinoApp.WriteLine(PLUGIN_NAME + ": " + problem.replace("\n", " "))
        Rhino.UI.Dialogs.ShowMessage(msg, PLUGIN_NAME + " - environment")
    except Exception:
        print(msg)


_ENV_PROBLEM = _env_problem()
if _ENV_PROBLEM:
    _env_report(_ENV_PROBLEM)
    raise SystemExit(0)

# =============================================================================
# HEADER / IMPORTS
# =============================================================================
import json
import math
import time
import base64
import ctypes
import logging
import datetime
import importlib
import textwrap
import traceback
import tempfile
import subprocess

IS_WINDOWS = sys.platform.startswith("win")
if IS_WINDOWS:
    import winreg
    from ctypes import wintypes
else:
    wintypes = None                     # monitor size detection is Windows only

import clr
import System
import Rhino
import scriptcontext as sc
import Eto.Forms as forms
import Eto.Drawing as drawing

# =============================================================================
# CONFIG
# =============================================================================
STICKY_KEY = PLUGIN_NAME + ".ui"
DEFAULT_PLUGIN_DIR = os.path.join(tempfile.gettempdir(), PLUGIN_NAME)   # only if __file__ is undefined
LOG_LIMIT_BYTES = 500 * 1024 * 1024
TOOLTIP_WIDTH = 60

DEFAULT_CAPTURE_DPI = 300.0        # resolution Rhino uses for shaded / rendered details in the PDF
ZOOM_MIN, ZOOM_MAX = 0.05, 64.0    # real zoom, 1.0 = 1:1
WHEEL_STEP = 1.25                  # zoom factor per mouse wheel notch
RENDER_DELAY_S = 0.15              # view settled for this long -> sharp render
AUTO_QUIET_S = 1.2                 # document quiet for this long -> automatic refresh
MAX_TILE_PIXELS = 60 * 1000 * 1000 # safety limit for one rendered image
FALLBACK_PPI = 96.0
PYMUPDF_PACKAGE = "pymupdf"
COLOR_MODES = [("Display color", "DisplayColor"), ("Print color", "PrintColor"),
               ("Black & white", "BlackAndWhite")]


def _plugin_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__)), "__file__"
    except Exception:
        return DEFAULT_PLUGIN_DIR, "default path (__file__ undefined)"


def _is_writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def _data_dir(plugin_dir):
    """Where the plugin writes (logs, lib, settings): its own folder, or a per-user folder if read-only."""
    if _is_writable(plugin_dir):
        return plugin_dir, "plugin folder"
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    alt = os.path.join(base, PLUGIN_NAME)
    if _is_writable(alt):
        return alt, "user folder (plugin folder is read-only)"
    return os.path.join(tempfile.gettempdir(), PLUGIN_NAME), "temp folder (plugin and user folders read-only)"


PLUGIN_DIR, PLUGIN_DIR_SOURCE = _plugin_dir()
DATA_DIR, DATA_DIR_SOURCE = _data_dir(PLUGIN_DIR)
LOG_DIR = os.path.join(DATA_DIR, "logs")
# PyMuPDF is compiled per Python version (Rhino 8 = 3.9, Rhino 9 = 3.13): one lib folder per version.
LIB_DIR = os.path.join(DATA_DIR, "lib", "py%d%d" % sys.version_info[:2])
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# =============================================================================
# LOGGING
# =============================================================================
_logger = logging.getLogger(PLUGIN_NAME)
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_log_path = None


def _trim_log_dir(current_path):
    try:
        files = []
        for name in os.listdir(LOG_DIR):
            p = os.path.join(LOG_DIR, name)
            if os.path.isfile(p) and name.lower().endswith(".log"):
                files.append((os.path.getmtime(p), p, os.path.getsize(p)))
        total = sum(f[2] for f in files)
        files.sort()
        for _, p, size in files:
            if total <= LOG_LIMIT_BYTES:
                break
            if current_path and os.path.normcase(p) == os.path.normcase(current_path):
                continue
            try:
                os.remove(p)
                total -= size
            except Exception:
                pass
    except Exception:
        pass


def start_log_session():
    """One log file per window session. Never blocks startup."""
    global _log_path
    for h in list(_logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        _logger.removeHandler(h)
    _log_path = None
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(LOG_DIR, f"{PLUGIN_NAME}_{stamp}.log")
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d | %(message)s", "%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(handler)
        _log_path = path
        _trim_log_dir(path)
    except Exception:
        _log_path = None
    return _log_path


def stop_log_session():
    for h in list(_logger.handlers):
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        _logger.removeHandler(h)


def log(tag, msg):
    try:
        _logger.info(f"{tag:<6} | {msg}")
    except Exception:
        pass


def log_exc(where):
    log("ERROR", f"{where}: exception\n{traceback.format_exc()}")


# =============================================================================
# THEME
# =============================================================================
TOKENS = {
    "bg_window": "#2B2D30",
    "bg_panel": "#1E1F22",
    "bg_hover": "#35373B",
    "border": "#393B40",
    "text": "#DFE1E5",
    "text_muted": "#9DA0A8",
    "text_disabled": "#5A5D63",
    "accent": "#7C5CD6",
    "accent_hover": "#8E71DE",
    "help_icon": "#3D8BFD",
    "success": "#4E9A51",
    "warning": "#C8912F",
    "error": "#D9534F",
    "canvas": "#3A3B3E",           # pasteboard around the sheet (neutral grey, like Photoshop)
    "paper": "#FFFFFF",
    "paper_shadow": "#232427",
    "font": ("Segoe UI", 9.0),
    "font_mono": ("Consolas", 9.0),
    "font_zoom": ("Segoe UI", 10.5),
    "space": 8,
    "pad_window": 14,
    "control_height": 28,
}


def color(token):
    h = TOKENS[token].lstrip("#")
    return drawing.Color.FromArgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def font(token="font", bold=False):
    fam, size = TOKENS[token]
    if bold:
        return drawing.Font(fam, size, drawing.FontStyle.Bold)
    return drawing.Font(fam, size)


def wrap_tip(text):
    return "\n".join(textwrap.fill(p, TOOLTIP_WIDTH) for p in text.split("\n"))


# =============================================================================
# CORE  (pure logic, no UI)
# =============================================================================
class ViewError(Exception):
    """Something the user must know about; message is the user-facing reason."""


# ---- settings file (per machine: screen choice) ------------------------------------
def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        log("SETTING", f"settings.json read: {data}")
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        log("SETTING", "settings.json absent: defaults used")
    except Exception:
        log_exc("read settings.json (defaults used)")
    return {}


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log("SETTING", f"settings.json written: {data}")
    except Exception:
        log_exc("write settings.json")


# ---- PyMuPDF (PDF rasteriser) ----------------------------------------------------
_fitz = None


def _import_fitz():
    """Import PyMuPDF from the plugin lib folder first. Returns the module or None."""
    global _fitz
    if _fitz is not None:
        return _fitz
    if os.path.isdir(LIB_DIR) and LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    importlib.invalidate_caches()
    for name in ("pymupdf", "fitz"):
        try:
            mod = importlib.import_module(name)
            if hasattr(mod, "open"):
                _fitz = mod
                return mod
        except Exception:
            log("API", f"import {name} failed: {sys.exc_info()[1]}")
    return None


def _rhino_python_exe():
    """Python executable of Rhino's own CPython (same version/ABI as the running engine)."""
    names = ["python.exe"] if IS_WINDOWS else ["bin/python3", "bin/python"]
    roots = [sys.prefix, os.path.dirname(sys.executable or "")]
    tag = "py%d%d-" % sys.version_info[:2]                       # e.g. ~/.rhinocode/py39-rh8
    rc = os.path.join(os.path.expanduser("~"), ".rhinocode")
    try:
        roots += [os.path.join(rc, d) for d in sorted(os.listdir(rc)) if d.startswith(tag)]
    except Exception:
        pass
    for root in roots:
        for n in names:
            c = os.path.join(root, n)
            if root and os.path.isfile(c):
                return c
    return None


def install_pymupdf():
    """pip install PyMuPDF into <plugin>\\lib. Keeps Rhino responsive. Returns (ok, message)."""
    py = _rhino_python_exe()
    if not py:
        return False, "Rhino's Python executable not found (looked in sys.prefix and ~/.rhinocode)."
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = os.path.join(LOG_DIR, f"PIP_INSTALL_{stamp}.log")
    args = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--upgrade", "--target", LIB_DIR, PYMUPDF_PACKAGE]
    log("API", f"pip: {' '.join(args)} -> {out_path}")
    t0 = time.time()
    with open(out_path, "w", encoding="utf-8") as out:
        extra = {"creationflags": 0x08000000} if IS_WINDOWS else {}  # CREATE_NO_WINDOW
        p = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **extra)
        while p.poll() is None:
            try:
                Rhino.RhinoApp.Wait()
            except Exception:
                pass
            time.sleep(0.1)
            if time.time() - t0 > 900:
                p.kill()
                return False, f"pip took more than 15 min, stopped. See {out_path}"
    log("API", f"pip exit code {p.returncode} in {time.time() - t0:.1f} s")
    if p.returncode != 0:
        return False, f"pip failed (code {p.returncode}). See {out_path}"
    if _import_fitz() is None:
        return False, f"PyMuPDF installed but cannot be imported. Restart Rhino and try again. See {out_path}"
    return True, f"PyMuPDF {getattr(_fitz, 'VersionBind', '?')} installed in {LIB_DIR}"


def ensure_pymupdf():
    """True when PyMuPDF can be used; asks before installing it."""
    if _import_fitz() is not None:
        return True
    q = ("PixelView_CC needs PyMuPDF, a free PDF renderer (about 25 MB, one-time download from the internet).\n\n"
         "It is installed inside the plugin folder only:\n" + LIB_DIR + "\n\n"
         "Install it now? Rhino may be slow for up to a minute.")
    try:
        res = Rhino.UI.Dialogs.ShowMessage(q, PLUGIN_NAME + " - first launch", Rhino.UI.ShowMessageButton.YesNo,
                                           Rhino.UI.ShowMessageIcon.Question)
    except Exception:
        log_exc("Rhino ShowMessage YesNo: Eto MessageBox used")
        res = forms.MessageBox.Show(q, PLUGIN_NAME + " - first launch", forms.MessageBoxButtons.YesNo,
                                    forms.MessageBoxType.Question)
    log("START", f"install PyMuPDF asked -> {res}")
    if str(res) != "Yes":
        Rhino.RhinoApp.WriteLine(f"{PLUGIN_NAME}: PyMuPDF not installed, viewer not opened.")
        return False
    Rhino.RhinoApp.WriteLine(f"{PLUGIN_NAME}: installing PyMuPDF, please wait...")
    try:
        Rhino.UI.StatusBar.SetMessagePane(f"{PLUGIN_NAME}: installing PyMuPDF...")
    except Exception:
        pass
    ok, msg = install_pymupdf()
    log("START" if ok else "ERROR", msg)
    Rhino.RhinoApp.WriteLine(f"{PLUGIN_NAME}: {msg}")
    try:
        Rhino.UI.StatusBar.ClearMessagePane()
    except Exception:
        pass
    if not ok:
        Rhino.UI.Dialogs.ShowMessage(
            msg + "\n\nManual install (command prompt / terminal):\n\"" + str(_rhino_python_exe()) +
            "\" -m pip install --target \"" + LIB_DIR + "\" pymupdf", PLUGIN_NAME + " - install failed")
    return ok


# ---- screen: physical size of the monitor the window is on ----------------------
class ScreenInfo:
    """ppi = physical pixels per inch of the monitor; scale = physical px per Eto logical px."""

    def __init__(self, ppi, scale, name, source, width_mm=0, px_w=0, px_h=0, device=""):
        self.ppi, self.scale, self.name, self.source = ppi, scale, name, source
        self.width_mm, self.px_w, self.px_h, self.device = width_mm, px_w, px_h, device

    @property
    def ppmm(self):
        return self.ppi / 25.4

    def key(self):
        return (self.device, round(self.ppi, 3), round(self.scale, 4))

    def describe(self):
        size = f"{self.width_mm:g} mm wide, " if self.width_mm else ""
        return f"{self.name} | {self.px_w}x{self.px_h} px | {size}{self.ppi:.2f} ppi | scale {self.scale * 100:.0f}%"


if IS_WINDOWS:
    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


_user32 = None
if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _user32.MonitorFromWindow.restype = ctypes.c_void_p
    _user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_EDID_CACHE = {}


def parse_edid(edid):
    """Bytes -> dict(name, width_mm, height_mm, native_w, native_h). Size from the first detailed
    timing descriptor (1 mm precision), else from bytes 21-22 (1 cm precision)."""
    edid = bytes(edid)
    if len(edid) < 128 or edid[0:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        raise ValueError("not an EDID block")
    info = {"name": "", "width_mm": 0.0, "height_mm": 0.0, "native_w": 0, "native_h": 0, "size_src": ""}
    for off in (54, 72, 90, 108):
        d = edid[off:off + 18]
        if d[0] == 0 and d[1] == 0:                      # display descriptor
            if d[3] == 0xFC:
                info["name"] = d[5:18].split(b"\x0a")[0].decode("ascii", "replace").strip()
        elif off == 54 or not info["native_w"]:          # detailed timing descriptor
            hw = d[2] | ((d[4] & 0xF0) << 4)
            vh = d[5] | ((d[7] & 0xF0) << 4)
            wmm = d[12] | ((d[14] & 0xF0) << 4)
            hmm = d[13] | ((d[14] & 0x0F) << 8)
            if hw and vh and not info["native_w"]:
                info["native_w"], info["native_h"] = hw, vh
                if wmm > 0 and hmm > 0:
                    info["width_mm"], info["height_mm"], info["size_src"] = float(wmm), float(hmm), "EDID DTD (1 mm)"
    if not info["width_mm"] and edid[21] and edid[22]:
        info["width_mm"], info["height_mm"], info["size_src"] = edid[21] * 10.0, edid[22] * 10.0, "EDID header (1 cm)"
    if not info["name"]:
        mid = (edid[8] << 8) | edid[9]
        maker = "".join(chr(((mid >> s) & 0x1F) + 64) for s in (10, 5, 0))
        info["name"] = f"{maker}{edid[11]:02X}{edid[10]:02X}"
    return info


def _edid_for_device_id(device_id):
    """'\\\\?\\DISPLAY#ACI32A4#5&...#{guid}' -> parsed EDID from the registry (read only)."""
    if device_id in _EDID_CACHE:
        return _EDID_CACHE[device_id]
    parts = device_id.split("#")
    if len(parts) < 3:
        raise ValueError(f"unexpected monitor id '{device_id}'")
    key = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{parts[1]}\{parts[2]}\Device Parameters"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
        raw, _ = winreg.QueryValueEx(k, "EDID")
    info = parse_edid(raw)
    info["registry"] = key
    _EDID_CACHE[device_id] = info
    return info


def _monitor_device_id(adapter_name):
    dd = _DISPLAY_DEVICEW()
    dd.cb = ctypes.sizeof(dd)
    first = None
    i = 0
    while _user32.EnumDisplayDevicesW(adapter_name, i, ctypes.byref(dd), 1):   # EDD_GET_DEVICE_INTERFACE_NAME
        if first is None:
            first = dd.DeviceID
        if dd.StateFlags & 0x1:                                                 # DISPLAY_DEVICE_ACTIVE
            return dd.DeviceID, dd.DeviceString
        i += 1
    return first, ""


def _monitor_scale(hmon, phys_w, logical_hint):
    try:
        x, y = wintypes.UINT(), wintypes.UINT()
        if ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), 0, ctypes.byref(x), ctypes.byref(y)) == 0:
            return x.value / 96.0, "GetDpiForMonitor"
    except Exception:
        pass
    if logical_hint and logical_hint > 0:
        return float(logical_hint), "Eto LogicalPixelSize"
    return 1.0, "default"


def detect_screen(hwnd, logical_hint=None):
    """ScreenInfo of the monitor showing hwnd. Raises ViewError when the size cannot be read."""
    if not IS_WINDOWS:
        raise ViewError("automatic monitor size is Windows only: type the screen PPI (Manual)")
    hmon = _user32.MonitorFromWindow(ctypes.c_void_p(hwnd or 0), 2)                     # NEAREST
    mi = _MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(mi)
    if not hmon or not _user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
        raise ViewError("monitor of the window not found")
    px_w = mi.rcMonitor.right - mi.rcMonitor.left
    px_h = mi.rcMonitor.bottom - mi.rcMonitor.top
    scale, scale_src = _monitor_scale(hmon, px_w, logical_hint)
    device_id, label = _monitor_device_id(mi.szDevice)
    if not device_id:
        raise ViewError(f"no monitor id for {mi.szDevice}")
    edid = _edid_for_device_id(device_id)
    if edid["width_mm"] <= 0:
        raise ViewError(f"monitor {edid['name']} does not report its size")
    # Windows may report scaled (virtualised) pixels if the process is not per-monitor DPI aware:
    # the native mode from the EDID then gives the real pixel count.
    real_w = px_w
    if edid["native_w"] and abs(px_w * scale - edid["native_w"]) < 2 and abs(px_w - edid["native_w"]) > 2:
        real_w = edid["native_w"]
    ppi = real_w / (edid["width_mm"] / 25.4)
    log("SCREEN", f"{mi.szDevice} id={device_id} label='{label}' rect={px_w}x{px_h} scale={scale:.3f} "
                  f"({scale_src}) edid={edid}")
    return ScreenInfo(ppi=ppi, scale=scale, name=edid["name"] or label or mi.szDevice,
                      source=edid.get("size_src", "EDID"), width_mm=edid["width_mm"],
                      px_w=real_w, px_h=px_h, device=device_id)


# ---- layout -> vector PDF in memory ----------------------------------------------
def page_views(doc):
    try:
        return list(doc.Views.GetPageViews())
    except Exception:
        log_exc("GetPageViews")
        return []


def layout_pdf_bytes(doc, page_view, color_mode, dpi):
    """Print one layout to a vector PDF (Rhino's own PDF engine) and return the bytes."""
    t0 = time.time()
    settings = Rhino.Display.ViewCaptureSettings(page_view, float(dpi))
    log("API", f"ViewCaptureSettings('{page_view.PageName}', {dpi}) -> {settings}")
    if settings is None or not getattr(settings, "IsValid", True):
        raise ViewError(f"Rhino refused to print layout '{page_view.PageName}'")
    try:
        settings.RasterMode = False                       # vector, like Rhino's Print default
    except Exception:
        log_exc("RasterMode")
    try:
        cm_enum = Rhino.Display.ViewCaptureSettings.ColorMode
        if color_mode:
            settings.OutputColor = getattr(cm_enum, color_mode)
    except Exception:
        log_exc(f"OutputColor {color_mode}")
    props = []
    for name in ("OutputColor", "RasterMode", "UsePrintWidths", "DefaultPrintWidthMillimeters",
                 "WireThicknessScale", "LinetypeWidthUnitsArePageLengths", "MediaSize", "Resolution"):
        try:
            props.append(f"{name}={getattr(settings, name)}")
        except Exception:
            props.append(f"{name}=?")
    log("API", "capture: " + " | ".join(props))
    pdf = Rhino.FileIO.FilePdf.Create()
    if pdf is None:
        raise ViewError("Rhino PDF engine not available (FilePdf.Create returned None)")
    idx = pdf.AddPage(settings)
    log("API", f"FilePdf.AddPage -> {idx}")
    data = None
    try:
        ms = System.IO.MemoryStream()
        pdf.Write(ms)
        arr = ms.ToArray()
        data = base64.b64decode(str(System.Convert.ToBase64String(arr)))
        ms.Dispose()
        log("API", f"FilePdf.Write(MemoryStream) -> {len(data)} bytes")
    except Exception:
        log_exc("FilePdf.Write(stream): file fallback")
    if not data:
        tmp = os.path.join(DATA_DIR, "cache", "last_layout.pdf")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        pdf.Write(tmp)
        with open(tmp, "rb") as f:
            data = f.read()
        log("API", f"FilePdf.Write('{tmp}') -> {len(data)} bytes")
    if not data or not data.startswith(b"%PDF"):
        raise ViewError("Rhino wrote an empty PDF")
    log("DONE", f"PDF of '{page_view.PageName}' built in {time.time() - t0:.2f} s, {len(data) / 1024:.0f} KB")
    return data


# ---- view geometry (device pixels) ---------------------------------------------
PT_PER_MM = 72.0 / 25.4


class ViewState:
    """Device-pixel view of a sheet. z = device px per paper mm; (ox, oy) = sheet origin in canvas px."""

    def __init__(self):
        self.z, self.ox, self.oy = 1.0, 0.0, 0.0

    def fit(self, page_w_mm, page_h_mm, w_px, h_px, margin=0.04):
        if page_w_mm <= 0 or page_h_mm <= 0 or w_px <= 0 or h_px <= 0:
            return
        self.z = min(w_px * (1 - 2 * margin) / page_w_mm, h_px * (1 - 2 * margin) / page_h_mm)
        self.ox = round((w_px - page_w_mm * self.z) / 2.0)
        self.oy = round((h_px - page_h_mm * self.z) / 2.0)

    def set_zoom_at(self, z_new, px, py):
        """Change zoom keeping the paper point under (px, py) fixed."""
        mx, my = (px - self.ox) / self.z, (py - self.oy) / self.z
        self.z = z_new
        self.ox = round(px - mx * z_new)
        self.oy = round(py - my * z_new)

    def pan(self, dx, dy):
        self.ox += dx
        self.oy += dy

    def paper_at(self, px, py):
        return (px - self.ox) / self.z, (py - self.oy) / self.z


def visible_pixel_rect(view, page_w_mm, page_h_mm, w_px, h_px):
    """Integer rect (x0, y0, x1, y1) of the sheet, in sheet pixels at zoom view.z, that the canvas shows."""
    pw, ph = page_w_mm * view.z, page_h_mm * view.z
    x0, y0 = max(0.0, -view.ox), max(0.0, -view.oy)
    x1, y1 = min(pw, w_px - view.ox), min(ph, h_px - view.oy)
    if x1 <= x0 or y1 <= y0:
        return None
    return int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(math.ceil(y1))


class Tile:
    """A rendered part of the sheet: image + where it sits, in sheet pixels at zoom z."""

    def __init__(self, image, z, x0, y0, w, h, ms):
        self.image, self.z, self.x0, self.y0, self.w, self.h, self.ms = image, z, x0, y0, w, h, ms


def render_png(page, z, rect):
    """Rasterise rect (sheet pixels at z px/mm) of a PyMuPDF page. Returns (png bytes, x0, y0, w, h)."""
    fz = _import_fitz()
    s = z / PT_PER_MM                                   # pixels per PDF point
    x0, y0, x1, y1 = rect
    if (x1 - x0) * (y1 - y0) > MAX_TILE_PIXELS:
        raise ViewError("zoomed area too large to render")
    r0 = page.rect
    clip = fz.Rect(r0.x0 + x0 / s, r0.y0 + y0 / s, r0.x0 + x1 / s, r0.y0 + y1 / s)
    pix = page.get_pixmap(matrix=fz.Matrix(s, s), clip=clip, alpha=False)
    # pix.x / pix.y: origin of the pixmap in the scaled page space (MuPDF rounds the clip outward)
    px0 = pix.x - int(round(r0.x0 * s))
    py0 = pix.y - int(round(r0.y0 * s))
    return pix.tobytes("png"), px0, py0, pix.width, pix.height


def png_to_bitmap(png):
    arr = System.Convert.FromBase64String(base64.b64encode(png).decode("ascii"))
    ms = System.IO.MemoryStream(arr)
    return drawing.Bitmap(ms)


# =============================================================================
# UI
# =============================================================================
class _Report:
    """Report panel: short coloured lines. Colour is optional (falls back to plain)."""

    def __init__(self):
        self.area = forms.RichTextArea()
        self.area.ReadOnly = True
        self.area.Font = font("font_mono")
        self.area.BackgroundColor = color("bg_panel")
        self.area.TextColor = color("text")
        self.area.Height = 62
        self.lines = []
        self._color_ok = True

    def clear(self):
        self.lines = []
        self._render()

    def add(self, text, kind="text"):
        self.lines.append((text, kind))
        self.lines = self.lines[-200:]
        self._render()

    def _render(self):
        full = "\n".join(t for t, _ in self.lines)
        self.area.Text = full
        if self._color_ok:
            try:
                pos = 0
                for text, kind in self.lines:
                    if kind in ("success", "warning", "error", "text_muted") and text:
                        rng = forms.Range[int](pos, pos + len(text) - 1)
                        self.area.Buffer.SetForeground(rng, color(kind))
                    pos += len(text) + 1
            except Exception:
                self._color_ok = False
                log("ERROR", "report colouring unavailable, plain text used\n" + traceback.format_exc())
        try:
            self.area.CaretIndex = len(full)
            self.area.ScrollToEnd()
        except Exception:
            pass


def _help_icon(tip):
    d = forms.Drawable()
    d.Size = drawing.Size(16, 16)
    d.ToolTip = wrap_tip(tip)

    def paint(sender, e):
        try:
            g = e.Graphics
            g.AntiAlias = True
            c = color("help_icon")
            rect = drawing.RectangleF(1.5, 1.5, 13.0, 13.0)
            g.DrawEllipse(drawing.Pen(c, 1.2), rect)
            f = drawing.Font("Segoe UI", 7.5, drawing.FontStyle.Bold)
            try:
                size = g.MeasureString(f, "?")
                w, h = size.Width, size.Height
            except Exception:
                w, h = 5.0, 11.0
            cx, cy = rect.X + rect.Width / 2.0, rect.Y + rect.Height / 2.0
            g.DrawText(f, c, cx - w / 2.0, cy - h / 2.0, "?")
        except Exception:
            log_exc("help icon paint")

    d.Paint += paint
    return d


def _label(text, muted=False, bold=False, tip=None, fnt=None):
    lb = forms.Label()
    lb.Text = text
    lb.Font = fnt or font(bold=bold)
    lb.TextColor = color("text_muted" if muted else "text")
    lb.VerticalAlignment = forms.VerticalAlignment.Center
    if tip:
        lb.ToolTip = wrap_tip(tip)
    return lb


def _stepper(value, mn, mx, inc, decimals, width=70):
    s = forms.NumericStepper()
    s.DecimalPlaces = decimals
    s.MinValue = mn
    s.MaxValue = mx
    s.Increment = inc
    s.Value = value
    s.Width = width
    s.TextColor = color("text")
    s.BackgroundColor = color("bg_panel")
    return s


# ---- custom-drawn controls -----------------------------------------------------
class _Event:
    """Minimal event: supports `obj.Evt += handler`; handlers get (sender, e)."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, sender):
        for fn in list(self.handlers):
            fn(sender, None)


def _text_size(fnt, text):
    try:
        sz = fnt.MeasureString(text)
        return float(sz.Width), float(sz.Height)
    except Exception:
        return len(text) * 6.5, 14.0


def _fit_text(fnt, text, max_w):
    """Text cut with '...' so it fits in max_w pixels."""
    if _text_size(fnt, text)[0] <= max_w:
        return text
    while len(text) > 1 and _text_size(fnt, text + "...")[0] > max_w:
        text = text[:-1]
    return text + "..."


def _fill_poly(g, c, pts):
    """Graphics.FillPolygon takes a .NET PointF[] (params): pass a real array."""
    g.FillPolygon(c, System.Array[drawing.PointF](pts))


def _pen(token, width=1.2):
    return drawing.Pen(color(token), width)


def _hook_mouse(widget, d):
    def enter(sender, e):
        widget.hover = True
        d.Invalidate()

    def leave(sender, e):
        widget.hover = False
        d.Invalidate()

    def up(sender, e):
        try:
            if widget.enabled:
                widget.clicked()
        except Exception:
            log_exc(f"{type(widget).__name__} click")

    d.MouseEnter += enter
    d.MouseLeave += leave
    d.MouseUp += up
    try:
        d.Cursor = forms.Cursors.Pointer
    except Exception:
        pass


class FlatButton:
    def __init__(self, text, tip=None, bg="bg_panel", bg_hover="bg_hover", min_width=0):
        self.text, self.bg, self.bg_hover = text, bg, bg_hover
        self.hover, self.enabled = False, True
        self.Click = _Event()
        self.font = font()
        w, _ = _text_size(self.font, text)
        d = forms.Drawable()
        d.Size = drawing.Size(max(int(w) + 28, min_width), TOKENS["control_height"])
        if tip:
            d.ToolTip = wrap_tip(tip)
        d.Paint += self._paint
        _hook_mouse(self, d)
        self.control = d

    @property
    def Enabled(self):
        return self.enabled

    @Enabled.setter
    def Enabled(self, value):
        self.enabled = bool(value)
        self.control.Invalidate()

    def clicked(self):
        self.Click.fire(self)

    def _paint(self, sender, e):
        try:
            g = e.Graphics
            w, h = float(self.control.Width), float(self.control.Height)
            bg = self.bg_hover if (self.hover and self.enabled) else self.bg
            if not self.enabled:
                bg = "bg_panel"
            g.FillRectangle(color(bg), 0.0, 0.0, w, h)
            g.DrawRectangle(color("border"), 0.0, 0.0, w - 1.0, h - 1.0)
            tw, th = _text_size(self.font, self.text)
            g.DrawText(self.font, color("text" if self.enabled else "text_disabled"),
                       (w - tw) / 2.0, (h - th) / 2.0, self.text)
        except Exception:
            log_exc("FlatButton paint")


class _Option:
    """One checkbox, drawn by hand."""

    def __init__(self, owner, index, text, kind):
        self.owner, self.index, self.text, self.kind = owner, index, text, kind
        self.hover, self.enabled = False, True
        self.font = font()
        w, _ = _text_size(self.font, text)
        d = forms.Drawable()
        d.Size = drawing.Size(int(w) + 26, TOKENS["control_height"] - 4)
        d.Paint += self._paint
        _hook_mouse(self, d)
        self.control = d

    def clicked(self):
        self.owner._clicked(self.index)

    def _paint(self, sender, e):
        try:
            g = e.Graphics
            g.AntiAlias = True
            h = float(self.control.Height)
            g.FillRectangle(color("bg_window"), 0.0, 0.0, float(self.control.Width), h)
            on = self.owner._is_on(self.index)
            edge = color("accent" if on else ("text" if self.hover else "text_muted"))
            box = drawing.RectangleF(2.0, h / 2.0 - 6.5, 13.0, 13.0)
            if on:
                g.FillRectangle(color("accent"), box)
                pen = drawing.Pen(color("text"), 1.6)
                g.DrawLine(pen, 5.0, h / 2.0, 7.5, h / 2.0 + 2.5)
                g.DrawLine(pen, 7.5, h / 2.0 + 2.5, 12.0, h / 2.0 - 3.0)
            else:
                g.DrawRectangle(drawing.Pen(edge, 1.2), box)
            tw, th = _text_size(self.font, self.text)
            g.DrawText(self.font, color("text"), 21.0, (h - th) / 2.0, self.text)
        except Exception:
            log_exc("option paint")


class Toggle:
    """Checkbox drawn by hand; checked box filled with the accent colour."""

    def __init__(self, text, checked=True, tip=None):
        self._checked = checked
        self.CheckedChanged = _Event()
        self.option = _Option(self, 0, text, "check")
        self.control = self.option.control
        if tip:
            self.control.ToolTip = wrap_tip(tip)

    @property
    def Checked(self):
        return self._checked

    def _is_on(self, i):
        return self._checked

    def _clicked(self, i):
        self._checked = not self._checked
        self.control.Invalidate()
        self.CheckedChanged.fire(self)


def _icon_refresh(g, w, h, tok):
    cx, cy, r = w / 2.0, h / 2.0, 6.0
    g.DrawArc(_pen(tok, 1.6), cx - r, cy - r, 2 * r, 2 * r, -60.0, 300.0)
    ax, ay = cx + r * math.cos(math.radians(-60.0)), cy + r * math.sin(math.radians(-60.0))
    _fill_poly(g, color(tok), [drawing.PointF(ax + 3.5, ay - 1.5), drawing.PointF(ax - 3.0, ay - 3.0),
                               drawing.PointF(ax + 0.5, ay + 3.5)])


def _icon_minus(g, w, h, tok):
    g.DrawLine(_pen(tok, 1.8), w / 2.0 - 5, h / 2.0, w / 2.0 + 5, h / 2.0)


def _icon_plus(g, w, h, tok):
    p = _pen(tok, 1.8)
    g.DrawLine(p, w / 2.0 - 5, h / 2.0, w / 2.0 + 5, h / 2.0)
    g.DrawLine(p, w / 2.0, h / 2.0 - 5, w / 2.0, h / 2.0 + 5)


def _icon_fit(g, w, h, tok):
    """Sheet inside four corner brackets."""
    p = _pen(tok, 1.4)
    cx, cy = w / 2.0, h / 2.0
    g.DrawRectangle(p, cx - 4.5, cy - 3.0, 9.0, 6.0)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = cx + sx * 8.0, cy + sy * 7.0
            g.DrawLine(p, x, y, x - sx * 3.5, y)
            g.DrawLine(p, x, y, x, y - sy * 3.5)


class IconButton:
    """Square button with a drawn icon (no text). Tooltip says what it does."""

    def __init__(self, painter, tip):
        self.painter = painter
        self.hover, self.enabled = False, True
        self.Click = _Event()
        d = forms.Drawable()
        side = TOKENS["control_height"]
        d.Size = drawing.Size(side, side)
        d.ToolTip = wrap_tip(tip)
        d.Paint += self._paint
        _hook_mouse(self, d)
        self.control = d

    @property
    def Enabled(self):
        return self.enabled

    @Enabled.setter
    def Enabled(self, value):
        self.enabled = bool(value)
        self.control.Invalidate()

    def clicked(self):
        self.Click.fire(self)

    def _paint(self, sender, e):
        try:
            g = e.Graphics
            g.AntiAlias = True
            w, h = float(self.control.Width), float(self.control.Height)
            g.FillRectangle(color("bg_hover" if (self.hover and self.enabled) else "bg_panel"), 0.0, 0.0, w, h)
            g.DrawRectangle(color("border"), 0.0, 0.0, w - 1.0, h - 1.0)
            self.painter(g, w, h, "text" if self.enabled else "text_disabled")
        except Exception:
            log_exc("IconButton paint")


class Dropdown:
    """Drawn button showing the current item + chevron. Click opens a ContextMenu."""

    def __init__(self, width=200, tip=None):
        self.items, self._index = [], -1
        self.hover, self.enabled = False, True
        self.SelectedIndexChanged = _Event()
        self.font = font()
        d = forms.Drawable()
        d.Size = drawing.Size(width, TOKENS["control_height"])
        if tip:
            d.ToolTip = wrap_tip(tip)
        d.Paint += self._paint
        _hook_mouse(self, d)
        self.control = d

    @property
    def SelectedIndex(self):
        return self._index

    def set_items(self, items, index):
        self.items, self._index = list(items), index
        self.control.Invalidate()

    def select(self, index):
        if index != self._index:
            self._index = index
            self.control.Invalidate()
            self.SelectedIndexChanged.fire(self)

    def clicked(self):
        menu = forms.ContextMenu()
        for i, text in enumerate(self.items):
            it = forms.ButtonMenuItem()
            it.Text = ("> " if i == self._index else "   ") + text
            it.Click += self._make_pick(i)
            menu.Items.Add(it)
        try:
            menu.Show(self.control)
        except Exception:
            menu.Show(self.control, drawing.PointF(0.0, float(self.control.Height)))

    def _make_pick(self, i):
        def pick(sender, e):
            try:
                self.select(i)
            except Exception:
                log_exc("Dropdown pick")
        return pick

    def _paint(self, sender, e):
        try:
            g = e.Graphics
            g.AntiAlias = True
            w, h = float(self.control.Width), float(self.control.Height)
            g.FillRectangle(color("bg_hover" if self.hover else "bg_panel"), 0.0, 0.0, w, h)
            g.DrawRectangle(color("border"), 0.0, 0.0, w - 1.0, h - 1.0)
            text = self.items[self._index] if 0 <= self._index < len(self.items) else ""
            text = _fit_text(self.font, text, w - 36.0)
            tw, th = _text_size(self.font, text)
            g.DrawText(self.font, color("text"), 8.0, (h - th) / 2.0, text)
            cx, cy = w - 14.0, h / 2.0
            _fill_poly(g, color("text_muted"), [drawing.PointF(cx - 4.0, cy - 2.0),
                                                drawing.PointF(cx + 4.0, cy - 2.0),
                                                drawing.PointF(cx, cy + 3.0)])
        except Exception:
            log_exc("Dropdown paint")


def _table_row(*items):
    """TableRow from controls or (control, expand); None = empty cell."""
    row = forms.TableRow()
    for it in items:
        ctrl, expand = it if isinstance(it, tuple) else (it, False)
        ctrl = getattr(ctrl, "control", ctrl)
        row.Cells.Add(forms.TableCell(ctrl, expand))
    return row


def _hrow(*items):
    """Single-row table; items are controls or (control, expand)."""
    t = forms.TableLayout()
    t.Spacing = drawing.Size(TOKENS["space"], 0)
    t.Rows.Add(_table_row(*items))
    return t


def _vsep():
    p = forms.Panel()
    p.Size = drawing.Size(1, TOKENS["control_height"] - 6)
    p.BackgroundColor = color("border")
    return p


SCREEN_MODES = ["Auto (monitor size)", "Manual PPI"]
HELP_SCREEN = ("1:1 needs the real size of your monitor.\n"
               "Auto: read from the monitor itself (EDID) for the monitor the window is on; "
               "moving the window to another monitor updates it.\n"
               "Manual: type the pixels per inch of your monitor (horizontal pixels / width in inches).\n"
               "Check: at 1:1, a 100 mm line on the sheet must measure 100 mm with a ruler on the screen.")
HELP_VIEW = ("Mouse wheel: zoom at the cursor.  Drag (left or middle button): pan.\n"
             "Double-click: 1:1 at that point.\n"
             "Keys: 1 = 1:1,  F = fit,  + / - = zoom,  R or F5 = refresh.")


class PluginUI:
    def __init__(self):
        self.version = __version__
        self.is_open = False
        self.prefs = {}
        self.doc_pdf = None              # PyMuPDF document of the current layout
        self.page = None
        self.page_mm = (0.0, 0.0)
        self.pdf_key = None              # (layout id, color, dpi) of doc_pdf
        self.views = []                  # RhinoPageView list, same order as the dropdown
        self.view = ViewState()
        self.tile = None
        self.screen = None
        self.need_fit = True
        self.render_due = 0.0            # time at which a sharp render should happen (0 = none)
        self.doc_dirty_at = 0.0          # last document change seen (0 = none)
        self.drag = None                 # (x, y) of the last drag point, device px
        self.busy = False
        self._doc_handlers = []
        start_log_session()
        self._log_header()
        self.prefs = load_settings()
        self._build()

    # ---- logging ----
    def _log_header(self):
        doc = Rhino.RhinoDoc.ActiveDoc
        log("START", f"{PLUGIN_NAME} v{__version__}")
        log("START", f"Rhino {Rhino.RhinoApp.Version} | Python {sys.version.split()[0]} | exe {sys.executable}")
        log("START", f"plugin dir {PLUGIN_DIR} (from {PLUGIN_DIR_SOURCE}) | data dir {DATA_DIR} ({DATA_DIR_SOURCE})")
        log("START", f"platform {sys.platform} | lib {LIB_DIR}")
        fz = _import_fitz()
        log("START", f"PyMuPDF {getattr(fz, 'VersionBind', '?')} from {getattr(fz, '__file__', '?')}")
        try:
            log("START", f"MuPDF anti-aliasing: graphics={fz.TOOLS.show_aa_level()}")
        except Exception:
            pass
        if doc is not None:
            log("START", f"file {doc.Path or '(unsaved)'} | units {doc.ModelUnitSystem} | "
                         f"page units {doc.PageUnitSystem} | layouts {len(page_views(doc))}")

    def _safe(self, name, fn):
        """Every event handler goes through this: Eto swallows exceptions silently otherwise."""
        def handler(sender, e):
            try:
                fn(sender, e)
            except Exception as ex:
                log_exc(name)
                try:
                    self.report.add(f"Error in {name}: {ex} (see log)", "error")
                except Exception:
                    pass
        return handler

    # ---- layout ----
    def _build(self):
        f = forms.Form()
        self.form = f
        f.Title = PLUGIN_NAME
        f.Resizable = True
        f.Minimizable = True
        f.Maximizable = True
        f.ClientSize = drawing.Size(1180, 820)
        f.BackgroundColor = color("bg_window")
        f.Padding = drawing.Padding(TOKENS["pad_window"])
        try:
            f.Owner = Rhino.UI.RhinoEtoApp.MainWindow
        except Exception:
            log_exc("set owner")

        badge = _label(f"v{__version__}", muted=True)

        # top bar: what to show
        self.dd_layout = Dropdown(230, "Layout shown. The list is refreshed with the Refresh button.")
        self.dd_layout.SelectedIndexChanged += self._safe("Layout", self.on_layout)
        self.dd_color = Dropdown(130, "Colours of the PDF, as in Rhino's Print dialog.")
        self.dd_color.set_items([c[0] for c in COLOR_MODES], 0)
        self.dd_color.SelectedIndexChanged += self._safe("Color", self.on_color)
        self.st_dpi = _stepper(DEFAULT_CAPTURE_DPI, 72, 1200, 50, 0, 64)
        self.st_dpi.ValueChanged += self._safe("Dpi", self.on_dpi)
        self.btn_refresh = FlatButton("Refresh", "Print the layout again (vector PDF in memory) and redraw. "
                                                 "Needed after zooming or panning inside a detail.",
                                      bg="accent", bg_hover="accent_hover")
        self.btn_refresh.Click += self._safe("Refresh", self.on_refresh)
        self.chk_auto = Toggle("Auto", True, "Refresh by itself about 1 s after the document changes "
                                             "(objects, layers, linetypes). Not during a command.")
        self.chk_auto.CheckedChanged += self._safe("Auto", self.on_auto)

        # canvas
        cv = forms.Drawable()
        cv.CanFocus = True
        cv.BackgroundColor = color("canvas")
        cv.ToolTip = wrap_tip(HELP_VIEW)
        cv.Paint += self._safe("Paint", self.on_paint)
        cv.SizeChanged += self._safe("Size", self.on_canvas_size)
        cv.MouseWheel += self._safe("Wheel", self.on_wheel)
        cv.MouseDown += self._safe("MouseDown", self.on_mouse_down)
        cv.MouseMove += self._safe("MouseMove", self.on_mouse_move)
        cv.MouseUp += self._safe("MouseUp", self.on_mouse_up)
        cv.MouseDoubleClick += self._safe("DoubleClick", self.on_double_click)
        cv.KeyDown += self._safe("Key", self.on_key)
        self.canvas = cv

        # bottom bar: zoom + screen
        self.btn_fit = IconButton(_icon_fit, "Fit the whole sheet (key F).")
        self.btn_fit.Click += self._safe("Fit", lambda s, e: self.zoom_fit())
        self.btn_one = FlatButton("1:1", "Real size: 10 mm on the sheet = 10 mm on your screen (key 1). "
                                         "Lineweights then look as on paper.", min_width=46)
        self.btn_one.Click += self._safe("1:1", lambda s, e: self.zoom_real(1.0))
        self.btn_out = IconButton(_icon_minus, "Zoom out (key -).")
        self.btn_out.Click += self._safe("ZoomOut", lambda s, e: self.zoom_step(1.0 / WHEEL_STEP))
        self.btn_in = IconButton(_icon_plus, "Zoom in (key +).")
        self.btn_in.Click += self._safe("ZoomIn", lambda s, e: self.zoom_step(WHEEL_STEP))
        self.lbl_zoom = _label("-", fnt=font("font_zoom", bold=True),
                               tip="Real zoom: 100% = real size on this screen.")
        self.lbl_zoom.Width = 64
        self.lbl_zoom.TextAlignment = forms.TextAlignment.Center

        self.dd_screen = Dropdown(150, "How the real size of the screen is known.")
        mode = 1 if self.prefs.get("screen_mode") == "manual" else 0
        self.dd_screen.set_items(SCREEN_MODES, mode)
        self.dd_screen.SelectedIndexChanged += self._safe("ScreenMode", self.on_screen_mode)
        self.st_ppi = _stepper(float(self.prefs.get("manual_ppi", FALLBACK_PPI)), 40, 600, 1, 2, 76)
        self.st_ppi.Enabled = (mode == 1)
        self.st_ppi.ValueChanged += self._safe("PPI", self.on_ppi)
        self.lbl_screen = _label("", muted=True)
        self.lbl_status = _label("", muted=True)

        self.report = _Report()
        self.btn_log = FlatButton("Open log folder", "Open the logs folder of this plugin (one file per window session).")
        self.btn_log.Click += self._safe("Log", self.on_log)

        main = forms.TableLayout()
        main.Spacing = drawing.Size(TOKENS["space"], TOKENS["space"])

        def add(ctrl, scale_height=False):
            row = _table_row((ctrl, True))
            row.ScaleHeight = scale_height
            main.Rows.Add(row)

        add(_hrow(_label("Layout"), self.dd_layout, self.dd_color,
                  _label("Detail raster"), self.st_dpi, _label("dpi", muted=True),
                  _help_icon("Resolution Rhino uses for shaded / rendered details inside the PDF "
                             "(wireframe and curves stay vector). Same as the Print dialog resolution."),
                  _vsep(), self.btn_refresh, self.chk_auto, (None, True), badge))
        add(self.canvas, scale_height=True)
        add(_hrow(self.btn_fit, self.btn_one, self.btn_out, self.lbl_zoom, self.btn_in, _vsep(),
                  _label("Screen"), self.dd_screen, self.st_ppi, _label("ppi", muted=True),
                  _help_icon(HELP_SCREEN), (self.lbl_screen, True)))
        add(self.report.area)
        add(_hrow(self.btn_log, (self.lbl_status, True)))

        f.Content = main
        f.Closed += self._safe("Closed", self.on_closed)
        try:
            f.LocationChanged += self._safe("Moved", lambda s, e: self._schedule_render())
        except Exception:
            log_exc("LocationChanged hook")

        self.timer = forms.UITimer()
        self.timer.Interval = 0.1
        self.timer.Elapsed += self._safe("Timer", self.on_timer)

        self._fill_layouts(prefer_active=True)
        self._preselect_color()

    def _preselect_color(self):
        """Start on the colour mode Rhino uses by default for printing (same output as a normal Rhino PDF)."""
        pv = self.current_layout()
        if pv is None:
            return
        try:
            default = str(Rhino.Display.ViewCaptureSettings(pv, DEFAULT_CAPTURE_DPI).OutputColor)
            for i, (_, name) in enumerate(COLOR_MODES):
                if name == default:
                    self.dd_color.set_items([c[0] for c in COLOR_MODES], i)
            log("SETTING", f"Rhino default OutputColor = {default}")
        except Exception:
            log_exc("default OutputColor")

    # ---- window ----
    def show(self):
        self.form.Show()
        self.is_open = True
        log("START", "window shown")
        self._hook_doc()
        self.timer.Start()
        self.canvas.Focus()
        self.refresh_pdf("open")

    def bring_to_front(self):
        try:
            self.form.BringToFront()
            self.form.Focus()
        except Exception:
            log_exc("bring_to_front")

    def close(self):
        try:
            self.form.Close()
        except Exception:
            log_exc("close")

    def on_closed(self, sender, e):
        self.is_open = False
        try:
            self.timer.Stop()
        except Exception:
            pass
        self._unhook_doc()
        try:
            if self.doc_pdf is not None:
                self.doc_pdf.close()
        except Exception:
            pass
        self.doc_pdf, self.page = None, None
        self._set_tile(None)
        log("CLOSE", "window closed")
        if sc.sticky.get(STICKY_KEY) is self:
            sc.sticky.pop(STICKY_KEY, None)
        stop_log_session()

    def on_log(self, sender, e):
        log("SELECT", "Log clicked")
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            if IS_WINDOWS:
                os.startfile(LOG_DIR)
            else:
                subprocess.Popen(["open", LOG_DIR])
        except Exception:
            log_exc("open log folder")
            self.report.add(f"Cannot open {LOG_DIR}", "error")

    # ---- document events (only set a flag; work happens in the timer) ----
    def _hook_doc(self):
        def changed(sender, e):
            if not self.busy:                     # our own printing must not trigger a refresh
                self.doc_dirty_at = time.time()

        def opened(sender, e):
            self.doc_dirty_at = time.time()
            self._layouts_stale = True

        events = ["AddRhinoObject", "DeleteRhinoObject", "UndeleteRhinoObject", "ReplaceRhinoObject",
                  "ModifyObjectAttributes", "LayerTableEvent", "DimensionStyleTableEvent",
                  "MaterialTableEvent", "GroupTableEvent", "DocumentPropertiesChanged"]
        for name in events:
            self._hook_one(name, changed)
        for name in ("EndOpenDocument", "NewDocument", "CloseDocument"):
            self._hook_one(name, opened)
        log("START", f"document events hooked: {[n for n, _ in self._doc_handlers]}")

    def _hook_one(self, name, fn):
        try:
            ev = getattr(Rhino.RhinoDoc, name)
            ev += fn
            self._doc_handlers.append((name, fn))
        except Exception:
            log("API", f"event {name} not hooked: {sys.exc_info()[1]}")

    def _unhook_doc(self):
        for name, fn in self._doc_handlers:
            try:
                ev = getattr(Rhino.RhinoDoc, name)
                ev -= fn
            except Exception:
                log("API", f"event {name} not unhooked: {sys.exc_info()[1]}")
        self._doc_handlers = []
        log("CLOSE", "document events unhooked")

    # ---- layouts ----
    _layouts_stale = False

    def _fill_layouts(self, prefer_active=False):
        doc = Rhino.RhinoDoc.ActiveDoc
        old = None
        if 0 <= self.dd_layout.SelectedIndex < len(self.views):
            try:
                old = self.views[self.dd_layout.SelectedIndex].MainViewport.Id
            except Exception:
                old = None
        self.views = page_views(doc) if doc is not None else []
        names = [v.PageName for v in self.views]
        idx = 0 if names else -1
        active = doc.Views.ActiveView if doc is not None else None
        for i, v in enumerate(self.views):
            vid = v.MainViewport.Id
            if old is not None and vid == old:
                idx = i
            elif old is None and prefer_active and active is not None and \
                    isinstance(active, Rhino.Display.RhinoPageView) and active.MainViewport.Id == vid:
                idx = i
        self.dd_layout.set_items(names or ["(no layout in this file)"], idx if names else 0)
        self._layouts_stale = False
        log("SELECT", f"layouts: {names} -> selected index {idx}")

    def current_layout(self):
        i = self.dd_layout.SelectedIndex
        if 0 <= i < len(self.views):
            return self.views[i]
        return None

    def on_layout(self, sender, e):
        pv = self.current_layout()
        log("SETTING", f"Layout = {pv.PageName if pv else None}")
        self.need_fit = True
        self.refresh_pdf("layout changed")

    def on_color(self, sender, e):
        log("SETTING", f"Color = {COLOR_MODES[self.dd_color.SelectedIndex][1]}")
        self.refresh_pdf("color changed")

    def on_dpi(self, sender, e):
        log("SETTING", f"Detail raster dpi = {self.st_dpi.Value}")
        self.doc_dirty_at = time.time()          # applied by the timer once the value stops changing

    def on_auto(self, sender, e):
        log("SETTING", f"Auto = {self.chk_auto.Checked}")

    def on_refresh(self, sender, e):
        log("RUN", "Refresh clicked")
        self._fill_layouts()
        self.refresh_pdf("refresh button", force=True)

    # ---- PDF ----
    def refresh_pdf(self, why, force=False):
        if self.busy:
            return
        pv = self.current_layout()
        if pv is None:
            self.report.add("No layout in this file: create one (Layout command) and click Refresh.", "warning")
            log("SKIP", "no layout")
            return
        doc = Rhino.RhinoDoc.ActiveDoc
        cm = COLOR_MODES[max(0, self.dd_color.SelectedIndex)][1]
        dpi = float(self.st_dpi.Value)
        log("RUN", f"refresh ({why}): layout='{pv.PageName}' color={cm} dpi={dpi:g}")
        self.doc_dirty_at = 0.0                   # a failure must not retry on every timer tick
        self.busy = True
        self.btn_refresh.Enabled = False
        self.lbl_status.Text = "Printing layout to PDF..."
        try:
            Rhino.RhinoApp.Wait()
        except Exception:
            pass
        t0 = time.time()
        try:
            data = layout_pdf_bytes(doc, pv, cm, dpi)
            fz = _import_fitz()
            new_doc = fz.open(stream=data, filetype="pdf")
            if new_doc.page_count < 1:
                raise ViewError("the PDF has no page")
            page = new_doc[0]
            w_mm, h_mm = page.rect.width / PT_PER_MM, page.rect.height / PT_PER_MM
            log("API", f"PyMuPDF page: rect={page.rect} rotation={page.rotation} -> {w_mm:.1f} x {h_mm:.1f} mm")
            old = self.doc_pdf
            self.doc_pdf, self.page = new_doc, page
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            if abs(w_mm - self.page_mm[0]) > 0.01 or abs(h_mm - self.page_mm[1]) > 0.01:
                self.need_fit = True
            self.page_mm = (w_mm, h_mm)
            self.pdf_key = (str(pv.MainViewport.Id), cm, dpi)
            dt = time.time() - t0
            self.pdf_seconds = dt
            self.report.add(f"{pv.PageName}: {w_mm:.0f} x {h_mm:.0f} mm, printed in {dt:.1f} s", "success")
            self.render_now()
        except ViewError as ex:
            log("SKIP", str(ex))
            self.report.add(str(ex), "error")
        except Exception as ex:
            log_exc("refresh_pdf")
            self.report.add(f"PDF failed: {ex} (see log)", "error")
        finally:
            self.busy = False
            self.btn_refresh.Enabled = True

    # ---- screen ----
    def _hwnd(self):
        for getter in (lambda: self.form.NativeHandle, lambda: self.form.ControlObject.Handle,
                       lambda: Rhino.RhinoApp.MainWindowHandle()):
            try:
                h = getter()
                v = int(h.ToInt64()) if hasattr(h, "ToInt64") else int(h)
                if v:
                    return v
            except Exception:
                continue
        return 0

    def _eto_scale(self):
        for getter in (lambda: self.form.LogicalPixelSize, lambda: self.form.Screen.LogicalPixelSize,
                       lambda: self.form.Screen.Scale):
            try:
                v = float(getter())
                if v > 0:
                    return v
            except Exception:
                continue
        return 1.0

    def _read_screen(self):
        eto_scale = self._eto_scale()
        manual = self.dd_screen.SelectedIndex == 1
        info = None
        err = None
        try:
            info = detect_screen(self._hwnd(), eto_scale)
        except Exception as ex:
            err = str(ex)
            if not isinstance(ex, ViewError):
                log_exc("detect_screen")
        if info is None:
            info = ScreenInfo(ppi=FALLBACK_PPI * eto_scale, scale=eto_scale, name="unknown monitor",
                              source="fallback")
        if abs(info.scale - eto_scale) > 0.01:
            log("SCREEN", f"scale monitor {info.scale:.3f} vs Eto {eto_scale:.3f}: Eto value used for drawing")
            info.scale = eto_scale
        if manual:
            info.ppi = float(self.st_ppi.Value)
            info.source = "manual"
        elif err:
            info.ppi = float(self.st_ppi.Value)
            info.source = "manual (auto failed)"
        return info, err

    def _update_screen(self):
        info, err = self._read_screen()
        old = self.screen
        if old is None or old.key() != info.key():
            self.screen = info
            log("SCREEN", f"using {info.describe()} ({info.source})" + (f" | auto failed: {err}" if err else ""))
            if err and self.dd_screen.SelectedIndex == 0:
                self.report.add(f"Screen size not found ({err}): using {info.ppi:.1f} ppi from the Manual "
                                f"field. Set Screen to Manual PPI and type your monitor's ppi.", "warning")
            self.lbl_screen.Text = info.describe() + ("" if info.source.startswith("EDID") else f"  ({info.source})")
            if self.dd_screen.SelectedIndex == 0 and not err:
                self.st_ppi.Value = round(info.ppi, 2)
            if old is not None and old.ppmm > 0 and abs(old.ppmm - info.ppmm) > 1e-6 and not self.need_fit:
                real = self.view.z / old.ppmm                     # keep the real zoom when the monitor changes
                w, h = self._canvas_px()
                self.view.set_zoom_at(real * info.ppmm, w / 2.0, h / 2.0)
            return True
        return False

    def on_screen_mode(self, sender, e):
        manual = self.dd_screen.SelectedIndex == 1
        self.st_ppi.Enabled = manual
        log("SETTING", f"Screen mode = {'manual' if manual else 'auto'}")
        self.prefs["screen_mode"] = "manual" if manual else "auto"
        save_settings(self.prefs)
        self._update_screen()
        self.render_now()

    def on_ppi(self, sender, e):
        if self.dd_screen.SelectedIndex != 1:
            return
        log("SETTING", f"Manual ppi = {self.st_ppi.Value}")
        self.prefs["manual_ppi"] = float(self.st_ppi.Value)
        save_settings(self.prefs)
        self._update_screen()
        self._schedule_render()

    # ---- view ----
    def _canvas_px(self):
        s = self.screen.scale if self.screen else self._eto_scale()
        return float(self.canvas.Width) * s, float(self.canvas.Height) * s

    def _event_px(self, e):
        s = self.screen.scale if self.screen else self._eto_scale()
        return float(e.Location.X) * s, float(e.Location.Y) * s

    def real_zoom(self):
        if self.screen is None or self.screen.ppmm <= 0:
            return 0.0
        return self.view.z / self.screen.ppmm

    def _set_zoom(self, z, px, py, why):
        if self.screen is None:
            self._update_screen()
        lo, hi = ZOOM_MIN * self.screen.ppmm, ZOOM_MAX * self.screen.ppmm
        z = min(max(z, lo), hi)
        self.view.set_zoom_at(z, px, py)
        log("VIEW", f"{why}: real zoom {self.real_zoom() * 100:.1f}% at ({px:.0f}, {py:.0f})")
        self._schedule_render()

    def zoom_fit(self):
        w, h = self._canvas_px()
        self.view.fit(self.page_mm[0], self.page_mm[1], w, h)
        self.need_fit = False
        log("VIEW", f"fit: real zoom {self.real_zoom() * 100:.1f}%")
        self._schedule_render()

    def zoom_real(self, real, at=None):
        self._update_screen()
        w, h = self._canvas_px()
        px, py = at if at else (w / 2.0, h / 2.0)
        self._set_zoom(real * self.screen.ppmm, px, py, f"zoom {real * 100:.0f}%")

    def zoom_step(self, factor, at=None):
        w, h = self._canvas_px()
        px, py = at if at else (w / 2.0, h / 2.0)
        self._set_zoom(self.view.z * factor, px, py, f"zoom x{factor:.3f}")

    def on_wheel(self, sender, e):
        steps = float(e.Delta.Height)
        if steps == 0:
            return
        self.zoom_step(WHEEL_STEP ** steps, self._event_px(e))
        e.Handled = True

    def on_mouse_down(self, sender, e):
        self.canvas.Focus()
        btn = e.Buttons
        if btn == forms.MouseButtons.Primary or btn == forms.MouseButtons.Middle:
            self.drag = self._event_px(e)
            try:
                self.canvas.Cursor = forms.Cursors.Move
            except Exception:
                pass
            try:
                self.canvas.CaptureMouse()
            except Exception:
                pass

    def on_mouse_move(self, sender, e):
        if self.drag is None:
            return
        x, y = self._event_px(e)
        dx, dy = round(x - self.drag[0]), round(y - self.drag[1])
        if dx or dy:
            self.view.pan(dx, dy)
            self.drag = (self.drag[0] + dx, self.drag[1] + dy)
            self.canvas.Invalidate()
            self.render_due = time.time() + RENDER_DELAY_S

    def on_mouse_up(self, sender, e):
        if self.drag is not None:
            self.drag = None
            try:
                self.canvas.ReleaseMouseCapture()
            except Exception:
                pass
            try:
                self.canvas.Cursor = forms.Cursors.Default
            except Exception:
                pass
            log("VIEW", f"pan to origin ({self.view.ox:.0f}, {self.view.oy:.0f})")
            self._schedule_render()

    def on_double_click(self, sender, e):
        self.drag = None
        self.zoom_real(1.0, self._event_px(e))

    def on_key(self, sender, e):
        k = e.Key

        def keys(*names):
            return [getattr(forms.Keys, n) for n in names if hasattr(forms.Keys, n)]

        if k in keys("D1", "Keypad1", "NumPad1"):
            self.zoom_real(1.0)
        elif k in keys("F", "D0", "Keypad0"):
            self.zoom_fit()
        elif k in keys("Add", "Plus", "Equal"):
            self.zoom_step(WHEEL_STEP)
        elif k in keys("Subtract", "Minus"):
            self.zoom_step(1.0 / WHEEL_STEP)
        elif k in keys("R", "F5"):
            self.on_refresh(sender, e)
        else:
            return
        e.Handled = True

    def on_canvas_size(self, sender, e):
        if self.need_fit and self.page is not None:
            self.zoom_fit()
        else:
            self._schedule_render()

    def _schedule_render(self):
        self.render_due = time.time() + RENDER_DELAY_S
        self._update_zoom_label()
        self.canvas.Invalidate()

    def _update_zoom_label(self):
        r = self.real_zoom()
        self.lbl_zoom.Text = f"{r * 100:.0f}%" if r >= 0.1 else f"{r * 100:.1f}%"
        on = abs(r - 1.0) < 0.005
        self.lbl_zoom.TextColor = color("success" if on else "text")

    def on_timer(self, sender, e):
        if not self.is_open or self.busy:
            return
        now = time.time()
        if self._layouts_stale:
            self._fill_layouts()
            self.need_fit = True
            self.refresh_pdf("document opened")
            return
        if self.doc_dirty_at and now - self.doc_dirty_at > AUTO_QUIET_S and self.chk_auto.Checked:
            try:
                in_cmd = Rhino.Commands.Command.InCommand()
            except Exception:
                in_cmd = False
            if not in_cmd and self.drag is None:
                self.refresh_pdf("document changed (Auto)")
                return
        if self.render_due and now >= self.render_due and self.drag is None:
            self.render_now()

    # ---- rendering ----
    def _set_tile(self, tile):
        old = self.tile
        self.tile = tile
        if old is not None and old is not tile:
            try:
                old.image.Dispose()
            except Exception:
                pass

    def render_now(self):
        self.render_due = 0.0
        if self.page is None:
            self.canvas.Invalidate()
            return
        self._update_screen()
        if self.need_fit:
            w, h = self._canvas_px()
            if w > 10 and h > 10:
                self.view.fit(self.page_mm[0], self.page_mm[1], w, h)
                self.need_fit = False
        w, h = self._canvas_px()
        rect = visible_pixel_rect(self.view, self.page_mm[0], self.page_mm[1], w, h)
        self._update_zoom_label()
        if rect is None:
            self.canvas.Invalidate()
            return
        t0 = time.time()
        try:
            png, x0, y0, pw, ph = render_png(self.page, self.view.z, rect)
            t1 = time.time()
            bmp = png_to_bitmap(png)
        except ViewError as ex:
            self.report.add(str(ex), "warning")
            log("SKIP", f"render: {ex}")
            return
        ms = (time.time() - t0) * 1000.0
        self._set_tile(Tile(bmp, self.view.z, x0, y0, pw, ph, ms))
        log("VIEW", f"render z={self.view.z:.4f} px/mm real={self.real_zoom() * 100:.1f}% rect={rect} -> "
                    f"{pw}x{ph} px at ({x0},{y0}) | raster {(t1 - t0) * 1000:.0f} ms, "
                    f"bitmap {(time.time() - t1) * 1000:.0f} ms, png {len(png) / 1024:.0f} KB")
        sc_txt = f"{self.screen.ppi:.1f} ppi" if self.screen else ""
        self.lbl_status.Text = (f"Sheet {self.page_mm[0]:.0f} x {self.page_mm[1]:.0f} mm  |  "
                                f"PDF {getattr(self, 'pdf_seconds', 0):.1f} s  |  render {ms:.0f} ms  |  {sc_txt}")
        self.canvas.Invalidate()

    def on_paint(self, sender, e):
        g = e.Graphics
        s = self.screen.scale if self.screen else self._eto_scale()
        lw, lh = float(self.canvas.Width), float(self.canvas.Height)
        g.FillRectangle(color("canvas"), 0.0, 0.0, lw, lh)
        if self.page is None:
            msg = "Printing layout..." if self.busy else "No layout to show. Pick a layout, then Refresh."
            tw, th = _text_size(font(), msg)
            g.DrawText(font(), color("text_muted"), (lw - tw) / 2.0, (lh - th) / 2.0, msg)
            return
        v = self.view
        pw, ph = self.page_mm[0] * v.z, self.page_mm[1] * v.z
        # sheet + shadow (device px -> logical units)
        g.FillRectangle(color("paper_shadow"), (v.ox + 4) / s, (v.oy + 4) / s, pw / s, ph / s)
        g.FillRectangle(color("paper"), v.ox / s, v.oy / s, pw / s, ph / s)
        t = self.tile
        if t is not None:
            k = v.z / t.z
            x, y = v.ox + t.x0 * k, v.oy + t.y0 * k
            exact = abs(k - 1.0) < 1e-9
            try:
                g.ImageInterpolation = getattr(drawing.ImageInterpolation, "None" if exact else "Low")
            except Exception:
                pass
            g.DrawImage(t.image, x / s, y / s, t.w * k / s, t.h * k / s)


# =============================================================================
# ENTRY POINT
# =============================================================================
def main():
    # Rhino / Python versions are checked by the ENVIRONMENT GUARD at the top of the file.
    existing = sc.sticky.get(STICKY_KEY)
    if existing is not None and getattr(existing, "is_open", False):
        if getattr(existing, "version", None) == __version__:
            existing.bring_to_front()
            return
        existing.close()
    if not ensure_pymupdf():
        return
    ui = PluginUI()
    sc.sticky[STICKY_KEY] = ui
    ui.show()


try:
    main()
except Exception:
    log_exc("main")
    Rhino.RhinoApp.WriteLine(f"{PLUGIN_NAME}: startup error, see {LOG_DIR}")
