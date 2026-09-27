import io,json,time,urllib.request,urllib.parse,uuid
from pathlib import Path
from PIL import Image,ImageDraw,ImageFilter
ROOT=Path(__file__).resolve().parent; COMFY='http://127.0.0.1:8190'; SOURCE=Path(r'C:\Users\32776\AppData\Local\Temp\codex-clipboard-7924adae-fc2f-4149-adae-4eebae41983d.png')
def upload(name,data):
 b='----Codex'+uuid.uuid4().hex; body=(f'--{b}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode()+data+f'\r\n--{b}--\r\n'.encode()); req=urllib.request.Request(COMFY+'/upload/image',body,{'Content-Type':f'multipart/form-data; boundary={b}'}); return json.load(urllib.request.urlopen(req,timeout=30))['name']
def get(p): return json.load(urllib.request.urlopen(COMFY+p,timeout=30))
def main():
 src=Image.open(SOURCE).convert('RGB'); w,h=src.size; pad=72; base=Image.new('RGB',(w+pad,h),src.getpixel((0,650))); base.paste(src,(pad,0)); base.save(ROOT/'fivefinger-source.png')
 mask=Image.new('L',base.size,0); ImageDraw.Draw(mask).rounded_rectangle((0,682,236,780),radius=18,fill=255); mask=mask.filter(ImageFilter.GaussianBlur(7)); mask.save(ROOT/'fivefinger-mask.png')
 base_name=upload('nyalume_5finger_base.png',(ROOT/'fivefinger-source.png').read_bytes()); mask_name=upload('nyalume_5finger_mask.png',(ROOT/'fivefinger-mask.png').read_bytes())
 source=json.loads((ROOT.parent/'double_lora_BA.source.json').read_text(encoding='utf-8')); nd={str(n['id']):n for n in source['nodes']}; ckpt=nd['3']['widgets_values'][0]; l1=nd['11']['widgets_values'][0]; l2=nd['9']['widgets_values'][0]
 positive='masterpiece, best quality, anime illustration, close view of a delicate feminine right hand resting flat palm-down on the wooden bench, hand fully visible inside frame, exactly five clearly visible separate fingers: one thumb plus index finger, middle finger, ring finger, little finger, all five digits individually readable, fingers gently fanned toward the left, natural elegant finger lengths and joints, wrist naturally joined to the existing cream sleeve cuff on the right, soft peach skin, matching original perspective, clean anime lineart, gentle cel shading'
 negative='worst quality, low quality, bad anatomy, bad hands, malformed hand, only three fingers, four fingers, extra fingers, missing finger, fused fingers, overlapping fingers, hidden fingers, cropped fingers, fingers under sleeve, clenched fist, broken wrist, detached hand, duplicate hand, text, watermark, photorealistic, 3d render'
 wf={
 '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':ckpt}},
 '2':{'class_type':'LoraLoader','inputs':{'model':['1',0],'clip':['1',1],'lora_name':l1,'strength_model':0.4,'strength_clip':0.4}},
 '3':{'class_type':'LoraLoader','inputs':{'model':['2',0],'clip':['2',1],'lora_name':l2,'strength_model':0.65,'strength_clip':0.65}},
 '4':{'class_type':'CLIPTextEncode','inputs':{'clip':['3',1],'text':positive}},
 '5':{'class_type':'CLIPTextEncode','inputs':{'clip':['3',1],'text':negative}},
 '6':{'class_type':'LoadImage','inputs':{'image':base_name}},
 '7':{'class_type':'LoadImageMask','inputs':{'image':mask_name,'channel':'red'}},
 '8':{'class_type':'VAEEncodeForInpaint','inputs':{'pixels':['6',0],'vae':['1',2],'mask':['7',0],'grow_mask_by':12}},
 '9':{'class_type':'KSampler','inputs':{'model':['3',0],'positive':['4',0],'negative':['5',0],'latent_image':['8',0],'seed':9275704,'steps':32,'cfg':7.5,'sampler_name':'dpmpp_2m','scheduler':'karras','denoise':0.98}},
 '10':{'class_type':'VAEDecode','inputs':{'samples':['9',0],'vae':['1',2]}},
 '11':{'class_type':'SaveImage','inputs':{'images':['10',0],'filename_prefix':'nyalume_handfix/five_fingers'}}}
 (ROOT/'fivefinger-workflow.json').write_text(json.dumps(wf,ensure_ascii=False,indent=2),encoding='utf-8')
 req=urllib.request.Request(COMFY+'/prompt',json.dumps({'prompt':wf,'client_id':'nyalume-fivefinger'}).encode(),{'Content-Type':'application/json'}); pid=json.load(urllib.request.urlopen(req,timeout=30))['prompt_id']; print('prompt',pid,flush=True)
 for _ in range(600):
  time.sleep(2); hist=get('/history/'+pid).get(pid)
  if not hist: continue
  if hist.get('status',{}).get('status_str')=='error': raise RuntimeError(json.dumps(hist['status'],ensure_ascii=False))
  ims=hist.get('outputs',{}).get('11',{}).get('images',[])
  if ims:
   p=urllib.parse.urlencode(ims[0]); (ROOT/'right-hand-five-fingers.png').write_bytes(urllib.request.urlopen(COMFY+'/view?'+p,timeout=60).read()); print('saved',ROOT/'right-hand-five-fingers.png',flush=True); return
 raise TimeoutError('ComfyUI generation timed out')
if __name__=='__main__': main()
