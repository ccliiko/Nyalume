Shader "Eku/White Stockings" {
    Properties {
        _MainTex ("Fiber (sRGB)", 2D) = "white" {}
        _SkinTex ("Skin (sRGB)", 2D) = "white" {}
        _DensityTex ("Density (linear)", 2D) = "white" {}
        _BumpMap ("Weave normal", 2D) = "bump" {}
        _Exposure ("Exposure", Range(0.2,3)) = 1
        _DebugView ("Diagnostic view", Float) = 0
        _WeaveStrength ("Weave normal strength", Range(0,1)) = .6
        _Silk ("Silk candidate", Float) = 0
        _SilkAmbient ("Silk ambient", Range(0,1)) = .28
        _SilkSpecular ("Silk soft highlight", Range(0,1)) = .26
    }
    SubShader {
        Tags { "RenderType"="Opaque" "Queue"="Geometry" }
        Cull Back ZWrite On
        Pass {
            Tags { "LightMode"="ForwardBase" }
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma target 3.0
            #include "UnityCG.cginc"
            sampler2D _MainTex, _SkinTex, _DensityTex, _BumpMap;
            float _Exposure, _DebugView, _WeaveStrength;
            float _Silk, _SilkAmbient, _SilkSpecular;
            struct v2f {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                float3 world : TEXCOORD1;
                float3 normal : TEXCOORD2;
                float3 tangent : TEXCOORD3;
                float3 bitangent : TEXCOORD4;
            };
            v2f vert(appdata_tan v) {
                v2f o;
                o.pos=UnityObjectToClipPos(v.vertex);
                o.world=mul(unity_ObjectToWorld,v.vertex).xyz;
                o.uv=v.texcoord;
                o.normal=UnityObjectToWorldNormal(v.normal);
                o.tangent=UnityObjectToWorldDir(v.tangent.xyz);
                o.bitangent=cross(o.normal,o.tangent)*v.tangent.w*unity_WorldTransformParams.w;
                return o;
            }
            float3 compress(float3 light,float coverage) {
                float lum=dot(light,float3(.2126,.7152,.0722));
                float mapped=saturate(lum*(2.51*lum+.03)/(lum*(2.43*lum+.59)+.14));
                float3 neutral=saturate(light*(2.51*light+.03)/(light*(2.43*light+.59)+.14));
                return lerp(neutral,light*mapped/max(lum,.0001),(1-coverage)*.75);
            }
            float4 frag(v2f i) : SV_Target {
                float3 smoothNormal=normalize(i.normal);
                float3 view=normalize(_WorldSpaceCameraPos-i.world);
                float facing=max(abs(dot(smoothNormal,view)),.12);
                float density=tex2D(_DensityTex,i.uv).r;
                float depth=lerp(.4,1.7,max(pow(1-facing,3),smoothstep(.6,.9,density)));
                float coverage=1-exp(-density*depth/facing);
                float3 base=lerp(tex2D(_SkinTex,i.uv).rgb,tex2D(_MainTex,i.uv).rgb,coverage);
                if(_DebugView==3) return float4(tex2D(_SkinTex,i.uv).rgb,1);
                float2 weaveUV=i.uv*20;
                float footprint=max(length(ddx(weaveUV)),length(ddy(weaveUV)))*16;
                float3 weave=UnpackNormal(tex2D(_BumpMap,weaveUV));
                if(_DebugView==1) return float4((1-smoothstep(.2,.65,footprint)).xxx,1);
                if(_DebugView==2) return float4(weave*.5+.5,1);
                weave.xy*=_WeaveStrength*(1-smoothstep(.2,.65,footprint));
                weave.z=sqrt(saturate(1-dot(weave.xy,weave.xy)));
                float3 normal=normalize(normalize(i.tangent)*weave.x+normalize(i.bitangent)*weave.y+smoothNormal*weave.z);
                // Local soft lighting keeps fiber/skin calibration independent of other meshes.
                float3 key=normalize(float3(0,1,-1));
                float3 fill=normalize(float3(1,.3,-1));
                float diffuse=.6+(max(dot(normal,key),0)+.25*max(dot(normal,fill),0))/UNITY_PI;
                float spec=pow(max(dot(normal,normalize(key+view)),0),8)*.022;
                float sheen=pow(1-facing,3)*.025;
                if(_Silk>.5) {
                    // Keep the broad light readable at desktop size. Fine weave
                    // perturbs it gently, instead of becoming glitter or a grid.
                    // A desktop pet has no fixed surrounding scene. Treat the
                    // stocking lights as a soft studio rig around the viewer.
                    float3 cameraRight=normalize(UNITY_MATRIX_V[0].xyz);
                    float3 cameraUp=normalize(UNITY_MATRIX_V[1].xyz);
                    float3 cameraFacing=normalize(UNITY_MATRIX_V[2].xyz);
                    key=normalize(cameraFacing+cameraUp*.85-cameraRight*.45);
                    fill=normalize(cameraFacing+cameraUp*.3+cameraRight);
                    float keyAmount=max(dot(normal,key),0);
                    float fillAmount=max(dot(normal,fill),0);
                    float backAmount=max(dot(normal,normalize(-cameraFacing+cameraUp*.45+cameraRight*.3)),0);
                    float3 lighting=_SilkAmbient*float3(.96,.98,1.04)
                        +keyAmount*.55*float3(1,.97,.95)
                        +fillAmount*.10*float3(.86,.92,1.04)
                        +backAmount*.25*float3(.90,.94,1.04);
                    float3 silkNormal=normalize(lerp(smoothNormal,normal,.35));
                    float highlight=pow(max(dot(silkNormal,normalize(key+view)),0),14)
                        *_SilkSpecular*lerp(.8,1,coverage);
                    float edge=pow(1-facing,3)*.055;
                    return float4(compress((base*lighting+highlight*float3(1,.985,.98)
                        +edge*float3(.94,.97,1))*_Exposure,coverage),1);
                }
                return float4(compress((base*diffuse+spec+sheen)*_Exposure,coverage),1);
            }
            ENDCG
        }
    }
    Fallback "Diffuse"
}
