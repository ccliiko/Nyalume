using System;

// Seconds and normalized 0..1 values. Serialized on EkuComparison, without another Update.
[Serializable] public sealed class EkuEmotionSettings {
    public float comfortRise=.08f,comfortDecaySeconds=20,stimulationDecaySeconds=4;
    public float tapStimulus=.18f,roughStimulusPerSecond=.35f;
    public float annoyedEnter=.65f,annoyedExit=.35f,annoyedHoldSeconds=.8f;
    public float reactionCooldown=1.5f,startleCooldown=4,reactionDuration=.75f;
    public float arousalRiseSeconds=.18f,arousalFallSeconds=2.2f,curiosityRiseSeconds=.7f,curiosityFallSeconds=2.5f;
    public float shynessSensitivity=.9f,shyContactDelay=.5f,shyRiseSeconds=.9f,shyFallSeconds=3;
    public float sleepyRiseSeconds=8,sleepyFallSeconds=2,idleSleepStart=90,idleSleepFull=300;
    public float stateHoldSeconds=.45f,stateHysteresis=.08f,refusalThreshold=.9f,refusalSeconds=.45f,refusalCooldown=3;
}

[Serializable] public struct EkuEmotionInput {
    public float attention,novelty,idleSeconds;
    public bool contact,faceContact,resting,working,inhibited;
    public int side; // Character's left=-1, right=1; zero means unknown.
}
[Serializable] public struct EkuEmotionWeights {
    public float neutral,curious,happy,shy,startled,annoyed,sleepy;
}
[Serializable] public struct EkuEmotionPose {
    public float pitch,yaw,roll,gazeAvoidance;
}
[Serializable] public struct EkuEmotionBehavior {
    public float engagement,playfulness,rest,retreat;
}

[Serializable] public sealed class EkuEmotionStatus {
    public int version=1;
    public int modelVersion=2;
    public float mood,energy,comfort,stimulation,annoyed,happy,sleepy,acceptance;
    public float arousal,curiosity,shy,startled;
    public float reactionWeight,reactionRemaining,cooldownRemaining;
    public bool interruptsBehavior;
    public string reaction,response,state,cancelReason,requestedCancel;
    public EkuEmotionWeights weights;
    public EkuEmotionPose pose;
    public EkuEmotionBehavior behavior;
}

// Python alone owns mood/energy. This class owns only transient state and envelopes.
// Explicit time steps keep the numerical model testable without Unity or a wall clock.
public sealed class EkuEmotion {
    public readonly EkuEmotionSettings Settings;
    public float Mood { get; private set; }=.6f;
    public float Energy { get; private set; }=1;
    public float Comfort { get; private set; }
    public float Stimulation { get; private set; }
    public float Annoyed { get; private set; }
    public float Arousal { get; private set; }
    public float Curiosity { get; private set; }
    public float Shy { get; private set; }
    public float Startled => Reaction=="startle" || Reaction=="retreat"?ReactionWeight:0;
    public bool IsAnnoyed { get; private set; }
    public float Happy => Unit((Mood-.45f)*1.1f+Comfort*.75f)*(1-Annoyed);
    public float Curious => Curiosity*(1-Annoyed*.8f)*(1-Sleepy*.8f);
    public float Sleepy { get; private set; }
    public float Acceptance => Unit(1-.65f*Annoyed-.15f*Sleepy-.12f*Startled);
    public EkuEmotionWeights Weights { get; private set; }
    public EkuEmotionPose Pose { get; private set; }
    public EkuEmotionBehavior Behavior { get; private set; }
    public string State { get; private set; }="Neutral";
    public string Reaction { get; private set; }="";
    public string Response { get; private set; }="";
    public string CancelReason { get; private set; }="";
    public string RequestedCancel { get; private set; }="";
    public bool InterruptsBehavior => (Reaction=="startle" || Reaction=="retreat") && ReactionWeight>.05f;
    public float ReactionRemaining => (float)Math.Max(0,reactionEnd-clock);
    public float ReactionWeight {
        get {
            if(Reaction=="" || clock>=reactionEnd) return 0;
            return amplitude*Unit((float)(clock-reactionAt)/.1f)*Unit((float)(reactionEnd-clock)/.3f);
        }
    }
    double clock,reactionAt,reactionEnd,nextReaction,nextStartle,annoyedUntil,stateAt,nextRefusal;
    float amplitude,roughTime,faceAge,noveltyTime;
    bool roughLatched,noveltyLatched,refusalLatched;
    int revision=-1;
    int lastSide;
    EkuEmotionInput input;

