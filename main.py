#!/usr/bin/env python3
"""VELMARA — a travel poster for a place that does not exist.

Draws a 1024x1024 travel poster procedurally with pure stdlib Python:
a cliff-top lighthouse town above a lavender sea at dusk. The poster's
title, VELMARA, is drawn in large hand-built vector lettering.

Writes one PNG to $MAKEFUN_OUTPUT_DIR/entry.png (default: ./output).
Deterministic: same bytes on every run. No network, no external assets.
"""

import os
import struct
import zlib

W, H = 1024, 1024

# ---------------------------------------------------------------- palette
# Dusk sky: deep violet at the top, rose in the middle, warm amber at the
# horizon. Sea: lavender with a warm reflection under the sun. Cliffs and
# town: deep indigo silhouettes with warm lit windows.
SKY_TOP = (38, 22, 74)
SKY_MID = (122, 62, 118)
SKY_LOW = (214, 116, 92)
HORIZON = (250, 178, 110)
SEA_TOP = (150, 118, 178)
SEA_MID = (108, 88, 150)
SEA_LOW = (58, 48, 96)
CLIFF = (34, 24, 58)
CLIFF_LIT = (58, 40, 88)
TOWN = (26, 18, 48)
CREAM = (250, 236, 205)
GOLD = (255, 196, 110)
WINDOW = (255, 205, 120)

# ---------------------------------------------------------------- helpers


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return (int(lerp(c1[0], c2[0], t)),
            int(lerp(c1[1], c2[1], t)),
            int(lerp(c1[2], c2[2], t)))


def clamp255(v):
    return 0 if v < 0 else (255 if v > 255 else int(v))


def blend(dst, src, alpha):
    """Alpha-blend src colour over dst colour (alpha 0..1)."""
    ia = 1.0 - alpha
    return (int(dst[0] * ia + src[0] * alpha),
            int(dst[1] * ia + src[1] * alpha),
            int(dst[2] * ia + src[2] * alpha))


def det_rand(x, y, seed=0):
    """Deterministic pseudo-random in [0, 1) from integer coords."""
    n = (x * 374761393 + y * 668265263 + seed * 974634211) & 0xFFFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967296.0


# 4x4 ordered Bayer matrix, for a fine dither that keeps the PNG small.
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))

# ---------------------------------------------------------------- canvas


class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [bytearray(w * 3) for _ in range(h)]

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = x * 3
            row = self.px[y]
            row[i], row[i + 1], row[i + 2] = c[0], c[1], c[2]

    def blend_px(self, x, y, c, alpha):
        if 0 <= x < self.w and 0 <= y < self.h and alpha > 0:
            i = x * 3
            row = self.px[y]
            ia = 1.0 - alpha
            row[i] = int(row[i] * ia + c[0] * alpha)
            row[i + 1] = int(row[i + 1] * ia + c[1] * alpha)
            row[i + 2] = int(row[i + 2] * ia + c[2] * alpha)

    def fill_rect(self, x0, y0, x1, y1, c):
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(self.w, x1)
        y1 = min(self.h, y1)
        for y in range(y0, y1):
            row = self.px[y]
            for x in range(x0, x1):
                i = x * 3
                row[i], row[i + 1], row[i + 2] = c[0], c[1], c[2]

    def disc(self, cx, cy, r, c, alpha=1.0):
        r2 = r * r
        for y in range(max(0, cy - r), min(self.h, cy + r + 1)):
            dy = y - cy
            dx = int((r2 - dy * dy) ** 0.5)
            for x in range(max(0, cx - dx), min(self.w, cx + dx + 1)):
                if alpha >= 1.0:
                    self.set(x, y, c)
                else:
                    self.blend_px(x, y, c, alpha)

    def poly(self, pts, c):
        """Fill a polygon given as a list of (x, y) vertices."""
        ys = [p[1] for p in pts]
        for y in range(max(0, min(ys)), min(self.h, max(ys) + 1)):
            xs = []
            n = len(pts)
            for i in range(n):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % n]
                if (y0 <= y < y1) or (y1 <= y < y0):
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for j in range(0, len(xs) - 1, 2):
                for x in range(max(0, int(xs[j])), min(self.w, int(xs[j + 1]) + 1)):
                    self.set(x, y, c)

# ---------------------------------------------------------------- lettering
# Bold 5x7 bitmap capitals, drawn by hand. Only VELMARA is used, but the
# full alphabet is defined so the title stays honest vector lettering.
FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
}


def draw_text(cv, text, x, y, scale, colour, outline=None):
    """Draw text with the bitmap font; returns the total pixel width."""
    cx = x
    for ch in text:
        glyph = FONT.get(ch)
        if glyph is None:
            cx += 6 * scale
            continue
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue
                bx = cx + gx * scale
                by = y + gy * scale
                if outline is not None:
                    for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2),
                                   (-2, -2), (2, -2), (-2, 2), (2, 2)):
                        cv.fill_rect(bx + ox, by + oy,
                                     bx + scale + ox, by + scale + oy,
                                     outline)
                cv.fill_rect(bx, by, bx + scale, by + scale, colour)
        cx += 6 * scale
    return cx - x - scale


