/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.math.BigDecimal;
import java.util.*;

/** A bounded reducer over the Bridge's complete, forced journal.
 * This class supplies durable facts, not native Job identity or executor authority. The caller
 * must bind exact current native objects, inspect full placed state and retain its ownership fence.
 * No method may be invoked from arbitrary client JSON or to infer a new lineage from a document.
 */
public final class NativeJobLineage {
    public interface Sink { void appendAndForce(String type,Map<String,Object> payload)throws Exception; }
    public static final int MAX_LINEAGES=128,MAX_ALIASES=1024,MAX_DOCUMENTS=128,MAX_IDS=100000,MAX_LOADS=512;
    private static final Gson GSON=new Gson();
    private final Sink sink;
    private final Map<String,State> states=new LinkedHashMap<>();
    private final Map<String,Alias> aliases=new LinkedHashMap<>();
    private final Map<String,Document> documents=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> admissionBindings=new LinkedHashMap<>();
    private final Map<String,Load> loads=new LinkedHashMap<>();
    private final Set<String> previouslyObservedJobs=new HashSet<>();
    private final Map<String,Map<String,Object>> replacementReceipts=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationAdoptions=new LinkedHashMap<>();
    private final Map<String,Map<String,Object>> continuationReplacementReceipts=new LinkedHashMap<>();
    private boolean faulted;private long generation;

    public static final class Fault extends Exception {
        public final String code;
        Fault(String code,String message){super(message);this.code=code;}
    }
    private static final class State {
        final String id;long revision=1;boolean admitted;String pending;String retiredBy;
        final Set<String> reserved=new TreeSet<>(),loadIds=new TreeSet<>();
        State(String id){this.id=id;}
    }
    private static final class Alias {
        final String lineage;long modelRevision;
        Alias(String lineage,long revision){this.lineage=lineage;this.modelRevision=revision;}
    }
    private static final class Document {
        final String lineage;final long revision;
        Document(String id,long revision){lineage=id;this.revision=revision;}
    }
    private static final class Load {
        final String state;final boolean complete;final Set<String> keys;final int entries;
        Load(Map<String,Object> record)throws Exception {
            state=string(record,"state",64);
            if(!Set.of("loaded","loading_unknown","presence_unconfirmed").contains(state))fail("LINEAGE_RECORD","Unknown board-load state");
            Object completeRaw=record.get("complete_native_history");if(!(completeRaw instanceof Boolean))fail("LINEAGE_RECORD","Missing native-history completeness");complete=(Boolean)completeRaw;
            Object history=record.get("placed_history");if(!(history instanceof Map))fail("LINEAGE_RECORD","Complete board-load history map required");
            TreeSet<String> checked=new TreeSet<>();for(Map.Entry<?,?> e:((Map<?,?>)history).entrySet()){if(!(e.getKey() instanceof String)||!(e.getValue() instanceof Boolean))fail("LINEAGE_RECORD","Invalid native history key/value");checked.add((String)e.getKey());}keys=Collections.unmodifiableSet(checked);entries=keys.size();if(entries>MAX_IDS)fail("LINEAGE_CAPACITY","History count exceeded");
        }
    }
    /** Unforgeable process-local capability held by one authority adapter until its finally block. */
    public static final class PendingPermit implements AutoCloseable {
        private final NativeJobLineage owner;private final String job,transaction;private boolean closed;
        private PendingPermit(NativeJobLineage owner,String job,String transaction){this.owner=owner;this.job=job;this.transaction=transaction;}
        public void close(){synchronized(owner){closed=true;}}
    }
    public static final class Snapshot {
        public final String lineageId;public final long revision;public final boolean admitted;
        public final String pendingTransaction;public final Set<String> reservedLogicalIds,loadIds;
        Snapshot(State s){lineageId=s.id;revision=s.revision;admitted=s.admitted;pendingTransaction=s.pending;
            reservedLogicalIds=Collections.unmodifiableSet(new TreeSet<>(s.reserved));loadIds=Collections.unmodifiableSet(new TreeSet<>(s.loadIds));}
    }
    public NativeJobLineage(Sink sink){this.sink=Objects.requireNonNull(sink);}

    /** Only a verified new native simulator import may call this method. */
    public synchronized String createFresh(String jobId,String origin)throws Exception {
        requireLive();String lineage=UUID.randomUUID().toString();
        append("job_lineage_created",map("lineage_id",lineage,"job_id",jobId,"origin",origin));return lineage;
    }
    public synchronized Snapshot snapshot(String jobId)throws Exception {requireLive();return new Snapshot(forJob(jobId));}
    /** Fault an already-attempted external journal publication; only verified replay may recover. */
    public synchronized void publicationFailed(){faulted=true;}
    /** Durable facts only. Native object/executor/placed-state authority remains a caller check. */
    public synchronized Map<String,Object> describe(String jobId)throws Exception {
        if(faulted)return immutable(map("status","faulted","structural_authority",false,"reason","LINEAGE_FAULT"));
        Alias alias=aliases.get(jobId);if(alias==null)return immutable(map("status","unknown","structural_authority",false,"reason","LINEAGE_UNKNOWN"));
        State state=states.get(alias.lineage);String reason=null;
        try{requirePristine(jobId,null);}catch(Fault failure){reason=failure.code;}
        return immutable(map("status","known","lineage_id",state.id,"lineage_revision",state.revision,"model_lineage_revision",alias.modelRevision,
            "processor_admitted",state.admitted,"pending_transaction",state.pending,"reserved_logical_id_count",state.reserved.size(),"associated_load_count",state.loadIds.size(),
            "durable_pristine",reason==null,"durable_guard_failure",reason,"structural_authority",false,"native_authority_checked",false));
    }

