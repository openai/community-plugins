/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.awt.Window;
import java.lang.reflect.*;
import java.net.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.jar.*;
import javax.swing.*;
import com.google.gson.Gson;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.Configuration;

/** Native-only application entry. The real shipped script creates the Bridge child loader. */
public final class NativeGuiRestartPackagedLauncher {
    static String sha(Path file)throws Exception {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file)));}
    static <T>T edt(Callable<T> body)throws Exception {FutureTask<T> task=new FutureTask<>(body);SwingUtilities.invokeLater(task);return task.get(30,TimeUnit.SECONDS);}
    public static Map<String,Object> verifyProduction(URLClassLoader child,Path jar,Path launcher)throws Exception {
        Map<String,String> origins=new TreeMap<>();try(JarFile packed=new JarFile(jar.toFile());JarFile nativeLauncher=new JarFile(launcher.toFile())){
            Enumeration<JarEntry> entries=packed.entries();while(entries.hasMoreElements()){JarEntry entry=entries.nextElement();if(!entry.getName().endsWith(".class"))continue;String name=entry.getName().replace('/','.').replaceFirst("\\.class$","");Class<?> type=Class.forName(name,false,child);Path origin=Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath();boolean preferences=name.startsWith("org.openpnp.codex.IsolatedPreferencesFactory");Path expected=preferences?launcher:jar;if(!origin.equals(expected))throw new AssertionError("Production class origin changed: "+name);
                byte[] actual=preferences?nativeLauncher.getInputStream(nativeLauncher.getJarEntry(entry.getName())).readAllBytes():packed.getInputStream(entry).readAllBytes();byte[] expectedBytes=packed.getInputStream(entry).readAllBytes();if(!Arrays.equals(actual,expectedBytes))throw new AssertionError("Production class bytes changed: "+name);origins.put(name,origin.toString());}}
        return Map.of("production_class_count",origins.size(),"all_origins_and_class_bytes_verified",true,"canonical_jar_sha256",sha(jar),"native_preferences_launcher_sha256",sha(launcher),"origins",origins);
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=5)throw new IllegalArgumentException("runtime, canonical jar, shipped bootstrap, state, helper classes");
        Path runtime=Path.of(args[0]).toRealPath(),jar=Path.of(args[1]).toRealPath(),script=Path.of(args[2]).toRealPath(),state=Path.of(args[3]).toRealPath(),helpers=Path.of(args[4]).toRealPath();
        Map<String,Object> proof=new LinkedHashMap<>();MainFrame frame=null;int[] welcome={0};
        try{
            try{Class.forName("org.openpnp.codex.Bridge",false,ClassLoader.getSystemClassLoader());throw new AssertionError("Bridge on application classpath");}catch(ClassNotFoundException expected){proof.put("bridge_absent_on_application_classpath",true);}
            Map<?,?> marker=new Gson().fromJson(Files.readString(state.resolve("crash-boundary.json")),Map.class);Path journal=state.resolve("journal/operations.jsonl");if(!sha(journal).equals(marker.get("journal_sha256")))throw new AssertionError("Original crash journal changed before bootstrap");Files.copy(journal,state.resolve("original-crash-prefix.jsonl"));proof.put("original_prefix_verified_before_bootstrap",true);
            Configuration.initialize(state.resolve("config").toFile());Configuration config=Configuration.get();
            javax.swing.Timer dismiss=new javax.swing.Timer(30,e->{for(Window w:Window.getWindows())if(w.isVisible()&&w.getClass().getSimpleName().equals("Welcome2_0Dialog")){welcome[0]++;w.dispose();}});dismiss.start();
            try{frame=edt(()->{MainFrame f=new MainFrame(config);f.setVisible(true);return f;});}finally{dismiss.stop();}
            Path bootstrap=config.getScripting().getScriptsDirectory().toPath().resolve("codex-bootstrap.js");Files.copy(script,bootstrap);
            Path token=state.resolve("bridge.token");Files.copy(state.resolve("token"),token);Files.setPosixFilePermissions(token,PosixFilePermissions.fromString("rw-------"));Files.setPosixFilePermissions(state,PosixFilePermissions.fromString("rwx------"));
            System.setProperty("openpnp.codex.bridgeJar",jar.toString());System.setProperty("openpnp.codex.bridgeSha256",sha(jar));System.setProperty("openpnp.codex.stateDir",state.toString());System.setProperty("openpnp.codex.sampleRoot",runtime.resolve("samples").toString());System.setProperty("openpnp.codex.bootstrapPath",bootstrap.toString());System.setProperty("openpnp.codex.bootstrapSha256",sha(script));System.setProperty("openpnp.codex.runtimeManifest",runtime.resolve("codex-build-manifest.json").toString());System.setProperty("openpnp.codex.runtimeManifestSha256",sha(runtime.resolve("codex-build-manifest.json")));
            System.setProperty("openpnp.codex.sensingStartup","restart");System.setProperty("openpnp.codex.sensingManifest",state.resolve("prepared-gui-fixture.json").toString());System.setProperty("openpnp.codex.sensingManifestSha256",sha(state.resolve("prepared-gui-fixture.json")));System.setProperty("openpnp.codex.sensingScenario","lost-before-place");System.setProperty("openpnp.codex.testWelcomeClosed",Integer.toString(welcome[0]));
            config.getScripting().execute(bootstrap.toFile());final MainFrame current=frame;Object controller=edt(()->current.getRootPane().getClientProperty("org.openpnp.codex.gui-controller.v1"));
            if(controller==null)throw new AssertionError("Actual script did not attach controller");
            ClassLoader raw=controller.getClass().getClassLoader();if(!(raw instanceof URLClassLoader))throw new AssertionError("Shipped bootstrap must own URLClassLoader");URLClassLoader child=(URLClassLoader)raw;
            if(!Arrays.equals(child.getURLs(),new URL[]{jar.toUri().toURL()}))throw new AssertionError("Bootstrap loader initially contains only exact packaged JAR");
            if(!Path.of(child.loadClass("org.openpnp.codex.Bridge").getProtectionDomain().getCodeSource().getLocation().toURI()).equals(jar))throw new AssertionError("Bridge origin drift");
            proof.put("shipped_bootstrap_sha256",sha(script));proof.put("canonical_jar_sha256",sha(jar));proof.put("initial_child_urls",List.of(child.getURLs()[0].toString()));proof.put("native_scripting_engine_executed",true);
            Path nativeLauncher=Path.of(System.getProperty("openpnp.codex.testNativeLauncher"));Files.writeString(state.resolve("production-origin-before-helper.json"),new Gson().toJson(verifyProduction(child,jar,nativeLauncher)));
            Method add=URLClassLoader.class.getDeclaredMethod("addURL",URL.class);add.setAccessible(true);add.invoke(child,helpers.toUri().toURL());proof.put("test_helper_added_after_bootstrap",true);Files.writeString(state.resolve("production-origin-after-helper.json"),new Gson().toJson(verifyProduction(child,jar,nativeLauncher)));proof.put("passed",true);Files.writeString(state.resolve("actual-bootstrap-proof.json"),new Gson().toJson(proof));
            child.loadClass("org.openpnp.codex.NativeGuiRestartPackagedMcpTest").getMethod("main",String[].class).invoke(null,(Object)new String[]{runtime.resolve("samples").toString(),state.toString()});
        }catch(Throwable t){t.printStackTrace();proof.put("passed",false);proof.put("failure",t.toString());Files.writeString(state.resolve("actual-bootstrap-proof.json"),new Gson().toJson(proof));try{Configuration.get().getMachine().close();}catch(Throwable ignored){}for(Window w:Window.getWindows())w.dispose();System.exit(1);}
    }
}
