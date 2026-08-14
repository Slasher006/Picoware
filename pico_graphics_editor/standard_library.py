"""Deterministic read-only visual assets for the Asset Library."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .asset_library import LibraryAsset
from .model import PixelArt, rgb_to_rgb565


WHITE = 0xFFFF
CYAN = 0x07FF
GREEN = 0x07E0
YELLOW = 0xFFE0
ORANGE = 0xFD20
MAGENTA = 0xF81F


STANDARD_ICON_NAMES = (
    "Home",
    "Back",
    "Forward",
    "Up",
    "Down",
    "Left",
    "Right",
    "Menu",
    "Close",
    "Check",
    "Plus",
    "Minus",
    "Search",
    "Settings",
    "Info",
    "Warning",
    "Help",
    "Save",
    "Open Folder",
    "File",
    "Trash",
    "Edit",
    "Copy",
    "Download",
    "Upload",
    "Refresh",
    "Play",
    "Pause",
    "Stop",
    "Record",
    "Volume",
    "Mute",
    "Wi-Fi",
    "Bluetooth",
    "Battery",
    "Power",
    "Lock",
    "Unlock",
    "User",
    "Users",
    "Heart",
    "Star",
    "Clock",
    "Calendar",
    "Camera",
    "Image",
    "Mail",
    "Phone",
    "Location",
    "Globe",
)


THEME_NAMES = (
    ("industrial", "Industrial"),
    ("creative", "Creative"),
    ("playful", "Playful"),
    ("feminine", "Feminine"),
    ("masculine", "Masculine"),
)

THEMED_ICON_NAMES = (
    "Home",
    "Back",
    "Forward",
    "Menu",
    "Close",
    "Check",
    "Plus",
    "Minus",
    "Search",
    "Settings",
    "Info",
    "Warning",
    "Save",
    "Edit",
    "Play",
    "Pause",
    "Power",
    "User",
    "Heart",
    "Star",
)

BUTTON_DESIGNS = (
    ("Primary Compact", 72, 20),
    ("Primary Wide", 120, 28),
    ("Secondary Compact", 72, 20),
    ("Secondary Wide", 120, 28),
    ("Success Compact", 72, 20),
    ("Success Wide", 120, 28),
    ("Danger Compact", 72, 20),
    ("Danger Wide", 120, 28),
    ("Outline Compact", 72, 20),
    ("Outline Wide", 120, 28),
    ("Ghost Compact", 72, 20),
    ("Ghost Wide", 120, 28),
)

WIDGET_DESIGNS = (
    ("Status Bar Compact", 128, 16),
    ("Status Bar Wide", 240, 20),
    ("Card Compact", 112, 56),
    ("Card Wide", 200, 72),
    ("List Row", 120, 22),
    ("Progress", 120, 14),
    ("Toggle", 48, 20),
    ("Slider", 120, 16),
    ("Input", 120, 24),
    ("Dialog", 120, 64),
    ("Metric", 80, 48),
    ("Navigation", 120, 24),
)

BACKGROUND_DESIGNS = (
    ("PicoCalc Grid", 320, 320, "PicoCalc 320x320"),
    ("PicoCalc Panel", 320, 320, "PicoCalc 320x320"),
    ("Cardputer Stripes", 240, 135, "Cardputer 240x135"),
    ("Cardputer Horizon", 240, 135, "Cardputer 240x135"),
    ("Flipper Matrix", 128, 64, "Flipper Zero 128x64"),
    ("Round Rings", 240, 240, "Round display 240x240"),
)


@dataclass(frozen=True)
class ThemePalette:
    """Define the colors shared by one built-in visual design system."""

    background: int
    surface: int
    surface_alt: int
    primary: int
    secondary: int
    accent: int
    text: int
    muted: int
    success: int
    danger: int


@dataclass(frozen=True)
class StandardAssetMetadata:
    """Describe a built-in asset without changing the personal-library format."""

    asset_id: str
    name: str
    theme: str
    theme_name: str
    kind: str
    design: str
    width: int
    height: int
    device_profile: str = ""


def _rgb(red: int, green: int, blue: int) -> int:
    return rgb_to_rgb565(red, green, blue)


THEME_PALETTES = {
    "industrial": ThemePalette(
        _rgb(17, 24, 28), _rgb(40, 52, 57), _rgb(65, 78, 83),
        _rgb(0, 204, 214), _rgb(255, 151, 36), _rgb(172, 198, 204),
        _rgb(239, 247, 248), _rgb(137, 157, 162), _rgb(73, 212, 146),
        _rgb(247, 76, 76),
    ),
    "creative": ThemePalette(
        _rgb(30, 16, 52), _rgb(72, 37, 101), _rgb(105, 53, 139),
        _rgb(255, 76, 185), _rgb(48, 214, 229), _rgb(255, 200, 67),
        _rgb(255, 245, 255), _rgb(190, 151, 205), _rgb(90, 222, 156),
        _rgb(255, 85, 105),
    ),
    "playful": ThemePalette(
        _rgb(255, 250, 224), _rgb(255, 221, 92), _rgb(119, 220, 255),
        _rgb(36, 184, 244), _rgb(255, 103, 171), _rgb(255, 143, 47),
        _rgb(52, 48, 71), _rgb(120, 104, 126), _rgb(71, 201, 112),
        _rgb(246, 75, 93),
    ),
    "feminine": ThemePalette(
        _rgb(255, 243, 247), _rgb(247, 207, 222), _rgb(228, 181, 214),
        _rgb(204, 84, 142), _rgb(140, 91, 180), _rgb(225, 156, 76),
        _rgb(78, 42, 65), _rgb(142, 102, 128), _rgb(88, 177, 127),
        _rgb(209, 67, 91),
    ),
    "masculine": ThemePalette(
        _rgb(12, 24, 39), _rgb(31, 52, 72), _rgb(55, 76, 92),
        _rgb(64, 145, 214), _rgb(225, 130, 51), _rgb(164, 181, 192),
        _rgb(232, 239, 243), _rgb(127, 148, 162), _rgb(67, 178, 122),
        _rgb(219, 72, 64),
    ),
}


def _slug(name: str) -> str:
    """Return a stable readable identity fragment."""
    return name.casefold().replace("-", "_").replace(" ", "_")


def _build_themed_metadata() -> tuple[StandardAssetMetadata, ...]:
    """Create the stable 50-item manifest for every visual theme."""
    records: list[StandardAssetMetadata] = []
    for theme, theme_name in THEME_NAMES:
        for design in THEMED_ICON_NAMES:
            records.append(_metadata(theme, theme_name, "icon", design, 16, 16))
        for design, width, height in BUTTON_DESIGNS:
            records.append(
                _metadata(theme, theme_name, "button", design, width, height)
            )
        for design, width, height in WIDGET_DESIGNS:
            records.append(
                _metadata(theme, theme_name, "widget", design, width, height)
            )
        for design, width, height, device_profile in BACKGROUND_DESIGNS:
            records.append(
                _metadata(
                    theme,
                    theme_name,
                    "background",
                    design,
                    width,
                    height,
                    device_profile,
                )
            )
    return tuple(records)


def _metadata(
    theme: str,
    theme_name: str,
    kind: str,
    design: str,
    width: int,
    height: int,
    device_profile: str = "",
) -> StandardAssetMetadata:
    asset_id = f"builtin_theme_{theme}_{kind}_{_slug(design)}"
    return StandardAssetMetadata(
        asset_id,
        f"{theme_name} / {kind.title()} / {design}",
        theme,
        theme_name,
        kind,
        design,
        width,
        height,
        device_profile,
    )


THEMED_ASSET_METADATA = _build_themed_metadata()
STANDARD_ASSET_NAMES = (
    *STANDARD_ICON_NAMES,
    *(metadata.name for metadata in THEMED_ASSET_METADATA),
)
_STANDARD_METADATA_BY_ID = {
    metadata.asset_id: metadata for metadata in THEMED_ASSET_METADATA
}


@lru_cache(maxsize=1)
def standard_library_assets() -> tuple[LibraryAsset, ...]:
    """Return immutable records for the starter icons and themed systems."""
    starter_icons = tuple(
        LibraryAsset.from_frames(
            f"builtin_icon_{_slug(name)}",
            name,
            (_draw_icon(name),),
        )
        for name in STANDARD_ICON_NAMES
    )
    themed_assets = tuple(
        LibraryAsset.from_frames(
            metadata.asset_id,
            metadata.name,
            (_draw_themed_asset(metadata),),
        )
        for metadata in THEMED_ASSET_METADATA
    )
    return (*starter_icons, *themed_assets)


def is_standard_asset_id(asset_id: str) -> bool:
    """Return whether an identity belongs to the read-only built-in collection."""
    return asset_id.startswith(("builtin_icon_", "builtin_theme_"))


def standard_asset_metadata(asset_id: str) -> StandardAssetMetadata | None:
    """Return theme/type/profile data for a themed built-in asset."""
    return _STANDARD_METADATA_BY_ID.get(asset_id)


def _draw_themed_asset(metadata: StandardAssetMetadata) -> PixelArt:
    """Render one catalogue record from its stable theme manifest."""
    palette = THEME_PALETTES[metadata.theme]
    if metadata.kind == "icon":
        return _draw_themed_icon(metadata.design, metadata.theme, palette)
    if metadata.kind == "button":
        return _draw_button(
            metadata.design,
            metadata.width,
            metadata.height,
            metadata.theme,
            palette,
        )
    if metadata.kind == "widget":
        return _draw_widget(
            metadata.design,
            metadata.width,
            metadata.height,
            metadata.theme,
            palette,
        )
    return _draw_background(
        metadata.design,
        metadata.width,
        metadata.height,
        metadata.theme,
        palette,
    )


def _draw_themed_icon(
    design: str,
    theme: str,
    palette: ThemePalette,
) -> PixelArt:
    """Recolor a familiar icon and add one theme-specific visual signature."""
    art = _draw_icon(design)
    replacements = {
        WHITE: palette.text,
        CYAN: palette.primary,
        GREEN: palette.success,
        YELLOW: palette.secondary,
        ORANGE: palette.accent,
        MAGENTA: palette.danger,
    }
    art.pixels = [replacements.get(pixel, pixel) for pixel in art.pixels]
    if theme == "industrial":
        art.set_pixel(1, 14, palette.secondary)
        art.set_pixel(2, 14, palette.secondary)
    elif theme == "creative":
        art.set_pixel(1, 2, palette.accent)
        art.set_pixel(2, 1, palette.secondary)
    elif theme == "playful":
        art.set_pixel(1, 1, palette.secondary)
        art.set_pixel(14, 2, palette.accent)
    elif theme == "feminine":
        art.set_pixel(1, 13, palette.primary)
        art.set_pixel(2, 14, palette.secondary)
        art.set_pixel(3, 13, palette.primary)
    else:
        art.draw_line(1, 14, 4, 14, palette.secondary)
    return art


def _draw_button(
    design: str,
    width: int,
    height: int,
    theme: str,
    palette: ThemePalette,
) -> PixelArt:
    """Draw a text-free button skin ready for a project-specific label."""
    art = PixelArt(width, height)
    role = design.split()[0]
    fill = {
        "Primary": palette.primary,
        "Secondary": palette.secondary,
        "Success": palette.success,
        "Danger": palette.danger,
        "Outline": palette.background,
        "Ghost": palette.surface,
    }[role]
    border = palette.text if role in {"Outline", "Ghost"} else palette.surface_alt
    _rounded_box(art, 1, 1, width - 2, height - 2, fill, border)
    if role == "Outline":
        art.draw_rectangle(3, 3, width - 6, height - 6, palette.background, True)
    elif role == "Ghost":
        art.draw_rectangle(2, height - 3, width - 4, 1, palette.primary, True)
    else:
        art.draw_line(4, 3, width - 5, 3, palette.text)
        art.draw_line(4, height - 4, width - 5, height - 4, palette.surface)
    _decorate_surface(art, theme, palette, inset=4)
    return art


def _draw_widget(
    design: str,
    width: int,
    height: int,
    theme: str,
    palette: ThemePalette,
) -> PixelArt:
    """Draw one reusable, text-free widget skin."""
    art = PixelArt(width, height)
    if design.startswith("Status Bar"):
        art.draw_rectangle(0, 0, width, height, palette.surface, True)
        art.draw_rectangle(0, height - 2, width, 2, palette.primary, True)
        for offset in range(3):
            art.draw_rectangle(width - 9 - offset * 5, 4, 3, height - 8, palette.text, True)
        art.draw_circle(7, height // 2, 3, palette.secondary, True)
    elif design.startswith("Card"):
        _rounded_box(art, 1, 1, width - 2, height - 2, palette.surface, palette.surface_alt)
        art.draw_rectangle(8, 8, max(14, width // 4), max(14, height - 16), palette.primary, True)
        for y, shrink in ((9, 18), (15, 28), (height - 11, 38)):
            color = palette.text if y == 9 else palette.muted
            art.draw_line(width // 3, y, width - shrink, y, color)
    elif design == "List Row":
        art.draw_rectangle(0, 0, width, height, palette.surface, True)
        art.draw_rectangle(0, height - 1, width, 1, palette.surface_alt, True)
        art.draw_circle(9, height // 2, 4, palette.primary, True)
        art.draw_line(18, height // 2 - 3, width - 12, height // 2 - 3, palette.text)
        art.draw_line(18, height // 2 + 3, width - 28, height // 2 + 3, palette.muted)
    elif design == "Progress":
        _rounded_box(art, 0, 2, width, height - 4, palette.surface, palette.surface_alt)
        _rounded_box(
            art,
            2,
            4,
            max(6, (width - 4) * 3 // 5),
            height - 8,
            palette.primary,
            palette.primary,
        )
    elif design == "Toggle":
        _rounded_box(art, 1, 1, width - 2, height - 2, palette.primary, palette.surface_alt)
        art.draw_circle(
            width - height // 2,
            height // 2,
            max(2, height // 2 - 4),
            palette.text,
            True,
        )
    elif design == "Slider":
        art.draw_rectangle(4, height // 2 - 1, width - 8, 3, palette.surface_alt, True)
        art.draw_rectangle(4, height // 2 - 1, width * 2 // 3, 3, palette.primary, True)
        art.draw_circle(width * 2 // 3, height // 2, 5, palette.text, True)
        art.draw_circle(width * 2 // 3, height // 2, 5, palette.primary)
    elif design == "Input":
        _rounded_box(art, 1, 1, width - 2, height - 2, palette.background, palette.muted)
        art.draw_line(8, height // 2, width - 18, height // 2, palette.muted)
        art.draw_line(7, 5, 7, height - 6, palette.primary)
    elif design == "Dialog":
        _rounded_box(art, 1, 1, width - 2, height - 2, palette.surface, palette.surface_alt)
        art.draw_rectangle(2, 2, width - 4, 14, palette.primary, True)
        art.draw_line(10, 25, width - 10, 25, palette.text)
        art.draw_line(10, 31, width - 28, 31, palette.muted)
        _rounded_box(art, width - 45, height - 19, 35, 12, palette.secondary, palette.secondary)
    elif design == "Metric":
        _rounded_box(art, 1, 1, width - 2, height - 2, palette.surface, palette.surface_alt)
        art.draw_line(9, 13, width - 10, 13, palette.muted)
        art.draw_rectangle(9, 22, width // 2, 10, palette.primary, True)
        art.draw_line(9, height - 9, width - 24, height - 9, palette.text)
    else:  # Navigation
        art.draw_rectangle(0, 0, width, height, palette.surface, True)
        slot = width // 4
        for index in range(4):
            cx = slot * index + slot // 2
            art.draw_circle(cx, 8, 3, palette.primary if index == 0 else palette.muted, True)
            art.draw_line(cx - 7, 16, cx + 7, 16, palette.text if index == 0 else palette.muted)
        art.draw_rectangle(3, height - 2, slot - 6, 2, palette.primary, True)
    _decorate_surface(art, theme, palette, inset=2)
    return art


def _draw_background(
    design: str,
    width: int,
    height: int,
    theme: str,
    palette: ThemePalette,
) -> PixelArt:
    """Draw an opaque device-sized background with restrained theme detail."""
    art = PixelArt(width, height, pixels=[palette.background] * (width * height))
    if design.endswith("Grid"):
        for x in range(0, width, 32):
            art.draw_line(x, 0, x, height - 1, palette.surface)
        for y in range(0, height, 32):
            art.draw_line(0, y, width - 1, y, palette.surface)
        for x in range(0, width, 64):
            for y in range(0, height, 64):
                art.draw_rectangle(x, y, 3, 3, palette.primary, True)
    elif design.endswith("Panel"):
        margin = 16
        art.draw_rectangle(
            margin,
            margin,
            width - margin * 2,
            height - margin * 2,
            palette.surface,
            True,
        )
        art.draw_rectangle(
            margin,
            margin,
            width - margin * 2,
            height - margin * 2,
            palette.surface_alt,
        )
        art.draw_rectangle(margin, margin, width - margin * 2, 5, palette.primary, True)
        for y in range(margin + 24, height - margin, 32):
            art.draw_line(margin + 12, y, width - margin - 12, y, palette.muted)
    elif design.endswith("Stripes"):
        for start in range(-height, width, 28):
            for offset in range(7):
                art.draw_line(
                    start + offset,
                    0,
                    start + height + offset,
                    height - 1,
                    palette.surface,
                )
        art.draw_rectangle(0, height - 6, width, 6, palette.primary, True)
    elif design.endswith("Horizon"):
        art.draw_rectangle(0, height // 2, width, height - height // 2, palette.surface, True)
        art.draw_line(0, height // 2, width - 1, height // 2, palette.primary)
        for x in range(0, width, 24):
            art.draw_line(width // 2, height // 2, x, height - 1, palette.surface_alt)
        for y in range(height // 2 + 14, height, 14):
            art.draw_line(0, y, width - 1, y, palette.surface_alt)
    elif design.endswith("Matrix"):
        for x in range(4, width, 8):
            for y in range(4, height, 8):
                color = palette.primary if (x + y) % 24 == 0 else palette.surface
                art.draw_rectangle(x, y, 2, 2, color, True)
        art.draw_rectangle(0, 0, width, height, palette.surface_alt)
    else:  # Round Rings
        radius = min(width, height) // 2 - 8
        rings = (
            (0, palette.surface),
            (24, palette.surface_alt),
            (48, palette.primary),
        )
        for offset, color in rings:
            art.draw_circle(width // 2, height // 2, max(4, radius - offset), color)
        art.draw_line(width // 2, 8, width // 2, height - 9, palette.surface)
        art.draw_line(8, height // 2, width - 9, height // 2, palette.surface)
    _decorate_background(art, theme, palette)
    return art


def _rounded_box(
    art: PixelArt,
    x: int,
    y: int,
    width: int,
    height: int,
    fill: int,
    border: int,
) -> None:
    """Draw a compact two-pixel-radius box on a transparent surface."""
    if width < 3 or height < 3:
        art.draw_rectangle(x, y, width, height, fill, True)
        return
    art.draw_rectangle(x + 1, y, width - 2, height, fill, True)
    art.draw_rectangle(x, y + 1, width, height - 2, fill, True)
    art.draw_line(x + 2, y, x + width - 3, y, border)
    art.draw_line(x + 2, y + height - 1, x + width - 3, y + height - 1, border)
    art.draw_line(x, y + 2, x, y + height - 3, border)
    art.draw_line(x + width - 1, y + 2, x + width - 1, y + height - 3, border)


def _decorate_surface(
    art: PixelArt,
    theme: str,
    palette: ThemePalette,
    *,
    inset: int,
) -> None:
    """Apply a quiet theme signature without baking content text into a skin."""
    if theme == "industrial":
        y = art.height - inset - 1
        art.draw_line(
            inset,
            y,
            min(art.width - inset - 1, inset + 8),
            y,
            palette.secondary,
        )
    elif theme == "creative":
        art.draw_line(
            art.width - inset - 8,
            inset,
            art.width - inset - 1,
            inset + 5,
            palette.accent,
        )
    elif theme == "playful":
        art.draw_circle(inset + 2, inset + 2, 2, palette.secondary, True)
    elif theme == "feminine":
        art.draw_circle(art.width - inset - 3, inset + 2, 2, palette.secondary)
    else:
        width = min(8, max(1, art.width - inset * 2))
        art.draw_rectangle(inset, inset, width, 2, palette.secondary, True)


def _decorate_background(
    art: PixelArt,
    theme: str,
    palette: ThemePalette,
) -> None:
    """Make every background recognizably part of its visual system."""
    if theme == "industrial":
        for x in range(8, art.width, 48):
            art.draw_rectangle(x, 6, 18, 3, palette.secondary, True)
    elif theme == "creative":
        art.draw_circle(art.width - 30, 28, 14, palette.primary)
        art.draw_circle(art.width - 30, 28, 8, palette.accent)
    elif theme == "playful":
        bubbles = (
            (12, 12, palette.secondary),
            (30, 22, palette.primary),
            (50, 10, palette.accent),
        )
        for x, y, color in bubbles:
            art.draw_circle(x, y, 3, color, True)
    elif theme == "feminine":
        for radius in (4, 8, 12):
            art.draw_circle(18, 18, radius, palette.primary if radius != 8 else palette.secondary)
    else:
        art.draw_line(0, 0, min(72, art.width - 1), 0, palette.secondary)
        art.draw_line(0, 1, min(48, art.width - 1), 1, palette.primary)


def _draw_icon(name: str) -> PixelArt:
    """Draw one recognizable 16 x 16 transparent RGB565 icon."""
    art = PixelArt(16, 16)
    color = _icon_color(name)
    line = art.draw_line
    rect = art.draw_rectangle
    pixel = art.set_pixel

    if name == "Home":
        line(2, 7, 8, 2, color)
        line(8, 2, 14, 7, color)
        rect(4, 7, 9, 7, color)
        rect(7, 10, 3, 4, color, True)
    elif name in {"Back", "Forward"}:
        direction = -1 if name == "Back" else 1
        tip = 2 if direction < 0 else 13
        base = 7 if direction < 0 else 8
        line(tip, 8, base, 3, color)
        line(tip, 8, base, 13, color)
        line(tip, 8, 13 if direction < 0 else 2, 8, color)
        line(base, 4, base, 12, color)
    elif name in {"Up", "Down"}:
        tip_y = 2 if name == "Up" else 13
        base_y = 7 if name == "Up" else 8
        line(8, tip_y, 3, base_y, color)
        line(8, tip_y, 13, base_y, color)
        line(8, tip_y, 8, 13 if name == "Up" else 2, color)
    elif name in {"Left", "Right"}:
        tip_x = 3 if name == "Left" else 12
        base_x = 9 if name == "Left" else 6
        line(tip_x, 8, base_x, 3, color)
        line(tip_x, 8, base_x, 13, color)
    elif name == "Menu":
        for y in (4, 8, 12):
            line(3, y, 13, y, color)
            pixel(2, y, WHITE)
    elif name == "Close":
        line(3, 3, 12, 12, color)
        line(12, 3, 3, 12, color)
        line(4, 3, 12, 11, color)
    elif name == "Check":
        line(2, 8, 6, 12, color)
        line(6, 12, 14, 3, color)
        line(2, 9, 6, 13, color)
    elif name == "Plus":
        rect(7, 2, 3, 12, color, True)
        rect(2, 7, 12, 3, color, True)
    elif name == "Minus":
        rect(2, 7, 12, 3, color, True)
    elif name == "Search":
        _circle(art, 6, 6, 4, color)
        line(9, 9, 14, 14, color)
        line(10, 9, 14, 13, color)
    elif name == "Settings":
        _circle(art, 8, 8, 3, color)
        _circle(art, 8, 8, 1, WHITE)
        for x1, y1, x2, y2 in (
            (8, 1, 8, 4),
            (8, 12, 8, 15),
            (1, 8, 4, 8),
            (12, 8, 15, 8),
            (3, 3, 5, 5),
            (11, 11, 13, 13),
            (13, 3, 11, 5),
            (3, 13, 5, 11),
        ):
            line(x1, y1, x2, y2, color)
    elif name == "Info":
        _circle(art, 8, 8, 6, color)
        rect(7, 7, 2, 5, WHITE, True)
        rect(7, 4, 2, 2, WHITE, True)
    elif name == "Warning":
        line(8, 1, 1, 14, color)
        line(8, 1, 15, 14, color)
        line(1, 14, 15, 14, color)
        rect(7, 6, 2, 5, WHITE, True)
        pixel(8, 12, WHITE)
    elif name == "Help":
        _circle(art, 8, 8, 6, color)
        line(5, 6, 6, 4, WHITE)
        line(6, 4, 10, 4, WHITE)
        line(10, 4, 11, 6, WHITE)
        line(11, 6, 8, 9, WHITE)
        pixel(8, 12, WHITE)
    elif name == "Save":
        rect(2, 2, 12, 12, color)
        rect(4, 2, 7, 4, WHITE, True)
        rect(5, 9, 6, 5, color, True)
        rect(6, 10, 4, 4, WHITE)
    elif name == "Open Folder":
        line(1, 5, 6, 5, color)
        line(3, 3, 7, 3, color)
        line(7, 3, 9, 6, color)
        rect(1, 5, 14, 9, color)
        line(3, 8, 14, 8, WHITE)
        line(1, 13, 3, 8, color)
    elif name == "File":
        rect(3, 1, 10, 14, color)
        line(8, 1, 13, 6, color)
        line(8, 1, 8, 6, color)
        line(8, 6, 13, 6, color)
        line(5, 10, 11, 10, WHITE)
        line(5, 12, 10, 12, WHITE)
    elif name == "Trash":
        rect(4, 5, 8, 10, color)
        line(3, 4, 13, 4, color)
        line(6, 2, 10, 2, color)
        line(7, 7, 7, 12, WHITE)
        line(9, 7, 9, 12, WHITE)
    elif name == "Edit":
        line(2, 13, 5, 10, color)
        line(5, 10, 12, 3, color)
        line(12, 3, 14, 5, color)
        line(14, 5, 7, 12, color)
        line(2, 13, 2, 15, WHITE)
        line(2, 15, 4, 15, WHITE)
    elif name == "Copy":
        rect(5, 2, 9, 10, color)
        rect(2, 5, 9, 10, WHITE)
    elif name in {"Download", "Upload"}:
        down = name == "Download"
        tip_y = 12 if down else 3
        base_y = 8 if down else 7
        line(8, 2 if down else 13, 8, tip_y, color)
        line(4, base_y, 8, tip_y, color)
        line(12, base_y, 8, tip_y, color)
        line(2, 14 if down else 1, 14, 14 if down else 1, WHITE)
    elif name == "Refresh":
        _circle(art, 8, 8, 5, color)
        rect(9, 1, 5, 3, color, True)
        line(14, 1, 14, 6, color)
        rect(2, 12, 5, 3, WHITE, True)
        line(2, 9, 2, 14, WHITE)
    elif name == "Play":
        for x in range(4, 12):
            for y in range(3 + (x - 4) // 2, 14 - (x - 4) // 2):
                pixel(x, y, color)
    elif name == "Pause":
        rect(3, 3, 4, 10, color, True)
        rect(10, 3, 4, 10, color, True)
    elif name == "Stop":
        rect(3, 3, 10, 10, color, True)
    elif name == "Record":
        _filled_circle(art, 8, 8, 5, color)
    elif name in {"Volume", "Mute"}:
        rect(2, 6, 4, 5, color, True)
        line(6, 6, 10, 3, color)
        line(6, 10, 10, 13, color)
        line(10, 3, 10, 13, color)
        if name == "Volume":
            _arc_right(art, 10, 8, 3, color)
            _arc_right(art, 10, 8, 5, WHITE)
        else:
            line(11, 5, 15, 11, WHITE)
            line(15, 5, 11, 11, WHITE)
    elif name == "Wi-Fi":
        line(2, 6, 4, 4, color)
        line(4, 4, 12, 4, color)
        line(12, 4, 14, 6, color)
        line(4, 9, 6, 7, color)
        line(6, 7, 10, 7, color)
        line(10, 7, 12, 9, color)
        line(6, 12, 8, 10, color)
        line(8, 10, 10, 12, color)
        pixel(8, 14, WHITE)
    elif name == "Bluetooth":
        line(7, 1, 12, 6, color)
        line(12, 6, 5, 12, color)
        line(7, 1, 7, 15, color)
        line(7, 15, 12, 10, color)
        line(12, 10, 4, 4, color)
    elif name == "Battery":
        rect(2, 4, 12, 8, color)
        rect(14, 6, 2, 4, color, True)
        rect(4, 6, 7, 4, GREEN, True)
    elif name == "Power":
        _circle(art, 8, 9, 6, color)
        rect(7, 1, 3, 8, WHITE, True)
        pixel(8, 9, color)
    elif name in {"Lock", "Unlock"}:
        rect(3, 7, 10, 8, color, True)
        if name == "Lock":
            _top_arc(art, 8, 7, 4, color)
        else:
            _top_arc(art, 10, 7, 4, color)
            line(6, 4, 6, 7, color)
        pixel(8, 10, WHITE)
        line(8, 10, 8, 12, WHITE)
    elif name == "User":
        _filled_circle(art, 8, 5, 3, color)
        _top_arc(art, 8, 15, 6, color)
        line(2, 14, 14, 14, color)
    elif name == "Users":
        _filled_circle(art, 6, 5, 3, color)
        _filled_circle(art, 11, 6, 2, WHITE)
        _top_arc(art, 6, 15, 5, color)
        _top_arc(art, 11, 14, 4, WHITE)
    elif name == "Heart":
        line(2, 6, 5, 3, color)
        line(5, 3, 8, 6, color)
        line(8, 6, 11, 3, color)
        line(11, 3, 14, 6, color)
        line(2, 6, 8, 14, color)
        line(14, 6, 8, 14, color)
        _flood_rows(art, ((4, 3, 9), (5, 2, 11), (6, 2, 13), (7, 3, 12)), color)
    elif name == "Star":
        points = (
            (8, 1),
            (10, 6),
            (15, 6),
            (11, 9),
            (13, 14),
            (8, 11),
            (3, 14),
            (5, 9),
            (1, 6),
            (6, 6),
            (8, 1),
        )
        _polyline(art, points, color)
        pixel(8, 7, WHITE)
    elif name == "Clock":
        _circle(art, 8, 8, 6, color)
        line(8, 8, 8, 4, WHITE)
        line(8, 8, 12, 10, WHITE)
    elif name == "Calendar":
        rect(2, 3, 12, 12, color)
        rect(2, 3, 12, 4, color, True)
        line(5, 1, 5, 5, WHITE)
        line(11, 1, 11, 5, WHITE)
        for x, y in ((5, 9), (8, 9), (11, 9), (5, 12), (8, 12), (11, 12)):
            pixel(x, y, color)
    elif name == "Camera":
        rect(1, 5, 14, 9, color)
        rect(5, 3, 6, 3, color, True)
        _circle(art, 8, 9, 3, WHITE)
        pixel(13, 7, WHITE)
    elif name == "Image":
        rect(1, 2, 14, 12, color)
        _filled_circle(art, 11, 5, 2, YELLOW)
        line(2, 13, 6, 8, GREEN)
        line(6, 8, 9, 11, GREEN)
        line(9, 11, 12, 8, GREEN)
        line(12, 8, 15, 12, GREEN)
    elif name == "Mail":
        rect(1, 3, 14, 10, color)
        line(1, 3, 8, 9, WHITE)
        line(15, 3, 8, 9, WHITE)
        line(1, 13, 6, 8, color)
        line(15, 13, 10, 8, color)
    elif name == "Phone":
        line(4, 2, 2, 5, color)
        line(2, 5, 6, 10, color)
        line(6, 10, 11, 14, color)
        line(11, 14, 14, 11, color)
        rect(3, 2, 4, 3, color, True)
        rect(11, 10, 3, 4, color, True)
    elif name == "Location":
        _circle(art, 8, 6, 5, color)
        line(3, 8, 8, 15, color)
        line(13, 8, 8, 15, color)
        _filled_circle(art, 8, 6, 2, WHITE)
    elif name == "Globe":
        _circle(art, 8, 8, 6, color)
        line(2, 8, 14, 8, color)
        line(3, 5, 13, 5, WHITE)
        line(3, 11, 13, 11, WHITE)
        line(8, 2, 8, 14, color)
        _top_arc(art, 8, 14, 4, WHITE)
        _bottom_arc(art, 8, 2, 4, WHITE)
    return art


def _icon_color(name: str) -> int:
    """Use a consistent category color while preserving familiar silhouettes."""
    if name in {"Warning", "Help", "Info", "Star", "Battery"}:
        return YELLOW
    if name in {"Play", "Pause", "Stop", "Record", "Volume", "Mute"}:
        return GREEN
    if name in {"Mail", "Phone", "Location", "Globe", "Wi-Fi", "Bluetooth"}:
        return CYAN
    if name in {"Close", "Trash", "Power", "Lock", "Unlock"}:
        return MAGENTA
    if name in {"Save", "Open Folder", "File", "Copy", "Download", "Upload"}:
        return ORANGE
    return WHITE


def _circle(art: PixelArt, cx: int, cy: int, radius: int, color: int) -> None:
    """Draw a compact midpoint circle."""
    x = radius
    y = 0
    error = 1 - radius
    while x >= y:
        for px, py in (
            (cx + x, cy + y),
            (cx + y, cy + x),
            (cx - y, cy + x),
            (cx - x, cy + y),
            (cx - x, cy - y),
            (cx - y, cy - x),
            (cx + y, cy - x),
            (cx + x, cy - y),
        ):
            art.set_pixel(px, py, color)
        y += 1
        if error < 0:
            error += 2 * y + 1
        else:
            x -= 1
            error += 2 * (y - x) + 1


def _filled_circle(art: PixelArt, cx: int, cy: int, radius: int, color: int) -> None:
    """Draw a small filled disc."""
    for y in range(cy - radius, cy + radius + 1):
        span = int(max(0, radius * radius - (y - cy) ** 2) ** 0.5)
        art.draw_line(cx - span, y, cx + span, y, color)


def _arc_right(art: PixelArt, cx: int, cy: int, radius: int, color: int) -> None:
    """Draw the right half of a circle."""
    points = []
    for y in range(cy - radius, cy + radius + 1):
        x = round((max(0, radius * radius - (y - cy) ** 2)) ** 0.5)
        points.append((cx + x, y))
    _polyline(art, tuple(points), color)


def _top_arc(art: PixelArt, cx: int, baseline: int, radius: int, color: int) -> None:
    """Draw a top semicircle ending at one baseline."""
    points = []
    for x in range(cx - radius, cx + radius + 1):
        y = baseline - round((max(0, radius * radius - (x - cx) ** 2)) ** 0.5)
        points.append((x, y))
    _polyline(art, tuple(points), color)


def _bottom_arc(art: PixelArt, cx: int, baseline: int, radius: int, color: int) -> None:
    """Draw a bottom semicircle starting at one baseline."""
    points = []
    for x in range(cx - radius, cx + radius + 1):
        y = baseline + round((max(0, radius * radius - (x - cx) ** 2)) ** 0.5)
        points.append((x, y))
    _polyline(art, tuple(points), color)


def _polyline(art: PixelArt, points: tuple[tuple[int, int], ...], color: int) -> None:
    """Draw connected line segments."""
    for start, end in zip(points, points[1:]):
        art.draw_line(start[0], start[1], end[0], end[1], color)


def _flood_rows(
    art: PixelArt,
    rows: tuple[tuple[int, int, int], ...],
    color: int,
) -> None:
    """Fill inclusive horizontal spans."""
    for y, start, end in rows:
        art.draw_line(start, y, end, y, color)
