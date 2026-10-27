/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.*;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.openpnp.machine.reference.ReferenceNozzle;

/** Ordered recorded sensing facts. Neither replay nor a null native Part grants empty-nozzle authority. */
final class NativeVacuumJournal {
    static final String PROFILE="native-vacuum-observation-v1";
    static final int MAX_OBSERVATIONS=200000,MAX_NOZZLES=64,MAX_ACTIVE=256,MAX_RECORD_BYTES=16384;
    private static final Gson JSON=new GsonBuilder().serializeNulls().create();
    private static final Set<String> EVENTS=Set.of("read.before","read.returned","read.failed","check.before","check.returned","check.failed","valve.before","valve.returned","valve.failed");
    private static final Set<String> STAGES=Set.of("after_pick","align","before_place","before_pick","after_place","direct");
    private static final List<String> COMMON=List.of("api_version","observation_id","parent_observation_id","native_stage","check_kind","nozzle_tip_id","sensor_id","source");
    @FunctionalInterface interface Sink {void append(String type,Map<String,Object> payload)throws Exception;}
    static final class Fence extends Error {
        private static final long serialVersionUID=1L;
        final String code;
        Fence(String code,String message,Throwable cause){super(message,cause);this.code=code;}
    }
    static final class Fault extends Exception {
        private static final long serialVersionUID=1L;
        final String code;
        Fault(String code,String message){super(message);this.code=code;}
    }
    private static final class Active {
        final Map<String,Object> payload,context,data;
        final String kind,nozzle;
        int reads;
        boolean valveOn,valveOff,childFailed;
        Active(Map<String,Object> p)throws IOException {payload=p;context=object(p,"context");data=object(p,"data");kind=text(p,"native_event",32).split("\\.")[0];nozzle=text(context,"nozzle_id",128);}
        Active(Active a){payload=a.payload;context=a.context;data=a.data;kind=a.kind;nozzle=a.nozzle;reads=a.reads;valveOn=a.valveOn;valveOff=a.valveOff;childFailed=a.childFailed;}
    }
    private static final class NozzleState {
        String occupancy="unobserved",lastObservation,lastInstance,reason;
        boolean sticky,emptyLive;
        long observations,returned,failed;
        Map<String,Object> last,emptyBinding,lastSuccessfulCheckBinding;
        NozzleState copy(){NozzleState n=new NozzleState();n.occupancy=occupancy;n.lastObservation=lastObservation;n.lastInstance=lastInstance;n.reason=reason;n.sticky=sticky;n.emptyLive=emptyLive;n.observations=observations;n.returned=returned;n.failed=failed;n.last=last;n.emptyBinding=emptyBinding;n.lastSuccessfulCheckBinding=lastSuccessfulCheckBinding;return n;}
    }
    private final Map<String,Active> active=new LinkedHashMap<>();
    private final Set<String> seen=new HashSet<>();
    private final Map<String,NozzleState> nozzles=new LinkedHashMap<>();
    private String machineId;
    private long generation;
    private boolean recovered,lifecycleFault;
    private final Set<String> lifecycleOperations=new HashSet<>();
    private final Map<String,Map<String,Object>> faultRecords=new LinkedHashMap<>(),completedRecords=new LinkedHashMap<>(),lifecycleRecords=new LinkedHashMap<>();
    private final Set<String> publicationFaults=new HashSet<>(),liveSuperseded=new HashSet<>();
    private final Map<String,String> historicalSuperseded=new LinkedHashMap<>();
    private final Map<String,Long> resolutionGenerations=new HashMap<>();
    private final Map<String,String> resolutionInstances=new HashMap<>();
    private long dispositionGeneration;
    private NativeSensingReconciliation reconciliation;
    synchronized void attachReconciliation(NativeSensingReconciliation value){if(reconciliation!=null)throw new IllegalStateException("Reconciliation already attached");reconciliation=Objects.requireNonNull(value);}


    static boolean matches(String type){return type!=null&&type.startsWith("vacuum_");}

