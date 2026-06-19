#!/usr/bin/env python3
"""
Object Tracker — YOLO + ByteTrack + Importance Scoring
Tracks ANY YOLO class (person, sports ball, animal, vehicle) and
applies importance scoring to keep the top 1-4 most important
subjects per frame — like a camera operator focusing on the action.

Dual-cluster detection: When subjects form two distinct groups
(left/right halves of frame), tracks which cluster has more activity
and prioritizes left cluster first, switching to right when its
motion exceeds threshold. This handles dual-video layouts.

Output format:
  [{time, cropX, cropY, subjectCount, primaryId, subjectTypes, cluster}, ...]

Usage:
  python3 object-tracker.py <video_path> --start 0 --duration 45 --fps 5

CLI interface is identical to person-tracker.py for drop-in replacement.

FIX: Y-axis is biased toward the upper 1/3 of each person's bounding box
to keep the face and head visible in the 9:16 crop frame.
"""
import sys
import json
import os
import argparse
import contextlib
import io
import time

import cv2
import numpy as np
from ultralytics import YOLO

TRACK_BOOTSTRAP_FRAMES = 2
MAX_SUBJECTS = 4  # maximum number of subjects to track per frame

# COCO 80 class importance weights (higher = more important to frame)
# These define what a "camera operator" would naturally focus on.
CLASS_IMPORTANCE = {
    # Sports/action — highest priority
    32: 20,   # sports ball
    # People
    0:  18,   # person
    # Animals
    14: 16,   # bird
    15: 16,   # cat
    16: 16,   # dog
    17: 14,   # horse
    18: 14,   # sheep
    19: 14,   # cow
    20: 13,   # elephant
    21: 13,   # bear
    22: 12,   # zebra
    23: 12,   # giraffe
    24: 12,   # backpack (object carried by person)
    # Vehicles
    1:  12,   # bicycle
    2:  14,   # car (racing, drifting)
    3:  12,   # motorcycle
    5:  11,   # bus
    7:  11,   # truck
    # Other
    4:   8,   # airplane
    6:   8,   # train
    8:   8,   # boat
}


def get_class_importance(cls_id):
    """Get importance weight for a COCO class. Defaults to 5 for unknown."""
    return CLASS_IMPORTANCE.get(int(cls_id), 5)


def compute_importance_score(cls_id, confidence, bbox, frame_w, frame_h, prev_center):
    """
    Combined importance score (0-100 scale).
    
    Formula:
      score = (confidence × 30) + (size_ratio × 25) + (motion × 25) + (type_score × 20)
    
    - confidence: raw YOLO confidence
    - size_ratio: bbox area / frame area (bigger = more important)
    - motion: distance from previous frame center (moving = important)
    - type_score: semantic importance by class (ball > person > animal > vehicle > other)
    """
    x1, y1, x2, y2 = bbox
    bbox_area = max(1.0, (x2 - x1) * (y2 - y1))
    frame_area = float(max(1, frame_w) * max(1, frame_h))
    size_ratio = min(1.0, bbox_area / frame_area)

    # Motion: displacement from previous frame center
    motion = 0.0
    if prev_center is not None:
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        dx = cx - prev_center[0]
        dy = cy - prev_center[1]
        motion = min(1.0, np.hypot(dx, dy) / max(frame_w, frame_h))

    type_weight = get_class_importance(cls_id) / 20.0  # normalize to 0-1

    score = (confidence * 30.0) + (size_ratio * 25.0) + (motion * 25.0) + (type_weight * 20.0)
    return float(score)


def extract_frame(video_path, timestamp):
    """Extract a single frame at given timestamp using OpenCV."""
    try:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None
    except Exception:
        return None


