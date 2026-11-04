// Pack-owned compatibility for the pinned Veil 4.3.2 first-person framebuffer.
// The normal hand pipeline, stencil, lighting and all item renderers remain active.
if (Platform.isClientEnvironment() && Platform.isLoaded('veil')) {
  (function () {
    var priority = Java.loadClass('net.neoforged.bus.api.EventPriority').HIGHEST;
    var GL11 = Java.loadClass('org.lwjgl.opengl.GL11C');
    var GL45 = Java.loadClass('org.lwjgl.opengl.GL45C');
    var RenderSystem = Java.loadClass('com.mojang.blaze3d.systems.RenderSystem');
    var VeilRenderSystem = Java.loadClass('foundry.veil.api.client.render.VeilRenderSystem');
    var VeilFramebuffers = Java.loadClass('foundry.veil.api.client.render.framebuffer.VeilFramebuffers');
    var armed = false;
    var intel = null;
    var reported = false;
    var failed = false;
    NativeEvents.onEvent(priority, 'net.neoforged.neoforge.client.event.RenderFrameEvent$Pre', function () {
      armed = true;
    });
    NativeEvents.onEvent(priority, 'net.neoforged.neoforge.client.event.RenderHandEvent', function () {
      // The event can fire for both hands. Clear only before the first one.
      if (!armed || failed) return;
      armed = false;
      try {
        if (intel === null) intel = String(GL11.glGetString(7936)).indexOf('Intel') >= 0;
        if (!intel) return;
        var fbo = VeilRenderSystem.renderer().getFramebufferManager().getFramebuffer(VeilFramebuffers.FIRST_PERSON);
        // Shader rendering and unrelated targets never satisfy this binding check.
        if (fbo === null || GL11.glGetInteger(36006) !== fbo.getId()) return;
        // Dynamic Veil wrappers can report format 0 and omit the stencil flag.
        // Inspect the actual texture, including after resize/re-entry and ID reuse.
        var depthTexture = fbo.getDepthTextureAttachment().getId();
        if (GL45.glGetTextureLevelParameteri(depthTexture, 0, 4099) !== 36013) return;
        if (!GL11.glGetBoolean(2930) || GL11.glIsEnabled(3089)) return;
        var originalClearDepth = GL11.glGetDouble(2931);
        try {
          if (originalClearDepth !== 1) RenderSystem.clearDepth(1);
          // Intel 32.0.101.8724 ignores the equivalent named clear while unbound.
          // At this event Veil has bound its own target; preserve its stencil.
          RenderSystem.clear(256, false);
        } finally {
          if (originalClearDepth !== 1) RenderSystem.clearDepth(originalClearDepth);
        }
        if (!reported) {
          reported = true;
          console.info('Intel first-person depth compatibility active for Veil DEPTH32F_STENCIL8');
        }
      } catch (error) {
        failed = true;
        console.error('Intel first-person depth compatibility failed: ' + error);
      }
    });
  })();
}
