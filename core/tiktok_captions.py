#!/usr/bin/env python3
"""
TikTok-Style Caption Generator
Generates ASS subtitles with word-level timing from faster-whisper,
styled with TikTok aesthetics: uppercase, Impact font, yellow, thick outline, pop animation.

Supports dual-language mode (original + translated):
- Big font for translated text (160px, yellow)
- Small font for original text (80px, white), positioned below
- Falls back to single-language if no translation provided

Smart word grouping: 1 word by default, groups 2 words together
if individual words are too fast (< 0.2s each) to avoid overlapping.

Usage:
  python3 tiktok_captions.py <video_path> <output_ass_path> [--translate "translated text"]
"""
import sys
import os
import json
import argparse
from faster_whisper import WhisperModel

def format_ass_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"

def group_words(words, max_words=2, min_duration=0.2):
    """
    Group words into chunks of up to max_words.
    Groups only when individual words are shorter than min_duration (fast speech).
    Returns list of chunks: [{text: "WORD1 WORD2", start: float, end: float}]
    """
    if not words:
        return []

    chunks = []
    i = 0
    while i < len(words):
        w = words[i]
        duration = w['end'] - w['start']

        # If this word is slow enough, keep it solo
        if duration >= min_duration or i + 1 >= len(words):
            chunks.append({
                'text': w['word'],
                'start': w['start'],
                'end': w['end'],
            })
            i += 1
        else:
            # Group with the next word (2 words at once)
            next_w = words[i + 1]
            chunks.append({
                'text': f"{w['word']} {next_w['word']}",
                'start': w['start'],
                'end': next_w['end'],
            })
            i += 2

    return chunks

def generate_tiktok_captions(video_path, ass_path, translated_text=None):
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(video_path, word_timestamps=True)

    # Build original word list
    original_words = []
    for segment in segments:
        for word in segment.words:
            clean_word = word.word.strip().upper()
            clean_word = clean_word.replace(".", "").replace(",", "").replace("?", "").replace("!", "")
            if not clean_word:
                continue
            original_words.append({
                'word': clean_word,
                'start': word.start,
                'end': word.end,
            })

    # Group original words into chunks (1 word by default, 2 words if too fast)
    original_chunks = group_words(original_words)

    # Build translated word chunks (if translation provided)
    translated_chunks = None
    if translated_text:
        orig_word_count = len(original_words)
        trans_word_parts = translated_text.strip().split()
        if orig_word_count > 0 and len(trans_word_parts) > 0:
            # Build raw translated words mapped to original timing
            raw_translated = []
            for i, word_text in enumerate(trans_word_parts):
                ratio = i / max(1, len(trans_word_parts))
                orig_idx = min(int(ratio * orig_word_count), orig_word_count - 1)
                start_t = original_words[orig_idx]['start']
                end_t = original_words[min(orig_idx + 1, orig_word_count - 1)]['end'] if orig_idx + 1 < orig_word_count else start_t + 0.3
                clean = word_text.strip().upper().replace(".", "").replace(",", "").replace("?", "").replace("!", "")
                if clean:
                    raw_translated.append({
                        'word': clean,
                        'start': start_t,
                        'end': end_t,
                    })
            # Group translated words the same way
            translated_chunks = group_words(raw_translated)

    # Build ASS content
    ass_content = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""

    if translated_chunks:
        ass_content += """Style: TransBig,Impact,160,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,10,0,2,10,10,480,1
Style: OrigSmall,Arial,80,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,10,10,480,1
"""
    else:
        ass_content += """Style: TikTokStyle,Impact,140,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,10,0,2,10,10,480,1
"""

    ass_content += """
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    word_count = 0

    if translated_chunks:
        # Dual-language mode: show translated big + original small
        for tc, oc in zip(translated_chunks, original_chunks[:len(translated_chunks)]):
            start_time = format_ass_time(max(0, tc['start'] + 0.08))
            end_time = format_ass_time(tc['end'])
            anim_tag = r"{\fscx50\fscy50\t(0,80,\fscx100\fscy100)}"
            ass_content += f"Dialogue: 0,{start_time},{end_time},TransBig,,0,0,0,,{anim_tag}{tc['text']}\n"
            ass_content += f"Dialogue: 0,{start_time},{end_time},OrigSmall,,0,0,40,,{anim_tag}{oc['text']}\n"
            word_count += 1
    else:
        # Single-language mode — use grouped chunks
        for chunk in original_chunks:
            adjusted_start = max(0, chunk['start'] + 0.08)
            start_time = format_ass_time(adjusted_start)
            end_time = format_ass_time(chunk['end'])
            anim_tag = r"{\fscx50\fscy50\t(0,80,\fscx100\fscy100)}"
            ass_content += f"Dialogue: 0,{start_time},{end_time},TikTokStyle,,0,0,0,,{anim_tag}{chunk['text']}\n"
            word_count += 1

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    result = {"word_count": word_count, "ass_file": ass_path}
    print(json.dumps(result))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate TikTok-style captions')
    parser.add_argument('video_path', help='Path to video file')
    parser.add_argument('output_ass_path', help='Path for output ASS file')
    parser.add_argument('--translate', help='Translated text', default=None)
    args = parser.parse_args()
    generate_tiktok_captions(args.video_path, args.output_ass_path, args.translate)