using System;
using System.IO.MemoryMappedFiles;
using UnityEngine;

// Read-only observer of the desktop command slot. It records which camera render
// first includes each accepted command; it never changes animation or bridge state.
public class EkuRenderProbe : MonoBehaviour {
    MemoryMappedFile commands,trace;
    MemoryMappedViewAccessor control,records;
    int last;
    void Start() {
        var args=Environment.GetCommandLineArgs();
        int option=Array.IndexOf(args,"--control");
        if(option<0 || option+1>=args.Length) return;
        string tag=args[option+1];
        commands=MemoryMappedFile.OpenExisting(tag);
        control=commands.CreateViewAccessor(0,64,MemoryMappedFileAccess.Read);
        trace=MemoryMappedFile.CreateOrOpen(tag+"-render",64+256*16);
        records=trace.CreateViewAccessor();
    }
    void OnPostRender() {
        if(control==null) return;
        int ack=control.ReadInt32(12);
        if(ack<=last) return;
        int offset=64+(ack%256)*16;
        records.Write(offset+12,0);
        records.Write(offset,Time.realtimeSinceStartupAsDouble);
        records.Write(offset+8,ack);
        records.Write(offset+12,ack);
        last=ack;
    }
    void OnDestroy() {
        control?.Dispose();commands?.Dispose();records?.Dispose();trace?.Dispose();
    }
}