def compute_group_center(bboxes, frame_w, frame_h):
    """
    Compute optimal crop center from a list of bounding boxes.
    - If 1 subject: center of that bbox, but Y biased to upper third (face area)
    - If 2+ subjects: midpoint of leftmost/rightmost edges
    - Y axis: tallest subject's upper-third point (keeps face in frame)
    Returns (cx, cy) or (-1, -1) if empty.
    """
    if not bboxes:
        return -1, -1
    if len(bboxes) == 1:
        x1, y1, x2, y2 = bboxes[0]
        # Bias Y toward upper third of bounding box (y1 + 0.33 * height)
        # instead of the vertical midpoint. This keeps the face/head visible
        # in the 9:16 crop window.
        face_y = y1 + (y2 - y1) * 0.33
        return (x1 + x2) / 2.0, face_y
    left = min(b[0] for b in bboxes)
    right = max(b[2] for b in bboxes)
    cx = (left + right) / 2.0
    # Y: tallest bbox — bias to upper third for face visibility
    tallest = max(bboxes, key=lambda b: b[3] - b[1])
    face_y = tallest[1] + (tallest[3] - tallest[1]) * 0.33
    return cx, face_y


def detect_clusters(detected, frame_midpoint):
    """
    Split detections into left/right clusters based on frame midpoint.
    Returns (left_cluster, right_cluster) where each is a list of detections.
    """
    left = []
    right = []
    for d in detected:
        bbox = d["bbox"]
        cx = (bbox[0] + bbox[2]) / 2.0
        if cx < frame_midpoint:
            left.append(d)
        else:
            right.append(d)
    return left, right


def compute_cluster_activity(cluster):
    """
    Compute total activity score for a cluster.
    Sum of importance scores of all detections in the cluster.
    Returns 0 if cluster is empty.
    """
    if not cluster:
        return 0.0
    return sum(d["score"] for d in cluster)


def choose_active_cluster(left, right, prev_active, frame_w):
    """
    Choose which cluster to focus on.
    Prioritizes LEFT first. Switches to RIGHT only when its activity
    significantly exceeds left activity.
    
    Args:
        left: list of detections on left half
        right: list of detections on right half
        prev_active: 'left' or 'right' from previous frame
        frame_w: frame width for midpoint calculation
        
    Returns:
        ('left', left_detections) or ('right', right_detections)
    """
    left_activity = compute_cluster_activity(left)
    right_activity = compute_cluster_activity(right)
    
    # If only one cluster has subjects, use that one
    if not left and not right:
        return 'left', left  # neither — keep previous
    if not left and right:
        return 'right', right
    if left and not right:
        return 'left', left
    
    # Both clusters have subjects — decide based on activity
    # Left priority: only switch to right if right activity is ≥1.5x left activity
    # Or if right has been active for multiple frames and left is quiet
    if prev_active == 'left':
        # Stay on left unless right is significantly more active
        if right_activity > left_activity * 1.5:
            return 'right', right
        return 'left', left
    else:
        # Stay on right unless left is significantly more active
        if left_activity > right_activity * 1.5:
            return 'left', left
        return 'right', right


