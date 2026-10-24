using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using VRM;

[Serializable] public class EkuAnimationStatus {
    public string posture,afk,expression,hand;
    public bool automatic,blocked;
    public float idle,faceWeight,contactWeight,handWeight,handAge;
    public string idleAction,earAction;
    public float idleAge,idleWait,faceAmount;
    public string behavior,blockReason,lastCancelReason,lastShort,previousShort;
    public bool pinned;
    public float behaviorRemaining,handCooldown,interactionIdle;
    public EkuBehaviorStatus director;
    public EkuReachState reach;
    public EkuReachMotionOutput reachMotion;
    public Quaternion[] reachBoneRotations;
    public Vector3[] reachBonePositions;
    public Vector3 reachRoot,reachForward,reachFrontPlane;
    public string approachMode;
    public Vector2 reachPalmViewport;
    public EkuInteractionStatus interaction;
    public EkuEmotionStatus emotion;
    public EkuCheekStatus cheek;
    public int cheekLeftCapabilities,cheekRightCapabilities;
    public string[] cheekMissingShapes;
    public int faceShapes;
    public int uniformContacts;
    public Vector3 hips,head,leftHand,rightHand,rightElbow,rightPalm,rightAim;
    public Quaternion[] rightFingers,earRotations;
    public string[] activeShapes;
    public float[] shapeWeights;
    public float headHairDegrees;
}

