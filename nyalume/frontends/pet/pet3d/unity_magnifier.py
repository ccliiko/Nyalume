"""Shared native viewer for freshly rendered Unity and Three.js camera crops."""
from collections import deque
import time

from PIL import Image, ImageDraw, ImageFont
from . import pet3d_win as pet


def viewer_position(pet_rect, work_area, size=(272, 304)):
    x,y,w,h=pet_rect;left,top,right,bottom=work_area
    px=x+w+12 if x+w+12+size[0]<=right else x-size[0]-12
    return (max(left,min(px,right-size[0])),max(top,min(y,bottom-size[1])))


def clamp_viewer_position(position,bounds,size=(272,304)):
    left,top,right,bottom=bounds
    return (round(max(left,min(position[0],right-size[0]))),round(max(top,min(position[1],bottom-size[1]))))


class Magnifier:
    def __init__(self,api):
        self.api=api;self.enabled=False;self.moving=False
        self.handles=[];self.frame=None;self.region=(.5,.2,.2,.13)
        self.drag=None;self.commands=deque();self.selection_command=None
        self.requested_center=None
        self.updated=0;self.visible=False;self.position=(0,0)
        self.manual_position=None;self.window_drag=None
        self.font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',13)
        api._native_overlay_start=self.start
        api._native_overlay_event=self.event
        api._native_overlay_close=self.close

    def start(self):
        self.handles=[pet._create_layered_window() for _ in range(3)]
        for hwnd,title in zip(self.handles,('Nyalume 放大镜','Nyalume 放大镜操作','Nyalume 框选区域')):
            pet._user32.SetWindowTextW(hwnd,title)
            pet._user32.ShowWindow(hwnd,0)
        # Only the image passes clicks through. Controls and the selection own theirs.
        hwnd=self.handles[0]
        pet._SetWindowLongPtr(hwnd,pet._GWL_EXSTYLE,pet._GetWindowLongPtr(hwnd,pet._GWL_EXSTYLE)|0x20)

    def close(self):
        for hwnd in self.handles:
            pet._user32.DestroyWindow(hwnd)
            state=pet._LAYER.pop(hwnd,None)
            if state:
                pet._gdi32.DeleteObject(state['hbmp']);pet._gdi32.DeleteDC(state['dc'])
        self.handles=[]

    def command(self,name):
        if name not in ('toggle','move','close'):return
        self.api._finish_left(cancel=True)
        if name in ('toggle','close'):
            self.enabled=not self.enabled if name=='toggle' else False
            self.moving=False;self.drag=None;self.selection_command=None;self.requested_center=None
            self.window_drag=None
            self.frame=None
            self.commands.append({'kind':'magnifier','value':self.enabled})
        else:
            if not self.enabled:self.command('toggle')
            self.moving=not self.moving
            if self.drag:self.select(*self.drag['last'],phase='end')
            self.drag=None
            self.commands.append({'kind':'magnifier_move','value':self.moving})
        self.updated=0

    def poll(self):
        if self.moving and pet._user32.GetAsyncKeyState(0x1B)&0x8000:self.command('move')
        if self.commands:return self.commands.popleft()
        command,self.selection_command=self.selection_command,None
        return command

    def receive(self,image,region):
        if self.enabled:
            self.frame=image
            matched=self.requested_center is None or all(abs(a-b)<.001 for a,b in zip(region,self.requested_center))
            if not self.drag and (not self.moving or matched):
                self.region=region;self.requested_center=None

    def selection_rect(self):
        cx,cy,sx,sy=self.region;api=self.api
        return (api._x+(cx-sx/2)*api._w,api._y+(cy-sy/2)*api._h,sx*api._w,sy*api._h)

    def select(self,x,y,phase='update'):
        api=self.api
        if not api._w or not api._h:return
        cx=max(0,min(1,(x-api._x)/api._w));cy=max(0,min(1,(y-api._y)/api._h))
        self.region=(cx,cy,*self.region[2:])
        self.requested_center=(cx,cy)
        self.selection_command={'kind':'magnifier_select','x':cx,'y':cy,'phase':phase}

    def event(self,msg,x,y):
        if not self.visible:return False
        if self.window_drag:
            if msg in (pet._WM_MOUSEMOVE,pet._WM_LBUTTONUP):
                dx,dy=self.window_drag
                (left,top),(right,bottom)=pet._virtual_screen()
                self.manual_position=clamp_viewer_position((x-dx,y-dy),(left,top,right,bottom))
                self.position=self.manual_position;self.updated=0
                if msg==pet._WM_LBUTTONUP:self.window_drag=None
            return True
        if self.drag:
            if msg in (pet._WM_MOUSEMOVE,pet._WM_LBUTTONUP):
                dx,dy=self.drag['offset'];self.drag['last']=(x-dx,y-dy)
                self.select(x-dx,y-dy,'end' if msg==pet._WM_LBUTTONUP else 'update')
                if msg==pet._WM_LBUTTONUP:self.drag=None
            return True
        px,py=self.position
        if px<=x<px+272 and py<=y<py+32:
            if msg==pet._WM_LBUTTONDOWN:
                if x-px>=224:self.command('close')
                elif x-px>=112:self.command('move')
                else:self.window_drag=(x-px,y-py)
            return True
        if self.moving and not self.api._menu_rects:
            rx,ry,rw,rh=self.selection_rect()
            if rx<=x<rx+rw and ry<=y<ry+rh:
                if msg==pet._WM_LBUTTONDOWN:
                    cx=rx+rw/2;cy=ry+rh/2
                    self.drag={'offset':(x-cx,y-cy),'last':(cx,cy)}
                return True
        return False

    @staticmethod
    def paint(hwnd,image,x,y):
        w,h=image.size
        bgra=Image.frombytes('RGBA',image.size,image.convert('RGBa').tobytes()).tobytes('raw','BGRA')
        pet._update_layered(hwnd,bgra,w,h,x,y,bounds=(x,y,x+w,y+h))
        pet._user32.ShowWindow(hwnd,8)

    def present(self):
        if not self.handles:return
        api=self.api
        show=self.enabled and bool(api._w and api._h)
        if not show:
            self.visible=False
            for hwnd in self.handles:pet._user32.ShowWindow(hwnd,0)
            return
        if time.monotonic()-self.updated<1/30:return
        self.updated=time.monotonic();self.visible=True
        if self.manual_position is None:
            self.position=viewer_position((api._x,api._y,api._w,api._h),pet._work_area())
        else:
            (left,top),(right,bottom)=pet._virtual_screen()
            self.position=clamp_viewer_position(self.manual_position,(left,top,right,bottom))
        x,y=self.position
        header=Image.new('RGBA',(272,32),'#faf8fc');draw=ImageDraw.Draw(header)
        draw.text((10,7),'放大镜 · 3×',font=self.font,fill='#34313e')
        draw.rounded_rectangle((112,3,221,29),radius=5,fill='#eadfe8' if self.moving else '#f0eaf1')
        draw.text((124,7),'完成框选' if self.moving else '移动框选',font=self.font,fill='#653a56')
        draw.text((234,7),'关闭',font=self.font,fill='#675763')
        panel=Image.new('RGBA',(272,272),'#faf8fc')
        if self.frame is not None:panel.alpha_composite(self.frame,(8,8))
        else:ImageDraw.Draw(panel).text((80,122),'正在准备画面…',font=self.font,fill='#675763')
        self.paint(self.handles[0],panel,x,y+32);self.paint(self.handles[1],header,x,y)
        if self.moving and not api._menu_rects:
            rx,ry,rw,rh=self.selection_rect();rw=max(4,round(rw));rh=max(4,round(rh))
            rect=Image.new('RGBA',(rw,rh),(250,248,252,2));draw=ImageDraw.Draw(rect)
            draw.rectangle((0,0,rw-1,rh-1),outline='#923660',width=2)
            draw.rectangle((rw*.2,rh*.2,rw*.8,rh*.8),outline=(146,54,96,160),width=1)
            self.paint(self.handles[2],rect,round(rx),round(ry))
        else:pet._user32.ShowWindow(self.handles[2],0)
