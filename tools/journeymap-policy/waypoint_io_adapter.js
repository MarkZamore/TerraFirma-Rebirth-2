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
