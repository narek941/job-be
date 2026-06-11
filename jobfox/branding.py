"""JobFox brand assets.

The fox mark is one evenodd path — the outer shield-and-ears silhouette
with the face cut out — so it can be inlined anywhere an <svg> works and
recolored via `fill`. Keep this the single source of truth: the static
/logo.svg and every served HTML page render from these constants.
"""

from __future__ import annotations

# Outer silhouette: left ear tip → notch between the ears (the brow band)
# → right ear tip → shield sides → chin. Inner subpath cuts out the face,
# with 45° chamfers where the band meets the side walls.
LOGO_MARK_PATH = (
    "M0 0 L78 72 L349 72 L427 0 L427 280 L214 445 L0 280 Z "
    "M100 148 L327 148 L355 176 L355 248 L214 372 L72 248 L72 176 Z"
)


def logo_mark_svg(size: int = 48, color: str = "#111") -> str:
    """Inline-able square fox mark (no wordmark)."""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 427 445" '
        'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="JobFox">'
        f'<path d="{LOGO_MARK_PATH}" fill="{color}" fill-rule="evenodd"/>'
        "</svg>"
    )


def logo_full_svg(color: str = "#111") -> str:
    """Mark + FOX wordmark lockup, as shipped in the brand asset."""
    return (
        '<svg width="427" height="640" viewBox="0 0 427 640" '
        'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="JobFox">'
        f'<path d="{LOGO_MARK_PATH}" fill="{color}" fill-rule="evenodd"/>'
        '<text x="213" y="610" text-anchor="middle" '
        'font-family="Futura, \'Century Gothic\', \'Avenir Next\', Verdana, sans-serif" '
        f'font-size="150" font-weight="700" letter-spacing="14" fill="{color}">FOX</text>'
        "</svg>"
    )