    /** Exact durable dependency export; this grants no native identity or replay authority. */
    synchronized Map<String,Object> captureReplacementState(String jobId)throws Exception {
        requireLive();State s=forJob(jobId);Alias alias=aliases.get(jobId);
        if(alias.modelRevision!=s.revision||s.pending!=null||!s.admitted)fail("LINEAGE_REPLACEMENT_REQUIRED","Exact admitted current attempt without unresolved structural edits required");
        return captureLineageState(jobId);
    }
    private Map<String,Object> captureLineageState(String jobId)throws Exception {
        State s=forJob(jobId);List<String> jobs=new ArrayList<>();for(Map.Entry<String,Alias> e:aliases.entrySet())if(e.getValue().lineage.equals(s.id))jobs.add(e.getKey());Collections.sort(jobs);
        Map<String,Object> operations=new TreeMap<>();for(Map.Entry<String,Map<String,Object>> e:admissionBindings.entrySet())if(jobs.contains(e.getValue().get("job_id")))operations.put(e.getKey(),e.getValue());
        return NativeFaultedJobReplacement.frozen(map("job_id",jobId,"lineage_id",s.id,"lineage_revision",s.revision,"job_aliases",jobs,"operation_bindings",operations,"load_ids",new ArrayList<>(s.loadIds),"reserved_logical_ids",new ArrayList<>(s.reserved),"processor_admitted",s.admitted,"retired_by",s.retiredBy==null?"":s.retiredBy));
    }
    /** Called only with a current in-process sensing recovery permit; records a fresh lineage. */
    synchronized Map<String,Object> createReplacement(NativeFaultedJobReplacement.Permit permit,String newJobId)throws Exception {
        Map<String,Object> authorization=permit.authorizeLineage(this,newJobId),old=NativeFaultedJobReplacement.object(authorization.get("old_lineage"));
        if(!NativeFaultedJobReplacement.same(old,captureReplacementState((String)old.get("job_id"))))fail("LINEAGE_STALE","Original attempt changed after replacement capture");
        String receipt=UUID.randomUUID().toString(),lineage=UUID.randomUUID().toString();
        Map<String,Object> record=map("schema_version",1,"receipt_id",receipt,"recovery_operation_id",authorization.get("recovery_operation_id"),"fault_set_sha256",authorization.get("fault_set_sha256"),"lineage_id",old.get("lineage_id"),"old_lineage",old,"new_job_id",newJobId,"new_lineage_id",lineage,"replaces_attempt",old.get("job_id"),"old_outcome_preserved",true);
        append("job_lineage_replacement",record);return NativeFaultedJobReplacement.frozen(record);
    }
    synchronized Map<String,Object> replacementReceipt(String receiptId){return replacementReceipts.get(receiptId);}
    synchronized boolean retiredJob(String jobId){Alias a=aliases.get(jobId);return a!=null&&states.get(a.lineage).retiredBy!=null;}

    /** One durable creation for an untouched original lineage phase. The owner checks exact
     * retained native graphs; this reducer preserves the original admitted lineage and retires it. */
    synchronized Map<String,Object> continueReplacement(NativeFaultedJobReplacement.ContinuationPermit permit,String newJobId)throws Exception {
        requireLive();
        try(var step=permit.beginLineageStep(this,newJobId)){
            Map<String,Object> authority=step.authority(),old=NativeFaultedJobReplacement.object(authority.get("old_lineage")),phase=NativeFaultedJobReplacement.object(authority.get("phase_row"));
            if(!"untouched".equals(NativeFaultedJobReplacement.effectivePhase(phase))||!NativeFaultedJobReplacement.same(old,captureReplacementState((String)old.get("job_id"))))fail("LINEAGE_CONTINUATION_SCOPE","Continuation requires the exact untouched original lineage");
            String receipt=step.stepId();
            Map<String,Object> record=map("schema_version",1,"receipt_id",receipt,"step_id",receipt,"continuation_id",authority.get("continuation_id"),"replacement_attempt_id",authority.get("replacement_attempt_id"),"recovery_operation_id",authority.get("recovery_operation_id"),"fault_set_sha256",authority.get("fault_set_sha256"),"continuation_capture_sha256",authority.get("continuation_capture_sha256"),"original_recovery_operation_id",authority.get("original_recovery_operation_id"),"original_fault_set_sha256",authority.get("original_fault_set_sha256"),"phase_row_sha256",authority.get("phase_row_sha256"),"lineage_id",old.get("lineage_id"),"old_lineage",old,"new_job_id",newJobId,"new_lineage_id",UUID.randomUUID().toString(),"replaces_attempt",old.get("job_id"),"old_outcome_preserved",true,"execution_authority_restored",false);
            step.check();append("job_lineage_continuation_replacement",record);step.acceptForcedEvent("job_lineage_continuation_replacement",record);step.check();return continuationReplacementReceipts.get(receipt);
        }
    }
    synchronized Map<String,Object> continuationReplacementReceipt(String receiptId){return continuationReplacementReceipts.get(receiptId);}

    /** Classify original and separately continued retirements without rewriting original facts. */
    synchronized Map<String,Object> replacementProgress(String recoveryOperation,String faultDigest,Map<String,Object> captured,String replacementJobId)throws Exception {
        NativeFaultedJobReplacement.uuid(recoveryOperation);NativeFaultedJobReplacement.hash(faultDigest);NativeFaultedJobReplacement.uuid(replacementJobId);
        String oldJob=NativeFaultedJobReplacement.uuid(captured.get("job_id"));Alias alias=aliases.get(oldJob);
        if(alias==null||!Objects.equals(alias.lineage,captured.get("lineage_id")))fail("LINEAGE_REPLACEMENT_REQUIRED","Original replacement lineage is absent");
        State old=states.get(alias.lineage);Map<String,Object> receipt=null,continued=null;
        for(Map<String,Object> row:replacementReceipts.values())if(recoveryOperation.equals(row.get("recovery_operation_id"))){
            if(receipt!=null||!faultDigest.equals(row.get("fault_set_sha256"))||!replacementJobId.equals(row.get("new_job_id"))||!NativeFaultedJobReplacement.same(captured,row.get("old_lineage")))fail("LINEAGE_CONFLICT","Foreign or repeated lineage retirement in replacement scope");receipt=row;
        }
        for(Map<String,Object> row:continuationReplacementReceipts.values())if(recoveryOperation.equals(row.get("original_recovery_operation_id"))){
            if(continued!=null||receipt!=null||!faultDigest.equals(row.get("original_fault_set_sha256"))||!replacementJobId.equals(row.get("new_job_id"))||!NativeFaultedJobReplacement.same(captured,row.get("old_lineage")))fail("LINEAGE_CONFLICT","Foreign or repeated continuation retirement in replacement scope");continued=row;
        }
        Map<String,Object> effective=continued==null?receipt:continued;
        if(effective==null){if(old.retiredBy!=null)fail("LINEAGE_CONFLICT","Original attempt was retired by another transaction");}
        else if(!Objects.equals(old.retiredBy,effective.get("receipt_id"))||!aliases.containsKey(replacementJobId)||!Objects.equals(aliases.get(replacementJobId).lineage,effective.get("new_lineage_id")))fail("LINEAGE_CONFLICT","Retirement receipt differs from retained aliases");
        Map<String,Object> progress=map("phase",receipt==null?"untouched":"completed","receipt_id",receipt==null?null:receipt.get("receipt_id"),"receipt_sha256",receipt==null?null:NativeFaultedJobReplacement.digest(receipt),"receipt",receipt,"original_job_id",oldJob,"replacement_job_id",replacementJobId,"publication_fault",faulted,"execution_authority_restored",false);
        if(continued!=null){progress.put("effective_phase","completed");progress.put("effective_receipt_id",continued.get("receipt_id"));progress.put("effective_receipt_sha256",NativeFaultedJobReplacement.digest(continued));progress.put("effective_receipt",continued);}
        return NativeFaultedJobReplacement.frozen(progress);
    }