    /** The caller serializes append/force/commit. A stale or reused commit is rejected. */
    synchronized Runnable prepare(String type,Map<String,Object> payload,String envelopeInstance)throws IOException {return prepare(type,payload,envelopeInstance,false);}
    private Runnable prepare(String type,Map<String,Object> payload,String envelopeInstance,boolean replay)throws IOException {
        if(!matches(type))return ()->{};
        if("vacuum_readiness_invalidated".equals(type))return prepareReadiness(payload,envelopeInstance);
        if("vacuum_lifecycle_uncertain".equals(type))return prepareLifecycle(payload,envelopeInstance);
        if(!Set.of("vacuum_observation_intent","vacuum_observation_outcome").contains(type))throw bad("Unknown event type");
        Map<String,Object> p=bounded(payload);keys(p,"schema_version","profile","context","native_event","data");
        integer(p,"schema_version",1,1);if(!PROFILE.equals(p.get("profile")))throw bad("Unknown profile");
        Map<String,Object> c=object(p,"context");validateContext(c);String instance=uuid(c,"bridge_instance_id"),machine=uuid(c,"machine_id");
        if(!instance.equals(uuidValue(envelopeInstance))||machineId!=null&&!machineId.equals(machine))throw bad("Foreign envelope or machine identity");
        String event=text(p,"native_event",32);if(!EVENTS.contains(event))throw bad("Unknown native event");
        boolean before=event.endsWith(".before"),failed=event.endsWith(".failed");
        if(before!=type.equals("vacuum_observation_intent"))throw bad("Event type/phase disagreement");
        Map<String,Object> d=object(p,"data");validateData(event,d);
        boolean recovery="recovery".equals(c.get("scope"));
        if(recovery){if(reconciliation==null)throw bad("Recovery coordinator unavailable");reconciliation.validateObservation(c,d,event,replay);}
        if(hasLifecycleFault(instance,replay)&&!cleanup(event,d)&&!recovery)throw bad("Machine lifecycle retains unresolved sensing evidence");
        String id=uuid(d,"observation_id"),nozzle=text(c,"nozzle_id",128),kind=event.split("\\.")[0];
        NozzleState prior=nozzles.get(nozzle),next=prior==null?new NozzleState():prior.copy();
        if(historicalSuperseded.containsKey(id))throw bad("Late outcome cannot rewrite a superseded observation");
        Map<String,Object> currentBinding=binding(c,d);
        if("observed_empty".equals(next.occupancy)&&!currentBinding.equals(next.emptyBinding)){next.occupancy="unobserved";next.emptyLive=false;next.reason="empty-binding-changed";}
        Active pending=active.get(id),admitted,parentUpdate=null;
        if(before){
            if(seen.contains(id)||seen.size()>=MAX_OBSERVATIONS||active.size()>=MAX_ACTIVE||prior==null&&nozzles.size()>=MAX_NOZZLES)throw bad("Duplicate observation or retained limit");
            validateParent(c,d,kind,replay);
            // A failed source/check cannot be retried under a new UUID. Mandatory valve-off is the sole exception.
            if(hasStickyFault(nozzle,instance,replay)&&!cleanup(event,d)&&!recovery)throw bad("Nozzle retains unresolved or retained-material evidence");
            admitted=new Active(p);next.observations++;
        }else{
            if(pending==null||!pending.kind.equals(kind)||!pending.context.equals(c))throw bad("Outcome lacks its exact intent/context");
            for(String key:COMMON)if(!Objects.equals(pending.data.get(key),d.get(key)))throw bad("Native outcome identity changed: "+key);
            if(kind.equals("valve"))for(String key:List.of("enabled","reason","cleanup_attempt"))if(!Objects.equals(pending.data.get(key),d.get(key)))throw bad("Valve outcome differs");
            for(Active child:active.values())if(id.equals(child.data.get("parent_observation_id")))throw bad("Parent completed before child outcome");
            if(!failed&&kind.equals("check")&&(pending.reads<1||pending.childFailed||"part_off".equals(d.get("check_kind"))&&(!pending.valveOn||!pending.valveOff)))throw bad("Successful check lacks completed native read/probe lifecycle");
            Object parent=d.get("parent_observation_id");
            if(parent!=null){parentUpdate=new Active(active.get(parent));if(failed)parentUpdate.childFailed=true;
                else if(kind.equals("read"))parentUpdate.reads++;
                else if(kind.equals("valve")){if(Boolean.TRUE.equals(d.get("enabled")))parentUpdate.valveOn=true;else parentUpdate.valveOff=true;}}
            admitted=null;
            if(failed){next.failed++;next.sticky=true;next.emptyLive=false;next.occupancy="unknown";next.reason="native-observation-failed";}
            else {
                next.returned++;
                if(kind.equals("check")&&!hasStickyFault(nozzle,instance,replay)&&!recovery){
                    boolean verdict=(Boolean)d.get("verdict");
                    if(verdict)next.lastSuccessfulCheckBinding=currentBinding;
                    if("part_on".equals(d.get("check_kind")))next.occupancy=verdict?"held":"not_detected";
                    else if(verdict){next.occupancy="observed_empty";next.emptyBinding=currentBinding;next.emptyLive=!replay;next.reason=null;}
                    else {next.occupancy="retained";next.emptyLive=false;next.sticky=true;next.reason="part-off-false";}
                }
            }
        }
        next.lastObservation=id;next.lastInstance=instance;next.last=p;
        final long expected=generation;final Active add=admitted,parentNext=parentUpdate;
        return new Runnable(){boolean used;public void run(){synchronized(NativeVacuumJournal.this){
            if(used||generation!=expected)throw new IllegalStateException("Vacuum journal stale or reused commit");
            used=true;machineId=machine;if(before){seen.add(id);active.put(id,add);}else active.remove(id);
            if(parentNext!=null)active.put((String)d.get("parent_observation_id"),parentNext);
            if(!before){completedRecords.put(id,p);if(failed||kind.equals("check")&&"part_off".equals(d.get("check_kind"))&&Boolean.FALSE.equals(d.get("verdict")))faultRecords.put(id,p);}
            nozzles.put(nozzle,next);generation++;
        }}};
    }

    /** A potentially effectful or unverifiable native machine-state policy never grants clearance. */
    private Runnable prepareLifecycle(Map<String,Object> payload,String envelopeInstance)throws IOException {
        Map<String,Object> p=bounded(payload);keys(p,"schema_version","profile","machine_id","bridge_instance_id","config_revision","operation_id","request_id","transition","reason");
        integer(p,"schema_version",1,1);if(!PROFILE.equals(p.get("profile")))throw bad("Unknown lifecycle profile");
        String machine=uuid(p,"machine_id"),instance=uuid(p,"bridge_instance_id"),operation=uuid(p,"operation_id");String request=requestText(p);revision(p,"config_revision","cfg-");
        if(operation.equals(request)||!instance.equals(uuidValue(envelopeInstance))||machineId!=null&&!machineId.equals(machine))throw bad("Foreign lifecycle identity");
        if(!"disable".equals(p.get("transition"))||!"machine-state-policy-unsafe".equals(p.get("reason")))throw bad("Unknown lifecycle transition");
        if(lifecycleOperations.contains(operation)||lifecycleOperations.size()>=MAX_OBSERVATIONS)throw bad("Repeated lifecycle operation or retained limit");
        final long expected=generation;
        return new Runnable(){boolean used;public void run(){synchronized(NativeVacuumJournal.this){
            if(used||expected!=generation)throw new IllegalStateException("Stale lifecycle commit");used=true;machineId=machine;lifecycleOperations.add(operation);lifecycleRecords.put(operation,p);lifecycleFault=true;
            for(NozzleState state:nozzles.values()){state.emptyBinding=null;state.emptyLive=false;if(!state.sticky){state.sticky=true;state.occupancy="unknown";state.reason="machine-state-policy-unsafe";}}
            generation++;
        }}};
    }
    /** Durable loss of empty-evidence freshness, never a clearance or a new unknown verdict. */
    private Runnable prepareReadiness(Map<String,Object> payload,String envelopeInstance)throws IOException {
        Map<String,Object> p=bounded(payload);keys(p,"schema_version","profile","machine_id","bridge_instance_id","config_revision","reason");
        integer(p,"schema_version",1,1);if(!PROFILE.equals(p.get("profile")))throw bad("Unknown readiness profile");
        String machine=uuid(p,"machine_id"),instance=uuid(p,"bridge_instance_id");revision(p,"config_revision","cfg-");
        if(!instance.equals(uuidValue(envelopeInstance))||machineId!=null&&!machineId.equals(machine))throw bad("Foreign readiness identity");
        String reason=text(p,"reason",64);if(!Set.of("native-action","configuration-change","ownership-change","manual-effect").contains(reason))throw bad("Unknown readiness invalidation");
        final long expected=generation;
        return new Runnable(){boolean used;public void run(){synchronized(NativeVacuumJournal.this){
            if(used||expected!=generation)throw new IllegalStateException("Stale readiness commit");used=true;machineId=machine;
            for(NozzleState state:nozzles.values())if("observed_empty".equals(state.occupancy)){state.occupancy="unobserved";state.emptyBinding=null;state.emptyLive=false;state.reason=reason;}
            generation++;
        }}};
    }
    synchronized boolean hasHistory(){return lifecycleFault||!nozzles.isEmpty();}

