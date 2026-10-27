/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;

/** Append-only sensing recovery facts. Native/GUI ownership and operation admission remain Bridge duties. */
final class NativeSensingReconciliation {
    static final String PROFILE="native-simulator-sensing-reconciliation-v1";
    static final int MAX_TASKS=1024,MAX_RECORD_BYTES=262144;
    private static final Gson JSON=new GsonBuilder().serializeNulls().create();
    static final String CONTINUATION_KIND="continue-faulted-job-replacement";
    static final String RESTART_KIND="restart-faulted-job-replacement";
    private static final Set<String> KINDS=Set.of("restore-sensing-readiness","replace-faulted-job-attempt",CONTINUATION_KIND,RESTART_KIND);
    @FunctionalInterface interface CompletionAuthority {
        /** Must inspect the exact forced successful local terminal operation and actual native wrapper completion. */
        boolean completed(String operationId,String instanceId,boolean replay)throws IOException;
    }
    @FunctionalInterface interface DispositionAuthority {
        /** Verify exact forced material/board replacement subreceipts and dependency union, never trust shaped DTOs. */
        boolean verified(Map<String,Object> capture,Map<String,Object> dispositions,boolean replay)throws IOException;
    }
    @FunctionalInterface interface RestartObservationAuthority {
        /** Verify exact forced attachment/disposition/observation records. This never authorizes sensing or job execution. */
        boolean verified(Map<String,Object> capture,Map<String,Object> restartContext,Map<String,Object> observations,boolean replay)throws IOException;
    }
    @FunctionalInterface interface Work<T>{T run()throws Exception;}
    static final class Capture {
        final Map<String,Object> payload;final String digest;final long generation;
        Capture(Map<String,Object> payload)throws IOException {this.payload=freeze(payload);digest=digest(this.payload);generation=number(this.payload,"prior_disposition_generation",0,9007199254740991L);}
    }
    static final class Permit {
        private final NativeSensingReconciliation owner;private final String taskId,operationId,instanceId;
        private final Object localOwner;private volatile boolean revoked;
        private Permit(NativeSensingReconciliation owner,Task task,Object localOwner){this.owner=owner;taskId=task.id();operationId=task.operation;instanceId=(String)task.record.get("bridge_instance_id");this.localOwner=localOwner;}
        String taskId(){return taskId;}String operationId(){return operationId;}
        synchronized void revoke(){revoked=true;}
    }
    static final class WrapperCompletion {
        private final NativeSensingReconciliation owner;private final String taskId,operationId,instanceId;private boolean used;
        private WrapperCompletion(NativeSensingReconciliation owner,Task task){this.owner=owner;taskId=task.id();operationId=task.operation;instanceId=(String)task.record.get("bridge_instance_id");}
    }
    private static final class Task {
        final Map<String,Object> record;String state="awaiting_local_action",operation;
        Map<String,Object> action,intervention,verification,receipt;Permit permit;boolean replayed,liveActivated;
        Task(Map<String,Object> record){this.record=record;}
        String id(){return(String)record.get("task_id");}
        Map<String,Object> capture()throws IOException{return object(record,"fault_set");}
    }
    private final NativeVacuumJournal vacuum;
    private final CompletionAuthority completion;
    private final DispositionAuthority dispositionAuthority;
    private final RestartObservationAuthority restartObservationAuthority;
    private final Map<String,Task> tasks=new LinkedHashMap<>();
    private final ThreadLocal<Permit> current=new ThreadLocal<>();
    private long generation;
    NativeSensingReconciliation(NativeVacuumJournal vacuum,CompletionAuthority completion,DispositionAuthority dispositionAuthority){this(vacuum,completion,dispositionAuthority,(capture,context,observations,replay)->false);}
    NativeSensingReconciliation(NativeVacuumJournal vacuum,CompletionAuthority completion,DispositionAuthority dispositionAuthority,RestartObservationAuthority restartObservationAuthority){this.vacuum=Objects.requireNonNull(vacuum);this.completion=Objects.requireNonNull(completion);this.dispositionAuthority=Objects.requireNonNull(dispositionAuthority);this.restartObservationAuthority=Objects.requireNonNull(restartObservationAuthority);vacuum.attachReconciliation(this);}
    static boolean matches(String type){return type!=null&&type.startsWith("sensing_reconciliation_");}

