"""
Kaggle kernel apq-ai-art-gen: renders the prompt list of the dataset <user>/apq-ai-art-input with Illustrious-XL v1.0
(OnomaAIResearch/Illustrious-XL-v1.0, ungated, CreativeML OpenRAIL++-M: commercial use of outputs allowed; NoobAI-XL is NOT used:
its licence prohibits commercial use). Generation only: our WD14 / censor / age / strict on-model gates run locally afterwards.
Inputs:  prompts.jsonl  one job per line {scope, id, k, seed, prompt, negative, w, h[, ipa, ref]};  budget.json {"minutes": N} (hard time box);
         ref_<id>.jpg  the official portrait for jobs with ipa > 0 (IP-Adapter plus-face SDXL, h94/IP-Adapter, Apache-2.0).
Output:  /kaggle/working/out/<scope>/<id>_<k>.png + .json, stats.json. Resumable: the driver only sends jobs without a picture.
No token or secret is read or written here.
"""
import json
import os
import subprocess
import sys
import time

T0 = time.time()
INP = next((dp for dp, _, fs in os.walk('/kaggle/input') if 'prompts.jsonl' in fs), '/kaggle/input/apq-ai-art-input')
print('input dir', INP, os.listdir(INP) if os.path.isdir(INP) else 'missing', flush=True)
OUT = '/kaggle/working/out'
MINUTES = float(json.load(open(os.path.join(INP, 'budget.json')))['minutes'])
END = T0 + MINUTES * 60 - 4 * 60
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'diffusers', 'transformers', 'accelerate', 'safetensors', 'compel', 'huggingface_hub'], check=False)
import torch  # noqa: E402
from compel import Compel, ReturnedEmbeddingsType  # noqa: E402
from diffusers import EulerAncestralDiscreteScheduler, StableDiffusionXLPipeline  # noqa: E402
from huggingface_hub import hf_hub_download  # noqa: E402

path = hf_hub_download('OnomaAIResearch/Illustrious-XL-v1.0', 'Illustrious-XL-v1.0.safetensors')
pipe = StableDiffusionXLPipeline.from_single_file(path, torch_dtype=torch.float16, use_safetensors=True).to('cuda')
pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)
pipe.set_progress_bar_config(disable=True)
comp = Compel(tokenizer=[pipe.tokenizer, pipe.tokenizer_2], text_encoder=[pipe.text_encoder, pipe.text_encoder_2],
              returned_embeddings_type=ReturnedEmbeddingsType.PENULTIMATE_HIDDEN_STATES_NON_NORMALIZED, requires_pooled=[False, True],
              truncate_long_prompts=False)
jobs = [json.loads(x) for x in open(os.path.join(INP, 'prompts.jsonl'), encoding='utf-8') if x.strip()]
jobs.sort(key=lambda j: float(j.get('ipa') or 0) > 0)   # plain jobs first; IP-Adapter is loaded once, before the first ipa job
ipa_on = False
ipa_broken = False
done = 0
fails = 0
sec = []
for j in jobs:
    if time.time() > END:
        break
    d = os.path.join(OUT, j['scope'])
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, f"{j['id']}_{j['k']}.png")
    if os.path.exists(f) or (ipa_broken and float(j.get('ipa') or 0) > 0):
        continue
    t = time.time()
    extra = {}
    try:
        if float(j.get('ipa') or 0) > 0:
            from PIL import Image
            if not ipa_on:
                pipe.load_ip_adapter('h94/IP-Adapter', subfolder='sdxl_models', weight_name='ip-adapter-plus-face_sdxl_vit-h.safetensors',
                                     image_encoder_folder='models/image_encoder')
                pipe.to('cuda')
                ipa_on = True
            pipe.set_ip_adapter_scale(float(j['ipa']))
            extra['ip_adapter_image'] = Image.open(os.path.join(INP, j['ref'])).convert('RGB')
        cond, pooled = comp(j['prompt'])
        neg, npooled = comp(j['negative'])
        if cond.shape[1] != neg.shape[1]:
            n = max(cond.shape[1], neg.shape[1])
            empty = comp('')[0]
            def padto(x):
                while x.shape[1] < n:
                    x = torch.cat([x, empty[:, :min(empty.shape[1], n - x.shape[1])]], dim=1)
                return x
            cond, neg = padto(cond), padto(neg)
        img = pipe(prompt_embeds=cond, pooled_prompt_embeds=pooled, negative_prompt_embeds=neg, negative_pooled_prompt_embeds=npooled,
                   width=j['w'], height=j['h'], num_inference_steps=int(j.get('steps') or 28), guidance_scale=5.5,
                   generator=torch.Generator('cpu').manual_seed(j['seed']), **extra).images[0]
    except Exception as e:  # noqa: BLE001
        print('job failed', j['id'], j['k'], str(e)[:300], flush=True)
        if float(j.get('ipa') or 0) > 0 and not ipa_on:
            ipa_broken = True
        fails += 1
        if fails >= 5 and done == 0:
            raise
        continue
    img.save(f)
    json.dump({'prompt': j['prompt'], 'seed': j['seed'], 'model': 'Illustrious-XL v1.0', 'ipa': j.get('ipa') or 0}, open(f[:-4] + '.json', 'w'))
    done += 1
    sec.append(time.time() - t)
json.dump({'generated': done, 'jobs': len(jobs), 'minutes': round((time.time() - T0) / 60, 1),
           'sec_per_image': round(sum(sec) / max(1, len(sec)), 1)}, open('/kaggle/working/stats.json', 'w'))
print('done', done, 'of', len(jobs))
