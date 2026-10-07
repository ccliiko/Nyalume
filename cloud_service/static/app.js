import {petAction} from './pet.js';
import './wallpaper.js';
const $=id=>document.getElementById(id);
let token=sessionStorage.getItem('nyalume-token')||localStorage.getItem('nyalume-token')||'', user=null, sid='', authMode='login', controller=null;
let before=null, mode='chat', story={}, failedDraft=null, persona='';
const choiceBox=$('story-choices');
const defaultConfig={base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'qwen3.7-plus',api_key:''};
const qwenModels=['qwen3.7-plus','qwen-plus','qwen-flash','qwen-max'];
let config={...defaultConfig};
const keyName=()=> 'nyalume-key:'+user.id;
function saveLogin(remember){sessionStorage.setItem('nyalume-token',token);if(remember)localStorage.setItem('nyalume-token',token);else localStorage.removeItem('nyalume-token');}
function feedback(id,error){$(id).textContent=error instanceof Error?error.message:error;}
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),...options.headers}});
  if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}if(response.status===401&&!path.includes('/auth/')){await leave(false);}
    throw Error(typeof detail==='string'?detail:'暂时没连上小窝喵，请稍后重试。');}
  return response.status===204?null:response.json();
}
function on(id,fn,event='click'){ $(id).addEventListener(event,e=>{Promise.resolve().then(()=>fn(e)).catch(error=>feedback('chat-status',error));}); }
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
function message(role,text){const article=document.createElement('article');article.className='message '+role;
  const who=document.createElement('span');who.className='who';who.textContent=role==='user'?'你':'Nyalume';
  const content=document.createElement('div');content.className='content';content.textContent=text;article.append(who,content);$('messages').append(article);$('messages').scrollTop=$('messages').scrollHeight;return content;}
function busy(value){$('send').disabled=value;$('new-chat').disabled=value||!user;$('stop').hidden=!value;$('message').disabled=value;
  $('sessions').querySelectorAll('button').forEach(b=>b.disabled=value);$('account-open').disabled=value;$('adventure-open').disabled=value;$('persona-open').disabled=value;$('older-messages').disabled=value;choiceBox.querySelectorAll('button').forEach(b=>b.disabled=value);}
async function listSessions(){
  const rows=await api('/api/web/sessions');$('sessions').replaceChildren();
  for(const row of rows){const item=document.createElement('div');item.className='session-row';const open=document.createElement('button');open.textContent=row.title;open.title=row.title;
    if(row.id===sid)open.className='active';open.onclick=()=>selectSession(row.id).catch(e=>feedback('chat-status',e));
    const remove=document.createElement('button');remove.textContent='×';remove.setAttribute('aria-label','删除聊天 '+row.title);
    remove.onclick=async()=>{if(!confirm('把这段聊天从小窝中移除？'))return;try{await api('/api/web/sessions/'+row.id,{method:'DELETE'});if(sid===row.id){sid='';mode='chat';story={};before=null;renderStory();$('older-messages').hidden=true;$('messages').replaceChildren();$('chat-title').textContent='今天想聊些什么？';}await listSessions();}catch(e){feedback('chat-status',e);}};
    item.append(open,remove);$('sessions').append(item);if(row.id===sid)$('chat-title').textContent=row.title;}
  return rows;
}
function renderStory(){
  $('persona-open').hidden=mode!=='fantasy';
  $('story-panel').hidden=mode!=='fantasy';choiceBox.replaceChildren();
  $('story-heading').textContent='奇幻手记 · 第 '+(story.turn||story.chapter||0)+' 次互动';
  $('story-location').textContent='所在之处：'+(story.location||'等待启程');
  $('story-inventory').textContent='背包：'+(story.inventory?.join('、')||'还没有物品');
  $('story-summary').textContent=story.summary||'发送第一条消息，一起走进故事吧喵。';
  $('message').placeholder=mode==='fantasy'?'选择一个方向，或自由输入行动、对话、追问…':'想说什么都可以喵…';
  choiceBox.remove();
  const reply=$('messages').querySelector('.message.assistant:last-child');
  if(mode==='fantasy'&&reply&&story.choices?.length){
    const hint=document.createElement('small');hint.textContent='点选一个回答直接发送，或在下方自由输入。';choiceBox.append(hint);
    for(const choice of story.choices){const button=document.createElement('button');button.type='button';
      const text=typeof choice==='string'?choice:choice.text;
      const title=document.createElement('strong');title.textContent=typeof choice==='string'?choice:choice.label;
      const answer=document.createElement('span');answer.textContent=text;button.append(title,answer);
      button.onclick=()=>{if(controller)return;$('message').value=text;$('chat-form').requestSubmit();};choiceBox.append(button);}
    reply.append(choiceBox);
  }
}
async function selectSession(id){if(controller)return;clearFailedDraft();$('message').value='';const data=await api('/api/web/sessions/'+id);sid=id;before=data.next_before;mode=data.mode;story=data.story||{};persona=data.persona||'';
  $('messages').replaceChildren();for(const row of data.messages)message(row.role,row.content);$('older-messages').hidden=!before;renderStory();await listSessions();}
