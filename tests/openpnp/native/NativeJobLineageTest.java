/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Reducer qualification using actual forced files, not native processor qualification. */
public final class NativeJobLineageTest {
    static final Gson GSON=new Gson();static int checks;static final String HASH="a".repeat(64),HASH2="b".repeat(64);
    interface Checked {void run()throws Exception;}
    static String id(){return UUID.randomUUID().toString();}
    static void check(boolean condition,String label){checks++;if(!condition)throw new AssertionError(label);}
    static void fails(String code,Checked task)throws Exception {try{task.run();throw new AssertionError("Expected "+code);}catch(NativeJobLineage.Fault e){check(e.code.equals(code),"Expected "+code+", got "+e.code);}}
    static Map<String,Object> map(Object... pairs){Map<String,Object> p=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)p.put((String)pairs[i],pairs[i+1]);return p;}
    static Map<String,Object> clone(Map<String,Object> value){return GSON.fromJson(GSON.toJson(value),Map.class);}
    static final class Store implements AutoCloseable {
        final Path path;final FileChannel file;final NativeJobLineage ledger;boolean failBefore,failAfter;int writes;
        Store(Path directory,String name)throws Exception {path=directory.resolve(name);file=FileChannel.open(path,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);ledger=new NativeJobLineage(this::append);}
        void append(String type,Map<String,Object> p)throws Exception {
            if(failBefore)throw new java.io.IOException("before append");
            byte[] bytes=(GSON.toJson(map("type",type,"payload",p))+"\n").getBytes(StandardCharsets.UTF_8);
            ByteBuffer buffer=ByteBuffer.wrap(bytes);while(buffer.hasRemaining())file.write(buffer);file.force(true);writes++;
            if(failAfter)throw new java.io.IOException("after actual force");
        }
        void observe(String type,Map<String,Object> p)throws Exception {append(type,p);ledger.observe(type,p);}
        NativeJobLineage recover()throws Exception {
            NativeJobLineage replay=new NativeJobLineage((t,p)->{throw new AssertionError("Recovery attempted write");});
            for(String line:Files.readAllLines(path)){Map<String,Object> row=GSON.fromJson(line,Map.class);replay.observe((String)row.get("type"),(Map<String,Object>)row.get("payload"));}
            return replay;
        }
        public void close()throws Exception {file.close();}
    }
    static Map<String,Object> operation(NativeJobLineage ledger,String job,String operation,String state)throws Exception {
        return map("operation_id",operation,"request_id","request-initial","method","openpnp_step_job","state",state,
            "job_id",job,"request_digest",HASH,"config_revision","cfg-1","job_revision",HASH,"board_load_revision","load-1","job_lineage",ledger.admissionFacts(job));
    }
    static Map<String,Object> load(String job,String load,String state,Map<String,Object> history,boolean complete) {
        return map("record",map("job_id",job,"load_id",load,"state",state,"complete_native_history",complete,"placed_history",history));
    }
    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("native-job-lineage-");
        try(Store s=new Store(root,"reservations.jsonl")){
            String job=id(),lineage=s.ledger.createFresh(job,"canonical-simulator");
            check(s.ledger.requirePristine(job,null).lineageId.equals(lineage),"fresh durable lineage");
            check(s.recover().requirePristine(job,null).revision==1,"creation replay");
            String transaction=id();NativeJobLineage.PendingPermit permit=s.ledger.reserve(job,1,transaction,HASH,List.of("B1⇒R2"));
            check(s.ledger.requirePristine(job,permit).revision==2,"own pending adapter may inspect new state");
            fails("LINEAGE_PENDING",()->s.ledger.requirePristine(job,null));
            fails("LINEAGE_PENDING",()->s.recover().requirePristine(job,permit));
            permit.close();fails("LINEAGE_PENDING",()->s.ledger.requirePristine(job,permit));
            fails("LINEAGE_PENDING",()->s.recover().requirePristine(job,null));
            fails("LINEAGE_PENDING",()->s.ledger.admissionFacts(job));
            fails("LINEAGE_PENDING",()->s.ledger.bindDocument(job,HASH));
            fails("LINEAGE_CONFLICT",()->s.ledger.publicationSucceeded(job,2,id(),HASH2));
            s.ledger.publicationSucceeded(job,2,transaction,HASH2);
            check(s.recover().requirePristine(job,null).reservedLogicalIds.contains("B1⇒R2"),"published tombstone survives replay");
            fails("LINEAGE_STALE",()->s.ledger.reserve(job,1,id(),HASH,List.of("B1⇒R3")));
            s.ledger.contentWillChange(job,id());check(s.ledger.requirePristine(job,null).revision==3,"setter edit advances bound native alias");
            check(s.recover().requirePristine(job,null).revision==3,"setter revision replay");
        }
        try(Store s=new Store(root,"documents.jsonl")){
            String job=id(),lineage=s.ledger.createFresh(job,"pinned-sample-simulator");s.ledger.bindDocument(job,HASH);
            String loaded=id();s.ledger.bindReload(loaded,HASH,lineage,1);
            check(s.ledger.requirePristine(loaded,null).revision==1,"current document alias");
            String transaction=id();s.ledger.reserve(loaded,1,transaction,HASH,List.of("B1⇒R9"));s.ledger.publicationSucceeded(loaded,2,transaction,HASH2);
            check(s.ledger.requirePristine(loaded,null).revision==2,"edited reloaded native alias advanced");
            fails("LINEAGE_DOCUMENT_STALE",()->s.ledger.requirePristine(job,null));
            fails("LINEAGE_DOCUMENT_STALE",()->s.ledger.bindDocument(job,HASH2));
            String old=id();s.ledger.bindReload(old,HASH,lineage,1);
            fails("LINEAGE_DOCUMENT_STALE",()->s.ledger.requirePristine(old,null));
            fails("LINEAGE_DOCUMENT_STALE",()->s.ledger.admissionFacts(old));
            s.ledger.bindDocument(loaded,HASH2);String current=id();s.ledger.bindReload(current,HASH2,lineage,2);
            check(s.recover().requirePristine(current,null).reservedLogicalIds.contains("B1⇒R9"),"document reload preserves reservations");
            fails("LINEAGE_DOCUMENT_UNKNOWN",()->s.ledger.bindReload(id(),"c".repeat(64),lineage,2));
            fails("LINEAGE_DOCUMENT_UNKNOWN",()->s.ledger.bindReload(id(),HASH2,id(),2));
            fails("LINEAGE_DOCUMENT_UNKNOWN",()->s.ledger.bindReload(id(),HASH2,lineage,1));
            String other=id();s.ledger.createFresh(other,"canonical-simulator");
            fails("LINEAGE_DOCUMENT_CONFLICT",()->s.ledger.bindDocument(other,HASH2));
        }
        try(Store s=new Store(root,"admission.jsonl")){
            String job=id();s.ledger.createFresh(job,"canonical-simulator");s.ledger.bindDocument(job,HASH);
            String operation=id();Map<String,Object> admitted=operation(s.ledger,job,operation,"accepted");
            s.observe("operation",admitted);
            fails("LINEAGE_EXECUTED",()->s.ledger.requirePristine(job,null));
            fails("LINEAGE_EXECUTED",()->s.recover().requirePristine(job,null));
            Map<String,Object> failed=clone(admitted);failed.put("state","failed");s.observe("operation",failed);
            fails("LINEAGE_EXECUTED",()->s.recover().requirePristine(job,null));
            String reload=id();s.ledger.bindReload(reload,HASH,s.ledger.snapshot(job).lineageId,1);
            fails("LINEAGE_EXECUTED",()->s.ledger.requirePristine(reload,null));
            for(String key:List.of("job_revision","board_load_revision","request_id","request_digest","config_revision","method")){
                Map<String,Object> bad=clone(failed);bad.put(key,key.equals("method")?"openpnp_start_job":"changed");
                fails("LINEAGE_CONFLICT",()->s.ledger.prepareObservation("operation",bad));
            }
            Map<String,Object> missing=clone(admitted);missing.remove("job_lineage");fails("LINEAGE_CONFLICT",()->s.ledger.prepareObservation("operation",missing));
            Map<String,Object> unknown=clone(admitted);unknown.put("job_lineage",map("status","unknown"));fails("LINEAGE_CONFLICT",()->s.ledger.prepareObservation("operation",unknown));
            Map<String,Object> hidden=clone(admitted);hidden.remove("job_lineage");hidden.remove("job_id");fails("LINEAGE_CONFLICT",()->s.ledger.prepareObservation("operation",hidden));
            Map<String,Object> hidden2=clone(admitted);hidden2.put("method","openpnp_capture_camera");final Map<String,Object> hiddenMethod=hidden2;fails("LINEAGE_CONFLICT",()->s.ledger.prepareObservation("operation",hiddenMethod));
            check(!s.ledger.admissionFacts(id()).containsKey("lineage_id"),"unknown job confers no lineage");
        }
        try(Store s=new Store(root,"loads.jsonl")){
            String job=id();s.ledger.createFresh(job,"canonical-simulator");String first=id();
            s.observe("board_load_intent",load(job,first,"loading_unknown",Map.of(),true));
            fails("LINEAGE_LOAD_PENDING",()->s.ledger.requirePristine(job,null));
            s.observe("board_load_outcome",load(job,first,"loaded",Map.of(),true));
            check(s.ledger.requirePristine(job,null).loadIds.size()==1,"completed empty load");
            s.observe("board_load_history",load(job,first,"loaded",Map.of("orphan⇒R0",false),true));
            fails("LINEAGE_HISTORY_PRESENT",()->s.ledger.requirePristine(job,null));
            String replacement=id();s.observe("board_load_outcome",load(job,replacement,"loaded",Map.of(),true));
            fails("LINEAGE_HISTORY_PRESENT",()->s.recover().requirePristine(job,null));
            String freshJob=id();s.ledger.createFresh(freshJob,"canonical-simulator");String freshLoad=id();
            s.observe("board_load_outcome",load(freshJob,freshLoad,"loaded",Map.of(),true));
            check(s.ledger.requirePristine(freshJob,null).loadIds.size()==1,"fresh board lineage does not inherit unrelated retired load");
            s.observe("board_load_history",load(freshJob,freshLoad,"loaded",Map.of(),false));
            fails("LINEAGE_HISTORY_UNKNOWN",()->s.ledger.requirePristine(freshJob,null));
        }
        try(Store s=new Store(root,"checkpoint.jsonl")){
            String job=id();s.ledger.createFresh(job,"canonical-simulator");String loadId=id();
            s.observe("board_load_outcome",load(job,loadId,"loaded",Map.of(),true));
            s.observe("native_placement_checkpoint",map("state","native-placement-complete-hook","context",map("native_placed_status",true,"board_load_id",loadId,"board_instance_id","B1","placement_id","R1")));
            fails("LINEAGE_HISTORY_PRESENT",()->s.recover().requirePristine(job,null));
            fails("LINEAGE_HISTORY_REGRESSION",()->s.ledger.prepareObservation("board_load_history",load(job,loadId,"loaded",Map.of(),true)));
            Map<String,Object> forbidden=map("lineage_id",s.ledger.snapshot(job).lineageId,"job_id",job,"expected_revision",1,"transaction_id",id(),"source_fingerprint",HASH,"logical_ids",List.of("B1⇒R4"));
            fails("LINEAGE_HISTORY_PRESENT",()->s.ledger.prepareObservation("job_lineage_reservation",forbidden));
            String unbound=id();s.observe("board_load_outcome",load(unbound,id(),"loaded",Map.of(),true));
            Map<String,Object> upgrade=map("lineage_id",id(),"job_id",unbound,"origin","canonical-simulator");fails("LINEAGE_CONFLICT",()->s.ledger.prepareObservation("job_lineage_created",upgrade));
            String oldOperationJob=id();s.observe("operation",map("method","openpnp_step_job","job_id",oldOperationJob));
            Map<String,Object> upgradeOp=map("lineage_id",id(),"job_id",oldOperationJob,"origin","canonical-simulator");fails("LINEAGE_CONFLICT",()->s.ledger.prepareObservation("job_lineage_created",upgradeOp));
        }
        for(boolean after:List.of(false,true))try(Store s=new Store(root,"uncertain-"+after+".jsonl")){
            String job=id();s.ledger.createFresh(job,"canonical-simulator");s.failBefore=!after;s.failAfter=after;
            try{s.ledger.reserve(job,1,id(),HASH,List.of("B1⇒R99"));throw new AssertionError("Expected force seam");}catch(java.io.IOException e){checks++;}
            fails("LINEAGE_FAULT",()->s.ledger.requirePristine(job,null));
            NativeJobLineage recovered=s.recover();
            if(after){fails("LINEAGE_PENDING",()->recovered.requirePristine(job,null));check(recovered.snapshot(job).reservedLogicalIds.contains("B1⇒R99"),"afterforce reservation recovered");}
            else check(recovered.requirePristine(job,null).revision==1,"beforewrite no reservation recovered");
        }
        try(Store s=new Store(root,"invalid.jsonl")){
            fails("LINEAGE_ORIGIN",()->s.ledger.createFresh(id(),"client-says-new"));
            String job=id(),lineage=s.ledger.createFresh(job,"canonical-simulator");
            int writes=s.writes;Map<String,Object> bad=map("lineage_id",lineage,"job_id",job,"expected_revision",1,"transaction_id",id(),"source_fingerprint",HASH,"logical_ids",List.of("same","same"));
            fails("LINEAGE_RECORD",()->s.ledger.prepareObservation("job_lineage_reservation",bad));
            check(s.ledger.snapshot(job).revision==1&&s.writes==writes,"invalid replay leaves state unchanged");
            Map<String,Object> malformed=map("lineage_id",lineage,"expected_revision",1.5,"job_id",job,"operation_id",id());
            fails("LINEAGE_RECORD",()->s.ledger.prepareObservation("job_lineage_content_change",malformed));
            malformed.put("expected_revision",Double.NaN);fails("LINEAGE_RECORD",()->s.ledger.prepareObservation("job_lineage_content_change",malformed));
            Map<String,Object> premature=operation(s.ledger,job,id(),"running");fails("LINEAGE_STALE",()->s.ledger.prepareObservation("operation",premature));
            check(!s.ledger.snapshot(job).admitted,"bad admission did not consume lineage");
        }
        try(Store s=new Store(root,"post-force-rejection.jsonl")){
            String job=id();s.ledger.createFresh(job,"canonical-simulator");
            Map<String,Object> wrong=operation(s.ledger,job,id(),"running");
            fails("LINEAGE_STALE",()->s.ledger.observe("operation",wrong));
            fails("LINEAGE_FAULT",()->s.ledger.requirePristine(job,null));
        }
        try(Store s=new Store(root,"repeated-commit.jsonl")){
            String job=id();s.ledger.createFresh(job,"canonical-simulator");
            Map<String,Object> op=operation(s.ledger,job,id(),"accepted");Runnable prepared=s.ledger.prepareObservation("operation",op);
            check(!s.ledger.snapshot(job).admitted,"pre-append validation has no state effect");
            s.append("operation",op);prepared.run();
            try{prepared.run();throw new AssertionError("Expected repeated commit failure");}catch(IllegalStateException expected){checks++;}
            fails("LINEAGE_FAULT",()->s.ledger.requirePristine(job,null));
            fails("LINEAGE_EXECUTED",()->s.recover().requirePristine(job,null));
        }
        try(Store s=new Store(root,"external-append-failure.jsonl")){
            String job=id();s.ledger.createFresh(job,"canonical-simulator");
            check(Boolean.TRUE.equals(s.ledger.describe(job).get("durable_pristine")),"readback durable pristine without native authority");
            s.ledger.publicationFailed();fails("LINEAGE_FAULT",()->s.ledger.requirePristine(job,null));
            check("faulted".equals(s.ledger.describe(job).get("status")),"external uncertain append has explicit readback fault");
        }
        System.out.println(GSON.toJson(map("passed",true,"assertions",checks,"scope","private forced-journal reducer; no native Job identity or processor qualification","physical_qualification",false)));
    }
}