    void recover(String type,Map<String,Object> payload,String envelopeInstance)throws IOException {
        prepare(type,payload,envelopeInstance,true).run();if(matches(type))synchronized(this){recovered=true;}
    }
    synchronized void verifyMachineIdentity(String id)throws IOException {uuidValue(id);if(machineId!=null&&!machineId.equals(id))throw bad("Durable machine identity differs");}

    /** Sink must call Bridge.event, which owns prepare/force/commit. Never double-apply here. */
    void observe(String event,ReferenceNozzle nozzle,Map<String,Object> data,Map<String,Object> context,Sink sink) {
        observe(event,nozzle==null?null:nozzle.getId(),data,context,sink);
    }
    void observe(String event,String nozzleId,Map<String,Object> data,Map<String,Object> context,Sink sink) {
        try {
            if(!Objects.equals(nozzleId,context.get("nozzle_id")))throw bad("Observer nozzle differs from bound context");
            Map<String,Object> p=bounded(map("schema_version",1,"profile",PROFILE,"context",context,"native_event",event,"data",data));
            if(!EVENTS.contains(event))throw bad("Unknown native event");
            String type=event.endsWith(".before")?"vacuum_observation_intent":"vacuum_observation_outcome";
            // Prevalidate independently of the sink; publication still belongs solely to Bridge.event.
            prepare(type,p,text(context,"bridge_instance_id",36));
            sink.append(type,p);
        } catch(Throwable failure){invalidate(nozzleId,"journal-publication-failed");throw new Fence("VACUUM_DURABILITY_UNKNOWN","Native sensing publication failed; retry is not authorized",failure);}
        // Only this post-commit fence bypasses publication-failure invalidation; sink-thrown Errors never do.
        if(event.endsWith(".failed"))throw new Fence("VACUUM_OUTCOME_UNKNOWN","Native sensing failed; retry is not authorized",null);
    }

    /** Conservative process-local overlay for failed publication. It never replaces durable evidence. */
    synchronized void invalidate(String nozzleId,String reason) {
        if(nozzleId==null||nozzleId.isBlank()||nozzleId.length()>128)return;
        NozzleState state=nozzles.get(nozzleId);if(state==null){if(nozzles.size()>=MAX_NOZZLES)return;state=new NozzleState();nozzles.put(nozzleId,state);}
        state.sticky=true;state.emptyLive=false;state.occupancy="unknown";state.reason="publication-or-native-fault";publicationFaults.add(nozzleId);generation++;
    }
    synchronized void requireNoFault()throws Fault {if(hasLifecycleFault())throw new Fault("VACUUM_OUTCOME_UNKNOWN","Native machine-state policy retains unresolved sensing evidence");for(String id:nozzles.keySet())requireNoFault(id);}
    synchronized void requireNoFault(String nozzleId)throws Fault {
        NozzleState state=nozzles.get(nozzleId);
        if(hasLifecycleFault()||hasStickyFault(nozzleId)||hasPending(nozzleId))throw new Fault("VACUUM_OUTCOME_UNKNOWN","Nozzle "+nozzleId+" retains unresolved or retained-material sensing evidence");
    }
    /** Global legacy guard visits known nozzles but cannot grant empty authority without current bindings. */
    synchronized void requireUnoccupied()throws Fault {requireNoFault();for(String id:nozzles.keySet())requireUnoccupied(id); }
    synchronized void requireUnoccupied(String nozzleId)throws Fault {
        requireNoFault(nozzleId);throw new Fault("VACUUM_CURRENT_BINDING_REQUIRED","Current native sensing binding is required for empty admission: "+nozzleId);
    }
    synchronized void requireUnoccupied(String nozzleId,Map<String,Object> suppliedBinding)throws Fault {
        requireNoFault(nozzleId);NozzleState state=nozzles.get(nozzleId);Map<String,Object> current;
        try{current=bounded(suppliedBinding);validateBinding(current);if(!nozzleId.equals(current.get("nozzle_id")))throw bad("Current nozzle binding differs");}
        catch(IOException invalid){throw new Fault("VACUUM_CURRENT_BINDING_REQUIRED","Invalid current native sensing binding");}
        if(state==null||!state.emptyLive||!"observed_empty".equals(state.occupancy))throw new Fault("NOZZLE_OCCUPANCY_UNRESOLVED","Nozzle "+nozzleId+" lacks current successful part-off evidence");
        if(!current.equals(state.emptyBinding)){state.occupancy="unobserved";state.emptyLive=false;state.reason="empty-binding-changed";generation++;throw new Fault("VACUUM_BINDING_CHANGED","Empty observation has a different native sensing binding");}
    }
    private boolean hasPending(String nozzle){for(Active entry:active.values())if(entry.nozzle.equals(nozzle)&&!liveSuperseded.contains(entry.data.get("observation_id")))return true;return false;}
    synchronized Map<String,Object> snapshot(){return snapshot(null);}
    synchronized Map<String,Object> snapshot(String currentInstance){
        List<Object> rows=new ArrayList<>();
        for(Map.Entry<String,NozzleState> entry:nozzles.entrySet()){
            NozzleState n=entry.getValue();boolean pending=hasPending(entry.getKey());
            rows.add(map("nozzle_id",entry.getKey(),"state",pending?"unknown":n.occupancy,"recorded_state",n.occupancy,"sticky_fault",hasStickyFault(entry.getKey())||pending,"recorded_sticky_fault",n.sticky,
                "reason",pending?"intent-without-outcome":n.reason,"observation_count",n.observations,"returned_count",n.returned,"failed_count",n.failed,
                "last_observation_id",n.lastObservation,"last_bridge_instance_id",n.lastInstance,
                "empty_observation_binding",n.emptyBinding,"last_successful_check_binding",n.lastSuccessfulCheckBinding,
                "historical",recovered||currentInstance!=null&&!Objects.equals(currentInstance,n.lastInstance),"last_record",n.last));
        }
        List<Object> unresolved=new ArrayList<>();for(Active a:active.values())unresolved.add(map("observation_id",a.data.get("observation_id"),"nozzle_id",a.nozzle,"operation_id",a.context.get("operation_id"),"kind",a.kind,"superseded_for_future_readiness_by",historicalSuperseded.get(a.data.get("observation_id"))));
        try{return bounded(map("profile",PROFILE,"observations",seen.size(),"observation_limit",MAX_OBSERVATIONS,"nozzle_limit",MAX_NOZZLES,"pending",unresolved,"nozzles",rows,
            "lifecycle_fault",hasLifecycleFault(),"recorded_lifecycle_fault",lifecycleFault,"disposition_generation",dispositionGeneration,"recovered_history",recovered,"execution_authority_restored",false,"physical_occupancy_verified",false,"hardware_qualified",false,"reconciliation_supported",false),1024*1024);}catch(IOException impossible){throw new IllegalStateException(impossible);}
    }