    Map<String,Object> taskRecord(String taskId,String requestOperationId,String requestId,Capture capture,String kind,long ownershipEpoch,String expiresAt)throws IOException {
        Map<String,Object> c=capture.payload;
        return freeze(map("schema_version",1,"profile",PROFILE,"task_id",taskId,"machine_id",c.get("machine_id"),"bridge_instance_id",c.get("bridge_instance_id"),"request_operation_id",requestOperationId,"request_id",requestId,"recovery_kind",kind,"ownership_epoch",ownershipEpoch,"expires_at",expiresAt,"fault_set_sha256",capture.digest,"fault_set",c));
    }
    Map<String,Object> taskRecordForContinuation(String taskId,String requestOperationId,String requestId,Capture capture,NativeFaultedJobReplacement.ContinuationCapture replacement,long ownershipEpoch,String expiresAt)throws IOException {
        if(replacement==null||!Objects.equals(capture.payload.get("dependencies"),replacement.dependencies())||!Objects.equals(capture.payload.get("job_context"),replacement.jobContext()))throw bad("Continuation task must capture the exact original and replacement dependency union");
        Map<String,Object> record=new LinkedHashMap<>(taskRecord(taskId,requestOperationId,requestId,capture,CONTINUATION_KIND,ownershipEpoch,expiresAt));
        record.put("replacement_context",map("replacement_attempt_id",replacement.payload.get("replacement_attempt_id"),"continuation_capture_sha256",replacement.digest));return freeze(record);
    }
    /** The typed capture binds one freshly staged restart decision. Its owner independently
     * compares this exact context before attaching any reconstructed native graph. */
    Map<String,Object> taskRecordForRestart(String taskId,String requestOperationId,String requestId,Capture capture,NativeFaultedJobReplacement.RestartCapture replacement,long ownershipEpoch,String expiresAt)throws IOException {
        if(capture==null||replacement==null||!Objects.equals(capture.payload.get("dependencies"),replacement.dependencies())||!Objects.equals(capture.payload.get("job_context"),replacement.jobContext()))throw bad("Restart task must capture the exact historical and replacement dependency union");
        if(!NativeFaultedJobReplacement.digest(replacement.payload).equals(replacement.digest))throw bad("Restart capture digest differs");
        Map<String,Object> record=new LinkedHashMap<>(taskRecord(taskId,requestOperationId,requestId,capture,RESTART_KIND,ownershipEpoch,expiresAt));
        record.put("restart_context",map("replacement_attempt_id",replacement.payload.get("replacement_attempt_id"),"restart_capture_sha256",replacement.digest));validateRestartContext(record);return freeze(record);
    }
    private static void validateRestartContext(Map<String,Object> record)throws IOException {
        Map<String,Object> context=object(record,"restart_context");keys(context,"replacement_attempt_id","restart_capture_sha256");uuid(context,"replacement_attempt_id");hash(context,"restart_capture_sha256");
        Map<String,Object> capture=object(record,"fault_set");if(capture.get("job_context")==null||!list(object(capture,"dependencies"),"job_attempt_ids",256).contains(context.get("replacement_attempt_id")))throw bad("Restart requires its replacement attempt in the job dependency union");
    }
    Map<String,Object> intentRecord(String taskId,String operationId,String interventionId,String submittedAt,long ownershipEpoch,String operatorLabel)throws IOException {synchronized(vacuum){
        Task t=task(taskId);return record(t,"recovery_operation_id",operationId,"local_action",map("intervention_id",interventionId,"kind",t.record.get("recovery_kind"),"submitted_at",submittedAt,"ownership_epoch",ownershipEpoch,"operator_label",operatorLabel,"operator_identity_verified",false));
    }}
    Map<String,Object> interventionRecord(String taskId,List<?> oldBindings,List<?> newBindings,List<String> actionIds,Map<String,Object> latchBefore,Map<String,Object> latchAfter)throws IOException {synchronized(vacuum){
        Task t=task(taskId);return record(t,"recovery_operation_id",t.operation,"intervention",map("old_bindings",oldBindings,"new_bindings",newBindings,"native_action_ids",actionIds,"synthetic_latch_before",latchBefore,"synthetic_latch_after",latchAfter));
    }}
    Map<String,Object> verifiedRecord(String taskId,Map<String,Object> verification)throws IOException {synchronized(vacuum){Task t=task(taskId);return record(t,"recovery_operation_id",t.operation,"verification",verification);}}
    /** Terminal receipt for the observation phase only. The reducer independently checks the
     * actual current wrapper and root authority; producing this DTO cannot close a task. */
    Map<String,Object> restartObservationsCompletedRecord(String taskId,String receiptId,Map<String,Object> observations)throws IOException {synchronized(vacuum){
        Task t=task(taskId);if(!RESTART_KIND.equals(t.record.get("recovery_kind")))throw bad("Only restart observations have this lifecycle completion");
        return record(t,"request_operation_id",t.record.get("request_operation_id"),"request_id",t.record.get("request_id"),"recovery_operation_id",t.operation,"receipt_id",receiptId,"restart_context",object(t.record,"restart_context"),"observations",observations,"observations_sha256",digest(observations),"native_wrapper_completed",true,"native_wrapper_succeeded",true,"faults_resolved",false,"execution_authority_restored",false,"history_rewritten",false,"old_operation_replayed",false,"old_operation_outcome_changed",false,"physical_occupancy_verified",false,"simulation_only",true,"hardware_qualified",false);
    }}
    Map<String,Object> resolvedRecord(String taskId,String receiptId,Map<String,Object> dispositions,List<?> evidence)throws IOException {synchronized(vacuum){
        Task t=task(taskId);return record(t,"request_operation_id",t.record.get("request_operation_id"),"request_id",t.record.get("request_id"),"original_operation_ids",originalIds(t,"operation_id"),"original_observation_ids",originalIds(t,"observation_id"),"original_action_ids",object(t.capture(),"dependencies").get("action_ids"),"scope",receiptScope(t),"local_action",t.action,"intervention",t.intervention,"verification",receiptVerification(t),"recovery_operation_id",t.operation,"receipt_id",receiptId,"prior_disposition_generation",t.capture().get("prior_disposition_generation"),"new_disposition_generation",number(t.capture(),"prior_disposition_generation",0,9007199254740990L)+1,"dispositions",dispositions,"evidence_artifacts",evidence,"history_rewritten",false,"old_operation_replayed",false,"old_operation_outcome_changed",false,"physical_occupancy_verified",false,"simulation_only",true,"hardware_qualified",false);
    }}
    Map<String,Object> closedRecord(String taskId,String reason)throws IOException {synchronized(vacuum){Task t=task(taskId);return record(t,"reason",reason);}}
    Map<String,Object> unknownRecord(String taskId,String reason)throws IOException {synchronized(vacuum){Task t=task(taskId);return record(t,"recovery_operation_id",t.operation,"reason",reason);}}
    private Map<String,Object> record(Task t,Object...values)throws IOException {
        Map<String,Object> p=map("schema_version",1,"profile",PROFILE,"task_id",t.id(),"machine_id",t.record.get("machine_id"),"bridge_instance_id",t.record.get("bridge_instance_id"),"fault_set_sha256",t.record.get("fault_set_sha256"));
        for(int i=0;i<values.length;i+=2)p.put((String)values[i],values[i+1]);return freeze(p);
    }

