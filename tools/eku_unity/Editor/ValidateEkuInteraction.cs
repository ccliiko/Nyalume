using System;
using UnityEngine;

public static class ValidateEkuInteraction {
    static void Require(bool condition,string label) {if(!condition)throw new Exception("Interaction: "+label);}
    public static void Run() {
        var profile=ScriptableObject.CreateInstance<EkuInteractionProfile>();
        var avatar=new GameObject("Interaction validation avatar");
        try {
            var runtime=new EkuInteractionRuntime(profile);int updates=0;
            runtime.Register("custom",_=>updates++);
            var definition=new EkuInteractionRegion("CustomRegion","head","custom"){priority=100};
            var input=new EkuInputSnapshot{at=10,valid=true,leftDown=true};
            Require(runtime.Begin(1,definition,input,Vector3.one) && runtime.Current.phase==EkuInteractionPhase.Press,"registered type receives Press");
            input.at=10.3;input.duration=.3f;runtime.Update(input,Vector3.one,true);
            Require(runtime.Current.phase==EkuInteractionPhase.Hold && runtime.Current.dwell>.29f,"static hold has dwell");
            input.at=10.4;input.speedDip=50;runtime.Update(input,Vector3.one,true);
            Require(runtime.Current.phase==EkuInteractionPhase.Drag && runtime.Current.dwell==0,"motion enters Drag without a second input sampler");
            Require(!runtime.Interrupt(20,"ambient") && runtime.Current.active,"low priority cannot interrupt direct contact");
            Require(!runtime.Begin(2,new EkuInteractionRegion("Other","head","other"){priority=50},input,Vector3.zero),"low priority begin cannot replace captured contact");
            Require(runtime.Interrupt(1000,"menu") && !runtime.Current.active && runtime.Current.reason=="menu","control cancels captured type");
            Require(updates==4,"subscriber sees one Press Hold Drag Cancel sequence");
            runtime.Register("cancel-on-begin",value=>{if(value.phase==EkuInteractionPhase.Press)runtime.Interrupt(1000,"listener");});
            Require(!runtime.Begin(2,new EkuInteractionRegion("Rejected","head","cancel-on-begin"),input,Vector3.zero)
                && !runtime.Current.active,"synchronous higher priority cancellation cannot resurrect a session");
            runtime.Begin(3,definition,input,Vector3.one);runtime.End(false,"");
            Require(runtime.Current.phase==EkuInteractionPhase.Release && runtime.Current.worldPoint==Vector3.one,"release retains contact geometry");
            var ear=new GameObject("Ear_Root.L");ear.transform.SetParent(avatar.transform);
            Require(profile.Resolve("Body","skin",ear.transform,HumanBodyBones.Head)?.id=="EarLeft","configured ear rule resolves before compatibility mapping");
            profile.rules=new[]{new EkuRegionRule{region="Body",meshContains="custom",materialContains="skin"}};
            Require(profile.Resolve("CustomMesh","Skin",ear.transform,HumanBodyBones.Head)?.id=="Body","custom mesh/material region uses data only");
            Require(profile.Resolve("Other","Skin",ear.transform,HumanBodyBones.Head)==null,"partial rule mismatch is not a false hit");
            profile.hover=new[]{new EkuHoverVolume("Head",HumanBodyBones.Head,Vector3.zero,Vector3.one*.5f,"Ear_Root.L")};
            var hover=new EkuInteractionHover(avatar.transform,null,1,profile);
            Require(hover.Pick(new Ray(new Vector3(0,0,-3),Vector3.forward),out var hit)?.id=="Head" && Mathf.Abs(hit.z+.5f)<.001f,"hover proxy uses a geometric ray intersection");
            ear.transform.position=Vector3.right*2;
            Require(hover.Pick(new Ray(new Vector3(0,0,-3),Vector3.forward),out hit)==null
                && hover.Pick(new Ray(new Vector3(2,0,-3),Vector3.forward),out hit)!=null,"cached hover proxy follows animation anchors");
            runtime.Begin(4,definition,input,Vector3.zero);
            for(int i=0;i<20;i++)runtime.Update(input,Vector3.zero,true);
            long before=GC.GetAllocatedBytesForCurrentThread();
            for(int i=0;i<10000;i++)runtime.Update(input,Vector3.zero,true);
            Require(GC.GetAllocatedBytesForCurrentThread()==before,"warmed lifecycle dispatch allocates no per-sample memory");
            Debug.Log("EKU_INTERACTION_VALIDATED checks=15");
        } finally {UnityEngine.Object.DestroyImmediate(avatar);UnityEngine.Object.DestroyImmediate(profile);}
    }
}
