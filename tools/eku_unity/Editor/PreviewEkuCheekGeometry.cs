using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

// Static diagnostic renders only: no Player, expressions, bone motion, or saved scene changes.
public static class PreviewEkuCheekGeometry {
    [Serializable] class Capture {
        public string view,shape,file;
        public float weight,maximumWorldDelta,maximumPixelDelta;
        public int changedPixels;
        public Vector3 cameraPosition,cameraEuler;
    }
    [Serializable] class Report {
        public string scene,meshAsset,meshSha256;
        public string description="Fixed bones, neutral face, one cheek shape at a time. Each capture renders its CPU-baked static mesh to avoid same-frame skinned-renderer reuse. Saved-scene lighting, no post-processing. 100% is diagnostic, not a proposed default; projection displacement is not an occlusion/visibility measurement.";
        public int resolution=512;
        public float[] weights;
        public bool complete;
        public Capture[] captures;
    }
    static string Arg(string name,string fallback="") {
        var args=Environment.GetCommandLineArgs();int i=Array.IndexOf(args,name);
        return i>=0 && i+1<args.Length?args[i+1]:fallback;
    }
    static void Require(bool condition,string message) {if(!condition)throw new InvalidOperationException(message);}
    public static void Run() {
        string scene=Arg("--cheek-scene","Assets/EkuWardrobe.unity");
        string meshPath=Arg("--cheek-preview-mesh","Assets/Derived/EkuBodyCheekCandidate.asset");
        string folder=Arg("--cheek-preview-output");
        var weights=Arg("--cheek-preview-weights","35,100").Split(',')
            .Select(value=>float.Parse(value,CultureInfo.InvariantCulture)).Distinct().ToArray();
        Require(weights.Length>0 && weights.All(value=>!float.IsNaN(value) && value>0 && value<=100),"Preview weights must be in (0,100]");
        Require(!string.IsNullOrWhiteSpace(folder),"Explicit --cheek-preview-output required");
        folder=Path.GetFullPath(folder);Require(!File.Exists(Path.Combine(folder,"report.json")),"Use a fresh preview output directory");
        EditorSceneManager.OpenScene(scene);
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        Require(comparison && comparison.avatar && comparison.view,"Saved comparison avatar/camera required");
        var skin=comparison.avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(s=>s.name=="Body");
        var original=skin.sharedMesh;var mesh=AssetDatabase.LoadAssetAtPath<Mesh>(meshPath);
        Require(mesh && mesh.vertexCount==original.vertexCount,"Matching derived Body mesh required");
        Require(EkuCheekFrame.TryCreate(comparison.avatar.GetComponent<Animator>(),out var frame),"Face anatomical frame required");
        string[] shapes=(from side in new[]{"L","R"} from direction in new[]{"press","pull","push","up","down"}
            select "cheek_"+direction+"_"+side).ToArray();
        foreach(string shape in shapes)Require(mesh.GetBlendShapeIndex(shape)>=0,"Missing preview shape "+shape);
        var saved=new float[original.blendShapeCount];for(int i=0;i<saved.Length;i++)saved[i]=skin.GetBlendShapeWeight(i);
        bool originalUpdateOffscreen=skin.updateWhenOffscreen;
        bool originalEnabled=skin.enabled;
        var staticObject=new GameObject("Temporary CPU-baked cheek preview");staticObject.layer=skin.gameObject.layer;
        staticObject.transform.SetParent(skin.transform,false);
        var filter=staticObject.AddComponent<MeshFilter>();var renderer=staticObject.AddComponent<MeshRenderer>();
        renderer.sharedMaterials=skin.sharedMaterials;renderer.shadowCastingMode=skin.shadowCastingMode;
        renderer.receiveShadows=skin.receiveShadows;renderer.renderingLayerMask=skin.renderingLayerMask;
        var cameraObject=new GameObject("Temporary static cheek preview camera");var camera=cameraObject.AddComponent<Camera>();
        var target=new RenderTexture(512,512,24,RenderTextureFormat.ARGB32){antiAliasing=2};
        var texture=new Texture2D(512,512,TextureFormat.RGBA32,false);var baked=new Mesh();
        var active=RenderTexture.active;var captures=new List<Capture>();
        var report=new Report{scene=scene,meshAsset=meshPath,weights=weights};
        using(var hash=SHA256.Create())report.meshSha256=BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(meshPath))).Replace("-","").ToLowerInvariant();
        Directory.CreateDirectory(folder);
        try {
            skin.sharedMesh=mesh;skin.updateWhenOffscreen=true;
            skin.enabled=false;
            for(int i=0;i<mesh.blendShapeCount;i++)skin.SetBlendShapeWeight(i,0);
            skin.BakeMesh(baked);var baseline=baked.vertices;
            camera.enabled=false;camera.orthographic=true;camera.orthographicSize=frame.eyeSeparation*1.8f;
            camera.aspect=1;camera.nearClipPlane=.001f;camera.farClipPlane=frame.eyeSeparation*100;
            camera.cullingMask=comparison.view.cullingMask;camera.clearFlags=CameraClearFlags.SolidColor;
            camera.backgroundColor=new Color(.82f,.82f,.84f,1);camera.allowHDR=false;camera.allowMSAA=true;
            target.Create();camera.targetTexture=target;
            var center=frame.origin-frame.up*(frame.eyeSeparation*.25f)+frame.forward*(frame.eyeSeparation*.4f);
            foreach(float yaw in new[]{0,-45f,45f}) {
                string view=yaw==0?"front":yaw<0?"left45":"right45";
                camera.transform.position=center+Quaternion.AngleAxis(yaw,frame.up)*frame.forward*(frame.eyeSeparation*8);
                camera.transform.LookAt(center,frame.up);
                Color32[] neutralPixels=null;
                Action<string,float> capture=(shape,weight)=>{
                    int index=shape=="neutral"?-1:mesh.GetBlendShapeIndex(shape);
                    if(index>=0)skin.SetBlendShapeWeight(index,weight);
                    skin.BakeMesh(baked);var vertices=baked.vertices;float maximum=0,pixels=0;
                    for(int v=0;v<vertices.Length;v++) {
                        var before=skin.transform.TransformPoint(baseline[v]);var after=skin.transform.TransformPoint(vertices[v]);
                        maximum=Mathf.Max(maximum,Vector3.Distance(before,after));
                        var a=camera.WorldToViewportPoint(before);var b=camera.WorldToViewportPoint(after);
                        pixels=Mathf.Max(pixels,new Vector2(b.x-a.x,b.y-a.y).magnitude*512);
                    }
                    baked.UploadMeshData(false);filter.sharedMesh=baked;
                    camera.Render();RenderTexture.active=target;
                    texture.ReadPixels(new Rect(0,0,512,512),0,0);texture.Apply(false);
                    var pixelsNow=texture.GetPixels32();int changed=0;
                    if(shape=="neutral")neutralPixels=pixelsNow;
                    else for(int p=0;p<pixelsNow.Length;p++) {
                        var a=neutralPixels[p];var b=pixelsNow[p];
                        if(Math.Abs(a.r-b.r)>2 || Math.Abs(a.g-b.g)>2 || Math.Abs(a.b-b.b)>2)changed++;
                    }
                    string file=view+"-"+shape+"-"+weight.ToString("0.##",CultureInfo.InvariantCulture)+".png";
                    File.WriteAllBytes(Path.Combine(folder,file),texture.EncodeToPNG());
                    captures.Add(new Capture{view=view,shape=shape,weight=weight,file=file,maximumWorldDelta=maximum,maximumPixelDelta=pixels,changedPixels=changed,
                        cameraPosition=camera.transform.position,cameraEuler=camera.transform.eulerAngles});
                    if(index>=0)skin.SetBlendShapeWeight(index,0);
                };
                capture("neutral",0);
                foreach(string shape in shapes)foreach(float weight in weights)capture(shape,weight);
            }
            report.complete=true;
            Debug.Log("EKU_CHEEK_STATIC_PREVIEW captures="+captures.Count+" output="+folder);
        } finally {
            report.captures=captures.ToArray();File.WriteAllText(Path.Combine(folder,"report.json"),JsonUtility.ToJson(report,true));
            skin.sharedMesh=original;skin.updateWhenOffscreen=originalUpdateOffscreen;skin.enabled=originalEnabled;
            for(int i=0;i<saved.Length;i++)skin.SetBlendShapeWeight(i,saved[i]);
            RenderTexture.active=active;camera.targetTexture=null;target.Release();
            UnityEngine.Object.DestroyImmediate(baked);UnityEngine.Object.DestroyImmediate(texture);
            UnityEngine.Object.DestroyImmediate(target);UnityEngine.Object.DestroyImmediate(cameraObject);
            UnityEngine.Object.DestroyImmediate(staticObject);
        }
    }
}
