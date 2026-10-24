using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UniGLTF;
using VRM;

public static class BuildEkuComparison {
    [MenuItem("Eku/Build local lilToon comparison")]
    public static void Build() {
        AssetDatabase.Refresh();
        var prefab=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Eku.prefab");
        if(!prefab) {
            using(var gltfData=new GlbFileParser(Path.GetFullPath("Assets/Eku.vrm")).Parse())
            using(var context=new VRMImporterContext(new VRMData(gltfData))) {
                var loaded=context.Load();
                new VRMEditorImporterContext(context,UnityPath.FromUnityPath("Assets/Eku.prefab")).SaveAsAsset(loaded);
            }
            AssetDatabase.Refresh();
            prefab=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Eku.prefab");
        }
        if(!prefab) throw new Exception("VRM0 import did not create a prefab");
        string data=Path.GetFullPath("../comparison.json");
        File.Copy(data,"Assets/comparison.json",true);
        AssetDatabase.ImportAsset("Assets/comparison.json",ImportAssetOptions.ForceSynchronousImport);
        File.Copy(Path.GetFullPath("../motion.json"),"Assets/motion.json",true);
        AssetDatabase.ImportAsset("Assets/motion.json",ImportAssetOptions.ForceSynchronousImport);
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
        var avatar=(GameObject)PrefabUtility.InstantiatePrefab(prefab);
        var assignments=new System.Collections.Generic.List<string>();
        foreach(var mesh in avatar.GetComponentsInChildren<Renderer>(true)) {
            var mats=mesh.sharedMaterials;
            for(int i=0;i<mats.Length;i++) {
                string name=mats[i].name.Replace("_mtoon","").Replace(" (Instance)","");
                string path="Assets/sep-neko-ya/Eku/Materials/Default/"+name+".mat";
                var original=AssetDatabase.LoadAssetAtPath<Material>(path);
                if(!original || original.shader.name=="Hidden/InternalErrorShader")
                    throw new Exception("Unresolved source lilToon material: "+path);
                assignments.Add(mats[i].name+" => "+path+" ("+original.shader.name+")");
                mats[i]=original;
            }
            mesh.sharedMaterials=mats;
            if(mesh is SkinnedMeshRenderer skin) skin.updateWhenOffscreen=true;
        }
        var camera=new GameObject("Comparison Camera").AddComponent<Camera>();
        var controller=new GameObject("Local comparison").AddComponent<EkuComparison>();
        controller.avatar=avatar.transform;controller.view=camera;
        controller.animations=BuildEkuAnimations.Build(avatar);
        controller.poseFile=AssetDatabase.LoadAssetAtPath<TextAsset>("Assets/comparison.json");
        controller.motionFile=AssetDatabase.LoadAssetAtPath<TextAsset>("Assets/motion.json");
        RenderSettings.ambientMode=AmbientMode.Flat;RenderSettings.ambientLight=Color.white*.72f;
        AddLight("Key",new Vector3(-.7f,1.2f,-1.5f),1.05f,Color.white);
        AddLight("Fill",new Vector3(1,.3f,-1),.22f,Color.white);
        AddLight("Rim",new Vector3(-.4f,.8f,1),.12f,new Color(.86f,.91f,1));
        GraphicsSettings.defaultRenderPipeline=null;
        QualitySettings.renderPipeline=null;
        PlayerSettings.colorSpace=UnityEngine.ColorSpace.Linear;
        PlayerSettings.runInBackground=true;
        PlayerSettings.defaultScreenWidth=424;PlayerSettings.defaultScreenHeight=621;
        PlayerSettings.fullScreenMode=FullScreenMode.Windowed;
        PlayerSettings.SetScriptingBackend(UnityEditor.Build.NamedBuildTarget.Standalone,ScriptingImplementation.Mono2x);
        PlayerSettings.SetApiCompatibilityLevel(UnityEditor.Build.NamedBuildTarget.Standalone,ApiCompatibilityLevel.NET_Unity_4_8);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneWindows64,new[]{GraphicsDeviceType.Direct3D11});
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),"Assets/Comparison.unity");
        string output=Path.GetFullPath("../player/EkuComparison.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        File.WriteAllLines(Path.GetFullPath("../material-mapping.txt"),assignments.Distinct());
        var result=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{"Assets/Comparison.unity"},
            locationPathName=output,target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(result.summary.result!=BuildResult.Succeeded) throw new Exception("Build failed: "+result.summary.result);
        Debug.Log("EKU_COMPARISON_BUILT "+output);
    }
    static void AddLight(string name,Vector3 direction,float intensity,Color color) {
        var light=new GameObject(name).AddComponent<Light>();
        light.type=LightType.Directional;light.intensity=intensity;light.color=color;
        light.transform.rotation=Quaternion.LookRotation(-direction.normalized);
    }
}
