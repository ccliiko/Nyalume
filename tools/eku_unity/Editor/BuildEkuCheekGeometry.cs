using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

// Editor-only audit/derived assets. Never edits the purchased FBX or original face mesh.
public static class BuildEkuCheekGeometry {
    const float MinimumNormalCosine=.2f,MinimumAreaRatio=.25f;
    static readonly string[] Expressions={"eye_wink_1_L","eye_wink_1_R","eye_relax","mouth_smile_1","mouth_a_1"};
    [Serializable] public class CandidateReport {
        public string name;
        public int supportVertices,affectedTriangles,reductions,checkedTriangles,skippedDegenerateTriangles;
        public float amplitudeEyeSeparations,maximumWorldDelta,minimumNormalCosine=1,minimumAreaRatio=1;
        public bool passed;
    }
    [Serializable] public class GeometryReport {
        public string sourceMesh,derivedMesh,faceMaterial,variant;
        public int sourceVertices,sourceShapes,derivedShapes,mainComponentVertices,expressionCombinations,normalizedChannelCombinationsPerSide=27;
        public float eyeSeparation,maximumSkinningError,minimumAllowedNormalCosine=MinimumNormalCosine,
            minimumAllowedAreaRatio=MinimumAreaRatio;
        public bool originalDataPreserved;
        public string[] expressions=Expressions;
        public int[] expressionWeights={0,50,100},cheekWeights={50,100};
        public CandidateReport[] candidates,normalizedCombinations;
    }
    [Serializable] class SubmeshAudit {public string material;public int[] triangles;}
    [Serializable] class ShapeAudit {
        public string name;
        public int frames,moved,left,right;
        public float maximumWorldDelta;
        public Vector3[] selectedWorldDeltas;
    }
    [Serializable] class FaceAudit {
        public string scene,meshAsset,meshGuid;
        public int vertices,blendShapes;
        public Vector3 origin,right,up,forward;
        public float eyeSeparation,maximumSkinningError;
        public Vector3[] sourceVertices,worldVertices,faceCoordinates,worldNormals;
        public Vector2[] uv;
        public SubmeshAudit[] submeshes;
        public ShapeAudit[] shapes;
    }
    static string Argument(string name,string fallback="") {
        var args=Environment.GetCommandLineArgs();int i=Array.IndexOf(args,name);
        return i>=0 && i+1<args.Length?args[i+1]:fallback;
    }
    static Matrix4x4 SkinMatrix(BoneWeight weight,Matrix4x4[] bones) {
        var result=new Matrix4x4();
        for(int k=0;k<4;k++) {
            float amount=k==0?weight.weight0:k==1?weight.weight1:k==2?weight.weight2:weight.weight3;
            int index=k==0?weight.boneIndex0:k==1?weight.boneIndex1:k==2?weight.boneIndex2:weight.boneIndex3;
            if(amount<=0)continue;
            var bone=bones[index];
            for(int j=0;j<16;j++)result[j]+=bone[j]*amount;
        }
        return result;
    }
    static void Require(bool condition,string message) {if(!condition)throw new InvalidOperationException(message);}
    static int Root(int[] parents,int v) {
        while(parents[v]!=v) {parents[v]=parents[parents[v]];v=parents[v];}return v;
    }
    static bool[] MainComponent(int vertexCount,int[] triangles) {
        var parents=Enumerable.Range(0,vertexCount).ToArray();var used=new HashSet<int>(triangles);
        for(int i=0;i<triangles.Length;i+=3) {
            int a=Root(parents,triangles[i]);
            parents[Root(parents,triangles[i+1])]=a;parents[Root(parents,triangles[i+2])]=a;
        }
        var largest=used.GroupBy(v=>Root(parents,v)).OrderByDescending(g=>g.Count()).First();
        var result=new bool[vertexCount];foreach(int v in largest)result[v]=true;return result;
    }
    static float Smooth(float value) {value=Mathf.Clamp01(value);return value*value*(3-2*value);}
    static float Support(Vector3 point,int side,string variant) {
        if(variant=="soft-v1") {
            float sx=(point.x-side*.70f)/1.0f,sy=(point.y+.60f)/.62f;
            float softRadius=Mathf.Sqrt(sx*sx+sy*sy);
            float softValue=(softRadius<1?.5f+.5f*Mathf.Cos(Mathf.PI*softRadius):0)
                *Smooth((-.25f-point.y)/.40f)*Smooth((Mathf.Abs(point.x)-.38f)/.18f)
                *Smooth((point.z+.6f)/.8f)*Smooth((point.y+1.1f)/.5f);
            return softValue<1e-6f?0:softValue;
        }
        float x=(point.x-side*.65f)/.65f,y=(point.y+.60f)/.43f;
        float radius=Mathf.Sqrt(x*x+y*y),t=Mathf.Max(0,1-radius);
        float value=t*t*t*t*(4*radius+1)*Smooth((-.25f-point.y)/.20f)
            *Smooth((Mathf.Abs(point.x)-.38f)/.18f)*Smooth((point.z+.1f)/.3f);
        return value<1e-6f?0:value;
    }
    // Evaluate existing shape frames at actual Unity weights, including the implicit zero frame.
    static Vector3[] ExpressionDelta(Mesh mesh,string name,float weight) {
        int shape=mesh.GetBlendShapeIndex(name);Require(shape>=0,"Required expression missing: "+name);
        int frames=mesh.GetBlendShapeFrameCount(shape),hi=0;
        while(hi<frames-1 && mesh.GetBlendShapeFrameWeight(shape,hi)<weight)hi++;
        int lo=hi-1;
        if(hi==frames-1 && mesh.GetBlendShapeFrameWeight(shape,hi)<weight)lo=frames-2;
        var a=new Vector3[mesh.vertexCount];var b=new Vector3[mesh.vertexCount];float wa=0;
        if(lo>=0) {mesh.GetBlendShapeFrameVertices(shape,lo,a,null,null);wa=mesh.GetBlendShapeFrameWeight(shape,lo);}
        mesh.GetBlendShapeFrameVertices(shape,hi,b,null,null);float wb=mesh.GetBlendShapeFrameWeight(shape,hi);
        Require(wb>wa,"Positive ascending expression frames required: "+name);
        float fraction=(weight-wa)/(wb-wa);
        for(int v=0;v<a.Length;v++)a[v]=Vector3.LerpUnclamped(a[v],b[v],fraction);
        return a;
    }
    static List<Vector3[]> ExpressionBases(Mesh mesh,Vector3[] neutral,Matrix4x4[] matrices) {
        var result=new List<Vector3[]>{neutral};
        foreach(string expression in Expressions) {
            int count=result.Count;var half=ExpressionDelta(mesh,expression,50);var full=ExpressionDelta(mesh,expression,100);
            for(int state=0;state<count;state++) {
                var a=new Vector3[neutral.Length];var b=new Vector3[neutral.Length];
                for(int v=0;v<a.Length;v++) {
                    a[v]=result[state][v]+matrices[v].MultiplyVector(half[v]);
                    b[v]=result[state][v]+matrices[v].MultiplyVector(full[v]);
                }
                result.Add(a);result.Add(b);
            }
        }
        return result;
    }
    static CandidateReport ValidateCandidate(string name,Vector3[] delta,int[] triangles,List<Vector3[]> bases,float eyeSeparation) {
        var report=new CandidateReport{name=name};
        float epsilon=eyeSeparation*eyeSeparation*1e-8f;
        foreach(var vertices in bases)for(int i=0;i<triangles.Length;i+=3) {
            int a=triangles[i],b=triangles[i+1],c=triangles[i+2];
            var before=Vector3.Cross(vertices[b]-vertices[a],vertices[c]-vertices[a]);float area=before.magnitude;
            if(area<=epsilon) {report.skippedDegenerateTriangles++;continue;}
            for(int w=1;w<=2;w++) {
                float weight=w*.5f;
                var after=Vector3.Cross(vertices[b]+delta[b]*weight-vertices[a]-delta[a]*weight,
                    vertices[c]+delta[c]*weight-vertices[a]-delta[a]*weight);
                float newArea=after.magnitude;
                float cosine=newArea>epsilon?Vector3.Dot(before,after)/(area*newArea):-1;
                report.minimumNormalCosine=Mathf.Min(report.minimumNormalCosine,cosine);
                report.minimumAreaRatio=Mathf.Min(report.minimumAreaRatio,newArea/area);report.checkedTriangles++;
            }
        }
        report.passed=report.checkedTriangles>0 && report.minimumNormalCosine>=MinimumNormalCosine && report.minimumAreaRatio>=MinimumAreaRatio;
        return report;
    }
    static string ShapeName(CheekDeformationProfile profile,EkuCheekSide side,EkuCheekChannel channel) {
        var matches=profile && profile.bindings!=null?profile.bindings.Where(b=>b!=null && b.side==side && b.channel==channel).ToArray():Array.Empty<EkuCheekBinding>();
        Require(matches.Length<=1,"Duplicate cheek profile binding: "+side+" "+channel);
        string name=matches.Length==1?matches[0].shape:"cheek_"+channel.ToString().ToLowerInvariant()+"_"+(side==EkuCheekSide.Left?"L":"R");
        Require(!string.IsNullOrWhiteSpace(name),"Empty geometric shape binding");return name;
    }
    static CandidateReport ValidateNormalizedCombinations(EkuCheekSide side,List<Vector3[]> channels,int[] triangles,List<Vector3[]> bases,float eyeSeparation) {
        var report=new CandidateReport{name="normalized_mix_"+(side==EkuCheekSide.Left?"L":"R"),passed=true};
        var delta=new Vector3[channels[0].Length];
        foreach(float pressure in new[]{0,.5f,1})for(int x=-1;x<=1;x++)for(int y=-1;y<=1;y++) {
            float total=Mathf.Max(1,pressure+Mathf.Abs(x)+Mathf.Abs(y));
            for(int v=0;v<delta.Length;v++)delta[v]=(channels[0][v]*pressure
                +(x==0?Vector3.zero:channels[x<0?2:1][v])+(y==0?Vector3.zero:channels[y<0?4:3][v]))/total;
            var check=ValidateCandidate(report.name,delta,triangles,bases,eyeSeparation);
            report.minimumNormalCosine=Mathf.Min(report.minimumNormalCosine,check.minimumNormalCosine);
            report.minimumAreaRatio=Mathf.Min(report.minimumAreaRatio,check.minimumAreaRatio);
            report.checkedTriangles+=check.checkedTriangles;report.skippedDegenerateTriangles+=check.skippedDegenerateTriangles;report.passed&=check.passed;
        }
        Require(report.passed,"Normalized pressure/drag combinations unsafe for "+side);return report;
    }
    static void AssertPreserved(Mesh source,Mesh result) {
        Require(source.vertices.SequenceEqual(result.vertices) && source.normals.SequenceEqual(result.normals)
            && source.tangents.SequenceEqual(result.tangents) && source.colors32.SequenceEqual(result.colors32),"Base vertex data changed");
        Require(source.bindposes.SequenceEqual(result.bindposes) && source.boneWeights.SequenceEqual(result.boneWeights),"Skin data changed");
        Require(source.subMeshCount==result.subMeshCount && source.indexFormat==result.indexFormat,"Submesh layout changed");
        for(int s=0;s<source.subMeshCount;s++)Require(source.GetSubMesh(s).Equals(result.GetSubMesh(s))
            && source.GetIndices(s).SequenceEqual(result.GetIndices(s)),"Submesh data changed: "+s);
        for(int channel=0;channel<8;channel++) {
            var a=new List<Vector4>();var b=new List<Vector4>();source.GetUVs(channel,a);result.GetUVs(channel,b);
            Require(a.SequenceEqual(b),"UV channel changed: "+channel);
        }
        var av=new Vector3[source.vertexCount];var an=new Vector3[source.vertexCount];var at=new Vector3[source.vertexCount];
        var bv=new Vector3[source.vertexCount];var bn=new Vector3[source.vertexCount];var bt=new Vector3[source.vertexCount];
        for(int s=0;s<source.blendShapeCount;s++) {
            Require(source.GetBlendShapeName(s)==result.GetBlendShapeName(s) && source.GetBlendShapeFrameCount(s)==result.GetBlendShapeFrameCount(s),"Original shape index/name/frame changed: "+s);
            for(int f=0;f<source.GetBlendShapeFrameCount(s);f++) {
                source.GetBlendShapeFrameVertices(s,f,av,an,at);result.GetBlendShapeFrameVertices(s,f,bv,bn,bt);
                Require(source.GetBlendShapeFrameWeight(s,f).Equals(result.GetBlendShapeFrameWeight(s,f))
                    && av.SequenceEqual(bv) && an.SequenceEqual(bn) && at.SequenceEqual(bt),"Original frame data changed: "+s+"/"+f);
            }
        }
    }
    // In-memory only. Caller owns the returned clone; no scene/asset writes and all renderer weights are restored.
    public static Mesh CreateDerivedMesh(GameObject avatar,Mesh original,CheekDeformationProfile profile,out GeometryReport report,string variant="original") {
        Require(avatar && original && original.isReadable,"Readable avatar Body mesh required");
        Require(variant=="original" || variant=="soft-v1","Unknown geometry variant");
        var skin=avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(s=>s.name=="Body");
        var previousMesh=skin.sharedMesh;
        Require(previousMesh && previousMesh.vertexCount==original.vertexCount,"Matching source Body topology required");
        Require(EkuCheekFrame.TryCreate(avatar.GetComponent<Animator>(),out var frame),"Anatomical eye/head frame missing");
        int count=original.vertexCount;var source=original.vertices;var weights=original.boneWeights;var bindposes=original.bindposes;
        Require(weights.Length==count && bindposes.Length==skin.bones.Length,"Four-influence skin data/bindposes required");
        var bones=new Matrix4x4[skin.bones.Length];for(int i=0;i<bones.Length;i++)bones[i]=skin.bones[i].localToWorldMatrix*bindposes[i];
        var matrices=new Matrix4x4[count];var inverse=new Matrix4x4[count];
        for(int v=0;v<count;v++) {
            matrices[v]=SkinMatrix(weights[v],bones);Require(Mathf.Abs(matrices[v].determinant)>1e-10f,"Singular skin matrix at "+v);
            inverse[v]=matrices[v].inverse;
        }
        var materials=skin.sharedMaterials;int faceSubmesh=Array.FindIndex(materials,m=>m && m.name.StartsWith("E_Face_mtoon",StringComparison.Ordinal));
        Require(faceSubmesh>=0 && faceSubmesh<original.subMeshCount,"Opaque E_Face_mtoon submesh missing");
        var triangles=original.GetTriangles(faceSubmesh);var main=MainComponent(count,triangles);
        var savedWeights=new float[previousMesh.blendShapeCount];for(int s=0;s<savedWeights.Length;s++)savedWeights[s]=skin.GetBlendShapeWeight(s);
        var baked=new Mesh();Mesh result=null,scratch=null;
        report=new GeometryReport{variant=variant,sourceMesh=AssetDatabase.GetAssetPath(original),sourceVertices=count,sourceShapes=original.blendShapeCount,
            eyeSeparation=frame.eyeSeparation,faceMaterial=materials[faceSubmesh].name,mainComponentVertices=main.Count(v=>v)};
        try {
            skin.sharedMesh=original;
            for(int s=0;s<original.blendShapeCount;s++)skin.SetBlendShapeWeight(s,0);
            skin.BakeMesh(baked);var bv=baked.vertices;var bn=baked.normals;
            Require(bn.Length==count,"Baked normals required");
            var world=new Vector3[count];var normal=new Vector3[count];var coordinates=new Vector3[count];
            for(int v=0;v<count;v++) {
                world[v]=skin.transform.TransformPoint(bv[v]);normal[v]=skin.transform.TransformDirection(bn[v]).normalized;
                var offset=world[v]-frame.origin;
                coordinates[v]=new Vector3(Vector3.Dot(offset,frame.right),Vector3.Dot(offset,frame.up),Vector3.Dot(offset,frame.forward))/frame.eyeSeparation;
                report.maximumSkinningError=Mathf.Max(report.maximumSkinningError,Vector3.Distance(world[v],matrices[v].MultiplyPoint3x4(source[v])));
            }
            Require(report.maximumSkinningError<frame.eyeSeparation*1e-4f,"BakeMesh and skin matrices disagree");
            var bases=ExpressionBases(original,world,matrices);report.expressionCombinations=bases.Count;
            result=UnityEngine.Object.Instantiate(original);result.name=original.name+"_CheekDerived_"+variant;
            scratch=UnityEngine.Object.Instantiate(original);scratch.ClearBlendShapes();scratch.RecalculateNormals();
            bool tangents=original.tangents.Length==count && original.uv.Length==count;
            if(tangents)scratch.RecalculateTangents();var baseNormals=scratch.normals;var baseTangents=scratch.tangents;
            var candidates=new List<CandidateReport>();var mixes=new List<CandidateReport>();var names=new HashSet<string>();
            foreach(var side in new[]{EkuCheekSide.Left,EkuCheekSide.Right}) {
                int sign=side==EkuCheekSide.Left?-1:1;var mask=new float[count];
                for(int v=0;v<count;v++)if(main[v])mask[v]=Support(coordinates[v],sign,variant);
                int support=mask.Count(m=>m>0);Require(support>=20,"Insufficient cheek support: "+side);
                var affected=new List<int>();for(int t=0;t<triangles.Length;t+=3)
                    if(mask[triangles[t]]>0 || mask[triangles[t+1]]>0 || mask[triangles[t+2]]>0)affected.AddRange(new[]{triangles[t],triangles[t+1],triangles[t+2]});
                int[] affectedTriangles=affected.ToArray();var channels=new List<Vector3[]>();
                foreach(var channel in new[]{EkuCheekChannel.Press,EkuCheekChannel.Pull,EkuCheekChannel.Push,EkuCheekChannel.Up,EkuCheekChannel.Down}) {
                    string name=ShapeName(profile,side,channel);Require(names.Add(name) && original.GetBlendShapeIndex(name)<0,"Shape already exists or binding collides: "+name);
                    float amplitude=variant=="soft-v1"?(channel==EkuCheekChannel.Press?.14f:channel==EkuCheekChannel.Pull?.16f:channel==EkuCheekChannel.Push?.105f:.12f):
                        channel==EkuCheekChannel.Pull?.20f:channel==EkuCheekChannel.Push?.09f:.14f;
                    var delta=new Vector3[count];CandidateReport candidate=null;
                    for(int reduction=0;reduction<=20;reduction++) {
                        for(int v=0;v<count;v++) {
                            var direction=channel==EkuCheekChannel.Press?-normal[v]:channel==EkuCheekChannel.Pull?frame.right*sign:
                                channel==EkuCheekChannel.Push?-frame.right*sign:channel==EkuCheekChannel.Up?frame.up:-frame.up;
                            delta[v]=direction*(mask[v]*amplitude*frame.eyeSeparation);
                        }
                        candidate=ValidateCandidate(name,delta,affectedTriangles,bases,frame.eyeSeparation);candidate.reductions=reduction;
                        if(candidate.passed)break;amplitude*=.85f;
                    }
                    Require(candidate.passed,"No safe candidate amplitude for "+name);
                    candidate.amplitudeEyeSeparations=amplitude;candidate.supportVertices=support;candidate.affectedTriangles=affectedTriangles.Length/3;
                    var localDelta=new Vector3[count];var deformed=new Vector3[count];
                    for(int v=0;v<count;v++) {localDelta[v]=inverse[v].MultiplyVector(delta[v]);deformed[v]=source[v]+localDelta[v];candidate.maximumWorldDelta=Mathf.Max(candidate.maximumWorldDelta,delta[v].magnitude);}
                    scratch.vertices=deformed;scratch.RecalculateNormals();if(tangents)scratch.RecalculateTangents();
                    var normals=scratch.normals;var normalDelta=new Vector3[count];var tangentDelta=new Vector3[count];var newTangents=scratch.tangents;
                    for(int v=0;v<count;v++) {
                        normalDelta[v]=normals[v]-baseNormals[v];
                        if(tangents)tangentDelta[v]=new Vector3(newTangents[v].x-baseTangents[v].x,newTangents[v].y-baseTangents[v].y,newTangents[v].z-baseTangents[v].z);
                    }
                    result.AddBlendShapeFrame(name,100,localDelta,normalDelta,tangentDelta);candidates.Add(candidate);channels.Add(delta);
                }
                var mix=ValidateNormalizedCombinations(side,channels,affectedTriangles,bases,frame.eyeSeparation);
                mix.supportVertices=support;mix.affectedTriangles=affectedTriangles.Length/3;mixes.Add(mix);
            }
            Require(result.blendShapeCount==original.blendShapeCount+10,"Ten appended shapes required");
            AssertPreserved(original,result);report.originalDataPreserved=true;report.derivedShapes=result.blendShapeCount;
            report.candidates=candidates.ToArray();report.normalizedCombinations=mixes.ToArray();
            return result;
        } catch {if(result)UnityEngine.Object.DestroyImmediate(result);throw;}
        finally {
            skin.sharedMesh=previousMesh;
            for(int s=0;s<savedWeights.Length;s++)skin.SetBlendShapeWeight(s,savedWeights[s]);
            UnityEngine.Object.DestroyImmediate(baked);if(scratch)UnityEngine.Object.DestroyImmediate(scratch);
        }
    }
    public static void Derive() {DeriveVariant("original",false);}
    public static void DeriveSoft() {DeriveVariant("soft-v1",true);}
    static void DeriveVariant(string variant,bool explicitSource) {
        string scene=Argument("--cheek-scene","Assets/EkuWardrobe.unity"),output=Argument("--cheek-mesh-output").Replace('\\','/');
        string reportPath=Argument("--cheek-geometry-report"),profilePath=Argument("--cheek-profile"),sourcePath=Argument("--cheek-source-mesh");
        Require(output.StartsWith("Assets/",StringComparison.Ordinal) && output.EndsWith(".asset",StringComparison.OrdinalIgnoreCase)
            && !output.Split('/').Contains(".."),"Explicit --cheek-mesh-output Assets/.../*.asset required");
        Require(!string.IsNullOrWhiteSpace(reportPath),"--cheek-geometry-report required");
        Require(!File.Exists(output) && !AssetDatabase.LoadMainAssetAtPath(output),"Refusing to overwrite existing mesh asset: "+output);
        var profile=string.IsNullOrWhiteSpace(profilePath)?null:AssetDatabase.LoadAssetAtPath<CheekDeformationProfile>(profilePath);
        Require(string.IsNullOrWhiteSpace(profilePath) || profile,"Cheek profile asset missing: "+profilePath);
        EditorSceneManager.OpenScene(scene);
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();Require(comparison && comparison.avatar,"Saved Eku scene required");
        Require(!explicitSource || !string.IsNullOrWhiteSpace(sourcePath),"Soft candidate requires explicit immutable --cheek-source-mesh (original 109 shapes)");
        var original=string.IsNullOrWhiteSpace(sourcePath)?comparison.avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(s=>s.name=="Body").sharedMesh:
            AssetDatabase.LoadAssetAtPath<Mesh>(sourcePath);
        Require(original && (!explicitSource || original.blendShapeCount==109),"Original readable 109-shape source asset required; do not stack derived shapes");
        var derived=CreateDerivedMesh(comparison.avatar.gameObject,original,profile,out var report,variant);
        try {
            string folder=Path.GetDirectoryName(output).Replace('\\','/'),parent="Assets";
            foreach(string part in folder.Split('/').Skip(1)) {string next=parent+"/"+part;if(!AssetDatabase.IsValidFolder(next))AssetDatabase.CreateFolder(parent,part);parent=next;}
            AssetDatabase.CreateAsset(derived,output);AssetDatabase.SaveAssetIfDirty(derived);report.derivedMesh=output;
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(reportPath)));File.WriteAllText(reportPath,JsonUtility.ToJson(report,true));
            Debug.Log("EKU_CHEEK_GEOMETRY_DERIVED "+output+" shapes="+report.derivedShapes+" originalDataPreserved="+report.originalDataPreserved+" report="+reportPath);
        } catch {if(!AssetDatabase.Contains(derived))UnityEngine.Object.DestroyImmediate(derived);throw;}
    }
    public static void Audit() {
        string scene=Argument("--cheek-scene","Assets/EkuWardrobe.unity");
        string output=Argument("--cheek-audit-report");
        if(string.IsNullOrWhiteSpace(output))throw new Exception("--cheek-audit-report required");
        EditorSceneManager.OpenScene(scene);
        var comparison=UnityEngine.Object.FindFirstObjectByType<EkuComparison>();
        if(!comparison || !comparison.avatar)throw new Exception("Saved Eku scene required");
        var rig=comparison.avatar.GetComponent<Animator>();
        if(!EkuCheekFrame.TryCreate(rig,out var frame))throw new Exception("Anatomical eye/head frame missing");
        var skin=comparison.avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Single(s=>s.name=="Body");
        var mesh=skin.sharedMesh;var vertices=mesh.vertices;var weights=mesh.boneWeights;
        var bones=new Matrix4x4[skin.bones.Length];var bindposes=mesh.bindposes;
        for(int i=0;i<bones.Length;i++)bones[i]=skin.bones[i].localToWorldMatrix*bindposes[i];
        var matrices=new Matrix4x4[vertices.Length];
        var baked=new Mesh();
        try {
            skin.BakeMesh(baked);var bv=baked.vertices;var bn=baked.normals;
            string asset=AssetDatabase.GetAssetPath(mesh);
            var report=new FaceAudit{scene=scene,meshAsset=asset,meshGuid=AssetDatabase.AssetPathToGUID(asset),
                vertices=vertices.Length,blendShapes=mesh.blendShapeCount,origin=frame.origin,right=frame.right,
                up=frame.up,forward=frame.forward,eyeSeparation=frame.eyeSeparation,
                sourceVertices=vertices,worldVertices=new Vector3[vertices.Length],faceCoordinates=new Vector3[vertices.Length],
                worldNormals=new Vector3[vertices.Length],uv=mesh.uv,submeshes=new SubmeshAudit[mesh.subMeshCount]};
            for(int i=0;i<vertices.Length;i++) {
                matrices[i]=SkinMatrix(weights[i],bones);
                var world=skin.transform.TransformPoint(bv[i]);var d=world-frame.origin;
                report.worldVertices[i]=world;
                report.faceCoordinates[i]=new Vector3(Vector3.Dot(d,frame.right),Vector3.Dot(d,frame.up),Vector3.Dot(d,frame.forward))/frame.eyeSeparation;
                report.worldNormals[i]=skin.transform.TransformDirection(bn[i]).normalized;
                report.maximumSkinningError=Mathf.Max(report.maximumSkinningError,Vector3.Distance(world,matrices[i].MultiplyPoint3x4(vertices[i])));
            }
            var materials=skin.sharedMaterials;
            for(int i=0;i<report.submeshes.Length;i++)report.submeshes[i]=new SubmeshAudit{
                material=i<materials.Length && materials[i]?materials[i].name:"",triangles=mesh.GetTriangles(i)};
            var selected=new HashSet<string>{"face_cheek_puku","face_cheek_puku_R","eye_wink_1_L","eye_wink_1_R","eye_relax","mouth_a_1","mouth_smile_1"};
            var shapes=new List<ShapeAudit>();var delta=new Vector3[vertices.Length];
            for(int i=0;i<mesh.blendShapeCount;i++) {
                var shape=new ShapeAudit{name=mesh.GetBlendShapeName(i),frames=mesh.GetBlendShapeFrameCount(i)};
                mesh.GetBlendShapeFrameVertices(i,0,delta,null,null);
                if(selected.Contains(shape.name))shape.selectedWorldDeltas=new Vector3[vertices.Length];
                for(int v=0;v<vertices.Length;v++) {
                    var world=matrices[v].MultiplyVector(delta[v]);
                    if(shape.selectedWorldDeltas!=null)shape.selectedWorldDeltas[v]=world;
                    shape.maximumWorldDelta=Mathf.Max(shape.maximumWorldDelta,world.magnitude);
                    if(world.sqrMagnitude<1e-12f)continue;
                    shape.moved++;if(report.faceCoordinates[v].x<0)shape.left++;else shape.right++;
                }
                shapes.Add(shape);
            }
            report.shapes=shapes.ToArray();
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output)));
            File.WriteAllText(output,JsonUtility.ToJson(report));
            Debug.Log("EKU_CHEEK_GEOMETRY_AUDIT vertices="+report.vertices+" shapes="+report.blendShapes+
                " eyeSeparation="+report.eyeSeparation+" skinningError="+report.maximumSkinningError+" report="+output);
        } finally {UnityEngine.Object.DestroyImmediate(baked);}
    }
}
