import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { loadStockings } from './stockings.js';
import { VRMLoaderPlugin, VRMUtils } from '/node_modules/@pixiv/three-vrm/lib/three-vrm.module.min.js';

// Keep the desktop's interaction names on the normalized rig, leaving source bones intact.
const boneNames = {
  hips: '下半身', spine: '上半身', chest: '上半身2', neck: '首', head: '頭',
  leftEye: '左目', rightEye: '右目', leftShoulder: '左肩', rightShoulder: '右肩',
  leftUpperArm: '左腕', rightUpperArm: '右腕', leftLowerArm: '左ひじ', rightLowerArm: '右ひじ',
  leftHand: '左手首', rightHand: '右手首', leftUpperLeg: '左足', rightUpperLeg: '右足',
  leftLowerLeg: '左ひざ', rightLowerLeg: '右ひざ', leftFoot: '左足首', rightFoot: '右足首',
};
const expressionAliases = {
  '笑い': 'happy', 'なごみ': 'relaxed', '照れ': 'happy', 'はぁと': 'happy',
  '怒り': 'angry', 'じと目': 'angry', '困る': 'sad', 'はぅ': 'surprised',
  'びっくり': 'surprised', 'まばたき': 'blink',
};
// Across the sagittal plane, mirrored Euler rotations keep X and negate Y/Z.
const restPose = {
  leftShoulder: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, .025)).toArray() },
  rightShoulder: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, -.025)).toArray() },
  leftUpperArm: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(.025, -.06, 1.25)).toArray() },
  rightUpperArm: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(.025, .06, -1.25)).toArray() },
  leftLowerArm: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(-.25, -.12, .025)).toArray() },
  rightLowerArm: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(-.25, .12, -.025)).toArray() },
  leftHand: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(-.2, .1, .04)).toArray() },
  rightHand: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(-.2, -.1, -.04)).toArray() },
};
// The normalized palm spans X/Z: hinge around Z toward the palm, not sideways around Y.
for (const side of ['left', 'right']) {
  const sign = side === 'left' ? 1 : -1;
  for (const [finger, angles] of [['Index', [.08, .2, .08]], ['Middle', [.1, .26, .1]],
                                ['Ring', [.14, .3, .1]], ['Little', [.18, .33, .11]]]) {
    ['Proximal', 'Intermediate', 'Distal'].forEach((joint, index) => {
      restPose[side + finger + joint] = {rotation: new THREE.Quaternion()
        .setFromEuler(new THREE.Euler(0, 0, sign * angles[index])).toArray()};
    });
  }
  restPose[side + 'ThumbMetacarpal'] = {rotation: new THREE.Quaternion()
    .setFromEuler(new THREE.Euler(0, sign * .2, sign * .1)).toArray()};
  for (const [joint, curl] of [['Proximal', .12], ['Distal', .07]]) {
    restPose[side + 'Thumb' + joint] = {rotation: new THREE.Quaternion()
      .setFromEuler(new THREE.Euler(0, 0, sign * curl)).toArray()};
  }
}

