const $=id=>document.getElementById(id);
let db, objectURL=null, imageBlob=null, strength=20;
const status=text=>$('wallpaper-status').textContent=text;
function apply(){
  if(objectURL)URL.revokeObjectURL(objectURL);
  objectURL=imageBlob?URL.createObjectURL(imageBlob):null;
  document.body.style.setProperty('--wallpaper-image',objectURL?`url("${objectURL}")`:'none');
  document.body.style.setProperty('--wallpaper-opacity',strength/100);
  $('wallpaper-strength').value=strength;
}
function save(){return new Promise((resolve,reject)=>{
  const tx=db.transaction('preferences','readwrite');
  if(imageBlob)tx.objectStore('preferences').put({blob:imageBlob,strength},'wallpaper');
  else tx.objectStore('preferences').delete('wallpaper');
  tx.oncomplete=resolve;tx.onabort=tx.onerror=()=>reject(Error('壁纸本次可用，但没能保存喵，请检查浏览器空间。'));
});}
async function persist(){try{if(!db)throw Error('浏览器存储暂不可用，壁纸本次可用喵。');await save();status(imageBlob?'壁纸保存好了，刷新也会陪着你喵。':'恢复默认背景啦。');}catch(e){status(e.message);}}
$('wallpaper-open').onclick=()=>$('wallpaper-dialog').showModal();
$('wallpaper-file').onchange=async e=>{
  const file=e.target.files[0];e.target.value='';if(!file)return;
  try{
    if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>10*1024*1024)throw Error('请选择 10 MB 以内的 PNG、JPG 或 WebP 喵。');
    const bitmap=await createImageBitmap(file);
    try{
      if(bitmap.width*bitmap.height>32000000)throw Error('图片分辨率有点大喵，先缩小到 3200 万像素以内吧。');
      const scale=Math.min(1,1920/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas');
      canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
      imageBlob=await new Promise(resolve=>canvas.toBlob(resolve,'image/webp',.85));
      if(!imageBlob)throw Error('没能读取这张图片喵。');
    }finally{bitmap.close();}
    apply();await persist();
  }catch(e){status(e.message);}
};
$('wallpaper-strength').oninput=e=>{strength=Number(e.target.value);document.body.style.setProperty('--wallpaper-opacity',strength/100);};
$('wallpaper-strength').onchange=persist;
$('wallpaper-clear').onclick=async()=>{imageBlob=null;apply();await persist();};
try{
const request=indexedDB.open('nyalume-web-decor',1);
request.onupgradeneeded=()=>request.result.createObjectStore('preferences');
request.onerror=()=>status('浏览器暂时不能保存壁纸喵。');
request.onsuccess=()=>{
  db=request.result;const read=db.transaction('preferences').objectStore('preferences').get('wallpaper');
  read.onsuccess=()=>{if(read.result){imageBlob=read.result.blob;strength=read.result.strength;apply();}};
};
}catch(e){status('浏览器暂时不能保存壁纸喵。');}
