import {storage,validateFiles,makeResolver} from './pet-assets.mjs';
const $ = id => document.getElementById(id);
let records=[], selected='', resolver=null, frame=null, current=null, loaded=false, saving=Promise.resolve();
const actions=[];
const volatileAssets=new Set();
let bootSerial=0;
const status = text => $('pet-status').textContent=text;
export function petAction(action) { if (frame && loaded) actions.push(action); }
function persist(record) {
  saving=saving.catch(()=>{}).then(()=>storage('put',record)).then(()=>true).catch(()=>{status('本次可以使用，但没能保存到浏览器喵；请检查可用空间或隐私模式。');return false;});
  return saving;
}
function options() {
  $('models').replaceChildren(new Option('选择模型',''));
  $('motions').replaceChildren(new Option('选择动作',''));
  for (const r of records) {
    if(r.kind==='model') for (const pmx of r.pmxs) $('models').add(new Option(pmx,JSON.stringify([r.id,pmx])));
    if(r.kind==='motion') $('motions').add(new Option(r.name,r.id));
  }
  $('models').value=selected;
  $('delete-model').disabled=!selected;
  const bytes=records.reduce((n,r)=>n+(r.size||0),0);
  $('storage-size').textContent=`资源 ${(bytes/1024/1024).toFixed(1)} MB`;
}
function dispose() {
  bootSerial++; loaded=false; actions.length=0;
  if(frame) { try{frame.contentWindow.__dispose?.();}catch{} frame.remove(); frame=null; }
  resolver?.dispose();resolver=null; window.__nyalumePetBoot=null;
  $('pet-empty').hidden=false;
}
async function openModel(value) {
  dispose(); selected=value; options();
  if(!value) return;
  const [id,pmx]=JSON.parse(value); current=records.find(r=>r.id===id);
  if(!current) return;
  const record=current, profileID='profile:'+value;
  let savedProfile=records.find(r=>r.id===profileID);
  if(!savedProfile){savedProfile={id:profileID,kind:'profile',owner:id,value:{zoom:1,view_yaw:0}};records.push(savedProfile);}
  const profile=savedProfile.value;
  $('physics').checked=!!profile.physics;
  const serial=bootSerial;
  const warnings=[];
  resolver=makeResolver(record.files,text=>{warnings.push(text);status(text);});
  window.__nyalumePetBoot={profile, pmx:resolver.localURL(pmx), resolve:resolver.resolve,
    api:{
      poll_action:async()=>actions.shift()||null,
      view_info:async(zoom,yaw)=>{
        if(serial!==bootSerial)return {ok:false};
        Object.assign(profile,{zoom,view_yaw:yaw,physics:$('physics').checked});
        return {ok:await persist(savedProfile)};
      },
      motion_loaded: text=> {if(!text.startsWith('[') && text!=='ok')status('动作提示：'+text);},
      motion_ended:()=>status('动作结束啦，回到待机喵。'),
      ready:()=>{if(serial!==bootSerial)return;loaded=true;status(volatileAssets.has(id)?'模型本次可以使用，但未保存到浏览器喵，请腾出空间后重新导入。':warnings.length?warnings.join('；'):'见面啦喵～点击互动，拖动旋转，滚轮缩放。');},
      failure:text=>status(text),
    }};
  $('pet-empty').hidden=true;
  frame=document.createElement('iframe');frame.title='本地 3D 角色';
  frame.src='/pet-viewer?web=1&physics='+($('physics').checked?'1':'0');
  $('stage').append(frame);
  status('正在加载模型与贴图喵…');
  await persist({id:'last',kind:'preference',value});
}
function bind(id,fn){$(id).addEventListener('click',()=>Promise.resolve().then(fn).catch(e=>status(e.message)));}
$('models').addEventListener('change',()=>openModel($('models').value).catch(e=>status(e.message)));
$('model-files').addEventListener('change',async e=>{
  const files=Array.from(e.target.files);e.target.value='';if(!files.length)return;
  try{
    status('检查模型文件中…');await validateFiles(files);
    const entries=files.map(f=>({path:f.webkitRelativePath||f.name,blob:f}));
    const record={id:crypto.randomUUID(),kind:'model',files:entries,pmxs:entries.filter(f=>/\.pmx$/i.test(f.path)).map(f=>f.path),profiles:{},size:files.reduce((n,f)=>n+f.size,0)};
    let saved=true;try{await storage('put',record);}catch{saved=false;volatileAssets.add(record.id);}
    records.push(record);await openModel(JSON.stringify([record.id,record.pmxs[0]]));
    if(!saved)status('模型已导入，本次可用，但浏览器保存失败喵。请腾出空间再导入。');
    navigator.storage?.persist?.().catch(()=>{});
  }catch(err){status(err.message);}
});
$('motion-files').addEventListener('change',async e=>{
  const files=Array.from(e.target.files);e.target.value='';if(!files.length)return;
  try{
    await validateFiles(files,true);let failures=0,last='';
    for(const file of files){const r={id:crypto.randomUUID(),kind:'motion',name:file.name,blob:file,size:file.size};try{await storage('put',r);}catch{failures++;}records.push(r);last=r.id;}
    options();$('motions').value=last;status(failures?'动作本次可用，但部分未能保存喵。':'动作导入好了，选一支试试看喵～');
  }catch(err){status(err.message);}
});
bind('play-motion',async()=>{
  if(!loaded)throw Error('先等模型准备好喵。');
  const r=records.find(r=>r.id===$('motions').value);if(!r)throw Error('先选一个动作喵。');
  status('正在加载动作…');
  $('play-motion').disabled=true;
  try{const ok=await frame.contentWindow.__setMotion(resolver.addBlob(r.blob));status(ok?'开始跳舞喵～若姿势不自然，可换动作或关闭物理效果。':'动作没能播放喵，请检查模型骨骼与动作是否兼容。');}
  finally{$('play-motion').disabled=false;}
});
bind('idle',()=>petAction({kind:'motion',url:''}));
bind('pat',()=>petAction({kind:'react',region:'head'}));
bind('zoom-in',()=>petAction({kind:'zoom',steps:1}));
bind('zoom-out',()=>petAction({kind:'zoom',steps:-1}));
bind('reset-view',()=>frame?.contentWindow.__resetView?.());
$('physics').addEventListener('change',async()=>{
  if(!selected)return;const profile=records.find(r=>r.id==='profile:'+selected);
  profile.value.physics=$('physics').checked;
  await persist(profile);await openModel(selected);
});
bind('delete-model',async()=>{
  if(!selected)return;if(!confirm('删除这个模型文件夹和其中的全部 PMX？原始文件不会改变喵。'))return;
  const [id]=JSON.parse(selected);await saving;await openModel('');await storage('delete',id);
  for(const r of records.filter(r=>r.owner===id))await storage('delete',r.id);
  records=records.filter(r=>r.id!==id&&r.owner!==id);await storage('delete','last');options();status('模型已经从浏览器移除喵。');
});
bind('delete-motion',async()=>{
  const id=$('motions').value;if(!id)return;petAction({kind:'motion',url:''});await storage('delete',id);records=records.filter(r=>r.id!==id);options();status('动作已移除喵。');
});
bind('clear-assets',async()=>{
  if(!confirm('清空此浏览器的全部模型、动作与视角设置？聊天记录不受影响喵。'))return;
  dispose();await saving;await storage('clear');records=[];selected='';options();status('资源清空了喵。');
});
const smallScreen=matchMedia('(max-width:820px), (pointer:coarse) and (max-width:1100px)');
if(window.visualViewport){
  const resize=()=>document.body.style.setProperty('--viewport-height',window.visualViewport.height+'px');
  window.visualViewport.addEventListener('resize',resize);resize();
}
function hidePet(hidden){
  document.body.classList.toggle('pet-hidden',hidden);document.body.classList.toggle('pet-visible',!hidden);$('toggle-pet').textContent=hidden?'展开角色':'收起角色';$('toggle-pet').setAttribute('aria-expanded',String(!hidden));
  const overlay=!hidden&&smallScreen.matches;
  $('sidebar').inert=overlay;document.querySelector('main').inert=overlay;
  if(overlay)$('close-pet').focus();
  frame?.contentWindow.__setPaused?.(hidden);
}
bind('toggle-pet',()=>hidePet(!document.body.classList.contains('pet-hidden')));
bind('close-pet',()=>{hidePet(true);$('toggle-pet').focus();});
hidePet(smallScreen.matches);
smallScreen.addEventListener('change',e=>hidePet(e.matches));
document.addEventListener('visibilitychange',()=>frame?.contentWindow.__setPaused?.(document.hidden||document.body.classList.contains('pet-hidden')));
window.addEventListener('pagehide',dispose);
async function restoreAssets(){
  try{records=await storage('getAll');options();const last=records.find(r=>r.id==='last');if(last?.value&&records.some(r=>r.id===JSON.parse(last.value)[0]))await openModel(last.value);}
  catch(e){status('浏览器暂时不能恢复资源喵：'+e.message+'。你仍可以重新导入。');}
}
restoreAssets();
