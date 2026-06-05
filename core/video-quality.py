#!/usr/bin/env python3
"""
Video Quality Assessor — automatic, CPU-friendly quality check.
Samples frames every N seconds and computes:
  - Laplacian variance (blur detection)
  - Canny edge density (high-frequency detail detection)
  - MUSIQ (optional, requires torch + musequal)

Conservative thresholds (auto reject):
  - laplacian_avg < 50  → "too_blurry"
  - musiq_avg < 35      → "low_quality" (if MUSIQ available)
  - edge_density_avg < 0.01 → "soft_or_upscaled"

Usage:
  python3 video-quality.py <video_path> --start 0 --duration 12 --interval 5

Output JSON:
  { "verdict": "accept"|"reject", "laplacian_avg": ..., "musiq_avg": ...,
    "edge_density_avg": ..., "frame_count": N, "rejection_reasons": [...] }
"""
import sys
import json
import os
import argparse
import contextlib
import io

import cv2
import numpy as np

# Optional MUSIQ (only if installed)
MUSIQ_AVAILABLE = False
try:
    import torch
    MUSIQ_MODEL = None
    # Try to import musequal — may fail if not installed
    try:
        from musequal import MUSIQ as MusiQClass
        MUSIQ_AVAILABLE = True
    except ImportError:
        pass
except ImportError:
    pass

# Conservative thresholds (per spec)
LAPLACIAN_REJECT = 50
MUSIQ_REJECT = 35
EDGE_DENSITY_REJECT = 0.01
SAMPLE_INTERVAL = 5  # seconds between frames


def compute_laplacian_variance(frame):
    """Compute Laplacian variance as blur metric. Higher = sharper."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def compute_edge_density(frame):
    """Canny edge density: edge pixels / total pixels. Higher = more detail."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 100, 200)
    edge_pixels = float(edges.sum() / 255)
    total_pixels = float(gray.size)
    return float(edge_pixels / total_pixels)


def compute_musiq_score(frame):
    """MUSIQ quality score. Returns float in [0, 100] or -1 if unavailable."""
    if not MUSIQ_AVAILABLE:
        return -1.0
    global MUSIQ_MODEL
    try:
        if MUSIQ_MODEL is None:
            MUSIQ_MODEL = MusiQClass()
        # musequal expects RGB uint8 tensor [1, 3, H, W]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # Resize to 384px on longest side (MUSIQ standard)
        h, w = rgb.shape[:2]
        if max(h, w) > 384:
            scale = 384.0 / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).float() / 255.0
        with torch.no_grad():
            score = MUSIQ_MODEL(tensor).item()
        return float(score)
    except Exception:
        return -1.0


def sample_frames(video_path, start, duration, interval=5):
    """Extract frames at regular intervals from the video clip."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    if start >= 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)

    fps = max(1.0, cap.get(cv2.CAP_PROP_FPS) or 30.0)
    step_frames = max(1, int(fps * interval))

    total_frames_to_read = max(1, int(duration * fps))
    frames = []
    for idx in range(0, total_frames_to_read, step_frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        frames.append(frame)
        # Safety: limit to 20 frames max
        if len(frames) >= 20:
            break
    cap.release()
    return frames


def assess(video_path, start=0, duration=12, interval=5):
    """
    Run full quality assessment on a video clip.
    Returns dict with verdict and all metrics.
    """
    if not os.path.exists(video_path):
        return {
            "verdict": "reject",
            "laplacian_avg": 0,
            "musiq_avg": -1,
            "edge_density_avg": 0,
            "frame_count": 0,
            "rejection_reasons": ["file_not_found"],
            "musiq_available": MUSIQ_AVAILABLE,
        }

    frames = sample_frames(video_path, start, duration, interval)
    if not frames:
        return {
            "verdict": "reject",
            "laplacian_avg": 0,
            "musiq_avg": -1,
            "edge_density_avg": 0,
            "frame_count": 0,
            "rejection_reasons": ["no_frames"],
            "musiq_available": MUSIQ_AVAILABLE,
        }

    laplacians = []
    edges = []
    musiqs = []

    for f in frames:
        laplacians.append(compute_laplacian_variance(f))
        edges.append(compute_edge_density(f))
        musiqs.append(compute_musiq_score(f))

    out = {
        "laplacian_avg": round(float(np.mean(laplacians)), 2),
        "edge_density_avg": round(float(np.mean(edges)), 5),
        "musiq_avg": round(float(np.mean(musiqs)), 2) if MUSIQ_AVAILABLE else -1.0,
        "musiq_available": MUSIQ_AVAILABLE,
        "frame_count": len(frames),
        "rejection_reasons": [],
    }

    if out["laplacian_avg"] < LAPLACIAN_REJECT:
        out["rejection_reasons"].append("too_blurry")

    if MUSIQ_AVAILABLE and out["musiq_avg"] >= 0 and out["musiq_avg"] < MUSIQ_REJECT:
        out["rejection_reasons"].append("low_quality")

    if out["edge_density_avg"] < EDGE_DENSITY_REJECT:
        out["rejection_reasons"].append("soft_or_upscaled")

    out["verdict"] = "reject" if out["rejection_reasons"] else "accept"
    return out


def main():
    parser = argparse.ArgumentParser(description="Video quality assessor")
    parser.add_argument("video_path", help="Path to video file")
    parser.add_argument("--start", type=float, default=0, help="Start time in seconds")
    parser.add_argument("--duration", type=float, default=12, help="Duration in seconds")
    parser.add_argument("--interval", type=int, default=5, help="Sample interval in seconds")
    args = parser.parse_args()

    result = assess(args.video_path, args.start, args.duration, args.interval)
    print(json.dumps(result))


if __name__ == "__main__":
    main()