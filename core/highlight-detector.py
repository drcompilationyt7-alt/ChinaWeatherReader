#!/usr/bin/env python3
"""
Highlight Detector — YOLO + OpenCV Scoring Pipeline

For long videos (>2 min), detects the most interesting 30-60s segment
by scoring scenes on motion, pose activity, and object diversity.

Pipeline:
  1. Scene Detection (PySceneDetect) — split into segments
  2. Motion Analysis (OpenCV) — optical flow → motion_score
  3. YOLO Pose — joint velocity → pose_score
  4. YOLO Object Detection — object count/diversity → object_score
  5. Weighted highlight_score = motion*0.4 + pose*0.3 + objects*0.15 + event_bonus*0.15
  6. Merge adjacent high-scoring windows → contiguous highlights
  7. Auto-trim: peak_start-2s to peak_end+2s

Usage:
  python3 highlight-detector.py <video_path> [--output-json]
"""

import sys
import json
import subprocess
import tempfile
import os
import math

import cv2
import numpy as np

# Check PySceneDetect availability
try:
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector
    HAS_SCENE_DETECT = True
except ImportError:
    HAS_SCENE_DETECT = False

# Check YOLO availability
try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    HAS_YOLO = False


def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'csv=p=0', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except:
        return 30.0


def get_video_fps(video_path):
    """Get video frame rate."""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=r_frame_rate',
            '-of', 'csv=p=0', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        parts = result.stdout.strip().split('/')
        if len(parts) == 2:
            return float(parts[0]) / float(parts[1])
        return float(parts[0])
    except:
        return 30.0


def detect_scenes(video_path):
    """Detect scene changes using PySceneDetect. Returns list of (start_sec, end_sec)."""
    if not HAS_SCENE_DETECT:
        # Fallback: uniform segments every 5 seconds
        duration = get_video_duration(video_path)
        segments = []
        for t in range(0, int(duration), 5):
            end = min(t + 5, duration)
            segments.append((t, end))
        return segments

    try:
        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=30.0))
        scene_manager.detect_scenes(video)
        scenes = scene_manager.get_scene_list()

        if not scenes:
            raise ValueError("No scenes detected")

        segments = []
        for start, end in scenes:
            segments.append((start.get_seconds(), end.get_seconds()))
        return segments
    except:
        # Fallback
        duration = get_video_duration(video_path)
        return [(t, min(t + 5, duration)) for t in range(0, int(duration), 5)]


def compute_motion_score(prev_gray, gray):
    """Compute optical flow magnitude between two grayscale frames. Returns 0-100."""
    if prev_gray is None:
        return 0
    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    magnitude = np.sqrt(flow[..., 0]**2 + flow[..., 1]**2)
    avg_motion = np.mean(magnitude)
    # Normalize: avg_motion ~1-3 for talking heads, ~5-15 for walking, ~20+ for dance
    score = min(100, avg_motion * 5)
    return score


def compute_pose_score(keypoints_list):
    """Compute pose activity score from list of keypoint arrays. Returns 0-100."""
    if not keypoints_list or len(keypoints_list) < 2:
        return 0

    velocities = []
    for i in range(1, len(keypoints_list)):
        if keypoints_list[i-1] is not None and keypoints_list[i] is not None:
            kp_prev = keypoints_list[i-1]
            kp_curr = keypoints_list[i]
            if kp_prev.shape == kp_curr.shape:
                # Average joint displacement
                diff = np.mean(np.sqrt(np.sum((kp_curr - kp_prev)**2, axis=1)))
                velocities.append(diff)

    if not velocities:
        return 0

    avg_velocity = np.mean(velocities)
    # Normalize: 0-5 = low, 5-20 = moderate, 20+ = high activity
    score = min(100, avg_velocity * 3)
    return score


