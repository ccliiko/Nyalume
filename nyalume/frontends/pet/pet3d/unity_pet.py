"""Local Eku/lilToon desktop trial, using the existing Windows input and presenter."""
import base64
import io
import json
import math
import mmap
import os
from pathlib import Path
import struct
import subprocess
import threading
import time
import uuid

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from . import pet3d_win as pet
from .unity_magnifier import Magnifier
from . import wardrobe
from . import presence as presence_config

OUTPUT=Path(pet._app_root())/'outputs/eku-natural-20261013'
BUNDLED_PLAYER=Path(pet._app_root())/'unity/EkuPlayer.exe'
PLAYER=OUTPUT/'player/EkuComparison.exe'
MATERIAL_PLAYER=Path(pet._app_root())/'outputs/eku-unity-stockings-20261013/player/EkuComparison.exe'
HAIR_PLAYER=Path(pet._app_root())/'outputs/eku-unity-hair-20261013/player/EkuComparison.exe'
COMBINED_PLAYER=MATERIAL_PLAYER.parent.parent/'combined/player/EkuComparison.exe'
WARDROBE_PLAYER=Path(pet._app_root())/'outputs/eku-wardrobe/player/EkuComparison.exe'
JK_PLAYER=Path(pet._app_root())/'outputs/eku-jk/player/EkuComparison.exe'
WARDROBE_BUNDLED_PLAYER=Path(pet._app_root())/'build/eku-v052-release/player/EkuPlayer.exe'
VOICE_PLAYER=Path(pet._app_root())/'build/eku-voice-release/player/EkuPlayer.exe'
POSE=OUTPUT/'preferred/comparison.json'
if BUNDLED_PLAYER.is_file():
    POSE=BUNDLED_PLAYER.parent/'pose.json'
    OUTPUT=Path(pet._STATE_DIR)/'unity'
CONTROL_BYTES=131072
ANIMATIONS={'greeting':'打招呼','fist':'握拳加油','thumbsup':'点赞','peace':'比耶',
            'point':'指一指','rocknroll':'摇滚手势','gun':'手指造型',
            'ears':'动动耳朵','stand':'恢复站立','crouch':'蹲一会儿','prone':'趴一会儿','rest':'休息','wake':'醒来'}
FACES={'neutral','happy','calm','angry','sad','surprise','sleepy','blink','shy','love',
       'relax','angry_2','angry_3','blank','chill','confused','confused_2','cry','cry_2',
       'curious','depressed','excited','joy','puzzled','satisfied','shocked','smile','smile_2',
       'wink','wink_2','wink_3'}


def supports_model(model):
    if model == pet.BUNDLED_EKU:return True
    target=Path(pet._app_root(),'models/Eku/Eku.vrm').resolve()
    return Path(model).resolve() in (target,target.parent)


class UnityBridge:
    def __init__(self, pose=POSE, player=PLAYER, size=None, wardrobe_state=None, output=None):
        output=Path(output) if output is not None else OUTPUT
        output.mkdir(parents=True,exist_ok=True)
        self.pose=json.loads(Path(pose).read_text(encoding='utf8'))
        self.width,self.height=size or (self.pose['width'],self.pose['heightPx'])
        self.max_width,self.max_height=(round(v*2.5) for v in (self.width,self.height))
        tag='NyalumeEku-'+uuid.uuid4().hex
        self.frames=mmap.mmap(-1,64+self.max_width*self.max_height*4,tagname=tag)
        self.control=mmap.mmap(-1,CONTROL_BYTES,tagname=tag+'-control')
        self.magnifier=mmap.mmap(-1,64+256*256*4,tagname=tag+'-magnifier')
        self.magnifier_sequence=0
        self.sequence=self.command=self.feedback=0
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        self.player=subprocess.Popen([str(player),'-batchmode','-force-d3d11','--bridge',tag,
            '--control',tag+'-control','--pose',str(Path(pose).resolve()),'--output',str(output/'desktop'),
            '--magnifier',tag+'-magnifier',
            *(['--full-body'] if os.environ.get('NYALUME_EKU_FULL_BODY') == '1' else []),
            *(['--cat-behavior'] if os.environ.get('NYALUME_EKU_CAT_BEHAVIOR') == '1' else []),
            *(['--body-performance'] if os.environ.get('NYALUME_EKU_BODY_PERFORMANCE') == '1' else []),
            *(['--body-task-probes'] if os.environ.get('NYALUME_EKU_BODY_TASK_PROBES') == '1' else []),
            '--width',str(self.width),'--height',str(self.height),
            '--max-width',str(self.max_width),'--max-height',str(self.max_height),
            *(['--wardrobe-state',json.dumps(wardrobe_state,separators=(',',':'))] if wardrobe_state is not None else []),
            '-logFile',str(output/'unity-desktop.log')],startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)

    @property
    def ready_for_command(self):
        return struct.unpack_from('<i',self.control,12)[0]==self.command

    def send(self, action):
        if not self.ready_for_command:return False
        action=dict(action)
        if isinstance(action.get('value'),bool):action['enabled']=action.pop('value')
        if action.get('id') is None:action['id']=0
        data=json.dumps(action,ensure_ascii=False,separators=(',',':')).encode('utf8')
        if len(data)>32768:raise ValueError('Unity action exceeds the control slot')
        self.control[64:64+len(data)]=data
        struct.pack_into('<i',self.control,4,len(data))
        self.command+=1;struct.pack_into('<i',self.control,0,self.command)
        return True

    def receive_feedback(self):
        seq=struct.unpack_from('<q',self.control,24)[0]
        if seq<=self.feedback or seq%2:return None
        length=struct.unpack_from('<i',self.control,20)[0]
        if not 0<length<=32768:raise ValueError('Invalid Unity feedback size')
        data=bytes(self.control[65536:65536+length])
        if struct.unpack_from('<q',self.control,24)[0]!=seq:return None
        self.feedback=seq;struct.pack_into('<q',self.control,32,seq)
        return json.loads(data)

    def frame(self):
        seq=struct.unpack_from('<q',self.frames,16)[0]
        if seq<=self.sequence or seq%2:return None
        width,height=struct.unpack_from('<ii',self.frames,4)
        if not (0<width<=self.max_width and 0<height<=self.max_height):return None
        data=bytes(self.frames[64:64+width*height*4])
        if struct.unpack_from('<q',self.frames,16)[0]!=seq:return None
        self.sequence=seq
        self.width,self.height=width,height
        return Image.frombytes('RGBA',(self.width,self.height),data,'raw','BGRA',0,-1)

    def magnifier_frame(self):
        seq=struct.unpack_from('<q',self.magnifier,16)[0]
        if seq<=self.magnifier_sequence or seq%2:return None
        data=bytes(self.magnifier[64:64+256*256*4])
        region=struct.unpack_from('<ffff',self.magnifier,32)
        if struct.unpack_from('<q',self.magnifier,16)[0]!=seq:return None
        self.magnifier_sequence=seq
        return Image.frombytes('RGBA',(256,256),data,'raw','BGRA',0,-1),region

    def close(self):
        if self.player.poll() is None:
            deadline=time.monotonic()+.5
            while not self.ready_for_command and time.monotonic()<deadline:time.sleep(.005)
            self.send({'kind':'quit'})
            try:self.player.wait(timeout=3)
            except subprocess.TimeoutExpired:self.player.terminate();self.player.wait(timeout=10)
        self.frames.close();self.control.close();self.magnifier.close()


