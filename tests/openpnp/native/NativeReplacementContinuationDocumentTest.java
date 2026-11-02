/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationPublicationTest.*;
import java.io.IOException;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Fresh local decisions persist missing candidate documents using real native models, owned
 * document storage and forced FileChannel journals. Original faults/wrappers/host callbacks are
 * explicitly synthetic. No native job initialization, feeder execution or placement. */
public final class NativeReplacementContinuationDocumentTest {
    static final String INTENT="faulted_job_replacement_continuation_document_intent";
    static final String OUTCOME="faulted_job_replacement_continuation_document_outcome";
    static final List<Map<String,Object>> results=new ArrayList<>();
    static int assertions,expectedRefusals;
    static final List<String> unexpectedAccepts=new ArrayList<>();
    interface Work {void run()throws Exception;}
    static void check(boolean value,String reason){assertions++;if(!value)throw new AssertionError(reason);}
    static void refuse(String reason,Work work)throws Exception{try{work.run();throw new AssertionError("Accepted "+reason);}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){assertions++;expectedRefusals++;}}
    static Map<String,Object> copy(Map<String,Object> row)throws IOException{return NativeFaultedJobReplacement.mutable(row);}
    static String hash(Map<String,Object> row)throws IOException{return NativeFaultedJobReplacement.digest(row);}
    static Map<String,Object> document(Env env,String attempt)throws Exception{return o(env.replacement.progress(attempt).get("document"));}
    static String phase(Map<String,Object> row)throws Exception{return NativeFaultedJobReplacement.effectivePhase(row);}
    static List<Map<String,Object>> selected(List<Map<String,Object>> events,String type)throws Exception{List<Map<String,Object>> out=new ArrayList<>();for(Map<String,Object> e:events)if(type.equals(e.get("type")))out.add(o(e.get("payload")));return out;}
    static Map<String,String> files(Path root)throws Exception{Map<String,String> out=new TreeMap<>();if(Files.isDirectory(root))try(var stream=Files.walk(root)){for(Path p:stream.filter(Files::isRegularFile).toList())if(!p.getFileName().toString().equals("receipt-key"))out.put(root.relativize(p).toString(),HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(p)))+":"+Files.getLastModifiedTime(p));}return out;}
    static final class Session implements AutoCloseable {
        final Fixture fixture;final Path owned;final Map<String,Object> original;final Map<String,Boolean> history;
        Fresh fresh;NativeFaultedJobReplacement.ContinuationPermit permit;NativeReplacementDocuments documents;
        final List<Map<String,Object>> storageBoundaries=new ArrayList<>();String failSuffix;Boolean failAfter;boolean storeFailureTriggered;
        Session(String name,boolean alreadySaved)throws Exception {
            fixture=new Fixture(name,"definition");owned=root.resolve(name+"-owned");
            if(alreadySaved)saveDocument(fixture,name);else Files.createDirectory(owned);
            original=immutableOriginal(fixture.e);history=new TreeMap<>(fixture.e.oldJob.getPlacedStatusSnapshot());
            fixture.closeOriginal("outcome_unknown");newDecision();documents=adapter();
        }
        NativeReplacementDocuments adapter()throws Exception{return new NativeReplacementDocuments(config,owned.resolve("documents"),owned,(target,renamed)->{
            if(target.getFileName().toString().equals("receipt-key"))return;
            storageBoundaries.add(m("target",target.getFileName().toString(),"after_rename",renamed));
            if(!storeFailureTriggered&&failSuffix!=null&&target.getFileName().toString().endsWith(failSuffix)&&renamed==failAfter){storeFailureTriggered=true;throw new IOException("test-only interrupted native document publication");}
        });}
        void newDecision()throws Exception{fresh=fixture.fresh(fixture.capture(),"continue-faulted-job-replacement");permit=fresh.begin();}
        void closeDecision()throws Exception{Map<String,Object> terminal=freshOperation(fresh);terminal.put("state","outcome_unknown");terminal.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",false,"physical_outcome_verified",false));fixture.e.append("operation",terminal);fixture.e.terminalWrappers.add(fresh.operation);fresh.revoke();permit.close();}
        void ensure()throws Exception{task(()->{fixture.e.replacement.ensureContinuationDocument(permit,documents);return null;});}
        void preserved()throws Exception{check(history.equals(fixture.e.oldJob.getPlacedStatusSnapshot()),"Original placed history stays exact");check(NativeFaultedJobReplacement.same(original,immutableOriginal(fixture.e)),"Original operation and native action outcomes stay exact");check(fixture.e.freshJob.getPlacedStatusSnapshot().isEmpty(),"Candidate gains no placed history");check(fixture.e.installedJob==null,"Document completion does not select a native host job");check(Boolean.FALSE.equals(fixture.e.replacement.status().get("execution_authority_restored")),"Document completion grants no execution authority");}
        public void close()throws Exception{permit.close();fixture.close();}
    }
    static Map<String,Object> effects(Session session)throws Exception{return m("counters",counters(),"configuration_files",configurationFiles(),"material",session.fixture.e.material.captureReplacementState(),"boards",session.fixture.e.boards.captureReplacementState(session.fixture.e.jobId),"lineage",session.fixture.e.lineage.captureReplacementState(session.fixture.e.jobId),"candidate_mapping",session.fixture.e.candidate.mapping());}
    static Map<String,Object> ensureRecord(Session s)throws Exception{return task(()->s.fixture.e.replacement.ensureContinuationDocument(s.permit,s.documents));}
    static void assertNoEffects(Session s,Map<String,Object> before,int eventsBefore)throws Exception{
        check(NativeFaultedJobReplacement.same(before,effects(s)),"Document completion changes no native counter/configuration/load/lineage/candidate mapping");
        for(Map<String,Object> row:s.fixture.e.events.subList(eventsBefore,s.fixture.e.events.size()))check(Set.of(INTENT,OUTCOME).contains(row.get("type")),"Only document records appear in the measured save boundary; no sensing/load effects");s.preserved();
    }
    static void positive(boolean alreadySaved,boolean occupied)throws Exception{
        int start=assertions;String name="document-positive-"+(alreadySaved?"existing":"missing")+(occupied?"-occupied":"");
        try(Session s=new Session(name,alreadySaved)){
            var e=s.fixture.e;if(occupied)task(()->{alterNativeGate("occupied");return null;});
            try{
                Map<String,Object> before=effects(s);Map<String,Object> oldDocument=copy(document(e,s.fixture.attempt));Map<String,String> oldFiles=files(s.owned);long bytes=Files.size(e.file);int offset=e.events.size(),boundary=s.storageBoundaries.size();
                Map<String,Object> record=ensureRecord(s);check(record!=null,"Exact candidate document record returned");
                assertNoEffects(s,before,offset);check("completed".equals(phase(document(e,s.fixture.attempt))),"Document progress is completed only after root outcome");
                if(alreadySaved){check(bytes==Files.size(e.file)&&offset==e.events.size(),"Existing forced document causes no redundant root records");check(oldFiles.equals(files(s.owned))&&boundary==s.storageBoundaries.size(),"Existing document causes no document-store write");check(NativeFaultedJobReplacement.same(oldDocument,document(e,s.fixture.attempt)),"Existing document progress stays exact");}
                else{
                    check(selected(e.events,INTENT).size()==1&&selected(e.events,OUTCOME).size()==1,"Missing document has one forced intent and outcome");
                    check(s.storageBoundaries.size()>boundary,"Actual native document adapter wrote archive and authenticated receipt");
                    check(NativeFaultedJobReplacement.same(record,selected(e.events,OUTCOME).get(0)),"Returned record is exact forced fresh-authority outcome");
                    check(s.fresh.operation.equals(record.get("recovery_operation_id"))&&!s.fixture.oldRecovery.equals(record.get("recovery_operation_id")),"Outcome uses current fresh recovery operation, preserving original separately");
                    check(s.fixture.oldRecovery.equals(record.get("original_recovery_operation_id")),"Original recovery context remains exact");
                    validateRecord(s,record);
                }
                Map<String,String> savedFiles=files(s.owned);long savedBytes=Files.size(e.file);int boundaries=s.storageBoundaries.size();Map<String,Object> repeated=ensureRecord(s);
                check(NativeFaultedJobReplacement.same(record,repeated)&&Files.size(e.file)==savedBytes&&savedFiles.equals(files(s.owned))&&boundaries==s.storageBoundaries.size(),"Repeated completed ensure is a no-op without redundant writes");
                task(()->{s.permit.check();return null;});if(occupied)check(config.getMachine().getDefaultHead().getDefaultNozzle().getPart()==tray.getPart(),"Pre-disposal save leaves exact native nozzle occupancy unchanged");
                replay(s,name+"-replay",true);if(!alreadySaved&&!occupied)malformed(s);
                results.add(m("name",name,"assertions",assertions-start,"existing_document_noop",alreadySaved,"occupied_save_allowed",occupied));
            }finally{if(occupied)task(()->{restoreNativeGate("occupied");return null;});}
        }
    }
    static void storageFailure(String suffix,boolean after)throws Exception{
        int start=assertions;String name="document-save-"+(suffix.equals(".zip")?"archive":"receipt")+"-"+(after?"after":"before");
        try(Session s=new Session(name,false)){
            var e=s.fixture.e;s.failSuffix=suffix;s.failAfter=after;Map<String,Object> before=effects(s);int offset=e.events.size();
            refuse("interrupted actual native document save",s::ensure);check(s.storeFailureTriggered,"Deterministic native file publication seam reached");
            check(selected(e.events,INTENT).size()==1&&selected(e.events,OUTCOME).isEmpty(),"Failed save retains only the fresh document intent");
            check(!"completed".equals(phase(document(e,s.fixture.attempt))),"Failed save never marks document completed");assertNoEffects(s,before,offset);
            task(()->{refuse("failed save closes old continuation permit",s.permit::check);return null;});
            long bytes=Files.size(e.file);Map<String,String> stored=files(s.owned);refuse("same decision cannot retry unknown save",s::ensure);check(bytes==Files.size(e.file)&&stored.equals(files(s.owned)),"Stale decision retry performs no new writes");
            Map<String,Object> first=copy(selected(e.events,INTENT).get(0));String priorContinuation=s.permit.continuationId(),priorOperation=s.fresh.operation;
            s.closeDecision();s.failSuffix=null;s.newDecision();check(!s.permit.continuationId().equals(priorContinuation)&&!s.fresh.operation.equals(priorOperation),"Retry has a distinct explicit local decision and operation");
            // Reuse the exact same adapter, including a receipt renamed before its original save failed.
            Map<String,Object> result=ensureRecord(s);check(selected(e.events,INTENT).size()==2&&selected(e.events,OUTCOME).size()==1,"Fresh retry appends its own intent/outcome without upgrading old unknown intent");
            check(NativeFaultedJobReplacement.same(first,selected(e.events,INTENT).get(0)),"Original unknown save intent remains byte-for-byte immutable");
            check(s.fresh.operation.equals(result.get("recovery_operation_id")),"Retry outcome is bound to the new local operation");validateRecord(s,result);s.preserved();replay(s,name+"-replay",true);
            results.add(m("name",name,"assertions",assertions-start,"same_adapter_retry",true,"prior_unknown_intent_preserved",true));
        }
    }
    static void rootFailure(String type,boolean after)throws Exception{
        int start=assertions;String name="document-root-"+(type.equals(INTENT)?"intent":"outcome")+"-"+(after?"after":"before");
        try(Session s=new Session(name,false)){
            var e=s.fixture.e;e.failType=type;e.failAfterForce=after;Map<String,Object> before=effects(s);int offset=e.events.size();
            try{refuse("root document journal force boundary",s::ensure);}finally{e.failType=null;}
            check(Boolean.TRUE.equals(e.replacement.status().get("publication_fault")),"Root journal uncertainty fences the live reducer");
            boolean durableOutcome=type.equals(OUTCOME)&&after;check((!selected(e.events,OUTCOME).isEmpty())==durableOutcome,"Only actually forced outcomes appear in immutable journal history");
            check(!"completed".equals(phase(document(e,s.fixture.attempt))),"Unacknowledged root write never creates a live successful document reduction");
            task(()->{refuse("root write failure closes continuation permit",s.permit::check);return null;});
            long bytes=Files.size(e.file);Map<String,String> disk=files(s.owned);refuse("faulted reducer refuses another document write",s::ensure);check(bytes==Files.size(e.file)&&disk.equals(files(s.owned)),"Faulted reducer retry performs no write");
            assertNoEffects(s,before,offset);replay(s,name+"-replay",durableOutcome);results.add(m("name",name,"assertions",assertions-start,"target_forced",after,"outcome_forced",durableOutcome));
        }
    }
    static void admissionRefusals()throws Exception {
        int start=assertions;
        try(Session s=new Session("document-admission",false);Session other=new Session("document-foreign-owner",false)){
            var e=s.fixture.e;long bytes=Files.size(e.file);Map<String,String> disk=files(s.owned);
            refuse("off-executor save",()->e.replacement.ensureContinuationDocument(s.permit,s.documents));
            refuse("missing local decision",()->task(()->e.replacement.ensureContinuationDocument(null,s.documents)));
            refuse("foreign owner's live continuation",()->task(()->e.replacement.ensureContinuationDocument(other.permit,s.documents)));
            task(()->{config.getMachine().setEnabled(true);return null;});try{refuse("enabled machine before saving",s::ensure);}finally{task(()->{config.getMachine().setEnabled(false);return null;});}
            check(bytes==Files.size(e.file)&&disk.equals(files(s.owned)),"Admission refusals precede all root and document writes");
            var placement=e.freshJob.getBoardLocations().get(0).getBoard().getPlacements().get(0);int rank=placement.getRank();task(()->{placement.setRank(rank+1);return null;});try{refuse("changed actual retained candidate",s::ensure);}finally{task(()->{placement.setRank(rank);return null;});}
            check(bytes==Files.size(e.file)&&disk.equals(files(s.owned)),"Changed candidate refuses before persistence");
            Map<String,Object> terminal=freshOperation(s.fresh);terminal.put("state","outcome_unknown");terminal.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",false,"physical_outcome_verified",false));e.append("operation",terminal);
            long terminalBytes=Files.size(e.file);refuse("terminal fresh operation without coordinator revocation",s::ensure);check(terminalBytes==Files.size(e.file)&&disk.equals(files(s.owned)),"Stale active-admission refusal causes no write");s.preserved();
        }results.add(m("name","document-admission-refusals","assertions",assertions-start));
    }
    static void postForceEnabled()throws Exception {
        int start=assertions;try(Session s=new Session("document-enabled-after-force",false)){
            var e=s.fixture.e;java.lang.reflect.Field field=NativeFaultedJobReplacement.class.getDeclaredField("sink");field.setAccessible(true);NativeFaultedJobReplacement.Sink actual=(NativeFaultedJobReplacement.Sink)field.get(e.replacement);int[] calls={0};
            field.set(e.replacement,(NativeFaultedJobReplacement.Sink)(type,payload)->{actual.appendAndForce(type,payload);if(type.equals(OUTCOME)){calls[0]++;config.getMachine().setEnabled(true);}});
            boolean rejected=false;try{try{s.ensure();}catch(IOException|Bridge.Fault expected){rejected=true;expectedRefusals++;}}finally{field.set(e.replacement,actual);task(()->{config.getMachine().setEnabled(false);return null;});}
            check(calls[0]==1,"Native enabled change follows an actually forced root outcome");assertions++;if(!rejected)unexpectedAccepts.add("enabled-after-outcome-force");
            check(selected(e.events,OUTCOME).size()==1&&"completed".equals(phase(document(e,s.fixture.attempt))),"Forced document outcome is retained as history after postforce state change");
            if(rejected)task(()->{refuse("postforce enabled change closes fresh decision",s.permit::check);return null;});s.preserved();replay(s,"document-enabled-after-force-replay",true);results.add(m("name","document-enabled-after-force","assertions",assertions-start,"accepted",!rejected));
        }
    }
    static void validateRecord(Session s,Map<String,Object> record)throws Exception {
        var e=s.fixture.e;Map<String,Object> manifest=o(record.get("manifest")),context=o(manifest.get("context"));
        check(s.fixture.attempt.equals(record.get("replacement_attempt_id"))&&s.permit.continuationId().equals(record.get("continuation_id")),"New document outcome identifies the exact fresh continuation and original replacement attempt");
        check(s.fresh.faultCapture.digest.equals(record.get("fault_set_sha256"))&&s.fresh.capture.digest.equals(record.get("continuation_capture_sha256")),"New document outcome binds current capture and sensing fault set");
        check(e.jobId.equals(context.get("original_job_id"))&&s.fixture.attempt.equals(context.get("replacement_attempt_id"))&&s.fixture.oldRecovery.equals(context.get("recovery_operation_id"))&&e.faultCapture.digest.equals(context.get("fault_set_sha256")),"Actual archive manifest preserves original save context without forging old decision completion");
        check(NativeFaultedJobReplacement.same(context,record.get("document_context"))&&NativeFaultedJobReplacement.same(manifest.get("source_mapping"),e.candidate.mapping()),"Exact original document context and retained candidate source mapping match");
        check(hash(manifest).equals(record.get("manifest_sha256"))&&hash(o(manifest.get("candidate_graph"))).equals(manifest.get("candidate_graph_sha256")),"Document manifest and exact native candidate graph hashes match");
        check(hash(o(manifest.get("source_mapping"))).equals(manifest.get("source_mapping_sha256")),"Source mapping content digest matches");
        String id=(String)record.get("document_id");Path archive=s.owned.resolve("documents").resolve(id+".zip");check(Files.isRegularFile(archive)&&HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(archive))).equals(id),"Actual native archive is present with exact content address");
        Map<String,Object> progress=document(e,s.fixture.attempt);check(NativeFaultedJobReplacement.same(record,progress.get("record"))&&Boolean.FALSE.equals(progress.get("storage_verified_by_this_read"))&&Boolean.FALSE.equals(progress.get("reattachment_performed")),"Status exposes durable document record without claiming a storage verification or reattachment");
        task(()->{try(var read=s.documents.reload(id,new NativeReplacementDocuments.Context(e.jobId,s.fixture.attempt,s.fixture.oldRecovery,e.faultCapture.digest))){check(read.job()!=e.freshJob&&read.job().getPlacedStatusSnapshot().isEmpty(),"Actual authenticated document reconstructs a separate unpublished empty-history native job");check(NativeFaultedJobReplacement.same(read.mapping(),e.candidate.mapping()),"Authenticated native reconstruction retains exact source mapping");}return null;});
    }
    static void malformed(Session s)throws Exception {
        for(String kind:List.of("unknown-field","missing-field","foreign-continuation","foreign-operation","foreign-attempt","foreign-fault","foreign-definition","wrong-progress","wrong-intent","manifest-unknown","manifest-mapping-hash","manifest-graph-hash","manifest-context","manifest-load-authority","duplicate-intent","duplicate-outcome","missing-intent","outcome-after-terminal")) {
            List<Map<String,Object>> changed=new ArrayList<>();
            for(Map<String,Object> original:s.fixture.e.events){Map<String,Object> event=copy(original);String type=(String)event.get("type");if(kind.equals("missing-intent")&&type.equals(INTENT))continue;
                if(type.equals(OUTCOME)){
                    Map<String,Object> p=copy(o(event.get("payload")));event.put("payload",p);
                    switch(kind){case "unknown-field":p.put("unexpected",true);break;case "missing-field":p.remove("definition_sha256");break;case "foreign-continuation":p.put("continuation_id",id());break;case "foreign-operation":p.put("recovery_operation_id",id());break;case "foreign-attempt":p.put("replacement_attempt_id",id());break;case "foreign-fault":p.put("fault_set_sha256","0".repeat(64));break;case "foreign-definition":p.put("definition_sha256","0".repeat(64));break;case "wrong-progress":p.put("progress_sha256","0".repeat(64));break;case "wrong-intent":p.put("document_intent_sha256","0".repeat(64));break;}
                    if(kind.startsWith("manifest-")){Map<String,Object> manifest=copy(o(p.get("manifest")));p.put("manifest",manifest);switch(kind){case "manifest-unknown":manifest.put("unexpected",true);break;case "manifest-mapping-hash":manifest.put("source_mapping_sha256","0".repeat(64));break;case "manifest-graph-hash":manifest.put("candidate_graph_sha256","0".repeat(64));break;case "manifest-context":Map<String,Object> context=copy(o(manifest.get("context")));context.put("recovery_operation_id",s.fresh.operation);manifest.put("context",context);break;case "manifest-load-authority":manifest.put("load_presence_verified",true);break;}p.put("manifest_sha256",hash(manifest));}
                    if(kind.equals("outcome-after-terminal")){Map<String,Object> terminal=freshOperation(s.fresh);terminal.put("state","outcome_unknown");terminal.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",false,"physical_outcome_verified",false));changed.add(m("type","operation","payload",terminal));}
                    if(kind.equals("duplicate-outcome"))changed.add(copy(event));
                }
                if(type.equals(INTENT)&&kind.equals("duplicate-intent"))changed.add(copy(event));changed.add(event);
            }
            Map<String,Object> before=effects(s);Map<String,String> disk=files(s.owned);
            try(Env reduced=new Env("document-corrupt-"+kind,true)){
                boolean rejected=false;try{reduced.replay(changed);}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){rejected=true;expectedRefusals++;}
                assertions++;if(!rejected)unexpectedAccepts.add(kind);
                check(Files.size(reduced.file)==0&&reduced.installedJob==null&&!reduced.replacement.ownsCandidate(s.fixture.e.candidate),"Malformed replay cannot produce native/job/candidate authority");
                check(NativeFaultedJobReplacement.same(before,effects(s))&&disk.equals(files(s.owned)),"Malformed replay performs no native effect or document write");
            }
        }
    }
    static void replay(Session s,String name,boolean completed)throws Exception {
        var e=s.fixture.e;Map<String,Object> before=effects(s);Map<String,String> disk=files(s.owned);
        try(Env reduced=new Env(name,true)){
            reduced.replay(e.events);check("completed".equals(phase(document(reduced,s.fixture.attempt)))==completed,"Replay retains exactly the document outcome actually forced");
            check(reduced.installedJob==null&&!reduced.replacement.ownsCandidate(e.candidate),"Historical document record acquires no actual native job or candidate");
            check(Boolean.FALSE.equals(reduced.replacement.status().get("execution_authority_restored")),"Replay grants no execution authority");
            refuse("replay cannot mint same-process candidate authority",()->task(()->reduced.replacement.captureContinuation(s.fixture.attempt,e.oldJob)));
            refuse("replayed root cannot borrow a foreign live continuation permit",()->task(()->reduced.replacement.ensureContinuationDocument(s.permit,s.documents)));
            check(Files.size(reduced.file)==0&&NativeFaultedJobReplacement.same(before,effects(s))&&disk.equals(files(s.owned)),"Replay appends no records and performs no native or document effects");
        }
    }

    public static void main(String[] args)throws Exception {
        initialize(Path.of(args[0]));Throwable error=null;
        try {positive(false,false);positive(true,false);positive(false,true);admissionRefusals();for(String suffix:List.of(".zip",".receipt.json"))for(boolean after:List.of(false,true))storageFailure(suffix,after);for(String type:List.of(INTENT,OUTCOME))for(boolean after:List.of(false,true))rootFailure(type,after);postForceEnabled();check(unexpectedAccepts.isEmpty(),"Unexpected malformed/stale document acceptance: "+unexpectedAccepts);}
        catch(Throwable failure){error=failure;failure.printStackTrace();}
        finally{config.getMachine().close();Map<String,Object> proof=m("passed",error==null,"assertions",assertions,"refusals",expectedRefusals,"fixture_assertions",checks,"cases",results,"unexpected_accepts",unexpectedAccepts,"error",error==null?null:error.toString(),"scope","Fresh explicit continuation document completion with actual native candidates, document archive/receipt force+rename and root FileChannel force boundaries; synthetic original fault, terminal wrapper and host fixture; actual unknown interrupted saves, no process kill or restart reattachment claimed","actual_native_job_initializations",0,"actual_native_feeds",0,"actual_native_job_placements",0,"actual_native_synthetic_nozzle_picks",nativeNozzlePicks,"actual_native_synthetic_nozzle_cleanup_places",nativeNozzleCleanupPlaces,"bridge_continuation_qualified",false,"restart_reattachment_qualified",false,"hardware_qualified",false,"public_package_qualified",false);Files.writeString(root.resolve("proof.json"),JSON.toJson(proof)+"\n");System.out.println("NATIVE_REPLACEMENT_CONTINUATION_DOCUMENT_RESULT "+JSON.toJson(proof));}System.exit(error==null?0:1);
    }
}
