/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.*;
import java.lang.reflect.Field;
import org.openpnp.spi.base.AbstractNozzle;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Passive source readiness and retained job admission. No remote input creates a source. */
public final class NativeVacuumSources {
    public static final String PROFILE = "native-vacuum-sensing-v1";
    public static final Set<String> SCENARIOS = Set.of("success", "missed-pick-retry", "retained-after-place", "lost-before-place", "invalid-read");
    private static volatile Fixture fixture;
    private NativeVacuumSources() { }

    public static Map<String,Object> inspect(Configuration config) {
        List<Object> rows = new ArrayList<>(); boolean available = false;
        if (config != null && Configuration.get() == config && config.getMachine() != null) {
            try { requireBoundedMachine(config); }
            catch (Exception failure) { return Collections.unmodifiableMap(map("profile", PROFILE, "available", false, "source_profile", "controlled-native-vacuum-v1", "simulation_only", true, "hardware_qualified", false, "code", "SENSING_GRAPH_LIMIT", "nozzles", List.of())); }
            for (Head head : config.getMachine().getHeads()) for (Nozzle nozzle : head.getNozzles()) {
                Map<String,Object> row = map("nozzle_id", nozzle.getId(), "ready", false);
                try {
                    requireRestartProbe(config,nozzle);
                    NativeVacuumSensing.Plan plan = NativeVacuumSensing.admit(config, nozzle.getId());
                    row.putAll(map("ready", true, "source", plan.source(), "nozzle_tip_id", nozzle.getNozzleTip().getId())); available = true;
                } catch (Exception e) { row.put("code", e instanceof Bridge.Fault ? ((Bridge.Fault)e).code : "SENSING_PROFILE_UNSUPPORTED"); }
                rows.add(Collections.unmodifiableMap(row));
            }
        }
        Map<String,Object> lifecycle = null;
        if (available) try { lifecycle = describeLifecyclePolicies(config); } catch (Exception changed) { available = false; }
        return Collections.unmodifiableMap(map("profile", PROFILE, "available", available, "source_profile", "controlled-native-vacuum-v1",
            "simulation_only", true, "hardware_qualified", false, "signal_model", "explicit-synthetic-native-check-scenario", "lifecycle_policies", lifecycle, "nozzles", List.copyOf(rows)));
    }

    /** Captures all eligible nozzle/tip pairs before initialize, including initial installed tips. */
    public static Admission admitJob(Configuration config, Job job) throws Exception { return new Admission(config, job); }

    public static final class Admission {
        private final Configuration config;
        private final Machine machine;
        private final Job job;
        private final List<Object> graph;
        private final Map<ReferenceNozzle,Set<ReferenceNozzleTip>> allowed = new IdentityHashMap<>();
        private final Map<ReferenceNozzle,Map<ReferenceNozzleTip,NativeVacuumSensing.CandidateBinding>> sensing = new IdentityHashMap<>();
        private final List<String> nozzleIds;

        private Admission(Configuration config, Job job) throws Exception {
            if (config == null || Configuration.get() != config || config.getMachine() == null || config.getMachine().getClass() != ReferenceMachine.class)
                throw fault("SENSING_CONFIGURATION_CHANGED", "A current exact native machine is required");
            this.config = config; machine = config.getMachine(); this.job = job;
            if (machine.getPnpJobProcessor().getClass() != ReferencePnpJobProcessor.class)
                throw fault("SENSING_PROFILE_UNSUPPORTED", "Exact native job processor required");
            requireBoundedMachine(config); requirePlanner(machine);
            for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles())requireRestartProbe(config,nozzle);
            List<BoardLocation> boards = boundedBoards(job);
            Set<Part> parts = Collections.newSetFromMap(new IdentityHashMap<Part,Boolean>());
            for (BoardLocation board : boards) {
                if (!board.isEnabled()) continue;
                for (Placement placement : board.getBoard().getPlacements())
                    if (placement.getType() == Placement.Type.Placement && placement.isEnabled() && placement.getSide() == board.getGlobalSide()
                            && !job.retrievePlacedStatus(board, placement.getId())) {
                        if (placement.getPart() == null) throw fault("SENSING_JOB_INVALID", "Every pending placement needs a native part");
                        if (placement.getPart().getPackage() != null) requireBoundedCompatibility(placement.getPart().getPackage());
                        parts.add(placement.getPart());
                        if (parts.size() > 4096) throw fault("SENSING_GRAPH_LIMIT", "At most 4096 pending native parts are supported");
                    }
            }
            if (parts.size() > 4096) throw fault("SENSING_GRAPH_LIMIT", "At most 4096 pending native parts are supported");
            Head head = machine.getDefaultHead(); int pairs = 0;
            for (Nozzle item : head.getNozzles()) {
                if (item.getClass() != ReferenceNozzle.class) throw fault("SENSING_PROFILE_UNSUPPORTED", "Exact native nozzles required");
                ReferenceNozzle nozzle = (ReferenceNozzle)item;
                Set<ReferenceNozzleTip> tips = Collections.newSetFromMap(new IdentityHashMap<ReferenceNozzleTip,Boolean>());
                if (nozzle.getNozzleTip() != null) tips.add(exactTip(machine, nozzle.getNozzleTip()));
                for (Part part : parts) for (NozzleTip tip : nozzle.getCompatibleNozzleTips(part)) tips.add(exactTip(machine, tip));
                allowed.put(nozzle, tips);
                Map<ReferenceNozzleTip,NativeVacuumSensing.CandidateBinding> bindings = new IdentityHashMap<>();
                for (ReferenceNozzleTip tip : tips) {
                    if (++pairs > 4096) throw fault("SENSING_GRAPH_LIMIT", "Too many eligible native nozzle and tip pairs");
                    if (usesSensing(tip)) bindings.put(tip, NativeVacuumSensing.bindCandidate(config, nozzle, tip));
                }
                if (!bindings.isEmpty()) sensing.put(nozzle, bindings);
            }
            graph = capture(config, job, !sensing.isEmpty());
            List<String> ids = new ArrayList<>(); for (ReferenceNozzle nozzle : sensing.keySet()) ids.add(nozzle.getId()); Collections.sort(ids); nozzleIds = List.copyOf(ids);
            validateCurrentState();
        }

