using System;
using UnityEngine;

[Serializable] public sealed class EkuHeadPatSettings {
    [Min(.02f)] public float responseSeconds=.14f;
    [Min(.05f)] public float recoverySeconds=.55f;
    [Min(.05f)] public float afterglowSeconds=1.6f;
    [Min(.02f)] public float cancelSeconds=.16f;
    [Min(.1f)] public float relaxSeconds=3.5f;
    [Range(0,10)] public float maximumDegrees=5;
    [Range(0,1)] public float neckShare=.22f;
    [Range(0,.5f)] public float microDegrees=.12f;
    [Range(0,3)] public float maximumBodyDegrees=1.2f;
    [Range(0,.5f)] public float bodyShare=.18f;
    [Range(0,1)] public float earResponse=1;
    [Range(.5f,1.5f)] public float relaxedBreathingScale=.88f;
    [Min(.001f)] public float headWidthRatio=.15f;
    // Anatomical coordinates: avatar right/up/forward, independent of bone axes.
    public Vector3 contactCenter=new Vector3(0,.025f,0);
    [Min(0)] public float speedDeadzone=.035f;
    [Min(.1f)] public float roughEnter=4;
    [Min(.1f)] public float roughExit=2.5f;
    [Min(.02f)] public float roughEnterSeconds=.16f;
    [Min(.02f)] public float roughExitSeconds=.28f;
    [Min(.1f)] public float streamTimeoutSeconds=.5f;
    [Min(.02f)] public float outsideGraceSeconds=.2f;
}

[Serializable] public struct EkuHeadPatOutput {
    public bool active,stroking,rough;
    public float strength,relax,smile,startle,speed,pleasure;
    public Vector3 headDegrees,neckDegrees;
    public Vector3 bodyDegrees;
    public Vector2 earWeights,earLiftWeights;
    public float breathingScale;
}