    public EkuEmotion(EkuEmotionSettings settings=null) {Settings=settings??new EkuEmotionSettings();UpdateAppearance();}
    public static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    static float Unit(float value) => Finite(value)?Math.Max(0,Math.Min(1,value)):0;
    static float Positive(float value,float fallback) => Finite(value) && value>0?value:fallback;
    static float Smooth(float value,float target,float elapsed,float seconds) => target+(value-target)*(float)Math.Exp(-elapsed/seconds);
    public void Observe(EkuEmotionInput value) {
        value.attention=Unit(value.attention);value.novelty=Unit(value.novelty);
        value.idleSeconds=Finite(value.idleSeconds)?Math.Max(0,value.idleSeconds):0;
        value.side=Math.Sign(value.side);input=value;
        if(value.contact && value.side!=0)lastSide=value.side;
    }
    public void Context(float mood,float energy,int sequence) {
        if(sequence<=revision || !Finite(mood) || !Finite(energy)) return;
        revision=sequence;Mood=Unit(mood);Energy=Unit(energy);
    }
    void EvaluateAnnoyance() {
        float enter=Unit(Settings.annoyedEnter),exit=Math.Min(enter,Unit(Settings.annoyedExit));
        if(!IsAnnoyed && Stimulation>=enter) {
            IsAnnoyed=true;annoyedUntil=clock+Positive(Settings.annoyedHoldSeconds,.8f);
        } else if(IsAnnoyed && clock>=annoyedUntil && Stimulation<=exit) IsAnnoyed=false;
    }
    string CurrentResponse() {
        if(IsAnnoyed || Annoyed>.4f) return "reluctant";
        if(Sleepy>.45f) return "drowsy";
        if(Shy>.3f) return "bashful";
        if(Comfort>.25f || Happy>.35f) return "warm";
        return "curious";
    }
    bool React(string kind,float strength) {
        if(Reaction!="" && clock<reactionEnd) {
            amplitude=Unit(Math.Max(amplitude,strength)); // Merge, never extend the deadline.
            if(kind=="startle" && Reaction=="poke" && clock>=nextStartle) {
                Reaction=kind;Response="surprised";reactionAt=clock;
                reactionEnd=clock+Positive(Settings.reactionDuration,.75f);
                nextReaction=Math.Max(nextReaction,clock+Positive(Settings.reactionCooldown,1.5f));
                nextStartle=clock+Positive(Settings.startleCooldown,4);
            }
            return false;
        }
        if(clock<nextReaction || (kind=="startle" && clock<nextStartle)) return false;
        Reaction=kind;Response=kind=="startle"?"surprised":kind=="retreat"?"reluctant":CurrentResponse();
        amplitude=Unit(strength);reactionAt=clock;
        reactionEnd=clock+Positive(Settings.reactionDuration,.75f);
        nextReaction=clock+Positive(Settings.reactionCooldown,1.5f);
        if(kind=="startle") nextStartle=clock+Positive(Settings.startleCooldown,4);
        return true;
    }
    public bool Tap(string region="face",int side=0) {
        if(input.inhibited) return false;
        if(side!=0)lastSide=Math.Sign(side);
        float regionAmount=region=="head"?.35f:region=="hand"?.5f:1;
        Stimulation=Unit(Stimulation+Unit(Settings.tapStimulus)*regionAmount);EvaluateAnnoyance();
        return React("poke",IsAnnoyed?.6f:Unit(.35f+.2f*Comfort+.15f*Arousal));
    }
    public string ConsumeCancelReason() {
        string reason=RequestedCancel;RequestedCancel="";return reason;
    }
    public void Cancel(string reason) {
        CancelReason=reason??"";Reaction=Response="";reactionEnd=clock;RequestedCancel="";
        roughTime=0;roughLatched=false;
        // Comfort, stimulus and cooldown survive cancellation; only the temporary layer exits.
        UpdateAppearance();
    }
    public void Step(float elapsed,float gentle,float rough) {
        if(!Finite(elapsed) || elapsed<=0) return;
        float dt=elapsed;clock+=dt;gentle=Unit(gentle);rough=Unit(rough);
        if(elapsed>.2f) gentle=rough=0; // A suspended frame never fabricates touch time.
        bool fresh=elapsed<=.2f && !input.inhibited;
        if(input.inhibited)gentle=rough=0;
        if(gentle>0 && rough<.1f) Comfort=Unit(Comfort+Positive(Settings.comfortRise,.08f)*gentle*dt);
        else Comfort*= (float)Math.Exp(-dt/Positive(Settings.comfortDecaySeconds,20));
        float tau=Positive(Settings.stimulationDecaySeconds,4),decay=(float)Math.Exp(-dt/tau);
        Stimulation=Unit(Stimulation*decay+Positive(Settings.roughStimulusPerSecond,.35f)*rough*tau*(1-decay));
        EvaluateAnnoyance();
        Annoyed+=( (IsAnnoyed?1f:0)-Annoyed)*(1-(float)Math.Exp(-dt/.25f));
        if(Reaction!="" && clock>=reactionEnd) Reaction=Response="";
        if(rough>=.7f) {
            roughTime+=dt;
            if(roughTime>=.15f && !roughLatched) {React("startle",.7f);roughLatched=true;}
        } else {roughTime=0;if(rough<.3f) roughLatched=false;}
        float attention=fresh?input.attention:0,novelty=fresh?input.novelty:0;
        float arousalTarget=Unit(.08f+Stimulation*.65f+rough*.3f+novelty*.65f);
        Arousal=Smooth(Arousal,arousalTarget,dt,Positive(arousalTarget>Arousal?Settings.arousalRiseSeconds:Settings.arousalFallSeconds,1));
        float idle=Unit((input.idleSeconds-Positive(Settings.idleSleepStart,90))/
            Math.Max(1,Positive(Settings.idleSleepFull,300)-Positive(Settings.idleSleepStart,90)));
        float sleepyTarget=input.resting?1:Unit((.45f-Energy)/.45f+idle*.7f);
        if(input.contact || input.working)sleepyTarget*=.35f;
        Sleepy=Smooth(Sleepy,sleepyTarget,dt,Positive(sleepyTarget>Sleepy?Settings.sleepyRiseSeconds:Settings.sleepyFallSeconds,2));
        float curiosityTarget=attention*(.7f+.3f*novelty)*(1-Annoyed*.8f)*(1-Sleepy*.7f);
        Curiosity=Smooth(Curiosity,curiosityTarget,dt,Positive(curiosityTarget>Curiosity?Settings.curiosityRiseSeconds:Settings.curiosityFallSeconds,1));
        faceAge=fresh && input.contact && input.faceContact?faceAge+dt:0;
        float shyTarget=faceAge>=Positive(Settings.shyContactDelay,.5f) && rough<.2f?
            Unit(Settings.shynessSensitivity)*(.65f+.35f*Comfort)*(1-Annoyed)*(1-Sleepy*.7f):0;
        Shy=Smooth(Shy,shyTarget,dt,Positive(shyTarget>Shy?Settings.shyRiseSeconds:Settings.shyFallSeconds,1));
        if(novelty>.7f && attention>.65f && !input.contact) {
            noveltyTime+=dt;
            if(noveltyTime>=.08f && !noveltyLatched) {React("startle",.55f);noveltyLatched=true;}
        } else {noveltyTime=0;if(novelty<.3f)noveltyLatched=false;}
        if(input.contact && fresh && rough>=.7f && Stimulation>=Unit(Settings.refusalThreshold)
            && roughTime>=Positive(Settings.refusalSeconds,.45f) && !refusalLatched && clock>=nextRefusal) {
            RequestedCancel="overstimulated";refusalLatched=true;
            nextRefusal=clock+Positive(Settings.refusalCooldown,3);React("retreat",.8f);
        }
        if(!input.contact || rough<.3f)refusalLatched=false;
        UpdateAppearance();
    }

