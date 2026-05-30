#!/usr/bin/env python3
"""
TikTok-Style Caption Generator
Generates ASS subtitles with word-level timing from faster-whisper,
styled with TikTok aesthetics: uppercase, Impact font, yellow, thick outline, pop animation.
Usage: python3 tiktok_captions.py <video_path> <output_ass_path>
"""
import sys
import os
import json
from faster_whisper import WhisperModel

def format_ass_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"

def generate_tiktok_captions(video_path, ass_path):
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(video_path, word_timestamps=True)

    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTokStyle,Impact,140,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,10,0,2,10,10,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    ass_content = ass_header
    word_count = 0

    for segment in segments:
        for word in segment.words:
            clean_word = word.word.strip().upper()
            clean_word = clean_word.replace(".", "").replace(",", "").replace("?", "").replace("!", "")
            if not clean_word:
                continue

            start_time = format_ass_time(word.start)
            end_time = format_ass_time(word.end)
            anim_tag = r"{\fscx50\fscy50\t(0,80,\fscx100\fscy100)}"
            line = f"Dialogue: 0,{start_time},{end_time},TikTokStyle,,0,0,0,,{anim_tag}{clean_word}\n"
            ass_content += line
            word_count += 1

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    # Return JSON so caller can get word count
    result = {"word_count": word_count, "ass_file": ass_path}
    print(json.dumps(result))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: python3 tiktok_captions.py <video_path> <output_ass_path>"}))
        sys.exit(1)
    generate_tiktok_captions(sys.argv[1], sys.argv[2])