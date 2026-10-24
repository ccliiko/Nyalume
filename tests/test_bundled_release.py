import importlib.machinery
import importlib.util
from pathlib import Path
import sys

from nyalume.frontends.pet.pet3d import model_profile, pet3d_win as pet, unity_pet
from tools import build_eku_release


def test_native_authenticated_bundle(tmp_path,monkeypatch):
    monkeypatch.setattr(build_eku_release,'OUTPUT',tmp_path)
    content=b'UnityFS\0'+bytes(range(256))*8
    digest=build_eku_release.encrypt_content(content,tmp_path/'player')
    assert len(digest)==64
    assert not (tmp_path/'eku_decoder.c').exists()
    ciphertext=(tmp_path/'player/EkuPlayer_Data/StreamingAssets/eku.bin').read_bytes()
    assert ciphertext.startswith(b'NYEKU001') and content not in ciphertext


def test_bundled_selection_and_user_models(tmp_path,monkeypatch):
    monkeypatch.setattr(pet,'_app_root',lambda:str(tmp_path))
    player=tmp_path/'unity/EkuPlayer.exe';player.parent.mkdir();player.write_bytes(b'test')
    miku=tmp_path/'models/Miku';miku.mkdir(parents=True);(miku/'Miku.pmx').write_bytes(b'test')
    assert pet._pick_default_model({})==pet.BUNDLED_EKU
    assert pet._resolve_model(pet.BUNDLED_EKU)==(str(tmp_path/'models'),pet.BUNDLED_EKU)
    assert unity_pet.supports_model(pet.BUNDLED_EKU)
    assert pet._pick_default_model({'last_model':str(miku)})==str(miku)
    api=pet._NativeApi.__new__(pet._NativeApi)
    api._model_file=pet.BUNDLED_EKU;api._model_profile=model_profile.defaults()
    api.load_library('',str(tmp_path/'models'))
    assert api._models==[('Eku（内置）',pet.BUNDLED_EKU),('Miku',str(miku))]
    assert api._model_index==0


def test_3d_executable_and_child_dispatch(monkeypatch):
    root=Path(__file__).resolve().parents[1]
    loader=importlib.machinery.SourceFileLoader('release_entry',str(root/'run_pet.pyw'))
    module=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader))
    loader.exec_module(module)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(root/'Nyalume3D.exe'))
    seen=[]
    monkeypatch.setattr(module,'_run_frozen_child',lambda:seen.append(list(sys.argv)) or True)
    monkeypatch.setattr(sys,'argv',['Nyalume3D.exe'])
    module.main()
    assert seen[-1][1:]==['--pet3d','--model',pet.BUNDLED_EKU,'--renderer','unity']
    monkeypatch.setattr(sys,'argv',['Nyalume3D.exe','--web-server','--port','8000'])
    module.main()
    assert seen[-1][1:]==['--web-server','--port','8000']
