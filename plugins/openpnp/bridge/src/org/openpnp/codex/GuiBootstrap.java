/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.IOException;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.SwingUtilities;
import org.openpnp.gui.MainFrame;
import org.openpnp.gui.MachineControlsPanel;
import org.openpnp.gui.JogControlsPanel;
import org.openpnp.gui.support.AxesComboBoxModel;
import org.openpnp.gui.support.ActuatorsComboBoxModel;
import org.openpnp.model.Configuration;
import org.openpnp.model.Job;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.spi.base.ExternalExecutionControl;
import org.openpnp.scripting.Scripting;

/** The one fixed entry point called by the verified local bootstrap script. */
public final class GuiBootstrap {
    static final String KEY="org.openpnp.codex.gui-controller.v1";
    static final Gson JSON=new Gson();
    private GuiBootstrap() {}
    public static boolean attach(URLClassLoader loader) throws Exception {
        MainFrame frame=MainFrame.get();
        if(frame==null || !frame.isDisplayable())throw new IllegalStateException("Open the native OpenPnP simulator GUI before attachment");
        Object existing=onEdt(()->frame.getRootPane().getClientProperty(KEY));
        if(existing!=null){if(!(existing instanceof Runnable))throw new IllegalStateException("GUI controller registry collision");onEdt(()->{((Runnable)existing).run();return null;});return false;}
        Configuration config=Configuration.get();
        Path state=absoluteProperty("openpnp.codex.stateDir"),samples=absoluteProperty("openpnp.codex.sampleRoot").toRealPath();
        Path bootstrap=absoluteProperty("openpnp.codex.bootstrapPath").toRealPath();
        String bootstrapHash=hashProperty("openpnp.codex.bootstrapSha256");
        if(!hash(Files.readAllBytes(bootstrap)).equals(bootstrapHash))throw new IllegalStateException("Bootstrap digest mismatch");
        Path ownJar=codeJar(GuiBootstrap.class);String ownHash=hash(Files.readAllBytes(ownJar));
        if(!ownHash.equals(hashProperty("openpnp.codex.bridgeSha256")))throw new IllegalStateException("Loaded bridge JAR digest mismatch");
        Map<String,Object> provenance=verifyRuntime();
        if(!"org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory"))||!java.util.prefs.Preferences.userRoot().getClass().getName().equals("org.openpnp.codex.IsolatedPreferencesFactory$MemoryNode"))throw new IllegalStateException("GUI attachment requires explicit isolated session preferences");
        String sensingStartup=System.getProperty("openpnp.codex.sensingStartup");
        validateSensingStartup(sensingStartup);
        if("restart".equals(sensingStartup))restartJournalProvenance(state);
        if(Files.isSymbolicLink(state))throw new IOException("GUI bridge state cannot be a symbolic link");
        Files.createDirectories(state);Files.setPosixFilePermissions(state,PosixFilePermissions.fromString("rwx------"));
        provenance.put("native_example_scripts",GuiExampleScripts.quarantine(config,state));
        Path token=state.resolve("bridge.token");
        if("restart".equals(sensingStartup)&&!Files.exists(token,LinkOption.NOFOLLOW_LINKS))throw new IOException("Restart requires the original GUI bridge token");
        if(!Files.exists(token,LinkOption.NOFOLLOW_LINKS)){byte[] random=new byte[32];new SecureRandom().nextBytes(random);privateWrite(token,Base64.getUrlEncoder().withoutPadding().encodeToString(random).getBytes(StandardCharsets.UTF_8),false);}
        if(Files.isSymbolicLink(token)||!Files.isRegularFile(token,LinkOption.NOFOLLOW_LINKS)||Files.size(token)>4096 || !Files.getPosixFilePermissions(token).equals(PosixFilePermissions.fromString("rw-------")))throw new IOException("GUI bridge token must be a bounded private regular file");
        String sensingManifest=System.getProperty("openpnp.codex.sensingManifest"),sensingHash=System.getProperty("openpnp.codex.sensingManifestSha256"),sensingScenario=System.getProperty("openpnp.codex.sensingScenario");
        final SensingStartup startup;
        if(sensingManifest!=null||sensingHash!=null||sensingScenario!=null||sensingStartup!=null){
            Path prepared=absoluteProperty("openpnp.codex.sensingManifest");String preparedHash=hashProperty("openpnp.codex.sensingManifestSha256");
            if(sensingScenario==null||!NativeVacuumSources.SCENARIOS.contains(sensingScenario))throw new IllegalArgumentException("Select one declared GUI sensing scenario");
            startup=onEdt(()->prepareSensingStartup(config,state,sensingStartup,prepared,preparedHash,sensingScenario));
            provenance.put("sensing_fixture",startup.provenance);provenance.put("sensing_fixture_attested",!startup.restart);
            provenance.put("sensing_restart_attested",startup.restart);
        }else startup=null;
        final GuiSimulatorController controller=onEdt(()->new GuiSimulatorController(config,frame,state,samples,bootstrap,bootstrapHash,provenance,loader,startup));
        try {
            controller.start(token);
            onEdt(()->{if(frame.getRootPane().getClientProperty(KEY)!=null)throw new IllegalStateException("Another GUI controller attached concurrently");frame.getRootPane().putClientProperty(KEY,controller);controller.run();return null;});
            return true;
        }catch(Throwable error){controller.closeAfterFailedStart();throw error;}
    }
    static void validateSensingStartup(String mode) {
        if(mode!=null&&!Set.of("prepared","restart").contains(mode))throw new IllegalArgumentException("Select prepared or restart GUI sensing startup explicitly");
    }
    /** EDT capture only. Restart never falls back to the source-installing prepared path. */
    static SensingStartup prepareSensingStartup(Configuration config,Path state,String mode,Path manifest,String hash,String scenario)throws Exception {
        if(!SwingUtilities.isEventDispatchThread())throw new IllegalStateException("GUI sensing startup capture requires EDT");
        validateSensingStartup(mode);
        if(!"restart".equals(mode))return new SensingStartup(config,manifest,hash,scenario,false,
            GuiSensingFixture.claimPreparedGuiFixture(config,manifest,hash,scenario));
        Map<String,Object> journal=restartJournalProvenance(state);
        try(GuiSensingFixture.RestartAttestation captured=GuiSensingFixture.captureRestart(config,manifest,hash,scenario)) {
            return new SensingStartup(config,manifest,hash,scenario,true,Bridge.map("profile","native-gui-source-absent-restart-v1",
                "startup_attestation",captured.descriptor(),"journal_prefix",journal,"source_authority_created",false,
                "execution_authority_restored",false,"simulation_only",true,"hardware_qualified",false));
        }
    }
    /** Captures only the existing journal prefix. Bridge independently locks and validates every record. */
    static Map<String,Object> restartJournalProvenance(Path state)throws Exception {
        state=state.toAbsolutePath().normalize();Path journal=state.resolve("journal"),identity=journal.resolve("machine-id"),events=journal.resolve("operations.jsonl");
        for(Path directory:List.of(state,journal))if(Files.isSymbolicLink(directory)||!Files.isDirectory(directory,LinkOption.NOFOLLOW_LINKS)||!directory.toRealPath().equals(directory))throw new IOException("Restart requires the original regular GUI state and journal directories");
        if(!Files.getPosixFilePermissions(state).equals(PosixFilePermissions.fromString("rwx------")))throw new IOException("Restart GUI state must remain private");
        for(Path file:List.of(identity,events))if(Files.isSymbolicLink(file)||!Files.isRegularFile(file,LinkOption.NOFOLLOW_LINKS)||!file.toRealPath().equals(file))throw new IOException("Restart requires existing regular machine identity and operations journal");
        if(Files.size(identity)>256||Files.size(events)==0||Files.size(events)>512L*1024*1024)throw new IOException("Restart journal provenance exceeds its bounds or is empty");
        String machineId=Files.readString(identity,StandardCharsets.UTF_8).trim();if(!UUID.fromString(machineId).toString().equals(machineId))throw new IOException("Restart machine identity must be canonical");
        long size=Files.size(events);MessageDigest digest=MessageDigest.getInstance("SHA-256");
        try(java.io.InputStream stream=Files.newInputStream(events,LinkOption.NOFOLLOW_LINKS)){byte[] buffer=new byte[65536];int n;while((n=stream.read(buffer))!=-1)digest.update(buffer,0,n);}
        if(Files.size(events)!=size)throw new IOException("Restart journal changed during startup capture");
        StringBuilder encoded=new StringBuilder(64);for(byte value:digest.digest())encoded.append(String.format(Locale.ROOT,"%02x",value));
        return Bridge.map("journal_directory",journal.toString(),"machine_id",machineId,"operations_bytes",size,"operations_sha256",encoded.toString(),"history_validated",false,"execution_authority_restored",false);
    }
    static final class SensingStartup {
        final Configuration config;final Path manifest;final String hash,scenario;final boolean restart;final Map<String,Object> provenance;
        private SensingStartup(Configuration config,Path manifest,String hash,String scenario,boolean restart,Map<String,Object> provenance)throws IOException {this.config=config;this.manifest=manifest;this.hash=hash;this.scenario=scenario;this.restart=restart;this.provenance=NativeSensingReconciliation.freeze(provenance);}
        GuiSensingFixture.RestartAttestation captureLocalRestart()throws Exception {
            if(!restart)throw new IOException("This GUI did not explicitly start in source-absent restart mode");
            return GuiSensingFixture.captureRestart(config,manifest,hash,scenario);
        }
    }
    static Map<String,Object> verifyRuntime() throws Exception {
        Path manifestPath=absoluteProperty("openpnp.codex.runtimeManifest");
        if(Files.size(manifestPath)>1024*1024)throw new IOException("Native runtime manifest exceeds 1 MiB");
        byte[] bytes=Files.readAllBytes(manifestPath);
        if(!hash(bytes).equals(hashProperty("openpnp.codex.runtimeManifestSha256")))throw new IOException("Native runtime manifest digest mismatch");
        JsonObject manifest=JSON.fromJson(new String(bytes,StandardCharsets.UTF_8),JsonObject.class);
        if(!Bridge.UPSTREAM.equals(manifest.get("upstream_commit").getAsString()))throw new IOException("GUI runtime upstream pin mismatch");
        JsonObject ownership=manifest.getAsJsonObject("gui_ownership");
        if(ownership==null||ownership.get("api_version").getAsInt()!=1||!"codex-gui-ownership-v1".equals(ownership.get("patch_id").getAsString()))throw new IOException("Explicit GUI ownership patch provenance is required");
        JsonObject observer=manifest.getAsJsonObject("native_action_observer");
        if(!ownership.get("patch_sha256").getAsString().matches("[a-f0-9]{64}")||observer==null||observer.get("api_version").getAsInt()!=1||!"native-action-observer-v1".equals(observer.get("patch_id").getAsString())||!observer.get("patch_sha256").getAsString().matches("[a-f0-9]{64}")||!Scripting.hasNativeActionObserverApi())throw new IOException("Combined GUI/action-observer patch provenance is missing");
        JsonObject history=manifest.getAsJsonObject("native_board_load_history");
        if(history==null||history.get("api_version").getAsInt()!=1||!"native-board-load-history-v1".equals(history.get("patch_id").getAsString())||!history.get("patch_sha256").getAsString().matches("[a-f0-9]{64}")||!NativeBoardLoads.fullHistoryAvailable())throw new IOException("Complete native board-load history provenance is required");
        JsonObject topology=manifest.getAsJsonObject("gui_topology_events");
        if(topology==null||topology.get("api_version").getAsInt()!=1||!"gui-topology-events-v1".equals(topology.get("patch_id").getAsString())||!topology.get("patch_sha256").getAsString().matches("[a-f0-9]{64}"))throw new IOException("GUI topology event dispatch patch provenance is required");
        Path nativeJar=codeJar(MainFrame.class);String nativeHash=hash(Files.readAllBytes(nativeJar));
        for(Class<?> core:Arrays.asList(Configuration.class,Job.class,AbstractMachine.class,ExternalExecutionControl.class,Scripting.class,MachineControlsPanel.class,JogControlsPanel.class,AxesComboBoxModel.class,ActuatorsComboBoxModel.class))if(!nativeJar.equals(codeJar(core)))throw new IOException("Mixed native class origins are unsupported");
        boolean found=false;Path manifestRoot=manifestPath.toRealPath().getParent();
        for(JsonElement element:manifest.getAsJsonArray("files")){JsonObject entry=element.getAsJsonObject();Path target=manifestRoot.resolve(entry.get("path").getAsString()).normalize();
            if(!target.startsWith(manifestRoot))throw new IOException("Runtime manifest path escapes its root");
            if(Files.exists(target)&&target.toRealPath().equals(nativeJar)&&nativeHash.equals(entry.get("sha256").getAsString()))found=true;
        }
        if(!found)throw new IOException("Loaded patched native JAR is not the verified runtime artifact");
        Path launcher=codeJar(java.util.prefs.Preferences.userRoot().getClass());String launcherHash=hash(Files.readAllBytes(launcher));boolean launcherFound=false;
        if(!manifest.has("gui_launcher_jar"))throw new IOException("Isolated GUI launcher provenance is required");
        for(JsonElement element:manifest.getAsJsonArray("files")){JsonObject entry=element.getAsJsonObject();if(entry.get("path").getAsString().equals(manifest.get("gui_launcher_jar").getAsString())&&manifestRoot.resolve(entry.get("path").getAsString()).toRealPath().equals(launcher)&&launcherHash.equals(entry.get("sha256").getAsString()))launcherFound=true;}
        if(!launcherFound)throw new IOException("Actual preferences factory is not the verified isolated launcher");
        return Bridge.map("upstream_commit",Bridge.UPSTREAM,"native_jar_sha256",nativeHash,"runtime_manifest_sha256",hash(bytes),"gui_ownership",JSON.fromJson(ownership,Map.class),"native_action_observer",JSON.fromJson(manifest.get("native_action_observer"),Map.class),"native_board_load_history",JSON.fromJson(history,Map.class),"gui_topology_events",JSON.fromJson(topology,Map.class),"gui_launcher_sha256",launcherHash,"ui_preferences_persistence",false,"platform_scope","Unix private state files; isolated in-memory GUI preferences; experimental simulator scope");
    }
    static Path codeJar(Class<?> clazz) throws Exception {Path result=Paths.get(clazz.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath();if(!Files.isRegularFile(result))throw new IOException("GUI attachment requires packaged classes: "+clazz.getName());return result;}
    static Path absoluteProperty(String key) {String value=System.getProperty(key);if(value==null||!Paths.get(value).isAbsolute())throw new IllegalArgumentException("Set absolute "+key);return Paths.get(value);}
    static String hashProperty(String key) {String value=System.getProperty(key);if(value==null||!value.matches("[a-f0-9]{64}"))throw new IllegalArgumentException("Set SHA-256 "+key);return value;}
    static String hash(byte[] bytes) throws Exception {StringBuilder text=new StringBuilder();for(byte value:MessageDigest.getInstance("SHA-256").digest(bytes))text.append(String.format(Locale.ROOT,"%02x",value));return text.toString();}
    static void privateWrite(Path target,byte[] bytes,boolean replace)throws Exception {
        Path temporary=target.resolveSibling("."+target.getFileName()+"."+UUID.randomUUID()+".tmp");
        try{Files.createFile(temporary,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));try(FileChannel file=FileChannel.open(temporary,StandardOpenOption.WRITE)){java.nio.ByteBuffer buffer=java.nio.ByteBuffer.wrap(bytes);while(buffer.hasRemaining())file.write(buffer);file.force(true);}
            if(replace)Files.move(temporary,target,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);else Files.move(temporary,target,StandardCopyOption.ATOMIC_MOVE);
            try(FileChannel directory=FileChannel.open(target.getParent(),StandardOpenOption.READ)){directory.force(true);}
        }finally{Files.deleteIfExists(temporary);}
    }
    static <T>T onEdt(Callable<T> action)throws Exception {
        if(SwingUtilities.isEventDispatchThread())return action.call();
        AtomicReference<T> result=new AtomicReference<>();AtomicReference<Throwable> failure=new AtomicReference<>();
        SwingUtilities.invokeAndWait(()->{try{result.set(action.call());}catch(Throwable error){failure.set(error);}});
        if(failure.get()!=null){if(failure.get() instanceof Exception)throw (Exception)failure.get();if(failure.get() instanceof Error)throw (Error)failure.get();throw new RuntimeException(failure.get());}return result.get();
    }
}