def text_width(text, scale):
    return len(text) * 6 * scale - scale


# ---------------------------------------------------------------- sky & sea


def draw_sky(cv):
    horizon = 560
    for y in range(0, horizon):
        t = y / horizon
        if t < 0.45:
            c = mix(SKY_TOP, SKY_MID, t / 0.45)
        elif t < 0.85:
            c = mix(SKY_MID, SKY_LOW, (t - 0.45) / 0.40)
        else:
            c = mix(SKY_LOW, HORIZON, (t - 0.85) / 0.15)
        row = cv.px[y]
        for x in range(W):
            d = BAYER[y % 4][x % 4] - 7  # +/-7 dither, invisible, kills banding
            i = x * 3
            row[i] = clamp255(c[0] + d)
            row[i + 1] = clamp255(c[1] + d)
            row[i + 2] = clamp255(c[2] + d)
    # Stars fading out toward the horizon.
    for _ in range(140):
        sx = int(det_rand(_, 1, 7) * W)
        sy = int(det_rand(_, 2, 7) * 330)
        if det_rand(sx, sy, 3) < 0.75:
            a = 0.9 * (1.0 - sy / 380.0)
            cv.blend_px(sx, sy, (255, 244, 220), max(0.0, a))
            if det_rand(sx, sy, 5) > 0.85:
                cv.blend_px(sx + 1, sy, (255, 244, 220), max(0.0, a * 0.6))


def draw_sun(cv):
    sx, sy = 330, 470
    # Wide warm glow built from many faint rings so no hard edge shows,
    # then the low sun disc itself.
    for r in (220, 195, 170, 145, 120, 98, 78):
        cv.disc(sx, sy, r, (255, 170, 105), 0.06)
    cv.disc(sx, sy, 62, (255, 214, 140))
    cv.disc(sx, sy, 50, (255, 232, 178))


def draw_sea(cv):
    horizon = 560
    for y in range(horizon, H):
        t = (y - horizon) / (H - horizon)
        if t < 0.5:
            c = mix(SEA_TOP, SEA_MID, t / 0.5)
        else:
            c = mix(SEA_MID, SEA_LOW, (t - 0.5) / 0.5)
        row = cv.px[y]
        for x in range(W):
            d = BAYER[y % 4][x % 4] - 7
            i = x * 3
            row[i] = clamp255(c[0] + d)
            row[i + 1] = clamp255(c[1] + d)
            row[i + 2] = clamp255(c[2] + d)
    # Sun reflection: a shimmering column under the sun.
    for y in range(horizon + 6, H - 120):
        depth = (y - horizon) / (H - horizon)
        half = int(26 + depth * 90)
        for x in range(330 - half, 330 + half + 1):
            wob = det_rand(x, y, 11)
            if wob > 0.55:
                a = (1.0 - depth) * 0.55 * (wob - 0.55) * 2.2
                cv.blend_px(x, y, (255, 196, 120), min(0.6, a))
    # Long horizontal wave strokes, sparse and calm.
    for k in range(46):
        wy = horizon + 24 + int(det_rand(k, 9, 21) * (H - horizon - 160))
        wx = int(det_rand(k, 10, 21) * W)
        wl = 26 + int(det_rand(k, 11, 21) * 90)
        a = 0.10 + det_rand(k, 12, 21) * 0.16
        for x in range(wx, min(W, wx + wl)):
            cv.blend_px(x, wy, (216, 190, 232), a)


# ---------------------------------------------------------------- the town


def draw_cliff_and_town(cv):
    # Main cliff mass on the right, rising from the sea to the lighthouse.
    cv.poly([(560, 1024), (640, 700), (700, 590), (790, 520),
             (880, 500), (1024, 520), (1024, 1024)], CLIFF)
    # A lit rim where the last sun catches the cliff edge.
    cv.poly([(640, 700), (700, 590), (790, 520), (800, 528),
             (712, 596), (652, 700)], CLIFF_LIT)
    # Rocky outcrop on the left, low in the sea.
    cv.poly([(0, 1024), (0, 880), (60, 830), (150, 850), (210, 920),
             (240, 1024)], CLIFF)