class UnityApi(pet._NativeApi):
    def __init__(self,*args,presence=None,**kwargs):
        self._presence=presence
        super().__init__(*args,**kwargs)
        self._animations=presence_config.ANIMATIONS if presence else ANIMATIONS
        self._ambience_modes=presence.modes if presence else ()
        saved_mode=pet._load_settings().get('eku_ambience')
        self._ambience_pending=(saved_mode if saved_mode in self._ambience_modes else
                                ('breeze' if 'breeze' in self._ambience_modes else next(iter(self._ambience_modes),None)))
        if presence:
            self._x,self._y,self._positioned=(540 if presence.modes else 60),120,True
            self._proactive=False
        self._material_styles=False
        self._auto_animation=bool(pet._load_settings().get('eku_auto_animation',True))
        self._animation_state={'posture':'stand','afk':'','automatic':self._auto_animation}
        self._animation_received_at=None
        self._context_at=0
        self._context_gate=None
        self._last_contact_at=time.monotonic()
        self._greeting_pending=False
        self._emotion_seq=None
        self._chat_face=None
        self._chat_face_until=0.0
        self._emotion_supported=False
        self._emotion_revision=0
        self._emotion_state=None
        self._wardrobe_supported=False
        self._wardrobe_outfits=['casual','uniform']
        self._wardrobe_state=wardrobe.restore(pet._load_settings().get('eku_wardrobe'))
        self._wardrobe_desired=wardrobe.restore(self._wardrobe_state)
        self._wardrobe_pending=None
        self._wardrobe_revision=0
        self._wardrobe_applied=-1
        saved_voice=pet._load_settings().get('eku_voice',{})
        if not isinstance(saved_voice,dict):saved_voice={}
        volume=saved_voice.get('volume',.6)
        if isinstance(volume,bool) or not isinstance(volume,(int,float)) or not math.isfinite(volume) or not 0<=volume<=1:volume=.6
        self._voice_config={'enabled':saved_voice.get('enabled',True) is not False,
                            'proactive':saved_voice.get('proactive',True) is not False,'volume':volume}
        self._voice_state=None
        self._voice_pending=None
        self._voice_start_pending=False
        self._full_body_supported=False
        self._mouse_target_supported=False
        self._mouse_due=0.0
        self._mouse_sent=0
        self._mouse_geometry=0
        self._mouse_forced_look=None
        self._mouse_forced_until=0.0
        self._mouse_debug=None
        self._attention_debug=None
        self._mouse_display=0
        self._mouse_bounds=None

    def _sample_pointer(self,x,y,origin,size,scale,active=False,left_down=False,mode=''):
        pointer=super()._sample_pointer(x,y,origin,size,scale,active,left_down,mode)
        if mode=='touch':
            real=self._last_sys_pos if self._locked and self._last_sys_pos is not None else (x,y)
            wire=self._mouse_target.sample(real,(x,y),pointer['at'],window=(*origin,*size),
                scale=scale,display=self._mouse_display,source='RawIntegrated' if self._locked else 'System',
                pressed=left_down,allow_attention=False,interaction_mode=mode,bounds=self._mouse_bounds or self._bbox_all,
                geometry_revision=self._mouse_geometry)
            pointer.update(sampleSeq=wire['sequence'],at=wire['timestamp'],velocityDip=wire['velocity'],
                           speedDip=wire['speed'],motionSpeed=wire['motionSpeed'],mouse=wire)
        return pointer

    def _interaction_event(self,phase,left):
        event=super()._interaction_event(phase,left)
        if phase=='begin':
            left['pose_control']=self._full_body_supported and bool(pet._user32.GetAsyncKeyState(0x11)&0x8000)
            left['pose_depth']=left['pose_control'] and bool(pet._user32.GetAsyncKeyState(0x10)&0x8000)
        if left.get('pose_control'):
            event.update(kind='pose',depth=left.get('pose_depth',False))
            if phase in ('end','cancel'):
                self._interaction_records.pop(left['id'],None)
        wire=event['pointer'].pop('mouse',None)
        if wire is not None:
            wire['interactionId']=left['id']
            event['mouse']=wire
            self._mouse_due=0.0
        return event

    def sample_mouse_target(self, now=None):
        now=time.monotonic() if now is None else now
        if self._mouse_sample is not None and now<self._mouse_due:return self._mouse_sample
        self._mouse_due=now+.02
        valid=True
        try:screen=pet._cursor_pos()
        except OSError:
            valid=False
            screen=tuple(self._mouse_sample['screenPosition'][k] for k in ('x','y')) if self._mouse_sample else (0,0)
        effective=self._eff_cursor if self._locked and self._eff_cursor is not None else screen
        display,scale=pet._pointer_display(*effective)
        self._mouse_display=display
        with self._interaction_lock:
            left=self._left
            if left:scale=left['scale']
            window=(self._x,self._y,self._w,self._h)
            interaction_id=left['id'] if left else 0
            mode=left['mode'] if left else ''
        blocked=bool(self._effective_quiet or self._locked or self._menu_rects or left
                     or self._desk.get('fullscreen') or self._desk.get('busy') or self._web_working)
        forced=self._mouse_forced_look if now<self._mouse_forced_until and not blocked else None
        allowed=not blocked and forced is None
        self._mouse_sample=self._mouse_target.sample(screen,effective,now,window=window,
            scale=scale,display=display,source='RawIntegrated' if self._locked else 'System',
            pressed=bool(pet._user32.GetAsyncKeyState(0x01)&0x8000),allow_attention=allowed,
            interaction_id=interaction_id,interaction_mode=mode,bounds=self._mouse_bounds or self._bbox_all,
            geometry_revision=self._mouse_geometry,position_valid=valid,forced_look=forced)
        return self._mouse_sample

    def mouse_action(self, action):
        """Piggyback the newest sample; never queue a backlog of pointer motion."""
        if not self._mouse_target_supported:return action
        if action and action['kind'] in ('spin','zoom'):
            self._mouse_geometry+=1;self._mouse_due=0.0
        # Boundary events own their timestamp and position, even after a stalled renderer.
        sample=action['mouse'] if action and 'mouse' in action else self.sample_mouse_target()
        if action is None and sample['sequence']==self._mouse_sent:return None
        self._mouse_sent=max(self._mouse_sent,sample['sequence'])
        wire={**sample,'age':max(0,time.monotonic()-sample['timestamp'])}
        return {**(action or {'kind':'mouse'}),'mouse':wire}

    def activity_info(self,data):
        cue=data.get('pet_emotion') or {}
        seq=cue.get('seq',0)
        if self._emotion_seq is not None and seq!=self._emotion_seq and time.time()-cue.get('at',0)<10 and cue.get('emotion') in FACES:
            self._chat_face={'kind':'face','emotion':cue['emotion'],'duration':3}
            self._chat_face_until=time.monotonic()+max(0,3-(time.time()-cue.get('at',0)))
        self._emotion_seq=seq

    def _emotion_context(self):
        state=self._pet_state.snapshot() if self._pet_state is not None else {'mood':.6,'energy':1}
        self._emotion_revision+=1
        return {'emotionVersion':1,'mood':state['mood'],'energy':state['energy'],
                'needsVersion':1,'affinity':state.get('affinity',.5),
                'emotionRevision':self._emotion_revision}

    def animation_context(self, include_emotion=True):
        reason=next((name for name,active in (
            ('menu',self._menu_rects),('quiet',self._effective_quiet),
            ('locked-pointer',self._locked),('fullscreen',self._desk.get('fullscreen')),
            ('busy',self._desk.get('busy'))) if active),'')
        with self._interaction_lock:
            left=self._left
            touching=bool(left and left['mode']=='touch' and
                          self._interaction_records.get(left.get('id'),{}).get('region'))
        context={'kind':'context','version':1,'idle':pet._idle_seconds(),
                'blocked':bool(reason),'blockReason':reason,
                'voiceSuppressed':bool(self._effective_quiet or self._locked or self._desk.get('fullscreen') or self._desk.get('busy')),
                'moving':bool(self._left and self._left['mode']=='move'),
                'interactionIdle':0 if touching else max(0,time.monotonic()-self._last_contact_at),
                'working':self._web_working,'automatic':self._auto_animation}
        if include_emotion:context.update(self._emotion_context())
        return context

    def poll_action(self):
        action=self._poll_animation_action()
        context=self.animation_context(include_emotion=False)
        gate=tuple(context[key] for key in ('blockReason','moving','working','automatic','voiceSuppressed'))
        now=time.monotonic()
        # Carry state with the next command: continuous input must not starve
        # suppression updates or reorder a begin/end/cancel pair.
        if gate!=self._context_gate or now>=self._context_at or (action and action['kind']=='context'):
            self._context_gate=gate;self._context_at=now+1
            context.update(self._emotion_context())
            if action and action['kind']!='context':return {**action,'context':context}
            return context
        return action

    def _poll_animation_action(self):
        if self._ambience_pending is not None:
            name,self._ambience_pending=self._ambience_pending,None
            return {'kind':'ambience','name':name}
        if self._voice_state is not None:
            if self._voice_pending is not None or self._voice_start_pending:
                name=self._voice_pending or ''
                if self._voice_start_pending:name='startup';self._voice_start_pending=False
                self._voice_pending=None
                return {'kind':'voice','name':name,'voice':dict(self._voice_config)}
        if self._wardrobe_pending is not None:
            action,self._wardrobe_pending=self._wardrobe_pending,None
            return action
        action=super().poll_action()
        if action and action['kind']!='look':return action
        if self._chat_face and time.monotonic()>=self._chat_face_until:
            self._chat_face=None
        if self._chat_face and not self._left and not self._web_working:
            face,self._chat_face=self._chat_face,None
            face['duration']=min(face['duration'],max(.01,self._chat_face_until-time.monotonic()))
            return face
        if self._greeting_pending:
            self._greeting_pending=False
            if not self._material_styles:return {'kind':'greet'}
        return action

    def interaction_info(self,gesture_id,region,phase,cancelled=False,reason='',quality=None):
        with self._interaction_lock:
            record=self._interaction_records.get(gesture_id)
            if record and ((phase=='begin' and region and region!='-' and not record['region'])
                           or (phase in ('end','cancel') and record['region'])):
                self._last_contact_at=time.monotonic()
        super().interaction_info(gesture_id,region,phase,cancelled,reason,quality)

    def receive_feedback(self,feedback):
        if isinstance(feedback.get('voice'),dict) and feedback['voice'].get('version')==1:
            self._voice_state=feedback['voice']
        if isinstance(feedback.get('attention'),dict):
            self._attention_debug=feedback['attention']
        if feedback.get('emotionVersion')==1:self._emotion_supported=True
        if feedback.get('emotion') is not None:self._emotion_state=feedback['emotion']
        if feedback.get('mouseTargetVersion')==1:self._mouse_target_supported=True
        if feedback.get('mouseTarget') is not None:self._mouse_debug=feedback['mouseTarget']
        if feedback.get('kind')=='wardrobe':
            data=feedback.get('wardrobe') or {}
            if data.get('version')!=1:return
            self._wardrobe_supported=True
            revision=data.get('revision',-1)
            if revision<self._wardrobe_applied:return
            self._wardrobe_applied=revision
            self._wardrobe_outfits=[name for name in data.get('outfits', ['casual','uniform']) if name in wardrobe.OUTFITS]
            self._wardrobe_state=wardrobe.restore(data.get('state'))
            pet._save_settings(eku_wardrobe=self._wardrobe_state)
            if revision==self._wardrobe_revision:
                self._wardrobe_desired=wardrobe.restore(self._wardrobe_state)
            if self._menu_rects:
                self._pending={'kind':'menu','items':self.menu_items()}
            return
        if feedback.get('kind')=='animation':
            self._animation_state=feedback['animation']
            self._full_body_supported=(self._animation_state.get('fullBody') or {}).get('version')==1
            self._animation_received_at=time.monotonic()
            if self._animation_state.get('emotion') is not None:
                self._emotion_state=self._animation_state['emotion']
        elif feedback.get('phase') in ('begin','end','cancel') and (feedback.get('cancelled') or feedback.get('qualityVersion')==1 or self._emotion_supported):
            quality=feedback if feedback.get('qualityVersion')==1 or self._emotion_supported else None
            self.interaction_info(feedback['id'],feedback.get('region'),feedback['phase'],
                                  bool(feedback.get('cancelled')),feedback.get('reason',''),quality)
        elif feedback.get('phase') in ('begin','end','cancel'):
            self.interaction_info(feedback['id'],feedback.get('region'),feedback['phase'])

    def pet_state(self):
        return {**super().pet_state(),'animation':dict(self._animation_state),
                'animation_age':None if self._animation_received_at is None else max(0,time.monotonic()-self._animation_received_at),
                'emotion':{'supported':self._emotion_supported,'state':self._emotion_state},
                'mouse_target':{'supported':self._mouse_target_supported,
                                'sample':self._mouse_sample,'resolved':self._mouse_debug,
                                'attention':self._attention_debug},
                'animations':[{'name':name,'label':label} for name,label in self._animations.items()
                              if name!='greeting' or not self._material_styles],
                'ambience_modes':list(self._ambience_modes),
                'faces':sorted(FACES),'auto_animation':self._auto_animation,
                'voice':{'supported':self._voice_state is not None,'settings':dict(self._voice_config),
                         'state':self._voice_state},
                'wardrobe':{'supported':self._wardrobe_supported,'state':wardrobe.restore(self._wardrobe_state),
                            'pending':self._wardrobe_supported and self._wardrobe_applied<self._wardrobe_revision,
                            'outfits':{k:wardrobe.OUTFITS[k] for k in self._wardrobe_outfits},
                            'parts':{k:wardrobe.PARTS[k] for k in self._wardrobe_outfits}}}

    def pet_command(self,payload,ua=''):
        kind=str(payload.get('action') or '').strip().lower()
        if kind=='animation' and str(payload.get('name','')).startswith('ambience_'):
            return self.pet_command({'action':'ambience','name':payload['name'].removeprefix('ambience_')},ua)
        if kind=='ambience':
            name=payload.get('name')
            if not isinstance(name,str) or name not in self._ambience_modes:
                return {'ok':False,'error':'未知动态待机模式'}
            self._ambience_pending=name
            pet._save_settings(eku_ambience=name)
            return {'ok':True,'name':name}
        if self._presence and kind in ('model','style','dance','interact','react','magnifier','proactive','talk_mode'):
            return {'ok':False,'error':'当前 Presence 播放器不支持此操作'}
        if kind=='voice':
            if self._voice_state is None:return {'ok':False,'error':'当前播放器不支持语音'}
            changes={key:payload[key] for key in ('enabled','proactive','volume') if key in payload}
            for key in ('enabled','proactive'):
                if key in changes and not isinstance(changes[key],bool):return {'ok':False,'error':'语音开关必须为布尔值'}
            if 'volume' in changes:
                volume=changes['volume']
                if isinstance(volume,bool) or not isinstance(volume,(int,float)) or not math.isfinite(volume) or not 0<=volume<=1:
                    return {'ok':False,'error':'语音音量必须在 0–1 之间'}
            preview=payload.get('preview',False)
            if not isinstance(preview,bool):return {'ok':False,'error':'试听必须为布尔值'}
            if not changes and not preview:return {'ok':False,'error':'缺少语音设置或试听操作'}
            config={**self._voice_config,**changes}
            if preview and (not config['enabled'] or config['volume']==0):return {'ok':False,'error':'请先开启语音并设置音量'}
            if preview and not self._voice_state.get('available'):return {'ok':False,'error':'本地语音素材不可用'}
            self._voice_config=config
            if changes:pet._save_settings(eku_voice=dict(config))
            self._voice_pending='preview' if preview else ''
            return {'ok':True,'settings':dict(config)}
        if kind=='look':
            result=super().pet_command(payload,ua)
            if result.get('ok'):
                self._mouse_forced_look=self._cursor
                self._mouse_forced_until=time.monotonic()+5
                self._mouse_due=0.0
            return result
        if kind=='wardrobe':
            if not self._wardrobe_supported:return {'ok':False,'error':'当前播放器不支持换装'}
            try:state=wardrobe.change(self._wardrobe_desired,payload)
            except ValueError as error:return {'ok':False,'error':str(error)}
            if state['outfit'] not in self._wardrobe_outfits:return {'ok':False,'error':'当前播放器没有这套服装'}
            self._wardrobe_desired=state
            self._wardrobe_revision+=1
            self._wardrobe_pending={'kind':'wardrobe','wardrobe':state,'revision':self._wardrobe_revision}
            return {'ok':True,'revision':self._wardrobe_revision}
        if kind=='animation':
            name=str(payload.get('name') or '').strip().lower()
            if name=='open':name='greeting'
            if name=='greeting' and self._material_styles:return {'ok':False,'error':'招手已取消'}
            if name=='auto':
                self._auto_animation=not self._auto_animation
                pet._save_settings(eku_auto_animation=self._auto_animation)
                self._pending=self.animation_context()
                return {'ok':True,'automatic':self._auto_animation}
            if name not in self._animations:return {'ok':False,'error':'未知 Eku 动画'}
            self._pending={'kind':'animation','name':name}
            return {'ok':True,'name':name}
        if kind=='face':
            emotion=str(payload.get('emotion') or 'happy').strip().lower().replace(' ','_')
            duration=payload.get('duration',2)
            if emotion not in FACES:return {'ok':False,'error':'未知 Eku 表情'}
            if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or not .2<=duration<=10:
                return {'ok':False,'error':'表情持续时间必须在 0.2–10 秒之间'}
            self._pending={'kind':'face','emotion':emotion,'duration':duration}
            return {'ok':True,'emotion':emotion}
        if kind=='idle':return self.pet_command({'action':'animation','name':'stand'},ua)
        return super().pet_command(payload,ua)

    @property
    def _last_png(self):
        image=getattr(self,'latest_image',None)
        if image is None:return getattr(self,'cached_png','')
        output=io.BytesIO();image.save(output,format='PNG')
        return base64.b64encode(output.getvalue()).decode()

    @_last_png.setter
    def _last_png(self,value):self.cached_png=value

    def menu_items(self):
        page,_,number=self._menu_page.partition(':')
        if self._presence:
            if page=='help':
                return [{'id':'page:root','label':'← 返回'}]+[{'id':'none','label':label} for label in (
                    '按住头部轻移：抚摸', 'Alt＋左键拖动：移动桌宠',
                    '右键拖动：旋转；轻点：菜单', '滚轮：放大 / 缩小')]
            if page=='ambience' and self._ambience_modes:
                mode=(self._animation_state.get('ambience') or {}).get('mode','')
                return [{'id':'page:motions','label':'← 返回'}]+[
                    {'id':'animation:ambience_'+name,'label':('● ' if name==mode else '')+presence_config.AMBIENCE[name]}
                    for name in self._ambience_modes]
            if page in ('motions','eku_hands','interactions'):
                return [{'id':'page:root','label':'← 返回'}]+[
                    {'id':'animation:'+name,'label':label} for name,label in self._animations.items()]+(
                    [{'id':'page:ambience','label':'动态待机 ▸'}] if self._ambience_modes else [])
        if page=='voice' and self._voice_state is not None:
            config=self._voice_config
            return [{'id':'page:config','label':'← 返回'},
                    {'id':'voice:enabled','label':'语音：'+('开' if config['enabled'] else '关')},
                    {'id':'voice:proactive','label':'主动呼唤：'+('开' if config['proactive'] else '关')}]+[
                    {'id':'voice:volume:'+str(value),'label':('● ' if abs(config['volume']-value/100)<.001 else '')+f'音量 {value}%'}
                    for value in (30,60,100)]+[{'id':'voice:preview','label':'试听'}]
        if page=='wardrobe' and self._wardrobe_supported:
            state=self._wardrobe_state
            return [{'id':'page:root','label':'← 返回'}]+[
                {'id':'wardrobe:outfit:'+name,'label':('● ' if state['outfit']==name else '')+label}
                for name,label in wardrobe.OUTFITS.items() if name in self._wardrobe_outfits]+[
                {'id':'page:wardrobe_parts','label':'穿脱设置 ▸'},
                {'id':'wardrobe:reset','label':'恢复默认'}]
        if page=='wardrobe_parts' and self._wardrobe_supported:
            state=self._wardrobe_state;outfit=state['outfit']
            return [{'id':'page:wardrobe','label':'← 返回'}]+[
                {'id':'wardrobe:part:'+name,'label':label,'detail':'穿着' if state[outfit][name] else '已脱去'}
                for name,label in wardrobe.PARTS[outfit].items()]
        if page in ('motions','eku_hands'):
            choices=['stand','crouch','prone','wake' if self._animation_state.get('afk') else 'rest'] if page=='motions' else ['greeting','thumbsup','peace','point','fist','rocknroll','gun']
            if self._material_styles:choices=[name for name in choices if name!='greeting']
            offset=min(int(number) if number.isdigit() else 0,(len(choices)-1)//5)*5
            parent='page:root' if page=='motions' else 'page:motions'
            items=[{'id':parent,'label':'← 返回'}]+[{'id':'animation:'+name,'label':ANIMATIONS[name]} for name in choices[offset:offset+5]]
            if offset:items.append({'id':'page:eku_hands:0','label':'‹ 上一页'})
            if len(choices)>offset+5:items.append({'id':'page:eku_hands:1','label':'下一页 ›'})
            if page=='motions':items.append({'id':'page:eku_hands','label':'手势 ▸'})
            return items
        items=super().menu_items()
        if page=='root' and self._wardrobe_supported:
            items.insert(3,{'id':'page:wardrobe','label':'换装','detail':wardrobe.OUTFITS[self._wardrobe_state['outfit']]})
        hidden={'page:faces'}
        if self._presence:hidden.update(('page:models','page:magnifier','page:talk'))
        return [item for item in items if item['id'] not in hidden]

    def wardrobe_menu(self,name):
        if name=='reset':payload={'reset':True}
        elif name.startswith('outfit:'):payload={'outfit':name.split(':',1)[1]}
        elif name.startswith('part:'):
            part=name.split(':',1)[1];parts=self._wardrobe_desired[self._wardrobe_desired['outfit']]
            if part not in parts:return {'ok':False,'error':'未知穿脱部件'}
            payload={'part':part,'enabled':not parts[part]}
        else:return {'ok':False,'error':'未知换装操作'}
        return self.pet_command({'action':'wardrobe',**payload})

    def voice_menu(self,name):
        if name in ('enabled','proactive'):payload={name:not self._voice_config[name]}
        elif name.startswith('volume:') and name.split(':',1)[1] in ('30','60','100'):payload={'volume':int(name.split(':',1)[1])/100}
        elif name=='preview':payload={'preview':True}
        else:return {'ok':False,'error':'未知语音操作'}
        return self.pet_command({'action':'voice',**payload})

    def _config_items(self):
        unsupported={'style','pick:motion_file','open:motions','ik'}
        if self._presence:unsupported.update(('pick:model_file','page:talk','notes'))
        items=[item for item in super()._config_items() if item['id'] not in unsupported]
        items.insert(1,{'id':'animation:auto','label':'自动动作：'+('开' if self._auto_animation else '关')})
        if self._voice_state is not None:items.insert(2,{'id':'page:voice','label':'语音 ▸'})
        return items

    def set_talk_mode(self,mode):
        if not self._presence:super().set_talk_mode(mode)

    def configure_model(self,filename,scale=None,rotate=None):
        super().configure_model(filename,scale,rotate)
        if filename == pet.BUNDLED_EKU:self._style_index=0


class Desktop:
    def __init__(self, api, width, height):
        self.api=api;self.base=(width,height);self.zoom=1;self.items=[];self.bubble='';self.until=0
        self.stopped=threading.Event();self.delivery=None
        self.font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',14)
        self._menu_cache=None

    def destroy(self):self.stopped.set()

    def action(self, action):
        kind=action['kind']
        if kind=='switch_model':
            self.items=[];self.api.menu_rects([])
            self.api.switch_model(action['index'])
            return {'kind':'menu_close'}
        if kind not in ('menu','look','mouse','say') and not self.api._menu_rects:
            self.items=[]
            action={**action,'close_menu':True}
        if kind=='zoom':
            self.zoom=max(.5,min(2.5,self.zoom*1.1**action['steps']))
            # Re-render at the new pixel size instead of enlarging a small bitmap.
            action={**action,'width':round(self.base[0]*self.zoom),'height':round(self.base[1]*self.zoom)}
        elif kind=='menu':self.items=action['items']
        elif kind=='menu_close':self.items=[];self.api.menu_rects([])
        elif kind=='say':
            self.bubble=str(action.get('text',''))[:600];self.until=time.monotonic()+max(5,min(30,len(self.bubble)/3))
            self.delivery=action.get('delivery_id')
            if action.get('plain') and self.bubble.startswith('♪ '):action={**action,'kind':'music'}
        return action

    def menu_overlay(self,w,h):
        key=(w,h,json.dumps(self.items,ensure_ascii=False))
        if self._menu_cache and self._menu_cache[0]==key:return self._menu_cache[1:]
        # Draw once at 2x, then cache the antialiased panel between Unity frames.
        scale=2;pad=8;row=24 if h<400 else 30;size=13 if h<400 else 14
        font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',size*scale)
        labels=[item['label'].replace(' ▸','').replace('← ','').replace('‹ ','').replace(' ›','').lstrip('　●▶ ') for item in self.items]
        width=math.ceil(min(w-pad*2,252,max(204,max((font.getlength(label)/scale for label in labels),default=0)+pad*4)))
        height=pad*2+row*len(self.items)+(8 if any(item['id']=='quit' for item in self.items) else 0)
        panel=Image.new('RGBA',(width*scale,height*scale))
        drawing=ImageDraw.Draw(panel)
        drawing.rounded_rectangle((scale,scale,(width-1)*scale,(height-1)*scale),radius=12*scale,
                                  fill=(250,248,252,222),outline=(255,255,255,184),width=scale)
        def shorten(text,available):
            if font.getlength(text)<=available*scale:return text
            while text and font.getlength(text+'…')>available*scale:text=text[:-1]
            return text+'…' if text else ''
        rects=[];y=pad
        for item,label in zip(self.items,labels):
            if item['id']=='quit':
                drawing.line((pad*2*scale,(y+3)*scale,(width-pad*2)*scale,(y+3)*scale),fill=(122,104,117,45),width=scale)
                y+=8
            selected=item['label'].startswith(('●','▶'))
            color='#923660' if selected else '#675763' if item['id'] in ('quit','none') else '#34313e'
            if selected:
                drawing.rounded_rectangle((pad*scale,(y+2)*scale,(width-pad)*scale,(y+row-2)*scale),radius=6*scale,fill=(198,95,142,31))
                drawing.ellipse(((pad+3)*scale,(y+row/2-2)*scale,(pad+7)*scale,(y+row/2+2)*scale),fill=color)
            back=item['label'].startswith(('←','‹'))
            forward=item['id'].startswith('page:') and not back
            text_x=pad*2+(10 if back else 3 if selected else 0)
            right=width-pad*2-(12 if forward else 0)
            if back or forward:
                x=pad*2+2 if back else width-pad*2-2
                direction=-1 if back else 1;cy=y+row/2
                drawing.line(((x-direction*2)*scale,(cy-3)*scale,(x+direction)*scale,cy*scale,(x-direction*2)*scale,(cy+3)*scale),fill='#675763',width=2*scale,joint='curve')
            detail=shorten(str(item.get('detail','')),min(82,max(0,right-text_x-font.getlength(label)/scale-12)))
            available=right-text_x-(font.getlength(detail)/scale+12 if detail else 0)
            if item['id']=='style':
                drawing.text((width/2*scale,(y+row/2)*scale),label,font=font,fill=color,anchor='mm')
                for x,direction in ((pad*2,-1),(width-pad*2,1)):
                    drawing.line(((x-direction*2)*scale,(y+row/2-3)*scale,(x+direction)*scale,(y+row/2)*scale,(x-direction*2)*scale,(y+row/2+3)*scale),fill=color,width=2*scale)
            else:
                drawing.text((text_x*scale,(y+row/2)*scale),shorten(label,available),font=font,fill=color,anchor='lm')
                if detail:drawing.text((right*scale,(y+row/2)*scale),detail,font=font,fill='#675763',anchor='rm')
            rects.append({'id':item['id'],'x':pad,'y':y,'w':width-pad*2,'h':row})
            y+=row
        fit=min(1,(h-pad*2)/height)
        panel=panel.resize((round(width*fit),round(height*fit)),Image.Resampling.LANCZOS)
        left=w-pad-panel.width;top=h-pad-panel.height
        rects=[{**rect,'x':left+rect['x']*fit,'y':top+rect['y']*fit,'w':rect['w']*fit,'h':rect['h']*fit} for rect in rects]
        overlay=Image.new('RGBA',(w,h));overlay.paste(panel,(left,top))
        self._menu_cache=(key,overlay,rects)
        return overlay,rects

    def present(self, image):
        api=self.api
        model_box=image.getchannel('A').getbbox()
        startup_ready=bool(model_box)
        # Bubbles and menus are not part of the character's proximity bounds.
        api._mouse_bounds=tuple(v/image.size[i%2] for i,v in enumerate(model_box)) if model_box else None
        w,h=(round(v*self.zoom) for v in self.base)
        if image.size!=(w,h):image=image.resize((w,h),Image.Resampling.LANCZOS)
        drawing=ImageDraw.Draw(image)
        if self.items:
            overlay,rects=self.menu_overlay(w,h)
            image=Image.alpha_composite(image,overlay)
            # Avoid logging identical menu rectangles for every frame.
            if rects!=api._menu_rects:api.menu_rects(rects)
        elif self.bubble and time.monotonic()<self.until:
            columns=max(8,(w-48)//14)
            lines=[self.bubble[i:i+columns] for i in range(0,len(self.bubble),columns)][:8]
            bottom=22+22*len(lines)
            drawing.rounded_rectangle((12,8,w-12,bottom+8),radius=8,fill='#faf8f5',outline='#78927f')
            for i,line in enumerate(lines):drawing.text((22,18+i*22),line,font=self.font,fill='#28392c')
            api.reply_target([12/w,8/h,(w-24)/w,(bottom+8)/h],self.bubble)
        else:api.reply_target(None)
        api.latest_image=image
        alpha=np.asarray(image.getchannel('A'))>32
        premul=image.convert('RGBa')
        bgra=Image.frombytes('RGBA',image.size,premul.tobytes()).tobytes('raw','BGRA')
        api._frames_in+=1
        before=api._last_present
        api._present_bgra(bgra,w,h,alpha,source_bytes=len(bgra),startup_ready=startup_ready)
        if self.delivery and api._last_present>before:
            api.proactive_displayed(self.delivery);self.delivery=None


def run(args,presence=None):
    if not (pet._single_instance(presence.instance_name) if presence else pet._single_instance()):
        pet._message_box('已有桌宠正在运行。请先从她的右键菜单退出，再启动。')
        return 0
    try:
        with pet.StartupAnimation(pet._log,enabled=not getattr(args,'debug',False),on_error=pet._message_box) as startup:
            return _run(args,startup,presence)
    except Exception as error:
        pet._log(f'Eku startup failed: {error}')
        return 2
    finally:pet._release_single_instance()


def _run(args,startup,presence=None):
    default_player=next((p for p in (BUNDLED_PLAYER,WARDROBE_BUNDLED_PLAYER,WARDROBE_PLAYER,COMBINED_PLAYER) if p.is_file()),PLAYER)
    player=presence.player if presence else Path(os.environ.get('NYALUME_UNITY_PLAYER',str(default_player))).resolve()
    pose=presence.pose if presence else POSE
    material_styles=player in (VOICE_PLAYER.resolve(),BUNDLED_PLAYER.resolve(),WARDROBE_BUNDLED_PLAYER.resolve(),WARDROBE_PLAYER.resolve(),JK_PLAYER.resolve(),MATERIAL_PLAYER.resolve(),HAIR_PLAYER.resolve()) or player.parent.parent.parent==MATERIAL_PLAYER.parent.parent.resolve()
    if not player.is_file() or not pose.is_file():
        raise FileNotFoundError('Eku 播放器或姿态文件缺失，请检查本地模型资源。')
    api=UnityApi(presence=presence) if presence else UnityApi()
    api._renderer_hidden=True;api._renderer='unity'
    api._startup=startup
    try:
        capability=json.loads((player.parent/'wardrobe.json').read_text(encoding='utf8'))
        material_styles=material_styles or capability.get('materialStyles') is True
        api._wardrobe_supported=not presence and capability.get('version')==1
        api._wardrobe_outfits=[name for name in capability.get('outfits',['casual','uniform']) if name in wardrobe.OUTFITS]
    except (OSError,ValueError,AttributeError):pass
    if presence:material_styles=False
    api._material_styles=material_styles
    if api._wardrobe_state['outfit'] not in api._wardrobe_outfits:
        api._wardrobe_state['outfit']='casual'
        api._wardrobe_desired=wardrobe.restore(api._wardrobe_state)
    initial_outfit=os.environ.get('NYALUME_EKU_OUTFIT')
    if initial_outfit in api._wardrobe_outfits:
        api._wardrobe_state['outfit']=initial_outfit
        api._wardrobe_desired=wardrobe.restore(api._wardrobe_state)
    folder,name=pet._resolve_model(args.model)
    api._model_dir=folder;api.configure_model(name if name==pet.BUNDLED_EKU else str(Path(folder,name)),args.scale,args.rotate)
    api.load_library(pet.default_motion_root(),folder)
    pet._remember_dirs(folder,pet.default_motion_root(),name)
    if material_styles:api._style_index=0
    api.style_info('自然·柔和' if material_styles else 'Unity · lilToon')
    width,height=(int(v) for v in args.size.lower().split('x'))
    desktop=Desktop(api,width,height);api._destroy_window=desktop.destroy
    api.magnifier=None if presence else Magnifier(api)
    bridge=None;display=None
    try:
        api.startup_state('renderer')
        bridge=UnityBridge(size=(width,height),player=player,pose=pose,output=presence.output if presence else None,
                           wardrobe_state=api._wardrobe_state if api._wardrobe_supported else None)
        api.startup_state('model')
        startup_stage='model'
        port=pet.start_server(folder,api=api);pet._write_endpoint(port,folder)
        for target in (pet._state_loop,pet._fps_watch,pet._desktop_watch):
            threading.Thread(target=target,args=(api,),daemon=True).start()
        try:
            from nyalume.core import llm as _llm_config
            if not presence and pet.proactive.llm_ready():
                threading.Thread(target=pet.proactive.run_loop,args=(api,),daemon=True).start()
            else:api._proactive=False
        except Exception as error:
            api._proactive=False;pet._log(f'Unity proactive unavailable: {error}')
        display=threading.Thread(target=pet._display_window_thread_guarded,
            args=(api,desktop,pet.default_motion_root(),folder),daemon=True);display.start()
        pending=api.animation_context()
        next_poll=0;first_deadline=time.monotonic()+30;greeted=False
        while not desktop.stopped.is_set():
            if bridge.player.poll() is not None:raise RuntimeError('Unity 已退出，请查看 unity-desktop.log')
            feedback=bridge.receive_feedback()
            if feedback:
                if startup_stage=='model' and feedback.get('kind')=='animation':
                    api.startup_state('animation');startup_stage='animation'
                api.receive_feedback(feedback)
            now=time.monotonic()
            api.sample_mouse_target(now)
            if bridge.ready_for_command:
                if pending is None and now>=next_poll:
                    pending=api.poll_action();next_poll=now+.02
                    pending=api.mouse_action(pending)
                    if pending:pending=desktop.action(pending)
                if pending is not None and bridge.send(pending):pending=None
            image=bridge.frame()
            if api.magnifier:
                lens_frame=bridge.magnifier_frame()
                if lens_frame is not None:api.magnifier.receive(*lens_frame)
                api.magnifier.present()
            if image is not None:
                if startup_stage!='display' and image.getchannel('A').getbbox():
                    api.startup_state('display');startup_stage='display'
                desktop.present(image)
                if not greeted and api._startup_finished:
                    greeted=True;api._greeting_pending=not material_styles
                    api._voice_start_pending=True
            if not api._startup_finished and now>first_deadline:raise RuntimeError('Unity 在 30 秒内没有送出有效画面')
            time.sleep(.002)
    except KeyboardInterrupt:pass
    except Exception as error:
        startup.close()
        pet._log(f'Unity trial failed: {error}')
        pet._message_box(f'Unity 桌宠启动/运行失败：{error}\n请检查 unity 目录资源及日志，或重新解压完整程序包。')
        return 2
    finally:
        api._finish_left(cancel=True);api._display_hwnd=0
        if api._display_thread_id:pet._user32.PostThreadMessageW(api._display_thread_id,0x12,0,0)
        if display:display.join(timeout=2)
        if bridge:bridge.close()
        for chat in (api._chat,api._quick_chat,api._fantasy_chat):
            if chat is not None:chat.close()
    return 0
