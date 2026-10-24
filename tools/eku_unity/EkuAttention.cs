using System;
using UnityEngine;

[Serializable] public sealed class EkuAttentionSettings {
    public bool enabled=true;
    // Distances and prediction offsets are 96-DPI logical pixels; times are seconds.
    public float enterDistance=180,exitDistance=260,observeSeconds=.22f,focusedSeconds=1.3f;
    public float minimumAttendSeconds=1.1f,maximumAttendSeconds=6,lookAwaySeconds=1.2f,cooldownSeconds=2.5f,memorySeconds=.8f;
    public float weightResponse=.22f,releaseSeconds=.45f,cancelSeconds=.12f;
    public float eyeResponse=.055f,headResponse=.23f,neckResponse=.42f,bodyResponse=.8f;
    public float headDelay=.1f,neckDelay=.2f,bodyDelay=.65f;
    // (pitch, yaw), anatomical Euler degrees. Sum of layers stays inside total limits.
    public Vector2 maximumEyeDegrees=new Vector2(8,13),maximumHeadDegrees=new Vector2(12,20);
    public Vector2 maximumNeckDegrees=new Vector2(5,9),maximumBodyDegrees=new Vector2(2,4);
    public Vector2 maximumTotalDegrees=new Vector2(24,38);
    public float maximumAngularSpeed=130,gazeDepthHeight=.8f;
    public float predictionSeconds=.09f,maximumPredictionDip=24,predictionResponse=.08f;
    public float maximumTrackSpeed=1800;
}

public struct EkuAttentionContext {
    public bool blocked,contact,resting,working,automatic;
    public string reason;
    public float energy,curiosity,acceptance;
}

[Serializable] public struct EkuAttentionOutput {
    public bool enabled,valid,targetValid,remembered;
    public string state,reason;
    public long sampleSeq;
    public Vector3 worldTarget,body;
    public Vector2 eyes,head,neck;
    public float weight,interest,predictionDip,awareness,novelty;
}

// Decisions and bounded gaze offsets only. AnimationPlayer owns every bone write.
public sealed class EkuAttention {
    public readonly EkuAttentionSettings Settings;
    public EkuAttentionOutput Output { get; private set; }
    readonly Camera view;
    readonly Transform avatar,headBone;
    readonly Vector3 originLocal;
    readonly float height;
    Vector2 eyes,head,neck,body,prediction,previousVelocity,lastDegrees;
    Vector3 lastWorld;
    string state="Ignore",session="";
    double observedAt=-1,attendAt=-1,lastSeen=-100,awayUntil,cooldownUntil,previousAt;
    long sequence;
    int mapping;
    float weight,novelty,lastWeight;
    bool near,previousEligible;

