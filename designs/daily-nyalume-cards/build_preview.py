from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / "nyalume/frontends/web/static/daily_nyalume"
OUTPUT = Path(__file__).with_name("preview-grid.jpg")
ITEMS = (
    ("quiet", "0–9  低电量 · 雾面"),
    ("dreamy", "10–19  迷糊 · 缎光"),
    ("slow", "20–29  慢热 · 珍珠光"),
    ("focused", "30–39  认真 · 云母光"),
    ("everyday", "40–49  日常 · 蛋白石光"),
    ("sunny", "50–59  元气 · 棱镜光"),
    ("caring", "60–69  贴心 · 极光"),
    ("creative", "70–79  灵感 · 星尘光"),
    ("reliable", "80–88  可靠 · 线性虹彩"),
    ("perfect", "100  满分 · 晶钻光"),
)


def font(size: int):
    for path in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main():
    card_size = (204, 306)
    cell_size = (224, 354)
    canvas = Image.new("RGB", (cell_size[0] * 5, cell_size[1] * 2), "#171522")
    draw = ImageDraw.Draw(canvas)
    label_font = font(16)
    for index, (key, label) in enumerate(ITEMS):
        x = index % 5 * cell_size[0] + 10
        y = index // 5 * cell_size[1] + 10
        with Image.open(CARDS / f"{key}.png") as source:
            card = source.convert("RGB").resize(card_size, Image.Resampling.LANCZOS)
        canvas.paste(card, (x, y))
        draw.text((x + card_size[0] / 2, y + card_size[1] + 16), label,
                  fill="#eeeaf8", font=label_font, anchor="mm")
    canvas.save(OUTPUT, quality=92, optimize=True)


if __name__ == "__main__":
    main()