async function newSession(options={}){clearFailedDraft();$('message').value='';const row=await api('/api/web/sessions',{method:'POST',body:JSON.stringify(options)});sid=row.id;mode=options.mode||'chat';story={};persona=options.persona||'';before=null;
  $('messages').replaceChildren();$('older-messages').hidden=true;renderStory();await listSessions();}
on('older-messages',async()=>{if(!before||controller)return;const data=await api('/api/web/sessions/'+sid+'?before='+before);
  const box=$('messages'),old=[...box.childNodes],height=box.scrollHeight;box.replaceChildren();for(const row of data.messages)message(row.role,row.content);box.append(...old);
  box.scrollTop=box.scrollHeight-height;before=data.next_before;$('older-messages').hidden=!before;});
function profile(){const signed=!!user;$('auth-fields').hidden=signed;$('auth-fields').disabled=signed;$('profile-fields').hidden=!signed;$('auth-submit').hidden=signed;
  if(signed){$('profile-remember-login').checked=localStorage.getItem('nyalume-token')===token;$('profile-email').textContent=user.email+(user.email_verified?' · 已验证':' · 待验证');$('profile-name').value=user.username;$('verify-section').hidden=user.email_verified;}}
async function enter(){
  user=await api('/api/v1/me');$('account-open').textContent=user.username;$('logout').hidden=false;profile();busy(false);
  if(!user.email_verified){feedback('chat-status','先在账号设置验证邮箱，再来聊天喵～');return;}
  config={...defaultConfig};let migrated=false;
  try{const saved=JSON.parse(localStorage.getItem(keyName())||'null');
    if(saved&&saved.base_url===defaultConfig.base_url&&qwenModels.includes(saved.model))config=saved;
    else if(saved){localStorage.removeItem(keyName());migrated=true;}
  }catch{}
  const rows=await listSessions();if(rows.length)await selectSession(rows[0].id);
  feedback('chat-status',migrated?'小窝已改用千问，请在「Key 与陪伴记忆」中填写百炼 API Key 喵。':config.api_key?'欢迎回来喵。':'先在「Key 与陪伴记忆」中填入你自己的百炼 API Key 吧喵。');
}
function setAuthMode(mode){authMode=mode;$('remember-login-label').hidden=mode==='reset';$('email-label').hidden=mode==='login';$('code-label').hidden=mode==='login';$('confirm-password-label').hidden=mode==='login';$('confirm-password').required=mode!=='login';$('account-label').hidden=mode==='reset';
  $('account-name').required=mode!=='reset';$('account-name').maxLength=mode==='register'?24:254;$('account-email').required=mode!=='login';$('reset-code').required=mode!=='login';
  $('account-password').autocomplete=mode==='login'?'current-password':'new-password';$('auth-submit').textContent=({login:'登录',register:'注册并进入小窝',reset:'重设密码'})[mode];
  ['login','register','reset'].forEach(m=>$(m+'-mode').classList.toggle('selected',m===mode));feedback('auth-status','');}
