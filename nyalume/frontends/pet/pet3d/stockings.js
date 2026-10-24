import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// Local Eku UV maps are optional and derived from the user's own avatar.
export async function loadStockings(meshes, avatarUrl) {
  const mesh = meshes.find(mesh => mesh.name === 'Knee_Socks'
    && [].concat(mesh.material).some(mat => mat.name === 'E_Costume_mtoon'));
  if (!mesh) return null;
  const base = new URL('Eku.stockings/', new URL('.', new URL(avatarUrl, location.href)));
  // A separate manager keeps optional missing maps from failing avatar startup.
  const loader = new THREE.TextureLoader(new THREE.LoadingManager());
  const results = await Promise.allSettled(['fabric.png', 'skin.png', 'density.png', 'weave-normal.png']
    .map(file => loader.loadAsync(new URL(file, base).href)));
  const textures = results.filter(result => result.status === 'fulfilled').map(result => result.value);
  if (textures.length !== results.length) {
    textures.forEach(texture => texture.dispose());
    console.warn('白丝贴图不可用，使用模型原材质');
    return null;
  }
  const [fabric, skin, density, weave] = textures;
  textures.forEach(texture => { texture.flipY = false; texture.anisotropy = 4; });
  fabric.colorSpace = skin.colorSpace = THREE.SRGBColorSpace;
  weave.wrapS = weave.wrapT = THREE.RepeatWrapping;
  weave.repeat.set(20, 20);
  const original = mesh.material;
  const surface = [].concat(original).find(mat => !mat.isOutline);
  const material = new THREE.MeshPhysicalMaterial({
    name: 'Eku white stocking fabric', map: fabric, normalMap: weave,
    normalScale: new THREE.Vector2(.3, .3), side: surface.side,
    roughness: .64, metalness: 0, specularIntensity: .22,
    sheen: .25, sheenColor: new THREE.Color('#fff9f1'), sheenRoughness: .75,
    transparent: false, opacity: 1, transmission: 0, clearcoat: 0,
    envMapIntensity: .25,
  });
  material.customProgramCacheKey = () => 'eku-white-fabric-v2-skin-luminance';
  material.onBeforeCompile = shader => {
    shader.uniforms.stockingSkin = {value: skin};
    shader.uniforms.stockingDensity = {value: density};
    shader.fragmentShader = 'uniform sampler2D stockingSkin;\nuniform sampler2D stockingDensity;\n'
      + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace('#include <normal_fragment_maps>', `
      vec3 stockingSmoothNormal = normal;
      #include <normal_fragment_maps>
      float footprint = max(length(dFdx(vNormalMapUv)), length(dFdy(vNormalMapUv))) * 16.0;
      normal = normalize(mix(stockingSmoothNormal, normal, 1.0 - smoothstep(0.2, 0.65, footprint)));
      float facing = max(abs(dot(stockingSmoothNormal, normalize(vViewPosition))), 0.12);
      float density = texture2D(stockingDensity, vMapUv).r;
      // Thin fabric shows more skin face-on; the rim and dense trim stay white.
      float fiberDepth = mix(0.4, 1.7, max(pow(1.0 - facing, 3.0), smoothstep(0.6, 0.9, density)));
      float coverage = 1.0 - exp(-density * fiberDepth / facing);
      vec3 skin = texture2D(stockingSkin, vMapUv).rgb;
      diffuseColor.rgb = mix(skin, diffuseColor.rgb, coverage);
    `);
    // Preserve skin chroma through HDR compression; dense fibers keep neutral highlights.
    shader.fragmentShader = shader.fragmentShader.replace('#include <opaque_fragment>', `
      float stockingLuminance = dot(outgoingLight, vec3(0.2126, 0.7152, 0.0722));
      float stockingMappedLuminance = clamp((stockingLuminance * (2.51 * stockingLuminance + 0.03))
        / (stockingLuminance * (2.43 * stockingLuminance + 0.59) + 0.14), 0.0, 1.0);
      vec3 stockingNeutralHighlights = clamp((outgoingLight * (2.51 * outgoingLight + 0.03))
        / (outgoingLight * (2.43 * outgoingLight + 0.59) + 0.14), 0.0, 1.0);
      outgoingLight = mix(stockingNeutralHighlights,
        outgoingLight * stockingMappedLuminance / max(stockingLuminance, 0.0001), (1.0 - coverage) * 0.75);
      #include <opaque_fragment>
    `);
  };
  // The imported geometry draws its outline as a second group over the same faces.
  const hiddenOutline = new THREE.MeshBasicMaterial({visible: false});
  const enhanced = Array.isArray(original) ? original.map(mat => mat.isOutline ? hiddenOutline : material) : material;
  let environment = null, disposed = false, available = true;
  return {
    initialize(renderer) {
      const room = new RoomEnvironment();
      const generator = new THREE.PMREMGenerator(renderer);
      try {
        environment = generator.fromScene(room, .04);
        material.envMap = environment.texture;
        material.needsUpdate = true;
      } catch (error) {
        available = false;
        mesh.material = original;
        console.warn('白丝环境反射不可用，使用模型原材质', error);
      } finally { room.dispose(); generator.dispose(); }
    },
    style(value) {
      const enabled = Boolean(value && available && !disposed);
      mesh.material = enabled ? enhanced : original;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      mesh.material = original;
      material.dispose(); hiddenOutline.dispose();
      textures.forEach(texture => texture.dispose());
      environment?.dispose();
    },
  };
}