    Runnable prepare(String type,Map<String,Object> supplied,String envelopeInstance)throws IOException {synchronized(vacuum){return prepare(type,supplied,envelopeInstance,false);}}
    void recover(String type,Map<String,Object> supplied,String envelopeInstance)throws IOException {synchronized(vacuum){prepare(type,supplied,envelopeInstance,true).run();}}
    private Runnable prepare(String type,Map<String,Object> supplied,String envelopeInstance,boolean replay)throws IOException {
        if(!matches(type))return()->{};
        Map<String,Object> p=freeze(supplied);header(p,envelopeInstance);String id=uuid(p,"task_id");Task t=tasks.get(id);String suffix=type.substring("sensing_reconciliation_".length());
        Runnable mutation;
        if(suffix.equals("task")){
            if(CONTINUATION_KIND.equals(p.get("recovery_kind"))){
                keys(p,"schema_version","profile","task_id","machine_id","bridge_instance_id","request_operation_id","request_id","recovery_kind","ownership_epoch","expires_at","fault_set_sha256","fault_set","replacement_context");Map<String,Object> r=object(p,"replacement_context");keys(r,"replacement_attempt_id","continuation_capture_sha256");uuid(r,"replacement_attempt_id");hash(r,"continuation_capture_sha256");
                if(object(p,"fault_set").get("job_context")==null||!list(object(object(p,"fault_set"),"dependencies"),"job_attempt_ids",256).contains(r.get("replacement_attempt_id")))throw bad("Continuation requires its replacement attempt in the job dependency union");
            }else if(RESTART_KIND.equals(p.get("recovery_kind"))){
                keys(p,"schema_version","profile","task_id","machine_id","bridge_instance_id","request_operation_id","request_id","recovery_kind","ownership_epoch","expires_at","fault_set_sha256","fault_set","restart_context");validateRestartContext(p);
            }else keys(p,"schema_version","profile","task_id","machine_id","bridge_instance_id","request_operation_id","request_id","recovery_kind","ownership_epoch","expires_at","fault_set_sha256","fault_set");
            if(t!=null||tasks.size()>=MAX_TASKS)throw bad("Repeated task or task limit");uuid(p,"request_operation_id");uuid(p,"request_id");if(p.get("request_operation_id").equals(p.get("request_id")))throw bad("Aliased request/operation");
            if(!KINDS.contains(p.get("recovery_kind")))throw bad("Unsupported recovery kind");number(p,"ownership_epoch",0,9007199254740991L);instant(p,"expires_at");
            Map<String,Object> c=object(p,"fault_set");vacuum.validateCapture(c,null);if(!digest(c).equals(hash(p,"fault_set_sha256")))throw bad("Fault capture digest differs");
            for(String k:List.of("machine_id","bridge_instance_id"))if(!p.get(k).equals(c.get(k)))throw bad("Capture identity differs");
            if(p.get("recovery_kind").equals("restore-sensing-readiness")&&(c.get("job_context")!=null||hasWorkpieceDependencies(object(c,"dependencies"))))throw bad("Standalone recovery cannot settle job/material/board dependencies");
            Task nt=new Task(p);nt.replayed=replay;mutation=()->tasks.put(id,nt);
        }else{
            if(t==null)throw bad("Unknown reconciliation task");for(String k:List.of("machine_id","bridge_instance_id","fault_set_sha256"))if(!Objects.equals(p.get(k),t.record.get(k)))throw bad("Foreign task binding");
            switch(suffix){
            case "intent":{
                fields(p,"recovery_operation_id","local_action");state(t,"awaiting_local_action");String operation=uuid(p,"recovery_operation_id");if(operation.equals(t.record.get("request_operation_id"))||operation.equals(t.record.get("request_id")))throw bad("Recovery operation aliases request");
                for(Task other:tasks.values())if(operation.equals(other.operation))throw bad("Recovery operation reused");
                Map<String,Object> a=object(p,"local_action");keys(a,"intervention_id","kind","submitted_at","ownership_epoch","operator_label","operator_identity_verified");uuid(a,"intervention_id");instant(a,"submitted_at");number(a,"ownership_epoch",0,9007199254740991L);text(a,"operator_label",128);falseValue(a,"operator_identity_verified");
                if(!a.get("kind").equals(t.record.get("recovery_kind"))||!a.get("ownership_epoch").equals(t.record.get("ownership_epoch"))||!Instant.parse((String)a.get("submitted_at")).isBefore(Instant.parse((String)t.record.get("expires_at"))))throw bad("Local decision differs or expired");
                vacuum.validateCapture(t.capture(),null);mutation=()->{t.operation=operation;t.action=a;t.state="reconciling";t.replayed|=replay;};break;}
            case "intervention":{
                fields(p,"recovery_operation_id","intervention");operation(t,p);state(t,"reconciling");Map<String,Object> i=object(p,"intervention");validateIntervention(t,i);vacuum.validateCapture(t.capture(),t.operation);
                mutation=()->{t.intervention=i;t.state="intervention_recorded";};break;}
            case "verified":{
                fields(p,"recovery_operation_id","verification");operation(t,p);state(t,"intervention_recorded");Map<String,Object> v=object(p,"verification");validateVerification(t,v);vacuum.validateCapture(t.capture(),t.operation);
                mutation=()->{t.verification=v;t.state="verified_pending_commit";};break;}
            case "restart_observations_completed":{
                fields(p,"request_operation_id","request_id","recovery_operation_id","receipt_id","restart_context","observations","observations_sha256","native_wrapper_completed","native_wrapper_succeeded","faults_resolved","execution_authority_restored","history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","simulation_only","hardware_qualified");
                operation(t,p);state(t,"reconciling");if(!RESTART_KIND.equals(t.record.get("recovery_kind")))throw bad("Only a restart observation task may complete this phase");String receipt=uuid(p,"receipt_id");
                for(Task other:tasks.values())if(other.receipt!=null&&receipt.equals(other.receipt.get("receipt_id")))throw bad("Lifecycle receipt identity reused");
                if(!Objects.equals(p.get("request_operation_id"),t.record.get("request_operation_id"))||!Objects.equals(p.get("request_id"),t.record.get("request_id"))||!Objects.equals(p.get("restart_context"),t.record.get("restart_context")))throw bad("Restart completion changes its exact local request/context");
                for(String k:List.of("faults_resolved","execution_authority_restored","history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","hardware_qualified"))falseValue(p,k);
                for(String k:List.of("native_wrapper_completed","native_wrapper_succeeded","simulation_only"))trueValue(p,k);
                Map<String,Object> observations=object(p,"observations");validateRestartObservations(t,observations);if(!digest(observations).equals(hash(p,"observations_sha256")))throw bad("Restart observation digest differs");
                vacuum.validateCapture(t.capture(),t.operation);
                if(!completion.completed(t.operation,(String)t.record.get("bridge_instance_id"),replay))throw bad("Successful exact current native wrapper and forced terminal operation required");
                if(!restartObservationAuthority.verified(t.capture(),object(t.record,"restart_context"),observations,replay))throw bad("Exact separately forced restart observations not verified");
                mutation=()->{t.receipt=p;t.state="restart_observations_completed";if(t.permit!=null)t.permit.revoke();};break;}
            case "resolved":{
                fields(p,"request_operation_id","request_id","original_operation_ids","original_observation_ids","original_action_ids","scope","local_action","intervention","verification","recovery_operation_id","receipt_id","prior_disposition_generation","new_disposition_generation","dispositions","evidence_artifacts","history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","simulation_only","hardware_qualified");operation(t,p);state(t,"verified_pending_commit");uuid(p,"receipt_id");
                if(!Objects.equals(p.get("request_operation_id"),t.record.get("request_operation_id"))||!Objects.equals(p.get("request_id"),t.record.get("request_id"))||!originalIds(t,"operation_id").equals(p.get("original_operation_ids"))||!originalIds(t,"observation_id").equals(p.get("original_observation_ids"))||!object(t.capture(),"dependencies").get("action_ids").equals(p.get("original_action_ids"))||!receiptScope(t).equals(p.get("scope"))||!Objects.equals(t.action,p.get("local_action"))||!Objects.equals(t.intervention,p.get("intervention"))||!receiptVerification(t).equals(p.get("verification")))throw bad("Final receipt changed original context or evidence");
                long prior=number(p,"prior_disposition_generation",0,9007199254740990L);if(prior!=number(t.capture(),"prior_disposition_generation",0,9007199254740990L)||number(p,"new_disposition_generation",1,9007199254740991L)!=prior+1)throw bad("Disposition generation differs");
                for(String k:List.of("history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","hardware_qualified"))falseValue(p,k);trueValue(p,"simulation_only");
                validateDispositions(t,object(p,"dispositions"));if(!dispositionAuthority.verified(t.capture(),object(p,"dispositions"),replay))throw bad("Separate exact material/board dependencies not verified");artifacts(p,"evidence_artifacts");validateVerification(t,t.verification);vacuum.validateCapture(t.capture(),t.operation);
                if(!completion.completed(t.operation,(String)t.record.get("bridge_instance_id"),replay))throw bad("Successful exact native wrapper and forced terminal operation required");
                Runnable disposition=vacuum.prepareDisposition(t.capture(),(String)p.get("receipt_id"),t.operation);
                mutation=()->{disposition.run();t.receipt=p;t.state="resolved_for_current_scope";if(t.permit!=null)t.permit.revoke();};break;}
            case "unknown":{
                fields(p,"recovery_operation_id","reason");operation(t,p);if(!Set.of("reconciling","intervention_recorded","verified_pending_commit").contains(t.state))throw bad("Unknown outcome outside admitted recovery");reason(p);mutation=()->{t.state="reconciliation_unknown";if(t.permit!=null)t.permit.revoke();};break;}
            case "closed":{
                fields(p,"reason");state(t,"awaiting_local_action");String reason=text(p,"reason",64);if(!Set.of("scope_stale","cancelled","expired").contains(reason))throw bad("Unknown task close reason");mutation=()->t.state=reason;break;}
            default:throw bad("Unknown reconciliation event");
            }
        }
        long expected=generation;return new Runnable(){boolean used;public void run(){synchronized(vacuum){if(used||generation!=expected)throw new IllegalStateException("Stale/reused reconciliation commit");used=true;mutation.run();generation++;}}};
    }