on('account-open',()=>{profile();$('account-dialog').showModal();});
['login','register','reset'].forEach(mode=>on(mode+'-mode',()=>setAuthMode(mode)));
on('request-code',async()=>{try{const r=await api(authMode==='register'?'/api/v1/auth/register/request':'/api/v1/auth/password-reset/request',{method:'POST',body:JSON.stringify({email:$('account-email').value})});feedback('auth-status',r.message);cooldown('request-code',r.retry_after||60);}catch(e){feedback('auth-status',e);}});
$('account-form').addEventListener('submit',async e=>{
  e.preventDefault();$('auth-submit').disabled=true;
  try{
    if(authMode!=='login'&&$('account-password').value!==$('confirm-password').value)throw Error('两次密码没有对上喵，再看看？');
    if(authMode==='reset'){
      await api('/api/v1/auth/password-reset/confirm',{method:'POST',body:JSON.stringify({email:$('account-email').value,code:$('reset-code').value,new_password:$('account-password').value})});
      setAuthMode('login');feedback('auth-status','密码换好了，重新登录吧喵。');return;
    }
    const body={password:$('account-password').value,device_name:'Nyalume web'};
    if(authMode==='register'){body.username=$('account-name').value;body.email=$('account-email').value;body.code=$('reset-code').value;}else body.account=$('account-name').value;
    if(user) await leave();
    const r=await api('/api/v1/auth/'+authMode,{method:'POST',body:JSON.stringify(body)});
    token=r.access_token;saveLogin($('remember-login').checked);await enter();$('account-password').value='';$('confirm-password').value='';$('reset-code').value='';$('account-dialog').close();
  }catch(error){feedback('auth-status',error);}finally{$('auth-submit').disabled=false;}
});
async function leave(revoke=true){
  clearFailedDraft();$('message').value='';
  if(user)try{localStorage.removeItem(keyName());}catch{}
  if(revoke)try{await api('/api/v1/auth/logout',{method:'DELETE'});}catch{}token='';user=null;sid='';config.api_key='';
  $('api-key').value='';$('memory').value='';$('remember-key').checked=false;sessionStorage.removeItem('nyalume-token');localStorage.removeItem('nyalume-token');$('remember-login').checked=false;$('profile-remember-login').checked=false;
  $('messages').replaceChildren();$('sessions').replaceChildren();$('account-open').textContent='登录 / 注册';$('logout').hidden=true;$('new-chat').disabled=true;$('chat-title').textContent='今天想聊些什么？';mode='chat';story={};before=null;renderStory();profile();
}
on('logout',async()=>{await leave();$('account-dialog').close();feedback('chat-status','已退出小窝，浏览器记住的 Key 也清除了喵。');});
on('settings-open',async()=>{
  if(!user){$('account-dialog').showModal();return;}
  $('base-url').value=config.base_url;$('llm-model').value=config.model;$('api-key').value=config.api_key;
  $('remember-key').checked=!!localStorage.getItem(keyName());$('settings-dialog').showModal();
  try{$('memory').value=(await api('/api/web/memory')).content;feedback('settings-status','');}catch(e){feedback('settings-status',e);}
});
$('settings-form').addEventListener('submit',async e=>{
  e.preventDefault();if(!user)return;
  try{
    await api('/api/web/memory',{method:'PUT',body:JSON.stringify({content:$('memory').value})});
    config={base_url:$('base-url').value.trim().replace(/\/$/,''),model:$('llm-model').value.trim(),api_key:$('api-key').value.trim()};
    if($('remember-key').checked)localStorage.setItem(keyName(),JSON.stringify(config));else localStorage.removeItem(keyName());
    $('settings-dialog').close();feedback('chat-status','准备好了，想聊什么喵？');
  }catch(e){feedback('settings-status',e);}
});
function clearFailedDraft(){failedDraft=null;$('restore-draft').hidden=true;}
on('restore-draft',()=>{if(failedDraft?.sid===sid){$('message').value=failedDraft.text;clearFailedDraft();$('message').focus();}});
on('new-chat',()=>newSession());on('stop',()=>controller?.abort());
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('chat-form').requestSubmit();}});
$('chat-form').addEventListener('submit',async e=>{
  e.preventDefault();if(controller)return;
  const text=$('message').value.trim();if(!text)return;
  if(!user){$('account-dialog').showModal();return;}
  if(!config.api_key){$('settings-open').click();return;}
  controller=new AbortController();busy(true);petAction({kind:'state',name:'working'});
  let output,userMessage,done=false,actualModel='';
  clearFailedDraft();choiceBox.replaceChildren();choiceBox.remove();
  feedback('chat-status',mode==='fantasy'?'正在整理这一段情节和回应选项喵…':'正在听你说喵…');
  try{
    if(!sid)await newSession();busy(true);
    userMessage=message('user',text);output=message('assistant','');$('message').value='';
    const response=await fetch('/api/web/chat',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({...config,session_id:sid,message:text}),signal:controller.signal});
    if(!response.ok){const error=await response.json();if(response.status===401)await leave(false);throw Error(typeof error.detail==='string'?error.detail:'连接失败喵。');}
    const reader=response.body.getReader(), decoder=new TextDecoder();let pending='';
    while(true){const result=await reader.read();if(result.done)break;pending+=decoder.decode(result.value,{stream:true});
      let boundary;
      while((boundary=pending.indexOf('\n\n'))!==-1){const line=pending.slice(0,boundary);pending=pending.slice(boundary+2);if(!line.startsWith('data:'))continue;const event=JSON.parse(line.slice(5));
        if(event.type==='status')feedback('chat-status',event.message);
        if(event.type==='error')throw Error(event.message);
        if(event.type==='delta'){output.textContent+=event.text;$('messages').scrollTop=$('messages').scrollHeight;}
        if(event.type==='done'){done=true;if(event.story){story=event.story;renderStory();}if(event.actual_model)actualModel=event.actual_model;}
      }
    }
    if(!done)throw Error('连接中断了喵，这轮对话尚未确认保存。');
    petAction({kind:'state',name:'done'});petAction({kind:'say',text:output.textContent.slice(0,40)});
    feedback('chat-status',(mode==='fantasy'?'这一段已记下，接下来由你决定喵。':'已记下这段聊天喵。')+(actualModel?' · 实际模型：'+actualModel:''));await listSessions();
  }catch(error){controller?.abort();if(output){output.parentElement.classList.add('error');if(!output.textContent)output.textContent=error.name==='AbortError'?'这一轮已停止，尚未确认保存。':error.message;}
    if(!done){failedDraft={sid,text};$('restore-draft').hidden=false;if(userMessage){userMessage.parentElement.classList.add('error');userMessage.parentElement.querySelector('.who').textContent='你 · 本轮未确认保存';}}
    feedback('chat-status',error.name==='AbortError'?'已停止喵。刷新聊天可确认服务器是否刚好保存完成。':error);
  }finally{controller=null;busy(false);petAction({kind:'state',name:'idle'});$('message').focus();}
});
function cooldown(id,seconds){const button=$(id),label=button.textContent;button.disabled=true;let left=seconds;
  const timer=setInterval(()=>{left--;button.textContent=left>0?left+' 秒后再发送':label;if(left<=0){clearInterval(timer);button.disabled=false;}},1000);}
