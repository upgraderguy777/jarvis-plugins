# ==============================================================================
# JARVIS Plugin — Screenshot & Annotate
# Brand: Gemind
# Author: upgraderguy777 (https://github.com/upgraderguy777/jarvis-plugins)
# License: MIT License
# Notice: Vibecoded with precision for free-tier users.
# ==============================================================================

"""
JARVIS plugin — Screenshot & Annotate (visual "here's where" teaching).
Takes a screenshot, identifies UI controls via Gemini 2.5 Flash, circles them
with numbered tags, and displays the image directly to the user.
"""

import io
import json
import re
from datetime import datetime
from pathlib import Path

PLUGIN = {
    "name": "screenshot_annotate",
    "version": "1.0.0",
    "author": "Gemind (upgraderguy777)",
    "license": "MIT",
    "repository": "https://github.com/upgraderguy777/jarvis-plugins",
    "description": (
        "Takes a SCREENSHOT of the user's current screen and answers a "
        "'where is / how do I / I can't find X' question by circling the "
        "relevant button/menu/icon directly on the screenshot (numbered in "
        "order if there are multiple steps), describing out loud where it "
        "is, saving the marked-up image, and opening it automatically. Use "
        "for: 'where is the export button', 'how do I turn on dark mode', "
        "'show me how to do X', 'circle the search bar', 'analyze my screen "
        "and help me, I can't find the button', 'ekranda ayarlar nerede', "
        "'bana X'i göster'. This is for VISUALLY POINTING at things on "
        "screen for teaching/how-to purposes, including when the user is "
        "just stuck and asks for general help finding something — use "
        "screen_process instead if the user just wants the screen content "
        "read or explained in words with no drawing. Pass the user's exact "
        "question in 'query'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "The user's exact question about the screen, verbatim, in their own language.",
            }
        },
        "required": ["query"],
    },
}

_MODEL             = "gemini-2.5-flash"
_MAX_UPLOAD_WIDTH  = 1400
_CIRCLE_COLOR      = (255, 90, 40)
_CIRCLE_WIDTH      = 4
_LABEL_BG          = (255, 90, 40)
_LABEL_FG          = (255, 255, 255)


def _get_api_key() -> str:
    try:
        from memory.config_manager import get_gemini_key
        key = get_gemini_key()
        if key:
            return key
    except Exception:
        pass
    try:
        base_dir = Path(__file__).resolve().parent.parent
        cfg = json.loads((base_dir / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return cfg.get("gemini_api_key", "")
    except Exception:
        return ""


def _grab_screenshot():
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(mon)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except ImportError:
        pass
    except Exception:
        return None
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        return img.convert("RGB")
    except Exception:
        return None


def _output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "", (name or "").strip()) or "screenshot"
    return name[:50]


def _open_file(path: Path) -> bool:
    import os as _os
    import platform
    import subprocess
    try:
        system = platform.system()
        if system == "Windows":
            _os.startfile(str(path))
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


def _locate(image, query: str, api_key: str) -> dict:
    from google import genai
    from google.genai import types as gtypes

    w, h = image.size
    upload_img = image
    if w > _MAX_UPLOAD_WIDTH:
        scale = _MAX_UPLOAD_WIDTH / w
        upload_img = image.resize((int(w * scale), int(h * scale)))

    buf = io.BytesIO()
    upload_img.save(buf, format="JPEG", quality=85)
    jpg_bytes = buf.getvalue()

    prompt = (
        "You are looking at a screenshot of the user's computer screen. The "
        f'user asked (respond in the SAME language as this): "{query}"\n'
        "Find the UI element(s) — button, menu item, icon, field — needed to "
        "answer the question, IF they are visible in this screenshot.\n"
        "IMPORTANT: the box must tightly bound ONLY the actual interactive "
        "element that matches the instruction (a button, icon, or field — "
        "usually has its own border/background/label) — not a nearby "
        "heading, file name, thumbnail, or unrelated icon that merely "
        "relates to the same topic.\n"
        "Return ONLY minified JSON, no markdown fences, no extra text, with keys:\n"
        ' "found": true if at least one relevant element is visible in this '
        "screenshot right now, false otherwise,\n"
        ' "spoken_summary": 1-2 short conversational sentences in the user\'s '
        "language that actually DESCRIBE the location in words,\n"
        ' "steps": ordered array of step objects the user should follow, each with:\n'
        '   "instruction": short text describing this step, in the user\'s language,\n'
        '   "box_2d": [ymin,xmin,ymax,xmax] normalized 0-1000 over THIS image, '
        "tightly around the element for this step, IF it is visible in this "
        "screenshot — otherwise null.\n"
        "Keep steps short (max 6)."
    )

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=_MODEL,
        contents=[
            gtypes.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"),
            prompt,
        ],
    )
    text = (resp.text or "").strip()
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    spec = json.loads(text)
    spec["_upload_size"] = upload_img.size
    return spec


