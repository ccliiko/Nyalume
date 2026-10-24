using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

// Runs only in the separate clothing project, never in the shared animation project.
public static class BuildEkuClothes {
    static string Output {
        get {
            var args=Environment.GetCommandLineArgs();
            int index=Array.IndexOf(args,"--clothes-output");
            if(index<0 || index+1>=args.Length) throw new Exception("--clothes-output required");
            return Path.GetFullPath(args[index+1]);
        }
    }
    [Serializable] class Change {
        public string mesh, source, candidate;
        public float occlusion, sheen, softness, fuzz, brightness;
    }
    [Serializable] class Report {public Change[] changed; public string[] preserved;}
    public static void Build() {
        AssetDatabase.Refresh();
        EditorSceneManager.OpenScene("Assets/EkuSilkComparison.unity");
        var style=UnityEngine.Object.FindFirstObjectByType<EkuMaterialStyle>();
        if(!style || !style.stockings || !style.stockings.silk)
            throw new Exception("Silk baseline scene required");
        SaveAndBuild("EkuClothesBefore","before/player");
        // BuildPipeline reloads scene objects; reacquire them before editing.
        EditorSceneManager.OpenScene("Assets/EkuSilkComparison.unity");
        style=UnityEngine.Object.FindFirstObjectByType<EkuMaterialStyle>();
        var shader=Shader.Find("Eku/Cloth");
        if(!shader) throw new Exception("Clothing shader missing");
        if(!AssetDatabase.IsValidFolder("Assets/EkuClothMaterials"))
            AssetDatabase.CreateFolder("Assets","EkuClothMaterials");
        var changes=new List<Change>();
        var preserved=new List<string>();
        foreach(var slot in style.slots) {
            string mesh=slot.renderer.name;
            if(!new[]{"Jacket","Sleeveless_Sweater","Spats"}.Contains(mesh)) {
                preserved.Add(mesh);continue;
            }
            var materials=(Material[])slot.enhanced.Clone();
            for(int i=0;i<materials.Length;i++) {
                var source=materials[i];
                var mat=new Material(source){shader=shader,name=source.name};
                mat.renderQueue=source.renderQueue;
                mat.SetFloat("_ClothAO",mesh=="Jacket"?.38f:.3f);
                mat.SetFloat("_ClothSheen",mesh=="Jacket"?.018f:mesh=="Spats"?.024f:.045f);
                mat.SetFloat("_ClothSoftness",mesh=="Jacket"?10:8);
                mat.SetFloat("_ClothFuzz",mesh=="Jacket"?.006f:.012f);
                mat.SetFloat("_ClothBrightness",mesh=="Jacket"?.87f:1);
                string path="Assets/EkuClothMaterials/"+mesh+"-"+i+".mat";
                var saved=AssetDatabase.LoadAssetAtPath<Material>(path);
                if(saved) {EditorUtility.CopySerialized(mat,saved);UnityEngine.Object.DestroyImmediate(mat);}
                else {AssetDatabase.CreateAsset(mat,path);saved=mat;}
                saved.name=source.name;EditorUtility.SetDirty(saved);
                materials[i]=saved;
                changes.Add(new Change{mesh=mesh,source=AssetDatabase.GetAssetPath(source),candidate=path,
                    occlusion=saved.GetFloat("_ClothAO"),sheen=saved.GetFloat("_ClothSheen"),
                    softness=saved.GetFloat("_ClothSoftness"),fuzz=saved.GetFloat("_ClothFuzz"),
                    brightness=saved.GetFloat("_ClothBrightness")});
            }
            slot.enhanced=materials;slot.renderer.sharedMaterials=materials;
        }
        if(changes.Count!=3) throw new Exception("Expected exactly three clothing slots");
        EditorUtility.SetDirty(style);
        SaveAndBuild("EkuClothesComparison","player");
        File.WriteAllText(Path.Combine(Output,"materials.json"),
            JsonUtility.ToJson(new Report{changed=changes.ToArray(),preserved=preserved.ToArray()},true));
    }
    static void SaveAndBuild(string name,string folder) {
        AssetDatabase.SaveAssets();
        string scene="Assets/"+name+".unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),scene);
        string output=Path.Combine(Output,folder,"EkuComparison.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        var result=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{scene},locationPathName=output,
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(result.summary.result!=BuildResult.Succeeded) throw new Exception("Clothing build failed");
        Debug.Log("EKU_CLOTHES_BUILT "+output);
    }
}