    void UpdateAppearance() {
        var w=new EkuEmotionWeights{curious=Curious*(1-Startled),happy=Happy*(1-Sleepy*.5f)*(1-Startled),
            shy=Shy*(1-Annoyed)*(1-Startled),startled=Startled,
            annoyed=Annoyed,sleepy=Sleepy*(1-Startled*.9f)};
        w.neutral=1-Math.Max(Math.Max(Math.Max(w.curious,w.happy),Math.Max(w.shy,w.startled)),Math.Max(w.annoyed,w.sleepy));
        float total=w.neutral+w.curious+w.happy+w.shy+w.startled+w.annoyed+w.sleepy;
        w.neutral/=total;w.curious/=total;w.happy/=total;w.shy/=total;w.startled/=total;w.annoyed/=total;w.sleepy/=total;
        Weights=w;
        string candidate="Neutral";float best=w.neutral;
        if(w.curious>best) {candidate="Curious";best=w.curious;}
        if(w.happy>best) {candidate="Happy";best=w.happy;}
        if(w.shy>best) {candidate="Shy";best=w.shy;}
        if(w.startled>best) {candidate="Startled";best=w.startled;}
        if(w.annoyed>best) {candidate="Annoyed";best=w.annoyed;}
        if(w.sleepy>best) {candidate="Sleepy";best=w.sleepy;}
        float current=State=="Curious"?w.curious:State=="Happy"?w.happy:State=="Shy"?w.shy:
            State=="Startled"?w.startled:State=="Annoyed"?w.annoyed:State=="Sleepy"?w.sleepy:w.neutral;
        if(candidate!=State && ((candidate=="Startled" && best>.3f) ||
            (clock-stateAt>=Positive(Settings.stateHoldSeconds,.45f) && best>current+Unit(Settings.stateHysteresis)))) {
            State=candidate;stateAt=clock;
        }
        float response=ReactionWeight;
        float nod=Response=="warm"?response*-.9f:Response=="drowsy"?response*.7f:0;
        Pose=new EkuEmotionPose{pitch=w.sleepy*1.5f+w.shy*.8f-w.curious*.6f-w.startled*2+nod,
            yaw=lastSide*(w.annoyed*2+w.shy*.7f),roll=lastSide*(w.curious*.7f-w.annoyed*.3f),
            gazeAvoidance=Unit(w.shy*.35f+w.annoyed*.55f+w.sleepy*.35f+w.startled*.4f)};
        Behavior=new EkuEmotionBehavior{engagement=Unit((.35f+.45f*Curiosity+.2f*Happy)*Acceptance*(1-Sleepy*.6f)),
            playfulness=Unit((Happy*.55f+Curiosity*.3f)*Energy*(1-Annoyed)*(1-Sleepy)*(.75f+.25f*Unit(Arousal/.6f))),
            rest=Unit(Sleepy*.8f+(1-Energy)*.2f),retreat=Unit(Annoyed*.8f+Startled*.2f)};
    }
    public EkuEmotionStatus Status() => new EkuEmotionStatus{
        mood=Mood,energy=Energy,comfort=Comfort,stimulation=Stimulation,annoyed=Annoyed,
        happy=Happy,sleepy=Sleepy,acceptance=Acceptance,reaction=Reaction,response=Response,state=State,
        arousal=Arousal,curiosity=Curiosity,shy=Shy,startled=Startled,weights=Weights,pose=Pose,behavior=Behavior,
        reactionWeight=ReactionWeight,reactionRemaining=ReactionRemaining,interruptsBehavior=InterruptsBehavior,
        cooldownRemaining=(float)Math.Max(0,nextReaction-clock),cancelReason=CancelReason,requestedCancel=RequestedCancel};
}