// Only the contact response lives here. No input collection, bone or mesh writes.
public sealed class EkuHeadPat {
    readonly EkuHeadPatSettings settings;
    EkuHeadPatOutput value;
    Vector3 point,filteredPoint,direction,filteredDirection,angles;
    Vector2 earFold,earLift;
    Vector3 strokeDirection;
    float strokeDistance,earAge=10,nextEarAt;
    float targetSpeed,roughAge,calmAge,age;
    double updatedAt;
    bool cancelled;
    public EkuHeadPatOutput Value => value;
    public EkuHeadPat(EkuHeadPatSettings settings) {this.settings=settings??new EkuHeadPatSettings();value.breathingScale=1;}
    static bool Finite(Vector3 v) => EkuEmotion.Finite(v.x) && EkuEmotion.Finite(v.y) && EkuEmotion.Finite(v.z);
    static Vector3 Contact(Vector3 v) => Finite(v)?Vector3.ClampMagnitude(v,3):Vector3.zero;
    public void Begin(Vector3 localPoint,double now) {
        value.active=true;value.rough=false;cancelled=false;
        value.speed=0;value.stroking=false;
        point=Contact(localPoint);filteredPoint=point;direction=filteredDirection=Vector3.zero;targetSpeed=0;
        roughAge=calmAge=age=strokeDistance=earAge=0;nextEarAt=1.25f;
        strokeDirection=Vector3.zero;updatedAt=now;
    }
    public void Sample(Vector3 localPoint,Vector3 localVelocity,float speed,double now) {
        if(!Finite(localPoint) || !Finite(localVelocity) || !EkuEmotion.Finite(speed) || double.IsNaN(now) || double.IsInfinity(now))return;
        point=Contact(localPoint);direction=Vector3.ClampMagnitude(localVelocity,6);
        targetSpeed=Mathf.Clamp(speed,0,12);updatedAt=now;
    }
    public void End(bool cancel) {
        value.active=false;cancelled=cancel;direction=Vector3.zero;targetSpeed=0;
        value.rough=false;roughAge=calmAge=0;
        earAge=cancel?10:.4f;
    }
    static float Pulse(float time,float rise,float hold,float fall) =>
        Mathf.SmoothStep(0,1,Mathf.Clamp01(time/rise))*Mathf.SmoothStep(0,1,Mathf.Clamp01((rise+hold+fall-time)/fall));
    public EkuHeadPatOutput Tick(float deltaTime,double now) {
        if(!EkuEmotion.Finite(deltaTime) || deltaTime<=0)return value;
        float dt=Mathf.Clamp(deltaTime,0,.05f);
        float response=Mathf.Max(.02f,settings.responseSeconds);
        float fade=1-Mathf.Exp(-dt/response);
        bool fresh=value.active && now-updatedAt<.12;
        value.speed=Mathf.Lerp(value.speed,fresh?targetSpeed:0,fade);
        filteredDirection=Vector3.Lerp(filteredDirection,fresh?direction:Vector3.zero,fade);
        filteredPoint=Vector3.Lerp(filteredPoint,point,fade);
        value.stroking=value.active && value.speed>settings.speedDeadzone;
        if(value.active) {
            age+=dt;
            roughAge=value.speed>=settings.roughEnter?roughAge+dt:0;
            calmAge=value.speed<=Mathf.Min(settings.roughExit,settings.roughEnter*.9f)?calmAge+dt:0;
            if(!value.rough && roughAge>=settings.roughEnterSeconds)value.rough=true;
            if(value.rough && calmAge>=settings.roughExitSeconds)value.rough=false;
            float growth=deltaTime>.2f?0:value.rough?-.9f:value.stroking?1:.2f;
            value.pleasure=Mathf.Clamp01(value.pleasure+growth*dt/Mathf.Max(.1f,settings.relaxSeconds));
        } else value.pleasure=Mathf.MoveTowards(value.pleasure,0,dt/Mathf.Max(.05f,settings.afterglowSeconds));
        float recovery=cancelled?settings.cancelSeconds:settings.recoverySeconds;
        value.strength=Mathf.MoveTowards(value.strength,value.active?1:0,dt/Mathf.Max(.02f,value.active?response:recovery));
        float relaxTarget=value.active?(.72f+.20f*value.pleasure)*(value.rough?.22f:1):0;
        value.relax=Mathf.MoveTowards(value.relax,relaxTarget,dt/Mathf.Max(.02f,value.active?.6f:cancelled?settings.cancelSeconds:settings.afterglowSeconds));
        value.startle=Mathf.MoveTowards(value.startle,value.active && value.rough?.22f:0,dt/.22f);
        float motion=Mathf.Clamp01(value.speed/1.8f);
        var target=new Vector3(filteredPoint.z*2+filteredDirection.z*.65f-filteredDirection.y*.24f-.6f,
            filteredDirection.x*.18f,-filteredPoint.x*2-filteredDirection.x*.65f);
        target*=.6f+.4f*motion;
        target+=new Vector3(Mathf.Sin(age*1.3f),0,Mathf.Sin(age*1.1f+.4f))*settings.microDegrees*(1-motion*.8f);
        if(value.rough)target*=.55f;
        target=Vector3.ClampMagnitude(target,Mathf.Max(0,settings.maximumDegrees));
        angles=Vector3.Lerp(angles,value.active?target:Vector3.zero,fade);
        if(!value.active && value.strength==0)angles=Vector3.zero;
        value.headDegrees=angles*value.strength;
        value.neckDegrees=value.headDegrees*Mathf.Clamp01(settings.neckShare);
        value.bodyDegrees=Vector3.ClampMagnitude((angles*Mathf.Clamp(settings.bodyShare,0,.5f)
            +Vector3.left*value.pleasure*.55f)*value.strength,Mathf.Max(0,settings.maximumBodyDegrees));
        earAge+=dt;
        if(value.active && value.stroking && !value.rough) {
            strokeDistance+=value.speed*dt;
            var directionNow=filteredDirection.normalized;
            bool reversed=directionNow.sqrMagnitude>.1f && strokeDirection.sqrMagnitude>.1f && Vector3.Dot(directionNow,strokeDirection)<-.2f;
            if(age>=nextEarAt && (reversed || strokeDistance>=.7f)) {
                earAge=0;nextEarAt=age+1.25f;strokeDistance=0;
            }
            if(directionNow.sqrMagnitude>.1f && (reversed || strokeDirection.sqrMagnitude<.1f))strokeDirection=directionNow;
        }
        float fold=Pulse(earAge,.18f,.18f,.36f)+.22f*Pulse(earAge-.88f,.08f,.04f,.18f);
        float lift=.75f*Pulse(earAge-.4f,.16f,.04f,.24f);
        float ear=Mathf.Clamp01(settings.earResponse);
        float down=value.rough?.1f:Mathf.Clamp01(.12f+.14f*value.pleasure+fold*.9f);
        float up=value.rough?0:lift;
        float leftSide=Mathf.Clamp01(-filteredPoint.x),rightSide=Mathf.Clamp01(filteredPoint.x);
        var sides=new Vector2(.8f+.2f*leftSide,.8f+.2f*rightSide)*ear;
        float earBlend=1-Mathf.Exp(-dt/Mathf.Max(.03f,response*.5f));
        earFold=Vector2.Lerp(earFold,sides*down,earBlend);
        earLift=Vector2.Lerp(earLift,sides*up,earBlend);
        value.earWeights=earFold*value.strength;
        value.earLiftWeights=earLift*value.strength;
        value.smile=value.relax*(.78f+.22f*Mathf.Clamp01(Mathf.Max(fold,lift)));
        value.breathingScale=Mathf.Lerp(1,Mathf.Clamp(settings.relaxedBreathingScale,.5f,1.5f),value.relax*value.strength);
        if(value.strength==0 && value.relax==0)value.speed=value.pleasure=0;
        return value;
    }
}
