// Keep MToon's alpha, outline, vertex deformation and lighting contract.
#define frag_forward frag_hair_base
#include "Packages/VRM/MToon/Shaders/MToonSM3.cginc"
#undef frag_forward

half _HairDepth, _HairSilk, _HairWidth;
half4 _HairHighlightColor;

float4 frag_forward(v2f i) : SV_TARGET
{
    float4 result = frag_hair_base(i);
#if !defined(MTOON_FORWARD_ADD) && !defined(MTOON_DEBUG_NORMAL) && !defined(MTOON_DEBUG_LITSHADERATE)
    if (i.isOutline > .5) return result;
    float2 uv = TRANSFORM_TEX(i.uv0, _MainTex);
    // The existing VRM AO asset is imported as sRGB. Recover its mask values
    // here rather than changing its importer (the ears and tail also use it).
    half ao = LinearToGammaSpace(tex2D(_ShadingGradeTexture, uv).rgb).r;
    float3 normal = normalize(float3(i.tspace0.z, i.tspace1.z, i.tspace2.z));
    float3 view = normalize(_WorldSpaceCameraPos.xyz - i.posWorld.xyz);
    normal *= dot(normal, view) < 0 ? -1 : 1;

    // VRM has no mesh tangents. Recover the strand's UV-V direction per chart.
    float3 dx = ddx(i.posWorld.xyz), dy = ddy(i.posWorld.xyz);
    float2 ux = ddx(uv), uy = ddy(uv);
    float determinant = ux.x * uy.y - ux.y * uy.x;
    float3 fiber = (dy * ux.x - dx * uy.x) * (determinant < 0 ? -1 : 1);
    fiber -= normal * dot(fiber, normal);
    float3 up = UNITY_MATRIX_V[1].xyz;
    if (dot(fiber, fiber) < 1e-16) fiber = up - normal * dot(up, normal);
    fiber = normalize(fiber + 1e-8);

    // ponytail: one camera-relative art light; this is silk styling, not a full
    // hair scattering model. Revisit only if real scene lights must drive it.
    float3 light = normalize(view + up * .5 + UNITY_MATRIX_V[0].xyz * .3);
    float3 halfway = normalize(view + light);
    float alignment = dot(fiber, halfway);
    half silk = pow(saturate(1 - alignment * alignment), _HairWidth);
    silk *= pow(saturate(dot(normal, halfway)), 3);
    silk *= .25 + .75 * ao;
    result.rgb *= 1 - _HairDepth * (1 - ao);
    result.rgb += _HairSilk * silk * _HairHighlightColor.rgb;
#endif
    return result;
}
