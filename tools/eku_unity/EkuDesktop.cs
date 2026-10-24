using System;
using System.Collections.Generic;
using System.IO.MemoryMappedFiles;
using System.Text;
using UnityEngine;

[Serializable] public class EkuPointer {
    public float x,y,screenX,screenY,dpi,width,height;
    public bool active,valid,allowAttention,leftDown;
    public long sampleSeq;
    public double at;
    public string mode;
}
[Serializable] public class EkuAnimationContext {
    public int version;
    public int emotionVersion,emotionRevision;
    public float mood,energy;
    public float idle,interactionIdle;
    public bool blocked,working,automatic,moving;
    public string blockReason;
}
[Serializable] public class EkuDesktopAction {
    public string kind,phase,name,emotion,region,reason;
    public long sampleSeq;
    public double at;
    public float path;
    public int id;
    public float x,y,dx,dy,moved,width,height,duration,value;
    public bool enabled,blocked,working,automatic,close_menu;
    public float idle;
    public float interactionIdle;
    public bool moving;
    public string blockReason;
    public EkuAnimationContext context;
    public EkuMouseSample mouse;
    public int emotionVersion,emotionRevision;
    public float mood,energy;
    public EkuPointer pointer;
    public EkuWardrobeState wardrobe;
    public int revision;
}
[Serializable] public class EkuFeedback {
    public int id,emotionVersion=1,mouseTargetVersion=1;
    public float gentleSeconds;
    public string region,phase,kind,reason,side,interactionType;
    public bool cancelled;
    public EkuAnimationStatus animation;
    public EkuWardrobeStatus wardrobe;
    public EkuEmotionStatus emotion;
    public EkuMouseTargetSnapshot mouseTarget;
    public EkuAttentionOutput attention;
}

