/* Native full-pixel median-cut palette quantizer. No potrace dependency. */
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <numeric>
#include <vector>

namespace {

struct Box {
    uint32_t lo, hi;
    int rmin, rmax, gmin, gmax, bmin, bmax;
    double score;
};

static inline void stats(const uint8_t* rgb, const std::vector<uint32_t>& idx,
                         Box& b) {
    int rmin = 255, rmax = 0, gmin = 255, gmax = 0, bmin = 255, bmax = 0;
    for (uint32_t i = b.lo; i < b.hi; ++i) {
        const uint8_t* p = rgb + (size_t)idx[i] * 3;
        int r = p[0], g = p[1], bl = p[2];
        if (r < rmin) rmin = r; if (r > rmax) rmax = r;
        if (g < gmin) gmin = g; if (g > gmax) gmax = g;
        if (bl < bmin) bmin = bl; if (bl > bmax) bmax = bl;
    }
    b.rmin = rmin; b.rmax = rmax;
    b.gmin = gmin; b.gmax = gmax;
    b.bmin = bmin; b.bmax = bmax;
    int range = std::max(std::max(rmax - rmin, gmax - gmin), bmax - bmin);
    b.score = (double)range * (double)(b.hi - b.lo);
}

static inline int widest(const Box& b) {
    int rr = b.rmax - b.rmin, gr = b.gmax - b.gmin, br = b.bmax - b.bmin;
    if (rr >= gr && rr >= br) return 0;
    return gr >= br ? 1 : 2;
}

} // namespace

extern "C" __declspec(dllexport)
int palette_median_cut(const unsigned char* rgb, int n, int k,
                       float* centers, int cap) {
    if (!rgb || n <= 0 || k <= 0 || !centers || cap < k * 3) return -1;
    std::vector<uint32_t> idx((size_t)n);
    std::iota(idx.begin(), idx.end(), 0u);

    std::vector<Box> boxes;
    boxes.reserve((size_t)k);
    Box root{0u, (uint32_t)n, 0, 0, 0, 0, 0, 0, 0.0};
    stats(rgb, idx, root);
    boxes.push_back(root);

    while ((int)boxes.size() < k) {
        int best = -1;
        double best_score = -1.0;
        for (size_t i = 0; i < boxes.size(); ++i) {
            if (boxes[i].hi - boxes[i].lo < 2) continue;
            if (boxes[i].score > best_score) {
                best_score = boxes[i].score;
                best = (int)i;
            }
        }
        if (best < 0) break;
        Box b = boxes[(size_t)best];
        int ch = widest(b);
        uint32_t mid = b.lo + (b.hi - b.lo) / 2;
        std::nth_element(idx.begin() + b.lo, idx.begin() + mid, idx.begin() + b.hi,
            [&](uint32_t a, uint32_t c) {
                int va = rgb[(size_t)a * 3 + ch];
                int vc = rgb[(size_t)c * 3 + ch];
                return va != vc ? va < vc : a < c;
            });
        Box left{b.lo, mid, 0, 0, 0, 0, 0, 0, 0.0};
        Box right{mid, b.hi, 0, 0, 0, 0, 0, 0, 0.0};
        stats(rgb, idx, left);
        stats(rgb, idx, right);
        boxes[(size_t)best] = left;
        boxes.push_back(right);
    }

    const int count = (int)boxes.size();
    for (int i = 0; i < count; ++i) {
        const Box& b = boxes[(size_t)i];
        double r = 0, g = 0, bl = 0;
        for (uint32_t j = b.lo; j < b.hi; ++j) {
            const uint8_t* p = rgb + (size_t)idx[j] * 3;
            r += p[0]; g += p[1]; bl += p[2];
        }
        double inv = 1.0 / (double)std::max<uint32_t>(1u, b.hi - b.lo);
        centers[(size_t)i * 3 + 0] = (float)(r * inv);
        centers[(size_t)i * 3 + 1] = (float)(g * inv);
        centers[(size_t)i * 3 + 2] = (float)(bl * inv);
    }
    return count;
}