    /** A new forced receipt adopts existing retirement facts; no lineage, alias, load, native
     * binding, processor admission, or execution authority is created by this operation. */
    synchronized Map<String,Object> adoptCompletedReplacement(NativeFaultedJobReplacement.ContinuationPermit permit,String replacementJobId)throws Exception {
        requireLive();Map<String,Object> authority=permit.authorizeLineage(this,replacementJobId),captured=NativeFaultedJobReplacement.object(authority.get("old_lineage")),phase=NativeFaultedJobReplacement.object(authority.get("phase_row"));
        if(!"completed".equals(NativeFaultedJobReplacement.effectivePhase(phase))||!NativeFaultedJobReplacement.same(phase,replacementProgress((String)authority.get("original_recovery_operation_id"),(String)authority.get("original_fault_set_sha256"),captured,replacementJobId)))fail("LINEAGE_CONTINUATION_INCOMPLETE","Only an unchanged completed lineage phase may be adopted");
        boolean continued=phase.containsKey("effective_receipt");String parentType=continued?"job_lineage_continuation_replacement":"job_lineage_replacement",parentId=NativeFaultedJobReplacement.uuid(phase.get(continued?"effective_receipt_id":"receipt_id"));Map<String,Object> parent=(continued?continuationReplacementReceipts:replacementReceipts).get(parentId);
        if(parent==null||!NativeFaultedJobReplacement.same(parent,phase.get(continued?"effective_receipt":"receipt"))||!NativeFaultedJobReplacement.digest(parent).equals(phase.get(continued?"effective_receipt_sha256":"receipt_sha256")))fail("LINEAGE_CONFLICT","Completed retirement parent differs");
        Map<String,Object> receipt=map("schema_version",2,"parent_receipt_type",parentType,"receipt_id",UUID.randomUUID().toString(),"continuation_id",authority.get("continuation_id"),"replacement_attempt_id",authority.get("replacement_attempt_id"),"recovery_operation_id",authority.get("recovery_operation_id"),"fault_set_sha256",authority.get("fault_set_sha256"),"continuation_capture_sha256",authority.get("continuation_capture_sha256"),"original_recovery_operation_id",authority.get("original_recovery_operation_id"),"original_fault_set_sha256",authority.get("original_fault_set_sha256"),"phase_row_sha256",NativeFaultedJobReplacement.continuationPhaseDigest(phase),"parent_receipt_id",parentId,"parent_receipt_sha256",NativeFaultedJobReplacement.digest(parent),"lineage_id",parent.get("lineage_id"),"old_job_id",parent.get("replaces_attempt"),"new_job_id",replacementJobId,"new_lineage_id",parent.get("new_lineage_id"),"old_lineage_sha256",NativeFaultedJobReplacement.digest(captureLineageState((String)parent.get("replaces_attempt"))),"new_lineage_sha256",NativeFaultedJobReplacement.digest(captureLineageState(replacementJobId)),"adoption_scope","completed-same-process","old_outcome_preserved",true,"execution_authority_restored",false);
        permit.check();append("job_lineage_continuation_adoption",receipt);permit.check();return continuationAdoptions.get((String)receipt.get("receipt_id"));
    }
    synchronized Map<String,Object> continuationAdoptionReceipt(String receiptId){return continuationAdoptions.get(receiptId);}
    synchronized List<Map<String,Object>> continuationAdoptionSnapshot(){return Collections.unmodifiableList(new ArrayList<>(continuationAdoptions.values()));}
    /** Validate current durable lineage for a compound continuation without changing any state.
     * Superseded unknown board loads remain historical dependencies; their identities must stay
     * in the exact union even though no current execution authority is granted for those loads. */
    synchronized void validateContinuationCompletion(String receiptType,String receiptId,Set<String> expectedNewLoadIds)throws Exception {
        requireLive();NativeFaultedJobReplacement.uuid(receiptId);
        Map<String,Object> selected,parent;String parentId;
        if("job_lineage_continuation_adoption".equals(receiptType)){
            selected=continuationAdoptions.get(receiptId);if(selected==null)fail("LINEAGE_CONTINUATION_SCOPE","Compound lineage adoption receipt is absent");
            String parentType=integer(selected,"schema_version")==1?"job_lineage_replacement":string(selected,"parent_receipt_type",64);parentId=uuid(selected,"parent_receipt_id");
            if("job_lineage_replacement".equals(parentType))parent=replacementReceipts.get(parentId);
            else if("job_lineage_continuation_replacement".equals(parentType))parent=continuationReplacementReceipts.get(parentId);
            else {fail("LINEAGE_CONTINUATION_SCOPE","Compound lineage adoption parent type is invalid");return;}
            if(parent==null||!NativeFaultedJobReplacement.digest(parent).equals(selected.get("parent_receipt_sha256"))||!Objects.equals(parent.get("lineage_id"),selected.get("lineage_id"))||!Objects.equals(parent.get("new_lineage_id"),selected.get("new_lineage_id"))||!Objects.equals(parent.get("new_job_id"),selected.get("new_job_id"))||!Objects.equals(parent.get("replaces_attempt"),selected.get("old_job_id")))fail("LINEAGE_CONTINUATION_SCOPE","Compound lineage adoption lost its exact retirement parent");
        }else if("job_lineage_continuation_replacement".equals(receiptType)){
            selected=continuationReplacementReceipts.get(receiptId);parent=selected;parentId=receiptId;if(parent==null)fail("LINEAGE_CONTINUATION_SCOPE","Compound lineage creation receipt is absent");
        }else {fail("LINEAGE_CONTINUATION_SCOPE","Compound lineage requires an exact continuation receipt");return;}
        String oldJob=uuid(parent,"replaces_attempt"),newJob=uuid(parent,"new_job_id");State old=forJob(oldJob),fresh=forJob(newJob);
        Map<String,Object> expectedOld=NativeFaultedJobReplacement.mutable(object(parent.get("old_lineage")));expectedOld.put("retired_by",parentId);
        if(!Objects.equals(old.retiredBy,parentId)||!NativeFaultedJobReplacement.same(expectedOld,captureLineageState(oldJob)))fail("LINEAGE_CONTINUATION_SCOPE","Original lineage changed after its exact retirement");
        Map<String,Object> current=captureLineageState(newJob);
        if(!fresh.id.equals(parent.get("new_lineage_id"))||fresh.revision!=1||aliases.get(newJob).modelRevision!=1||fresh.admitted||fresh.pending!=null||fresh.retiredBy!=null||!fresh.reserved.isEmpty()||!List.of(newJob).equals(current.get("job_aliases"))||!object(current.get("operation_bindings")).isEmpty())fail("LINEAGE_CONTINUATION_SCOPE","Replacement lineage changed before compound completion");
        if(expectedNewLoadIds==null||expectedNewLoadIds.size()>MAX_LOADS)fail("LINEAGE_CONTINUATION_SCOPE","Bounded exact replacement board load union required");
        Set<String> expected=new TreeSet<>();for(String id:expectedNewLoadIds){NativeFaultedJobReplacement.uuid(id);expected.add(id);}
        if(!fresh.loadIds.equals(expected)||!loads.keySet().containsAll(expected))fail("LINEAGE_CONTINUATION_SCOPE","Replacement lineage board load union differs from completed components");
    }
    private Runnable validateContinuationReplacement(Map<String,Object> p)throws Exception {
        fields(p,"schema_version","receipt_id","step_id","continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","phase_row_sha256","lineage_id","old_lineage","new_job_id","new_lineage_id","replaces_attempt","old_outcome_preserved","execution_authority_restored");
        if(integer(p,"schema_version")!=1||!Boolean.TRUE.equals(p.get("old_outcome_preserved"))||!Boolean.FALSE.equals(p.get("execution_authority_restored")))fail("LINEAGE_RECORD","Invalid lineage continuation scope");
        for(String key:List.of("receipt_id","step_id","continuation_id","replacement_attempt_id","recovery_operation_id","original_recovery_operation_id","lineage_id","new_job_id","new_lineage_id","replaces_attempt"))uuid(p,key);
        for(String key:List.of("fault_set_sha256","continuation_capture_sha256","original_fault_set_sha256","phase_row_sha256"))hash(p,key);
        String receipt=(String)p.get("receipt_id"),oldJob=(String)p.get("replaces_attempt"),newJob=(String)p.get("new_job_id"),fresh=(String)p.get("new_lineage_id");Map<String,Object> prior=object(p.get("old_lineage"));
        if(!receipt.equals(p.get("step_id"))||!newJob.equals(p.get("replacement_attempt_id"))||Objects.equals(p.get("recovery_operation_id"),p.get("original_recovery_operation_id"))||!oldJob.equals(prior.get("job_id"))||!Objects.equals(p.get("lineage_id"),prior.get("lineage_id"))||!NativeFaultedJobReplacement.same(prior,captureReplacementState(oldJob)))fail("LINEAGE_CONTINUATION_SCOPE","Lineage continuation changes the exact original attempt or fresh decision");
        State old=forJob(oldJob);Map<String,Object> phase=replacementProgress((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),prior,newJob);
        if(!"untouched".equals(NativeFaultedJobReplacement.effectivePhase(phase))||!NativeFaultedJobReplacement.continuationPhaseDigest(phase).equals(p.get("phase_row_sha256"))||old.retiredBy!=null||states.containsKey(fresh)||aliases.containsKey(newJob)||previouslyObservedJobs.contains(newJob)||replacementReceipts.containsKey(receipt)||continuationReplacementReceipts.containsKey(receipt)||continuationAdoptions.containsKey(receipt))fail("LINEAGE_CONTINUATION_SCOPE","Lineage creation is not an exact unused continuation of an untouched phase");
        if(states.size()>=MAX_LINEAGES||aliases.size()>=MAX_ALIASES||continuationReplacementReceipts.size()>=4096)fail("LINEAGE_CAPACITY","Continuation lineage capacity reached");
        return ()->{old.retiredBy=receipt;states.put(fresh,new State(fresh));aliases.put(newJob,new Alias(fresh,1));continuationReplacementReceipts.put(receipt,p);};
    }
    private Runnable validateContinuationAdoption(Map<String,Object> p)throws Exception {
        long schema=integer(p,"schema_version");Set<String> keys=new HashSet<>(List.of("schema_version","receipt_id","continuation_id","replacement_attempt_id","recovery_operation_id","fault_set_sha256","continuation_capture_sha256","original_recovery_operation_id","original_fault_set_sha256","phase_row_sha256","parent_receipt_id","parent_receipt_sha256","lineage_id","old_job_id","new_job_id","new_lineage_id","old_lineage_sha256","new_lineage_sha256","adoption_scope","old_outcome_preserved","execution_authority_restored"));if(schema==2)keys.add("parent_receipt_type");fields(p,keys.toArray(new String[0]));
        if((schema!=1&&schema!=2)||!"completed-same-process".equals(p.get("adoption_scope"))||!Boolean.TRUE.equals(p.get("old_outcome_preserved"))||!Boolean.FALSE.equals(p.get("execution_authority_restored")))fail("LINEAGE_RECORD","Invalid continuation adoption scope");
        String parentType=schema==1?"job_lineage_replacement":string(p,"parent_receipt_type",64);if(!Set.of("job_lineage_replacement","job_lineage_continuation_replacement").contains(parentType))fail("LINEAGE_RECORD","Invalid lineage adoption parent type");boolean continued=parentType.equals("job_lineage_continuation_replacement");
        for(String key:List.of("receipt_id","continuation_id","replacement_attempt_id","recovery_operation_id","original_recovery_operation_id","parent_receipt_id","lineage_id","old_job_id","new_job_id","new_lineage_id"))uuid(p,key);
        for(String key:List.of("fault_set_sha256","continuation_capture_sha256","original_fault_set_sha256","phase_row_sha256","parent_receipt_sha256","old_lineage_sha256","new_lineage_sha256"))hash(p,key);
        String parentId=(String)p.get("parent_receipt_id"),receipt=(String)p.get("receipt_id"),oldJob=(String)p.get("old_job_id"),newJob=(String)p.get("new_job_id");Map<String,Object> parent=(continued?continuationReplacementReceipts:replacementReceipts).get(parentId);
        if(parent==null||!NativeFaultedJobReplacement.digest(parent).equals(p.get("parent_receipt_sha256"))||Objects.equals(p.get("recovery_operation_id"),p.get("original_recovery_operation_id"))||!Objects.equals(p.get("original_recovery_operation_id"),parent.get(continued?"original_recovery_operation_id":"recovery_operation_id"))||!Objects.equals(p.get("original_fault_set_sha256"),parent.get(continued?"original_fault_set_sha256":"fault_set_sha256"))||!Objects.equals(p.get("replacement_attempt_id"),newJob)||!Objects.equals(newJob,parent.get("new_job_id"))||!Objects.equals(oldJob,parent.get("replaces_attempt"))||!Objects.equals(p.get("lineage_id"),parent.get("lineage_id"))||!Objects.equals(p.get("new_lineage_id"),parent.get("new_lineage_id")))fail("LINEAGE_CONFLICT","Adoption has no exact completed retirement parent");
        Map<String,Object> phase=replacementProgress((String)p.get("original_recovery_operation_id"),(String)p.get("original_fault_set_sha256"),object(parent.get("old_lineage")),newJob);
        if(!"completed".equals(NativeFaultedJobReplacement.effectivePhase(phase))||!(schema==1?NativeFaultedJobReplacement.digest(phase):NativeFaultedJobReplacement.continuationPhaseDigest(phase)).equals(p.get("phase_row_sha256")))fail("LINEAGE_CONFLICT","Adoption phase differs from exact current lineage retirement");
        State old=forJob(oldJob),fresh=forJob(newJob);Map<String,Object> expectedOld=NativeFaultedJobReplacement.mutable(NativeFaultedJobReplacement.object(parent.get("old_lineage")));expectedOld.put("retired_by",parentId);
        if(!Objects.equals(old.retiredBy,parentId)||!NativeFaultedJobReplacement.same(expectedOld,captureLineageState(oldJob))||!NativeFaultedJobReplacement.digest(captureLineageState(oldJob)).equals(p.get("old_lineage_sha256"))||!NativeFaultedJobReplacement.digest(captureLineageState(newJob)).equals(p.get("new_lineage_sha256"))||!fresh.id.equals(parent.get("new_lineage_id"))||fresh.revision!=1||aliases.get(newJob).modelRevision!=1||fresh.admitted||fresh.pending!=null||fresh.retiredBy!=null||!fresh.reserved.isEmpty())fail("LINEAGE_CONFLICT","Original retirement or replacement lineage changed");
        if(continuationAdoptions.size()>=4096||continuationAdoptions.containsKey(receipt)||replacementReceipts.containsKey(receipt)||continuationReplacementReceipts.containsKey(receipt))fail("LINEAGE_CAPACITY","Continuation receipt capacity or identity conflict");
        for(Map<String,Object> previous:continuationAdoptions.values())if(Objects.equals(previous.get("continuation_id"),p.get("continuation_id"))&&Objects.equals(previous.get("parent_receipt_id"),parentId))fail("LINEAGE_CONFLICT","Completed lineage already adopted by this continuation");
        return ()->continuationAdoptions.put(receipt,p);
    }

