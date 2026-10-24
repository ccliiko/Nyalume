using System;
using UnityEngine;

[Serializable] public sealed class EkuReachMotionSettings {
    public float prepareSeconds=.2f,headDelay=.055f,neckDelay=.09f,bodyDelay=.08f;
    public float armExtendSeconds=.28f,recoverSeconds=.65f,gazeLingerSeconds=.15f,cancelSeconds=.12f;
    public float gazeAcquireSeconds=.05f,bodyRiseSeconds=.28f,gripSeconds=.18f;
    public float gazeDepthHeight=.7f,maximumPitch=22,maximumYaw=30;
    public float bodyMaximumPitch=6,bodyMaximumYaw=5,bodyMaximumRoll=2.5f;
    public float bodyAngularSpeed=35,headAngularSpeed=90,eyeAngularSpeed=180;
    public float wristMaximumDegrees=35,wristAngularSpeed=140;
}

public struct EkuReachMotionInput {
    public bool reaching,grabbed,cancelled;
    // Avatar-local direction from head to raw target, in world units. Depth is virtual.
    public Vector3 localTarget;
    public float height;
}

[Serializable] public struct EkuReachMotionOutput {
    public string stage;
    public float ownership,gazeOwnership,armBlend,wristWeight,grip;
    // Anatomical Euler degrees; these offsets already include their envelopes.
    public Vector3 spine,chest,shoulder,head,neck;
    public Vector2 eyes;
}

