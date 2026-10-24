using System;
using System.IO;
using UnityEngine;
using UnityEditor;
using UnityEditor.SceneManagement;

public static class ValidateEkuHeadPat {
    static int checks;
    static void Require(bool value,string message) {if(!value)throw new Exception("HeadPat: "+message);checks++;}
    static EkuHeadPatOutput Advance(EkuHeadPat pat,ref double now,float seconds,float speed=0,Vector3 direction=default,Vector3 point=default) {
        EkuHeadPatOutput output=default;
        for(int i=0;i<Mathf.CeilToInt(seconds*60);i++) {
            now+=1.0/60;pat.Sample(point,direction,speed,now);output=pat.Tick(1f/60,now);
        }
        return output;
    }
    public static void Run() {
        checks=0;
        var bare=JsonUtility.FromJson<EkuDesktopAction>("{\"kind\":\"look\",\"x\":0.5}");
        Require(bare.context==null || bare.context.version==0,"absent context must not carry a valid version");
        Require(bare.mouse==null || bare.mouse.version==0,"absent mouse must not replace source velocity");
        var nested=JsonUtility.FromJson<EkuDesktopAction>("{\"kind\":\"look\",\"context\":{\"version\":1,\"automatic\":true,\"idle\":300}}");
        Require(nested.context!=null && nested.context.version==1 && nested.context.automatic && nested.context.idle==300,"explicit context must survive JSON decoding");
        Debug.Log("EKU_CONTEXT_JSON_PASSED absentObject="+(bare.context!=null));
        var settings=new EkuHeadPatSettings();var pat=new EkuHeadPat(settings);double now=0;
        pat.Begin(new Vector3(-.7f,0,0),now);
        var left=Advance(pat,ref now,.4f,point:new Vector3(-.7f,0,0));
        Require(left.active && left.strength>.9f && left.headDegrees.z>.2f,"stationary left contact");
        Require(left.earWeights.x>left.earWeights.y && left.bodyDegrees.magnitude<=settings.maximumBodyDegrees,"left ear and bounded body follow contact");
        Require(left.relax>=.3f && left.earWeights.x>.3f,"early contact produces visible eye and ear response");
        var right=Advance(pat,ref now,.6f,point:new Vector3(.7f,0,0));
        Require(right.headDegrees.z<-.2f,"contact side must change lean direction");
        Require(right.earWeights.y>right.earWeights.x,"right contact changes ear response side");
        var gentle=Advance(pat,ref now,3,1,new Vector3(1,0,0));
        Require(gentle.pleasure>.5f && gentle.relax>.35f && gentle.stroking && !gentle.rough,"gentle duration response");
        Require(gentle.relax>.8f && gentle.smile>.6f,"sustained gentle contact has clear coordinated expression");
        Require(gentle.breathingScale<.99f && gentle.breathingScale>=settings.relaxedBreathingScale,"gentle contact gradually changes breathing");
        var stopped=Advance(pat,ref now,.8f);
        Require(!stopped.stroking && stopped.active && stopped.strength>.9f,"stop motion without releasing");
        float retained=stopped.relax;
        pat.End(false);
        for(int i=0;i<36;i++) {now+=1.0/60;stopped=pat.Tick(1f/60,now);}
        Require(stopped.strength==0 && stopped.headDegrees==Vector3.zero && stopped.relax>0 && stopped.relax<retained,"pose before afterglow recovery");
        Require(stopped.bodyDegrees==Vector3.zero && stopped.earWeights==Vector2.zero && stopped.earLiftWeights==Vector2.zero && stopped.breathingScale==1,"body ears breathing restore before face");
        pat.Begin(Vector3.zero,now);
        var spike=Advance(pat,ref now,.04f,10,new Vector3(10,0,0));
        Require(!spike.rough,"single spike must not trigger rough");
        var rough=Advance(pat,ref now,.7f,10,new Vector3(10,0,0));
        Require(rough.rough && rough.startle>.1f && rough.headDegrees.magnitude<=settings.maximumDegrees,"rough hysteresis and limit");
        var calm=Advance(pat,ref now,1,.3f);
        Require(!calm.rough,"rough must release after calm input");
        pat.End(true);
        for(int i=0;i<24;i++) {now+=1.0/60;calm=pat.Tick(1f/60,now);}
        Require(calm.strength==0 && calm.relax==0 && calm.headDegrees==Vector3.zero,"cancel removes temporary layers");
        pat.Begin(Vector3.zero,now);pat.Sample(Vector3.one*float.NaN,Vector3.zero,float.NaN,now);
        calm=pat.Tick(1f/60,now+1f/60);
        Require(EkuEmotion.Finite(calm.headDegrees.x) && calm.speed==0,"invalid sample cannot poison or inherit velocity");
        var cycle=new EkuHeadPat(settings);double cycleTime=0;cycle.Begin(new Vector3(-.7f,0,0),cycleTime);
        float maximumFold=0,maximumLift=0,foldAt=0,liftAt=0;
        for(int i=0;i<85;i++) {
            var current=Advance(cycle,ref cycleTime,1f/60,1,Vector3.right,new Vector3(-.7f,0,0));
            if(current.earWeights.x>maximumFold) {maximumFold=current.earWeights.x;foldAt=(float)cycleTime;}
            if(current.earLiftWeights.x>maximumLift) {maximumLift=current.earLiftWeights.x;liftAt=(float)cycleTime;}
        }
        Require(maximumFold>.8f && maximumLift>.45f,"contact has a clear fold and rebound rather than a fixed ear pose");
        Require(liftAt>foldAt+.1f,"ear rebound follows the initial fold");
        var quiet=Advance(cycle,ref cycleTime,3);
        Require(quiet.earWeights.x<.32f && quiet.earLiftWeights.x<.01f,"stationary holding settles instead of perpetual ear vibration");
        pat.End(true);for(int i=0;i<60;i++)pat.Tick(1f/60,now);
        // Warmed deterministic evaluator: transport/status/render allocations are excluded.
        for(int i=0;i<10;i++)pat.Tick(1f/60,now);
        long before=GC.GetAllocatedBytesForCurrentThread();
        for(int i=0;i<10000;i++) {now+=1.0/60;pat.Tick(1f/60,now);}
        long allocated=GC.GetAllocatedBytesForCurrentThread()-before;
        Require(allocated==0,"steady evaluator allocated "+allocated);
        string[] args=Environment.GetCommandLineArgs();int option=Array.IndexOf(args,"--headpat-report");
        if(option>=0)File.WriteAllText(args[option+1],"{\"checks\":"+checks+",\"evaluatorAllocatedBytes\":"+allocated+"}");
        Debug.Log("EKU_HEADPAT_CHECKS_PASSED checks="+checks+" allocated="+allocated);
    }
    public static void Build() {
        Run();
        var assembly=typeof(ValidateEkuHeadPat).Assembly;
        assembly.GetType("ValidateEkuCheek")?.GetMethod("Run")?.Invoke(null,null);
        assembly.GetType("ValidateEkuMouseTarget")?.GetMethod("Run")?.Invoke(null,null);
        var args=Environment.GetCommandLineArgs();int sceneOption=Array.IndexOf(args,"--release-scene");
        string scene=sceneOption>=0?args[sceneOption+1]:"Assets/EkuWardrobe.unity";
        EditorSceneManager.OpenScene(scene);
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        if(comparison && !comparison.cheekProfile) {
            const string path="Assets/EkuCheekDefault.asset";
            var profile=AssetDatabase.LoadAssetAtPath<CheekDeformationProfile>(path);
            if(!profile) {profile=ScriptableObject.CreateInstance<CheekDeformationProfile>();AssetDatabase.CreateAsset(profile,path);}
            comparison.cheekProfile=profile;EditorUtility.SetDirty(comparison);
            EditorSceneManager.SaveScene(comparison.gameObject.scene);
        }
        BuildEkuRelease.Build();
    }
}
