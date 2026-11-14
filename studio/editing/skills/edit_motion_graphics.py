"""edit-motion-graphics — titles, lower-thirds, CTA, logo bug, kinetic text.

Generates a graph fragment [src] -> [dst] (plus extra image inputs for logos).
All text is placed inside the platform safe area (top 10% / bottom 20% are kept
for platform UI on vertical video) so it never collides with subtitles or UI.
"""
from __future__ import annotations

from pydantic import BaseModel

from studio.editing.ffmpeg import default_font, esc_filter_path, esc_text
from studio.editing.schemas import Overlay, OverlayKind
from studio.editing.skills.base import SkillSpec

FADE = 0.3


class GraphicsInput(BaseModel):
    overlays: list[Overlay]
    width: int
    height: int
    first_extra_input: int = 1   # ffmpeg input index the first logo image gets
    src: str = "gin"
    dst: str = "gout"


class GraphicsOutput(BaseModel):
    graph: str
    extra_inputs: list[str]
    warnings: list[str] = []


def _alpha(o: Overlay) -> str:
    s, e = o.start, o.end
    return (f"if(lt(t\\,{s + FADE:.3f})\\,(t-{s:.3f})/{FADE}\\,if(gt(t\\,{e - FADE:.3f})\\,({e:.3f}-t)/{FADE}\\,1))")


def _en(o: Overlay) -> str:
    return f"enable='between(t,{o.start:.3f},{o.end:.3f})'"


def _font() -> str:
    f = default_font()
    return f"fontfile='{esc_filter_path(f)}':" if f else ""


def _text(o: Overlay, text: str, size: int, x: str, y: str, color: str | None = None, box: str = "") -> str:
    return (f"drawtext={_font()}text='{esc_text(text)}':fontsize={size}:fontcolor={color or o.color}:"
            f"x={x}:y={y}:alpha='{_alpha(o)}':borderw={max(2, size // 18)}:bordercolor=black@0.6{box}:{_en(o)}")


def run(inp: GraphicsInput) -> GraphicsOutput:
    W, H = inp.width, inp.height
    u = W / 1080
    vertical = H > W
    top_safe = int(H * (0.10 if vertical else 0.06))
    bottom_safe = int(H * (0.22 if vertical else 0.12))
    chain: list[str] = []
    logos: list[tuple[Overlay, str]] = []
    warnings: list[str] = []
    for o in inp.overlays:
        if o.end <= o.start:
            warnings.append(f"overlay {o.kind.value} has no duration; skipped")
            continue
        if o.kind == OverlayKind.title:
            ypos = {"top": str(top_safe + int(40 * u)), "center": "(h-text_h)/2"}.get(o.position, "(h-text_h)/2")
            chain.append(_text(o, o.text, int(96 * u), "(w-text_w)/2", ypos))
            if o.subtext:
                chain.append(_text(o, o.subtext, int(48 * u), "(w-text_w)/2", f"{ypos}+{int(120 * u)}"))
        elif o.kind == OverlayKind.lower_third:
            y = H - bottom_safe - int(170 * u)
            chain.append(f"drawbox=x={int(48 * u)}:y={y}:w={int(14 * u)}:h={int(130 * u)}:color={o.accent}@0.95:t=fill:{_en(o)}")
            chain.append(_text(o, o.text, int(56 * u), str(int(80 * u)), str(y)))
            if o.subtext:
                chain.append(_text(o, o.subtext, int(38 * u), str(int(80 * u)), str(y + int(76 * u)), "white@0.85"))
        elif o.kind == OverlayKind.cta:
            y = H - bottom_safe - int(260 * u)
            chain.append(_text(o, o.text, int(64 * u), "(w-text_w)/2", str(y), "black",
                               box=f":box=1:boxcolor={o.accent}@0.95:boxborderw={int(28 * u)}"))
        elif o.kind == OverlayKind.kinetic:
            # slide up 60px while fading in: y animates over the first FADE seconds
            base = int(H * 0.38)
            y = f"{base}+{int(60 * u)}*max(0\\,1-(t-{o.start:.3f})/{FADE})"
            chain.append(_text(o, o.text.upper(), int(110 * u), "(w-text_w)/2", y))
        elif o.kind == OverlayKind.logo:
            if not o.image:
                warnings.append("logo overlay without image; skipped")
                continue
            logos.append((o, o.image))
    graph_parts = []
    cur = inp.src
    if chain:
        graph_parts.append(f"[{cur}]{','.join(chain)}[g_txt]")
        cur = "g_txt"
    extra = []
    for k, (o, img) in enumerate(logos):
        idx = inp.first_extra_input + k
        lw = int(W * 0.14)
        m = int(W * 0.04)
        pos = {"top_left": (f"{m}", f"{top_safe}"), "bottom_right": (f"W-w-{m}", f"H-h-{bottom_safe}")}.get(
            o.position, (f"W-w-{m}", f"{top_safe}"))
        graph_parts.append(f"[{idx}:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.85[logo{k}]")
        nxt = f"g_logo{k}"
        graph_parts.append(f"[{cur}][logo{k}]overlay=x={pos[0]}:y={pos[1]}:{_en(o)}[{nxt}]")
        cur = nxt
        extra.append(img)
    graph_parts.append(f"[{cur}]null[{inp.dst}]")
    return GraphicsOutput(graph=";".join(graph_parts), extra_inputs=extra, warnings=warnings)


SPEC = SkillSpec("edit-motion-graphics", "graphics", "Title, lower-third, CTA, logo and kinetic text in safe zones",
                 GraphicsInput, GraphicsOutput, run)
