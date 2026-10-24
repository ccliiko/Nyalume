using System;
using System.Linq;
using UnityEngine;

[Serializable] public class EkuAnimationCurve {
    public string path,property;
    public int muscle=-1;
    public AnimationCurve curve;
    public Quaternion parentRest=Quaternion.identity,boneRest=Quaternion.identity;
}
[Serializable] public class EkuAnimationClip {
    public string name,group;
    public float duration;
    public EkuAnimationCurve[] curves;
    public float Value(string property,float time,float fallback=0) {
        var binding=curves.FirstOrDefault(c=>c.property==property);
        return binding==null?fallback:binding.curve.Evaluate(time);
    }
}
public sealed class EkuAnimationLibrary : ScriptableObject {
    public EkuAnimationClip[] clips;
    public EkuAnimationClip Find(string group,string name) => clips.FirstOrDefault(c=>c.group==group && c.name==name);
}
