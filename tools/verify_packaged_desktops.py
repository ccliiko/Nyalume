"""Cold-start both packaged desktop entries without developer model paths or API keys."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request,urlopen

from PIL import ImageGrab
import psutil


def windows(pid):
    user=ctypes.windll.user32
    result=[]
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,unused):
        owner=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==pid and user.IsWindowVisible(hwnd):
            rect=wintypes.RECT();user.GetWindowRect(hwnd,ctypes.byref(rect))
            if rect.right>rect.left and rect.bottom>rect.top:
                result.append((hwnd,(rect.left,rect.top,rect.right,rect.bottom)))
        return True
    user.EnumWindows(callback(visit),0)
    return result


def command(port,payload):
    request=Request(f'http://127.0.0.1:{port}/pet',data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=3) as response:return json.load(response)


def check_magnifier(port,pid,output,label):
    assert command(port,{'action':'magnifier','name':'toggle'})['enabled']
    time.sleep(2)
    viewers=[]
    for hwnd,rect in windows(pid):
        title=ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd,title,256)
        if title.value=='Nyalume 放大镜':viewers.append(rect)
    assert viewers,'Packaged magnifier has no visible image window'
    ImageGrab.grab(bbox=viewers[0]).save(output/f'{label}-magnifier.png')
    assert command(port,{'action':'magnifier','name':'move'})['moving']
    assert not command(port,{'action':'magnifier','name':'move'})['moving']
    assert not command(port,{'action':'magnifier','name':'close'})['enabled']
    return {'visible':True,'selection_toggle':True,'closed':True}


def check_wardrobe(port,output):
    def apply(payload,expected):
        assert command(port,{'action':'wardrobe',**payload})['ok']
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            with urlopen(f'http://127.0.0.1:{port}/pet_state',timeout=2) as response:state=json.load(response)
            wardrobe=state['wardrobe']
            if not wardrobe['pending'] and expected(wardrobe['state']):return state
            time.sleep(.1)
        raise AssertionError(f'Wardrobe did not apply {payload}')
    state=apply({'outfit':'uniform'},lambda s:s['outfit']=='uniform')
    assert set(state['wardrobe']['outfits'])=={'casual','uniform'}
    state=apply({'part':'jacket','enabled':False},lambda s:not s['uniform']['jacket'])
    rect=(*state['pos'],state['pos'][0]+state['size'][0],state['pos'][1]+state['size'][1])
    ImageGrab.grab(bbox=rect).save(output/'wardrobe-uniform-no-jacket.png')
    apply({'outfit':'casual'},lambda s:s['outfit']=='casual')
    apply({'outfit':'uniform'},lambda s:s['outfit']=='uniform' and not s['uniform']['jacket'])
    state=apply({'reset':True},lambda s:s['outfit']=='casual' and all(s['casual'].values()) and all(s['uniform'].values()))
    assert not command(port,{'action':'wardrobe','part':'invalid','enabled':False})['ok']
    return state['wardrobe']


def verify(folder,output,check_updates=False):
    output.mkdir(parents=True,exist_ok=True)
    assert not list(folder.rglob('*.vrm')) and not list(folder.rglob('*.fbx'))
    assert not list(folder.rglob('*.blend')) and not list(folder.rglob('*.content'))
    assert not list(folder.rglob('.env*'))
    assert (folder/'models/野餐式MikuQ/Readme.txt').is_file()
    assert 'miratsu/新生帝国野餐队队长' in (folder/'借物表.md').read_text(encoding='utf8')
    endpoint=Path.home()/'.nyalume/pet3d_endpoint.json'
    settings=Path.home()/'.nyalume/pet3d_settings.json'
    original=settings.read_bytes() if settings.exists() else None
    env={**os.environ,'MEMORY_DB':str(output/'verification.db'),'LLM_API_KEY':''}
    env.pop('NYALUME_UNITY_PLAYER',None)
    report={}
    try:
        for name in ('Nyalume3D.exe','Nyalume2D.exe'):
            process=subprocess.Popen([str(folder/name)],cwd=output,env=env,
                creationflags=subprocess.CREATE_NO_WINDOW)
            owned={process.pid}
            try:
                deadline=time.monotonic()+60
                state=None;visible=[]
                while time.monotonic()<deadline:
                    assert process.poll() is None,f'{name} exited before startup'
                    visible=windows(process.pid)
                    if name=='Nyalume3D.exe':
                        try:
                            info=json.loads(endpoint.read_text(encoding='utf8'))
                            if info['pid']==process.pid:
                                with urlopen(f"http://127.0.0.1:{info['port']}/pet_state",timeout=2) as response:
                                    state=json.load(response)
                                if state.get('frames_in',0)>60 and visible:break
                        except (OSError,ValueError,KeyError):pass
                    elif visible and time.monotonic()>deadline-53:break
                    time.sleep(.1)
                assert visible,f'{name} has no visible desktop window'
                if name=='Nyalume3D.exe':
                    assert state and state['frames_in']>60
                    awareness=ctypes.c_int()
                    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                    kernel.OpenProcess.restype=wintypes.HANDLE
                    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                    handle=kernel.OpenProcess(0x1000,False,process.pid)
                    assert handle,'Cannot inspect packaged DPI awareness'
                    try:
                        get_awareness=ctypes.windll.shcore.GetProcessDpiAwareness
                        get_awareness.argtypes=[wintypes.HANDLE,ctypes.POINTER(ctypes.c_int)]
                        assert get_awareness(handle,ctypes.byref(awareness))==0
                        assert awareness.value==2,'Packaged 3D entry lacks physical-pixel DPI awareness'
                    finally:kernel.CloseHandle(handle)
                    report['dpi_awareness']=awareness.value
                    assert state['model']=='Eku（内置）' and '野餐式MikuQ' in state['models']
                    assert 'animation' in state
                    if check_updates:assert state.get('wardrobe',{}).get('supported'), 'Missing wardrobe assets'
                    port=info['port']
                    request=Request(f'http://127.0.0.1:{port}/pet',data=json.dumps(
                        {'action':'face','emotion':'happy','duration':2}).encode(),
                        headers={'Content-Type':'application/json'})
                    with urlopen(request,timeout=3) as response:assert json.load(response)['ok']
                    time.sleep(2)
                    # Exercise the real restart path: encrypted Unity -> PMX -> Unity.
                    switches=[]
                    for model in ('野餐式MikuQ','Eku（内置）'):
                        request=Request(f'http://127.0.0.1:{port}/pet',data=json.dumps(
                            {'action':'model','name':model}).encode(),
                            headers={'Content-Type':'application/json'})
                        with urlopen(request,timeout=3) as response:assert json.load(response)['ok']
                        deadline=time.monotonic()+60
                        switched=False
                        while time.monotonic()<deadline:
                            try:
                                info=json.loads(endpoint.read_text(encoding='utf8'))
                                if info['pid'] not in owned:
                                    candidate=psutil.Process(info['pid'])
                                    assert Path(candidate.exe()).resolve()==(folder/name).resolve()
                                    owned.add(info['pid'])
                                port=info['port']
                                with urlopen(f'http://127.0.0.1:{port}/pet_state',timeout=2) as response:
                                    updated=json.load(response)
                                if updated['model']==model and updated['frames_in']>45:
                                    state=updated;switched=True;break
                            except (OSError,ValueError,KeyError,psutil.NoSuchProcess):pass
                            time.sleep(.1)
                        assert switched,f'Could not switch to {model}'
                        switches.append(model)
                        if check_updates:
                            report[model+' magnifier']=check_magnifier(port,info['pid'],output,model)
                        rect=(*state['pos'],state['pos'][0]+state['size'][0],state['pos'][1]+state['size'][1])
                        ImageGrab.grab(bbox=rect).save(output/(model+'.png'))
                    report['model_switches']=switches
                    if check_updates:report['wardrobe']=check_wardrobe(port,output)
                    rect=(*state['pos'],state['pos'][0]+state['size'][0],state['pos'][1]+state['size'][1])
                    report[name]=state
                else:
                    rect=min((rect for _,rect in visible),key=lambda r:(r[2]-r[0])*(r[3]-r[1]))
                    report[name]={'visible_windows':len(visible),'window_rect':rect}
                ImageGrab.grab(bbox=rect).save(output/(name+'.png'))
            finally:
                # WM_CLOSE deliberately triggers window recovery. Stop only test-owned
                # processes, including the instances created by explicit model switches.
                for pid in owned:
                    try:
                        if Path(psutil.Process(pid).exe()).resolve()==(folder/name).resolve():
                            subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],capture_output=True)
                    except psutil.NoSuchProcess:pass
                process.wait(timeout=10)
                time.sleep(1)
        report['encrypted_content_sha256']=hashlib.sha256(
            (folder/'unity/EkuPlayer_Data/StreamingAssets/eku.bin').read_bytes()).hexdigest()
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print('Both packaged EXEs started and rendered successfully',flush=True)
    finally:
        if original is not None:settings.write_bytes(original)


if __name__=='__main__':
    # Match the app's physical-pixel window coordinates when capturing scaled displays.
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--check-updates',action='store_true',help='Require wardrobe and exercise both native magnifiers')
    args=parser.parse_args();verify(args.folder.resolve(),args.output.resolve(),args.check_updates)
