/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.JsonObject;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.Callable;
import org.openpnp.model.Configuration;
import org.openpnp.scripting.Scripting;

/** A private, consumed-once admission object, constructed only after byte-bound adoption validation. */
public final class NativePortableLaunch implements AutoCloseable {
    public final Path configurationDirectory,journalDirectory;
    private final Path adoptionDirectory;
    private final Map<String,Object> provenance;
    private Configuration configuration;
    private Scripting.ExecutionConstraint scriptConstraint;
    private boolean closed,journalClaimed;
    private final Object journalDirectoryKey;
    private NativePortableLaunch(Path adoption,Path config,Path journal,Map<String,Object> provenance)throws Exception {adoptionDirectory=adoption;configurationDirectory=config;journalDirectory=journal;this.provenance=Collections.unmodifiableMap(provenance);journalDirectoryKey=Files.readAttributes(journal,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey();}
    public static NativePortableLaunch claim(Path adoption,Path journal)throws Exception {
        adoption=adoption.toAbsolutePath().normalize();journal=journal.toAbsolutePath().normalize();
        NativePortableConfiguration.checkAncestors(adoption);NativePortableConfiguration.checkAncestors(journal.getParent());
        if(Files.exists(adoption.resolve("first-launch.json"),LinkOption.NOFOLLOW_LINKS))throw new Bridge.Fault("ADOPTION_ALREADY_LAUNCHED","This adopted instance has consumed its one-time launch. Its configuration and journal are retained; automatic relaunch is unsupported.");
        if(journal.startsWith(adoption)||adoption.startsWith(journal))throw new Bridge.Fault("ADOPTION_STATE_CONFLICT","New operational journal must be outside the adopted configuration tree");
        Path config=NativePortableConfiguration.activeConfiguration(adoption);
        try{Files.createDirectory(journal,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));}catch(FileAlreadyExistsException exists){throw new Bridge.Fault("JOURNAL_EXISTS","An adopted first launch requires a new operational journal directory");}
        JsonObject reservation=NativePortableConfiguration.object(NativePortableConfiguration.read(adoption.resolve("reservation.json"),4096));
        Map<String,Object> provenance=Bridge.map("scope","fresh-native-simulator-adoption","launch_id",UUID.randomUUID().toString(),"archive_sha256",reservation.get("archive_sha256").getAsString(),"activation_receipt_sha256",NativePortableConfiguration.sha(NativePortableConfiguration.read(adoption.resolve("adoption.json"),128*1024)),"claimed_at",Instant.now().toString(),"first_launch_only",true,"source_configuration_retained",true,"fixture_acceleration_applied",false,"startup_enable_performed",false,"startup_home_performed",false,"scripts_activated",false,"physical_state_transferred",false,"operational_identity","new; no source journal or board-load history adopted","native_model_limits",NativePortableLimits.describe());
        try{NativePortableConfiguration.writeNew(adoption.resolve("first-launch.json"),NativePortableConfiguration.JSON.toJson(provenance).getBytes(java.nio.charset.StandardCharsets.UTF_8));}catch(FileAlreadyExistsException exists){throw new Bridge.Fault("ADOPTION_ALREADY_LAUNCHED","Another process has already claimed this adopted instance");}
        return new NativePortableLaunch(adoption,config,journal,provenance);
    }
    public Configuration initialize()throws Exception {
        if(configuration!=null||closed)throw new IllegalStateException("Launch context already initialized or closed");
        try {
            // Recheck the exact activated bytes after the durable claim, before native readers.
            if(!configurationDirectory.equals(NativePortableConfiguration.activeConfiguration(adoptionDirectory)))throw new Bridge.Fault("ACTIVATION_INVALID","Adopted generation changed");
            Configuration.initialize(configurationDirectory.toFile());configuration=Configuration.get();
            scriptConstraint=configuration.getScripting().constrainExecution(file->{throw new IllegalStateException("Script evaluation is disabled in adopted simulator instances");});
            configuration.load();NativePortableConfiguration.quiescent(configuration);NativePortableLibraries.verifyFirstLaunch(configuration,configurationDirectory);Bridge.verifyNativeSimulatorClasses(configuration.getMachine());
            if(configuration.getMachine().isHomed())throw new Bridge.Fault("AUTHORITY","First launch must remain unhomed");
            guard(configuration);record("first-launch-loaded.json",Bridge.map("phase","native-loaded","disabled",true,"homed",false,"native_model_limits",NativePortableLimits.describe()));return configuration;
        }catch(Exception|Error failure){recordFailure(failure);try{close();}catch(Exception ignored){}throw failure;}
    }
    public synchronized void claimJournal(Path supplied)throws Exception {
        if(journalClaimed||!journalDirectory.equals(supplied.toAbsolutePath().normalize()))throw new Bridge.Fault("ADOPTION_STATE_CONFLICT","Bridge journal does not match its fresh adopted launch reservation");
        NativePortableConfiguration.checkAncestors(journalDirectory);if(!Objects.equals(journalDirectoryKey,Files.readAttributes(journalDirectory,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey()))throw new Bridge.Fault("ADOPTION_STATE_CONFLICT","Adopted journal reservation directory was replaced");
        try(var files=Files.list(journalDirectory)){if(files.findAny().isPresent())throw new Bridge.Fault("ADOPTION_STATE_CONFLICT","Adopted journal reservation is no longer empty");}journalClaimed=true;
    }
    public Map<String,Object> snapshot(){return provenance;}
    public void guard(Configuration actual)throws Exception {
        if(closed||actual!=configuration||!actual.getConfigurationDirectory().toPath().toAbsolutePath().normalize().equals(configurationDirectory)||scriptConstraint==null)throw new Bridge.Fault("ADOPTION_CONTEXT_INVALID","Native simulator is not bound to its one-time adopted context");
        Path scripts=configurationDirectory.resolve("scripts");if(Files.exists(scripts,LinkOption.NOFOLLOW_LINKS)&&!NativePortableConfiguration.treeHashes(scripts).isEmpty())throw new Bridge.Fault("SCRIPT_POLICY_REJECTED","Adopted active script inventory must remain empty");
    }
    public <T>T invokeNative(Callable<T> action)throws Exception {try{return action.call();}catch(Scripting.ExecutionRejected rejected){throw new GuiOwnership.ScriptRejected("Adopted simulator script evaluation was refused");}}
    public void ready(Bridge bridge)throws Exception {record("first-launch-ready.json",Bridge.map("phase","bridge-ready","port",bridge.getPort(),"new_journal",true));}
    private void record(String file,Map<String,Object> data)throws Exception {data.put("launch_id",provenance.get("launch_id"));data.put("at",Instant.now().toString());NativePortableConfiguration.writeNew(adoptionDirectory.resolve(file),NativePortableConfiguration.JSON.toJson(data).getBytes(java.nio.charset.StandardCharsets.UTF_8));}
    private void recordFailure(Throwable failure){try{record("first-launch-failed.json",Bridge.map("phase","failed-after-claim","error_type",failure.getClass().getSimpleName(),"automatic_retry",false));}catch(Exception ignored){}}
    @Override public void close()throws Exception {if(closed)return;closed=true;boolean busy=configuration!=null&&configuration.getMachine()!=null&&configuration.getMachine().isBusy();try{if(configuration!=null&&configuration.getMachine()!=null)configuration.getMachine().close();}finally{if(scriptConstraint!=null)scriptConstraint.close();record("first-launch-closed.json",Bridge.map("phase","process-context-closed","native_busy_at_close",busy,"effect_outcome_verified",false,"automatic_relaunch",false));}}
}
