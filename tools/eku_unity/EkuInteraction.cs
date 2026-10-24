using System;
using UnityEngine;

// Source timestamps are Python monotonic seconds; receivedAt is Unity's clock.
[Serializable] public struct EkuInputSnapshot {
    public long sampleSeq;
    public double at,receivedAt;
    public bool valid,allowAttention,leftDown;
    public Vector2 viewport,velocityDip;
    public float speedDip,path,duration;
    public Vector3 localPoint,localDirection;
}

[Serializable] public class EkuInteractionStatus {
    public int id;
    public string region,phase,reason;
    public EkuInputSnapshot input;
    public EkuHeadPatOutput headPat;
    public EkuCheekStatus cheek;
    public EkuInteractionEvent lifecycle;
    public bool cheekRegionsValid;
    public Vector2 cheekLeftViewport,cheekRightViewport;
    public Quaternion headRotation,neckRotation;
    public float pickMilliseconds;
    public long evaluatorAllocatedBytes;
}

// Humanoid deltas have one final writer, after HumanPoseHandler and before springs.
public struct EkuDesktopPose {
    public EkuAttentionOutput attention;
    public Vector2 look;
    public Vector3 gesture;
    public Vector3 cheekDegrees;
    public EkuHeadPatOutput headPat;
    public float contactStrength,contactAge;
    public string region;
    public HumanBodyBones hand;
}
