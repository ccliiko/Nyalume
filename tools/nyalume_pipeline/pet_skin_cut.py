"""把 ComfyUI 生成的纯色背景图处理成桌宠透明 PNG 皮肤。

用法：
    python build_skin.py <input.png> [input2.png ...]

处理：自动采样图片边框的背景主色 → 只删除“从边缘连进来的背景连通区域”
（避免误伤主体内部的相似色，如腮红/白毛高光）→ 边缘柔化 →
裁掉空边 → 缩放到正方形画布（默认 300x300）→ 输出
user_pets/nyalume/idle_<n>.png + _preview_<n>.png（棋盘格预览）。

--hard / --soft 控制删除阈值，出图不理想时再调。
"""

import argparse
from pathlib import Path

import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent


def border_key_rgb(img: Image.Image) -> np.ndarray:
    """取 8px 边框的均值作为背景主色。"""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    border = np.concatenate(
        [
            a[:8].reshape(-1, 3),
            a[-8:].reshape(-1, 3),
            a[:, :8].reshape(-1, 3),
            a[:, -8:].reshape(-1, 3),
        ]
    )
    return border.mean(axis=0)


def make_transparent(
    img: Image.Image, key_rgb, hard: int, soft: int, adaptive: bool = False
) -> Image.Image:
    img = img.convert("RGBA")
    arr = np.asarray(img, dtype=np.float32).copy()
    rgb = arr[:, :, :3]
    if adaptive:
        # 缩到小图做中值滤波估计“局部背景色”，再放大回原尺寸，
        # 可容忍纯色底的轻微渐变/色带，又不至于太慢。
        small_rgb = Image.fromarray(
            arr[:, :, :3].astype(np.uint8)
        ).resize((256, 256), Image.BILINEAR)
        small_arr = np.asarray(small_rgb, dtype=np.float32)
        bg_small = ndimage.median_filter(small_arr, size=(17, 17, 1))
        bg_img = Image.fromarray(bg_small.astype(np.uint8)).resize(
            (rgb.shape[1], rgb.shape[0]), Image.BILINEAR
        )
        key = np.asarray(bg_img, dtype=np.float32)
    else:
        key = np.broadcast_to(
            np.asarray(key_rgb, dtype=np.float32)[None, None, :], rgb.shape
        )
    dist = np.linalg.norm(rgb - key, axis=2)
    h, w = dist.shape

    core_bg = dist < hard
    seed = np.zeros_like(core_bg)
    seed[0, :] = core_bg[0, :]
    seed[-1, :] = core_bg[-1, :]
    seed[:, 0] = core_bg[:, 0]
    seed[:, -1] = core_bg[:, -1]
    bg = ndimage.binary_propagation(seed, mask=core_bg, structure=np.ones((3, 3)))

    # 背景全透明；紧贴背景的半透明过渡带；主体其余部分不透明。
    alpha = np.full((h, w), 255.0, dtype=np.float32)
    alpha[bg] = 0.0
    fringe = (~bg) & (dist < hard + soft)
    if fringe.any():
        alpha[fringe] = (
            np.clip((dist[fringe] - hard) / max(soft, 1), 0.0, 1.0) * 255.0
        )
    alpha = ndimage.gaussian_filter(alpha, sigma=0.8)
    alpha = np.clip(alpha, 0.0, 255.0)
    arr[:, :, 3] = np.minimum(arr[:, :, 3], alpha)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def trim_and_square(img: Image.Image, canvas: int) -> Image.Image:
    bbox = img.getbbox()
    if not bbox:
        raise ValueError("图片全透明，无法成皮肤")
    img = img.crop(bbox)
    w, h = img.size
    side = max(w, h)
    padded = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    padded.paste(img, ((side - w) // 2, (side - h) // 2))
    return padded.resize((canvas, canvas), Image.LANCZOS)


def checker_preview(img: Image.Image) -> Image.Image:
    size = 32
    w, h = img.size
    base = Image.new("RGB", (w, h), "#ffffff")
    draw = ImageDraw.Draw(base)
    for y in range(0, h, size):
        for x in range(0, w, size):
            if (x // size + y // size) % 2:
                draw.rectangle([x, y, x + size - 1, y + size - 1], fill="#cccccc")
    base.paste(img, (0, 0), img)
    return base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", help="ComfyUI 输出的 PNG 路径")
    parser.add_argument("--hard", type=int, default=45,
                        help="与背景色差小于该值的边缘像素判为背景")
    parser.add_argument("--soft", type=int, default=60,
                        help="背景色差在该区间内的边缘像素做半透明过渡")
    parser.add_argument("--canvas", type=int, default=300)
    parser.add_argument("--prefix", default="idle_",
                        help="输出文件名前缀，如 chibi_")
    parser.add_argument("--adaptive", action="store_true",
                        help="用局部背景色估计抠图，容忍渐变背景")
    parser.add_argument("--out", default="",
                        help="输出目录（默认脚本所在目录）")
    args = parser.parse_args()
    out_dir = Path(args.out).resolve() if args.out else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, path in enumerate(args.inputs, start=1):
        src = Image.open(path)
        key_rgb = border_key_rgb(src)
        cut = make_transparent(src, key_rgb, args.hard, args.soft, args.adaptive)
        final = trim_and_square(cut, args.canvas)
        out = out_dir / f"{args.prefix}{i}.png"
        prev = out_dir / f"_{args.prefix}{i}.png"
        final.save(out)
        checker_preview(final).save(prev)
        print(
            f"saved {out}  preview {prev}  "
            f"(key={tuple(round(float(x)) for x in key_rgb)} "
            f"hard={args.hard} soft={args.soft})"
        )


if __name__ == "__main__":
    main()