def draw_house(cv, x, y, w, h, roof_h):
    cv.fill_rect(x, y, x + w, y + h, TOWN)
    cv.poly([(x - 3, y), (x + w // 2, y - roof_h), (x + w + 3, y)], TOWN)
    # Two warm windows.
    for wx in (x + 5, x + w - 13):
        if w > 26:
            cv.fill_rect(wx, y + 8, wx + 8, y + 16, WINDOW)


def draw_lighthouse(cv):
    # Tower, gallery and lamp room on the cliff top.
    tx, base_y = 800, 520
    cv.poly([(tx - 26, base_y), (tx - 18, 356), (tx + 18, 356),
             (tx + 26, base_y)], (244, 232, 208))
    cv.poly([(tx - 26, base_y), (tx - 22, 440), (tx + 22, 440),
             (tx + 26, base_y)], (196, 52, 68))
    cv.fill_rect(tx - 30, 348, tx + 30, 356, (30, 20, 52))
    cv.fill_rect(tx - 16, 322, tx + 16, 348, (30, 20, 52))
    cv.disc(tx, 335, 12, (255, 226, 150))
    cv.poly([(tx - 20, 322), (tx, 300), (tx + 20, 322)], (30, 20, 52))
    # The beam, sweeping out over the lavender sea to the left.
    # Wedge with apex at the lamp: top edge (tx-14, 330) -> (120, 470),
    # bottom edge (tx-14, 344) -> (120, 560). Scan it row by row.
    for y in range(330, 561):
        if y <= 344:
            xl = int(lerp(tx - 14, 120, (y - 330) / 140.0))
            xr = tx - 14
        elif y <= 470:
            xl = int(lerp(tx - 14, 120, (y - 330) / 140.0))
            xr = int(lerp(tx - 14, 120, (y - 344) / 216.0))
        else:
            xl = 120
            xr = int(lerp(tx - 14, 120, (y - 344) / 216.0))
        fade = 1.0 - (y - 330) / 231.0
        for x in range(max(0, xl), min(W, xr)):
            cv.blend_px(x, y, (255, 226, 150), 0.16 * fade)
    # Lamp glow halo.
    cv.disc(tx, 335, 46, (255, 214, 140), 0.10)
    cv.disc(tx, 335, 26, (255, 226, 150), 0.16)


def draw_town(cv):
    # A small row of houses along the cliff crest, left of the lighthouse.
    # The crest runs (640,700) -> (700,590) -> (790,520) -> (880,500);
    # each house base sits a few pixels into the crest line.
    crest = [(640, 700), (700, 590), (790, 520), (880, 500)]

    def crest_y(cx):
        for i in range(len(crest) - 1):
            x0, y0 = crest[i]
            x1, y1 = crest[i + 1]
            if x0 <= cx <= x1:
                return int(lerp(y0, y1, (cx - x0) / (x1 - x0)))
        return crest[-1][1]

    for (x, w, h, rh) in [(646, 42, 58, 20), (694, 44, 60, 22),
                          (740, 40, 56, 18)]:
        base = crest_y(x + w // 2) + 4
        draw_house(cv, x, base - h, w, h, rh)
    # A few gulls in the evening sky.
    for (bx, by, s) in [(220, 240, 14), (300, 190, 11), (520, 150, 12),
                        (430, 280, 9)]:
        for dx in range(-s, s + 1):
            yy = by + int(abs(dx) * 0.35)
            cv.blend_px(bx + dx, yy, (40, 26, 66), 0.85)


# ---------------------------------------------------------------- title


def draw_title(cv):
    name = "VELMARA"
    scale = 15
    tw = text_width(name, scale)
    x = (W - tw) // 2
    y = 852
    # A soft dark ribbon behind the letters so they stay legible over sea.
    # Lighter at the edges, darker behind the letters.
    for yy in range(y - 26, y + 7 * scale + 30):
        a = 0.55 if yy < y - 8 or yy > y + 7 * scale + 12 else 0.72
        row = cv.px[yy]
        for xx in range(W):
            i = xx * 3
            row[i] = int(row[i] * (1 - a) + 24 * a)
            row[i + 1] = int(row[i + 1] * (1 - a) + 16 * a)
            row[i + 2] = int(row[i + 2] * (1 - a) + 48 * a)
    # Thin gold rules above and below the name.
    cv.fill_rect(x - 60, y - 14, x + tw + 60, y - 10, GOLD)
    cv.fill_rect(x - 60, y + 7 * scale + 16, x + tw + 60, y + 7 * scale + 20,
                 GOLD)
    draw_text(cv, name, x, y, scale, CREAM, outline=(24, 16, 48))

# ---------------------------------------------------------------- PNG output


def write_png(cv, path):
    raw = bytearray()
    for row in cv.px:
        raw.append(0)  # filter: none
        raw += row
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(kind, data):
        out = struct.pack(">I", len(data)) + kind + data
        out += struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return out

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", compressed)
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def build():
    cv = Canvas(W, H)
    draw_sky(cv)
    draw_sun(cv)
    draw_sea(cv)
    draw_cliff_and_town(cv)
    draw_town(cv)
    draw_lighthouse(cv)
    draw_title(cv)
    return cv


def main():
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "output")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "entry.png")
    cv = build()
    size = write_png(cv, path)
    print(f"wrote {path}: {W}x{H}, {size} bytes")


if __name__ == "__main__":
    main()
