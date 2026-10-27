/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.*;
import java.util.concurrent.atomic.*;
import org.openpnp.machine.reference.*;
import org.openpnp.model.*;
import org.openpnp.scripting.Scripting;

/** Actual native source repair/disposal component tests. No Bridge, durable recovery or physical qualification. */
public final class NativeVacuumSourceReconciliationTest {
    static int checks;
    @FunctionalInterface interface Checked { void run() throws Exception; }
    static void yes(boolean value, String label) { checks++; if (!value) throw new AssertionError(label); }
    static String uuid() { return UUID.randomUUID().toString(); }
    static void refused(String code, Checked action) throws Exception {
        try { action.run(); throw new AssertionError("Allowed " + code); }
        catch (Exception e) { Throwable cause=e; while(cause!=null && !(cause instanceof Bridge.Fault))cause=cause.getCause();
            yes(cause instanceof Bridge.Fault && code.equals(((Bridge.Fault)cause).code), "Expected " + code + ", got " + e); }
    }
    @SuppressWarnings("unchecked") static Map<String,Object> snapshot(NativeVacuumSourcesTest.Fixture f) { return NativeVacuumSources.reconciliationSnapshot(f.config); }
    static String sourceId(NativeVacuumSourcesTest.Fixture f) { return (String)((Map<?,?>)snapshot(f).get("source")).get("source_id"); }
    static Map<String,Object> signal(NativeVacuumSourcesTest.Fixture f) { return signal(f,f.nozzle.getId()); }
    @SuppressWarnings("unchecked") static Map<String,Object> signal(NativeVacuumSourcesTest.Fixture f,String nozzleId) {
        for(Object row:(List<?>)snapshot(f).get("nozzles"))if(nozzleId.equals(((Map<?,?>)row).get("nozzle_id")))return (Map<String,Object>)((Map<?,?>)row).get("signal");
        throw new AssertionError("No fixture signal");
    }
    static Map<String,Object> context(NativeVacuumSourcesTest.Fixture f, String operation) {
        return Bridge.map("operation_id", operation, "request_id", uuid(), "machine_id",uuid(),"bridge_instance_id",uuid(),
            "config_revision","cfg-0","nozzle_id",f.nozzle.getId(),"scope","manual","job_context",null);
    }
    static VacuumSensing.Observer observer(NativeVacuumSourcesTest.Fixture f, Map<String,Object> context) {
        return (event,nozzle,data)->NativeVacuumSources.observe(f.config,event,nozzle,data,context);
    }
    static List<Map<String,Object>> records() { return new ArrayList<>(); }
    static NativeVacuumSources.InterventionSink sink(List<Map<String,Object>> records) {
        return (event,facts)->records.add(Bridge.map("event",event,"facts",facts));
    }
    static void enable(NativeVacuumSourcesTest.Fixture f) throws Exception { f.task(()->{f.machine.setEnabled(true);f.machine.home();return null;}); }
    static void disable(NativeVacuumSourcesTest.Fixture f) throws Exception { f.task(()->{f.machine.setEnabled(false);return null;}); }
    static Map<String,Object> verify(NativeVacuumSourcesTest.Fixture f, String state) throws Exception {
        return f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify(state,()->{},observer(f,context(f,uuid()))));
    }
    static NativeVacuumSources.SourceIntervention begin(NativeVacuumSourcesTest.Fixture f, NativeVacuumSensing.Guard guard) throws Exception {
        String source=sourceId(f),id=uuid();return f.task(()->NativeVacuumSources.beginReconciliation(f.config,f.nozzle.getId(),source,id,guard));
    }
    static Job faultedJob(NativeVacuumSourcesTest.Fixture f) throws Exception {
        f.part.setPickRetryCount(0);f.feeder.setPickRetryCount(0);f.feeder.setFeedRetryCount(0);Job job=f.job();
        NativeVacuumSources.Admission admission=NativeVacuumSources.admitJob(f.config,job);Map<String,Object> context=context(f,uuid());
        Throwable failure=f.task(()->{f.machine.setEnabled(true);f.machine.home();
            try(VacuumSensing.Scope scope=VacuumSensing.observe(observer(f,context))){
                f.machine.getPnpJobProcessor().initialize(job);int i=0;boolean more;
                do{if(++i>500)throw new AssertionError("Native step bound");admission.validateCurrentState();more=f.machine.getPnpJobProcessor().next();}while(more);
                return null;
            }catch(Throwable caught){return caught;}});
        yes(failure!=null,"Actual native fault scenario fails");yes(f.feeder.getFeedCount()==1,"Actual fault consumed one native feed");
        yes(!job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1"),"Faulted native placement stays incomplete");
        return job;
    }
    public static void main(String[] args) throws Exception {
        try {
            invalidReadRepair(); retainedDisposal(); lostDisposal(); unknownJobDisposal(); revocationAndPublication(); disposalPublicationFailure(); exactBindings(); terminalObservationRetirement(); sharedSourceGenerations(); reloadNoAuthority();
            System.out.println("NATIVE_VACUUM_SOURCE_RECONCILIATION_PASS " + checks + " checks; actual native source/disposal; no public or physical qualification");
        } catch(Throwable failure) { failure.printStackTrace();System.exit(1); }
        System.exit(0);
    }
    static void invalidReadRepair() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("invalid-read",true)) {
            Map<String,Object> context=context(f,uuid());
            Throwable failure=f.task(()->{try{NativeVacuumSensing.admit(f.config,f.nozzle.getId()).measure(1,()->{},observer(f,context));return null;}catch(Throwable e){return e;}});
            yes(failure!=null,"Invalid native read fails");yes(Boolean.TRUE.equals(signal(f).get("sensor_failure_observed")),"Native failed read retained");yes(Boolean.FALSE.equals(signal(f).get("material_unknown_observed")),"Standalone direct sensor failure does not invent a material-handling fault");
            Map<?,?> origin=(Map<?,?>)signal(f).get("fault_origin");yes(context.equals(origin.get("bridge_context")),"Native observation retains supplied component context");
            String original=sourceId(f);NativeVacuumSensing.Plan old=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            NativeVacuumSources.SourceIntervention permit=begin(f,()->{});List<Map<String,Object>> records=records();
            f.task(()->permit.repairSource(sink(records)));
            yes(records.size()==2,"Repair publishes intent and returned source facts");yes(!sourceId(f).equals(original),"Repair rotates actual native source generation");
            yes(((Number)snapshot(f).get("source_generation")).longValue()==1,"Source generation incremented once");
            yes(Boolean.TRUE.equals(signal(f).get("sensor_failure_observed"))&&origin.equals(signal(f).get("fault_origin")),"Repair preserves original failure evidence");
            refused("SENSING_SOURCE_CHANGED",old::validateCurrentState);
            yes(f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).measure(1,()->{},observer(f,context))).get("samples").equals(List.of(70.0)),"Fresh source read goes through native nozzle");
            enable(f);yes(Boolean.TRUE.equals(verify(f,"part_off").get("native_verdict")),"Repaired source supports actual complete native probe");
            yes(Boolean.FALSE.equals(snapshot(f).get("execution_authority")),"Source facts alone never grant journal execution authority");
            disable(f);refused("SENSING_RECOVERY_SPENT",()->f.task(()->permit.repairSource(sink(records))));
            String id=(String)permit.preview().get("intervention_id");permit.close();
            refused("SENSING_RECOVERY_SPENT",()->f.task(()->NativeVacuumSources.beginReconciliation(f.config,f.nozzle.getId(),sourceId(f),id,()->{})));
            yes(f.feeder.getFeedCount()==0,"Standalone repair and probes feed no material");
        }
    }
    static void retainedDisposal() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("retained-after-place",true)) {
            Job job=faultedJob(f);yes(f.nozzle.getPart()==null,"Retained after-place fault has null native model Part");
            yes(Boolean.TRUE.equals(signal(f).get("retained_observed")),"Actual after-place check captured retained latch");Map<?,?> origin=(Map<?,?>)signal(f).get("fault_origin");
            enable(f);f.task(()->{f.nozzle.moveToSafeZ();return null;});
            yes(Boolean.FALSE.equals(verify(f,"part_off").get("native_verdict")),"Direct native probe before repair preserves retained material after explicit test homing/Safe Z");
            disable(f);NativeVacuumSources.SourceIntervention permit=begin(f,()->{});List<Map<String,Object>> records=records();f.task(()->permit.repairSource(sink(records)));enable(f);
            yes(Boolean.FALSE.equals(verify(f,"part_off").get("native_verdict")),"Source repair plus a direct check cannot clear retained material");
            yes(Boolean.FALSE.equals(verify(f,"part_off").get("native_verdict")),"Repeated native direct probe remains retained");
            AtomicInteger actualRelease=new AtomicInteger();String action=uuid();
            f.task(()->{try(Scripting.NativeObserverScope hooks=Scripting.observeNativeEvents(new Scripting.NativeObserver(){
                public void beforeScripts(String event,Map<String,Object> globals){}
                public void afterScripts(String event,Map<String,Object> globals){if(event.equals("Nozzle.AfterPlace"))actualRelease.incrementAndGet();}
            })) {return permit.dispose(action,(event,facts)->{
                if(event.equals("sensing_source_disposal_stage_returned")&&facts.get("stage").equals("native-release"))yes(Boolean.FALSE.equals(signal(f).get("disposed_by_native_action")),"Latch remains unresolved until returned release is durably accepted");
                records.add(Bridge.map("event",event,"facts",facts));
            });}});
            yes(actualRelease.get()==1,"Disposal invokes exactly one actual native release");yes(Boolean.TRUE.equals(signal(f).get("disposed_by_native_action")),"Actual returned disposal records synthetic disposition");
            yes(action.equals(signal(f).get("disposal_action_id")),"Synthetic disposition binds exact new native action");
            yes(Boolean.TRUE.equals(verify(f,"part_off").get("native_verdict")),"Only post-disposal native probe observes empty");
            yes(origin.equals(signal(f).get("fault_origin")),"Original retained fault origin preserved");
            yes(f.feeder.getFeedCount()==1&&!job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1"),"Disposal changes neither feeder count nor old placed flag");
            refused("SENSING_RECOVERY_SPENT",()->f.task(()->permit.dispose(uuid(),sink(records))));yes(actualRelease.get()==1,"Spent capability does not release again");permit.close();
        }
    }
    static void lostDisposal() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("lost-before-place",true)) {
            Job job=faultedJob(f);yes(Boolean.TRUE.equals(signal(f).get("lost_observed")),"Native before-place failure records declared lost material");
            disable(f);NativeVacuumSources.SourceIntervention permit=begin(f,()->{});f.task(()->permit.repairSource(sink(records())));enable(f);
            yes(Boolean.FALSE.equals(verify(f,"part_on").get("native_verdict")),"Source repair does not fabricate recovered lost component");
            f.task(()->permit.dispose(uuid(),sink(records())));yes(f.nozzle.getPart()==null,"Native release clears remaining model ownership");
            yes(Boolean.TRUE.equals(signal(f).get("lost_observed")),"Original loss history remains after model cleanup");
            yes(Boolean.TRUE.equals(verify(f,"part_off").get("native_verdict")),"Post-disposal native probe passes");
            yes(f.feeder.getFeedCount()==1&&!job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1"),"Lost material is not returned to tray or counted placed");permit.close();
        }
    }
    static void unknownJobDisposal() throws Exception {
        for(boolean beforePick : new boolean[]{true,false}) try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("invalid-read",true)) {
            f.tip.setPartOffCheckBeforePick(beforePick);
            Job job=faultedJob(f);Map<?,?> origin=(Map<?,?>)signal(f).get("fault_origin");
            yes(Boolean.TRUE.equals(signal(f).get("material_unknown_observed")),"Paired actual failed job check retains unknown material after its call closes");
            yes(((List<?>)snapshot(f).get("pending_observation_ids")).isEmpty(),"Unknown job material persists independently of pending observation bookkeeping");
            yes((beforePick?"before_pick":"after_pick").equals(origin.get("native_stage"))&&Objects.equals(beforePick?null:f.part.getId(),origin.get("original_part_id")),"Native unknown origin identifies the actual before/after-pick stage and observed Part without fabrication");
            yes(((Map<?,?>)origin.get("bridge_context")).get("operation_id").equals(origin.get("original_operation_id"))&&origin.get("original_action_id")==null,"Unknown origin retains exact observed operation without inventing an action identity");
            disable(f);NativeVacuumSources.SourceIntervention permit=begin(f,()->{});f.task(()->permit.repairSource(sink(records())));enable(f);
            Throwable repairedRead=f.task(()->{try{NativeVacuumSensing.admit(f.config,f.nozzle.getId()).measure(1,()->{},observer(f,context(f,uuid())));return null;}catch(Throwable failure){return failure;}});
            yes(repairedRead!=null&&Boolean.FALSE.equals(signal(f).get("disposed_by_native_action")),"Sensor repair cannot produce healthy readings while job material remains unknown");
            yes(origin.equals(signal(f).get("fault_origin")),"Subsequent failed readings preserve original native job provenance");
            AtomicInteger releases=new AtomicInteger();String action=uuid();
            f.task(()->{try(Scripting.NativeObserverScope hooks=Scripting.observeNativeEvents(new Scripting.NativeObserver(){
                public void beforeScripts(String event,Map<String,Object> globals){}
                public void afterScripts(String event,Map<String,Object> globals){if(event.equals("Nozzle.AfterPlace"))releases.incrementAndGet();}
            })){return permit.dispose(action,sink(records()));}});
            yes(releases.get()==1&&action.equals(signal(f).get("disposal_action_id")),"Unknown job material receives one distinct actual native disposal action");
            yes(Boolean.TRUE.equals(verify(f,"part_off").get("native_verdict")),"Fresh complete native empty probe succeeds only after unknown-material disposal");
            yes(Boolean.TRUE.equals(signal(f).get("material_unknown_observed"))&&origin.equals(signal(f).get("fault_origin")),"Successful disposal retains original unknown history and provenance");
            yes(f.feeder.getFeedCount()==1&&!job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1"),"Unknown-material recovery neither replays feed nor marks the old placement complete");permit.close();
        }
    }
    static void revocationAndPublication() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("success",true)) {
            String original=sourceId(f);AtomicBoolean allowed=new AtomicBoolean(true);NativeVacuumSources.SourceIntervention permit=begin(f,()->{if(!allowed.get())throw new Bridge.Fault("OWNERSHIP_REVOKED","Test local grant revoked");});
            refused("OWNERSHIP_REVOKED",()->f.task(()->permit.repairSource((event,facts)->allowed.set(false))));
            yes(original.equals(sourceId(f)),"Ownership is checked after durable sink and before source replacement");
            allowed.set(true);refused("SENSING_RECOVERY_SPENT",()->f.task(()->permit.repairSource(sink(records()))));permit.close();
            NativeVacuumSources.SourceIntervention writeFailure=begin(f,()->{});
            Throwable failure=f.task(()->{try{writeFailure.repairSource((event,facts)->{throw new java.io.IOException("Synthetic intent publication failure");});return null;}catch(Throwable e){return e;}});
            yes(failure instanceof java.io.IOException&&original.equals(sourceId(f)),"Failed intent publication leaves old source unchanged and capability spent");writeFailure.close();
        }
    }
    static void disposalPublicationFailure() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("retained-after-place",true)) {
            faultedJob(f);disable(f);NativeVacuumSources.SourceIntervention permit=begin(f,()->{});f.task(()->permit.repairSource(sink(records())));enable(f);
            Throwable failure=f.task(()->{try{permit.dispose(uuid(),(event,facts)->{if(event.equals("sensing_source_disposal_stage_returned")&&facts.get("stage").equals("native-release"))throw new java.io.IOException("Synthetic release outcome force failure");});return null;}catch(Throwable e){return e;}});
            yes(failure instanceof java.io.IOException,"Actual native disposal can return before outcome publication fails");
            yes(Boolean.FALSE.equals(signal(f).get("disposed_by_native_action")),"Unforced release outcome does not clear synthetic retained latch");
            refused("SENSING_RECOVERY_SPENT",()->f.task(()->permit.dispose(uuid(),sink(records()))));permit.close();
        }
    }
    static void exactBindings() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("success",true)) {
            refused("SENSING_RECOVERY_EXECUTOR_REQUIRED",()->NativeVacuumSources.beginReconciliation(f.config,f.nozzle.getId(),sourceId(f),uuid(),()->{}));
            refused("SENSING_SOURCE_CHANGED",()->f.task(()->NativeVacuumSources.beginReconciliation(f.config,f.nozzle.getId(),uuid(),uuid(),()->{})));
            refused("SENSING_RECOVERY_ID_REQUIRED",()->f.task(()->NativeVacuumSources.beginReconciliation(f.config,f.nozzle.getId(),sourceId(f),"not-a-uuid",()->{})));
            NativeVacuumSources.SourceIntervention permit=begin(f,()->{});refused("SENSING_RECOVERY_BUSY",()->begin(f,()->{}));
            Location original=f.machine.getDiscardLocation();((ReferenceMachine)f.machine).setDiscardLocation(original.derive(original.getX()+1,null,null,null));
            refused("SENSING_CONFIGURATION_CHANGED",()->f.task(()->permit.repairSource(sink(records()))));((ReferenceMachine)f.machine).setDiscardLocation(original);permit.close();
            NativeVacuumSources.SourceIntervention settings=begin(f,()->{});f.tip.setVacuumLevelPartOffHigh(f.tip.getVacuumLevelPartOffHigh()+1);
            refused("SENSING_CONFIGURATION_CHANGED",()->f.task(()->settings.repairSource(sink(records()))));settings.close();
        }
    }
    @SuppressWarnings("unchecked") static void terminalObservationRetirement() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("success",true)) {
            enable(f);Map<String,Object> context=context(f,uuid());
            java.util.concurrent.Future<Throwable> original=f.machine.submit(()->{
                try{NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},(event,nozzle,data)->{
                    NativeVacuumSources.observe(f.config,event,nozzle,data,context);
                    if(event.equals("check.before"))throw new IllegalStateException("Synthetic observer interruption after actual native check intent");
                });return null;}catch(Throwable failure){return failure;}
            },null,true);
            Throwable originalFailure=original.get(30,java.util.concurrent.TimeUnit.SECONDS);
            yes(originalFailure!=null&&original.isDone()&&!original.isCancelled(),"Original native wrapper is actually terminal after observer interruption");
            disable(f);Set<String> pending=Set.copyOf((List<String>)snapshot(f).get("pending_observation_ids"));
            yes(pending.size()==1,"Actual native interrupted check retains its exact observation ID");
            refused("SENSING_OBSERVATION_PENDING",()->begin(f,()->{}));
            AtomicInteger terminalChecks=new AtomicInteger();NativeVacuumSensing.Guard terminal=()->{
                terminalChecks.incrementAndGet();if(!original.isDone()||original.isCancelled()||original.get()==null)
                    throw new Bridge.Fault("TERMINAL_SCOPE_UNPROVEN","Original actual native wrapper is not terminal");
            };
            refused("SENSING_TERMINAL_SCOPE_CHANGED",()->f.task(()->NativeVacuumSources.beginReconciliationAfterTerminalScope(f.config,f.nozzle.getId(),sourceId(f),uuid(),Set.of(uuid()),()->{},terminal)));
            refused("TERMINAL_SCOPE_UNPROVEN",()->f.task(()->NativeVacuumSources.beginReconciliationAfterTerminalScope(f.config,f.nozzle.getId(),sourceId(f),uuid(),pending,()->{},()->{throw new Bridge.Fault("TERMINAL_SCOPE_UNPROVEN","Deliberately unavailable terminal proof");})));
            NativeVacuumSources.SourceIntervention permit=f.task(()->NativeVacuumSources.beginReconciliationAfterTerminalScope(f.config,f.nozzle.getId(),sourceId(f),uuid(),pending,()->{},terminal));
            refused("SENSING_OBSERVATION_PENDING",()->f.task(()->permit.repairSource(sink(records()))));
            List<Map<String,Object>> retirement=records();Map<String,Object> receipt=f.task(()->permit.retireTerminalObservations(sink(retirement)));
            yes(retirement.size()==2&&terminalChecks.get()>=5,"Exact scope retirement rechecks actual terminal proof around both publications");
            yes(Boolean.FALSE.equals(receipt.get("original_outcomes_known"))&&Boolean.FALSE.equals(receipt.get("occupancy_cleared")),"Retirement does not invent old outcome or empty occupancy");
            yes(((List<?>)snapshot(f).get("pending_observation_ids")).isEmpty(),"Only exact retired call bookkeeping is no longer active");
            yes(Boolean.TRUE.equals(signal(f).get("material_unknown_observed"))&&((List<?>)signal(f).get("retired_observations")).size()==1,"Original unknown scope persists with conservative material latch");
            refused("SENSING_RECOVERY_SPENT",()->f.task(()->permit.retireTerminalObservations(sink(records()))));
            f.task(()->permit.repairSource(sink(records())));enable(f);
            Throwable checkFailure=f.task(()->{try{NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},observer(f,context));return null;}catch(Throwable failure){return failure;}});
            yes(checkFailure!=null&&Boolean.FALSE.equals(signal(f).get("disposed_by_native_action")),"Unknown material cannot be made empty by repairing the sensor source");
            f.task(()->permit.dispose(uuid(),sink(records())));yes(Boolean.TRUE.equals(verify(f,"part_off").get("native_verdict")),"Actual disposal is required before fresh native empty observation");
            yes(((List<?>)signal(f).get("retired_observations")).size()==1&&f.feeder.getFeedCount()==0,"Original unknown scope retained and no native feed replayed");permit.close();
        }
    }
    static void sharedSourceGenerations() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("success",false)) {
            ReferenceNozzle second=new ReferenceNozzle();second.setName("Explicit second source consumer");
            f.nozzle.getHead().addNozzle(second);second.setAxisX(f.nozzle.getAxisX());second.setAxisY(f.nozzle.getAxisY());second.setAxisZ(f.nozzle.getAxisZ());second.setAxisRotation(f.nozzle.getAxisRotation());
            second.addCompatibleNozzleTip(f.tip);second.setNozzleTip(f.tip);
            ReferenceActuator valve=new ReferenceActuator();valve.setName("Second source fixture valve");valve.setDriver(f.machine.getDrivers().get(0));valve.setValueType(org.openpnp.spi.Actuator.ActuatorValueType.Boolean);second.getHead().addActuator(valve);second.setVacuumActuator(valve);
            ReferenceActuator sensor=new ReferenceActuator();sensor.setName("Second source fixture sensor");sensor.setDriver(f.machine.getDrivers().get(0));sensor.setValueType(org.openpnp.spi.Actuator.ActuatorValueType.Boolean);second.getHead().addActuator(sensor);second.setVacuumSenseActuator(sensor);
            SimulatorMain.configureVacuumLifecycleFixture(f.machine);NativeVacuumSources.installFixture(f.config,"success");
            NativeVacuumSensing.Plan first=NativeVacuumSensing.admit(f.config,f.nozzle.getId()),other=NativeVacuumSensing.admit(f.config,second.getId());
            yes(first.source().equals(other.source()),"Two real native sensor actuators share one owned source generation");
            NativeVacuumSources.SourceIntervention permit=begin(f,()->{});f.task(()->permit.repairSource(sink(records())));
            refused("SENSING_SOURCE_CHANGED",first::validateCurrentState);refused("SENSING_SOURCE_CHANGED",other::validateCurrentState);
            yes(NativeVacuumSensing.admit(f.config,f.nozzle.getId()).source().equals(NativeVacuumSensing.admit(f.config,second.getId()).source()),"Source rotation invalidates both old plans and exposes one exact new generation");
            yes(((List<?>)snapshot(f).get("nozzles")).size()==2,"Source snapshot includes the complete shared consumer union");permit.close();
            sharedUnknownDisposal(f,second);
        }
    }
    @SuppressWarnings("unchecked") static void sharedUnknownDisposal(NativeVacuumSourcesTest.Fixture f,ReferenceNozzle second) throws Exception {
        enable(f);List<java.util.concurrent.Future<Throwable>> originals=new ArrayList<>();
        for(ReferenceNozzle nozzle:List.of(f.nozzle,second)) {
            Map<String,Object> context=context(f,uuid());context.put("nozzle_id",nozzle.getId());
            java.util.concurrent.Future<Throwable> original=f.machine.submit(()->{
                try{NativeVacuumSensing.admit(f.config,nozzle.getId()).verify("part_off",()->{},(event,observed,data)->{
                    NativeVacuumSources.observe(f.config,event,observed,data,context);
                    if(event.equals("check.before"))throw new IllegalStateException("Test interruption of actual shared-source native check");
                });return null;}catch(Throwable failure){return failure;}
            },null,true);yes(original.get(30,java.util.concurrent.TimeUnit.SECONDS)!=null,"Each shared-source pending check comes from an actual completed failed wrapper");originals.add(original);
        }
        disable(f);Set<String> pending=Set.copyOf((List<String>)snapshot(f).get("pending_observation_ids"));yes(pending.size()==2,"Complete shared-source union retains both interrupted native check IDs");
        NativeVacuumSensing.Guard terminal=()->{for(java.util.concurrent.Future<Throwable> original:originals)if(!original.isDone()||original.isCancelled()||original.get()==null)throw new Bridge.Fault("TERMINAL_SCOPE_UNPROVEN","Exact original shared-source wrapper not terminal");};
        refused("SENSING_TERMINAL_SCOPE_CHANGED",()->f.task(()->NativeVacuumSources.beginReconciliationAfterTerminalScope(f.config,f.nozzle.getId(),sourceId(f),uuid(),Set.of(pending.iterator().next()),()->{},terminal)));
        NativeVacuumSources.SourceIntervention first=f.task(()->NativeVacuumSources.beginReconciliationAfterTerminalScope(f.config,f.nozzle.getId(),sourceId(f),uuid(),pending,()->{},terminal));
        f.task(()->first.retireTerminalObservations(sink(records())));
        yes(Boolean.TRUE.equals(signal(f).get("material_unknown_observed"))&&Boolean.TRUE.equals(signal(f,second.getId()).get("material_unknown_observed")),"Exact scope retirement latches unknown material on both affected nozzles");
        f.task(()->first.repairSource(sink(records())));NativeVacuumSensing.Plan firstGeneration=NativeVacuumSensing.admit(f.config,f.nozzle.getId());enable(f);String firstAction=uuid();f.task(()->first.dispose(firstAction,sink(records())));
        yes(Boolean.TRUE.equals(signal(f).get("disposed_by_native_action"))&&Boolean.FALSE.equals(signal(f,second.getId()).get("disposed_by_native_action")),"One nozzle's actual disposal cannot clear the other shared-source material latch");
        Map<String,Object> secondContext=context(f,uuid());secondContext.put("nozzle_id",second.getId());
        Throwable secondRead=f.task(()->{try{NativeVacuumSensing.admit(f.config,second.getId()).measure(1,()->{},observer(f,secondContext));return null;}catch(Throwable failure){return failure;}});yes(secondRead!=null,"Other nozzle still returns unknown through its actual native sensor after first disposal");
        disable(f);first.close();NativeVacuumSources.SourceIntervention other=f.task(()->NativeVacuumSources.beginReconciliation(f.config,second.getId(),sourceId(f),uuid(),()->{}));f.task(()->other.repairSource(sink(records())));
        refused("SENSING_SOURCE_CHANGED",firstGeneration::validateCurrentState);
        yes(firstAction.equals(signal(f).get("disposal_action_id"))&&Boolean.FALSE.equals(signal(f,second.getId()).get("disposed_by_native_action")),"Next shared source generation preserves first disposition and second unknown state");
        enable(f);String secondAction=uuid();f.task(()->other.dispose(secondAction,sink(records())));
        for(ReferenceNozzle nozzle:List.of(f.nozzle,second)) {
            NativeVacuumSensing.Plan fresh=NativeVacuumSensing.admit(f.config,nozzle.getId());Map<String,Object> context=context(f,uuid());context.put("nozzle_id",nozzle.getId());
            yes(Boolean.TRUE.equals(f.task(()->fresh.verify("part_off",()->{},observer(f,context))).get("native_verdict")),"Fresh final-generation native probe observes empty for each disposed nozzle");
            yes(((List<?>)signal(f,nozzle.getId()).get("retired_observations")).size()==1,"Each original interrupted observation remains retained after both disposals");
        }
        yes(!firstAction.equals(secondAction)&&f.feeder.getFeedCount()==0,"Shared-nozzle cleanup uses distinct new actions without any feeder replay");other.close();
    }
    static void reloadNoAuthority() throws Exception {
        try(NativeVacuumSourcesTest.Fixture f=new NativeVacuumSourcesTest.Fixture("success",true)) {
            NativeVacuumSources.SourceIntervention permit=begin(f,()->{});f.task(()->permit.repairSource(sink(records())));String source=sourceId(f);f.config.save();
            yes(!java.nio.file.Files.readString(f.root.resolve("machine.xml")).contains(source),"Source generation not serialized as authority");
            f.machine.close();Configuration.initialize(f.root.toFile());Configuration.get().load();
            try {yes(Boolean.FALSE.equals(NativeVacuumSources.reconciliationSnapshot(Configuration.get()).get("available")),"Native reload restores no source or intervention authority");}
            finally {Configuration.get().getMachine().close();permit.close();}
        }
    }
}
