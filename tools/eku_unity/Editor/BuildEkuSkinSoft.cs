using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildEkuSkinSoft {
    const string Folder="Assets/EkuSkinSoftCandidate";
    public static void Build() {
        AssetDatabase.Refresh();
        EditorSceneManager.OpenScene("Assets/EkuCombinedComparison.unity");
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        var body=comparison.avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(r=>r.name=="Body_base");
        var mesh=body.sharedMesh;
        string source=File.ReadAllText("Assets/EkuSkinCandidate/Skin.shader");
        string wrapper=source.Split('\n').First(line=>line.StartsWith("float4 frag_forward(v2f"));
        string shader=source.Replace("Shader \"Eku/Skin Pressure\"","Shader \"Eku/Soft Skin\"")
            .Replace(wrapper,"#include \"Assets/EkuSkinSoftCandidate/SkinSoft.cginc\"");
        File.WriteAllText(Folder+"/Skin.shader",shader);
        AssetDatabase.ImportAsset(Folder+"/Skin.shader",ImportAssetOptions.ForceSynchronousImport);
        var skinShader=AssetDatabase.LoadAssetAtPath<Shader>(Folder+"/Skin.shader");
        if(!skinShader)throw new Exception("Skin shader missing");
        var before=body.sharedMaterials;
        var mats=before.Select((original,i)=>{
            var mat=new Material(original){name=original.name,shader=skinShader};
            mat.SetFloat("_ShadeToony",.30f);
            mat.SetFloat("_ShadeShift",-.18f);
            string path=Folder+"/Skin-"+i+".mat";
            var saved=AssetDatabase.LoadAssetAtPath<Material>(path);
            if(saved){EditorUtility.CopySerialized(mat,saved);UnityEngine.Object.DestroyImmediate(mat);}
            else {string name=mat.name;AssetDatabase.CreateAsset(mat,path);mat.name=name;EditorUtility.SetDirty(mat);}
            return AssetDatabase.LoadAssetAtPath<Material>(path);
        }).ToArray();
        body.sharedMaterials=mats;
        comparison.avatar.GetComponent<EkuMaterialStyle>().slots.Single(s=>s.renderer==body).enhanced=mats;
        if(body.sharedMesh!=mesh)throw new Exception("Skin mesh changed");
        AssetDatabase.SaveAssets();string scene="Assets/EkuSkinSoftComparison.unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),scene);
        var args=Environment.GetCommandLineArgs();int outputArg=Array.IndexOf(args,"--skin-soft-output");
        string output=args[outputArg+1];Directory.CreateDirectory(Path.GetDirectoryName(output));
        var result=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{scene},locationPathName=output,
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(result.summary.result!=BuildResult.Succeeded)throw new Exception("Soft skin build failed");
        Debug.Log("EKU_SKIN_SOFT_BUILT body_only=True mesh_unchanged=True "+output);
    }
}
