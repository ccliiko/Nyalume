using System;
using System.Collections.Generic;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Linq;
using UnityEngine;
using UnityEngine.Rendering;
using VRM;

[Serializable] public class EkuBone { public string name; public float[] rotation; public float[] position; }
[Serializable] public class EkuExpression { public string name; public float weight; }
[Serializable] public class EkuMotionFrame { public EkuBone[] bones; public EkuExpression[] expressions; }
[Serializable] public class EkuMotion { public EkuMotionFrame[] frames; }
[Serializable] public class EkuPose {
    public float height; public float[] modelPosition; public float[] camera;
    public float fov; public EkuBone[] bones; public int width; public int heightPx;
}

// Local comparison harness. The shipping desktop still uses Three.js.
public class EkuComparison : MonoBehaviour {
    public Transform avatar;
    public Camera view;
    public TextAsset poseFile;
    public TextAsset motionFile;
    public EkuAnimationLibrary animations;
    public EkuHeadPatSettings headPat=new EkuHeadPatSettings();
    public EkuEmotionSettings emotion=new EkuEmotionSettings();
    public CheekDeformationProfile cheekProfile;
    public EkuFacialSettings facial=new EkuFacialSettings();
    public EkuInteractionProfile interactionProfile;
    public EkuAttentionSettings attention=new EkuAttentionSettings();
    public EkuBehaviorSettings behavior=new EkuBehaviorSettings();
    public EkuReachSettings reach=new EkuReachSettings();
    EkuPose pose;
    EkuMotion motion;
    EkuDesktop desktop;
    public EkuInteractionRuntime Interactions => desktop?.Interactions;
    public bool IsResting => desktop!=null && desktop.Resting;
    EkuMagnifier magnifier;
    Dictionary<string,Transform> poseBones;
    Transform[] bones;
    Quaternion[] rest;
    Animator rig;
    VRMBlendShapeProxy expressions;
    RenderTexture target, supersampled;
    EkuColorGrade colorGrade;
    int renderScale;
    VRMSpringBone[] springs;
    Transform tailRoot;
    Quaternion tailRest;
    Transform visibleTail;
    Quaternion visibleTailRest;
    float tailMotionDegrees;
    int maxWidth,maxHeight;
    MemoryMappedFile mapping;
    MemoryMappedViewAccessor shared;
    int pending;
    long sequence, published;
    int ack, command, frame;
    double actionAt = -100;
    string output;
    bool capture, dynamicCapture;
    readonly List<float> intervals = new List<float>();

