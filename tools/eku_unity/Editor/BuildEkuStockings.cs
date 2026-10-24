using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildEkuStockings {
    [Serializable] class TextureBinding {public string property,path;}
    [Serializable] class MaterialBinding {public string name;public TextureBinding[] textures;}
    [Serializable] class Bindings {public MaterialBinding[] materials;}
    public static void Build() {
        var args=Environment.GetCommandLineArgs();
        int stageArg=Array.IndexOf(args,"--surface-stage");
        int stage=stageArg<0?0:int.Parse(args[stageArg+1]);
        bool silk=Array.IndexOf(args,"--silk")>=0;
        string silkName=stage==0?"EkuSilk":"EkuSilkSurface"+stage;
        string silkFolder=stage==0?"silk":"silk-surface-"+stage;
        AssetDatabase.Refresh();
        // Reuse the latest completed animation scene without rebuilding its assets.
        EditorSceneManager.OpenScene("Assets/Comparison.unity");
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        if(!comparison || !comparison.animations) throw new Exception("Completed animation scene required");
        var bindings=JsonUtility.FromJson<Bindings>(File.ReadAllText("Assets/EkuStockingMaps/mtoon-bindings.json"));
        var slots=new System.Collections.Generic.List<EkuMaterialStyle.Slot>();
        string materialFolder=silk?silkName+"Materials":stage==0?"EkuStockingMaterials":"EkuSurface"+stage+"Materials";
        if(!AssetDatabase.IsValidFolder("Assets/"+materialFolder)) AssetDatabase.CreateFolder("Assets",materialFolder);
        // Copy each renderer slot so shared hair/ear/tail and costume surfaces can evolve independently.
        foreach(var renderer in comparison.avatar.GetComponentsInChildren<Renderer>(true)) {
            var materials=renderer.sharedMaterials;
            var originals=new Material[materials.Length];
            for(int slot=0;slot<materials.Length;slot++) {
                string name=materials[slot].name.Replace(" (Instance)","");
                if(!name.EndsWith("_mtoon")) name+="_mtoon";
                var source=AssetDatabase.LoadAssetAtPath<Material>("Assets/Eku.Materials/"+name+".asset");
                if(!source) throw new Exception("Missing VRM material "+name);
                var mat=new Material(source){name=name};
                foreach(var texture in bindings.materials.Single(b=>b.name==name).textures) {
                    var image=AssetDatabase.LoadAssetAtPath<Texture2D>(texture.path);
                    if(!image) throw new Exception("Missing VRM texture "+texture.path);
                    mat.SetTexture(texture.property,image);
                }
                string prefix="Assets/"+materialFolder+"/"+renderer.name.Replace("/","_")+"-"+slot;
                originals[slot]=SaveMaterial(new Material(mat){name=name},prefix+"-original.mat");
                bool face=name.Contains("Face") || name.Contains("Body"),hair=name.Contains("Hair");
                float toony=face?.72f:hair?.82f:.8f,shift=face?-.15f:hair?-.22f:-.2f;
                float unityShift=-shift-(1-toony);
                mat.SetFloat("_ShadeShift",unityShift);
                mat.SetFloat("_ShadeToony",1-(1-toony)/(.5f-.5f*unityShift));
                mat.SetColor("_ShadeColor",Color.Lerp(mat.GetColor("_ShadeColor"),mat.color,face?.2f:.08f));
                mat.SetFloat("_OutlineWidth",Mathf.Min(mat.GetFloat("_OutlineWidth"),hair?.16f:.2f));
                if(!face) mat.SetColor("_RimColor",new Color(.035f,.028f,.024f));
                mat.SetFloat("_RimLightingMix",.65f);
                // Each step targets actual meshes; the ears/tail share source hair materials.
                if(stage>=1 && new[]{"Jacket","Sleeveless_Sweater","Spats"}.Contains(renderer.name)) {
                    mat.SetColor("_ShadeColor",Color.Lerp(Color.white,new Color(.8491f,.7329f,.7452f),silk?.6f:.25f));
                    mat.SetFloat("_RimFresnelPower",3);
                }
                if(stage>=2 && new[]{"Hair_Front","Hair_Head_3","Hair_OneSideUp","Hair_Side_Back"}.Contains(renderer.name)) {
                    mat.SetFloat("_RimFresnelPower",3);
                    mat.SetTexture("_RimTexture",Map("hair-backlit-mask",false,false));
                }
                if(stage>=3 && renderer.name=="Body_base") {
                    mat.SetColor("_RimColor",new Color(.012f,.009f,.007f));
                    mat.SetFloat("_RimFresnelPower",3);
                }
                materials[slot]=SaveMaterial(mat,prefix+".mat");
            }
            renderer.sharedMaterials=materials;
            slots.Add(new EkuMaterialStyle.Slot{renderer=renderer,source=originals,enhanced=materials});
        }
        var grade=comparison.view.gameObject.AddComponent<EkuColorGrade>();
        grade.shader=Shader.Find("Hidden/Eku/ColorGrade");
        comparison.view.gameObject.AddComponent<EkuRenderProbe>();
        var socks=comparison.avatar.GetComponentsInChildren<Renderer>(true)
            .Single(r=>r.name.Replace(".baked","").Replace("(copy)","")=="Knee_Socks");
        var effect=socks.gameObject.AddComponent<EkuStockings>();
        var sockMesh=((SkinnedMeshRenderer)socks).sharedMesh;
        Debug.Log("EKU_SOCK_MESH vertices="+sockMesh.vertexCount+", tangents="+sockMesh.tangents.Length);
        effect.socks=socks;effect.shader=Shader.Find("Eku/White Stockings");
        effect.silk=silk;
        effect.fabric=Map("fabric",true,false);effect.skin=Map("skin",true,false);
        effect.density=Map("density",false,false);effect.weave=Map("weave-normal",false,true);
        if(!effect.shader) throw new Exception("Missing stocking shader");
        var style=comparison.avatar.gameObject.AddComponent<EkuMaterialStyle>();
        style.slots=slots.ToArray();style.stockings=effect;style.grade=grade;
        SaveAndBuild(silk?silkName+"Comparison":stage==0?"EkuStockingsComparison":"EkuSurface"+stage+"Comparison",
            silk?silkFolder+"/player":stage==0?"player":"surface-"+stage+"/player");
    }
    static Material SaveMaterial(Material mat,string path) {
        var saved=AssetDatabase.LoadAssetAtPath<Material>(path);
        if(saved) {EditorUtility.CopySerialized(mat,saved);UnityEngine.Object.DestroyImmediate(mat);return saved;}
        string name=mat.name;
        AssetDatabase.CreateAsset(mat,path);
        // CreateAsset renames new materials after their file; contact picking
        // classifies face/hair using the original material name.
        mat.name=name;EditorUtility.SetDirty(mat);
        return mat;
    }
    public static void BuildBaseline() {
        AssetDatabase.Refresh();
        EditorSceneManager.OpenScene("Assets/Comparison.unity");
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        comparison.view.gameObject.AddComponent<EkuRenderProbe>();
        SaveAndBuild("EkuMaterialsBaseline","baseline-player");
    }
    static void SaveAndBuild(string name,string folder) {
        AssetDatabase.SaveAssets();
        string scene="Assets/"+name+".unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),scene);
        string output=Path.GetFullPath("../../eku-unity-stockings-20261013/"+folder+"/EkuComparison.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        var result=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{scene},
            locationPathName=output,target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(result.summary.result!=BuildResult.Succeeded) throw new Exception("Stocking build failed");
        Debug.Log("EKU_STOCKINGS_BUILT "+output);
    }
    static Texture2D Map(string name,bool srgb,bool normal) {
        string path="Assets/EkuStockingMaps/"+name+".png";
        var importer=AssetImporter.GetAtPath(path) as TextureImporter;
        if(!importer) return null; // Optional maps preserve the existing material on failure.
        importer.textureType=normal?TextureImporterType.NormalMap:TextureImporterType.Default;
        importer.sRGBTexture=srgb;importer.mipmapEnabled=true;importer.anisoLevel=4;
        importer.wrapMode=normal?TextureWrapMode.Repeat:TextureWrapMode.Clamp;
        importer.textureCompression=TextureImporterCompression.Uncompressed;
        importer.SaveAndReimport();
        return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
    }
}
