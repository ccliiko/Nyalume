// All imported bytes remain in this browser. No upload endpoint exists.
export function normalizePath(path) {
  const parts = [];
  for (const part of path.replaceAll('\\', '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') { if (!parts.length) throw new Error('资源路径越过模型文件夹'); parts.pop(); }
    else parts.push(part);
  }
  return parts.join('/');
}
export function makeResolver(files, notify = () => {}) {
  const root = '/__petfiles/';
  const exact = new Map(), folded = new Map(), urls = new Set(), missing = new Set(), extraBlobs = new WeakMap();
  for (const entry of files) {
    const path = normalizePath(entry.path);
    const url = URL.createObjectURL(entry.blob);
    exact.set(path, url); urls.add(url);
    const key = path.toLowerCase();
    folded.set(key, folded.has(key) ? null : url);
  }
  const localURL = path => root + normalizePath(path).split('/').map(encodeURIComponent).join('/');
  function resolve(url) {
    if (urls.has(url) || url === '/motions/idle.vmd' || url.startsWith('data:image/')) return url;
    let path = '';
    // MMD texture names can contain literal # or %. Do not treat them as URL fragments.
    const relative = url.startsWith(root) ? url.slice(root.length)
      : url.startsWith(location.origin + root) ? url.slice((location.origin + root).length) : null;
    const candidates=[];
    if(relative!==null){
      try{candidates.push(normalizePath(relative));}catch{}
      try{candidates.push(normalizePath(decodeURIComponent(relative)));}catch{}
    }
    let result;
    for(const candidate of candidates){result=exact.get(candidate)||folded.get(candidate.toLowerCase());if(result)break;}
    path=candidates.at(-1)||'';
    if (result) return result;
    if (!missing.has(url)) { missing.add(url); notify('缺少或不允许加载的贴图：' + (path || url).slice(0,160)); }
    // A local transparent texture prevents untrusted PMX paths from making network requests.
    return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
  }
  return {resolve, localURL, addBlob(blob) { if(extraBlobs.has(blob))return extraBlobs.get(blob);const url=URL.createObjectURL(blob); urls.add(url);extraBlobs.set(blob,url);return url; },
    dispose() { for (const url of urls) URL.revokeObjectURL(url); urls.clear(); }};
}

let dbPromise;
function database() {
  if (!dbPromise) dbPromise = new Promise((resolve,reject) => {
    const request = indexedDB.open('nyalume-pet-library', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('assets', {keyPath:'id'});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('请关闭其他小窝标签页后重试'));
  });
  return dbPromise;
}
export async function storage(action, value) {
  const db = await database();
  return new Promise((resolve,reject) => {
    const tx = db.transaction('assets', action === 'getAll' ? 'readonly' : 'readwrite');
    const request = tx.objectStore('assets')[action](...(value === undefined ? [] : [value]));
    tx.oncomplete = () => resolve(request.result);
    tx.onerror = tx.onabort = () => reject(tx.error || new Error('保存失败，可能是浏览器空间不足'));
  });
}
export async function validateFiles(files, motion = false) {
  if (!files.length || files.length > 2000) throw new Error('请选择文件，单次最多 2000 个');
  let total = 0;
  for (const file of files) {
    total += file.size;
    if (file.size > (motion ? 32 : 128)*1024*1024) throw new Error('文件过大：' + file.name);
  }
  if (total > 256*1024*1024) throw new Error('单次导入不能超过 256 MB');
  const models = files.filter(f=>/\.pmx$/i.test(f.name));
  if (!motion && !models.length) throw new Error('这个文件夹里没有 PMX 模型喵');
  for (const file of motion ? files : models) {
    const head = new TextDecoder().decode(await file.slice(0,30).arrayBuffer());
    if (motion ? !/\.vmd$/i.test(file.name) || !head.startsWith('Vocaloid Motion Data') : !head.startsWith('PMX '))
      throw new Error('文件内容与格式不符：' + file.name);
  }
}
