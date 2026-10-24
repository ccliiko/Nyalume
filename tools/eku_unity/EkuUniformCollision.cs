using System;
using System.Collections.Generic;
using UnityEngine;

[Serializable] public class EkuUniformCapsule {
    public Transform start,end;
    public float radius,endRadius;
}

// A final surface constraint over the authored bone/spring pose. Push contacts
// towards the garment's own outside so opposite skirt panels cannot swap sides.
// ponytail: anatomical capsules preserve the mesh; use a cloth solver when new
// garments need self-collision rather than body clearance.
public class EkuUniformCollision : MonoBehaviour {
    public EkuUniformCapsule[] capsules;
    public Transform hips;
    SkinnedMeshRenderer source;
    Mesh surface;
    MeshRenderer visible;
    Vector3[] outside;
    readonly List<Vector3> points=new List<Vector3>(),normals=new List<Vector3>(),changed=new List<Vector3>();
    bool[] movable;
    Vector3[] starts,ends;
    float[] radii,endRadii;
    public int Contacts {get;private set;}
    public Mesh Surface => surface;
    void Awake() {
        source=GetComponent<SkinnedMeshRenderer>();
        surface=new Mesh {name="Eku uniform constrained surface"};surface.MarkDynamic();
        var node=new GameObject("Uniform surface");node.transform.SetParent(transform,false);
        node.AddComponent<MeshFilter>().sharedMesh=surface;
        visible=node.AddComponent<MeshRenderer>();visible.sharedMaterials=source.sharedMaterials;
        source.forceRenderingOff=true;
        var rest=source.sharedMesh.vertices;
        outside=new Vector3[rest.Length];movable=new bool[rest.Length];
        for(int i=0;i<rest.Length;i++) {
            movable[i]=rest[i].y<.72f;
            var radial=new Vector3(rest[i].x,0,rest[i].z+.01f).normalized;
            outside[i]=hips.InverseTransformDirection(transform.TransformDirection(radial));
        }
        starts=new Vector3[capsules.Length];ends=new Vector3[capsules.Length];
        radii=new float[capsules.Length];endRadii=new float[capsules.Length];
    }
    public void Constrain() {
        visible.enabled=source.enabled && source.gameObject.activeInHierarchy;
        Contacts=0;if(!visible.enabled)return;
        source.BakeMesh(surface);surface.GetVertices(points);surface.GetNormals(normals);
        for(int c=0;c<capsules.Length;c++) {
            var capsule=capsules[c];starts[c]=capsule.start.position;ends[c]=capsule.end.position;
            float scale=capsule.start.lossyScale.x;
            radii[c]=capsule.radius*scale+.010f;endRadii[c]=capsule.endRadius*scale+.010f;
        }
        var world=transform.localToWorldMatrix;var local=transform.worldToLocalMatrix;
        for(int i=0;i<points.Count;i++)if(movable[i]) {
            var point=world.MultiplyPoint3x4(points[i]);var initial=point;
            var outward=hips.TransformDirection(outside[i]);
            // Revisit overlaps (e.g. bent knee/thigh) after projecting a capsule.
            for(int pass=0;pass<4;pass++) {
                bool touched=false;
                for(int c=0;c<capsules.Length;c++) {
                    var axis=ends[c]-starts[c];
                    float t=Mathf.Clamp01(Vector3.Dot(point-starts[c],axis)/Mathf.Max(axis.sqrMagnitude,1e-8f));
                    var delta=point-Vector3.Lerp(starts[c],ends[c],t);
                    float radius=Mathf.Lerp(radii[c],endRadii[c],t);
                    if(delta.sqrMagnitude>=radius*radius)continue;
                    float along=Vector3.Dot(delta,outward);
                    // Legs are inside the skirt; hands touching its outside
                    // must dent the cloth inward rather than be covered by it.
                    var direction=c>=6 && along<0?-outward:outward;
                    along=Vector3.Dot(delta,direction);
                    float distance=-along+Mathf.Sqrt(along*along+radius*radius-delta.sqrMagnitude);
                    point+=direction*(distance+.001f);touched=true;
                }
                if(!touched)break;
            }
            if((point-initial).sqrMagnitude>1e-10f)Contacts++;
            points[i]=local.MultiplyPoint3x4(point);
        }
        surface.SetVertices(points);
        if(Contacts>0) {
            surface.RecalculateNormals();surface.GetNormals(changed);
            for(int i=0;i<changed.Count;i++)if(!movable[i])changed[i]=normals[i];
            surface.SetNormals(changed);surface.RecalculateTangents();
        }
        surface.RecalculateBounds();
    }
    public void BakeSurface(Mesh target) {
        // Same source topology/weights for existing touch and magnifier anchors.
        source.BakeMesh(target);
        if(points.Count==target.vertexCount)target.SetVertices(points);
        target.RecalculateBounds();
    }
    void OnDestroy() {
        if(source)source.forceRenderingOff=false;
        if(surface)Destroy(surface);
    }
}
