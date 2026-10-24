using System;
using System.Linq;
using UnityEngine;

// Separate from the animation controller; only the sock renderer is replaced.
[DefaultExecutionOrder(100)]
public class EkuStockings : MonoBehaviour {
    public Renderer socks;
    public Texture2D fabric,skin,density,weave;
    public Shader shader;
    public bool silk;
    Material[] original;
    Material surface;
    bool applied;
    public bool Applied => applied;
    void Start() {
        var args=Environment.GetCommandLineArgs();
        int option=Array.IndexOf(args,"--camera-target-y");
        if(option>=0 && option+1<args.Length) {
            float y=float.Parse(args[option+1],System.Globalization.CultureInfo.InvariantCulture);
            UnityEngine.Object.FindFirstObjectByType<EkuComparison>().view.transform.LookAt(new Vector3(0,y,0));
        }
    }
    void Awake() {
        var args=Environment.GetCommandLineArgs();
        int missing=Array.IndexOf(args,"--stocking-missing-map");
        if(missing>=0 && missing+1<args.Length) {
            switch(args[missing+1]) {
                case "fabric":fabric=null;break;case "skin":skin=null;break;
                case "density":density=null;break;case "weave-normal":weave=null;break;
            }
        }
        if(!socks || !fabric || !skin || !density || !weave || !shader || !shader.isSupported) return;
        int option=Array.IndexOf(args,"--stockings");
        if(option>=0 && option+1<args.Length && args[option+1]=="0") return;
        surface=new Material(shader){name="E_Costume_stockings"};
        surface.SetTexture("_MainTex",fabric);surface.SetTexture("_SkinTex",skin);
        surface.SetTexture("_DensityTex",density);surface.SetTexture("_BumpMap",weave);
        surface.SetFloat("_Silk",silk?1:0);
        int weaveOption=Array.IndexOf(args,"--stocking-weave-strength");
        if(weaveOption>=0 && weaveOption+1<args.Length)surface.SetFloat("_WeaveStrength",
            Mathf.Clamp01(float.Parse(args[weaveOption+1],System.Globalization.CultureInfo.InvariantCulture)));
        // Internal capture controls; Eku exposes no material/style selector.
        foreach(var parameter in new[]{"Ambient","Specular"}) {
            option=Array.IndexOf(args,"--stocking-"+parameter.ToLowerInvariant());
            if(option>=0 && option+1<args.Length) surface.SetFloat("_Silk"+parameter,
                Mathf.Clamp01(float.Parse(args[option+1],System.Globalization.CultureInfo.InvariantCulture)));
        }
        option=Array.IndexOf(args,"--stocking-debug");
        if(option>=0 && option+1<args.Length) surface.SetFloat("_DebugView",int.Parse(args[option+1]));
        SetEnabled(true);
    }
    public void SetEnabled(bool value) {
        if(!socks || !surface) return;
        if(applied && !value) socks.sharedMaterials=original;
        else if(!applied && value) {
            original=socks.sharedMaterials;
            socks.sharedMaterials=original.Select(_=>surface).ToArray();
        }
        applied=value;
    }
    void OnDestroy() {
        SetEnabled(false);
        if(surface) Destroy(surface);
        // Imported maps are shared assets owned by Unity, not runtime allocations.
    }
}