def _norm_box_to_px(box_2d, img_w, img_h):
    ymin, xmin, ymax, xmax = [float(v) for v in box_2d]
    return ((xmin / 1000.0) * img_w, (ymin / 1000.0) * img_h,
             (xmax / 1000.0) * img_w, (ymax / 1000.0) * img_h)


def _refine_box(image_full, approx_px_box: tuple, instruction: str, api_key: str) -> tuple:
    x1, y1, x2, y2 = approx_px_box
    bw, bh = max(x2 - x1, 1), max(y2 - y1, 1)
    pad_x, pad_y = max(bw * 2.0, 160), max(bh * 2.0, 160)
    full_w, full_h = image_full.size

    cx1, cy1 = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y))
    cx2, cy2 = min(full_w, int(x2 + pad_x)), min(full_h, int(y2 + pad_y))
    if cx2 - cx1 < 20 or cy2 - cy1 < 20:
        return approx_px_box

    crop = image_full.crop((cx1, cy1, cx2, cy2))
    orig_crop_w, orig_crop_h = crop.size
    send_crop = crop
    if max(orig_crop_w, orig_crop_h) < 700:
        scale = 700 / max(orig_crop_w, orig_crop_h)
        send_crop = crop.resize((int(orig_crop_w * scale), int(orig_crop_h * scale)))

    buf = io.BytesIO()
    send_crop.save(buf, format="JPEG", quality=90)
    jpg_bytes = buf.getvalue()

    prompt = (
        "This is a ZOOMED-IN crop of a larger screenshot, centered on "
        f'roughly where this should be: "{instruction}".\n'
        "Find the EXACT clickable element that matches.\n"
        'Return ONLY minified JSON: {"visible": true/false, "box_2d": '
        "[ymin,xmin,ymax,xmax] normalized 0-1000 over THIS crop, tightly "
        "around just that element — or null if it isn't actually in this crop}."
    )

    try:
        from google import genai
        from google.genai import types as gtypes
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=_MODEL,
            contents=[gtypes.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"), prompt],
        )
        text = (resp.text or "").strip()
        if "{" in text and "}" in text:
            text = text[text.find("{"): text.rfind("}") + 1]
        refined = json.loads(text)
    except Exception:
        return approx_px_box

    rbox = refined.get("box_2d")
    if not refined.get("visible") or not (isinstance(rbox, (list, tuple)) and len(rbox) == 4):
        return approx_px_box

    try:
        rx1, ry1, rx2, ry2 = _norm_box_to_px(rbox, send_crop.size[0], send_crop.size[1])
    except (TypeError, ValueError):
        return approx_px_box

    back_x = orig_crop_w / send_crop.size[0]
    back_y = orig_crop_h / send_crop.size[1]
    return (cx1 + rx1 * back_x, cy1 + ry1 * back_y,
            cx1 + rx2 * back_x, cy1 + ry2 * back_y)


