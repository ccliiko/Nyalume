using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using VRM;

public static class BuildEkuAnimations {
    [Serializable] class VertexDump {public Vector3[] source,target;public Vector2[] sourceUV,targetUV;}
    const string Root="Assets/sep-neko-ya/Eku/";
    public static EkuAnimationLibrary Build(GameObject avatar) {
        var source=UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(Root+"FBX/Eku.fbx"));
        if(!source) throw new Exception("Eku source FBX is missing");
        try {
            var library=ScriptableObject.CreateInstance<EkuAnimationLibrary>();
            var clips=new List<EkuAnimationClip>();var report=new List<string>();
            foreach(var file in Directory.GetFiles(Root+"Animation","*.anim",SearchOption.AllDirectories).OrderBy(p=>p)) {
                string path=file.Replace('\\','/');
                var clip=AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                string group=path.Substring((Root+"Animation/").Length).Split('/')[0];
                var curves=new List<EkuAnimationCurve>();int ignored=0;
                foreach(var binding in AnimationUtility.GetCurveBindings(clip)) {
                    var curve=AnimationUtility.GetEditorCurve(clip,binding);
                    int muscle=Array.FindIndex(HumanTrait.MuscleName,n=>n==binding.propertyName || FingerProperty(n)==binding.propertyName);
                    bool face=binding.propertyName.StartsWith("blendShape.") && binding.path=="Body";
                    bool root=binding.type==typeof(Animator) && (binding.propertyName.StartsWith("RootT.") || binding.propertyName.StartsWith("RootQ."));
                    bool bone=(group=="Kemo Ear" || group=="Kemo Tail") && RotationProperty(binding.propertyName);
                    if(!(muscle>=0 || root || face || bone)) {ignored++;continue;}
                    if(face && !curve.keys.Any(k=>Mathf.Abs(k.value)>0.0001f)) continue;
                    var entry=new EkuAnimationCurve{path=binding.path,property=binding.propertyName,muscle=muscle,curve=curve};
                    if(bone) {
                        var node=source.transform.Find(binding.path);
                        if(!node) throw new Exception("Unresolved source bone: "+binding.path);
                        entry.parentRest=node.parent.rotation;entry.boneRest=node.rotation;
                    }
                    curves.Add(entry);
                }
                clips.Add(new EkuAnimationClip{name=clip.name,group=group,duration=clip.length,curves=curves.ToArray()});
                report.Add(group+"/"+clip.name+": "+curves.Count+" usable curves, "+ignored+" excluded property curves");
            }
            library.clips=clips.ToArray();
            RestoreFace(source,avatar,library,report);
            AssetDatabase.CreateAsset(library,AssetDatabase.GenerateUniqueAssetPath("Assets/EkuAnimations.asset"));
            File.WriteAllLines(Path.GetFullPath("../animation-bindings.txt"),report);
            return library;
        } finally {UnityEngine.Object.DestroyImmediate(source);}
    }

    static bool RotationProperty(string property) => property.StartsWith("m_LocalRotation.") || property.StartsWith("localEulerAngles") || property.StartsWith("m_LocalEulerAngles.");

    // Repair only the five ear clips in an existing, approved scene's library.
    public static void RefreshEars(EkuAnimationLibrary library) {
        var source=UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(Root+"FBX/Eku.fbx"));
        if(!source) throw new Exception("Eku source FBX is missing");
        try {
            var report=new List<string>();
            foreach(var entry in library.clips.Where(c=>c.group=="Kemo Ear")) {
                var clip=AssetDatabase.LoadAssetAtPath<AnimationClip>(Root+"Animation/Kemo Ear/"+entry.name+".anim");
                if(!clip) throw new Exception("Missing ear clip: "+entry.name);
                var curves=new List<EkuAnimationCurve>();
                foreach(var binding in AnimationUtility.GetCurveBindings(clip).Where(b=>b.type==typeof(Transform) && RotationProperty(b.propertyName))) {
                    var node=source.transform.Find(binding.path);
                    if(!node) throw new Exception("Unresolved ear bone: "+binding.path);
                    curves.Add(new EkuAnimationCurve{path=binding.path,property=binding.propertyName,
                        curve=AnimationUtility.GetEditorCurve(clip,binding),parentRest=node.parent.rotation,boneRest=node.rotation});
                }
                foreach(string name in new[]{"Ear_Root.L","Ear.L","Ear.L.001","Ear_Root.R","Ear.R","Ear.R.001"}) {
                    var bound=curves.Where(c=>c.path.Split('/').Last()==name).ToArray();
                    if(bound.Length<3) throw new Exception("Incomplete ear rotation: "+entry.name+"/"+name);
                    report.Add(entry.name+"/"+name+": "+string.Join(", ",bound.Select(c=>c.property)));
                }
                entry.curves=curves.ToArray();
            }
            EditorUtility.SetDirty(library);AssetDatabase.SaveAssetIfDirty(library);
            File.WriteAllLines(Path.GetFullPath("../ear-bindings.txt"),report);
        } finally {UnityEngine.Object.DestroyImmediate(source);}
    }

    static string FingerProperty(string name) {
        foreach(string side in new[]{"Left","Right"}) foreach(string finger in new[]{"Thumb","Index","Middle","Ring","Little"}) {
            string prefix=side+" "+finger+" ";
            if(name.StartsWith(prefix)) return side+"Hand."+finger+"."+name.Substring(prefix.Length);
        }
        return name;
    }

    static void RestoreFace(GameObject source,GameObject avatar,EkuAnimationLibrary library,List<string> report) {
        // Normalize a disposable FBX copy exactly as UniVRM does before exporting.
        VRMBoneNormalizer.Execute(source,true,false);
        var src=source.GetComponentsInChildren<SkinnedMeshRenderer>().Single(s=>s.name=="Body");
        var dst=avatar.GetComponentsInChildren<SkinnedMeshRenderer>().Single(s=>s.name=="Body");
        var mesh=UnityEngine.Object.Instantiate(dst.sharedMesh);mesh.name="Eku complete face";
        var sv=src.sharedMesh.vertices;var su=src.sharedMesh.uv;var dv=mesh.vertices;var du=mesh.uv;
        var toTarget=dst.transform.worldToLocalMatrix*src.transform.localToWorldMatrix;
        // UV seams can duplicate vertices. Position + UV establishes the same surface point.
        var points=new Dictionary<string,List<int>>();
        Func<Vector3,Vector2,string> key=(p,u)=>$"{Mathf.RoundToInt(p.x*10000)},{Mathf.RoundToInt(p.y*10000)},{Mathf.RoundToInt(p.z*10000)}/{Mathf.RoundToInt(u.x*10000)},{Mathf.RoundToInt(u.y*10000)}";
        for(int i=0;i<sv.Length;i++) {
            string k=key(toTarget.MultiplyPoint3x4(sv[i]),su[i]);
            if(!points.TryGetValue(k,out var indices)) points[k]=indices=new List<int>();
            indices.Add(i);
        }
        var map=new int[dv.Length];int missing=0;
        bool sameTopology=sv.Length==dv.Length && src.sharedMesh.triangles.SequenceEqual(mesh.triangles)
            && su.Length==du.Length && su.Select((uv,i)=>(uv-du[i]).sqrMagnitude<=1e-10f).All(v=>v);
        report.Add("Identical indexed triangle topology and UVs: "+sameTopology);
        for(int i=0;i<dv.Length;i++) {
            // The existing VRM has baked facial adjustments. Identical topology and
            // per-index UVs prove correspondence even where its neutral shape differs.
            if(sameTopology) {map[i]=i;continue;}
            if(!points.TryGetValue(key(dv[i],du[i]),out var indices)) {
                // Independent FBX/VRM imports differ by a few floating point ULPs.
                // A bounded position/UV match repairs rounding-cell boundaries only.
                map[i]=-1;float distance=1e-8f;
                for(int j=0;j<sv.Length;j++) if((su[j]-du[i]).sqrMagnitude<=1e-10f) {
                    float error=(toTarget.MultiplyPoint3x4(sv[j])-dv[i]).sqrMagnitude;
                    if(error<distance) {distance=error;map[i]=j;}
                }
                if(map[i]<0) missing++;
            }
            else map[i]=indices[0];
        }
        report.Add($"Face vertex mapping: source={sv.Length}, target={dv.Length}, missing={missing}");
        if(missing!=0) {
            File.WriteAllText(Path.GetFullPath("../face-mapping-debug.json"),JsonUtility.ToJson(new VertexDump{source=sv.Select(v=>toTarget.MultiplyPoint3x4(v)).ToArray(),target=dv,sourceUV=su,targetUV=du}));
            File.WriteAllLines(Path.GetFullPath("../animation-bindings.txt"),report);
            throw new Exception("Eku face mapping incomplete: "+missing+" vertices; inspect animation-bindings.txt");
        }
        var needed=new HashSet<string>(library.clips.SelectMany(c=>c.curves)
            .Where(c=>c.property.StartsWith("blendShape.")).Select(c=>c.property.Substring(11)));
        // Runtime facial layers also use shapes not referenced by an animation clip.
        var runtime=new HashSet<string>(new[]{"まばたき","eye_relax","eye_angry","eye_surprised","mouth_smile_1",
            "mouth_o_small","mouth_ω","eyebrow_down","eyebrow_tare","eyebrow_up","eyebrow_happy","eyebrow_angry","extra_shy_2"});
        var comparison=avatar.GetComponent<EkuComparison>();
        var profile=comparison?comparison.cheekProfile:null;
        if(!profile)profile=AssetDatabase.LoadAssetAtPath<CheekDeformationProfile>("Assets/EkuCheekDefault.asset");
        if(profile && profile.bindings!=null)foreach(var binding in profile.bindings)
            if(binding!=null && !string.IsNullOrWhiteSpace(binding.shape))runtime.Add(binding.shape);
        foreach(string name in runtime) {
            if(mesh.GetBlendShapeIndex(name)>=0 || src.sharedMesh.GetBlendShapeIndex(name)>=0)needed.Add(name);
            else report.Add("Optional runtime face shape unavailable: "+name+" (derive cheek geometry separately)");
        }
        foreach(string name in needed) {
            if(mesh.GetBlendShapeIndex(name)>=0) continue;
            int index=src.sharedMesh.GetBlendShapeIndex(name);
            if(index<0) throw new Exception("Missing source face shape: "+name);
            var delta=new Vector3[sv.Length];var normal=new Vector3[sv.Length];var tangent=new Vector3[sv.Length];
            src.sharedMesh.GetBlendShapeFrameVertices(index,0,delta,normal,tangent);
            var targetDelta=new Vector3[dv.Length];var targetNormal=new Vector3[dv.Length];var targetTangent=new Vector3[dv.Length];
            for(int i=0;i<dv.Length;i++) {
                targetDelta[i]=toTarget.MultiplyVector(delta[map[i]]);
                targetNormal[i]=toTarget.MultiplyVector(normal[map[i]]);
                targetTangent[i]=toTarget.MultiplyVector(tangent[map[i]]);
            }
            mesh.AddBlendShapeFrame(name,100,targetDelta,targetNormal,targetTangent);
        }
        string output=AssetDatabase.GenerateUniqueAssetPath("Assets/EkuCompleteFace.asset");
        AssetDatabase.CreateAsset(mesh,output);dst.sharedMesh=mesh;
        report.Add($"Face restored: {mesh.blendShapeCount} shapes, {needed.Count} animation/runtime bindings verified");
    }
}
