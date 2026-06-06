#!/usr/bin/env python3
"""
NLLB-200 Translation — Translates non-English text to English using Facebook's NLLB-200 distilled 600M model.

Usage:
  python3 nllb-translate.py "original text"
  Output: {"translated_text": "english translation", "source_lang": "xxx"}
"""
import sys
import json
import os
import warnings
warnings.filterwarnings('ignore')

def translate(text):
    if not text or not text.strip():
        return {"translated_text": "", "source_lang": "unknown"}

    try:
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        import torch

        model_name = "facebook/nllb-200-distilled-600M"
        cache_dir = os.path.expanduser("~/.cache/huggingface/hub")

        # Load tokenizer and model on CPU
        tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
        model = AutoModelForSeq2SeqLM.from_pretrained(
            model_name,
            cache_dir=cache_dir,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True
        )

        # Tokenize and translate
        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            translated_tokens = model.generate(
                **inputs,
                forced_bos_token_id=tokenizer.lang_code_to_id["eng_Latn"],
                max_length=512,
                num_beams=4,
                early_stopping=True
            )
        translated_text = tokenizer.decode(translated_tokens[0], skip_special_tokens=True)

        return {"translated_text": translated_text, "source_lang": "auto"}
    except Exception as e:
        return {"error": str(e), "translated_text": ""}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python3 nllb-translate.py <text>"}))
        sys.exit(1)

    text = sys.argv[1]
    result = translate(text)
    print(json.dumps(result))