// Interactive local trial: existing Windows input sends the same begin/update/end events.
public sealed class EkuDesktop : IDisposable {
    public int RenderWidth { get; private set; }
    public int RenderHeight { get; private set; }
    public bool MagnifierEnabled { get; private set; }
    public bool MagnifierMoving { get; private set; }
    public bool MagnifierVisible => MagnifierEnabled;
    public Vector2 MagnifierSelection { get; private set; }
    public Transform MagnifierAnchor { get; private set; }
    public Vector3 MagnifierLocalPoint { get; private set; }
    Transform pickedAnchor;
    Vector3 pickedPoint;
    public float DragLag => moveLag.magnitude;
    public float MaxDragLag { get; private set; }
    public int DragActions { get; private set; }
    readonly Transform avatar;
    readonly Animator rig;
    readonly Camera view;
    readonly float height;
    readonly EkuAnimationPlayer animations;
    public bool Resting => animations.Resting;
    readonly EkuWardrobe wardrobe;
    float nextStatus;
    readonly Vector3 avatarPosition;
    Vector2 moveLag,moveTarget;
    readonly MemoryMappedFile mapping;
    readonly MemoryMappedViewAccessor control;
    readonly Queue<EkuFeedback> feedback=new Queue<EkuFeedback>();
    readonly Dictionary<Transform,HumanBodyBones> humanoid=new Dictionary<Transform,HumanBodyBones>();
    readonly List<PickMesh> meshes=new List<PickMesh>();
    class PickMesh { public SkinnedMeshRenderer skin; public Mesh source,mesh; public MeshCollider collider; public EkuUniformCollision surface; public BoneWeight[] weights; public int[] triangles,submeshEnds; public Transform[] bones; public string[] materials; public bool head; }
    readonly EkuHeadPatSettings settings;
    readonly EkuHeadPat headPat;
    readonly EkuEmotion emotion;
    readonly EkuCheek cheek;
    readonly CheekDeformationProfile cheekProfile;
    readonly bool ownsCheekProfile;
    readonly EkuMouseTarget mouseTarget;
    readonly EkuAttention attention;
    readonly EkuInteractionProfile interactionProfile;
    readonly bool ownsInteractionProfile;
    readonly EkuInteractionHover hover;
    public EkuInteractionRuntime Interactions { get; private set; }
    EkuInteractionRegion pickedRegionDefinition,selectedRegion;
    bool environmentBlocked,environmentWorking,environmentAutomatic=true;
    string environmentReason="";
    float environmentIdle;
    double nextHover;
    float gentleSeconds;
    readonly Transform headBone,neckBone;
    EkuInputSnapshot input;
    public EkuInputSnapshot Input => input;
    public EkuMouseTargetSnapshot MouseTarget => mouseTarget.Snapshot;
    int lastContactId;
    double sourceBegin,sourceAt,receivedAt;
    float sampledPath,pickMilliseconds;
    long evaluatorAllocatedBytes;
    string contactPhase="idle",cancelReason="";
    EkuDesktopAction contact;
    string region="",heldRegion="",gesture="";
    bool quiet,menu,engaged,moving;
    HumanBodyBones hand=HumanBodyBones.LastBone;
    float contactAt,distance,lastX,lastY,strength,gestureAt=-100,lookX,lookY,lookTx,lookTy;
    float gestureDuration=2.4f,gestureAmount=1;
    float yaw,postureYaw;
    float outsideAt=-1;
    long response;
    public EkuDesktop(Transform avatar,Animator rig,Camera view,string map,float height,EkuAnimationLibrary library,EkuHeadPatSettings headPatSettings=null,EkuEmotionSettings emotionSettings=null,CheekDeformationProfile profile=null,EkuAttentionSettings attentionSettings=null,EkuInteractionProfile interactionSettings=null,EkuBehaviorSettings behaviorSettings=null,EkuReachSettings reachSettings=null,EkuFacialSettings facialSettings=null) {
        this.avatar=avatar;this.rig=rig;this.view=view;this.height=height;
        settings=headPatSettings??new EkuHeadPatSettings();headPat=new EkuHeadPat(settings);
        headBone=rig.GetBoneTransform(HumanBodyBones.Head);neckBone=rig.GetBoneTransform(HumanBodyBones.Neck);
        emotion=new EkuEmotion(emotionSettings);
        ownsCheekProfile=!profile;cheekProfile=profile?profile:ScriptableObject.CreateInstance<CheekDeformationProfile>();
        cheek=new EkuCheek(cheekProfile);
        mouseTarget=new EkuMouseTarget(view,avatar,headBone,height);
        attention=new EkuAttention(view,avatar,headBone,height,attentionSettings);
        ownsInteractionProfile=!interactionSettings;
        interactionProfile=interactionSettings?interactionSettings:ScriptableObject.CreateInstance<EkuInteractionProfile>();
        hover=new EkuInteractionHover(avatar,rig,height,interactionProfile);
        Interactions=new EkuInteractionRuntime(interactionProfile);
        Interactions.InterruptRequested+=reason=>Finish(true,reason);
        avatarPosition=avatar.localPosition;
        animations=new EkuAnimationPlayer(avatar,rig,library);
        animations.SetEmotion(emotion);animations.SetCheek(cheek);
        animations.ConfigureBehavior(behaviorSettings,reachSettings,view,height);animations.ConfigureFacial(facialSettings);
        wardrobe=avatar.GetComponent<EkuWardrobe>();
        if(wardrobe)feedback.Enqueue(new EkuFeedback{kind="wardrobe",wardrobe=wardrobe.Status()});
        mapping=MemoryMappedFile.CreateOrOpen(map,131072);
        control=mapping.CreateViewAccessor();
        foreach(HumanBodyBones bone in Enum.GetValues(typeof(HumanBodyBones))) {
            if(bone==HumanBodyBones.LastBone) continue;
            var node=rig.GetBoneTransform(bone);if(node) humanoid[node]=bone;
        }
        foreach(var skin in avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true)) {
            var node=new GameObject("Local touch picking");node.transform.SetParent(skin.transform,false);
            meshes.Add(new PickMesh{skin=skin,source=skin.sharedMesh,mesh=new Mesh(),collider=node.AddComponent<MeshCollider>(),
                surface=skin.GetComponent<EkuUniformCollision>(),
                weights=skin.sharedMesh.boneWeights,triangles=skin.sharedMesh.triangles});
        }
        foreach(var mesh in meshes) CachePick(mesh);
    }
    void Reply(int id,string part,string phase,bool cancelled=false,string reason="") {
        if(feedback.Count<64)feedback.Enqueue(new EkuFeedback{id=id,region=part,phase=phase,cancelled=cancelled,reason=reason,
            gentleSeconds=gentleSeconds,interactionType=cheek.Active?"cheek":part=="head"?"headpat":"contact",
            side=cheek.Active?cheek.Side.ToString():""});
    }
    void FlushFeedback() {
        if(control.ReadInt64(32)!=response*2) return;
        if(feedback.Count==0 && Time.time>=nextStatus) {
            feedback.Enqueue(new EkuFeedback{kind="animation",animation=Status()});
            nextStatus=Time.time+1;
        }
        if(feedback.Count==0) return;
        var message=feedback.Dequeue();message.mouseTarget=mouseTarget.Snapshot;message.emotion=emotion.Status();message.attention=attention.Output;
        byte[] bytes=Encoding.UTF8.GetBytes(JsonUtility.ToJson(message));
        long next=++response;control.Write(24,next*2-1);control.Write(20,bytes.Length);
        control.WriteArray(65536,bytes,0,bytes.Length);control.Write(24,next*2);
    }
    void CachePick(PickMesh mesh) {
        mesh.source=mesh.skin.sharedMesh;mesh.weights=mesh.source.boneWeights;mesh.triangles=mesh.source.triangles;
        mesh.bones=mesh.skin.bones;var materials=mesh.skin.sharedMaterials;
        mesh.materials=new string[materials.Length];mesh.submeshEnds=new int[mesh.source.subMeshCount];
        mesh.head=false;int count=0;
        for(int i=0;i<materials.Length;i++) {
            mesh.materials[i]=materials[i]?materials[i].name.ToLowerInvariant():"";
            mesh.head|=mesh.materials[i].Contains("hair") || mesh.materials[i].Contains("face");
        }
        // Shared skeletons also list unused head bones on clothes and stockings.
        // Only influences with nonzero weight make a mesh part of head picking.
        if(!mesh.head) {
            var headBones=new bool[mesh.bones.Length];
            for(int i=0;i<headBones.Length;i++)headBones[i]=mesh.bones[i] && (mesh.bones[i]==headBone || mesh.bones[i].IsChildOf(headBone));
            foreach(var weight in mesh.weights) {
                if((weight.weight0>0 && headBones[weight.boneIndex0]) || (weight.weight1>0 && headBones[weight.boneIndex1]) ||
                   (weight.weight2>0 && headBones[weight.boneIndex2]) || (weight.weight3>0 && headBones[weight.boneIndex3])) {mesh.head=true;break;}
            }
        }
        for(int i=0;i<mesh.submeshEnds.Length;i++) {count+=(int)mesh.source.GetIndexCount(i)/3;mesh.submeshEnds[i]=count;}
    }
    string Pick(float x,float y,out HumanBodyBones pickedHand,bool headOnly=false,bool faceOnly=false) {
        long started=System.Diagnostics.Stopwatch.GetTimestamp();
        pickedHand=HumanBodyBones.LastBone;pickedAnchor=null;pickedRegionDefinition=null;
        var ray=view.ViewportPointToRay(new Vector3(x,1-y,0));
        foreach(var mesh in meshes) {
            if(mesh.source!=mesh.skin.sharedMesh) {CachePick(mesh);mesh.mesh.Clear();}
            bool enabled=mesh.skin.enabled && mesh.skin.gameObject.activeInHierarchy && (!headOnly || mesh.head)
                && (!faceOnly || mesh.skin.name=="Body") && mesh.skin.bounds.IntersectRay(ray);
            mesh.collider.enabled=enabled;if(!enabled)continue;
            if(mesh.surface)mesh.surface.BakeSurface(mesh.mesh);else mesh.skin.BakeMesh(mesh.mesh);
            mesh.collider.sharedMesh=null;mesh.collider.sharedMesh=mesh.mesh;
        }
        Physics.SyncTransforms();
        PickMesh nearest=null;RaycastHit hit=default;float depth=float.MaxValue;
        foreach(var mesh in meshes) if(mesh.collider.enabled && (!headOnly || mesh.head) && (!faceOnly || mesh.skin.name=="Body") && mesh.collider.Raycast(ray,out var test,100) && test.distance<depth) {
            hit=test;depth=test.distance;nearest=mesh;
        }
        pickMilliseconds=(float)((System.Diagnostics.Stopwatch.GetTimestamp()-started)*1000.0/System.Diagnostics.Stopwatch.Frequency);
        if(nearest==null)return null;
        int offset=hit.triangleIndex*3,submesh=0;
        while(submesh+1<nearest.submeshEnds.Length && hit.triangleIndex>=nearest.submeshEnds[submesh])submesh++;
        string material=submesh<nearest.materials.Length?nearest.materials[submesh]:"";
        Transform weighted=null;float maxWeight=-1;
        for(int i=0;i<3;i++) {
            var weight=nearest.weights[nearest.triangles[offset+i]];
            for(int j=0;j<4;j++) {
                float amount=j==0?weight.weight0:j==1?weight.weight1:j==2?weight.weight2:weight.weight3;
                int index=j==0?weight.boneIndex0:j==1?weight.boneIndex1:j==2?weight.boneIndex2:weight.boneIndex3;
                if(amount>maxWeight && index<nearest.bones.Length) {maxWeight=amount;weighted=nearest.bones[index];}
            }
        }
        pickedAnchor=weighted;pickedPoint=hit.point;
        HumanBodyBones human=HumanBodyBones.LastBone;
        for(var node=weighted;node && node!=avatar;node=node.parent)if(humanoid.TryGetValue(node,out human))break;
        if(human==HumanBodyBones.LeftHand || human==HumanBodyBones.RightHand ||
           (human>=HumanBodyBones.LeftThumbProximal && human<=HumanBodyBones.RightLittleDistal))
            pickedHand=human==HumanBodyBones.LeftHand || (human>=HumanBodyBones.LeftThumbProximal && human<=HumanBodyBones.LeftLittleDistal)?HumanBodyBones.LeftHand:HumanBodyBones.RightHand;
        pickedRegionDefinition=interactionProfile.Resolve(nearest.skin.name,material,weighted,human);
        if(pickedRegionDefinition!=null)return pickedRegionDefinition.legacy;
        if(material.Contains("hair"))return "head";
        if(material.Contains("face"))return "face";
        if(pickedHand!=HumanBodyBones.LastBone)return "hand";
        if(human==HumanBodyBones.Head || human==HumanBodyBones.Neck)return "head";
        if(material.Contains("costume") || (wardrobe && Array.IndexOf(wardrobe.Clothing,nearest.skin)>=0)) {
            return hit.point.y<rig.GetBoneTransform(HumanBodyBones.Hips).position.y?"skirt":"cloth";
        }
        switch(human) {
            case HumanBodyBones.LeftUpperLeg:case HumanBodyBones.RightUpperLeg:
            case HumanBodyBones.LeftLowerLeg:case HumanBodyBones.RightLowerLeg:
            case HumanBodyBones.LeftFoot:case HumanBodyBones.RightFoot:
            case HumanBodyBones.LeftToes:case HumanBodyBones.RightToes:return "leg";
        }
        return human==HumanBodyBones.Chest || human==HumanBodyBones.UpperChest?"chest":"belly";
    }
    Vector3 ContactPoint() => Quaternion.Inverse(avatar.rotation)*(pickedPoint-headBone.position)/Mathf.Max(.001f,height*settings.headWidthRatio*.5f)-settings.contactCenter;
    Vector3 PointerPlane(float x,float y) {
        var ray=view.ViewportPointToRay(new Vector3(x,1-y,0));
        return new Plane(view.transform.forward,headBone.position).Raycast(ray,out float distance)?ray.GetPoint(distance):headBone.position;
    }
    bool Sample(EkuDesktopAction action,bool beginning) {
        long sequence=action.sampleSeq!=0?action.sampleSeq:action.pointer!=null?action.pointer.sampleSeq:0;
        double stamp=action.at>0?action.at:action.pointer!=null && action.pointer.at>0?action.pointer.at:Time.realtimeSinceStartupAsDouble;
        if(double.IsNaN(stamp) || double.IsInfinity(stamp) || action.at<0 || sequence<0 || !EkuEmotion.Finite(action.x) || !EkuEmotion.Finite(action.y)
           || !EkuEmotion.Finite(action.width) || !EkuEmotion.Finite(action.height) || !EkuEmotion.Finite(action.path) || !EkuEmotion.Finite(action.moved)
           || action.width<0 || action.height<0 || action.path<0 || action.moved<0)return false;
        bool hasMouse=action.mouse!=null && action.mouse.version==1;
        if(hasMouse && (!action.mouse.positionValid || !EkuEmotion.Finite(action.mouse.age) || action.mouse.age>settings.streamTimeoutSeconds))return false;
        var mouse=mouseTarget.Snapshot;
        if(hasMouse && (!mouse.valid || mouse.sequence!=action.mouse.sequence || mouse.sessionId!=action.mouse.sessionId
            || mouse.timestamp!=action.mouse.timestamp || action.mouse.sequence!=sequence || action.mouse.timestamp!=stamp))return false;
        if(!beginning && ((sequence!=0 && sequence<=input.sampleSeq) || stamp<=sourceAt))return false;
        double now=Time.realtimeSinceStartupAsDouble;float dt=beginning?0:(float)(stamp-sourceAt);
        float width=action.width>0?action.width:view.targetTexture.width;
        float heightPx=action.height>0?action.height:view.targetTexture.height;
        var delta=new Vector2((action.x-lastX)*width,(action.y-lastY)*heightPx);
        float path=action.path>0?Mathf.Max(0,action.path-sampledPath):delta.magnitude;
        input=new EkuInputSnapshot{sampleSeq=sequence,at=stamp,receivedAt=now,valid=true,leftDown=action.phase!="end" && action.phase!="cancel",
            viewport=new Vector2(action.x,action.y),path=action.path,duration=beginning?0:(float)Math.Max(0,stamp-sourceBegin)};
        if(dt>=.001f && dt<=settings.streamTimeoutSeconds) {
            input.velocityDip=delta/dt;input.speedDip=Mathf.Max(delta.magnitude,path)/dt;
            float headWidth=Vector2.Distance(view.WorldToViewportPoint(headBone.position),view.WorldToViewportPoint(headBone.position+view.transform.right*this.height*settings.headWidthRatio))*width;
            var worldDelta=PointerPlane(action.x,action.y)-PointerPlane(lastX,lastY);
            input.localDirection=Quaternion.Inverse(avatar.rotation)*worldDelta/(Mathf.Max(.001f,this.height*settings.headWidthRatio)*dt);
        }
        if(hasMouse) {
            input.allowAttention=mouse.allowAttention;input.valid=mouse.positionValid;
            input.velocityDip=mouse.velocity;input.speedDip=Mathf.Max(mouse.speed,mouse.motionSpeed);
            var velocityPoint=PointerPlane(action.x+input.velocityDip.x/width,action.y+input.velocityDip.y/heightPx)-PointerPlane(action.x,action.y);
            input.localDirection=Quaternion.Inverse(avatar.rotation)*velocityPoint/Mathf.Max(.001f,this.height*settings.headWidthRatio);
        }
        input.localPoint=ContactPoint();sourceAt=stamp;sampledPath=action.path;
        if(beginning)sourceBegin=stamp;
        else distance+=delta.magnitude;
        lastX=action.x;lastY=action.y;receivedAt=now;
        return true;
    }
    EkuAnimationStatus Status() {
        var status=animations.Status();
        status.interaction=new EkuInteractionStatus{id=contact!=null?contact.id:lastContactId,region=heldRegion,phase=contactPhase,reason=cancelReason,
            input=input,headPat=headPat.Value,cheek=cheek.Status(),lifecycle=Interactions.Current,headRotation=headBone.localRotation,neckRotation=neckBone?neckBone.localRotation:Quaternion.identity,
            pickMilliseconds=pickMilliseconds,evaluatorAllocatedBytes=evaluatorAllocatedBytes};
        if(EkuCheekFrame.TryCreate(rig,out var frame) && Vector3.Dot(frame.forward,view.transform.position-frame.origin)>0) {
            var left=view.WorldToViewportPoint(frame.origin+(frame.right*cheekProfile.leftCenter.x+frame.up*cheekProfile.leftCenter.y)*frame.eyeSeparation);
            var right=view.WorldToViewportPoint(frame.origin+(frame.right*cheekProfile.rightCenter.x+frame.up*cheekProfile.rightCenter.y)*frame.eyeSeparation);
            status.interaction.cheekRegionsValid=left.z>0 && right.z>0;
            status.interaction.cheekLeftViewport=new Vector2(left.x,1-left.y);status.interaction.cheekRightViewport=new Vector2(right.x,1-right.y);
        }
        return status;
    }
    void Finish(bool cancel,string reason="cancelled") {
        if(contact==null) return;
        bool wasCheek=cheek.Active;
        ObserveEmotion();
        if(!cancel && !wasCheek && !engaged && input.duration<.35f && contact.moved<=6 && emotion.Tap(region)) {
            animations.Touch(false);animations.Tap(region,false);
            if(region=="face") StartGesture("glance");
            else if(region=="head") StartGesture("nod");
        }
        contactPhase=cancel?"cancel":"recover";cancelReason=cancel?reason:"";
        Reply(contact.id,region,"end",cancel,cancelReason);headPat.End(cancel);
        Interactions.End(cancel,cancelReason);
        cheek.End(cancel,Time.realtimeSinceStartupAsDouble,cancelReason);mouseTarget.EndContact();
        if(cancel)emotion.Cancel(cancelReason);
        contact=null;engaged=false;input.leftDown=false;animations.Touch(false);
    }
    void Gesture(string name) {
        if(contact!=null || (name!="pat" && name!="nod" && name!="cheer" && name!="glance")) return;
        StartGesture(name);
        animations.Gesture(name);
    }
    void StartGesture(string name) {
        gesture=name;gestureAt=Time.time;
        gestureDuration=name=="nod"?UnityEngine.Random.Range(1.1f,1.6f):2.4f;
        gestureAmount=name=="nod"?UnityEngine.Random.Range(.7f,1):1;
    }
    void SetFace(string name,float duration) {
        animations.Face(name,duration);
    }
    void Handle(EkuDesktopAction action) {
        double now=Time.realtimeSinceStartupAsDouble;
        if(action.mouse!=null && action.mouse.version==1)mouseTarget.Receive(action.mouse,now);
        if(action.close_menu) menu=false;
        if(action.context!=null && action.context.version==1) {
            var context=action.context;moving=context.moving;
            Environment(context.blocked,context.working,context.automatic,context.idle,context.blockReason);
            if(context.emotionVersion==1)emotion.Context(context.mood,context.energy,context.emotionRevision);
            animations.Context(context.idle,context.blocked,context.working,context.automatic,context.blockReason,context.interactionIdle);
        }
        animations.Control(menu?"menu":quiet?"quiet":moving?"move":"");
        // Explicit controls take over immediately; ordinary pointer motion
        // keeps flowing to attention without cancelling automatic behavior.
        switch(action.kind) {
            case "menu":case "animation":case "wardrobe":case "spin":case "zoom":
            case "magnifier":case "magnifier_move":case "magnifier_select":case "quit":
                Finish(true,action.kind);gesture="";animations.StopIdle(action.kind);break;
            case "drag":
                Finish(true);gesture="";animations.Control("move");break;
        }
        switch(action.kind) {
            case "wardrobe":
                if(!wardrobe)break;
                Finish(true);
                wardrobe.Apply(action.wardrobe,action.revision);
                foreach(var mesh in meshes)CachePick(mesh);
                feedback.Enqueue(new EkuFeedback{kind="wardrobe",wardrobe=wardrobe.Status()});
                break;
            case "magnifier":
                MagnifierEnabled=action.enabled;MagnifierMoving=false;Finish(true);
                if(MagnifierEnabled) {
                    MagnifierAnchor=rig.GetBoneTransform(HumanBodyBones.Head);
                    MagnifierLocalPoint=MagnifierAnchor.InverseTransformPoint(MagnifierAnchor.position+Vector3.up*height*.045f);
                    var point=view.WorldToViewportPoint(MagnifierAnchor.TransformPoint(MagnifierLocalPoint));
                    MagnifierSelection=new Vector2(point.x,1-point.y);
                }
                break;
            case "magnifier_move":MagnifierMoving=action.enabled;Finish(true);break;
            case "magnifier_select":
                MagnifierSelection=new Vector2(Mathf.Clamp01(action.x),Mathf.Clamp01(action.y));
                if(action.phase=="end") {
                    pickedAnchor=null;
                    Pick(MagnifierSelection.x,MagnifierSelection.y,out var unusedHand);
                    if(pickedAnchor) {
                        MagnifierAnchor=pickedAnchor;
                        MagnifierLocalPoint=pickedAnchor.InverseTransformPoint(pickedPoint);
                    } else if(MagnifierAnchor) {
                        var old=view.WorldToViewportPoint(MagnifierAnchor.TransformPoint(MagnifierLocalPoint));
                        var world=view.ViewportToWorldPoint(new Vector3(MagnifierSelection.x,1-MagnifierSelection.y,old.z));
                        MagnifierLocalPoint=MagnifierAnchor.InverseTransformPoint(world);
                    }
                }
                break;
            case "interaction":
                if(action.phase=="begin") {
                    if(action.id<=lastContactId)return;
                    if(!EkuEmotion.Finite(action.x) || !EkuEmotion.Finite(action.y)) {Reply(action.id,null,"end",true,"invalid_sample");return;}
                    Finish(true,"replaced");lastContactId=action.id;gesture="";
                    region=Pick(action.x,action.y,out hand);
                    if(region==null) {Reply(action.id,null,"end",true,"miss");return;}
                    selectedRegion=pickedRegionDefinition??interactionProfile.Legacy(region);
                    bool cheekRegion=false;EkuCheekSide side=default;EkuCheekFrame frame=default;
                    if((region=="head" || region=="face") && (selectedRegion==null || selectedRegion.id=="Head" || selectedRegion.id=="Face") && EkuCheekFrame.TryCreate(rig,out frame) && Vector3.Dot(frame.forward,view.transform.position-frame.origin)>0) {
                        if(region=="face")cheekRegion=cheek.TryRegion(pickedPoint,frame,out side);
                        else {
                            var savedPoint=pickedPoint;var savedAnchor=pickedAnchor;
                            string faceHit=Pick(action.x,action.y,out var unusedHand,true,true);
                            cheekRegion=faceHit=="face" && Vector3.Distance(savedPoint,pickedPoint)<=frame.eyeSeparation*.75f && cheek.TryRegion(pickedPoint,frame,out side);
                            if(cheekRegion) {region="face";hand=HumanBodyBones.LastBone;}
                            else {pickedPoint=savedPoint;pickedAnchor=savedAnchor;}
                        }
                    }
                    if(cheekRegion)selectedRegion=interactionProfile.Region(side==EkuCheekSide.Left?"FaceLeft":"FaceRight")??selectedRegion;
                    if(!Sample(action,true)) {Reply(action.id,null,"end",true,"invalid_sample");return;}
                    animations.Touch(true);
                    contact=action;contactAt=Time.time;distance=0;
                    engaged=false;outsideAt=-1;heldRegion=region;contactPhase="contact";cancelReason="";
                    gentleSeconds=0;
                    if(!Interactions.Begin(action.id,selectedRegion,input,pickedPoint)) {Finish(true,selectedRegion==null?"region_unconfigured":"priority");return;}
                    if(selectedRegion.type=="headpat")headPat.Begin(input.localPoint,Time.realtimeSinceStartupAsDouble);
                    else headPat.End(true);
                    if(cheekRegion) {
                        cheek.Begin(action.id,side,pickedPoint,frame,input.at,now,input.sampleSeq);
                        ObserveEmotion();emotion.Tap("face",side==EkuCheekSide.Left?-1:1);
                    }
                    mouseTarget.BeginContact(pickedPoint,headBone);
                    Reply(action.id,region,"begin");
                } else if(contact!=null && (action.id==contact.id || (action.id==0 && action.phase=="cancel"))) {
                    if(action.phase=="cancel") {Finish(true,action.reason??"input_cancelled");break;}
                    if(action.phase!="update" && action.phase!="end")break;
                    if(!Sample(action,false)) {
                        if(action.mouse!=null && action.mouse.version==1 && !action.mouse.positionValid)Finish(true,"position_unavailable");
                        else if(action.phase=="end")Finish(true,"invalid_sample");
                        break;
                    }
                    contact.moved=action.moved;
                    if(cheek.Active) {
                        cheek.Sample(view.ViewportPointToRay(new Vector3(action.x,1-action.y,0)),input.at,now,input.sampleSeq);
                        if(cheek.Outside) {if(outsideAt<0)outsideAt=Time.time;}else outsideAt=-1;
                    } else if(hand==HumanBodyBones.LastBone && action.phase=="update") {
                        string current=Pick(action.x,action.y,out var ignoredHand,region=="head");
                        if(current!=region) {
                            if(outsideAt<0)outsideAt=Time.time;
                            if(region=="head")headPat.Sample(input.localPoint,Vector3.zero,0,now);
                        }
                        else {
                            outsideAt=-1;input.localPoint=ContactPoint();
                            if(region=="head") {
                                float projected=Vector2.Distance(view.WorldToViewportPoint(headBone.position),view.WorldToViewportPoint(headBone.position+view.transform.right*height*settings.headWidthRatio))*Mathf.Max(action.width,1);
                                headPat.Sample(input.localPoint,input.localDirection,input.speedDip/Mathf.Max(projected,1),Time.realtimeSinceStartupAsDouble);
                            }
                        }
                    }
                    Interactions.Update(input,pickedPoint,outsideAt<0);
                    if(action.phase=="end")Finish(false);
                }
                break;
            case "interaction_status":feedback.Enqueue(new EkuFeedback{kind="animation",animation=Status()});break;
            case "spin": Finish(true,"rotation");yaw+=action.dx*.0015f*Mathf.Rad2Deg;avatar.localRotation=Quaternion.Euler(0,180-yaw,0);break;
            case "drag":
                // Native Windows movement already carries the pet across the screen.
                // Match the Three renderer's small root lag to drive the spring bones.
                float metresPerPixel=2*Mathf.Abs(view.transform.position.z)*Mathf.Tan(view.fieldOfView*Mathf.Deg2Rad/2)/view.targetTexture.height;
                moveTarget=new Vector2(Mathf.Clamp(-action.dx*metresPerPixel*1.8f,-.055f,.055f),
                    Mathf.Clamp(action.dy*metresPerPixel*1.8f,-.045f,.045f));
                DragActions++;break;
            case "zoom": Finish(true,"resize");RenderWidth=(int)action.width;RenderHeight=(int)action.height;break;
            case "look":
                if(!mouseTarget.HasSample) {lookTx=Mathf.Clamp(action.x*1.1f,-.2f,.2f);lookTy=Mathf.Clamp(action.y,-.17f,.17f);}
                break;
            case "quiet":quiet=action.enabled;if(quiet) {Finish(true,"quiet");animations.StopIdle();gesture="";}break;
            case "menu":menu=true;animations.StopIdle();Finish(true,"menu");gesture="";break;
            case "menu_close":menu=false;break;
            case "fps":Application.targetFrameRate=Mathf.Clamp((int)action.value,15,60);break;
            case "interact":
                if(action.name=="pat" || action.name=="nod" || action.name=="cheer") Gesture(action.name);
                break;
            case "face":SetFace(action.emotion,action.duration);break;
            case "react":animations.Tap(action.region??"head",false);StartGesture("nod");break;
            case "state":animations.State(action.name=="working");if(action.name!="working") Gesture("nod");break;
            case "animation":Finish(true,"animation");animations.Animation(action.name);break;
            case "context":
                moving=action.moving;
                Environment(action.blocked,action.working,action.automatic,action.idle,action.blockReason);
                if(action.emotionVersion==1)emotion.Context(action.mood,action.energy,action.emotionRevision);
                animations.Context(action.idle,action.blocked,action.working,action.automatic,action.blockReason,action.interactionIdle);
                break;
            case "greet":animations.Greeting();break;
            case "music":animations.Music();break;
            case "quit":Finish(true);Application.Quit();break;
        }
        animations.Control(menu?"menu":quiet?"quiet":moving?"move":"");
        if(moving && contact!=null)Finish(true,"move");
    }
    void Environment(bool blocked,bool working,bool automatic,float idle,string reason) {
        environmentBlocked=blocked;environmentWorking=working;environmentAutomatic=automatic;
        environmentIdle=idle;environmentReason=reason??"";
    }
    void UpdateMouse(double now) {
        mouseTarget.Refresh(now);
        if(!mouseTarget.HasSample)return;
        var target=mouseTarget.Snapshot;
        attention.Step(Time.deltaTime,now,target,new EkuAttentionContext{blocked=environmentBlocked || quiet || menu || moving,
            contact=contact!=null || headPat.Value.strength>.01f || cheek.Contact.sqrMagnitude>.0001f,resting=animations.Resting,
            working=environmentWorking,automatic=environmentAutomatic,reason=menu?"menu":quiet?"quiet":moving?"move":environmentReason,
            energy=emotion.Energy,curiosity=emotion.Curiosity,acceptance=emotion.Acceptance});
        if(contact==null && now>=nextHover) {
            nextHover=now+Mathf.Max(.03f,interactionProfile.hoverInterval);
            EkuInteractionRegion hovered=null;Vector3 point=default;
            if(target.valid && target.insideViewport && !quiet && !menu && !moving)
                hovered=hover.Pick(view.ViewportPointToRay(new Vector3(target.windowPosition.x,1-target.windowPosition.y,0)),out point);
            if(hovered!=null && hovered.id=="Face" && EkuCheekFrame.TryCreate(rig,out var hoverFrame) && cheek.TryRegion(point,hoverFrame,out var hoverSide))
                hovered=interactionProfile.Region(hoverSide==EkuCheekSide.Left?"FaceLeft":"FaceRight")??hovered;
            Interactions.Hover(hovered,new EkuInputSnapshot{sampleSeq=target.sequence,at=target.timestamp,receivedAt=now,
                valid=target.valid,allowAttention=target.allowAttention,viewport=target.windowPosition,velocityDip=target.velocity,
                speedDip=target.speed,leftDown=target.isPressed},point);
        }
        if(attention.Output.enabled) {lookTx=lookTy=0;return;}
        bool legacyAttention=target.valid && (target.allowAttention || target.forcedLook) && !quiet && !menu && contact==null;
        lookTx=legacyAttention?Mathf.Clamp(target.lookPosition.x*1.1f,-.2f,.2f):0;
        lookTy=legacyAttention?Mathf.Clamp(target.lookPosition.y,-.17f,.17f):0;
    }
    public void ApplyEarPhysics() => animations.ApplyEarPhysics();
    void ObserveEmotion() {
        emotion.Observe(new EkuEmotionInput{attention=attention.Output.awareness,novelty=attention.Output.novelty,
            idleSeconds=environmentIdle,contact=contact!=null,faceContact=cheek.Active,resting=animations.Resting,
            working=environmentWorking,inhibited=menu || moving || (contact!=null && !input.valid),
            side=cheek.Active?(cheek.Side==EkuCheekSide.Left?-1:1):0});
    }
    public void Tick() {
        int seq=control.ReadInt32(0);
        if(seq!=control.ReadInt32(12)) {
            int length=control.ReadInt32(4);
            if(length>0 && length<=32768) {
                var bytes=new byte[length];control.ReadArray(64,bytes,0,length);
                if(control.ReadInt32(0)==seq) {
                    try {Handle(JsonUtility.FromJson<EkuDesktopAction>(Encoding.UTF8.GetString(bytes)));}
                    catch(Exception error) {Debug.LogException(error);}
                    control.Write(12,seq);
                }
            }
        }
        avatar.localPosition=avatarPosition;
        if(contact!=null) {
            float age=Time.time-contactAt;
            if(Time.realtimeSinceStartupAsDouble-receivedAt>settings.streamTimeoutSeconds)Finish(true,"timeout");
            else if(outsideAt>=0 && Time.time-outsideAt>=settings.outsideGraceSeconds)Finish(true,"outside");
            else if((region=="head" && (age>=.35f || distance>=8)) || (age>=.35f && hand!=HumanBodyBones.LastBone))engaged=true;
        }
        strength=Mathf.MoveTowards(strength,engaged?1:0,Time.deltaTime/(engaged ? .2f : .28f));
        postureYaw=Mathf.LerpAngle(postureYaw,animations.PostureYaw,1-Mathf.Exp(-Mathf.Min(Time.deltaTime,.05f)/.12f));
        avatar.localRotation=Quaternion.Euler(0,180-yaw+postureYaw,0);
        long allocated=GC.GetAllocatedBytesForCurrentThread();
        var pat=headPat.Tick(Time.deltaTime,Time.realtimeSinceStartupAsDouble);
        float gentle=pat.active && pat.stroking && !pat.rough && outsideAt<0 && Time.deltaTime<=.2f?Mathf.Clamp01(pat.speed/1.5f)*(1-Mathf.Clamp01((pat.speed-2.5f)/1.5f)):0;
        float rough=pat.active && pat.rough?1:cheek.Active?Mathf.Clamp01(cheek.Speed/4):0;
        ObserveEmotion();
        emotion.Step(Time.deltaTime,gentle,rough);
        string refusal=emotion.ConsumeCancelReason();if(!string.IsNullOrEmpty(refusal))Finish(true,refusal);
        gentleSeconds+=gentle*Mathf.Clamp(Time.deltaTime,0,.05f);
        cheek.Acceptance=emotion.Acceptance;cheek.Step(Time.deltaTime,Time.realtimeSinceStartupAsDouble);
        evaluatorAllocatedBytes=GC.GetAllocatedBytesForCurrentThread()-allocated;
        if(heldRegion=="head")strength=pat.strength;
        animations.SetBehaviorTarget(mouseTarget.Snapshot,attention.Output);
        animations.Tick(strength,heldRegion,hand,pat);
        UpdateMouse(Time.realtimeSinceStartupAsDouble);
        lookX=Mathf.Lerp(lookX,lookTx,1-Mathf.Exp(-8*Time.deltaTime));lookY=Mathf.Lerp(lookY,lookTy,1-Mathf.Exp(-8*Time.deltaTime));
        Vector3 gestureDelta=Vector3.zero;
        float ageGesture=Time.time-gestureAt;
        if(ageGesture>=0 && ageGesture<gestureDuration && contact==null && pat.strength<.01f) {
            float envelope=Mathf.Sin(ageGesture/gestureDuration*Mathf.PI);
            if(gesture=="nod")gestureDelta.x=-.14f*gestureAmount*Mathf.Sin(Mathf.SmoothStep(0,1,ageGesture/gestureDuration)*Mathf.PI)*Mathf.Rad2Deg;
            if(gesture=="glance")gestureDelta.y=-.08f*Mathf.Sin(ageGesture*2)*envelope*Mathf.Rad2Deg;
            if(gesture=="pat")gestureDelta=new Vector3(-.04f*envelope,0,.065f*envelope)*Mathf.Rad2Deg;
        }
        animations.ApplyDesktopPose(new EkuDesktopPose{attention=attention.Output,look=new Vector2(lookX,lookY),gesture=gestureDelta,headPat=pat,
            cheekDegrees=cheek.HeadDegrees,contactStrength=strength,contactAge=Time.time-contactAt,region=heldRegion,hand=hand});
        float dt=Mathf.Min(Time.deltaTime,.05f);
        moveLag=Vector2.Lerp(moveLag,moveTarget,1-Mathf.Exp(-12*dt));
        moveTarget*=Mathf.Exp(-7.67f*dt);
        MaxDragLag=Mathf.Max(MaxDragLag,DragLag);
        avatar.localPosition=avatarPosition+view.transform.right*moveLag.x+view.transform.up*moveLag.y;
        FlushFeedback();
    }
    public void Dispose() {
        Finish(true,"dispose");
        animations.Dispose();
        if(ownsCheekProfile)UnityEngine.Object.Destroy(cheekProfile);
        if(ownsInteractionProfile)UnityEngine.Object.Destroy(interactionProfile);
        control.Dispose();mapping.Dispose();
        foreach(var mesh in meshes) {
            if(mesh.collider) UnityEngine.Object.Destroy(mesh.collider.gameObject);
            if(mesh.mesh) UnityEngine.Object.Destroy(mesh.mesh);
        }
    }
}
