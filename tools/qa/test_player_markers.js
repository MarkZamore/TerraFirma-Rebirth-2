const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../client-policy/scripts/lanmc_journeymap_player_markers.js'), 'utf8');

class IdentityHashMap {
  constructor() { this.values = new Map(); }
  put(key, value) { this.values.set(key, value); }
  containsKey(key) { return this.values.has(key); }
  size() { return this.values.size; }
  isEmpty() { return this.values.size === 0; }
}

function iterator(values) {
  let index = 0;
  return {hasNext: () => index < values.length, next: () => values[index++]};
}

function player(name, x, invisible = false) {
  return {
    name, x,
    getUUID() { return {equals: other => other.toString() === name, toString: () => name}; },
    isRemoved: () => false,
    isInvisibleTo: () => invisible,
  };
}

function fixture({client = true, loaded = true, host = true} = {}) {
  const local = player('host', 0);
  const near = player('near', 10);
  const unknown = player('unknown', 20);
  const hidden = player('hidden', 30);
  const invisible = player('invisible', 40, true);
  const radarValues = new Map([
    ['near', {x: 1}], ['far', {x: 500}], ['hidden', null], ['invisible', {x: 2}],
  ]);
  const state = {
    ticks: [], loads: [], errors: [], updates: [], clears: 0,
    livePlayers: [local, near, unknown, hidden, invisible],
    radarValues, near,
    savedWaypoints: ['base', 'home'],
  };
  const mc = {
    level: {players: () => ({iterator: () => iterator(state.livePlayers)})},
    player: local,
    hasSingleplayerServer: () => host,
  };
  const radar = {
    getPlayers: () => ({
      get: uuid => radarValues.get(uuid.toString()) ?? null,
      values: () => ({iterator: () => iterator([...radarValues.values()])}),
    }),
    updatePlayers(location) {
      const value = radarValues.get(location.player.name);
      value.x = location.player.x;
      state.updates.push(location.player.name);
    },
  };
  class PlayerLoc {
    constructor(entity, visible) { assert.equal(visible, true); this.player = entity; }
  }
  const classes = {
    'net.minecraft.client.Minecraft': {getInstance: () => mc},
    'journeymap.common.util.PlayerRadarManager': {getInstance: () => radar},
    'journeymap.common.network.model.PlayerLoc': PlayerLoc,
    'journeymap.common.waypoint.WaypointStore': {
      getInstance: () => ({getPlayerPoints: () => ({clear: () => {state.clears++;}})}),
    },
    'java.util.IdentityHashMap': IdentityHashMap,
  };
  vm.runInNewContext(source, {
    Platform: {isClientEnvironment: () => client, isLoaded: () => loaded},
    Java: {loadClass(name) { state.loads.push(name); return classes[name]; }},
    NativeEvents: {onEvent(name, fn) {
      assert.equal(name, 'net.neoforged.neoforge.client.event.ClientTickEvent$Post');
      state.ticks.push(fn);
    }},
    console: {info() {}, error(message) { state.errors.push(message); }},
  });
  state.mc = mc;
  state.tick = () => state.ticks.forEach(fn => fn());
  return state;
}

for (const options of [{client: false}, {loaded: false}]) {
  const state = fixture(options);
  assert.equal(state.loads.length, 0);
  assert.equal(state.ticks.length, 0);
}

const guest = fixture({host: false});
guest.tick();
assert.equal(guest.clears, 0);
assert.deepEqual(guest.updates, []);

const host = fixture();
host.tick();
assert.deepEqual(host.updates, ['near']);
assert.equal(host.radarValues.get('near').x, 10);
assert.equal(host.radarValues.get('far').x, 500);
assert.equal(host.radarValues.get('invisible').x, 2);
assert.equal(host.radarValues.get('hidden'), null);
assert.equal(host.radarValues.has('unknown'), false);
assert.equal(host.clears, 1);
assert.deepEqual(host.savedWaypoints, ['base', 'home']);

host.near.x = 25;
host.tick();
assert.equal(host.radarValues.get('near').x, 25);
assert.equal(host.clears, 1, 'ordinary movement preserves the native marker objects');

host.radarValues.set('near', {x: 5});
host.tick();
assert.equal(host.radarValues.get('near').x, 25);
assert.equal(host.clears, 2, 'replacement with unchanged player count invalidates the stale marker');

host.radarValues.delete('far');
host.tick();
assert.equal(host.clears, 3, 'leaving players invalidate transient markers');

const level = host.mc.level;
host.mc.level = null;
host.tick();
assert.equal(host.clears, 3, 'disconnect does not mutate JourneyMap during teardown');
host.mc.level = level;
host.tick();
assert.equal(host.clears, 4, 're-entry refreshes the marker collection');
assert.deepEqual(host.savedWaypoints, ['base', 'home']);
assert.deepEqual(host.errors, []);
console.log('PASS actual player-marker script: environment gates, guest isolation, movement, visibility, distant records, replacement, departure, disconnect and re-entry');
