#!/usr/bin/env python3
"""
Person Detector — Multi-person detection with full-body awareness.
Detects ALL people in a frame, returns their bounding boxes and body types.
Computes optimal crop center that keeps everyone in frame.

For landscape sources, the crop is full-height (uses all vertical space)
with a sliding horizontal window. For portrait sources taller than 9:16,
the crop is full-width with vertical centering.

Usage: python3 person-detector.py <image_path>
Output: JSON {
  "center_x": 500.0,      // optimal crop center X (or -1 if no people)
  "confidence": 0.92,     // confidence of primary subject
  "person_count": 3,      // number of people detected
  "body_type": "full",    // "full" | "upper" | "partial" | "none"
  "bboxes": [[x1,y1,x2,y2], ...],  // bounding boxes of all people
  "frame_width": 1920,    // original frame dimensions
  "frame_height": 1080
}
"""
import sys
import json
import contextlib
import io
from ultralytics import YOLO


def compute_body_type(bbox_height, frame_height):
    """Classify body type from bounding box height proportion."""
    if frame_height <= 0:
        return "partial"
    ratio = bbox_height / frame_height
    if ratio > 0.55:
        return "full"
    elif ratio > 0.25:
        return "upper"
    else:
        return "partial"


def get_optimal_center(image_path):
    """Detect all people and compute optimal crop center."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        model = YOLO("yolov8n.pt", verbose=False)
        results = model.predict(image_path, verbose=False)

    people_bboxes = []
    other_bboxes = []

    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue

        for box in boxes:
            obj_class = result.names[int(box.cls[0])]
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
            bbox = [x1, y1, x2, y2]
            entry = {
                "bbox": bbox,
                "confidence": confidence,
                "center_x": (x1 + x2) / 2,
                "center_y": (y1 + y2) / 2,
                "width": x2 - x1,
                "height": y2 - y1,
            }
            if obj_class == 'person':
                people_bboxes.append(entry)
            else:
                other_bboxes.append(entry)

    # Get frame dimensions from the result
    frame_height = 0
    frame_width = 0
    if results and len(results) > 0:
        orig = results[0].orig_shape
        if orig:
            frame_height = orig[0]
            frame_width = orig[1]

    if not people_bboxes:
        # No people detected — return center = -1, caller will use frame center
        print(json.dumps({
            "center_x": -1,
            "confidence": 0,
            "person_count": 0,
            "body_type": "none",
            "bboxes": [],
            "frame_width": frame_width,
            "frame_height": frame_height,
        }))
        return

    # Compute body type for each person
    for p in people_bboxes:
        p["body_type"] = compute_body_type(p["height"], frame_height)

    # Sort by confidence descending
    people_bboxes.sort(key=lambda p: p["confidence"], reverse=True)

    # Primary = highest confidence person
    primary = people_bboxes[0]
    primary_body = primary["body_type"]
    primary_conf = primary["confidence"]

    # Compute group bounds for all detected people
    min_x = min(p["bbox"][0] for p in people_bboxes)
    max_x = max(p["bbox"][2] for p in people_bboxes)
    group_center = (min_x + max_x) / 2

    # Keep everyone in frame: center between leftmost/rightmost person edges
    # If only one person, just use their center
    center_x = primary["center_x"] if len(people_bboxes) == 1 else group_center

    print(json.dumps({
        "center_x": center_x,
        "confidence": primary_conf,
        "person_count": len(people_bboxes),
        "body_type": primary_body,
        "bboxes": [p["bbox"] for p in people_bboxes],
        "frame_width": frame_width,
        "frame_height": frame_height,
    }))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python3 person-detector.py <image_path>"}))
        sys.exit(1)
    get_optimal_center(sys.argv[1])