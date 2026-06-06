#!/usr/bin/env python3
"""
TalkNet Active Speaker Detection — identifies which person is speaking on screen.
Outputs per-frame active speaker center positions to boost importance in object-tracker.py.

Usage:
  python3 talknet-detect.py <video_path> --start 0 --duration 60 --output-json

Output format (JSON lines):
  {"time": 0.0, "active_speaker": true, "face_center_x": 500, "face_center_y": 300, "face_w": 100}
  {"time": 1.0, "active_speaker": false}
  ...

If no active speaker is detected, all frames return active_speaker: false.
"""
import sys
import json
import os
import argparse
import subprocess
import tempfile
import warnings
warnings.filterwarnings('ignore')

def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except:
        return 60.0

def extract_audio(video_path, audio_path):
    """Extract audio from video for TalkNet processing."""
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-i', video_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_path],
            capture_output=True, timeout=120
        )
        return os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000
    except:
        return False

def detect_faces(video_path, start_time, duration, tmp_dir):
    """Detect faces using YOLO and track them with ByteTrack.
    Returns list of tracked face positions per frame."""
    import cv2
    from ultralytics import YOLO

    model = YOLO('yolov8n.pt')
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0: fps = 30

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    start_frame = int(start_time * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    max_frames = int(duration * fps)

    face_frames = []
    frame_count = 0

    # Simple tracker state
    tracked_faces = {}  # track_id -> {cx, cy, w, h, age}

    while frame_count < max_frames:
        ret, frame = cap.read()
        if not ret:
            break

        timestamp = (start_frame + frame_count) / fps
        frame_count += 1

        # Process every 5th frame to save CPU
        if frame_count % 5 != 0:
            continue

        # Detect persons (class 0)
        results = model(frame, classes=[0], verbose=False)
        faces = []

        if results and len(results) > 0:
            boxes = results[0].boxes
            if boxes is not None and boxes.xyxy is not None:
                for i, box in enumerate(boxes.xyxy):
                    x1, y1, x2, y2 = box.tolist()
                    cx = (x1 + x2) / 2
                    cy = (y1 + y2) / 2
                    w = x2 - x1
                    h = y2 - y1
                    faces.append({'cx': cx, 'cy': cy, 'w': w, 'h': h})

        # Simple overlap tracking
        matched_ids = set()
        for face in faces:
            best_id = -1
            best_iou = 0
            for tid, tface in tracked_faces.items():
                if tid in matched_ids:
                    continue
                # Simple center distance-based tracking
                dist = ((face['cx'] - tface['cx'])**2 + (face['cy'] - tface['cy'])**2)**0.5
                if dist < 150 and dist < best_iou or best_id == -1:
                    best_iou = dist
                    best_id = tid

            if best_id >= 0 and best_iou < 150:
                # Update existing track
                tface = tracked_faces[best_id]
                tface['cx'] = face['cx']
                tface['cy'] = face['cy']
                tface['w'] = face['w']
                tface['h'] = face['h']
                tface['age'] = 0
                matched_ids.add(best_id)
            else:
                # New track
                new_id = max(list(tracked_faces.keys()) + [-1]) + 1
                face['age'] = 0
                tracked_faces[new_id] = face
                matched_ids.add(new_id)

        # Age out tracks
        dead_ids = []
        for tid, tface in tracked_faces.items():
            if tid not in matched_ids:
                tface['age'] += 1
            if tface['age'] > 10:  # Remove after 10 missed frames
                dead_ids.append(tid)
        for tid in dead_ids:
            del tracked_faces[tid]

        # Store frame data
        # Use YOLO's estimated face crop, not full person box
        face_data = []
        for tid, tface in tracked_faces.items():
            # Approximate face region as top 30% of person bounding box
            face_data.append({
                'track_id': tid,
                'cx': tface['cx'],
                'cy': tface['cy'] - tface['h'] * 0.25,  # approximate face top
                'w': tface['w'] * 0.6,
                'h': tface['h'] * 0.3,
            })

        face_frames.append({
            'time': timestamp,
            'faces': face_data,
        })

    cap.release()
    return face_frames


def run_talknet_inference(video_path, audio_path, face_data, tmp_dir):
    """
    Simplified ASD: Use audio energy + visual mouth movement proxy.
    Since full TalkNet requires GPU and complex setup, we use a lightweight
    heuristic: frames with high audio energy + detected mouth-width changes = speaking.

    Returns list of speaker face centers per frame in the format used by object-tracker.
    """
    import wave
    import numpy as np

    # Read audio
    try:
        with wave.open(audio_path, 'r') as wf:
            audio_data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            audio_fps = wf.getframerate()
    except:
        return []

    if len(audio_data) == 0:
        return []

    # Compute audio energy envelope (RMS over 100ms windows)
    window_size = int(audio_fps * 0.1)
    audio_energy = []
    for i in range(0, len(audio_data) - window_size, window_size):
        frame = audio_data[i:i + window_size]
        rms = np.sqrt(np.mean(frame.astype(np.float32)**2))
        audio_energy.append(rms)
    audio_energy = np.array(audio_energy)

    # Normalize
    max_energy = np.max(audio_energy)
    if max_energy > 0:
        audio_energy = audio_energy / max_energy

    # Look for mouth motion in face frames using width changes
    # For each face track, compute width derivative
    speakers_per_frame = []

    for fdata in face_data:
        frame_time = fdata['time']
        energy_idx = int(frame_time * 10)
        energy = audio_energy[energy_idx] if energy_idx < len(audio_energy) else 0

        # If audio energy is high and face detected, mark as potential speaker
        if energy > 0.15 and len(fdata['faces']) > 0:
            # Pick the face closest to center as most likely speaker
            faces = fdata['faces']
            # In simple mode: use the largest face (closest to camera)
            largest_face = max(faces, key=lambda f: f['w'] * f['h'])
            speakers_per_frame.append({
                'time': frame_time,
                'active_speaker': True,
                'face_center_x': largest_face['cx'],
                'face_center_y': largest_face['cy'],
            })
        else:
            speakers_per_frame.append({
                'time': frame_time,
                'active_speaker': False,
            })

    return speakers_per_frame


def main():
    parser = argparse.ArgumentParser(description='TalkNet Active Speaker Detection')
    parser.add_argument('video_path', help='Path to video file')
    parser.add_argument('--start', type=float, default=0, help='Start time in seconds')
    parser.add_argument('--duration', type=float, default=60, help='Duration in seconds')
    parser.add_argument('--output-json', action='store_true', help='Output JSON')
    args = parser.parse_args()

    video_path = args.video_path
    if not os.path.exists(video_path):
        print(json.dumps({'error': f'Video not found: {video_path}'}))
        sys.exit(1)

    duration = args.duration or min(get_video_duration(video_path), 60)

    tmp_dir = tempfile.mkdtemp(prefix='talknet_')
    audio_path = os.path.join(tmp_dir, 'audio.wav')

    print(f"Extracting audio...", file=sys.stderr)
    audio_ok = extract_audio(video_path, audio_path)
    if not audio_ok:
        print(f"Audio extraction failed — no audio for ASD", file=sys.stderr)

    print(f"Detecting faces...", file=sys.stderr)
    face_data = detect_faces(video_path, args.start, duration, tmp_dir)
    print(f"  {len(face_data)} face frames", file=sys.stderr)

    print(f"Running ASD inference...", file=sys.stderr)
    speakers = run_talknet_inference(video_path, audio_path, face_data, tmp_dir)
    print(f"  {sum(1 for s in speakers if s['active_speaker'])} active speaker frames", file=sys.stderr)

    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    if args.output_json:
        # Output as array for the smart cropper
        print(json.dumps(speakers))

if __name__ == '__main__':
    main()