function accountAction(id,fn){on(id,async()=>{try{await fn();}catch(e){feedback('auth-status',e);}});}
accountAction('send-verify',async()=>{const r=await api('/api/v1/auth/verify/request',{method:'POST'});feedback('auth-status',r.message);cooldown('send-verify',r.retry_after||60);});
accountAction('confirm-verify',async()=>{await api('/api/v1/auth/verify/confirm',{method:'POST',body:JSON.stringify({code:$('verify-code').value})});await enter();feedback('auth-status','邮箱验证好了，欢迎回来喵～');});
accountAction('rename-user',async()=>{await api('/api/v1/me',{method:'PATCH',body:JSON.stringify({username:$('profile-name').value})});user=await api('/api/v1/me');profile();$('account-open').textContent=user.username;feedback('auth-status','新名字记住啦喵～');});
accountAction('change-password',async()=>{if($('new-password').value!==$('new-password-confirm').value)throw Error('两次新密码没有对上喵。');
  await api('/api/v1/me/password',{method:'PUT',body:JSON.stringify({current_password:$('old-password').value,new_password:$('new-password').value})});
  for(const id of ['old-password','new-password','new-password-confirm'])$(id).value='';await leave();setAuthMode('login');feedback('auth-status','密码换好啦，请重新登录喵～');});
