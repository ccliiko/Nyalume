using System;
using UnityEngine;

// Anatomical frame comes from eye/head positions, not the imported bone's local axes.
public struct EkuCheekFrame {
    public Vector3 origin,right,up,forward;
    public float eyeSeparation;
    public static bool TryCreate(Animator rig,out EkuCheekFrame frame) {
        frame=default;
        var left=rig.GetBoneTransform(HumanBodyBones.LeftEye);var right=rig.GetBoneTransform(HumanBodyBones.RightEye);
        var head=rig.GetBoneTransform(HumanBodyBones.Head);var neck=rig.GetBoneTransform(HumanBodyBones.Neck);
        if(!left || !right || !head || !neck) return false;
        var across=right.position-left.position;float width=across.magnitude;
        if(width<.00001f) return false;
        frame.origin=(left.position+right.position)*.5f;frame.right=across/width;
        frame.up=Vector3.ProjectOnPlane(head.position-neck.position,frame.right).normalized;
        if(frame.up.sqrMagnitude<.5f) return false;
        frame.forward=Vector3.Cross(frame.right,frame.up).normalized;
        if(Vector3.Dot(frame.forward,frame.origin-head.position)<0) frame.forward=-frame.forward;
        frame.eyeSeparation=width;return true;
    }
}

[Serializable] public struct EkuCheekEvent {
    public int id;
    public long sampleSeq;
    public double at;
    public string phase,reason;
    public EkuCheekSide side;
    public Vector2 drag;
    public float duration,speed,stimulus;
}

[Serializable] public struct EkuCheekStatus {
    public bool active;
    public string side,phase;
    public Vector2 drag,dragLeft,dragRight,contact,wink,pressure;
    public bool dragging;
    public float speed,stimulus;
}

// Evaluates contact only. Events are facts for Reaction/Emotion; this class chooses no mood.
public sealed class EkuCheek {
    public readonly CheekDeformationProfile Profile;
    public event Action<EkuCheekEvent> Changed;
    public bool Active { get; private set; }
    public EkuCheekSide Side { get; private set; }
    public Vector2 Drag { get; private set; }
    public Vector2 Contact { get; private set; }
    public Vector2 Wink { get; private set; }
    public Vector2 Pressure { get; private set; }
    public Vector2 LeftDrag { get; private set; }
    public Vector2 RightDrag { get; private set; }
    public Vector3 HeadDegrees { get; private set; }
    public float Speed { get; private set; }
    public float Stimulus { get; private set; }
    public float Acceptance=1;
    public bool Outside { get; private set; }
    int id;
    long sequence;
    double beganAt,lastAt,lastReceived,nextReaction;
    readonly double[] pulseAt={double.NegativeInfinity,double.NegativeInfinity};
    Vector2 targetDrag,previousDrag;
    Vector3 capturedPoint,capturedRight,capturedUp;
    Plane capturedPlane;
    float faceWidth;
    bool cancelled;

