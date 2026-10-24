using System;
using System.Linq;
using UnityEngine;
using VRM;

[Serializable] public class EkuOutfitParts {
    public bool jacket=true,dress=true,spats=true,socks=true,shoes=true;
}
[Serializable] public class EkuWardrobeState {
    public string outfit="casual";
    public EkuOutfitParts casual=new EkuOutfitParts(),uniform=new EkuOutfitParts();
}
[Serializable] public class EkuWardrobeStatus {
    public int version=1,revision;
    public EkuWardrobeState state;
    public string[] visible;
    public string body;
    public int bodyVertices;
    public int uniformContacts;
    public string[] meshInstances,materialInstances;
}

// Only wardrobe properties are written here; facial/humanoid animation stays independent.
public class EkuWardrobe : MonoBehaviour {
    public SkinnedMeshRenderer body;
    public SkinnedMeshRenderer bareBody,sockBody;
    public Mesh dressedBody,completeBody,completeSockBody;
    public Renderer bra,underwear,jacket,dress,spats,socks,shoes;
    public Renderer uniformJacket,blouse,blouseArm,uniformInner,boots;
    public EkuStockings stockings;
    public VRMSpringBone uniformPhysics,uniformSleevePhysics;
    public EkuUniformCollision uniformSurface;
    public EkuWardrobeState State {get;private set;}=new EkuWardrobeState();
    public int Revision {get;private set;}
    public int UniformContacts => uniformSurface?uniformSurface.Contacts:0;
    public SkinnedMeshRenderer ActiveBody {get;private set;}
    public Renderer[] Clothing => new[]{jacket,dress,spats,socks,shoes,uniformJacket,blouse,blouseArm,uniformInner,boots};
    static void Visible(Renderer renderer,bool value) {if(renderer) renderer.enabled=value;}
    static void Shape(SkinnedMeshRenderer renderer,string name,float value) {
        int index=renderer.sharedMesh.GetBlendShapeIndex(name);
        if(index>=0)renderer.SetBlendShapeWeight(index,value);
    }
    public void Apply(EkuWardrobeState state,int revision=0) {
        if(state==null || state.casual==null || state.uniform==null || (state.outfit!="casual" && state.outfit!="uniform"))
            throw new ArgumentException("Invalid wardrobe state");
        bool casual=state.outfit=="casual";
        var parts=casual?state.casual:state.uniform;
        // Base coverage is enabled first, before any clothing disappears.
        Visible(bra,true);Visible(underwear,true);
        bool whiteSocks=casual && parts.socks;
        // Unity 6 caches GPU skinning buffers by renderer. Fixed meshes avoid
        // stale vertex strides when switching between the two body topologies.
        ActiveBody=casual && parts.dress && parts.spats && parts.socks && parts.shoes
            ?body:whiteSocks?sockBody:bareBody;
        foreach(var renderer in new[]{body,bareBody,sockBody})Visible(renderer,renderer==ActiveBody);
        ActiveBody.sharedMaterials=body.sharedMaterials;
        for(int i=0;i<ActiveBody.sharedMesh.blendShapeCount;i++)ActiveBody.SetBlendShapeWeight(i,0);
        if(ActiveBody!=body) {
            Shape(ActiveBody,"Foot_heels",casual && parts.shoes?100:0);
        }
        Visible(jacket,casual && parts.jacket);Visible(dress,casual && parts.dress);
        Visible(spats,casual && parts.spats);Visible(socks,whiteSocks);Visible(shoes,casual && parts.shoes);
        Visible(uniformJacket,!casual && parts.jacket);
        Visible(blouse,!casual && parts.dress);Visible(blouseArm,!casual && parts.dress && !parts.jacket);
        Visible(uniformInner,!casual && parts.dress);Visible(boots,!casual && parts.shoes);
        if(uniformPhysics)uniformPhysics.enabled=!casual && parts.dress;
        if(uniformSleevePhysics)uniformSleevePhysics.enabled=!casual && parts.jacket;
        Shape((SkinnedMeshRenderer)blouse,"Shrink_Sleeve",parts.jacket?100:0);
        Shape((SkinnedMeshRenderer)socks,"Foot_heels",casual && parts.shoes?100:0);
        State=JsonUtility.FromJson<EkuWardrobeState>(JsonUtility.ToJson(state));Revision=revision;
    }
    public EkuWardrobeStatus Status() {
        var renderers=Clothing.Concat(new[]{bra,underwear,body,bareBody,sockBody}).Where(r=>r).ToArray();
        return new EkuWardrobeStatus{revision=Revision,state=State,
            visible=Clothing.Concat(new[]{bra,underwear}).Where(r=>r && r.enabled).Select(r=>r.name).ToArray(),
            body=ActiveBody.sharedMesh.name,bodyVertices=ActiveBody.sharedMesh.vertexCount,
            uniformContacts=UniformContacts,
            meshInstances=renderers.OfType<SkinnedMeshRenderer>().Select(r=>r.sharedMesh.GetEntityId().ToString())
                .Concat(uniformSurface && uniformSurface.Surface?new[]{uniformSurface.Surface.GetEntityId().ToString()}:Array.Empty<string>()).ToArray(),
            materialInstances=renderers.SelectMany(r=>r.sharedMaterials).Select(m=>m.GetEntityId().ToString()).Distinct().ToArray()};
    }
}
