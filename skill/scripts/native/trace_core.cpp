/* Thin C ABI wrapper around potrace 1.16 (GPL). See README.md and COPYING. */
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "potracelib.h"
#include "bitmap.h"

static void append_path(std::string &out, const potrace_curve_t &curve,
                        double sx, double sy, double ox, double oy, int prec) {
    int n = curve.n;
    if (n <= 0) return;
    auto pt = [&](const potrace_dpoint_t &p) {
        char buf[64];
        std::snprintf(buf, sizeof(buf), "%.*f %.*f", prec, (p.x + ox) * sx, prec, (p.y + oy) * sy);
        return std::string(buf);
    };
    out += "M";
    out += pt(curve.c[n - 1][2]);
    for (int i = 0; i < n; ++i) {
        const potrace_dpoint_t *c = curve.c[i];
        if (curve.tag[i] == POTRACE_CORNER) {
            out += "L"; out += pt(c[1]);
            out += "L"; out += pt(c[2]);
        } else {
            out += "C"; out += pt(c[0]); out += " ";
            out += pt(c[1]); out += " "; out += pt(c[2]);
        }
    }
    out += "Z";
}

static int trace_impl(const unsigned char *mask, int w, int h,
                     double sx, double sy, double ox, double oy,
                     int turdsize, double alphamax, int prec,
                     char *out, int cap) {
    if (!mask || w <= 0 || h <= 0 || !out || cap <= 0) return -1;
    int dy = (w + BM_WORDBITS - 1) / BM_WORDBITS;
    size_t words = (size_t)dy * (size_t)h;
    potrace_word *map = (potrace_word*)std::calloc(words, sizeof(potrace_word));
    if (!map) return -2;
    potrace_bitmap_t bm;
    bm.w = w; bm.h = h; bm.dy = dy; bm.map = map;
    for (int y = 0; y < h; ++y) {
        const unsigned char *row = mask + (size_t)y * w;
        for (int x = 0; x < w; ++x) {
            if (row[x]) BM_PUT(&bm, x, y, 1);
        }
    }
    potrace_param_t *param = potrace_param_default();
    if (!param) { std::free(map); return -2; }
    param->turdsize = turdsize;
    param->alphamax = alphamax;
    param->opticurve = 1;
    param->opttolerance = 0.2;
    potrace_state_t *st = potrace_trace(param, &bm);
    std::free(map);
    potrace_param_free(param);
    if (!st || st->status != POTRACE_STATUS_OK) {
        if (st) potrace_state_free(st);
        return -3;
    }
    std::string result;
    result.reserve(256);
    for (potrace_path_t *p = st->plist; p; p = p->next) {
        append_path(result, p->curve, sx, sy, ox, oy, prec);
    }
    potrace_state_free(st);
    if ((int)result.size() + 1 > cap) return -4;
    std::memcpy(out, result.data(), result.size());
    out[result.size()] = 0;
    return (int)result.size();
}


extern "C" __declspec(dllexport)
int trace_mask_bytes(const unsigned char *mask, int w, int h,
                     double sx, double sy, double ox, double oy,
                     char *out, int cap) {
    return trace_impl(mask, w, h, sx, sy, ox, oy, 1, 1.0, 1, out, cap);
}

extern "C" __declspec(dllexport)
int trace_mask_ex(const unsigned char *mask, int w, int h,
                  double sx, double sy, double ox, double oy,
                  int turdsize, double alphamax, int prec,
                  char *out, int cap) {
    return trace_impl(mask, w, h, sx, sy, ox, oy, turdsize, alphamax, prec, out, cap);
}
