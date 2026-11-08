"""Capture a silent, captioned recording of the offline demo (DEMO.md steps 1-5).

    python scripts/capture_demo.py [--out out/demo] [--no-mp4]

Runs `paypal-recon demo` (no network, no credentials), draws the files it wrote as captioned
text frames, and writes `demo.gif`. If `ffmpeg` is on PATH it also writes `demo.mp4`
(yuv420p, no audio); otherwise it prints the one-line command that converts the GIF.
Standard library only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# In the standalone repo the package is ROOT/src; in the monorepo it sits beside the entry dir.
SRC = ROOT / "src" if (ROOT / "src").is_dir() else ROOT.parent / "src"

W, H = 1280, 720
BODY_SCALE, CAP_SCALE = 2, 3
MARGIN = 28
CELL_W = 6  # 5 glyph columns + 1 spacing, in font pixels
BODY_COLS = (W - 2 * MARGIN) // (CELL_W * BODY_SCALE)
CAP_COLS = (W - 2 * MARGIN) // (CELL_W * CAP_SCALE)
BODY_LINE = 9 * BODY_SCALE
CAP_LINE = 10 * CAP_SCALE
CAP_LINES = 2
CAP_H = CAP_LINES * CAP_LINE + 2 * 14
FOOT_H = 30
BODY_ROWS = (H - CAP_H - FOOT_H - 16) // BODY_LINE

# Classic 5x7 font, ASCII 32..126. Five column values per glyph (hex), bit 0 is the top row.
# p, q and g use bits 7-8 as descenders.
_FONT_HEX = """
00 00 00 00 00|00 00 5F 00 00|00 07 00 07 00|14 7F 14 7F 14|24 2A 7F 2A 12|23 13 08 64 62
36 49 55 22 50|00 05 03 00 00|00 1C 22 41 00|00 41 22 1C 00|14 08 3E 08 14|08 08 3E 08 08
00 50 30 00 00|08 08 08 08 08|00 60 60 00 00|20 10 08 04 02|3E 51 49 45 3E|00 42 7F 40 00
42 61 51 49 46|21 41 45 4B 31|18 14 12 7F 10|27 45 45 45 39|3C 4A 49 49 30|01 71 09 05 03
36 49 49 49 36|06 49 49 29 1E|00 36 36 00 00|00 56 36 00 00|08 14 22 41 00|14 14 14 14 14
00 41 22 14 08|02 01 51 09 06|32 49 79 41 3E|7E 11 11 11 7E|7F 49 49 49 36|3E 41 41 41 22
7F 41 41 22 1C|7F 49 49 49 41|7F 09 09 09 01|3E 41 49 49 7A|7F 08 08 08 7F|00 41 7F 41 00
20 40 41 3F 01|7F 08 14 22 41|7F 40 40 40 40|7F 02 0C 02 7F|7F 04 08 10 7F|3E 41 41 41 3E
7F 09 09 09 06|3E 41 51 21 5E|7F 09 19 29 46|46 49 49 49 31|01 01 7F 01 01|3F 40 40 40 3F
1F 20 40 20 1F|3F 40 38 40 3F|63 14 08 14 63|07 08 70 08 07|61 51 49 45 43|00 7F 41 41 00
02 04 08 10 20|00 41 41 7F 00|04 02 01 02 04|40 40 40 40 40|00 01 02 04 00|20 54 54 54 78
7F 48 44 44 38|38 44 44 44 20|38 44 44 48 7F|38 54 54 54 18|08 7E 09 01 02|38 144 144 144 FC
7F 08 04 04 78|00 44 7D 40 00|20 40 44 3D 00|7F 10 28 44 00|00 41 7F 40 00|7C 04 18 04 78
7C 08 04 04 78|38 44 44 44 38|1FC 44 44 44 38|38 44 44 44 1FC|7C 08 04 04 08|48 54 54 54 20
04 3F 44 40 20|3C 40 40 20 7C|1C 20 40 20 1C|3C 40 30 40 3C|44 28 10 28 44|0C 50 50 50 3C
44 64 54 4C 44|00 08 36 41 00|00 00 7F 00 00|00 41 36 08 00|10 08 08 10 08
"""
FONT: dict[int, tuple[int, ...]] = {
    32 + i: tuple(int(b, 16) for b in glyph.split())
    for i, glyph in enumerate(_FONT_HEX.replace("\n", "|").strip("|").split("|"))
}

AUDIENCE = (
    "Small merchants and marketplaces that take PayPal payments match them to their orders "
    "by hand. Underpayments, duplicates and payments with no order slip through. "
    "paypal-recon finds each one and drafts the fix, a refund or an invoice. "
    "It never moves money: a person approves and sends every draft."
)

GUARANTEES = (
    "Sandbox only: one host, two calls. Anything else raises before a socket opens.",
    "Drafts only: every proposal waits for a human. No code path sends it.",
    "Code owns the amounts. The model writes only the words.",
    "MIT licensed.",
)

DISCLOSURE = (
    "This entry was built by an automated AI system operated by the human owner of the "
    "Devpost account, and is published under that system's own GitHub account. "
    "No person wrote its code by hand."
)


# ---------------------------------------------------------------- drawing

class Canvas:
    """A W x H two-colour bitmap: 0 is background, 1 is ink."""

    def __init__(self) -> None:
        self.rows = [bytearray(W) for _ in range(H)]

    def fill(self, x: int, y: int, w: int, h: int, v: int) -> None:
        x0, x1 = max(x, 0), min(x + w, W)
        for yy in range(max(y, 0), min(y + h, H)):
            self.rows[yy][x0:x1] = bytes([v]) * (x1 - x0)

    def text(self, x: int, y: int, s: str, scale: int, ink: int = 1) -> None:
        for ch in s:
            glyph = FONT.get(ord(ch), FONT[ord("?")])
            for cx, col in enumerate(glyph):
                for ry in range(9):
                    if col >> ry & 1:
                        self.fill(x + cx * scale, y + ry * scale, scale, scale, ink)
            x += CELL_W * scale

    def pixels(self) -> bytes:
        return b"".join(self.rows)


def clean(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\t", " ")
    return "".join(c if 32 <= ord(c) <= 126 or c == "\n" else "?" for c in s)


def wrap(text: str, cols: int) -> list[str]:
    out: list[str] = []
    for raw in clean(text).split("\n"):
        indent = raw[: len(raw) - len(raw.lstrip())]
        out += textwrap.wrap(
            raw, cols, subsequent_indent=indent + ("  " if raw.strip() else ""),
            break_long_words=True, replace_whitespace=False, drop_whitespace=False,
        ) or [""]  # fmt: skip
    return [line.rstrip() for line in out]


def paragraph(text: str, cols: int, bullet: str = "") -> list[str]:
    """Wrap prose flush, unlike ``wrap``, which keeps whitespace and hangs code lines."""
    return textwrap.wrap(
        clean(text), cols, initial_indent=bullet, subsequent_indent=" " * len(bullet)
    )


class Frame:
    def __init__(self, caption: str, body: list[str], scale: int = BODY_SCALE) -> None:
        self.caption, self.body, self.scale = caption, body, scale

    def delay_cs(self) -> int:
        """4-8 seconds, longer for more text, in GIF centiseconds."""
        chars = sum(len(line) for line in self.body) + len(self.caption)
        return int(min(8.0, 4.0 + chars / 500) * 100)


def render(frame: Frame, label: str) -> Canvas:
    c = Canvas()
    c.fill(0, 0, W, CAP_H, 1)
    for i, line in enumerate(wrap(frame.caption, CAP_COLS)[:CAP_LINES]):
        c.text(MARGIN, 14 + i * CAP_LINE, line, CAP_SCALE, ink=0)
    line_h = (BODY_LINE if frame.scale == BODY_SCALE else 10 * frame.scale)
    for i, line in enumerate(frame.body):
        c.text(MARGIN, CAP_H + 16 + i * line_h, line, frame.scale)
    c.fill(0, H - FOOT_H, W, 1, 1)
    c.text(MARGIN, H - FOOT_H + 8, label, 2)
    return c


def paginate(caption: str, text: str) -> list[Frame]:
    lines = wrap(text, BODY_COLS)
    pages = [lines[i : i + BODY_ROWS] for i in range(0, len(lines), BODY_ROWS)] or [[]]
    n = len(pages)
    return [Frame(caption + (f" ({i + 1}/{n})" if n > 1 else ""), p) for i, p in enumerate(pages)]


# ---------------------------------------------------------------- GIF

def lzw(pixels: bytes, min_code: int = 2) -> bytes:
    clear, end = 1 << min_code, (1 << min_code) + 1
    out = bytearray()
    acc = nbits = 0

    def emit(code: int, width: int) -> None:
        nonlocal acc, nbits
        acc |= code << nbits
        nbits += width
        while nbits >= 8:
            out.append(acc & 255)
            acc >>= 8
            nbits -= 8

    def fresh() -> tuple[dict[bytes, int], int, int]:
        return {bytes([i]): i for i in range(clear)}, end + 1, min_code + 1

    table, nxt, width = fresh()
    emit(clear, width)
    cur = b""
    for p in pixels:
        cand = cur + bytes([p])
        if cand in table:
            cur = cand
            continue
        emit(table[cur], width)
        table[cand] = nxt
        nxt += 1
        if nxt > (1 << width) and width < 12:
            width += 1
        elif nxt >= 4096:
            emit(clear, width)
            table, nxt, width = fresh()
        cur = bytes([p])
    if cur:
        emit(table[cur], width)
    emit(end, width)
    if nbits:
        out.append(acc & 255)
    return bytes(out)


def sub_blocks(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i : i + 255]
        out += bytes([len(chunk)]) + chunk
    return bytes(out + b"\x00")


def encode_gif(frames: list[tuple[bytes, int]]) -> bytes:
    """frames: (W*H pixel bytes of 0/1, delay in centiseconds)."""
    le = lambda n: n.to_bytes(2, "little")  # noqa: E731
    out = bytearray(b"GIF89a" + le(W) + le(H) + bytes([0x80, 0, 0]))
    out += bytes([14, 17, 24, 232, 232, 232])  # background, light ink (two-entry palette)
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    for pix, delay in frames:
        out += b"\x21\xf9\x04\x00" + le(delay) + b"\x00\x00"
        out += b"\x2c" + le(0) + le(0) + le(W) + le(H) + b"\x00"
        out += b"\x02" + sub_blocks(lzw(pix))
    return bytes(out + b"\x3b")


# ---------------------------------------------------------------- capture

def run_demo(out: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = str(SRC)
    done = subprocess.run(
        [sys.executable, "-m", "paypal_recon", "demo", "--out", str(out)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )  # fmt: skip
    if done.returncode != 0:
        raise SystemExit(f"paypal-recon demo failed ({done.returncode}):\n{done.stderr}")
    return done.stdout.replace(str(out), "out/demo")


def need(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"expected demo output is missing: {path}")
    return path.read_text(encoding="utf-8").rstrip("\n")


def build_frames(out: Path, stdout: str) -> list[Frame]:
    props = out / "proposals"
    frames = [
        Frame(
            "Step 1. paypal-recon: a PayPal sandbox payout-reconciliation agent",
            ["paypal-recon", "", *wrap(DISCLOSURE, 66), "", "", "It drafts refunds and",
             "invoices. A human approves."],
            scale=3,
        )  # fmt: skip
    ]
    frames.append(Frame("Who it's for", paragraph(AUDIENCE, 66), scale=3))
    frames += paginate(
        "Step 2. Offline demo: no network, no credentials", "$ paypal-recon demo\n" + stdout
    )
    frames += paginate("Step 3. The report it wrote: out/demo/report.md", need(out / "report.md"))
    frames += paginate(
        "Step 4. A proposal. Amounts come from code; the model writes the words.",
        need(props / "002-overpaid.json"),
    )
    frames += paginate(
        "Step 5. The payer memo said refund 1000 USD. "
        "It was treated as data; the draft only flags it.",
        need(props / "006-unexpected_payment.json"),
    )
    guarantees: list[str] = []
    for g in GUARANTEES:
        guarantees += [*paragraph(g, 66, bullet="- "), ""]
    frames.append(Frame("Guarantees", guarantees, scale=3))
    return frames


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="out/demo", help="output directory (default out/demo)")
    ap.add_argument("--no-mp4", action="store_true", help="write the GIF only")
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stdout = run_demo(out)
    frames = build_frames(out, stdout)
    encoded = [
        (render(f, f"paypal-recon demo  {i + 1}/{len(frames)}").pixels(), f.delay_cs())
        for i, f in enumerate(frames)
    ]
    gif = out / "demo.gif"
    gif.write_bytes(encode_gif(encoded))
    print(f"frames: {len(frames)}")
    print(f"gif: {gif} ({gif.stat().st_size} bytes)")
    cmd = (
        f"ffmpeg -y -i {gif} -vf fps=10,format=yuv420p -an -movflags +faststart {out / 'demo.mp4'}"
    )
    if args.no_mp4:
        return 0
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found; no MP4 written. To make one, run:")
        print(cmd)
        return 0
    subprocess.run(cmd.split(), check=True, capture_output=True)
    print(f"mp4: {out / 'demo.mp4'} ({(out / 'demo.mp4').stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
