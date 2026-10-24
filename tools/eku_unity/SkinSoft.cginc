// Preserve the existing cuff pressure; add broad skin light only in the base pass.
float4 frag_forward(v2f i) : SV_TARGET {
    float4 c=EkuMToonFrag(i);
    if(i.isOutline<.5) {
        float3 n=normalize(float3(i.tspace0.z,i.tspace1.z,i.tspace2.z));
        float3 up=normalize(UNITY_MATRIX_V[1].xyz);
        float3 front=normalize(UNITY_MATRIX_V[2].xyz);
        float3 right=normalize(UNITY_MATRIX_V[0].xyz);
        float3 light=normalize(front+up*.7);
        c.rgb*=lerp(1,.975+.025*saturate(dot(n,light)),i.color.b);
        c.rgb*=1-i.color.r*float3(.012,.025,.022);
#ifndef MTOON_FORWARD_ADD
        float3 view=normalize(lerp(_WorldSpaceCameraPos.xyz-i.posWorld.xyz,front,unity_OrthoParams.w));
        n*=dot(n,view)<0?-1:1;
        float3 key=normalize(front+up*.6-right*.6);
        float diffuse=smoothstep(-.35,.85,dot(n,key));
        // Warm, shallow shadows and a satin highlight leave the skin texture visible.
        c.rgb*=lerp(float3(.88,.855,.865),float3(1,1,1),diffuse);
        float highlight=pow(saturate(dot(n,normalize(key+view))),8)*.085;
        c.rgb=lerp(c.rgb,max(c.rgb,float3(.96,.93,.91)),highlight);
#endif
    }
    return c;
}
