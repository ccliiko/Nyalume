using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

// This candidate derives meshes/materials from the completed stocking scene.
// Originals, animation bindings and the stocking shader are never rewritten.
public static class BuildEkuSkin {
    const string Folder="Assets/EkuSkinCandidate";
    static float Bell(float y,float center,float width) {return Mathf.Exp(-Mathf.Pow((y-center)/width,2));}
    static bool Thigh(Vector3 p) {return Mathf.Abs(p.x)<.15f && p.y>.525f && p.y<.625f;}
    static Vector3 Radial(Vector3 p) {return new Vector3(p.x-Mathf.Sign(p.x)*.055f,0,p.z-.016f).normalized;}
    static Vector3 Deform(Vector3 p) {
        if(!Thigh(p))return p;
        return p-Radial(p)*.0015f*Bell(p.y,.5588f,.014f);
    }
    class Shape {public string name;public float weight;public Vector3[] p,n,t;}
    [Serializable] class Geometry {public Vector3[] vertices;public int[] triangles;}
    [Serializable] class Export {public Geometry body,socks;}
    [Serializable] class Baked {public Vector3[] basis,vertices;}
    class Vertex {
        public Vector3 p,n;public Vector4 t;public Vector2 uv;public BoneWeight w;
        public Vector3[] dp,dn,dt;
    }
    static BoneWeight BlendWeights(Vertex[] values) {
        var weights=new Dictionary<int,float>();
        foreach(var v in values) {
            int[] bones={v.w.boneIndex0,v.w.boneIndex1,v.w.boneIndex2,v.w.boneIndex3};
            float[] amounts={v.w.weight0,v.w.weight1,v.w.weight2,v.w.weight3};
            for(int j=0;j<4;j++) {
                if(!weights.ContainsKey(bones[j]))weights[bones[j]]=0;
                weights[bones[j]]+=amounts[j]/values.Length;
            }
        }
        var top=weights.OrderByDescending(k=>k.Value).Take(4).ToArray();
        int[] ids=new int[4];float[] ws=new float[4];float total=top.Sum(k=>k.Value);
        for(int i=0;i<top.Length;i++){ids[i]=top[i].Key;ws[i]=top[i].Value/total;}
        return new BoneWeight{boneIndex0=ids[0],boneIndex1=ids[1],boneIndex2=ids[2],boneIndex3=ids[3],
            weight0=ws[0],weight1=ws[1],weight2=ws[2],weight3=ws[3]};
    }
    static int Add(List<Vertex> vertices,params int[] indices) {
        var vs=indices.Select(i=>vertices[i]).ToArray();
        var v=new Vertex{w=BlendWeights(vs),dp=new Vector3[vs[0].dp.Length],dn=new Vector3[vs[0].dn.Length],dt=new Vector3[vs[0].dt.Length]};
        foreach(var a in vs){v.p+=a.p/vs.Length;v.n+=a.n/vs.Length;v.t+=a.t/vs.Length;v.uv+=a.uv/vs.Length;}
        foreach(var a in vs)for(int s=0;s<v.dp.Length;s++){v.dp[s]+=a.dp[s]/vs.Length;v.dn[s]+=a.dn[s]/vs.Length;v.dt[s]+=a.dt[s]/vs.Length;}
        v.n.Normalize();v.t.w=Mathf.Sign(v.t.w);
        // Round the midpoint using the source normals, avoiding a faceted bulge.
        if(vs.Length==2 && Thigh(v.p))v.p+=v.n*Mathf.Max(0,Vector3.Dot(vs[0].p-vs[1].p,vs[0].n-vs[1].n)/8);
        vertices.Add(v);return vertices.Count-1;
    }
    static Mesh SkinMesh(Mesh source,bool deform=true,string bakedPath=null) {
        var shapes=new List<Shape>();
        for(int s=0;s<source.blendShapeCount;s++)for(int f=0;f<source.GetBlendShapeFrameCount(s);f++) {
            var shape=new Shape{name=source.GetBlendShapeName(s),weight=source.GetBlendShapeFrameWeight(s,f),
                p=new Vector3[source.vertexCount],n=new Vector3[source.vertexCount],t=new Vector3[source.vertexCount]};
            source.GetBlendShapeFrameVertices(s,f,shape.p,shape.n,shape.t);shapes.Add(shape);
        }
        var vertices=new List<Vertex>();var positions=source.vertices;var normals=source.normals;
        var tangents=source.tangents;var uv=source.uv;var weights=source.boneWeights;
        for(int i=0;i<positions.Length;i++)vertices.Add(new Vertex{p=positions[i],n=normals[i],
            t=tangents.Length>i?tangents[i]:new Vector4(1,0,0,1),uv=uv[i],w=weights[i],
            dp=shapes.Select(s=>s.p[i]).ToArray(),dn=shapes.Select(s=>s.n[i]).ToArray(),dt=shapes.Select(s=>s.t[i]).ToArray()});
        var submeshes=Enumerable.Range(0,source.subMeshCount).Select(source.GetTriangles).ToArray();
        for(int iteration=0;iteration<2;iteration++) {
            var edges=new Dictionary<(int,int),int>();
            foreach(var triangles in submeshes)for(int i=0;i<triangles.Length;i+=3) {
                int[] tri={triangles[i],triangles[i+1],triangles[i+2]};
                if(!tri.All(a=>Thigh(vertices[a].p)))continue;
                for(int e=0;e<3;e++) {
                    int a=tri[e],b=tri[(e+1)%3];if(a>b){int swap=a;a=b;b=swap;}
                    if(Vector3.Distance(vertices[a].p,vertices[b].p)>.006f && !edges.ContainsKey((a,b)))edges[(a,b)]=Add(vertices,a,b);
                }
            }
            for(int s=0;s<submeshes.Length;s++) {
                var result=new List<int>();var triangles=submeshes[s];
                for(int i=0;i<triangles.Length;i+=3) {
                    int[] tri={triangles[i],triangles[i+1],triangles[i+2]};var polygon=new List<int>();
                    for(int e=0;e<3;e++) {
                        int a=tri[e],b=tri[(e+1)%3];polygon.Add(a);
                        if(edges.TryGetValue((Math.Min(a,b),Math.Max(a,b)),out int mid))polygon.Add(mid);
                    }
                    if(polygon.Count==3)result.AddRange(tri);
                    else {
                        int center=Add(vertices,tri);
                        for(int e=0;e<polygon.Count;e++)result.AddRange(new[]{polygon[e],polygon[(e+1)%polygon.Count],center});
                    }
                }
                submeshes[s]=result.ToArray();
            }
        }
        var colors=new Color[vertices.Count];var originalNormals=vertices.Select(v=>v.n).ToArray();
        var baked=string.IsNullOrEmpty(bakedPath)?null:JsonUtility.FromJson<Baked>(File.ReadAllText(bakedPath));
        if(baked!=null && (baked.basis.Length!=vertices.Count || baked.vertices.Length!=vertices.Count))throw new Exception("Plugin vertex count changed");
        float maxOut=0,maxIn=0;int changed=0;
        for(int i=0;i<vertices.Count;i++) {
            var v=vertices[i];if(!Thigh(v.p))continue;
            float pressure=Bell(v.p.y,.5588f,.014f);
            float displacement=-.0015f*pressure;
            if(baked!=null) {
                if(Vector3.Distance(baked.basis[i],v.p)>1e-6f || Vector3.Distance(baked.vertices[i],v.p)>.02f)throw new Exception("Plugin basis/order or displacement mismatch");
                displacement=Vector3.Dot(baked.vertices[i]-v.p,Radial(v.p));v.p=baked.vertices[i];
            } else if(deform) {
                for(int s=0;s<v.dp.Length;s++)v.dp[s]=Deform(v.p+v.dp[s])-Deform(v.p);
                v.p=Deform(v.p);
            }
            maxOut=Mathf.Max(maxOut,displacement);maxIn=Mathf.Min(maxIn,displacement);changed++;
            colors[i]=new Color(pressure*Mathf.SmoothStep(0,1,(v.p.y-.557f)/.010f),0,
                Mathf.SmoothStep(0,1,(v.p.y-.556f)/.012f)*(1-Mathf.SmoothStep(0,1,(v.p.y-.60f)/.024f)),1);
        }
        var mesh=new Mesh{name="Body_base_skin_pressure",indexFormat=source.indexFormat};
        mesh.vertices=vertices.Select(v=>v.p).ToArray();mesh.uv=vertices.Select(v=>v.uv).ToArray();
        mesh.normals=originalNormals;mesh.tangents=vertices.Select(v=>v.t).ToArray();mesh.colors=colors;
        mesh.boneWeights=vertices.Select(v=>v.w).ToArray();mesh.bindposes=source.bindposes;
        for(int s=0;s<shapes.Count;s++)mesh.AddBlendShapeFrame(shapes[s].name,shapes[s].weight,
            vertices.Select(v=>v.dp[s]).ToArray(),vertices.Select(v=>v.dn[s]).ToArray(),vertices.Select(v=>v.dt[s]).ToArray());
        mesh.subMeshCount=submeshes.Length;for(int s=0;s<submeshes.Length;s++)mesh.SetTriangles(submeshes[s],s);
        mesh.RecalculateNormals();var calculated=mesh.normals;
        var seamNormals=new Dictionary<Vector3,List<int>>();
        for(int i=0;i<vertices.Count;i++) {
            if(!Thigh(vertices[i].p)){calculated[i]=originalNormals[i];continue;}
            Vector3 p=vertices[i].p;var key=new Vector3(Mathf.Round(p.x*100000),Mathf.Round(p.y*100000),Mathf.Round(p.z*100000));
            if(!seamNormals.ContainsKey(key))seamNormals[key]=new List<int>();seamNormals[key].Add(i);
        }
        foreach(var seam in seamNormals.Values) {
            Vector3 normal=Vector3.zero;foreach(int i in seam)normal+=calculated[i];normal.Normalize();
            foreach(int i in seam)calculated[i]=normal;
        }
        mesh.normals=calculated;mesh.RecalculateBounds();
        if(mesh.blendShapeCount!=source.blendShapeCount || mesh.bindposes.Length!=source.bindposes.Length)throw new Exception("Skin binding preservation failed");
        var finalPositions=mesh.vertices;var finalWeights=mesh.boneWeights;
        for(int i=0;i<source.vertexCount;i++) {
            if(!Thigh(positions[i]) && (finalPositions[i]!=positions[i] || calculated[i]!=normals[i]))throw new Exception("Skin outside cuff changed");
            if(!finalWeights[i].Equals(weights[i]))throw new Exception("Original skin weights changed");
        }
        if(finalWeights.Any(w=>Mathf.Abs(w.weight0+w.weight1+w.weight2+w.weight3-1)>.0001f))throw new Exception("Invalid interpolated skin weights");
        Debug.Log("EKU_SKIN source="+(baked!=null?"Squeeze Tools":"local")+", vertices="+source.vertexCount+" -> "+mesh.vertexCount+", changed="+changed+", bulge="+maxOut+", compression="+maxIn+", blends="+mesh.blendShapeCount+", shapes="+string.Join(",",shapes.Select(s=>s.name)));
        return mesh;
    }
    static Mesh SockMesh(Mesh source) {
        var mesh=UnityEngine.Object.Instantiate(source);mesh.name="Knee_Socks_pressure_rim";var p=mesh.vertices;
        for(int i=0;i<p.Length;i++)if(p[i].y>.535f && Mathf.Abs(p[i].x)<.15f)
            p[i]-=Radial(p[i])*.0012f*Bell(p[i].y,.559f,.014f);
        mesh.vertices=p;mesh.RecalculateBounds();return mesh;
    }
    static void Save(UnityEngine.Object value,string path) {
        string originalName=value.name;
        var existing=AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
        if(existing){EditorUtility.CopySerialized(value,existing);UnityEngine.Object.DestroyImmediate(value);}
        else {AssetDatabase.CreateAsset(value,path);value.name=originalName;EditorUtility.SetDirty(value);}
    }
    public static void Build() {
        AssetDatabase.Refresh();EditorSceneManager.OpenScene("Assets/EkuStockingsComparison.unity");
        var args=Environment.GetCommandLineArgs();int outputArg=Array.IndexOf(args,"--skin-output");
        string output=Path.GetFullPath(outputArg>=0?args[outputArg+1]:"../../eku-unity-stockings-20261013/skin/player/EkuComparison.exe");
        if(Array.IndexOf(args,"--skin-baseline")>=0) {BuildPlayer("Assets/EkuStockingsComparison.unity",output);return;}
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        var body=comparison.avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(r=>r.name=="Body_base");
        var socks=comparison.avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(r=>r.name=="Knee_Socks");
        int exportArg=Array.IndexOf(args,"--skin-export");
        if(exportArg>=0) {
            var mesh=SkinMesh(body.sharedMesh,false);var rim=SockMesh(socks.sharedMesh);
            File.WriteAllText(args[exportArg+1],JsonUtility.ToJson(new Export{
                body=new Geometry{vertices=mesh.vertices,triangles=mesh.triangles},
                socks=new Geometry{vertices=rim.vertices,triangles=rim.triangles}}));return;
        }
        Debug.Log("EKU_SKIN_AUDIT body_bounds="+body.sharedMesh.bounds+", blends="+body.sharedMesh.blendShapeCount+", transform="+body.transform.localToWorldMatrix);
        if(!AssetDatabase.IsValidFolder(Folder))AssetDatabase.CreateFolder("Assets","EkuSkinCandidate");
        int bakedArg=Array.IndexOf(args,"--skin-baked");
        Save(SkinMesh(body.sharedMesh,true,bakedArg>=0?args[bakedArg+1]:null),Folder+"/Skin.asset");Save(SockMesh(socks.sharedMesh),Folder+"/SockRim.asset");
        body.sharedMesh=AssetDatabase.LoadAssetAtPath<Mesh>(Folder+"/Skin.asset");
        socks.sharedMesh=AssetDatabase.LoadAssetAtPath<Mesh>(Folder+"/SockRim.asset");
        string shader=File.ReadAllText("Packages/VRM/MToon/Shaders/MToon.shader")
            .Replace("Shader \"VRM/MToon\"","Shader \"Eku/Skin Pressure\"")
            .Replace("#include \"./MToonSM3.cginc\"","#define frag_forward EkuMToonFrag\n#include \"Packages/VRM/MToon/Shaders/MToonSM3.cginc\"\n#undef frag_forward\nfloat4 frag_forward(v2f i):SV_TARGET { float4 c=EkuMToonFrag(i); if(i.isOutline<.5) { float3 n=normalize(float3(i.tspace0.z,i.tspace1.z,i.tspace2.z)); float3 light=normalize(UNITY_MATRIX_V[2].xyz+UNITY_MATRIX_V[1].xyz*.7); c.rgb*=lerp(1,.975+.025*saturate(dot(n,light)),i.color.b); c.rgb*=1-i.color.r*float3(.012,.025,.022); } return c; }")
            .Replace("CustomEditor \"MToon.MToonInspector\"","");
        File.WriteAllText(Folder+"/Skin.shader",shader);AssetDatabase.ImportAsset(Folder+"/Skin.shader",ImportAssetOptions.ForceSynchronousImport);
        var skinShader=AssetDatabase.LoadAssetAtPath<Shader>(Folder+"/Skin.shader");
        var mats=body.sharedMaterials;
        for(int i=0;i<mats.Length;i++) {var mat=new Material(mats[i]);mat.shader=skinShader;Save(mat,Folder+"/Skin-"+i+".mat");mats[i]=AssetDatabase.LoadAssetAtPath<Material>(Folder+"/Skin-"+i+".mat");}
        body.sharedMaterials=mats;
        var style=comparison.avatar.GetComponent<EkuMaterialStyle>();style.slots.Single(s=>s.renderer==body).enhanced=mats;
        AssetDatabase.SaveAssets();string scene="Assets/EkuSkinComparison.unity";
        EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),scene);
        BuildPlayer(scene,output);
    }
    static void BuildPlayer(string scene,string output) {
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        var result=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{scene},locationPathName=output,
            target=BuildTarget.StandaloneWindows64,options=BuildOptions.None});
        if(result.summary.result!=BuildResult.Succeeded)throw new Exception("Skin build failed");
        Debug.Log("EKU_SKIN_BUILT "+output);
    }
}