def compute_object_score(detections):
    """Compute object diversity/interest score from YOLO detections. Returns 0-100."""
    if not detections:
        return 0

    # Interesting objects (sports, animals, vehicles)
    interesting_classes = {
        'person': 1, 'dog': 10, 'cat': 10, 'horse': 10, 'bird': 8,
        'ball': 8, 'frisbee': 8, 'skateboard': 8, 'surfboard': 8,
        'sports ball': 8, 'baseball bat': 7, 'baseball glove': 7,
        'bicycle': 6, 'motorcycle': 6, 'car': 5, 'airplane': 8,
        'boat': 7, 'fire hydrant': 3, 'stop sign': 2, 'parking meter': 1,
        'bench': 1,
    }

    total_score = 0
    for det in detections:
        cls_name = det.get('class', '').lower()
        weight = interesting_classes.get(cls_name, 1)
        total_score += weight

    # Normalize: each detection contributes up to 10, cap at 100
    return min(100, total_score * 10)


def extract_frame(video_path, timestamp, size=(640, 360)):
    """Extract a single frame from video at given timestamp."""
    try:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            return cv2.resize(frame, size)
        return None
    except:
        return None


def sample_video(video_path, sample_interval=1.0, max_duration=300):
    """
    Sample video at regular intervals.
    Returns list of (timestamp, motion_score, pose_score, object_score) tuples.
    """
    duration = min(get_video_duration(video_path), max_duration)
    fps = get_video_fps(video_path)
    sample_frame_interval = max(1, int(fps * sample_interval))

    # Load YOLO models once
    yolo_pose = None
    yolo_detect = None
    if HAS_YOLO:
        try:
            yolo_pose = YOLO('yolov8n-pose.pt')
            yolo_detect = YOLO('yolov8n.pt')
        except:
            yolo_pose = None
            yolo_detect = None

    cap = cv2.VideoCapture(video_path)
    results = []
    prev_gray = None
    keypoints_history = []
    frame_count = 0
    last_printed_progress = -1

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        timestamp = frame_count / fps
        if timestamp > duration:
            break

        frame_count += 1
        if frame_count % sample_frame_interval != 0:
            continue

        # Progress
        pct = int(timestamp / duration * 100)
        if pct >= last_printed_progress + 10:
            print(f"  Analyzing: {pct}% ({timestamp:.0f}s / {duration:.0f}s)", file=sys.stderr)
            last_printed_progress = pct

        # Resize for processing
        small_frame = cv2.resize(frame, (640, 360))
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)

        # Motion score
        motion_score = compute_motion_score(prev_gray, gray)
        prev_gray = gray

        # Pose score
        pose_score = 0
        if yolo_pose:
            try:
                pose_results = yolo_pose(small_frame, verbose=False)
                if pose_results and len(pose_results) > 0:
                    kps = pose_results[0].keypoints
                    if kps is not None and kps.data is not None and len(kps.data) > 0:
                        # Get first person's keypoints as xy array
                        keypoints = kps.data[0].cpu().numpy()[:, :2]
                        keypoints_history.append(keypoints)
                        if len(keypoints_history) > 30:
                            keypoints_history.pop(0)
                        pose_score = compute_pose_score(keypoints_history)
                    else:
                        keypoints_history.append(None)
                        if len(keypoints_history) > 30:
                            keypoints_history.pop(0)
                else:
                    keypoints_history.append(None)
                    if len(keypoints_history) > 30:
                        keypoints_history.pop(0)
            except:
                keypoints_history.append(None)
                if len(keypoints_history) > 30:
                    keypoints_history.pop(0)

        # Object score
        object_score = 0
        if yolo_detect:
            try:
                detect_results = yolo_detect(small_frame, verbose=False)
                if detect_results and len(detect_results) > 0:
                    boxes = detect_results[0].boxes
                    names = detect_results[0].names
                    detections = []
                    if boxes is not None and boxes.cls is not None:
                        for cls_id in boxes.cls:
                            cls_name = names.get(int(cls_id), 'unknown')
                            detections.append({'class': cls_name})
                    object_score = compute_object_score(detections)
            except:
                pass

        results.append({
            'timestamp': timestamp,
            'motion': round(motion_score, 1),
            'pose': round(pose_score, 1),
            'objects': round(object_score, 1),
        })

    cap.release()
    print(f"  Done: {len(results)} samples analyzed", file=sys.stderr)
    return results, duration


