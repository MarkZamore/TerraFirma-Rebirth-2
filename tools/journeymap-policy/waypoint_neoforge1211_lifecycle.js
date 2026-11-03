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
