using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

// Foot-only repairs. The calf/cuff geometry, colors and authored fabric maps stay intact.
public static class BuildEkuSockFoot {
    const float Ankle=.14f;
    public static Mesh Restore(Mesh original,Vector3[] heels) {
        var mesh=UnityEngine.Object.Instantiate(original);
        var vertices=mesh.vertices;var originalNormals=mesh.normals;var originalTangents=mesh.tangents;
        for(int i=0;i<heels.Length;i++)vertices[i]-=heels[i];
        mesh.vertices=vertices;mesh.RecalculateNormals();
        var normals=mesh.normals;
        // RecalculateNormals splits UV seams; weld only the coincident foot normals.
        var groups=new Dictionary<Vector3Int,List<int>>();
        for(int i=0;i<vertices.Length;i++)if(vertices[i].y<Ankle) {
            var key=Vector3Int.RoundToInt(vertices[i]*100000);
            if(!groups.TryGetValue(key,out var group))groups[key]=group=new List<int>();
            group.Add(i);
        } else normals[i]=originalNormals[i];
        foreach(var group in groups.Values) {
            var normal=group.Aggregate(Vector3.zero,(sum,i)=>sum+normals[i]).normalized;
            foreach(int i in group)normals[i]=normal;
        }
        mesh.normals=normals;mesh.RecalculateTangents();var tangents=mesh.tangents;
        var normalDelta=new Vector3[vertices.Length];var tangentDelta=new Vector3[vertices.Length];
        var fullHeels=new Vector3[vertices.Length];Array.Copy(heels,fullHeels,heels.Length);
        int changed=0;
        for(int i=0;i<vertices.Length;i++) {
            if(vertices[i].y>=Ankle) {normals[i]=originalNormals[i];tangents[i]=originalTangents[i];}
            else {
                if(Vector3.Dot(normals[i],originalNormals[i])<.999f)changed++;
                // Preserve handedness while morphing the normal/tangent basis with the heel.
                tangents[i].w=originalTangents[i].w;
            }
            normalDelta[i]=originalNormals[i]-normals[i];
            tangentDelta[i]=(Vector3)originalTangents[i]-(Vector3)tangents[i];
        }
        mesh.normals=normals;mesh.tangents=tangents;
        mesh.AddBlendShapeFrame("Foot_heels",100,fullHeels,normalDelta,tangentDelta);
        mesh.RecalculateBounds();
        Debug.Log("EKU_SOCK_FOOT_NORMALS repaired="+changed);
        return mesh;
    }
    static Vector3 Closest(Vector3 p,Vector3 a,Vector3 b,Vector3 c,out Vector3 weights) {
        var ab=b-a;var ac=c-a;var ap=p-a;float d1=Vector3.Dot(ab,ap),d2=Vector3.Dot(ac,ap);
        if(d1<=0 && d2<=0) {weights=Vector3.right;return a;}
        var bp=p-b;float d3=Vector3.Dot(ab,bp),d4=Vector3.Dot(ac,bp);
        if(d3>=0 && d4<=d3) {weights=Vector3.up;return b;}
        float vc=d1*d4-d3*d2;
        if(vc<=0 && d1>=0 && d3<=0) {float v=d1/(d1-d3);weights=new Vector3(1-v,v,0);return a+v*ab;}
        var cp=p-c;float d5=Vector3.Dot(ab,cp),d6=Vector3.Dot(ac,cp);
        if(d6>=0 && d5<=d6) {weights=Vector3.forward;return c;}
        float vb=d5*d2-d1*d6;
        if(vb<=0 && d2>=0 && d6<=0) {float w=d2/(d2-d6);weights=new Vector3(1-w,0,w);return a+w*ac;}
        float va=d3*d6-d5*d4;
        if(va<=0 && d4-d3>=0 && d5-d6>=0) {float w=(d4-d3)/(d4-d3+d5-d6);weights=new Vector3(0,1-w,w);return b+w*(c-b);}
        float inverse=1/(va+vb+vc);float bv=vb*inverse,cw=vc*inverse;
        weights=new Vector3(1-bv-cw,bv,cw);return a+ab*bv+ac*cw;
    }
    public static void BakeMaps(Mesh socks,Mesh body,Texture2D skin,EkuStockings stockings) {
        var previous=stockings.skin;
        var textures=new[]{skin,previous,stockings.fabric,stockings.density}.Distinct().Select(t=>(TextureImporter)AssetImporter.GetAtPath(AssetDatabase.GetAssetPath(t))).ToArray();
        var readable=textures.Select(t=>t.isReadable).ToArray();
        try {
            foreach(var importer in textures)if(!importer.isReadable) {importer.isReadable=true;importer.SaveAndReimport();}
            var pixels=previous.GetPixels();int width=previous.width,height=previous.height;
            var fibers=stockings.fabric.GetPixels();var density=stockings.density.GetPixels();
            if(fibers.Length!=pixels.Length || density.Length!=pixels.Length)throw new Exception("Stocking map dimensions differ");
            var plainFiber=stockings.fabric.GetPixel(0,0);var plainDensity=stockings.density.GetPixel(0,0);
            var vertices=body.vertices;var uv=body.uv;var indices=body.triangles;
            var left=new List<int>();var right=new List<int>();
            for(int i=0;i<indices.Length;i+=3) {
                var a=vertices[indices[i]];var b=vertices[indices[i+1]];var c=vertices[indices[i+2]];
                if(Mathf.Min(a.y,Mathf.Min(b.y,c.y))>Ankle || Vector3.Cross(b-a,c-a).sqrMagnitude<1e-16f)continue;
                ((a.x+b.x+c.x)<0?left:right).Add(i);
            }
            var positions=socks.vertices;var coords=socks.uv;var triangles=socks.triangles;var changed=new HashSet<int>();
            for(int i=0;i<triangles.Length;i+=3) {
                int a=triangles[i],b=triangles[i+1],c=triangles[i+2];
                if(Mathf.Min(positions[a].y,Mathf.Min(positions[b].y,positions[c].y))>=Ankle)continue;
                var u=Vector2.Scale(coords[a],new Vector2(width,height));
                var v=Vector2.Scale(coords[b],new Vector2(width,height));var w=Vector2.Scale(coords[c],new Vector2(width,height));
                float denominator=(v.y-w.y)*(u.x-w.x)+(w.x-v.x)*(u.y-w.y);
                if(Mathf.Abs(denominator)<1e-8f)continue;
                int minX=Mathf.Max(0,Mathf.FloorToInt(Mathf.Min(u.x,Mathf.Min(v.x,w.x))));
                int maxX=Mathf.Min(width-1,Mathf.CeilToInt(Mathf.Max(u.x,Mathf.Max(v.x,w.x))));
                int minY=Mathf.Max(0,Mathf.FloorToInt(Mathf.Min(u.y,Mathf.Min(v.y,w.y))));
                int maxY=Mathf.Min(height-1,Mathf.CeilToInt(Mathf.Max(u.y,Mathf.Max(v.y,w.y))));
                for(int y=minY;y<=maxY;y++)for(int x=minX;x<=maxX;x++) {
                    float wa=((v.y-w.y)*(x+.5f-w.x)+(w.x-v.x)*(y+.5f-w.y))/denominator;
                    float wb=((w.y-u.y)*(x+.5f-w.x)+(u.x-w.x)*(y+.5f-w.y))/denominator;
                    float wc=1-wa-wb;if(wa<0 || wb<0 || wc<0)continue;
                    var point=positions[a]*wa+positions[b]*wb+positions[c]*wc;
                    if(point.y>=Ankle)continue;
                    float distance=float.PositiveInfinity;Vector2 target=Vector2.zero;
                    foreach(int face in point.x<0?left:right) {
                        int ia=indices[face],ib=indices[face+1],ic=indices[face+2];
                        var hit=Closest(point,vertices[ia],vertices[ib],vertices[ic],out var weight);
                        float error=(hit-point).sqrMagnitude;
                        if(error<distance) {distance=error;target=uv[ia]*weight.x+uv[ib]*weight.y+uv[ic]*weight.z;}
                    }
                    if(float.IsInfinity(distance))throw new Exception("Restored foot skin projection failed");
                    int pixel=y*width+x;
                    float amount=1-Mathf.SmoothStep(0,1,(point.y-.10f)/.04f);
                    pixels[pixel]=Color.Lerp(pixels[pixel],skin.GetPixelBilinear(target.x,target.y),amount);
                    // The old rectangular ornament mask also covered the sole UV islands.
                    // Foot fabric has no bows/lace: restore the ordinary stocking density.
                    fibers[pixel]=Color.Lerp(fibers[pixel],plainFiber,amount);
                    density[pixel]=Color.Lerp(density[pixel],plainDensity,amount);
                    changed.Add(pixel);
                }
            }
            stockings.skin=SaveMap("FootSkin",width,height,pixels,true);
            stockings.fabric=SaveMap("FootFabric",width,height,fibers,true);
            stockings.density=SaveMap("FootDensity",width,height,density,false);
            Debug.Log("EKU_SOCK_FOOT_MAPS pixels="+changed.Count+", sole density="+plainDensity.r);
        } finally {
            for(int i=0;i<textures.Length;i++)if(textures[i].isReadable!=readable[i]) {textures[i].isReadable=readable[i];textures[i].SaveAndReimport();}
        }
    }
    static Texture2D SaveMap(string name,int width,int height,Color[] pixels,bool srgb) {
        var result=new Texture2D(width,height,TextureFormat.RGBA32,false);result.SetPixels(pixels);result.Apply();
        string path="Assets/EkuWardrobe/"+name+".png";
        File.WriteAllBytes(path,result.EncodeToPNG());UnityEngine.Object.DestroyImmediate(result);
        AssetDatabase.ImportAsset(path,ImportAssetOptions.ForceSynchronousImport);
        var output=(TextureImporter)AssetImporter.GetAtPath(path);output.sRGBTexture=srgb;output.mipmapEnabled=true;
        output.textureCompression=TextureImporterCompression.Uncompressed;output.filterMode=FilterMode.Bilinear;output.SaveAndReimport();
        return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
    }
}
