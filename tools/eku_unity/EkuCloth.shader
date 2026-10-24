// Camera-relative soft lighting is intentionally chosen for desktop readability.
Shader "Eku/Cloth" {
    Properties
    {
        _Cutoff ("Alpha Cutoff", Range(0, 1)) = 0.5
        _Color ("Lit Color + Alpha", Color) = (1,1,1,1)
        _ShadeColor ("Shade Color", Color) = (0.97, 0.81, 0.86, 1)
        [NoScaleOffset] _MainTex ("Lit Texture + Alpha", 2D) = "white" {}
        [NoScaleOffset] _ShadeTexture ("Shade Texture", 2D) = "white" {}
        _BumpScale ("Normal Scale", Float) = 1.0
        [Normal] _BumpMap ("Normal Texture", 2D) = "bump" {}
        _ReceiveShadowRate ("Receive Shadow", Range(0, 1)) = 1
        [NoScaleOffset] _ReceiveShadowTexture ("Receive Shadow Texture", 2D) = "white" {}
        _ShadingGradeRate ("Shading Grade", Range(0, 1)) = 1
        [NoScaleOffset] _ShadingGradeTexture ("Shading Grade Texture", 2D) = "white" {}
        _ShadeShift ("Shade Shift", Range(-1, 1)) = 0
        _ShadeToony ("Shade Toony", Range(0, 1)) = 0.9
        _LightColorAttenuation ("Light Color Attenuation", Range(0, 1)) = 0
        _IndirectLightIntensity ("Indirect Light Intensity", Range(0, 1)) = 0.1
        [HDR] _RimColor ("Rim Color", Color) = (0,0,0)
        [NoScaleOffset] _RimTexture ("Rim Texture", 2D) = "white" {}
        _RimLightingMix ("Rim Lighting Mix", Range(0, 1)) = 0
        [PowerSlider(4.0)] _RimFresnelPower ("Rim Fresnel Power", Range(0, 100)) = 1
        _RimLift ("Rim Lift", Range(0, 1)) = 0
        [NoScaleOffset] _SphereAdd ("Sphere Texture(Add)", 2D) = "black" {}
        [HDR] _EmissionColor ("Color", Color) = (0,0,0)
        [NoScaleOffset] _EmissionMap ("Emission", 2D) = "white" {}
        [NoScaleOffset] _OutlineWidthTexture ("Outline Width Tex", 2D) = "white" {}
        _OutlineWidth ("Outline Width", Range(0.01, 1)) = 0.5
        _OutlineScaledMaxDistance ("Outline Scaled Max Distance", Range(1, 10)) = 1
        _OutlineColor ("Outline Color", Color) = (0,0,0,1)
        _OutlineLightingMix ("Outline Lighting Mix", Range(0, 1)) = 1
        [NoScaleOffset] _UvAnimMaskTexture ("UV Animation Mask", 2D) = "white" {}
        _UvAnimScrollX ("UV Animation Scroll X", Float) = 0
        _UvAnimScrollY ("UV Animation Scroll Y", Float) = 0
        _UvAnimRotation ("UV Animation Rotation", Float) = 0

        [HideInInspector] _MToonVersion ("_MToonVersion", Float) = 39
        [HideInInspector] _DebugMode ("_DebugMode", Float) = 0.0
        [HideInInspector] _BlendMode ("_BlendMode", Float) = 0.0
        [HideInInspector] _OutlineWidthMode ("_OutlineWidthMode", Float) = 0.0
        [HideInInspector] _OutlineColorMode ("_OutlineColorMode", Float) = 0.0
        [HideInInspector] _CullMode ("_CullMode", Float) = 2.0
        [HideInInspector] _OutlineCullMode ("_OutlineCullMode", Float) = 1.0
        [HideInInspector] _SrcBlend ("_SrcBlend", Float) = 1.0
        [HideInInspector] _DstBlend ("_DstBlend", Float) = 0.0
        [HideInInspector] _ZWrite ("_ZWrite", Float) = 1.0
        [HideInInspector] _AlphaToMask ("_AlphaToMask", Float) = 0.0
        _ClothAO ("Cloth Occlusion", Range(0,1)) = .38
        _ClothSheen ("Broad Sheen", Range(0,.2)) = .018
        _ClothSoftness ("Sheen Power", Range(2,32)) = 10
        _ClothFuzz ("Edge Fuzz", Range(0,.1)) = .006
        _ClothBrightness ("Diffuse Brightness", Range(0,1)) = 1
    }
    SubShader {
        Tags { "RenderType"="TransparentCutout" "Queue"="Transparent" }
        Pass {
            Name "CLOTH"
            Tags { "LightMode"="ForwardBase" }
            Cull [_CullMode]
            Blend [_SrcBlend] [_DstBlend]
            ZWrite [_ZWrite]
            ZTest LEqual
            BlendOp Add, Max
            AlphaToMask [_AlphaToMask]
            CGPROGRAM
            #pragma target 3.0
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile _ _ALPHATEST_ON _ALPHABLEND_ON
            #include "UnityCG.cginc"
            sampler2D _MainTex, _ShadingGradeTexture;
            float4 _MainTex_ST, _Color;
            float _Cutoff, _ClothAO, _ClothSheen, _ClothSoftness, _ClothFuzz, _ClothBrightness;
            struct appdata {float4 vertex:POSITION; float3 normal:NORMAL; float2 uv:TEXCOORD0;};
            struct v2f {float4 vertex:SV_POSITION; float2 uv:TEXCOORD0; float3 world:TEXCOORD1; float3 normal:TEXCOORD2;};
            v2f vert(appdata v) {
                v2f o; o.vertex=UnityObjectToClipPos(v.vertex);
                o.uv=TRANSFORM_TEX(v.uv,_MainTex);
                o.world=mul(unity_ObjectToWorld,v.vertex).xyz;
                o.normal=UnityObjectToWorldNormal(v.normal);
                return o;
            }
            float4 frag(v2f i, float facing:VFACE):SV_Target {
                float4 base=tex2D(_MainTex,i.uv)*_Color;
                #if defined(_ALPHATEST_ON)
                    clip(base.a-_Cutoff);
                #endif
                // Match MToon's D3D11 transparent-depth behavior exactly.
                #if defined(_ALPHABLEND_ON) && defined(SHADER_API_D3D11)
                    clip(base.a-0.0009765625);
                #endif
                float3 normal=normalize(i.normal)*(facing>=0?1:-1);
                float3 view=normalize(_WorldSpaceCameraPos-i.world);
                float3 right=normalize(UNITY_MATRIX_V[0].xyz);
                float3 up=normalize(UNITY_MATRIX_V[1].xyz);
                float3 front=normalize(UNITY_MATRIX_V[2].xyz);
                float3 key=normalize(front+up*.85-right*.45);
                float3 fill=normalize(front+up*.3+right);
                float keyAmount=saturate(dot(normal,key));
                float fillAmount=saturate(dot(normal,fill));
                float ao=lerp(1,tex2D(_ShadingGradeTexture,i.uv).r,_ClothAO);
                float3 light=.4*float3(.99,1,1.02)
                    +.48*keyAmount*float3(1.03,1,.97)
                    +.16*fillAmount*float3(.97,.99,1.04);
                float broad=pow(saturate(dot(normal,normalize(view+key))),_ClothSoftness);
                float edge=pow(1-saturate(dot(normal,view)),3);
                // Keep bright trims matte while giving dark knit a readable soft sheen.
                float luminance=dot(base.rgb,float3(.2126,.7152,.0722));
                float sheenMask=lerp(1,.45,saturate(luminance*1.6));
                float3 sheen=(_ClothSheen*broad*sheenMask+_ClothFuzz*edge)
                    *float3(1,.96,.94)*ao;
                return float4(base.rgb*light*ao*_ClothBrightness+sheen,base.a);
            }
            ENDCG
        }
        // Preserve original outline width, UV mask, culling and alpha behavior.
        UsePass "VRM/MToon/FORWARD_BASE_ONLY_OUTLINE"
    }
    Fallback "VRM/MToon"
}
