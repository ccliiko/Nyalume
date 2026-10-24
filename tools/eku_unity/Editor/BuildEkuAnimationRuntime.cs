using System;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

// Recompile runtime code against a saved scene without changing its visual assets.
public static class BuildEkuAnimationRuntime {
    public static void Build() {
        var args=Environment.GetCommandLineArgs();
        int scene=Array.IndexOf(args,"--runtime-scene"),output=Array.IndexOf(args,"--runtime-output");
        if(scene<0 || output<0) throw new Exception("Runtime scene and output are required");
        EditorSceneManager.OpenScene(args[scene+1]);
        var library=UnityEngine.Object.FindFirstObjectByType<EkuComparison>().animations;
        BuildEkuAnimations.RefreshEars(library);
        var result=BuildPipeline.BuildPlayer(new BuildPlayerOptions{
            scenes=new[]{args[scene+1]},locationPathName=args[output+1],
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(result.summary.result!=BuildResult.Succeeded) throw new Exception("Animation runtime build failed");
    }
}
