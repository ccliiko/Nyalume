using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

// This editor runs only in the combined candidate's private project.
public static class BuildEkuCombined {
    static T Asset<T>(string path) where T:UnityEngine.Object {
        var asset=AssetDatabase.LoadAssetAtPath<T>(path);
        if(!asset) throw new Exception("Missing combined asset: "+path);
        return asset;
    }
    public static void Build() {
        AssetDatabase.Refresh();
        EditorSceneManager.OpenScene("Assets/EkuSilkComparison.unity");
        var style=UnityEngine.Object.FindFirstObjectByType<EkuMaterialStyle>();
        if(!style || !style.stockings.silk) throw new Exception("Latest silk scene required");
        string[] clothes={"Jacket","Sleeveless_Sweater","Spats"};
        string[] hair={"Hair_Front","Hair_Head_3","Hair_OneSideUp","Hair_Side_Back"};
        int count=0;
        foreach(var slot in style.slots) {
            string mesh=slot.renderer.name;
            string folder=clothes.Contains(mesh)?"EkuClothMaterials":hair.Contains(mesh)?"EkuHairMaterials":mesh=="Body_base"?"EkuSkinCandidate":null;
            if(folder==null) continue;
            var mats=new Material[slot.enhanced.Length];
            for(int index=0;index<mats.Length;index++) {
                string name=mesh=="Body_base"?"Skin":mesh;
                mats[index]=Asset<Material>("Assets/"+folder+"/"+name+"-"+index+".mat");
                if(ShaderUtil.ShaderHasError(mats[index].shader)) throw new Exception("Invalid shader: "+mats[index].shader.name);
                if(mats[index].name!=slot.enhanced[index].name) throw new Exception("Material identity changed: "+mesh);
                count++;
            }
            slot.enhanced=mats;slot.renderer.sharedMaterials=mats;
        }
        if(count!=8) throw new Exception("Expected eight combined material slots, got "+count);
        var body=(SkinnedMeshRenderer)style.slots.Single(s=>s.renderer.name=="Body_base").renderer;
        var socks=(SkinnedMeshRenderer)style.slots.Single(s=>s.renderer.name=="Knee_Socks").renderer;
        ReplaceMesh(body,Asset<Mesh>("Assets/EkuSkinCandidate/Skin.asset"));
        ReplaceMesh(socks,Asset<Mesh>("Assets/EkuSkinCandidate/SockRim.asset"));
        EditorUtility.SetDirty(style);
        const string scene="Assets/EkuCombinedComparison.unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),scene);
        AssetDatabase.SaveAssets();
        var args=Environment.GetCommandLineArgs();int option=Array.IndexOf(args,"--combined-output");
        if(option<0 || option+1>=args.Length)throw new Exception("--combined-output required");
        string output=Path.GetFullPath(args[option+1]);Directory.CreateDirectory(Path.GetDirectoryName(output));
        var report=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{scene},locationPathName=output,
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(report.summary.result!=BuildResult.Succeeded) throw new Exception("Combined build failed");
        Debug.Log("EKU_COMBINED_BUILT "+output);
    }
    static void ReplaceMesh(SkinnedMeshRenderer renderer,Mesh mesh) {
        var source=renderer.sharedMesh;
        if(source.subMeshCount!=mesh.subMeshCount || source.bindposes.Length!=mesh.bindposes.Length ||
            source.blendShapeCount!=mesh.blendShapeCount || !source.bindposes.SequenceEqual(mesh.bindposes))
            throw new Exception("Skin binding mismatch: "+renderer.name);
        for(int i=0;i<source.blendShapeCount;i++)
            if(source.GetBlendShapeName(i)!=mesh.GetBlendShapeName(i) || source.GetBlendShapeFrameCount(i)!=mesh.GetBlendShapeFrameCount(i))
                throw new Exception("Skin blendshape mismatch: "+renderer.name);
        renderer.sharedMesh=mesh;
    }
}
