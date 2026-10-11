"""真实模型与 Ammo 下的抚摸、握手、退出与渐变反馈。"""
from pathlib import Path
from urllib.parse import quote

import pytest
from playwright.sync_api import sync_playwright

from nyalume.frontends.pet.pet3d import model_profile, pet3d_win as pet

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('dpi', [1, 1.5, 2])
def test_contact_feedback_on_real_model(tmp_path, monkeypatch, dpi):
    model_path = ROOT / 'models/锁瞑'
    if not model_path.exists():
        pytest.skip('本地模型未安装')
    monkeypatch.setattr(model_profile, 'PROFILE_DIR', str(tmp_path / 'profiles'))
    monkeypatch.setattr(pet, '_log', lambda *_: None)
    directory, pmx = pet._resolve_model(str(model_path))
    api = pet._NativeApi(enabled=False)
    api.configure_model(str(Path(directory) / pmx))
    port = pet.start_server(directory, str(ROOT / 'motions'), api=api)
    source = (ROOT / 'nyalume/frontends/pet/pet3d/viewer.html').read_text(encoding='utf-8')
    probe = """
    clearInterval(actionTimer); clearInterval(frameTimerId);
    window.reports = [];
    window.pywebview = {api: {interaction_info: (...args) => reports.push(args)}};
    let testId = 0, testAt = 0;
    window.rig = {
      get contact() { return contact && {...contact, hand: contact.hand?.name, grab: contact.grab?.name}; },
      get touch() { return {...touch}; },
      get strength() { return contactStrength; },
      get handPose() { return {side:heldArm?.side, active:Boolean(heldHand || heldArm)}; },
      get weights() { return reactWeights.slice(); },
      get particles() { return touchParticles.length; },
      get particleOpacity() { return touchParticles[0]?.sprite.material.opacity || 0; },
      get bounce() { return reactLeft; },
      get grab() { return {name:grabBone?.name, target:grabTgt.toArray(), offset:grabOff.toArray()}; },
      get height() { return height; },
      distributionCheck() {
        const saved = {skeleton:model.skeleton, influences:grabInfluences, radius:grabRadius, offset:grabOff.clone()};
        try {
          const root = new THREE.Group(); root.rotation.y=.3; root.scale.set(1.1,1.2,1);
          const bones=[], grid=[];
          for (let row=0; row<11; row++) {
            grid[row]=[];
            for (let col=0; col<7; col++) {
              const b = new THREE.Bone(); b.name=`skirt_${row}_${col}`;
              if (row) { b.position.y=-height*.035; grid[row-1][col].add(b); }
              else { b.position.x=(col-3)*height*.025; root.add(b); }
              grid[row][col]=b; bones.push(b);
            }
          }
          root.updateMatrixWorld(true); model.skeleton={bones};
          const anchor=grid[5][3], before=bones.map(b=>b.getWorldPosition(new THREE.Vector3()));
          grabInfluences=buildGrabInfluences(anchor); grabOff.set(height*.025,0,0);
          applyGrabOffset(); root.updateMatrixWorld(true);
          const delta=bones.map((b,i)=>b.getWorldPosition(new THREE.Vector3()).sub(before[i]).length());
          return {anchor:delta[bones.indexOf(anchor)]/height,
            neighbor:delta[bones.indexOf(grid[5][4])]/height,
            waist:Math.max(...grid[0].map(b=>delta[bones.indexOf(b)]))/height,
            far:delta[bones.indexOf(grid[10][3])]/height,
            finite:delta.every(Number.isFinite)};
        } finally {
          model.skeleton=saved.skeleton; grabInfluences=saved.influences; grabRadius=saved.radius; grabOff.copy(saved.offset);
        }
      },
      patchMotion() {
        const offset=grabOff.clone(), bones=grabInfluences.map(item=>item.bone);
        grabOff.negate(); applyGrabOffset(); model.updateMatrixWorld(true);
        const before=bones.map(b=>b.getWorldPosition(new THREE.Vector3()));
        grabOff.copy(offset); applyGrabOffset(); model.updateMatrixWorld(true);
        const moved=bones.map((b,i)=>b.getWorldPosition(new THREE.Vector3()).distanceTo(before[i]));
        return {count:moved.filter(v=>v>offset.length()*.15).length,
          max:Math.max(...moved)/height, finite:moved.every(Number.isFinite)};
      },
      bounceAmplitude() {
        let amplitude=0;
        for (let i=0; i<8; i++) { this.step(); amplitude=Math.max(amplitude, Math.abs(model.position.y-baseY)); }
        return amplitude/height;
      },
      symbol(symbol) {
        clock.elapsedTime += 2.01; updateTouchParticles();
        burstTouch(symbol);
        const item = touchParticles.at(-1), canvas = particleTextures.get(symbol).image;
        const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
        let alpha=0, white=0;
        for (let i=0; i<pixels.length; i+=4) {
          alpha=Math.max(alpha,pixels[i+3]);
          if (pixels[i+3] && pixels[i]>245 && pixels[i+1]>245 && pixels[i+2]>245) white++;
        }
        return {count:touchParticles.length, alpha, white, angle:Math.atan2(item.vx,item.vy), opacity:item.sprite.material.opacity};
      },
      reactions() {
        const result = [];
        for (let i=0; i<12; i++) { window.__react('head',''); result.push(window.__lastReact[1]); }
        return result;
      },
      poseDelta() {
        lookTx=lookTy=lookX=lookY=0; gesture.kind='';
        const bones = [lookBones.head, lookBones.neck, heldHand].filter(Boolean);
        const before = bones.map(b => ({q:b.quaternion.clone(),p:b.position.clone()}));
        applyLookPose(1/60);
        return Math.max(0,...bones.flatMap((b,i) => [
          before[i].q.normalize().angleTo(b.quaternion.clone().normalize()),
          before[i].p.distanceTo(b.position)]));
      },
      headPositions() {
        return [lookBones.head,lookBones.neck].filter(Boolean).map(b=>b.position.toArray());
      },
      faceGrab() {
        const bone=lookBones.head, world=bone.getWorldPosition(new THREE.Vector3());
        world.y+=1; world.z+=.6;
        const hit={bone,world};
        return {region:regionOf(hit),grab:grabBoneOf(hit)?.name || null};
      },
      armSnapshot(side) {
        model.updateMatrixWorld(true);
        return model.skeleton.bones.filter(b=>b.name.startsWith(side)
          && /肩|腕|ひじ|手首/.test(b.name) && !NEVER_GRAB.test(b.name)).map(b=>({
            name:b.name,position:b.position.toArray(),
            length:b.getWorldPosition(new THREE.Vector3()).distanceTo(b.parent.getWorldPosition(new THREE.Vector3()))}));
      },
      handPoseDelta() {
        const bones=[heldArm.shoulder,heldArm.arm,heldArm.elbow,heldHand].filter(Boolean);
        const before=bones.map(b=>({q:b.quaternion.clone(),p:b.position.clone()}));
        applyLookPose(1/60);
        const result=bones.map((b,i)=>({name:b.name,
          angle:before[i].q.normalize().angleTo(b.quaternion.clone().normalize()),
          movement:before[i].p.distanceTo(b.position)}));
        bones.forEach((b,i)=>b.quaternion.copy(before[i].q));
        return result;
      },
      bodyGrabs() {
        return ['左腕','右腕','左ひじ','右ひじ','左足','右足'].map(name=>{
          const bone=model.skeleton.bones.find(b=>b.name===name);
          return grabBoneOf({bone,world:bone.getWorldPosition(new THREE.Vector3())})?.name || null;
        });
      },
      morphsMissing(value) {
        if (value) { this.savedMorphs=model.morphTargetDictionary; model.morphTargetDictionary={}; }
        else model.morphTargetDictionary=this.savedMorphs;
      },
      get helper() { return helper; }, get model() { return model; },
      pose(yaw, zoom) { viewYaw = yaw; viewZoomTgt = zoom; applyBox(); this.step(); },
      step() {
        clock.getDelta = () => { clock.elapsedTime += 1/60; return 1/60; };
        tickFrame();
      },
      point(region, offset = 0, side = '') {
        if (!grabProxy) grabProxy = buildGrabProxy();
        const attr = model.geometry.attributes.position;
        for (let i = 0; i < grabProxy.n; i += 3) {
          const bone = model.skeleton.bones[grabProxy.bone[i]];
          if (region === 'head' && !/髪|Hair|頭/.test(bone.name)) continue;
          if (region === 'hand' && !/手首|指/.test(bone.name)) continue;
          if (side && !bone.name.startsWith(side)) continue;
          if (region === 'arm' && !/腕/.test(bone.name)) continue;
          if (region === 'sleeve' && !/袖|腕|ひじ/.test(bone.name)) continue;
          if (region === 'skirt' && !SKIRT_BONE.test(bone.name)) continue;
          if (region === 'skirt') {
            const bottom=Math.min(...model.skeleton.bones.filter(b=>SKIRT_BONE.test(b.name)).map(b=>b.getWorldPosition(new THREE.Vector3()).y));
            if (bone.getWorldPosition(new THREE.Vector3()).y > bottom+height*.045) continue;
          }
          const p = new THREE.Vector3().fromBufferAttribute(attr, grabProxy.idx[i]);
          model.applyBoneTransform(grabProxy.idx[i], p); p.applyMatrix4(model.matrixWorld).project(camera);
          const x = (p.x+1)/2 + offset/renderer.domElement.clientWidth, y = (1-p.y)/2;
          const hit = pickBone(x, y);
          if (region === 'head' && regionOf(hit) === 'head') return {x,y};
          if (region === 'face' && regionOf(hit) === 'face') return {x,y};
          if (region === 'hand' && handBone(hit)) return {x,y};
          if (region === 'arm' && /腕/.test(hit?.bone.name || '') && !contactHand(hit,x,y) && !grabBoneOf(hit)) return {x,y};
          if (region === 'sleeve' && regionOf(hit) === 'hand' && !contactHand(hit,x,y) && grabBoneOf(hit)) return {x,y};
          if (region === 'skirt' && regionOf(hit) === 'skirt' && SKIRT_BONE.test(grabBoneOf(hit)?.name || '')) return {x,y};
        }
        return null;
      },
      async send(phase, point, dt = 0) {
        if (phase === 'begin') testId++;
        testAt += dt;
        await window.__interaction({kind:'interaction', id:testId, phase, ...point,
          at:testAt, moved: phase === 'update' ? 10 : 0,
          width:renderer.domElement.clientWidth, height:renderer.domElement.clientHeight});
      },
      async advance(seconds, point, alive = true) {
        for (let i=0; i<Math.round(seconds*60); i++) {
          if (alive && contact && i%2 === 0) await this.send('update', point, 1/30);
          this.step();
        }
      },
      headPoints() {
        const a = this.point('head');
        for (const dx of [10,-10,14,-14]) {
          const b = {...a, x:a.x + dx/renderer.domElement.clientWidth};
          if (regionOf(pickBone(b.x,b.y)) === 'head') return [a,b];
        }
        throw new Error('找不到可连续抚摸的头部区域');
      },
      async quickTap(point, dt) { await this.send('begin', point, dt); await this.send('end', point, .05); },
      async nativeTap(point) {
        const api=window.pywebview.api, saved=api.poll_action;
        let region=null; api.tap_info=value=>region=value;
        api.poll_action=()=>({kind:'tap',...point,bounce:true});
        try { await pullActions(); return region; }
        finally { api.poll_action=saved; delete api.tap_info; }
      },
      idle() { window.__idleBeat(); },
      face() { window.__face('love'); },
      handMissing(hit) {
        const saved = model.skeleton.bones.find(b => b.name === '右手首');
        const name = saved.name; saved.name = 'missing';
        const result = handBone({bone:saved}); saved.name = name; return result;
      }
    };
    """
    source = source.replace('</script>\n</body>', probe + '</script>\n</body>')
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(device_scale_factor=dpi)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('window.requestAnimationFrame = () => 0')
        page.route('**/*', lambda route: route.fulfill(body=source, content_type='text/html')
                   if route.request.resource_type == 'document' else route.continue_())
        try:
            page.goto(f'http://127.0.0.1:{port}/viewer.html?pmx=/model/{quote(pmx)}&box=440x660&physics=1')
            page.wait_for_function("document.querySelector('#tip').textContent === ''", polling=100, timeout=60000)
            page.evaluate('rig.step()')
            for yaw, zoom in [(0, 1), (0.3, 0.9), (-0.3, 1.08)]:
                page.evaluate('v => rig.pose(...v)', [yaw, zoom])
                assert page.evaluate("rig.point('head')")
                assert page.evaluate("rig.point('hand')")
            page.evaluate('rig.pose(0, 1)')
            distribution = page.evaluate('rig.distributionCheck()')
            assert distribution['anchor'] == pytest.approx(.025, abs=1e-6)
            assert .015 < distribution['neighbor'] < .025
            assert distribution['waist'] < 1e-6 and distribution['far'] < 1e-6
            assert distribution['finite']
            for symbol in ['♡', '！', '？', '♪', 'zzz']:
                visual = page.evaluate('s => rig.symbol(s)', symbol)
                assert visual['count'] == 1
                assert 0 < visual['alpha'] <= 192 and visual['white'] == 0
                assert abs(visual['angle']) <= 3.141593 / 3
            page.evaluate('rig.advance(.9,{x:-1,y:-1},false)')
            assert .35 < page.evaluate('rig.particleOpacity') < .6
            page.locator('canvas').screenshot(path=str(tmp_path / 'symbol-fade.png'))
            page.evaluate('rig.advance(1,{x:-1,y:-1},false)')
            assert page.evaluate('rig.particles') == 0
            head = page.evaluate("rig.point('head')")
            assert page.evaluate('p => rig.nativeTap(p)', head) == 'head'
            assert page.evaluate('rig.bounceAmplitude()') > .005
            assert page.evaluate('rig.contact') is None
            page.evaluate('p => rig.advance(1.6,p,false)', head)
            a, b = page.evaluate('rig.headPoints()')
            head_positions = page.evaluate('rig.headPositions()')
            page.evaluate("p => rig.send('begin', p)", a)
            assert page.evaluate('rig.contact.grab || null') is None
            assert page.evaluate('rig.touch.kind') == 'notice'
            assert page.evaluate('rig.bounce') == 0
            page.evaluate('rig.step()')
            assert 0 < max(page.evaluate('rig.weights')) < 0.2
            page.evaluate('p => rig.advance(.8,p)', a)
            assert not page.evaluate('rig.contact.engaged')  # 静止按住不算抚摸。
            page.evaluate("p => rig.send('update',p,.04)", b)
            assert page.evaluate('rig.touch.kind') == 'relax'
            page.evaluate('p => rig.advance(.25,p)', b)
            assert page.evaluate('rig.strength') > 0.9
            assert max(page.evaluate('rig.weights')) > 0.9
            assert page.evaluate('rig.grab.name || null') is None
            for before, after in zip(head_positions, page.evaluate('rig.headPositions()')):
                assert after == pytest.approx(before, abs=.001)  # 抚摸允许旋转，不平移头颈骨骼。
            name_before = page.evaluate('rig.touch.kind')
            page.evaluate('rig.idle()')
            assert page.evaluate('rig.touch.kind') == name_before
            assert page.evaluate('rig.particles') <= 1
            page.evaluate("p => rig.send('end',p,.04)", b)
            assert page.evaluate('rig.touch.rapid') == 0
            page.evaluate('p => rig.advance(.45,p,false)', b)
            assert page.evaluate('rig.strength') == 0
            assert max(page.evaluate('rig.weights')) == 0
            assert page.evaluate('rig.touch.kind') == 'linger'
            page.evaluate('p => rig.advance(3,p,false)', b)
            assert page.evaluate('rig.touch.kind') == 'idle'

            assert page.evaluate('rig.faceGrab()') == {'region': 'face', 'grab': None}
            face = page.evaluate("rig.point('face')")
            assert face
            head_positions = page.evaluate('rig.headPositions()')
            page.evaluate("p => rig.send('begin',p,1)", face)
            assert page.evaluate('rig.contact.region') == 'face'
            assert page.evaluate('rig.contact.grab || null') is None
            assert not page.evaluate('rig.contact.canHold')
            page.evaluate('p => rig.advance(.6,p)', face)
            dragged_face = {**face, 'y': face['y'] - .2}
            page.evaluate("p => rig.send('update',p,.04)", dragged_face)
            page.evaluate('p => rig.advance(.3,p)', dragged_face)
            assert page.evaluate('rig.contact') is None
            assert page.evaluate('rig.grab.name || null') is None
            for before, after in zip(head_positions, page.evaluate('rig.headPositions()')):
                assert after == pytest.approx(before, abs=.001)  # 长按拖脸不拉长脖子。
            page.evaluate("p => rig.send('end',p,.04)", dragged_face)

            assert page.evaluate('rig.bodyGrabs()') == [None] * 6
            sides = []
            for side in ['左', '右']:
                hand = page.evaluate("side => rig.point('hand',0,side)", side)
                assert hand
                before = page.evaluate('side => rig.armSnapshot(side)', side)
                page.evaluate("p => rig.send('begin',p,1)", hand)
                page.evaluate('p => rig.advance(.5,p)', hand)
                assert page.evaluate('rig.touch.kind') == 'hold'
                assert page.evaluate('rig.contact.hand').startswith(side)
                assert page.evaluate('rig.grab.name || null') is None
                sides.append(page.evaluate('rig.handPose.side'))
                changes = page.evaluate('rig.handPoseDelta()')
                assert len(changes) >= 3
                assert sum(c['angle'] > .01 for c in changes) >= 3
                assert all(c['movement'] < 1e-6 for c in changes)
                pulled = {**hand, 'x': hand['x'] + .3, 'y': hand['y'] - .3}
                page.evaluate("p => rig.send('update',p,.04)", pulled)
                page.evaluate('p => rig.advance(1,p)', pulled)
                assert page.evaluate('rig.contact.engaged')
                assert page.evaluate('rig.grab.offset') == pytest.approx([0, 0, 0], abs=1e-6)
                after = page.evaluate('side => rig.armSnapshot(side)', side)
                for start, end in zip(before, after):
                    assert start['name'] == end['name']
                    assert end['position'] == pytest.approx(start['position'], abs=.001)
                    assert end['length'] == pytest.approx(start['length'], abs=.001)
                page.locator('canvas').screenshot(path=str(tmp_path / f'handshake-{side}.png'))
                page.evaluate("p => rig.send('end',p,.04)", pulled)
                page.evaluate('p => rig.advance(.5,p,false)', hand)
                assert page.evaluate('rig.strength') == 0
                assert not page.evaluate('rig.handPose.active')
                assert page.evaluate('rig.poseDelta()') < 1e-6
            assert sides[0] == -sides[1]
            assert page.evaluate('rig.handMissing()') is None

            arm = page.evaluate("rig.point('arm')")
            assert arm
            page.evaluate("p => rig.send('begin',p,1)", arm)
            assert not page.evaluate('rig.contact.canHold')
            assert page.evaluate('rig.contact.grab || null') is None
            page.evaluate('p => rig.advance(.7,p)', arm)
            assert not page.evaluate('rig.contact?.engaged || false')
            page.evaluate("p => rig.send('cancel',p,.04)", arm)

            sleeve = page.evaluate("rig.point('sleeve')")
            assert sleeve
            page.evaluate('p => rig.nativeTap(p)', sleeve)
            assert page.evaluate('rig.bounceAmplitude()') > .005
            page.evaluate('p => rig.advance(1.6,p,false)', sleeve)
            page.evaluate("p => rig.send('begin',p,1)", sleeve)
            assert page.evaluate('rig.bounce') == 0  # Alt 按下不轻弹。
            page.evaluate("p => rig.send('end',p,.05)", sleeve)
            assert page.evaluate('rig.bounce') == 0  # Alt 单点松手也不轻弹。
            assert page.evaluate('rig.bounceAmplitude()') < 1e-6
            page.evaluate("p => rig.send('begin',p,1)", sleeve)
            assert not page.evaluate('rig.contact.canHold')
            page.evaluate('p => rig.advance(.6,p)', sleeve)
            assert page.evaluate('rig.touch.kind') == 'pull'
            pulled = {**sleeve, 'x': sleeve['x'] + 0.08}
            page.evaluate("p => rig.send('update',p,.04)", pulled)
            page.evaluate('p => rig.advance(.2,p)', pulled)
            assert page.evaluate('rig.grab.offset[0]') > 0.01
            assert page.evaluate('rig.contact.engaged')  # 拉离原轮廓仍锁定抓取。
            assert page.evaluate('rig.model.position.x') == 0
            page.locator('canvas').screenshot(path=str(tmp_path / 'cloth-pull.png'))
            page.evaluate("p => rig.send('end',p,.04)", sleeve)
            page.evaluate('p => rig.advance(.6,p,false)', sleeve)
            assert page.evaluate('rig.grab.name || null') is None
            assert max(abs(v) for v in page.evaluate('rig.grab.offset')) < .001

            skirt = page.evaluate("rig.point('skirt')")
            assert skirt
            page.evaluate("p => rig.send('begin',p,1)", skirt)
            page.evaluate('p => rig.advance(.5,p)', skirt)
            skirt_pulled = {**skirt, 'x': skirt['x'] + .12, 'y': skirt['y'] - .04}
            page.evaluate('p => rig.advance(.5,p)', skirt_pulled)
            motion = page.evaluate('rig.patchMotion()')
            assert motion['count'] >= 5  # 周围多根裙骨共同移动。
            assert 0 < motion['max'] < .04 and motion['finite']
            page.locator('canvas').screenshot(path=str(tmp_path / 'skirt-patch.png'))
            page.evaluate("p => rig.send('end',p,.04)", skirt_pulled)
            page.evaluate('p => rig.advance(2,p,false)', skirt)
            assert page.evaluate('rig.grab.name || null') is None
            assert max(abs(v) for v in page.evaluate('rig.grab.offset')) < .001

            a, b = page.evaluate('rig.headPoints()')
            page.evaluate("p => rig.send('begin',p,1)", a)
            page.evaluate('p => rig.advance(.7,p)', b)
            page.evaluate("rig.send('update',{x:-1,y:-1},.04)")
            page.evaluate('rig.advance(.25,{x:-1,y:-1})')
            assert page.evaluate('rig.contact') is None  # 离开部位结束接触。
            page.evaluate("p => rig.send('end',p,.04)", a)

            a = page.evaluate("rig.point('head')")
            page.evaluate('p => rig.quickTap(p,1)', a)
            assert page.evaluate('rig.bounce') == 0
            assert page.evaluate('rig.bounceAmplitude()') < 1e-6
            assert page.evaluate('rig.poseDelta()') > .001  # 保留轻点时的轻微点头反馈。
            page.evaluate('p => rig.quickTap(p,.15)', a)
            page.evaluate('p => rig.quickTap(p,.15)', a)
            assert page.evaluate('rig.touch.kind') == 'dodge'
            page.evaluate('p => rig.advance(4.1,p,false)', a)
            page.evaluate("p => rig.send('begin',p,1)", a)
            page.evaluate('p => rig.advance(.6,p,false)', a)
            assert page.evaluate('rig.contact') is None  # 桥接停止也会收尾。
            page.evaluate('rig.advance(1,{x:-1,y:-1},false)')
            combos = page.evaluate('rig.reactions()')
            assert all(first != second for first, second in zip(combos, combos[1:]))
            page.evaluate('rig.advance(1,{x:-1,y:-1},false)')
            a = page.evaluate("rig.point('head')")
            page.evaluate('rig.morphsMissing(true)')
            page.evaluate("p => rig.send('begin',p,1)", a)
            page.evaluate('p => rig.advance(.7,p)', a)
            page.evaluate("p => rig.send('end',p,.04)", a)
            assert max(page.evaluate('rig.weights')) == 0
            page.evaluate('rig.morphsMissing(false)')
            page.evaluate('rig.advance(.5,{x:-1,y:-1},false)')
            hand = page.evaluate("rig.point('hand')")
            page.evaluate("p => rig.send('begin',p,1)", hand)
            page.evaluate('p => rig.advance(.5,p)', hand)
            assert page.evaluate('rig.contact.engaged')
            dance = next((ROOT / 'motions').rglob('IRIS OUT.vmd'))
            url = '/vmd/' + quote(dance.relative_to(ROOT / 'motions').as_posix())
            assert page.evaluate('url => window.__setMotion(url)', url)
            assert page.evaluate('rig.poseDelta()') < 1e-6  # 舞蹈不叠加头/手互动姿态。
            page.evaluate('rig.step()')
            assert page.evaluate('rig.model.morphTargetInfluences.some(v=>v>.5)')
            assert page.evaluate('rig.helper.enabled.physics')
            page.evaluate("p => rig.send('cancel',p,.04)", hand)
            reports = page.evaluate('reports')
            beginnings = [r[0] for r in reports if r[2] == 'begin']
            endings = [r[0] for r in reports if r[2] == 'end']
            assert sorted(beginnings) == sorted(endings)
            assert len(endings) == len(set(endings))
            assert not errors
        finally:
            browser.close()
