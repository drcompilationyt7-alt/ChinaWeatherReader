#!/usr/bin/env python3
"""
Person Tracker — YOLO Pose + ByteTrack + Group-center Calculation
Processes a video clip and outputs per-frame crop center positions
with stable person tracking across frames.

Pipeline:
  1. YOLO detection @ 5 FPS with tracking (ByteTrack via ultralytics)
  2. Cross-frame track ID persistence (no identity switching)
  3. Group-center calculation (all tracked people, weighted toward primary)
  4. Per-frame crop center output

Usage: python3 person-tracker.py <video_path> [--start 0] [--duration 45] [--fps 5]
Output: JSON array of [{time, cropX, personCount, primaryId}]
"""
import sys
import json
import tempfile
import os
import subprocess
import argparse
import contextlib
import io

import cv2
import numpy as np
from ultralytics import YOLO

# Number of consecutive frames a track must appear to be considered stable
TRACK_BOOTSTRAP_FRAMES = 2


def extract_frame(video_path, timestamp):
    """Extract a single frame from video at given timestamp using ffmpeg."""
    try:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            return frame
        return None
    except:
        return None


def compute_group_center(tracked_boxes, frame_width):
    """
    Compute the optimal crop center from tracked person boxes.
    
    - If 1 person: center of that person's bounding box
    - If 2+ people: center of the group (midpoint of leftmost/rightmost edges)
      If group is wider than the crop window, favor the primary subject
      (most consistently tracked person).
    - If 0 people: return -1 (caller handles default)
    """
    if not tracked_boxes:
        return -1

    # Primary = most consistently tracked (highest count in track history)
    # For single-frame, use the person with largest bbox area (closest to camera)
    primary = max(tracked_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))

    if len(tracked_boxes) == 1:
        cx = (primary[0] + primary[2]) / 2
        return cx

    # Multiple people: compute group bounds
    left_edges = [b[0] for b in tracked_boxes]
    right_edges = [b[2] for b in tracked_boxes]
    group_left = min(left_edges)
    group_right = max(right_edges)
    group_width = group_right - group_left

    # Assume crop window is ~56% of frame width (9:16 in landscape)
    crop_width_ratio = 0.56
    crop_width = frame_width * crop_width_ratio

    if group_width < crop_width:
        # Group fits in crop window — center on the group
        cx = (group_left + group_right) / 2
    else:
        # Group is wider than crop — favor primary subject
        # but keep as much of the group as possible
        primary_cx = (primary[0] + primary[2]) / 2
        # Shift toward group center but don't exceed bounds
        group_cx = (group_left + group_right) / 2
        cx = (primary_cx * 0.6 + group_cx * 0.4)

    return cx


def track_video(video_path, start_time, duration, fps=5, max_crop_x=None):
    """
    Track people in a video clip using YOLO with ByteTrack.
    Returns array of {time, cropX, personCount, primaryId}.
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        # Use YOLO detection model (not pose — faster, and we just need bbox)
        model = YOLO("yolov8n.pt", verbose=False)

        sample_interval = 1.0 / fps
        num_samples = max(1, int(duration * fps))

        # Track state across frames
        track_history = {}  # track_id -> [center_x, ...]
        track_stability = {}  # track_id -> consecutive frames seen
        active_tracks = {}  # frame_index -> [{track_id, center_x, bbox}]

        positions = []

        for i in range(num_samples):
            t = i * sample_interval
            if t > duration:
                break

            frame = extract_frame(video_path, start_time + t)
            if frame is None:
                continue

            h, w = frame.shape[:2]
            frame_width = max_crop_x or w

            # Run YOLO tracking with persist=True for cross-frame IDs
            results = model.track(frame, persist=True, verbose=False)

            tracked_boxes = []
            if results and len(results) > 0:
                boxes = results[0].boxes
                if boxes is not None and boxes.id is not None:
                    for j, box in enumerate(boxes):
                        cls_id = int(box.cls[0])
                        if cls_id != 0:  # Only track people (class 0)
                            continue
                        conf = float(box.conf[0])
                        if conf < 0.35:
                            continue
                        track_id = int(box.id[j])
                        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                        cx = (x1 + x2) / 2

                        # Update track history
                        if track_id not in track_history:
                            track_history[track_id] = []
                            track_stability[track_id] = 0
                        track_history[track_id].append(cx)
                        track_stability[track_id] += 1

                        # Only consider stable tracks (seen multiple frames)
                        if track_stability[track_id] >= TRACK_BOOTSTRAP_FRAMES:
                            tracked_boxes.append({
                                "track_id": track_id,
                                "center_x": cx,
                                "bbox": [x1, y1, x2, y2],
                                "stability": track_stability[track_id],
                            })

            # Compute crop center from tracked people
            if tracked_boxes:
                # Extract bbox arrays for group calculation
                bboxes = [b["bbox"] for b in tracked_boxes]
                group_cx = compute_group_center(bboxes, frame_width)
                primary_id = max(tracked_boxes, key=lambda b: b["stability"])["track_id"]

                if group_cx >= 0:
                    positions.append({
                        "time": round(t, 2),
                        "cropX": group_cx,
                        "personCount": len(tracked_boxes),
                        "primaryId": primary_id,
                    })
                    if i % (fps * 2) == 0 or i == num_samples - 1:
                        print(f"  {t:.1f}s: {len(tracked_boxes)} person(s), group_center={group_cx:.0f}, primary_id={primary_id}",
                              file=sys.stderr)
                else:
                    # No valid group center — use previous position if available
                    if positions:
                        positions.append({
                            "time": round(t, 2),
                            "cropX": positions[-1]["cropX"],
                            "personCount": 0,
                            "primaryId": -1,
                        })
            else:
                # No people detected — use previous position
                if positions:
                    positions.append({
                        "time": round(t, 2),
                        "cropX": positions[-1]["cropX"],
                        "personCount": 0,
                        "primaryId": -1,
                    })

    print(f"  Tracked {num_samples} samples, {len(positions)} with detections",
          file=sys.stderr)
    return positions


def main():
    parser = argparse.ArgumentParser(description="Person tracker for dynamic crop")
    parser.add_argument("video_path", help="Path to video file")
    parser.add_argument("--start", type=float, default=0, help="Start time in seconds")
    parser.add_argument("--duration", type=float, default=45, help="Duration in seconds")
    parser.add_argument("--fps", type=int, default=5, help="Detection frequency (FPS)")
    parser.add_argument("--max-crop-x", type=int, default=None, help="Maximum crop X value")
    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(json.dumps({"error": f"Video not found: {args.video_path}"}))
        sys.exit(1)

    print(f"Video: {args.video_path}", file=sys.stderr)
    print(f"Duration: {args.duration}s, FPS: {args.fps}", file=sys.stderr)

    positions = track_video(
        args.video_path,
        args.start,
        args.duration,
        fps=args.fps,
        max_crop_x=args.max_crop_x,
    )

    # Output as JSON array to stdout
    print(json.dumps(positions))


if __name__ == "__main__":
    main()