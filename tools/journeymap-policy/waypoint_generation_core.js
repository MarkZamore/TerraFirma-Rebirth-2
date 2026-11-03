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
