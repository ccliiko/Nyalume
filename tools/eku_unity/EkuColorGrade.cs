using UnityEngine;
using UnityEngine.Rendering;

[DefaultExecutionOrder(110)]
public class EkuColorGrade : MonoBehaviour {
    public Shader shader;
    Material grade;
    int style;
    void Start() {
        if(shader && shader.isSupported) grade=new Material(shader);
        SetStyle(style);
    }
    public void SetStyle(int value) {
        style=value;
        bool game=style==2 || style==3,original=style==4,vivid=style==1 || style==3;
        RenderSettings.ambientMode=AmbientMode.Flat;
        RenderSettings.ambientLight=(Color.white*(original?1:game?.35f:.6f)).gamma;
        foreach(var light in FindObjectsByType<Light>(FindObjectsSortMode.None)) {
            light.color=Color.white;
            if(light.name=="Key") {
                light.intensity=(game?1.25f:1)/Mathf.PI;
                light.transform.rotation=Quaternion.LookRotation(-new Vector3(0,1,-1).normalized);
            } else if(light.name=="Fill") {
                // Unity MToon halves ForwardAdd; Three uses .25 / pi directly.
                light.intensity=(original?1:game?.24f:.5f)/Mathf.PI;
                light.transform.rotation=Quaternion.LookRotation(-new Vector3(1,.3f,-1).normalized);
            } else if(light.name=="Rim") light.intensity=game?1.8f/Mathf.PI:0;
        }
        if(grade) {
            grade.SetFloat("_GradeEnabled",original?0:1);
            grade.SetFloat("_Contrast",vivid?1.12f:1.22f);
            grade.SetFloat("_Saturation",vivid?1.28f:1.45f);
            grade.SetFloat("_Brightness",vivid?1.03f:1.02f);
            var hue=Matrix4x4.identity;
            if(!vivid) {
                hue.SetRow(0,new Vector4(1.0133f,.0516f,-.0649f,0));
                hue.SetRow(1,new Vector4(-.0095f,.9895f,.0200f,0));
                hue.SetRow(2,new Vector4(.0544f,-.0481f,.9937f,0));
            }
            grade.SetMatrix("_Hue",hue);
        }
    }
    void OnRenderImage(RenderTexture source,RenderTexture destination) => Apply(source,destination);
    public void Apply(RenderTexture source,RenderTexture destination) {
        if(grade) Graphics.Blit(source,destination,grade,0);
        else Graphics.Blit(source,destination);
    }
    public void Downsample(RenderTexture source,RenderTexture destination) {
        if(grade) Graphics.Blit(source,destination,grade,1);
        else Graphics.Blit(source,destination);
    }
    void OnDestroy() {if(grade) Destroy(grade);}
}
