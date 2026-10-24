using System;
using System.Globalization;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class ValidateEkuFullInteraction {
    public static void Run() {
        string reachJson=JsonUtility.ToJson(new EkuReachState{Weight=.5f,Phase=EkuReachPhase.Grabbed});
        if(!reachJson.Contains("\"Weight\":0.5") || !reachJson.Contains("\"Phase\":2"))
            throw new Exception("Reach state fields must survive the real Unity serializer");
        ValidateEkuHeadPat.Run();ValidateEkuInteraction.Run();ValidateEkuCheek.Run();ValidateEkuMouseTarget.Run();ValidateEkuAttention.Run();
        Debug.Log("EKU_FULL_INTERACTION_VALIDATED");
    }
    public static void Build() {
        Run();
        BuildScene();
    }
    public static void BuildCheekUpdate() {
        ValidateEkuCheek.Run();
        BuildScene();
    }
    static void BuildScene() {
        var args=Environment.GetCommandLineArgs();int option=Array.IndexOf(args,"--release-scene");
        EditorSceneManager.OpenScene(option>=0?args[option+1]:"Assets/EkuWardrobe.unity");
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        if(!comparison)throw new Exception("Interaction scene missing");
        SkinnedMeshRenderer body=null;
        foreach(var skin in comparison.avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true))if(skin.name=="Body") {body=skin;break;}
        if(!body)throw new Exception("Body renderer missing");
        int meshOption=Array.IndexOf(args,"--cheek-build-mesh");
        var derived=meshOption>=0?AssetDatabase.LoadAssetAtPath<Mesh>(args[meshOption+1]):body.sharedMesh;
        if(!derived || derived.blendShapeCount!=119)throw new Exception("Validated 119-shape derived cheek mesh required");
        body.sharedMesh=derived;EditorUtility.SetDirty(body);
        int strengthOption=Array.IndexOf(args,"--cheek-build-strength");
        if(strengthOption>=0) {
            float strength=float.Parse(args[strengthOption+1],CultureInfo.InvariantCulture);
            if(!comparison.cheekProfile || !EkuEmotion.Finite(strength) || strength<0 || strength>1)
                throw new Exception("Existing cheek profile and finite 0..1 strength required");
            comparison.cheekProfile.deformationStrength=strength;EditorUtility.SetDirty(comparison.cheekProfile);
            AssetDatabase.SaveAssetIfDirty(comparison.cheekProfile);
        }
        if(!comparison.interactionProfile) {
            const string path="Assets/EkuInteractionDefault.asset";
            var profile=AssetDatabase.LoadAssetAtPath<EkuInteractionProfile>(path);
            if(!profile) {profile=ScriptableObject.CreateInstance<EkuInteractionProfile>();AssetDatabase.CreateAsset(profile,path);}
            comparison.interactionProfile=profile;
        }
        EditorUtility.SetDirty(comparison);EditorSceneManager.SaveScene(comparison.gameObject.scene);
        BuildEkuRelease.Build();
    }
}
