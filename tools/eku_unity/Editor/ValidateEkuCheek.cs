using System;
using System.Collections.Generic;
using UnityEngine;

// Batch entry: -executeMethod ValidateEkuCheek.Run. No scene or model changes.
public static class ValidateEkuCheek {
    static int checks;
    static void Check(bool condition,string message) {
        if(!condition) throw new InvalidOperationException("Cheek validation: "+message);
        checks++;
    }
    static EkuCheekFrame Frame => new EkuCheekFrame{origin=Vector3.zero,right=Vector3.right,
        up=Vector3.up,forward=Vector3.forward,eyeSeparation=1};
    static void Advance(EkuCheek cheek,ref double now,int frames=100) {
        for(int i=0;i<frames;i++) {now+=.01;cheek.Step(.01f,now);}
    }
    public static void Run() {
        checks=0;
        var profile=ScriptableObject.CreateInstance<CheekDeformationProfile>();
        var library=ScriptableObject.CreateInstance<EkuAnimationLibrary>();
        library.clips=Array.Empty<EkuAnimationClip>();
        try {
            var cheek=new EkuCheek(profile);var frame=Frame;
            var point=new Vector3(profile.leftCenter.x,profile.leftCenter.y,0);
            Check(cheek.TryRegion(point,frame,out var side) && side==EkuCheekSide.Left,"anatomical left cheek");
            Check(cheek.TryRegion(new Vector3(profile.rightCenter.x,profile.rightCenter.y,0),frame,out side)
                && side==EkuCheekSide.Right,"anatomical right cheek");
            Check(!cheek.TryRegion(Vector3.zero,frame,out side),"eye/nose center excluded");
            var rotation=Quaternion.Euler(30,75,15);var transformed=frame;
            transformed.origin=new Vector3(7,2,-3);transformed.right=rotation*frame.right;
            transformed.up=rotation*frame.up;transformed.forward=rotation*frame.forward;
            Check(cheek.TryRegion(transformed.origin+rotation*point,transformed,out side)
                && side==EkuCheekSide.Left,"head rotation preserves side");

            int begins=0,ends=0,cancels=0;
            cheek.Changed+=e=>{if(e.phase=="begin")begins++;if(e.phase=="end")ends++;if(e.phase=="cancel")cancels++;};
            double now=0;
            Check(cheek.Begin(1,EkuCheekSide.Left,point,frame,100,now,1),"begin accepted");
            Check(!cheek.Begin(1,EkuCheekSide.Right,point,frame,100,now,1) && begins==1,"duplicate begin ignored");
            Advance(cheek,ref now,10);
            Check(cheek.Wink.x>cheek.Wink.y && cheek.Contact.x>0,"matching side responds first");
            var ray=new Ray(point+Vector3.right*100-Vector3.forward,Vector3.forward);
            Check(cheek.Sample(ray,100.1,now,2),"drag sample accepted");
            Advance(cheek,ref now);
            Check(cheek.Drag.magnitude<=profile.maximumDragDistance+.00001f && cheek.Outside,"drag clamps with outside flag");
            Check(!cheek.Sample(ray,100.1,now,2) && !cheek.Sample(ray,99,now,3),"duplicate/reordered samples ignored");
            float held=cheek.Contact.x;
            Advance(cheek,ref now);
            Check(cheek.Active && cheek.Contact.x>=held && cheek.Speed<.001f,"stationary contact held; speed decays");
            float before=cheek.Contact.x;
            cheek.End(false,now);
            Check(cheek.Contact.x==before && ends==1,"release does not snap or replay begin");
            Advance(cheek,ref now,400);
            Check(cheek.Contact==Vector2.zero && cheek.Drag==Vector2.zero && cheek.HeadDegrees==Vector3.zero,"complete finite recovery");

            var rapid=new EkuCheek(profile);now=0;
            rapid.Begin(1,EkuCheekSide.Left,point,frame,1,now,1);
            Advance(rapid,ref now,5);rapid.End(false,now);
            rapid.Begin(2,EkuCheekSide.Left,point,frame,2,now,2);
            Advance(rapid,ref now,27);
            Check(rapid.Wink.x<.25f && rapid.Stimulus<=1,"rapid poke cannot extend blink deadline");
            for(int i=0;i<50;i++) {rapid.End(false,now);rapid.Begin(i+3,EkuCheekSide.Left,point,frame,i+3,now,i+3);}
            Check(rapid.Stimulus<=1,"stimulus has an upper bound");
            cheek.Begin(2,EkuCheekSide.Right,point,frame,200,now,4);cheek.End(true,now,"menu");
            cheek.End(true,now,"menu");
            Check(cancels==1,"cancel exactly once");
            Advance(cheek,ref now,400);
            Check(cheek.Wink==Vector2.zero && cheek.Contact==Vector2.zero,"cancel recovers without late blink");

            var shapes=new Dictionary<string,int>{{"まばたき",0},{"eye_wink_1_R",1},{"eyebrow_down_R",2},{"cheek_press_R",3}};
            var driver=new EkuFacialDriver(library,shapes);driver.SetCheek(rapid);
            Check(driver.LeftGeometryCapabilities==0 && driver.RightGeometryCapabilities==1,"missing geometry disables only its side/direction");
            Check(driver.MissingShapes.Length>0,"missing names reported");
            var weights=new float[4];var neutral=new float[4];
            driver.ApplyCheek(weights,neutral);
            Check(weights[0]>0 && weights[1]==0,"missing side wink uses a small bilateral fallback");
            rapid.End(true,now);Advance(rapid,ref now,400);
            rapid.Begin(99,EkuCheekSide.Right,point,frame,300,now,99);Advance(rapid,ref now,10);
            weights[0]=100;driver.ApplyCheek(weights,neutral);
            Check(weights[0]==100 && weights[1]==0,"bilateral blink and wink never add closure");

            profile.expressionIntensity=0;Array.Clear(weights,0,weights.Length);
            driver.ApplyCheek(weights,neutral);
            Check(weights[0]==0 && weights[1]==0 && weights[2]==0,"expression gain zero leaves eye/brow channels unchanged");
            profile.deformationStrength=0;Array.Clear(weights,0,weights.Length);
            driver.ApplyCheek(weights,neutral);
            Check(weights[3]==0,"geometry gain is independent of expression gain");

            var testClip=new EkuAnimationClip{curves=new[]{new EkuAnimationCurve{property="blendShape.eye_wink_1_R",curve=AnimationCurve.Constant(0,1,80)}}};
            library.clips=new[]{testClip};var cached=new EkuFacialDriver(library,shapes);
            testClip.curves[0].property="changed_after_binding";Array.Clear(weights,0,weights.Length);
            cached.Sample(testClip,0,.5f,weights);
            Check(Mathf.Abs(weights[1]-40)<.0001f,"curve index is cached by name at initialization");

            profile.expressionIntensity=.55f;profile.deformationStrength=1;
            var split=new EkuCheek(profile);now=0;
            split.Begin(1,EkuCheekSide.Left,point,frame,1,now,1);
            split.Sample(new Ray(point-Vector3.right*.3f-Vector3.forward,Vector3.forward),1.1,now,2);
            Advance(split,ref now,100);
            float leftPull=split.Signal(EkuCheekSide.Left,EkuCheekChannel.Pull);
            Check(leftPull>.4f && split.Signal(EkuCheekSide.Right,EkuCheekChannel.Pull)==0,"directional drag owns its anatomical side");
            split.End(false,now);split.Begin(2,EkuCheekSide.Right,point,frame,2,now,3);
            Advance(split,ref now,1);
            Check(split.LeftDrag.x<-.01f && split.RightDrag==Vector2.zero && split.Contact.x>0 && split.Contact.y>0,
                "switching cheeks preserves the old side's independent recovery");
            float light=split.Pressure.y;
            Advance(split,ref now,180);
            Check(split.Pressure.y>light+.5f,"held contact ramps visual pressure by duration");
            split.Sample(new Ray(point+new Vector3(.3f,.3f,-1),Vector3.forward),2.1,now,4);
            Advance(split,ref now,80);
            float total=0;for(int c=(int)EkuCheekChannel.Press;c<=(int)EkuCheekChannel.Down;c++)total+=split.Signal(EkuCheekSide.Right,(EkuCheekChannel)c);
            Check(total<=1.00001f,"combined pressure and directional geometry share one normalized budget");
            var normal=new EkuCheek(profile);var cancel=new EkuCheek(profile);now=0;
            normal.Begin(1,EkuCheekSide.Left,point,frame,1,now);cancel.Begin(1,EkuCheekSide.Left,point,frame,1,now);
            for(int i=0;i<100;i++){now+=.01;normal.Step(.01f,now);cancel.Step(.01f,now);}
            normal.End(false,now);cancel.End(true,now);
            for(int i=0;i<10;i++){now+=.01;normal.Step(.01f,now);cancel.Step(.01f,now);}
            Check(cancel.Contact.x<normal.Contact.x*.6f,"cancellation recovers faster than ordinary release");

            library.clips=Array.Empty<EkuAnimationClip>();
            var semantic=new Dictionary<string,int>{{"まばたき",0},{"eye_wink_1_L",1},{"eye_wink_1_R",2},
                {"eye_relax",3},{"eyebrow_down_L",4},{"eyebrow_down_R",5},{"mouth_smile_1",6},{"extra_shy_2",7},{"eyelid_test",8}};
            var mixed=new EkuFacialDriver(library,semantic);mixed.SetCheek(split);
            var mixedWeights=new float[9];var mixedNeutral=new float[9];
            mixedWeights[1]=12;mixedWeights[6]=44;mixedWeights[7]=23;
            mixed.ApplyCheek(mixedWeights,mixedNeutral);
            Check(mixedWeights[6]==44 && mixedWeights[7]==23,"cheek preserves explicit mouth and blush without a mapped contact channel");
            Check(mixedWeights[1]>=11.9f,"opposite side's explicit eye shape survives a unilateral contact");
            mixedWeights=new float[]{20,60,40,50,0,0,0,0,30};mixed.NormalizeClosures(mixedWeights);
            Check(mixedWeights[0]==20 && mixedWeights[0]+mixedWeights[1]+mixedWeights[3]<=100.001f
                && mixedWeights[0]+mixedWeights[2]+mixedWeights[3]<=100.001f,"natural blink and both eye closures share bounded per-eye totals");
            Check(mixedWeights[8]==30,"eyelid corrective is excluded from primary closure budget");
            mixedWeights[7]=23;mixed.ApplyHeadPat(mixedWeights,new EkuHeadPatOutput{relax=.8f,smile=.7f},mixedNeutral);
            Check(mixedWeights[7]==23,"head pat preserves unrelated explicit blush channel");
            ValidateHeadPatClosures(library);
            ValidateHeadPatEnjoyment(library);
            ValidateEmotionChannels(library);
            Debug.Log("EKU_CHEEK_VALIDATED checks="+checks);
        } finally {UnityEngine.Object.DestroyImmediate(profile);UnityEngine.Object.DestroyImmediate(library);}
    }
    static void ValidateHeadPatClosures(EkuAnimationLibrary library) {
        var names=new Dictionary<string,int>{{"まばたき",0},{"eye_smile_1",1},{"eye_relax",2},{"eye_surprised",3}};
        var driver=new EkuFacialDriver(library,names);var neutral=new float[4];var values=new float[4];
        driver.ApplyHeadPat(values,new EkuHeadPatOutput{relax=.32f},neutral);driver.NormalizeClosures(values);
        Check(values[1]>20 && values[1]<60,"early gentle pat produces a visible gradual smile-eye closure");
        Array.Clear(values,0,values.Length);values[2]=70;
        driver.ApplyHeadPat(values,new EkuHeadPatOutput{relax=.92f},neutral);driver.NormalizeClosures(values);
        Check(values[1]>99 && values[2]<.001f,"sustained gentle pat closes the eyes with one primary shape");
        values[0]=55;values[2]=80;
        driver.ApplyHeadPat(values,new EkuHeadPatOutput{relax=.92f},neutral);driver.NormalizeClosures(values);
        Check(values[0]==55 && values[0]+values[1]+values[2]<=100.001f,"natural blink and pat closure cannot overfold eyelids");
        Array.Clear(values,0,values.Length);
        driver.ApplyHeadPat(values,new EkuHeadPatOutput{relax=.2f,startle=.22f},neutral);driver.NormalizeClosures(values);
        Check(values[1]<20 && values[3]>20,"rough touch retains open startled eyes rather than full enjoyment closure");
        Array.Clear(values,0,values.Length);
        driver.ApplyHeadPat(values,default(EkuHeadPatOutput),neutral);
        Check(values[0]+values[1]+values[2]+values[3]==0,"recovered head pat leaves no closure or startle");
        var fallback=new EkuFacialDriver(library,new Dictionary<string,int>{{"まばたき",0},{"eye_relax",1}});
        var fallbackValues=new float[2];fallback.ApplyHeadPat(fallbackValues,new EkuHeadPatOutput{relax=.92f},new float[2]);
        fallback.NormalizeClosures(fallbackValues);
        Check(fallbackValues[0]==100 && fallbackValues[1]==0,"missing smile eye shape falls back by name to ordinary blink");
    }
    static void ValidateHeadPatEnjoyment(EkuAnimationLibrary library) {
        var names=new Dictionary<string,int>{{"eye_smile_1",0},{"mouth_smile_1",1},{"mouth_wide",2},
            {"mouth_yaeba_2_L",3},{"eyebrow_tare",4},{"extra_shy_2",5},{"mouth_a_1",6}};
        var driver=new EkuFacialDriver(library,names);var neutral=new float[7];var values=new float[7];
        var pat=new EkuHeadPatOutput{relax=.92f,smile=.828f,earWeights=Vector2.one};
        values[5]=23;driver.ApplyHeadPat(values,pat,neutral);driver.NormalizeClosures(values);
        Check(values[0]<=100 && values[1]>70 && values[2]>35 && values[3]>50 && values[6]>20 && values[6]<35,
            "gentle head pat uses a small open Relax smile while keeping eyelids bounded");
        Check(values[4]>40 && values[5]==23,"head pat coordinates brows and preserves unrelated explicit blush");
        float peak=values[1],openPeak=values[6];Array.Clear(values,0,values.Length);pat.earWeights=Vector2.one*.2f;
        driver.ApplyHeadPat(values,pat,neutral);
        Check(peak-values[1]>10 && openPeak-values[6]>3,"mouth opening and smile follow the ear event and settle between strokes");
        Array.Clear(values,0,values.Length);
        driver.ApplyHeadPat(values,new EkuHeadPatOutput{relax=.2f,smile=.05f,startle=.22f},neutral);
        Check(values[1]<10 && values[2]<5 && values[3]<5 && values[6]<3,"rough touch does not inherit the strong enjoyment mouth");
    }
    static void ValidateEmotionChannels(EkuAnimationLibrary library) {
        string[] names={"まばたき","mouth_smile_1","eyebrow_happy","eyebrow_up","eye_relax","eyebrow_tare",
            "mouth_ω","eyebrow_down","eyebrow_angry","eye_angry","eye_surprised","mouth_o_small","extra_shy_2"};
        var map=new Dictionary<string,int>();for(int i=0;i<names.Length;i++)map[names[i]]=i;
        var signatures=new List<float[]>();
        foreach(string state in new[]{"neutral","happy","curious","shy","sleepy","annoyed","startled"}) {
            var emotion=new EkuEmotion();var driver=new EkuFacialDriver(library,map);var values=new float[names.Length];
            emotion.Context(state=="happy"?1:.45f,state=="sleepy"?0:1,1);
            if(state=="annoyed")for(int i=0;i<8;i++)emotion.Tap();
            var input=new EkuEmotionInput{attention=state=="curious" || state=="startled"?1:0,
                novelty=state=="startled"?1:0,contact=state=="shy",faceContact=state=="shy",resting=state=="sleepy"};
            int frames=state=="startled"?24:state=="annoyed"?40:1200;
            for(int i=0;i<frames;i++) {
                emotion.Observe(input);emotion.Step(.01f,state=="shy"?.2f:0,0);
                Array.Clear(values,0,values.Length);driver.ApplyEmotion(values,emotion,.01f);driver.NormalizeClosures(values);
            }
            foreach(var other in signatures) {
                float difference=0;for(int i=0;i<values.Length;i++)difference+=Mathf.Abs(values[i]-other[i]);
                Check(difference>.5f,state+" renders a distinct continuous facial mix");
            }
            Check(values[12]==0,"unreviewed blush overlay remains disabled by default");
            signatures.Add(values);
        }
    }
}
