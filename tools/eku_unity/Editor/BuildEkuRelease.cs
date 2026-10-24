using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildEkuRelease {
    public static void Build() {
        var args=Environment.GetCommandLineArgs();
        int option=Array.IndexOf(args,"--release-root");
        if(option<0) throw new Exception("--release-root required");
        string root=Path.GetFullPath(args[option+1]);
        int sceneOption=Array.IndexOf(args,"--release-scene");
        string content=sceneOption>=0?args[sceneOption+1]:"Assets/EkuCombinedComparison.unity";
        EditorSceneManager.OpenScene(content);
        var desktop=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        if(!desktop || !desktop.avatar || !desktop.animations) throw new Exception("Completed Eku scene required");
        string bundles=Path.Combine(root,"bundles");Directory.CreateDirectory(bundles);
        var manifest=BuildPipeline.BuildAssetBundles(bundles,new[]{new AssetBundleBuild{
            assetBundleName="eku.content",assetNames=new[]{content}}},
            BuildAssetBundleOptions.ChunkBasedCompression|BuildAssetBundleOptions.StrictMode,
            BuildTarget.StandaloneWindows64);
        if(!manifest || manifest.GetAllDependencies("eku.content").Length!=0)
            throw new Exception("Eku bundle must be self-contained");
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
        new GameObject("Encrypted Eku startup").AddComponent<EkuBundledStartup>();
        const string startup="Assets/EkuEncryptedStartup.unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),startup);
        Directory.CreateDirectory(Path.Combine(root,"player"));
        var report=BuildPipeline.BuildPlayer(new BuildPlayerOptions{
            scenes=new[]{startup},locationPathName=Path.Combine(root,"player/EkuPlayer.exe"),
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.DetailedBuildReport});
        if(report.summary.result!=BuildResult.Succeeded) throw new Exception("Eku player build failed");
        var packed=report.packedAssets.SelectMany(p=>p.contents).Select(p=>p.sourceAssetPath).Distinct().ToArray();
        File.WriteAllLines(Path.Combine(root,"player-assets.txt"),packed);
        // Empty bootstrap must not retain a second, unencrypted copy of purchased content.
        var leaked=packed.Where(p=>p.StartsWith("Assets/") &&
            new[]{".png",".jpg",".fbx",".vrm",".prefab",".asset",".anim",".mat",".wav",".ogg",".mp3"}
            .Contains(Path.GetExtension(p).ToLowerInvariant())).ToArray();
        if(leaked.Length!=0) throw new Exception("Unencrypted player assets: "+string.Join(", ",leaked));
        Debug.Log("EKU_RELEASE_BUILT "+root);
    }
}
