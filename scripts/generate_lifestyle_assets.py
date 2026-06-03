"""Generate lifestyle PNG assets for all brands.

Run once to (re)create all placeholder lifestyle elements in data/lifestyle/.
Each PNG is 400x400 RGBA with transparent background — composited onto thumbnails.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "data" / "lifestyle"


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _save(img: Image.Image, brand: str, name: str):
    dest = OUT / brand
    dest.mkdir(parents=True, exist_ok=True)
    img.save(dest / f"{name}.png")
    print(f"  {brand}/{name}.png")


# ---------------------------------------------------------------------------
# ZOOVERA — pets
# ---------------------------------------------------------------------------

def _zoovera_dog():
    img, d = _canvas()
    # Body ellipse (light brown)
    d.ellipse([80, 180, 280, 340], fill=(205, 133, 63, 220))
    # Head
    d.ellipse([200, 100, 340, 240], fill=(205, 133, 63, 220))
    # Ears
    d.ellipse([190, 80, 250, 160], fill=(160, 90, 30, 220))
    d.ellipse([295, 80, 355, 160], fill=(160, 90, 30, 220))
    # Eye
    d.ellipse([260, 140, 285, 165], fill=(30, 20, 10, 240))
    d.ellipse([268, 148, 278, 158], fill=(255, 255, 255, 180))
    # Nose
    d.ellipse([275, 190, 305, 215], fill=(60, 30, 10, 240))
    # Tail
    d.arc([20, 200, 120, 320], start=200, end=340, fill=(205, 133, 63, 200), width=18)
    # Legs
    for x in [110, 170, 215, 255]:
        d.rounded_rectangle([x, 310, x + 35, 380], radius=10, fill=(185, 110, 45, 210))
    _save(img, "zoovera", "dog_sitting")


def _zoovera_cat():
    img, d = _canvas()
    # Body
    d.ellipse([90, 180, 290, 360], fill=(150, 150, 160, 220))
    # Head
    d.ellipse([150, 80, 310, 230], fill=(150, 150, 160, 220))
    # Ears (triangles)
    d.polygon([(155, 110), (185, 40), (215, 110)], fill=(120, 120, 130, 230))
    d.polygon([(265, 110), (295, 40), (320, 110)], fill=(120, 120, 130, 230))
    # Eyes
    d.ellipse([180, 130, 215, 165], fill=(50, 180, 80, 240))
    d.ellipse([255, 130, 290, 165], fill=(50, 180, 80, 240))
    d.ellipse([194, 144, 202, 152], fill=(10, 10, 10, 255))
    d.ellipse([269, 144, 277, 152], fill=(10, 10, 10, 255))
    # Nose & mouth
    d.polygon([(228, 178), (240, 190), (252, 178)], fill=(220, 100, 120, 240))
    # Whiskers
    for y in [188, 198]:
        d.line([(160, y), (220, y + 2)], fill=(80, 80, 80, 160), width=2)
        d.line([(260, y), (320, y + 2)], fill=(80, 80, 80, 160), width=2)
    # Tail
    d.arc([20, 250, 140, 380], start=250, end=40, fill=(140, 140, 150, 200), width=16)
    _save(img, "zoovera", "cat_sitting")


def _zoovera_paw():
    img, d = _canvas()
    # Main paw pad
    d.ellipse([120, 180, 280, 320], fill=(220, 160, 130, 230))
    # Toe pads
    for cx, cy in [(140, 140), (195, 110), (250, 110), (305, 140)]:
        d.ellipse([cx - 28, cy - 28, cx + 28, cy + 28], fill=(220, 160, 130, 230))
    # Dark outlines
    d.ellipse([122, 182, 278, 318], outline=(180, 110, 80, 150), width=3)
    _save(img, "zoovera", "paw_print")


def _zoovera_bone():
    img, d = _canvas()
    c = (210, 180, 150, 230)
    # Shaft
    d.rounded_rectangle([120, 175, 280, 225], radius=20, fill=c)
    # End balls
    for cx, cy in [(100, 150), (100, 250), (300, 150), (300, 250)]:
        d.ellipse([cx - 35, cy - 35, cx + 35, cy + 35], fill=c)
    _save(img, "zoovera", "bone")


def _zoovera_fish():
    img, d = _canvas()
    # Body
    d.ellipse([80, 150, 300, 260], fill=(70, 140, 200, 220))
    # Tail
    d.polygon([(300, 200), (360, 140), (360, 260)], fill=(50, 110, 180, 220))
    # Eye
    d.ellipse([105, 165, 135, 195], fill=(255, 255, 255, 240))
    d.ellipse([112, 172, 128, 188], fill=(10, 10, 50, 255))
    # Fin
    d.polygon([(160, 150), (210, 90), (260, 150)], fill=(50, 110, 180, 200))
    _save(img, "zoovera", "fish")


# ---------------------------------------------------------------------------
# GARDENSTEIN — garden
# ---------------------------------------------------------------------------

def _gardenstein_flower():
    img, d = _canvas()
    cx, cy = 200, 200
    # Petals
    petal_color = (255, 180, 50, 220)
    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        px = int(cx + 80 * math.cos(rad))
        py = int(cy + 80 * math.sin(rad))
        d.ellipse([px - 40, py - 40, px + 40, py + 40], fill=petal_color)
    # Center
    d.ellipse([cx - 45, cy - 45, cx + 45, cy + 45], fill=(255, 220, 0, 240))
    d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=(200, 160, 0, 240))
    _save(img, "gardenstein", "sunflower")


def _gardenstein_butterfly():
    img, d = _canvas()
    # Wings
    d.ellipse([60, 100, 200, 240], fill=(180, 100, 200, 200))
    d.ellipse([200, 100, 340, 240], fill=(180, 100, 200, 200))
    d.ellipse([80, 220, 200, 330], fill=(200, 120, 220, 190))
    d.ellipse([200, 220, 320, 330], fill=(200, 120, 220, 190))
    # Wing patterns
    d.ellipse([100, 130, 170, 200], fill=(255, 200, 50, 160))
    d.ellipse([230, 130, 300, 200], fill=(255, 200, 50, 160))
    # Body
    d.ellipse([185, 110, 215, 330], fill=(60, 30, 80, 240))
    # Antennae
    d.line([(200, 110), (155, 55)], fill=(60, 30, 80, 220), width=3)
    d.line([(200, 110), (245, 55)], fill=(60, 30, 80, 220), width=3)
    d.ellipse([148, 48, 163, 63], fill=(60, 30, 80, 220))
    d.ellipse([238, 48, 253, 63], fill=(60, 30, 80, 220))
    _save(img, "gardenstein", "butterfly")


def _gardenstein_pot():
    img, d = _canvas()
    # Pot
    d.polygon([(120, 360), (100, 220), (300, 220), (280, 360)], fill=(190, 90, 40, 230))
    d.rounded_rectangle([95, 200, 305, 230], radius=8, fill=(210, 105, 50, 230))
    # Soil
    d.ellipse([105, 195, 295, 235], fill=(80, 50, 20, 220))
    # Stem
    d.line([(200, 210), (200, 120)], fill=(40, 140, 40, 240), width=8)
    # Leaves
    d.ellipse([120, 100, 200, 160], fill=(50, 160, 50, 220))
    d.ellipse([200, 110, 280, 170], fill=(40, 150, 40, 220))
    # Flower
    for angle in range(0, 360, 60):
        rad = math.radians(angle)
        px = int(200 + 30 * math.cos(rad))
        py = int(95 + 30 * math.sin(rad))
        d.ellipse([px - 18, py - 18, px + 18, py + 18], fill=(255, 100, 150, 220))
    d.ellipse([182, 77, 218, 113], fill=(255, 220, 0, 240))
    _save(img, "gardenstein", "flower_pot")


def _gardenstein_leaf():
    img, d = _canvas()
    # Big leaf
    d.polygon([
        (200, 60), (320, 150), (350, 250), (280, 340),
        (200, 370), (120, 340), (50, 250), (80, 150),
    ], fill=(50, 160, 60, 220))
    # Vein
    d.line([(200, 60), (200, 370)], fill=(30, 120, 40, 200), width=4)
    for y, spread in [(130, 40), (180, 60), (230, 70), (280, 60), (330, 40)]:
        d.line([(200, y), (200 - spread, y + 20)], fill=(30, 120, 40, 180), width=2)
        d.line([(200, y), (200 + spread, y + 20)], fill=(30, 120, 40, 180), width=2)
    _save(img, "gardenstein", "leaf")


def _gardenstein_watering_can():
    img, d = _canvas()
    # Can body
    d.rounded_rectangle([100, 160, 300, 320], radius=20, fill=(70, 130, 180, 230))
    # Spout
    d.polygon([(100, 200), (30, 170), (25, 200), (100, 230)], fill=(60, 110, 160, 230))
    # Nozzle drips
    for i in range(5):
        x = 15 + i * 3
        d.line([(x, 200), (x - 10, 240)], fill=(100, 180, 220, 200), width=2)
        d.ellipse([x - 13, 240, x - 7, 248], fill=(100, 180, 220, 200))
    # Handle
    d.arc([260, 120, 360, 280], start=270, end=90, fill=(60, 110, 160, 230), width=14)
    _save(img, "gardenstein", "watering_can")


# ---------------------------------------------------------------------------
# INTEX — pools / water
# ---------------------------------------------------------------------------

def _intex_splash():
    img, d = _canvas()
    # Water drops
    for cx, cy, r, a in [
        (200, 200, 80, 200), (120, 280, 50, 180), (290, 260, 60, 180),
        (160, 140, 40, 170), (260, 130, 35, 170),
    ]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(80, 180, 230, a))
    # Splash lines
    for angle in range(0, 360, 30):
        rad = math.radians(angle)
        x1 = int(200 + 80 * math.cos(rad))
        y1 = int(200 + 80 * math.sin(rad))
        x2 = int(200 + 140 * math.cos(rad))
        y2 = int(200 + 140 * math.sin(rad))
        d.line([(x1, y1), (x2, y2)], fill=(120, 200, 240, 180), width=4)
    _save(img, "intex", "splash")


def _intex_float():
    img, d = _canvas()
    # Pool ring
    d.ellipse([60, 100, 340, 310], fill=(255, 80, 80, 220))
    d.ellipse([110, 145, 290, 265], fill=(0, 0, 0, 0))  # hole
    # Stripes
    for i, col in enumerate([(255, 220, 0, 180), (255, 255, 255, 160)]):
        d.arc([60 + i * 20, 100 + i * 15, 340 - i * 20, 310 - i * 15],
              start=30, end=150, fill=col, width=12)
    _save(img, "intex", "pool_float")


def _intex_wave():
    img, d = _canvas()
    # Waves
    for i, (y_base, alpha) in enumerate([(160, 220), (220, 200), (280, 180)]):
        pts = []
        for x in range(0, 401, 10):
            y = y_base + int(25 * math.sin(math.radians(x * 2 + i * 60)))
            pts.append((x, y))
        pts += [(400, 400), (0, 400)]
        colors = [(30, 120, 200, alpha), (50, 150, 220, alpha), (80, 180, 240, alpha)]
        d.polygon(pts, fill=colors[i])
    _save(img, "intex", "waves")


def _intex_sun():
    img, d = _canvas()
    # Rays
    for angle in range(0, 360, 30):
        rad = math.radians(angle)
        x1 = int(200 + 80 * math.cos(rad))
        y1 = int(200 + 80 * math.sin(rad))
        x2 = int(200 + 130 * math.cos(rad))
        y2 = int(200 + 130 * math.sin(rad))
        d.line([(x1, y1), (x2, y2)], fill=(255, 200, 0, 220), width=8)
    d.ellipse([120, 120, 280, 280], fill=(255, 220, 30, 240))
    d.ellipse([145, 145, 255, 255], fill=(255, 240, 100, 200))
    _save(img, "intex", "sun")


def _intex_droplet():
    img, d = _canvas()
    # Teardrop shape
    d.polygon([
        (200, 60), (300, 200), (280, 300), (200, 340),
        (120, 300), (100, 200),
    ], fill=(40, 160, 220, 230))
    # Shine
    d.ellipse([150, 110, 200, 160], fill=(180, 230, 255, 140))
    _save(img, "intex", "droplet")


# ---------------------------------------------------------------------------
# MARKETIA HOME — household
# ---------------------------------------------------------------------------

def _marketia_star():
    img, d = _canvas()
    # Star
    pts = []
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        r = 130 if i % 2 == 0 else 65
        pts.append((200 + int(r * math.cos(angle)), 200 + int(r * math.sin(angle))))
    d.polygon(pts, fill=(255, 185, 0, 230))
    _save(img, "marketia_home", "star")


def _marketia_home_leaf():
    img, d = _canvas()
    d.polygon([
        (200, 80), (300, 170), (280, 300), (200, 350), (120, 300), (100, 170),
    ], fill=(60, 170, 80, 220))
    d.line([(200, 80), (200, 350)], fill=(40, 130, 50, 200), width=5)
    _save(img, "marketia_home", "plant")


def _marketia_brush():
    img, d = _canvas()
    # Handle
    d.rounded_rectangle([175, 80, 225, 280], radius=12, fill=(180, 130, 80, 230))
    # Brush head
    d.rounded_rectangle([150, 270, 250, 350], radius=8, fill=(80, 60, 40, 230))
    for x in range(158, 244, 12):
        d.line([(x, 345), (x + 4, 390)], fill=(120, 100, 70, 200), width=4)
    _save(img, "marketia_home", "cleaning_brush")


def _marketia_towel():
    img, d = _canvas()
    # Folded towel layers
    colors = [(220, 230, 245, 220), (200, 215, 235, 220), (180, 200, 225, 220)]
    for i, col in enumerate(colors):
        d.rounded_rectangle([80 + i * 5, 150 + i * 30, 320 - i * 5, 210 + i * 30],
                            radius=6, fill=col)
    # Pattern stripes
    for x in range(100, 300, 25):
        d.line([(x, 155), (x, 205)], fill=(150, 170, 200, 120), width=3)
    _save(img, "marketia_home", "towel_folded")


def _marketia_sparkle():
    img, d = _canvas()
    # 4-pointed sparkle
    for angle in [0, 45, 90, 135]:
        rad = math.radians(angle)
        for r in [110, 55]:
            x = int(200 + r * math.cos(rad))
            y = int(200 + r * math.sin(rad))
            x2 = int(200 - r * math.cos(rad))
            y2 = int(200 - r * math.sin(rad))
            d.line([(x, y), (x2, y2)], fill=(255, 220, 0, 200), width=10 if r == 110 else 5)
    d.ellipse([175, 175, 225, 225], fill=(255, 240, 100, 240))
    _save(img, "marketia_home", "sparkle")


# ---------------------------------------------------------------------------
# VILLAGO ACCESSORIES — lifestyle accessories
# ---------------------------------------------------------------------------

def _villago_coffee():
    img, d = _canvas()
    # Saucer
    d.ellipse([100, 290, 300, 340], fill=(210, 190, 160, 220))
    # Cup
    d.rounded_rectangle([120, 200, 280, 300], radius=15, fill=(240, 230, 210, 230))
    # Cup rim
    d.rounded_rectangle([115, 190, 285, 215], radius=8, fill=(220, 210, 190, 230))
    # Handle
    d.arc([260, 220, 330, 290], start=270, end=90, fill=(200, 180, 150, 230), width=14)
    # Coffee liquid
    d.ellipse([135, 202, 265, 225], fill=(100, 60, 20, 220))
    # Steam wisps
    for x, offset in [(160, 0), (200, 10), (240, 0)]:
        pts = [(x, 185), (x + offset, 155), (x - offset, 130), (x + offset, 105)]
        for i in range(len(pts) - 1):
            d.line([pts[i], pts[i + 1]], fill=(180, 180, 180, 120), width=3)
    _save(img, "villago", "coffee_cup")


def _villago_plant():
    img, d = _canvas()
    # Small succulent
    d.rounded_rectangle([160, 290, 240, 360], radius=10, fill=(180, 100, 50, 230))
    d.ellipse([140, 270, 260, 310], fill=(160, 80, 40, 230))
    # Leaves
    for angle, col in [(270, (60, 160, 80, 220)), (210, (50, 140, 70, 220)),
                       (330, (70, 170, 90, 220)), (180, (40, 130, 60, 210)),
                       (0, (80, 180, 100, 210))]:
        rad = math.radians(angle)
        cx = int(200 + 50 * math.cos(rad))
        cy = int(280 + 50 * math.sin(rad))
        d.ellipse([cx - 35, cy - 50, cx + 35, cy + 10], fill=col)
    _save(img, "villago", "plant_decor")


def _villago_bag():
    img, d = _canvas()
    # Bag body
    d.rounded_rectangle([100, 170, 300, 350], radius=18, fill=(70, 100, 160, 230))
    # Handle
    d.arc([140, 100, 260, 200], start=180, end=0, fill=(50, 80, 140, 230), width=14)
    # Pocket
    d.rounded_rectangle([130, 240, 270, 310], radius=10, fill=(55, 80, 140, 220))
    # Zipper
    d.line([(145, 245), (255, 245)], fill=(200, 180, 100, 220), width=4)
    d.ellipse([192, 238, 208, 254], fill=(200, 180, 100, 230))
    _save(img, "villago", "bag")


def _villago_bicycle():
    img, d = _canvas()
    wc = (60, 100, 180, 220)
    # Wheels
    d.ellipse([40, 220, 180, 360], outline=wc, width=12)
    d.ellipse([220, 220, 360, 360], outline=wc, width=12)
    d.ellipse([95, 275, 125, 305], fill=wc)
    d.ellipse([275, 275, 305, 305], fill=wc)
    # Frame
    d.line([(110, 290), (200, 180)], fill=wc, width=10)
    d.line([(200, 180), (290, 290)], fill=wc, width=10)
    d.line([(200, 180), (200, 290)], fill=wc, width=10)
    d.line([(200, 290), (290, 290)], fill=wc, width=8)
    # Handlebar
    d.line([(200, 180), (230, 160)], fill=wc, width=8)
    d.line([(220, 150), (240, 170)], fill=wc, width=8)
    # Seat
    d.rounded_rectangle([180, 170, 220, 185], radius=5, fill=wc)
    _save(img, "villago", "bicycle")


def _villago_lamp():
    img, d = _canvas()
    # Base
    d.rounded_rectangle([170, 340, 230, 380], radius=5, fill=(120, 100, 70, 230))
    d.rounded_rectangle([155, 330, 245, 348], radius=5, fill=(130, 110, 80, 230))
    # Pole
    d.line([(200, 330), (200, 200)], fill=(140, 120, 90, 230), width=8)
    # Shade
    d.polygon([(130, 200), (270, 200), (240, 120), (160, 120)], fill=(220, 200, 140, 230))
    # Glow
    d.ellipse([155, 190, 245, 240], fill=(255, 240, 180, 80))
    _save(img, "villago", "lamp")


# ---------------------------------------------------------------------------
# HOPLA TOYS — children
# ---------------------------------------------------------------------------

def _hopla_star():
    img, d = _canvas()
    pts = []
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        r = 140 if i % 2 == 0 else 65
        pts.append((200 + int(r * math.cos(angle)), 200 + int(r * math.sin(angle))))
    d.polygon(pts, fill=(255, 200, 0, 230))
    d.polygon(pts, outline=(240, 180, 0, 200), width=3)
    _save(img, "hopla_toys", "star")


def _hopla_ball():
    img, d = _canvas()
    d.ellipse([60, 60, 340, 340], fill=(255, 80, 80, 220))
    d.arc([60, 60, 340, 340], start=45, end=135, fill=(255, 200, 0, 200), width=35)
    d.arc([60, 60, 340, 340], start=225, end=315, fill=(255, 200, 0, 200), width=35)
    d.ellipse([100, 100, 180, 150], fill=(255, 255, 255, 100))
    _save(img, "hopla_toys", "ball")


def _hopla_teddy():
    img, d = _canvas()
    c = (200, 160, 100, 220)
    # Body
    d.ellipse([110, 200, 290, 370], fill=c)
    # Head
    d.ellipse([120, 80, 280, 230], fill=c)
    # Ears
    d.ellipse([100, 60, 165, 130], fill=c)
    d.ellipse([235, 60, 300, 130], fill=c)
    d.ellipse([112, 72, 153, 118], fill=(230, 180, 130, 200))
    d.ellipse([247, 72, 288, 118], fill=(230, 180, 130, 200))
    # Eyes
    d.ellipse([155, 130, 185, 160], fill=(30, 20, 10, 255))
    d.ellipse([215, 130, 245, 160], fill=(30, 20, 10, 255))
    d.ellipse([161, 136, 172, 147], fill=(255, 255, 255, 200))
    # Snout
    d.ellipse([162, 175, 238, 220], fill=(230, 180, 130, 220))
    d.ellipse([185, 185, 215, 205], fill=(60, 40, 20, 230))
    # Arms
    d.ellipse([60, 220, 140, 310], fill=c)
    d.ellipse([260, 220, 340, 310], fill=c)
    _save(img, "hopla_toys", "teddy_bear")


def _hopla_blocks():
    img, d = _canvas()
    colors = [(255, 80, 80, 220), (80, 160, 255, 220), (255, 200, 0, 220), (80, 200, 100, 220)]
    positions = [(80, 220), (180, 220), (80, 120), (180, 120)]
    for (x, y), col in zip(positions, colors):
        d.rounded_rectangle([x, y, x + 95, y + 95], radius=8, fill=col)
        # Simple letter
        letters = ["A", "B", "1", "2"]
        idx = positions.index((x, y))
        # Draw a simple cross as letter stand-in
        mx, my = x + 47, y + 47
        d.line([(mx - 20, my), (mx + 20, my)], fill=(255, 255, 255, 200), width=5)
        d.line([(mx, my - 20), (mx, my + 20)], fill=(255, 255, 255, 200), width=5)
    _save(img, "hopla_toys", "blocks")


def _hopla_rainbow():
    img, d = _canvas()
    arcs = [
        (255, 0, 0, 200), (255, 127, 0, 200), (255, 200, 0, 200),
        (0, 200, 0, 200), (0, 100, 255, 200), (100, 0, 200, 200),
    ]
    for i, col in enumerate(arcs):
        r = 30 + i * 25
        d.arc([200 - 160 + r, 150 + r // 2, 200 + 160 - r, 350 - r // 2],
              start=180, end=0, fill=col, width=20)
    # Cloud base
    for cx, cy in [(100, 320), (140, 310), (180, 310), (220, 310), (260, 310), (300, 320)]:
        d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=(255, 255, 255, 210))
    _save(img, "hopla_toys", "rainbow")


# ---------------------------------------------------------------------------
# JUMI — furniture (chairs, tables, office)
# ---------------------------------------------------------------------------

def _jumi_chair():
    img, d = _canvas()
    c = (80, 80, 90, 220)
    light = (200, 200, 210, 220)
    # Seat
    d.rounded_rectangle([100, 210, 300, 260], radius=10, fill=light)
    # Back
    d.rounded_rectangle([100, 100, 300, 220], radius=10, fill=light)
    # Back bars
    for x in [130, 180, 230, 280]:
        d.line([(x, 115), (x, 215)], fill=c, width=5)
    # Legs
    for x in [115, 275]:
        d.line([(x, 255), (x - 15, 380)], fill=c, width=10)
        d.line([(x, 255), (x + 15, 380)], fill=c, width=10)
    _save(img, "jumi", "chair")


def _jumi_office_chair():
    img, d = _canvas()
    c = (40, 40, 50, 220)
    sc = (50, 50, 60, 220)
    # Seat & back cushion
    d.rounded_rectangle([110, 180, 290, 230], radius=12, fill=sc)
    d.rounded_rectangle([130, 80, 270, 185], radius=12, fill=sc)
    # Gas lift
    d.line([(200, 230), (200, 310)], fill=c, width=12)
    # Base star
    for angle in range(0, 360, 72):
        rad = math.radians(angle)
        x = int(200 + 90 * math.cos(rad))
        y = int(350 + 30 * math.sin(rad))
        d.line([(200, 330), (x, y)], fill=c, width=8)
        d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=c)
    # Armrests
    d.rounded_rectangle([75, 195, 115, 240], radius=6, fill=c)
    d.rounded_rectangle([285, 195, 325, 240], radius=6, fill=c)
    _save(img, "jumi", "office_chair")


def _jumi_table():
    img, d = _canvas()
    c = (140, 100, 60, 220)
    # Tabletop
    d.rounded_rectangle([60, 140, 340, 190], radius=8, fill=c)
    # Legs
    for x in [90, 310]:
        d.line([(x, 188), (x, 360)], fill=(110, 75, 40, 220), width=14)
    # Crossbar
    d.line([(90, 280), (310, 280)], fill=(110, 75, 40, 200), width=8)
    _save(img, "jumi", "table")


def _jumi_lamp():
    img, d = _canvas()
    # Shade
    d.polygon([(120, 200), (280, 200), (250, 110), (150, 110)], fill=(220, 200, 100, 230))
    # Glow
    d.ellipse([140, 195, 260, 240], fill=(255, 240, 160, 80))
    # Pole
    d.line([(200, 200), (200, 360)], fill=(100, 80, 60, 230), width=8)
    # Base
    d.rounded_rectangle([155, 350, 245, 375], radius=8, fill=(110, 90, 70, 230))
    d.rounded_rectangle([140, 365, 260, 380], radius=5, fill=(120, 100, 80, 230))
    _save(img, "jumi", "desk_lamp")


# ---------------------------------------------------------------------------
# GENERIC / UNIVERSAL
# ---------------------------------------------------------------------------

def _generic_heart():
    img, d = _canvas()
    # Heart polygon approximation
    pts = []
    for t in range(0, 360, 3):
        rad = math.radians(t)
        x = 16 * (math.sin(rad) ** 3)
        y = -(13 * math.cos(rad) - 5 * math.cos(2 * rad) - 2 * math.cos(3 * rad) - math.cos(4 * rad))
        pts.append((int(200 + x * 11), int(200 + y * 11)))
    d.polygon(pts, fill=(220, 50, 80, 230))
    _save(img, "generic", "heart")


def _generic_checkmark():
    img, d = _canvas()
    d.ellipse([30, 30, 370, 370], fill=(40, 180, 80, 200))
    d.line([(100, 210), (170, 290), (310, 120)], fill=(255, 255, 255, 240), width=22)
    _save(img, "generic", "checkmark")


def _generic_ribbon():
    img, d = _canvas()
    # Ribbon / award
    d.polygon([(200, 40), (240, 130), (340, 130), (265, 185), (295, 280),
               (200, 225), (105, 280), (135, 185), (60, 130), (160, 130)],
              fill=(255, 180, 0, 230))
    d.ellipse([140, 130, 260, 240], fill=(255, 220, 50, 230))
    d.ellipse([160, 150, 240, 220], fill=(240, 200, 30, 220))
    _save(img, "generic", "award")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_all():
    print("Generating lifestyle assets...")
    _zoovera_dog()
    _zoovera_cat()
    _zoovera_paw()
    _zoovera_bone()
    _zoovera_fish()

    _gardenstein_flower()
    _gardenstein_butterfly()
    _gardenstein_pot()
    _gardenstein_leaf()
    _gardenstein_watering_can()

    _intex_splash()
    _intex_float()
    _intex_wave()
    _intex_sun()
    _intex_droplet()

    _marketia_star()
    _marketia_home_leaf()
    _marketia_brush()
    _marketia_towel()
    _marketia_sparkle()

    _villago_coffee()
    _villago_plant()
    _villago_bag()
    _villago_bicycle()
    _villago_lamp()

    _hopla_star()
    _hopla_ball()
    _hopla_teddy()
    _hopla_blocks()
    _hopla_rainbow()

    _jumi_chair()
    _jumi_office_chair()
    _jumi_table()
    _jumi_lamp()

    _generic_heart()
    _generic_checkmark()
    _generic_ribbon()

    print(f"\nDone. Assets saved to {OUT}")


if __name__ == "__main__":
    generate_all()