def compute_highlight_scores(samples, detect_type='auto'):
    """
    Weight the scores into a final highlight_score.
    detect_type: 'dance', 'sports', or 'auto'
    """
    if detect_type == 'dance':
        w_motion, w_pose, w_objects, w_events = 0.50, 0.40, 0.05, 0.05
    elif detect_type == 'sports':
        w_motion, w_pose, w_objects, w_events = 0.30, 0.20, 0.30, 0.20
    else:
        w_motion, w_pose, w_objects, w_events = 0.40, 0.30, 0.15, 0.15

    scored = []
    for s in samples:
        # Heuristic event bonus: significant object count changes or high motion + pose
        event_bonus = 0
        if s['objects'] > 50:
            event_bonus += 15
        if s['motion'] > 60 and s['pose'] > 40:
            event_bonus += 15
        if s['motion'] > 80:
            event_bonus += 10

        highlight = (
            s['motion'] * w_motion +
            s['pose'] * w_pose +
            s['objects'] * w_objects +
            event_bonus * w_events
        )
        scored.append({**s, 'highlight': round(highlight, 1)})

    return scored


def merge_peaks(scored_samples, min_peak_score=40, merge_gap=3):
    """
    Merge adjacent high-scoring windows into contiguous highlights.
    min_peak_score: minimum score to be considered a peak
    merge_gap: merge peaks if separated by <= this many seconds
    """
    highlights = []
    current_start = None
    current_end = None
    current_score = 0
    count = 0

    for s in scored_samples:
        if s['highlight'] >= min_peak_score:
            if current_start is None:
                current_start = s['timestamp']
                current_end = s['timestamp']
                current_score = s['highlight']
                count = 1
            else:
                gap = s['timestamp'] - current_end
                if gap <= merge_gap:
                    current_end = s['timestamp']
                    current_score = max(current_score, s['highlight'])
                    count += 1
                else:
                    if count >= 2:  # Only keep if at least 2 samples
                        highlights.append({
                            'start': max(0, current_start - 2),
                            'end': current_end + 2,
                            'peak_score': current_score,
                            'duration': current_end - current_start + 4,
                        })
                    current_start = s['timestamp']
                    current_end = s['timestamp']
                    current_score = s['highlight']
                    count = 1
        else:
            if current_start is not None and count >= 2:
                highlights.append({
                    'start': max(0, current_start - 2),
                    'end': current_end + 2,
                    'peak_score': current_score,
                    'duration': current_end - current_start + 4,
                })
            current_start = None
            current_end = None
            current_score = 0
            count = 0

    # Handle final peak
    if current_start is not None and count >= 2:
        highlights.append({
            'start': max(0, current_start - 2),
            'end': current_end + 2,
            'peak_score': current_score,
            'duration': current_end - current_start + 4,
        })

    return highlights


def select_best_clip(highlights, duration, target_duration=45):
    """
    Select the best clip (up to target_duration seconds).
    Prefers highest-scoring highlight, trims to target.
    """
    if not highlights:
        return None

    # Sort by peak score descending
    highlights.sort(key=lambda h: h['peak_score'], reverse=True)

    best = highlights[0]
    clip_start = best['start']
    clip_end = best['end']

    # Try to expand with adjacent highlights for more context
    if len(highlights) > 1:
        second = highlights[1]
        if abs(second['start'] - clip_end) < 5:
            clip_end = max(clip_end, second['end'])
        elif second['start'] < clip_start and clip_start - second['end'] < 5:
            clip_start = min(clip_start, second['start'])

    # Limit to target_duration
    clip_duration = clip_end - clip_start
    if clip_duration > target_duration:
        # Center the clip around the peak
        peak_mid = (best['start'] + best['end']) / 2
        clip_start = max(0, peak_mid - target_duration / 2)
        clip_end = min(duration, clip_start + target_duration)
    elif clip_duration < 15:
        # Too short — pad to at least 15s
        mid = (clip_start + clip_end) / 2
        clip_start = max(0, mid - 7.5)
        clip_end = min(duration, mid + 7.5)

    return {
        'start': round(clip_start, 1),
        'end': round(clip_end, 1),
        'duration': round(clip_end - clip_start, 1),
        'peak_highlight_score': best['peak_score'],
    }


