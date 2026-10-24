using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildEkuHair {
    public static void Build() {
        AssetDatabase.Refresh();
        EditorSceneManager.OpenScene("Assets/EkuStockingsComparison.unity");
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        var style=comparison.avatar.GetComponent<EkuMaterialStyle>();
        if(!comparison.animations || !style) throw new Exception("Completed Eku scene required");
        bool baseline=Environment.GetCommandLineArgs().Contains("--hair-baseline");
        if(!baseline) {
            var shader=Shader.Find("Eku/Hair Silk");
            if(!shader || ShaderUtil.ShaderHasError(shader)) throw new Exception("Hair shader failed to compile");
            if(!AssetDatabase.IsValidFolder("Assets/EkuHairMaterials")) AssetDatabase.CreateFolder("Assets","EkuHairMaterials");
            string[] names={"Hair_Front","Hair_Head_3","Hair_OneSideUp","Hair_Side_Back"};
            var hair=style.slots.Where(slot=>names.Contains(slot.renderer.name)).ToArray();
            if(hair.Length!=4) throw new Exception("Expected four hair meshes");
            foreach(var slot in hair) {
                var materials=(Material[])slot.enhanced.Clone();
                for(int index=0;index<materials.Length;index++) {
                    var mat=new Material(materials[index]);
                    int queue=mat.renderQueue;
                    mat.shader=shader;mat.renderQueue=queue;
                    mat.SetFloat("_HairDepth",.52f);
                    mat.SetFloat("_HairSilk",.24f);
                    mat.SetFloat("_HairWidth",10);
                    mat.SetColor("_HairHighlightColor",new Color(1,.75f,.54f));
                    string path="Assets/EkuHairMaterials/"+slot.renderer.name+"-"+index+".mat";
                    var saved=AssetDatabase.LoadAssetAtPath<Material>(path);
                    if(saved) {EditorUtility.CopySerialized(mat,saved);UnityEngine.Object.DestroyImmediate(mat);}
                    else {string name=mat.name;AssetDatabase.CreateAsset(mat,path);mat.name=name;saved=mat;}
                    EditorUtility.SetDirty(saved);materials[index]=saved;
                }
                slot.enhanced=materials;slot.renderer.sharedMaterials=materials;
            }
            EditorUtility.SetDirty(style);
        }
        string scene="Assets/EkuHair"+(baseline?"Baseline":"Comparison")+".unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),scene);
        AssetDatabase.SaveAssets();
        string output=Path.GetFullPath("../../eku-unity-hair-20261013/"+(baseline?"baseline-player":"player")+"/EkuComparison.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        var result=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{scene},locationPathName=output,
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(result.summary.result!=BuildResult.Succeeded) throw new Exception("Hair build failed");
        Debug.Log("EKU_HAIR_BUILT "+output);
    }
}
