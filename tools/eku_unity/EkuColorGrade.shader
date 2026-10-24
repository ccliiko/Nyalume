Shader "Hidden/Eku/ColorGrade" {
    Properties { _MainTex ("Source", 2D) = "white" {} }
    SubShader {
        Cull Off ZWrite Off ZTest Always
        Pass {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _MainTex;
            float _Contrast, _Saturation, _Brightness, _GradeEnabled;
            float4x4 _Hue;
            float4 frag(v2f_img i) : SV_Target {
                float4 color=tex2D(_MainTex,i.uv);
                // The camera resolves coverage against transparent black. Return
                // straight alpha for PNG/PIL; the Windows presenter premultiplies once.
                color.rgb/=max(color.a,.00001);
                if(_GradeEnabled<.5) return color;
                float3 rgb=LinearToGammaSpace(color.rgb);
                rgb=(rgb-.5)*_Contrast+.5;
                float lum=dot(rgb,float3(.2126,.7152,.0722));
                rgb=lerp(lum.xxx,rgb,_Saturation)*_Brightness;
                rgb=mul(_Hue,float4(rgb,0)).rgb;
                return float4(GammaToLinearSpace(saturate(rgb)),color.a);
            }
            ENDCG
        }
        Pass {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment downsample
            #include "UnityCG.cginc"
            sampler2D _MainTex;
            float4 _MainTex_TexelSize;
            float4 downsample(v2f_img i) : SV_Target {
                float2 d=_MainTex_TexelSize.xy*.5;
                float4 a=tex2D(_MainTex,i.uv+float2(-d.x,-d.y));
                float4 b=tex2D(_MainTex,i.uv+float2( d.x,-d.y));
                float4 c=tex2D(_MainTex,i.uv+float2(-d.x, d.y));
                float4 e=tex2D(_MainTex,i.uv+float2( d.x, d.y));
                // Filter in premultiplied alpha, then return straight alpha to the presenter.
                float alpha=a.a+b.a+c.a+e.a;
                return float4((a.rgb*a.a+b.rgb*b.a+c.rgb*c.a+e.rgb*e.a)/max(alpha,.00001),alpha*.25);
            }
            ENDCG
        }
    }
}
