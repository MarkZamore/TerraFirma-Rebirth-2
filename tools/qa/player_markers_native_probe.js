// QA-only. Copy into the independent fixture's startup_scripts, never the pack.
// Runs once after an integrated test world opens and removes its synthetic data.
if (Platform.isClientEnvironment() && Platform.isLoaded('journeymap')) {
  (function () {
    var Minecraft = Java.loadClass('net.minecraft.client.Minecraft');
    var RemotePlayer = Java.loadClass('net.minecraft.client.player.RemotePlayer');
    var GameProfile = Java.loadClass('com.mojang.authlib.GameProfile');
    var UUID = Java.loadClass('java.util.UUID');
    var RemovalReason = Java.loadClass('net.minecraft.world.entity.Entity$RemovalReason');
    var PlayerRadarManager = Java.loadClass('journeymap.common.util.PlayerRadarManager');
    var WaypointStore = Java.loadClass('journeymap.common.waypoint.WaypointStore');
    var FileUtils = Java.loadClass('org.apache.commons.io.FileUtils');
    var GamePaths = Java.loadClass('dev.latvian.mods.kubejs.KubeJSPaths');
    var PlayerInfoUpdate = Java.loadClass('net.minecraft.network.protocol.game.ClientboundPlayerInfoUpdatePacket');
    var PlayerInfoAction = Java.loadClass('net.minecraft.network.protocol.game.ClientboundPlayerInfoUpdatePacket$Action');
    var PlayerInfoRemove = Java.loadClass('net.minecraft.network.protocol.game.ClientboundPlayerInfoRemovePacket');
    var ServerPlayer = Java.loadClass('net.minecraft.server.level.ServerPlayer');
    var ClientInformation = Java.loadClass('net.minecraft.server.level.ClientInformation');
    var ArrayList = Java.loadClass('java.util.ArrayList');
    var handler = null;
    var capturedNativeEvents = {
      onEvent: function (type, callback) {
        if (String(type) !== 'net.neoforged.neoforge.client.event.ClientTickEvent$Post') {
          throw new Error('Unexpected marker event ' + type);
        }
        handler = callback;
      }
    };
    (function (NativeEvents) {
      var source = GamePaths.GAMEDIR['resolve(java.lang.String)']('kubejs/startup_scripts/lanmc_journeymap_player_markers.js').toFile();
      eval(String(FileUtils['readFileToString(java.io.File,java.lang.String)'](source, 'UTF-8')));
    })(capturedNativeEvents);
    var ticks = 0;
    var complete = false;
    NativeEvents.onEvent('net.neoforged.neoforge.client.event.ClientTickEvent$Post', function () {
      if (complete) return;
      var mc = Minecraft.getInstance();
      if (mc.level === null || mc.player === null || !mc.hasSingleplayerServer()) return;
      ticks++;
      if (ticks < 40) return;
      complete = true;
      var result = {schemaVersion: 1, checks: [], status: 'FAIL', scope: 'native synthetic integrated-client fixture'};
      var uuid = UUID.randomUUID();
      var name = 'LanmcJMProbe';
      var profile = new GameProfile(uuid, name);
      var level = mc.level;
      var radar = PlayerRadarManager.getInstance().getPlayers();
      var store = WaypointStore.getInstance();
      var actual = null;
      var tabAdded = false;
      var id = -2147483000;
      function check(value, message) {
        if (!value) throw new Error(message);
        result.checks.push(message);
      }
      function ghost(x) {
        var value = new RemotePlayer(level, profile);
        value.setPos(x, mc.player.getY(), mc.player.getZ());
        return value;
      }
      function point() {
        var iterator = store.getPlayerPoints().iterator();
        while (iterator.hasNext()) {
          var value = iterator.next();
          if (String(value.getDisplayName()) === name) return value;
        }
        throw new Error('Synthetic player marker missing');
      }
      try {
        check(handler !== null, 'actual source registered its native callback');
        check(level.getEntity(id) === null, 'synthetic entity ID is unused');
        // Entry(ServerPlayer) reads connection.latency() even for ADD_PLAYER.
        // Borrow that read-only getter from the fixture host without joining
        // the synthetic player to the integrated server or creating a socket.
        var server = mc.getSingleplayerServer();
        var host = server.getPlayerList().getPlayer(mc.player.getUUID());
        var syntheticServerPlayer = new ServerPlayer(server, server.overworld(), profile, ClientInformation.createDefault());
        syntheticServerPlayer.connection = host.connection;
        try {
          mc.getConnection().handlePlayerInfoUpdate(new PlayerInfoUpdate(PlayerInfoAction.ADD_PLAYER, syntheticServerPlayer));
          tabAdded = true;
        } finally {
          syntheticServerPlayer.connection = null;
        }
        check(mc.getConnection().getPlayerInfo(uuid) !== null, 'native tab-list profile permits radar updates');
        actual = ghost(100);
        actual.setId(id);
        level.addEntity(actual);
        check(level.players.contains(actual), 'native ClientLevel tracks the synthetic RemotePlayer');
        radar.put(uuid, ghost(1));
        store.getPlayerPoints().clear();
        check(point().getX() === 1, 'baseline marker starts at original radar object');
        radar.put(uuid, ghost(2));
        check(point().getX() === 1, 'baseline preserves stale reference after same-count object replacement');
        handler();
        check(point().getX() === 100, 'actual source refreshes the host marker to the native loaded player');
        actual.setPos(110, actual.getY(), actual.getZ());
        handler();
        check(point().getX() === 110, 'marker follows subsequent native player movement');
        radar.put(uuid, ghost(3));
        actual.setPos(120, actual.getY(), actual.getZ());
        handler();
        check(point().getX() === 120, 'same-count replacement refreshes the active marker cache');
        radar.put(uuid, null);
        handler();
        check(radar.get(uuid) === null, 'native hidden radar record remains hidden');
        actual.setInvisible(true);
        radar.put(uuid, ghost(4));
        handler();
        check(radar.get(uuid).getX() === 4, 'invisible loaded player does not override native radar data');
        result.status = 'PASS';
      } catch (error) {
        result.error = String(error);
        console.error('Player marker native proof failed: ' + error);
      } finally {
        radar.remove(uuid);
        var current = level.getEntity(id);
        if (actual !== null && current !== null && current.equals(actual)) level.removeEntity(id, RemovalReason.DISCARDED);
        if (tabAdded) {
          var removed = new ArrayList();
          removed.add(uuid);
          mc.getConnection().handlePlayerInfoRemove(new PlayerInfoRemove(removed));
        }
        store.getPlayerPoints().clear();
        result.syntheticPlayerRemoved = radar.get(uuid) === null && level.getEntity(id) === null;
        JsonIO.write('logs/lanmc-player-marker-native-proof.json', result);
        console.info('Player marker native proof ' + result.status + ', checks=' + result.checks.length);
      }
    });
  })();
}