// Evaluates offsets only. The Player remains the only owner of bone writes.
public sealed class EkuReachMotion {
    public readonly EkuReachMotionSettings Settings;
    public EkuReachMotionOutput Output { get; private set; }
    public bool IsIdle => !running&&!recovering;
    EkuReachMotionOutput release;
    bool previousReaching,running,recovering,cancelling;
    float elapsed,releasedAt;
    public EkuReachMotion(EkuReachMotionSettings settings=null) {
        Settings=settings??new EkuReachMotionSettings();Output=new EkuReachMotionOutput {stage="idle"};
    }
    static bool Finite(float x) => !float.IsNaN(x)&&!float.IsInfinity(x);
    static float Positive(float x,float fallback) => Finite(x)&&x>0?x:fallback;
    static float Ease(float t) {t=Mathf.Clamp01(t);return t*t*(3-2*t);}
    static Vector3 Move(Vector3 value,Vector3 target,float speed,float dt) => Vector3.MoveTowards(value,target,speed*dt);
    void Release(bool cancel) {
        release=Output;releasedAt=0;running=false;recovering=true;cancelling=cancel;
    }
    public EkuReachMotionOutput Step(float dt,EkuReachMotionInput input) {
        if(!Finite(dt)||dt<=0||dt>.2f) return StepOnce(dt,input);
        // Short numerical steps keep the delayed speed-limited layers alike at 30/144 Hz.
        int count=(int)Math.Ceiling(dt*240);
        for(int i=0;i<count;i++) StepOnce(dt/count,input);
        return Output;
    }
    EkuReachMotionOutput StepOnce(float dt,EkuReachMotionInput input) {
        // A suspended frame cannot replay preparation, finish a grab, or leave stale gaze.
        if(!Finite(dt)||dt<0||dt>.2f) {
            running=recovering=cancelling=false;previousReaching=input.reaching;
            Output=new EkuReachMotionOutput {stage="idle"};return Output;
        }
        bool valid=Finite(input.height)&&input.height>0&&Finite(input.localTarget.x)&&Finite(input.localTarget.y)&&Finite(input.localTarget.z);
        bool cancel=input.cancelled||((input.reaching||input.grabbed)&&!valid);
        bool rising=input.reaching&&!previousReaching;previousReaching=input.reaching;
        if(cancel) {
            if(running || (recovering&&!cancelling)) Release(true);
        // Do not queue an old attempt during withdrawal. The owner starts a new
        // attempt after idle; it must provide a fresh reaching edge.
        } else if(rising&&!running&&!recovering) {running=true;elapsed=0;}
        if(running&&!(input.reaching||input.grabbed)) Release(false);
        var next=Output;
        if(running) {
            elapsed+=dt;
            float prepare=Positive(Settings.prepareSeconds,.2f);
            float depth=input.height*Positive(Settings.gazeDepthHeight,.7f);
            float pitch=Mathf.Clamp(-Mathf.Atan2(input.localTarget.y,depth)*Mathf.Rad2Deg,
                -Positive(Settings.maximumPitch,22),Positive(Settings.maximumPitch,22));
            float yaw=Mathf.Clamp(Mathf.Atan2(input.localTarget.x,depth)*Mathf.Rad2Deg,
                -Positive(Settings.maximumYaw,30),Positive(Settings.maximumYaw,30));
            Vector2 gaze=new Vector2(pitch,yaw);
            float headEnvelope=Ease((elapsed-Positive(Settings.headDelay,.055f))/prepare);
            float neckEnvelope=Ease((elapsed-Positive(Settings.neckDelay,.09f))/prepare);
            float bodyEnvelope=Ease((elapsed-Positive(Settings.bodyDelay,.08f))/Positive(Settings.bodyRiseSeconds,.28f));
            // Anatomical +X tilts avatar-up toward avatar-forward. Keep a small
            // forward preparation instead of cancelling it at common low targets.
            Vector3 body=new Vector3(Mathf.Clamp(2+pitch*.08f,-Positive(Settings.bodyMaximumPitch,6),Positive(Settings.bodyMaximumPitch,6)),
                Mathf.Clamp(yaw*.12f,-Positive(Settings.bodyMaximumYaw,5),Positive(Settings.bodyMaximumYaw,5)),
                Mathf.Clamp(-yaw*.06f,-Positive(Settings.bodyMaximumRoll,2.5f),Positive(Settings.bodyMaximumRoll,2.5f)))*bodyEnvelope;
            float bodySpeed=Positive(Settings.bodyAngularSpeed,35),headSpeed=Positive(Settings.headAngularSpeed,90);
            next.spine=Move(next.spine,body*.35f,bodySpeed*.35f,dt);
            next.chest=Move(next.chest,body*.5f,bodySpeed*.5f,dt);
            next.shoulder=Move(next.shoulder,body*.15f,bodySpeed*.15f,dt);
            next.head=Move(next.head,new Vector3(pitch,yaw,0)*(.48f*headEnvelope),headSpeed*(.48f/.68f),dt);
            next.neck=Move(next.neck,new Vector3(pitch,yaw,0)*(.2f*neckEnvelope),headSpeed*(.2f/.68f),dt);
            next.eyes=Vector2.MoveTowards(next.eyes,gaze*.32f,Positive(Settings.eyeAngularSpeed,180)*dt);
            next.ownership=Ease(elapsed/prepare);next.gazeOwnership=Ease(elapsed/Positive(Settings.gazeAcquireSeconds,.05f));
            next.armBlend=Ease((elapsed-prepare)/Positive(Settings.armExtendSeconds,.28f));
            next.wristWeight=next.armBlend;
            next.grip=Mathf.MoveTowards(next.grip,input.grabbed&&next.armBlend>=.85f?1:0,dt/Positive(Settings.gripSeconds,.18f));
            next.stage=elapsed<=prepare?"prepare":input.grabbed?"hold":"reach";
        } else if(recovering) {
            releasedAt+=dt;
            float duration=cancelling?Positive(Settings.cancelSeconds,.12f):Positive(Settings.recoverSeconds,.65f);
            float fade=1-Ease(releasedAt/duration);
            float linger=Mathf.Min(Positive(Settings.gazeLingerSeconds,.15f),duration*.8f);
            float gazeFade=cancelling?0:1-Ease((releasedAt-linger)/(duration-linger));
            // Cancel has a short withdrawal envelope; it does not retain gaze or arm ownership.
            float bodySpeed=cancelling?2000:Positive(Settings.bodyAngularSpeed,35);
            float headSpeed=cancelling?2000:Positive(Settings.headAngularSpeed,90);
            next.spine=Move(next.spine,release.spine*fade,bodySpeed*.35f,dt);
            next.chest=Move(next.chest,release.chest*fade,bodySpeed*.5f,dt);
            next.shoulder=Move(next.shoulder,release.shoulder*fade,bodySpeed*.15f,dt);
            next.head=Move(next.head,release.head*gazeFade,headSpeed*(.48f/.68f),dt);
            next.neck=Move(next.neck,release.neck*gazeFade,headSpeed*(.2f/.68f),dt);
            next.eyes=Vector2.MoveTowards(next.eyes,release.eyes*gazeFade,(cancelling?2000:Positive(Settings.eyeAngularSpeed,180))*dt);
            if(cancelling) {next.head=next.neck=Vector3.zero;next.eyes=Vector2.zero;}
            next.ownership=release.ownership*fade;next.gazeOwnership=release.gazeOwnership*gazeFade;
            next.armBlend=cancelling?0:release.armBlend*fade;
            next.wristWeight=cancelling?0:release.wristWeight*fade;
            next.grip=cancelling?0:release.grip*fade;
            next.stage=cancelling?"cancelled":"recover";
            if(releasedAt>=duration&&next.spine.sqrMagnitude+next.chest.sqrMagnitude+next.shoulder.sqrMagnitude+
                next.head.sqrMagnitude+next.neck.sqrMagnitude+next.eyes.sqrMagnitude<1e-8f) {
                recovering=cancelling=false;next=new EkuReachMotionOutput {stage="idle"};
            }
        }
        Output=next;return next;
    }
}
