"""Build an encrypted Unity Eku player from the completed combined scene."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.eku_validation import gpu_validation

ROOT=Path(__file__).resolve().parents[1]
MAGIC=b'NYEKU001'
OUTPUT=ROOT/'build/eku-release'
SOURCE=Path(os.environ.get('LOCALAPPDATA',''))/'NyalumeEkuCombinedCandidate/unity'
SCENE='Assets/EkuCombinedComparison.unity'
LICENSE=ROOT/'outputs/eku-license/Eku_vn3license_v1_2_0_ja.pdf'
if not LICENSE.is_file():
    LICENSE=ROOT/'release/Nyalume-v0.5.0-windows-x64/unity/Eku-LICENSE-ja.pdf'


def encrypt_content(content,player):
    key=secrets.token_bytes(32);nonce=secrets.token_bytes(12)
    sealed=AESGCM(key).encrypt(nonce,content,MAGIC)
    data=MAGIC+nonce+sealed[-16:]+sealed[:-16]
    destination=player/'EkuPlayer_Data/StreamingAssets'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'eku.bin').write_bytes(data)
    native=player/'EkuPlayer_Data/Plugins/x86_64'
    native.mkdir(parents=True,exist_ok=True)
    template=(ROOT/'packaging/eku_decoder.c.tmpl').read_text(encoding='utf8')
    source=OUTPUT/'eku_decoder.c'
    source.write_text(template.replace('@KEY_BYTES@',','.join(str(v) for v in key)),encoding='utf8')
    dll=native/'eku_decoder.dll'
    try:
        subprocess.run([r'E:\mingw64\bin\gcc.exe','-shared','-O2','-s','-static-libgcc',
                        str(source),'-o',str(dll),'-lbcrypt'],check=True,capture_output=True)
    finally:source.unlink(missing_ok=True)
    # Check native CNG authentication against the exact bundle before shipping.
    decoder=ctypes.CDLL(str(dll),winmode=0x1100)
    decoder.eku_decode.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
    decoder.eku_decode.restype=ctypes.c_int
    output=ctypes.create_string_buffer(len(content))
    assert decoder.eku_decode(data,len(data),output,len(content))==len(content)
    assert hashlib.sha256(output.raw).digest()==hashlib.sha256(content).digest()
    for offset in (0,8,20,len(data)-1):
        changed=bytearray(data);changed[offset]^=1
        assert decoder.eku_decode(bytes(changed),len(data),output,len(content))<0
        assert not any(output.raw)
    return hashlib.sha256(content).hexdigest()


def build():
    assert (SOURCE/SCENE).exists(),'Completed Eku Unity scene missing'
    assert LICENSE.is_file(),'Eku license missing; use --license with the saved original PDF'
    OUTPUT.mkdir(parents=True,exist_ok=True)
    project=OUTPUT/'unity'
    with gpu_validation():
        for folder in ('Assets','Packages','ProjectSettings','Library'):
            shutil.copytree(SOURCE/folder,project/folder,dirs_exist_ok=True)
        manifest_path=project/'Packages/manifest.json'
        packages=json.loads(manifest_path.read_text(encoding='utf8'))
        packages['dependencies']['com.unity.modules.assetbundle']='1.0.0'
        manifest_path.write_text(json.dumps(packages,indent=2),encoding='utf8')
        for name in ('EkuBundledStartup.cs','Editor/BuildEkuRelease.cs'):
            shutil.copy2(ROOT/'tools/eku_unity'/name,project/'Assets'/name)
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        subprocess.run([r'D:\download\Unity\Hub\Editor\6000.6.5f1\Editor\Unity.exe',
            '-batchmode','-nographics','-quit','-job-worker-count','2','-projectPath',str(project),
            '-executeMethod','BuildEkuRelease.Build','--release-root',str(OUTPUT),
            '--release-scene',SCENE,
            '-logFile',str(OUTPUT/'unity-build.log')],check=True,
            startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
    bundle=OUTPUT/'bundles/eku.content'
    player=OUTPUT/'player'
    digest=encrypt_content(bundle.read_bytes(),player)
    finish_release(project,player,digest,LICENSE)
    print('Encrypted Unity player ready:',player,flush=True)


def finish_release(project,player,digest,license_path):
    """Attach the original license and metadata to the authenticated player."""
    # Only display/camera/bone pose data; no meshes, textures or model bytes.
    pose=json.loads((ROOT/'outputs/eku-natural-20261013/preferred/comparison.json').read_text(encoding='utf8'))
    pose={name:pose[name] for name in ('height','modelPosition','camera','fov','bones','width','heightPx')}
    (player/'pose.json').write_text(json.dumps(pose,separators=(',',':')),encoding='utf8')
    shutil.copy2(license_path,player/'Eku-LICENSE-ja.pdf')
    notices=player/'licenses';notices.mkdir(exist_ok=True)
    for name,owner in [('UniVRM',project/'Packages'),('lilToon',project/'Assets/lilToon')]:
        candidates=list(owner.rglob('LICENSE*'))
        for index,path in enumerate(p for p in candidates if p.is_file() and p.suffix!='.meta'):
            shutil.copy2(path,notices/f'{name}-{index}-{path.name}')
    (player/'manifest.json').write_text(json.dumps({
        'name':'Eku','renderer':'unity','encrypted':True,'format':'NYEKU001',
        'content_sha256':digest,'unity':'6000.6.5f1',
        'source':'https://septem47.booth.pm/items/7328764',
        'credit':'Eku © sep-neko-ya / septem47; Arisaka Mito; Chocoan',
        'license_sha256':hashlib.sha256(license_path.read_bytes()).hexdigest()},indent=2),encoding='utf8')
    if SCENE=='Assets/EkuWardrobe.unity':
        (player/'wardrobe.json').write_text('{"version":1}',encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--scene',default=SCENE)
    parser.add_argument('--license',type=Path,default=LICENSE)
    args=parser.parse_args()
    OUTPUT=args.output.resolve();SOURCE=args.source.resolve();SCENE=args.scene;LICENSE=args.license.resolve()
    build()
