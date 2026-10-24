"""Serialize Eku validation jobs, including after an interrupted run or reboot."""
import argparse
from contextlib import contextmanager
import ctypes
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'outputs/eku-natural-20261013'


@contextmanager
def gpu_validation():
    kernel=ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes=[ctypes.c_void_p]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateMutexW(None,False,'Local\\NyalumeEkuGpuValidation')
    if not handle:raise ctypes.WinError()
    try:
        waiting=False
        while True:
            result=kernel.WaitForSingleObject(handle,1000)
            if result in (0,0x80):break  # Abandoned mutex is recoverable after a crash.
            if result!=258:raise ctypes.WinError()
            if not waiting:print('Waiting for the previous Eku validation process to exit...',flush=True);waiting=True
        try:yield
        finally:kernel.ReleaseMutex(handle)
    finally:kernel.CloseHandle(handle)


def run_unity(operation,editor):
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    if operation=='build':
        args=[str(editor),'-batchmode','-nographics','-quit','-job-worker-count','2',
            '-projectPath',str(OUTPUT/'unity'),'-executeMethod','BuildEkuComparison.Build',
            '-logFile',str(OUTPUT/'unity-build.log')]
    else:
        args=[str(OUTPUT/'player/EkuComparison.exe'),'-batchmode','-force-d3d11',
            '--capture','2' if operation=='dynamic' else '1','--output',str(OUTPUT/'detail') if operation=='detail' else str(OUTPUT),
            '-logFile',str(OUTPUT/f'unity-{operation}.log')]
        if operation=='detail':args+=['--detail','1']
    with gpu_validation():
        result=subprocess.run(args,startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise SystemExit(f'Unity {operation} failed ({result.returncode}); inspect its log')
    print(f'Unity {operation} completed',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['build','static','dynamic','detail'])
    parser.add_argument('--editor',type=Path,default=Path(r'D:\download\Unity\Hub\Editor\6000.6.5f1\Editor\Unity.exe'))
    args=parser.parse_args();run_unity(args.operation,args.editor)
