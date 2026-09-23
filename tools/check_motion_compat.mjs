// 使用渲染器自带的 PMX/VMD 解析器，不另造二进制解析器。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Parser } from '../nyalume/frontends/pet/pet3d/node_modules/three/examples/jsm/libs/mmdparser.module.js';

export function compare(model, motion) {
  const bones = new Set(model.bones.map(bone => bone.name));
  const morphs = new Set(model.morphs.map(morph => morph.name));
  const usedBones = [...new Set(motion.motions.map(frame => frame.boneName))].sort();
  const usedMorphs = [...new Set(motion.morphs.filter(frame => frame.weight !== 0).map(frame => frame.morphName))].sort();
  const movingBones = new Set(motion.motions.filter(frame =>
    frame.position.some(value => Math.abs(value) > 1e-6)
    || frame.rotation.slice(0, 3).some(value => Math.abs(value) > 1e-6)).map(frame => frame.boneName));
  return {
    bone_count: usedBones.length,
    missing_bones: usedBones.filter(name => !bones.has(name)),
    missing_non_neutral_bones: usedBones.filter(name => !bones.has(name) && movingBones.has(name)),
    missing_morphs: usedMorphs.filter(name => !morphs.has(name)),
    camera_only: usedBones.length === 0 && usedMorphs.length === 0,
  };
}

function readBuffer(filename) {
  const buffer = fs.readFileSync(filename);
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}

function motionsAt(target) {
  if (!fs.statSync(target).isDirectory()) return [target];
  return fs.readdirSync(target, { withFileTypes: true }).flatMap(entry => {
    const filename = path.join(target, entry.name);
    if (entry.isDirectory()) return motionsAt(filename);
    return entry.isFile() && /\.vmd$/i.test(entry.name) ? [filename] : [];
  }).sort();
}

export function report(modelFile, motionTarget) {
  const parser = new Parser();
  const model = parser.parsePmx(readBuffer(modelFile), true);
  const files = Array.isArray(motionTarget) ? motionTarget : motionsAt(motionTarget);
  if (!files.length) throw new Error('没有找到 VMD 文件');
  return files.map(filename => {
    try {
      return { file: filename, ...compare(model, parser.parseVmd(readBuffer(filename), true)) };
    } catch (error) {
      return { file: filename, error: String(error.message || error) };
    }
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const json = args.includes('--json'), strict = args.includes('--strict');
  const fromStdin = args.includes('--files-stdin');
  const paths = args.filter(arg => !['--json', '--strict', '--files-stdin'].includes(arg));
  try {
    if (paths.length !== (fromStdin ? 1 : 2)) throw new Error('用法：node tools/check_motion_compat.mjs 模型.pmx 动作.vmd或目录 [--json] [--strict]');
    const target = fromStdin ? JSON.parse(fs.readFileSync(0, 'utf8')) : paths[1];
    if (fromStdin && (!Array.isArray(target) || target.length > 200 || target.some(p => typeof p !== 'string'))) throw new Error('动作列表无效，最多 200 项');
    const results = report(paths[0], target);
    if (json) console.log(JSON.stringify(results, null, 2));
    else {
      for (const result of results) {
        console.log(`\n${result.file}`);
        if (result.error) { console.log(`  解析失败：${result.error}`); continue; }
        if (result.camera_only) { console.log('  无骨骼/有效表情轨（镜头或空动作）'); continue; }
        console.log(`  使用骨骼 ${result.bone_count}；缺失骨骼：${result.missing_bones.join('、') || '无'}`);
        console.log(`  其中含非零位移/旋转的缺失骨骼：${result.missing_non_neutral_bones.join('、') || '无'}`);
        console.log(`  缺失表情：${result.missing_morphs.join('、') || '无'}`);
      }
      console.log('\n缺失是兼容性提示，不代表整支动作不可播放；骨骼齐全也不能保证无穿模。');
    }
    process.exitCode = results.some(r => r.error) ? 2 : strict && results.some(r => r.missing_bones.length || r.missing_morphs.length) ? 1 : 0;
  } catch (error) {
    console.error(String(error.message || error));
    process.exitCode = 2;
  }
}
