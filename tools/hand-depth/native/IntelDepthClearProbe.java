import org.lwjgl.glfw.GLFW;
import org.lwjgl.opengl.*;
import java.nio.*;
public class IntelDepthClearProbe {
 static final int FMT=GL30C.GL_DEPTH32F_STENCIL8;
 static float depth(int t){ ByteBuffer b=ByteBuffer.allocateDirect(8).order(ByteOrder.nativeOrder());GL45C.glGetTextureSubImage(t,0,0,0,0,1,1,1,GL30C.GL_DEPTH_STENCIL,GL30C.GL_FLOAT_32_UNSIGNED_INT_24_8_REV,b);int e=GL11C.glGetError();if(e!=0)throw new IllegalStateException("read error "+e);return b.getFloat(0); }
 static void error(String at){int e=GL11C.glGetError();if(e!=0)throw new IllegalStateException(at+" error "+e);}
 public static void main(String[] a){
 if(!GLFW.glfwInit())throw new IllegalStateException("glfw init");GLFW.glfwWindowHint(GLFW.GLFW_VISIBLE,GLFW.GLFW_FALSE);GLFW.glfwWindowHint(GLFW.GLFW_CONTEXT_VERSION_MAJOR,4);GLFW.glfwWindowHint(GLFW.GLFW_CONTEXT_VERSION_MINOR,6);GLFW.glfwWindowHint(GLFW.GLFW_OPENGL_PROFILE,GLFW.GLFW_OPENGL_CORE_PROFILE);long w=GLFW.glfwCreateWindow(8,8,"Depth diagnostic",0,0);if(w==0)throw new IllegalStateException("context");GLFW.glfwMakeContextCurrent(w);GL.createCapabilities();System.out.println("GPU "+GL11C.glGetString(GL11C.GL_RENDERER));System.out.println("DRIVER "+GL11C.glGetString(GL11C.GL_VERSION));
 int t=GL45C.glCreateTextures(GL11C.GL_TEXTURE_2D);GL45C.glTextureStorage2D(t,1,FMT,8,8);int f=GL45C.glCreateFramebuffers();GL45C.glNamedFramebufferTexture(f,GL30C.GL_DEPTH_STENCIL_ATTACHMENT,t,0);GL45C.glNamedFramebufferDrawBuffer(f,GL11C.GL_NONE);GL45C.glNamedFramebufferReadBuffer(f,GL11C.GL_NONE);if(GL45C.glCheckNamedFramebufferStatus(f,GL30C.GL_FRAMEBUFFER)!=GL30C.GL_FRAMEBUFFER_COMPLETE)throw new IllegalStateException("fbo incomplete");error("setup");
 for(boolean mask:new boolean[]{true,false,true}){
 GL30C.glBindFramebuffer(GL30C.GL_FRAMEBUFFER,0);
 GL11C.glDepthMask(true);GL45C.glClearNamedFramebufferfi(f,GL30C.GL_DEPTH_STENCIL,0,0.25f,0);error("seed");System.out.println("seed="+depth(t));GL11C.glDepthMask(mask);GL45C.glClearNamedFramebufferfv(f,GL11C.GL_DEPTH,0,new float[]{1});error("named clear");System.out.println("named depth mask="+mask+" depth="+depth(t));
 GL11C.glDepthMask(true);GL45C.glClearNamedFramebufferfi(f,GL30C.GL_DEPTH_STENCIL,0,0.25f,0);GL11C.glDepthMask(mask);GL30C.glBindFramebuffer(GL30C.GL_FRAMEBUFFER,f);GL11C.glClearDepth(1);GL11C.glClear(GL11C.GL_DEPTH_BUFFER_BIT);error("bound clear");System.out.println("bound depth mask="+mask+" depth="+depth(t));}
 int t2=GL45C.glCreateTextures(GL11C.GL_TEXTURE_2D);GL45C.glTextureStorage2D(t2,1,FMT,8,8);int f2=GL45C.glCreateFramebuffers();GL45C.glNamedFramebufferTexture(f2,GL30C.GL_DEPTH_STENCIL_ATTACHMENT,t2,0);GL45C.glNamedFramebufferDrawBuffer(f2,GL11C.GL_NONE);GL45C.glNamedFramebufferReadBuffer(f2,GL11C.GL_NONE);
 for(int k=0;k<3;k++){
 GL11C.glDepthMask(true);GL45C.glClearNamedFramebufferfi(f,GL30C.GL_DEPTH_STENCIL,0,0.25f,0);GL45C.glClearNamedFramebufferfi(f2,GL30C.GL_DEPTH_STENCIL,0,0.75f,0);GL30C.glBindFramebuffer(GL30C.GL_FRAMEBUFFER,f2);GL45C.glClearNamedFramebufferfv(f,GL11C.GL_DEPTH,0,new float[]{1});error("named while other bound");System.out.println("other-bound trial="+k+" target="+depth(t)+" boundOther="+depth(t2));
 GL30C.glBindFramebuffer(GL30C.GL_FRAMEBUFFER,f);GL11C.glClearDepth(1);GL11C.glClear(GL11C.GL_DEPTH_BUFFER_BIT);error("bound repair");System.out.println("bound repair target="+depth(t)+" other="+depth(t2));}
 GL30C.glDeleteFramebuffers(f2);GL11C.glDeleteTextures(t2);GL30C.glDeleteFramebuffers(f);GL11C.glDeleteTextures(t);GLFW.glfwDestroyWindow(w);GLFW.glfwTerminate(); }
}
