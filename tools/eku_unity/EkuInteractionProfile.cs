using System;
using UnityEngine;

[Serializable] public sealed class EkuInteractionRegion {
    public string id,legacy,type;
    public int priority=100;
    public EkuInteractionRegion(string id,string legacy,string type) {this.id=id;this.legacy=legacy;this.type=type;}
}
[Serializable] public sealed class EkuRegionRule {
    public string region,meshContains,materialContains,nodePrefix,nodeContains;
    public HumanBodyBones bone=HumanBodyBones.LastBone;
}
[Serializable] public sealed class EkuHoverVolume {
    public string region,node;
    public HumanBodyBones bone=HumanBodyBones.Head;
    // Avatar anatomical axes, fractions of character height.
    public Vector3 center,radii;
    public EkuHoverVolume(string region,HumanBodyBones bone,Vector3 center,Vector3 radii,string node=null) {
        this.region=region;this.bone=bone;this.center=center;this.radii=radii;this.node=node;
    }
}
[CreateAssetMenu(menuName="Nyalume/Interaction Profile")]
public sealed class EkuInteractionProfile : ScriptableObject {
    [Min(.02f)] public float holdSeconds=.18f;
    [Min(0)] public float dragSpeedDip=12;
    [Min(.03f)] public float hoverInterval=.1f;
    public EkuInteractionRegion[] regions={
        new EkuInteractionRegion("Head","head","headpat"),new EkuInteractionRegion("Face","face","contact"),
        new EkuInteractionRegion("FaceLeft","face","cheek"),new EkuInteractionRegion("FaceRight","face","cheek"),
        new EkuInteractionRegion("EarLeft","head","headpat"),new EkuInteractionRegion("EarRight","head","headpat"),
        new EkuInteractionRegion("Hand","hand","handshake"),new EkuInteractionRegion("Body","belly","contact"),
        new EkuInteractionRegion("Chest","chest","contact"),new EkuInteractionRegion("Leg","leg","contact"),
        new EkuInteractionRegion("Clothing","cloth","contact"),new EkuInteractionRegion("Skirt","skirt","contact")
    };
    public EkuRegionRule[] rules={
        new EkuRegionRule{region="EarLeft",nodePrefix="Ear",nodeContains=".L"},
        new EkuRegionRule{region="EarRight",nodePrefix="Ear",nodeContains=".R"}
    };
    public EkuHoverVolume[] hover={
        new EkuHoverVolume("Head",HumanBodyBones.Head,new Vector3(0,.04f,0),new Vector3(.065f,.085f,.065f)),
        new EkuHoverVolume("Face",HumanBodyBones.Head,new Vector3(0,.025f,.055f),new Vector3(.045f,.04f,.025f)),
        new EkuHoverVolume("EarLeft",HumanBodyBones.Head,Vector3.zero,new Vector3(.023f,.033f,.023f),"Ear_Root.L"),
        new EkuHoverVolume("EarRight",HumanBodyBones.Head,Vector3.zero,new Vector3(.023f,.033f,.023f),"Ear_Root.R"),
        new EkuHoverVolume("Chest",HumanBodyBones.Chest,Vector3.zero,new Vector3(.095f,.10f,.065f)),
        new EkuHoverVolume("Body",HumanBodyBones.Hips,new Vector3(0,.035f,0),new Vector3(.10f,.095f,.065f)),
        new EkuHoverVolume("Hand",HumanBodyBones.LeftHand,Vector3.zero,new Vector3(.028f,.035f,.025f)),
        new EkuHoverVolume("Hand",HumanBodyBones.RightHand,Vector3.zero,new Vector3(.028f,.035f,.025f))
    };
    public EkuInteractionRegion Region(string id) {
        if(regions!=null)foreach(var region in regions)if(region!=null && region.id==id)return region;
        return null;
    }
    public EkuInteractionRegion Legacy(string legacy) {
        if(regions!=null)foreach(var region in regions)if(region!=null && region.legacy==legacy)return region;
        return null;
    }
    static bool Contains(string source,string match) => string.IsNullOrEmpty(match) || (source!=null && source.IndexOf(match,StringComparison.OrdinalIgnoreCase)>=0);
    public EkuInteractionRegion Resolve(string mesh,string material,Transform node,HumanBodyBones human) {
        if(rules==null)return null;
        foreach(var rule in rules) {
            if(rule==null || (rule.bone!=HumanBodyBones.LastBone && rule.bone!=human)
                || !Contains(mesh,rule.meshContains) || !Contains(material,rule.materialContains))continue;
            if(!string.IsNullOrEmpty(rule.nodeContains) || !string.IsNullOrEmpty(rule.nodePrefix)) {
                bool matched=false;
                for(var current=node;current;current=current.parent) {
                    if(Contains(current.name,rule.nodeContains) && (string.IsNullOrEmpty(rule.nodePrefix)
                        || current.name.StartsWith(rule.nodePrefix,StringComparison.OrdinalIgnoreCase))) {matched=true;break;}
                }
                if(!matched)continue;
            }
            var region=Region(rule.region);if(region!=null)return region;
        }
        return null;
    }
}
