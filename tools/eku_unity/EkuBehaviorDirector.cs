using System;

[Serializable] public sealed class EkuBehaviorSettings {
    public bool enabled=true,allowReach=true,allowApproach=true;
    public float decisionSeconds=.8f,minimumScore=.35f,idleWeight=.65f;
    public float ambientMinDelay=12,ambientMaxDelay=25,extraPauseChance=.2f;
    public float poseMinDelay=120,poseMaxDelay=240,handMinDelay=75,handMaxDelay=130;
    public float mouseMinDelay=18,mouseMaxDelay=35,reachMinDelay=35,reachMaxDelay=60;
    public float releaseMinDelay=8,releaseMaxDelay=16;
    public float nearDistance=.8f,maxTargetSpeed=3,minimumExposure=1.2f;
    public float minimumPlayEnergy=.5f,maximumPlayAnnoyance=.4f;
    public float ambientMinDuration=2.6f,ambientMaxDuration=3.8f,poseMinDuration=8,poseMaxDuration=20;
    public float approachMinDuration=1.2f,approachMaxDuration=2,reachDuration=4.5f,restIdleSeconds=300;
}

// Distances and speeds use projected character heights, seconds use monotonic elapsed time.
public struct EkuBehaviorInput {
    public string blockReason;
    public bool resting,pinned,standing,occupied,canRest;
    public float systemIdle,interactionIdle,mood,energy,comfort,annoyed;
    public float engagement,playfulness,rest,retreat;
    public bool targetValid,targetSystem,targetPressed,allowAttention,targetReachable,canApproach;
    public float targetDistance,targetSpeed,targetStill;
}
public struct EkuBehaviorDecision { public string action;public float duration,score; }
[Serializable] public struct EkuBehaviorScore { public string action,reason;public float score,cooldown; }
[Serializable] public sealed class EkuBehaviorStatus {
    public string action,lastAction,previousAction,cancelReason,blockedReason;
    public float remaining,minimumRemaining,score,decisionIn,ambientCooldown,mouseCooldown,reachCooldown;
    public EkuBehaviorScore[] candidates;
}

