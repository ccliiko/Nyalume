using System;
using UnityEngine;

// Wire coordinates: physical desktop pixels, top-left normalized canvas, DIP/s motion.
[Serializable] public sealed class EkuMouseSample {
    public int version,mappingRevision,interactionId;
    public long sequence;
    public double timestamp;
    public string sessionId,source,interactionMode;
    public Vector2 screenPosition,effectiveScreenPosition,windowPosition,windowSize,velocity,direction,lookPosition;
    public float age,dpiScale,speed,pathDistance,pathTotal,motionSpeed,stationaryDuration,distanceToCharacter;
    public bool positionValid,insideViewport,isPressed,isMoving,allowAttention,forcedLook;
}

// A value copy is exposed to consumers: no behavior mutates the shared input sample.
[Serializable] public struct EkuMouseTargetSnapshot {
    public string sessionId,source,interactionMode,projectionMode,invalidReason;
    public long sequence;
    public double timestamp;
    public int mappingRevision,interactionId;
    public Vector2 screenPosition,effectiveScreenPosition,windowPosition,windowSize,velocity,direction,lookPosition;
    public Vector3 worldPosition,contactLocalPoint;
    public float age,dpiScale,speed,pathDistance,pathTotal,motionSpeed,stationaryDuration;
    public float distanceToCharacter,distanceToHead,distanceToFace;
    public bool valid,positionValid,insideViewport,isPressed,isMoving,allowAttention,forcedLook;
}

public sealed class EkuMouseTarget {
    readonly Camera view;
    readonly Transform avatar,head;
    readonly float height;
    readonly Vector3 headPlaneLocal,faceLocalOffset;
    readonly double timeout;
    EkuMouseSample input;
    double receivedAt;
    bool touching;
    int contactRevision;
    long hitSequence;
    Vector3 contactPoint,contactNormal;
    Transform contactAnchor;
    public EkuMouseTargetSnapshot Snapshot { get; private set; }
    public bool HasSample => input!=null;