def track_video(video_path, start_time, duration, fps=5, max_crop_x=None, max_crop_y=None):
    """
    Track objects in a video clip using YOLO with ByteTrack.
    Returns array of {time, cropX, cropY, subjectCount, primaryId, subjectTypes, cluster}.
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        t0 = time.time()
        print("  Loading YOLO model...", file=sys.stderr)
        model = YOLO("yolov8n.pt", verbose=False)
        print(f"  Model loaded in {time.time() - t0:.1f}s", file=sys.stderr)

        sample_interval = 1.0 / max(1, fps)
        num_samples = max(1, int(duration * fps))

        # Per-track state
        track_prev_center = {}  # track_id -> (cx, cy)
        track_class = {}         # track_id -> cls_id
        track_stability = {}     # track_id -> frame count
        positions = []
        active_cluster = 'left'  # start with left priority

        infer_t0 = time.time()
        for i in range(num_samples):
            t = i * sample_interval
            if t > duration:
                break

            frame = extract_frame(video_path, start_time + t)
            if frame is None:
                print(f"  Frame {i+1}/{num_samples}: no frame extracted", file=sys.stderr)
                continue

            h, w = frame.shape[:2]

            # Log progress every 2 frames or on first/last
            if i == 0 or i == num_samples - 1 or i % 2 == 0:
                print(f"  Frame {i+1}/{num_samples}: {w}x{h}", file=sys.stderr)
            frame_w = float(max_crop_x or w)
            frame_h = float(max_crop_y or h)

            results = model.track(frame, persist=True, verbose=False, conf=0.30)
            detected = []  # list of {track_id, cls_id, conf, bbox, score}

            if results and len(results) > 0 and results[0].boxes is not None:
                boxes = results[0].boxes
                cls_t = boxes.cls
                conf_t = boxes.conf
                xyxy_t = boxes.xyxy
                id_t = boxes.id
                n = len(boxes)

                for j in range(n):
                    cls_id = int(cls_t[j].item()) if cls_t is not None else -1
                    conf_val = float(conf_t[j].item()) if conf_t is not None else 0.0
                    if conf_val < 0.30:
                        continue

                    # Get track ID — use negative index as pseudo-ID if none
                    if id_t is not None and j < len(id_t):
                        track_id = int(id_t[j].item())
                    else:
                        track_id = -(j + 1)

                    x1, y1, x2, y2 = [float(v) for v in xyxy_t[j].tolist()]
                    bbox = [x1, y1, x2, y2]

                    # Update track stability
                    if track_id not in track_stability:
                        track_stability[track_id] = 0
                    track_stability[track_id] += 1
                    track_class[track_id] = cls_id

                    # Skip unstable tracks (needs N consecutive frames)
                    if track_stability[track_id] < TRACK_BOOTSTRAP_FRAMES:
                        continue

                    prev = track_prev_center.get(track_id)
                    score = compute_importance_score(
                        cls_id, conf_val, bbox, w, h, prev
                    )

                    detected.append({
                        "track_id": track_id,
                        "cls_id": cls_id,
                        "conf": conf_val,
                        "bbox": bbox,
                        "score": score,
                    })
                    track_prev_center[track_id] = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

            # Sort by importance score, keep top MAX_SUBJECTS
            detected.sort(key=lambda d: d["score"], reverse=True)
            kept = detected[:MAX_SUBJECTS]

            if kept:
                # Dual-cluster detection
                frame_midpoint = frame_w / 2.0
                left_cluster, right_cluster = detect_clusters(kept, frame_midpoint)
                
                # Choose which cluster to focus on (left-priority)
                cluster_name, active_detections = choose_active_cluster(
                    left_cluster, right_cluster, active_cluster, frame_w
                )
                active_cluster = cluster_name
                
                # Compute crop center from the active cluster only
                active_bboxes = [d["bbox"] for d in active_detections]
                cx, cy = compute_group_center(active_bboxes, frame_w, frame_h)
                primary = max(active_detections, key=lambda d: d["score"])
                
                # Log cluster info
                if i % (fps * 2) == 0 or i == num_samples - 1:
                    left_count = len(left_cluster)
                    right_count = len(right_cluster)
                    left_act = compute_cluster_activity(left_cluster)
                    right_act = compute_cluster_activity(right_cluster)
                    print(
                        f"  {t:.1f}s: cluster={cluster_name}, "
                        f"left={left_count}({left_act:.0f}), "
                        f"right={right_count}({right_act:.0f}), "
                        f"cx={cx:.0f} cy={cy:.0f}",
                        file=sys.stderr,
                    )
                
                positions.append({
                    "time": round(t, 2),
                    "cropX": cx,
                    "cropY": cy,
                    "subjectCount": len(active_detections),
                    "primaryId": primary["track_id"],
                    "subjectTypes": [d["cls_id"] for d in active_detections],
                    "cluster": cluster_name,
                })
            else:
                # No detections — hold last position
                if positions:
                    positions.append({
                        "time": round(t, 2),
                        "cropX": positions[-1]["cropX"],
                        "cropY": positions[-1]["cropY"],
                        "subjectCount": 0,
                        "primaryId": -1,
                        "subjectTypes": [],
                        "cluster": active_cluster,
                    })

    print(
        f"  Tracked {num_samples} samples, {len(positions)} with detections, "
        f"inference took {time.time() - infer_t0:.1f}s total",
        file=sys.stderr,
    )
    return positions


def main():
    parser = argparse.ArgumentParser(
        description="Object tracker for dynamic crop (multi-class)"
    )
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