    /** Unknown older archives must not call this. The signed manifest must agree with this record. */
    public synchronized void bindReload(String newJobId,String bundle,String manifestLineage,long manifestRevision)throws Exception {
        requireLive();Document d=documents.get(bundle);
        if(d==null||!d.lineage.equals(manifestLineage)||d.revision!=manifestRevision)fail("LINEAGE_DOCUMENT_UNKNOWN","Document has no matching durable lineage association");
        append("job_lineage_alias",map("lineage_id",d.lineage,"job_id",newJobId,"bundle_sha256",bundle,"document_revision",d.revision));
    }
    public synchronized void bindDocument(String jobId,String bundle)throws Exception {
        requireLive();State s=forJob(jobId);
        if(s.pending!=null)fail("LINEAGE_PENDING","Cannot bind a document during uncertain structural publication");
        if(aliases.get(jobId).modelRevision!=s.revision)fail("LINEAGE_DOCUMENT_STALE","Cannot bind stale native content as current lineage");
        append("job_lineage_document",map("lineage_id",s.id,"lineage_revision",s.revision,"bundle_sha256",bundle));
    }

    /** Call before initial operation admission. Add this returned value to the immutable op record.
     * The caller must also include current job_id, job_revision and board_load_revision. */
    public synchronized Map<String,Object> admissionFacts(String jobId)throws Exception {
        requireLive();Alias alias=aliases.get(jobId);
        if(alias==null)return immutable(map("status","unknown"));
        State s=states.get(alias.lineage);
        if(s.retiredBy!=null)fail("LINEAGE_RETIRED","Faulted attempt is retired; select its fresh replacement");
        if(alias.modelRevision!=s.revision)fail("LINEAGE_DOCUMENT_STALE","Historical native content cannot be admitted as the current lineage version");
        if(s.pending!=null)fail("LINEAGE_PENDING","An unresolved structural publication prevents processor admission");
        return immutable(map("status","known","lineage_id",s.id,"lineage_revision",s.revision));
    }

