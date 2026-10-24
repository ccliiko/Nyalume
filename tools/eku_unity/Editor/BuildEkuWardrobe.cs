using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.Build.Reporting;
using UnityEngine;
using VRM;

public static class BuildEkuWardrobe {
    const string Eku="Assets/sep-neko-ya/Eku/";
    const string School="Assets/sep-neko-ya/AsteriaLycee/";
    [Serializable] class ShapeDump {public string name;public Vector3[] delta;}
    [Serializable] class BodyDump {public Vector3[] source,target;public ShapeDump[] shapes;}
    const string Folder="Assets/EkuWardrobe";
    static string Key(string name) => new string(name.Where(char.IsLetterOrDigit).ToArray());
    static T Save<T>(T asset,string name) where T:UnityEngine.Object {
        string path=Folder+"/"+name;
        var old=AssetDatabase.LoadAssetAtPath<T>(path);
        if(old) {EditorUtility.CopySerialized(asset,old);UnityEngine.Object.DestroyImmediate(asset);return old;}
        AssetDatabase.CreateAsset(asset,path);return asset;
    }
    static Vector3[] Delta(SkinnedMeshRenderer src,SkinnedMeshRenderer dst,string shape) {
        var mesh=src.sharedMesh;int index=mesh.GetBlendShapeIndex(shape);
        if(index<0)throw new Exception("Missing clothing shape: "+shape);
        var values=new Vector3[mesh.vertexCount];mesh.GetBlendShapeFrameVertices(index,0,values,null,null);
        var bones=dst.transform.root.GetComponentsInChildren<Transform>(true).GroupBy(t=>Key(t.name)).ToDictionary(g=>g.Key,g=>g.First());
        var weights=mesh.boneWeights;
        var needed=new HashSet<int>();
        foreach(var w in weights) {
            if(w.weight0>0)needed.Add(w.boneIndex0);if(w.weight1>0)needed.Add(w.boneIndex1);
            if(w.weight2>0)needed.Add(w.boneIndex2);if(w.weight3>0)needed.Add(w.boneIndex3);
        }
        var matrices=src.bones.Select((bone,i)=>needed.Contains(i)?
            dst.transform.worldToLocalMatrix*bones[Key(bone.name)].localToWorldMatrix*mesh.bindposes[i]:Matrix4x4.identity).ToArray();
        for(int i=0;i<values.Length;i++) {
            var w=weights[i];var d=values[i];
            values[i]=matrices[w.boneIndex0].MultiplyVector(d)*w.weight0+matrices[w.boneIndex1].MultiplyVector(d)*w.weight1+
                matrices[w.boneIndex2].MultiplyVector(d)*w.weight2+matrices[w.boneIndex3].MultiplyVector(d)*w.weight3;
        }
        return values;
    }
    static Mesh Recover(SkinnedMeshRenderer source,SkinnedMeshRenderer target,Mesh original,bool socks) {
        var mesh=UnityEngine.Object.Instantiate(original);var positions=mesh.vertices;
        string[] shapes=socks?new[]{"Spine_1","Spine_2","Foot_heels"}:
            new[]{"Spine_1","Spine_2","Upper_leg_2","Knee","Lower_leg","Ankle","Foot","Toe","Foot_heels"};
        foreach(string name in shapes) {
            int index=original.GetBlendShapeIndex(name);
            // VRM retained zero-delta placeholders for the baked shrink shapes.
            // The source FBX carries the real displacement needed to reverse them.
            Vector3[] delta=Delta(source,target,name);
            for(int i=0;i<Math.Min(delta.Length,positions.Length);i++)positions[i]-=delta[i];
            if(name=="Foot_heels" && index<0) {
                var full=new Vector3[positions.Length];Array.Copy(delta,full,delta.Length);
                mesh.AddBlendShapeFrame(name,100,full,new Vector3[positions.Length],new Vector3[positions.Length]);
            }
        }
        mesh.vertices=positions;mesh.RecalculateNormals();mesh.RecalculateBounds();
        if(!socks)mesh.colors=new Color[mesh.vertexCount];
        return Save(mesh,socks?"CompleteSocks.asset":"CompleteBare.asset");
    }
    static SkinnedMeshRenderer ImportRenderer(SkinnedMeshRenderer source,Transform avatar,Dictionary<string,Transform> bones,Material material) {
        Func<Transform,Transform> bind=null;
        bind=node=>{
            if(bones.TryGetValue(Key(node.name),out var found))return found;
            if(!node.parent)return avatar;
            var parent=bind(node.parent);
            var created=new GameObject(node.name).transform;created.SetParent(parent,false);
            created.position=node.position;created.rotation=node.rotation;created.localScale=node.localScale;
            bones.Add(Key(node.name),created);return created;
        };
        var obj=new GameObject(source.name);obj.transform.SetParent(avatar,false);
        var dst=obj.AddComponent<SkinnedMeshRenderer>();
        var mesh=UnityEngine.Object.Instantiate(source.sharedMesh);
        var needed=new HashSet<int>();
        foreach(var w in mesh.boneWeights) {
            if(w.weight0>0)needed.Add(w.boneIndex0);if(w.weight1>0)needed.Add(w.boneIndex1);
            if(w.weight2>0)needed.Add(w.boneIndex2);if(w.weight3>0)needed.Add(w.boneIndex3);
        }
        dst.bones=source.bones.Select((bone,i)=>needed.Contains(i)?bind(bone):avatar).ToArray();dst.rootBone=bind(source.rootBone);
        // The school FBX has its own humanoid rest pose. Bind the visible
        // normalized garment to the current avatar's rest matrices instead of
        // carrying that different rest pose into the running animation rig.
        var space=dst.transform.worldToLocalMatrix*source.transform.localToWorldMatrix;
        mesh.vertices=mesh.vertices.Select(space.MultiplyPoint3x4).ToArray();
        mesh.normals=mesh.normals.Select(n=>space.MultiplyVector(n).normalized).ToArray();
        mesh.bindposes=dst.bones.Select(b=>b.worldToLocalMatrix*dst.transform.localToWorldMatrix).ToArray();
        mesh.RecalculateBounds();
        dst.sharedMesh=Save(mesh,source.name+".asset");
        dst.sharedMaterials=Enumerable.Repeat(material,dst.sharedMesh.subMeshCount).ToArray();
        dst.localBounds=new Bounds(new Vector3(0,.65f,0),new Vector3(2,2,2));dst.updateWhenOffscreen=true;
        return dst;
    }
    static EkuUniformCapsule Limb(Animator rig,HumanBodyBones start,HumanBodyBones end,float radius,float endRadius) {
        return new EkuUniformCapsule {start=rig.GetBoneTransform(start),end=rig.GetBoneTransform(end),radius=radius,endRadius=endRadius};
    }
    static EkuUniformCapsule[] UniformColliders(Animator rig) {
        return new[]{
            Limb(rig,HumanBodyBones.Hips,HumanBodyBones.Spine,.083f,.077f),
            Limb(rig,HumanBodyBones.Spine,HumanBodyBones.Chest,.077f,.082f),
            Limb(rig,HumanBodyBones.LeftUpperLeg,HumanBodyBones.LeftLowerLeg,.074f,.052f),
            Limb(rig,HumanBodyBones.RightUpperLeg,HumanBodyBones.RightLowerLeg,.074f,.052f),
            Limb(rig,HumanBodyBones.LeftLowerLeg,HumanBodyBones.LeftFoot,.052f,.029f),
            Limb(rig,HumanBodyBones.RightLowerLeg,HumanBodyBones.RightFoot,.052f,.029f),
            Limb(rig,HumanBodyBones.LeftUpperArm,HumanBodyBones.LeftLowerArm,.036f,.029f),
            Limb(rig,HumanBodyBones.RightUpperArm,HumanBodyBones.RightLowerArm,.036f,.029f),
            Limb(rig,HumanBodyBones.LeftLowerArm,HumanBodyBones.LeftHand,.029f,.022f),
            Limb(rig,HumanBodyBones.RightLowerArm,HumanBodyBones.RightHand,.029f,.022f),
            Limb(rig,HumanBodyBones.LeftHand,HumanBodyBones.LeftMiddleDistal,.027f,.021f),
            Limb(rig,HumanBodyBones.RightHand,HumanBodyBones.RightMiddleDistal,.027f,.021f)};
    }
    public static void Build() {
        EditorSceneManager.OpenScene("Assets/EkuCombinedComparison.unity");
        if(!AssetDatabase.IsValidFolder(Folder))AssetDatabase.CreateFolder("Assets","EkuWardrobe");
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();var avatar=comparison.avatar;
        var old=avatar.GetComponent<EkuWardrobe>();if(old)throw new Exception("Build from the frozen combined scene only");
        var wardrobe=avatar.gameObject.AddComponent<EkuWardrobe>();
        var current=avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).ToDictionary(s=>s.name);
        wardrobe.body=current["Body_base"];wardrobe.dressedBody=wardrobe.body.sharedMesh;
        wardrobe.jacket=current["Jacket"];wardrobe.dress=current["Sleeveless_Sweater"];
        wardrobe.spats=current["Spats"];wardrobe.socks=current["Knee_Socks"];wardrobe.shoes=current["Pumps"];
        wardrobe.stockings=avatar.GetComponent<EkuMaterialStyle>().stockings;
        var source=UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(Eku+"FBX/Eku.fbx"));
        VRMBoneNormalizer.Execute(source,true,false);
        var original=source.GetComponentsInChildren<SkinnedMeshRenderer>(true).ToDictionary(s=>s.name);
        var baseline=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Eku.prefab").GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(s=>s.name=="Body_base").sharedMesh;
        if(original["Body_base"].sharedMesh.vertexCount!=baseline.vertexCount ||
            !original["Body_base"].sharedMesh.uv.Select((uv,i)=>(uv-baseline.uv[i]).sqrMagnitude<1e-10f).All(v=>v))
            throw new Exception("Body topology/UV correspondence changed");
        wardrobe.completeBody=Recover(original["Body_base"],wardrobe.body,baseline,false);
        wardrobe.completeSockBody=Recover(original["Body_base"],wardrobe.body,wardrobe.dressedBody,true);
        wardrobe.bareBody=BodyRenderer(wardrobe.body,wardrobe.completeBody,"Body_base_bare");
        wardrobe.sockBody=BodyRenderer(wardrobe.body,wardrobe.completeSockBody,"Body_base_complete_sock");
        var bones=avatar.GetComponentsInChildren<Transform>(true).GroupBy(t=>Key(t.name)).ToDictionary(g=>g.Key,g=>g.First());
        var underwearMaterial=Save(new Material(AssetDatabase.LoadAssetAtPath<Material>(Eku+"Materials/Default/E_Costume.mat")),"Underwear.mat");
        wardrobe.bra=ImportRenderer(original["Bra"],avatar,bones,underwearMaterial);
        wardrobe.underwear=ImportRenderer(original["Underwear"],avatar,bones,underwearMaterial);
        var bra=(SkinnedMeshRenderer)wardrobe.bra;int breast=bra.sharedMesh.GetBlendShapeIndex("Breast_inside");
        if(breast>=0)bra.SetBlendShapeWeight(breast,100);
        var socks=(SkinnedMeshRenderer)wardrobe.socks;
        var heels=Delta(original["Knee_Socks"],socks,"Foot_heels");
        socks.sharedMesh=Save(BuildEkuSockFoot.Restore(socks.sharedMesh,heels),"Stockings.asset");
        BuildEkuSockFoot.BakeMaps(socks.sharedMesh,wardrobe.completeBody,
            (Texture2D)wardrobe.body.sharedMaterial.GetTexture("_MainTex"),wardrobe.stockings);
        var school=UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(School+"Prefabs/Eku/FBX/AsteriaLycee_Eku.fbx"));
        VRMBoneNormalizer.Execute(school,true,false);
        var material=Save(new Material(AssetDatabase.LoadAssetAtPath<Material>(School+"Material/M_AsteriaLycee_Eku.mat")),"Uniform.mat");
        if(!material.GetTexture("_MainTex") || material.shader.name=="Hidden/InternalErrorShader")throw new Exception("Uniform material is unresolved");
        var imported=school.GetComponentsInChildren<SkinnedMeshRenderer>(true).ToDictionary(s=>s.name,s=>ImportRenderer(s,avatar,bones,material));
        wardrobe.uniformJacket=imported["AsteriaLycee_Jacket"];wardrobe.blouse=imported["AsteriaLycee_Blouse"];
        wardrobe.blouseArm=imported["AsteriaLycee_Blouse_Arm"];wardrobe.uniformInner=imported["AsteriaLycee_Inner"];
        wardrobe.boots=imported["AsteriaLycee_Boots"];
        var roots=bones.Values.Where(t=>t.name.StartsWith("AsteriaLycee_Skirt_") && t.name.Contains("_Root_")).Distinct().ToList();
        var physics=avatar.gameObject.AddComponent<VRMSpringBone>();physics.m_comment="Eku uniform clothing";
        physics.RootBones=roots;physics.m_stiffnessForce=.8f;physics.m_dragForce=.6f;physics.m_gravityPower=.015f;physics.m_hitRadius=.003f;
        physics.ColliderGroups=avatar.GetComponentsInChildren<VRMSpringBoneColliderGroup>(true);physics.m_center=avatar;
        wardrobe.uniformPhysics=physics;
        wardrobe.uniformSurface=imported["AsteriaLycee_Blouse"].gameObject.AddComponent<EkuUniformCollision>();
        wardrobe.uniformSurface.capsules=UniformColliders(avatar.GetComponent<Animator>());
        wardrobe.uniformSurface.hips=avatar.GetComponent<Animator>().GetBoneTransform(HumanBodyBones.Hips);
        var sleeves=avatar.gameObject.AddComponent<VRMSpringBone>();sleeves.m_comment="Eku uniform sleeves";
        sleeves.RootBones=bones.Values.Where(t=>t.name.StartsWith("AsteriaLycee_Shoulder_Sleeve")).ToList();
        sleeves.m_stiffnessForce=.8f;sleeves.m_dragForce=.7f;sleeves.m_hitRadius=.003f;sleeves.m_center=avatar;
        sleeves.ColliderGroups=Array.Empty<VRMSpringBoneColliderGroup>();
        wardrobe.uniformSleevePhysics=sleeves;
        wardrobe.Apply(new EkuWardrobeState());
        File.WriteAllText(Path.GetFullPath("../wardrobe-build-state.json"),JsonUtility.ToJson(wardrobe.Status(),true));
        UnityEngine.Object.DestroyImmediate(source);UnityEngine.Object.DestroyImmediate(school);
        const string scene="Assets/EkuWardrobe.unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),scene);AssetDatabase.SaveAssets();
        var args=Environment.GetCommandLineArgs();int option=Array.IndexOf(args,"--wardrobe-output");
        if(option<0)throw new Exception("--wardrobe-output required");string output=args[option+1];Directory.CreateDirectory(Path.GetDirectoryName(output));
        var report=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{scene},locationPathName=output,
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(report.summary.result!=BuildResult.Succeeded)throw new Exception("Wardrobe player build failed");
        Debug.Log("EKU_WARDROBE_BUILT "+output);
    }
    static SkinnedMeshRenderer BodyRenderer(SkinnedMeshRenderer source,Mesh mesh,string name) {
        var node=new GameObject(name);node.transform.SetParent(source.transform.parent,false);
        node.transform.localPosition=source.transform.localPosition;node.transform.localRotation=source.transform.localRotation;
        node.transform.localScale=source.transform.localScale;
        var skin=node.AddComponent<SkinnedMeshRenderer>();skin.sharedMesh=mesh;skin.bones=source.bones;
        skin.rootBone=source.rootBone;skin.sharedMaterials=source.sharedMaterials;
        skin.localBounds=source.localBounds;skin.updateWhenOffscreen=true;return skin;
    }
    public static void Inspect() {
        EditorSceneManager.OpenScene("Assets/EkuCombinedComparison.unity");
        var avatar=UnityEngine.Object.FindFirstObjectByType<EkuComparison>().avatar;
        var source=UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(Eku+"FBX/Eku.fbx"));
        var lines=new List<string>();
        VRMBoneNormalizer.Execute(source,true,false);
        foreach(var src in source.GetComponentsInChildren<SkinnedMeshRenderer>(true)) {
            var dst=avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).FirstOrDefault(s=>s.name==src.name);
            lines.Add(src.name+" vertices="+src.sharedMesh.vertexCount+" target="+(dst?dst.sharedMesh.vertexCount:0)+" materials="+string.Join(",",src.sharedMaterials.Select(m=>m.name)));
            if(src.name=="Body_base") {
                lines.Add("Source bounds="+src.sharedMesh.bounds+" matrix="+src.transform.localToWorldMatrix);
                if(dst) lines.Add("Target bounds="+dst.sharedMesh.bounds+" matrix="+dst.transform.localToWorldMatrix);
                for(int i=0;i<src.sharedMesh.blendShapeCount;i++) lines.Add(src.sharedMesh.GetBlendShapeName(i)+"="+src.GetBlendShapeWeight(i));
                if(dst) {
                    var a=src.sharedMesh.uv;var b=dst.sharedMesh.uv;
                    lines.Add("UV prefix match="+b.Take(Math.Min(a.Length,b.Length)).Select((v,i)=>(v-a[i]).sqrMagnitude<1e-10f).Count(v=>v));
                    var vertices=src.sharedMesh.vertices;var target=dst.sharedMesh.vertices;
                    for(int i=0;i<vertices.Length;i+=2000)lines.Add("Vertex "+i+" src="+vertices[i].ToString("F5")+" dst="+target[i].ToString("F5"));
                    var original=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Eku.prefab").GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(s=>s.name=="Body_base");
                    var shapes=new List<ShapeDump>();
                    for(int i=0;i<src.sharedMesh.blendShapeCount;i++) {
                        string name=src.sharedMesh.GetBlendShapeName(i);
                        if(name.StartsWith("---") || name.EndsWith("_L") || name.EndsWith("_R") || name.StartsWith("Nipple") || name.StartsWith("Nail"))continue;
                        var delta=new Vector3[vertices.Length];src.sharedMesh.GetBlendShapeFrameVertices(i,0,delta,null,null);
                        shapes.Add(new ShapeDump{name=name,delta=delta});
                    }
                    File.WriteAllText(Path.GetFullPath("../body-recovery.json"),JsonUtility.ToJson(new BodyDump{source=vertices,target=original.sharedMesh.vertices,shapes=shapes.ToArray()}));
                }
            }
        }
        var school=UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(School+"Prefabs/Eku/FBX/AsteriaLycee_Eku.fbx"));
        foreach(var s in school.GetComponentsInChildren<SkinnedMeshRenderer>(true)) lines.Add("School "+s.name+" vertices="+s.sharedMesh.vertexCount+" mats="+string.Join(",",s.sharedMaterials.Select(m=>m.name))+" bounds="+s.sharedMesh.bounds);
        var pref=AssetDatabase.LoadAssetAtPath<GameObject>(Eku+"Prefab/Eku.prefab");
        foreach(var mod in PrefabUtility.GetPropertyModifications(pref)??Array.Empty<PropertyModification>())
            if(mod.propertyPath.Contains("BlendShape") || mod.propertyPath=="m_IsActive")lines.Add("Prefab "+mod.target.name+" "+mod.propertyPath+"="+mod.value);
        File.WriteAllLines(Path.GetFullPath("../inspection.txt"),lines);
        UnityEngine.Object.DestroyImmediate(source);UnityEngine.Object.DestroyImmediate(school);
    }
}
