using System;
using System.Collections.Generic;
using UnityEngine;

public enum EkuFacialChannel { Eyes, Eyebrows, Mouth, Blush, Cheek, Gaze, Other }
[Serializable] public sealed class EkuFacialSettings {
    [Range(0,1)] public float emotionIntensity=.8f;
    [Min(.02f)] public float emotionResponseSeconds=.25f;
    [Range(0,1)] public float touchEyeOverride=1,touchBrowOverride=.8f,touchMouthOverride=.65f;
    // This avatar's blush is overlay geometry; enable only after a rendered check.
    public bool enableBlush;
    [Range(0,1)] public float blushIntensity=1;
}

// Buffer composition only. EkuAnimationPlayer remains the sole facial mesh writer.
public sealed class EkuFacialDriver {
    struct CurveBinding { public int index;public AnimationCurve curve; }
    struct CheekBinding { public int index;public EkuCheekBinding binding; }
    struct ShapeBinding { public int index,side;public EkuFacialChannel channel;public bool closure; }
    readonly Dictionary<string,int> shapes;
    readonly Dictionary<EkuAnimationClip,CurveBinding[]> curves=new Dictionary<EkuAnimationClip,CurveBinding[]>();
    readonly ShapeBinding[] channels;
    readonly int blink,relax,patClose,smile,patWide,patFang,patOpen,browDown,browTare,browUp,browHappy,browAngry,angry,surprise,mouthRound,mouthShy,blush;
    EkuFacialSettings settings=new EkuFacialSettings();
    EkuCheek cheek;
    CheekBinding[] cheeks=Array.Empty<CheekBinding>();
    EkuEmotionWeights emotionWeights;
    public int LeftGeometryCapabilities { get; private set; }
    public int RightGeometryCapabilities { get; private set; }
    public string[] MissingShapes { get; private set; }=Array.Empty<string>();
    int Index(string name) => shapes.TryGetValue(name,out int index)?index:-1;
    static EkuFacialChannel Channel(string name) {
        if(name.StartsWith("eye_look",StringComparison.Ordinal) || name.StartsWith("vrc.looking",StringComparison.Ordinal))return EkuFacialChannel.Gaze;
        if(name.StartsWith("eyebrow_",StringComparison.Ordinal))return EkuFacialChannel.Eyebrows;
        if(name.StartsWith("mouth_",StringComparison.Ordinal) || name.StartsWith("tongue_",StringComparison.Ordinal))return EkuFacialChannel.Mouth;
        if(name.StartsWith("extra_shy",StringComparison.Ordinal))return EkuFacialChannel.Blush;
        if(name.StartsWith("cheek_",StringComparison.Ordinal) || name.StartsWith("face_cheek",StringComparison.Ordinal))return EkuFacialChannel.Cheek;
        if(name=="まばたき" || name.StartsWith("eyelid_",StringComparison.Ordinal) ||
            (name.StartsWith("eye_",StringComparison.Ordinal) && !name.StartsWith("eye_highlight",StringComparison.Ordinal) && !name.StartsWith("eye_pupil",StringComparison.Ordinal)))return EkuFacialChannel.Eyes;
        return EkuFacialChannel.Other;
    }
    public EkuFacialDriver(EkuAnimationLibrary library,Dictionary<string,int> shapes) {
        this.shapes=shapes;
        blink=Index("まばたき");relax=Index("eye_relax");smile=Index("mouth_smile_1");
        patClose=Index("eye_smile_1");if(patClose<0)patClose=blink>=0?blink:relax;
        patWide=Index("mouth_wide");patFang=Index("mouth_yaeba_2_L");
        patOpen=Index("mouth_a_1");
        browDown=Index("eyebrow_down");browTare=Index("eyebrow_tare");browUp=Index("eyebrow_up");
        browHappy=Index("eyebrow_happy");browAngry=Index("eyebrow_angry");
        angry=Index("eye_angry");surprise=Index("eye_surprised");mouthRound=Index("mouth_o_small");
        mouthShy=Index("mouth_ω");blush=Index("extra_shy_2");
        foreach(var clip in library.clips) {
            var bound=new List<CurveBinding>();
            foreach(var curve in clip.curves) if(curve.property.StartsWith("blendShape.",StringComparison.Ordinal)
                && shapes.TryGetValue(curve.property.Substring(11),out int index))
                bound.Add(new CurveBinding{index=index,curve=curve.curve});
            curves[clip]=bound.ToArray();
        }
        var all=new List<ShapeBinding>();
        foreach(var shape in shapes) {
            string n=shape.Key;
            all.Add(new ShapeBinding{index=shape.Value,channel=Channel(n),
                side=n.EndsWith("_L",StringComparison.Ordinal)?1:n.EndsWith("_R",StringComparison.Ordinal)?2:3,
                closure=n=="まばたき" || n=="eye_relax" || n.StartsWith("eye_blink_",StringComparison.Ordinal)
                    || n.StartsWith("eye_smile_",StringComparison.Ordinal) || n.StartsWith("eye_wink_",StringComparison.Ordinal)});
        }
        channels=all.ToArray();
    }
    public void Configure(EkuFacialSettings value) {settings=value??new EkuFacialSettings();}
    public void Sample(EkuAnimationClip clip,float time,float weight,float[] weights) {
        if(clip==null || !curves.TryGetValue(clip,out var bound)) return;
        foreach(var curve in bound) weights[curve.index]=Mathf.Lerp(weights[curve.index],curve.curve.Evaluate(time),weight);
    }
    static void Blend(float[] weights,int index,float target,float amount) {
        if(index>=0) weights[index]=Mathf.Lerp(weights[index],target,Mathf.Clamp01(amount));
    }
    public void Suppress(float[] weights,float[] neutral,EkuFacialChannel channel,float amount,int side=3) {
        if(neutral==null || amount<=0)return;
        foreach(var binding in channels)if(binding.index!=blink && binding.channel==channel && (binding.side&side)!=0)
            weights[binding.index]=Mathf.Lerp(weights[binding.index],neutral[binding.index],Mathf.Clamp01(amount));
    }
    public void ApplyEmotion(float[] weights,EkuEmotion emotion,float dt) {
        if(emotion==null) return;
        var target=emotion.Weights;
        float k=1-Mathf.Exp(-Mathf.Clamp(dt,0,.05f)/Mathf.Max(.02f,settings.emotionResponseSeconds));
        emotionWeights.happy=Mathf.Lerp(emotionWeights.happy,target.happy,k);
        emotionWeights.curious=Mathf.Lerp(emotionWeights.curious,target.curious,k);
        emotionWeights.shy=Mathf.Lerp(emotionWeights.shy,target.shy,k);
        emotionWeights.sleepy=Mathf.Lerp(emotionWeights.sleepy,target.sleepy,k);
        emotionWeights.annoyed=Mathf.Lerp(emotionWeights.annoyed,target.annoyed,k);
        emotionWeights.startled=Mathf.Lerp(emotionWeights.startled,target.startled,1-Mathf.Exp(-Mathf.Clamp(dt,0,.05f)/.06f));
        var w=emotionWeights;float gain=Mathf.Clamp01(settings.emotionIntensity);
        Blend(weights,smile,100,w.happy*.5f*gain);
        Blend(weights,browHappy,100,w.happy*.2f*gain);
        Blend(weights,browUp,100,w.curious*.3f*gain);
        Blend(weights,relax,100,(w.shy*.25f+w.sleepy*.9f)*gain);
        Blend(weights,browTare,100,(w.shy*.4f+w.sleepy*.18f)*gain);
        Blend(weights,mouthShy,100,w.shy*.25f*gain);
        Blend(weights,browDown,100,w.annoyed*.35f*gain);
        Blend(weights,browAngry,100,w.annoyed*.2f*gain);
        Blend(weights,angry,100,w.annoyed*.3f*gain);
        Blend(weights,surprise,100,(w.curious*.12f+w.startled*.7f)*gain);
        Blend(weights,mouthRound,100,w.startled*.22f*gain);
        if(settings.enableBlush)Blend(weights,blush,100,Mathf.Clamp01(w.shy/.35f)*Mathf.Clamp01(settings.blushIntensity)*gain);
        float reaction=emotion.ReactionWeight*gain;
        if(emotion.Reaction=="startle" || emotion.Reaction=="retreat")Blend(weights,surprise,100,reaction*.4f);
        else if(emotion.Reaction=="poke") {
            if(emotion.Response=="warm")Blend(weights,smile,100,reaction*.25f);
            else if(emotion.Response=="bashful")Blend(weights,browTare,100,reaction*.25f);
            else if(emotion.Response=="reluctant")Blend(weights,browDown,100,reaction*.2f);
        }
    }
    public void ApplyHeadPat(float[] weights,EkuHeadPatOutput pat,float[] neutral=null) {
        // The relaxed shape alone leaves the eyes half open. Use one existing
        // closed smile shape, sharing the final closure budget with blinks.
        float closure=Mathf.SmoothStep(0,1,Mathf.Clamp01(pat.relax/.85f));
        float accent=Mathf.Clamp01(Mathf.Max(Mathf.Max(pat.earWeights.x,pat.earWeights.y),Mathf.Max(pat.earLiftWeights.x,pat.earLiftWeights.y)));
        float enjoyment=Mathf.Clamp01(pat.smile)*(.8f+.2f*accent);
        float amount=Mathf.Max(pat.relax,pat.startle);
        Suppress(weights,neutral,EkuFacialChannel.Eyes,Mathf.Max(closure,pat.startle)*Mathf.Clamp01(settings.touchEyeOverride));
        Suppress(weights,neutral,EkuFacialChannel.Eyebrows,amount*Mathf.Clamp01(settings.touchBrowOverride));
        Suppress(weights,neutral,EkuFacialChannel.Mouth,enjoyment*Mathf.Clamp01(settings.touchMouthOverride));
        Blend(weights,patClose,100,closure);
        Blend(weights,browTare,55,enjoyment);Blend(weights,browDown,40,pat.relax);
        Blend(weights,browHappy,15,enjoyment);
        // Recover the original Relax mouth's readable shape, without its blush overlay.
        Blend(weights,smile,100,enjoyment);Blend(weights,patWide,50.8f,enjoyment);Blend(weights,patFang,80,enjoyment);
        Blend(weights,patOpen,35,enjoyment);
        Blend(weights,surprise,100,pat.startle);
    }
    public void SetCheek(EkuCheek value) {
        cheek=value;LeftGeometryCapabilities=RightGeometryCapabilities=0;
        var resolved=new List<CheekBinding>();var missing=new List<string>();
        if(cheek!=null && cheek.Profile.bindings!=null) foreach(var binding in cheek.Profile.bindings) {
            if(binding==null || string.IsNullOrWhiteSpace(binding.shape)) continue;
            if(!shapes.TryGetValue(binding.shape,out int index)) {missing.Add(binding.shape);continue;}
            resolved.Add(new CheekBinding{index=index,binding=binding});
            if(binding.channel>=EkuCheekChannel.Press) {
                int bit=1<<((int)binding.channel-(int)EkuCheekChannel.Press);
                if(binding.side==EkuCheekSide.Left) LeftGeometryCapabilities|=bit;else RightGeometryCapabilities|=bit;
            }
        }
        cheeks=resolved.ToArray();MissingShapes=missing.ToArray();
    }
    public void ApplyCheek(float[] weights,float[] neutral) {
        if(cheek==null)return;
        int eyes=0,brows=0,mouth=0,blushSides=0;
        foreach(var item in cheeks) {
            int side=item.binding.side==EkuCheekSide.Left?1:2;
            switch(item.binding.channel) {
                case EkuCheekChannel.Eye:eyes|=side;break;
                case EkuCheekChannel.Eyebrow:brows|=side;break;
                case EkuCheekChannel.Mouth:mouth|=side;break;
                case EkuCheekChannel.Blush:blushSides|=side;break;
            }
        }
        float expression=Mathf.Clamp01(cheek.Profile.expressionIntensity*3)*Mathf.Clamp01(cheek.Acceptance);
        for(int side=1;side<=2;side++) {
            float contact=(side==1?cheek.Contact.x:cheek.Contact.y),amount=contact*expression;
            if((eyes&side)!=0)Suppress(weights,neutral,EkuFacialChannel.Eyes,amount*Mathf.Clamp01(settings.touchEyeOverride),side);
            if((brows&side)!=0)Suppress(weights,neutral,EkuFacialChannel.Eyebrows,amount*Mathf.Clamp01(settings.touchBrowOverride),side);
            if((mouth&side)!=0)Suppress(weights,neutral,EkuFacialChannel.Mouth,amount*Mathf.Clamp01(settings.touchMouthOverride),side);
            if((blushSides&side)!=0)Suppress(weights,neutral,EkuFacialChannel.Blush,amount,side);
            if((side==1?LeftGeometryCapabilities:RightGeometryCapabilities)!=0)
                Suppress(weights,neutral,EkuFacialChannel.Cheek,contact*Mathf.Clamp01(cheek.Profile.deformationStrength*3),side);
        }
        foreach(var item in cheeks) {
            var binding=item.binding;float signal=cheek.Signal(binding.side,binding.channel);
            if(signal<=0)continue;
            float response=binding.response==null?signal:binding.response.Evaluate(signal);
            weights[item.index]=Mathf.Max(weights[item.index],Mathf.Clamp01(response)*Mathf.Clamp(binding.maximumWeight,0,100));
        }
        if(blink>=0) {
            float fallback=Mathf.Max((eyes&1)!=0?0:cheek.Signal(EkuCheekSide.Left,EkuCheekChannel.Eye),
                (eyes&2)!=0?0:cheek.Signal(EkuCheekSide.Right,EkuCheekChannel.Eye));
            weights[blink]=Mathf.Max(weights[blink],fallback*60);
        }
        NormalizeClosures(weights);
    }
    public void NormalizeClosures(float[] weights) {
        float reserved=blink>=0?Mathf.Clamp(weights[blink],0,100):0,shared=0,left=0,right=0;
        foreach(var item in channels)if(item.closure && item.index!=blink) {
            float value=Mathf.Max(0,weights[item.index]);
            if(item.side==3)shared+=value;else if(item.side==1)left+=value;else right+=value;
        }
        float scale=Mathf.Min(1,(100-reserved)/Mathf.Max(.0001f,shared+Mathf.Max(left,right)));
        foreach(var item in channels)if(item.closure && item.index!=blink)weights[item.index]*=scale;
    }
}
