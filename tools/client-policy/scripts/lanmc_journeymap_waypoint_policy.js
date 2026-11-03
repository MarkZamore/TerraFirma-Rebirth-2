// Pack-owned JourneyMap waypoint generation association; images and game mechanics are unchanged.
(function () {
// Pack-owned JourneyMap 5.10 singleplayer waypoint association.
// IO is supplied by the checked KubeJS adapter. No map images are moved.
// Required IO: exists, readJson, writeJsonAtomic, mkdirs, rename, removeFile,
// assertPath, uuid, now. All paths are relative to the game directory.
function createWaypointGenerationPolicy(io) {
  var WORLD_FILE = 'lanmc-waypoints-world.json';
  var OWNER_FILE = 'lanmc-waypoints-owner.json';
  var JOURNAL_FILE = 'lanmc-waypoints-transition.json';
  var ARCHIVES = 'local/lanmc-waypoint-archives/v1';
  var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

  function requireValue(ok, message) {
    if (!ok) throw new Error('Waypoint association: ' + message);
  }
  function path(value) {
    requireValue(typeof value === 'string' && value.length > 0, 'missing path');
    requireValue(value.indexOf('\\') < 0 && value.indexOf(':') < 0 && value.charAt(0) !== '/', 'non-relative path');
    var parts = value.split('/');
    for (var i = 0; i < parts.length; i++) {
      requireValue(parts[i] !== '' && parts[i] !== '.' && parts[i] !== '..', 'unsafe path component');
    }
    io.assertPath(value);
    return value;
  }
  function id(value) {
    requireValue(typeof value === 'string' && UUID.test(value), 'invalid UUID');
    return value;
  }
  function read(file) {
    file = path(file);
    if (!io.exists(file)) return null;
    var value = io.readJson(file);
    requireValue(value !== null && typeof value === 'object', 'existing metadata is not a JSON object');
    return value;
  }
  function write(file, value) { io.writeJsonAtomic(path(file), value); }
  function move(from, to) {
    from = path(from); to = path(to);
    requireValue(io.exists(from) && !io.exists(to), 'rename would overwrite or source disappeared');
    io.mkdirs(path(to.substring(0, to.lastIndexOf('/'))));
    requireValue(io.rename(from, to) === true, 'rename failed; original data retained');
    requireValue(!io.exists(from) && io.exists(to), 'rename postcondition failed');
  }
  function validWorld(world) {
    requireValue(world && world.schemaVersion === 1, 'invalid world metadata');
    id(world.worldId);
    requireValue(typeof world.legacyEligible === 'boolean', 'invalid legacy ownership');
    requireValue(typeof world.birthMillis === 'number' && world.birthMillis >= 0, 'invalid world birth');
    return world;
  }
  function worldFor(saveRoot, legacyEligible) {
    var file = path(saveRoot + '/' + WORLD_FILE);
    var world = read(file);
    if (!world) {
      world = {schemaVersion: 1, worldId: id(io.uuid()), legacyEligible: legacyEligible,
        birthMillis: legacyEligible ? 0 : io.now()};
      write(file, world);
    }
    return validWorld(world);
  }
  function validOwner(owner) {
    requireValue(owner && owner.schemaVersion === 1, 'invalid waypoint ownership metadata');
    id(owner.slotId); id(owner.worldId);
    return owner;
  }
  function archived(slotId, worldId) {
    return path(ARCHIVES + '/' + id(slotId) + '/' + id(worldId) + '/waypoints');
  }
  function ownerFor(slotId, worldId) {
    return {schemaVersion: 1, slotId: id(slotId), worldId: id(worldId)};
  }
  function finish(mapRoot, journal) {
    requireValue(journal && journal.schemaVersion === 1, 'invalid transition journal');
    id(journal.slotId); id(journal.newWorldId);
    if (journal.oldWorldId !== null) id(journal.oldWorldId);
    requireValue(typeof journal.hadOutgoing === 'boolean' && typeof journal.hadIncoming === 'boolean', 'invalid transition flags');
    requireValue(journal.oldWorldId !== journal.newWorldId, 'self transition');
    requireValue(journal.phase === 'prepared' || journal.phase === 'outgoing_stored' || journal.phase === 'incoming_restored', 'invalid transition phase');
    var live = path(mapRoot + '/waypoints');
    var oldPath = journal.oldWorldId === null ? null : archived(journal.slotId, journal.oldWorldId);
    var newPath = archived(journal.slotId, journal.newWorldId);
    var journalPath = path(mapRoot + '/' + JOURNAL_FILE);

    // Commit precedes journal retirement. A failed delete or process exit must
    // not reinterpret legitimate points created AFTER the ownership commit.
    if (journal.phase === 'incoming_restored') {
      var committed = read(mapRoot + '/' + OWNER_FILE);
      if (committed) {
        validOwner(committed);
        if (committed.slotId === journal.slotId && committed.worldId === journal.newWorldId) {
          requireValue(io.removeFile(journalPath) === true, 'committed journal could not be removed');
          return;
        }
      }
    }
    var previousOwner = read(mapRoot + '/' + OWNER_FILE);
    if (previousOwner) {
      validOwner(previousOwner);
      requireValue(previousOwner.slotId === journal.slotId && previousOwner.worldId === journal.oldWorldId,
        'transition journal does not match current ownership');
    }

    // The journal is durable before each rename. Resume only unambiguous states.
    // An unexpected late writer/collision is retained and reported, never deleted.
    if (journal.phase === 'prepared') {
      if (journal.hadOutgoing) {
        requireValue(oldPath !== null, 'outgoing data without an owner');
        if (io.exists(live) && !io.exists(oldPath)) move(live, oldPath);
        else requireValue(!io.exists(live) && io.exists(oldPath), 'ambiguous outgoing state');
      } else requireValue(!io.exists(live), 'unexpected outgoing waypoint directory');
      journal.phase = 'outgoing_stored'; write(journalPath, journal);
    }
    if (journal.phase === 'outgoing_stored') {
      if (journal.hadIncoming) {
        if (io.exists(newPath) && !io.exists(live)) move(newPath, live);
        else requireValue(!io.exists(newPath) && io.exists(live), 'ambiguous incoming state');
      } else requireValue(!io.exists(live) && !io.exists(newPath), 'unexpected incoming waypoint directory');
      journal.phase = 'incoming_restored'; write(journalPath, journal);
    }
    if (journal.hadIncoming) requireValue(io.exists(live) && !io.exists(newPath), 'incoming data missing');
    else requireValue(!io.exists(live) && !io.exists(newPath), 'unexpected data before ownership commit');
    write(mapRoot + '/' + OWNER_FILE, ownerFor(journal.slotId, journal.newWorldId));
    requireValue(io.removeFile(journalPath) === true, 'completed journal could not be removed');
  }
  function associate(saveRoot, mapRoot) {
    saveRoot = path(saveRoot); mapRoot = path(mapRoot);
    var world = worldFor(saveRoot, false);
    io.mkdirs(mapRoot);
    var pending = read(mapRoot + '/' + JOURNAL_FILE);
    if (pending) finish(mapRoot, pending);
    var owner = read(mapRoot + '/' + OWNER_FILE);
    if (owner) validOwner(owner);
    var live = path(mapRoot + '/waypoints');
    if (!owner && world.legacyEligible) {
      // First installation adopts the existing world's own data unchanged.
      owner = ownerFor(io.uuid(), world.worldId);
      write(mapRoot + '/' + OWNER_FILE, owner);
    }
    if (!owner || owner.worldId !== world.worldId) {
      var slotId = owner ? owner.slotId : id(io.uuid());
      var oldId = owner ? owner.worldId : (io.exists(live) ? id(io.uuid()) : null);
      var oldPath = oldId === null ? null : archived(slotId, oldId);
      var incoming = archived(slotId, world.worldId);
      requireValue(oldPath === null || !io.exists(oldPath), 'outgoing archive already exists');
      var journal = {schemaVersion: 1, slotId: slotId, oldWorldId: oldId,
        newWorldId: world.worldId, hadOutgoing: io.exists(live),
        hadIncoming: io.exists(incoming), phase: 'prepared'};
      write(mapRoot + '/' + JOURNAL_FILE, journal);
      finish(mapRoot, journal);
    }
    if (world.legacyEligible) {
      world.legacyEligible = false;
      write(saveRoot + '/' + WORLD_FILE, world);
    }
    return {worldId: world.worldId, birthMillis: world.birthMillis, mapRoot: mapRoot};
  }
  function bootstrap(existingSaves) {
    for (var i = 0; i < existingSaves.length; i++) {
      var item = existingSaves[i];
      var world = worldFor(path(item.saveRoot), true);
      // Only an existing map path is claimed. Do not create an empty canonical
      // directory that would override JourneyMap's native legacy-name fallback.
      if (item.mapRoot && io.exists(path(item.mapRoot))) associate(item.saveRoot, item.mapRoot);
    }
  }
  return {bootstrap: bootstrap, associate: associate};
}

// Pack-owned IO for waypoint_generation_core.js. No launcher hooks or filter changes.
// Optional bridge is only for private fixtures; production uses the normal KubeJS APIs.
function createWaypointIo(bridge) {
  bridge = bridge || {};
  var utils = bridge.utils || Java.loadClass('dev.latvian.mods.kubejs.util.UtilsJS');
  var files = bridge.files || Java.loadClass('org.apache.commons.io.FileUtils');
  var uuids = bridge.uuids || Java.loadClass('java.util.UUID');
  var root = bridge.rootFile || utils.getFileFromPath('.');
  if (!root || !root.isDirectory()) throw new Error('Waypoint IO: game directory is unavailable');
  var rootReal = root.toPath().toRealPath();
  var rootLexical = root.toPath().toAbsolutePath().normalize();
  // The native level storage directory is the sole permitted root alias.
  // Launchers legitimately place saves behind a junction. Pin that target
  // once, while retaining exact-path checks for every child and all other IO.
  var savesLexical = null;
  var savesReal = null;
  if (bridge.nativeSavesRootFile) {
    savesLexical = child(rootLexical, 'saves');
    var nativeSaves = bridge.nativeSavesRootFile;
    need(nativeSaves.isDirectory(), 'native saves root is unavailable');
    need(nativeSaves.toPath().toAbsolutePath().normalize().equals(savesLexical), 'unexpected native saves root');
    savesReal = nativeSaves.toPath().toRealPath();
  }
  var ignoreCase = String(root.toPath().getFileSystem().getSeparator()) === '\\';
  var busy = {};
  var NEW = '.lanmc-waypoint-new';
  var OLD = '.lanmc-waypoint-old';

  function need(ok, message) {
    if (!ok) throw new Error('Waypoint IO: ' + message);
  }
  function child(parent, name) {
    // KubeJS registers accept-all String and Path wrappers. Select the Java
    // overload explicitly; even a typed Path is otherwise ambiguous.
    return parent['resolve(java.lang.String)'](name);
  }
  function within(candidate, anchor) {
    // The native String overload still compares Path components. Passing an
    // external canonical Path as an argument would invoke KubeJS's Path
    // wrapper and incorrectly reject the authoritative saves junction target.
    return candidate['startsWith(java.lang.String)'](String(anchor.toString()));
  }
  function relativeTo(candidate, anchor) {
    need(within(candidate, anchor) && !candidate.equals(anchor), 'missing child path');
    return String(candidate.subpath(anchor.getNameCount(), candidate.getNameCount()).toString()).replace(/\\/g, '/');
  }
  function parts(relative) {
    need(typeof relative === 'string' && relative.length > 0, 'missing relative path');
    need(relative.indexOf('\\') < 0 && relative.indexOf(':') < 0 && relative.indexOf('\u0000') < 0 && relative.charAt(0) !== '/', 'non-relative path');
    var result = relative.split('/');
    for (var i = 0; i < result.length; i++) {
      need(result[i] !== '' && result[i] !== '.' && result[i] !== '..', 'unsafe path component');
    }
    return result;
  }
  function checked(relative) {
    var components = parts(relative), prefix = '', expected = rootReal, result = null, parent = root;
    for (var i = 0; i < components.length; i++) {
      prefix += (prefix ? '/' : '') + components[i];
      expected = i === 0 && components[i] === 'saves' && savesReal !== null
        ? savesReal : child(expected, components[i]);
      result = utils.getFileFromPath(prefix);
      need(result !== null, 'KubeJS refused the path');
      if (result.exists()) {
        // File.getCanonicalPath and FileUtils.isSymlink do NOT detect Windows
        // junctions reliably. Native returned Path.toRealPath resolves them.
        var actual = result.toPath().toRealPath();
        var anchor = components[0] === 'saves' && savesReal !== null ? savesReal : rootReal;
        need(within(actual, anchor) && actual.equals(expected), 'linked or aliased path refused');
        if (i + 1 < components.length) need(result.isDirectory(), 'parent is not a directory');
      } else if (parent.exists()) {
        // File.exists() is false for a dangling junction. Its directory entry
        // still exists, so an absent path is safe only when its existing
        // parent can be listed and contains no matching unresolved entry.
        need(parent.isDirectory(), 'parent is not a directory');
        var entries = parent.listFiles();
        need(entries !== null, 'could not inspect the existing parent');
        var wanted = ignoreCase ? components[i].toLowerCase() : components[i];
        for (var j = 0; j < entries.length; j++) {
          var entryName = String(entries[j].getName());
          if (ignoreCase) entryName = entryName.toLowerCase();
          need(entryName !== wanted, 'unresolvable directory entry refused');
        }
      }
      parent = result;
    }
    return result;
  }
  function isMeta(relative) {
    var p = parts(relative);
    if (p.length === 3 && p[0] === 'saves' && p[2] === 'lanmc-waypoints-world.json') return true;
    if (p.length === 5 && p[0] === 'journeymap' && p[1] === 'data' && p[2] === 'sp' &&
        (p[4] === 'lanmc-waypoints-owner.json' || p[4] === 'lanmc-waypoints-transition.json')) return true;
    return p.length === 2 && p[0] === 'local' && p[1] === 'lanmc-waypoints-bootstrap.json';
  }
  function mkdirs(relative) {
    var target = checked(relative);
    if (!target.exists()) need(target.mkdirs() === true, 'could not create directory');
    target = checked(relative);
    need(target.isDirectory(), 'directory expected');
  }
  function rawRename(from, to) {
    var source = checked(from), target = checked(to);
    need(source.exists() && !target.exists(), 'rename source missing or target already exists');
    var ok = source.renameTo(target);
    need(ok === true && !checked(from).exists() && checked(to).exists(), 'rename failed; data retained');
  }
  function text(relative) {
    var target = checked(relative);
    need(target.isFile(), 'JSON metadata is not a regular file');
    return String(files['readFileToString(java.io.File,java.lang.String)'](target, 'UTF-8'));
  }
  function object(relative) {
    var value = JSON.parse(text(relative));
    need(value !== null && typeof value === 'object' && !Array.isArray(value), 'metadata is not a JSON object');
    return value;
  }
  function uuid() { return String(uuids.randomUUID().toString()); }
  function quarantine(relative) {
    if (!checked(relative).exists()) return;
    var target = relative + '.retained-' + uuid();
    need(!checked(target).exists(), 'retained metadata name collision');
    rawRename(relative, target);
  }
  function recover(relative) {
    need(isMeta(relative), 'unowned JSON transaction');
    need(!busy[relative], 'metadata transaction is already in progress');
    var previous = relative + OLD, staged = relative + NEW;
    if (checked(previous).exists()) {
      object(previous);
      if (!checked(relative).exists()) rawRename(previous, relative);
      else {
        object(relative);
        // A current valid JSON plus an old backup means commit completed.
        // Retain the backup rather than guessing whether an interrupted
        // cleanup or an external writer left it here.
        quarantine(previous);
      }
    }
    // A staged file is not authoritative before its no-overwrite rename.
    // Retain even a partial staged file; never delete an unknown payload.
    quarantine(staged);
  }
  function exists(relative) {
    var target = checked(relative);
    return target.exists() || (isMeta(relative) && checked(relative + OLD).exists());
  }
  function readJson(relative) {
    if (isMeta(relative)) recover(relative);
    return JSON.parse(text(relative));
  }
  function writeJsonAtomic(relative, value) {
    need(isMeta(relative), 'only owned waypoint metadata may be written');
    need(value !== null && typeof value === 'object' && !Array.isArray(value), 'metadata is not an object');
    recover(relative);
    var target = checked(relative), previous = relative + OLD, staged = relative + NEW;
    var encoded = JSON.stringify(value) + '\n';
    need(typeof encoded === 'string', 'metadata could not be serialized');
    if (target.exists()) object(relative);
    var slash = relative.lastIndexOf('/');
    need(slash > 0, 'metadata must have an owned parent directory');
    mkdirs(relative.substring(0, slash));
    need(!checked(previous).exists() && !checked(staged).exists(), 'unfinished metadata transaction');
    busy[relative] = true;
    var hadOld = target.exists();
    try {
      need(checked(staged).createNewFile() === true, 'staged metadata already exists');
      files['writeStringToFile(java.io.File,java.lang.String,java.lang.String)'](checked(staged), encoded, 'UTF-8');
      need(text(staged) === encoded, 'staged metadata readback mismatch');
      object(staged);
      if (hadOld) rawRename(relative, previous);
      rawRename(staged, relative);
      need(text(relative) === encoded, 'committed metadata readback mismatch');
      if (hadOld) need(checked(previous).delete() === true, 'committed metadata backup could not be removed');
    } catch (failure) {
      // Restore the old authoritative file when the replacement did not
      // commit. All orphaned/staged bytes are retained under unique names.
      try {
        if (checked(previous).exists()) {
          if (checked(relative).exists()) quarantine(relative);
          rawRename(previous, relative);
        } else if (!hadOld && checked(relative).exists()) quarantine(relative);
        quarantine(staged);
      } catch (rollback) {
        throw new Error('Waypoint IO: transaction failed; original/retained metadata requires recovery: ' + failure + '; ' + rollback);
      }
      throw failure;
    } finally {
      delete busy[relative];
    }
  }
  function removeFile(relative) {
    need(isMeta(relative), 'only owned waypoint metadata may be removed');
    recover(relative);
    var target = checked(relative);
    need(!target.exists() || target.isFile(), 'refusing to remove a directory');
    return target.exists() && target.delete() === true;
  }
  function relativeFile(file, allowMissing) {
    need(file !== null, 'source directory is unavailable');
    var lexical = file.toPath().toAbsolutePath().normalize();
    var relative;
    if (within(lexical, rootLexical) && !lexical.equals(rootLexical)) {
      relative = relativeTo(lexical, rootLexical);
    } else {
      // Some native storage implementations return a canonical world path.
      // It may use only the previously pinned authoritative saves anchor.
      need(savesReal !== null && within(lexical, savesReal) && !lexical.equals(savesReal), 'source path escaped owned roots');
      relative = 'saves/' + relativeTo(lexical, savesReal);
    }
    // The pure JourneyMap resolver may name a map directory that has not
    // been created yet. Validate its lexical path and every existing
    // ancestor before the policy creates anything. Server world roots
    // retain the existing-only default.
    var guarded = checked(relative);
    if (file.exists()) {
      var actual = file.toPath().toRealPath();
      var anchor = parts(relative)[0] === 'saves' && savesReal !== null ? savesReal : rootReal;
      need(within(actual, anchor) && !actual.equals(anchor), 'source path escaped owned roots');
      need(guarded.exists() && guarded.toPath().toRealPath().equals(actual), 'source alias changed');
    } else need(allowMissing === true && !guarded.exists(), 'source directory does not exist');
    return relative;
  }
  return {
    exists: exists, readJson: readJson, writeJsonAtomic: writeJsonAtomic,
    mkdirs: mkdirs, rename: function(from, to) { rawRename(from, to); return true; },
    removeFile: removeFile, assertPath: function(relative) { checked(relative); },
    uuid: uuid, now: function() { return new Date().getTime(); },
    file: checked, relativeFile: relativeFile, rootFile: root
  };
}

// Concatenate after the reviewed shared generation core and guarded IO adapter.
// Exact reviewed targets: Minecraft 1.21.1 / NeoForge, JourneyMap 6.0.3 and 6.0.9,
// KubeJS 2101.7.2 builds 368 and 377. This is a pack-owned startup policy.
if (Platform.isClientEnvironment() && Platform.isLoaded('journeymap')) {
  (function () {
    var io = null;
    var policy = null;
    var ready = false;
    var Minecraft = null;
    var LevelResource = null;
    var FileHandler = null;
    var JourneyMapClient = null;
    var jmRoot = 'journeymap/data/sp/';
    StartupEvents.postInit(function () {
      ready = false;
      try {
        Minecraft = Java.loadClass('net.minecraft.client.Minecraft');
        var gameRoot = Java.loadClass('dev.latvian.mods.kubejs.KubeJSPaths').GAMEDIR;
        var nativeSaves = Minecraft.getInstance().getLevelSource().getBaseDir().toFile();
        // UtilsJS.getFileFromPath was removed in KubeJS 7. The authoritative
        // game root is its public GAMEDIR; the shared IO still validates every
        // component and pins only the native top-level saves junction.
        var pathFacade = {getFileFromPath: function (relative) {
          return gameRoot['resolve(java.lang.String)'](String(relative)).normalize().toFile();
        }};
        io = createWaypointIo({rootFile: gameRoot.toFile(), utils: pathFacade,
          nativeSavesRootFile: nativeSaves});
        policy = createWaypointGenerationPolicy(io);
        LevelResource = Java.loadClass('net.minecraft.world.level.storage.LevelResource');
        FileHandler = Java.loadClass('journeymap.client.io.FileHandler');
        JourneyMapClient = Java.loadClass('journeymap.client.JourneymapClient');
        var root = io.file('saves');
        var files = null;
        if (root.exists()) {
          if (!root.isDirectory()) throw new Error('Saves path is not a directory');
          files = root.listFiles();
          if (files === null) throw new Error('Existing saves could not be enumerated');
        }
        var saves = [];
        if (files) {
          for (var i = 0; i < files.length; i++) {
            if (!files[i].isDirectory()) continue;
            var folder = String(files[i].getName());
            var saveRoot = 'saves/' + folder;
            io.assertPath(saveRoot);
            if (!io.exists(saveRoot + '/level.dat') && !io.exists(saveRoot + '/level.dat_old')) continue;
            saves.push({saveRoot: saveRoot, mapRoot: jmRoot + folder});
          }
        }
        policy.bootstrap(saves);
        ready = true;
      } catch (error) {
        console.warn('Waypoint association bootstrap failed; existing waypoints are preserved: ' + error);
      }
    });
    // JourneyMap's own LoggingIn listener is NORMAL priority. Associate before
    // its handshake and the next mapping tick; no render or gameplay settings.
    var priority = Java.loadClass('net.neoforged.bus.api.EventPriority').HIGHEST;
    NativeEvents.onEvent(priority, 'net.neoforged.neoforge.client.event.ClientPlayerNetworkEvent$LoggingIn', function () {
      global.lanmcWaypointGeneration = null;
      if (!ready || !policy || !io) {
        console.warn('Waypoint association is not ready; existing waypoints are preserved');
        return;
      }
      try {
        var mc = Minecraft.getInstance();
        if (!mc.hasSingleplayerServer()) return;
        var server = mc.getSingleplayerServer();
        if (server === null) throw new Error('Missing integrated server');
        var saveRoot = String(io.relativeFile(server.getWorldPath(LevelResource.ROOT).toFile()));
        if (!/^saves\/[^/]+$/.test(saveRoot)) throw new Error('Unexpected world directory');
        io.assertPath(saveRoot);
        io.assertPath('journeymap/data/sp');
        var worldId = JourneyMapClient.getInstance().getCurrentWorldId();
        var mapRoot = String(io.relativeFile(FileHandler.getJMWorldDirForWorldId(mc, worldId), true));
        if (!/^journeymap\/data\/sp\/[^/]+$/.test(mapRoot)) throw new Error('Unexpected JourneyMap directory');
        io.assertPath(mapRoot);
        global.lanmcWaypointGeneration = policy.associate(saveRoot, mapRoot);
      } catch (error) {
        console.warn('Waypoint association skipped; existing waypoints are preserved: ' + error);
      }
    });
  })();
}

})();