def extract_clip(video_path, output_path, start, end, crf=0, pix_fmt='yuv444p'):
    """Extract a clip from the video using ffmpeg with lossless quality."""
    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start),
        '-i', video_path,
        '-to', str(end),
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', str(crf),
        '-pix_fmt', pix_fmt,
        '-c:a', 'aac',
        '-b:a', '320k',
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=300)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 50000


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 highlight-detector.py <video_path> [--output-json]", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]
    output_json = '--output-json' in sys.argv

    if not os.path.exists(video_path):
        print(json.dumps({'error': f'Video not found: {video_path}'}))
        sys.exit(1)

    duration = get_video_duration(video_path)
    print(f"Video: {video_path}", file=sys.stderr)
    print(f"Duration: {duration:.1f}s", file=sys.stderr)

    # If video is already short (< 120s), return it as-is
    if duration <= 120:
        result = {
            'action': 'use_full',
            'start': 0,
            'end': duration,
            'duration': duration,
            'reason': f'Video is only {duration:.0f}s, no highlight detection needed',
        }
        print(json.dumps(result))
        return

    # Step 1: Detect scenes
    print("\nStep 1: Scene Detection...", file=sys.stderr)
    scenes = detect_scenes(video_path)
    print(f"  Found {len(scenes)} segments", file=sys.stderr)

    # Step 2-4: Sample video and score
    print("\nStep 2-4: Motion + Pose + Object Analysis...", file=sys.stderr)
    if duration > 600:
        # For very long videos (>10 min), sample less frequently
        sample_interval = 2.0
    else:
        sample_interval = 1.0

    samples, _ = sample_video(video_path, sample_interval=sample_interval, max_duration=duration)

    # Step 5: Compute highlight scores
    print("\nStep 5: Computing Highlight Scores...", file=sys.stderr)
    scored = compute_highlight_scores(samples)

    # Step 6: Merge peaks
    print("Step 6: Merging Peaks...", file=sys.stderr)
    highlights = merge_peaks(scored, min_peak_score=35, merge_gap=3)
    print(f"  Found {len(highlights)} highlight segments", file=sys.stderr)

    if not highlights:
        print("No highlights found — using middle 45s of video", file=sys.stderr)
        mid = duration / 2
        result = {
            'action': 'extract',
            'start': round(mid - 22.5, 1),
            'end': round(mid + 22.5, 1),
            'duration': 45,
            'peak_highlight_score': 0,
        }
        if output_json:
            print(json.dumps(result))
        return

    # Step 7: Select best clip
    print("Step 7: Selecting Best Clip...", file=sys.stderr)
    best = select_best_clip(highlights, duration, target_duration=45)

    if best:
        print(f"\n✅ Best clip: {best['start']}s → {best['end']}s ({best['duration']}s)", file=sys.stderr)
        print(f"   Peak highlight score: {best['peak_highlight_score']:.1f}/100", file=sys.stderr)
        result = {
            'action': 'extract',
            'start': best['start'],
            'end': best['end'],
            'duration': best['duration'],
            'peak_highlight_score': best['peak_highlight_score'],
        }
    else:
        print("Fallback: center 45s", file=sys.stderr)
        mid = duration / 2
        result = {
            'action': 'extract',
            'start': round(mid - 22.5, 1),
            'end': round(mid + 22.5, 1),
            'duration': 45,
            'peak_highlight_score': 0,
        }

    if output_json:
        print(json.dumps(result))


if __name__ == '__main__':
    main()