    public EkuMouseTarget(Camera view,Transform avatar,Transform head,float height,double timeout=.25) {
        this.view=view;this.avatar=avatar;this.head=head;this.height=height;this.timeout=timeout;
        if(head && avatar && view) {
            headPlaneLocal=avatar.InverseTransformPoint(head.position+Vector3.up*height*.045f);
            faceLocalOffset=head.InverseTransformVector(view.transform.up*height*.015f-view.transform.forward*height*.04f);
        }
    }
    static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    static bool Finite(Vector2 value) => Finite(value.x) && Finite(value.y);
    static bool Finite(Vector3 value) => Finite(value.x) && Finite(value.y) && Finite(value.z);
    public bool Receive(EkuMouseSample sample,double now) {
        if(sample==null || sample.version!=1 || string.IsNullOrEmpty(sample.sessionId) || sample.sequence<=0
            || double.IsNaN(sample.timestamp) || double.IsInfinity(sample.timestamp)) return false;
        if(input!=null && input.sessionId==sample.sessionId &&
            (sample.sequence<=input.sequence || sample.timestamp<=input.timestamp)) return false;
        if(!Finite(sample.windowPosition) || !Finite(sample.screenPosition) || !Finite(sample.effectiveScreenPosition)
            || !Finite(sample.windowSize) || !Finite(sample.velocity) || !Finite(sample.direction)
            || !Finite(sample.lookPosition) || !Finite(sample.dpiScale) || sample.dpiScale<=0
            || !Finite(sample.speed) || !Finite(sample.pathDistance) || !Finite(sample.pathTotal)
            || !Finite(sample.motionSpeed) || !Finite(sample.stationaryDuration) || !Finite(sample.distanceToCharacter)
            || !Finite(sample.age) || sample.age<0
            || sample.speed<0 || sample.pathDistance<0 || sample.pathTotal<0 || sample.motionSpeed<0 || sample.stationaryDuration<0)
            return false;
        if(input==null || input.sessionId!=sample.sessionId) touching=false;
        input=sample;receivedAt=now-sample.age;
        Refresh(now);
        return true;
    }
    public void BeginContact(Vector3 point,Transform anchor) {
        if(!HasSample) return;
        touching=true;contactPoint=point;contactNormal=view.transform.forward;contactAnchor=anchor;
        contactRevision=input.mappingRevision;hitSequence=input.sequence;
    }
    public void EndContact() { touching=false;contactAnchor=null; }
    float ScreenDistance(Vector3 point) {
        var projected=view.WorldToViewportPoint(point);
        if(projected.z<=0 || !Finite(projected)) return -1;
        return Vector2.Scale(input.windowPosition-new Vector2(projected.x,1-projected.y),input.windowSize).magnitude/input.dpiScale;
    }
    public void Refresh(double now) {
        if(input==null) return;
        var next=new EkuMouseTargetSnapshot {
            sessionId=input.sessionId,sequence=input.sequence,timestamp=input.timestamp,source=input.source,age=(float)Math.Max(0,now-receivedAt),
            screenPosition=input.screenPosition,effectiveScreenPosition=input.effectiveScreenPosition,
            windowPosition=input.windowPosition,windowSize=input.windowSize,dpiScale=input.dpiScale,mappingRevision=input.mappingRevision,
            interactionId=input.interactionId,interactionMode=input.interactionMode,
            positionValid=input.positionValid,insideViewport=input.insideViewport,isPressed=input.isPressed,
            velocity=input.velocity,speed=input.speed,direction=input.direction,isMoving=input.isMoving,
            pathDistance=input.pathDistance,pathTotal=input.pathTotal,motionSpeed=input.motionSpeed,
            stationaryDuration=input.stationaryDuration,lookPosition=input.lookPosition,forcedLook=input.forcedLook,
            distanceToCharacter=input.distanceToCharacter,distanceToHead=-1,distanceToFace=-1,projectionMode="None"
        };
        string error=null;
        if(now-receivedAt>timeout || now<receivedAt) error="Stale";
        else if(!input.positionValid) error="PositionUnavailable";
        else if(!view || !head || !avatar || !view.targetTexture) error="NotReady";
        // Normalized viewport coordinates support supersampling. A different aspect
        // is unsafe; a larger render texture with the same aspect is not.
        else if(input.windowSize.x<=0 || input.windowSize.y<=0 || Mathf.Abs(input.windowSize.x/input.windowSize.y-view.aspect)>2f/Mathf.Min(input.windowSize.y,view.targetTexture.height)
                || (touching && input.mappingRevision!=contactRevision)) error="MappingChanged";
        if(error==null) {
            next.distanceToHead=ScreenDistance(head.position+Vector3.up*height*.045f);
            next.distanceToFace=ScreenDistance(head.TransformPoint(faceLocalOffset));
            var ray=view.ViewportPointToRay(new Vector3(input.windowPosition.x,1-input.windowPosition.y,0));
            var plane=new Plane(touching?contactNormal:view.transform.forward,
                touching?contactPoint:avatar.TransformPoint(headPlaneLocal));
            if(touching && input.sequence==hitSequence) {
                next.worldPosition=contactPoint;next.projectionMode="SurfaceHit";
            } else if(plane.Raycast(ray,out float distance) && Finite(ray.GetPoint(distance))) {
                next.worldPosition=ray.GetPoint(distance);next.projectionMode=touching?"ContactPlane":"HeadPlane";
            } else error="ProjectionUnavailable";
            if(error==null) {
                next.valid=true;
                if(touching && contactAnchor) next.contactLocalPoint=contactAnchor.InverseTransformPoint(next.worldPosition);
                next.allowAttention=input.allowAttention && input.source=="System" && !touching;
            }
        }
        if(error!=null) {
            next.invalidReason=error;next.worldPosition=Vector3.zero;next.projectionMode="None";
            next.velocity=next.direction=Vector2.zero;next.speed=next.motionSpeed=next.pathDistance=0;
            next.isMoving=false;next.forcedLook=false;
        }
        Snapshot=next;
    }
}
