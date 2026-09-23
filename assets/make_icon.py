"""
Generate app icons for Mac (.icns) and Windows (.ico).
Run once from the project root:  python3 assets/make_icon.py
Requires Pillow: pip install Pillow
"""
import os
import struct
import zlib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

NAVY  = (15,  34,  64,  255)
GOLD  = (196, 162, 87,  255)
WHITE = (255, 255, 255, 255)

OUT_DIR = Path(__file__).parent


def make_base_image(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rectangle background
    radius = size // 5
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=GOLD)

    # "DP" text centred — only draw at sizes where text renders cleanly
    font_size = max(12, int(size * 0.42))
    font = None
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]:
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    text = "DP"
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
            text, fill=NAVY, font=font,
        )
    except Exception:
        pass  # skip text on very small sizes
    return img


def make_ico(path: Path):
    sizes = [16, 32, 48, 64, 128, 256]
    frames = [make_base_image(s).convert("RGBA") for s in sizes]
    frames[0].save(
        path, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"  created {path}")


def make_icns(path: Path):
    """
    Build a minimal .icns file with ic07 (128), ic08 (256), ic09 (512),
    ic10 (1024) entries using raw PNG data.
    """
    ICON_TYPES = [
        ("ic07", 128),
        ("ic08", 256),
        ("ic09", 512),
        ("ic10", 1024),
    ]
    chunks = b""
    for icon_type, size in ICON_TYPES:
        img = make_base_image(size)
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_data = buf.getvalue()
        type_bytes = icon_type.encode("ascii")
        length = 8 + len(png_data)
        chunks += type_bytes + struct.pack(">I", length) + png_data

    header = b"icns" + struct.pack(">I", 8 + len(chunks))
    with open(path, "wb") as f:
        f.write(header + chunks)
    print(f"  created {path}")


def make_png(path: Path, size: int = 512):
    img = make_base_image(size)
    img.save(path, format="PNG")
    print(f"  created {path}")


if __name__ == "__main__":
    print("Generating icons…")
    make_png(OUT_DIR / "icon.png")
    make_ico(OUT_DIR / "icon.ico")
    make_icns(OUT_DIR / "icon.icns")
    print("Done.")