    public EkuCheek(CheekDeformationProfile profile) {Profile=profile;}
    static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    public static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
    static float Component(Vector2 value,EkuCheekSide side) => side==EkuCheekSide.Left?value.x:value.y;
    public bool TryRegion(Vector3 worldPoint,EkuCheekFrame frame,out EkuCheekSide side) {
        side=default;
        if(frame.eyeSeparation<.00001f || !Finite(worldPoint.x) || !Finite(worldPoint.y) || !Finite(worldPoint.z)) return false;
        var d=worldPoint-frame.origin;
        var p=new Vector2(Vector3.Dot(d,frame.right),Vector3.Dot(d,frame.up))/frame.eyeSeparation;
        side=p.x<0?EkuCheekSide.Left:EkuCheekSide.Right;
        var center=side==EkuCheekSide.Left?Profile.leftCenter:Profile.rightCenter;
        var offset=p-center;
        var radii=Profile.regionRadii;
        return radii.x>0 && radii.y>0 && Mathf.Abs(p.x)>.12f
            && offset.x*offset.x/(radii.x*radii.x)+offset.y*offset.y/(radii.y*radii.y)<=1;
    }
    public bool Begin(int contactId,EkuCheekSide side,Vector3 worldPoint,EkuCheekFrame frame,
                      double sourceAt,double now,long sampleSeq=0) {
        if(Active && id==contactId) return false;
        if(!Finite(sourceAt) || !Finite(now) || frame.eyeSeparation<.00001f) return false;
        Active=true;cancelled=false;Side=side;id=contactId;sequence=sampleSeq;beganAt=now;lastAt=sourceAt;lastReceived=now;
        capturedPoint=worldPoint;capturedRight=frame.right;capturedUp=frame.up;
        capturedPlane=new Plane(frame.forward,worldPoint);
        faceWidth=frame.eyeSeparation*Mathf.Max(1,Profile.faceWidthInEyeSeparations);
        targetDrag=previousDrag=Vector2.zero;Speed=0;Outside=false;
        Stimulus=Mathf.Clamp01(Stimulus+.22f);
        if(now>=nextReaction) {
            pulseAt[(int)side]=now;
            nextReaction=now+Mathf.Max(.1f,Profile.reactionCooldownSeconds);
        }
        Emit("begin","",sourceAt,now);return true;
    }
    public bool Sample(Ray ray,double sourceAt,double now,long sampleSeq=0) {
        if(!Active || !Finite(sourceAt) || !Finite(now) || sourceAt<=lastAt
            || (sampleSeq>0 && sequence>0 && sampleSeq<=sequence)) return false;
        if(!capturedPlane.Raycast(ray,out float distance) || !Finite(distance)) {Outside=true;return false;}
        var delta=ray.GetPoint(distance)-capturedPoint;
        var raw=new Vector2(Vector3.Dot(delta,capturedRight),Vector3.Dot(delta,capturedUp))/faceWidth;
        if(!Finite(raw.x) || !Finite(raw.y)) return false;
        float limit=Mathf.Clamp(Profile.maximumDragDistance,.02f,.5f);
        Outside=raw.magnitude>limit*3;
        targetDrag=Vector2.ClampMagnitude(raw,limit);
        Speed=Mathf.Clamp((targetDrag-previousDrag).magnitude/(float)Math.Max(.001,sourceAt-lastAt),0,20);
        previousDrag=targetDrag;lastAt=sourceAt;lastReceived=now;sequence=sampleSeq;
        Emit("update","",sourceAt,now);return true;
    }
    public void End(bool cancel,double now,string reason="") {
        if(!Active) return;
        Emit(cancel?"cancel":"end",reason,lastAt,now);
        Active=false;cancelled=cancel;Outside=false;targetDrag=Vector2.zero;Speed=0;
        if(cancel) {pulseAt[0]=pulseAt[1]=double.NegativeInfinity;}
    }
    void Emit(string phase,string reason,double sourceAt,double now) {
        Changed?.Invoke(new EkuCheekEvent{id=id,sampleSeq=sequence,at=sourceAt,phase=phase,reason=reason,
            side=Side,drag=targetDrag,duration=(float)Math.Max(0,now-beganAt),speed=Speed,stimulus=Stimulus});
    }
    static float Pulse(double age) {
        if(age<0 || age>=.27) return 0;
        return Mathf.SmoothStep(0,1,(float)age/.045f)*Mathf.SmoothStep(0,1,(.27f-(float)age)/.17f);
    }
    public void Step(float deltaTime,double now) {
        if(!Finite(deltaTime) || !Finite(now) || deltaTime<=0) return;
        float dt=Mathf.Min(deltaTime,.05f);
        float k=Active?Mathf.Clamp(Profile.responseSpeed,1,30):
            Mathf.Clamp(Profile.recoverySpeed,1,30)*(cancelled?Mathf.Clamp(Profile.cancelRecoveryMultiplier,1,4):1);
        float alpha=1-Mathf.Exp(-k*dt);
        var target=Active?(Side==EkuCheekSide.Left?Vector2.right:Vector2.up):Vector2.zero;
        Contact=Vector2.Lerp(Contact,target,alpha);
        LeftDrag=Vector2.Lerp(LeftDrag,Active && Side==EkuCheekSide.Left?targetDrag:Vector2.zero,alpha);
        RightDrag=Vector2.Lerp(RightDrag,Active && Side==EkuCheekSide.Right?targetDrag:Vector2.zero,alpha);
        Drag=Side==EkuCheekSide.Left?LeftDrag:RightDrag;
        float age=(float)Math.Max(0,now-beganAt);
        float heldPressure=Mathf.Lerp(Mathf.Clamp01(Profile.initialPress),1,
            Mathf.SmoothStep(0,1,age/Mathf.Max(.1f,Profile.holdPressSeconds)));
        Pressure=Vector2.Lerp(Pressure,target*heldPressure,alpha);
        var blink=new Vector2(Pulse(now-pulseAt[0]),Pulse(now-pulseAt[1]));
        // A held finger keeps a small squint; it does not keep the eye fully closed.
        blink=Vector2.Max(blink,Contact*.16f);
        float blinkRate=Active || blink.sqrMagnitude>Wink.sqrMagnitude?45:Mathf.Clamp(Profile.recoverySpeed,1,30);
        Wink=Vector2.Lerp(Wink,blink,1-Mathf.Exp(-blinkRate*dt));
        if(now-lastReceived>.12) Speed*=Mathf.Exp(-12*dt);
        Stimulus*=Mathf.Exp(-dt/1.4f);
        float side=Contact.y-Contact.x;
        float max=Mathf.Clamp(Profile.maximumHeadDegrees,0,8);
        var combinedDrag=LeftDrag+RightDrag;
        var angles=new Vector3(-combinedDrag.y*max*2,combinedDrag.x*max,side*max*.55f+combinedDrag.x*max*2);
        HeadDegrees=Vector3.ClampMagnitude(angles,max)*Mathf.Clamp01(Acceptance);
        if(!Active && Contact.sqrMagnitude<1e-7f && Wink.sqrMagnitude<1e-7f) {
            Contact=Wink=Drag=LeftDrag=RightDrag=Pressure=Vector2.zero;HeadDegrees=Vector3.zero;
        }
    }
    public float Signal(EkuCheekSide side,EkuCheekChannel channel) {
        float contact=Component(Contact,side),expression=Mathf.Clamp01(Profile.expressionIntensity);
        float acceptance=Mathf.Clamp01(Acceptance);
        if(channel==EkuCheekChannel.Eye) return Mathf.Clamp01(Component(Wink,side)*expression*acceptance);
        if(channel<=EkuCheekChannel.Blush) return Mathf.Clamp01(contact*expression*acceptance);
        float limit=Mathf.Clamp(Profile.maximumDragDistance,.02f,.5f);
        var drag=side==EkuCheekSide.Left?LeftDrag:RightDrag;
        float x=drag.x/limit*(side==EkuCheekSide.Left?-1:1)*contact,y=drag.y/limit*contact;
        float press=Component(Pressure,side)*(1-.75f*Mathf.Clamp01(drag.magnitude/limit));
        float budget=Mathf.Max(1,press+Mathf.Abs(x)+Mathf.Abs(y));
        float value=channel==EkuCheekChannel.Press?press:channel==EkuCheekChannel.Pull?Mathf.Max(0,x):
            channel==EkuCheekChannel.Push?Mathf.Max(0,-x):channel==EkuCheekChannel.Up?Mathf.Max(0,y):Mathf.Max(0,-y);
        return Mathf.Clamp01(value/budget*Mathf.Clamp01(Profile.deformationStrength)*acceptance);
    }
    public EkuCheekStatus Status() => new EkuCheekStatus{active=Active,side=Side.ToString(),
        phase=Active?(Drag.sqrMagnitude>.000025f?"drag":"contact"):Contact.sqrMagnitude>0?"recover":"idle",
        dragging=Active && Drag.sqrMagnitude>.000025f,drag=Drag,dragLeft=LeftDrag,dragRight=RightDrag,
        pressure=Pressure,contact=Contact,wink=Wink,speed=Speed,stimulus=Stimulus};
}
