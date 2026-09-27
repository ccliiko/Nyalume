"""Generate Nyalume home scenes through the user's local ComfyUI.

python tools/generate_home_art.py submit|collect
Saved API workflows include every prompt, seed and model setting.
"""
import copy
import io
import json
import sys
import urllib.request
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'designs/nyalume-home-art/images'
RUN = ROOT / 'docs/home-art-workflows'
URL = 'http://127.0.0.1:8190'
SOURCE = Path(r'D:\Downloads\ComfyUI-aki-v3.2\ComfyUI\user\default\workflows\double_lora_BA.json')
CHARACTER = 'masterpiece, best quality, very aesthetic, absurdres, highres, 1girl, solo, 22-year-old young adult woman, petite adult woman, adult body proportions, normal head proportions, cute anime face, soft cheeks, lively expression, (silver hair:1.4), (white hair:1.2), (very long hair:1.25), (single side ponytail:1.35), hair over shoulder, straight bangs, (pink-purple gradient eyes:1.25), pink eyes, gentle smile, slight blush, looking at viewer, no hair ornament, slender adult feminine figure, medium breasts, (character focus:1.3), (large character in foreground:1.2), centered single character, delicate anime lineart, soft cel shading, detailed illustration, restrained atmospheric background, portrait composition, one continuous scene'
NEGATIVE = 'worst quality, low quality, lowres, bad anatomy, bad hands, extra fingers, extra limbs, missing fingers, fused fingers, twisted wrist, disconnected hand, extra hands, interlocked fingers, heart hands, hands together, overlapping hands, (speech bubble:1.5), dialogue, japanese text, comic lettering, text, watermark, signature, logo, letters, multiple girls, collage, panels, split screen, (2girls:1.4), duplicate character, reflection of person, mirrored person, comic page, (child:1.5), (loli:1.5), underage, toddler, chibi, oversized head, baby face, child proportions, flat chest, nude, nipples, explicit, underwear, orange fruit, orange slice, tangerine, hair ornament, hairclip, animal ears, twin tails, double ponytail, hair loops, braided hair, tangled hair, hair rings, floating hair strands'
SCENES = {
    'sakura': '(cowboy shot:1.25), (sitting:1.2), crossed legs, bare legs, knees visible, (white and pink short dress:1.25), pink frills, pink lace trim, pink ribbons, white detached sleeves, soft neckline, fitted bodice, skirt covering hips, one hand resting palm down on sofa beside hip, other hand resting flat on knee, separated hands, arms apart, relaxed wrists, warm ivory and pale rose shoujo illustration, cherry blossoms outside window, cream sofa, sunlight on legs, soft translucent light, beautifully rendered face',
    'neon': '(cowboy shot:1.3), (head to knees:1.25), camera pulled back, full arms in frame, (black halter dress:1.3), pink ribbon at neckline, (cleavage:1.1), bare shoulders, (breast focus:1.1), seated on velvet sofa, (both hands resting visibly on knees:1.3), arms apart, relaxed fingers, medium breasts, city pop cel illustration, dark teal and muted magenta, rain outside window, neon bokeh, sweet playful smile, cinematic rim lighting',
    'moon': '(looking back:1.2), three-quarter rear view, (backless dress:1.35), bare back, white and pink satin dress, ribbon, side slit, thigh visible through slit, hips covered, ponytail swept over one shoulder away from back, slender adult shoulders, one relaxed hand resting on hip, other arm at side, moonlit gothic arch, deep midnight blue and silver fantasy illustration, moonlight on back, quiet shadows',
    'pixel': '(sitting:1.15), crossed legs, (pink off-shoulder sweater:1.15), sleeves ending at wrists, both hands visible, one hand resting on knee, other hand resting on bed beside hip, white short skirt, white thighhighs, skirt covering hips,  warm bedside lamp, cozy bedroom, rain outside window, softly glowing blue and lavender night outside, gentle warm interior lighting, delicate lineart, soft cel shading, smooth colors, detailed hair strands, intimate quiet atmosphere',
    'manga': '(monochrome:1.5), (black and white illustration:1.3), ink drawing, fine pen lines, delicate crosshatching, sitting sideways on cafe chair, crossed legs, bare legs, (white frilled short dress:1.2), lace trim, ribbons, sleeveless, soft neckline, adult feminine figure, one hand resting on knee, other hand resting on chair seat beside hip, both hands visible, gentle smile, cafe window, no speech bubbles, cowboy shot',
}

