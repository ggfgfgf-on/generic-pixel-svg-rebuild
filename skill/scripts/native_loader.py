"""Single source of truth for the optional native DLLs (trace_core / palette_core).

Both `vectorize.py` and `paint_process.py` import this module instead of each
carrying its own ctypes boilerplate: one place to fix a signature, one place to
handle a missing/incompatible DLL.

Every entry point returns None when the DLL is absent, disabled, or errors out;
callers must then fall back to the pure-Python potrace/median-cut path.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

_NATIVE_DIR = Path(__file__).resolve().parent / "native"

_TRACE = None
_TRACE_TRIED = False
_PALETTE = None
_PALETTE_TRIED = False


def _disabled(env_name: str) -> bool:
    return os.environ.get(env_name, "1").lower() in ("0", "false", "off")


def _resolve(env_name: str, filename: str):
    candidates = []
    if os.environ.get(env_name):
        candidates.append(Path(os.environ[env_name]))
    candidates.append(_NATIVE_DIR / filename)
    return next((p for p in candidates if p.exists()), None)


def _bind_trace(lib):
    fn = lib.trace_mask_ex
    fn.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int, ctypes.c_int,
                   ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
                   ctypes.c_int, ctypes.c_double, ctypes.c_int,
                   ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    return fn


def trace_dll():
    """(lib, fn, buf) for trace_mask_ex, or None."""
    global _TRACE, _TRACE_TRIED
    if _TRACE_TRIED:
        return _TRACE
    _TRACE_TRIED = True
    if _disabled("PAINT_NATIVE_TRACE"):
        return None
    lib_path = _resolve("PAINT_TRACE_DLL", "trace_core.dll")
    if lib_path is None:
        return None
    try:
        lib = ctypes.CDLL(str(lib_path))
        _TRACE = (lib, _bind_trace(lib), ctypes.create_string_buffer(8_000_000))
    except Exception as exc:                              # wrong arch, no MSVC runtime, ...
        print("native trace unavailable (%s), using pure Python" % exc, file=os.sys.stderr)
        _TRACE = None
    return _TRACE


def trace_mask(mask, sx, sy, ox=0.0, oy=0.0, turdsize=1, alphamax=1.0, prec=2):
    """Trace a boolean mask with the DLL. Returns an SVG path string, or None.

    The buffer is grown when the DLL reports -4 (output truncated), so a very
    detailed mask degrades in speed, never in correctness.
    """
    global _TRACE
    native = trace_dll()
    if native is None:
        return None
    import numpy as np
    lib, fn, buf = native
    m = np.ascontiguousarray(mask, dtype=np.uint8)
    for _ in range(3):
        r = fn(m.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)), m.shape[1], m.shape[0],
               float(sx), float(sy), float(ox), float(oy),
               int(turdsize), float(alphamax), int(prec), buf, len(buf))
        if r >= 0:
            return buf.value.decode("ascii")
        if r != -4:                                       # -4 = buffer too small
            break
        buf = ctypes.create_string_buffer(len(buf) * 4)
        _TRACE = (lib, fn, buf)
    print("native trace failed, using pure Python", file=os.sys.stderr)
    return None


def palette_dll():
    """(lib, fn) for palette_median_cut, or None."""
    global _PALETTE, _PALETTE_TRIED
    if _PALETTE_TRIED:
        return _PALETTE
    _PALETTE_TRIED = True
    if _disabled("PAINT_PALETTE_NATIVE"):
        return None
    lib_path = _resolve("PAINT_PALETTE_DLL", "palette_core.dll")
    if lib_path is None:
        return None
    try:
        lib = ctypes.CDLL(str(lib_path))
        fn = lib.palette_median_cut
        fn.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int, ctypes.c_int,
                       ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        fn.restype = ctypes.c_int
        _PALETTE = (lib, fn)
    except Exception as exc:
        print("native palette unavailable (%s)" % exc, file=os.sys.stderr)
        _PALETTE = None
    return _PALETTE


def palette_centers(px, k):
    """k x 3 float32 palette from every pixel, or None."""
    native = palette_dll()
    if native is None:
        return None
    import numpy as np
    lib, fn = native
    p = np.ascontiguousarray(px, dtype=np.uint8)
    out = np.zeros(int(k) * 3, dtype=np.float32)
    n = fn(p.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)), len(p), int(k),
           out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(out))
    if n <= 0:
        return None
    return out[:n * 3].reshape(-1, 3).copy()