// One writer for humanoid pose and face weights; contacts are applied afterwards.
public sealed class EkuAnimationPlayer : IDisposable {
    sealed class BoneClip {
        public BoneBinding[] ears,tail;
    }
    struct BoneBinding {
        public Transform node;
        public Quaternion before,after;
        public AnimationCurve x,y,z,w;
        public bool quaternion,left;
        public Quaternion Evaluate(float time) => quaternion?
            new Quaternion(Value(x,time,0),Value(y,time,0),Value(z,time,0),Value(w,time,1)):
            Quaternion.Euler(Value(x,time,0),Value(y,time,0),Value(z,time,0));
        static float Value(AnimationCurve curve,float time,float fallback) => curve==null?fallback:curve.Evaluate(time);
    }
    readonly Dictionary<string,Dictionary<string,EkuAnimationClip>> clipLookup=new Dictionary<string,Dictionary<string,EkuAnimationClip>>();
    readonly Dictionary<EkuAnimationClip,BoneClip> boneClips=new Dictionary<EkuAnimationClip,BoneClip>();
    readonly Dictionary<string,EkuAnimationClip> afkBodies=new Dictionary<string,EkuAnimationClip>(),afkFaces=new Dictionary<string,EkuAnimationClip>();
    static readonly string[] TailBones={"Tail_2"};
    static readonly int[] SpringEarIndices={1,2,4,5};
    readonly EkuAnimationLibrary library;
    readonly Transform avatar;
    readonly Animator rig;
    readonly EkuWardrobe wardrobe;
    readonly HumanPoseHandler handler;
    readonly HumanPose rest;
    HumanPose pose;
    readonly SkinnedMeshRenderer skin;
    readonly Dictionary<string,int> shapes=new Dictionary<string,int>();
    readonly float[] neutral,weights;
    readonly EkuFacialDriver facial;
    readonly Transform[] desktopEyes;
    readonly Quaternion[] desktopEyeRest;
    EkuEmotion emotion;
    EkuCheek cheek;
    readonly Dictionary<string,Transform> nodes;
    readonly Dictionary<Transform,Quaternion> boneRest=new Dictionary<Transform,Quaternion>();
    readonly Dictionary<Transform,Quaternion> parentRest=new Dictionary<Transform,Quaternion>();
    readonly Transform[] headHair;
    readonly Quaternion[] headHairRest;
    EkuAnimationClip body,face,handClip,ears,tail;
    string posture="stand",afk="",expression="neutral",handName="";
    float bodyAt,faceAt,faceUntil,faceWeight,handAt,handUntil,handWeight,phaseAt;
    float idle,poseUntil,musicUntil,wakeIdle,contactWeight,breathingPhase;
    EkuBehaviorDirector director=new EkuBehaviorDirector();
    EkuReach reach=new EkuReach();
    EkuReachState reachState;
    EkuReachMotion reachMotion=new EkuReachMotion();
    EkuReachMotionOutput reachMotionState;
    EkuMouseTargetSnapshot behaviorTarget;
    EkuAttentionOutput behaviorGaze;
    Camera behaviorCamera;
    float behaviorHeight=1,behaviorAt,behaviorDuration,approachWeight;
    Transform reachUpper,reachLower,reachPalm;
    Vector3 reachPlanePoint;
    Quaternion reachUpperRotation,reachLowerRotation,reachWristRotation;
    static readonly HumanBodyBones[] ReachBones={HumanBodyBones.Spine,HumanBodyBones.Chest,HumanBodyBones.Neck,HumanBodyBones.Head,
        HumanBodyBones.RightShoulder,HumanBodyBones.RightUpperArm,HumanBodyBones.RightLowerArm,HumanBodyBones.RightHand,
        HumanBodyBones.Hips,HumanBodyBones.LeftFoot,HumanBodyBones.RightFoot};
    bool automatic=true,blocked,working,pinned,restManual,contact,greeted;
    string controlReason="",contextReason="",lastCancelReason="";
    float interactionIdle;
    string lastShort="",previousShort="";
    string afterWake="stand",queuedHand="";
    bool afterWakePinned;
    readonly float[] targetMuscles;
    readonly Dictionary<string,int> muscleIndices=HumanTrait.MuscleName.Select((name,index)=>new {name,index}).ToDictionary(m=>m.name,m=>m.index);
    readonly Transform[] rightFingers;
    readonly Vector3 palmNormal;
    bool greeting;
    bool softMood,idleHand,doubleBlink;
    float faceAmount=1,faceRise=.25f,faceFall=.25f,handRise=.4f,handFall=.4f;
    float idleAt,idleUntil,idleLean,idleWrist,idleTailSpeed=1;
    float lookAt,lookUntil,nextBlink,blinkAt=-10,blinkClose=.1f,blinkOpen=.16f;
    string idleAction="";
    static readonly string[] EarBones={"Ear_Root.L","Ear.L","Ear.L.001","Ear_Root.R","Ear.R","Ear.R.001"};
    readonly Quaternion[] earPose=new Quaternion[6];
    float earAt,earUntil,earAmount,earFadeAt=float.PositiveInfinity;
    bool idleEars;
    Vector2 idleLook,idleEyes,idleHead;
    Vector3 bodyPosition;
    Quaternion bodyRotation;
    public string Posture => posture;
    public string Afk => afk;
    public float PostureYaw => posture=="prone" && afk==""?35:0;
    public EkuAnimationPlayer(Transform avatar,Animator rig,EkuAnimationLibrary library) {
        if(library==null) throw new InvalidOperationException("Eku animation library is missing; rebuild the local player");
        this.avatar=avatar;this.library=library;this.rig=rig;wardrobe=avatar.GetComponent<EkuWardrobe>();
        handler=new HumanPoseHandler(rig.avatar,avatar);handler.GetHumanPose(ref pose);
        // GetHumanPose reports world centre/orientation; SetHumanPose consumes them
        // relative to the avatar root. Strip its placement exactly once.
        pose.bodyPosition=Quaternion.Inverse(avatar.rotation)*(pose.bodyPosition-avatar.position/rig.humanScale);
        pose.bodyRotation=Quaternion.Inverse(avatar.rotation)*pose.bodyRotation;
        rest=pose;rest.muscles=(float[])pose.muscles.Clone();pose.muscles=(float[])pose.muscles.Clone();
        targetMuscles=new float[rest.muscles.Length];bodyPosition=rest.bodyPosition;bodyRotation=rest.bodyRotation;
        rightFingers=new[]{HumanBodyBones.RightThumbProximal,HumanBodyBones.RightThumbIntermediate,HumanBodyBones.RightThumbDistal,
            HumanBodyBones.RightIndexProximal,HumanBodyBones.RightIndexIntermediate,HumanBodyBones.RightIndexDistal,
            HumanBodyBones.RightMiddleProximal,HumanBodyBones.RightMiddleIntermediate,HumanBodyBones.RightMiddleDistal,
            HumanBodyBones.RightRingProximal,HumanBodyBones.RightRingIntermediate,HumanBodyBones.RightRingDistal,
            HumanBodyBones.RightLittleProximal,HumanBodyBones.RightLittleIntermediate,HumanBodyBones.RightLittleDistal}.Select(rig.GetBoneTransform).ToArray();
        var wrist=rig.GetBoneTransform(HumanBodyBones.RightHand);
        palmNormal=wrist.InverseTransformDirection(Vector3.Cross(rightFingers[6].position-wrist.position,rightFingers[3].position-rightFingers[12].position).normalized);
        skin=avatar.GetComponentsInChildren<SkinnedMeshRenderer>().Single(s=>s.name=="Body");
        neutral=new float[skin.sharedMesh.blendShapeCount];weights=new float[neutral.Length];
        for(int i=0;i<neutral.Length;i++) {shapes[skin.sharedMesh.GetBlendShapeName(i)]=i;neutral[i]=skin.GetBlendShapeWeight(i);}
        facial=new EkuFacialDriver(library,shapes);
        desktopEyes=new[]{rig.GetBoneTransform(HumanBodyBones.LeftEye),rig.GetBoneTransform(HumanBodyBones.RightEye)};
        desktopEyeRest=new Quaternion[desktopEyes.Length];
        for(int i=0;i<desktopEyes.Length;i++) desktopEyeRest[i]=desktopEyes[i]?desktopEyes[i].localRotation:Quaternion.identity;
        nodes=avatar.GetComponentsInChildren<Transform>().GroupBy(t=>t.name).ToDictionary(g=>g.Key,g=>g.First());
        foreach(var node in nodes.Values) {boneRest[node]=node.localRotation;parentRest[node]=node.parent?node.parent.rotation:Quaternion.identity;}
        headHair=avatar.GetComponentsInChildren<VRMSpringBone>().SelectMany(s=>s.RootBones)
            .Where(b=>b && b.name.Contains("Hair") && !b.name.Contains("Tie")).Distinct().ToArray();
        headHairRest=headHair.Select(b=>b.localRotation).ToArray();
        var proxy=avatar.GetComponent<VRMBlendShapeProxy>();if(proxy) proxy.enabled=false;
        CacheClips();

        nextBlink=Time.time+UnityEngine.Random.Range(2,6);
    }
    void DeferIdle() {director.DeferAmbient(UnityEngine.Random.Range(8,16));}
    public void StopIdle(string reason="") {
        if(reason!="") {lastCancelReason=reason;director.Cancel(reason);reach.Cancel(Time.time,reason);}
        if(softMood) {faceUntil=Mathf.Min(faceUntil,Time.time);faceFall=Mathf.Min(faceFall,.25f);}
        if(idleHand) {handUntil=Mathf.Min(handUntil,Time.time);handFall=Mathf.Min(handFall,.25f);}
        if(idleEars) StopEars();
        lookUntil=idleUntil=Time.time;DeferIdle();
    }
    void StartEars(bool automatic) {
        if(contact) return;
        earAt=Time.time;earUntil=earAt+2.35f;earFadeAt=float.PositiveInfinity;
        idleEars=automatic;earAmount=automatic?.32f:.55f;
    }
    void StopEars() {
        earFadeAt=Mathf.Min(earFadeAt,Time.time);earUntil=Mathf.Min(earUntil,Time.time+.2f);
    }
    static float EarPulse(float age,float rise,float hold,float fall) => Ease(age/rise)*Ease((rise+hold+fall-age)/fall);
    static float Ease(float value) => Mathf.SmoothStep(0,1,Mathf.Clamp01(value));
    Vector2 IdleGaze(float rise,float fall) => idleLook*Ease((Time.time-lookAt)/rise)*Ease((lookUntil-Time.time)/fall);
    public Vector2 IdleEyes => idleEyes;
    public Vector2 IdleHead => idleHead;
    EkuAnimationClip Clip(string group,string name) => clipLookup.TryGetValue(group,out var clips) && clips.TryGetValue(name,out var clip)?clip:null;
    void CacheClips() {
        foreach(var clip in library.clips) {
            if(!clipLookup.TryGetValue(clip.group,out var group)) {group=new Dictionary<string,EkuAnimationClip>();clipLookup[clip.group]=group;}
            if(!group.ContainsKey(clip.name)) group.Add(clip.name,clip);
        }
        foreach(string phase in new[]{"intro","main","loop","outro"}) {
            string suffix=char.ToUpperInvariant(phase[0])+phase.Substring(1);
            afkBodies[phase]=Clip("AFK","AFK_Action_"+suffix+"_VRSuya");
            afkFaces[phase]=Clip("AFK","AFK_FX_"+suffix+"_VRSuya");
        }
        foreach(var clip in library.clips) boneClips[clip]=new BoneClip{ears=BindBones(clip,false),tail=BindBones(clip,true)};
    }
    BoneBinding[] BindBones(EkuAnimationClip clip,bool tail) {
        var result=new List<BoneBinding>();
        foreach(string name in tail?TailBones:EarBones) {
            if(!nodes.TryGetValue(name,out var node)) continue;
            string source=tail?"Tail":name;
            var bound=clip.curves.Where(c=>c.path.Substring(c.path.LastIndexOf('/')+1)==source).ToArray();
            if(bound.Length==0) continue;
            bool quaternion=bound.Any(c=>c.property.StartsWith("m_LocalRotation.",StringComparison.Ordinal));
            string prefix=quaternion?"m_LocalRotation.":bound[0].property.Substring(0,bound[0].property.LastIndexOf('.')+1);
            Func<string,AnimationCurve> find=property=>bound.FirstOrDefault(c=>c.property==property)?.curve;
            var binding=bound[0];var parent=parentRest[node];
            result.Add(new BoneBinding{node=node,before=Quaternion.Inverse(parent)*binding.parentRest,
                after=Quaternion.Inverse(binding.boneRest)*parent*boneRest[node],quaternion=quaternion,left=name.Contains(".L"),
                x=find(prefix+"x"),y=find(prefix+"y"),z=find(prefix+"z"),w=find(prefix+"w")});
        }
        return result.ToArray();
    }
    static string FaceName(string name) {
        switch(name.ToLowerInvariant()) {
            case "happy":return "Smile";case "calm":case "shy":return "Relax";case "love":return "Satisfied";
            case "sad":return "Cry";case "surprise":return "Excited";case "sleepy":return "Chill";
            default:return name.Replace('_',' ');
        }
    }
    public void Face(string name,float duration=2) {
        faceAmount=1;faceRise=faceFall=.25f;softMood=false;DeferIdle();
        string wanted=FaceName(name);
        face=library.clips.FirstOrDefault(c=>(c.group=="Facial" || c.group=="Contact") && string.Equals(c.name,wanted,StringComparison.OrdinalIgnoreCase));
        expression=name;faceAt=Time.time;faceUntil=faceAt+Mathf.Clamp(duration<=0?2:duration,.2f,10);
        if(name=="neutral") {face=null;faceUntil=faceAt;}
    }
    void Mood(string name,string ear="Up",string tailName="Up",float duration=2) {
        Face(name,duration);ears=Clip("Kemo Ear",ear);tail=Clip("Kemo Tail",tailName);
    }
    void Hand(string name,float duration=3.2f) {
        idleHand=false;handRise=handFall=.4f;DeferIdle();
        handClip=Clip("Hand",name);handName=name;handAt=Time.time;handUntil=handAt+duration;greeting=name=="Open";
    }
    public bool Animation(string name) {
        StopIdle();StopEars();
        if(name=="ears") {StartEars(false);return true;}
        if(name=="rest") {if(afk=="") StartRest(true);return true;}
        if(name=="wake" || name=="stand") {Wake();queuedHand="";afterWake="stand";afterWakePinned=false;if(afk=="") SetPosture("stand",false);return true;}
        if(name=="crouch" || name=="prone") {Wake();queuedHand="";if(afk!="") {afterWake=name;afterWakePinned=true;}else SetPosture(name,true);return true;}
        var names=new Dictionary<string,string>{{"greeting","Open"},{"open","Open"},{"fist","Fist"},{"peace","Peace"},{"point","Point"},{"thumbsup","thumbsup"},{"rocknroll","rocknroll"},{"gun","gun"}};
        if(!names.TryGetValue(name,out var clip)) return false;
        Wake();if(afk!="") {queuedHand=clip;return true;}
        Hand(clip);Mood(name=="fist"?"Joy":name=="gun"?"Wink":"Smile","Up","Shake");return true;
    }
    public void Greeting() {
        if(greeted) return;greeted=true;
        if(!blocked && afk=="" && !contact) {Hand("Open",3.2f);greeting=true;Mood("Smile","Up","Shake",3.2f);}
    }
    void SetPosture(string name,bool manual) {
        StopIdle();StopEars();
        posture=name;pinned=manual;bodyAt=Time.time;
        body=name=="crouch"?Clip("Pose","Crouching"):name=="prone"?Clip("Pose","prone"):null;
        poseUntil=manual?float.PositiveInfinity:Time.time+UnityEngine.Random.Range(8,20);
    }
    void StartRest(bool manual) {
        StopIdle();StopEars();
        restManual=manual;afk="intro";posture="rest";phaseAt=Time.time;pinned=false;
        afterWake="stand";afterWakePinned=false;queuedHand="";
        handClip=null;face=null;faceUntil=Time.time;wakeIdle=idle;
    }
    public void Wake() {
        if(afk=="" || afk=="outro") return;
        afk="outro";phaseAt=Time.time;wakeIdle=idle;restManual=false;
    }
    public void Context(float idleSeconds,bool suppress,bool thinking,bool auto,string reason="",float companionIdle=0) {
        bool input=idleSeconds+0.5f<idle;
        bool wasSuppressed=blocked || working || !automatic;
        idle=Mathf.Max(0,idleSeconds);blocked=suppress;working=thinking;automatic=auto;
        contextReason=reason;interactionIdle=Mathf.Max(0,companionIdle);
        if((blocked || working || !automatic) && !wasSuppressed) StopIdle(BlockReason);
        else if(wasSuppressed && !blocked && !working && automatic) DeferIdle();
        if(afk!="" && afk!="outro" && (input || thinking)) Wake();
        if((blocked || !auto) && !pinned && afk=="" && posture!="stand") SetPosture("stand",false);
        if(!auto && !restManual) Wake();
    }
    string BlockReason => controlReason!=""?controlReason:blocked?(contextReason!=""?contextReason:"suppressed"):
        working?"working":!automatic?"automatic-off":contact?"contact":afk!=""?"rest":pinned?"manual-posture":"";
    public void Control(string reason) {
        if(controlReason==reason) return;
        controlReason=reason;
        if(reason=="") {DeferIdle();return;}
        emotion?.Cancel(reason);
        StopIdle(reason);
        if(reason=="move") {
            Wake();queuedHand="";handUntil=Time.time;faceUntil=Time.time;
            if(!pinned) poseUntil=Time.time;
        }
    }
    public void Touch(bool active) {
        if(active || contact) StopIdle(active?"touch":"touch-ended");
        contact=active;
        if(active) {StopEars();Wake();queuedHand="";handUntil=Time.time;if(!pinned) poseUntil=Time.time;}
    }
    public void Tap(string region,bool repeated) {
        StopIdle();
        Wake();
        if(emotion!=null) return; // Reaction owns the temporary face; no second fixed mood clip.
        if(repeated) Mood("Angry","AngryEars","Down",2.5f);
        else if(region=="face") Mood(UnityEngine.Random.value<.5f?"Wink":"Wink 3","Up","Shake");
        else if(region=="head") Mood("Smile","Up","Shake");
        else Mood(UnityEngine.Random.value<.5f?"Curious":"Satisfied","Up","Up");
    }
    public void Gesture(string name) {
        if(contact) return;StopIdle();Wake();
        if(name=="cheer") {
            string wanted=UnityEngine.Random.value<.5f?"Fist":"thumbsup";
            if(afk!="") queuedHand=wanted;else Hand(wanted,2.4f);
            Mood("Joy","Up","Shake",2.4f);
        }
        else if(name=="pat") Mood("Relax","HalfFold","Shake",2.4f);
        else if(name=="nod") Mood("Smile");
    }
    public void State(bool thinking) {
        StopIdle();
        working=thinking;Wake();
        if(!contact) {if(thinking) Mood("Curious","Up","Up",10);else faceUntil=Time.time;}
    }
    public void Music() {
        if(Time.time<musicUntil || controlReason!="" || blocked || working || contact || afk!="") return;
        StopIdle();musicUntil=Time.time+30;Hand("rocknroll");Mood("Excited","Up","Shake");
    }
    public void SetEmotion(EkuEmotion value) {emotion=value;}
    public void SetCheek(EkuCheek value) {cheek=value;facial.SetCheek(value);}
    public void ConfigureFacial(EkuFacialSettings settings) {facial.Configure(settings);}
    public void ApplyDesktopPose(EkuDesktopPose value) {
        UpdateReachMotion(Time.deltaTime);
        var head=rig.GetBoneTransform(HumanBodyBones.Head);
        var neck=rig.GetBoneTransform(HumanBodyBones.Neck);
        var chest=rig.GetBoneTransform(HumanBodyBones.Chest);
        float breathingScale=value.headPat.breathingScale>0?value.headPat.breathingScale:1;
        breathingPhase=(breathingPhase+Mathf.Clamp(Time.deltaTime,0,.05f)*1.3f*Mathf.Clamp(breathingScale,.5f,1.5f))%(Mathf.PI*2);
        chest?.Rotate(Mathf.Sin(breathingPhase)*.006f*Mathf.Rad2Deg,0,0,Space.Self);
        var emotionPose=emotion==null?default(EkuEmotionPose):emotion.Pose;
        float gaze=Resting?0:(1-Mathf.Clamp01(value.headPat.strength)*.8f)*(1-Mathf.Clamp01(emotionPose.gazeAvoidance));
        gaze*=1-Mathf.Clamp01(reachMotionState.gazeOwnership);
        var attention=value.attention;
        float idleAmount=attention.enabled?1-Mathf.Clamp01(attention.weight):1;
        if(attention.enabled) {
            ApplyAnatomical(chest,attention.body*gaze);
            ApplyAnatomical(neck,new Vector3(attention.neck.x,attention.neck.y,0)*gaze);
            ApplyAnatomical(head,new Vector3(attention.head.x,attention.head.y,0)*gaze);
        }
        var legacy=attention.enabled?Vector2.zero:value.look;
        head.Rotate((legacy.y+idleHead.y*idleAmount)*.2f*Mathf.Rad2Deg*gaze,-(legacy.x+idleHead.x*idleAmount)*.25f*Mathf.Rad2Deg*gaze,0,Space.Self);
        for(int i=0;i<desktopEyes.Length;i++) if(desktopEyes[i]) {
            desktopEyes[i].localRotation=desktopEyeRest[i];
            desktopEyes[i].Rotate((legacy.y+idleEyes.y*idleAmount)*Mathf.Rad2Deg*gaze,-(legacy.x+idleEyes.x*idleAmount)*Mathf.Rad2Deg*gaze,0,Space.Self);
            if(attention.enabled)ApplyAnatomical(desktopEyes[i],new Vector3(attention.eyes.x,attention.eyes.y,0)*gaze);
            ApplyAnatomical(desktopEyes[i],new Vector3(reachMotionState.eyes.x,reachMotionState.eyes.y,0));
        }
        neck?.Rotate((legacy.y+idleHead.y*.6f*idleAmount)*.28f*Mathf.Rad2Deg*gaze,-(legacy.x+idleHead.x*.6f*idleAmount)*.35f*Mathf.Rad2Deg*gaze,0,Space.Self);
        if(!Resting) {
            var degrees=Vector3.ClampMagnitude(new Vector3(emotionPose.pitch,emotionPose.yaw,emotionPose.roll),3);
            ApplyAnatomical(chest,degrees*.12f);ApplyAnatomical(neck,degrees*.25f);ApplyAnatomical(head,degrees*.63f);
        }
        ApplyAnatomical(head,reachMotionState.head);ApplyAnatomical(neck,reachMotionState.neck);
        head.Rotate(value.gesture,Space.Self);
        ApplyAnatomical(chest,value.headPat.bodyDegrees);
        ApplyAnatomical(head,value.headPat.headDegrees+value.cheekDegrees);
        ApplyAnatomical(neck,value.headPat.neckDegrees);
        if(value.contactStrength>0 && value.hand!=HumanBodyBones.LastBone && value.region=="hand") {
            bool left=value.hand==HumanBodyBones.LeftHand;float side=left?1:-1;
            float strength=value.contactStrength,sway=Mathf.Sin(value.contactAge*4)*.025f;
            rig.GetBoneTransform(left?HumanBodyBones.LeftUpperArm:HumanBodyBones.RightUpperArm)?.Rotate(.12f*strength*Mathf.Rad2Deg,0,.08f*side*strength*Mathf.Rad2Deg,Space.Self);
            rig.GetBoneTransform(left?HumanBodyBones.LeftLowerArm:HumanBodyBones.RightLowerArm)?.Rotate(0,side*(.22f+sway)*strength*Mathf.Rad2Deg,0,Space.Self);
            rig.GetBoneTransform(value.hand)?.Rotate(-sway*strength*Mathf.Rad2Deg,0,side*.08f*strength*Mathf.Rad2Deg,Space.Self);
        }
        ApplyReach(Time.deltaTime);
    }
    public void ConfigureBehavior(EkuBehaviorSettings settings,EkuReachSettings reachSettings,Camera camera,float height) {
        director=new EkuBehaviorDirector(settings,UnityEngine.Random.Range(1,int.MaxValue));reach=new EkuReach(reachSettings);
        reachMotion=new EkuReachMotion(reach.Settings.Motion);
        behaviorCamera=camera;behaviorHeight=Mathf.Max(.01f,height);
        reachUpper=rig.GetBoneTransform(HumanBodyBones.RightUpperArm);
        reachLower=rig.GetBoneTransform(HumanBodyBones.RightLowerArm);reachPalm=rig.GetBoneTransform(HumanBodyBones.RightHand);
    }
    public void SetBehaviorTarget(EkuMouseTargetSnapshot value,EkuAttentionOutput gaze=default) {behaviorTarget=value;behaviorGaze=gaze;}
    Vector2 PalmViewport() {
        if(!behaviorCamera || !reachPalm)return Vector2.zero;
        var p=behaviorCamera.WorldToViewportPoint(reachPalm.position);return new Vector2(p.x,1-p.y);
    }
    Vector3 FrontReachPlane() {
        float length=Vector3.Distance(reachUpper.position,reachLower.position)+Vector3.Distance(reachLower.position,reachPalm.position);
        return EkuReach.FrontPlane(reachUpper.position,-behaviorCamera.transform.forward,length,reach.Settings.TargetFrontFraction);
    }
    EkuReachInput ReachInput() {
        var target=behaviorTarget;
        bool valid=behaviorCamera && reachUpper && reachLower && reachPalm && target.valid && target.insideViewport
            && (!behaviorGaze.enabled || behaviorGaze.targetValid || reach.Phase==EkuReachPhase.Reach || reach.Phase==EkuReachPhase.Grabbed);
        Vector3 point=Vector3.zero;
        if(valid) {
            var plane=new Plane(behaviorCamera.transform.forward,reach.Phase==EkuReachPhase.Idle?FrontReachPlane():reachPlanePoint);
            var ray=behaviorCamera.ViewportPointToRay(new Vector3(target.windowPosition.x,1-target.windowPosition.y,0));
            valid=plane.Raycast(ray,out float distance);if(valid)point=ray.GetPoint(distance);
        }
        float heightDip=ProjectedHeightDip();
        return new EkuReachInput {Target=point,Shoulder=reachUpper?reachUpper.position:Vector3.zero,
            Palm=reachPalm?reachPalm.position:Vector3.zero,
            Pole=behaviorCamera && reachUpper && reachLower && reachPalm?EkuReach.FrontPole(reachUpper.position,
                -behaviorCamera.transform.forward,avatar.right,avatar.up,
                Vector3.Distance(reachUpper.position,reachLower.position)+Vector3.Distance(reachLower.position,reachPalm.position)):Vector3.zero,
            UpperLength=reachUpper && reachLower?Vector3.Distance(reachUpper.position,reachLower.position):0,
            LowerLength=reachPalm && reachLower?Vector3.Distance(reachLower.position,reachPalm.position):0,
            SampleAge=target.age,Valid=valid,AllowAttention=target.allowAttention,Pressed=target.isPressed,
            TooFast=target.motionSpeed/heightDip>director.Settings.maxTargetSpeed,
            Source=target.source,SessionId=target.sessionId,MappingRevision=target.mappingRevision};
    }
    float ProjectedHeightDip() {
        if(!behaviorCamera)return 1;
        var a=behaviorCamera.WorldToViewportPoint(avatar.position);
        var b=behaviorCamera.WorldToViewportPoint(avatar.position+Vector3.up*behaviorHeight);
        return Mathf.Max(1,Mathf.Abs(a.y-b.y)*Mathf.Max(1,behaviorTarget.windowSize.y)/Mathf.Max(.01f,behaviorTarget.dpiScale));
    }
    EkuBehaviorInput BehaviorInput(EkuHeadPatOutput pat) {
        bool recovering=(cheek!=null && (cheek.Contact.sqrMagnitude>=.0001f || cheek.Wink.sqrMagnitude>=.0001f)) || pat.relax>=.01f;
        string reason=controlReason!=""?controlReason:blocked?(contextReason!=""?contextReason:"suppressed"):
            working?"working":!automatic?"automatic-off":contact?"contact":recovering?"recovery":
            emotion!=null && emotion.InterruptsBehavior?"reaction":"";
        var input=ReachInput();float length=input.UpperLength+input.LowerLength;
        float ratio=length>0?Vector3.Distance(input.Target,input.Shoulder)/length:float.PositiveInfinity;
        var e=emotion==null?default(EkuEmotionBehavior):emotion.Behavior;
        float heightDip=ProjectedHeightDip();
        return new EkuBehaviorInput {blockReason=reason,resting=afk!="",pinned=pinned,standing=posture=="stand",
            occupied=(Time.time<faceUntil && !softMood) || (handWeight>.01f && !idleHand) || (Time.time<earUntil && !idleEars),
            canRest=wakeIdle<0 || idle<wakeIdle || idle-wakeIdle>5,
            systemIdle=idle,interactionIdle=interactionIdle,mood=emotion==null?.6f:emotion.Mood,
            energy=emotion==null?1:emotion.Energy,comfort=emotion==null?0:emotion.Comfort,annoyed=emotion==null?0:emotion.Annoyed,
            engagement=e.engagement,playfulness=e.playfulness,rest=e.rest,retreat=e.retreat,
            targetValid=input.Valid,targetSystem=input.Source=="System",targetPressed=input.Pressed,allowAttention=input.AllowAttention,
            targetReachable=ratio>=reach.Settings.MinReachFraction && ratio<=reach.Settings.MaxReachFraction && reach.CooldownRemaining(Time.time)<=0,
            canApproach=ratio>.97f && ratio<1.15f,targetDistance=behaviorTarget.distanceToCharacter/heightDip,
            targetSpeed=behaviorTarget.motionSpeed/heightDip,targetStill=behaviorTarget.stationaryDuration};
    }
    void RunBehavior(EkuBehaviorDecision decision) {
        string choice=decision.action;float duration=decision.duration;
        behaviorAt=Time.time;behaviorDuration=duration;
        if(choice=="rest") {StartRest(false);return;}
        if(choice=="pose") {SetPosture(UnityEngine.Random.value<.65f?"crouch":"prone",false);poseUntil=Time.time+duration;return;}
        if(choice=="reach") {
            var input=ReachInput();reachPlanePoint=FrontReachPlane();
            reachUpperRotation=reachUpper.rotation;reachLowerRotation=reachLower.rotation;reachWristRotation=reachPalm.rotation;
            if(!reach.Begin(Time.time,input))director.Complete("reach-unavailable");
        } else if(choice=="ears") StartEars(true);
        else if(choice=="hand") {
            string shape=UnityEngine.Random.value<.5f?"thumbsup":"Peace";
            Hand(shape,duration);handAt+=.16f;handUntil+=.16f;idleHand=true;
            handRise=.7f;handFall=.8f;idleLean=UnityEngine.Random.Range(-.014f,.014f);idleWrist=UnityEngine.Random.Range(-.06f,.06f);
            Mood("Smile","Up","Shake",duration);
        } else if(choice=="look-around" || choice=="curiosity" || choice=="teasing") {
            Mood("Curious","Up","Up",duration);lookAt=Time.time;lookUntil=lookAt+duration;
            idleLook=choice=="look-around"?new Vector2((UnityEngine.Random.value<.5f?-1:1)*UnityEngine.Random.Range(.2f,.35f),UnityEngine.Random.Range(-.06f,.06f)):
                new Vector2(Mathf.Clamp(behaviorTarget.windowPosition.x-.5f,-.08f,.08f),-.025f);
        } else if(choice!="approach")Mood(choice,"Up","Up",duration);
        if(choice!="ears" && choice!="reach" && choice!="approach") {
            softMood=true;faceAmount=choice=="Wink"?1:UnityEngine.Random.Range(.35f,.6f);
            faceRise=UnityEngine.Random.Range(.35f,.55f);faceFall=UnityEngine.Random.Range(.55f,.8f);idleTailSpeed=UnityEngine.Random.Range(.65f,.9f);
        }
        previousShort=lastShort;lastShort=choice;idleAction=choice;idleAt=Time.time;idleUntil=idleAt+duration;
    }
    bool ReachControlled => controlReason!="" || blocked || working || !automatic || contact || pinned || afk!="" || (emotion!=null && emotion.InterruptsBehavior);
    void UpdateReachMotion(float elapsed) {
        float now=Time.time;
        if(ReachControlled)reach.Cancel(now,BlockReason!=""?BlockReason:"reaction");
        else if(reach.Phase==EkuReachPhase.Reach && director.CurrentAction!="reach")reach.Cancel(now,"decision-ended");
        var before=reach.Phase;var input=ReachInput();
        // Arrival is measured after this frame's body, arm and wrist pose.
        reachState=reach.Tick(now,elapsed,input,false);
        if(before!=EkuReachPhase.Idle && reachState.Phase==EkuReachPhase.Idle && director.CurrentAction=="reach")director.Complete(reachState.CancelReason);
        string reason=reachState.CancelReason;
        bool cancelled=ReachControlled || (reason!="" && reason!="complete" && reason!="timeout");
        var head=rig.GetBoneTransform(HumanBodyBones.Head);
        reachMotionState=reachMotion.Step(elapsed,new EkuReachMotionInput {
            reaching=reachState.Phase==EkuReachPhase.Reach,grabbed=reachState.Grabbed,cancelled=cancelled,
            localTarget=avatar.InverseTransformDirection(input.Target-head.position),height=behaviorHeight});
    }
    void ApplyReach(float elapsed) {
        float now=Time.time,dt=Mathf.Clamp(elapsed,0,.05f);
        if(!reachUpper || !reachLower || !reachPalm)return;
        float aim=director.CurrentAction=="approach"?Mathf.Sin(Mathf.PI*Mathf.Clamp01((now-behaviorAt)/Mathf.Max(.01f,behaviorDuration))):0;
        approachWeight=Mathf.MoveTowards(approachWeight,ReachControlled?0:aim,dt*3);
        if(approachWeight>0)ApplyAnatomical(rig.GetBoneTransform(HumanBodyBones.Chest),new Vector3(3,0,Mathf.Clamp(.5f-behaviorTarget.windowPosition.x,-.5f,.5f)*5)*approachWeight);
        ApplyAnatomical(rig.GetBoneTransform(HumanBodyBones.Spine),reachMotionState.spine);
        ApplyAnatomical(rig.GetBoneTransform(HumanBodyBones.Chest),reachMotionState.chest);
        ApplyAnatomical(rig.GetBoneTransform(HumanBodyBones.RightShoulder),reachMotionState.shoulder);
        var input=ReachInput();
        reachState=reach.MeasurePalm(now,0,input);
        float armWeight=reachState.Weight*reachMotionState.armBlend;
        if(armWeight<=0)return;
        var upperBefore=reachUpper.rotation;var lowerBefore=reachLower.rotation;
        reachUpper.rotation=Quaternion.FromToRotation(reachLower.position-reachUpper.position,reachState.Elbow-reachUpper.position)*upperBefore;
        reachLower.rotation=Quaternion.FromToRotation(reachPalm.position-reachLower.position,reachState.ClampedTarget-reachLower.position)*reachLower.rotation;
        var upperGoal=reachUpper.rotation;var lowerGoal=reachLower.rotation;
        reachUpper.rotation=upperBefore;reachLower.rotation=lowerBefore;
        reachUpperRotation=Quaternion.RotateTowards(reachUpperRotation,upperGoal,180*dt);
        reachLowerRotation=Quaternion.RotateTowards(reachLowerRotation,lowerGoal,180*dt);
        reachUpper.rotation=Quaternion.Slerp(upperBefore,reachUpperRotation,armWeight);
        reachLower.rotation=Quaternion.Slerp(lowerBefore,reachLowerRotation,armWeight);
        if(behaviorCamera && reachMotionState.wristWeight>0) {
            var wristBefore=reachPalm.rotation;
            var goal=Quaternion.FromToRotation(reachPalm.TransformDirection(palmNormal),-behaviorCamera.transform.forward)*wristBefore;
            float maximum=Mathf.Clamp(reachMotion.Settings.wristMaximumDegrees,0,60);
            goal=Quaternion.RotateTowards(wristBefore,goal,maximum);
            reachWristRotation=Quaternion.RotateTowards(reachWristRotation,goal,Mathf.Max(0,reachMotion.Settings.wristAngularSpeed)*dt);
            reachPalm.rotation=Quaternion.Slerp(wristBefore,Quaternion.RotateTowards(wristBefore,reachWristRotation,maximum),armWeight*reachMotionState.wristWeight);
        }
        input=ReachInput();input.Palm=reachPalm.position;
        reachState=reach.MeasurePalm(now,reachMotionState.armBlend>=.85f?elapsed:0,input);
    }
    void ApplyAnatomical(Transform bone,Vector3 degrees) {
        if(bone) bone.rotation=avatar.rotation*Quaternion.Euler(degrees)*Quaternion.Inverse(avatar.rotation)*bone.rotation;
    }
    public void Tick(float strength,string region,HumanBodyBones heldHand,EkuHeadPatOutput headPat=default) {
        float now=Time.time,dt=Mathf.Min(Time.deltaTime,.05f);contactWeight=strength;
        string previousDecision=director.CurrentAction;
        var decision=director.Step(Time.deltaTime,BehaviorInput(headPat));
        if(previousDecision!="idle" && director.CurrentAction=="idle" && !string.IsNullOrEmpty(director.CancelReason))
            StopIdle(director.CancelReason);
        if(!string.IsNullOrEmpty(decision.action))RunBehavior(decision);
        if(afk!="") {
            body=afkBodies[afk];
            if(now-phaseAt>=body.duration) {
                if(afk=="outro") {
                    afk="";SetPosture(afterWake,afterWakePinned);
                    if(!contact && controlReason=="" && !blocked && !working) {
                        if(queuedHand!="") Hand(queuedHand);
                    }
                    queuedHand="";
                }
                else {
                    afk=afk=="intro"?"main":"loop";phaseAt=now;
                    // Sample the new phase in this frame; the old clip at time
                    // zero briefly pulls the body back to its starting pose.
                    body=Clip("AFK",afk=="main"?"AFK_Action_Main_VRSuya":"AFK_Action_Loop_VRSuya");
                }
            }
        } else {
            if(!pinned && !contact && posture!="stand" && now>=poseUntil) SetPosture("stand",false);
        }
        idleHead=Vector2.Lerp(idleHead,IdleGaze(.55f,.8f),1-Mathf.Exp(-dt/.1f));
        // Eyes lead the glance, then recenter as the head catches up.
        var eyeTarget=IdleGaze(.22f,.45f)-new Vector2(idleHead.x*.46f,idleHead.y*.368f);
        idleEyes=Vector2.Lerp(idleEyes,eyeTarget,1-Mathf.Exp(-dt/.06f));
        Array.Copy(rest.muscles,targetMuscles,targetMuscles.Length);
        var desiredPosition=rest.bodyPosition;var desiredRotation=rest.bodyRotation;
        float age=afk!=""?now-phaseAt:now-bodyAt;
        if(body!=null && (posture!="stand" || afk!="")) {
            float time=afk=="loop"?age%Mathf.Max(body.duration,.01f):Mathf.Clamp(age,0,body.duration);
            foreach(var c in body.curves) if(c.muscle>=0) targetMuscles[c.muscle]=c.curve.Evaluate(time);
            var standing=Clip("Pose","Standing");
            var root=new Vector3(body.Value("RootT.x",time,standing.Value("RootT.x",0)),body.Value("RootT.y",time,standing.Value("RootT.y",0)),body.Value("RootT.z",time,standing.Value("RootT.z",0)));
            var origin=new Vector3(standing.Value("RootT.x",0),standing.Value("RootT.y",0),standing.Value("RootT.z",0));
            desiredPosition+=root-origin;
            var q=new Quaternion(body.Value("RootQ.x",time),body.Value("RootQ.y",time),body.Value("RootQ.z",time),body.Value("RootQ.w",time,1));
            desiredRotation=desiredRotation*q;
        }
        float bodyBlend=1-Mathf.Exp(-dt/.12f);
        bool raising=now>=handAt && now<handUntil && afk=="" && !contact;
        handWeight=Mathf.MoveTowards(handWeight,raising?1:0,dt/(raising?handRise:handFall));
        if(now>=handUntil && handWeight<.001f) idleHand=false;
        var restingHands=posture=="crouch"?Clip("Hand","desktop_croushing_hands"):posture=="prone"?Clip("Hand","desktop_prone_hands"):Clip("Hand","idle");
        if(afk=="") ApplyHand(restingHands,targetMuscles,0,1,false,false);
        // Arm presentation and fingers share one pose writer and one envelope.
        // Rotating a normalized VRM elbow around local Y only twists it.
        if(handWeight>0) {
            float lift=-.1f,front=.05f,twist=.65f,bend=-.75f,roll=-.7f,wrist=0,tilt=0;
            // Different hand shapes need different elbow and palm presentation.
            switch(handName) {
                case "thumbsup":lift=-.35f;front=.25f;twist=.05f;bend=-.65f;roll=.65f;break;
                case "rocknroll":lift=-.25f;front=-.15f;twist=.85f;bend=-.65f;roll=-.9f;wrist=-.12f;tilt=.12f;break;
                case "gun":lift=-.35f;front=-.6f;twist=.1f;bend=.9f;roll=.65f;tilt=-.1f;break;
                case "Fist":lift=-.28f;front=.12f;twist=.4f;bend=-.8f;roll=.35f;break;
            }
            if(idleHand) {lift+=Mathf.Sin((now-handAt)*1.7f)*.012f;tilt+=idleWrist;}
            ArmMuscle("Right Arm Down-Up",lift);
            ArmMuscle("Right Arm Front-Back",front);
            ArmMuscle("Right Arm Twist In-Out",twist);
            ArmMuscle("Right Forearm Stretch",bend);
            ArmMuscle("Right Forearm Twist In-Out",roll);
            ArmMuscle("Right Hand Down-Up",wrist);
            ArmMuscle("Right Hand In-Out",greeting?Mathf.Sin(Mathf.Max(0,now-handAt-.5f)*7)*.18f:tilt);
            ApplyHand(handClip,targetMuscles,now-handAt,handWeight,false,true);
            if(idleHand) {
                targetMuscles[muscleIndices["Spine Left-Right"]]+=idleLean*Ease(handWeight);
                targetMuscles[muscleIndices["Head Tilt Left-Right"]]-=idleLean*Ease(handWeight);
            }
        }
        if(strength>0 && region=="hand") ApplyHand(Clip("Hand","Open"),targetMuscles,0,strength,heldHand==HumanBodyBones.LeftHand,heldHand==HumanBodyBones.RightHand);
        if(wardrobe && wardrobe.State.outfit=="uniform" && wardrobe.State.uniform.dress && posture=="stand" && afk=="") {
            // The wider uniform hem needs a little clearance for relaxed hands.
            // Fade the raised side out so gesture presentation stays unchanged.
            targetMuscles[muscleIndices["Left Arm Down-Up"]]+=.12f;
            targetMuscles[muscleIndices["Right Arm Down-Up"]]+=.12f*(1-handWeight);
        }
        if(wardrobe && wardrobe.State.outfit=="uniform" && wardrobe.State.uniform.jacket && afk!="") {
            // Give the wide sleeves space in the compact folded rest poses.
            targetMuscles[muscleIndices["Left Arm Front-Back"]]-=.06f;
            targetMuscles[muscleIndices["Right Arm Front-Back"]]-=.06f;
        }
        if(wardrobe && wardrobe.State.outfit=="uniform" && wardrobe.State.uniform.dress && posture=="crouch" && afk=="") {
            // Clear the skirt front without folding the two cuffs into each other.
            targetMuscles[muscleIndices["Left Arm Front-Back"]]-=.14f;
            targetMuscles[muscleIndices["Right Arm Front-Back"]]-=.14f*(1-handWeight);
            targetMuscles[muscleIndices["Left Arm Down-Up"]]+=.18f;
            targetMuscles[muscleIndices["Right Arm Down-Up"]]+=.18f*(1-handWeight);
        }
        if(reachMotionState.grip>0 && !ReachControlled) {
            var grip=Clip("Hand","Fist");
            if(grip!=null)ApplyHand(grip,targetMuscles,grip.duration,reachState.Weight*reachMotionState.grip,false,true);
        }
        for(int i=0;i<pose.muscles.Length;i++) pose.muscles[i]=Mathf.Lerp(pose.muscles[i],targetMuscles[i],bodyBlend);
        bodyPosition=Vector3.Lerp(bodyPosition,desiredPosition,bodyBlend);bodyRotation=Quaternion.Slerp(bodyRotation,desiredRotation,bodyBlend);
        pose.bodyPosition=bodyPosition;pose.bodyRotation=bodyRotation;handler.SetHumanPose(ref pose);
        Array.Copy(neutral,weights,neutral.Length);
        facial.ApplyEmotion(weights,emotion,dt);
        faceWeight=Mathf.MoveTowards(faceWeight,now<faceUntil?1:0,dt/(now<faceUntil?faceRise:faceFall));
        if(faceWeight<=0 && now>=faceUntil) {expression="neutral";face=null;}
        if(face!=null) FaceCurves(face,Mathf.Min(now-faceAt,face.duration),(softMood?Ease(faceWeight):faceWeight)*faceAmount);
        if(working && afk=="" && faceWeight<.01f) FaceCurves(Clip("Facial","Curious"),0,.5f);
        if(afk!="") {
            FaceCurves(afkFaces[afk],Mathf.Clamp(now-phaseAt,0,body.duration),1);
        }
        var patFace=headPat;
        float acceptance=emotion==null?1:emotion.Acceptance;
        patFace.relax*=acceptance;patFace.smile*=acceptance;
        facial.ApplyHeadPat(weights,patFace,neutral);
        // Blink remains available through the original VRM export's renamed shape.
        if(now>=nextBlink) {
            blinkAt=now;blinkClose=UnityEngine.Random.Range(.08f,.12f);blinkOpen=UnityEngine.Random.Range(.13f,.2f);
            float pause=!doubleBlink && UnityEngine.Random.value<.12f?UnityEngine.Random.Range(.28f,.42f):UnityEngine.Random.Range(3.3f,7.5f);
            doubleBlink=pause<1;nextBlink=now+pause;
        }
        float blinkAge=now-blinkAt;
        float blink=expression=="blink" && now<faceUntil?1:blinkAge<blinkClose?Ease(blinkAge/blinkClose):1-Ease((blinkAge-blinkClose)/blinkOpen);
        if(afk=="" && !expression.StartsWith("Wink",StringComparison.OrdinalIgnoreCase) && (expression=="blink" || faceWeight<.8f || softMood) && patFace.relax<.5f && shapes.TryGetValue("まばたき",out int blinkIndex)) weights[blinkIndex]=Mathf.Max(weights[blinkIndex],100*blink*(softMood?1-faceAmount*faceWeight:1));
        if(cheek!=null) {
            cheek.Acceptance=emotion==null?1:emotion.Acceptance;
            if(afk=="") facial.ApplyCheek(weights,neutral);
        }
        facial.NormalizeClosures(weights);
        for(int i=0;i<weights.Length;i++) skin.SetBlendShapeWeight(i,Mathf.Clamp(weights[i],0,100));
        float earContact=region=="head"?headPat.strength:0;
        var earClip=afk!=""?Clip("Kemo Ear","Down1"):ears;
        var tailClip=earContact>0?Clip("Kemo Tail","Shake"):afk!=""?Clip("Kemo Tail","Down"):tail;
        float faceMood=(softMood?Ease(faceWeight):faceWeight)*faceAmount;
        float mood=Mathf.Max(faceMood,earContact);if(afk!="") mood=faceMood=1;
        bool gentle=softMood && strength<=0 && afk=="";
        BoneCurves(earClip,0,gentle?Mathf.Min(faceMood,Ease((now-faceAt-.1f)/.4f)):faceMood,false);
        // HalfFold contains only right-ear curves; Down2 supplies both sides.
        BoneCurves(Clip("Kemo Ear","Down2"),0,1,false,true,
            Mathf.Clamp01(headPat.earWeights.x)*acceptance,Mathf.Clamp01(headPat.earWeights.y)*acceptance);
        BoneCurves(Clip("Kemo Ear","Up"),0,1,false,true,
            Mathf.Clamp01(headPat.earLiftWeights.x)*acceptance,Mathf.Clamp01(headPat.earLiftWeights.y)*acceptance);
        if(now<earUntil) {
            float t=now-earAt;
            float amount=earAmount*(afk!=""?.45f:1)*Ease(1-(now-earFadeAt)/.2f);
            // Two small folds; the second is lighter and the ears move in turn.
            float left=EarPulse(t,.3f,.3f,.8f)+.38f*EarPulse(t-1.3f,.18f,.1f,.38f);
            float right=EarPulse(t-.18f,.3f,.3f,.8f)+.38f*EarPulse(t-1.4f,.18f,.1f,.38f);
            BoneCurves(Clip("Kemo Ear",afk!=""?"Up":"Down1"),0,amount,false,true,left,right);
        }
        for(int i=0;i<EarBones.Length;i++) if(nodes.TryGetValue(EarBones[i],out var node)) earPose[i]=node.localRotation;
        float tailTime=Mathf.Max(0,now-faceAt-(gentle?.24f:0))*(gentle?idleTailSpeed:1);
        BoneCurves(tailClip,tailTime%1,gentle?Mathf.Min(mood,Ease((now-faceAt-.24f)/.55f))*.8f:mood,true);
        if(nodes.TryGetValue("Tail_Root",out var tailRoot)) tailRoot.localRotation=boneRest[tailRoot]*Quaternion.Euler(0,Mathf.Sin(now*.7f)*2.8f,0);
    }
    public void ApplyEarPhysics() {
        // VRM's ear springs write a cached rest pose. Retain their physical
        // offset while restoring the animated pose, as with an additive layer.
        foreach(int i in SpringEarIndices) if(nodes.TryGetValue(EarBones[i],out var node))
            node.localRotation=earPose[i]*Quaternion.Inverse(boneRest[node])*node.localRotation;
    }
    void ArmMuscle(string name,float value) {
        int index=muscleIndices[name];targetMuscles[index]=Mathf.Lerp(targetMuscles[index],value,idleHand?Ease(handWeight):handWeight);
    }
    void ApplyHand(EkuAnimationClip clip,float[] muscles,float time,float weight,bool onlyLeft,bool onlyRight) {
        if(clip==null) return;
        foreach(var c in clip.curves) if(c.muscle>=0) {
            string n=HumanTrait.MuscleName[c.muscle];
            bool finger=n.Contains("Thumb") || n.Contains("Index") || n.Contains("Middle") || n.Contains("Ring") || n.Contains("Little");
            if(!finger || (onlyLeft && !n.StartsWith("Left")) || (onlyRight && !n.StartsWith("Right"))) continue;
            muscles[c.muscle]=Mathf.Lerp(muscles[c.muscle],c.curve.Evaluate(Mathf.Clamp(time,0,clip.duration)),weight);
        }
    }
    void FaceCurves(EkuAnimationClip clip,float time,float weight) {
        facial.Sample(clip,time,weight,weights);
    }
    void BoneCurves(EkuAnimationClip clip,float time,float weight,bool tailOnly,bool overlay=false,float left=1,float right=1) {
        if(!overlay) foreach(string name in tailOnly?TailBones:EarBones)
            if(nodes.TryGetValue(name,out var node)) node.localRotation=boneRest[node];
        if(clip==null || weight<=0 || !boneClips.TryGetValue(clip,out var cached)) return;
        foreach(var binding in tailOnly?cached.tail:cached.ears) {
            float side=overlay?(binding.left?left:right):1;
            var target=binding.before*binding.Evaluate(time)*binding.after;
            binding.node.localRotation=Quaternion.Slerp(binding.node.localRotation,target,weight*side);
        }
    }
    public bool Resting => afk!="";
    public EkuAnimationStatus Status() {
        var active=shapes.Where(p=>weights[p.Value]>.01f).ToArray();
        return new EkuAnimationStatus{posture=posture,afk=afk,expression=expression,hand=handWeight>.01f?(greeting?"greeting":handName):"",automatic=automatic,blocked=blocked,idle=idle,faceWeight=faceWeight,contactWeight=contactWeight,faceShapes=weights.Length,
            uniformContacts=wardrobe?wardrobe.UniformContacts:0,
            director=director.Status(),reach=reachState,reachMotion=reachMotionState,reachRoot=avatar.position,reachForward=behaviorCamera?-behaviorCamera.transform.forward:avatar.forward,reachFrontPlane=reachPlanePoint,
            reachBoneRotations=ReachBones.Select(b=>rig.GetBoneTransform(b)).Select(t=>t?t.rotation:Quaternion.identity).ToArray(),
            reachBonePositions=ReachBones.Select(b=>rig.GetBoneTransform(b)).Select(t=>t?t.position:Vector3.zero).ToArray(),
            approachMode=director.CurrentAction=="approach"?"torso-lean":"",
            reachPalmViewport=PalmViewport(),
            emotion=emotion?.Status(),cheek=cheek==null?default:cheek.Status(),
            cheekLeftCapabilities=facial.LeftGeometryCapabilities,cheekRightCapabilities=facial.RightGeometryCapabilities,
            cheekMissingShapes=facial.MissingShapes,
            behavior=afk!=""?"rest/"+afk:contact?"contact":controlReason!=""?controlReason:Time.time<idleUntil?idleAction:posture,
            blockReason=BlockReason,lastCancelReason=lastCancelReason,pinned=pinned,
            lastShort=lastShort,previousShort=previousShort,handCooldown=director.HandCooldown,interactionIdle=interactionIdle,
            behaviorRemaining=afk!=""?Mathf.Max(0,body.duration-(Time.time-phaseAt)):
                Time.time<idleUntil?idleUntil-Time.time:pinned?-1:posture!="stand"?Mathf.Max(0,poseUntil-Time.time):0,
            headHairDegrees=headHair.Select((b,i)=>Quaternion.Angle(headHairRest[i],b.localRotation)).DefaultIfEmpty(0).Max(),
            hips=rig.GetBoneTransform(HumanBodyBones.Hips).position,head=rig.GetBoneTransform(HumanBodyBones.Head).position,
            leftHand=rig.GetBoneTransform(HumanBodyBones.LeftHand).position,rightHand=rig.GetBoneTransform(HumanBodyBones.RightHand).position,
            rightElbow=rig.GetBoneTransform(HumanBodyBones.RightLowerArm).position,handWeight=handWeight,handAge=Time.time-handAt,
            rightPalm=rig.GetBoneTransform(HumanBodyBones.RightHand).TransformDirection(palmNormal),
            rightAim=(rightFingers[5].position-rightFingers[3].position).normalized,
            idleAction=Time.time<idleUntil?idleAction:"",idleAge=Time.time-idleAt,idleWait=director.AmbientCooldown,faceAmount=faceAmount,
            earAction=Time.time<earUntil?"ears":"",earRotations=EarBones.Select(n=>nodes.TryGetValue(n,out var node)?node.localRotation:Quaternion.identity).ToArray(),
            rightFingers=rightFingers.Select(t=>t?t.localRotation:Quaternion.identity).ToArray(),
            activeShapes=active.Select(p=>p.Key).ToArray(),shapeWeights=active.Select(p=>weights[p.Value]).ToArray()};
    }
    public void Dispose() {handler.Dispose();}
}
