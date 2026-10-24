using System;
using UnityEngine;

public enum EkuCheekSide { Left, Right }
public enum EkuCheekChannel { Eye, Eyebrow, Mouth, Blush, Press, Pull, Push, Up, Down }

[Serializable] public sealed class EkuCheekBinding {
    public EkuCheekSide side;
    public EkuCheekChannel channel;
    public string shape;
    [Range(0,100)] public float maximumWeight=100;
    public AnimationCurve response=AnimationCurve.Linear(0,0,1,1);
    public EkuCheekBinding(EkuCheekSide side,EkuCheekChannel channel,string shape,float maximum=100) {
        this.side=side;this.channel=channel;this.shape=shape;maximumWeight=maximum;
    }
}

[CreateAssetMenu(menuName="Eku/Cheek deformation profile")]
public sealed class CheekDeformationProfile : ScriptableObject {
    [Range(0,1),Tooltip("Gain on available geometric shapes only. Missing shapes stay disabled.")]
    public float deformationStrength=.65f;
    [Range(1,30),Tooltip("Continuous target response, inverse seconds. Larger is faster.")]
    public float responseSpeed=12;
    [Range(1,30),Tooltip("Release/cancel recovery, inverse seconds. Larger is faster.")]
    public float recoverySpeed=8;
    [Range(.02f,.5f),Tooltip("Drag radius in face widths, captured at press; independent of screen DPI.")]
    public float maximumDragDistance=.18f;
    [Range(0,1),Tooltip("Eye/eyebrow/mouth expression gain; independent of deformation strength.")]
    public float expressionIntensity=.55f;

    [Header("Anatomical regions, in eye separations")]
    [Tooltip("X is character-right, Y is up. Origin is the midpoint between the eye bones.")]
    public Vector2 leftCenter=new Vector2(-.65f,-.6f),rightCenter=new Vector2(.65f,-.6f);
    public Vector2 regionRadii=new Vector2(.38f,.42f);
    [Min(1)] public float faceWidthInEyeSeparations=2.4f;
    [Range(0,8)] public float maximumHeadDegrees=2.5f;
    [Min(.1f)] public float reactionCooldownSeconds=.35f;
    [Min(.1f),Tooltip("Seconds from the first light press to the full held-contact visual pressure; not a pressure sensor.")]
    public float holdPressSeconds=1.2f;
    [Range(.1f,1),Tooltip("Initial held-contact visual pressure before the duration ramp.")]
    public float initialPress=.55f;
    [Range(1,4),Tooltip("Recovery speed multiplier for cancelled contacts.")]
    public float cancelRecoveryMultiplier=2;
    [Tooltip("Only validated side-specific eye/brow shapes are enabled by default. Geometry names are optional.")]
    public EkuCheekBinding[] bindings={
        new EkuCheekBinding(EkuCheekSide.Left,EkuCheekChannel.Eye,"eye_wink_1_L"),
        new EkuCheekBinding(EkuCheekSide.Right,EkuCheekChannel.Eye,"eye_wink_1_R"),
        new EkuCheekBinding(EkuCheekSide.Left,EkuCheekChannel.Eyebrow,"eyebrow_down_L",18),
        new EkuCheekBinding(EkuCheekSide.Right,EkuCheekChannel.Eyebrow,"eyebrow_down_R",18),
        new EkuCheekBinding(EkuCheekSide.Left,EkuCheekChannel.Press,"cheek_press_L"),
        new EkuCheekBinding(EkuCheekSide.Right,EkuCheekChannel.Press,"cheek_press_R"),
        new EkuCheekBinding(EkuCheekSide.Left,EkuCheekChannel.Pull,"cheek_pull_L"),
        new EkuCheekBinding(EkuCheekSide.Right,EkuCheekChannel.Pull,"cheek_pull_R"),
        new EkuCheekBinding(EkuCheekSide.Left,EkuCheekChannel.Push,"cheek_push_L"),
        new EkuCheekBinding(EkuCheekSide.Right,EkuCheekChannel.Push,"cheek_push_R"),
        new EkuCheekBinding(EkuCheekSide.Left,EkuCheekChannel.Up,"cheek_up_L"),
        new EkuCheekBinding(EkuCheekSide.Right,EkuCheekChannel.Up,"cheek_up_R"),
        new EkuCheekBinding(EkuCheekSide.Left,EkuCheekChannel.Down,"cheek_down_L"),
        new EkuCheekBinding(EkuCheekSide.Right,EkuCheekChannel.Down,"cheek_down_R")
    };
}
