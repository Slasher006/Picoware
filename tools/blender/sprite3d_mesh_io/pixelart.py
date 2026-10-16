# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic RGB332 pixel styling, independent of Blender."""
# Fixed palette shared by every view and pose. Values are already device colors.
PALETTE = (0, 37, 73, 146, 219, 255, 224, 236, 252, 104, 80, 20, 28, 121, 11, 151)


def rgb(code):
    return ((code >> 5) * 255 // 7, ((code >> 2) & 7) * 255 // 7, (code & 3) * 255 // 3)


def nearest(code):
    r, g, b = rgb(code)
    return min(PALETTE, key=lambda c: sum(w*(a-v)**2 for w,a,v in zip((2,3,1),(r,g,b),rgb(c))))


LOOKUP = bytes(nearest(c) for c in range(256))


def stylize(pixels, mask, width, height, outline=False):
    if len(pixels) != width*height or len(mask) != (width*height+7)//8:
        raise ValueError('Invalid pixel or mask length')
    result = bytearray(len(pixels)); output_mask = bytearray(mask)
    opaque = lambda k: bool(mask[k//8] & (128 >> (k%8)))
    for k, color in enumerate(pixels):
        if opaque(k): result[k] = LOOKUP[color]
        elif outline:
            x,y = k%width,k//width
            neighbors = [ny*width+nx for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)) if 0<=nx<width and 0<=ny<height]
            if any(opaque(n) for n in neighbors): output_mask[k//8] |= 128 >> (k%8)
    return bytes(result), bytes(output_mask)