    private boolean superseded(String id,String instance,boolean replay){return liveSuperseded.contains(id)||replay&&Objects.equals(instance,resolutionInstances.get(historicalSuperseded.get(id)));}
    private boolean hasLifecycleFault(){return hasLifecycleFault(null,false);}
    private boolean hasLifecycleFault(String instance,boolean replay){for(String op:lifecycleOperations)if(!superseded(op,instance,replay))return true;return false;}
    private boolean hasStickyFault(String nozzle){return hasStickyFault(nozzle,null,false);}
    private boolean hasStickyFault(String nozzle,String instance,boolean replay){
        if(publicationFaults.contains(nozzle))return true;
        for(Map.Entry<String,Map<String,Object>> e:faultRecords.entrySet())try{if(nozzle.equals(object(e.getValue(),"context").get("nozzle_id"))&&!superseded(e.getKey(),instance,replay))return true;}catch(IOException impossible){throw new IllegalStateException(impossible);}
        return false;
    }
    /** Complete current failure set; remote callers cannot select or omit fault rows. */
    synchronized NativeSensingReconciliation.Capture captureFaultSet(Map<String,Object> suppliedScope)throws IOException {
        Map<String,Object> scope=NativeSensingReconciliation.freeze(suppliedScope);
        NativeSensingReconciliation.keys(scope,"machine_id","bridge_instance_id","config_revision","native_graph_sha256","nozzle_bindings","job_context","dependencies");
        uuid(scope,"machine_id");uuid(scope,"bridge_instance_id");revision(scope,"config_revision","cfg-");NativeSensingReconciliation.hash(scope,"native_graph_sha256");
        if(machineId!=null&&!machineId.equals(scope.get("machine_id")))throw bad("Fault scope machine differs");
        Map<String,Map<String,Object>> bindings=NativeSensingReconciliation.bindingByNozzle(NativeSensingReconciliation.list(scope,"nozzle_bindings",MAX_NOZZLES));
        for(Map<String,Object>b:bindings.values())for(String k:List.of("machine_id","bridge_instance_id","config_revision"))if(!Objects.equals(scope.get(k),b.get(k)))throw bad("Fault scope binding differs");
        NativeSensingReconciliation.validateDependencies(object(scope,"dependencies"));validateJobScope(scope.get("job_context"));
        Map<String,Object> result=new LinkedHashMap<>(scope);result.put("nozzle_bindings",new ArrayList<>(bindings.values()));Map<String,Object> orderedDependencies=new LinkedHashMap<>();for(Map.Entry<String,Object> e:object(scope,"dependencies").entrySet()){List<String> ordered=new ArrayList<>();for(Object id:(List<?>)e.getValue())ordered.add((String)id);Collections.sort(ordered);orderedDependencies.put(e.getKey(),ordered);}result.put("dependencies",orderedDependencies);result.put("schema_version",1);result.put("prior_disposition_generation",dispositionGeneration);result.put("faults",faultRows(null,(String)scope.get("bridge_instance_id")));
        if(((List<?>)result.get("faults")).isEmpty())throw bad("No recorded sensing fault to reconcile");validateFaultScope(result,bindings);return new NativeSensingReconciliation.Capture(result);
    }
    synchronized void validateCapture(Map<String,Object> capture,String recoveryOperation)throws IOException {
        NativeSensingReconciliation.keys(capture,"schema_version","machine_id","bridge_instance_id","config_revision","native_graph_sha256","nozzle_bindings","job_context","dependencies","prior_disposition_generation","faults");
        integer(capture,"schema_version",1,1);if(integer(capture,"prior_disposition_generation",0,9007199254740991L)!=dispositionGeneration)throw bad("Fault disposition generation changed");
        if(!publicationFaults.isEmpty())throw bad("Unpublished sensing fault requires journal diagnosis, not live reconciliation");
        List<Object> actual=faultRows(recoveryOperation,(String)capture.get("bridge_instance_id"));if(!actual.equals(capture.get("faults")))throw bad("Complete fault set changed or was selectively omitted");
        Map<String,Object> scope=new LinkedHashMap<>(capture);scope.remove("schema_version");scope.remove("prior_disposition_generation");scope.remove("faults");
        uuid(scope,"machine_id");uuid(scope,"bridge_instance_id");revision(scope,"config_revision","cfg-");NativeSensingReconciliation.hash(scope,"native_graph_sha256");if(machineId!=null&&!machineId.equals(scope.get("machine_id")))throw bad("Foreign fault machine");
        Map<String,Map<String,Object>> bindings=NativeSensingReconciliation.bindingByNozzle(NativeSensingReconciliation.list(scope,"nozzle_bindings",MAX_NOZZLES));
        for(Map<String,Object>b:bindings.values())for(String k:List.of("machine_id","bridge_instance_id","config_revision"))if(!Objects.equals(scope.get(k),b.get(k)))throw bad("Capture binding changed");
        NativeSensingReconciliation.validateDependencies(object(scope,"dependencies"));validateJobScope(scope.get("job_context"));validateFaultScope(capture,bindings);
    }
    private void validateFaultScope(Map<String,Object> capture,Map<String,Map<String,Object>> bindings)throws IOException {
        List<?> rows=NativeSensingReconciliation.list(capture,"faults",1024);if(rows.isEmpty())throw bad("Empty fault set");
        for(Object item:rows){Map<String,Object> row=NativeSensingReconciliation.asObject(item);Object nozzle=row.get("nozzle_id");if(nozzle!=null&&!bindings.containsKey(nozzle))throw bad("Fault nozzle omitted from recovery scope");
            Map<String,Object> record=object(row,"payload");if(record.containsKey("context")){Map<String,Object> c=object(record,"context");if(!capture.get("machine_id").equals(c.get("machine_id")))throw bad("Original fault machine differs");
                if(c.get("job_context")!=null&&!Objects.equals(c.get("job_context"),capture.get("job_context")))throw bad("Original job dependencies omitted or changed");
                if(c.get("job_context")!=null&&!NativeSensingReconciliation.list(object(capture,"dependencies"),"operation_ids",256).contains(c.get("operation_id")))throw bad("Original job operation omitted");}
        }
    }
    private static void validateJobScope(Object value)throws IOException {
        if(value==null)return;Map<String,Object>j=NativeSensingReconciliation.asObject(value);keys(j,"job_id","job_revision","board_load_revision","material_setup_revision","lineage_id","lineage_revision");uuid(j,"job_id");uuid(j,"lineage_id");if(!text(j,"job_revision",64).matches("[a-f0-9]{64}"))throw bad("Invalid job revision");revision(j,"board_load_revision","load-");revision(j,"material_setup_revision","material-");integer(j,"lineage_revision",0,9007199254740991L);
    }
    private List<Object> faultRows(String recoveryOperation,String instance)throws IOException {
        if(!publicationFaults.isEmpty())throw bad("Unpublished fault cannot be reconciled in damaged running process");TreeMap<String,Object>rows=new TreeMap<>();
        for(Map.Entry<String,Map<String,Object>>e:faultRecords.entrySet())if(!superseded(e.getKey(),instance,true)){
            Map<String,Object>c=object(e.getValue(),"context");rows.put("fault:"+e.getKey(),bounded(map("kind","fault","observation_id",e.getKey(),"operation_id",c.get("operation_id"),"nozzle_id",c.get("nozzle_id"),"payload",e.getValue(),"prior_disposition_receipt_id",historicalSuperseded.get(e.getKey()))));}
        // A known lost-part result is not a sensor failure. Retain its actual returned
        // observation for job/material disposition without changing ordinary sticky guards.
        // After-pick negatives remain eligible for the native bounded missed-pick retry.
        for(Map.Entry<String,Map<String,Object>> e:completedRecords.entrySet())if(!superseded(e.getKey(),instance,true)){
            Map<String,Object> record=e.getValue(),c=object(record,"context"),d=object(record,"data");
            if("job".equals(c.get("scope"))&&c.get("job_context")!=null&&"check.returned".equals(record.get("native_event"))
                &&"part_on".equals(d.get("check_kind"))&&Boolean.FALSE.equals(d.get("verdict"))&&Set.of("align","before_place").contains(d.get("native_stage")))
                rows.put("material:"+e.getKey(),bounded(map("kind","material_not_detected","observation_id",e.getKey(),"operation_id",c.get("operation_id"),"nozzle_id",c.get("nozzle_id"),"payload",record,"prior_disposition_receipt_id",historicalSuperseded.get(e.getKey()))));
        }
        for(Map.Entry<String,Active>e:active.entrySet())if(!superseded(e.getKey(),instance,true)&&!Objects.equals(recoveryOperation,e.getValue().context.get("operation_id"))){Active a=e.getValue();rows.put("pending:"+e.getKey(),bounded(map("kind","pending","observation_id",e.getKey(),"operation_id",a.context.get("operation_id"),"nozzle_id",a.nozzle,"payload",a.payload,"prior_disposition_receipt_id",historicalSuperseded.get(e.getKey()))));}
        for(Map.Entry<String,Map<String,Object>>e:lifecycleRecords.entrySet())if(!superseded(e.getKey(),instance,true))rows.put("lifecycle:"+e.getKey(),bounded(map("kind","lifecycle","observation_id",null,"operation_id",e.getKey(),"nozzle_id",null,"payload",e.getValue(),"prior_disposition_receipt_id",historicalSuperseded.get(e.getKey()))));
        if(rows.size()>1024)throw bad("Recovery fault-set bound exceeded");return Collections.unmodifiableList(new ArrayList<>(rows.values()));
    }
    synchronized Runnable prepareDisposition(Map<String,Object> capture,String receiptId,String recoveryOperation)throws IOException {
        validateCapture(capture,recoveryOperation);uuidValue(receiptId);if(resolutionGenerations.containsKey(receiptId))throw bad("Disposition receipt reused");final long expected=generation;
        List<String>ids=capturedIds(capture);return new Runnable(){boolean used;public void run(){synchronized(NativeVacuumJournal.this){if(used||expected!=generation)throw new IllegalStateException("Stale disposition commit");used=true;for(String id:ids)historicalSuperseded.put(id,receiptId);dispositionGeneration++;generation++;resolutionGenerations.put(receiptId,generation);resolutionInstances.put(receiptId,(String)capture.get("bridge_instance_id"));}}};
    }
    synchronized void activateDisposition(Map<String,Object> capture,String receiptId,List<?> probes)throws IOException {
        Long resolvedGeneration=resolutionGenerations.get(receiptId);if(resolvedGeneration==null||resolvedGeneration!=generation||integer(capture,"prior_disposition_generation",0,9007199254740990L)+1!=dispositionGeneration)throw bad("Disposition changed before live activation");
        List<String>ids=capturedIds(capture);for(String id:ids)if(!receiptId.equals(historicalSuperseded.get(id)))throw bad("Disposition membership changed");
        for(Object item:probes){Map<String,Object>p=NativeSensingReconciliation.asObject(item),binding=object(p,"exact_current_binding");validateBinding(binding);String nozzle=text(p,"nozzle_id",128);NozzleState n=nozzles.get(nozzle);if(n==null)throw bad("Verified nozzle absent");n.occupancy="observed_empty";n.emptyBinding=binding;n.lastSuccessfulCheckBinding=binding;n.emptyLive=true;n.reason="explicit-simulator-reconciliation";}
        liveSuperseded.addAll(ids);generation++;
    }
    private static List<String>capturedIds(Map<String,Object>capture)throws IOException {List<String>ids=new ArrayList<>();for(Object item:NativeSensingReconciliation.list(capture,"faults",1024)){Map<String,Object>f=NativeSensingReconciliation.asObject(item);ids.add((String)(f.get("observation_id")!=null?f.get("observation_id"):f.get("operation_id")));}return ids;}
    synchronized boolean hasRecoveryCheck(String operation,String nozzle)throws IOException {
        for(Active a:active.values())if(a.kind.equals("check")&&operation.equals(a.context.get("operation_id"))&&nozzle.equals(a.nozzle))return true;
        for(Map<String,Object>p:completedRecords.values())if(((String)p.get("native_event")).startsWith("check.")&&operation.equals(object(p,"context").get("operation_id"))&&nozzle.equals(object(p,"context").get("nozzle_id")))return true;
        return false;
    }
    synchronized void validateRecoveryProbe(String operation,Map<String,Object>probe)throws IOException {
        String check=uuid(probe,"check_observation_id");Map<String,Object>parent=completedRecords.get(check);if(parent==null||!"check.returned".equals(parent.get("native_event")))throw bad("Probe has no returned check");
        Map<String,Object>c=object(parent,"context"),d=object(parent,"data");if(!operation.equals(c.get("operation_id"))||!"recovery".equals(c.get("scope"))||!"part_off".equals(d.get("check_kind"))||!Boolean.TRUE.equals(d.get("verdict"))||!Objects.equals(c.get("nozzle_id"),probe.get("nozzle_id"))||!binding(c,d).equals(probe.get("exact_current_binding")))throw bad("Probe is stale, false, or foreign");
        if(active.containsKey(check))throw bad("Probe check still pending");Set<String>reads=new HashSet<>();String on=null,off=null;
        for(Map.Entry<String,Map<String,Object>>e:completedRecords.entrySet()){Map<String,Object>r=e.getValue(),rd=object(r,"data");if(!check.equals(rd.get("parent_observation_id")))continue;if(!object(r,"context").equals(c))throw bad("Probe child context changed");String event=(String)r.get("native_event");if(event.equals("read.returned"))reads.add(e.getKey());else if(event.equals("valve.returned")){if(Boolean.TRUE.equals(rd.get("enabled"))){if(on!=null)throw bad("Repeated valve-on");on=e.getKey();}else{if(off!=null)throw bad("Repeated valve-off");off=e.getKey();}}else throw bad("Probe child failed");}
        for(Active a:active.values())if(check.equals(a.data.get("parent_observation_id")))throw bad("Probe child still pending");
        List<String>declared=NativeSensingReconciliation.ids(probe,"read_observation_ids",32,true);if(!reads.equals(new HashSet<>(declared))||!Objects.equals(on,probe.get("valve_on_id"))||!Objects.equals(off,probe.get("valve_off_id")))throw bad("Probe children omitted or invented");
    }

