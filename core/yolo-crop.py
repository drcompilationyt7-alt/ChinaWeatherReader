#!/usr/bin/env python3
"""
YOLO-based smart crop helper.
Extracts the center X coordinate of the primary subject (person) in a video frame.
Usage: python3 yolo-crop.py <image_path>
Output: JSON {"center_x": 500, "confidence": 0.92, "subject": "person", "bbox": [x1, y1, x2, y2]}
"""
import sys
import json
from ultralytics import YOLO

def get_smart_crop_center(image_path):
    # Load model (auto-downloads yolov8n.pt on first run, ~6MB)
    model = YOLO("yolov8n.pt", verbose=False)
    
    results = model(image_path)
    
    best_person = None
    best_confidence = 0
    
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
            
        for box in boxes:
            obj_class = result.names[int(box.cls[0])]
            confidence = float(box.conf[0])
            
            # Prefer persons, but accept any detected object
            score = confidence * (2.0 if obj_class == 'person' else 1.0)
            
            if score > best_confidence:
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                center_x = (x1 + x2) / 2
                best_person = {
                    "center_x": center_x,
                    "confidence": confidence,
                    "subject": obj_class,
                    "bbox": [x1, y1, x2, y2]
                }
                best_confidence = score
    
    if best_person:
        print(json.dumps(best_person))
    else:
        # No object detected — return center of frame
        print(json.dumps({
            "center_x": -1,
            "confidence": 0,
            "subject": "none",
            "bbox": []
        }))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python3 yolo-crop.py <image_path>"}))
        sys.exit(1)
    get_smart_crop_center(sys.argv[1])