def request(path, body=None):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def submit():
    RUN.mkdir(parents=True, exist_ok=True)
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    (RUN / 'double_lora_BA.source.json').write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding='utf-8')
    links = {link[0]: link for link in source['links']}
    nodes = {str(node['id']): node for node in source['nodes']}
    wf = {}
    def convert(node_id):
        if node_id in wf:
            return
        node = nodes[node_id]
        inputs = {}
        values = iter(node.get('widgets_values') or [])
        for field in node.get('inputs', []):
            if field.get('link') is not None:
                link = links[field['link']]
                origin = str(link[1])
                convert(origin)
                inputs[field['name']] = [origin, link[2]]
            elif field.get('widget'):
                inputs[field['name']] = next(values)
                if field['name'] == 'seed':
                    assert next(values) in ('fixed', 'randomize', 'increment', 'decrement')
        wf[node_id] = {'class_type': node['type'], 'inputs': inputs}
    convert('5')
    assert wf['11']['inputs']['strength_model'] == 0.4
    assert wf['9']['inputs']['strength_model'] == 0.65
    assert wf['6']['inputs']['cfg'] == 8
    assert wf['27']['inputs']['denoise'] == 0.55
    wf['20']['inputs'].update(width=896, height=1152)
    wf['8']['inputs']['text'] = NEGATIVE
    # Preserve the user's BA graph, but keep inpainting close to the base pose.
    wf['14']['inputs'].update(denoise=0.32, noise_mask_feather=8)
    wf['25']['inputs'].update(crop_factor=2.5, dilation=4)
    wf['27']['inputs'].update(denoise=0.3, noise_mask_feather=8)
    wf['30'] = {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 1], 'text': 'masterpiece, best quality, 1girl, young adult woman, 22 years old, cute anime face, soft cheeks, pink-purple gradient eyes, silver white hair, very long hair, single side ponytail, straight bangs, gentle smile, slight blush, adult facial proportions, delicate lineart, soft cel shading'}}
    wf['14']['inputs']['positive'] = ['30', 0]
    jobs = json.loads((RUN / 'jobs.json').read_text()) if (RUN / 'jobs.json').exists() else {}
    for i, (name, scene) in enumerate(SCENES.items()):
        if len(sys.argv) > 2 and name != sys.argv[2]:
            continue
        job = copy.deepcopy(wf)
        job['7']['inputs']['text'] = CHARACTER + ', ' + scene
        job['23']['inputs']['text'] = CHARACTER + ', ' + scene + ', natural relaxed hand, fingers together, five fingers, anatomically correct wrist, preserve original pose'
        job['6']['inputs']['seed'] = (2026093200 if name in ('neon', 'manga') else 2026093100) + i
        job['5']['inputs']['filename_prefix'] = 'nyalume_home_BA_final/' + name
        (RUN / (name + '.json')).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        result = request('/prompt', {'prompt': job, 'client_id': 'nyalume-home-art'})
        jobs[name] = result['prompt_id']
        print(name, result['prompt_id'], flush=True)
    (RUN / 'jobs.json').write_text(json.dumps(jobs, indent=2), encoding='utf-8')

def collect():
    ART.mkdir(parents=True, exist_ok=True)
    for name, job_id in json.loads((RUN / 'jobs.json').read_text()).items():
        history = request('/history/' + job_id).get(job_id)
        if not history:
            print(name, 'pending')
            continue
        outputs = history.get('outputs', {}).get('5', {}).get('images', [])
        if not outputs:
            print(name, history.get('status'))
            continue
        from urllib.parse import urlencode
        with urllib.request.urlopen(URL + '/view?' + urlencode(outputs[0]), timeout=30) as response:
            data = response.read()
        picture = Image.open(io.BytesIO(data)).convert('RGB')
        if name == 'manga':
            picture = picture.convert('L').convert('RGB')
        picture.save(ART / (name + '.png'))
        picture.save(ART / (name + '.webp'), quality=91, method=6)
        print(name, 'saved', flush=True)

if __name__ == '__main__':
    {'submit': submit, 'collect': collect}[sys.argv[1]]()