    /** Caller supplies freshly observed native identities; no field here is remote authority. */
    static Map<String,Object> binding(Map<String,Object> context,Map<String,Object> data)throws IOException {
        return binding((String)context.get("machine_id"),(String)context.get("bridge_instance_id"),(String)context.get("config_revision"),(String)context.get("nozzle_id"),(String)data.get("nozzle_tip_id"),(String)data.get("sensor_id"),object(data,"source"));
    }
    static Map<String,Object> binding(String machine,String instance,String revision,String nozzle,String tip,String sensor,Map<String,Object> source)throws IOException {
        Map<String,Object> b=bounded(map("machine_id",machine,"bridge_instance_id",instance,"config_revision",revision,"nozzle_id",nozzle,"nozzle_tip_id",tip,"sensor_id",sensor,"source",source));validateBinding(b);return b;
    }
    static void validateBinding(Map<String,Object> b)throws IOException {
        keys(b,"machine_id","bridge_instance_id","config_revision","nozzle_id","nozzle_tip_id","sensor_id","source");uuid(b,"machine_id");uuid(b,"bridge_instance_id");revision(b,"config_revision","cfg-");for(String k:List.of("nozzle_id","nozzle_tip_id","sensor_id"))identifier(b,k);source(object(b,"source"));
    }

