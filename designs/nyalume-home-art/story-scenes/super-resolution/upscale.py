import json, time, urllib.parse, urllib.request, uuid
from pathlib import Path
from PIL import Image

base = 'http://127.0.0.1:8190'
src = Path(r'C:\Users\32776\AppData\Local\Temp\codex-clipboard-b756708c-5124-4fae-b257-0e77a60b6752.png')
out = Path(__file__).resolve().parent / 'nyalume-lineart-4x.png'
boundary = '----Codex' + uuid.uuid4().hex
body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="nyalume_lineart_source.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + src.read_bytes() + f'\r\n--{boundary}--\r\n'.encode())
req = urllib.request.Request(base + '/upload/image', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
name = json.load(urllib.request.urlopen(req, timeout=30))['name']
workflow = {
 '1': {'class_type':'LoadImage','inputs':{'image':name}},
 '2': {'class_type':'UpscaleModelLoader','inputs':{'model_name':'RealESRGAN_x4plus_anime_6B.pth'}},
 '3': {'class_type':'ImageUpscaleWithModel','inputs':{'upscale_model':['2',0],'image':['1',0]}},
 '4': {'class_type':'SaveImage','inputs':{'images':['3',0],'filename_prefix':'nyalume_upscale/lineart_4x'}}
}
(out.parent / 'workflow.json').write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding='utf-8')
req = urllib.request.Request(base+'/prompt', json.dumps({'prompt':workflow,'client_id':'nyalume-upscale'}).encode(), {'Content-Type':'application/json'})
pid = json.load(urllib.request.urlopen(req, timeout=30))['prompt_id']
print('prompt', pid, flush=True)
for _ in range(600):
 time.sleep(2)
 h = json.load(urllib.request.urlopen(base+'/history/'+pid, timeout=30)).get(pid)
 if not h: continue
 if h.get('status',{}).get('status_str') == 'error': raise RuntimeError(json.dumps(h['status'], ensure_ascii=False))
 images = h.get('outputs',{}).get('4',{}).get('images',[])
 if images:
  result = urllib.request.urlopen(base+'/view?'+urllib.parse.urlencode(images[0]), timeout=120).read()
  out.write_bytes(result)
  print('saved', out, 'size', Image.open(out).size, 'bytes', len(result), flush=True)
  break
else: raise TimeoutError('upscale timed out')
