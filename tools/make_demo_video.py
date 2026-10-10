"""
Render a synthetic conveyor clip for trying xBAKE without a camera.

Forty textured flatbreads (good, burnt, undercooked, and deformed) travel over a
woven belt in three lanes. A matching config.json is written next to the video.

    python tools/make_demo_video.py demo/
    xbake run -c demo/config.json --mock-hardware

The footage is procedurally generated and deterministic (fixed random seed);
it is meant for demos, screenshots, and smoke tests, not for training models.
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 960, 540, 25
SPEED_PX_PER_FRAME = 4  # 100 px/s at 25 FPS
SPACING_PX = 150
LANES = (135, 270, 405)

BASE_BGR = {
    "good": (95, 165, 215),
    "good2": (80, 150, 205),
    "burnt": (38, 55, 80),
    "raw": (190, 222, 238),
    "deformed": (90, 160, 212),
}
SCHEDULE = 2 * [
    "good",
    "burnt",
    "good2",
    "raw",
    "good",
    "deformed",
    "good2",
    "good",
    "burnt",
    "good",
    "raw",
    "good2",
    "deformed",
    "good",
    "good",
    "burnt",
    "good2",
    "good",
    "raw",
    "good",
]


def make_sprite(rng: np.random.Generator, kind: str, r: int):
    """Return (BGR image, alpha mask) for one product of radius r."""
    size = 2 * r + 20
    c = size // 2
    mask = np.zeros((size, size), np.uint8)
    if kind == "deformed":
        if rng.random() < 0.5:  # squashed
            cv2.ellipse(mask, (c, c), (r, int(r * 0.58)), float(rng.uniform(-20, 20)), 0, 360, 255, -1)
        else:  # broken piece
            cv2.circle(mask, (c, c), r, 255, -1)
            cv2.circle(mask, (c + int(r * 0.8), c - int(r * 0.3)), int(r * 0.75), 0, -1)
    else:
        angles = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        radii = r * (1 + rng.normal(0, 0.025, angles.size))
        pts = np.stack([c + radii * np.cos(angles), c + radii * np.sin(angles)], axis=1)
        cv2.fillPoly(mask, [pts.astype(np.int32)], 255)

    base = np.array(BASE_BGR[kind], np.float32)
    img = np.empty((size, size, 3), np.float32)
    img[:] = base
    # Crust texture: low-frequency browning plus fine grain
    low = cv2.resize(rng.normal(0, 1, (8, 8)).astype(np.float32), (size, size), interpolation=cv2.INTER_CUBIC)
    fine = cv2.GaussianBlur(rng.normal(0, 1, (size, size)).astype(np.float32), (0, 0), 1.2)
    img += (low * 14 + fine * 9)[..., None]
    # Darker rim
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    rim = np.clip(1 - dist / (r * 0.35), 0, 1)
    img -= (rim * (18 if kind == "raw" else 30))[..., None]
    # Bubbles and char spots
    for _ in range({"burnt": 30, "raw": 3}.get(kind, 12)):
        ang, rad = rng.uniform(0, 2 * np.pi), rng.uniform(0, r * 0.8)
        x, y = int(c + rad * np.cos(ang)), int(c + rad * np.sin(ang))
        dark = rng.uniform(15, 35) if kind == "burnt" else rng.uniform(20, 45)
        cv2.circle(img, (x, y), int(rng.integers(2, 6)), tuple(float(v) for v in base - dark), -1)
    img = np.clip(cv2.GaussianBlur(img, (3, 3), 0), 0, 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32) / 255.0
    return img, alpha


def belt_texture(rng: np.random.Generator) -> np.ndarray:
    grain = cv2.GaussianBlur(rng.normal(0, 1, (H, W * 2)).astype(np.float32), (0, 0), 0.8)
    belt = np.full((H, W * 2, 3), (24, 22, 20), np.float32) + (grain * 5)[..., None]
    belt[::6] -= 4  # woven lines
    return np.clip(belt, 0, 255).astype(np.uint8)


def render(out_dir: Path, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    belt = belt_texture(rng)

    items = []
    for i, kind in enumerate(SCHEDULE):
        sprite, alpha = make_sprite(rng, kind, int(rng.integers(52, 60)))
        lane_y = LANES[i % len(LANES)] + int(rng.integers(-12, 12))
        items.append((lane_y, -120 - i * SPACING_PX, sprite, alpha))

    last_start = -120 - (len(SCHEDULE) - 1) * SPACING_PX
    frames = (W + 200 - last_start) // SPEED_PX_PER_FRAME
    vignette = cv2.getGaussianKernel(H, H * 0.9) @ cv2.getGaussianKernel(W, W * 0.9).T
    vignette = (0.8 + 0.2 * vignette / vignette.max())[..., None]

    video_path = out_dir / "demo.avi"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    for f in range(frames):
        off = (f * SPEED_PX_PER_FRAME) % W
        frame = belt[:, W - off : 2 * W - off].astype(np.float32)
        for y, start, sprite, alpha in items:
            s = sprite.shape[0]
            x0, y0 = start + f * SPEED_PX_PER_FRAME - s // 2, y - s // 2
            fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(W, x0 + s), min(H, y0 + s)
            if fx0 >= fx1 or fy0 >= fy1:
                continue
            sp = sprite[fy0 - y0 : fy1 - y0, fx0 - x0 : fx1 - x0].astype(np.float32)
            a = alpha[fy0 - y0 : fy1 - y0, fx0 - x0 : fx1 - x0][..., None]
            frame[fy0:fy1, fx0:fx1] = frame[fy0:fy1, fx0:fx1] * (1 - a) + sp * a
        frame = frame * vignette + rng.normal(0, 1.5, frame.shape)
        writer.write(np.clip(frame, 0, 255).astype(np.uint8))
    writer.release()

    config = {
        "camera": {"source": str(video_path)},
        "vision": {
            "lower_hsv": [0, 0, 45],
            "upper_hsv": [179, 255, 255],
            "morph_cl": [9, 9],
            "min_radius": 30,
            "max_radius": 90,
            "roi_x": 20,
            "roi_y": 30,
            "roi_w": 360,
            "roi_h": 360,
        },
        "inspection": {"min_frames_to_reject": 4},
        "hardware": {
            "driver": "mock",
            "conveyor_speed_px_per_s": 100.0,
            "rejector_trigger_line_x": 720,
            "debounce_s": 0.05,
        },
        "storage": {"db_path": str(out_dir / "xbake.db"), "snapshot_dir": str(out_dir / "snapshots")},
    }
    (out_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    return video_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("out_dir", nargs="?", default="demo", help="Output directory (default: demo)")
    parser.add_argument("--seed", type=int, default=7, help="Random seed")
    args = parser.parse_args()
    video = render(Path(args.out_dir), args.seed)
    counts = {k: SCHEDULE.count(k) for k in ("good", "good2", "burnt", "raw", "deformed")}
    print(f"Wrote {video} and {video.parent / 'config.json'}")
    print(
        f"Expected verdicts: good {counts['good'] + counts['good2']}, burnt {counts['burnt']}, "
        f"undercooked {counts['raw']}, deformed {counts['deformed']}"
    )


if __name__ == "__main__":
    main()