    static string Arg(string name, string fallback="") {
        var args=Environment.GetCommandLineArgs();
        var index=Array.IndexOf(args,name);
        return index>=0 && index+1<args.Length ? args[index+1] : fallback;
    }
    void Start() {
        Application.runInBackground=true;
        QualitySettings.vSyncCount=0; Application.targetFrameRate=60;
        var posePath=Arg("--pose");
        pose=JsonUtility.FromJson<EkuPose>(posePath!=""?File.ReadAllText(posePath):poseFile.text);
        output=Arg("--output",Path.GetFullPath(Path.Combine(Application.dataPath,"../captures")));
        Directory.CreateDirectory(output); dynamicCapture=Arg("--capture","0")=="2";
        capture=Arg("--capture","0")=="1" || dynamicCapture;
        if(dynamicCapture) motion=JsonUtility.FromJson<EkuMotion>(motionFile.text);
        springs=avatar.GetComponentsInChildren<VRMSpringBone>();
        if(capture) foreach(var spring in springs) spring.enabled=false;
        rig=avatar.GetComponent<Animator>(); if(rig) rig.enabled=false;
        var wardrobe=avatar.GetComponent<EkuWardrobe>();
        if(wardrobe) {
            string saved=Arg("--wardrobe-state");
            wardrobe.Apply(saved!=""?JsonUtility.FromJson<EkuWardrobeState>(saved):new EkuWardrobeState());
        }
        // Three PropertyBinding strips dots/brackets/slashes from glTF bone names.
        var all=avatar.GetComponentsInChildren<Transform>(true).GroupBy(t=>
            t.name.Replace(".","").Replace(":","").Replace("/","").Replace("[","").Replace("]","").Replace(" ","_"))
            .ToDictionary(g=>g.Key,g=>g.First());
        poseBones=all;
        int matched=0;
        foreach(var b in pose.bones) {
            if(!all.TryGetValue(b.name,out var bone)) continue;
            matched++;
            // UniGLTF reflects glTF Z. Pose is exported from the exact Three VRM rig.
            bone.localRotation=new Quaternion(-b.rotation[0],-b.rotation[1],b.rotation[2],b.rotation[3]);
        }
        avatar.localPosition=new Vector3(pose.modelPosition[0],pose.modelPosition[1],-pose.modelPosition[2])/10;
        avatar.localRotation=Quaternion.Euler(0,180,0);
        tailRoot=avatar.GetComponentsInChildren<Transform>().FirstOrDefault(b=>b.name=="Tail_Root");
        if(tailRoot) tailRest=tailRoot.localRotation;
        visibleTail=avatar.GetComponentsInChildren<Transform>().FirstOrDefault(b=>b.name=="Tail_2");
        if(visibleTail) visibleTailRest=visibleTail.localRotation;
        foreach(var spring in springs) {
            // Eku's spring points to an unweighted duplicate tail in the VRM.
            for(int i=0;i<spring.RootBones.Count;i++)
                if(visibleTail && spring.RootBones[i]?.name=="Tail 1") spring.RootBones[i]=visibleTail;
            spring.m_updateType=VRMSpringBone.SpringBoneUpdateType.Manual;
            var headCenter=rig.GetBoneTransform(HumanBodyBones.Head);
            if(wardrobe && spring.RootBones.Count>0 && spring.RootBones.All(b=>b && (b==headCenter || b.IsChildOf(headCenter))))
                spring.m_center=headCenter;
            spring.Setup(true);
        }
        File.WriteAllText(Path.Combine(output,"unity-pose-mapping.json"),
            "{\"matched\":"+matched+",\"exported\":"+pose.bones.Length+"}");
        bones=Enum.GetValues(typeof(HumanBodyBones)).Cast<HumanBodyBones>()
            .Where(b=>b!=HumanBodyBones.LastBone).Select(b=>rig.GetBoneTransform(b))
            .Where(b=>b!=null).Distinct().ToArray();
        rest=bones.Select(b=>b.localRotation).ToArray();
        expressions=avatar.GetComponent<VRMBlendShapeProxy>();
        foreach(var look in avatar.GetComponentsInChildren<VRMLookAtHead>()) look.enabled=false;
        view.transform.position=new Vector3(pose.camera[0],pose.camera[1],-pose.camera[2])/10;
        view.transform.LookAt(Vector3.zero); view.fieldOfView=pose.fov;
        view.aspect=(float)pose.width/pose.heightPx; view.nearClipPlane=.01f;view.farClipPlane=400;
        view.clearFlags=CameraClearFlags.SolidColor;view.backgroundColor=Color.clear;
        view.allowHDR=false;view.allowMSAA=true;view.enabled=false;
        int detail=capture && Arg("--detail","0")=="1"?3:1;
        int width=int.Parse(Arg("--width",(pose.width*detail).ToString()));
        int height=int.Parse(Arg("--height",(pose.heightPx*detail).ToString()));
        maxWidth=int.Parse(Arg("--max-width",width.ToString()));maxHeight=int.Parse(Arg("--max-height",height.ToString()));
        colorGrade=view.GetComponent<EkuColorGrade>();
        renderScale=Mathf.Clamp(int.Parse(Arg("--render-scale",capture?"1":"2")),1,2);
        Resize(width,height);
        if(Arg("--control")!="") {
            desktop=new EkuDesktop(avatar,rig,view,Arg("--control"),pose.height/10,animations,headPat,emotion,cheekProfile,attention,interactionProfile,behavior,reach,facial);
        }
        var mapName=Arg("--bridge");
        if(desktop!=null && Arg("--magnifier")!="") magnifier=new EkuMagnifier(view,desktop,Arg("--magnifier"));
        if(mapName!="") {
            mapping=MemoryMappedFile.CreateOrOpen(mapName,64L+maxWidth*maxHeight*4);
            shared=mapping.CreateViewAccessor();
            shared.Write(0,0x45554B31); shared.Write(4,pose.width);shared.Write(8,pose.heightPx);
        }
    }
    void LateUpdate() {
        frame++; if(capture) intervals.Add(Time.unscaledDeltaTime*1000);
        if(desktop==null) for(int i=0;i<bones.Length;i++) bones[i].localRotation=rest[i];
        if(dynamicCapture && (frame==60 || frame==130 || frame==210)) {
            command=frame==60?2:frame==130?4:3;actionAt=Time.realtimeSinceStartupAsDouble;
        }
        if(shared!=null) {
            int id=shared.ReadInt32(40);
            if(id!=ack) { ack=id;command=shared.ReadInt32(44);actionAt=Time.realtimeSinceStartupAsDouble; }
        }
        double age=Time.realtimeSinceStartupAsDouble-actionAt;
        var head=rig.GetBoneTransform(HumanBodyBones.Head);
        var chest=rig.GetBoneTransform(HumanBodyBones.Chest);
        float t=22+frame/60f;
        if(chest && !capture && desktop==null) chest.Rotate(Mathf.Sin(t*1.3f)*.006f*Mathf.Rad2Deg,0,0,Space.Self);
        if(head && age<2.4) {
            float envelope=Mathf.Sin((float)age/2.4f*Mathf.PI);
            if(command==1) head.Rotate(-Mathf.Sin((float)age*8)*9*envelope,0,0,Space.Self);
            if(command==4) head.Rotate(0,-Mathf.Sin((float)age*2)*.08f*Mathf.Rad2Deg*envelope,0,Space.Self);
            if(command==3) {
                var elbow=rig.GetBoneTransform(HumanBodyBones.RightLowerArm);
                elbow?.Rotate(0,-.22f*Mathf.Rad2Deg*envelope,0,Space.Self);
            }
        }
        if(expressions && desktop==null) {
            float blink=Mathf.Max(0,1-Mathf.Abs((t%4.7f)-4.5f)/.12f);
            if(command==2 && age<.25) blink=1;
            expressions.ImmediatelySetValue(BlendShapeKey.CreateFromPreset(BlendShapePreset.Blink),blink);
        }
        if(capture && !dynamicCapture) {
            float yaw=frame<=120?0:frame<=240?90:180;
            avatar.localRotation=Quaternion.Euler(0,180-yaw,0);
        }
        // Dynamic comparison replays exported poses, including finger and hair rotations.
        if(dynamicCapture && frame%6==0) {
            var exported=motion.frames[frame/6-1];
            foreach(var b in exported.bones) if(poseBones.TryGetValue(b.name,out var bone))
                bone.localRotation=new Quaternion(-b.rotation[0],-b.rotation[1],b.rotation[2],b.rotation[3]);
            foreach(var e in exported.expressions) {
                string preset=e.name=="happy"?"Joy":e.name=="relaxed"?"Fun":e.name=="sad"?"Sorrow":
                    e.name=="aa"?"A":e.name=="ih"?"I":e.name=="ou"?"U":e.name=="ee"?"E":e.name=="oh"?"O":e.name;
                if(Enum.TryParse(preset,true,out BlendShapePreset value))
                    expressions?.ImmediatelySetValue(BlendShapeKey.CreateFromPreset(value),e.weight);
            }
        }
        desktop?.Tick();
        if(desktop!=null) {
            if(desktop.RenderWidth>0 && desktop.RenderHeight>0) Resize(desktop.RenderWidth,desktop.RenderHeight);
        }
        // Pose -> spring simulation -> render, all in the same frame.
        if(!capture) foreach(var spring in springs) if(spring.enabled) spring.ManualUpdate(Mathf.Min(Time.deltaTime,1f/30));
        desktop?.ApplyEarPhysics();
        avatar.GetComponent<EkuWardrobe>()?.uniformSurface?.Constrain();
        if(visibleTail) tailMotionDegrees=Mathf.Max(tailMotionDegrees,Quaternion.Angle(visibleTailRest,visibleTail.localRotation));
        if(desktop!=null && frame==120) SavePhysicsStats();
        // Keep display pixels for input and magnifier math; only the camera render is larger.
        if(supersampled) {
            var previousTarget=RenderTexture.active;
            view.targetTexture=supersampled;
            try {
                view.Render();
                if(colorGrade) colorGrade.Downsample(supersampled,target);
                else Graphics.Blit(supersampled,target);
            } finally {view.targetTexture=target;RenderTexture.active=previousTarget;}
        } else view.Render();
        magnifier?.Tick();
        if(dynamicCapture && frame%6==0) SavePng("unity-motion-"+(frame/6).ToString("D3")+".png");
        if(capture && !dynamicCapture && (frame==60 || frame==180 || frame==300)) {
            string angle=frame==60?"front":frame==180?"side":"back";
            SavePng("unity-liltoon-"+angle+".png");
        }
        if(shared!=null && pending<3) {
            pending++;long current=++sequence;
            double stamp=Time.realtimeSinceStartupAsDouble;
            int frameAck=ack;
            int frameWidth=target.width,frameHeight=target.height;
            AsyncGPUReadback.Request(target,0,TextureFormat.BGRA32,request=>{
                try {
                    if(request.hasError || shared==null || current<=published) return;
                    var data=request.GetData<byte>().ToArray();
                    // Seqlock: consumer ignores in-progress or superseded frames.
                    shared.Write(16,current*2-1);
                    shared.Write(4,frameWidth);shared.Write(8,frameHeight);
                    shared.WriteArray(64,data,0,data.Length);
                    shared.Write(24,stamp);shared.Write(48,frameAck);
                    shared.Write(16,current*2);
                    published=current;
                } finally {pending--;}
            });
        }
        if(capture && frame>=360) {
            File.WriteAllText(Path.Combine(output,"unity-render.json"),
                JsonUtility.ToJson(new Stats{unity=Application.unityVersion,gpu=SystemInfo.graphicsDeviceName,
                    readback=SystemInfo.supportsAsyncGPUReadback,frames=frame,meanFrameMs=intervals.Skip(60).Average()},true));
            Application.Quit();
        }
    }
    [Serializable] class Stats {public string unity,gpu;public bool readback;public int frames;public float meanFrameMs;}
    [Serializable] class PhysicsStats {public float tailMotionDegrees,dragLag,maxDragLag;public int dragActions;public string[] springRoots;}
    void SavePhysicsStats() {
        File.WriteAllText(Path.Combine(output,"unity-desktop-physics.json"),
            JsonUtility.ToJson(new PhysicsStats{tailMotionDegrees=tailMotionDegrees,
                dragLag=desktop.DragLag,maxDragLag=desktop.MaxDragLag,dragActions=desktop.DragActions,
                springRoots=springs.SelectMany(s=>s.RootBones).Where(b=>b!=null).Select(b=>b.name).ToArray()},true));
    }
    void Resize(int width,int height) {
        width=Mathf.Clamp(width,1,maxWidth);height=Mathf.Clamp(height,1,maxHeight);
        if(target && target.width==width && target.height==height) return;
        // Complete the three bounded readbacks before releasing their texture.
        if(target) {AsyncGPUReadback.WaitAllRequests();target.Release();Destroy(target);}
        if(supersampled) {supersampled.Release();Destroy(supersampled);}
        target=new RenderTexture(width,height,24,RenderTextureFormat.ARGB32){antiAliasing=2};
        target.Create();view.targetTexture=target;view.aspect=(float)width/height;
        if(renderScale>1) {
            supersampled=new RenderTexture(width*renderScale,height*renderScale,24,RenderTextureFormat.ARGB32){antiAliasing=2};
            supersampled.Create();
        }
        Debug.Log($"EKU_RENDER_SIZE display={width}x{height} render={width*renderScale}x{height*renderScale}");
    }
    void SavePng(string name) {
        var previous=RenderTexture.active;RenderTexture.active=target;
        var image=new Texture2D(target.width,target.height,TextureFormat.RGBA32,false);
        image.ReadPixels(new Rect(0,0,target.width,target.height),0,0);image.Apply();
        File.WriteAllBytes(Path.Combine(output,name),image.EncodeToPNG());
        Destroy(image);RenderTexture.active=previous;
    }
    void OnDestroy() {
        if(desktop!=null) SavePhysicsStats();
        AsyncGPUReadback.WaitAllRequests();magnifier?.Dispose();desktop?.Dispose();shared?.Dispose();mapping?.Dispose();
        if(target) {target.Release();Destroy(target);}
        if(supersampled) {supersampled.Release();Destroy(supersampled);}
    }
}