    Permit issuePermit(String taskId,String recoveryOperationId,String faultDigest,Object localOwner)throws IOException {synchronized(vacuum){
        Task t=task(taskId);state(t,"reconciling");if(t.replayed||t.permit!=null||localOwner==null||!Objects.equals(t.operation,recoveryOperationId)||!Objects.equals(t.record.get("fault_set_sha256"),faultDigest))throw bad("Recovery permit not available");if(!Instant.now().isBefore(Instant.parse((String)t.record.get("expires_at"))))throw bad("Local recovery task expired");vacuum.validateCapture(t.capture(),t.operation);return t.permit=new Permit(this,t,localOwner);
    }}
    <T>T withPermit(Permit permit,Object localOwner,Work<T> work)throws Exception {
        synchronized(vacuum){validatePermit(permit,localOwner);if(current.get()!=null)throw bad("Nested recovery scope");current.set(permit);}
        try{return work.run();}finally{current.remove();}
    }
    void requirePermit(Permit permit,Object localOwner)throws IOException {synchronized(vacuum){validatePermit(permit,localOwner);}}
    private void validatePermit(Permit p,Object owner)throws IOException {
        if(p==null||p.owner!=this||p.revoked||p.localOwner!=owner)throw bad("Invalid/revoked local recovery permit");Task t=task(p.taskId);if(t.permit!=p||!Set.of("reconciling","intervention_recorded").contains(t.state)||!p.operationId.equals(t.operation)||!p.instanceId.equals(t.record.get("bridge_instance_id")))throw bad("Permit scope differs");if(!Instant.now().isBefore(Instant.parse((String)t.record.get("expires_at"))))throw bad("Recovery permit expired");vacuum.validateCapture(t.capture(),t.operation);
    }
    Map<String,Object> observationContext(Permit p,String nozzleId)throws IOException {synchronized(vacuum){
        validatePermit(p,p.localOwner);Task t=task(p.taskId);Map<String,Object> c=t.capture();if(!bindingByNozzle(list(c,"nozzle_bindings",64)).containsKey(nozzleId))throw bad("Nozzle outside recovery scope");
        return freeze(map("operation_id",t.operation,"request_id",t.record.get("request_id"),"machine_id",c.get("machine_id"),"bridge_instance_id",c.get("bridge_instance_id"),"config_revision",c.get("config_revision"),"nozzle_id",nozzleId,"scope","recovery","job_context",c.get("job_context"),"recovery_context",map("task_id",t.id(),"fault_set_sha256",t.record.get("fault_set_sha256"))));
    }}
    boolean validateObservation(Map<String,Object> c,Map<String,Object> d,String event,boolean replay)throws IOException {synchronized(vacuum){
        Map<String,Object> r=object(c,"recovery_context");keys(r,"task_id","fault_set_sha256");Task t=task(uuid(r,"task_id"));if(!Objects.equals(t.record.get("fault_set_sha256"),hash(r,"fault_set_sha256"))||!Objects.equals(c.get("operation_id"),t.operation))throw bad("Foreign recovery observation");
        boolean cleanup=event.startsWith("valve.")&&Boolean.FALSE.equals(d.get("enabled"))&&Boolean.TRUE.equals(d.get("cleanup_attempt"));
        if(!Set.of("intervention_recorded","verified_pending_commit").contains(t.state)&&!(cleanup&&"reconciliation_unknown".equals(t.state)))throw bad("Recovery observation outside intervention/probe phase");
        if(!replay){Permit p=current.get();if(p==null||p!=t.permit)throw bad("Recovery observation lacks live local permit");if(!cleanup&&event.endsWith(".before"))validatePermit(p,p.localOwner);else if(p.owner!=this||!p.operationId.equals(t.operation))throw bad("Outcome/cleanup permit identity differs");}
        if(event.equals("check.before")&&vacuum.hasRecoveryCheck(t.operation,(String)c.get("nozzle_id")))throw bad("Recovery cannot repeat its native probe");
        Map<String,Object> cap=t.capture();for(String k:List.of("machine_id","bridge_instance_id","config_revision","job_context"))if(!Objects.equals(c.get(k),cap.get(k)))throw bad("Recovery observation context changed");
        if(!Objects.equals(c.get("request_id"),t.record.get("request_id")))throw bad("Recovery request differs");
        Map<String,Object> expected=bindingByNozzle(list(t.intervention,"new_bindings",64)).get(c.get("nozzle_id"));if(expected==null||!expected.equals(NativeVacuumJournal.binding(c,d)))throw bad("Recovery source/binding changed");
        if(!"direct".equals(d.get("native_stage"))||!"part_off".equals(d.get("check_kind")))throw bad("Recovery only permits one native direct part-off probe per nozzle");
        return true;
    }}
    boolean capturedPending(Map<String,Object> context,String observationId)throws IOException {synchronized(vacuum){
        Task t=task(uuid(object(context,"recovery_context"),"task_id"));for(Object item:list(t.capture(),"faults",1024)){Map<String,Object> f=asObject(item);if(observationId.equals(f.get("observation_id")))return true;}return false;
    }}
    WrapperCompletion wrapperCompleted(String taskId,String recoveryOperationId)throws IOException {synchronized(vacuum){
        Task t=task(taskId);if(!Set.of("verified_pending_commit","resolved_for_current_scope").contains(t.state)||t.replayed||!Objects.equals(t.operation,recoveryOperationId)||!completion.completed(t.operation,(String)t.record.get("bridge_instance_id"),false))throw bad("Actual successful wrapper completion required");return new WrapperCompletion(this,t);
    }}
    void activateResolution(String taskId,WrapperCompletion token)throws IOException {synchronized(vacuum){
        Task t=task(taskId);state(t,"resolved_for_current_scope");if(token==null||token.owner!=this||token.used||!token.taskId.equals(taskId)||!Objects.equals(token.operationId,t.operation)||!token.instanceId.equals(t.record.get("bridge_instance_id"))||t.replayed||!completion.completed(t.operation,token.instanceId,false))throw bad("Resolution lacks current native completion authority");
        validateVerification(t,t.verification);vacuum.activateDisposition(t.capture(),(String)t.receipt.get("receipt_id"),list(t.verification,"probes",64));token.used=true;t.liveActivated=true;
    }}
    /** Only the exact captured original operation may cease blocking future work. Its old outcome is never edited. */
    boolean liveDisposesOperation(String operationId,String currentInstance){synchronized(vacuum){
        if(operationId==null||currentInstance==null)return false;
        for(Task t:tasks.values())if(t.liveActivated&&!t.replayed&&"resolved_for_current_scope".equals(t.state)&&currentInstance.equals(t.record.get("bridge_instance_id"))){
            try{for(Object item:list(t.capture(),"faults",1024))if(operationId.equals(asObject(item).get("operation_id")))return true;
                if(list(object(t.capture(),"dependencies"),"operation_ids",256).contains(operationId))return true;
            }catch(IOException impossible){throw new IllegalStateException(impossible);}
        }
        return false;
    }}
    List<Map<String,Object>> summaries(){synchronized(vacuum){
        List<Map<String,Object>> out=new ArrayList<>();for(Task t:tasks.values())try{out.add(freeze(map("task_id",t.id(),"state",t.state,"request_operation_id",t.record.get("request_operation_id"),"recovery_operation_id",t.operation,"fault_set_sha256",t.record.get("fault_set_sha256"),"recovery_kind",t.record.get("recovery_kind"),"historical",t.replayed,"live_resolution_activated",t.liveActivated,"receipt_id",t.receipt==null?null:t.receipt.get("receipt_id"),"unresolved_dependencies",object(t.capture(),"dependencies").get("unresolved_dependencies"))));}catch(IOException impossible){throw new IllegalStateException(impossible);}return Collections.unmodifiableList(out);
    }}
    void revokeAll(){synchronized(vacuum){for(Task t:tasks.values())if(t.permit!=null)t.permit.revoke();}}
    Map<String,Object> snapshot(String taskId)throws IOException {synchronized(vacuum){
        Task t=task(taskId);return freeze(map("profile",PROFILE,"task",t.record,"state",t.state,"recovery_operation_id",t.operation,"local_action",t.action,"intervention",t.intervention,"verification",t.verification,"receipt",t.receipt,"historical",t.replayed,"live_resolution_activated",t.liveActivated,"execution_authority_restored",false));
    }}