    /** Full native placed-state emptiness, Job identity and exclusive ownership are checked by
     * the native adapter. A token is internal, single-use adapter state, never a client argument. */
    public synchronized Snapshot requirePristine(String jobId,PendingPermit permit)throws Exception {
        requireLive();Alias a=aliases.get(jobId);State s=forJob(jobId);
        if(s.retiredBy!=null)fail("LINEAGE_RETIRED","Retired attempt cannot regain structural authority");
        if(a.modelRevision!=s.revision)fail("LINEAGE_DOCUMENT_STALE","A historical document cannot regain current structural authority");
        if(s.admitted)fail("LINEAGE_EXECUTED","Native processor admission permanently prevents structural edits");
        if(s.pending!=null&&(permit==null||permit.owner!=this||permit.closed||!permit.job.equals(jobId)||!s.pending.equals(permit.transaction)))fail("LINEAGE_PENDING","Structural publication remains unresolved");
        requireEmptyLoads(s);return new Snapshot(s);
    }
    private void requireEmptyLoads(State s)throws Exception {
        for(String id:s.loadIds){Load load=loads.get(id);
            if(load==null||!load.complete)fail("LINEAGE_HISTORY_UNKNOWN","Associated native load history is incomplete");
            if(load.entries!=0)fail("LINEAGE_HISTORY_PRESENT","Associated current or retired load contains native history keys");
            if(load.state.equals("loading_unknown"))fail("LINEAGE_LOAD_PENDING","Associated load change remains unresolved");
        }
    }