// Cropped camera projection preserves the main view's angle, materials and grading.
public sealed class EkuMagnifier : IDisposable {
    const int Size=256;
    readonly Camera source,lens;
    readonly EkuDesktop desktop;
    readonly EkuColorGrade grade;
    readonly RenderTexture raw,output;
    readonly MemoryMappedFile mapping;
    readonly MemoryMappedViewAccessor shared;
    Vector2 center,velocity,lastSelection;
    bool wasEnabled,pending;
    long sequence;
    double nextFrame;
    public EkuMagnifier(Camera source,EkuDesktop desktop,string map) {
        this.source=source;this.desktop=desktop;grade=source.GetComponent<EkuColorGrade>();
        lens=new GameObject("Desktop magnifier camera").AddComponent<Camera>();lens.enabled=false;
        raw=new RenderTexture(Size,Size,24,RenderTextureFormat.ARGB32){antiAliasing=2};raw.Create();
        output=new RenderTexture(Size,Size,0,RenderTextureFormat.ARGB32);output.Create();
        mapping=MemoryMappedFile.CreateOrOpen(map,64+Size*Size*4);shared=mapping.CreateViewAccessor();
    }
    public void Tick() {
        bool enabled=desktop.MagnifierVisible;
        if(!enabled) {if(!desktop.MagnifierEnabled) wasEnabled=false;return;}
        var selection=desktop.MagnifierSelection;
        if(!wasEnabled || selection!=lastSelection) {center=selection;velocity=Vector2.zero;}
        lastSelection=selection;wasEnabled=true;
        var span=new Vector2(Size/(source.targetTexture.width*3f),Size/(source.targetTexture.height*3f));
        var tracked=center;
        if(!desktop.MagnifierMoving && desktop.MagnifierAnchor) {
            var point=source.WorldToViewportPoint(desktop.MagnifierAnchor.TransformPoint(desktop.MagnifierLocalPoint));
            if(point.z<=0) return;
            tracked=new Vector2(point.x,1-point.y);
            var delta=tracked-center;
            var goal=center+new Vector2(Mathf.Sign(delta.x)*Mathf.Max(0,Mathf.Abs(delta.x)-span.x*.3f),
                                       Mathf.Sign(delta.y)*Mathf.Max(0,Mathf.Abs(delta.y)-span.y*.3f));
            if(goal==center) velocity=Vector2.zero;
            else center=Vector2.SmoothDamp(center,goal,ref velocity,.2f,Mathf.Infinity,Mathf.Min(Time.unscaledDeltaTime,.05f));
        }
        double now=Time.realtimeSinceStartupAsDouble;
        if(pending || now<nextFrame) return;
        nextFrame=now+1.0/30;
        lens.CopyFrom(source);lens.enabled=false;lens.targetTexture=raw;lens.rect=new Rect(0,0,1,1);
        lens.transform.SetPositionAndRotation(source.transform.position,source.transform.rotation);
        var crop=Matrix4x4.identity;crop.m00=1/span.x;crop.m11=1/span.y;
        crop.m03=-(center.x*2-1)/span.x;crop.m13=-((1-center.y)*2-1)/span.y;
        var previousTarget=RenderTexture.active;
        lens.projectionMatrix=crop*source.projectionMatrix;lens.Render();
        if(grade) grade.Apply(raw,output);else Graphics.Blit(raw,output);
        RenderTexture.active=previousTarget;
        pending=true;long current=++sequence;var frameCenter=center;var frameSpan=span;var frameTracked=tracked;
        AsyncGPUReadback.Request(output,0,TextureFormat.BGRA32,request=>{
            try {
                if(request.hasError) return;
                var data=request.GetData<byte>().ToArray();shared.Write(16,current*2-1);
                shared.Write(4,Size);shared.Write(8,Size);shared.WriteArray(64,data,0,data.Length);
                shared.Write(32,frameCenter.x);shared.Write(36,frameCenter.y);
                shared.Write(40,frameSpan.x);shared.Write(44,frameSpan.y);
                shared.Write(48,frameTracked.x);shared.Write(52,frameTracked.y);
                shared.Write(16,current*2);
            } finally {pending=false;}
        });
    }
    public void Dispose() {
        shared.Dispose();mapping.Dispose();raw.Release();output.Release();
        UnityEngine.Object.Destroy(raw);UnityEngine.Object.Destroy(output);
        if(lens)UnityEngine.Object.Destroy(lens.gameObject);
    }
}
