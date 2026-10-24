using System;
using UnityEngine;

// Distances, speeds and acceleration are fractions of the complete arm length.
[Serializable] public sealed class EkuReachSettings {
    public float MaxSpeed=1.2f,MaxAcceleration=4f,MaxWeight=1f,BlendSpeed=3f;
    public float AttemptSeconds=3f,CooldownSeconds=45f,ArrivalDistance=.08f;
    public float ArrivalHoldSeconds=.18f,GrabSeconds=.65f,MaxSampleAge=.25f;
    public float MinReachFraction=.08f,MaxReachFraction=.97f;
    public float TargetFrontFraction=.55f;
    public EkuReachMotionSettings Motion=new EkuReachMotionSettings();
}

public enum EkuReachPhase { Idle,Reach,Grabbed,Recover }
public struct EkuReachInput {
    public Vector3 Target,Shoulder,Palm,Pole;
    public float UpperLength,LowerLength,SampleAge;
    public bool Valid,AllowAttention,Pressed,TooFast;
    public string Source,SessionId;
    public int MappingRevision;
}
[Serializable] public struct EkuReachState {
    public EkuReachPhase Phase;
    public float Weight,PalmDistance;
    public Vector3 Target,Velocity,Elbow,ClampedTarget;
    public bool IsClamped,Reachable,Grabbed;
    public string CancelReason;
}

