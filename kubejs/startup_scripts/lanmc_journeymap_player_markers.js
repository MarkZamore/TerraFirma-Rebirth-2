// Keep the integrated host's JourneyMap player markers attached to live players.
// Only existing, visible radar entries are refreshed through JourneyMap's own API.
if (Platform.isClientEnvironment() && Platform.isLoaded('journeymap')) {
  (function () {
    var Minecraft = Java.loadClass('net.minecraft.client.Minecraft');
    var PlayerRadarManager = Java.loadClass('journeymap.common.util.PlayerRadarManager');
    var PlayerLoc = Java.loadClass('journeymap.common.network.model.PlayerLoc');
    var WaypointStore = Java.loadClass('journeymap.common.waypoint.WaypointStore');
    var IdentityHashMap = Java.loadClass('java.util.IdentityHashMap');
    var previousObjects = null;
    var reported = false;
    var failed = false;

    NativeEvents.onEvent('net.neoforged.neoforge.client.event.ClientTickEvent$Post', function () {
      if (failed) return;
      var mc = Minecraft.getInstance();
      if (mc.level === null || mc.player === null || !mc.hasSingleplayerServer()) {
        previousObjects = null;
        return;
      }
      try {
        var radar = PlayerRadarManager.getInstance();
        var players = radar.getPlayers();
        // KubeJS exposes players as a collection property, not a callable.
        var livePlayers = mc.level.players.iterator();
        while (livePlayers.hasNext()) {
          var player = livePlayers.next();
          var uuid = player.getUUID();
          if (uuid.equals(mc.player.getUUID()) || player.isRemoved() || player.isInvisibleTo(mc.player)) continue;
          // JourneyMap owns permission/visibility and distant-player updates.
          // A missing or null entry must never be made visible by this layer.
          if (players.get(uuid) !== null) radar.updatePlayers(new PlayerLoc(player, true));
        }

        var currentObjects = new IdentityHashMap();
        var values = players.values().iterator();
        var changed = previousObjects === null;
        while (values.hasNext()) {
          var value = values.next();
          if (value === null) continue;
          currentObjects.put(value, true);
          if (previousObjects !== null && !previousObjects.containsKey(value)) changed = true;
        }
        if (previousObjects !== null && previousObjects.size() !== currentObjects.size()) changed = true;
        if (changed) {
          // This collection contains transient player heads, separate from
          // saved waypoints. JourneyMap rebuilds it with current Player objects.
          WaypointStore.getInstance().getPlayerPoints().clear();
        }
        previousObjects = currentObjects;
        if (!reported && !currentObjects.isEmpty()) {
          reported = true;
          console.info('JourneyMap integrated-host player marker compatibility active');
        }
      } catch (error) {
        failed = true;
        console.error('JourneyMap integrated-host player marker compatibility failed: ' + error);
      }
    });
  })();
}
