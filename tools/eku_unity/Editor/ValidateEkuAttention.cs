using System;
using UnityEngine;

public static class ValidateEkuAttention {
    static int checks;
    static string failures;
    static void Check(bool value,string reason) {checks++;if(!value)failures+="\n"+reason;}
    static EkuMouseTargetSnapshot Mouse(long seq,double now) => new EkuMouseTargetSnapshot {
        sessionId="attention-test",sequence=seq,timestamp=now,source="System",mappingRevision=1,
        valid=true,positionValid=true,allowAttention=true,insideViewport=true,
        windowPosition=new Vector2(.72f,.5f),windowSize=new Vector2(400,600),dpiScale=1,
        worldPosition=new Vector3(.4f,1.3f,0),distanceToCharacter=0,stationaryDuration=2
    };
    public static void Run() {
        checks=0;failures="";
        var root=new GameObject("Attention avatar");root.transform.rotation=Quaternion.Euler(0,180,0);
        var head=new GameObject("Attention head");head.transform.SetParent(root.transform,false);head.transform.position=Vector3.up*1.3f;
        var cameraObject=new GameObject("Attention camera");var camera=cameraObject.AddComponent<Camera>();
        camera.transform.position=new Vector3(0,1.3f,-3);camera.transform.LookAt(head.transform.position);camera.aspect=2f/3;camera.fieldOfView=45;
        try {
            var settings=new EkuAttentionSettings();
            var context=new EkuAttentionContext{automatic=true,energy=1,acceptance=1,curiosity=.5f};
            var attention=new EkuAttention(camera,root.transform,head.transform,1.5f,settings);
            double now=0;long seq=0;
            Func<int,EkuMouseTargetSnapshot,EkuAttentionOutput> advance=(frames,mouse) => {
                EkuAttentionOutput result=default;
                for(int i=0;i<frames;i++) {now+=1.0/60;mouse.sequence=++seq;mouse.timestamp=now;result=attention.Step(1f/60,now,mouse,context);}
                return result;
            };
            Check(!attention.Step(1f/60,now,default,context).enabled,"legacy input retains fallback ownership");
            var sample=Mouse(1,0);
            var first=advance(3,sample);
            Check(first.state=="Observe" && !first.targetValid,"observation delay gates autonomous targets");
            Check(Mathf.Abs(first.eyes.y)>Mathf.Abs(first.head.y)+Mathf.Abs(first.neck.y),"eyes lead slower head and neck");
            var attending=advance(50,sample);
            Check(attending.targetValid && attending.head.y<0 && attending.eyes.y<0,"screen right maps to anatomical negative yaw");
            Check(attending.state=="Attend","old stationary duration does not skip this attention bout's focus delay");
            Check(attending.head.magnitude>attending.eyes.magnitude,"eyes recenter as head catches up");
            var focused=advance(100,sample);
            Check(focused.state=="Focused" && Mathf.Abs(focused.body.y)>.01f,"slow body layer participates in sustained focus");
            float sum=Mathf.Abs(focused.eyes.y+focused.head.y+focused.neck.y+focused.body.y);
            Check(sum<=settings.maximumTotalDegrees.y+.001f,"combined angular limit");
            Check(Mathf.Abs(focused.eyes.y)<=settings.maximumEyeDegrees.y && Mathf.Abs(focused.head.y)<=settings.maximumHeadDegrees.y,"per-layer limits");
            var previous=focused.eyes+focused.head+focused.neck+new Vector2(focused.body.x,focused.body.y);
            sample.windowPosition.x=.1f;bool bounded=true;
            for(int i=0;i<40;i++) {
                var value=advance(1,sample);var total=value.eyes+value.head+value.neck+new Vector2(value.body.x,value.body.y);
                bounded&=(total-previous).magnitude<=settings.maximumAngularSpeed/60+.001f;previous=total;
            }
            Check(bounded,"combined angular velocity remains bounded during reversal");
            sample.velocity=new Vector2(400,0);sample.speed=400;sample.isMoving=true;
            var predicted=advance(20,sample);
            Check(predicted.predictionDip>1 && predicted.predictionDip<=settings.maximumPredictionDip+.001f,"bounded DIP prediction");
            Check(predicted.worldTarget==sample.worldPosition,"Reach target is never predicted");
            sample.velocity=Vector2.zero;sample.speed=0;sample.isMoving=false;
            Check(advance(60,sample).predictionDip<.01f,"prediction decays after motion stops");
            sample.distanceToCharacter=500;
            var memory=advance(2,sample);
            Check(memory.state=="Remember" && memory.remembered && !memory.targetValid,"memory is explicitly ineligible for Reach");
            Check(memory.worldTarget!=Vector3.zero,"short memory retains the actual last world target");
            var released=advance(120,sample);
            Check(released.weight<.05f,"leaving eventually releases gaze: weight="+released.weight);

            sample=Mouse(1,now);advance(40,sample);context.contact=true;
            var cancelled=advance(40,sample);
            Check(cancelled.enabled && !cancelled.targetValid && !cancelled.remembered && cancelled.state=="Ignore","contact preserves ownership while cancelling target and memory");
            float residual=cancelled.head.magnitude+cancelled.neck.magnitude+cancelled.eyes.magnitude;
            Check(residual<.15f,"contact cancellation recovers pose promptly: degrees="+residual);
            context.contact=false;advance(40,sample);sample.source="RawIntegrated";sample.velocity=new Vector2(1000,0);sample.speed=1000;
            Check(!advance(2,sample).targetValid && attention.Output.state=="Ignore","RawIntegrated cannot invite autonomous attention");
            sample=Mouse(1,now);
            Check(advance(1,sample).novelty==0,"switching from RawIntegrated to a stationary System cursor does not fabricate novelty");
            sample=Mouse(1,now);advance(40,sample);context.blocked=true;context.reason="menu";
            Check(advance(2,sample).reason=="menu" && !attention.Output.remembered,"environmental gate clears memory");
            context.blocked=false;context.automatic=false;sample.forcedLook=true;sample.lookPosition=new Vector2(.1f,.05f);sample.allowAttention=false;
            var explicitLook=advance(30,sample);
            Check(explicitLook.valid && !explicitLook.targetValid && explicitLook.reason=="explicit_look","explicit look bypasses only automatic setting, never enables Reach");
            context.working=true;
            Check(advance(2,sample).state=="Ignore","explicit look cannot bypass working gate");
            context.working=false;context.automatic=true;
            sample=Mouse(1,now);sample.mappingRevision=2;sample.stationaryDuration=0;
            Check(advance(1,sample).state=="Observe","mapping change restarts observation without a snap");
            sample.valid=false;sample.allowAttention=false;sample.invalidReason="PositionUnavailable";
            Check(advance(1,sample).state=="Ignore","unavailable coordinates cannot sustain memory");
            sample=Mouse(1,now);sample.isPressed=true;
            Check(advance(1,sample).reason=="pointer_pressed" && !attention.Output.targetValid,"selection drags outside the pet do not invite tracking");
            attention=new EkuAttention(camera,root.transform,head.transform,1.5f,settings);
            head.transform.position-=Vector3.up*.5f;
            sample=Mouse(1,now);
            Check(advance(60,sample).head.x<0,"lowered base posture looks up to the unchanged screen target");
            head.transform.position+=Vector3.up*.5f;

            settings.maximumAttendSeconds=.6f;settings.minimumAttendSeconds=.25f;settings.observeSeconds=.1f;
            settings.lookAwaySeconds=.2f;settings.cooldownSeconds=.3f;
            attention=new EkuAttention(camera,root.transform,head.transform,1.5f,settings);
            sample=Mouse(1,now);bool sawBreak=false,sawReturn=false;
            for(int i=0;i<120;i++) {
                var value=advance(1,sample);if(value.state=="LookAway")sawBreak=true;
                if(sawBreak && value.targetValid)sawReturn=true;
            }
            Check(sawBreak && sawReturn,"stationary cursor receives bounded bouts, breaks and reacquisition");
            sample.velocity=new Vector2(500,0);sample.speed=500;sample.isMoving=true;
            advance(10,sample);
            var duplicate=Mouse(seq,now);duplicate.velocity=sample.velocity;duplicate.speed=500;duplicate.isMoving=true;
            float beforeNovelty=attention.Output.novelty;
            for(int i=0;i<30;i++) {now+=1.0/60;attention.Step(1f/60,now,duplicate,context);}
            Check(attention.Output.novelty<=beforeNovelty,"duplicate samples cannot accumulate novelty");
            var slowSettings=new EkuAttentionSettings{maximumAngularSpeed=1,maximumAttendSeconds=60};
            attention=new EkuAttention(camera,root.transform,head.transform,1.5f,slowSettings);
            sample=Mouse(1,now);advance(720,sample);context.contact=true;
            var last=attention.Output;bool slowBounded=true;
            for(int i=0;i<240;i++) {
                var value=advance(1,sample);
                var a=last.eyes+last.head+last.neck+new Vector2(last.body.x,last.body.y);
                var b=value.eyes+value.head+value.neck+new Vector2(value.body.x,value.body.y);
                slowBounded&=(b-a).magnitude<=1f/60+.001f;last=value;
            }
            Check(slowBounded && last.valid && last.weight==0,"weight expiry never snaps a slow recovering pose past its angular speed limit");
            context.contact=false;attention=new EkuAttention(camera,root.transform,head.transform,1.5f,settings);
            for(int i=0;i<30;i++)advance(1,sample);
            long before=GC.GetAllocatedBytesForCurrentThread();
            for(int i=0;i<10000;i++) {now+=1.0/60;sample.sequence=++seq;sample.timestamp=now;attention.Step(1f/60,now,sample,context);}
            long allocated=GC.GetAllocatedBytesForCurrentThread()-before;
            Check(allocated==0,"warmed evaluator allocation: "+allocated);
            if(failures!="")throw new Exception("Attention: numerical checks failed"+failures);
            Debug.Log("EKU_ATTENTION_VALIDATED checks="+checks+" allocated="+allocated);
        } finally {
            UnityEngine.Object.DestroyImmediate(cameraObject);UnityEngine.Object.DestroyImmediate(root);
        }
    }
}
