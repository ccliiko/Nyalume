import json, time, urllib.request, urllib.parse, uuid
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parent
COMFY='http://127.0.0.1:8190'
SOURCE=Path(r'C:\Users\32776\AppData\Local\Temp\codex-clipboard-7924adae-fc2f-4149-adae-4eebae41983d.png')
OUT=ROOT/'right-hand-detectorfix-v3.png'

def upload(name,data):
    boundary='----Codex'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode()+data+f'\r\n--{boundary}--\r\n'.encode())
    req=urllib.request.Request(COMFY+'/upload/image',body,{'Content-Type':f'multipart/form-data; boundary={boundary}'})
    return json.load(urllib.request.urlopen(req,timeout=30))['name']

def get(path): return json.load(urllib.request.urlopen(COMFY+path,timeout=30))

def main():
    base=json.loads((ROOT.parent/'double_lora_BA.source.json').read_text(encoding='utf-8'))
    nd={str(n['id']):n for n in base['nodes']}
    ckpt=nd['3']['widgets_values'][0]; lora1=nd['11']['widgets_values'][0]; lora2=nd['9']['widgets_values'][0]
    source_name=upload('nyalume_hand_detector_source.png',SOURCE.read_bytes())
    wf={
      '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':ckpt}},
      '2':{'class_type':'LoraLoader','inputs':{'model':['1',0],'clip':['1',1],'lora_name':lora1,'strength_model':0.4,'strength_clip':0.4}},
      '3':{'class_type':'LoraLoader','inputs':{'model':['2',0],'clip':['2',1],'lora_name':lora2,'strength_model':0.65,'strength_clip':0.65}},
      '4':{'class_type':'CLIPTextEncode','inputs':{'clip':['3',1],'text':'masterpiece, best quality, close-up of one small slender right hand, back of hand visible, resting flat on top of a wooden bench, all five fingers naturally extended toward the left edge, four long fingers gently together with clear separation at the tips, thumb angled naturally away from palm, visible knuckles, relaxed supportive pose, wrist continues under cream sailor sleeve cuff on the right, accurate perspective, delicate anime lineart, peach skin, soft cel shading'}},
      '5':{'class_type':'CLIPTextEncode','inputs':{'clip':['3',1],'text':'worst quality, malformed hand, extra fingers, missing fingers, fused fingers, twisted fingers, extra thumb, broken wrist, detached hand, duplicated hand, fingers tucked under sleeve, hidden hand, sleeve covering hand, bad anatomy, blurry, photorealistic, 3d render'}},
      '6':{'class_type':'LoadImage','inputs':{'image':source_name}},
      '7':{'class_type':'UltralyticsDetectorProvider','inputs':{'model_name':'bbox/hand_yolov8s.pt'}},
      '8':{'class_type':'BboxDetectorSEGS','inputs':{'bbox_detector':['7',0],'image':['6',0],'threshold':0.15,'dilation':32,'crop_factor':4.0,'drop_size':4,'labels':'all'}},
      '9':{'class_type':'DetailerForEach','inputs':{'image':['6',0],'segs':['8',0],'model':['3',0],'clip':['3',1],'vae':['1',2],'guide_size':512,'guide_size_for':False,'max_size':1024,'seed':9275604,'steps':30,'cfg':6.0,'sampler_name':'dpmpp_2m','scheduler':'karras','positive':['4',0],'negative':['5',0],'denoise':0.75,'feather':10,'noise_mask':True,'force_inpaint':True,'wildcard':'','cycle':1,'inpaint_model':False,'noise_mask_feather':20,'tiled_encode':False,'tiled_decode':False}},
      '10':{'class_type':'SaveImage','inputs':{'images':['9',0],'filename_prefix':'nyalume_handfix/detectorfix_v3'}}
    }
    (ROOT/'detector-workflow.json').write_text(json.dumps(wf,ensure_ascii=False,indent=2),encoding='utf-8')
    pid=json.load(urllib.request.urlopen(urllib.request.Request(COMFY+'/prompt',json.dumps({'prompt':wf,'client_id':'nyalume-handfix'}).encode(),{'Content-Type':'application/json'}),timeout=30))['prompt_id']
    print('prompt',pid,flush=True)
    for _ in range(600):
      time.sleep(2); h=get('/history/'+pid).get(pid)
      if not h: continue
      if h.get('status',{}).get('status_str')=='error': raise RuntimeError(json.dumps(h.get('status'),ensure_ascii=False))
      ims=h.get('outputs',{}).get('10',{}).get('images',[])
      if ims:
        OUT.write_bytes(urllib.request.urlopen(COMFY+'/view?'+urllib.parse.urlencode(ims[0]),timeout=60).read())
        print('saved',OUT,Image.open(OUT).size,flush=True); return
    raise TimeoutError('ComfyUI generation timed out')

if __name__=='__main__': main()