on('copy-qq',async()=>{await navigator.clipboard.writeText('939851001');feedback('chat-status','群号复制好了，欢迎来氵群喵～');});
function requireAccount(){if(!user||!user.email_verified){$('account-open').click();return false;}return true;}
on('adventure-open',()=>{if(requireAccount())$('adventure-dialog').showModal();});
$('adventure-form').addEventListener('submit',async e=>{e.preventDefault();try{await newSession({mode:'fantasy',premise:$('adventure-premise').value.trim(),persona:$('adventure-persona').value.trim()});$('adventure-dialog').close();
  $('message').value='请带我走进故事的开场，交代眼前的处境，等我决定第一步。';$('message').focus();feedback('chat-status','手记准备好了喵。可以修改开场要求，再点击发送。');}catch(e){feedback('chat-status',e);}});
on('profile-remember-login',()=>{saveLogin($('profile-remember-login').checked);feedback('auth-status',$('profile-remember-login').checked?'记住啦，下次打开会自动回到小窝喵～':'已取消自动登录，这个标签页仍可继续使用喵。');},'change');
on('persona-open',()=>{if(controller||mode!=='fantasy')return;$('persona-text').value=persona;feedback('persona-status','');$('persona-dialog').showModal();});
$('persona-form').addEventListener('submit',async e=>{e.preventDefault();if(controller||mode!=='fantasy')return;const button=e.submitter;button.disabled=true;
  try{const data=await api('/api/web/sessions/'+sid+'/persona',{method:'PATCH',body:JSON.stringify({persona:$('persona-text').value})});persona=data.persona;$('persona-dialog').close();feedback('chat-status',persona?'人设记下啦，下一轮就按这个性格一起冒险喵～':'已恢复默认 Nyalume 人设喵～');}
  catch(error){feedback('persona-status',error);}finally{button.disabled=false;}});
let cardBefore=null;
function cardView(card,compact=false){const box=document.createElement('article');box.className=compact?'collected-card':'daily-card';
  const cardName=window.NyalumeCardCatalog?.[card.profile]?.title||card.name;
  const image=document.createElement('img');image.src=card.image;image.alt=cardName;image.loading='lazy';
  const title=document.createElement('strong');title.textContent=card.day+' · '+cardName;
  const text=document.createElement('p');text.textContent=card.blessing;
  const like=document.createElement('button');like.type='button';const label=()=>{like.textContent=card.liked?'♥ 已珍藏':'♡ 珍藏';like.setAttribute('aria-pressed',String(card.liked));};label();
  like.onclick=async()=>{like.disabled=true;try{await api('/api/web/daily/'+card.day,{method:'PATCH',body:JSON.stringify({liked:!card.liked})});card.liked=!card.liked;label();await collection();todayCard(await api('/api/web/daily'));}catch(e){feedback('daily-status',e);}finally{like.disabled=false;}};
  box.append(image,title,text,like);return box;}
async function collection(append=false){const data=await api('/api/web/daily/collection'+(append&&cardBefore?'?before='+cardBefore:''));if(!append)$('daily-collection').replaceChildren();
  for(const card of data.cards)$('daily-collection').append(cardView(card,true));cardBefore=data.next_before;$('more-cards').hidden=!cardBefore;}
function todayCard(card){$('daily-card').replaceChildren();$('draw-daily').hidden=!!card.profile;if(card.profile)$('daily-card').append(cardView(card));}
on('daily-open',async()=>{if(!requireAccount())return;$('daily-dialog').showModal();try{todayCard(await api('/api/web/daily'));await collection();feedback('daily-status','');}catch(e){feedback('daily-status',e);}});
on('draw-daily',async()=>{$('draw-daily').disabled=true;try{todayCard(await api('/api/web/daily',{method:'POST'}));await collection();feedback('daily-status','今天的祝福收好喵～');}catch(e){feedback('daily-status',e);}finally{$('draw-daily').disabled=false;}});
on('more-cards',()=>collection(true));
async function initialize(){
try{const caps=await api('/api/web/capabilities');$('version').textContent=caps.version;$('reset-mode').hidden=!caps.password_reset;$('message').maxLength=caps.limits.message_chars;$('memory').maxLength=caps.limits.memory_chars;
  if(token)await enter();
}catch(e){feedback('chat-status',e);}
}
initialize();