def _draw_annotations(image, steps: list):
    from PIL import ImageDraw, ImageFont

    img = image.copy()
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arialbd.ttf", 28)
    except Exception:
        font = ImageFont.load_default()

    drawn = 0
    for i, step in enumerate(steps, start=1):
        px_box = step.get("_px_box")
        if not px_box:
            continue
        x1, y1, x2, y2 = px_box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        rx, ry = max((x2 - x1) / 2, 22), max((y2 - y1) / 2, 22)

        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                      outline=_CIRCLE_COLOR, width=_CIRCLE_WIDTH)

        label = str(i)
        try:
            text_w = draw.textlength(label, font=font)
        except Exception:
            text_w = 16
        lx = max(0, cx - rx - 4)
        ly = max(0, cy - ry - 34)
        draw.ellipse([lx, ly, lx + text_w + 20, ly + 32], fill=_LABEL_BG)
        draw.text((lx + 10, ly + 3), label, fill=_LABEL_FG, font=font)
        drawn += 1

    return img, drawn


def run(parameters: dict, player=None, session_memory=None) -> str:
    query = (parameters.get("query") or "").strip()
    if not query:
        return "What are you trying to find on screen?"

    try:
        import mss  # noqa: F401
    except ImportError:
        return "I need mss to take screenshots. Run: pip install mss"
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return "I need Pillow to annotate screenshots. Run: pip install Pillow"

    api_key = _get_api_key()
    if not api_key:
        return "I can't analyze the screen — no API key is configured."

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(msg)
            except Exception:
                pass

    shot = _grab_screenshot()
    if shot is None:
        return "I couldn't capture a screenshot, sorry."

    _log("JARVIS: Screen scan started.")

    try:
        spec = _locate(shot, query, api_key)
    except Exception as e:
        return f"I couldn't work out where that is: {e}"

    steps = spec.get("steps") or []
    upload_size = spec.get("_upload_size", shot.size)
    for step in steps:
        box = step.get("box_2d")
        px_box = None
        if isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                approx = _norm_box_to_px(box, *upload_size)
                sx, sy = shot.size[0] / upload_size[0], shot.size[1] / upload_size[1]
                approx = tuple(v * (sx if i % 2 == 0 else sy) for i, v in enumerate(approx))
                px_box = _refine_box(shot, approx, step.get("instruction", ""), api_key)
            except Exception:
                px_box = None
        step["_px_box"] = px_box

    annotated, drawn = _draw_annotations(shot, steps) if steps else (shot, 0)

    fname = _safe_name(query[:40])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _output_dir() / f"{fname}_{stamp}.png"
    opened = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        annotated.save(path)
        opened = _open_file(path)
    except Exception:
        path = None

    if steps:
        panel_lines = []
        for i, step in enumerate(steps, start=1):
            instr = step.get("instruction") or ""
            tag = "" if step.get("_px_box") else "  (not visible on this screen yet)"
            panel_lines.append(f"{i}. {instr}{tag}")
        panel_text = "\n".join(panel_lines)
    else:
        panel_text = "Nothing specific found on this screen."

    if player:
        try:
            player.show_content("🎯 SCREEN GUIDE", panel_text)
        except Exception:
            pass
        if path:
            for meth_name in ("show_image", "display_image", "show_picture"):
                meth = getattr(player, meth_name, None)
                if callable(meth):
                    try:
                        meth(str(path))
                        break
                    except Exception:
                        pass

    _log(f"JARVIS: Screen scan complete — {drawn} element(s) circled"
         f"{' — image opened' if opened else ''}.")

    spoken = (spec.get("spoken_summary") or "").strip()
    if not spoken:
        spoken = ("Here's what I found." if spec.get("found")
                   else "I couldn't find that on your current screen.")
    if path:
        if opened:
            spoken += " I've circled it and opened the screenshot for you."
        else:
            spoken += f" I've saved the marked-up screenshot to your Downloads as {path.name}."

    return spoken