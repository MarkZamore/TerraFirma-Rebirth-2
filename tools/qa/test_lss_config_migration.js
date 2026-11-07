'use strict';
// Exercise the shipped migration, including LSS's config-load rewrite and retries.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const migration = require('../client-policy/scripts/lanmc_lss_config_migration.js');
const clone = value => JSON.parse(JSON.stringify(value));
const legacy = JSON.parse(fs.readFileSync(path.join(__dirname, '../../config/vss-server-config.json'), 'utf8'));
const normalizedText = fs.readFileSync(path.join(__dirname, 'lss_known_normalized.json'), 'utf8');
assert.equal(crypto.createHash('sha256').update(normalizedText).digest('hex'), migration.knownUpgradeSha256);
const defaults = {mbPerSecondLimitGlobal: 75, mbPerSecondLimitPerPlayer: 25,
  maxConcurrentDiskReads: 0, lodStore: 'off', lodStoreMaxMB: 0, lodStoreBackfill: true,
  lodDistanceChunks: 512, generationConcurrencyLimitGlobal: 40, generationConcurrencyLimitPerPlayer: 40};
let checks = 0;

function fixture(raw, options = {}) {
  const state = {saved: clone(raw), journal: null, receipt: null, saves: 0, logs: [],
    failSave: false, failComplete: false, nativeLoaded: false};
  const io = {
    completed: () => state.receipt !== null,
    pending: () => clone(state.journal),
    exists: file => file === (options.canonical ? 'config/lss-server-config.json' : 'config/vss-server-config.json'),
    text: () => !state.nativeLoaded && options.text ? options.text : JSON.stringify(state.saved),
    sha256: text => crypto.createHash('sha256').update(text).digest('hex'),
    writePending: plan => {assert.equal(state.nativeLoaded, false); state.journal = clone(plan);},
    nativeConfig: () => {
      state.nativeLoaded = true;
      // LSS drops unrecognized VSS keys as it loads, before migration writes fields.
      const config = Object.assign({}, defaults);
      for (const key of Object.keys(config)) if (key in state.saved) config[key] = state.saved[key];
      config.diskReaderThreads = state.saved.diskReaderThreads;
      config.personalSentinel = state.saved.personalSentinel;
      state.saved = clone(config);
      config.validate = () => {};
      config.save = () => {
        if (state.failSave) throw Error('synthetic config save failure');
        state.saved = JSON.parse(JSON.stringify(config));
        state.saves++;
      };
      return config;
    },
    complete: () => {
      if (state.failComplete) throw Error('synthetic receipt commit failure');
      state.receipt = clone(state.journal); state.journal = null;
    },
    log: message => state.logs.push(message),
  };
  return {state, run: () => migration.runMigration(io)};
}

const fresh = fixture({...legacy, lodDistanceChunks: 177, personalSentinel: 'retain'});
fresh.run();
assert.equal(fresh.state.saved.mbPerSecondLimitGlobal * 1048576, legacy.totalBandwidthBytesPerSecond);
assert.equal(fresh.state.saved.maxConcurrentDiskReads, legacy.maxConcurrentNbtReads);
assert.equal(fresh.state.saved.lodStore, 'on');
assert.equal(fresh.state.saved.lodStoreMaxMB, legacy.persistentColumnCacheMaxMiB);
assert.equal(fresh.state.saved.lodStoreBackfill, false);
assert.equal(fresh.state.saved.lodDistanceChunks, 177);
assert.equal(fresh.state.saved.generationConcurrencyLimitGlobal, legacy.generationConcurrencyLimitGlobal);
assert.equal(fresh.state.saved.generationConcurrencyLimitPerPlayer, legacy.generationConcurrencyLimitPerPlayer);
assert.equal(fresh.state.saved.personalSentinel, 'retain');
checks++;

fresh.state.saved.lodStore = 'off';
fresh.state.saved.mbPerSecondLimitGlobal = 11;
fresh.run();
assert.equal(fresh.state.saved.lodStore, 'off');
assert.equal(fresh.state.saved.mbPerSecondLimitGlobal, 11);
assert.equal(fresh.state.saves, 1);
checks++;

