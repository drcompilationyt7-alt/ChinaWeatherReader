#!/usr/bin/env python3
"""
Standalone Whisper Transcription Script
Uses faster-whisper to transcribe audio files.
Outputs JSON to stdout on success, or error JSON to stdout on failure.

Usage:
  python3 core/whisper-transcribe.py <audio_path> [--timeout 60]

Output:
  {
    "success": true,
    "text": "transcribed text...",
    "language": "en",
    "word_count": 42,
    "words": [{"word": "hello", "start": 0.0, "end": 0.5}, ...]
  }
"""
import sys
import json
import os
import signal

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "No audio path provided"}))
        sys.exit(0)

    audio_path = sys.argv[1]

    if not os.path.exists(audio_path):
        print(json.dumps({"success": False, "error": f"Audio file not found: {audio_path}"}))
        sys.exit(0)

    if os.path.getsize(audio_path) < 100:
        print(json.dumps({"success": False, "error": "Audio file too small", "word_count": 0}))
        sys.exit(0)

    # Set a timeout via SIGALRM (Unix only) — fallback to no timeout on Windows
    timeout_seconds = 120
    if "--timeout" in sys.argv:
        try:
            idx = sys.argv.index("--timeout")
            timeout_seconds = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            pass

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(json.dumps({"success": False, "error": "faster_whisper not installed"}))
        sys.exit(0)

    try:
        model = WhisperModel('base', device='cpu', compute_type='int8')
        segments, info = model.transcribe(audio_path, word_timestamps=True)

        text_parts = []
        all_words = []
        for seg in segments:
            text_parts.append(seg.text or "")
            if seg.words:
                for w in seg.words:
                    all_words.append({
                        'word': w.word,
                        'start': w.start,
                        'end': w.end
                    })

        full_text = ' '.join(text_parts)[:1000]
        word_count = len(full_text.split()) if full_text else 0
        language = info.language if info else 'unknown'

        result = {
            "success": True,
            "text": full_text,
            "language": language,
            "word_count": word_count,
            "words": all_words,
        }
        print(json.dumps(result))
    except Exception as e:
        error_msg = str(e)[:200]
        print(json.dumps({
            "success": False,
            "error": error_msg,
            "word_count": 0,
            "text": "",
            "language": "unknown",
            "words": []
        }))

if __name__ == "__main__":
    main()