    /** Intents keep all IDs permanently reserved even when publication fails. */
    public synchronized PendingPermit reserve(String jobId,long expectedRevision,String transaction,String sourceFingerprint,Collection<String> ids)throws Exception {
        Snapshot s=requirePristine(jobId,null);if(s.revision!=expectedRevision)fail("LINEAGE_STALE","Lineage changed after preview");
        append("job_lineage_reservation",map("lineage_id",s.lineageId,"job_id",jobId,"expected_revision",expectedRevision,
            "transaction_id",transaction,"source_fingerprint",sourceFingerprint,"logical_ids",new ArrayList<>(ids)));
        return new PendingPermit(this,jobId,transaction);
    }
    /** Caller records this only after native publication AND required invalidations succeed. */
    public synchronized void publicationSucceeded(String jobId,long revision,String transaction,String resultFingerprint)throws Exception {
        requireLive();State s=forJob(jobId);
        append("job_lineage_published",map("lineage_id",s.id,"job_id",jobId,"lineage_revision",revision,"transaction_id",transaction,"result_fingerprint",resultFingerprint));
    }
    /** Existing setter edits also stale saved versions; the caller forces this before setters. */
    public synchronized void contentWillChange(String jobId,String operationId)throws Exception {
        requireLive();State s=forJob(jobId);
        if(aliases.get(jobId).modelRevision!=s.revision)fail("LINEAGE_DOCUMENT_STALE","Cannot revise stale native content as current lineage");
        append("job_lineage_content_change",map("lineage_id",s.id,"job_id",jobId,"expected_revision",s.revision,"operation_id",operationId));
    }