// Numeric only: the animation owner applies one arm layer after its base pose.
public sealed class EkuReach {
    public readonly EkuReachSettings Settings;
    public EkuReachPhase Phase { get; private set; }
    public float Weight { get; private set; }
    public Vector3 Target { get; private set; }
    public Vector3 Velocity { get; private set; }
    public string LastCancelReason { get; private set; }="";
    float startedAt,grabbedAt,arrivalHold,cooldownUntil,lastMeasureAt=float.NegativeInfinity;
    string sessionId;
    int mappingRevision;
    public EkuReach(EkuReachSettings settings=null) { Settings=settings??new EkuReachSettings(); }
    static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    static bool Finite(Vector3 value) => Finite(value.x)&&Finite(value.y)&&Finite(value.z);
    static float Positive(float value,float fallback) => Finite(value)&&value>0?value:fallback;
    // The cursor belongs to the foreground, rather than the resting hand's depth.
    public static Vector3 FrontPlane(Vector3 shoulder,Vector3 towardViewer,float length,float fraction=.55f) =>
        shoulder+towardViewer.normalized*length*Mathf.Clamp(Positive(fraction,.55f),.25f,.75f);
    public static Vector3 FrontPole(Vector3 shoulder,Vector3 towardViewer,Vector3 right,Vector3 up,float length) {
        var front=towardViewer.normalized;
        var side=Vector3.ProjectOnPlane(right,front).normalized;
        if(side.sqrMagnitude<.001f)side=Vector3.Cross(up,front).normalized;
        return shoulder+(side*.5f-up.normalized*.45f+front*.45f)*length;
    }
    public float CooldownRemaining(float now) => Finite(now)?Mathf.Max(0,cooldownUntil-now):float.PositiveInfinity;
    string Invalid(EkuReachInput input) {
        if(!input.Valid || !input.AllowAttention) return "target-unavailable";
        if(input.Source!="System") return "source";
        if(input.Pressed) return "pressed";
        if(input.TooFast) return "too-fast";
        if(!Finite(input.SampleAge) || input.SampleAge<0 || input.SampleAge>Positive(Settings.MaxSampleAge,.25f)) return "stale";
        if(string.IsNullOrEmpty(input.SessionId) || !Finite(input.Target) || !Finite(input.Palm)
            || !Finite(input.Shoulder) || !Finite(input.Pole) || !Finite(input.UpperLength)
            || !Finite(input.LowerLength) || input.UpperLength<=0 || input.LowerLength<=0) return "invalid-geometry";
        float length=input.UpperLength+input.LowerLength;
        float distance=(input.Target-input.Shoulder).magnitude;
        float minimum=Mathf.Max(Mathf.Abs(input.UpperLength-input.LowerLength)+length*.0001f,
            length*Mathf.Clamp(Positive(Settings.MinReachFraction,.08f),.0001f,.9f));
        float maximum=length*Mathf.Clamp(Positive(Settings.MaxReachFraction,.97f),.1f,.9999f);
        if(distance<minimum || distance>maximum) return "unreachable";
        return "";
    }
    public bool Begin(float now,EkuReachInput input) {
        if(!Finite(now) || Phase!=EkuReachPhase.Idle || CooldownRemaining(now)>0 || Invalid(input)!="") return false;
        sessionId=input.SessionId;mappingRevision=input.MappingRevision;startedAt=now;
        arrivalHold=0;lastMeasureAt=float.NegativeInfinity;Target=input.Palm;Velocity=Vector3.zero;Weight=0;
        LastCancelReason="";Phase=EkuReachPhase.Reach;return true;
    }
    void End(float now,string reason,bool immediate) {
        if(Phase==EkuReachPhase.Idle && Weight==0) return;
        LastCancelReason=reason??"cancelled";arrivalHold=0;Velocity=Vector3.zero;
        cooldownUntil=Mathf.Max(cooldownUntil,now+Positive(Settings.CooldownSeconds,45));
        if(immediate) {Weight=0;Phase=EkuReachPhase.Idle;}
        else Phase=EkuReachPhase.Recover;
    }
    // Explicit user controls withdraw the layer immediately; automatic exits fade.
    public void Cancel(float now,string reason) { End(Finite(now)?now:startedAt,reason,true); }
    public EkuReachState Tick(float now,float dt,EkuReachInput input,bool measureArrival=true) {
        bool active=Phase==EkuReachPhase.Reach || Phase==EkuReachPhase.Grabbed;
        if(active) {
            string reason=Invalid(input);
            if(input.SessionId!=sessionId || input.MappingRevision!=mappingRevision) reason="mapping-changed";
            if(!Finite(now) || now<startedAt || !Finite(dt) || dt<0 || dt>.2f) reason="suspended";
            if(reason!="") End(Finite(now)?now:startedAt,reason,reason=="suspended");
            else if(Phase==EkuReachPhase.Reach && now-startedAt>=Positive(Settings.AttemptSeconds,3)) End(now,"timeout",false);
            else if(Phase==EkuReachPhase.Grabbed && now-grabbedAt>=Positive(Settings.GrabSeconds,.65f)) End(now,"complete",false);
        }
        float step=Finite(dt)&&dt>0&&dt<=.2f?dt:0;
        if(Phase==EkuReachPhase.Reach || Phase==EkuReachPhase.Grabbed) {
            float length=input.UpperLength+input.LowerLength;
            float acceleration=Positive(Settings.MaxAcceleration,4)*length;
            Vector3 delta=input.Target-Target;
            float speed=Mathf.Min(Positive(Settings.MaxSpeed,1.2f)*length,Mathf.Sqrt(2*acceleration*delta.magnitude));
            Vector3 velocity=Vector3.MoveTowards(Velocity,delta.normalized*speed,acceleration*step);
            Target+=(Velocity+velocity)*(.5f*step);Velocity=velocity;
            Weight=Mathf.MoveTowards(Weight,Mathf.Clamp01(Settings.MaxWeight),Positive(Settings.BlendSpeed,3)*step);
            if(measureArrival)AdvanceArrival(now,step,input);
        } else if(Phase==EkuReachPhase.Recover) {
            Weight=Mathf.MoveTowards(Weight,0,Positive(Settings.BlendSpeed,3)*step);
            if(Weight==0) Phase=EkuReachPhase.Idle;
        }
        return Snapshot(input);
    }
    void AdvanceArrival(float now,float dt,EkuReachInput input) {
        if(Phase!=EkuReachPhase.Reach || !Finite(now) || now<=lastMeasureAt || dt<=0 || dt>.2f)return;
        lastMeasureAt=now;
        float length=input.UpperLength+input.LowerLength;
        if((input.Palm-input.Target).magnitude<=Positive(Settings.ArrivalDistance,.08f)*length)arrivalHold+=dt;
        else arrivalHold=0;
        if(arrivalHold>=Positive(Settings.ArrivalHoldSeconds,.18f)) {Phase=EkuReachPhase.Grabbed;grabbedAt=now;}
    }
    // With body/shoulder motion, defer arrival until the one pose writer has applied
    // this frame's IK. Repeated status reads at the same time cannot extend contact.
    public EkuReachState MeasurePalm(float now,float dt,EkuReachInput actual) {
        if(Phase==EkuReachPhase.Reach && Finite(now) && now>lastMeasureAt && Finite(dt) && dt>0 && dt<=.2f) {
            if(Invalid(actual)=="" && actual.SessionId==sessionId && actual.MappingRevision==mappingRevision)AdvanceArrival(now,dt,actual);
            else {arrivalHold=0;lastMeasureAt=now;}
        }
        return Snapshot(actual);
    }
    EkuReachState Snapshot(EkuReachInput input) {
        Vector3 elbow,clamped;
        bool solved=SolveTwoBone(input.Shoulder,Target,input.Pole,input.UpperLength,input.LowerLength,out elbow,out clamped);
        return new EkuReachState {Phase=Phase,Weight=Weight,Target=Target,Velocity=Velocity,Elbow=elbow,
            ClampedTarget=clamped,IsClamped=solved&&(clamped-Target).sqrMagnitude>1e-10f,
            Reachable=Invalid(input)=="",PalmDistance=Finite(input.Palm)&&Finite(input.Target)?(input.Palm-input.Target).magnitude:float.PositiveInfinity,
            Grabbed=Phase==EkuReachPhase.Grabbed,CancelReason=LastCancelReason};
    }
    public static bool SolveTwoBone(Vector3 shoulder,Vector3 target,Vector3 pole,float upperLength,float lowerLength,
        out Vector3 elbow,out Vector3 clampedTarget) {
        elbow=shoulder;clampedTarget=shoulder;
        if(!Finite(shoulder)||!Finite(target)||!Finite(pole)||!Finite(upperLength)||!Finite(lowerLength)
            ||upperLength<=0||lowerLength<=0) return false;
        float length=upperLength+lowerLength,epsilon=length*.0001f;
        Vector3 delta=target-shoulder;
        Vector3 direction=delta.sqrMagnitude>epsilon*epsilon?delta.normalized:Vector3.forward;
        float distance=Mathf.Clamp(delta.magnitude,Mathf.Abs(upperLength-lowerLength)+epsilon,length-epsilon);
        clampedTarget=shoulder+direction*distance;
        Vector3 bend=Vector3.ProjectOnPlane(pole-shoulder,direction);
        if(bend.sqrMagnitude<epsilon*epsilon)
            bend=Vector3.ProjectOnPlane(Mathf.Abs(direction.y)<.9f?Vector3.up:Vector3.right,direction);
        bend.Normalize();
        float along=(upperLength*upperLength+distance*distance-lowerLength*lowerLength)/(2*distance);
        float height=Mathf.Sqrt(Mathf.Max(0,upperLength*upperLength-along*along));
        elbow=shoulder+direction*along+bend*height;return true;
    }
}
