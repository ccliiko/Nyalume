"""Selection dragging must never fall through to pet touch handling."""
from types import SimpleNamespace
from PIL import Image
from nyalume.frontends.pet.pet3d.unity_magnifier import Magnifier,viewer_position
from nyalume.frontends.pet.pet3d import pet3d_win as pet


def test_selection_drag_and_mode_switch(monkeypatch):
    monkeypatch.setattr(pet._user32,'GetAsyncKeyState',lambda key:0)
    api=SimpleNamespace(_x=100,_y=100,_w=440,_h=660,_menu_rects=[],_finish_left=lambda **kw:None)
    lens=Magnifier(api)
    lens.command('move')
    assert lens.poll()=={'kind':'magnifier','value':True}
    assert lens.poll()=={'kind':'magnifier_move','value':True}
    lens.visible=True;lens.position=(800,100)
    lens.region=(.5,.25,256/(440*3),256/(660*3))
    x,y=320,265
    assert lens.event(pet._WM_LBUTTONDOWN,x+10,y)
    assert lens.event(pet._WM_MOUSEMOVE,x+60,y+66)
    assert lens.event(pet._WM_LBUTTONUP,x+60,y+66)
    action=lens.poll()
    assert action['phase']=='end' and abs(action['x']-(.5+50/440))<1e-8
    assert abs(action['y']-.35)<1e-8 and not lens.drag
    lens.command('move');assert lens.poll()=={'kind':'magnifier_move','value':False}
    lens.requested_center=(.5,.5)
    lens.receive(Image.new('RGBA',(256,256)),(.6,.6,.2,.2))
    assert lens.region==(.6,.6,.2,.2) and lens.requested_center is None
    assert not lens.event(pet._WM_LBUTTONDOWN,x,y)
    assert not lens.event(pet._WM_LBUTTONDOWN,900,200)  # Image passes clicks through.
    lens.command('close');assert lens.poll()=={'kind':'magnifier','value':False}


def test_viewer_stays_inside_work_area():
    assert viewer_position((100,100,440,660),(0,0,1920,1040))==(552,100)
    assert viewer_position((1500,900,440,660),(0,0,1920,1040))==(1216,736)
    assert viewer_position((-1800,-50,440,660),(-1920,0,0,1040))==(-1348,0)


def test_window_drag_pins_position_and_menu_keeps_viewer(monkeypatch):
    monkeypatch.setattr(pet,'_work_area',lambda:(0,0,1920,1040))
    monkeypatch.setattr(pet,'_virtual_screen',lambda:((-1920,0),(1920,1080)))
    monkeypatch.setattr(pet._user32,'ShowWindow',lambda *args:None)
    api=SimpleNamespace(_x=100,_y=100,_w=440,_h=660,_menu_rects=[{'id':'menu'}])
    lens=Magnifier(api);lens.enabled=True;lens.handles=[1,2,3]
    painted=[];monkeypatch.setattr(lens,'paint',lambda handle,image,x,y:painted.append((handle,x,y)))
    lens.present();assert lens.visible
    px,py=lens.position
    assert lens.event(pet._WM_LBUTTONDOWN,px+40,py+12)
    assert lens.event(pet._WM_MOUSEMOVE,px-260,py+112)
    assert lens.event(pet._WM_LBUTTONUP,px-260,py+112)
    assert lens.manual_position==(px-300,py+100)
    pinned=lens.position;api._x+=300;lens.updated=0;lens.present()
    assert lens.visible and lens.position==pinned and lens.selection_command is None


def test_both_renderers_share_menu_and_commands(monkeypatch,tmp_path):
    from nyalume.frontends.pet.pet3d.unity_pet import UnityApi
    monkeypatch.setattr(pet,'_load_settings',lambda:{})
    monkeypatch.setattr(pet,'_PetState',lambda:None)
    for kind in (pet._NativeApi,UnityApi):
        api=kind(enabled=False);api.magnifier=Magnifier(api)
        assert any(item['id']=='page:magnifier' for item in api.menu_items())
        api._menu_page='magnifier'
        assert any(item['id']=='magnifier:toggle' for item in api.menu_items())
        assert api.pet_command({'action':'magnifier','name':'toggle'})['enabled']
        assert not api.pet_command({'action':'magnifier','name':'invalid'})['ok']
