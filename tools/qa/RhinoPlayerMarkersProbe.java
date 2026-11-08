import dev.latvian.mods.rhino.Context;
import dev.latvian.mods.rhino.ContextFactory;
import dev.latvian.mods.rhino.NativeJavaClass;
import dev.latvian.mods.rhino.NativeJavaObject;
import dev.latvian.mods.rhino.CustomMember;
import dev.latvian.mods.rhino.ScriptableObject;
import dev.latvian.mods.rhino.type.TypeInfo;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.UUID;

/** Run the actual script in Rhino with the Java collection binding recorded in-game. */
public class RhinoPlayerMarkersProbe {
    public static class Player {
        private final UUID uuid = UUID.randomUUID();
        public int x;

        public Player(int x) { this.x = x; }
        public UUID getUUID() { return uuid; }
        public boolean isRemoved() { return false; }
        public boolean isInvisibleTo(Player viewer) { return false; }
    }

    public static class Level {
        private final List<Player> entries = new ArrayList<>();
        public List<Player> players() { return entries; }
    }

    private static void check(String source, boolean baseline) {
        Context context = new ContextFactory().enter();
        var scope = context.initStandardObjects();
        Player local = new Player(0);
        Player near = new Player(10);
        Level level = new Level();
        level.entries.add(local);
        level.entries.add(near);
        NativeJavaObject boundLevel = (NativeJavaObject) context.javaToJS(level, scope);
        // Model the observed KubeJS binding with Rhino's real member API and
        // Java List wrapper, rather than a callable JavaScript mock.
        boundLevel.addCustomMember(new CustomMember("players", TypeInfo.of(List.class), level.entries));
        ScriptableObject.putProperty(scope, "nativeLevel", boundLevel, context);
        ScriptableObject.putProperty(scope, "nativeLocal", context.javaToJS(local, scope), context);
        ScriptableObject.putProperty(scope, "nativeNear", context.javaToJS(near, scope), context);
        ScriptableObject.putProperty(scope, "NativeIdentityMap",
            new NativeJavaClass(context, scope, IdentityHashMap.class), context);
        context.evaluateString(scope, """
            var errors = [], callback = null, clears = 0, updates = 0, isHost = true;
            var nearId = String(nativeNear.getUUID());
            var tracked = {}, visible = {x: 1}, far = {x: 500};
            tracked[nearId] = visible;
            function values() {
              var entries = [tracked[nearId], far], index = 0;
              return {iterator: function () { return {
                hasNext: function () { return index < entries.length; },
                next: function () { return entries[index++]; }
              }; }};
            }
            var mc = {level: nativeLevel, player: nativeLocal,
              hasSingleplayerServer: function () { return isHost; }};
            var radar = {getPlayers: function () { return {
              get: function (uuid) { return tracked[String(uuid)] || null; }, values: values
            }; }, updatePlayers: function (location) {
              updates++; tracked[String(location.player.getUUID())].x = location.player.x;
            }};
            var classes = {
              'net.minecraft.client.Minecraft': {getInstance: function () { return mc; }},
              'journeymap.common.util.PlayerRadarManager': {getInstance: function () { return radar; }},
              'journeymap.common.network.model.PlayerLoc': function (player, visible) { this.player = player; },
              'journeymap.common.waypoint.WaypointStore': {getInstance: function () { return {
                getPlayerPoints: function () { return {clear: function () { clears++; }}; }
              }; }},
              'java.util.IdentityHashMap': NativeIdentityMap
            };
            var Platform = {isClientEnvironment: function () { return true; }, isLoaded: function () { return true; }};
            var Java = {loadClass: function (name) { return classes[name]; }};
            var NativeEvents = {onEvent: function (name, fn) { callback = fn; }};
            var console = {info: function () {}, error: function (message) { errors.push(String(message)); }};
            """, "native-java-member-fixture", 1, null);
        context.evaluateString(scope, source, "actual-player-marker-script", 1, null);
        context.evaluateString(scope, "callback();", "first-host-tick", 1, null);
        if (baseline) {
            context.evaluateString(scope, """
                if (errors.length !== 1 || errors[0].indexOf('players') < 0 || updates !== 0)
                  throw new Error('The old field/method failure was not reproduced: errors=' + errors.join(' | ') + ', updates=' + updates);
                """, "baseline-assert", 1, null);
            return;
        }
        context.evaluateString(scope, """
            if (errors.length || visible.x !== 10 || clears !== 1 || far.x !== 500)
              throw new Error('First host tick failed: ' + errors.join(' | '));
            nativeNear.x = 25; callback();
            if (errors.length || visible.x !== 25 || clears !== 1)
              throw new Error('Movement did not update through real Java interop');
            tracked[nearId] = {x: 1}; callback();
            if (errors.length || tracked[nearId].x !== 25 || clears !== 2)
              throw new Error('Replacement cache did not refresh');
            isHost = false; nativeNear.x = 30; callback();
            if (tracked[nearId].x !== 25 || updates !== 3)
              throw new Error('Guest path must not run the host correction');
            isHost = true; callback();
            if (errors.length || tracked[nearId].x !== 30 || clears !== 3)
              throw new Error('Host re-entry failed');
            """, "fixed-host-assert", 1, null);
    }

    public static void main(String[] args) throws Exception {
        String source = Files.readString(Path.of(args[0]));
        if (!source.contains("mc.level.players.iterator()")) {
            throw new IllegalStateException("The production script does not use the native collection property");
        }
        check(source.replace("mc.level.players.iterator()", "mc.level.players().iterator()"), true);
        check(source, false);
        System.out.println("PASS pinned Rhino Java interop: baseline failure, host movement, replacement, guest and re-entry");
    }
}