const custom = fixture({...legacy, mbPerSecondLimitGlobal: 7, maxConcurrentDiskReads: 3,
  lodStore: 'off', lodStoreMaxMB: 333, lodStoreBackfill: true});
custom.run();
assert.equal(custom.state.saves, 0);
assert.equal(custom.state.receipt, null);
checks++;

const normalized = fixture(JSON.parse(normalizedText), {text: normalizedText});
normalized.run();
assert.equal(normalized.state.saved.mbPerSecondLimitGlobal * 1048576, 1000000);
assert.equal(normalized.state.saved.maxConcurrentDiskReads, 2);
assert.equal(normalized.state.saved.lodStore, 'on');
assert.equal(normalized.state.saved.lodStoreMaxMB, 1024);
assert.equal(normalized.state.saved.lodDistanceChunks, 128);
checks++;

for (const options of [{}, {canonical: true, text: normalizedText}]) {
  const untouched = fixture(JSON.parse(normalizedText), options);
  untouched.run();
  assert.equal(untouched.state.saves, 0);
  assert.equal(untouched.state.receipt, null);
  checks++;
}

const interrupted = fixture(legacy);
interrupted.state.failSave = true;
assert.throws(interrupted.run, /save failure/);
assert.ok(interrupted.state.journal);
interrupted.state.failSave = false;
interrupted.run();
assert.equal(interrupted.state.saved.lodStore, 'on');
assert.ok(interrupted.state.receipt);
checks++;

const receiptFailure = fixture(legacy);
receiptFailure.state.failComplete = true;
assert.throws(receiptFailure.run, /receipt commit failure/);
const saves = receiptFailure.state.saves;
receiptFailure.state.saved.lodStoreMaxMB = 4096;
receiptFailure.state.failComplete = false;
receiptFailure.run();
assert.equal(receiptFailure.state.saved.lodStoreMaxMB, 4096);
assert.equal(receiptFailure.state.saves, saves);
assert.ok(receiptFailure.state.receipt);
checks++;

const explicitBytes = migration.migrationPlan({...legacy, bytesPerSecondLimitGlobal: 4000000},
  'config/vss-server-config.json', 'personal');
assert.ok(!explicitBytes.changes.some(change => change.key === 'mbPerSecondLimitGlobal'));
checks++;
const invalidJournal = fixture(legacy);
invalidJournal.state.journal = {schemaVersion: 1, file: 'config/vss-server-config.json', changes: [
  {key: 'lodStore', before: 'off', after: 'on'}, {key: 'lodDistanceChunks', before: 128, after: 16}]};
assert.throws(invalidJournal.run, /Unrecognized/);
assert.equal(invalidJournal.state.nativeLoaded, false);
checks++;

function pathFixture(alias, dangling) {
  function nativePath(value) {
    return {'resolve(java.lang.String)': child => nativePath(value + '/' + child),
      equals: other => value === other.value, value};
  }
  const entries = {'.': {directory: true}, config: {directory: true},
    'config/vss-server-config.json': {}, local: {directory: true}};
  function file(relative) {
    const item = entries[relative];
    return {exists: () => !!item && relative !== dangling,
      isDirectory: () => !!item?.directory,
      toPath: () => ({toRealPath: () => nativePath(relative === alias ? 'outside' :
        relative === '.' ? 'game' : 'game/' + relative)}),
      listFiles: () => Object.keys(entries).filter(name => !name.includes('/') && name !== '.')
        .map(name => ({getName: () => name})),
      getName: () => relative.split('/').at(-1)};
  }
  return migration.createCheckedPath({getFileFromPath: file});
}
assert.ok(pathFixture()('config/vss-server-config.json'));
assert.throws(() => pathFixture('config')('config/vss-server-config.json'), /alias refused/);
assert.throws(() => pathFixture('config/vss-server-config.json')('config/vss-server-config.json'), /alias refused/);
assert.throws(() => pathFixture('local')('local/lanmc-lss-config-migration-v1.json'), /alias refused/);
assert.throws(() => pathFixture(null, 'local')('local/lanmc-lss-config-migration-v1.json'), /Unresolved/);
assert.throws(() => pathFixture()('../outside.json'), /Unexpected/);
checks += 6;
console.log(`PASS ${checks} LSS migration regression scenarios`);
