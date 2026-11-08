import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
# standalone repo: tests/ beside scripts/; monorepo: products/paypal_recon/scripts
_CANDIDATES = (
    _HERE.parents[1] / "scripts" / "capture_demo.py",
    _HERE.parents[2] / "paypal_recon" / "scripts" / "capture_demo.py",
)
SCRIPT = next(p for p in _CANDIDATES if p.is_file())


def _run(out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--no-mp4"],
        capture_output=True, text=True,
    )  # fmt: skip


def _image_descriptors(gif: bytes) -> int:
    """Walk the GIF block structure and count image descriptors."""
    i = 13
    if gif[10] & 0x80:
        i += 3 * (2 << (gif[10] & 7))
    count = 0
    while gif[i] != 0x3B:
        if gif[i] == 0x21:  # extension: label, then sub-blocks
            i += 2
        elif gif[i] == 0x2C:  # image: 9-byte descriptor, min code size, sub-blocks
            count += 1
            i += 10
            if gif[i - 1] & 0x80:
                i += 3 * (2 << (gif[i - 1] & 7))
            i += 1
        else:
            raise AssertionError(f"bad block {gif[i]:#x} at {i}")
        while gif[i]:
            i += gif[i] + 1
        i += 1
    return count


def _load():
    spec = importlib.util.spec_from_file_location("capture_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_font_covers_printable_ascii():
    mod = _load()
    assert set(mod.FONT) == set(range(32, 127))
    assert all(len(g) == 5 for g in mod.FONT.values())


def test_capture_writes_valid_deterministic_gif(tmp_path):
    first = _run(tmp_path / "a")
    assert first.returncode == 0, first.stderr
    gif = (tmp_path / "a" / "demo.gif").read_bytes()
    assert gif.startswith(b"GIF89a") and gif.endswith(b"\x3b")
    assert _image_descriptors(gif) >= 5
    assert not (tmp_path / "a" / "demo.mp4").exists()
    assert _run(tmp_path / "b").returncode == 0
    assert (tmp_path / "b" / "demo.gif").read_bytes() == gif


@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    mod = _load()
    out = tmp_path_factory.mktemp("demo")
    return mod.build_frames(out, mod.run_demo(out))


def test_frames_open_with_disclosure_then_audience_and_close_with_guarantees(frames):
    assert "automated AI system" in " ".join(frames[0].body)
    assert frames[1].caption.startswith("Who it's for")
    assert frames[-1].caption.startswith("Guarantees")
    assert any("sandbox" in line.lower() for line in frames[-1].body)


def test_total_capture_stays_under_the_three_minute_limit(frames):
    # Devpost caps the video at 3 minutes; a guard against pagination growth, not a tight bound
    assert sum(f.delay_cs() for f in frames) < 170 * 100