    private List<String> originalIds(Task t,String key)throws IOException {
        TreeSet<String> ids=new TreeSet<>();for(Object item:list(t.capture(),"faults",1024)){Object id=asObject(item).get(key);if(id!=null)ids.add(uuidValue((String)id));}
        if(key.equals("operation_id"))for(Object id:list(object(t.capture(),"dependencies"),"operation_ids",256))ids.add((String)id);return Collections.unmodifiableList(new ArrayList<>(ids));
    }
    private Map<String,Object> receiptScope(Task t)throws IOException {
        Map<String,Object>c=t.capture(),job=c.get("job_context")==null?Collections.emptyMap():object(c,"job_context");Map<String,Map<String,Object>> bindings=bindingByNozzle(list(c,"nozzle_bindings",64));TreeMap<String,Object> sources=new TreeMap<>();for(Map<String,Object>b:bindings.values()){Map<String,Object>source=object(b,"source");sources.put((String)source.get("source_id"),source);}
        return freeze(map("nozzle_ids",new ArrayList<>(bindings.keySet()),"job_id",job.get("job_id"),"job_revision",job.get("job_revision"),"board_load_revision",job.get("board_load_revision"),"material_revision",job.get("material_setup_revision"),"lineage_id",job.get("lineage_id"),"lineage_revision",job.get("lineage_revision"),"config_revision",c.get("config_revision"),"native_graph_sha256",c.get("native_graph_sha256"),"original_source_provenance",new ArrayList<>(sources.values())));
    }
    private Map<String,Object> receiptVerification(Task t)throws IOException {
        if(t.verification==null)throw bad("No verified probe receipt");Map<String,Object>v=new LinkedHashMap<>(t.verification);v.put("native_wrapper_completed",true);v.put("native_wrapper_succeeded",true);return freeze(v);
    }
    private void validateIntervention(Task t,Map<String,Object> i)throws IOException {
        keys(i,"old_bindings","new_bindings","native_action_ids","synthetic_latch_before","synthetic_latch_after");Map<String,Map<String,Object>> old=bindingByNozzle(list(i,"old_bindings",64)),next=bindingByNozzle(list(i,"new_bindings",64)),captured=bindingByNozzle(list(t.capture(),"nozzle_bindings",64));
        if(!old.equals(captured)||!next.keySet().equals(old.keySet()))throw bad("Intervention nozzle scope differs");
        for(String n:old.keySet()){Map<String,Object>a=old.get(n),b=next.get(n);for(String k:List.of("machine_id","bridge_instance_id","config_revision","nozzle_id","nozzle_tip_id","sensor_id"))if(!Objects.equals(a.get(k),b.get(k)))throw bad("Intervention changed native graph/configuration");if(Objects.equals(object(a,"source").get("source_id"),object(b,"source").get("source_id")))throw bad("Repair needs fresh source generation");}
        ids(i,"native_action_ids",256,false);Map<String,Object>before=object(i,"synthetic_latch_before"),after=object(i,"synthetic_latch_after");latches(before);latches(after);
        if(!before.equals(after)){
            if(!Set.of("replace-faulted-job-attempt",CONTINUATION_KIND).contains(t.record.get("recovery_kind"))||list(i,"native_action_ids",256).isEmpty()||!"empty".equals(after.get("state")))throw bad("Source repair cannot clear a synthetic material latch");
            for(String k:List.of("original_operation_id","original_action_id","original_part_id"))if(!Objects.equals(before.get(k),after.get(k)))throw bad("Disposal must preserve original material context");
        }
    }
    private void validateVerification(Task t,Map<String,Object> v)throws IOException {
        keys(v,"native_probe_operation_id","probes");if(!Objects.equals(v.get("native_probe_operation_id"),t.operation))throw bad("Probe operation differs");List<?> probes=list(v,"probes",64);Map<String,Map<String,Object>> bindings=bindingByNozzle(list(t.intervention,"new_bindings",64));Set<String> seen=new HashSet<>();
        for(Object item:probes){Map<String,Object> p=asObject(item);keys(p,"nozzle_id","check_observation_id","read_observation_ids","valve_on_id","valve_off_id","native_verdict","exact_current_binding");String n=text(p,"nozzle_id",128);if(!seen.add(n)||!Objects.equals(bindings.get(n),object(p,"exact_current_binding")))throw bad("Probe binding/scope differs");uuid(p,"check_observation_id");uuid(p,"valve_on_id");uuid(p,"valve_off_id");ids(p,"read_observation_ids",32,true);trueValue(p,"native_verdict");vacuum.validateRecoveryProbe(t.operation,p);}
        if(!seen.equals(bindings.keySet()))throw bad("Incomplete shared-source nozzle verification");
    }
    private void validateDispositions(Task t,Map<String,Object> d)throws IOException {
        // Task admission and fresh observations alone cannot attest completed restart loads,
        // native publication or terminal authority. Add resolution only with those owned facts.
        if(RESTART_KIND.equals(t.record.get("recovery_kind")))throw bad("Restart disposition requires separately implemented completed replacement and publication authority");
        if(CONTINUATION_KIND.equals(t.record.get("recovery_kind"))){validateContinuationDispositions(t,d);return;}
        keys(d,"sensing","nozzle_material","feeder_loads","board_loads","replacement_attempt_id","unresolved_dependencies");if(!"fresh-observed-empty".equals(d.get("sensing")))throw bad("Invalid sensing disposition");
        if(!Set.of("none-present","disposed-synthetic").contains(d.get("nozzle_material")))throw bad("Invalid material disposition");ids(d,"unresolved_dependencies",256,false);
        List<?> feeders=list(d,"feeder_loads",64),boards=list(d,"board_loads",64);boolean job="replace-faulted-job-attempt".equals(t.record.get("recovery_kind"));
        if(!job){if(!feeders.isEmpty()||!boards.isEmpty()||d.get("replacement_attempt_id")!=null||!list(d,"unresolved_dependencies",256).isEmpty())throw bad("Standalone recovery cannot disposition loads");}
        else {uuid(d,"replacement_attempt_id");if(feeders.isEmpty()||boards.isEmpty())throw bad("Job recovery requires separate material and board subreceipts");for(Object item:feeders)subreceipt(asObject(item),"material");for(Object item:boards)subreceipt(asObject(item),"board");if(!list(d,"unresolved_dependencies",256).isEmpty())throw bad("Job replacement dependencies unresolved");}
    }
    private void validateRestartObservations(Task t,Map<String,Object> facts)throws IOException {
        keys(facts,"reattachment_id","replacement_attempt_id","restart_capture_sha256","restart_attachment_sha256","prior_process_disposition_id","prior_process_disposition_sha256","observations");
        for(String key:List.of("reattachment_id","replacement_attempt_id","prior_process_disposition_id"))uuid(facts,key);
        for(String key:List.of("restart_capture_sha256","restart_attachment_sha256","prior_process_disposition_sha256"))hash(facts,key);
        Map<String,Object> context=object(t.record,"restart_context");for(String key:List.of("replacement_attempt_id","restart_capture_sha256"))if(!Objects.equals(context.get(key),facts.get(key)))throw bad("Restart observations change the captured replacement");
        List<?> observations=list(facts,"observations",258);int sourceCount=0,boardCount=0,materialCount=0;String previous=null;
        for(Object raw:observations){Map<String,Object> row=asObject(raw);keys(row,"type","receipt_id","payload_sha256");String receipt=uuid(row,"receipt_id");hash(row,"payload_sha256");if(previous!=null&&previous.compareTo(receipt)>=0)throw bad("Restart observation receipts must be unique and sorted");previous=receipt;
            String type=text(row,"type",64);switch(type){case "sensing_source_restart_bootstrap_returned":sourceCount++;break;case "board_restart_observation":boardCount++;break;case "material_restart_observation":materialCount++;break;default:throw bad("Unsupported restart observation type");}
        }
        // Dependency IDs include both historical and successive replacement loads. Only the
        // root authority can match one descriptor to each original captured material row.
        if(sourceCount!=1||boardCount!=1||materialCount==0)throw bad("Restart requires source, material and board observations");
    }
    /** Shape validation only. The independent authority still binds the complete forced
     * continuation/publication receipts and exact captured dependency union. */
    private void validateContinuationDispositions(Task t,Map<String,Object> d)throws IOException {
        keys(d,"sensing","nozzle_material","feeder_loads","board_loads","replacement_attempt_id","unresolved_dependencies","continuation_id","continuation_receipt_sha256","publication_id","publication_receipt_sha256");
        if(!"fresh-observed-empty".equals(d.get("sensing"))||!Set.of("none-present","disposed-synthetic").contains(text(d,"nozzle_material",64)))throw bad("Invalid continuation sensing/material disposition");
        for(String key:List.of("replacement_attempt_id","continuation_id","publication_id"))uuid(d,key);
        for(String key:List.of("continuation_receipt_sha256","publication_receipt_sha256"))hash(d,key);
        if(!Objects.equals(d.get("replacement_attempt_id"),object(t.record,"replacement_context").get("replacement_attempt_id")))throw bad("Continuation replacement attempt differs from task");
        if(!ids(d,"unresolved_dependencies",256,false).isEmpty())throw bad("Continuation dependencies unresolved");
        List<?> feeders=list(d,"feeder_loads",64),boards=list(d,"board_loads",64);if(feeders.isEmpty()||boards.isEmpty())throw bad("Continuation requires separate material and board subreceipts");
        Set<String> oldIds=new HashSet<>(),newIds=new HashSet<>(),receiptIds=new HashSet<>();
        for(Object item:feeders)continuationSubreceipt(asObject(item),"material",oldIds,newIds,receiptIds);
        for(Object item:boards)continuationSubreceipt(asObject(item),"board",oldIds,newIds,receiptIds);
        if(!Collections.disjoint(oldIds,newIds))throw bad("Continuation old/new load identities overlap");
    }
    private static void continuationSubreceipt(Map<String,Object> p,String kind,Set<String> oldIds,Set<String> newIds,Set<String> receiptIds)throws IOException {
        keys(p,"kind","old_load_id","new_load_id","receipt_type","receipt_id","receipt_sha256","old_outcome_preserved");
        if(!kind.equals(p.get("kind"))||!Set.of(kind+"_continuation_adoption",kind+"_continuation_outcome").contains(text(p,"receipt_type",64)))throw bad("Continuation subreceipt kind/type differs");
        String oldId=uuid(p,"old_load_id"),newId=uuid(p,"new_load_id"),receiptId=uuid(p,"receipt_id");hash(p,"receipt_sha256");trueValue(p,"old_outcome_preserved");
        if(oldId.equals(newId)||!oldIds.add(oldId)||!newIds.add(newId)||!receiptIds.add(receiptId))throw bad("Repeated or aliased continuation subreceipt identity");
    }
    private static void subreceipt(Map<String,Object> p,String kind)throws IOException {keys(p,"kind","old_load_id","new_load_id","receipt_id","receipt_sha256","old_outcome_preserved");if(!kind.equals(p.get("kind")))throw bad("Subreceipt kind differs");for(String k:List.of("old_load_id","new_load_id","receipt_id"))uuid(p,k);hash(p,"receipt_sha256");trueValue(p,"old_outcome_preserved");if(p.get("old_load_id").equals(p.get("new_load_id")))throw bad("Load replacement must create new identity");}
    private static void latches(Map<String,Object> p)throws IOException {keys(p,"state","original_operation_id","original_action_id","original_part_id");if(!Set.of("empty","retained","lost","unknown").contains(p.get("state")))throw bad("Unknown synthetic latch");if(p.get("original_operation_id")!=null)uuid(p,"original_operation_id");if(p.get("original_action_id")!=null){String action=nativeActionId(p.get("original_action_id"));if(p.get("original_operation_id")==null||!action.startsWith(p.get("original_operation_id")+"/"))throw bad("Original action has another operation");}if(p.get("original_part_id")!=null)text(p,"original_part_id",128);}
    private static void artifacts(Map<String,Object> p,String k)throws IOException {for(Object item:list(p,k,64)){Map<String,Object>a=asObject(item);keys(a,"artifact_id","sha256","size");uuid(a,"artifact_id");hash(a,"sha256");number(a,"size",0,16*1024*1024);}}
    static Map<String,Map<String,Object>> bindingByNozzle(List<?> values)throws IOException {Map<String,Map<String,Object>> r=new TreeMap<>();for(Object item:values){Map<String,Object>b=asObject(item);NativeVacuumJournal.validateBinding(b);if(r.put(text(b,"nozzle_id",128),b)!=null)throw bad("Repeated nozzle binding");}if(r.isEmpty())throw bad("Empty nozzle scope");return r;}
    static boolean hasDependencies(Map<String,Object> d)throws IOException {for(Object v:d.values())if(!(v instanceof List)||!((List<?>)v).isEmpty())return true;return false;}
    static boolean hasWorkpieceDependencies(Map<String,Object> d)throws IOException {validateDependencies(d);for(String k:List.of("action_ids","board_load_ids","material_load_ids","job_attempt_ids","unresolved_dependencies"))if(!((List<?>)d.get(k)).isEmpty())return true;return false;}
    static String nativeActionId(Object raw)throws IOException {
        if(!(raw instanceof String))throw bad("Native action identifier required");String value=(String)raw;int slash=value.indexOf('/');
        if(slash!=36||!value.substring(slash).matches("/native-action-[1-9][0-9]{0,8}"))throw bad("Exact native action identifier required");uuidValue(value.substring(0,slash));return value;
    }
    static void validateDependencies(Map<String,Object>d)throws IOException {
        keys(d,"action_ids","operation_ids","board_load_ids","material_load_ids","job_attempt_ids","unresolved_dependencies");for(String k:d.keySet())if(!k.equals("action_ids"))ids(d,k,256,false);
        Set<String> seen=new HashSet<>();for(Object raw:list(d,"action_ids",256)){String id=nativeActionId(raw);if(!seen.add(id))throw bad("Repeated native action ID");if(!((List<?>)d.get("operation_ids")).contains(id.substring(0,36)))throw bad("Native action operation absent from dependency union");}
    }
    private Task task(String id)throws IOException {Task t=tasks.get(id);if(t==null)throw bad("Unknown task");return t;}
    private static void state(Task t,String s)throws IOException {if(!s.equals(t.state))throw bad("Invalid transition from "+t.state);}
    private static void operation(Task t,Map<String,Object>p)throws IOException {if(!Objects.equals(t.operation,uuid(p,"recovery_operation_id")))throw bad("Recovery operation differs");}
    private static void reason(Map<String,Object>p)throws IOException {if(!Set.of("scope_stale","cancelled","expired","native_failure","wrapper_unknown","publication_unknown","source_changed","ownership_changed").contains(text(p,"reason",64)))throw bad("Unknown uncertainty reason");}
    private static void header(Map<String,Object>p,String instance)throws IOException {number(p,"schema_version",1,1);if(!PROFILE.equals(p.get("profile")))throw bad("Unknown profile");uuid(p,"machine_id");if(!uuid(p,"bridge_instance_id").equals(uuidValue(instance)))throw bad("Foreign envelope");}
    private static void fields(Map<String,Object>p,String...extra)throws IOException {List<String>k=new ArrayList<>(List.of("schema_version","profile","task_id","machine_id","bridge_instance_id","fault_set_sha256"));k.addAll(Arrays.asList(extra));keys(p,k.toArray(new String[0]));}
    static Map<String,Object> freeze(Map<String,Object>p)throws IOException {if(p==null)throw bad("Object required");Object copy=canonical(p,0,new int[1]);if(JSON.toJson(copy).getBytes(StandardCharsets.UTF_8).length>MAX_RECORD_BYTES)throw bad("Recovery record limit");return asObject(copy);}
    static String digest(Map<String,Object>p)throws IOException {try{byte[] out=MessageDigest.getInstance("SHA-256").digest(JSON.toJson(canonical(p,0,new int[1])).getBytes(StandardCharsets.UTF_8));StringBuilder h=new StringBuilder();for(byte b:out)h.append(String.format("%02x",b&255));return h.toString();}catch(IOException e){throw e;}catch(Exception e){throw new IOException(e);}}
    private static Object canonical(Object x,int depth,int[] count)throws IOException {
        if(depth>32||++count[0]>32768)throw bad("Recovery structure limit");if(x instanceof Map){TreeMap<String,Object>r=new TreeMap<>();for(Map.Entry<?,?>e:((Map<?,?>)x).entrySet()){if(!(e.getKey() instanceof String))throw bad("String key required");validString((String)e.getKey());r.put((String)e.getKey(),canonical(e.getValue(),depth+1,count));}return Collections.unmodifiableMap(r);}
        if(x instanceof List){List<Object>r=new ArrayList<>();for(Object v:(List<?>)x)r.add(canonical(v,depth+1,count));return Collections.unmodifiableList(r);}if(x==null||x instanceof Boolean)return x;if(x instanceof String){validString((String)x);return x;}if(x instanceof Number){try{String raw=x.toString();if(raw.length()>256)throw new NumberFormatException();BigDecimal n=new BigDecimal(raw).stripTrailingZeros();if(Math.abs((long)n.scale())>10000||!Double.isFinite(n.doubleValue()))throw new NumberFormatException();return n;}catch(NumberFormatException e){throw bad("Finite bounded number required");}}throw bad("Non-JSON value");
    }
    static void validString(String s)throws IOException {for(int i=0;i<s.length();i++){char c=s.charAt(i);if(Character.isHighSurrogate(c)){if(++i>=s.length()||!Character.isLowSurrogate(s.charAt(i)))throw bad("Invalid Unicode");}else if(Character.isLowSurrogate(c))throw bad("Invalid Unicode");}}
    static void keys(Map<String,Object>p,String...keys)throws IOException {if(!p.keySet().equals(new HashSet<>(Arrays.asList(keys))))throw bad("Unexpected fields");}
    @SuppressWarnings("unchecked")static Map<String,Object>asObject(Object p)throws IOException {if(!(p instanceof Map))throw bad("Object required");return(Map<String,Object>)p;}
    static Map<String,Object>object(Map<String,Object>p,String k)throws IOException {return asObject(p.get(k));}
    static List<?>list(Map<String,Object>p,String k,int max)throws IOException {if(!(p.get(k) instanceof List)||((List<?>)p.get(k)).size()>max)throw bad("Bounded list required: "+k);return(List<?>)p.get(k);}
    static List<String>ids(Map<String,Object>p,String k,int max,boolean nonempty)throws IOException {List<?>a=list(p,k,max);Set<String>seen=new HashSet<>();List<String>out=new ArrayList<>();for(Object x:a){if(!(x instanceof String))throw bad("Identifier required");String id=uuidValue((String)x);if(!seen.add(id))throw bad("Duplicate identifier");out.add(id);}if(nonempty&&out.isEmpty())throw bad("Nonempty IDs required");return out;}
    static String text(Map<String,Object>p,String k,int max)throws IOException {Object x=p.get(k);if(!(x instanceof String)||((String)x).isBlank()||((String)x).length()>max)throw bad("Bounded text required: "+k);return(String)x;}
    static String uuid(Map<String,Object>p,String k)throws IOException {return uuidValue(text(p,k,36));}
    static String uuidValue(String id)throws IOException {try{if(!UUID.fromString(id).toString().equals(id))throw new IllegalArgumentException();return id;}catch(RuntimeException e){throw bad("Canonical UUID required");}}
    static String hash(Map<String,Object>p,String k)throws IOException {String h=text(p,k,64);if(!h.matches("[a-f0-9]{64}"))throw bad("SHA-256 required");return h;}
    static long number(Map<String,Object>p,String k,long low,long high)throws IOException {try{return NativeJournalJson.integer(p.get(k),low,high);}catch(RuntimeException e){throw bad("Exact bounded integer required: "+k);}}
    static void instant(Map<String,Object>p,String k)throws IOException {try{Instant.parse(text(p,k,40));}catch(RuntimeException e){throw bad("ISO instant required");}}
    static void falseValue(Map<String,Object>p,String k)throws IOException {if(!Boolean.FALSE.equals(p.get(k)))throw bad("False required: "+k);}
    static void trueValue(Map<String,Object>p,String k)throws IOException {if(!Boolean.TRUE.equals(p.get(k)))throw bad("True required: "+k);}
    static Map<String,Object>map(Object...v){Map<String,Object>p=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)p.put((String)v[i],v[i+1]);return p;}
    static IOException bad(String message){return new IOException("Sensing reconciliation: "+message);}
}