        public void validateCurrentState() throws Exception {
            if (Configuration.get() != config || config.getMachine() != machine || !graph.equals(capture(config, job, !sensing.isEmpty())))
                throw fault("SENSING_CONFIGURATION_CHANGED", "Native job, tool candidates or sensing settings changed after admission");
            for (Map.Entry<ReferenceNozzle,Set<ReferenceNozzleTip>> entry : allowed.entrySet()) {
                requireRestartProbe(config,entry.getKey());
                ReferenceNozzleTip installed = entry.getKey().getNozzleTip();
                if (installed != null && !entry.getValue().contains(installed)) throw fault("SENSING_TIP_CHANGED", "Installed tip was not an admitted job candidate");
            }
            for (Map<ReferenceNozzleTip,NativeVacuumSensing.CandidateBinding> bindings : sensing.values())
                for (NativeVacuumSensing.CandidateBinding binding : bindings.values()) binding.validateCurrentState();
        }
        public List<String> sensingNozzleIds() { return nozzleIds; }
        public Map<String,Object> source(ReferenceNozzle nozzle) throws Exception { return current(nozzle).source(); }
        public Map<String,Object> currentBinding(String machineId, String instance, String revision, String nozzleId) throws Exception {
            validateCurrentState();
            ReferenceNozzle found = null;
            for (ReferenceNozzle nozzle : sensing.keySet()) if (nozzleId.equals(nozzle.getId())) found = nozzle;
            if (found == null) throw fault("SENSING_BINDING_REQUIRED", "No admitted sensing nozzle matches the request");
            NativeVacuumSensing.CandidateBinding binding = current(found);
            return NativeVacuumJournal.binding(machineId, instance, revision, nozzleId, binding.tip().getId(), found.getVacuumSenseActuator().getId(), binding.source());
        }
        public void observe(String event, ReferenceNozzle nozzle, Map<String,Object> data) throws Exception { observe(event, nozzle, data, null); }
        public void observe(String event, ReferenceNozzle nozzle, Map<String,Object> data, Map<String,Object> context) throws Exception {
            boolean cleanup = event.startsWith("valve.") && Boolean.FALSE.equals(data.get("enabled"));
            if (!cleanup) {
                validateCurrentState(); NativeVacuumSensing.CandidateBinding binding = current(nozzle);
                if (!Objects.equals(data.get("nozzle_tip_id"), binding.tip().getId())
                        || !Objects.equals(data.get("sensor_id"), nozzle.getVacuumSenseActuator().getId()) || !binding.source().equals(data.get("source")))
                    throw fault("SENSING_IDENTITY_CHANGED", "Native observation differs from its admitted source and installed tip");
            }
            NativeVacuumSources.observe(config, event, nozzle, data, context);
        }
        private NativeVacuumSensing.CandidateBinding current(ReferenceNozzle nozzle) throws Exception {
            Map<ReferenceNozzleTip,NativeVacuumSensing.CandidateBinding> bindings = sensing.get(nozzle);
            NativeVacuumSensing.CandidateBinding binding = bindings == null ? null : bindings.get(nozzle.getNozzleTip());
            if (binding == null) throw fault("SENSING_TIP_CHANGED", "Current installed tip has no admitted sensing source");
            binding.validateCurrentState(); return binding;
        }
    }

    /** Called only from actual native observer events; it never reads or actuates a device. */
    public static void observe(Configuration config, String event, ReferenceNozzle nozzle, Map<String,Object> data) {
        Fixture current = fixture;
        if (current != null && current.config == config) current.observe(event, nozzle, data, null);
    }

    /** Called after final fixture reload. This capability and its signals are never serialized. */
    static void installFixture(Configuration config, String scenario) throws Exception {
        if (!SCENARIOS.contains(scenario) || !SimulatorMain.isSettledFreshFixture(config))
            throw fault("SENSING_FIXTURE_REQUIRED", "An explicitly selected fresh settled fixture is required");
        if (config.getMachine().isEnabled() || config.getMachine().isHomed() || config.getMachine().isBusy())
            throw fault("SENSING_FIXTURE_REQUIRED", "Install fixture sources before native work begins");
        Fixture next = new Fixture(config, scenario); fixture = next;
    }
    /** Bounded process-owned native identity capture, independent of any controlled source. */
    static final class RestartGraph {
        private final Configuration config;private final List<Object> identities;private final Map<String,Object> descriptor;
        private RestartGraph(Configuration config)throws Exception {this.config=config;identities=restartIdentities(config);descriptor=restartDescriptor(config);}
        Map<String,Object> descriptor(){return descriptor;}
        void check()throws Exception {if(!identities.equals(restartIdentities(config))||!descriptor.equals(restartDescriptor(config)))throw fault("SENSING_RESTART_GRAPH_CHANGED","Restart native identities or settings changed after local capture");}
    }
    static RestartGraph captureRestartGraph(Configuration config)throws Exception {return new RestartGraph(config);}
    private static List<Object> restartIdentities(Configuration config)throws Exception {
        List<Object> values=new ArrayList<>(capture(config,null,true));Machine machine=config.getMachine();
        for(Driver driver:machine.getDrivers())object(values,driver);
        for(Axis axis:machine.getAxes())object(values,axis);
        for(Actuator actuator:machine.getAllActuators()){object(values,actuator);object(values,actuator.getDriver());object(values,actuator.getHead());}
        for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles()){object(values,nozzle.getNozzleTip());object(values,nozzle.getPart());}
        return List.copyOf(values);
    }
    private static Map<String,Object> restartDescriptor(Configuration config)throws Exception {
        requireBoundedMachine(config);requireLifecycleSafe(config);Bridge.verifyNativeSimulatorClasses(config.getMachine());
        List<Object> rows=new ArrayList<>();Set<String> ids=new HashSet<>();
        for(Head head:config.getMachine().getHeads())for(Nozzle item:head.getNozzles()){
            ReferenceNozzle nozzle=(ReferenceNozzle)item;String id=nozzle.getId();
            if(id==null||!id.matches("[A-Za-z0-9_.:+-]{1,128}")||!ids.add(id.toLowerCase(Locale.ROOT)))throw fault("SENSING_IDENTITY_CHANGED","Restart nozzle identifiers must be unique and bounded");
            ReferenceNozzleTip tip=exactTip(config.getMachine(),nozzle.getNozzleTip());
            Map<String,Object> settings=NativeVacuumSettings.describe(config,nozzle,tip);
            if(nozzle.getVacuumSenseActuator()==null||nozzle.getVacuumActuator()==null)throw fault("SENSING_BINDING_REQUIRED","Every restart nozzle needs exact configured sensor and valve");
            rows.add(immutable(map("nozzle_id",id,"nozzle_tip_id",tip.getId(),"sensor_id",nozzle.getVacuumSenseActuator().getId(),"valve_id",nozzle.getVacuumActuator().getId(),"settings",settings)));
        }
        if(rows.isEmpty())throw fault("SENSING_BINDING_REQUIRED","Restart source needs at least one native nozzle");
        rows.sort(Comparator.comparing(row->(String)((Map<?,?>)row).get("nozzle_id")));
        java.io.ByteArrayOutputStream xml=new java.io.ByteArrayOutputStream(){
            @Override public synchronized void write(int value){if(count>=8*1024*1024)throw new IllegalStateException("Restart native machine serialization capacity exceeded");super.write(value);}
            @Override public synchronized void write(byte[] bytes,int offset,int length){if(length>8*1024*1024-count)throw new IllegalStateException("Restart native machine serialization capacity exceeded");super.write(bytes,offset,length);}
        };
        Configuration.createSerializer().write(config.getMachine(),xml);
        StringBuilder encoded=new StringBuilder(64);
        for(byte value:java.security.MessageDigest.getInstance("SHA-256").digest(xml.toByteArray()))encoded.append(String.format(Locale.ROOT,"%02x",value));
        String hash=encoded.toString();
        return immutable(map("nozzles",rows,"native_machine_sha256",hash,"native_machine_bytes",xml.size(),"discard_location",location(config.getMachine().getDiscardLocation()),"physical_occupancy_verified",false));
    }

    /** Fresh source bootstrap under the actual root restart permit. Old calls remain history,
     * and no model Part is set or released by installing this deliberately unknown signal. */
    static synchronized Map<String,Object> installRestartFixture(GuiSensingFixture.RestartAttestation attestation,
            NativeFaultedJobReplacement.RestartPermit permit,InterventionSink sink)throws Exception {
        Configuration config=attestation.configuration();permit.check();attestation.consume();
        Fixture next=null;
        try {
            Map<String,Object> authorized=permit.authorizeSourceBootstrap(config,attestation.descriptor());
            Map<String,Object> history=NativeSensingReconciliation.object(authorized,"historical_fault_sets");
            // One shared immutable history map retains exact provenance without duplicating it per nozzle.
            history=NativeSensingReconciliation.freeze(history);if(history.isEmpty())throw fault("SENSING_RESTART_HISTORY_REQUIRED","Restart needs exact historical local fault sets");
            String historyHash=NativeFaultedJobReplacement.digest(history),bootstrap=UUID.randomUUID().toString();
            Map<String,Object> intent=new LinkedHashMap<>(authorized);intent.remove("historical_fault_sets");
            intent.putAll(map("schema_version",1,"bootstrap_id",bootstrap,"historical_fault_sets_sha256",historyHash,
                "simulation_only",true,"physical_occupancy_verified",false,"execution_authority_restored",false));
            intent=NativeSensingReconciliation.freeze(intent);
            next=new Fixture(config,attestation.scenario(),false);next.restartHistory=history;next.restartHistoryHash=historyHash;next.restartIdentity=(String)authorized.get("reattachment_id");next.initializeRestartHistory();
            bootstrapBoundary(attestation,permit,authorized);sink.append("sensing_source_restart_bootstrap_intent",intent);bootstrapBoundary(attestation,permit,authorized);permit.requireForcedSourceBootstrapIntent(intent);
            if(((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource()!=null)throw fault("SENSING_SOURCE_CHANGED","Another controlled source appeared before restart bootstrap");
            next.source=next.installSource(0);fixture=next;
            bootstrapBoundary(attestation,permit,authorized);next.requireCurrent();
            for(String old:next.historicalSourceIds)if(old.equals(next.source.provenance().get("source_id")))throw fault("SENSING_SOURCE_CHANGED","Restart must allocate a distinct native source identity");
            Map<String,Object> result=new LinkedHashMap<>(intent);result.putAll(map("receipt_id",UUID.randomUUID().toString(),"bootstrap_intent_sha256",NativeFaultedJobReplacement.digest(intent),"source",next.source.provenance(),"source_generation",0,"fixture",next.snapshot()));
            result=NativeSensingReconciliation.freeze(result);
            sink.append("sensing_source_restart_bootstrap_returned",result);bootstrapBoundary(attestation,permit,authorized);next.requireCurrent();
            permit.acceptForcedEvent("sensing_source_restart_bootstrap_returned",result);bootstrapBoundary(attestation,permit,authorized);return result;
        }catch(Exception|Error failure){
            permit.close();attestation.close();
            if(next!=null&&next.source!=null)try{next.source.close(next.owner);}catch(Exception cleanup){failure.addSuppressed(cleanup);}
            throw failure;
        }
    }
    private static void bootstrapBoundary(GuiSensingFixture.RestartAttestation attestation,NativeFaultedJobReplacement.RestartPermit permit,Map<String,Object> authorized)throws Exception {
        permit.check();attestation.check();
        if(!authorized.equals(permit.authorizeSourceBootstrap(attestation.configuration(),attestation.descriptor())))throw fault("SENSING_RESTART_HISTORY_CHANGED","Exact restart source history or authority changed");
        permit.check();
    }
    private static void requireRestartProbe(Configuration config,Nozzle nozzle)throws Exception {
        Fixture current=fixture;if(current==null||current.config!=config)return;
        synchronized(current){Signal signal=current.signals.get(nozzle);if(signal!=null&&signal.restartRequired&&!current.restartProbeCurrent(signal))throw fault("SENSING_RESTART_RECONCILIATION_REQUIRED","A fresh source grants no sensing or job readiness; native disposal and a current-source part-off probe are required");}
    }

    /** Bridge supplies its already validated native operation context; it grants no source authority. */
    static void observe(Configuration config, String event, ReferenceNozzle nozzle, Map<String,Object> data, Map<String,Object> context) {
        Fixture current = fixture;
        if (current != null && current.config == config) current.observe(event, nozzle, data, context);
    }

    /** Append must force before returning. The owner must use its existing operation journal. */
    @FunctionalInterface interface InterventionSink { void append(String event, Map<String,Object> facts) throws Exception; }

    /** Native executor only; the Guard is the local one-use task's live authority, never a JSON token. */
    static SourceIntervention beginReconciliation(Configuration config, String nozzleId, String expectedSourceId,
            String interventionId, NativeVacuumSensing.Guard guard) throws Exception {
        return beginReconciliation(config, nozzleId, expectedSourceId, interventionId, guard, null, null);
    }

    /** Terminal guard must attest the original wrapper is complete and this submission alone owns native execution. */
    static SourceIntervention beginReconciliationAfterTerminalScope(Configuration config, String nozzleId, String expectedSourceId,
            String interventionId, Set<String> expectedPendingObservationIds, NativeVacuumSensing.Guard guard,
            NativeVacuumSensing.Guard terminalNativeScopeGuard) throws Exception {
        if (expectedPendingObservationIds == null || expectedPendingObservationIds.isEmpty() || expectedPendingObservationIds.size() > 256 || terminalNativeScopeGuard == null)
            throw fault("SENSING_TERMINAL_SCOPE_REQUIRED", "Exact bounded pending IDs and a live terminal native scope guard are required");
        Set<String> expected = new HashSet<>(expectedPendingObservationIds); for (String id : expected) canonicalUuid(id);
        return beginReconciliation(config, nozzleId, expectedSourceId, interventionId, guard, Set.copyOf(expected), terminalNativeScopeGuard);
    }
    private static SourceIntervention beginReconciliation(Configuration config, String nozzleId, String expectedSourceId,
            String interventionId, NativeVacuumSensing.Guard guard, Set<String> expectedPending, NativeVacuumSensing.Guard terminalGuard) throws Exception {
        canonicalUuid(interventionId); canonicalUuid(expectedSourceId);
        if (guard == null) throw fault("SENSING_RECOVERY_AUTHORITY_REQUIRED", "A live local recovery guard is required");
        guard.check(); Fixture current = fixture;
        if (current == null || current.config != config) throw fault("SENSING_FIXTURE_REQUIRED", "No current owned fixture exists");
        current.requireCurrent();
        if (config.getMachine().isEnabled()) throw fault("SENSING_RECOVERY_DISABLED_REQUIRED", "Disable before admitting source intervention");
        SourceIntervention result;
        synchronized (current) {
            if (current.intervention != null && !current.intervention.closed) throw fault("SENSING_RECOVERY_BUSY", "An existing source intervention is still owned");
            if (!expectedSourceId.equals(current.source.provenance().get("source_id"))) throw fault("SENSING_SOURCE_CHANGED", "The expected source is no longer current");
            if (current.usedInterventions.contains(interventionId) || current.usedInterventions.size() >= 1024)
                throw fault("SENSING_RECOVERY_SPENT", "Intervention identity reused or retained capacity reached");
            Set<String> pending = current.pendingIds();
            if (expectedPending == null && !pending.isEmpty()) throw fault("SENSING_OBSERVATION_PENDING", "A native observation still owns the source");
            if (expectedPending != null) {
                terminalGuard.check();
                if (!pending.equals(expectedPending)) throw fault("SENSING_TERMINAL_SCOPE_CHANGED", "The complete shared source pending scope differs");
                if (current.retiredCount() + pending.size() > 256) throw fault("SENSING_RECOVERY_CAPACITY", "Retained native scope history capacity reached; no history is evicted");
            }
            result = new SourceIntervention(current, nozzleId, interventionId, guard, expectedPending, terminalGuard);
            guard.check(); current.usedInterventions.add(interventionId); current.intervention = result;
        }
        return result;
    }

    /** Passive source facts only. Null/empty model Parts do not establish occupancy. */
    static Map<String,Object> reconciliationSnapshot(Configuration config) {
        Fixture current = fixture;
        if (current == null || current.config != config || Configuration.get() != config)
            return immutable(map("available", false, "execution_authority", false));
        synchronized (current) { return current.snapshot(); }
    }

    static final class SourceIntervention implements AutoCloseable {
        private final Fixture fixture;
        private final String id;
        private final NativeVacuumSensing.Guard guard, terminalGuard;
        private final Set<String> expectedPending;
        private final Map<String,Map<String,Object>> pendingBaseline;
        private final ReferenceNozzle nozzle;
        private final List<Object> graph;
        private final Location discard;
        private final Map<String,Object> oldSource;
        private NativeVacuumSensing.Plan plan;
        private VacuumSensing.ControlledSource expectedSource;
        private volatile boolean retirementStarted, retired, repairStarted, repaired, disposalStarted, closed, failed;
        private SourceIntervention(Fixture fixture, String nozzleId, String id, NativeVacuumSensing.Guard guard,
                Set<String> expectedPending, NativeVacuumSensing.Guard terminalGuard) throws Exception {
            this.fixture = fixture; this.id = id; this.guard = guard; this.expectedPending = expectedPending; this.terminalGuard = terminalGuard;
            pendingBaseline = expectedPending == null ? Map.of() : fixture.pendingRecords();
            plan = NativeVacuumSensing.admit(fixture.config, nozzleId); nozzle = plan.nozzle();
            if (!fixture.signals.containsKey(nozzle)) throw fault("SENSING_FIXTURE_REQUIRED", "The nozzle has no owned fixture signal");
            graph = capture(fixture.config, null, true); discard = fixture.config.getMachine().getDiscardLocation();
            expectedSource = fixture.source; oldSource = immutable(expectedSource.provenance());
        }
        Map<String,Object> preview() {
            return immutable(map("intervention_id", id, "nozzle_id", nozzle.getId(), "old_source", oldSource,
                "current_source", expectedSource.provenance(), "discard_location", location(discard),
                "pending_observation_ids", expectedPending == null ? List.of() : sorted(expectedPending),
                "terminal_scope_retired", retired, "repair_started", repairStarted, "repair_returned", repaired, "disposal_started", disposalStarted,
                "failed_or_uncertain", failed, "closed", closed, "simulation_only", true, "physical_occupancy_verified", false));
        }
        private void boundary() throws Exception {
            if (closed || failed) throw fault("SENSING_RECOVERY_SPENT", "Source intervention is closed or uncertain");
            guard.check(); fixture.requireCurrent();
            if (fixture.intervention != this || fixture.source != expectedSource) throw fault("SENSING_SOURCE_CHANGED", "Intervention source ownership changed");
            if (!graph.equals(capture(fixture.config, null, true)) || !Objects.equals(discard, fixture.config.getMachine().getDiscardLocation()))
                throw fault("SENSING_CONFIGURATION_CHANGED", "Recovery native graph or discard geometry changed");
            plan.validateCurrentState(); guard.check();
        }
        private Map<String,Object> facts(String kind, String stage, String actionId) {
            return immutable(map("profile", "native-simulator-source-intervention-v1", "intervention_id", id,
                "action_id", actionId, "kind", kind, "stage", stage, "nozzle_id", nozzle.getId(),
                "old_source", oldSource, "current_source", expectedSource.provenance(),
                "fixture", fixture.snapshot(), "simulation_only", true, "physical_occupancy_verified", false));
        }
        /** Retires only captured call bookkeeping after exact terminal-wrapper attestation; original outcomes remain unknown. */
        Map<String,Object> retireTerminalObservations(InterventionSink sink) throws Exception {
            if (sink == null) throw fault("SENSING_RECOVERY_SINK_REQUIRED", "A durable intervention sink is required");
            boundary();
            if (expectedPending == null || retirementStarted) throw fault("SENSING_RECOVERY_SPENT", "No unused exact terminal scope retirement exists");
            requirePendingScope(); retirementStarted = true;
            try {
                Map<String,Object> receipt = immutable(map("profile", "native-simulator-source-intervention-v1", "intervention_id", id,
                    "old_source", oldSource, "pending_observation_ids", sorted(expectedPending), "retired_observations", new ArrayList<>(pendingBaseline.values()),
                    "original_outcomes_known", false, "occupancy_cleared", false, "simulation_only", true, "physical_occupancy_verified", false));
                sink.append("sensing_source_scope_retirement_intent", receipt); boundary(); requirePendingScope();
                sink.append("sensing_source_scope_retirement_returned", receipt); boundary(); requirePendingScope();
                synchronized (fixture) {
                    for (Signal signal : fixture.signals.values()) {
                        for (String observation : new ArrayList<>(signal.active.keySet())) {
                            Map<String,Object> record = signal.active.get(observation);
                            if (!expectedPending.contains(observation)) throw fault("SENSING_TERMINAL_SCOPE_CHANGED", "New native observation appeared before retirement");
                            signal.retired.put(observation, immutable(map("intervention_id", id, "observation", record, "original_outcome", "unknown")));
                            signal.materialUnknown = true; signal.disposed = false;
                            if (signal.origin == null) signal.origin = immutable(map("native_event", record.get("native_event"), "nozzle_id", record.get("nozzle_id"),
                                "observation_id", observation, "source", oldSource, "bridge_context", record.get("bridge_context")));
                        }
                        signal.active.clear(); signal.checkId = null; signal.kind = null; signal.stage = null;
                    }
                    retired = true;
                }
                return receipt;
            } catch (Exception | Error failure) { failed = true; throw failure; }
        }
        private void requirePendingScope() throws Exception {
            terminalGuard.check();
            if (!expectedPending.equals(fixture.pendingIds()) || !pendingBaseline.equals(fixture.pendingRecords()))
                throw fault("SENSING_TERMINAL_SCOPE_CHANGED", "Captured native pending scope changed");
            terminalGuard.check(); guard.check();
        }
        /** A new healthy source generation retains material latches and their original evidence. */
        Map<String,Object> repairSource(InterventionSink sink) throws Exception {
            if (sink == null) throw fault("SENSING_RECOVERY_SINK_REQUIRED", "A durable intervention sink is required");
            boundary();
            if (repairStarted) throw fault("SENSING_RECOVERY_SPENT", "Source repair cannot be replayed");
            if (expectedPending != null && !retired || !fixture.pendingIds().isEmpty())
                throw fault("SENSING_OBSERVATION_PENDING", "Retire the exact terminal native scope before source repair");
            if (fixture.config.getMachine().isEnabled()) throw fault("SENSING_RECOVERY_DISABLED_REQUIRED", "Repair source only while disabled");
            repairStarted = true;
            try {
                sink.append("sensing_source_intervention_intent", facts("repair_sensor_source", "replace-source", null));
                boundary(); // after durable intent, immediately before source revocation/replacement
                synchronized (fixture) {
                    expectedSource.close(fixture.owner);
                    // Revocation is itself an effect. A revoked grant must not install a successor.
                    guard.check();
                    if (Configuration.get() != fixture.config || fixture.config.getMachine().isEnabled()
                            || !graph.equals(capture(fixture.config, null, true))
                            || ((NullDriver)fixture.config.getMachine().getDrivers().get(0)).getControlledVacuumSource() != expectedSource)
                        throw fault("SENSING_CONFIGURATION_CHANGED", "Source replacement context changed after revocation");
                    guard.check();
                    fixture.source = fixture.installSource(fixture.sourceGeneration + 1);
                    fixture.sourceGeneration++; fixture.recoveryActive = true; fixture.sensorRepaired = true;
                    expectedSource = fixture.source;
                }
                plan = NativeVacuumSensing.admit(fixture.config, nozzle.getId());
                boundary();
                Map<String,Object> result = facts("repair_sensor_source", "replace-source", null);
                sink.append("sensing_source_intervention_returned", result);
                boundary(); repaired = true; return result;
            } catch (Exception | Error failure) { failed = true; throw failure; }
        }
        /** Fixed native disposal at the captured machine discard location; never accepts a callback or Part setter. */
        Map<String,Object> dispose(String actionId, InterventionSink sink) throws Exception {
            canonicalUuid(actionId);
            if (sink == null) throw fault("SENSING_RECOVERY_SINK_REQUIRED", "A durable intervention sink is required");
            boundary();
            if (!repaired || disposalStarted) throw fault("SENSING_RECOVERY_SPENT", "Repair must complete once before bounded disposal");
            Machine machine = fixture.config.getMachine();
            if (!machine.isEnabled() || !machine.isHomed()) throw fault("SENSING_SAFE_Z_REQUIRED", "Native disposal requires enabled, homed simulator");
            if (!finite(discard)) throw fault("SENSING_DISCARD_UNQUALIFIED", "A finite native discard location is required");
            Length safe = nozzle.getEffectiveSafeZ();
            if (safe == null) throw fault("SENSING_DISCARD_UNQUALIFIED", "The native discard path requires finite Safe Z");
            Location traverse = discard.derive(null, null, safe.convertToUnits(discard.getUnits()).getValue(), null);
            if (!finite(traverse) || !nozzle.isInSafeZZone(traverse.getLengthZ())) throw fault("SENSING_DISCARD_UNQUALIFIED", "Discard traverse is outside native Safe Z");
            requireValidLocation(traverse); requireValidLocation(discard); boundary(); disposalStarted = true;
            try {
                effect("safe-z-before", actionId, sink, () -> nozzle.getHead().moveToSafeZ());
                effect("discard-traverse", actionId, sink, () -> nozzle.moveTo(traverse));
                effect("discard-descend", actionId, sink, () -> nozzle.moveTo(discard));
                if (!at(nozzle.getLocation(), discard)) throw fault("SENSING_DISCARD_UNQUALIFIED", "Native nozzle did not reach the captured discard location");
                effect("native-release", actionId, sink, () -> nozzle.place());
                // Only a returned, durably observed native release changes the synthetic latch.
                // The original fault and component history stay present; this grants no journal clearance.
                synchronized (fixture) {
                    fixture.signals.get(nozzle).disposalAction = actionId;
                    fixture.signals.get(nozzle).disposed = true;
                }
                effect("safe-z-after", actionId, sink, () -> nozzle.moveToSafeZ());
                effect("standstill", actionId, sink, () -> machine.getMotionPlanner().waitForCompletion(null, MotionPlanner.CompletionType.WaitForStillstand));
                boundary();
                if (nozzle.getPart() != null || !nozzle.isInSafeZZone(nozzle.getLocation().getLengthZ()))
                    throw fault("SENSING_DISPOSAL_UNRESOLVED", "Native disposal lacks model-empty/Safe Z postconditions");
                Map<String,Object> result = facts("dispose_synthetic_nozzle_material", "complete", actionId);
                sink.append("sensing_source_disposal_returned", result); boundary(); return result;
            } catch (Exception | Error failure) { failed = true; throw failure; }
        }
        private void requireValidLocation(Location target) throws Exception {
            AxesLocation axes = nozzle.toRaw(nozzle.toHeadLocation(target, Locatable.LocationOption.Quiet), Locatable.LocationOption.Quiet);
            if (!fixture.config.getMachine().getMotionPlanner().isValidLocation(nozzle, axes))
                throw fault("SENSING_DISCARD_UNQUALIFIED", "Discard target violates native motion limits");
        }
        @FunctionalInterface private interface NativeEffect { void run() throws Exception; }
        private void effect(String stage, String action, InterventionSink sink, NativeEffect effect) throws Exception {
            boundary(); sink.append("sensing_source_disposal_intent", facts("dispose_synthetic_nozzle_material", stage, action));
            boundary(); effect.run(); boundary();
            sink.append("sensing_source_disposal_stage_returned", facts("dispose_synthetic_nozzle_material", stage, action)); boundary();
        }
        @Override public void close() { closed = true; }
    }

    private static final class Signal {
        String checkId, kind, stage; long afterPick;
        boolean retained, lost, sensorFault, materialUnknown, disposed;
        String disposalAction, restartProbeSource;boolean restartRequired;
        final List<String> historicalObservationIds=new ArrayList<>(),historicalPendingIds=new ArrayList<>();
        Map<String,Object> origin;
        final Map<String,Map<String,Object>> active = new LinkedHashMap<>(), retired = new LinkedHashMap<>();
        Map<String,Object> snapshot() {Map<String,Object> result=map("retained_observed", retained, "lost_observed", lost,
            "sensor_failure_observed", sensorFault, "material_unknown_observed", materialUnknown,
            "disposed_by_native_action", disposed, "disposal_action_id", disposalAction,
            "fault_origin", origin, "active_native_check_id", checkId, "pending_observations", new ArrayList<>(active.values()),
            "retired_observations", new ArrayList<>(retired.values()));
            if(restartRequired)result.putAll(map("restart_material_history",true,"restart_probe_source_id",restartProbeSource,"historical_observation_ids",List.copyOf(historicalObservationIds),"historical_pending_observation_ids",List.copyOf(historicalPendingIds)));
            return immutable(result); }
    }
    private static final class Fixture {
        final Configuration config; final String scenario;
        final Object owner = new Object();
        final Map<ReferenceNozzle,Signal> signals = new IdentityHashMap<>();
        final Set<String> usedInterventions = new HashSet<>();
        VacuumSensing.ControlledSource source;
        SourceIntervention intervention;
        boolean recoveryActive, sensorRepaired;
        Map<String,Object> restartHistory;String restartIdentity,restartHistoryHash;final Set<String> historicalSourceIds=new TreeSet<>();
        long sourceGeneration;
        Fixture(Configuration config,String scenario)throws Exception {this(config,scenario,true);}
        Fixture(Configuration config, String scenario,boolean install) throws Exception {
            this.config = config; this.scenario = scenario;
            Machine machine = config.getMachine(); requireBoundedMachine(config);
            if (machine.getDrivers().size() != 1 || machine.getDrivers().get(0).getClass() != NullDriver.class)
                throw fault("SENSING_PROFILE_UNSUPPORTED", "One exact native simulator driver required");
            for (Head head : machine.getHeads()) for (Nozzle item : head.getNozzles()) signals.put((ReferenceNozzle)item, new Signal());
            if(install)source = installSource(0);
        }
        void initializeRestartHistory()throws Exception {
            Map<String,Signal> byId=new TreeMap<>();for(Map.Entry<ReferenceNozzle,Signal> entry:signals.entrySet()){Signal signal=entry.getValue();signal.materialUnknown=true;signal.restartRequired=true;byId.put(entry.getKey().getId(),signal);}
            Set<String> observations=new HashSet<>();
            for(Object raw:restartHistory.values()){
                Map<String,Object> capture=NativeSensingReconciliation.asObject(raw);
                for(Object binding:NativeSensingReconciliation.list(capture,"nozzle_bindings",64)){
                    Map<String,Object> b=NativeSensingReconciliation.asObject(binding);if(!byId.containsKey(b.get("nozzle_id")))throw fault("SENSING_RESTART_HISTORY_CHANGED","Historical nozzle is absent from the captured native configuration");
                    historicalSourceIds.add(NativeSensingReconciliation.uuid(NativeSensingReconciliation.object(b,"source"),"source_id"));
                }
                for(Object item:NativeSensingReconciliation.list(capture,"faults",1024)){
                    Map<String,Object> row=NativeSensingReconciliation.asObject(item);Object nozzleId=row.get("nozzle_id");if(nozzleId==null)continue;
                    Signal signal=byId.get(nozzleId);if(signal==null)throw fault("SENSING_RESTART_HISTORY_CHANGED","Historical fault nozzle was omitted");
                    String id=row.get("observation_id")==null?null:NativeSensingReconciliation.uuid(row,"observation_id");
                    if(id!=null){if(observations.add(id)&&observations.size()>1024)throw fault("SENSING_RECOVERY_CAPACITY","Historical restart observation capacity exceeded");if(!signal.historicalObservationIds.contains(id))signal.historicalObservationIds.add(id);if("pending".equals(row.get("kind"))&&!signal.historicalPendingIds.contains(id))signal.historicalPendingIds.add(id);}
                    Map<String,Object> payload=NativeSensingReconciliation.object(row,"payload");
                    if(payload.get("data") instanceof Map){Map<String,Object> data=NativeSensingReconciliation.object(payload,"data");Object event=payload.get("native_event");
                        if("check.returned".equals(event)&&Boolean.FALSE.equals(data.get("verdict"))){if("part_off".equals(data.get("check_kind")))signal.retained=true;if("part_on".equals(data.get("check_kind"))&&Set.of("align","before_place").contains(data.get("native_stage")))signal.lost=true;}
                        if("read.failed".equals(event)||"check.failed".equals(event))signal.sensorFault=true;
                        if(signal.origin==null)signal.origin=immutable(map("native_event",event,"nozzle_id",nozzleId,"observation_id",id,"native_stage",data.get("native_stage"),"nozzle_tip_id",data.get("nozzle_tip_id"),"sensor_id",data.get("sensor_id"),"source",data.get("source"),"bridge_context",payload.get("context"),"original_operation_id",row.get("operation_id"),"original_action_id",null,"original_part_id",null));
                    }
                }
            }
            for(Signal signal:signals.values()){Collections.sort(signal.historicalObservationIds);Collections.sort(signal.historicalPendingIds);}
        }
        boolean restartProbeCurrent(Signal signal){return source!=null&&!source.isClosed()&&source.provenance().get("source_id").equals(signal.restartProbeSource)&&signal.disposed;}
        VacuumSensing.ControlledSource installSource(long generation) throws Exception {
            Map<Actuator,VacuumSensing.SampleSource> providers = new IdentityHashMap<>();
            for (Map.Entry<ReferenceNozzle,Signal> entry : signals.entrySet()) {
                Actuator sensor = entry.getKey().getVacuumSenseActuator(); Signal signal = entry.getValue();
                if (sensor == null || providers.put(sensor, index -> read(signal)) != null)
                    throw fault("SENSING_BINDING_REQUIRED", "Each fixture nozzle needs its own native sensor");
            }
            return ((NullDriver)config.getMachine().getDrivers().get(0)).installControlledVacuumSource(owner, providers,
                Map.of("fixture_id", "codex-explicit-vacuum-simulator-v1", "scenario_id", restartIdentity!=null?scenario+":restart79:"+restartIdentity+":"+generation:generation == 0 ? scenario : scenario + ":repair79:" + generation, "units", "native-actuator-units"));
        }
        void requireCurrent() throws Exception {
            if (fixture != this || Configuration.get() != config || !config.getMachine().isTask(Thread.currentThread()))
                throw fault("SENSING_RECOVERY_EXECUTOR_REQUIRED", "The current owned native fixture executor is required");
            requireBoundedMachine(config); requireLifecycleSafe(config);
            if (source == null || source.isClosed() || config.getMachine().getDrivers().size() != 1
                    || config.getMachine().getDrivers().get(0).getClass() != NullDriver.class
                    || ((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource() != source)
                throw fault("SENSING_SOURCE_CHANGED", "The owned source was revoked or replaced");
        }
        synchronized int retiredCount() { int count = 0; for (Signal signal : signals.values()) count += signal.retired.size(); return count; }
        synchronized Set<String> pendingIds() { Set<String> ids = new HashSet<>(); for (Signal signal : signals.values()) ids.addAll(signal.active.keySet()); return Set.copyOf(ids); }
        synchronized Map<String,Map<String,Object>> pendingRecords() { Map<String,Map<String,Object>> records = new TreeMap<>(); for (Signal signal : signals.values()) records.putAll(signal.active); return immutable(records); }
        synchronized Map<String,Object> snapshot() {
            List<Object> rows = new ArrayList<>();
            for (Map.Entry<ReferenceNozzle,Signal> entry : signals.entrySet()) rows.add(immutable(map("nozzle_id", entry.getKey().getId(), "signal", entry.getValue().snapshot())));
            rows.sort(Comparator.comparing(row -> (String)((Map<?,?>)row).get("nozzle_id")));
            Map<String,Object> result=map("available", source != null && !source.isClosed(), "source", source == null ? null : source.provenance(),
                "source_generation", sourceGeneration, "recovery_activated", recoveryActive, "sensor_repaired", sensorRepaired,
                "nozzles", rows, "pending_observation_ids", sorted(pendingIds()), "execution_authority", false, "simulation_only", true, "physical_occupancy_verified", false);
            if(restartHistory!=null){result.put("restart_history_sha256",restartHistoryHash);result.put("historical_task_ids",sorted(restartHistory.keySet()));result.put("restart_reattachment_id",restartIdentity);result.put("restart_reconciliation_required",signals.values().stream().anyMatch(signal->!restartProbeCurrent(signal)));}
            return immutable(result);
        }
        synchronized String read(Signal signal) {
            // Observed retained/lost material survives every stage and source repair.
            // Normal pre-fault S78 scenario execution remains unchanged.
            if (signal.materialUnknown && !signal.disposed) return "NaN";
            if (signal.retained && !signal.disposed && "part_off".equals(signal.kind)) return "70";
            if (signal.lost && !signal.disposed && "part_on".equals(signal.kind)) return "0";
            if (recoveryActive) {
                if (!sensorRepaired && scenario.equals("invalid-read")) return "NaN";
                if ("part_off".equals(signal.kind)) return signal.retained && !signal.disposed ? "70" : "0";
                if (signal.lost && !signal.disposed) return "0";
                return "70";
            }
            if (scenario.equals("invalid-read")) return "NaN";
            if ("part_off".equals(signal.kind)) return scenario.equals("retained-after-place") && "after_place".equals(signal.stage) ? "70" : "0";
            if (scenario.equals("missed-pick-retry") && "after_pick".equals(signal.stage) && signal.afterPick == 1) return "0";
            if (scenario.equals("lost-before-place") && "before_place".equals(signal.stage)) return "0";
            return "70";
        }
        synchronized void observe(String event, ReferenceNozzle nozzle, Map<String,Object> data, Map<String,Object> context) {
            Signal signal = signals.get(nozzle);
            if (signal == null || !config.getMachine().isTask(Thread.currentThread()) || source.isClosed() || !source.provenance().equals(data.get("source"))) return;
            String observation = (String)data.get("observation_id");
            if (event.endsWith(".before")) {
                if (signal.historicalObservationIds.contains(observation) || signal.retired.containsKey(observation) || pendingIds().contains(observation) || pendingIds().size() >= 256)
                    throw new IllegalStateException("Duplicate or excessive actual native source observation");
                signal.active.put(observation, immutable(map("native_event", event, "nozzle_id", nozzle.getId(), "observation_id", observation, "data", data, "bridge_context", context)));
            } else if (event.endsWith(".returned") || event.endsWith(".failed")) {
                Map<String,Object> pending = signal.active.get(observation);
                if (pending == null || !event.substring(0,event.indexOf('.')).equals(((String)pending.get("native_event")).split("\\.")[0])) return;
                signal.active.remove(observation);
            }
            if (event.equals("check.before")) {
                signal.checkId = (String)data.get("observation_id"); signal.kind = (String)data.get("check_kind"); signal.stage = (String)data.get("native_stage");
                if ("after_pick".equals(signal.stage)) signal.afterPick++;
            } else if (event.equals("read.failed")) {
                signal.sensorFault = true;signal.restartProbeSource=null; remember(signal, event, nozzle, data, context);
            } else if ((event.equals("check.returned") || event.equals("check.failed")) && Objects.equals(signal.checkId, data.get("observation_id"))) {
                // A returned exception closes call bookkeeping, not material uncertainty.
                // Native job stages involve pickup, held material or a release; a new
                // healthy sensor cannot turn their failed observation into known empty.
                if (event.equals("check.failed") && signal.stage != null
                        && Set.of("before_pick", "after_pick", "align", "before_place", "after_place").contains(signal.stage)) {
                    signal.materialUnknown = true; signal.disposed = false;
                    remember(signal, event, nozzle, data, context);
                }
                if (event.equals("check.returned") && Boolean.FALSE.equals(data.get("verdict"))) {
                    if ("part_off".equals(signal.kind)) { signal.retained = true; signal.disposed = false; remember(signal, event, nozzle, data, context); }
                    if ("part_on".equals(signal.kind) && "before_place".equals(signal.stage)) { signal.lost = true; signal.disposed = false; remember(signal, event, nozzle, data, context); }
                }
                if(signal.restartRequired&&"check.returned".equals(event)&&"part_off".equals(signal.kind)&&Boolean.TRUE.equals(data.get("verdict"))&&signal.disposed&&recoveryActive&&sensorRepaired)signal.restartProbeSource=(String)source.provenance().get("source_id");
                else if(signal.restartRequired&&(event.equals("check.failed")||Boolean.FALSE.equals(data.get("verdict"))))signal.restartProbeSource=null;
                signal.checkId = null; signal.kind = null; signal.stage = null;
            }
        }
        private void remember(Signal signal, String event, ReferenceNozzle nozzle, Map<String,Object> data, Map<String,Object> context) {
            if (signal.origin == null) signal.origin = immutable(map("native_event", event, "nozzle_id", nozzle.getId(),
                "observation_id", data.get("observation_id"), "native_stage", data.get("native_stage"), "nozzle_tip_id", data.get("nozzle_tip_id"),
                "sensor_id", data.get("sensor_id"), "source", data.get("source"), "bridge_context", context,
                "original_operation_id", context == null ? null : context.get("operation_id"),
                "original_action_id", context == null ? null : context.get("original_action_id"),
                "original_part_id", nozzle.getPart() == null ? null : nozzle.getPart().getId()));
        }
    }

    private static List<String> sorted(Collection<String> values) { List<String> result = new ArrayList<>(values); Collections.sort(result); return List.copyOf(result); }
    private static void canonicalUuid(String value) throws Bridge.Fault {
        try { if (value == null || !UUID.fromString(value).toString().equals(value)) throw new IllegalArgumentException(); }
        catch (IllegalArgumentException invalid) { throw fault("SENSING_RECOVERY_ID_REQUIRED", "A canonical UUID is required"); }
    }
    private static boolean finite(Location value) { return value != null && Double.isFinite(value.getX()) && Double.isFinite(value.getY()) && Double.isFinite(value.getZ()) && Double.isFinite(value.getRotation()); }
    private static boolean at(Location a, Location b) { if (!finite(a) || !finite(b)) return false; Location v = a.convertToUnits(b.getUnits()); return Math.abs(v.getX()-b.getX()) < 1e-6 && Math.abs(v.getY()-b.getY()) < 1e-6 && Math.abs(v.getZ()-b.getZ()) < 1e-6 && Math.abs(v.getRotation()-b.getRotation()) < 1e-6; }
    private static Map<String,Object> location(Location value) { return value == null ? null : immutable(map("units", value.getUnits().toString(), "x", value.getX(), "y", value.getY(), "z", value.getZ(), "rotation", value.getRotation())); }
    @SuppressWarnings("unchecked") private static <T> T immutable(T value) {
        if (value instanceof Map) { Map<String,Object> result = new LinkedHashMap<>(); for (Map.Entry<?,?> e : ((Map<?,?>)value).entrySet()) result.put((String)e.getKey(), immutable(e.getValue())); return (T)Collections.unmodifiableMap(result); }
        if (value instanceof List) { List<Object> result = new ArrayList<>(); for (Object item : (List<?>)value) result.add(immutable(item)); return (T)Collections.unmodifiableList(result); }
        return value;
    }

    private static boolean usesSensing(ReferenceNozzleTip tip) throws Exception {
        return NativeVacuumSensing.readMethod(tip, true) != ReferenceNozzleTip.VacuumMeasurementMethod.None || NativeVacuumSensing.readMethod(tip, false) != ReferenceNozzleTip.VacuumMeasurementMethod.None
            || tip.isEstablishPartOnLevel() || tip.isEstablishPartOffLevel();
    }
    private static ReferenceNozzleTip exactTip(Machine machine, NozzleTip tip) throws Exception {
        if (tip == null || tip.getClass() != ReferenceNozzleTip.class || machine.getNozzleTip(tip.getId()) != tip)
            throw fault("SENSING_IDENTITY_CHANGED", "Native candidate tips must be exact canonical objects");
        return (ReferenceNozzleTip)tip;
    }
    private static final class Identity {
        final Object value;
        Identity(Object value) { this.value = value; }
        public boolean equals(Object other) { return other instanceof Identity && ((Identity)other).value == value; }
        public int hashCode() { return System.identityHashCode(value); }
    }
    private static void object(List<Object> out, Object value) { out.add(new Identity(value)); }
    /** Identity and settings capture excludes evolving native placed flags and installed-tip selection. */
    private static List<Object> capture(Configuration config, Job job, boolean sensingEnabled) throws Exception {
        requireBoundedMachine(config); Machine machine = config.getMachine(); requirePlanner(machine); List<BoardLocation> boards = boundedBoards(job);
        List<Object> out = new ArrayList<>(); if (sensingEnabled) out.addAll(lifecyclePolicies(config)); object(out, ((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).planner); object(out, config); object(out, machine); object(out, machine.getPnpJobProcessor()); object(out, machine.getDefaultHead()); object(out, job);
        if (machine.getHeads().size() > 8 || machine.getNozzleTips().size() > 128) throw fault("SENSING_GRAPH_LIMIT", "Native sensing graph exceeds bounds");
        int nozzles = 0, placements = 0;
        for (Head head : machine.getHeads()) {
            object(out, head); out.add(head.getId());
            for (Nozzle item : head.getNozzles()) {
                if (++nozzles > 32 || item.getClass() != ReferenceNozzle.class) throw fault("SENSING_GRAPH_LIMIT", "At most 32 exact native nozzles are supported");
                ReferenceNozzle n = (ReferenceNozzle)item; object(out, n); out.add(n.getId()); object(out, n.getHead()); object(out, n.getVacuumSenseActuator()); object(out, n.getVacuumActuator());
                for (NozzleTip tip : n.getCompatibleNozzleTips()) object(out, tip);
            }
        }
        for (NozzleTip item : machine.getNozzleTips()) {
            ReferenceNozzleTip t = exactTip(machine, item); object(out, t); out.add(t.getId());
            Collections.addAll(out, NativeVacuumSensing.readMethod(t, true), NativeVacuumSensing.readMethod(t, false), t.getVacuumLevelPartOnLow(), t.getVacuumLevelPartOnHigh(), t.getVacuumLevelPartOffLow(), t.getVacuumLevelPartOffHigh(),
                t.isPartOnCheckAfterPick(), t.isPartOnCheckAlign(), t.isPartOnCheckBeforePlace(), t.isPartOffCheckBeforePick(), t.isPartOffCheckAfterPlace(),
                t.isEstablishPartOnLevel(), t.isEstablishPartOffLevel(), t.getPartOffProbingMilliseconds(), t.getPartOffDwellMilliseconds(), t.getPickDwellMilliseconds(), t.getPlaceDwellMilliseconds());
        }
        for (BoardLocation board : boards) {
            object(out, board); object(out, board.getBoard()); Collections.addAll(out, board.isEnabled(), board.getGlobalSide());
            for (Placement p : board.getBoard().getPlacements()) {
                if (++placements > 20000) throw fault("SENSING_GRAPH_LIMIT", "At most 20000 native placements are supported");
                object(out, p); Collections.addAll(out, p.getId(), p.getType(), p.getSide(), p.isEnabled()); object(out, p.getPart());
                if (p.getPart() != null) {
                    object(out, p.getPart().getPackage());
                    if (p.getPart().getPackage() != null) { requireBoundedCompatibility(p.getPart().getPackage()); for (NozzleTip tip : p.getPart().getPackage().getCompatibleNozzleTips()) object(out, tip); }
                }
            }
        }
        return out;
    }
    /** Bound native inventories before helpers such as getAllActuators materialize them. */
    static void requireBoundedMachine(Configuration config) throws Exception {
        if (config == null || Configuration.get() != config || config.getMachine() == null || config.getMachine().getClass() != ReferenceMachine.class)
            throw fault("SENSING_CONFIGURATION_CHANGED", "Current exact native machine required");
        Machine m = config.getMachine();
        if (m.getHeads().size() > 8 || m.getNozzleTips().size() > 128 || m.getAxes().size() > 16 || m.getDrivers().size() > 8 || m.getFeeders().size() > 4096
                || m.getActuators().size() > 64 || m.getCameras().size() > 64)
            throw fault("SENSING_GRAPH_LIMIT", "Native sensing inventory exceeds bounded counts");
        long nozzles = 0, actuators = m.getActuators().size(), cameras = m.getCameras().size();
        for (Head head : m.getHeads()) {
            if (head == null || head.getClass() != ReferenceHead.class) throw fault("SENSING_PROFILE_UNSUPPORTED", "Exact native heads required");
            nozzles += head.getNozzles().size(); actuators += head.getActuators().size(); cameras += head.getCameras().size();
            if (nozzles > 32 || actuators > 64 || cameras > 64) throw fault("SENSING_GRAPH_LIMIT", "Native head inventories exceed bounded counts");
            for (Nozzle nozzle : head.getNozzles()) {
                if (nozzle == null || nozzle.getClass() != ReferenceNozzle.class) throw fault("SENSING_PROFILE_UNSUPPORTED", "Exact native nozzles required");
                requireBoundedCompatibility(nozzle);
            }
        }
    }
    /** Unknown native methods are sensing-relevant; this predicate never normalizes them. */
    static boolean hasConfiguredSensing(Configuration config) throws Exception {
        requireBoundedMachine(config); Set<NozzleTip> tips = Collections.newSetFromMap(new IdentityHashMap<NozzleTip,Boolean>());
        tips.addAll(config.getMachine().getNozzleTips());
        for (Head head : config.getMachine().getHeads()) for (Nozzle nozzle : head.getNozzles()) {
            if (nozzle.getNozzleTip() != null) tips.add(nozzle.getNozzleTip());
            tips.addAll(nozzle.getCompatibleNozzleTips());
        }
        for (NozzleTip item : tips) {
            if (item == null || item.getClass() != ReferenceNozzleTip.class) return true;
            ReferenceNozzleTip tip = (ReferenceNozzleTip)item;
            if (NativeVacuumSettings.readMethod(tip, true) != ReferenceNozzleTip.VacuumMeasurementMethod.None
                    || NativeVacuumSettings.readMethod(tip, false) != ReferenceNozzleTip.VacuumMeasurementMethod.None
                    || tip.isEstablishPartOnLevel() || tip.isEstablishPartOffLevel()) return true;
        }
        return false;
    }
    private static Map<String,Object> describeLifecyclePolicies(Configuration config) throws Exception {
        requireLifecycleSafe(config); List<Object> actuators = new ArrayList<>();
        for (Actuator item : config.getMachine().getAllActuators()) {
            ReferenceActuator a = (ReferenceActuator)item;
            actuators.add(Collections.unmodifiableMap(map("actuator_id", a.getId(), "enabled_actuation", a.getEnabledActuation().name(),
                "homed_actuation", a.getHomedActuation().name(), "disabled_actuation", a.getDisabledActuation().name())));
        }
        return Collections.unmodifiableMap(map("home_after_enabled", ((ReferenceMachine)config.getMachine()).getHomeAfterEnabled(),
            "automatic_actuation", false, "assumptions_are_sensor_evidence", false, "actuators", List.copyOf(actuators)));
    }
    /** Sensing-qualified lifecycle actions must not queue hidden motion or actuator effects. */
    static void requireLifecycleSafe(Configuration config) throws Exception { lifecyclePolicies(config); }
    static List<Object> lifecyclePolicies(Configuration config) throws Exception {
        requireBoundedMachine(config); ReferenceMachine machine = (ReferenceMachine)config.getMachine();
        if (machine.getHomeAfterEnabled()) throw fault("SENSING_AUTOMATION_UNSUPPORTED", "Automatic homing after enable is outside the sensing profile");
        List<Object> values = new ArrayList<>(); values.add(machine.getHomeAfterEnabled());
        for (Actuator actuator : machine.getAllActuators()) {
            if (actuator == null || actuator.getClass() != ReferenceActuator.class)
                throw fault("SENSING_AUTOMATION_UNSUPPORTED", "Lifecycle policies require exact native ReferenceActuators");
            ReferenceActuator a = (ReferenceActuator)actuator; object(values, a);
            for (ReferenceActuator.MachineStateActuation policy : Arrays.asList(a.getEnabledActuation(), a.getHomedActuation(), a.getDisabledActuation())) {
                if (policy == null || policy == ReferenceActuator.MachineStateActuation.ActuateOn || policy == ReferenceActuator.MachineStateActuation.ActuateOff)
                    throw fault("SENSING_AUTOMATION_UNSUPPORTED", "Automatic actuator effects and unknown lifecycle policies are outside the sensing profile");
                // Assume policies only change native cached state; they never attest a sensor reading.
                values.add(policy);
            }
        }
        return List.copyOf(values);
    }
    private static void requirePlanner(Machine machine) throws Exception {
        if (machine.getPnpJobProcessor().getClass() != ReferencePnpJobProcessor.class
                || ((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).planner == null
                || ((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).planner.getClass() != ReferencePnpJobProcessor.SimplePnpJobPlanner.class)
            throw fault("SENSING_PLANNER_UNSUPPORTED", "The tested exact native SimplePnpJobPlanner is required");
    }
    /** Fixed pinned fields are checked before lazy native compatibility getters can resolve IDs. */
    private static void requireBoundedCompatibility(Object object) throws Exception {
        Class<?> type = object instanceof ReferenceNozzle ? AbstractNozzle.class : org.openpnp.model.Package.class;
        if (object.getClass() != ReferenceNozzle.class && object.getClass() != org.openpnp.model.Package.class)
            throw fault("SENSING_PROFILE_UNSUPPORTED", "Exact native compatibility owner required");
        for (String name : List.of("compatibleNozzleTipIds", "compatibleNozzleTips")) {
            Field field = type.getDeclaredField(name); field.setAccessible(true); Object value = field.get(object);
            if (value != null && (!(value instanceof Collection) || ((Collection<?>)value).size() > 128))
                throw fault("SENSING_GRAPH_LIMIT", "Native compatibility inventory exceeds 128 references");
        }
    }
    /** Iterative tree traversal avoids the native recursive flattened-list helper before limits. */
    private static List<BoardLocation> boundedBoards(Job job) throws Exception {
        if (job == null) return List.of();
        if (job.getClass() != Job.class || job.getRootPanelLocation() == null || job.getRootPanelLocation().getParent() != null) throw fault("SENSING_JOB_INVALID", "Exact rooted native job required");
        List<BoardLocation> result = new ArrayList<>();
        Deque<PlacementsHolderLocation<?>> locations = new ArrayDeque<>(); Deque<Integer> depths = new ArrayDeque<>();
        Set<PlacementsHolderLocation<?>> seen = Collections.newSetFromMap(new IdentityHashMap<PlacementsHolderLocation<?>,Boolean>());
        locations.push(job.getRootPanelLocation()); depths.push(0); int placements = 0;
        while (!locations.isEmpty()) {
            PlacementsHolderLocation<?> location = locations.pop(); int depth = depths.pop();
            if (depth > 32 || !seen.add(location) || seen.size() > 4096) throw fault("SENSING_GRAPH_LIMIT", "Native job hierarchy exceeds bounded depth or membership");
            if (location.getClass() == BoardLocation.class) {
                BoardLocation board = (BoardLocation)location;
                if (board.getBoard() == null || board.getBoard().getClass() != Board.class) throw fault("SENSING_JOB_INVALID", "Exact native board required");
                int size = board.getBoard().getPlacements().size();
                if (size > 20000 - placements) throw fault("SENSING_GRAPH_LIMIT", "At most 20000 expanded native placements are supported");
                placements += size; result.add(board);
            } else if (location.getClass() == PanelLocation.class) {
                PanelLocation panel = (PanelLocation)location;
                if (panel.getPanel() == null || panel.getPanel().getClass() != Panel.class) throw fault("SENSING_JOB_INVALID", "Exact native panel required");
                List<PlacementsHolderLocation<?>> children = panel.getPanel().getChildren();
                if (children.size() > 4096 - seen.size() - locations.size()) throw fault("SENSING_GRAPH_LIMIT", "Native job child inventory exceeds bound");
                for (int i=children.size()-1;i>=0;i--) {
                    PlacementsHolderLocation<?> child = children.get(i);
                    if (child == null || child.getParent() != panel) throw fault("SENSING_JOB_INVALID", "Native job parent identity differs");
                    locations.push(child); depths.push(depth+1);
                }
            } else throw fault("SENSING_JOB_INVALID", "Unsupported native job location");
        }
        return result;
    }
    private static Map<String,Object> map(Object... values) { Map<String,Object> out = new LinkedHashMap<>(); for (int i=0;i<values.length;i+=2) out.put((String)values[i], values[i+1]); return out; }
    private static Bridge.Fault fault(String code, String message) { return new Bridge.Fault(code, message); }
}
