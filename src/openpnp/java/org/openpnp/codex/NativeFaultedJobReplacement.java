/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.spi.*;

/** Private simulator replacement adapter. Source facts come only from the same integrity-checked,
 * forced operation/action/load/lineage stream. A receipt never grants source, occupancy, processor
 * admission or wrapper-completion authority. The original native Job is never initialized or reset.
 * The owning Bridge must serialize observe/replay, capture, native actions and append/force calls.
 */
final class NativeFaultedJobReplacement implements AutoCloseable {
    interface Sink {void appendAndForce(String type,Map<String,Object> payload)throws Exception;}
    interface TerminalAuthority {boolean completed(String operationId)throws Exception;}
    interface JobAuthority {boolean installed(Job exactJob,String exactAttemptId)throws Exception;}
    /** Host-owned proof of the current exclusive journal lock and entered native callback.
     * It says nothing about prior OS process death or completion of an old native callback. */
    interface RestartAuthority {void requireOwnership(String currentOperationId,String currentInstanceId,String configRevision,Job exactOriginal,String originalJobId,Map<String,Object> exactPriorOperations)throws Exception;}
    private static final Gson JSON=new GsonBuilder().serializeNulls().create();
    private static final int MAX_OPERATIONS=10000,MAX_EVENTS=250000,MAX_REPLACEMENTS=128;
    private final Configuration config;private final Machine machine;private final NativeMaterialLoads material;
    private final NativeBoardLoads boards;private final NativeJobLineage lineage;private final Sink sink;
    private final TerminalAuthority terminalAuthority;
    private final JobAuthority jobAuthority;private final RestartAuthority restartAuthority;
    private final Map<String,Map<String,Object>> operations=new LinkedHashMap<>(),intents=new LinkedHashMap<>(),receipts=new LinkedHashMap<>();
    private final Map<String,List<Map<String,Object>>> actionEvents=new LinkedHashMap<>();
    private final Map<String,NativeActionLedger.Replay> actionReplays=new LinkedHashMap<>();
    private final Set<String> observedEventIds=new HashSet<>();private boolean faulted;private long generation;
    private final Map<String,Permit> livePermits=new HashMap<>();
    private final Map<String,NativeReplacementJob.Candidate> candidates=new HashMap<>();
    private final Map<String,Map<String,Object>> definitions=new LinkedHashMap<>(),publicationIntents=new LinkedHashMap<>(),publications=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> documents=new LinkedHashMap<>();
    private final Map<String,Capture> retainedCaptures=new HashMap<>();
    private final Map<String,RestartStage> restartStages=new HashMap<>();
    private final Map<String,Map<String,Object>> restartAttachments=new LinkedHashMap<>(),restartObservations=new LinkedHashMap<>();
    private final Map<String,RestartPermit> liveRestarts=new HashMap<>(),retainedRestarts=new HashMap<>();
    private final Map<String,NativeReplacementDocuments.Reconstructed> attachedGraphs=new HashMap<>();
    private final Map<String,Map<String,Object>> restartPriorDispositions=new LinkedHashMap<>();
    private final Map<String,RestartPermit> priorDispositionWitnesses=new HashMap<>();
    private final Map<String,Map<String,Object>> continuationIntents=new LinkedHashMap<>(),continuationAdoptions=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationStepIntents=new LinkedHashMap<>(),continuationStepOutcomes=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationReceipts=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationDocumentIntents=new LinkedHashMap<>(),continuationDocuments=new LinkedHashMap<>(),continuationDefinitions=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationPublicationIntents=new LinkedHashMap<>(),continuationPublications=new LinkedHashMap<>();
    private final Map<String,Publication> livePublications=new HashMap<>(),publishedWitnesses=new HashMap<>();
    private final Map<String,ContinuationPermit> liveContinuations=new HashMap<>();
    private boolean closed;
    static final class Capture {
        final Map<String,Object> payload;final String digest;private final NativeFaultedJobReplacement owner;
        private final Job oldJob;private final Map<String,Boolean> oldHistory;
        private Capture(NativeFaultedJobReplacement owner,Job job,Map<String,Object> payload)throws Exception {
            this.owner=owner;oldJob=job;this.payload=frozen(payload);digest=digest(payload);oldHistory=Collections.unmodifiableMap(new TreeMap<>(job.getPlacedStatusSnapshot()));
        }
        Map<String,Object> dependencies()throws IOException{return object(payload.get("dependencies"));}
    }
    /** Composes the coordinator's exact process-local permit. No boolean or client DTO can mint it. */
    static final class Permit implements AutoCloseable {
        private final NativeFaultedJobReplacement owner;private final Capture capture;
        private final NativeSensingReconciliation coordinator;private final NativeSensingReconciliation.Permit recovery;
        private final Object localOwner;private final String faultDigest,attemptId;private final Job freshJob;private boolean closed;
        private Permit(NativeFaultedJobReplacement owner,Capture capture,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner,String faultDigest,Job fresh,String attempt){this.owner=owner;this.capture=capture;this.coordinator=coordinator;this.recovery=recovery;this.localOwner=localOwner;this.faultDigest=faultDigest;freshJob=fresh;attemptId=attempt;}
        void check()throws Exception {owner.nativeOwner();if(closed||owner.faulted||owner.livePermits.get(attemptId)!=this)throw bad("Replacement permit is closed or foreign");coordinator.requirePermit(recovery,localOwner);if(!capture.oldHistory.equals(capture.oldJob.getPlacedStatusSnapshot()))throw bad("Original native placed history changed during replacement");NativeBoardLoads.requireDetachedReplacement(capture.oldJob,freshJob);}
        String attemptId(){return attemptId;}
        private Map<String,Object> authority()throws Exception {check();return map("recovery_operation_id",recovery.operationId(),"fault_set_sha256",faultDigest);}
        Map<String,Object> authorizeMaterial(NativeMaterialLoads ledger,String oldLoadId)throws Exception {
            Map<String,Object> out=authority();if(ledger!=owner.material||!((List<?>)capture.dependencies().get("material_load_ids")).contains(oldLoadId))throw bad("Material load not captured by this permit");
            Map<String,Object> materials=object(capture.payload.get("material")),old=object(object(materials.get("loads")).get(oldLoadId));if(!object(materials.get("active")).containsValue(oldLoadId))throw bad("Historical inactive material requires its own disposition");
            Map<String,Object> pending=new TreeMap<>();for(Map.Entry<String,Object> e:object(materials.get("pending_feeds")).entrySet())if(oldLoadId.equals(object(object(e.getValue()).get("material_load")).get("load_id")))pending.put(e.getKey(),e.getValue());
            out.put("old_load",old);out.put("pending_feeds",pending);return frozen(out);
        }
        Map<String,Object> authorizeBoards(NativeBoardLoads ledger,Job old,Job fresh,String newJobId)throws Exception {
            Map<String,Object> out=authority();if(ledger!=owner.boards||old!=capture.oldJob||fresh!=freshJob||!attemptId.equals(newJobId))throw bad("Foreign native board replacement");out.put("old_boards",capture.payload.get("boards"));return frozen(out);
        }
        Map<String,Object> authorizeLineage(NativeJobLineage ledger,String newJobId)throws Exception {Map<String,Object> out=authority();if(ledger!=owner.lineage||!attemptId.equals(newJobId))throw bad("Foreign attempt lineage");out.put("old_lineage",capture.payload.get("lineage"));return frozen(out);}
        public void close(){closed=true;owner.livePermits.remove(attemptId);}
    }
    /** Owns reconstructed, inert graphs while a new local restart decision is being prepared.
     * This object is not a Permit and cannot bind loads, publish a Job or admit a native action. */
    static final class RestartStage implements AutoCloseable {
        private final NativeFaultedJobReplacement owner;private final String attempt;
        private final Map<String,Object> intent,progress;private final NativeReplacementDocuments.Reconstructed graphs;
        private boolean closed;
        private RestartStage(NativeFaultedJobReplacement owner,String attempt,Map<String,Object> intent,Map<String,Object> progress,NativeReplacementDocuments.Reconstructed graphs){this.owner=owner;this.attempt=attempt;this.intent=intent;this.progress=progress;this.graphs=graphs;}
        Job original(){if(closed)throw new IllegalStateException("Restart reconstruction is closed");return graphs.original();}
        NativeReplacementJob.Candidate candidate(){if(closed)throw new IllegalStateException("Restart reconstruction is closed");return graphs.candidate();}
        void requireCurrent()throws Exception {
            owner.nativeOwner();
            if(closed||owner.restartStages.get(attempt)!=this||owner.machine.isEnabled()||owner.retainedCaptures.containsKey(attempt)||owner.candidates.containsKey(attempt))throw bad("Restart reconstruction is closed, changed or already attached");
            if(!same(intent,owner.intents.get(attempt))||!same(progress,owner.progress(attempt)))throw bad("Restart transaction changed after reconstruction");
            graphs.requireCurrent();
            if(!same(NativeBoardLoads.replacementModel(graphs.original()),boardModels(object(object(intent.get("capture")).get("boards")))))throw bad("Reconstructed original no longer matches the recorded load graph");
        }
        public void close(){if(!closed){closed=true;owner.restartStages.remove(attempt,this);graphs.close();}}
    }
    /** A current native stage and exact historical facts, prepared for a new local decision. */
    static final class RestartCapture {
        final Map<String,Object> payload;final String digest;
        private final NativeFaultedJobReplacement owner;private final RestartStage stage;
        private RestartCapture(NativeFaultedJobReplacement owner,RestartStage stage,Map<String,Object> facts)throws Exception {this.owner=owner;this.stage=stage;payload=frozen(facts);digest=digest(payload);}
        Map<String,Object> dependencies()throws IOException{return object(payload.get("dependencies"));}
        Map<String,Object> jobContext()throws IOException{return object(payload.get("job_context"));}
    }
    /** Authorizes fresh observations of reattached inactive graphs. It grants no job execution,
     * source clearance, physical inventory or completion of a prior process's callbacks. */
    static final class RestartPermit implements AutoCloseable {
        private final NativeFaultedJobReplacement owner;private final RestartCapture capture;
        private final NativeSensingReconciliation coordinator;private final NativeSensingReconciliation.Permit recovery;
        private final Object localOwner;private final String id,attempt,faultDigest;private boolean closed,hostAttached;private Map<String,Object> observationFacts;
        private final Map<String,Map<String,Object>> accepted=new HashMap<>();
        private RestartPermit(NativeFaultedJobReplacement owner,RestartCapture capture,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner,String id,String faultDigest){this.owner=owner;this.capture=capture;this.coordinator=coordinator;this.recovery=recovery;this.localOwner=localOwner;this.id=id;attempt=capture.stage.attempt;this.faultDigest=faultDigest;}
        String reattachmentId(){return id;}String attemptId(){return attempt;}
        Job original(){return capture.stage.graphs.original();}NativeReplacementJob.Candidate candidate(){return capture.stage.graphs.candidate();}
        private void requireRetained()throws Exception {
            owner.nativeOwner();
            if(owner.retainedRestarts.get(attempt)!=this||owner.attachedGraphs.get(attempt)!=capture.stage.graphs||owner.candidates.get(attempt)!=candidate()||owner.retainedCaptures.get(attempt)==null||owner.retainedCaptures.get(attempt).oldJob!=original()||!owner.restartAttachments.containsKey(id))throw bad("Restart graph attachment is absent or changed");
            capture.stage.graphs.requireCurrent();
        }
        void check()throws Exception {
            requireRetained();if(closed||owner.liveRestarts.get(attempt)!=this||owner.machine.isEnabled())throw bad("Restart observation permit is closed, foreign or enabled");
            coordinator.requirePermit(recovery,localOwner);owner.requireSameActiveAdmission(object(owner.restartAttachments.get(id).get("local_admission")));
            if(!same(durableContinuationCapture(capture.payload),durableContinuationCapture(owner.restartFacts(attempt,id))))throw bad("Restart dependency facts changed after the local decision");
        }
        private Map<String,Object> authority()throws Exception {
            check();Map<String,Object> original=owner.intents.get(attempt);
            return map("reattachment_id",id,"replacement_attempt_id",attempt,"recovery_operation_id",recovery.operationId(),"fault_set_sha256",faultDigest,"restart_capture_sha256",capture.digest,"original_recovery_operation_id",original.get("recovery_operation_id"),"original_fault_set_sha256",original.get("fault_set_sha256"));
        }
        Map<String,Object> authorizeMaterial(NativeMaterialLoads ledger,String oldLoadId)throws Exception {
            Map<String,Object> result=authority();if(ledger!=owner.material)throw bad("Foreign restart material ledger");
            result.put("old_material",object(object(owner.intents.get(attempt).get("capture")).get("material")));result.put("phase_row",progressRow(object(capture.payload.get("progress")),"material",oldLoadId));return frozen(result);
        }
        Map<String,Object> authorizeBoards(NativeBoardLoads ledger,Job old,Job fresh,String replacementJobId)throws Exception {
            Map<String,Object> result=authority();if(ledger!=owner.boards||old!=original()||fresh!=candidate().job()||!attempt.equals(replacementJobId))throw bad("Foreign restart board graph");
            result.put("old_boards",object(object(owner.intents.get(attempt).get("capture")).get("boards")));result.put("board_progress",object(capture.payload.get("progress")).get("boards"));return frozen(result);
        }
        Map<String,Object> authorizeSourceBootstrap(Configuration config,Map<String,Object> attestation)throws Exception {
            Map<String,Object> result=authority();if(config!=owner.config)throw bad("Foreign restart source configuration");Map<String,Object> history=new TreeMap<>();
            for(Object row:object(capture.payload.get("prior_operations")).values()){
                Map<String,Object> operation=object(row);String taskId=uuid(operation.get("task_id"));Map<String,Object> task=object(coordinator.snapshot(taskId).get("task"));
                if(!Objects.equals(operation.get("request_digest"),task.get("fault_set_sha256"))||!Objects.equals(operation.get("reconciliation_request_id"),task.get("request_id")))throw bad("Restart source history differs from the recorded local recovery task");
                history.put(taskId,task.get("fault_set"));
            }
            result.put("historical_fault_sets",history);result.put("attestation",frozen(attestation));result.put("attestation_sha256",digest(attestation));return frozen(result);
        }
        void acceptForcedEvent(String type,Map<String,Object> payload)throws Exception {
            check();String receipt=uuid(payload.get("receipt_id"));Map<String,Object> event=owner.restartObservations.get(receipt);
            if(event==null||!type.equals(event.get("type"))||!same(payload,event.get("payload"))||accepted.containsKey(receipt))throw bad("Restart observation has no exact new forced receipt");accepted.put(receipt,event);
        }
        void requireForcedSourceBootstrapIntent(Map<String,Object> payload)throws Exception {
            check();Map<String,Object> event=owner.restartObservations.get(uuid(payload.get("bootstrap_id")));
            if(event==null||!"sensing_source_restart_bootstrap_intent".equals(event.get("type"))||!same(payload,event.get("payload"))||!id.equals(payload.get("reattachment_id")))throw bad("Restart source installation requires its exact committed forced intent");
        }
        Job originalForHost()throws Exception {check();owner.requireSealedPriorDisposition(this);return original();}
        void confirmOriginalHostAttachment()throws Exception {
            check();owner.requireSealedPriorDisposition(this);
            if(!owner.jobAuthority.installed(original(),(String)capture.jobContext().get("job_id")))throw bad("Host did not attach the exact inactive original Job and identity");
            hostAttached=true;
        }
        void requireSealedMaterial(NativeMaterialLoads ledger,String oldLoadId,String receiptId)throws Exception {
            requireRetained();if(ledger!=owner.material)throw bad("Foreign restart material witness");Map<String,Object> event=accepted.get(uuid(receiptId));
            if(event==null||!"material_restart_observation".equals(event.get("type"))||!oldLoadId.equals(object(event.get("payload")).get("old_load_id")))throw bad("No exact current-process restart material observation");
        }
        void requireSealedBoards(NativeBoardLoads ledger,Job old,Job fresh,String replacementJobId,String receiptId)throws Exception {
            requireRetained();if(ledger!=owner.boards||old!=original()||fresh!=candidate().job()||!attempt.equals(replacementJobId))throw bad("Foreign restart board witness");Map<String,Object> event=accepted.get(uuid(receiptId));
            if(event==null||!"board_restart_observation".equals(event.get("type")))throw bad("No exact current-process restart board observation");
        }
        public void close(){closed=true;owner.liveRestarts.remove(attempt,this);}
    }
    /** A new local decision captures the existing transaction, not another original-job request.
     * Retained native identity is deliberately process-local; journal replay cannot create it. */
    static final class ContinuationCapture {
        final Map<String,Object> payload;final String digest;
        private final NativeFaultedJobReplacement owner;private final Capture original;
        private final NativeReplacementJob.Candidate candidate;
        private ContinuationCapture(NativeFaultedJobReplacement owner,Capture original,NativeReplacementJob.Candidate candidate,Map<String,Object> payload)throws Exception {this.owner=owner;this.original=original;this.candidate=candidate;this.payload=frozen(payload);digest=digest(payload);}
        Map<String,Object> dependencies()throws IOException{return object(payload.get("dependencies"));}
        Map<String,Object> jobContext()throws IOException{return object(payload.get("job_context"));}
    }
    static final class ContinuationPermit implements AutoCloseable {
        private final NativeFaultedJobReplacement owner;private final ContinuationCapture capture;
        private final NativeSensingReconciliation coordinator;private final NativeSensingReconciliation.Permit recovery;
        private final Object localOwner;private final String id,attempt,faultDigest;private boolean closed;private Map<String,Object> expectedProgress;private Step activeStep;
        private ContinuationPermit(NativeFaultedJobReplacement owner,ContinuationCapture capture,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner,String id,String faultDigest)throws Exception {this.owner=owner;this.capture=capture;this.coordinator=coordinator;this.recovery=recovery;this.localOwner=localOwner;this.id=id;attempt=(String)capture.payload.get("replacement_attempt_id");this.faultDigest=faultDigest;expectedProgress=object(capture.payload.get("progress"));}
        String continuationId(){return id;}String attemptId(){return attempt;}
        private void checkIdentity()throws Exception {
            owner.nativeOwner();if(closed||owner.liveContinuations.get(attempt)!=this||owner.liveRestarts.containsKey(attempt))throw bad("Closed, foreign or conflicting continuation permit");coordinator.requirePermit(recovery,localOwner);
            owner.requireRetainedContinuation(attempt,capture.original.oldJob);if(owner.candidates.get(attempt)!=capture.candidate)throw bad("Continuation candidate identity changed");
            owner.requireSameActiveAdmission(object(owner.continuationIntents.get(id).get("local_admission")));
            for(Map.Entry<String,Object> row:object(capture.payload.get("prior_operations")).entrySet())if(!same(row.getValue(),owner.operations.get(row.getKey()))||!owner.priorRecoveryCompleted(attempt,row.getKey(),false,(String)object(owner.continuationIntents.get(id).get("local_admission")).get("bridge_instance_id")))throw bad("Prior local recovery outcome or wrapper changed");
        }
        void check()throws Exception {checkIdentity();if(!same(expectedProgress,owner.progress(attempt)))throw bad("Continuation dependency phases changed after the local decision");}
        private Map<String,Object> authority()throws Exception {check();owner.requireAdoptionState();if(owner.continuationReceipts.containsKey(id))throw bad("Continuation steps are already sealed by their compound receipt");Map<String,Object> intent=owner.intents.get(attempt);return map("continuation_id",id,"replacement_attempt_id",attempt,"recovery_operation_id",recovery.operationId(),"fault_set_sha256",faultDigest,"continuation_capture_sha256",capture.digest,"original_recovery_operation_id",intent.get("recovery_operation_id"),"original_fault_set_sha256",intent.get("fault_set_sha256"));}
        Map<String,Object> authorizeMaterial(NativeMaterialLoads ledger,String oldLoadId)throws Exception {Map<String,Object> out=authority();if(ledger!=owner.material)throw bad("Foreign continuation material ledger");out.put("phase_row",progressRow(expectedProgress,"material",oldLoadId));return frozen(out);}
        Map<String,Object> authorizeBoards(NativeBoardLoads ledger,Job old,Job fresh,String replacementJobId)throws Exception {Map<String,Object> out=authority();if(ledger!=owner.boards||old!=capture.original.oldJob||fresh!=capture.candidate.job()||!attempt.equals(replacementJobId))throw bad("Foreign continuation board graph");out.put("old_boards",capture.original.payload.get("boards"));out.put("board_progress",expectedProgress.get("boards"));return frozen(out);}
        Map<String,Object> authorizeLineage(NativeJobLineage ledger,String replacementJobId)throws Exception {Map<String,Object> out=authority();if(ledger!=owner.lineage||!attempt.equals(replacementJobId))throw bad("Foreign continuation lineage");out.put("old_lineage",capture.original.payload.get("lineage"));out.put("phase_row",expectedProgress.get("lineage"));return frozen(out);}
        Step beginMaterialStep(NativeMaterialLoads ledger,String oldLoadId)throws Exception {Map<String,Object> a=authorizeMaterial(ledger,oldLoadId);return beginStep("material",oldLoadId,a);}
        Step beginLineageStep(NativeJobLineage ledger,String replacementJobId)throws Exception {Map<String,Object> a=authorizeLineage(ledger,replacementJobId);return beginStep("lineage",uuid(object(a.get("old_lineage")).get("job_id")),a);}
        Step beginBoardStep(NativeBoardLoads ledger,Job old,Job fresh,String replacementJobId,String oldLoadId)throws Exception {Map<String,Object> a=mutable(authorizeBoards(ledger,old,fresh,replacementJobId));if(!"completed".equals(effectivePhase(object(expectedProgress.get("lineage")))))throw bad("Create the replacement lineage before continuing board loads");a.remove("board_progress");a.put("phase_row",progressRow(expectedProgress,"boards",oldLoadId));return beginStep("boards",oldLoadId,a);}
        private Step beginStep(String kind,String oldLoadId,Map<String,Object> supplied)throws Exception {
            check();if(activeStep!=null)throw bad("A continuation step is already active");Map<String,Object> a=mutable(supplied),row=object(a.get("phase_row"));if(!Set.of("untouched","pending").contains(effectivePhase(row)))throw bad("Completed replacement requires adoption, not another effect");
            String stepId=UUID.randomUUID().toString();a.put("phase_row",durablePhase(row));a.put("phase_row_sha256",continuationPhaseDigest(row));a.put("step_id",stepId);return activeStep=new Step(this,kind,uuid(oldLoadId),stepId,frozen(a));
        }
        public void close(){if(closed)return;closed=true;owner.liveContinuations.remove(attempt,this);for(Publication p:new ArrayList<>(owner.livePublications.values()))if(p.parent==this)p.close();}
    }
    /** One effectful step, granted only by a current local continuation. Its progress may advance
     * only after the exact corresponding record was forced and the native component committed it. */
    static final class Step implements AutoCloseable {
        private final ContinuationPermit parent;private final String kind,oldLoadId,id;private final Map<String,Object> authority;
        private boolean intentAccepted,outcomeAccepted,closed;
        private Step(ContinuationPermit parent,String kind,String oldLoadId,String id,Map<String,Object> authority){this.parent=parent;this.kind=kind;this.oldLoadId=oldLoadId;this.id=id;this.authority=authority;}
        String stepId(){return id;}Map<String,Object> authority()throws Exception {check();return authority;}
        void check()throws Exception {if(closed||parent.activeStep!=this)throw bad("Closed or foreign continuation step");parent.check();parent.owner.requireAdoptionState();}
        void acceptForcedEvent(String type,Map<String,Object> payload)throws Exception {
            if(closed||parent.activeStep!=this)throw bad("Continuation step is closed");parent.checkIdentity();parent.owner.requireAdoptionState();
            if(kind.equals("lineage")){
                Map<String,Object> recorded=parent.owner.continuationStepOutcomes.get(id);
                if(!type.equals("job_lineage_continuation_replacement")||intentAccepted||outcomeAccepted||!id.equals(payload.get("step_id"))||!id.equals(payload.get("receipt_id"))||!oldLoadId.equals(payload.get("replaces_attempt"))||recorded==null||!type.equals(recorded.get("type"))||!same(payload,recorded.get("payload")))throw bad("Lineage continuation has no exact single forced event");
                Map<String,Object> next=parent.owner.progress(parent.attempt);validateLineageProgress(parent.expectedProgress,next,payload);parent.expectedProgress=frozen(next);check();outcomeAccepted=true;return;
            }
            boolean outcome=type.endsWith("_outcome");String prefix=kind.equals("material")?"material":"board";
            if(!type.equals(prefix+"_continuation_"+(outcome?"outcome":"intent"))||!id.equals(payload.get("step_id"))||!id.equals(payload.get("receipt_id"))||!oldLoadId.equals(payload.get("old_load_id"))||outcomeAccepted||outcome&&!intentAccepted||!outcome&&intentAccepted)throw bad("Continuation step event is repeated or foreign");
            Map<String,Object> recorded=(outcome?parent.owner.continuationStepOutcomes:parent.owner.continuationStepIntents).get(id);
            if(recorded==null||!type.equals(recorded.get("type"))||!same(payload,recorded.get("payload")))throw bad("Continuation step has no exact forced event");
            Map<String,Object> next=parent.owner.progress(parent.attempt);validateStepProgress(parent.expectedProgress,next,kind,oldLoadId,payload,outcome);
            parent.expectedProgress=frozen(next);check();if(outcome)outcomeAccepted=true;else intentAccepted=true;
        }
        public void close(){if(closed)return;closed=true;if(parent.activeStep==this)parent.activeStep=null;if(!outcomeAccepted)parent.close();}
    }
    /** Process-local publication capability. Its durable records never recreate native bindings.
     * Board readiness survives successful local permit closure, but never an uncertain publication. */
    static final class Publication implements AutoCloseable {
        private final NativeFaultedJobReplacement owner;private final ContinuationPermit parent;
        private final String id;private final Map<String,Object> record;private boolean closed,successful;
        private Publication(NativeFaultedJobReplacement owner,ContinuationPermit parent,String id,Map<String,Object> record){this.owner=owner;this.parent=parent;this.id=id;this.record=record;}
        String publicationId(){return id;}String attemptId(){return parent.attempt;}
        void check()throws Exception {
            if(closed||owner.livePublications.get(id)!=this)throw bad("Publication capability is closed or foreign");parent.checkIdentity();owner.requireAdoptionState();owner.material.requireReady();
            Map<String,Object> current=owner.progress(parent.attempt),sealed=owner.continuationReceipts.get(parent.id);
            if(sealed==null||!same(sealed,owner.continuationOutcomeRecord(owner.continuationIntents.get(parent.id),current))||!digest(sealed).equals(record.get("continuation_outcome_sha256"))||!id.equals(owner.latestContinuationPublication(parent.attempt)))throw bad("Publication lost its exact sealed continuation or current ownership");
        }
        Map<String,Object> authorizeBoards(NativeBoardLoads ledger,Job oldJob,Job freshJob,String attempt)throws Exception {
            check();if(ledger!=owner.boards||oldJob!=parent.capture.original.oldJob||freshJob!=parent.capture.candidate.job()||!parent.attempt.equals(attempt))throw bad("Foreign native continuation publication graph");
            return frozen(map("publication_id",id,"continuation_id",parent.id,"replacement_attempt_id",attempt,"continuation_outcome_sha256",record.get("continuation_outcome_sha256"),"board_progress",owner.progress(attempt).get("boards"),"old_boards",parent.capture.original.payload.get("boards")));
        }
        boolean bindingReady(NativeBoardLoads ledger,Job job,String attempt){try{return successful&&!owner.closed&&!owner.faulted&&ledger==owner.boards&&job==parent.capture.candidate.job()&&parent.attempt.equals(attempt)&&id.equals(owner.latestContinuationPublication(attempt))&&owner.publishedWitnesses.get(id)==this&&owner.continuationPublications.containsKey(id)&&owner.jobAuthority.installed(job,attempt);}catch(Exception failure){return false;}}
        /** Read-only completion-thread access after the actual native wrapper ended. This does
         * not restore the expired effect permit or authorize another native submission. */
        void authorizeCompletedRead(Object component)throws Exception {
            if(component!=owner.material&&component!=owner.boards&&component!=parent.capture.candidate)throw bad("Foreign publication component");
            if(Configuration.get()!=owner.config||owner.config.getMachine()!=owner.machine||owner.machine.isBusy()||!bindingReady(owner.boards,parent.capture.candidate.job(),parent.attempt)||!owner.terminalAuthority.completed(parent.recovery.operationId()))throw bad("Exact quiescent completed native publication required");
            Map<String,Object> op=owner.operations.get(parent.recovery.operationId());
            if(op==null||!"succeeded".equals(op.get("state"))||!Boolean.TRUE.equals(object(op.get("native_completion")).get("native_wrapper_completed"))||!Boolean.TRUE.equals(object(op.get("native_completion")).get("native_wrapper_succeeded")))throw bad("Native publication wrapper did not succeed");
            owner.requireAdoptionState();
        }
        public void close(){if(closed)return;closed=true;owner.livePublications.remove(id,this);if(!successful)parent.close();}
    }
    static String effectivePhase(Map<String,Object> row)throws IOException {Object phase=row.getOrDefault("effective_phase",row.get("phase"));if(!(phase instanceof String)||!Set.of("untouched","pending","completed").contains(phase))throw bad("Invalid effective continuation phase");return(String)phase;}
    static Map<String,Object> durablePhase(Map<String,Object> row)throws IOException {Map<String,Object> result=mutable(row);result.remove("binding_status");return frozen(result);}
    static String continuationPhaseDigest(Map<String,Object> row)throws IOException {return digest(durablePhase(row));}
    private static void validateLineageProgress(Map<String,Object> previous,Map<String,Object> current,Map<String,Object> record)throws Exception {
        Map<String,Object> before=mutable(previous),after=mutable(current),old=object(before.remove("lineage")),fresh=object(after.remove("lineage"));
        if(!same(before,after)||!"untouched".equals(effectivePhase(old))||!"completed".equals(effectivePhase(fresh)))throw bad("Lineage continuation changed an unrelated stage or repeated retirement");
        for(Map.Entry<String,Object> field:old.entrySet())if(!same(field.getValue(),fresh.get(field.getKey())))throw bad("Lineage continuation rewrote original phase facts");
        if(!same(record,fresh.get("effective_receipt"))||!Objects.equals(record.get("receipt_id"),fresh.get("effective_receipt_id"))||!digest(record).equals(fresh.get("effective_receipt_sha256")))throw bad("Lineage continuation did not commit its exact receipt");
    }
    private static void validateStepProgress(Map<String,Object> previous,Map<String,Object> current,String kind,String oldId,Map<String,Object> record,boolean outcome)throws Exception {
        Map<String,Object> before=mutable(previous),after=mutable(current),beforeStage=object(before.remove(kind)),afterStage=object(after.remove(kind));if(!same(before,after))throw bad("Continuation step changed an unrelated dependency stage");
        Map<String,Object> oldRow=progressRow(previous,kind,oldId),newRow=progressRow(current,kind,oldId);
        List<?> oldRows=(List<?>)beforeStage.remove("rows"),newRows=(List<?>)afterStage.remove("rows");if(oldRows.size()!=newRows.size())throw bad("Continuation changed original load union");
        for(Object raw:oldRows){Map<String,Object> row=object(raw);if(!oldId.equals(row.get("old_load_id"))&&!same(row,progressRow(current,kind,uuid(row.get("old_load_id")))))throw bad("Continuation step changed another load");}
        for(String key:List.of("material_setup_revision","board_load_revision","effective_counts")){beforeStage.remove(key);afterStage.remove(key);}if(!same(beforeStage,afterStage))throw bad("Continuation step changed unrelated ledger metadata");
        for(String key:List.of("old_load_id","old_load","old_feed_facts","phase","parent_intent","parent_outcome","new_load","root_instance_id","feeder_id"))if(!same(oldRow.get(key),newRow.get(key)))throw bad("Continuation step rewrote original load facts");
        Map<String,Object> fresh=object(record.get("new_load"));if(!effectivePhase(newRow).equals(outcome?"completed":"pending")||!same(fresh,newRow.get("effective_new_load"))||!Objects.equals(fresh.get("load_id"),newRow.get("current_load_id"))||!same(fresh,newRow.get("current_load")))throw bad("Continuation step did not establish its exact forced new load phase");
    }
    private Capture requireRetainedContinuation(String attempt,Job oldJob)throws Exception {
        nativeOwner();Capture original=retainedCaptures.get(uuid(attempt));if(original==null||original.oldJob!=oldJob)throw bad("Continuation requires exact retained original Job; restart needs explicit reattachment");
        if(!original.oldHistory.equals(oldJob.getPlacedStatusSnapshot()))throw bad("Original placed history changed before continuation");
        NativeReplacementJob.Candidate candidate=retainedCandidate(attempt);NativeBoardLoads.requireDetachedReplacement(oldJob,candidate.job());return original;
    }
    private void requireAdoptionState()throws Exception {
        if(machine.isEnabled())throw bad("Disable the simulator before adopting completed replacement steps");
        for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles())if(nozzle.getPart()!=null)throw bad("Resolve nozzle material before adopting completed replacement steps");
    }
    private void requireSameActiveAdmission(Map<String,Object> expected)throws Exception {
        String id=uuid(expected.get("operation_id"));Map<String,Object> actual=operations.get(id);
        if(actual==null||!"local_native_sensing_reconciliation".equals(actual.get("method"))||!Set.of("accepted","running").contains(actual.get("state")))throw bad("Continuation local operation is no longer active");
        for(String key:List.of("operation_id","request_id","method","task_id","bridge_instance_id","config_revision","request_digest","reconciliation_request_id"))if(!Objects.equals(expected.get(key),actual.get(key)))throw bad("Continuation local admission changed");
    }
    ContinuationCapture captureContinuation(String attempt,Job oldJob)throws Exception {
        Capture original=requireRetainedContinuation(attempt,oldJob);if(livePermits.containsKey(attempt)||liveContinuations.containsKey(attempt)||liveRestarts.containsKey(attempt))throw bad("An earlier replacement or restart permit remains live");
        Map<String,Object> facts=continuationFacts(attempt);for(String id:object(facts.get("prior_operations")).keySet())if(!priorRecoveryCompleted(attempt,id,false,null))throw bad("Prior native recovery wrapper is not independently complete or explicitly disposed");
        return new ContinuationCapture(this,original,retainedCandidate(attempt),facts);
    }
    /** The host may still hold the original job or already hold the unpublished/uncertain
     * candidate. Resolve only exact retained identities; history cannot create this binding. */
    ContinuationCapture captureContinuationForHost(String attempt)throws Exception {
        nativeOwner();Capture original=retainedCaptures.get(uuid(attempt));if(original==null)throw bad("Restart requires explicit candidate reattachment");
        NativeReplacementJob.Candidate candidate=retainedCandidate(attempt);
        if(!jobAuthority.installed(original.oldJob,(String)original.payload.get("job_id"))&&!jobAuthority.installed(candidate.job(),attempt))throw bad("Host job is outside this retained replacement transaction");
        return captureContinuation(attempt,original.oldJob);
    }
    Set<String> continuationOperationIds(String attempt)throws Exception {
        Map<String,Object> original=intents.get(uuid(attempt));if(original==null)throw bad("Unknown replacement transaction");
        Set<String> result=new HashSet<>(stringIds(object(object(original.get("capture")).get("dependencies")).get("operation_ids")));
        result.add((String)original.get("recovery_operation_id"));for(Map<String,Object> continuation:continuationIntents.values())if(attempt.equals(continuation.get("replacement_attempt_id")))result.add((String)continuation.get("recovery_operation_id"));for(Map<String,Object> attachment:restartAttachments.values())if(attempt.equals(attachment.get("replacement_attempt_id")))result.add((String)attachment.get("recovery_operation_id"));return Collections.unmodifiableSet(result);
    }
    private Map<String,Object> continuationFacts(String attempt)throws Exception {return continuationFacts(attempt,false);}
    private Map<String,Object> continuationFacts(String attempt,boolean historicalOnly)throws Exception {
        Map<String,Object> original=intents.get(uuid(attempt));if(original==null)throw bad("Unknown replacement transaction");Map<String,Object> captured=object(original.get("capture")),progress=progress(attempt),prior=new TreeMap<>(),adoptions=new TreeMap<>(),mutations=new TreeMap<>(),completions=new TreeMap<>();
        addPriorRecovery(attempt,prior,(String)original.get("recovery_operation_id"),(String)original.get("task_id"),historicalOnly);
        for(Map<String,Object> continuation:continuationIntents.values())if(attempt.equals(continuation.get("replacement_attempt_id")))addPriorRecovery(attempt,prior,(String)continuation.get("recovery_operation_id"),(String)continuation.get("task_id"),historicalOnly);
        Map<String,Object> restarts=new TreeMap<>(),restartFacts=new TreeMap<>();
        if(!historicalOnly){
            for(Map.Entry<String,Map<String,Object>> entry:restartAttachments.entrySet())if(attempt.equals(entry.getValue().get("replacement_attempt_id"))){Map<String,Object> attachment=entry.getValue();addPriorRecovery(attempt,prior,(String)attachment.get("recovery_operation_id"),(String)attachment.get("task_id"),false);restarts.put(entry.getKey(),attachment);}
            for(Map.Entry<String,Map<String,Object>> entry:restartObservations.entrySet())if(restarts.containsKey(object(entry.getValue().get("payload")).get("reattachment_id")))restartFacts.put(entry.getKey(),entry.getValue());
        }
        for(Map.Entry<String,Map<String,Object>> e:continuationAdoptions.entrySet())if(attempt.equals(object(e.getValue().get("payload")).get("replacement_attempt_id")))adoptions.put(e.getKey(),e.getValue());
        for(Map<String,Map<String,Object>> records:List.of(continuationStepIntents,continuationStepOutcomes))for(Map.Entry<String,Map<String,Object>> e:records.entrySet())if(attempt.equals(object(e.getValue().get("payload")).get("replacement_attempt_id")))mutations.put(e.getValue().get("type")+":"+e.getKey(),e.getValue());
        for(Map.Entry<String,Map<String,Object>> e:continuationReceipts.entrySet())if(attempt.equals(e.getValue().get("replacement_attempt_id")))completions.put(e.getKey(),e.getValue());
        Map<String,Object> deps=mutable(object(captured.get("dependencies")));TreeSet<String> ops=new TreeSet<>(stringIds(deps.get("operation_ids")));ops.addAll(prior.keySet());deps.put("operation_ids",new ArrayList<>(ops));
        TreeSet<String> jobs=new TreeSet<>(stringIds(deps.get("job_attempt_ids")));jobs.add(attempt);deps.put("job_attempt_ids",new ArrayList<>(jobs));
        for(String kind:List.of("material","boards")){String key=kind.equals("material")?"material_load_ids":"board_load_ids";TreeSet<String> ids=new TreeSet<>(stringIds(deps.get(key)));for(Object item:(List<?>)object(progress.get(kind)).get("rows")){Map<String,Object> row=object(item);if(row.get("new_load")!=null)ids.add(uuid(object(row.get("new_load")).get("load_id")));if(row.get("continuation_chain") instanceof List)for(Object step:(List<?>)row.get("continuation_chain"))ids.add(uuid(object(step).get("new_load_id")));}deps.put(key,new ArrayList<>(ids));}
        NativeSensingReconciliation.validateDependencies(deps);Map<String,Object> b=object(captured.get("boards")),m=object(captured.get("material")),lin=object(captured.get("lineage"));
        Map<String,Object> context=map("job_id",captured.get("job_id"),"job_revision",b.get("job_revision"),"board_load_revision",b.get("revision"),"material_setup_revision",m.get("revision"),"lineage_id",lin.get("lineage_id"),"lineage_revision",lin.get("lineage_revision"));
        Map<String,Object> result=map("schema_version",1,"replacement_attempt_id",attempt,"original_intent_sha256",digest(original),"original_capture_sha256",original.get("capture_sha256"),"progress",progress,"prior_operations",prior,"prior_adoptions",adoptions,"dependencies",deps,"job_context",context);if(!mutations.isEmpty())result.put("prior_mutations",mutations);if(!completions.isEmpty())result.put("prior_completions",completions);if(!restarts.isEmpty()){result.put("prior_reattachments",restarts);result.put("prior_restart_observations",restartFacts);}Map<String,Object> priorDispositions=priorDispositionRecords(attempt,null);if(!historicalOnly&&!priorDispositions.isEmpty())result.put("prior_process_dispositions",priorDispositions);if(latestContinuationPublication(attempt)!=null)result.put("publication_history",publicationHistory(attempt));return frozen(result);
    }
    private void addPriorRecovery(String attempt,Map<String,Object> prior,String id,String task,boolean historicalOnly)throws Exception {
        Map<String,Object> op=operations.get(uuid(id));if(op==null||!"local_native_sensing_reconciliation".equals(op.get("method"))||!Objects.equals(task,op.get("task_id"))||!Set.of("succeeded","failed","outcome_unknown").contains(op.get("state"))||!historicalOnly&&!hasWrapperRecord(op)&&!priorProcessDisposedForContinuation(attempt,id,true))throw bad("Every prior local recovery must have its exact terminal native wrapper record");
        if(prior.put(id,op)!=null)throw bad("Local recovery operation reused across continuation decisions");
    }
    ContinuationPermit beginContinuation(ContinuationCapture capture,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner)throws Exception {
        nativeOwner();coordinator.requirePermit(recovery,localOwner);if(capture==null||capture.owner!=this)throw bad("Foreign continuation capture");String attempt=(String)capture.payload.get("replacement_attempt_id");
        requireRetainedContinuation(attempt,capture.original.oldJob);if(livePermits.containsKey(attempt)||liveContinuations.containsKey(attempt)||liveRestarts.containsKey(attempt)||!same(capture.payload,continuationFacts(attempt)))throw bad("Continuation capture is stale or already active");
        for(String id:object(capture.payload.get("prior_operations")).keySet())if(!priorRecoveryCompleted(attempt,id,false,(String)object(object(coordinator.snapshot(recovery.taskId()).get("task")).get("fault_set")).get("bridge_instance_id")))throw bad("Prior native wrapper remains live or unresolved");
        Map<String,Object> task=object(coordinator.snapshot(recovery.taskId()).get("task")),scope=object(task.get("fault_set")),binding=object(task.get("replacement_context"));
        if(!NativeSensingReconciliation.CONTINUATION_KIND.equals(task.get("recovery_kind"))||!same(capture.dependencies(),scope.get("dependencies"))||!same(capture.jobContext(),scope.get("job_context"))||!attempt.equals(binding.get("replacement_attempt_id"))||!capture.digest.equals(binding.get("continuation_capture_sha256")))throw bad("Fresh local task does not bind the exact continuation capture");
        Map<String,Object> admission=operations.get(recovery.operationId());if(admission==null||!Objects.equals(admission.get("task_id"),recovery.taskId())||!Objects.equals(admission.get("bridge_instance_id"),scope.get("bridge_instance_id"))||!Objects.equals(admission.get("config_revision"),scope.get("config_revision"))||!Objects.equals(admission.get("request_digest"),task.get("fault_set_sha256"))||!Objects.equals(admission.get("reconciliation_request_id"),task.get("request_id")))throw bad("Local continuation admission differs from its exact fresh sensing task");requireSameActiveAdmission(admission);
        String id=UUID.randomUUID().toString();ContinuationPermit permit=new ContinuationPermit(this,capture,coordinator,recovery,localOwner,id,(String)task.get("fault_set_sha256"));Map<String,Object> original=intents.get(attempt);
        Map<String,Object> record=map("schema_version",1,"continuation_id",id,"replacement_attempt_id",attempt,"task_id",recovery.taskId(),"recovery_operation_id",recovery.operationId(),"fault_set_sha256",task.get("fault_set_sha256"),"continuation_capture_sha256",capture.digest,"original_recovery_operation_id",original.get("recovery_operation_id"),"original_fault_set_sha256",original.get("fault_set_sha256"),"capture",capture.payload,"local_admission",admission,"state","continuation_pending");
        append("faulted_job_replacement_continuation_intent",record);liveContinuations.put(attempt,permit);return permit;
    }
    private Runnable prepareContinuationIntent(Map<String,Object> p)throws Exception {
        keys(p,"schema_version","continuation_id","replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","capture","local_admission","state");
        if(number(p.get("schema_version"))!=1||!"continuation_pending".equals(p.get("state")))throw bad("Invalid continuation intent");String id=uuid(p.get("continuation_id")),attempt=uuid(p.get("replacement_attempt_id")),op=uuid(p.get("recovery_operation_id"));uuid(p.get("task_id"));hash(p.get("fault_set_sha256"));Map<String,Object> original=intents.get(attempt),capture=object(p.get("capture"));
        if(original==null||continuationIntents.containsKey(id)||continuationIntents.size()>=MAX_REPLACEMENTS||!Objects.equals(original.get("recovery_operation_id"),p.get("original_recovery_operation_id"))||!Objects.equals(original.get("fault_set_sha256"),p.get("original_fault_set_sha256"))||!digest(capture).equals(hash(p.get("continuation_capture_sha256"))))throw bad("Continuation lacks exact original transaction and capture");
        Map<String,Object> current=continuationFacts(attempt);if(!same(durableContinuationCapture(capture),durableContinuationCapture(current)))throw bad("Continuation capture differs from the forced transaction state");
        if(object(capture.get("prior_operations")).containsKey(op))throw bad("A new local recovery operation is required");Map<String,Object> admitted=operations.get(op);
        if(admitted==null||!same(admitted,p.get("local_admission"))||!Objects.equals(p.get("task_id"),admitted.get("task_id"))||!Objects.equals(p.get("fault_set_sha256"),admitted.get("request_digest")))throw bad("Continuation has no exact active local operation admission");requireSameActiveAdmission(admitted);uuid(admitted.get("bridge_instance_id"));uuid(admitted.get("reconciliation_request_id"));uuid(admitted.get("request_id"));if(!(admitted.get("config_revision") instanceof String)||!((String)admitted.get("config_revision")).matches("cfg-[0-9]+"))throw bad("Invalid local continuation configuration scope");
        return ()->{continuationIntents.put(id,p);generation++;};
    }
    private static Map<String,Object> durableContinuationCapture(Map<String,Object> capture)throws Exception {
        Map<String,Object> out=mutable(capture),p=object(out.get("progress"));object(p.get("definition")).remove("candidate_retained_in_process");
        for(String kind:List.of("material","boards"))for(Object row:(List<?>)object(p.get(kind)).get("rows"))object(row).remove("binding_status");
        return frozen(out);
    }
    private static Map<String,Object> continuationRow(Map<String,Object> capture,String kind,String oldLoadId)throws Exception {
        return progressRow(object(capture.get("progress")),kind,oldLoadId);
    }
    private static Map<String,Object> progressRow(Map<String,Object> progress,String kind,String oldLoadId)throws Exception {uuid(oldLoadId);for(Object raw:(List<?>)object(progress.get(kind)).get("rows")){Map<String,Object> row=object(raw);if(oldLoadId.equals(row.get("old_load_id")))return row;}throw bad("Load is outside captured continuation scope");}
    static boolean isContinuationAdoption(String type){return Set.of("material_continuation_adoption","board_continuation_adoption","job_lineage_continuation_adoption").contains(type);}
    static boolean isContinuationMutation(String type){return Set.of("material_continuation_intent","material_continuation_outcome","board_continuation_intent","board_continuation_outcome","job_lineage_continuation_replacement").contains(type);}
    static boolean isContinuationEvent(String type){return isContinuationAdoption(type)||isContinuationMutation(type);}
    static boolean isRestartObservation(String type){return Set.of("material_restart_observation","board_restart_observation","sensing_source_restart_bootstrap_intent","sensing_source_restart_bootstrap_returned").contains(type);}
    /** Pre-force and replay validator. Component reducers independently verify their exact parent
     * receipts. Neither this record nor journal replay mints a local continuation permit. */
    Runnable prepareContinuationObservation(String type,Map<String,Object> supplied)throws Exception {
        if(isRestartObservation(type))return prepareRestartObservation(type,supplied);
        if(isContinuationMutation(type))return prepareContinuationMutation(type,supplied);
        if(!isContinuationAdoption(type))return ()->{};if(faulted)throw bad("Continuation journal is uncertain");Map<String,Object> p=frozen(supplied);String id=uuid(p.get("continuation_id")),receipt=uuid(p.get("receipt_id"));Map<String,Object> intent=continuationIntents.get(id);
        if(intent==null||continuationReceipts.containsKey(id)||continuationAdoptions.containsKey(receipt)||continuationAdoptions.size()>=MAX_EVENTS)throw bad("Adoption lacks a unique unsealed forced continuation intent");
        requireSameActiveAdmission(object(intent.get("local_admission")));
        for(String key:List.of("replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256"))if(!Objects.equals(intent.get(key),p.get(key)))throw bad("Adoption changes fresh local continuation authority");
        Map<String,Object> capture=object(intent.get("capture"));String kind=type.startsWith("material_")?"material":type.startsWith("board_")?"boards":"lineage";boolean chained=number(p.get("schema_version"))==2;
        Map<String,Object> row=kind.equals("lineage")?object((chained?progress((String)intent.get("replacement_attempt_id")):object(capture.get("progress"))).get("lineage")):chained?progressRow(progress((String)intent.get("replacement_attempt_id")),kind,uuid(p.get("old_load_id"))):continuationRow(capture,kind,uuid(p.get("old_load_id")));
        if(!"completed".equals(effectivePhase(row))||!(chained?continuationPhaseDigest(row):digest(row)).equals(hash(p.get("phase_row_sha256"))))throw bad("Adoption is not the exact captured completed step");
        for(Map<String,Object> old:continuationAdoptions.values())if(type.equals(old.get("type"))){Map<String,Object> payload=object(old.get("payload"));if(id.equals(payload.get("continuation_id"))&&(kind.equals("lineage")||Objects.equals(p.get("old_load_id"),payload.get("old_load_id"))))throw bad("Continuation step already adopted");}
        Map<String,Object> event=frozen(map("type",type,"payload",p));return ()->{continuationAdoptions.put(receipt,event);generation++;};
    }
    private Runnable prepareRestartObservation(String type,Map<String,Object> supplied)throws Exception {
        if(faulted)throw bad("Restart journal is uncertain");Map<String,Object> p=frozen(supplied);boolean source=type.startsWith("sensing_source_"),intent=type.endsWith("_intent");
        String id=uuid(p.get("reattachment_id")),receipt=uuid(p.get(intent?"bootstrap_id":"receipt_id"));Map<String,Object> attachment=restartAttachments.get(id);
        if(number(p.get("schema_version"))!=1||attachment==null||restartObservations.containsKey(receipt)||restartObservations.size()>=MAX_EVENTS)throw bad("Restart observation lacks a unique forced local attachment");requireSameActiveAdmission(object(attachment.get("local_admission")));
        for(String key:List.of("replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256"))if(!Objects.equals(attachment.get(key),p.get(key)))throw bad("Restart observation changes its local authority");
        if(!Boolean.FALSE.equals(p.get("execution_authority_restored")))throw bad("Restart observation cannot restore execution");
        Map<String,Object> captured=object(attachment.get("capture"));
        if(source){
            List<String> fields=new ArrayList<>(List.of("schema_version","bootstrap_id","reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","attestation","attestation_sha256","historical_fault_sets_sha256","simulation_only","physical_occupancy_verified","execution_authority_restored"));if(!intent)fields.addAll(List.of("receipt_id","bootstrap_intent_sha256","source","source_generation","fixture"));keys(p,fields.toArray(String[]::new));
            hash(p.get("attestation_sha256"));hash(p.get("historical_fault_sets_sha256"));if(!digest(object(p.get("attestation"))).equals(p.get("attestation_sha256"))||!Boolean.TRUE.equals(p.get("simulation_only"))||!Boolean.FALSE.equals(p.get("physical_occupancy_verified")))throw bad("Invalid restart source observation scope");
            uuid(p.get("bootstrap_id"));
            if(!intent){Map<String,Object> previous=restartObservations.get(p.get("bootstrap_id"));if(previous==null||!"sensing_source_restart_bootstrap_intent".equals(previous.get("type")))throw bad("Restart source return lacks its forced intent");Map<String,Object> parent=object(previous.get("payload"));for(Map.Entry<String,Object> entry:parent.entrySet())if(!same(entry.getValue(),p.get(entry.getKey())))throw bad("Restart source return changes its forced intent");if(!digest(parent).equals(hash(p.get("bootstrap_intent_sha256"))))throw bad("Restart source return changes its parent digest");object(p.get("source"));object(p.get("fixture"));if(number(p.get("source_generation"))!=0)throw bad("Restart source must start a new generation");for(Map<String,Object> prior:restartObservations.values())if(type.equals(prior.get("type"))&&Objects.equals(object(prior.get("payload")).get("bootstrap_id"),p.get("bootstrap_id")))throw bad("Restart source intent already has a return");}
        }else{
            if(!"continuation-only".equals(p.get("binding_scope"))||!Boolean.TRUE.equals(p.get("original_outcomes_preserved"))||!Boolean.FALSE.equals(p.get(type.equals("material_restart_observation")?"physical_inventory_verified":"physical_load_verified")))throw bad("Restart load observation cannot establish physical presence");
            if(type.equals("material_restart_observation")){String old=uuid(p.get("old_load_id"));Map<String,Object> row=progressRow(object(captured.get("progress")),"material",old);if(!continuationPhaseDigest(row).equals(hash(p.get("phase_row_sha256"))))throw bad("Restart material observation changes its captured phase");}
            else {
                Map<String,Object> original=object(intents.get(attachment.get("replacement_attempt_id")).get("capture")),oldBoards=object(original.get("boards")),boardProgress=mutable(object(object(captured.get("progress")).get("boards")));
                for(Object row:(List<?>)boardProgress.get("rows"))object(row).remove("binding_status");
                if(!digest(oldBoards).equals(hash(p.get("old_boards_sha256")))||!digest(boardProgress).equals(hash(p.get("board_progress_sha256"))))throw bad("Restart board observation changes its exact captured union");
                Map<String,Object> bundle=object(intents.get(attachment.get("replacement_attempt_id")).get("reconstruction_bundle")),history=object(bundle.get("original_history")),active=object(oldBoards.get("active"));Set<String> seen=new HashSet<>();
                if(!(p.get("roots") instanceof List)||((List<?>)p.get("roots")).size()!=active.size())throw bad("Restart board observation omits a root");
                for(Object value:(List<?>)p.get("roots")){
                    Map<String,Object> row=object(value);Object root=row.get("root_instance_id");if(!(root instanceof String)||!active.containsKey(root)||!seen.add((String)root)||!Objects.equals(active.get(root),row.get("old_load_id")))throw bad("Restart board observation has duplicate or foreign roots");
                    Map<String,Object> expected=new TreeMap<>();String prefix=root+PlacementsHolderLocation.ID_DELIMITTER;for(Map.Entry<String,Object> entry:history.entrySet())if(entry.getKey().startsWith(prefix))expected.put(entry.getKey(),entry.getValue());
                    if(!same(expected,row.get("old_native_history"))||!digest(expected).equals(hash(row.get("old_native_history_sha256")))||!continuationPhaseDigest(progressRow(object(captured.get("progress")),"boards",uuid(row.get("old_load_id")))).equals(hash(row.get("phase_row_sha256"))))throw bad("Restart board observation changes the atomically recorded complete history");
                }
            }
            for(Map<String,Object> prior:restartObservations.values())if(type.equals(prior.get("type"))){Map<String,Object> data=object(prior.get("payload"));if(id.equals(data.get("reattachment_id"))&&(!type.equals("material_restart_observation")||Objects.equals(p.get("old_load_id"),data.get("old_load_id"))))throw bad("Restart load scope already observed under this decision");}
        }
        Map<String,Object> event=frozen(map("type",type,"payload",p));return ()->{restartObservations.put(receipt,event);generation++;};
    }
    private Runnable prepareContinuationMutation(String type,Map<String,Object> supplied)throws Exception {
        if(faulted)throw bad("Continuation journal is uncertain");Map<String,Object> p=frozen(supplied);String step=uuid(p.get("step_id")),receipt=uuid(p.get("receipt_id")),id=uuid(p.get("continuation_id"));
        if(!step.equals(receipt)||number(p.get("schema_version"))!=1)throw bad("Continuation step identity/schema differs");Map<String,Object> decision=continuationIntents.get(id);if(decision==null||continuationReceipts.containsKey(id))throw bad("Continuation step lacks an unsealed forced local decision");requireSameActiveAdmission(object(decision.get("local_admission")));
        for(String key:List.of("replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256"))if(!Objects.equals(decision.get(key),p.get(key)))throw bad("Continuation step changes local decision authority");
        if(type.equals("job_lineage_continuation_replacement")){
            if(continuationStepIntents.containsKey(step)||continuationStepOutcomes.containsKey(step)||continuationAdoptions.containsKey(step)||continuationStepOutcomes.size()>=MAX_EVENTS)throw bad("Lineage step identity/capacity conflict");
            Map<String,Object> phase=object(progress((String)decision.get("replacement_attempt_id")).get("lineage"));
            if(!"untouched".equals(effectivePhase(phase))||!continuationPhaseDigest(phase).equals(hash(p.get("phase_row_sha256")))||!Objects.equals(phase.get("original_job_id"),p.get("replaces_attempt"))||!Objects.equals(phase.get("replacement_job_id"),p.get("new_job_id")))throw bad("Lineage step does not bind exact untouched original attempt");
            Map<String,Object> event=frozen(map("type",type,"payload",p));return ()->{continuationStepOutcomes.put(step,event);generation++;};
        }
        String kind=type.startsWith("material_")?"material":"boards",old=uuid(p.get("old_load_id"));boolean outcome=type.endsWith("_outcome");Map<String,Object> event=frozen(map("type",type,"payload",p));
        if(!outcome){
            if(continuationStepIntents.containsKey(step)||continuationStepOutcomes.containsKey(step)||continuationAdoptions.containsKey(step)||continuationStepIntents.size()>=MAX_EVENTS)throw bad("Continuation step identity/capacity conflict");
            Map<String,Object> row=progressRow(progress((String)decision.get("replacement_attempt_id")),kind,old);String phase=effectivePhase(row);
            if(!Set.of("untouched","pending").contains(phase)||!continuationPhaseDigest(row).equals(hash(p.get("phase_row_sha256")))||!Objects.equals(p.get("mode"),phase.equals("untouched")?"replace-untouched":"supersede-unknown"))throw bad("Continuation step does not bind exact current untouched/unknown load");
            return ()->{continuationStepIntents.put(step,event);generation++;};
        }
        Map<String,Object> original=continuationStepIntents.get(step);if(original==null||continuationStepOutcomes.containsKey(step)||!type.replace("_outcome","_intent").equals(original.get("type")))throw bad("Continuation outcome has no exact pending step");
        Map<String,Object> comparison=mutable(p);if(!"loaded".equals(object(comparison.get("new_load")).get("state")))throw bad("Continuation outcome must describe the new loaded identity");object(comparison.get("new_load")).put("state","loading_unknown");
        if(!same(comparison,original.get("payload")))throw bad("Continuation outcome changes its forced intent");return ()->{continuationStepOutcomes.put(step,event);generation++;};
    }
    NativeFaultedJobReplacement(Configuration config,NativeMaterialLoads material,NativeBoardLoads boards,NativeJobLineage lineage,Sink sink){this(config,material,boards,lineage,sink,id->false);}
    NativeFaultedJobReplacement(Configuration config,NativeMaterialLoads material,NativeBoardLoads boards,NativeJobLineage lineage,Sink sink,TerminalAuthority terminalAuthority){this(config,material,boards,lineage,sink,terminalAuthority,(job,attempt)->false);}
    NativeFaultedJobReplacement(Configuration config,NativeMaterialLoads material,NativeBoardLoads boards,NativeJobLineage lineage,Sink sink,TerminalAuthority terminalAuthority,JobAuthority jobAuthority){this(config,material,boards,lineage,sink,terminalAuthority,jobAuthority,(operation,instance,revision,old,job,prior)->{throw bad("Independent current journal/native ownership is required");});}
    NativeFaultedJobReplacement(Configuration config,NativeMaterialLoads material,NativeBoardLoads boards,NativeJobLineage lineage,Sink sink,TerminalAuthority terminalAuthority,JobAuthority jobAuthority,RestartAuthority restartAuthority){this.restartAuthority=Objects.requireNonNull(restartAuthority);this.config=Objects.requireNonNull(config);machine=config.getMachine();this.material=Objects.requireNonNull(material);this.boards=Objects.requireNonNull(boards);this.lineage=Objects.requireNonNull(lineage);this.sink=Objects.requireNonNull(sink);this.terminalAuthority=Objects.requireNonNull(terminalAuthority);this.jobAuthority=Objects.requireNonNull(jobAuthority);}
    private void nativeOwner()throws Exception {
        if(Configuration.get()!=config||config.getMachine()!=machine||!machine.isTask(Thread.currentThread())||machine.getClass()!=ReferenceMachine.class)throw bad("Exact native simulator owner required");
        if(machine.getDrivers().isEmpty())throw bad("Explicit simulator driver required");for(Driver driver:machine.getDrivers())if(driver.getClass()!=NullDriver.class)throw bad("Replacement profile requires exact NullDriver");
        if(closed||faulted)throw bad("Closed or uncertain replacement owner requires verified journal restart");
    }
    /** Invoke only after the Bridge's existing record validators AND append/force have succeeded.
     * Replay invokes in the same original order, never from arbitrary request arguments. */
    void observe(String type,Map<String,Object> supplied)throws Exception {
        if(faulted)throw bad("Replacement source journal is faulted");Map<String,Object> p=frozen(supplied);
        try {
            if(type.equals("operation")){
                String id=historyOperationId(p.get("operation_id"));Map<String,Object> old=operations.get(id);if(old==null&&operations.size()>=MAX_OPERATIONS)throw bad("Operation capacity reached");
                if(old!=null)for(String key:List.of("job_id","job_revision","config_revision","board_load_revision","method","request_id","job_lineage"))if(!same(old.get(key),p.get(key)))throw bad("Operation changed original dependency scope");operations.put(id,p);generation++;
            } else if(NativeActionLedger.isLedgerEventType(type)) {
                String op=uuid(p.get("operation_id")),id=(String)p.get("event_id");if(observedEventIds.contains(id)||observedEventIds.size()>=MAX_EVENTS)throw bad("Duplicate or over-capacity native event");
                if(!operations.containsKey(op))throw bad("Native action precedes operation admission");if(!Objects.equals(operations.get(op).get("job_id"),p.get("job_id")))throw bad("Native action job differs from admitted operation");
                Map<String,Object> event=frozen(map("type",type,"payload",p));actionReplays.computeIfAbsent(op,NativeActionLedger.Replay::new).accept(event);actionEvents.computeIfAbsent(op,k->new ArrayList<>()).add(event);observedEventIds.add(id);generation++;
            }
        }catch(Exception|Error failure){faulted=true;throw failure;}
    }
    Capture capture(Job oldJob,String jobId,String originalOperationId)throws Exception {
        nativeOwner();uuid(jobId);uuid(originalOperationId);requireNewReplacement(jobId);Map<String,Object> payload=captureFacts(jobId,originalOperationId);
        if(!NativeBoardLoads.replacementModel(oldJob).equals(boardModels(object(payload.get("boards")))))throw bad("Native job differs from retained board graph");return new Capture(this,oldJob,payload);
    }
    private void requireNewReplacement(String jobId)throws Exception {
        // Check before source repair/disposal. A prior transaction may already have reset trays or
        // retired lineage even when its final receipt is missing. It requires explicit continuation.
        for(Map<String,Object> intent:intents.values())if(((List<?>)object(object(intent.get("capture")).get("dependencies")).get("job_attempt_ids")).contains(jobId))throw bad("Existing replacement transaction requires continuation: "+intent.get("replacement_attempt_id"));
        if(lineage.retiredJob(jobId))throw bad("Retired job attempt requires its existing replacement transaction");
    }
    private Map<String,Object> captureFacts(String jobId,String originalOperationId)throws Exception {
        Map<String,Object> lin=lineage.captureReplacementState(jobId),b=boards.captureReplacementState(jobId),m=material.captureReplacementState();
        Map<String,Object> original=operations.get(originalOperationId);if(original==null||!((List<?>)lin.get("job_aliases")).contains(original.get("job_id"))||!Set.of("failed","outcome_unknown").contains(original.get("state")))throw bad("Exact terminal faulted native job operation required");
        if(!object(m.get("pending_changes")).isEmpty()||!object(m.get("replacement_intents")).isEmpty()||!object(b.get("pending_changes")).isEmpty()||!object(b.get("replacement_intents")).isEmpty())throw bad("Unresolved load mutation requires document/setup recovery");
        Map<String,Object> ops=new TreeMap<>(),events=new TreeMap<>(),replays=new TreeMap<>();Set<String> actionIds=new TreeSet<>(),materialIds=new TreeSet<>();
        for(String op:object(lin.get("operation_bindings")).keySet()){
            Map<String,Object> actual=operations.get(op);if(actual==null||!Set.of("succeeded","failed","outcome_unknown","cancelled").contains(actual.get("state")))throw bad("Every attempt operation must be terminal and captured");ops.put(op,actual);
            List<Map<String,Object>> rows=actionEvents.getOrDefault(op,List.of());events.put(op,rows);if(actionReplays.containsKey(op))replays.put(op,actionReplays.get(op).snapshot());
            for(Map<String,Object> row:rows){Map<String,Object> p=object(row.get("payload"));if(p.get("action_id")!=null)actionIds.add(NativeSensingReconciliation.nativeActionId(p.get("action_id")));if(p.get("context") instanceof Map){Map<String,Object> ctx=object(p.get("context"));if(ctx.get("material_load") instanceof Map)materialIds.add(uuid(object(ctx.get("material_load")).get("load_id")));}}
        }
        if(!ops.containsKey(originalOperationId)||actionIds.isEmpty())throw bad("Faulted processor attempt requires complete native action history");
        // All currently bound trays are conservatively included, preventing an alias from hiding unknown consumption.
        for(Object id:object(m.get("active")).values())materialIds.add(uuid(id));if(materialIds.isEmpty())throw bad("Explicit material load setup required for job replacement");
        for(String id:materialIds)if(!object(m.get("active")).containsValue(id))throw bad("Historical inactive material load requires a separate disposition");
        for(Object raw:object(m.get("pending_feeds")).values()){Map<String,Object> feed=object(raw);if(!ops.containsKey(feed.get("operation_id"))&&!object(m.get("retired_loads")).containsKey(object(feed.get("material_load")).get("load_id")))throw bad("Unknown feed from another attempt is outside replacement scope");}
        Set<String> boardUnion=new TreeSet<>();for(Object id:(List<?>)lin.get("load_ids"))boardUnion.add(uuid(id));for(Object id:object(b.get("active")).values())boardUnion.add(uuid(id));for(String id:boardUnion)if(!object(b.get("active")).containsValue(id))throw bad("Historical board load requires its separate retained disposition");List<String> boardIds=new ArrayList<>(boardUnion);
        Map<String,Object> dependencies=map("action_ids",new ArrayList<>(actionIds),"operation_ids",new ArrayList<>(ops.keySet()),"board_load_ids",boardIds,"material_load_ids",new ArrayList<>(materialIds),"job_attempt_ids",new ArrayList<>((List<?>)lin.get("job_aliases")),"unresolved_dependencies",List.of());NativeSensingReconciliation.validateDependencies(dependencies);
        return frozen(map("schema_version",1,"job_id",jobId,"original_operation_id",originalOperationId,"lineage",lin,"boards",b,"material",m,"operations",ops,"action_events",events,"action_summaries",replays,"dependencies",dependencies));
    }
    private static Map<String,Object> boardModels(Map<String,Object> state)throws IOException {Map<String,Object> out=new TreeMap<>();for(Map.Entry<String,Object> e:object(state.get("active")).entrySet())out.put(e.getKey(),object(object(state.get("loads")).get(e.getValue())).get("signature"));return frozen(out);}
    Permit begin(Capture capture,Job freshJob,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner)throws Exception {
        return beginCaptured(capture,freshJob,null,coordinator,recovery,localOwner);
    }
    /** New native transactions commit their reconstructible state in the first forced intent.
     * No later definition/archive publication is needed to preserve an interrupted candidate.
     * The Job overload remains for reading/testing the earlier, non-reconstructible protocol. */
    Permit begin(Capture capture,NativeReplacementJob.Candidate candidate,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner)throws Exception {
        nativeOwner();coordinator.requirePermit(recovery,localOwner);
        if(capture==null||capture.owner!=this||candidate==null)throw bad("Exact native replacement capture and candidate required");
        if(machine.isEnabled())throw bad("Disable the simulator before capturing replacement reconstruction data");
        if(!capture.oldHistory.equals(capture.oldJob.getPlacedStatusSnapshot()))throw bad("Original placed history changed before replacement admission");
        Map<String,Object> reconstruction=NativeReplacementDocuments.captureReconstruction(config,capture.oldJob,candidate);
        candidate.requireCurrent();coordinator.requirePermit(recovery,localOwner);
        return beginCaptured(capture,candidate.job(),reconstruction,coordinator,recovery,localOwner);
    }
    private Permit beginCaptured(Capture capture,Job freshJob,Map<String,Object> reconstruction,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner)throws Exception {
        nativeOwner();coordinator.requirePermit(recovery,localOwner);if(capture==null||capture.owner!=this)throw bad("Replacement capture is stale or foreign");requireNewReplacement((String)capture.payload.get("job_id"));if(!same(capture.payload,captureFacts((String)capture.payload.get("job_id"),(String)capture.payload.get("original_operation_id"))))throw bad("Replacement capture is stale or foreign");
        if(reconstruction!=null&&(machine.isEnabled()||!capture.oldHistory.equals(capture.oldJob.getPlacedStatusSnapshot())))throw bad("Native reconstruction changed before its forced admission");
        NativeBoardLoads.requireDetachedReplacement(capture.oldJob,freshJob);Map<String,Object> task=object(coordinator.snapshot(recovery.taskId()).get("task")),faultSet=object(task.get("fault_set"));
        if(!"replace-faulted-job-attempt".equals(task.get("recovery_kind"))||!same(capture.dependencies(),faultSet.get("dependencies"))||!Objects.equals(capture.payload.get("job_id"),object(faultSet.get("job_context")).get("job_id")))throw bad("Coordinator task does not capture this exact job dependency union");
        if(intents.size()+receipts.size()>=MAX_REPLACEMENTS)throw bad("Replacement history capacity reached");String attempt=UUID.randomUUID().toString();Permit permit=new Permit(this,capture,coordinator,recovery,localOwner,(String)task.get("fault_set_sha256"),freshJob,attempt);
        Map<String,Object> intent=map("schema_version",reconstruction==null?1:2,"replacement_attempt_id",attempt,"task_id",recovery.taskId(),"recovery_operation_id",recovery.operationId(),"fault_set_sha256",task.get("fault_set_sha256"),"capture_sha256",capture.digest,"capture",capture.payload,"state","replacement_pending");
        if(reconstruction!=null){intent.put("reconstruction_bundle",reconstruction);intent.put("reconstruction_sha256",digest(reconstruction));
            // Reserve the outer event envelope before any forced transaction admission. The
            // Bridge independently checks the complete encoded record at append time.
            if(JSON.toJson(frozen(intent)).getBytes(StandardCharsets.UTF_8).length>Bridge.MAX_JOURNAL_RECORD_BYTES-65536)throw bad("Replacement reconstruction exceeds the bounded journal record");
        }
        append("faulted_job_replacement_intent",intent);retainedCaptures.put(attempt,capture);livePermits.put(attempt,permit);return permit;
    }
    /** Transfer unpublished graph ownership before attempting its forced record. A failed or uncertain
     * append must not let callback cleanup destroy objects that durable board receipts may reference. */
    void retainCandidate(Permit permit,NativeReplacementJob.Candidate candidate)throws Exception {
        permit.check();candidate.requireCurrent();if(candidate.job()!=permit.freshJob||candidates.containsKey(permit.attemptId))throw bad("Replacement candidate is foreign or already retained");
        Map<String,Object> original=intents.get(permit.attemptId);
        if(original.containsKey("reconstruction_bundle")&&!same(candidate.mapping(),object(original.get("reconstruction_bundle")).get("source_mapping")))throw bad("Candidate definition differs from its atomically retained reconstruction");
        candidates.put(permit.attemptId,candidate);
        Map<String,Object> record=recordBase(permit);record.put("mapping",candidate.mapping());record.put("mapping_sha256",digest(candidate.mapping()));
        append("faulted_job_replacement_definition",record);
    }
    boolean ownsCandidate(NativeReplacementJob.Candidate candidate){for(NativeReplacementJob.Candidate retained:candidates.values())if(retained==candidate)return true;return false;}
    NativeReplacementJob.Candidate retainedCandidate(String attempt)throws Exception {
        nativeOwner();NativeReplacementJob.Candidate candidate=candidates.get(uuid(attempt));if(candidate==null)throw bad("Replacement candidate requires explicit document reattachment");candidate.requireCurrent();return candidate;
    }
    private Map<String,Object> recordBase(Permit permit)throws Exception {permit.check();return map("schema_version",1,"replacement_attempt_id",permit.attemptId,"recovery_operation_id",permit.recovery.operationId(),"fault_set_sha256",permit.faultDigest,"capture_sha256",permit.capture.digest);}
    void recordDocument(Permit permit,NativeReplacementDocuments.Saved saved)throws Exception {
        permit.check();retainedCandidate(permit.attemptId);Map<String,Object> record=recordBase(permit);
        record.put("document_id",saved.documentId);record.put("manifest",saved.manifest);record.put("manifest_sha256",digest(saved.manifest));
        append("faulted_job_replacement_document",record);
    }
    /** Complete an interrupted candidate save under this fresh local decision, before source
     * repair, probing or disposal. Original archive context remains immutable; the new forced
     * records identify the decision that actually completed persistence. Occupied nozzles are
     * permitted here because saving a detached document does not resolve their material. */
    Map<String,Object> ensureContinuationDocument(ContinuationPermit permit,NativeReplacementDocuments store)throws Exception {
        if(permit==null||permit.owner!=this)throw bad("Exact continuation permit required for candidate persistence");
        permit.check();if(machine.isEnabled())throw bad("Disable the simulator before persisting its replacement candidate");
        Objects.requireNonNull(store);Map<String,Object> existing=documents.get(permit.attempt);
        if(existing!=null)try {
            store.verifySaved(hash(existing.get("document_id")),documentContext(intents.get(permit.attempt)),object(existing.get("manifest")));
            permit.check();if(machine.isEnabled())throw bad("Simulator was enabled while verifying its candidate document");return existing;
        }catch(Exception|Error failure){permit.close();throw failure;}
        if(permit.activeStep!=null||continuationReceipts.containsKey(permit.id)||continuationDocumentIntents.containsKey(permit.id))throw bad("Candidate save needs a fresh unsealed continuation decision");
        Map<String,Object> decision=continuationIntents.get(permit.id),original=intents.get(permit.attempt),definition=ensureContinuationDefinition(permit);
        NativeReplacementDocuments.Context context=documentContext(original);
        Map<String,Object> intent=continuationDocumentBase(decision,original,definition,context.payload);
        intent.put("progress_sha256",durableProgressDigest(permit.expectedProgress));intent.put("state","document_pending");
        try {
            append("faulted_job_replacement_continuation_document_intent",intent);advanceDocumentProgress(permit);
            permit.check();if(machine.isEnabled())throw bad("Simulator was enabled before candidate persistence");
            NativeReplacementDocuments.Saved saved=store.save(context,permit.capture.candidate);
            permit.check();if(machine.isEnabled())throw bad("Simulator was enabled during candidate persistence");
            Map<String,Object> outcome=mutable(intent);outcome.put("state","document_committed");outcome.put("document_intent_sha256",digest(intent));
            outcome.put("document_id",saved.documentId);outcome.put("manifest",saved.manifest);outcome.put("manifest_sha256",digest(saved.manifest));
            append("faulted_job_replacement_continuation_document_outcome",outcome);advanceDocumentProgress(permit);
            if(machine.isEnabled())throw bad("Simulator was enabled during candidate document publication");
            return documents.get(permit.attempt);
        }catch(Exception|Error failure){permit.close();throw failure;}
    }
    /** A first-intent crash may precede the original definition record. The new definition is
     * published by the current continuation, from the exact atomically captured native graph. */
    Map<String,Object> ensureContinuationDefinition(ContinuationPermit permit)throws Exception {
        if(permit==null||permit.owner!=this)throw bad("Exact continuation permit required for candidate definition");
        permit.check();if(machine.isEnabled())throw bad("Disable the simulator before publishing its replacement definition");
        Map<String,Object> existing=definitions.get(permit.attempt);if(existing!=null)return existing;
        if(permit.activeStep!=null||continuationReceipts.containsKey(permit.id)||continuationDocumentIntents.containsKey(permit.id))throw bad("Missing definition requires an unsealed current continuation before document persistence");
        Map<String,Object> original=intents.get(permit.attempt),decision=continuationIntents.get(permit.id);Map<String,Object> bundle=reconstructionBundle(permit.attempt);
        permit.capture.candidate.requireOriginal(permit.capture.original.oldJob);
        Map<String,Object> mapping=permit.capture.candidate.mapping();if(!same(mapping,bundle.get("source_mapping")))throw bad("Retained candidate differs from its original atomic reconstruction mapping");
        Map<String,Object> record=continuationDefinitionBase(decision,original,bundle);record.put("progress_sha256",durableProgressDigest(permit.expectedProgress));
        try {
            append("faulted_job_replacement_continuation_definition",record);
            permit.checkIdentity();Map<String,Object> current=progress(permit.attempt),expected=mutable(permit.expectedProgress);expected.put("definition",current.get("definition"));
            if(!same(expected,current))throw bad("Definition publication changed another replacement phase");permit.expectedProgress=frozen(current);permit.check();
            if(machine.isEnabled())throw bad("Simulator was enabled during candidate definition publication");
            if(!same(record,definitions.get(permit.attempt)))throw bad("Candidate definition was not committed exactly");return definitions.get(permit.attempt);
        }catch(Exception|Error failure){permit.close();throw failure;}
    }
    private static Map<String,Object> continuationDefinitionBase(Map<String,Object> decision,Map<String,Object> original,Map<String,Object> bundle)throws Exception {
        Map<String,Object> record=map("schema_version",1);for(String key:List.of("continuation_id","replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256"))record.put(key,decision.get(key));
        record.put("original_capture_sha256",original.get("capture_sha256"));record.put("reconstruction_sha256",original.get("reconstruction_sha256"));record.put("mapping",bundle.get("source_mapping"));record.put("mapping_sha256",digest(object(bundle.get("source_mapping"))));record.put("native_job_initialized",false);record.put("execution_authority_restored",false);return record;
    }
    private Runnable prepareContinuationDefinition(Map<String,Object> p)throws Exception {
        keys(p,"schema_version","continuation_id","replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","original_capture_sha256","reconstruction_sha256","mapping","mapping_sha256","native_job_initialized","execution_authority_restored","progress_sha256");
        String id=uuid(p.get("continuation_id")),attempt=uuid(p.get("replacement_attempt_id"));Map<String,Object> decision=continuationIntents.get(id),original=intents.get(attempt);
        if(decision==null||original==null||definitions.containsKey(attempt)||documents.containsKey(attempt)||continuationDefinitions.containsKey(id)||continuationReceipts.containsKey(id)||continuationDocumentIntents.containsKey(id))throw bad("Candidate definition lacks a unique current continuation and missing definition");
        requireSameActiveAdmission(object(decision.get("local_admission")));Map<String,Object> bundle=reconstructionBundle(attempt),expected=continuationDefinitionBase(decision,original,bundle);
        for(String key:expected.keySet())if(!same(expected.get(key),p.get(key)))throw bad("Candidate definition changes its atomic graph or current decision");
        if(!durableProgressDigest(progress(attempt)).equals(hash(p.get("progress_sha256"))))throw bad("Candidate definition changes its captured dependency phases");
        return ()->{definitions.put(attempt,p);continuationDefinitions.put(id,p);generation++;};
    }
    private static NativeReplacementDocuments.Context documentContext(Map<String,Object> original)throws Exception {
        return new NativeReplacementDocuments.Context(uuid(object(original.get("capture")).get("job_id")),uuid(original.get("replacement_attempt_id")),uuid(original.get("recovery_operation_id")),hash(original.get("fault_set_sha256")));
    }
    private static Map<String,Object> continuationDocumentBase(Map<String,Object> decision,Map<String,Object> original,Map<String,Object> definition,Map<String,Object> context)throws Exception {
        Map<String,Object> result=map("schema_version",1);
        for(String key:List.of("continuation_id","replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256"))result.put(key,decision.get(key));
        result.put("original_capture_sha256",original.get("capture_sha256"));result.put("definition_sha256",digest(definition));result.put("document_context",context);
        result.put("native_job_initialized",false);result.put("execution_authority_restored",false);return result;
    }
    private void advanceDocumentProgress(ContinuationPermit permit)throws Exception {
        permit.checkIdentity();Map<String,Object> current=progress(permit.attempt),expected=mutable(permit.expectedProgress);
        expected.put("document",current.get("document"));if(!same(expected,current))throw bad("Candidate persistence changed another replacement phase");
        permit.expectedProgress=frozen(current);permit.check();
    }
    private Runnable prepareContinuationDocument(String type,Map<String,Object> p)throws Exception {
        boolean outcome=type.endsWith("_outcome");Set<String> fields=new HashSet<>(List.of("schema_version","continuation_id","replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","original_capture_sha256","definition_sha256","document_context","native_job_initialized","execution_authority_restored","progress_sha256","state"));
        if(outcome)fields.addAll(List.of("document_intent_sha256","document_id","manifest","manifest_sha256"));if(!fields.equals(p.keySet()))throw bad("Missing or unknown continuation document fields");
        String id=uuid(p.get("continuation_id")),attempt=uuid(p.get("replacement_attempt_id"));Map<String,Object> decision=continuationIntents.get(id),original=intents.get(attempt),definition=definitions.get(attempt);
        if(decision==null||original==null||definition==null||documents.containsKey(attempt)||continuationReceipts.containsKey(id))throw bad("Candidate persistence lacks an unsealed local decision and missing document");
        requireSameActiveAdmission(object(decision.get("local_admission")));
        Map<String,Object> base=continuationDocumentBase(decision,original,definition,documentContext(original).payload);
        for(String key:base.keySet())if(!same(base.get(key),p.get(key)))throw bad("Candidate persistence changes its exact decision, definition or original context");
        hash(p.get("progress_sha256"));Map<String,Object> prior=continuationDocumentIntents.get(id);
        if(!outcome){
            if(prior!=null||!"document_pending".equals(p.get("state"))||!durableProgressDigest(progress(attempt)).equals(p.get("progress_sha256")))throw bad("Candidate save intent is duplicate or its dependency phases changed");
            return ()->{continuationDocumentIntents.put(id,p);generation++;};
        }
        if(prior==null||continuationDocuments.containsKey(id)||!"document_committed".equals(p.get("state"))||!digest(prior).equals(hash(p.get("document_intent_sha256"))))throw bad("Candidate document completion lacks its unique forced intent");
        Map<String,Object> comparison=mutable(p);for(String key:List.of("document_intent_sha256","document_id","manifest","manifest_sha256"))comparison.remove(key);comparison.put("state","document_pending");
        if(!same(comparison,prior)||!durableProgressDigest(progressBeforeDocumentIntent(attempt,id)).equals(p.get("progress_sha256")))throw bad("Candidate document completion changes its intent or other dependency phases");
        validateDocument(p,original,definition);
        return ()->{continuationDocuments.put(id,p);documents.put(attempt,p);generation++;};
    }
    private Map<String,Object> progressBeforeDocumentIntent(String attempt,String id)throws Exception {
        Map<String,Object> current=mutable(progress(attempt)),document=object(current.get("document")),history=object(document.get("continuation_attempts"));
        if(history.remove(id)==null)throw bad("Candidate document intent is absent from progress");
        if(history.isEmpty()){document.remove("continuation_attempts");document.put("phase","untouched");}else document.put("phase","pending");
        return frozen(current);
    }
    private void validateDocument(Map<String,Object> p,Map<String,Object> original,Map<String,Object> definition)throws Exception {
        hash(p.get("document_id"));Map<String,Object> manifest=object(p.get("manifest"));
        NativeReplacementDocuments.validateManifest(manifest,documentContext(original));
        if(definition==null||!digest(manifest).equals(hash(p.get("manifest_sha256")))||!NativeReplacementDocuments.FORMAT.equals(manifest.get("format"))||!Bridge.UPSTREAM.equals(manifest.get("upstream_commit"))||number(manifest.get("schema_version"))!=1||!same(definition.get("mapping"),manifest.get("source_mapping"))||!same(documentContext(original).payload,manifest.get("context")))throw bad("Candidate document is corrupt or outside the original graph and transaction scope");
        if(!Boolean.FALSE.equals(manifest.get("job_installed"))||!Boolean.FALSE.equals(manifest.get("native_job_initialized"))||!Boolean.FALSE.equals(manifest.get("execution_authority_restored"))||number(manifest.get("candidate_history_entries"))!=0)throw bad("Candidate document cannot carry execution history or authority");
    }
    private Map<String,Object> documentProgress(String attempt,Map<String,Object> document)throws Exception {
        Map<String,Object> history=new LinkedHashMap<>();for(Map.Entry<String,Map<String,Object>> e:continuationDocumentIntents.entrySet())if(attempt.equals(e.getValue().get("replacement_attempt_id")))history.put(e.getKey(),map("intent",e.getValue(),"outcome",continuationDocuments.get(e.getKey())));
        Map<String,Object> result=map("phase",document!=null?"completed":history.isEmpty()?"untouched":"pending","record",document,"record_sha256",document==null?null:digest(document),"storage_verified_by_this_read",false,"reattachment_performed",false);
        if(!history.isEmpty())result.put("continuation_attempts",history);return result;
    }
    void beginPublication(Permit permit)throws Exception {
        permit.check();retainedCandidate(permit.attemptId);Map<String,Object> record=recordBase(permit);Map<String,Object> definition=definitions.get(permit.attemptId),outcome=receipts.get(permit.attemptId);
        if(definition==null||outcome==null)throw bad("Candidate definition and complete replacement receipts required before publication");
        record.put("definition_sha256",digest(definition));record.put("replacement_outcome_sha256",digest(outcome));record.put("state","publication_pending");append("faulted_job_replacement_publication_intent",record);
    }
    void finishPublication(Permit permit)throws Exception {
        permit.check();Map<String,Object> intent=publicationIntents.get(permit.attemptId);if(intent==null)throw bad("Native publication has no forced intent");
        Map<String,Object> record=mutable(intent);record.put("state","native_job_published");append("faulted_job_replacement_publication_outcome",record);
    }
    /** Returns durable subreceipts only. The caller still needs fresh registration/material/job validation,
     * forced native wrapper/terminal completion and the coordinator's final disposition publication. */
    Map<String,Object> replace(Permit permit)throws Exception {
        permit.check();Map<String,Object> lin=lineage.createReplacement(permit,permit.attemptId);List<Object> materials=new ArrayList<>();
        for(Object id:(List<?>)permit.capture.dependencies().get("material_load_ids")){permit.check();materials.add(descriptor("material",material.replaceRetiredLoad(permit,(String)id)));}
        permit.check();List<Object> boardReceipts=new ArrayList<>();for(Map<String,Object> record:boards.quarantineAndBindReplacement(permit,permit.capture.oldJob,permit.freshJob,permit.attemptId))boardReceipts.add(descriptor("board",record));permit.check();material.requireReady();boards.requireReady(permit.freshJob);
        Map<String,Object> receipt=map("schema_version",1,"replacement_attempt_id",permit.attemptId,"recovery_operation_id",permit.recovery.operationId(),"fault_set_sha256",permit.faultDigest,"capture_sha256",permit.capture.digest,"lineage_receipt_id",lin.get("receipt_id"),"lineage_receipt_sha256",digest(lin),"material_receipts",materials,"board_receipts",boardReceipts,"old_outcome_preserved",true,"new_attempt_requires_validation",true,"native_job_initialized",false);
        append("faulted_job_replacement_outcome",receipt);return frozen(receipt);
    }
    Job replacementJob(Permit permit)throws Exception{permit.check();if(!receipts.containsKey(permit.attemptId))throw bad("Replacement subreceipts incomplete");return permit.freshJob;}
    /** Continue one retained replacement transaction from its actual phases. A completed step is
     * explicitly adopted; only untouched/unknown load steps may allocate a further new identity.
     * The Bridge still owns source repair, native publication and subsequent job validation. */
    Map<String,Object> continueReplacement(ContinuationPermit permit)throws Exception {
        if(permit==null||permit.owner!=this)throw bad("Exact continuation permit required");permit.check();requireAdoptionState();
        if(!definitions.containsKey(permit.attempt)||!documents.containsKey(permit.attempt))throw bad("Persist the exact replacement candidate before continuing load effects");
        Map<String,Object> current=progress(permit.attempt);
        if("completed".equals(effectivePhase(object(current.get("lineage")))))lineage.adoptCompletedReplacement(permit,permit.attempt);else lineage.continueReplacement(permit,permit.attempt);
        for(Object old:(List<?>)permit.capture.original.dependencies().get("material_load_ids")){
            permit.check();Map<String,Object> row=progressRow(progress(permit.attempt),"material",(String)old);
            if("completed".equals(effectivePhase(row)))material.adoptCompletedReplacement(permit,(String)old);else material.continueReplacement(permit,(String)old);
        }
        for(Object old:(List<?>)permit.capture.original.dependencies().get("board_load_ids")){
            permit.check();Map<String,Object> row=progressRow(progress(permit.attempt),"boards",(String)old);
            if("completed".equals(effectivePhase(row)))boards.adoptCompletedReplacement(permit,permit.capture.original.oldJob,permit.capture.candidate.job(),permit.attempt,(String)old);else boards.continueReplacement(permit,permit.capture.original.oldJob,permit.capture.candidate.job(),permit.attempt,(String)old);
        }
        return completeContinuation(permit);
    }
    /** Seal the exact union of this local decision's completed effects and explicit adoptions.
     * This does not bind a native Job, publish it, initialize it, or establish sensing readiness. */
    Map<String,Object> completeContinuation(ContinuationPermit permit)throws Exception {
        if(permit==null||permit.owner!=this)throw bad("Exact continuation permit required");permit.check();requireAdoptionState();
        if(permit.activeStep!=null||continuationReceipts.containsKey(permit.id))throw bad("Continuation step is active or the union is already sealed");
        Map<String,Object> record=continuationOutcomeRecord(continuationIntents.get(permit.id),progress(permit.attempt));material.requireReady();permit.check();requireAdoptionState();
        try{
            append("faulted_job_replacement_continuation_outcome",record);permit.check();requireAdoptionState();material.requireReady();
            if(!same(record,continuationOutcomeRecord(continuationIntents.get(permit.id),progress(permit.attempt))))throw bad("Continuation dependencies changed during compound publication");
            return continuationReceipts.get(permit.id);
        }catch(Exception|Error failure){permit.close();throw failure;}
    }
    Map<String,Object> continuationReceipt(String continuationId){return continuationReceipts.get(continuationId);}
    private static String durableProgressDigest(Map<String,Object> progress)throws Exception {return digest(object(durableContinuationCapture(map("progress",progress)).get("progress")));}
    private Map<String,Object> continuationOutcomeRecord(Map<String,Object> decision,Map<String,Object> current)throws Exception {return continuationOutcomeRecord(decision,current,true);}
    private Map<String,Object> continuationOutcomeRecord(Map<String,Object> decision,Map<String,Object> current,boolean active)throws Exception {
        if(decision==null)throw bad("Continuation outcome has no forced decision");String id=uuid(decision.get("continuation_id")),attempt=uuid(decision.get("replacement_attempt_id"));if(active)requireSameActiveAdmission(object(decision.get("local_admission")));
        Map<String,Object> definition=definitions.get(attempt),document=documents.get(attempt),original=intents.get(attempt);
        if(definition==null||document==null||original==null)throw bad("A forced candidate definition and document are required before sealing continuation");
        Map<String,Object> lin=object(current.get("lineage"));if(!"completed".equals(effectivePhase(lin)))throw bad("Replacement lineage is incomplete");
        Map<String,Object> lineageReceipt=completionDescriptor(id,"lineage",lin);List<Object> materials=new ArrayList<>(),boardReceipts=new ArrayList<>();Set<String> lineageLoads=new TreeSet<>();
        for(String kind:List.of("material","boards"))for(Object raw:(List<?>)object(current.get(kind)).get("rows")){
            Map<String,Object> row=object(raw);if(!"completed".equals(effectivePhase(row)))throw bad("Continuation still has untouched or unknown loads");
            Map<String,Object> effective=object(row.getOrDefault("effective_new_load",row.get("new_load")));
            if(!"loaded".equals(effective.get("state"))||!Objects.equals(effective.get("load_id"),row.get("current_load_id"))||!same(effective,row.get("current_load")))throw bad("Continuation completed load is no longer current");
            (kind.equals("material")?materials:boardReceipts).add(completionDescriptor(id,kind,row));
            if(kind.equals("boards")){
                if(row.get("new_load") instanceof Map)lineageLoads.add(uuid(object(row.get("new_load")).get("load_id")));
                if(row.get("continuation_chain") instanceof List)for(Object step:(List<?>)row.get("continuation_chain"))lineageLoads.add(uuid(object(step).get("new_load_id")));
            }
        }
        lineage.validateContinuationCompletion((String)lineageReceipt.get("receipt_type"),(String)lineageReceipt.get("receipt_id"),lineageLoads);
        return frozen(map("schema_version",1,"continuation_id",id,"replacement_attempt_id",attempt,"task_id",decision.get("task_id"),"recovery_operation_id",decision.get("recovery_operation_id"),"fault_set_sha256",decision.get("fault_set_sha256"),"continuation_capture_sha256",decision.get("continuation_capture_sha256"),"original_recovery_operation_id",decision.get("original_recovery_operation_id"),"original_fault_set_sha256",decision.get("original_fault_set_sha256"),"original_capture_sha256",original.get("capture_sha256"),"definition_sha256",digest(definition),"document_sha256",digest(document),"progress_sha256",durableProgressDigest(current),"lineage_receipt",lineageReceipt,"material_receipts",materials,"board_receipts",boardReceipts,"original_outcomes_preserved",true,"new_attempt_requires_validation",true,"native_job_initialized",false,"execution_authority_restored",false));
    }
    private Map<String,Object> completionDescriptor(String continuation,String kind,Map<String,Object> row)throws Exception {
        String prefix=kind.equals("material")?"material":kind.equals("boards")?"board":"job_lineage",adoptionType=prefix+"_continuation_adoption",mutationType=kind.equals("lineage")?"job_lineage_continuation_replacement":prefix+"_continuation_outcome";
        String old=uuid(row.get(kind.equals("lineage")?"original_job_id":"old_load_id"));Map<String,Object> selected=null;
        // An explicit adoption takes precedence if this same decision also created the load.
        // Both records remain in history; neither may refer to a prior local decision here.
        for(Map<String,Object> event:continuationAdoptions.values())if(adoptionType.equals(event.get("type"))){Map<String,Object> p=object(event.get("payload"));if(continuation.equals(p.get("continuation_id"))&&old.equals(p.get(kind.equals("lineage")?"old_job_id":"old_load_id"))){if(selected!=null)throw bad("Repeated completed-step adoption");selected=event;}}
        if(selected==null)for(Map<String,Object> event:continuationStepOutcomes.values())if(mutationType.equals(event.get("type"))){Map<String,Object> p=object(event.get("payload"));if(continuation.equals(p.get("continuation_id"))&&old.equals(p.get(kind.equals("lineage")?"replaces_attempt":"old_load_id"))){if(selected!=null)throw bad("Repeated completed continuation effect");selected=event;}}
        if(selected==null)throw bad("Every completed dependency requires this decision's exact effect or explicit adoption");
        Map<String,Object> p=object(selected.get("payload"));String type=(String)selected.get("type"),receipt=uuid(p.get("receipt_id"));boolean adoption=type.equals(adoptionType);Map<String,Object> nativeRecord;
        if(kind.equals("lineage"))nativeRecord=adoption?lineage.continuationAdoptionReceipt(receipt):lineage.continuationReplacementReceipt(receipt);
        else if(kind.equals("material"))nativeRecord=adoption?material.continuationAdoptionReceipt(receipt):material.continuationMutationReceipt(receipt);
        else nativeRecord=adoption?boards.continuationAdoptionReceipt(receipt):boards.continuationStepOutcome(receipt);
        if(nativeRecord==null||!same(p,nativeRecord))throw bad("Continuation component has no exact committed native receipt");
        Map<String,Object> result=map("kind",kind.equals("boards")?"board":kind,"receipt_type",type,"receipt_id",receipt,"receipt_sha256",digest(p),"old_outcome_preserved",true);
        if(kind.equals("lineage")){
            Map<String,Object> effective=object(row.getOrDefault("effective_receipt",row.get("receipt")));String parent=adoption?uuid(p.get("parent_receipt_id")):receipt;
            if(!parent.equals(effective.get("receipt_id"))||!Objects.equals(row.get("replacement_job_id"),p.get("new_job_id"))||!Objects.equals(effective.get("new_lineage_id"),p.get("new_lineage_id")))throw bad("Selected lineage receipt is not the effective retirement");
            result.put("old_job_id",old);result.put("new_job_id",p.get("new_job_id"));
        }else{
            Map<String,Object> effective=object(row.getOrDefault("effective_new_load",row.get("new_load")));Object load=adoption?(kind.equals("material")?object(p.get("adopted_load")).get("load_id"):p.get("new_load_id")):object(p.get("new_load")).get("load_id");
            if(!Objects.equals(effective.get("load_id"),load))throw bad("Selected load receipt belongs to an earlier generation");result.put("old_load_id",old);result.put("new_load_id",uuid(load));
        }
        return frozen(result);
    }
    private Runnable prepareContinuationOutcome(Map<String,Object> supplied)throws Exception {
        Map<String,Object> p=frozen(supplied);String id=uuid(p.get("continuation_id"));Map<String,Object> decision=continuationIntents.get(id);
        if(decision==null||continuationReceipts.containsKey(id)||continuationReceipts.size()>=MAX_REPLACEMENTS)throw bad("Continuation outcome is missing, duplicated or over capacity");
        if(!same(p,continuationOutcomeRecord(decision,progress(uuid(decision.get("replacement_attempt_id"))))))throw bad("Continuation outcome changes the exact completed dependency union");
        return ()->{continuationReceipts.put(id,p);generation++;};
    }
    private String latestContinuationPublication(String attempt){String found=null;for(Map.Entry<String,Map<String,Object>> row:continuationPublicationIntents.entrySet())if(attempt.equals(row.getValue().get("replacement_attempt_id")))found=row.getKey();return found;}
    private List<Object> publicationHistory(String attempt)throws Exception {
        List<Object> rows=new ArrayList<>();
        if(publicationIntents.containsKey(attempt))rows.add(map("type","faulted_job_replacement_publication_intent","publication_id",attempt,"sha256",digest(publicationIntents.get(attempt))));
        if(publications.containsKey(attempt))rows.add(map("type","faulted_job_replacement_publication_outcome","publication_id",attempt,"sha256",digest(publications.get(attempt))));
        for(Map.Entry<String,Map<String,Object>> row:continuationPublicationIntents.entrySet())if(attempt.equals(row.getValue().get("replacement_attempt_id"))){rows.add(map("type","faulted_job_replacement_continuation_publication_intent","publication_id",row.getKey(),"sha256",digest(row.getValue())));if(continuationPublications.containsKey(row.getKey()))rows.add(map("type","faulted_job_replacement_continuation_publication_outcome","publication_id",row.getKey(),"sha256",digest(continuationPublications.get(row.getKey()))));}
        return rows;
    }
    private Map<String,Object> continuationPublicationRecord(Map<String,Object> decision,String publication)throws Exception {
        String id=uuid(decision.get("continuation_id")),attempt=uuid(decision.get("replacement_attempt_id"));Map<String,Object> receipt=continuationReceipts.get(id);
        if(receipt==null||!same(receipt,continuationOutcomeRecord(decision,progress(attempt))))throw bad("Publication requires this decision's exact current sealed continuation");
        return frozen(map("schema_version",1,"publication_id",uuid(publication),"continuation_id",id,"replacement_attempt_id",attempt,"task_id",decision.get("task_id"),"recovery_operation_id",decision.get("recovery_operation_id"),"fault_set_sha256",decision.get("fault_set_sha256"),"continuation_outcome_sha256",digest(receipt),"definition_sha256",receipt.get("definition_sha256"),"document_sha256",receipt.get("document_sha256"),"progress_sha256",receipt.get("progress_sha256"),"prior_publications",publicationHistory(attempt),"state","publication_pending","native_job_initialized",false,"execution_authority_restored",false));
    }
    Publication beginContinuationPublication(ContinuationPermit permit)throws Exception {
        if(permit==null||permit.owner!=this)throw bad("Exact live continuation permit required");permit.check();requireAdoptionState();material.requireReady();
        for(Map<String,Object> old:continuationPublicationIntents.values())if(permit.id.equals(old.get("continuation_id")))throw bad("This local decision already attempted publication; make a new local decision");
        for(Publication old:livePublications.values())if(permit.attempt.equals(old.parent.attempt))throw bad("Previous native publication capability remains live");
        String id=UUID.randomUUID().toString();Map<String,Object> record=continuationPublicationRecord(continuationIntents.get(permit.id),id);
        try{append("faulted_job_replacement_continuation_publication_intent",record);Publication publication=new Publication(this,permit,id,record);livePublications.put(id,publication);publication.check();return publication;}
        catch(Exception|Error failure){permit.close();throw failure;}
    }
    /** Transfer resource ownership before the host installs the exact retained native candidate.
     * The forced intent remains pending until the host and staged board binding are verified. */
    Job replacementJobForPublication(Publication publication)throws Exception {requirePublication(publication);publication.check();publication.parent.capture.candidate.publish();return publication.parent.capture.candidate.job();}
    Job bindContinuationForPublication(Publication publication)throws Exception {
        requirePublication(publication);publication.check();Job candidate=publication.parent.capture.candidate.job();
        boards.bindContinuedReplacement(publication,publication.parent.capture.original.oldJob,candidate,publication.attemptId());
        return replacementJobForPublication(publication);
    }
    private void requirePublication(Publication publication)throws Exception {if(publication==null||publication.owner!=this)throw bad("Exact owned native publication capability required");}
    void finishContinuationPublication(Publication publication)throws Exception {
        requirePublication(publication);
        try{
            publication.check();Job candidate=publication.parent.capture.candidate.job();String attempt=publication.attemptId();
            if(publication.successful||continuationPublications.containsKey(publication.id))throw bad("Publication already completed");
            boards.requireContinuationBinding(publication,candidate,attempt);if(!jobAuthority.installed(candidate,attempt))throw bad("Host has not installed the exact native replacement Job and attempt identity");
            Map<String,Object> outcome=mutable(publication.record);outcome.put("state","native_job_published");publication.check();
            append("faulted_job_replacement_continuation_publication_outcome",outcome);
            publication.check();boards.requireContinuationBinding(publication,candidate,attempt);if(!jobAuthority.installed(candidate,attempt))throw bad("Host native job changed during publication");
            publication.parent.expectedProgress=frozen(progress(attempt));publication.successful=true;publishedWitnesses.put(publication.id,publication);
            try{boards.requireReady(candidate);publication.parent.check();}catch(Exception|Error failure){publication.successful=false;publishedWitnesses.remove(publication.id);throw failure;}
        }catch(Exception|Error failure){publication.close();throw failure;}
    }
    Map<String,Object> publicationReceipt(String publicationId){return continuationPublications.get(publicationId);}
    private Runnable prepareContinuationPublication(String type,Map<String,Object> supplied)throws Exception {
        Map<String,Object> p=frozen(supplied);String id=uuid(p.get("publication_id")),continuation=uuid(p.get("continuation_id"));Map<String,Object> decision=continuationIntents.get(continuation);
        if(decision==null||continuationPublications.containsKey(id))throw bad("Native publication is foreign or already complete");requireSameActiveAdmission(object(decision.get("local_admission")));
        if(type.equals("faulted_job_replacement_continuation_publication_intent")){
            if(continuationPublicationIntents.containsKey(id)||continuationPublicationIntents.size()>=MAX_REPLACEMENTS)throw bad("Native publication identity or capacity conflict");
            for(Map<String,Object> old:continuationPublicationIntents.values())if(continuation.equals(old.get("continuation_id")))throw bad("Repeated native publication under the same local decision");
            if(!same(p,continuationPublicationRecord(decision,id)))throw bad("Native publication does not capture exact current continuation/history");
            return ()->{continuationPublicationIntents.put(id,p);generation++;};
        }
        if(!type.equals("faulted_job_replacement_continuation_publication_outcome"))throw bad("Unknown native continuation publication event");
        Map<String,Object> comparison=mutable(p);comparison.put("state","publication_pending");Map<String,Object> original=continuationPublicationIntents.get(id);
        if(original==null||!"native_job_published".equals(p.get("state"))||!same(original,comparison)||!id.equals(latestContinuationPublication(uuid(p.get("replacement_attempt_id")))))throw bad("Native publication outcome lacks its exact current intent");
        Map<String,Object> receipt=continuationReceipts.get(continuation);if(receipt==null||!same(receipt,continuationOutcomeRecord(decision,progress((String)p.get("replacement_attempt_id")))))throw bad("Native publication's completed dependency union changed");
        return ()->{continuationPublications.put(id,p);generation++;};
    }
    Map<String,Object> continuationDispositions(String publicationId,String nozzleMaterial)throws Exception {
        if(!Set.of("none-present","disposed-synthetic").contains(nozzleMaterial))throw bad("Unknown nozzle disposition");Map<String,Object> publication=continuationPublications.get(uuid(publicationId));if(publication==null)throw bad("Native continuation publication has no forced outcome");String id=(String)publication.get("continuation_id");Map<String,Object> receipt=continuationReceipts.get(id);if(receipt==null)throw bad("Publication has no exact sealed continuation");
        return frozen(map("sensing","fresh-observed-empty","nozzle_material",nozzleMaterial,"feeder_loads",receipt.get("material_receipts"),"board_loads",receipt.get("board_receipts"),"replacement_attempt_id",receipt.get("replacement_attempt_id"),"unresolved_dependencies",List.of(),"continuation_id",id,"continuation_receipt_sha256",digest(receipt),"publication_id",publicationId,"publication_receipt_sha256",digest(publication)));
    }
    boolean verifiedContinuation(Map<String,Object> capture,Map<String,Object> dispositions,boolean replay)throws IOException {
        try{
            String id=uuid(dispositions.get("continuation_id")),publicationId=uuid(dispositions.get("publication_id"));Map<String,Object> decision=continuationIntents.get(id),publication=continuationPublications.get(publicationId),receipt=continuationReceipts.get(id);
            if(decision==null||publication==null||receipt==null||!id.equals(publication.get("continuation_id"))||!publicationId.equals(latestContinuationPublication((String)decision.get("replacement_attempt_id"))))return false;
            Map<String,Object> captured=object(decision.get("capture"));if(!same(captured.get("dependencies"),capture.get("dependencies"))||!same(captured.get("job_context"),capture.get("job_context"))||!NativeSensingReconciliation.digest(capture).equals(decision.get("fault_set_sha256")))return false;
            if(!same(dispositions,continuationDispositions(publicationId,(String)dispositions.get("nozzle_material")))||!same(receipt,continuationOutcomeRecord(decision,progress((String)decision.get("replacement_attempt_id")),false)))return false;
            Map<String,Object> operation=operations.get(decision.get("recovery_operation_id"));if(operation==null||!"succeeded".equals(operation.get("state"))||!Boolean.TRUE.equals(object(operation.get("native_completion")).get("native_wrapper_completed"))||!Boolean.TRUE.equals(object(operation.get("native_completion")).get("native_wrapper_succeeded")))return false;
            for(String key:List.of("operation_id","request_id","method","task_id","bridge_instance_id","config_revision","request_digest","reconciliation_request_id"))if(!Objects.equals(object(decision.get("local_admission")).get(key),operation.get(key)))return false;
            if(!replay){
                Publication witness=publishedWitnesses.get(publicationId);if(witness==null)return false;
                witness.authorizeCompletedRead(material);material.requireReadyForPublication(witness);
                witness.parent.capture.candidate.requireCurrentForPublication(witness);boards.requireReadyForPublication(witness.parent.capture.candidate.job(),witness);
                if(!witness.parent.capture.original.oldHistory.equals(witness.parent.capture.original.oldJob.getPlacedStatusSnapshot()))return false;
                witness.authorizeCompletedRead(material);
            }
            return true;
        }catch(Exception failure){if(failure instanceof IOException)throw(IOException)failure;throw new IOException("Native continuation disposition could not be verified",failure);}
    }
    Map<String,Object> dispositions(String attempt,String nozzleMaterial)throws Exception {
        Map<String,Object> receipt=receipts.get(attempt);if(receipt==null)throw bad("Replacement outcome not forced");if(!Set.of("none-present","disposed-synthetic").contains(nozzleMaterial))throw bad("Unknown nozzle disposition");
        return frozen(map("sensing","fresh-observed-empty","nozzle_material",nozzleMaterial,"feeder_loads",receipt.get("material_receipts"),"board_loads",receipt.get("board_receipts"),"replacement_attempt_id",attempt,"unresolved_dependencies",List.of()));
    }
    /** Mandatory concrete authority for NativeSensingReconciliation.DispositionAuthority. */
    boolean verified(Map<String,Object> faultCapture,Map<String,Object> dispositions,boolean replay)throws IOException {
        if(dispositions.containsKey("continuation_id"))return verifiedContinuation(faultCapture,dispositions,replay);
        try {
            Object attempt=dispositions.get("replacement_attempt_id");if(!(attempt instanceof String))return false;Map<String,Object> receipt=receipts.get(attempt),intent=intents.get(attempt);if(receipt==null||intent==null)return false;
            Map<String,Object> capture=object(intent.get("capture"));if(!same(capture.get("dependencies"),faultCapture.get("dependencies"))||!Objects.equals(capture.get("job_id"),object(faultCapture.get("job_context")).get("job_id"))||!Objects.equals(intent.get("fault_set_sha256"),NativeSensingReconciliation.digest(faultCapture)))return false;
            validateOutcome(receipt);if(!same(receipt.get("material_receipts"),dispositions.get("feeder_loads"))||!same(receipt.get("board_receipts"),dispositions.get("board_loads"))||!List.of().equals(dispositions.get("unresolved_dependencies")))return false;
            if(!replay){Permit permit=livePermits.get(attempt);if(permit==null||permit.closed||!same(permit.capture.oldHistory,permit.capture.oldJob.getPlacedStatusSnapshot()))return false;}
            return true;
        }catch(Exception e){if(e instanceof IOException)throw(IOException)e;throw new IOException("Replacement disposition could not be verified",e);}
    }
    private void append(String type,Map<String,Object> payload)throws Exception {Runnable commit=prepare(type,payload);try{sink.appendAndForce(type,frozen(payload));commit.run();}catch(Exception|Error failure){faulted=true;throw failure;}}
    void recover(String type,Map<String,Object> payload)throws Exception {prepare(type,payload).run();}
    /** Historical data only. A caller may reconstruct inert objects, but must separately obtain
     * a fresh local reattachment capability before installing any runtime transaction witness. */
    Map<String,Object> reconstructionBundle(String attempt)throws Exception {
        Map<String,Object> intent=intents.get(uuid(attempt));
        if(intent==null||!intent.containsKey("reconstruction_bundle"))throw bad("This transaction has no atomically recorded reconstruction bundle");
        validateReconstructionIntent(intent,object(intent.get("capture")));
        return object(intent.get("reconstruction_bundle"));
    }
    private Map<String,Object> restartFacts(String attempt)throws Exception {return restartFacts(attempt,null);}
    private Map<String,Object> restartFacts(String attempt,String excludedAttachment)throws Exception {
        Map<String,Object> facts=mutable(continuationFacts(attempt,true)),prior=object(facts.get("prior_operations")),attachments=new TreeMap<>(),observations=new TreeMap<>();
        for(Map.Entry<String,Map<String,Object>> entry:restartAttachments.entrySet())if(attempt.equals(entry.getValue().get("replacement_attempt_id"))&&!entry.getKey().equals(excludedAttachment)){
            Map<String,Object> receipt=entry.getValue();attachments.put(entry.getKey(),receipt);addPriorRecovery(attempt,prior,uuid(receipt.get("recovery_operation_id")),uuid(receipt.get("task_id")),true);
        }
        for(Map.Entry<String,Map<String,Object>> entry:restartObservations.entrySet())if(attachments.containsKey(object(entry.getValue().get("payload")).get("reattachment_id")))observations.put(entry.getKey(),entry.getValue());
        TreeSet<String> operations=new TreeSet<>(stringIds(object(facts.get("dependencies")).get("operation_ids")));operations.addAll(prior.keySet());object(facts.get("dependencies")).put("operation_ids",new ArrayList<>(operations));
        facts.put("reconstruction_sha256",hash(intents.get(attempt).get("reconstruction_sha256")));facts.put("prior_reattachments",attachments);facts.put("prior_restart_observations",observations);
        Map<String,Object> dispositions=priorDispositionRecords(attempt,excludedAttachment);if(!dispositions.isEmpty())facts.put("prior_process_dispositions",dispositions);facts.put("prior_wrapper_completion_inferred",false);NativeSensingReconciliation.validateDependencies(object(facts.get("dependencies")));return frozen(facts);
    }
    RestartCapture captureRestart(RestartStage stage)throws Exception {
        nativeOwner();if(stage==null||stage.owner!=this)throw bad("Foreign restart stage");stage.requireCurrent();return new RestartCapture(this,stage,restartFacts(stage.attempt));
    }
    RestartPermit beginRestart(RestartCapture capture,NativeSensingReconciliation coordinator,NativeSensingReconciliation.Permit recovery,Object localOwner)throws Exception {
        nativeOwner();coordinator.requirePermit(recovery,localOwner);if(capture==null||capture.owner!=this)throw bad("Foreign restart capture");capture.stage.requireCurrent();String attempt=capture.stage.attempt;
        if(liveRestarts.containsKey(attempt)||retainedRestarts.containsKey(attempt)||!same(capture.payload,restartFacts(attempt)))throw bad("Restart capture is stale or already attached");
        Map<String,Object> task=object(coordinator.snapshot(recovery.taskId()).get("task")),scope=object(task.get("fault_set")),context=object(task.get("restart_context"));
        if(!NativeSensingReconciliation.RESTART_KIND.equals(task.get("recovery_kind"))||!same(capture.dependencies(),scope.get("dependencies"))||!same(capture.jobContext(),scope.get("job_context"))||!attempt.equals(context.get("replacement_attempt_id"))||!capture.digest.equals(context.get("restart_capture_sha256")))throw bad("Fresh local restart task does not bind its exact native capture");
        Map<String,Object> admission=operations.get(recovery.operationId());if(admission==null||!Objects.equals(admission.get("task_id"),recovery.taskId())||!Objects.equals(admission.get("bridge_instance_id"),scope.get("bridge_instance_id"))||!Objects.equals(admission.get("config_revision"),scope.get("config_revision"))||!Objects.equals(admission.get("request_digest"),task.get("fault_set_sha256"))||!Objects.equals(admission.get("reconciliation_request_id"),task.get("request_id")))throw bad("Restart admission differs from its exact fresh sensing task");requireSameActiveAdmission(admission);
        validateRestartInstance(capture.payload,admission);
        for(Map.Entry<String,Object> entry:object(capture.payload.get("prior_operations")).entrySet())if(Objects.equals(object(entry.getValue()).get("bridge_instance_id"),admission.get("bridge_instance_id"))&&!terminalAuthority.completed(entry.getKey()))throw bad("Earlier native callback in this process is not independently complete");
        String id=UUID.randomUUID().toString();Map<String,Object> original=intents.get(attempt);
        Map<String,Object> record=map("schema_version",1,"reattachment_id",id,"replacement_attempt_id",attempt,"task_id",recovery.taskId(),"recovery_operation_id",recovery.operationId(),"fault_set_sha256",task.get("fault_set_sha256"),"restart_capture_sha256",capture.digest,"original_recovery_operation_id",original.get("recovery_operation_id"),"original_fault_set_sha256",original.get("fault_set_sha256"),"capture",capture.payload,"local_admission",admission,"state","reattachment_authorized","scope","inactive-native-graphs","original_outcomes_preserved",true,"prior_wrapper_completion_inferred",false,"execution_authority_restored",false,"native_job_installed",false,"sensing_authority_restored",false,"load_presence_verified",false);
        RestartPermit permit=new RestartPermit(this,capture,coordinator,recovery,localOwner,id,(String)task.get("fault_set_sha256"));Capture oldCapture=new Capture(this,capture.stage.graphs.original(),object(original.get("capture")));
        if(JSON.toJson(frozen(record)).getBytes(StandardCharsets.UTF_8).length>Bridge.MAX_JOURNAL_RECORD_BYTES-65536)throw bad("Restart authorization exceeds the bounded journal record");
        append("faulted_job_replacement_restart_attachment",record);
        // Nothing process-local transfers before force returns. An uncertain append leaves the
        // stage inactive and owned by its original caller for cleanup or fresh-process recovery.
        coordinator.requirePermit(recovery,localOwner);capture.stage.requireCurrent();requireSameActiveAdmission(admission);
        if(!same(capture.payload,restartFacts(attempt,id)))throw bad("Restart dependency facts changed during forced authorization");
        capture.stage.closed=true;restartStages.remove(attempt,capture.stage);attachedGraphs.put(attempt,capture.stage.graphs);retainedCaptures.put(attempt,oldCapture);candidates.put(attempt,capture.stage.graphs.candidate());retainedRestarts.put(attempt,permit);liveRestarts.put(attempt,permit);permit.check();return permit;
    }
    private void validateRestartInstance(Map<String,Object> capture,Map<String,Object> admission)throws Exception {
        String current=uuid(admission.get("bridge_instance_id")),op=uuid(admission.get("operation_id")),attempt=uuid(capture.get("replacement_attempt_id"));Map<String,Object> prior=object(capture.get("prior_operations"));
        if(prior.containsKey(op))throw bad("Restart must use a new local operation");Map<String,Object> original=object(prior.get(intents.get(attempt).get("recovery_operation_id")));
        if(current.equals(uuid(original.get("bridge_instance_id"))))throw bad("Restart attachment requires a new Bridge instance");
        for(Object row:prior.values()){
            Map<String,Object> previous=object(row);uuid(previous.get("bridge_instance_id"));
            if(current.equals(previous.get("bridge_instance_id"))&&(!(previous.get("native_completion") instanceof Map)||!Boolean.TRUE.equals(object(previous.get("native_completion")).get("native_wrapper_completed"))))throw bad("An earlier callback in this process lacks its actual terminal wrapper record");
        }
    }
    private static boolean hasWrapperRecord(Map<String,Object> operation)throws IOException {
        return operation!=null&&operation.get("native_completion") instanceof Map&&Boolean.TRUE.equals(object(operation.get("native_completion")).get("native_wrapper_completed"));
    }
    private Map<String,Object> priorDispositionRecords(String attempt,String excludedAttachment)throws Exception {
        Map<String,Object> result=new TreeMap<>();for(Map.Entry<String,Map<String,Object>> entry:restartPriorDispositions.entrySet())if(attempt.equals(entry.getValue().get("replacement_attempt_id"))&&!Objects.equals(excludedAttachment,entry.getValue().get("reattachment_id")))result.put(entry.getKey(),entry.getValue());return frozen(result);
    }
    private Map<String,Object> oldProcessOperations(Map<String,Object> capture,Map<String,Object> admission)throws Exception {
        Map<String,Object> result=new TreeMap<>();String instance=uuid(admission.get("bridge_instance_id"));
        for(Map.Entry<String,Object> entry:object(capture.get("prior_operations")).entrySet()){
            Map<String,Object> operation=object(entry.getValue());uuid(entry.getKey());String oldInstance=uuid(operation.get("bridge_instance_id"));
            if(!same(operation,operations.get(entry.getKey()))||!Set.of("succeeded","failed","outcome_unknown").contains(operation.get("state")))throw bad("Prior process operation no longer matches its exact terminal historical record");
            if(!instance.equals(oldInstance))result.put(entry.getKey(),operation);
        }
        if(result.isEmpty())throw bad("No exact prior-process recovery operations were captured");return frozen(result);
    }
    /** Explicitly discontinues old process recovery callbacks under a new exclusive host owner.
     * The original records remain unchanged; this receipt never asserts wrapper completion. */
    Map<String,Object> disposePriorProcess(RestartPermit permit)throws Exception {
        if(permit==null||permit.owner!=this)throw bad("Foreign restart disposition permit");permit.check();
        Map<String,Object> attachment=restartAttachments.get(permit.id),admission=object(attachment.get("local_admission")),prior=oldProcessOperations(permit.capture.payload,admission);
        for(Map<String,Object> old:restartPriorDispositions.values())if(permit.id.equals(old.get("reattachment_id")))throw bad("Prior process disposition already recorded for this decision");
        String current=uuid(admission.get("bridge_instance_id")),revision=(String)admission.get("config_revision"),job=(String)permit.capture.jobContext().get("job_id");
        restartAuthority.requireOwnership(permit.recovery.operationId(),current,revision,permit.original(),job,prior);
        Map<String,Object> record=mutable(permit.authority());record.putAll(map("schema_version",1,"receipt_id",UUID.randomUUID().toString(),"task_id",permit.recovery.taskId(),"local_admission",admission,"restart_attachment_sha256",digest(attachment),"prior_operations",prior,"prior_operations_sha256",digest(prior),"disposition","old-process-recovery-discontinued","ownership_scope","exclusive-current-journal-and-native-callback","original_outcomes_preserved",true,"prior_wrapper_completion_inferred",false,"prior_process_death_inferred",false,"execution_authority_restored",false));
        try {
            append("faulted_job_replacement_restart_prior_process_disposition",record);
            permit.check();restartAuthority.requireOwnership(permit.recovery.operationId(),current,revision,permit.original(),job,prior);
            if(!same(prior,oldProcessOperations(permit.capture.payload,admission))||!same(record,restartPriorDispositions.get(record.get("receipt_id"))))throw bad("Prior process disposition was not committed exactly");
            priorDispositionWitnesses.put((String)record.get("receipt_id"),permit);return frozen(record);
        }catch(Exception|Error failure){permit.close();throw failure;}
    }
    private Runnable preparePriorDisposition(Map<String,Object> p)throws Exception {
        keys(p,"schema_version","receipt_id","reattachment_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","task_id","local_admission","restart_attachment_sha256","prior_operations","prior_operations_sha256","disposition","ownership_scope","original_outcomes_preserved","prior_wrapper_completion_inferred","prior_process_death_inferred","execution_authority_restored");
        String receipt=uuid(p.get("receipt_id")),attachmentId=uuid(p.get("reattachment_id"));Map<String,Object> attachment=restartAttachments.get(attachmentId);
        if(number(p.get("schema_version"))!=1||attachment==null||restartPriorDispositions.containsKey(receipt)||restartPriorDispositions.size()>=MAX_REPLACEMENTS||!"old-process-recovery-discontinued".equals(p.get("disposition"))||!"exclusive-current-journal-and-native-callback".equals(p.get("ownership_scope"))||!Boolean.TRUE.equals(p.get("original_outcomes_preserved")))throw bad("Invalid prior-process disposition identity or scope");
        for(String flag:List.of("prior_wrapper_completion_inferred","prior_process_death_inferred","execution_authority_restored"))if(!Boolean.FALSE.equals(p.get(flag)))throw bad("Prior-process disposition cannot invent completion, process death or execution authority");
        for(String key:List.of("replacement_attempt_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","task_id","local_admission"))if(!same(attachment.get(key),p.get(key)))throw bad("Prior-process disposition changes the fresh restart authority");
        requireSameActiveAdmission(object(attachment.get("local_admission")));
        Map<String,Object> prior=object(p.get("prior_operations"));if(!digest(attachment).equals(hash(p.get("restart_attachment_sha256")))||!digest(prior).equals(hash(p.get("prior_operations_sha256")))||!same(prior,oldProcessOperations(object(attachment.get("capture")),object(attachment.get("local_admission")))))throw bad("Prior-process disposition is outside its exact old-instance capture");
        for(Map<String,Object> old:restartPriorDispositions.values())if(attachmentId.equals(old.get("reattachment_id")))throw bad("Restart prior-process disposition is repeated");
        return ()->{restartPriorDispositions.put(receipt,p);generation++;};
    }
    private Map<String,Object> requireSealedPriorDisposition(RestartPermit permit)throws Exception {
        for(Map.Entry<String,RestartPermit> entry:priorDispositionWitnesses.entrySet())if(entry.getValue()==permit){Map<String,Object> record=restartPriorDispositions.get(entry.getKey());if(record!=null&&permit.id.equals(record.get("reattachment_id"))&&same(record.get("prior_operations"),oldProcessOperations(permit.capture.payload,object(record.get("local_admission")))))return record;}
        throw bad("No exact current-process prior-operation disposition witness");
    }
    /** Replay can check a historical relationship; it cannot install the process-local witness.
     * Live callers also require the NEW restart callback to be independently complete. */
    boolean priorProcessDisposedForContinuation(String attempt,String operationId,boolean replay)throws Exception {
        uuid(attempt);uuid(operationId);Map<String,Object> actual=operations.get(operationId);if(actual==null)return false;
        for(Map.Entry<String,Map<String,Object>> entry:restartPriorDispositions.entrySet()){
            Map<String,Object> record=entry.getValue();if(!attempt.equals(record.get("replacement_attempt_id"))||!same(actual,object(record.get("prior_operations")).get(operationId)))continue;
            Map<String,Object> admission=object(record.get("local_admission"));if(Objects.equals(actual.get("bridge_instance_id"),admission.get("bridge_instance_id")))continue;
            if(replay)return true;RestartPermit witness=priorDispositionWitnesses.get(entry.getKey());if(witness==null||liveRestarts.containsKey(attempt))continue;
            witness.requireRetained();if(!witness.hostAttached||!jobAuthority.installed(witness.original(),(String)witness.capture.jobContext().get("job_id"))&&!jobAuthority.installed(witness.candidate().job(),attempt))continue;
            Map<String,Object> restart=operations.get(admission.get("operation_id"));if(restart==null||!Set.of("succeeded","failed","outcome_unknown").contains(restart.get("state"))||!hasWrapperRecord(restart)||!terminalAuthority.completed((String)admission.get("operation_id")))continue;
            return true;
        }
        return false;
    }
    private boolean priorRecoveryCompleted(String attempt,String operation,boolean replay,String currentInstance)throws Exception {
        Map<String,Object> record=operations.get(operation);if(record!=null&&hasWrapperRecord(record)&&terminalAuthority.completed(operation))return true;
        if(currentInstance!=null&&record!=null&&currentInstance.equals(record.get("bridge_instance_id")))return false;
        if(!priorProcessDisposedForContinuation(attempt,operation,replay))return false;
        if(!replay&&currentInstance!=null){for(Map.Entry<String,RestartPermit> entry:priorDispositionWitnesses.entrySet())if(attempt.equals(entry.getValue().attempt)&&object(restartPriorDispositions.get(entry.getKey()).get("prior_operations")).containsKey(operation)&&currentInstance.equals(object(restartPriorDispositions.get(entry.getKey()).get("local_admission")).get("bridge_instance_id")))return true;return false;}
        return true;
    }
    Map<String,Object> priorProcessDisposition(String reattachmentId)throws Exception {
        uuid(reattachmentId);for(Map<String,Object> record:restartPriorDispositions.values())if(reattachmentId.equals(record.get("reattachment_id")))return record;throw bad("Restart has no forced prior-process disposition");
    }
    Map<String,Object> restartObservationFacts(RestartPermit permit)throws Exception {
        if(permit==null||permit.owner!=this)throw bad("Foreign restart observation permit");permit.check();Map<String,Object> disposition=requireSealedPriorDisposition(permit);
        if(!permit.hostAttached||!jobAuthority.installed(permit.original(),(String)permit.capture.jobContext().get("job_id")))throw bad("Exact inactive original host attachment is unconfirmed");
        Map<String,Object> facts=restartObservationFactsFor(permit.id);
        for(Object raw:(List<?>)facts.get("observations")){Map<String,Object> row=object(raw);if(!permit.accepted.containsKey(row.get("receipt_id")))throw bad("Restart observation lacks its current-process sealed receipt");}
        if(!Objects.equals(disposition.get("receipt_id"),facts.get("prior_process_disposition_id")))throw bad("Restart observation disposition witness differs");
        permit.observationFacts=facts;return facts;
    }
    private Map<String,Object> restartObservationFactsFor(String id)throws Exception {
        Map<String,Object> attachment=restartAttachments.get(uuid(id));if(attachment==null)throw bad("Unknown restart attachment");Map<String,Object> disposition=priorProcessDisposition(id),captured=object(attachment.get("capture"));
        Set<String> materialIds=new TreeSet<>();for(Object row:(List<?>)object(object(captured.get("progress")).get("material")).get("rows"))materialIds.add(uuid(object(row).get("old_load_id")));
        Set<String> observedMaterial=new TreeSet<>();int boardsSeen=0,sourceSeen=0;List<Map<String,Object>> descriptors=new ArrayList<>();
        for(Map.Entry<String,Map<String,Object>> entry:restartObservations.entrySet()){
            Map<String,Object> event=entry.getValue(),p=object(event.get("payload"));String type=(String)event.get("type");if(!id.equals(p.get("reattachment_id"))||type.endsWith("_intent"))continue;
            if(type.equals("material_restart_observation")){if(!observedMaterial.add(uuid(p.get("old_load_id"))))throw bad("Duplicate restart material observation");}
            else if(type.equals("board_restart_observation"))boardsSeen++;
            else if(type.equals("sensing_source_restart_bootstrap_returned"))sourceSeen++;
            else throw bad("Unknown restart observation completion type");
            descriptors.add(map("type",type,"receipt_id",entry.getKey(),"payload_sha256",digest(p)));
        }
        if(!observedMaterial.equals(materialIds)||boardsSeen!=1||sourceSeen!=1)throw bad("Restart completion requires exact material, board and source observation union");
        descriptors.sort(Comparator.comparing(row->(String)row.get("receipt_id")));
        return frozen(map("reattachment_id",id,"replacement_attempt_id",attachment.get("replacement_attempt_id"),"restart_capture_sha256",attachment.get("restart_capture_sha256"),"restart_attachment_sha256",digest(attachment),"prior_process_disposition_id",disposition.get("receipt_id"),"prior_process_disposition_sha256",digest(disposition),"observations",descriptors));
    }
    /** Completion-thread validation reads retained references and sealed records only. It grants
     * no sensor clearance/load readiness; a later native continuation revalidates full graphs. */
    boolean verifiedRestartObservations(Map<String,Object> faultCapture,Map<String,Object> restartContext,Map<String,Object> observations,boolean replay)throws IOException {
        try {
            String id=uuid(observations.get("reattachment_id"));Map<String,Object> attachment=restartAttachments.get(id);if(attachment==null||!same(observations,restartObservationFactsFor(id)))return false;
            if(!Objects.equals(restartContext.get("replacement_attempt_id"),attachment.get("replacement_attempt_id"))||!Objects.equals(restartContext.get("restart_capture_sha256"),attachment.get("restart_capture_sha256"))||!Objects.equals(NativeSensingReconciliation.digest(faultCapture),attachment.get("fault_set_sha256")))return false;
            Map<String,Object> op=operations.get(attachment.get("recovery_operation_id"));if(op==null||!"succeeded".equals(op.get("state"))||!hasWrapperRecord(op)||!Boolean.TRUE.equals(object(op.get("native_completion")).get("native_wrapper_succeeded")))return false;
            if(replay)return true;
            RestartPermit permit=retainedRestarts.get(attachment.get("replacement_attempt_id"));if(closed||faulted||permit==null||!id.equals(permit.id)||!same(permit.observationFacts,observations)||!permit.hostAttached||priorDispositionWitnesses.get(observations.get("prior_process_disposition_id"))!=permit||Configuration.get()!=config||config.getMachine()!=machine||machine.isEnabled()||machine.isBusy())return false;
            if(!jobAuthority.installed(permit.original(),(String)permit.capture.jobContext().get("job_id"))||!terminalAuthority.completed((String)attachment.get("recovery_operation_id")))return false;
            for(Object raw:(List<?>)observations.get("observations"))if(!permit.accepted.containsKey(object(raw).get("receipt_id")))return false;
            return true;
        }catch(Exception failure){if(failure instanceof IOException)throw(IOException)failure;throw new IOException("Restart observations could not be verified",failure);}
    }
    private Runnable prepareRestartAttachment(Map<String,Object> p)throws Exception {
        keys(p,"schema_version","reattachment_id","replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","restart_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","capture","local_admission","state","scope","original_outcomes_preserved","prior_wrapper_completion_inferred","execution_authority_restored","native_job_installed","sensing_authority_restored","load_presence_verified");
        String id=uuid(p.get("reattachment_id")),attempt=uuid(p.get("replacement_attempt_id")),operation=uuid(p.get("recovery_operation_id"));uuid(p.get("task_id"));hash(p.get("fault_set_sha256"));
        if(number(p.get("schema_version"))!=1||restartAttachments.containsKey(id)||restartAttachments.size()>=MAX_REPLACEMENTS||!"reattachment_authorized".equals(p.get("state"))||!"inactive-native-graphs".equals(p.get("scope"))||!Boolean.TRUE.equals(p.get("original_outcomes_preserved")))throw bad("Invalid restart attachment scope, identity or capacity");
        for(String flag:List.of("prior_wrapper_completion_inferred","execution_authority_restored","native_job_installed","sensing_authority_restored","load_presence_verified"))if(!Boolean.FALSE.equals(p.get(flag)))throw bad("Restart attachment cannot restore execution or invent observations");
        Map<String,Object> original=intents.get(attempt),captured=object(p.get("capture")),admission=operations.get(operation);
        if(original==null||!Objects.equals(original.get("recovery_operation_id"),p.get("original_recovery_operation_id"))||!Objects.equals(original.get("fault_set_sha256"),p.get("original_fault_set_sha256"))||!digest(captured).equals(hash(p.get("restart_capture_sha256")))||!same(durableContinuationCapture(captured),durableContinuationCapture(restartFacts(attempt))))throw bad("Restart authorization differs from its exact durable transaction");
        if(admission==null||!same(admission,p.get("local_admission"))||!Objects.equals(admission.get("task_id"),p.get("task_id"))||!Objects.equals(admission.get("request_digest"),p.get("fault_set_sha256")))throw bad("Restart authorization has no exact current local admission");requireSameActiveAdmission(admission);validateRestartInstance(captured,admission);
        return ()->{restartAttachments.put(id,p);generation++;};
    }
    /** Releases inactive graph resources only. Forced observations and unknown outcomes remain. */
    void detachRestart(RestartPermit permit)throws Exception {
        nativeOwner();if(permit==null||permit.owner!=this||retainedRestarts.get(permit.attempt)!=permit||machine.isEnabled()||jobAuthority.installed(permit.original(),(String)permit.capture.jobContext().get("job_id"))||jobAuthority.installed(permit.candidate().job(),permit.attempt)||liveContinuations.containsKey(permit.attempt))throw bad("Only an inactive owned restart attachment can detach");
        permit.close();retainedRestarts.remove(permit.attempt);retainedCaptures.remove(permit.attempt);candidates.remove(permit.attempt);attachedGraphs.remove(permit.attempt).close();
    }
    RestartStage stageRestart(String attempt)throws Exception {
        nativeOwner();uuid(attempt);
        if(machine.isEnabled()||retainedCaptures.containsKey(attempt)||candidates.containsKey(attempt)||restartStages.containsKey(attempt)||livePermits.containsKey(attempt)||liveContinuations.containsKey(attempt))throw bad("Restart staging requires a disabled, unattached transaction");
        Map<String,Object> intent=intents.get(attempt),bundle=reconstructionBundle(attempt),current=progress(attempt);
        NativeReplacementDocuments.Reconstructed graphs=NativeReplacementDocuments.reconstruct(config,bundle);
        RestartStage stage=new RestartStage(this,attempt,intent,current,graphs);
        try{restartStages.put(attempt,stage);stage.requireCurrent();return stage;}
        catch(Exception|Error failure){stage.close();throw failure;}
    }
    private static void validateReconstructionIntent(Map<String,Object> intent,Map<String,Object> capture)throws Exception {
        Map<String,Object> bundle=object(intent.get("reconstruction_bundle"));
        NativeReplacementDocuments.validateReconstruction(bundle);
        if(!digest(bundle).equals(hash(intent.get("reconstruction_sha256"))))throw bad("Replacement reconstruction digest differs");
        Map<String,Object> boardState=object(capture.get("boards"));
        if(!same(boardModels(boardState),bundle.get("original_board_models")))throw bad("Reconstructed graph differs from the captured board loads");
        Map<String,Object> history=object(bundle.get("original_history"));
        for(Object load:object(boardState.get("loads")).values())for(Map.Entry<String,Object> entry:object(object(load).get("placed_history")).entrySet())
            if(Boolean.TRUE.equals(entry.getValue())&&!Boolean.TRUE.equals(history.get(entry.getKey())))throw bad("Reconstruction regresses a recorded placement");
    }
    private Runnable prepare(String type,Map<String,Object> supplied)throws Exception {
        if(faulted)throw bad("Replacement journal is faulted");Map<String,Object> p=frozen(supplied);if(type.equals("faulted_job_replacement_continuation_definition"))return prepareContinuationDefinition(p);if(type.equals("faulted_job_replacement_restart_prior_process_disposition"))return preparePriorDisposition(p);if(type.equals("faulted_job_replacement_restart_attachment"))return prepareRestartAttachment(p);if(type.equals("faulted_job_replacement_continuation_intent"))return prepareContinuationIntent(p);if(type.equals("faulted_job_replacement_continuation_outcome"))return prepareContinuationOutcome(p);if(type.equals("faulted_job_replacement_continuation_document_intent")||type.equals("faulted_job_replacement_continuation_document_outcome"))return prepareContinuationDocument(type,p);if(type.equals("faulted_job_replacement_continuation_publication_intent")||type.equals("faulted_job_replacement_continuation_publication_outcome"))return prepareContinuationPublication(type,p);String attempt=uuid(p.get("replacement_attempt_id"));long schema=number(p.get("schema_version"));if(schema!=1&&!(schema==2&&type.equals("faulted_job_replacement_intent")))throw bad("Replacement schema");uuid(p.get("recovery_operation_id"));hash(p.get("fault_set_sha256"));hash(p.get("capture_sha256"));
        if(type.equals("faulted_job_replacement_intent")){
            List<String> fields=new ArrayList<>(List.of("schema_version","replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","capture_sha256","capture","state"));if(schema==2)fields.addAll(List.of("reconstruction_bundle","reconstruction_sha256"));keys(p,fields.toArray(String[]::new));uuid(p.get("task_id"));Map<String,Object> capture=object(p.get("capture"));
            if(schema==2){
                validateReconstructionIntent(p,capture);
                Map<String,Object> admission=operations.get(p.get("recovery_operation_id"));
                if(admission==null||!"local_native_sensing_reconciliation".equals(admission.get("method"))||!Objects.equals(p.get("task_id"),admission.get("task_id"))||!Objects.equals(p.get("fault_set_sha256"),admission.get("request_digest")))throw bad("Reconstructible replacement lacks its exact active local recovery admission");
                requireSameActiveAdmission(admission);
            }
            requireNewReplacement(uuid(capture.get("job_id")));
            if(!"replacement_pending".equals(p.get("state"))||intents.containsKey(attempt)||receipts.containsKey(attempt)||operations.containsKey(attempt)||intents.size()>=MAX_REPLACEMENTS||!digest(capture).equals(p.get("capture_sha256"))||!same(capture,captureFacts((String)capture.get("job_id"),(String)capture.get("original_operation_id"))))throw bad("Replacement intent lacks exact current dependency capture");return ()->{intents.put(attempt,p);generation++;};
        }
        Map<String,Object> original=intents.get(attempt);if(original==null)throw bad("Replacement transition has no original intent");
        for(String key:List.of("recovery_operation_id","fault_set_sha256","capture_sha256"))if(!Objects.equals(original.get(key),p.get(key)))throw bad("Replacement transition changes transaction scope");
        if(type.equals("faulted_job_replacement_definition")){
            keys(p,"schema_version","replacement_attempt_id","recovery_operation_id","fault_set_sha256","capture_sha256","mapping","mapping_sha256");Map<String,Object> mapping=object(p.get("mapping"));
            if(definitions.containsKey(attempt)||!digest(mapping).equals(hash(p.get("mapping_sha256")))||number(mapping.get("schema_version"))!=1||number(mapping.get("replacement_history_entries"))!=0||!Boolean.FALSE.equals(mapping.get("native_job_initialized"))||!Boolean.FALSE.equals(mapping.get("registration_copied")))throw bad("Replacement definition is duplicate, corrupt or grants stale state");
            if(original.containsKey("reconstruction_bundle")&&!same(mapping,object(original.get("reconstruction_bundle")).get("source_mapping")))throw bad("Definition changes its atomically recorded native reconstruction");
            return ()->{definitions.put(attempt,p);generation++;};
        }
        if(type.equals("faulted_job_replacement_document")){
            keys(p,"schema_version","replacement_attempt_id","recovery_operation_id","fault_set_sha256","capture_sha256","document_id","manifest","manifest_sha256");
            if(documents.containsKey(attempt))throw bad("Candidate document is already recorded");validateDocument(p,original,definitions.get(attempt));
            return ()->{documents.put(attempt,p);generation++;};
        }
        if(type.equals("faulted_job_replacement_publication_intent")||type.equals("faulted_job_replacement_publication_outcome")){
            keys(p,"schema_version","replacement_attempt_id","recovery_operation_id","fault_set_sha256","capture_sha256","definition_sha256","replacement_outcome_sha256","state");
            if(!definitions.containsKey(attempt)||!receipts.containsKey(attempt)||!digest(definitions.get(attempt)).equals(hash(p.get("definition_sha256")))||!digest(receipts.get(attempt)).equals(hash(p.get("replacement_outcome_sha256")))||publications.containsKey(attempt))throw bad("Publication lacks exact forced definition and replacement receipts");
            if(type.endsWith("_intent")){if(publicationIntents.containsKey(attempt)||!"publication_pending".equals(p.get("state")))throw bad("Repeated or invalid publication intent");return ()->{publicationIntents.put(attempt,p);generation++;};}
            Map<String,Object> comparison=mutable(p);comparison.put("state","publication_pending");if(!"native_job_published".equals(p.get("state"))||!same(comparison,publicationIntents.get(attempt)))throw bad("Publication outcome lacks its exact intent");return ()->{publications.put(attempt,p);generation++;};
        }
        if(!type.equals("faulted_job_replacement_outcome")||receipts.containsKey(attempt))throw bad("Unknown or repeated replacement transition");validateOutcome(p);return ()->{receipts.put(attempt,p);generation++;};
    }
    private void validateOutcome(Map<String,Object> p)throws Exception {
        keys(p,"schema_version","replacement_attempt_id","recovery_operation_id","fault_set_sha256","capture_sha256","lineage_receipt_id","lineage_receipt_sha256","material_receipts","board_receipts","old_outcome_preserved","new_attempt_requires_validation","native_job_initialized");String attempt=uuid(p.get("replacement_attempt_id"));Map<String,Object> intent=intents.get(attempt);if(intent==null)throw bad("Replacement outcome has no intent");
        for(String key:List.of("recovery_operation_id","fault_set_sha256","capture_sha256"))if(!Objects.equals(intent.get(key),p.get(key)))throw bad("Replacement outcome changes admitted scope");
        if(!Boolean.TRUE.equals(p.get("old_outcome_preserved"))||!Boolean.TRUE.equals(p.get("new_attempt_requires_validation"))||!Boolean.FALSE.equals(p.get("native_job_initialized")))throw bad("Replacement cannot claim validation or old success");
        Map<String,Object> capture=object(intent.get("capture")),lin=lineage.replacementReceipt(uuid(p.get("lineage_receipt_id")));if(lin==null||!digest(lin).equals(hash(p.get("lineage_receipt_sha256")))||!attempt.equals(lin.get("new_job_id"))||!same(capture.get("lineage"),lin.get("old_lineage")))throw bad("Exact forced new lineage receipt required");binding(lin,p);
        verifySubreceipts(p,"material_receipts","material",(List<?>)object(capture.get("dependencies")).get("material_load_ids"));verifySubreceipts(p,"board_receipts","board",(List<?>)object(capture.get("dependencies")).get("board_load_ids"));
        for(Map.Entry<String,Object> e:object(capture.get("operations")).entrySet())if(!same(e.getValue(),operations.get(e.getKey())))throw bad("Original operation outcome changed after capture");
        for(Map.Entry<String,Object> e:object(capture.get("action_events")).entrySet())if(!same(e.getValue(),actionEvents.getOrDefault(e.getKey(),List.of())))throw bad("Original native action history changed after capture");
    }
    private void verifySubreceipts(Map<String,Object> p,String field,String kind,List<?> required)throws Exception {
        Object raw=p.get(field);if(!(raw instanceof List)||((List<?>)raw).size()!=required.size())throw bad("Replacement receipt union is incomplete");Set<String> seen=new HashSet<>();
        for(Object item:(List<?>)raw){Map<String,Object> descriptor=object(item);keys(descriptor,"kind","old_load_id","new_load_id","receipt_id","receipt_sha256","old_outcome_preserved");String id=uuid(descriptor.get("receipt_id")),old=uuid(descriptor.get("old_load_id"));Map<String,Object> record=kind.equals("material")?material.replacementReceipt(id):boards.replacementReceipt(id);
            if(record==null||!required.contains(old)||!seen.add(old)||!same(descriptor,descriptor(kind,record)))throw bad("Subreceipt is missing, forged, duplicated or outside captured union");binding(record,p);if(kind.equals("board")&&!Objects.equals(record.get("new_job_id"),p.get("replacement_attempt_id")))throw bad("New board belongs to another attempt");}
    }
    private static void binding(Map<String,Object> record,Map<String,Object> outer)throws IOException {for(String key:List.of("recovery_operation_id","fault_set_sha256"))if(!Objects.equals(record.get(key),outer.get(key)))throw bad("Subreceipt belongs to another recovery");}
    private static Map<String,Object> descriptor(String kind,Map<String,Object> receipt)throws IOException {return frozen(map("kind",kind,"old_load_id",receipt.get("old_load_id"),"new_load_id",object(receipt.get("new_load")).get("load_id"),"receipt_id",receipt.get("receipt_id"),"receipt_sha256",digest(receipt),"old_outcome_preserved",true));}
    List<Map<String,Object>> snapshot(){return Collections.unmodifiableList(new ArrayList<>(receipts.values()));}
    /** Read only exact reducer state. Successful subreceipts describe past effects, never permission
     * to repeat or adopt them; unresolved native completion and restart reattachment stay separate. */
    Map<String,Object> progress(String attempt)throws Exception {
        Map<String,Object> intent=intents.get(uuid(attempt));if(intent==null)throw bad("Replacement transaction not found");Map<String,Object> capture=object(intent.get("capture")),deps=object(capture.get("dependencies"));
        for(Map.Entry<String,Object> row:object(capture.get("operations")).entrySet())if(!same(row.getValue(),operations.get(row.getKey())))throw bad("Original operation changed after replacement admission");
        for(Map.Entry<String,Object> row:object(capture.get("action_events")).entrySet())if(!same(row.getValue(),actionEvents.getOrDefault(row.getKey(),List.of())))throw bad("Original native action history changed after replacement admission");
        String operation=(String)intent.get("recovery_operation_id"),hash=(String)intent.get("fault_set_sha256");
        Map<String,Object> trays=material.replacementProgress(operation,hash,stringIds(deps.get("material_load_ids"))),board=boards.replacementProgress(operation,hash,attempt,stringIds(deps.get("board_load_ids")));
        compareOriginalLoads(trays,object(object(capture.get("material")).get("loads")));compareOriginalLoads(board,object(object(capture.get("boards")).get("loads")));
        Map<String,Object> pub=publications.get(attempt),pubIntent=publicationIntents.get(attempt),outcome=receipts.get(attempt),definition=definitions.get(attempt),document=documents.get(attempt);
        return frozen(map("schema_version",1,"replacement_attempt_id",attempt,"task_id",intent.get("task_id"),"recovery_operation_id",operation,"fault_set_sha256",hash,"capture_sha256",intent.get("capture_sha256"),"original_job_id",capture.get("job_id"),"original_operation_id",capture.get("original_operation_id"),"original_dependencies",deps,"original_outcomes_preserved",true,
            "lineage",lineage.replacementProgress(operation,hash,object(capture.get("lineage")),attempt),"material",trays,"boards",board,
            "definition",map("phase",definition==null?"untouched":"completed","record",definition,"record_sha256",definition==null?null:digest(definition),"candidate_retained_in_process",candidates.containsKey(attempt)),
            "document",documentProgress(attempt,document),
            "compound",map("phase",outcome==null?"pending":"completed","record",outcome,"record_sha256",outcome==null?null:digest(outcome)),
            "publication",map("phase",pub!=null?"completed":pubIntent!=null?"pending":"untouched","intent",pubIntent,"outcome",pub),"local_operation",operations.get(operation),"publication_fault",faulted,"execution_authority_restored",false,"continuation_supported",false));
    }
    private static List<String> stringIds(Object raw)throws IOException {if(!(raw instanceof List))throw bad("Dependency list required");List<String> result=new ArrayList<>();for(Object id:(List<?>)raw)result.add(uuid(id));return result;}
    private static void compareOriginalLoads(Map<String,Object> progress,Map<String,Object> captured)throws Exception {
        for(Object raw:(List<?>)progress.get("rows")){Map<String,Object> row=object(raw);if(!same(row.get("old_load"),captured.get(row.get("old_load_id"))))throw bad("Original load differs from transaction capture");}
    }
    List<Map<String,Object>> progressForTask(String taskId)throws Exception {uuid(taskId);Set<String> attempts=new LinkedHashSet<>();for(Map<String,Object> intent:intents.values())if(taskId.equals(intent.get("task_id")))attempts.add((String)intent.get("replacement_attempt_id"));for(Map<String,Object> continuation:continuationIntents.values())if(taskId.equals(continuation.get("task_id")))attempts.add((String)continuation.get("replacement_attempt_id"));List<Map<String,Object>> rows=new ArrayList<>();for(String attempt:attempts)rows.add(progress(attempt));return Collections.unmodifiableList(rows);}
    Map<String,Object> status()throws IOException{return frozen(map("intents",intents,"receipts",receipts,"definitions",definitions,"documents",documents,"publication_intents",publicationIntents,"publications",publications,"continuation_intents",continuationIntents,"continuation_adoptions",continuationAdoptions,"continuation_step_intents",continuationStepIntents,"continuation_step_outcomes",continuationStepOutcomes,"continuation_receipts",continuationReceipts,"continuation_document_intents",continuationDocumentIntents,"continuation_documents",continuationDocuments,"continuation_definitions",continuationDefinitions,"continuation_publication_intents",continuationPublicationIntents,"continuation_publications",continuationPublications,"restart_attachments",restartAttachments,"restart_observations",restartObservations,"restart_prior_process_dispositions",restartPriorDispositions,"retained_restart_count",retainedRestarts.size(),"retained_candidate_count",candidates.size(),"publication_fault",faulted,"execution_authority_restored",false));}
    @Override public void close(){closed=true;for(RestartPermit permit:new ArrayList<>(liveRestarts.values()))permit.close();for(RestartStage stage:new ArrayList<>(restartStages.values()))stage.close();for(Permit permit:new ArrayList<>(livePermits.values()))permit.close();for(ContinuationPermit permit:new ArrayList<>(liveContinuations.values()))permit.close();for(Publication publication:new ArrayList<>(livePublications.values()))publication.close();publishedWitnesses.clear();Set<NativeReplacementJob.Candidate> reconstructed=Collections.newSetFromMap(new IdentityHashMap<>());for(NativeReplacementDocuments.Reconstructed graphs:attachedGraphs.values())reconstructed.add(graphs.candidate());for(NativeReplacementJob.Candidate candidate:candidates.values())if(!reconstructed.contains(candidate))candidate.close();for(NativeReplacementDocuments.Reconstructed graphs:attachedGraphs.values())graphs.close();attachedGraphs.clear();retainedRestarts.clear();priorDispositionWitnesses.clear();candidates.clear();retainedCaptures.clear();}
    static IOException bad(String message){return new IOException(message);}
    static Map<String,Object> map(Object... pairs){Map<String,Object> out=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)out.put((String)pairs[i],pairs[i+1]);return out;}
    @SuppressWarnings("unchecked")static Map<String,Object> object(Object x)throws IOException{if(!(x instanceof Map))throw bad("Object required");return(Map<String,Object>)x;}
    static void keys(Map<String,Object> p,String... keys)throws IOException{if(!p.keySet().equals(new HashSet<>(Arrays.asList(keys))))throw bad("Missing or unknown replacement fields");}
    /** Generic Bridge history retains bounded opaque IDs; typed native recovery IDs remain UUIDs. */
    private static String historyOperationId(Object value)throws IOException {
        if(!(value instanceof String)||((String)value).isEmpty()||((String)value).length()>1024)throw bad("Bounded nonempty operation history ID required");
        return (String)value;
    }
    static String uuid(Object x)throws IOException{if(!(x instanceof String))throw bad("UUID required");try{String s=(String)x;if(!UUID.fromString(s).toString().equals(s))throw new IllegalArgumentException();return s;}catch(IllegalArgumentException e){throw bad("Canonical UUID required");}}
    static String hash(Object x)throws IOException{if(!(x instanceof String)||!((String)x).matches("[a-f0-9]{64}"))throw bad("SHA256 required");return(String)x;}
    static long number(Object x)throws IOException{try{if(!(x instanceof Number))throw new ArithmeticException();long value=new BigDecimal(x.toString()).longValueExact();if(value<0||value>9007199254740991L)throw new ArithmeticException();return value;}catch(ArithmeticException|NumberFormatException e){throw bad("Bounded exact integer required");}}
    static Map<String,Object> frozen(Map<String,Object> p)throws IOException{return object(canonical(p,true,0,new int[1]));}
    static Map<String,Object> mutable(Map<String,Object> p)throws IOException{return object(canonical(p,false,0,new int[1]));}
    static boolean same(Object a,Object b)throws IOException{return Objects.equals(canonical(a,true,0,new int[1]),canonical(b,true,0,new int[1]));}
    static String digest(Map<String,Object> p)throws IOException{try{byte[] bytes=JSON.toJson(canonical(p,true,0,new int[1])).getBytes(StandardCharsets.UTF_8);if(bytes.length>16*1024*1024)throw bad("Replacement record capacity reached");byte[] hash=MessageDigest.getInstance("SHA-256").digest(bytes);StringBuilder out=new StringBuilder();for(byte v:hash)out.append(String.format("%02x",v&255));return out.toString();}catch(IOException e){throw e;}catch(Exception e){throw new IOException(e);}}
    private static Object canonical(Object x,boolean immutable,int depth,int[] count)throws IOException {
        if(depth>32||++count[0]>500000)throw bad("Replacement structure capacity reached");if(x instanceof Map){Map<String,Object> out=new TreeMap<>();for(Map.Entry<?,?> e:((Map<?,?>)x).entrySet()){if(!(e.getKey() instanceof String))throw bad("String key required");out.put((String)e.getKey(),canonical(e.getValue(),immutable,depth+1,count));}return immutable?Collections.unmodifiableMap(out):out;}
        if(x instanceof Collection){List<Object> out=new ArrayList<>();for(Object item:(Collection<?>)x)out.add(canonical(item,immutable,depth+1,count));return immutable?Collections.unmodifiableList(out):out;}if(x==null||x instanceof Boolean||x instanceof String)return x;if(x instanceof Number){try{BigDecimal n=new BigDecimal(x.toString()).stripTrailingZeros();if(!Double.isFinite(n.doubleValue()))throw new NumberFormatException();return n;}catch(NumberFormatException e){throw bad("Finite JSON number required");}}throw bad("JSON value required");
    }
}
