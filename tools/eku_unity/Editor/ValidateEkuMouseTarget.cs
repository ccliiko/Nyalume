using System;
using UnityEngine;

// Pure projection/protocol checks; never save a scene or modify model assets.
public static class ValidateEkuMouseTarget {
    static void Check(bool value,string message) { if(!value) throw new Exception("MouseTarget: "+message); }
    static EkuMouseSample Sample(long sequence,double timestamp,string session="test") => new EkuMouseSample {
        version=1,sessionId=session,sequence=sequence,timestamp=timestamp,source="System",dpiScale=1,
        positionValid=true,allowAttention=true,insideViewport=true,windowPosition=new Vector2(.5f,.5f),
        windowSize=new Vector2(400,600),screenPosition=new Vector2(-200,300),mappingRevision=1
    };
    public static void Run() {
        var avatar=new GameObject("MouseTarget test avatar");
        var head=new GameObject("MouseTarget test head");head.transform.SetParent(avatar.transform,false);head.transform.localPosition=Vector3.up*1.3f;
        var cameraObject=new GameObject("MouseTarget test camera");var camera=cameraObject.AddComponent<Camera>();
        camera.transform.position=new Vector3(0,1.3f,-3);camera.transform.LookAt(head.transform.position);
        camera.fieldOfView=45;camera.aspect=2f/3;
        var texture=new RenderTexture(400,600,0);camera.targetTexture=texture;
        try {
            var target=new EkuMouseTarget(camera,avatar.transform,head.transform,1.5f);
            Check(target.Receive(Sample(1,100),10),"first sample");
            Check(target.Snapshot.valid && target.Snapshot.projectionMode=="HeadPlane","head projection");
            Check((target.Snapshot.worldPosition-head.transform.position).sqrMagnitude<.000001f,"center ray position");
            Check(target.Snapshot.distanceToHead>=0 && target.Snapshot.distanceToFace>=0,"DIP distances");
            Check(target.Snapshot.screenPosition.x==-200,"negative desktop coordinates");
            var doubled=new RenderTexture(800,1200,0);camera.targetTexture=doubled;target.Refresh(10);
            Check(target.Snapshot.valid,"same-aspect supersampling preserves viewport projection");
            camera.targetTexture=texture;UnityEngine.Object.DestroyImmediate(doubled);
            Check(!target.Receive(Sample(1,100),10.2),"duplicate rejected");
            target.Refresh(10.26);
            Check(!target.Snapshot.valid && target.Snapshot.invalidReason=="Stale","duplicates do not refresh timeout");
            Check(Mathf.Abs(target.Snapshot.age-.26f)<.001f,"age uses receiver clock without mixing source clock epochs");
            Check(!target.Snapshot.allowAttention && target.Snapshot.direction==Vector2.zero,"stale disarmed");
            Check(target.Receive(Sample(2,100.02),10.3),"new sample after gap");
            target.BeginContact(head.transform.position,head.transform);target.Refresh(10.3);
            Check(target.Snapshot.projectionMode=="SurfaceHit","actual press hit");
            var moved=Sample(3,100.04);moved.windowPosition.x=.6f;
            target.Receive(moved,10.32);
            Check(target.Snapshot.valid && target.Snapshot.projectionMode=="ContactPlane","drag projects onto captured plane");
            Check(target.Snapshot.worldPosition.x>0 && Mathf.Abs(target.Snapshot.worldPosition.z)<.00001f,"drag sign and fixed depth");
            Check(!target.Snapshot.allowAttention,"contact owns attention");
            moved=Sample(4,100.06);moved.mappingRevision=2;target.Receive(moved,10.34);
            Check(!target.Snapshot.valid && target.Snapshot.invalidReason=="MappingChanged","mapping change invalidates captured contact");
            target.EndContact();target.Refresh(10.34);
            Check(target.Snapshot.valid && target.Snapshot.projectionMode=="HeadPlane","release restores observation plane");
            var raw=Sample(5,100.08);raw.source="RawIntegrated";target.Receive(raw,10.36);
            Check(target.Snapshot.valid && !target.Snapshot.allowAttention,"raw position is valid without autonomous invitation");
            var wrongSize=Sample(6,100.1);wrongSize.windowSize.x=800;target.Receive(wrongSize,10.38);
            Check(!target.Snapshot.valid && target.Snapshot.invalidReason=="MappingChanged","render size mismatch");
            var old=Sample(7,100.12);old.age=.5f;target.Receive(old,10.4);
            Check(!target.Snapshot.valid && target.Snapshot.invalidReason=="Stale","delayed boundary retains age");
            Check(Mathf.Abs(target.Snapshot.age-.5f)<.001f,"receiver age includes source queue delay");
            var invalid=Sample(8,100.14);invalid.windowPosition.x=float.NaN;
            Check(!target.Receive(invalid,10.42),"NaN rejected");
            Check(!target.Receive(Sample(8,99),10.42),"backward source timestamp rejected");
            Check(target.Receive(Sample(1,1,"restart"),10.44),"new session resets timestamp epoch");
            var saved=target.Snapshot;saved.worldPosition=Vector3.one*900;
            Check(target.Snapshot.worldPosition!=saved.worldPosition,"consumer receives a value copy");
            var badPosition=Sample(2,1.02,"restart");badPosition.positionValid=false;target.Receive(badPosition,10.46);
            Check(!target.Snapshot.valid && target.Snapshot.invalidReason=="PositionUnavailable","OS sample failure");
            Debug.Log("EKU_MOUSE_TARGET_VALIDATION_PASSED");
        } finally {
            camera.targetTexture=null;
            UnityEngine.Object.DestroyImmediate(texture);UnityEngine.Object.DestroyImmediate(cameraObject);
            UnityEngine.Object.DestroyImmediate(avatar);
        }
    }
}
