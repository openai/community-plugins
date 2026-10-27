/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationTest.*;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.SwingUtilities;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Actual current native configuration, root restart permits, controlled sources and forced
 * component journal. Initial fault/action and local decisions are explicitly synthetic fixtures.
 * This does not enter Bridge, restore execution, or perform any job/feed/placement. */
public final class NativeRestartVacuumSourceTest {
    static int assertions,refusals,nativeReleases;static Path manifest;static String manifestHash,mode;
    static final List<String> checks=new ArrayList<>();
    interface Checked {void run()throws Exception;}
    static void check(boolean value,String why){assertions++;checks.add(why);if(!value)throw new AssertionError(why);}
    static void reject(String why,Checked action)throws Exception {try{action.run();throw new AssertionError("Accepted: "+why);}catch(IOException|Bridge.Fault|IllegalStateException|IllegalArgumentException|VacuumSensing.SensorValueException expected){refusals++;check(true,why);}}
    static <T>T edt(Callable<T> work)throws Exception {AtomicReference<T> result=new AtomicReference<>();AtomicReference<Throwable> error=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{result.set(work.call());}catch(Throwable failure){error.set(failure);}});if(error.get()!=null){if(error.get() instanceof Exception)throw(Exception)error.get();throw(Error)error.get();}return result.get();}
    static NullDriver driver(){return(NullDriver)config.getMachine().getDrivers().get(0);}
    static ReferenceNozzle nozzle()throws Exception{return(ReferenceNozzle)config.getMachine().getDefaultHead().getDefaultNozzle();}
    static void write(String name,Object value)throws Exception {Files.writeString(root.resolve(name),JSON.toJson(value)+"\n",StandardOpenOption.CREATE_NEW);}
    static void prepare(Path directory)throws Exception {
        root=directory;Files.createDirectory(root);manifest=root.resolve("prepared.json");
        Map<String,Object> prepared=GuiSensingFixture.prepare(root.resolve("config"),manifest,"success");manifestHash=(String)prepared.get("manifest_sha256");
        Configuration.get().getMachine().close();Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();
        for(Feeder f:config.getMachine().getFeeders()){if(f.getPart()==config.getPart("R0603-1K"))tray=(ReferenceTrayFeeder)f;if(f.getPart()==config.getPart("R0402-1K"))otherTray=(ReferenceTrayFeeder)f;}
        // Real legitimate saved change; the original preparation inventory is now obsolete.
        task(()->{tray.setTrayCountX(3);tray.setTrayCountY(2);tray.setFeedCount(1);otherTray.setTrayCountX(3);otherTray.setTrayCountY(2);config.save();return null;});
        check(driver().getControlledVacuumSource()==null,"Prepared configuration and a legitimate save create no source");
        reject("Old initial claim refuses legitimately changed saved configuration",()->edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,manifestHash,"success")));
    }
    static GuiSensingFixture.RestartAttestation capture()throws Exception{return edt(()->GuiSensingFixture.captureRestart(config,manifest,manifestHash,"success"));}
    static void captureRefusals()throws Exception {
        Map<String,String> files=configurationFiles();reject("Restart capture requires the local EDT",()->GuiSensingFixture.captureRestart(config,manifest,manifestHash,"success"));
        reject("Wrong original manifest hash",()->edt(()->GuiSensingFixture.captureRestart(config,manifest,"0".repeat(64),"success")));
        reject("Wrong original scenario",()->edt(()->GuiSensingFixture.captureRestart(config,manifest,manifestHash,"invalid-read")));
        try(GuiSensingFixture.RestartAttestation a=capture()){
            a.check();check(Boolean.FALSE.equals(a.descriptor().get("source_authority_created"))&&Boolean.FALSE.equals(a.descriptor().get("physical_occupancy_verified")),"Fresh descriptor explicitly grants no source or occupancy authority");
            reject("Attestation alone cannot claim a source",()->task(()->GuiSensingFixture.claimRestart(a,null,(t,p)->{})));
            reject("Attestation cannot be consumed off native executor",a::consume);
            Path machine=root.resolve("config/machine.xml");byte[] before=Files.readAllBytes(machine);Files.writeString(machine,"\n",StandardOpenOption.APPEND);reject("Current saved inventory drift revokes attestation",a::check);Files.write(machine,before);
            int counter=tray.getFeedCount();task(()->{tray.setFeedCount(counter+1);return null;});reject("Unsaved actual native counter drift revokes attestation",a::check);task(()->{tray.setFeedCount(counter);return null;});a.check();
            double limit=nozzle().getNozzleTip().getVacuumLevelPartOnLow();task(()->{nozzle().getNozzleTip().setVacuumLevelPartOnLow(limit+1);return null;});reject("Actual native sensing setting drift revokes attestation",a::check);task(()->{nozzle().getNozzleTip().setVacuumLevelPartOnLow(limit);return null;});a.check();
        }
        check(driver().getControlledVacuumSource()==null,"Every capture-only refusal leaves the native source absent");
        // The test deliberately restores only its controlled file/model drift, never occupancy.
        check(files.keySet().equals(configurationFiles().keySet()),"Capture creates no configuration files");
        GuiSensingFixture.RestartAttestation closed=capture();closed.close();reject("Closed attestation cannot be reused",closed::check);
    }
    static void replayHistoricalTasks(NativeRestartSensingTaskTest.Journal journal,NativeReplacementContinuationTest.Env source)throws Exception {
        for(Map<String,Object> row:source.events){String type=(String)row.get("type");Map<String,Object> payload=o(row.get("payload"));
            if(NativeVacuumJournal.matches(type))journal.vacuum.recover(type,payload,(String)o(payload.get("context")).get("bridge_instance_id"));
            else if(NativeSensingReconciliation.matches(type))journal.coordinator.recover(type,payload,NativeSensingReconciliationTest.INSTANCE);
        }
    }
    static List<Map<String,Object>> events(NativeRestartSensingTaskTest.RestartFixture f,String type){List<Map<String,Object>> out=new ArrayList<>();for(Map<String,Object> row:f.replay.history)if(type.equals(row.get("type")))out.add(row);return out;}
    static void nativeObservation(NativeRestartSensingTaskTest.RestartFixture f,String event,ReferenceNozzle nozzle,Map<String,Object> data){try{f.replay.append("test_source_native_observation",m("native_event",event,"data",data));NativeVacuumSources.observe(config,event,nozzle,data);}catch(Exception failure){throw new IllegalStateException("Native observation force failed",failure);}}
    static Map<String,Object> signal(){try{return o(o(((List<?>)NativeVacuumSources.reconciliationSnapshot(config).get("nozzles")).get(0)).get("signal"));}catch(Exception error){throw new IllegalStateException(error);}}
    static void exercise()throws Exception {
        try(NativeRestartSensingTaskTest.RestartFixture f=new NativeRestartSensingTaskTest.RestartFixture("source-bootstrap","definition");NativeRestartSensingTaskTest.Journal decision=new NativeRestartSensingTaskTest.Journal("source-authority",id())){
            replayHistoricalTasks(decision,f.source);
            Map<String,Object> scope=NativeSensingReconciliationTest.scope(decision.instanceId,NativeSensingReconciliationTest.SOURCE);scope.put("dependencies",f.capture.dependencies());scope.put("job_context",f.capture.jobContext());decision.capture=decision.vacuum.captureFaultSet(scope);
            decision.record=decision.coordinator.taskRecordForRestart(decision.taskId,id(),id(),decision.capture,f.capture,2,"2030-01-01T00:00:00Z");decision.admit();
            f.replay.append("operation",m("operation_id",decision.operation,"request_id",id(),"request_digest",decision.capture.digest,"bridge_instance_id",decision.instanceId,"config_revision","cfg-2","method","local_native_sensing_reconciliation","task_id",decision.taskId,"reconciliation_request_id",decision.record.get("request_id"),"state","running"));
            NativeFaultedJobReplacement.RestartPermit permit=task(()->f.replay.replacement.beginRestart(f.capture,decision.coordinator,decision.permit,decision.owner));
            Map<String,Boolean> oldHistory=new TreeMap<>(f.source.oldJob.getPlacedStatusSnapshot());Map<String,Integer> counts=counters();byte[] originalJournal=Files.readAllBytes(f.source.file);Map<String,String> files=configurationFiles();
            try(GuiSensingFixture.RestartAttestation a=capture()){
                check(driver().getControlledVacuumSource()==null,"A real owned restart permit still installs no source before claim");
                String intent="sensing_source_restart_bootstrap_intent",returned="sensing_source_restart_bootstrap_returned";
                AtomicBoolean installedWithoutForcedIntent=new AtomicBoolean();
                NativeVacuumSources.InterventionSink sink=(type,payload)->{
                    if(mode.equals("intent-no-commit")){if(type.equals(intent))return;installedWithoutForcedIntent.set(driver().getControlledVacuumSource()!=null&&!driver().getControlledVacuumSource().isClosed());}
                    boolean atIntent=type.equals(intent),target=mode.startsWith("intent")?atIntent:mode.startsWith("returned")&&!atIntent;
                    if(target&&mode.endsWith("before"))throw new IOException("Synthetic failure before actual source event force");
                    f.replay.append(type,payload);
                    if(target&&mode.endsWith("after"))throw new IOException("Synthetic failure after actual source event force");
                    if(target&&mode.endsWith("revoke"))decision.permit.revoke();
                };
                if(!mode.equals("success")){
                    reject("Bootstrap force/guard interruption must fail",()->task(()->GuiSensingFixture.claimRestart(a,permit,sink)));
                    VacuumSensing.ControlledSource installed=driver().getControlledVacuumSource();
                    check(mode.equals("intent-no-commit")?installed==null||installed.isClosed():mode.startsWith("intent")?installed==null:installed!=null&&installed.isClosed(),"No source survives an interrupted or revoked bootstrap");
                    reject("Interrupted bootstrap revokes exact root permit",()->task(()->{permit.check();return null;}));
                    long bytes=Files.size(f.replay.file);reject("Consumed bootstrap cannot replay installation",()->task(()->GuiSensingFixture.claimRestart(a,permit,sink)));check(bytes==Files.size(f.replay.file),"Replay attempts append no source event");
                    check(events(f,intent).size()==(mode.equals("intent-before")||mode.equals("intent-no-commit")?0:1),"Intent count matches actual forced boundary");
                    check(events(f,returned).size()==(mode.equals("returned-after")||mode.equals("returned-revoke")?1:0),"Returned receipt count matches actual forced boundary");
                    if(mode.equals("intent-no-commit"))check(!installedWithoutForcedIntent.get(),"A deliberately noncommitting sink cannot install a source without the root's forced intent seal");
                }else{
                    Map<String,Object> receipt=task(()->GuiSensingFixture.claimRestart(a,permit,sink));write("bootstrap-receipt.json",receipt);
                    VacuumSensing.ControlledSource installed=driver().getControlledVacuumSource();String source=(String)installed.provenance().get("source_id");
                    check(installed!=null&&!installed.isClosed()&&!source.equals(NativeSensingReconciliationTest.SOURCE),"Native source has a genuinely fresh identity");
                    check(((Number)installed.provenance().get("process_id")).longValue()==ProcessHandle.current().pid(),"Fresh source belongs to this actual JVM");
                    Map<String,Object> snapshot=NativeVacuumSources.reconciliationSnapshot(config);check(((List<?>)snapshot.get("pending_observation_ids")).isEmpty(),"Old pending observation is never installed as a current callback");
                    check(((List<?>)signal().get("historical_pending_observation_ids")).contains(NativeSensingReconciliationTest.id(100)),"Exact original pending observation ID remains historical provenance");
                    for(Object row:(List<?>)snapshot.get("nozzles")){Map<String,Object> s=o(o(row).get("signal"));check(Boolean.TRUE.equals(s.get("material_unknown_observed"))&&Boolean.FALSE.equals(s.get("disposed_by_native_action"))&&s.get("active_native_check_id")==null&&((List<?>)s.get("pending_observations")).isEmpty(),"Every native nozzle starts conservatively unknown and has no restored call or disposal");}
                    reject("Historical observation ID cannot become a fresh source callback",()->task(()->{NativeVacuumSources.observe(config,"read.before",nozzle(),m("observation_id",NativeSensingReconciliationTest.id(100),"source",installed.provenance()));return null;}));
                    check(((List<?>)NativeVacuumSources.reconciliationSnapshot(config).get("pending_observation_ids")).isEmpty(),"Rejected old callback ID creates no active observation");
                    check(Boolean.FALSE.equals(NativeVacuumSources.inspect(config).get("available")),"Fresh bootstrap is not ordinary sensing readiness");
                    reject("Fresh source cannot admit an ordinary job",()->task(()->NativeVacuumSources.admitJob(config,permit.candidate().job())));
                    Map<String,Object> noModelHistory=NativeFaultedJobReplacement.frozen(m("original",f.source.oldJob.getPlacedStatusSnapshot(),"candidate",permit.candidate().job().getPlacedStatusSnapshot()));
                    reject("Unknown restart source cannot produce finite native readings",()->task(()->NativeVacuumSensing.admit(config,nozzle().getId()).measure(1,permit::check,(event,n,data)->nativeObservation(f,event,n,data) )));
                    check(noModelHistory.equals(NativeFaultedJobReplacement.frozen(m("original",f.source.oldJob.getPlacedStatusSnapshot(),"candidate",permit.candidate().job().getPlacedStatusSnapshot()))),"Failed native read changes no model history");
                    check(events(f,intent).size()==1&&events(f,returned).size()==1,"Bootstrap forced exactly one intent and one returned receipt");
                    long bytes=Files.size(f.replay.file);reject("Consumed attestation cannot rotate the source",()->task(()->GuiSensingFixture.claimRestart(a,permit,sink)));check(bytes==Files.size(f.replay.file)&&driver().getControlledVacuumSource()==installed,"Duplicate claim performs no source effect or force");
                    task(()->{permit.check();return null;});
                    // The next bounded SourceIntervention uses an explicitly synthetic fresh local
                    // component guard; no continuation/execution route is claimed by this test.
                    AtomicBoolean localGuard=new AtomicBoolean(true);NativeVacuumSensing.Guard guard=()->{if(!localGuard.get()||!config.getMachine().isTask(Thread.currentThread()))throw new IOException("Synthetic local source reconciliation revoked");};
                    task(()->{try(NativeVacuumSources.SourceIntervention intervention=NativeVacuumSources.beginReconciliation(config,nozzle().getId(),source,id(),guard)){
                        intervention.repairSource(f.replay::append);
                        reject("Sensor repair alone cannot fabricate finite occupancy",()->NativeVacuumSensing.admit(config,nozzle().getId()).measure(1,guard,(event,n,data)->nativeObservation(f,event,n,data)));
                        config.getMachine().setEnabled(true);config.getMachine().home();intervention.dispose(id(),f.replay::append);nativeReleases++;
                        Map<String,Object> probe=NativeVacuumSensing.admit(config,nozzle().getId()).verify("part_off",guard,(event,n,data)->nativeObservation(f,event,n,data));
                        check(Boolean.TRUE.equals(probe.get("native_verdict")),"Actual native disposal followed by a fresh part-off probe establishes source-level empty signal");
                    }finally{if(config.getMachine().isEnabled())config.getMachine().setEnabled(false);}return null;});
                    check(Boolean.TRUE.equals(signal().get("material_unknown_observed"))&&Boolean.TRUE.equals(signal().get("disposed_by_native_action")),"Old unknown history is retained alongside new actual disposal evidence");
                    check(Boolean.FALSE.equals(NativeVacuumSources.reconciliationSnapshot(config).get("restart_reconciliation_required")),"Current-source native probe removes only the source component fence");
                    try{decision.vacuum.requireNoFault();throw new AssertionError("Restart history became no-fault");}catch(NativeVacuumJournal.Fault expected){check(true,"Coordinator journal still retains unknown faults and grants no execution readiness");}
                    permit.close();
                }
            }
            check(Arrays.equals(originalJournal,Files.readAllBytes(f.source.file))&&oldHistory.equals(f.source.oldJob.getPlacedStatusSnapshot()),"Original forced operations and native placed history remain exact");
            check(counts.equals(counters()),"Bootstrap and diagnostic disposal perform no feeder reset or consumption");
            check(files.equals(configurationFiles()),"Bootstrap and diagnostic actions perform no configuration save");
            check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"All effects finish with native simulator disabled and idle");
            write("restart-root-status.json",f.replay.replacement.status());
        }
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=2)throw new IllegalArgumentException("Expected exclusive state directory and test mode");mode=args[1];int exit=0;Throwable error=null;
        try{prepare(Path.of(args[0]));if(mode.equals("capture"))captureRefusals();else exercise();}catch(Throwable failure){failure.printStackTrace();error=failure;exit=1;}
        finally{try{if(config!=null)config.getMachine().close();}catch(Throwable failure){failure.printStackTrace();exit=1;}Map<String,Object> proof=m("passed",exit==0,"assertions",assertions,"refusals",refusals,"checks",checks,"mode",mode,"failure",error==null?null:error.toString(),"pid",ProcessHandle.current().pid(),"native_standalone_release_calls",nativeReleases,"native_jobs",0,"native_feeds",0,"native_job_placements",0,"scope","Actual native saved configuration, real root/coordinator-issued restart permit and controlled-source install; synthetic initial fault/action and local operator authority. Optional actual diagnostic disposal/probe has synthetic fresh component guard.","bridge_restart_entrypoint_qualified",false,"restart_execution_qualified",false,"public_package_qualified",false,"hardware_qualified",false);write("proof.json",proof);System.out.println("NATIVE_RESTART_VACUUM_SOURCE_RESULT "+JSON.toJson(proof));}System.exit(exit);
    }
}
