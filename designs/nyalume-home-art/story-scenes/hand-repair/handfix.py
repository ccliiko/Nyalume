import io, json, time, urllib.request, urllib.parse, uuid
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
COMFY = 'http://127.0.0.1:8190'
SOURCE = Path(r'C:\Users\32776\AppData\Local\Temp\codex-clipboard-7924adae-fc2f-4149-adae-4eebae41983d.png')
OUT = ROOT / 'right-hand-fixed-v2.png'
SEED = 9275204

def post_json(path, obj):
    req = urllib.request.Request(COMFY + path, json.dumps(obj).encode(), {'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=30))

def upload(name, data):
    boundary = '----Codex' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode() + data + f'\r\n--{boundary}--\r\n'.encode())
    req = urllib.request.Request(COMFY + '/upload/image', body, {'Content-Type':f'multipart/form-data; boundary={boundary}'})
    return json.load(urllib.request.urlopen(req, timeout=30))['name']

def main():
    original = Image.open(SOURCE).convert('RGB')
    w,h = original.size
    original.save(ROOT/'source-original.png')
    mask = Image.new('L', original.size, 0)
    d = ImageDraw.Draw(mask)
    # Local mask covers the existing hand, wrist junction, and just enough bench for clean edges.
    d.rounded_rectangle((0,690,126,762), radius=14, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(6))
    mask.save(ROOT/'hand-mask-v2.png')
    in_name = upload('nyalume_handfix_source_v2.png', (ROOT/'source-original.png').read_bytes())
    mask_name = upload('nyalume_handfix_mask_v2.png', (ROOT/'hand-mask-v2.png').read_bytes())
    source_wf = json.loads((ROOT.parent/'double_lora_BA.source.json').read_text(encoding='utf-8'))
    nodes = {str(n['id']):n for n in source_wf['nodes']}
    ckpt = nodes['3']['widgets_values'][0]
    lora1 = nodes['11']['widgets_values'][0]
    lora2 = nodes['9']['widgets_values'][0]
    pos = 'masterpiece, best quality, anime illustration, one slender right hand resting palm-down on top of the wooden bench, fingers pointing toward the left edge, five distinct gently relaxed fingers with natural spacing and lengths, visible thumb, natural knuckles and wrist seamlessly connected to the cream sailor sleeve, hand clearly visible against the wood, supporting her as she leans, correct perspective, soft peach skin, delicate clean lineart, same soft cel shading and daylight'
    neg = 'worst quality, low quality, bad anatomy, bad hands, malformed hand, extra fingers, missing fingers, fused fingers, twisted fingers, extra thumb, broken wrist, detached hand, floating hand, duplicate hand, hidden hand, sleeve covering hand, text, watermark, photorealistic, 3d render'
    wf = {
      '1': {'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':ckpt}},
      '2': {'class_type':'LoraLoader','inputs':{'model':['1',0],'clip':['1',1],'lora_name':lora1,'strength_model':0.4,'strength_clip':0.4}},
      '3': {'class_type':'LoraLoader','inputs':{'model':['2',0],'clip':['2',1],'lora_name':lora2,'strength_model':0.65,'strength_clip':0.65}},
      '4': {'class_type':'CLIPTextEncode','inputs':{'clip':['3',1],'text':pos}},
      '5': {'class_type':'CLIPTextEncode','inputs':{'clip':['3',1],'text':neg}},
      '6': {'class_type':'LoadImage','inputs':{'image':in_name}},
      '7': {'class_type':'LoadImageMask','inputs':{'image':mask_name,'channel':'red'}},
      '8': {'class_type':'VAEEncodeForInpaint','inputs':{'pixels':['6',0],'vae':['1',2],'mask':['7',0],'grow_mask_by':8}},
      '9': {'class_type':'KSampler','inputs':{'model':['3',0],'positive':['4',0],'negative':['5',0],'latent_image':['8',0],'seed':9275304,'steps':30,'cfg':7.0,'sampler_name':'dpmpp_2m','scheduler':'karras','denoise':0.9}},
      '10': {'class_type':'VAEDecode','inputs':{'samples':['9',0],'vae':['1',2]}},
      '11': {'class_type':'SaveImage','inputs':{'images':['10',0],'filename_prefix':'nyalume_handfix/right_hand_v2'}}
    }
    (ROOT/'workflow.json').write_text(json.dumps(wf,ensure_ascii=False,indent=2),encoding='utf-8')
    prompt = post_json('/prompt', {'prompt':wf,'client_id':'nyalume-handfix'})['prompt_id']
    print('prompt',prompt,flush=True)
    for _ in range(600):
        time.sleep(2)
        hst = json.load(urllib.request.urlopen(COMFY+'/history/'+prompt, timeout=30)).get(prompt)
        if not hst: continue
        if hst.get('status',{}).get('status_str') == 'error':
            raise RuntimeError(json.dumps(hst.get('status'),ensure_ascii=False))
        images = hst.get('outputs',{}).get('11',{}).get('images',[])
        if images:
            im = images[0]
            q = urllib.parse.urlencode(im)
            data = urllib.request.urlopen(COMFY+'/view?'+q,timeout=60).read()
            OUT.write_bytes(data)
            print('saved',OUT,Image.open(io.BytesIO(data)).size,flush=True)
            return
    raise TimeoutError('ComfyUI job did not finish within 20 minutes')

if __name__ == '__main__': main()

