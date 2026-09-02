"""Generate the Codex Studio PNG and multi-resolution Windows icon."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

CANVAS_SIZE = 1024
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _mix(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float):
    return tuple(round(a + (b - a) * ratio) for a, b in zip(start, end, strict=True))


def _gradient_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    start_color: tuple[int, int, int],
    end_color: tuple[int, int, int],
    width: int,
) -> None:
    steps = 80
    for index in range(steps):
        left = index / steps
        right = (index + 1) / steps
        p1 = (
            round(start[0] + (end[0] - start[0]) * left),
            round(start[1] + (end[1] - start[1]) * left),
        )
        p2 = (
            round(start[0] + (end[0] - start[0]) * right),
            round(start[1] + (end[1] - start[1]) * right),
        )
        draw.line(
            (p1, p2), fill=(*_mix(start_color, end_color, left), 255), width=width
        )


def build_icon() -> Image.Image:
    image = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    background = Image.new("RGBA", image.size, (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(background)
    bg_draw.rounded_rectangle(
        (62, 62, 962, 962),
        radius=224,
        fill=(7, 25, 39, 255),
        outline=(72, 177, 177, 96),
        width=12,
    )
    for radius, alpha in ((390, 42), (300, 34), (210, 28)):
        bg_draw.ellipse(
            (160 - radius, 120 - radius, 160 + radius, 120 + radius),
            fill=(42, 218, 192, alpha),
        )
    background = background.filter(ImageFilter.GaussianBlur(54))
    image.alpha_composite(background)

    # Restore a crisp shell after the atmospheric glow.
    shell = ImageDraw.Draw(image)
    shell.rounded_rectangle(
        (62, 62, 962, 962),
        radius=224,
        fill=(7, 25, 39, 234),
        outline=(93, 217, 205, 92),
        width=10,
    )

    top = (512, 188)
    left = (236, 348)
    center = (512, 510)
    right = (788, 348)
    bottom = (512, 832)
    cyan = (121, 242, 212)
    blue = (75, 125, 243)

    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for start, end in (
        (top, left),
        (top, right),
        (left, center),
        (right, center),
        (left, bottom),
        (right, bottom),
        (center, bottom),
    ):
        glow_draw.line((start, end), fill=(66, 218, 204, 126), width=42)
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(34)))

    lattice = ImageDraw.Draw(image)
    _gradient_line(lattice, top, left, cyan, (62, 201, 203), 28)
    _gradient_line(lattice, top, right, cyan, blue, 28)
    _gradient_line(lattice, left, center, (62, 201, 203), cyan, 24)
    _gradient_line(lattice, right, center, blue, cyan, 24)
    _gradient_line(lattice, left, bottom, (62, 201, 203), (64, 153, 224), 24)
    _gradient_line(lattice, right, bottom, blue, (64, 153, 224), 24)
    _gradient_line(lattice, center, bottom, cyan, (64, 153, 224), 22)

    for point, radius, color in (
        (top, 47, cyan),
        (left, 42, (74, 220, 205)),
        (right, 42, blue),
        (center, 58, (86, 225, 210)),
        (bottom, 40, (70, 166, 230)),
    ):
        x, y = point
        lattice.ellipse(
            (x - radius, y - radius, x + radius, y + radius), fill=(*color, 255)
        )
        lattice.ellipse(
            (x - radius // 3, y - radius // 3, x + radius // 3, y + radius // 3),
            fill=(226, 255, 250, 210),
        )
    return image


def main() -> None:
    output_dir = Path(__file__).resolve().parents[1] / "pdf2zh" / "assets"
    output_dir.mkdir(parents=True, exist_ok=True)
    image = build_icon()
    image.resize((512, 512), Image.Resampling.LANCZOS).save(
        output_dir / "desktop-icon.png",
        optimize=True,
    )
    image.save(
        output_dir / "desktop-icon.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
    )


if __name__ == "__main__":
    main()
