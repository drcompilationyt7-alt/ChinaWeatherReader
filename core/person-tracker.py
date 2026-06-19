#!/usr/bin/env python3
"""
Person Tracker — YOLO + ByteTrack + Group-center Calculation
Processes a video clip and outputs per-frame crop center (X and Y) positions
with stable person tracking across frames.

Pipeline:
  1. YOLO detection @ 5 FPS with tracking (ByteTrack via ultralytics)
  2. Cross-frame track ID persistence (no identity switching)
  3. Group-center calculation (all tracked people, weighted toward primary)
  4. Per-frame crop center output with cropX and cropY

Y-axis is biased toward the upper 1/3 of each person's bounding box
to keep the face and head visible in the 9:16 crop frame.

Usage: python3 person-tracker.py <video_path> [--start 0] [--duration 45] [--fps 5]
Output: JSON array of [{time, cropX, cropY, personCount, primaryId}]
"""
import sys
import json
import os
import argparse
import contextlib
import io

import cv2
from ultralytics import YOLO

# Number of consecutive frames a track must appear to be considered stable
TRACK_BOOTSTRAP_FRAMES = 2


def extract_frame(video_path, timestamp):
    """Extract a single frame from video at given timestamp using OpenCV."""
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


def compute_group_center(tracked_boxes, frame_width, frame_height):
    """
    Compute the optimal crop center (X, Y) from tracked person boxes.
    Returns (center_x, center_y) or (-1, -1) if no valid center.
    
    - If 1 person: center of that person's bounding box
    - If 2+ people: center of the group (midpoint of leftmost/rightmost edges)
    - Y axis: tallest person's upper-third point (to keep face/head in frame)
    """
    if not tracked_boxes:
        return -1, -1

    # Primary = person with largest bbox area (closest to camera)
    primary = max(tracked_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))

    if len(tracked_boxes) == 1:
        cx = (primary[0] + primary[2]) / 2
        # Bias Y toward upper third of bounding box for face visibility
        cy = primary[1] + (primary[3] - primary[1]) * 0.33
        return cx, cy

    # Multiple people: compute group bounds for X
    left_edges = [b[0] for b in tracked_boxes]
    right_edges = [b[2] for b in tracked_boxes]
    group_left = min(left_edges)
    group_right = max(right_edges)
    group_width = group_right - group_left

    # For Y, use the tallest person's upper-third point (keeps head/face in frame)
    tallest = max(tracked_boxes, key=lambda b: b[3] - b[1])
    cy = tallest[1] + (tallest[3] - tallest[1]) * 0.33

    # Assume crop window is ~56% of frame width (9:16 in landscape)
    crop_width_ratio = 0.56
    crop_width = frame_width * crop_width_ratio

    if group_width < crop_width:
        # Group fits in crop window — center on the group
        cx = (group_left + group_right) / 2
    else:
        # Group is wider than crop — favor primary subject
        primary_cx = (primary[0] + primary[2]) / 2
        group_cx = (group_left + group_right) / 2
        cx = (primary_cx * 0.6 + group_cx * 0.4)

    return cx, cy


def track_video(video_path, start_time, duration, fps=5, max_crop_x=None, max_crop_y=None):
    """
    Track people in a video clip using YOLO with ByteTrack.
    Returns array of {time, cropX, cropY, personCount, primaryId}.
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        model = YOLO("yolov8n.pt", verbose=False)

        sample_interval = 1.0 / fps
        num_samples = max(1, int(duration * fps))

        track_history = {}
        track_stability = {}
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
            frame_height = max_crop_y or h

            results = model.track(frame, persist=True, verbose=False)

            tracked_boxes = []
            if results and len(results) > 0:
                boxes = results[0].boxes
                if boxes is not None:
                    cls_tensor = boxes.cls
                    conf_tensor = boxes.conf
                    xyxy_tensor = boxes.xyxy
                    id_tensor = boxes.id

                    num_detections = len(boxes)
                    for j in range(num_detections):
                        cls_id = int(cls_tensor[j].item()) if cls_tensor is not None else -1
                        if cls_id != 0:
                            continue
                        conf = float(conf_tensor[j].item()) if conf_tensor is not None else 0
                        if conf < 0.35:
                            continue
                        # Safely get track ID — may be None if no tracker assigned
                        track_id = None
                        if id_tensor is not None and j < len(id_tensor):
                            track_id = int(id_tensor[j].item())
                        # If no track ID, still track the detection (use detection-only mode)
                        if track_id is None:
                            track_id = - (j + 1)  # use negative index as pseudo-ID
                        x1, y1, x2, y2 = [float(v) for v in xyxy_tensor[j].tolist()]

                        if track_id not in track_history:
                            track_history[track_id] = []
                            track_stability[track_id] = 0
                        track_history[track_id].append((x1 + x2) / 2)
                        track_stability[track_id] += 1

                        if track_stability[track_id] >= TRACK_BOOTSTRAP_FRAMES:
                            tracked_boxes.append({
                                "track_id": track_id,
                                "center_x": (x1 + x2) / 2,
                                "center_y": (y1 + y2) / 2,
                                "bbox": [x1, y1, x2, y2],
                                "stability": track_stability[track_id],
                            })

            if tracked_boxes:
                bboxes = [b["bbox"] for b in tracked_boxes]
                group_cx, group_cy = compute_group_center(bboxes, frame_width, frame_height)
                primary_id = max(tracked_boxes, key=lambda b: b["stability"])["track_id"]

                if group_cx >= 0:
                    positions.append({
                        "time": round(t, 2),
                        "cropX": group_cx,
                        "cropY": group_cy,
                        "personCount": len(tracked_boxes),
                        "primaryId": primary_id,
                    })
                    if i % (fps * 2) == 0 or i == num_samples - 1:
                        print(f"  {t:.1f}s: {len(tracked_boxes)} person(s), cx={group_cx:.0f}, cy={group_cy:.0f}, pid={primary_id}",
                              file=sys.stderr)
                else:
                    if positions:
                        positions.append({
                            "time": round(t, 2),
                            "cropX": positions[-1]["cropX"],
                            "cropY": positions[-1]["cropY"],
                            "personCount": 0,
                            "primaryId": -1,
                        })
            else:
                if positions:
                    positions.append({
                        "time": round(t, 2),
                        "cropX": positions[-1]["cropX"],
                        "cropY": positions[-1]["cropY"],
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
    parser.add_argument("--max-crop-y", type=int, default=None, help="Maximum crop Y value")
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
        max_crop_y=args.max_crop_y,
    )

    print(json.dumps(positions))


if __name__ == "__main__":
    main()