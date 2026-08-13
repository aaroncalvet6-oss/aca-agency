"""Genera favicon y og-image de ACA Agency (sin dependencias externas de red)."""
from PIL import Image, ImageDraw, ImageFont

ACCENT = (37, 99, 235)
HEADING = (15, 31, 51)
MUTED = (84, 91, 104)
WHITE = (255, 255, 255)
BG_ALT = (247, 248, 250)

FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=255)
    return mask


def gen_favicon():
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(size * 0.22)
    d.rounded_rectangle([0, 0, size, size], radius=radius, fill=ACCENT)
    font = ImageFont.truetype(FONT_BOLD, int(size * 0.58))
    text = "A"
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), text, font=font, fill=WHITE)

    img.save("img/favicon-512.png")

    for s in [16, 32, 48, 180]:
        resized = img.resize((s, s), Image.LANCZOS)
        if s == 180:
            resized.convert("RGB").save("img/apple-touch-icon.png")
        else:
            resized.save(f"img/favicon-{s}.png")

    icon_sizes = [(16, 16), (32, 32), (48, 48)]
    imgs = [img.resize(s, Image.LANCZOS) for s in icon_sizes]
    imgs[0].save("img/favicon.ico", sizes=icon_sizes)


def gen_og_image():
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(img)

    # franja de acento superior
    d.rectangle([0, 0, w, 10], fill=ACCENT)

    # marca
    mark_size = 56
    mx, my = 80, 74
    d.rounded_rectangle([mx, my, mx + mark_size, my + mark_size], radius=13, fill=ACCENT)
    font_mark = ImageFont.truetype(FONT_BOLD, 32)
    bbox = d.textbbox((0, 0), "A", font=font_mark)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((mx + (mark_size - tw) / 2 - bbox[0], my + (mark_size - th) / 2 - bbox[1]), "A", font=font_mark, fill=WHITE)

    font_logo = ImageFont.truetype(FONT_BOLD, 30)
    d.text((mx + mark_size + 16, my + 12), "ACA Agency", font=font_logo, fill=HEADING)

    # titular
    font_h1 = ImageFont.truetype(FONT_BOLD, 54)
    lines = [
        "Webs para empresas de",
        "reformas que convierten",
        "visitas en presupuestos",
    ]
    y = 200
    for line in lines:
        d.text((80, y), line, font=font_h1, fill=HEADING)
        y += 66

    # subtitulo
    font_sub = ImageFont.truetype(FONT_REG, 26)
    d.text((80, y + 18), "Diseño web especializado en reformas — Castellón y C. Valenciana", font=font_sub, fill=MUTED)

    # linea inferior de acento
    d.rectangle([0, h - 10, w, h], fill=ACCENT)

    img.save("img/og-image.png")


if __name__ == "__main__":
    gen_favicon()
    gen_og_image()
    print("ok")
