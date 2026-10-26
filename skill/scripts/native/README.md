# Native tracing core

Windows x64 DLL used by `paint_process.py` to replace the pure-Python `potrace.py`
implementation. It links potrace 1.16 (GPL) and exposes one C ABI:

```c
int trace_mask_bytes(const unsigned char *mask, int w, int h,
                     double sx, double sy, double ox, double oy,
                     char *out, int cap);
```

## Loading

`paint_process.py` loads this DLL automatically when it exists:

```text
scripts/native/trace_core.dll
```

Override or disable:

```powershell
$env:PAINT_TRACE_DLL = "D:\path\trace_core.dll"   # custom DLL
$env:PAINT_NATIVE_TRACE = "0"                       # force pure Python
```

If the DLL is missing, fails to load, or returns an error, the script falls back
to the pure-Python tracer. SVG output is unchanged.

## Measured

Same 438x952 / 256-colour render on this machine:

| backend | time |
|---|---:|
| pure Python `potrace.py` | 40.4 s |
| native DLL | 2.61 s |

Full 876x1904 / 256-colour render: **10.5 s** (formerly ~2 min).

## Rebuild

Requires LLVM (clang/clang++) and Visual Studio Build Tools (MSVC linker).

1. Download potrace 1.16 source and extract it.
2. Compile the core C files with `-DHAVE_CONFIG_H`:
   `bbox.c`, `curve.c`, `decompose.c`, `potracelib.c`, `trace.c`.
3. Compile `trace_core.cpp` with clang++ and link the objects into
   `trace_core.dll`:

```bat
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set PATH=C:\Program Files\LLVM\bin;%PATH%
clang++ -O3 -std=c++17 -shared -I"<potrace-src>" trace_core.cpp ^
  bbox.o curve.o decompose.o potracelib.o trace.o -o trace_core.dll
```

## Palette quantizer

`palette_core.dll` is a separate, plain C++17 DLL with no potrace dependency. It
exposes the full-pixel median-cut path used by `vectorize.py --full-palette`:

```c
int palette_median_cut(const unsigned char *rgb, int n, int k,
                       float *centers, int cap);
```

`rgb` is packed RGB8, `n` is the pixel count, `k` the requested palette size,
`centers` receives `k * 3` floats. The function returns the actual number of
centers, or a negative error code. Build it with `build_palette.bat` (vcvars64 +
LLVM clang++). Override with `PAINT_PALETTE_DLL`; disable with
`PAINT_PALETTE_NATIVE=0`.

## License

potrace is Copyright (C) 2001-2019 Peter Selinger and is distributed under the
GNU General Public License. See `COPYING` in this directory. The wrapper is a
thin ABI adapter; the tracing algorithm and generated curves come from potrace.
