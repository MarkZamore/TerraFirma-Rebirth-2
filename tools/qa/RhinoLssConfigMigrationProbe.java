import dev.latvian.mods.rhino.Context;
import dev.latvian.mods.rhino.ContextFactory;
import java.nio.file.Files;
import java.nio.file.Path;

/** Runs the shipped startup wrapper through the pinned Rhino parser with bounded IO. */
public class RhinoLssConfigMigrationProbe {
    public static void main(String[] args) throws Exception {
        Context context = new ContextFactory().enter();
        var scope = context.initStandardObjects();
        context.evaluateString(scope, """
            var errors = [], logs = [], saves = 0;
            var Platform = {isLoaded: function () { return true; }};
            var console = {info: function (s) { logs.push(s); }, error: function (s) { errors.push(s); }};
            var contents = {'config/vss-server-config.json': JSON.stringify({
              totalBandwidthBytesPerSecond: 1000000, maxConcurrentNbtReads: 2,
              enablePersistentColumnCache: true, persistentColumnCacheMaxMiB: 1024})};
            function nativePath(name) {
              return {name: name, 'resolve(java.lang.String)': function (part) {
                return nativePath(part === '.' ? name : name + '/' + part);
              }, normalize: function () { return this; }, toFile: function () {
                return file(name === 'game' ? '.' : name.substring(5));
              }, toRealPath: function () { return this; }, equals: function (other) {
                return this.name === other.name;
              }};
            }
            function file(name) {
              return {name: name, exists: function () {
                return name === '.' || name === 'config' || name === 'local' || contents[name] !== undefined;
              }, isDirectory: function () { return name === '.' || name === 'config' || name === 'local'; },
              isFile: function () { return contents[name] !== undefined; },
              toPath: function () { return nativePath(name === '.' ? 'game' : 'game/' + name); },
              listFiles: function () { return []; }, getParentFile: function () { return file('local'); },
              createNewFile: function () { contents[name] = ''; return true; },
              renameTo: function (target) { contents[target.name] = contents[name]; delete contents[name]; return true; }};
            }
            var config = {mbPerSecondLimitGlobal: 75, maxConcurrentDiskReads: 0,
              lodStore: 'off', lodStoreMaxMB: 0, lodStoreBackfill: true, lodDistanceChunks: 128,
              validate: function () {}, save: function () {
                saves++; contents['config/vss-server-config.json'] = JSON.stringify(this);
              }};
            var classes = {
              'dev.latvian.mods.kubejs.KubeJSPaths': {GAMEDIR: nativePath('game')},
              'org.apache.commons.io.FileUtils': {
                'readFileToString(java.io.File,java.lang.String)': function (f) { return contents[f.name]; },
                'writeStringToFile(java.io.File,java.lang.String,java.lang.String)': function (f, text) { contents[f.name] = text; },
                forceMkdir: function () {}},
              'org.apache.commons.codec.digest.DigestUtils': {'sha256Hex(java.lang.String)': function () { return 'legacy'; }},
              'dev.vox.lss.config.LSSServerConfig': {CONFIG: config}};
            var Java = {loadClass: function (name) { return classes[name]; }};
            """, "lss-rhino-fixture", 1, null);
        context.evaluateString(scope, Files.readString(Path.of(args[0])), args[0], 1, null);
        Object passed = context.evaluateString(scope, """
            if (errors.length) throw new Error(errors.join(' | '));
            if (saves !== 1 || config.lodStore !== 'on' || config.maxConcurrentDiskReads !== 2 ||
                config.mbPerSecondLimitGlobal * 1048576 !== 1000000 || config.lodDistanceChunks !== 128 ||
                !contents['local/lanmc-lss-config-migration-v1.json']) throw new Error('Migration did not complete');
            'PASS pinned Rhino startup-wrapper migration';
            """, "lss-rhino-assert", 1, null);
        System.out.println(passed);
    }
}
