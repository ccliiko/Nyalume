using System;
using System.IO.MemoryMappedFiles;
using System.IO;
using System.Linq;
using System.Text;
using UnityEngine;

[DefaultExecutionOrder(-100)]
public class EkuMaterialStyle : MonoBehaviour {
    [Serializable] public class Slot {public Renderer renderer;public Material[] source,enhanced;}
    [Serializable] class Action {public string kind;public int index,step;}
    public Slot[] slots;
    public EkuStockings stockings;
    public EkuColorGrade grade;
    public int Index {get;private set;}
    MemoryMappedFile mapping;
    MemoryMappedViewAccessor control;
    int observed;
    readonly byte[] bytes=new byte[32768];
    [Serializable] class State {public int index;public bool stockings;public MaterialState[] materials;}
    [Serializable] class MaterialState {public string mesh,shader,instance;public int queue;}
    void Start() {
        var args=Environment.GetCommandLineArgs();
        int option=Array.IndexOf(args,"--style");
        Apply(option>=0 && option+1<args.Length?int.Parse(args[option+1]):0);
        option=Array.IndexOf(args,"--control");
        if(option>=0 && option+1<args.Length) {
            mapping=MemoryMappedFile.OpenExisting(args[option+1]);
            control=mapping.CreateViewAccessor(0,32832,MemoryMappedFileAccess.Read);
        }
    }
    void LateUpdate() {
        if(control==null) return;
        int seq=control.ReadInt32(0),length=control.ReadInt32(4);
        if(seq==observed || length<1 || length>bytes.Length) return;
        control.ReadArray(64,bytes,0,length);
        if(control.ReadInt32(0)!=seq) return;
        observed=seq;
        var action=JsonUtility.FromJson<Action>(Encoding.UTF8.GetString(bytes,0,length));
        if(action.kind=="style") Apply(action.index);
        else if(action.kind=="style_step") Apply(Index+action.step);
    }
    public void Apply(int index) {
        Index=(index%5+5)%5;
        stockings.SetEnabled(false);
        foreach(var slot in slots) slot.renderer.sharedMaterials=Index==4?slot.source:slot.enhanced;
        if(Index!=4) stockings.SetEnabled(true);
        grade.SetStyle(Index);
        File.WriteAllText(Path.Combine(Application.dataPath,"../material-state.json"),
            JsonUtility.ToJson(new State{index=Index,stockings=stockings.Applied,
                materials=slots.SelectMany(slot=>slot.renderer.sharedMaterials.Select(mat=>new MaterialState{
                    mesh=slot.renderer.name,shader=mat.shader.name,instance=mat.GetEntityId().ToString(),queue=mat.renderQueue})).ToArray()},true));
    }
    void OnDestroy() {control?.Dispose();mapping?.Dispose();}
}
