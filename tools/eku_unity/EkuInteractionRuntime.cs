using System;
using System.Collections.Generic;
using UnityEngine;

public enum EkuInteractionPhase { Idle,Hover,Press,Hold,Drag,Release,Cancel }
[Serializable] public struct EkuInteractionEvent {
    public int id,priority;
    public string region,type,reason;
    public EkuInteractionPhase phase;
    public bool active,overSurface,approximate;
    public float duration,dwell;
    public Vector3 worldPoint;
    public EkuInputSnapshot input;
}

// Lifecycle fan-out: consumers request poses/effects, without collecting input again.
public sealed class EkuInteractionRuntime {
    readonly EkuInteractionProfile profile;
    readonly Dictionary<string,Action<EkuInteractionEvent>> handlers=new Dictionary<string,Action<EkuInteractionEvent>>();
    EkuInteractionEvent current;
    double stillAt;
    public EkuInteractionEvent Current => current;
    public event Action<EkuInteractionEvent> Changed;
    public event Action<string> InterruptRequested;
    public EkuInteractionRuntime(EkuInteractionProfile profile) {this.profile=profile;}
    public void Register(string type,Action<EkuInteractionEvent> handler) {
        if(string.IsNullOrWhiteSpace(type) || handler==null)throw new ArgumentException("Interaction type and handler are required");
        if(handlers.ContainsKey(type))throw new InvalidOperationException("Interaction already registered: "+type);
        handlers.Add(type,handler);
    }
    public bool Unregister(string type) => handlers.Remove(type);
    void Publish() {
        if(current.type!=null && handlers.TryGetValue(current.type,out var handler))handler(current);
        Changed?.Invoke(current);
    }
    public bool Begin(int id,EkuInteractionRegion region,EkuInputSnapshot input,Vector3 worldPoint) {
        if(region==null || (current.active && current.priority>region.priority))return false;
        if(current.active)End(true,"replaced");
        current=new EkuInteractionEvent{id=id,region=region.id,type=region.type,priority=region.priority,
            input=input,worldPoint=worldPoint,phase=EkuInteractionPhase.Press,active=true,overSurface=true};
        stillAt=input.at;Publish();return current.active && current.id==id;
    }
    public void Update(EkuInputSnapshot input,Vector3 worldPoint,bool overSurface) {
        if(!current.active)return;
        if(input.speedDip>profile.dragSpeedDip)stillAt=input.at;
        current.input=input;current.worldPoint=worldPoint;current.overSurface=overSurface;
        current.duration=input.duration;current.dwell=(float)Math.Max(0,input.at-stillAt);
        current.phase=input.duration<profile.holdSeconds?EkuInteractionPhase.Press:
            input.speedDip>profile.dragSpeedDip?EkuInteractionPhase.Drag:EkuInteractionPhase.Hold;
        Publish();
    }
    public void Hover(EkuInteractionRegion region,EkuInputSnapshot input,Vector3 point) {
        if(current.active)return;
        if(current.region!=(region==null?null:region.id))stillAt=input.at;
        current=new EkuInteractionEvent{region=region==null?null:region.id,type=region==null?null:region.type,
            phase=region==null?EkuInteractionPhase.Idle:EkuInteractionPhase.Hover,input=input,worldPoint=point,
            overSurface=region!=null,approximate=region!=null,dwell=(float)Math.Max(0,input.at-stillAt)};
        Publish();
    }
    public void End(bool cancelled,string reason) {
        if(!current.active)return;
        current.active=false;current.phase=cancelled?EkuInteractionPhase.Cancel:EkuInteractionPhase.Release;
        current.reason=cancelled?reason:"";current.input.leftDown=false;Publish();
    }
    public bool Interrupt(int priority,string reason) {
        if(!current.active || priority<current.priority)return false;
        if(InterruptRequested==null)End(true,reason??"interrupted");
        else InterruptRequested.Invoke(reason??"interrupted");
        return true;
    }
}

// Cached ellipsoid proxies follow animated anchors; Press still uses the real mesh.
public sealed class EkuInteractionHover {
    struct Bound {public Transform anchor;public EkuHoverVolume volume;public Vector3 localCenter;public Quaternion localAxes;}
    readonly Bound[] bounds;
    readonly float height;
    readonly EkuInteractionProfile profile;
    public EkuInteractionHover(Transform avatar,Animator rig,float height,EkuInteractionProfile profile) {
        this.height=height;this.profile=profile;
        var nodes=new Dictionary<string,Transform>();foreach(var node in avatar.GetComponentsInChildren<Transform>(true))nodes[node.name]=node;
        var configured=profile.hover??Array.Empty<EkuHoverVolume>();bounds=new Bound[configured.Length];
        for(int i=0;i<bounds.Length;i++) {
            var volume=configured[i];if(volume==null)continue;
            Transform anchor=string.IsNullOrEmpty(volume.node)?rig.GetBoneTransform(volume.bone):nodes.TryGetValue(volume.node,out var node)?node:null;
            if(!anchor)continue;
            bounds[i]=new Bound{anchor=anchor,volume=volume,localCenter=anchor.InverseTransformVector(avatar.rotation*(volume.center*height)),
                localAxes=Quaternion.Inverse(anchor.rotation)*avatar.rotation};
        }
    }
    public EkuInteractionRegion Pick(Ray ray,out Vector3 point) {
        float nearest=float.MaxValue;EkuInteractionRegion region=null;point=Vector3.zero;
        foreach(var bound in bounds) {
            if(!bound.anchor || !bound.anchor.gameObject.activeInHierarchy)continue;
            var radii=bound.volume.radii*height;if(radii.x<=0 || radii.y<=0 || radii.z<=0)continue;
            var center=bound.anchor.TransformPoint(bound.localCenter);var inverse=Quaternion.Inverse(bound.anchor.rotation*bound.localAxes);
            var origin=inverse*(ray.origin-center);var direction=inverse*ray.direction;
            origin=new Vector3(origin.x/radii.x,origin.y/radii.y,origin.z/radii.z);
            direction=new Vector3(direction.x/radii.x,direction.y/radii.y,direction.z/radii.z);
            float a=Vector3.Dot(direction,direction),b=Vector3.Dot(origin,direction),c=Vector3.Dot(origin,origin)-1;
            float discriminant=b*b-a*c;if(a<.000001f || discriminant<0)continue;
            float distance=(-b-Mathf.Sqrt(discriminant))/a;if(distance<0 || distance>=nearest)continue;
            var candidate=profile.Region(bound.volume.region);if(candidate==null)continue;
            nearest=distance;region=candidate;point=ray.GetPoint(distance);
        }
        return region;
    }
}