export async function loadVRM(url, manager, physics) {
  const loader = new GLTFLoader(manager);
  loader.register(parser => ({
    name: 'EkuTailSpringRepair',
    beforeRoot() {
      // This VRM exports a spare tail chain. Its spring references the spare,
      // while the visible mesh is weighted to Tail_2. Repair in memory only.
      const nodes = parser.json.nodes || [];
      const spare = nodes.findIndex(node => node.name === 'Tail 1');
      const visible = nodes.findIndex(node => node.name === 'Tail_2');
      const root = nodes.find(node => node.name === 'Tail_Root');
      if (spare < 0 || visible < 0 || !root?.children?.includes(spare) || !root.children.includes(visible)) return;
      for (const group of parser.json.extensions?.VRM?.secondaryAnimation?.boneGroups || []) {
        group.bones = group.bones.map(index => index === spare ? visible : index);
      }
    },
  }));
  loader.register(parser => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm;
  if (!vrm?.humanoid) throw new Error('文件没有有效的 VRM 人形骨骼');
  VRMUtils.rotateVRM0(vrm);
  vrm.lookAt = null; // The desktop drives normalized eye/head rotations itself.
  if (!physics) vrm.springBoneManager = null;
  const tailRoot = vrm.scene.getObjectByName('Tail_Root');
  const tailRest = tailRoot?.quaternion.clone();
  // VRM is in metres; the desktop's offsets and camera clipping use MMD-sized units.
  const model = new THREE.Group();
  model.scale.setScalar(10);
  model.add(vrm.scene);
  const bones = [], rawToNormalized = new Map();
  for (const [name, alias] of Object.entries(boneNames)) {
    const bone = vrm.humanoid.getNormalizedBoneNode(name);
    if (!bone) continue;
    bone.name = alias;
    bones.push(bone);
    rawToNormalized.set(vrm.humanoid.getRawBoneNode(name), bone);
  }
  model.skeleton = { bones };
  const names = Object.keys(vrm.expressionManager?.expressionMap || {});
  model.morphTargetInfluences = names.map(() => 0);
  model.morphTargetDictionary = Object.fromEntries(names.map((name, i) => [name, i]));
  for (const [alias, name] of Object.entries(expressionAliases)) {
    const index = names.indexOf(name);
    if (index >= 0) model.morphTargetDictionary[alias] = index;
  }
  // VRM 0 avatars may lack a surprised preset; use their open-mouth expression.
  if (model.morphTargetDictionary['びっくり'] === undefined && names.includes('aa')) {
    model.morphTargetDictionary['びっくり'] = names.indexOf('aa');
    model.morphTargetDictionary['はぅ'] = names.indexOf('aa');
  }
  const meshes = [];
  vrm.scene.traverse(obj => {
    if (obj.isMesh) { obj.frustumCulled = false; meshes.push(obj); }
  });
  const materials = [...new Set(meshes.flatMap(mesh => [].concat(mesh.material)))];
  const sourceMaterials = new Map(materials.filter(mat => mat?.isMToonMaterial).map(mat => [mat, {
    shift: mat.shadingShiftFactor, toony: mat.shadingToonyFactor,
    shade: mat.shadeColorFactor.clone(), outline: mat.outlineWidthFactor,
    rim: mat.parametricRimColorFactor.clone(), rimMix: mat.rimLightingMixFactor,
  }]));
  const stockings = await loadStockings(meshes, url);
  const raycaster = new THREE.Raycaster();
  const emotions = {
    happy: ['happy'], calm: ['relaxed'], angry: ['angry'], sad: ['sad'],
    surprise: [names.includes('surprised') ? 'surprised' : 'aa'],
    sleepy: ['blink'], blink: ['blink'], shy: ['happy'], love: ['happy'],
  };
  for (const key of Object.keys(emotions)) emotions[key] = emotions[key].filter(name => names.includes(name));
  const reactions = {
    head: [['happy'], ['relaxed']], face: [['blink'], emotions.surprise],
    chest: [['angry'], ['sad']], belly: [emotions.surprise, ['sad']],
    hand: [['happy'], ['relaxed']], skirt: [['angry'], emotions.surprise],
    cloth: [['angry'], ['sad']], leg: [emotions.surprise, ['sad']],
  };
  return {
    model,
    emotions,
    reactions,
    // VRM 0 faces -Z before rotateVRM0; its normalized pitch has the opposite
    // screen direction from MMD / VRM 1. Yaw keeps its sign after the Y rotation.
    lookPitchSign: vrm.meta.metaVersion === '0' ? -1 : 1,
    style(enhanced = true) {
      for (const [mat, original] of sourceMaterials) {
        const face = /face|body/i.test(mat.name), hair = /hair/i.test(mat.name);
        mat.shadingShiftFactor = enhanced ? (face ? -.15 : hair ? -.22 : -.2) : original.shift;
        mat.shadingToonyFactor = enhanced ? (face ? .72 : hair ? .82 : .8) : original.toony;
        mat.shadeColorFactor.copy(original.shade);
        if (enhanced) mat.shadeColorFactor.lerp(mat.color, face ? .2 : .08);
        mat.outlineWidthFactor = enhanced ? Math.min(original.outline, hair ? .0016 : .002) : original.outline;
        mat.parametricRimColorFactor.copy(original.rim);
        if (enhanced && !face) mat.parametricRimColorFactor.setRGB(.035, .028, .024);
        mat.rimLightingMixFactor = enhanced ? .65 : original.rimMix;
      }
      stockings?.style(enhanced);
    },
    initializeMaterials(renderer) { stockings?.initialize(renderer); },
    disposeMaterials() { stockings?.dispose(); },
    initializePhysics() {
      // Spring tails were initialized in the loader's metre-sized scene. Reset after
      // the desktop has scaled and centred it, preserving the avatar's rest hair pose.
      const springs = vrm.springBoneManager;
      if (!springs) return;
      model.updateMatrixWorld(true);
      for (const joint of springs.joints) {
        joint.settings.stiffness *= model.scale.x;
        joint.settings.gravityPower *= model.scale.x;
        joint.settings.hitRadius *= model.scale.x;
      }
      springs.reset();
    },
    reset(time = 0) {
      vrm.humanoid.resetNormalizedPose();
      vrm.humanoid.setNormalizedPose(restPose);
      // Chest breath, not vertical root motion: the soles stay planted.
      vrm.humanoid.getNormalizedBoneNode('chest')?.rotateX(Math.sin(time * 1.3) * .006);
      vrm.humanoid.getNormalizedBoneNode('leftShoulder')?.rotateZ(Math.sin(time * 1.3) * .002);
      vrm.humanoid.getNormalizedBoneNode('rightShoulder')?.rotateZ(-Math.sin(time * 1.3) * .002);
    },
    update(delta, time) {
      if (physics && tailRoot) {
        tailRoot.quaternion.copy(tailRest).multiply(new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(0, 1, 0), Math.sin(time * .7) * .055 + Math.sin(time * 1.13 + .8) * .025));
      }
      names.forEach((name, i) => vrm.expressionManager.setValue(name, model.morphTargetInfluences[i]));
      const blink = Math.max(0, 1 - Math.abs((time % 4.7) - 4.5) / 0.12);
      vrm.expressionManager?.setValue('blink', Math.max(blink, vrm.expressionManager.getValue('blink') || 0));
      vrm.update(delta);
    },
    pick(nx, ny, camera) {
      raycaster.setFromCamera(new THREE.Vector2(nx * 2 - 1, 1 - ny * 2), camera);
      const hit = raycaster.intersectObjects(meshes, false)[0];
      if (!hit?.face || !hit.object.isSkinnedMesh) return null;
      const geo = hit.object.geometry, weights = geo.attributes.skinWeight, indices = geo.attributes.skinIndex;
      let raw = null, best = -1;
      for (const vertex of [hit.face.a, hit.face.b, hit.face.c]) {
        for (let i = 0; i < 4; i++) {
          const weight = weights.getComponent(vertex, i);
          if (weight > best) { best = weight; raw = hit.object.skeleton.bones[indices.getComponent(vertex, i)]; }
        }
      }
      // Hair/finger joints resolve to their nearest humanoid parent for touch feedback.
      const anchorBone = raw;
      while (raw && !rawToNormalized.has(raw)) raw = raw.parent;
      const bone = rawToNormalized.get(raw);
      return bone ? { bone, anchorBone, world: hit.point } : null;
    },
  };
}