// Sole decision point. No Unity calls, Transform writes, per-frame candidate allocation or second scheduler.
public sealed class EkuBehaviorDirector {
    static readonly string[] Names={"idle","Smile","Relax","look-around","Wink","ears","hand","pose","curiosity","teasing","approach","reach","rest"};
    readonly EkuBehaviorScore[] scores=new EkuBehaviorScore[Names.Length];
    readonly Random random;
    public readonly EkuBehaviorSettings Settings;
    double clock,nextDecision,nextAmbient,nextPose,nextHand,nextMouse,nextReach,until,minUntil,lastInterest=-100;
    float exposure,currentScore;
    bool wasBlocked;
    string current="idle",last="",previous="",cancelReason="",blockedReason="";
    public string CurrentAction => current;
    public string CancelReason => cancelReason;
    public float Remaining => (float)Math.Max(0,until-clock);
    public float AmbientCooldown => (float)Math.Max(0,nextAmbient-clock);
    public float HandCooldown => (float)Math.Max(0,nextHand-clock);
    static bool Finite(float n) => !float.IsNaN(n) && !float.IsInfinity(n);
    static float Unit(float n) => Finite(n)?Math.Max(0,Math.Min(1,n)):0;
    static float Positive(float n,float fallback) => Finite(n) && n>0?n:fallback;
    float Range(float a,float b) {a=Positive(a,.01f);b=Math.Max(a,Positive(b,a));return a+(b-a)*(float)random.NextDouble();}
    public EkuBehaviorDirector(EkuBehaviorSettings settings=null,int seed=17) {
        Settings=settings??new EkuBehaviorSettings();random=new Random(seed);
        nextAmbient=AmbientDelay();nextPose=Range(Settings.poseMinDelay,Settings.poseMaxDelay);
        nextHand=Range(45,75);
        for(int i=0;i<scores.Length;i++)scores[i].action=Names[i];
    }
    float AmbientDelay() => Range(Settings.ambientMinDelay,Settings.ambientMaxDelay)+(random.NextDouble()<Unit(Settings.extraPauseChance)?Range(12,24):0);
    // An ambient clip postpones other ambient clips, not fresh mouse decisions.
    public void DeferAmbient(float seconds) {nextAmbient=Math.Max(nextAmbient,clock+Math.Max(0,seconds));}
    public void Defer(float seconds) {nextDecision=Math.Max(nextDecision,clock+Math.Max(0,seconds));DeferAmbient(seconds);}
    public void Cancel(string reason) {
        if(current!="idle") {
            if(current=="reach" || current=="approach")nextReach=Math.Max(nextReach,clock+Range(Settings.reachMinDelay,Settings.reachMaxDelay));
            if(IsMouse(current))nextMouse=Math.Max(nextMouse,clock+Range(Settings.mouseMinDelay,Settings.mouseMaxDelay));
        }
        current="idle";until=minUntil=clock;currentScore=0;cancelReason=reason??"";
        Defer(Range(Settings.releaseMinDelay,Settings.releaseMaxDelay));
    }
    public void Complete(string reason="completed") {Cancel(reason);}
    static bool IsMouse(string name) => name=="curiosity" || name=="teasing" || name=="approach" || name=="reach";
    bool Target(EkuBehaviorInput x) => x.targetValid && x.targetSystem && !x.targetPressed && x.allowAttention
        && Finite(x.targetDistance) && x.targetDistance>=0 && x.targetDistance<=Positive(Settings.nearDistance,.8f)
        && Finite(x.targetSpeed) && x.targetSpeed>=0 && x.targetSpeed<=Positive(Settings.maxTargetSpeed,3);
    string Gate(EkuBehaviorInput x) => !Settings.enabled?"disabled":!string.IsNullOrEmpty(x.blockReason)?x.blockReason:
        x.resting?"rest":x.pinned?"manual-posture":x.occupied?"occupied":!x.standing?"posture":null;
    void Score(int i,float value,string reason="",double cooldown=0) {
        if(reason=="" && i!=0 && i!=12 && (Names[i]==last || Names[i]==previous)) {value=0;reason="recent-two";}
        scores[i].score=Finite(value)?Math.Max(0,value):0;scores[i].reason=reason;
        scores[i].cooldown=(float)Math.Max(0,cooldown-clock);
    }
    void Evaluate(EkuBehaviorInput x,bool target) {
        float boredom=Unit(x.interactionIdle/180),energy=Unit(x.energy),mood=Unit(x.mood),annoyed=Unit(x.annoyed);
        float play=Unit(.45f*mood+.35f*energy+.2f*x.playfulness)*(1-annoyed);
        bool ambient=clock>=nextAmbient,mouse=target && exposure>=Positive(Settings.minimumExposure,1.2f) && clock>=nextMouse;
        bool playful=energy>=Unit(Settings.minimumPlayEnergy) && annoyed<Unit(Settings.maximumPlayAnnoyance) && x.retreat<.3f;
        string a=ambient?"":"ambient-cooldown",m=mouse?"":!target?"target-unavailable":exposure<Settings.minimumExposure?"observe-first":"mouse-cooldown";
        Score(0,Math.Max(0,Settings.idleWeight)*(1+.8f*Unit(x.interactionIdle/900)+.5f*(1-energy)));
        Score(1,ambient?.4f+.8f*mood*(1-annoyed):0,annoyed>.5f?"annoyed":a,nextAmbient);
        Score(2,ambient?.35f+.5f*Unit(x.comfort)+.4f*(1-energy):0,a,nextAmbient);
        Score(3,ambient?.5f+.3f*boredom:0,a,nextAmbient);
        Score(4,ambient?.25f+.5f*play:0,annoyed>.4f?"annoyed":a,nextAmbient);
        Score(5,ambient?.5f+.15f*boredom:0,a,nextAmbient);
        Score(6,ambient && clock>=nextHand && playful?.35f+.6f*play:0,!playful?"low-playfulness":clock<nextHand?"hand-cooldown":a,nextHand);
        Score(7,ambient && clock>=nextPose && x.systemIdle>=30?.65f+.6f*(1-energy):0,clock<nextPose?"pose-cooldown":x.systemIdle<30?"system-active":a,nextPose);
        Score(8,mouse?.55f+.35f*mood+.25f*Unit(x.targetStill/3)+.2f*Unit(x.engagement):0,m,nextMouse);
        Score(9,mouse && playful && x.targetSpeed>.15f?.4f+.8f*play:0,!playful?"low-playfulness":x.targetSpeed<=.15f?"stationary":m,nextMouse);
        bool familiar=clock-lastInterest<20;
        string reachReason=!Settings.allowReach?"reach-disabled":!playful?"low-playfulness":!familiar?"observe-first":clock<nextReach?"reach-cooldown":m;
        Score(10,mouse && playful && familiar && clock>=nextReach && Settings.allowApproach && x.canApproach?.45f+.65f*play:0,
            !Settings.allowApproach?"approach-disabled":!x.canApproach?"outside-approach":reachReason,nextReach);
        Score(11,mouse && playful && familiar && clock>=nextReach && Settings.allowReach && x.targetReachable?.6f+.7f*play:0,
            !x.targetReachable?"unreachable":reachReason,nextReach);
        Score(12,0,x.canRest?"below-rest-threshold":"wake-cooldown");
        // Rejection reasons are authoritative, not merely explanatory labels.
        for(int i=1;i<scores.Length;i++)if(scores[i].reason!="")scores[i].score=0;
    }
    EkuBehaviorDecision Start(int index) {
        string name=Names[index];float duration,min;
        if(name=="rest") {min=0;duration=60;}
        else if(name=="pose") {min=4;duration=Range(Settings.poseMinDuration,Settings.poseMaxDuration);nextPose=clock+Range(Settings.poseMinDelay,Settings.poseMaxDelay);}
        else if(name=="reach") {min=.6f;duration=Positive(Settings.reachDuration,4.5f);nextReach=clock+Range(Settings.reachMinDelay,Settings.reachMaxDelay);}
        else if(name=="approach") {min=.5f;duration=Range(Settings.approachMinDuration,Settings.approachMaxDuration);nextReach=clock+Range(Settings.reachMinDelay,Settings.reachMaxDelay);}
        else {min=.7f;duration=name=="ears"?2.35f:Range(Settings.ambientMinDuration,Settings.ambientMaxDuration);}
        if(name=="hand")nextHand=clock+Range(Settings.handMinDelay,Settings.handMaxDelay);
        if(IsMouse(name)) {
            // Curiosity can lead to one reach attempt, after the observation completes.
            lastInterest=clock;nextMouse=clock+(name=="curiosity" || name=="teasing"?duration+Range(1,3):Range(Settings.mouseMinDelay,Settings.mouseMaxDelay));
        }
        previous=last;last=name;current=name;currentScore=scores[index].score;
        until=clock+duration;minUntil=clock+min;cancelReason="";nextAmbient=clock+AmbientDelay();
        return new EkuBehaviorDecision{action=name,duration=duration,score=currentScore};
    }
    public EkuBehaviorDecision Step(float elapsed,EkuBehaviorInput input) {
        if(!Finite(elapsed) || elapsed<=0)return default;
        clock+=elapsed;
        bool target=Target(input);
        exposure=target?Math.Min(10,exposure+(elapsed>.2f?0:elapsed)):0;
        blockedReason=Gate(input)??"";
        if(blockedReason!="") {
            if(!wasBlocked || current!="idle")Cancel(blockedReason);
            wasBlocked=true;return default;
        }
        if(wasBlocked) {wasBlocked=false;Defer(Range(Settings.releaseMinDelay,Settings.releaseMaxDelay));}
        if(IsMouse(current) && (!target || input.retreat>=.3f || input.annoyed>=Settings.maximumPlayAnnoyance))Cancel(!target?"target-lost":"retreat");
        if(input.canRest && input.systemIdle>=Positive(Settings.restIdleSeconds,300)) {
            if(current!="rest") {Score(12,2);return Start(12);}return default;
        }
        if(current!="idle" && clock<until)return default;
        if(current!="idle") {current="idle";currentScore=0;}
        if(clock<nextDecision)return default;
        nextDecision=clock+Positive(Settings.decisionSeconds,.8f);
        Evaluate(input,target);
        float total=scores[0].score;
        for(int i=1;i<scores.Length;i++)if(scores[i].score>=Settings.minimumScore)total+=scores[i].score;
        if(total<=0)return default;
        float pick=(float)random.NextDouble()*total;
        if((pick-=scores[0].score)<0)return default;
        for(int i=1;i<scores.Length;i++) {
            if(scores[i].score<Settings.minimumScore)continue;
            if((pick-=scores[i].score)<0)return Start(i);
        }
        return default;
    }
    public EkuBehaviorStatus Status() => new EkuBehaviorStatus {
        action=current,lastAction=last,previousAction=previous,cancelReason=cancelReason,blockedReason=blockedReason,
        remaining=Remaining,minimumRemaining=(float)Math.Max(0,minUntil-clock),score=currentScore,
        decisionIn=(float)Math.Max(0,nextDecision-clock),ambientCooldown=(float)Math.Max(0,nextAmbient-clock),
        mouseCooldown=(float)Math.Max(0,nextMouse-clock),reachCooldown=(float)Math.Max(0,nextReach-clock),
        candidates=(EkuBehaviorScore[])scores.Clone()
    };
}
