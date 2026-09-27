"""Local ComfyUI asset production only. Usage: generate.py submit dessert star | collect"""
import copy
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlencode
from PIL import Image

HERE = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8190'
SOURCE = Path(r'D:\Downloads\ComfyUI-aki-v3.2\ComfyUI\user\default\workflows\double_lora_BA.json')
IDENTITY = '1girl, solo, young adult woman, 22 years old, petite adult proportions, cute anime face, (silver white hair:1.3), (very long single side ponytail:1.3), straight bangs, (pink purple eyes:1.2), recognizable consistent character, natural adult feminine figure'
NEGATIVE = 'worst quality, low quality, lowres, bad anatomy, bad hands, extra fingers, fused fingers, missing fingers, extra arms, extra legs, detached limbs, broken wrist, (2girls:1.4), multiple people, duplicate character, collage, panels, split screen, mirrored person, (text:1.2), speech bubble, lettering, watermark, logo, (child:1.4), loli, underage, toddler, chibi, baby proportions, mature woman, aged face, photorealistic, 3d render, pinup, seductive pose, cleavage focus, nude, nipples, underwear, orange fruit, orange slice, tangerine, hair ornament, animal ears, twin tails, double ponytail, short hair'

def request(path, body=None):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def submit(names):
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    (HERE / 'double_lora_BA.source.json').write_bytes(SOURCE.read_bytes())
    links = {link[0]: link for link in source['links']}
    nodes = {str(node['id']): node for node in source['nodes']}
    wf = {}
    def convert(node_id):
        node = nodes[node_id]
        values = iter(node.get('widgets_values') or [])
        inputs = {}
        for field in node.get('inputs', []):
            if field.get('link') is not None:
                link = links[field['link']]
                origin = str(link[1])
                if origin not in wf:
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
    wf['8']['inputs']['text'] = NEGATIVE
    wf['14']['inputs'].update(denoise=0.28, noise_mask_feather=8)
    wf['25']['inputs'].update(crop_factor=2.5, dilation=4)
    wf['27']['inputs'].update(denoise=0.26, noise_mask_feather=8)
    shots = json.loads((HERE / 'shots.json').read_text(encoding='utf-8'))
    jobs_path = HERE / 'jobs.json'
    jobs = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
    for name in names or shots:
        shot = shots[name]
        job = copy.deepcopy(wf)
        positive = ', '.join(['masterpiece, best quality, very aesthetic', shot['action'], IDENTITY, shot['outfit'], shot['setting']])
        job['8']['inputs']['text'] = NEGATIVE + ', ' + shot.get('negative', '')
        job['7']['inputs']['text'] = positive
        job['23']['inputs']['text'] = positive + ', anatomically correct hands, five fingers, natural wrist, preserve original gesture and held object'
        job['30'] = {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 1], 'text': 'best quality, ' + IDENTITY + ', ' + shot['expression'] + ', delicate anime lineart'}}
        job['14']['inputs']['positive'] = ['30', 0]
        job['20']['inputs'].update(width=shot['size'][0], height=shot['size'][1])
        job['6']['inputs']['seed'] = shot['seed']
        job['6']['inputs']['cfg'] = shot.get('cfg', 8)
        if shot.get('pose'):
            job['40'] = {'class_type': 'LoadImage', 'inputs': {'image': shot['pose']}}
            job['41'] = {'class_type': 'ControlNetLoader', 'inputs': {'control_net_name': shot.get('control_model', 'control-lora-openposeXL2-rank256.safetensors')}}
            job['42'] = {'class_type': 'ControlNetApplyAdvanced', 'inputs': {'positive': ['7', 0], 'negative': ['8', 0], 'control_net': ['41', 0], 'image': ['40', 0], 'strength': shot.get('control_strength', 0.7), 'start_percent': 0.0, 'end_percent': 0.95}}
            job['6']['inputs']['positive'] = ['42', 0]
            job['6']['inputs']['negative'] = ['42', 1]
        job['5']['inputs']['filename_prefix'] = 'nyalume_story_scenes/' + name
        job['31'] = {'class_type': 'SaveImage', 'inputs': {'images': ['2', 0], 'filename_prefix': 'nyalume_story_scenes/base_' + name}}
        key = name + '-' + str(shot['seed'])
        (HERE / (key + '.json')).write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        result = request('/prompt', {'prompt': job, 'client_id': 'nyalume-story-assets'})
        jobs[key] = {'prompt_id': result['prompt_id'], 'scene': name, 'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest()}
        jobs_path.write_text(json.dumps(jobs,indent=2),encoding='utf-8')
        print(key, result['prompt_id'], flush=True)

def collect():
    for key, job in json.loads((HERE / 'jobs.json').read_text()).items():
        if job.get('status', '').startswith('cancelled'):
            continue
        target = HERE / (key + '.png')
        if target.exists():
            print(key, 'saved'); continue
        history = request('/history/' + job['prompt_id']).get(job['prompt_id'])
        if not history:
            print(key, 'pending'); continue
        outputs = history.get('outputs', {}).get('5', {}).get('images', [])
        if not outputs:
            print(key, history.get('status')); continue
        with urllib.request.urlopen(URL + '/view?' + urlencode(outputs[0]), timeout=30) as response:
            data = response.read()
        target.write_bytes(data)
        Image.open(io.BytesIO(data)).convert('RGB').save(HERE / (key + '.webp'), quality=93, method=6)
        print(key, 'saved', flush=True)

if __name__ == '__main__':
    if sys.argv[1] == 'submit':
        submit(sys.argv[2:])
    else:
        collect()
