// Restore the authored VSS pacing/cache settings through the pinned LSS public API.
// Receipt version 1 is deliberately one-shot, so later player choices remain theirs.
(function () {
  var KNOWN_UPGRADE_SHA256 = '7f27c91d8c340cf3f946b7bf6c9aa4c3a883514229ea85d1b40f0f2720f98d81';
  var ALLOWED = ['mbPerSecondLimitGlobal', 'maxConcurrentDiskReads', 'lodStore',
    'lodStoreMaxMB', 'lodStoreBackfill'];

  function createCheckedPath(utils) {
    var root = utils.getFileFromPath('.');
    if (!root || !root.isDirectory()) throw new Error('LSS game directory is unavailable');
    var rootReal = root.toPath().toRealPath();
    return function (path) {
      if (typeof path !== 'string' || !/^(config|local)\/[a-z0-9_.-]+$/.test(path)) {
        throw new Error('Unexpected LSS migration path');
      }
      var parts = path.split('/'), prefix = '', expected = rootReal, parent = root, value;
      for (var i = 0; i < parts.length; i++) {
        prefix += (prefix ? '/' : '') + parts[i];
        expected = expected['resolve(java.lang.String)'](String(parts[i]));
        value = utils.getFileFromPath(prefix);
        if (value === null) throw new Error('KubeJS refused LSS migration path');
        if (value.exists()) {
          if (!value.toPath().toRealPath().equals(expected)) throw new Error('LSS path alias refused');
          if (i + 1 < parts.length && !value.isDirectory()) throw new Error('LSS parent is not a directory');
        } else if (parent.exists()) {
          // File.exists() is false for dangling Windows junctions. Refuse an
          // unresolved directory entry, including a case-insensitive alias.
          var entries = parent.listFiles();
          if (entries === null) throw new Error('Could not inspect LSS parent');
          for (var j = 0; j < entries.length; j++) {
            if (String(entries[j].getName()).toLowerCase() === parts[i].toLowerCase()) {
              throw new Error('Unresolved LSS path entry refused');
            }
          }
        }
        parent = value;
      }
      return value;
    };
  }

  function migrationPlan(raw, file, hash) {
    var changes = [];
    function missing(key) { return typeof raw[key] === 'undefined'; }
    function add(key, before, after) {
      if (before !== after) changes.push({key: key, before: before, after: after});
    }
    var mode = 'author-vss';
    if (file === 'config/vss-server-config.json' && hash === KNOWN_UPGRADE_SHA256) {
      mode = 'known-lss-upgrade';
      add('mbPerSecondLimitGlobal', 75, 1000000 / 1048576);
      add('maxConcurrentDiskReads', 0, 2);
      add('lodStore', 'off', 'on');
      add('lodStoreMaxMB', 0, 1024);
      // The authored cache fills from requested columns, without a whole-world walk.
      add('lodStoreBackfill', true, false);
    } else {
      if (missing('mbPerSecondLimitGlobal') && missing('bytesPerSecondLimitGlobal') &&
          typeof raw.totalBandwidthBytesPerSecond === 'number' &&
          raw.totalBandwidthBytesPerSecond >= 1024 && raw.totalBandwidthBytesPerSecond <= 1073741824) {
        add('mbPerSecondLimitGlobal', 75, raw.totalBandwidthBytesPerSecond / 1048576);
      }
      if (missing('maxConcurrentDiskReads') && typeof raw.maxConcurrentNbtReads === 'number' &&
          raw.maxConcurrentNbtReads >= 1 && raw.maxConcurrentNbtReads <= 64 &&
          Math.floor(raw.maxConcurrentNbtReads) === raw.maxConcurrentNbtReads) {
        add('maxConcurrentDiskReads', 0, raw.maxConcurrentNbtReads);
      }
      if (missing('lodStore') && raw.enablePersistentColumnCache === true) {
        add('lodStore', 'off', 'on');
        if (missing('lodStoreBackfill')) add('lodStoreBackfill', true, false);
      }
      if (missing('lodStoreMaxMB') && typeof raw.persistentColumnCacheMaxMiB === 'number' &&
          raw.persistentColumnCacheMaxMiB >= 64 && raw.persistentColumnCacheMaxMiB <= 1048576 &&
          Math.floor(raw.persistentColumnCacheMaxMiB) === raw.persistentColumnCacheMaxMiB) {
        add('lodStoreMaxMB', 0, raw.persistentColumnCacheMaxMiB);
      }
    }
    return {schemaVersion: 1, mode: mode, file: file, sourceSha256: hash, changes: changes};
  }

  function runMigration(io) {
    if (io.completed()) return;
    var plan = io.pending();
    if (!plan) {
      // Read before loading CONFIG: its static initializer rewrites legacy JSON.
      var file = io.exists('config/lss-server-config.json')
        ? 'config/lss-server-config.json' : 'config/vss-server-config.json';
      if (!io.exists(file)) return;
      var text = io.text(file), raw = JSON.parse(text);
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('LSS config must be an object');
      plan = migrationPlan(raw, file, io.sha256(text));
      if (plan.changes.length === 0) return;
      io.writePending(plan);
    }
    if (plan.schemaVersion !== 1 || !Array.isArray(plan.changes) ||
        (plan.file !== 'config/lss-server-config.json' && plan.file !== 'config/vss-server-config.json')) {
      throw new Error('Unrecognized LSS migration receipt');
    }
    // Validate the entire journal before changing any live field.
    for (var k = 0; k < plan.changes.length; k++) {
      var action = plan.changes[k];
      if (!action || ALLOWED.indexOf(action.key) < 0 || typeof action.before !== typeof action.after ||
          ['number', 'boolean', 'string'].indexOf(typeof action.after) < 0) {
        throw new Error('Unrecognized LSS migration key or value');
      }
    }
    var config = io.nativeConfig(), changed = false, applied = [];
    for (var i = 0; i < plan.changes.length; i++) {
      var change = plan.changes[i];
      // Resume interrupted saves, preserving a subsequent personal change.
      if (config[change.key] === change.before) {
        config[change.key] = change.after;
        applied.push(change);
        changed = true;
      }
    }
    if (changed) {
      config.validate();
      config.save();
      var saved = JSON.parse(io.text(plan.file));
      for (var j = 0; j < plan.changes.length; j++) {
        var item = plan.changes[j];
        if (saved[item.key] !== config[item.key]) throw new Error('LSS config save verification failed for ' + item.key);
      }
    }
    io.complete();
    io.log('LSS legacy configuration migration completed (' + plan.mode + '): ' +
      JSON.stringify(applied));
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {migrationPlan: migrationPlan, runMigration: runMigration,
      knownUpgradeSha256: KNOWN_UPGRADE_SHA256, createCheckedPath: createCheckedPath};
    return;
  }
  if (!Platform.isLoaded('lss')) return;
  try {
    var gameRoot = Java.loadClass('dev.latvian.mods.kubejs.KubeJSPaths').GAMEDIR;
    var utils = {getFileFromPath: function (relative) {
      return gameRoot['resolve(java.lang.String)'](String(relative)).normalize().toFile();
    }};
    var files = Java.loadClass('org.apache.commons.io.FileUtils');
    var digest = Java.loadClass('org.apache.commons.codec.digest.DigestUtils');
    var base = 'local/lanmc-lss-config-migration-v1';
    var file = createCheckedPath(utils);
    var read = function (path) {
      return String(files['readFileToString(java.io.File,java.lang.String)'](file(path), 'UTF-8'));
    };
    runMigration({
      completed: function () { return file(base + '.json').isFile(); },
      pending: function () {
        return file(base + '.pending.json').isFile() ? JSON.parse(read(base + '.pending.json')) : null;
      },
      exists: function (path) { return file(path).isFile(); },
      text: read,
      sha256: function (text) { return String(digest['sha256Hex(java.lang.String)'](text)); },
      nativeConfig: function () {
        // CONFIG.load/save writes the selected config's .tmp sibling as well.
        file('config/lss-server-config.json');
        file('config/vss-server-config.json');
        file('config/lss-server-config.json.tmp');
        file('config/vss-server-config.json.tmp');
        return Java.loadClass('dev.vox.lss.config.LSSServerConfig').CONFIG;
      },
      writePending: function (plan) {
        var target = file(base + '.pending.json'), staged = file(base + '.staged.json');
        files.forceMkdir(target.getParentFile());
        var encoded = JSON.stringify(plan) + '\n';
        if (staged.exists()) {
          if (read(base + '.staged.json') !== encoded) throw new Error('Unrecognized LSS staged receipt retained');
        } else {
          if (!file(base + '.staged.json').createNewFile()) throw new Error('LSS staged receipt already exists');
          files['writeStringToFile(java.io.File,java.lang.String,java.lang.String)'](file(base + '.staged.json'), encoded, 'UTF-8');
        }
        if (read(base + '.staged.json') !== encoded || target.exists() || !staged.renameTo(target)) {
          throw new Error('Could not commit LSS migration journal');
        }
      },
      complete: function () {
        if (!file(base + '.pending.json').renameTo(file(base + '.json'))) {
          throw new Error('Could not finish LSS migration receipt');
        }
      },
      log: function (message) { console.info(message); }
    });
  } catch (error) {
    console.error('LSS legacy configuration migration failed: ' + error);
  }
})();