    public EkuAttention(Camera view,Transform avatar,Transform head,float height,EkuAttentionSettings settings=null) {
        this.view=view;this.avatar=avatar;headBone=head;this.height=Mathf.Max(.01f,height);
        Settings=settings??new EkuAttentionSettings();
        if(avatar && head)originLocal=avatar.InverseTransformPoint(head.position+Vector3.up*height*.015f);
    }
    static float Positive(float value,float fallback) => EkuEmotion.Finite(value) && value>0?value:fallback;
    static float Unit(float value) => EkuEmotion.Finite(value)?Mathf.Clamp01(value):0;
    static Vector2 Clamp(Vector2 value,Vector2 limit) => new Vector2(
        Mathf.Clamp(value.x,-Mathf.Abs(limit.x),Mathf.Abs(limit.x)),Mathf.Clamp(value.y,-Mathf.Abs(limit.y),Mathf.Abs(limit.y)));
    static float Ease(float age,float delay) => Mathf.SmoothStep(0,1,Mathf.Clamp01(age/Positive(delay,.001f)));
    Vector2 Follow(Vector2 current,Vector2 target,float tau,float dt) {
        var smoothed=Vector2.Lerp(current,target,1-Mathf.Exp(-dt/Positive(tau,.1f)));
        return Vector2.MoveTowards(current,smoothed,Positive(Settings.maximumAngularSpeed,130)*dt);
    }
    void Forget() {
        near=false;previousEligible=false;observedAt=attendAt=-1;lastSeen=-100;lastDegrees=prediction=Vector2.zero;
    }
    bool Project(EkuMouseTargetSnapshot mouse,Vector2 offset,out Vector2 degrees) {
        degrees=Vector2.zero;
        if(!view || !avatar)return false;
        // Called after the base HumanPose and before the final attention writer:
        // posture changes move the gaze origin without feeding last frame's gaze back.
        var origin=headBone?headBone.position+Vector3.up*height*.015f:avatar.TransformPoint(originLocal);
        var ray=view.ViewportPointToRay(new Vector3(mouse.windowPosition.x+offset.x,1-mouse.windowPosition.y-offset.y,0));
        if(!new Plane(view.transform.forward,origin).Raycast(ray,out var distance))return false;
        var delta=ray.GetPoint(distance)-origin;
        // Cursor world depth remains the MouseTarget plane. Gaze uses a documented
        // virtual viewing distance towards the camera, never a claimed cursor depth.
        var direction=delta-view.transform.forward*Vector3.Dot(delta,view.transform.forward)
            -view.transform.forward*height*Positive(Settings.gazeDepthHeight,.8f);
        var local=avatar.InverseTransformDirection(direction);
        if(local.z<=.001f)return false; // Do not twist towards a target behind the avatar.
        degrees=new Vector2(-Mathf.Atan2(local.y,Mathf.Sqrt(local.x*local.x+local.z*local.z)),Mathf.Atan2(local.x,local.z))*Mathf.Rad2Deg;
        return EkuEmotion.Finite(degrees.x) && EkuEmotion.Finite(degrees.y);
    }
    public EkuAttentionOutput Step(float elapsed,double now,EkuMouseTargetSnapshot mouse,EkuAttentionContext context) {
        var oldEyes=eyes;var oldHead=head;var oldNeck=neck;var oldBody=body;
        bool owns=mouse.sequence>0;
        float dt=EkuEmotion.Finite(elapsed)?Mathf.Clamp(elapsed,0,.05f):0;
        bool changedSession=mouse.sessionId!=session,changedMapping=mouse.mappingRevision!=mapping;
        if(changedSession || changedMapping) {
            Forget();session=mouse.sessionId;mapping=mouse.mappingRevision;
            if(changedSession) {sequence=0;previousVelocity=Vector2.zero;previousAt=0;awayUntil=cooldownUntil=0;}
        }
        bool forced=mouse.valid && mouse.forcedLook;
        bool stale=mouse.invalidReason=="Stale";
        bool hardBlocked=!Settings.enabled || !owns || context.blocked || context.contact || context.resting || context.working
            || mouse.isPressed
            || (!context.automatic && !forced) || mouse.source!="System"
            || (!mouse.allowAttention && !forced && !stale) || elapsed>.25f || !EkuEmotion.Finite(elapsed);
        string reason=!Settings.enabled?"disabled":context.contact?"contact":context.resting?"resting":context.working?"working":
            context.blocked?(string.IsNullOrEmpty(context.reason)?"blocked":context.reason):mouse.isPressed?"pointer_pressed":!context.automatic && !forced?"automatic_off":
            mouse.source!="System"?"source":elapsed>.25f?"frame_gap":"unavailable";
        bool fresh=mouse.valid && mouse.allowAttention && mouse.source=="System";
        float distance=mouse.distanceToCharacter;
        float enter=Positive(Settings.enterDistance,180),exit=Mathf.Max(enter,Positive(Settings.exitDistance,260));
        bool within=fresh && distance>=0 && distance<=(near?exit:enter);
        float interest=within?Mathf.Lerp(.35f,1,1-Mathf.Clamp01(distance/exit)):0;
        if(within)interest*=Mathf.Lerp(.5f,1,1-Mathf.Clamp01(mouse.speed/Positive(Settings.maximumTrackSpeed,1800)));
        bool newSample=mouse.sequence!=sequence;
        if(hardBlocked || !fresh)previousEligible=false;
        novelty*=Mathf.Exp(-dt/.35f);
        if(newSample) {
            float sourceDt=(float)(mouse.timestamp-previousAt);
            if(fresh && !hardBlocked && previousEligible && !changedMapping && sourceDt>=.005f && sourceDt<=.25f)
                novelty=Mathf.Max(novelty,Unit((mouse.velocity-previousVelocity).magnitude/sourceDt/12000)*interest);
            previousAt=mouse.timestamp;sequence=mouse.sequence;
            previousEligible=fresh && !hardBlocked;
        }
        Vector2 desired=Vector2.zero;
        float desiredWeight=0;
        bool remembered=false,targetValid=false;
        if(hardBlocked) {
            Forget();state="Ignore";novelty=0;
        } else if(forced) {
            state="Focused";reason="explicit_look";near=false;lastSeen=-100;
            desired=new Vector2(mouse.lookPosition.y,-mouse.lookPosition.x)*Mathf.Rad2Deg;
            desiredWeight=1;interest=0;prediction=Vector2.zero;
            if(attendAt<0)attendAt=now;
        } else {
            if(now<awayUntil || now<cooldownUntil) {
                state=now<awayUntil?"LookAway":"Ignore";reason="attention_break";near=false;observedAt=-1;
                if(now<awayUntil) {
                    desired=new Vector2(1,lastDegrees.y>=0?-7:7);desiredWeight=.55f;
                }
            } else if(within) {
                if(!near) {observedAt=now;attendAt=-1;}
                near=true;
                float age=(float)(now-observedAt);
                state=age<Positive(Settings.observeSeconds,.22f)?"Observe":"Attend";
                if(state=="Attend" && attendAt<0)attendAt=now;
                float focusDelay=Positive(Settings.focusedSeconds,1.3f);
                if(attendAt>=0 && now-attendAt>=focusDelay && mouse.stationaryDuration>=focusDelay)state="Focused";
                float duration=attendAt<0?0:(float)(now-attendAt);
                if(attendAt>=0 && duration>=Mathf.Max(Positive(Settings.minimumAttendSeconds,1.1f),Positive(Settings.maximumAttendSeconds,6))) {
                    awayUntil=now+Positive(Settings.lookAwaySeconds,1.2f);
                    cooldownUntil=awayUntil+Positive(Settings.cooldownSeconds,2.5f);
                    state="LookAway";reason="attention_break";desired=new Vector2(1,lastDegrees.y>=0?-7:7);desiredWeight=.55f;
                    near=false;observedAt=attendAt=-1;lastSeen=-100;prediction=Vector2.zero;
                } else {
                    Vector2 predictionTarget=Vector2.zero;
                    bool steady=mouse.isMoving && mouse.speed>8 && mouse.speed<Positive(Settings.maximumTrackSpeed,1800)
                        && (!newSample || previousVelocity.sqrMagnitude<1 || Vector2.Dot(previousVelocity.normalized,mouse.velocity.normalized)>.2f);
                    if(steady && mouse.windowSize.x>0 && mouse.windowSize.y>0) {
                        float horizon=EkuEmotion.Finite(Settings.predictionSeconds)?Mathf.Max(0,Settings.predictionSeconds):.09f;
                        var dipOffset=Vector2.ClampMagnitude(mouse.velocity*horizon,Mathf.Max(0,Settings.maximumPredictionDip));
                        predictionTarget=Vector2.Scale(dipOffset,new Vector2(mouse.dpiScale/mouse.windowSize.x,mouse.dpiScale/mouse.windowSize.y));
                    }
                    prediction=Vector2.Lerp(prediction,predictionTarget,1-Mathf.Exp(-dt/Positive(Settings.predictionResponse,.08f)));
                    if(Project(mouse,prediction,out desired)) {
                        float amplitude=Mathf.Lerp(.35f,1,Unit(context.energy))*Mathf.Lerp(.45f,1,Unit(context.acceptance))
                            *Mathf.Lerp(.85f,1,Unit(context.curiosity));
                        desiredWeight=(state=="Observe"?.3f:1)*interest*amplitude;
                        targetValid=state!="Observe";lastWorld=mouse.worldPosition;lastDegrees=desired;lastWeight=desiredWeight;lastSeen=now;reason="pointer";
                    } else {reason="behind_or_projection";state="Ignore";Forget();}
                }
            } else {
                near=false;observedAt=-1;prediction=Vector2.zero;
                float age=(float)(now-lastSeen),memory=Positive(Settings.memorySeconds,.8f);
                if(age>=0 && age<memory && attendAt>=0) {
                    state="Remember";reason="last_seen";remembered=true;desired=lastDegrees;
                    desiredWeight=lastWeight*(1-age/memory);
                } else {state="Ignore";reason=stale?"stale":"out_of_range";attendAt=-1;}
            }
        }
        if(newSample)previousVelocity=mouse.velocity;
        float response=hardBlocked?Positive(Settings.cancelSeconds,.12f):desiredWeight>weight?Positive(Settings.weightResponse,.22f):Positive(Settings.releaseSeconds,.45f);
        weight=Mathf.Lerp(weight,desiredWeight,1-Mathf.Exp(-dt/response));
        desired=Clamp(desired,Settings.maximumTotalDegrees)*weight;
        float followAge=attendAt<0?0:(float)(now-attendAt);
        float slow=hardBlocked?Positive(Settings.cancelSeconds,.12f):0;
        var bodyTarget=Clamp(desired*.1f,Settings.maximumBodyDegrees)*Ease(followAge,Positive(Settings.bodyDelay,.65f));
        if(state!="Focused" || Unit(context.energy)<.4f)bodyTarget=Vector2.zero;
        body=Follow(body,bodyTarget,slow>0?slow:Settings.bodyResponse,dt);
        var neckTarget=hardBlocked?Vector2.zero:Clamp((desired-body)*.18f,Settings.maximumNeckDegrees)*Ease(followAge,Positive(Settings.neckDelay,.2f));
        neck=Follow(neck,neckTarget,slow>0?slow:Settings.neckResponse,dt);
        var headTarget=hardBlocked?Vector2.zero:Clamp((desired-body-neck)*.7f,Settings.maximumHeadDegrees)*Ease(followAge,Positive(Settings.headDelay,.1f));
        head=Follow(head,headTarget,slow>0?slow:Settings.headResponse,dt);
        eyes=Follow(eyes,hardBlocked?Vector2.zero:Clamp(desired-body-neck-head,Settings.maximumEyeDegrees),slow>0?slow:Settings.eyeResponse,dt);
        // Independent layers can briefly oppose during a reversal. Bound the combined
        // angle as well, without an unbounded instantaneous corrective rotation.
        var total=eyes+head+neck+body;
        float scale=Mathf.Min(1,Mathf.Min(Mathf.Abs(Settings.maximumTotalDegrees.x)/Mathf.Max(.001f,Mathf.Abs(total.x)),
                                        Mathf.Abs(Settings.maximumTotalDegrees.y)/Mathf.Max(.001f,Mathf.Abs(total.y))));
        eyes*=scale;head*=scale;neck*=scale;body*=scale;
        float angularDelta=(eyes+head+neck+body-oldEyes-oldHead-oldNeck-oldBody).magnitude;
        float maximumStep=Positive(Settings.maximumAngularSpeed,130)*dt;
        if(angularDelta>maximumStep && angularDelta>0) {
            float blend=maximumStep/angularDelta;
            eyes=Vector2.Lerp(oldEyes,eyes,blend);head=Vector2.Lerp(oldHead,head,blend);
            neck=Vector2.Lerp(oldNeck,neck,blend);body=Vector2.Lerp(oldBody,body,blend);
        }
        if(weight<.0001f && desiredWeight==0)weight=0;
        float predictionDip=mouse.dpiScale>0?Vector2.Scale(prediction,mouse.windowSize).magnitude/mouse.dpiScale:0;
        Output=new EkuAttentionOutput{enabled=owns,valid=weight>.001f || eyes.sqrMagnitude+head.sqrMagnitude+neck.sqrMagnitude+body.sqrMagnitude>.0001f,targetValid=targetValid && !hardBlocked,
            remembered=remembered,state=state,reason=reason,sampleSeq=mouse.sequence,worldTarget=targetValid || remembered?lastWorld:Vector3.zero,
            eyes=eyes,head=head,neck=neck,body=new Vector3(body.x,body.y,0),weight=weight,interest=interest,
            predictionDip=predictionDip,awareness=hardBlocked?0:targetValid?interest:state=="Observe"?interest*.25f:0,novelty=novelty};
        return Output;
    }
}