    /** Live caller invokes after the SAME forced append used for operation/load admission.
     * Recovery invokes on each already integrity-checked record, in its original sequence. */
    public synchronized void observe(String type,Map<String,Object> payload)throws Exception {
        try{prepareObservation(type,payload).run();}catch(Exception|Error failure){faulted=true;throw failure;}
    }
    /** Validate before a caller-owned append. Hold the Bridge's serialization fence until commit;
     * run exactly once after force. A stale/double commit permanently faults this authority. */
    public synchronized Runnable prepareObservation(String type,Map<String,Object> payload)throws Exception {
        requireLive();Map<String,Object> frozen=immutable(payload);Runnable commit;
        if(type.startsWith("job_lineage_"))commit=validate(type,frozen);
        else if(type.equals("operation"))commit=validateOperation(frozen);
        else if(Set.of("board_load_intent","board_load_outcome","board_load_history").contains(type))commit=validateLoad(frozen);
        else if(Set.of("board_replacement_intent","board_replacement_outcome","board_continuation_intent","board_continuation_outcome").contains(type))commit=validateLoad(map("record",frozen.get("new_load")));
        else if(type.equals("native_placement_checkpoint"))commit=validateCheckpoint(frozen);
        else commit=()->{};
        final long expected=generation;final Runnable action=commit;
        return ()->{synchronized(NativeJobLineage.this){
            if(faulted||generation!=expected){faulted=true;throw new IllegalStateException("Lineage commit is stale or repeated");}
            try{action.run();generation++;}catch(RuntimeException|Error failure){faulted=true;throw failure;}
        }};
    }
    private void append(String type,Map<String,Object> payload)throws Exception {
        Map<String,Object> frozen=immutable(payload);Runnable commit=prepareObservation(type,frozen);
        try{sink.appendAndForce(type,frozen);}catch(Exception|Error e){faulted=true;throw e;}
        // The supplied sink appends only. It must not recursively call this reducer.
        commit.run();
    }
    private Runnable validate(String type,Map<String,Object> p)throws Exception {
        String id=uuid(p,"lineage_id");
        if(type.equals("job_lineage_continuation_adoption"))return validateContinuationAdoption(p);
        if(type.equals("job_lineage_continuation_replacement"))return validateContinuationReplacement(p);
        if(type.equals("job_lineage_created")){
            fields(p,"lineage_id","job_id","origin");String job=uuid(p,"job_id"),origin=string(p,"origin",32);
            if(!Set.of("canonical-simulator","pinned-sample-simulator").contains(origin))fail("LINEAGE_ORIGIN","Only trusted fresh simulator imports establish lineage");
            if(states.containsKey(id)||aliases.containsKey(job)||previouslyObservedJobs.contains(job))fail("LINEAGE_CONFLICT","Lineage or job alias already exists");
            if(states.size()>=MAX_LINEAGES||aliases.size()>=MAX_ALIASES)fail("LINEAGE_CAPACITY","Lineage capacity reached");
            return ()->{states.put(id,new State(id));aliases.put(job,new Alias(id,1));};
        }
        State s=states.get(id);if(s==null)fail("LINEAGE_UNKNOWN","Record references an unknown lineage");
        switch(type){
        case "job_lineage_replacement": {
            fields(p,"schema_version","receipt_id","recovery_operation_id","fault_set_sha256","lineage_id","old_lineage","new_job_id","new_lineage_id","replaces_attempt","old_outcome_preserved");
            if(integer(p,"schema_version")!=1||!Boolean.TRUE.equals(p.get("old_outcome_preserved")))fail("LINEAGE_RECORD","Invalid replacement scope");
            String receipt=uuid(p,"receipt_id"),job=uuid(p,"new_job_id"),fresh=uuid(p,"new_lineage_id");uuid(p,"recovery_operation_id");hash(p,"fault_set_sha256");
            Map<String,Object> prior=object(p.get("old_lineage"));String oldJob=uuid(prior,"job_id");
            if(!id.equals(prior.get("lineage_id"))||!oldJob.equals(p.get("replaces_attempt"))||!NativeFaultedJobReplacement.same(prior,captureReplacementState(oldJob)))fail("LINEAGE_CONFLICT","Replacement does not capture exact old attempt history");
            if(replacementReceipts.containsKey(receipt)||continuationReplacementReceipts.containsKey(receipt)||continuationAdoptions.containsKey(receipt)||states.containsKey(fresh)||aliases.containsKey(job)||previouslyObservedJobs.contains(job)||s.retiredBy!=null)fail("LINEAGE_CONFLICT","Replacement identities reused or old attempt already retired");
            if(states.size()>=MAX_LINEAGES||aliases.size()>=MAX_ALIASES)fail("LINEAGE_CAPACITY","Replacement lineage capacity reached");
            return ()->{s.retiredBy=receipt;states.put(fresh,new State(fresh));aliases.put(job,new Alias(fresh,1));replacementReceipts.put(receipt,p);};
        }
        case "job_lineage_document": {
            fields(p,"lineage_id","lineage_revision","bundle_sha256");long revision=integer(p,"lineage_revision");String bundle=hash(p,"bundle_sha256");
            if(revision!=s.revision||s.pending!=null)fail("LINEAGE_STALE","Document binding is not current");
            Document prior=documents.get(bundle);
            if(prior!=null&&(!prior.lineage.equals(id)||prior.revision!=revision))fail("LINEAGE_DOCUMENT_CONFLICT","Document digest belongs to a different lineage version");
            if(prior==null&&documents.size()>=MAX_DOCUMENTS)fail("LINEAGE_CAPACITY","Document lineage capacity reached");
            return ()->documents.put(bundle,new Document(id,revision));
        }
        case "job_lineage_alias": {
            fields(p,"lineage_id","job_id","bundle_sha256","document_revision");String job=uuid(p,"job_id"),bundle=hash(p,"bundle_sha256");long revision=integer(p,"document_revision");Document d=documents.get(bundle);
            if(d==null||!d.lineage.equals(id)||d.revision!=revision)fail("LINEAGE_DOCUMENT_UNKNOWN","Reload has no matching document lineage");
            if(aliases.containsKey(job)||previouslyObservedJobs.contains(job))fail("LINEAGE_CONFLICT","Job alias already exists or predates trusted binding");if(aliases.size()>=MAX_ALIASES)fail("LINEAGE_CAPACITY","Job alias capacity reached");
            return ()->aliases.put(job,new Alias(id,revision));
        }
        case "job_lineage_reservation": {
            fields(p,"lineage_id","job_id","expected_revision","transaction_id","source_fingerprint","logical_ids");Alias alias=currentAlias(p,s);long revision=integer(p,"expected_revision");String transaction=uuid(p,"transaction_id");hash(p,"source_fingerprint");
            if(revision!=s.revision||s.pending!=null||s.admitted)fail("LINEAGE_STALE","Reservation is not admitted by current lineage");
            requireEmptyLoads(s);
            if(!(p.get("logical_ids") instanceof List))fail("LINEAGE_RECORD","Reservation IDs must be a list");List<?> raw=(List<?>)p.get("logical_ids");
            if(raw.isEmpty()||raw.size()>MAX_IDS)fail("LINEAGE_CAPACITY","Reservation must contain bounded IDs");Set<String> ids=new TreeSet<>();
            for(Object value:raw){if(!(value instanceof String))fail("LINEAGE_RECORD","Logical IDs must be text");String key=(String)value;if(key.isEmpty()||key.length()>4096||key.indexOf('\0')>=0||!ids.add(key))fail("LINEAGE_RECORD","Invalid or duplicate logical ID");}
            Set<String> merged=new TreeSet<>(s.reserved);merged.addAll(ids);if(merged.size()>MAX_IDS)fail("LINEAGE_CAPACITY","Lineage reservation inventory reached capacity");
            int total=merged.size();for(State other:states.values())if(other!=s)total+=other.reserved.size();if(total>MAX_IDS)fail("LINEAGE_CAPACITY","Store reservation inventory reached capacity");
            return ()->{s.reserved.addAll(ids);s.pending=transaction;s.revision++;alias.modelRevision=s.revision;};
        }
        case "job_lineage_published": {
            fields(p,"lineage_id","job_id","lineage_revision","transaction_id","result_fingerprint");hash(p,"result_fingerprint");currentAlias(p,s);long revision=integer(p,"lineage_revision");String transaction=uuid(p,"transaction_id");
            if(revision!=s.revision||!transaction.equals(s.pending))fail("LINEAGE_CONFLICT","Publication has no matching pending reservation");
            return ()->s.pending=null;
        }
        case "job_lineage_content_change": {
            fields(p,"lineage_id","job_id","expected_revision","operation_id");Alias alias=currentAlias(p,s);long revision=integer(p,"expected_revision");uuid(p,"operation_id");
            if(revision!=s.revision||s.pending!=null)fail("LINEAGE_STALE","Content change has no current lineage");return ()->{s.revision++;alias.modelRevision=s.revision;};
        }
        default: fail("LINEAGE_RECORD","Unknown lineage event");return null;
        }
    }
    private Runnable validateOperation(Map<String,Object> p)throws Exception {
        Object operationRaw=p.get("operation_id");
        Map<String,Object> priorBinding=operationRaw instanceof String?admissionBindings.get(operationRaw):null;
        if(priorBinding!=null){
            Map<String,Object> candidate=immutable(map("job_id",p.get("job_id"),"job_revision",p.get("job_revision"),"board_load_revision",p.get("board_load_revision"),"request_id",p.get("request_id"),"request_digest",p.get("request_digest"),"config_revision",p.get("config_revision"),"method",p.get("method"),"job_lineage",p.get("job_lineage")));
            if(!priorBinding.equals(candidate))fail("LINEAGE_CONFLICT","Operation changed or omitted its immutable job binding");
            return ()->{};
        }
        if(!Set.of("openpnp_start_job","openpnp_step_job").contains(p.get("method")))return ()->{};
        Object raw=p.get("job_lineage");String job=p.get("job_id") instanceof String?(String)p.get("job_id"):null;
        if(raw==null){if(job!=null&&aliases.containsKey(job))fail("LINEAGE_ADMISSION_MISSING","Known job admission omitted lineage");return observeUnknownJob(job);} // Older receipts confer no authority.
        Map<String,Object> facts=object(raw);String status=string(facts,"status",16);
        if(status.equals("unknown")){fields(facts,"status");if(job!=null&&aliases.containsKey(job))fail("LINEAGE_ADMISSION_MISSING","Known lineage cannot become unknown");return observeUnknownJob(job);}
        fields(facts,"status","lineage_id","lineage_revision");if(!status.equals("known"))fail("LINEAGE_RECORD","Unknown admission status");
        String id=uuid(facts,"lineage_id"),operation=uuid(p,"operation_id");long revision=integer(facts,"lineage_revision");
        uuid(p,"job_id");hash(p,"job_revision");string(p,"board_load_revision",128);string(p,"request_id",128);hash(p,"request_digest");string(p,"config_revision",128);
        State s=forJob(job);if(s.retiredBy!=null)fail("LINEAGE_RETIRED","Retired attempt cannot be admitted again");if(aliases.get(job).modelRevision!=s.revision)fail("LINEAGE_DOCUMENT_STALE","Initial admission uses stale native content");if(!s.id.equals(id))fail("LINEAGE_CONFLICT","Admission job alias differs from lineage");
        Map<String,Object> binding=immutable(map("job_id",job,"job_revision",p.get("job_revision"),"board_load_revision",p.get("board_load_revision"),"request_id",p.get("request_id"),"request_digest",p.get("request_digest"),"config_revision",p.get("config_revision"),"method",p.get("method"),"job_lineage",facts));
        Map<String,Object> old=admissionBindings.get(operation);
        if(old!=null){if(!old.equals(binding))fail("LINEAGE_CONFLICT","Operation changed its immutable job binding");return ()->{};}
        if(!"accepted".equals(p.get("state"))||revision!=s.revision||s.pending!=null)fail("LINEAGE_STALE","Initial native admission is not current");
        if(admissionBindings.size()>=10000)fail("LINEAGE_CAPACITY","Admission inventory reached capacity");
        return ()->{admissionBindings.put(operation,binding);s.admitted=true;};
    }
    private Runnable validateLoad(Map<String,Object> payload)throws Exception {
        Map<String,Object> record=object(payload.get("record"));String id=uuid(record,"load_id");Load next=new Load(record);
        Load prior=loads.get(id);if(prior!=null&&!next.keys.containsAll(prior.keys))fail("LINEAGE_HISTORY_REGRESSION","Native load history keys regressed");
        if(!loads.containsKey(id)&&loads.size()>=MAX_LOADS)fail("LINEAGE_CAPACITY","Load inventory reached capacity");
        Object job=record.get("job_id");Alias alias=job instanceof String?aliases.get(job):null;Runnable observed=observeUnknownJob(job instanceof String?(String)job:null);
        return ()->{observed.run();loads.put(id,next);if(alias!=null)states.get(alias.lineage).loadIds.add(id);};
    }
    private Runnable validateCheckpoint(Map<String,Object> payload)throws Exception {
        if(!"native-placement-complete-hook".equals(payload.get("state")))return ()->{};
        if(!(payload.get("context") instanceof Map))return ()->{};Map<String,Object> context=object(payload.get("context"));
        if(!Boolean.TRUE.equals(context.get("native_placed_status"))||!(context.get("board_load_id") instanceof String))return ()->{};
        String id=uuid(context,"board_load_id");Load prior=loads.get(id);if(prior==null)fail("LINEAGE_HISTORY_UNKNOWN","Native placement refers to an unknown load");
        String key=string(context,"board_instance_id",2048)+"⇒"+string(context,"placement_id",512);Map<String,Object> history=new TreeMap<>();for(String k:prior.keys)history.put(k,true);history.put(key,true);
        Load next=new Load(map("state",prior.state,"complete_native_history",prior.complete,"placed_history",history));
        return ()->loads.put(id,next);
    }
    private Runnable observeUnknownJob(String job)throws Exception {
        if(job==null||aliases.containsKey(job))return ()->{};
        if(job.length()>128)fail("LINEAGE_RECORD","Observed job ID exceeds bound");
        if(!previouslyObservedJobs.contains(job)&&previouslyObservedJobs.size()>=10000)fail("LINEAGE_CAPACITY","Unbound historical job inventory reached capacity");
        return ()->previouslyObservedJobs.add(job);
    }
    private Alias currentAlias(Map<String,Object> p,State s)throws Exception {String job=uuid(p,"job_id");Alias a=aliases.get(job);if(a==null||!a.lineage.equals(s.id)||a.modelRevision!=s.revision)fail("LINEAGE_DOCUMENT_STALE","Job alias is not the current lineage version");return a;}
    private State forJob(String job)throws Exception {Alias a=aliases.get(job);if(a==null)fail("LINEAGE_UNKNOWN","Job lacks trusted durable lineage");return states.get(a.lineage);}
    private void requireLive()throws Exception {if(faulted)fail("LINEAGE_FAULT","Uncertain journal publication requires verified restart recovery");}
    private static String string(Map<String,Object> p,String key,int max)throws Exception {Object raw=p.get(key);if(!(raw instanceof String))fail("LINEAGE_RECORD",key+" must be text");String value=(String)raw;if(value.isEmpty()||value.length()>max||value.indexOf('\0')>=0)fail("LINEAGE_RECORD","Invalid "+key);return value;}
    private static String uuid(Map<String,Object> p,String key)throws Exception {String value=string(p,key,36);try{if(!UUID.fromString(value).toString().equals(value))throw new IllegalArgumentException();}catch(IllegalArgumentException e){fail("LINEAGE_RECORD","Invalid UUID: "+key);}return value;}
    private static String hash(Map<String,Object> p,String key)throws Exception {String value=string(p,key,64);if(!value.matches("[a-f0-9]{64}"))fail("LINEAGE_RECORD","Invalid digest: "+key);return value;}
    private static long integer(Map<String,Object> p,String key)throws Exception {Object raw=p.get(key);if(!(raw instanceof Number))fail("LINEAGE_RECORD",key+" must be numeric");try{long n=new BigDecimal(raw.toString()).longValueExact();if(n<1||n>=9007199254740991L)throw new ArithmeticException();return n;}catch(ArithmeticException|NumberFormatException e){fail("LINEAGE_RECORD","Invalid exact revision: "+key);return 0;}}
    private static void fields(Map<String,Object> p,String... names)throws Exception {if(!p.keySet().equals(new HashSet<>(Arrays.asList(names))))fail("LINEAGE_RECORD","Missing or unknown record fields");}
    @SuppressWarnings("unchecked") private static Map<String,Object> object(Object raw)throws Exception {if(!(raw instanceof Map))fail("LINEAGE_RECORD","Object required");return (Map<String,Object>)raw;}
    @SuppressWarnings("unchecked") private static Map<String,Object> immutable(Map<String,Object> value)throws Exception {
        try{return (Map<String,Object>)freeze(NativeJournalJson.copy(value));}
        catch(IllegalArgumentException e){fail("LINEAGE_RECORD","Record cannot be represented as finite JSON");return null;}
    }
    private static Object freeze(Object value){
        if(value instanceof Map){Map<String,Object> out=new LinkedHashMap<>();for(Map.Entry<?,?> e:((Map<?,?>)value).entrySet())out.put((String)e.getKey(),freeze(e.getValue()));return Collections.unmodifiableMap(out);}
        if(value instanceof List){List<Object> out=new ArrayList<>();for(Object item:(List<?>)value)out.add(freeze(item));return Collections.unmodifiableList(out);}return value;
    }
    private static Map<String,Object> map(Object... pairs){Map<String,Object> result=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
    private static void fail(String code,String message)throws Fault {throw new Fault(code,message);}
}
