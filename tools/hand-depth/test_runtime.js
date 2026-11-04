// Executes the actual shipped template against a bounded native-event/GL facade.
// This checks policy behavior; it does not claim Rhino, GPU or game acceptance.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../client-policy/scripts/lanmc_intel_hand_depth.js'), 'utf8');
const FRAME = 'net.neoforged.neoforge.client.event.RenderFrameEvent$Pre';
const HAND = 'net.neoforged.neoforge.client.event.RenderHandEvent';
let checks = 0;

function fixture(overrides = {}) {
  const state = Object.assign({client: true, veil: true, vendor: 'Intel', bound: 28,
    framebuffer: 28, texture: 761, format: 36013, depthWrite: true, scissor: false,
    clearDepth: 1, throwOnClear: false, missingFramebuffer: false}, overrides);
  const events = new Map(), calls = [], logs = [], loaded = [];
  const fbo = {getId: () => state.framebuffer,
    getDepthTextureAttachment: () => ({getId: () => state.texture})};
  const classes = {
    'net.neoforged.bus.api.EventPriority': {HIGHEST: 'HIGHEST'},
    'org.lwjgl.opengl.GL11C': {
      glGetString: p => {assert.equal(p, 7936); return state.vendor;},
      glGetInteger: p => {assert.equal(p, 36006); return state.bound;},
      glGetBoolean: p => {assert.equal(p, 2930); return state.depthWrite;},
      glIsEnabled: p => {assert.equal(p, 3089); return state.scissor;},
      glGetDouble: p => {assert.equal(p, 2931); return state.clearDepth;},
    },
    'org.lwjgl.opengl.GL45C': {
      glGetTextureLevelParameteri: (id, level, p) => {
        assert.equal(id, state.texture); assert.equal(level, 0); assert.equal(p, 4099);
        calls.push({operation: 'queryFormat', id}); return state.format;
      },
    },
    'com.mojang.blaze3d.systems.RenderSystem': {
      clearDepth: value => {calls.push({operation: 'clearDepth', value}); state.clearDepth = value;},
      clear: (mask, osx) => {
        assert.equal(mask, 256); assert.equal(osx, false);
        calls.push({operation: 'clear', framebuffer: state.bound, depth: state.clearDepth});
        if (state.throwOnClear) throw Error('synthetic native clear failure');
      },
    },
    'foundry.veil.api.client.render.VeilRenderSystem': {
      renderer: () => ({getFramebufferManager: () => ({getFramebuffer: id => {
        assert.equal(id, 'veil:first_person'); return state.missingFramebuffer ? null : fbo;
      }})}),
    },
    'foundry.veil.api.client.render.framebuffer.VeilFramebuffers': {FIRST_PERSON: 'veil:first_person'},
  };
  vm.runInNewContext(script, {
    Platform: {isClientEnvironment: () => state.client, isLoaded: mod => {
      assert.equal(mod, 'veil'); return state.veil;
    }},
    Java: {loadClass: name => {loaded.push(name); assert.ok(name in classes, name); return classes[name];}},
    NativeEvents: {onEvent: (priority, name, callback) => {
      assert.equal(priority, 'HIGHEST'); assert.ok([FRAME, HAND].includes(name));
      assert.ok(!events.has(name), 'duplicate native listener'); events.set(name, callback);
    }},
    console: {info: value => logs.push({level: 'info', value}), error: value => logs.push({level: 'error', value})},
  }, {filename: 'lanmc_intel_hand_depth.js'});
  return {state, calls, logs, loaded, events,
    frame() {assert.ok(events.has(FRAME)); events.get(FRAME)();},
    hand() {assert.ok(events.has(HAND)); events.get(HAND)();},
    clears() {return calls.filter(row => row.operation === 'clear');}};
}

function check(name, run) {
  run(); checks++; process.stdout.write('PASS ' + name + '\n');
}

check('Intel actual depth-stencil target is cleared once before either hand', () => {
  const f = fixture(); f.hand(); assert.equal(f.clears().length, 0);
  f.frame(); f.hand(); f.hand(); assert.equal(f.clears().length, 1);
  assert.deepEqual(f.clears()[0], {operation: 'clear', framebuffer: 28, depth: 1});
  f.frame(); f.hand(); assert.equal(f.clears().length, 2);
  assert.equal(f.logs.filter(row => row.level === 'info').length, 1);
});

for (const vendor of ['NVIDIA Corporation', 'AMD', 'Mesa/X.org']) {
  check('unaffected vendor remains unchanged: ' + vendor, () => {
    const f = fixture({vendor}); f.frame(); f.hand(); f.hand(); assert.equal(f.clears().length, 0);
    assert.equal(f.calls.length, 0);
  });
}

for (const [name, overrides] of [
  ['shader/unrelated framebuffer', {bound: 2}],
  ['no registered Veil framebuffer', {missingFramebuffer: true}],
  ['different real depth format', {format: 33190}],
  ['depth writes disabled', {depthWrite: false}],
  ['scissor enabled', {scissor: true}],
]) {
  check(name + ' is not modified', () => {
    const f = fixture(overrides); f.frame(); f.hand(); f.hand(); assert.equal(f.clears().length, 0);
    assert.equal(f.state.clearDepth, 1); assert.equal(f.logs.length, 0);
  });
}

check('real format is checked again after texture ID reuse', () => {
  const f = fixture(); f.frame(); f.hand();
  f.state.format = 33190; f.frame(); f.hand();
  f.state.format = 36013; f.frame(); f.hand();
  assert.equal(f.calls.filter(row => row.operation === 'queryFormat').length, 3);
  assert.equal(f.clears().length, 2);
});

check('changed framebuffer and texture after re-entry are followed', () => {
  const f = fixture(); f.frame(); f.hand();
  f.state.bound = f.state.framebuffer = 42; f.state.texture = 999;
  f.frame(); f.hand(); f.hand();
  assert.deepEqual(f.clears().map(row => row.framebuffer), [28, 42]);
  assert.deepEqual(f.calls.filter(row => row.operation === 'queryFormat').map(row => row.id), [761, 999]);
});

check('previous clear-depth state is restored after successful clear', () => {
  const f = fixture({clearDepth: 0.25}); f.frame(); f.hand();
  assert.equal(f.clears()[0].depth, 1); assert.equal(f.state.clearDepth, 0.25);
  assert.deepEqual(f.calls.filter(row => row.operation === 'clearDepth').map(row => row.value), [1, 0.25]);
});

check('failed native clear restores depth state and disables only this compatibility listener', () => {
  const f = fixture({clearDepth: 0.25, throwOnClear: true}); f.frame();
  assert.doesNotThrow(() => f.hand()); assert.equal(f.state.clearDepth, 0.25);
  f.frame(); f.hand(); assert.equal(f.clears().length, 1);
  assert.equal(f.logs.filter(row => row.level === 'error').length, 1);
  assert.equal(f.logs.filter(row => row.level === 'info').length, 0);
});

for (const overrides of [{client: false}, {veil: false}]) {
  check('unsupported environment installs no native callbacks', () => {
    const f = fixture(overrides); assert.equal(f.events.size, 0); assert.equal(f.loaded.length, 0);
  });
}

process.stdout.write('PASS actual template behavior: ' + checks + ' cases; native/GPU acceptance not claimed.\n');