    private void validateParent(Map<String,Object> c,Map<String,Object> d,String kind,boolean replay)throws IOException {
        Object parent=d.get("parent_observation_id");
        if(parent==null){
            if(kind.equals("valve"))throw bad("Probe valve lacks check parent");
            for(Active a:active.values())if(a.nozzle.equals(c.get("nozzle_id"))&&!superseded((String)a.data.get("observation_id"),(String)c.get("bridge_instance_id"),replay)){
                if(!"recovery".equals(c.get("scope"))||reconciliation==null||!reconciliation.capturedPending(c,(String)a.data.get("observation_id")))throw bad("Overlapping root observations");
            }
            if(kind.equals("read")&&d.get("check_kind")!=null)throw bad("Standalone read declares check kind");
            return;
        }
        Active p=active.get(uuidValue((String)parent));
        if(p==null||!p.kind.equals("check")||kind.equals("check")||!p.context.equals(c))throw bad("Unknown or foreign parent check");
        for(String key:List.of("native_stage","check_kind","nozzle_tip_id","sensor_id","source"))if(!Objects.equals(p.data.get(key),d.get(key)))throw bad("Child differs from its check: "+key);
        for(Active a:active.values())if(parent.equals(a.data.get("parent_observation_id")))throw bad("Overlapping sibling observations");
        if(kind.equals("valve")&&!"part_off".equals(d.get("check_kind")))throw bad("Valve pulse outside part-off check");
        if(kind.equals("valve")&&(Boolean.TRUE.equals(d.get("enabled"))?(p.valveOn||p.valveOff):p.valveOff||!p.valveOn&&!p.childFailed))throw bad("Probe valve order differs");
    }
    private static boolean cleanup(String event,Map<String,Object> d){return event.equals("valve.before")&&Boolean.FALSE.equals(d.get("enabled"))&&Boolean.TRUE.equals(d.get("cleanup_attempt"));}
    private static void validateContext(Map<String,Object> c)throws IOException {
        if("recovery".equals(c.get("scope")))keys(c,"operation_id","request_id","machine_id","bridge_instance_id","config_revision","nozzle_id","scope","job_context","recovery_context");
        else keys(c,"operation_id","request_id","machine_id","bridge_instance_id","config_revision","nozzle_id","scope","job_context");
        for(String k:List.of("operation_id","machine_id","bridge_instance_id"))uuid(c,k);
        if(c.get("operation_id").equals(c.get("request_id")))throw bad("Operation/request identity aliased");
        revision(c,"config_revision","cfg-");identifier(c,"nozzle_id");
        String scope=text(c,"scope",16);if(!Set.of("manual","job","cleanup","recovery").contains(scope))throw bad("Unknown observation scope");
        if(scope.equals("manual")){uuid(c,"request_id");if(c.get("job_context")!=null)throw bad("Manual context has job binding");}
        else if(scope.equals("recovery")&&c.get("job_context")==null){uuid(c,"request_id");Map<String,Object> r=object(c,"recovery_context");keys(r,"task_id","fault_set_sha256");uuid(r,"task_id");if(!text(r,"fault_set_sha256",64).matches("[a-f0-9]{64}"))throw bad("Invalid recovery fault digest");}
        else {requestText(c);Map<String,Object> j=object(c,"job_context");keys(j,"job_id","job_revision","board_load_revision","material_setup_revision","lineage_id","lineage_revision");
            uuid(j,"job_id");uuid(j,"lineage_id");if(!text(j,"job_revision",64).matches("[a-f0-9]{64}"))throw bad("Invalid job revision digest");
            revision(j,"board_load_revision","load-");revision(j,"material_setup_revision","material-");integer(j,"lineage_revision",0,9007199254740991L);}
    }
    private static void validateData(String event,Map<String,Object> d)throws IOException {
        Set<String> expected=new HashSet<>(COMMON);String kind=event.split("\\.")[0];boolean failed=event.endsWith(".failed"),returned=event.endsWith(".returned");
        if(kind.equals("valve"))expected.addAll(List.of("enabled","reason","cleanup_attempt"));
        if(returned&&kind.equals("check"))expected.add("verdict");
        if(returned&&kind.equals("read"))expected.addAll(List.of("raw","raw_length","value"));
        if(failed){expected.addAll(List.of("failure_code","failure_type"));if(d.containsKey("raw"))expected.addAll(List.of("raw","raw_length"));for(String flag:List.of("raw_truncated","raw_sanitized"))if(d.containsKey(flag))expected.add(flag);}
        if(!expected.equals(d.keySet()))throw bad("Unexpected native observation fields");
        integer(d,"api_version",1,1);uuid(d,"observation_id");if(d.get("parent_observation_id")!=null)uuid(d,"parent_observation_id");
        if(Objects.equals(d.get("observation_id"),d.get("parent_observation_id")))throw bad("Observation is its own parent");
        if(!STAGES.contains(d.get("native_stage")))throw bad("Unknown native sensing stage");
        Object check=d.get("check_kind");if(check!=null&&!Set.of("part_on","part_off").contains(check)||kind.equals("check")&&check==null)throw bad("Invalid check kind");
        identifier(d,"nozzle_tip_id");identifier(d,"sensor_id");source(object(d,"source"));
        if(kind.equals("valve")){bool(d,"enabled");bool(d,"cleanup_attempt");if(!"part_off_probe".equals(d.get("reason"))||d.get("enabled").equals(d.get("cleanup_attempt")))throw bad("Invalid probe valve intent");}
        if(returned&&kind.equals("check"))bool(d,"verdict");
        if(d.containsKey("raw")){
            Object raw=d.get("raw");if(!(raw instanceof String)||((String)raw).length()>128||((String)raw).chars().anyMatch(ch->ch>127))throw bad("Invalid bounded raw sample");
            long length=integer(d,"raw_length",0,Integer.MAX_VALUE);
            if(((String)raw).length()!=Math.min(128,length))throw bad("Raw sample length mismatch");
            if(d.containsKey("raw_truncated")!= (length>128)||d.containsKey("raw_truncated")&&!Boolean.TRUE.equals(d.get("raw_truncated")))throw bad("Truncation flag mismatch");
        }else if(d.containsKey("raw_sanitized")||d.containsKey("raw_truncated"))throw bad("Raw flags without sample");
        if(d.containsKey("raw_sanitized")&&!Boolean.TRUE.equals(d.get("raw_sanitized")))throw bad("Invalid raw sanitation flag");
        if(returned&&kind.equals("read")){
            double parsed;try{parsed=Double.parseDouble((String)d.get("raw"));}catch(RuntimeException invalid){throw bad("Returned raw sample is not numeric");}
            if(!Double.isFinite(parsed)||!(d.get("value") instanceof BigDecimal)||BigDecimal.valueOf(parsed).compareTo((BigDecimal)d.get("value"))!=0)throw bad("Returned finite value differs from raw sample");
        }
        if(failed){
            Set<String> codes=kind.equals("read")?Set.of("SENSOR_VALUE_INVALID","SENSOR_VALUE_NONFINITE","SENSOR_READ_FAILED"):kind.equals("check")?Set.of("NATIVE_CHECK_FAILED"):Set.of("VALVE_ACTUATION_FAILED");
            if(!codes.contains(d.get("failure_code")))throw bad("Failure code differs from kind");identifier(d,"failure_type");
            if(!kind.equals("read")&&d.containsKey("raw"))throw bad("Non-read failure invents raw sample");
        }
    }
    private static void source(Map<String,Object> s)throws IOException {
        keys(s,"origin","profile","source_id","process_id","simulation_only","hardware_qualified","fixture_id","scenario_id","units");
        if(!"controlled-simulator".equals(s.get("origin"))||!"controlled-native-vacuum-v1".equals(s.get("profile"))||!Boolean.TRUE.equals(s.get("simulation_only"))||!Boolean.FALSE.equals(s.get("hardware_qualified")))throw bad("Unqualified source provenance");
        uuid(s,"source_id");integer(s,"process_id",1,9007199254740991L);
        for(String key:List.of("fixture_id","scenario_id","units")){String value=text(s,key,128);if(value.chars().anyMatch(ch->ch<32||ch>126))throw bad("Source provenance must be printable ASCII");}
    }
    static void validateRawRecord(String raw)throws IOException {
        if(raw==null||raw.getBytes(StandardCharsets.UTF_8).length>MAX_RECORD_BYTES+4096)throw bad("Raw record limit");
        try(JsonReader reader=new JsonReader(new StringReader(raw))){reader.setLenient(false);strict(reader,0,new int[1]);if(reader.peek()!=JsonToken.END_DOCUMENT)throw bad("Trailing JSON value");}
        catch(IllegalStateException|NumberFormatException invalid){throw bad("Malformed JSON record");}
    }
    private static void strict(JsonReader r,int depth,int[] nodes)throws IOException {
        if(depth>32||++nodes[0]>4096)throw bad("JSON structural limit");
        switch(r.peek()){
            case BEGIN_OBJECT:r.beginObject();Set<String> names=new HashSet<>();while(r.hasNext()){String name=r.nextName();validString(name);if(!names.add(name))throw bad("Duplicate JSON object key");strict(r,depth+1,nodes);}r.endObject();break;
            case BEGIN_ARRAY:r.beginArray();while(r.hasNext())strict(r,depth+1,nodes);r.endArray();break;
            case STRING:validString(r.nextString());break;
            case NUMBER:decimal(r.nextString());break;
            case BOOLEAN:r.nextBoolean();break;
            case NULL:r.nextNull();break;
            default:throw bad("Invalid JSON token");
        }
    }
    private static Map<String,Object> bounded(Map<String,Object> p)throws IOException{return bounded(p,MAX_RECORD_BYTES);}
    @SuppressWarnings("unchecked") private static Map<String,Object> bounded(Map<String,Object> p,int max)throws IOException {
        if(p==null)throw bad("Object required");Map<String,Object> result=(Map<String,Object>)copy(p,0,new int[1]);
        if(JSON.toJson(result).getBytes(StandardCharsets.UTF_8).length>max)throw bad("Payload size limit");return result;
    }
    private static Object copy(Object v,int depth,int[] nodes)throws IOException {
        if(depth>32||++nodes[0]>16384)throw bad("DTO structural limit");
        if(v instanceof Map){Map<String,Object> out=new LinkedHashMap<>();for(Map.Entry<?,?> e:((Map<?,?>)v).entrySet()){if(!(e.getKey() instanceof String))throw bad("String keys required");validString((String)e.getKey());out.put((String)e.getKey(),copy(e.getValue(),depth+1,nodes));}return Collections.unmodifiableMap(out);}
        if(v instanceof List){List<Object> out=new ArrayList<>();for(Object x:(List<?>)v)out.add(copy(x,depth+1,nodes));return Collections.unmodifiableList(out);}
        if(v==null||v instanceof Boolean)return v;if(v instanceof String){validString((String)v);return v;}if(v instanceof Number)return decimal(v.toString());throw bad("Non-JSON DTO value");
    }
    private static BigDecimal decimal(String raw)throws IOException {try{if(raw.length()>256)throw new NumberFormatException();BigDecimal n=new BigDecimal(raw);if(Math.abs((long)n.scale())>10000||!Double.isFinite(n.doubleValue()))throw new NumberFormatException();return n.stripTrailingZeros();}catch(NumberFormatException e){throw bad("Invalid finite number");}}
    private static void validString(String s)throws IOException {for(int i=0;i<s.length();i++){char c=s.charAt(i);if(Character.isHighSurrogate(c)){if(++i>=s.length()||!Character.isLowSurrogate(s.charAt(i)))throw bad("Malformed Unicode");}else if(Character.isLowSurrogate(c))throw bad("Malformed Unicode");}}
    private static void keys(Map<String,Object> p,String...k)throws IOException {if(!p.keySet().equals(new HashSet<>(Arrays.asList(k))))throw bad("Unexpected fields");}
    @SuppressWarnings("unchecked") private static Map<String,Object> object(Map<String,Object> p,String k)throws IOException {if(!(p.get(k) instanceof Map))throw bad("Object required: "+k);return(Map<String,Object>)p.get(k);}
    private static String text(Map<String,Object> p,String k,int max)throws IOException {if(!(p.get(k) instanceof String)||((String)p.get(k)).isBlank()||((String)p.get(k)).length()>max)throw bad("Bounded string required: "+k);return(String)p.get(k);}
    /** Existing Bridge requests are bounded nonempty strings; preserve their exact bytes, including whitespace. */
    private static String requestText(Map<String,Object> p)throws IOException {
        Object value=p.get("request_id");if(!(value instanceof String)||((String)value).isEmpty()||((String)value).length()>1024)throw bad("Bounded legacy request string required");
        validString((String)value);return(String)value;
    }
    private static String identifier(Map<String,Object> p,String k)throws IOException {String s=text(p,k,128);if(s.chars().anyMatch(ch->ch<32||ch==127))throw bad("Invalid native identifier");return s;}
    private static String uuid(Map<String,Object> p,String k)throws IOException{return uuidValue(text(p,k,36));}
    private static String uuidValue(String s)throws IOException {try{if(s==null||!UUID.fromString(s).toString().equals(s))throw new IllegalArgumentException();return s;}catch(RuntimeException invalid){throw bad("Canonical UUID required");}}
    private static long integer(Map<String,Object> p,String k,long low,long high)throws IOException {try{return NativeJournalJson.integer(p.get(k),low,high);}catch(RuntimeException invalid){throw bad("Exact bounded integer required: "+k);}}
    private static void revision(Map<String,Object> p,String k,String prefix)throws IOException {String s=text(p,k,32);if(!s.matches(prefix+"(0|[1-9][0-9]*)"))throw bad("Invalid revision: "+k);try{if(new BigDecimal(s.substring(prefix.length())).compareTo(BigDecimal.valueOf(9007199254740991L))>0)throw bad("Revision exceeds exact integer bound");}catch(NumberFormatException invalid){throw bad("Invalid revision");}}
    private static void bool(Map<String,Object> p,String k)throws IOException {if(!(p.get(k) instanceof Boolean))throw bad("Boolean required: "+k);}
    private static Map<String,Object> map(Object...v){Map<String,Object> p=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)p.put((String)v[i],v[i+1]);return p;}
    private static IOException bad(String message){return new IOException("Vacuum journal: "+message);}
}
