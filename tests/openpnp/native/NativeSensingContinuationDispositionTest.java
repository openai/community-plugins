/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeSensingReconciliationTest.*;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;

/** Pinned-Gson coordinator schema tests. All sensor events, completion/disposition
 * authorities and load descriptors are explicit synthetic reducer fixtures. No native
 * machine, actual source intervention, journal force, Bridge or GUI is exercised. */
public final class NativeSensingContinuationDispositionTest {
    static final String ATTEMPT=id(801), CONTINUATION=id(802), PUBLICATION=id(803), JOB=id(804);
    static int assertions,rejected,prepared;
    interface Checked { void run() throws Exception; }
    static void check(boolean value,String why){assertions++;if(!value)throw new AssertionError(why);}
    static void refuse(String why,Checked work)throws Exception{try{work.run();throw new AssertionError("Accepted "+why);}catch(java.io.IOException expected){assertions++;rejected++;}}
    @SuppressWarnings("unchecked")static Map<String,Object> obj(Object value){return(Map<String,Object>)value;}
    static Map<String,Object> descriptor(String kind,int offset,String suffix){return m("kind",kind,"old_load_id",id(offset),"new_load_id",id(offset+1),"receipt_type",kind+"_continuation_"+suffix,"receipt_id",id(offset+2),"receipt_sha256","a".repeat(64),"old_outcome_preserved",true);}
    static Map<String,Object> dispositions(String material,String board){return m("sensing","fresh-observed-empty","nozzle_material","none-present","feeder_loads",List.of(descriptor("material",810,material),descriptor("material",820,material)),"board_loads",List.of(descriptor("board",830,board),descriptor("board",840,board)),"replacement_attempt_id",ATTEMPT,"unresolved_dependencies",List.of(),"continuation_id",CONTINUATION,"continuation_receipt_sha256","b".repeat(64),"publication_id",PUBLICATION,"publication_receipt_sha256","c".repeat(64));}
    static Env admitted()throws Exception{
        Env e=new Env();Map<String,Object> job=m("job_id",JOB,"job_revision","b".repeat(64),"board_load_revision","load-2","material_setup_revision","material-3","lineage_id",id(805),"lineage_revision",4);
        Map<String,Object> context=context();context.put("scope","job");context.put("job_context",job);e.obs(event("read.before",100,null,context,SOURCE));Map<String,Object> scope=scope(INSTANCE,SOURCE);scope.put("job_context",job);
        o(scope,"dependencies").putAll(m("operation_ids",List.of(OLDOP),"material_load_ids",List.of(id(810),id(811),id(820),id(821)),"board_load_ids",List.of(id(830),id(831),id(840),id(841)),"job_attempt_ids",List.of(JOB,ATTEMPT)));
        e.capture=e.j.captureFaultSet(scope);Map<String,Object> record=copy(e.r.taskRecord(TASK,REQOP,REQUEST,e.capture,NativeSensingReconciliation.CONTINUATION_KIND,7,"2030-01-01T00:00:00Z"));
        record.put("replacement_context",m("replacement_attempt_id",ATTEMPT,"continuation_capture_sha256","d".repeat(64)));e.append("sensing_reconciliation_task",record);e.admit();return e;
    }
    static Env verified()throws Exception{Env e=admitted();e.repair();e.probe();e.append("sensing_reconciliation_verified",e.r.verifiedRecord(TASK,e.verification));return e;}
    static Map<String,Object> resolved(Env e,Map<String,Object> d)throws Exception{return e.r.resolvedRecord(TASK,RECEIPT,d,List.of());}
    static Map<String,Object> row(Map<String,Object>d,String key,int index){return obj(((List<?>)d.get(key)).get(index));}
    static void malformed(Env e,Map<String,Object> canonical,String why,Consumer<Map<String,Object>> edit)throws Exception{
        Map<String,Object>d=copy(canonical);edit.accept(d);Map<String,Object>before=e.r.snapshot(TASK);int records=e.records.size();refuse(why,()->e.r.prepare("sensing_reconciliation_resolved",resolved(e,d),INSTANCE));
        check(NativeSensingReconciliation.digest(before).equals(NativeSensingReconciliation.digest(e.r.snapshot(TASK))),"Malformed disposition leaves task state exact");check(e.records.size()==records,"Malformed disposition appends no record");
    }
    static void schemaAndAuthority()throws Exception{
        Env e=verified();Map<String,Object>d=dispositions("adoption","outcome");e.completed.set(true);e.disposition.set(false);
        refuse("complete shaped continuation without independent exact disposition authority",()->e.r.prepare("sensing_reconciliation_resolved",resolved(e,d),INSTANCE));check(!e.r.liveDisposesOperation(OLDOP,INSTANCE),"Shape never authorizes future work");e.disposition.set(true);
        for(String material:List.of("adoption","outcome"))for(String board:List.of("adoption","outcome")){
            check(e.r.prepare("sensing_reconciliation_resolved",resolved(e,dispositions(material,board)),INSTANCE)!=null,"Allowed per-kind continuation subreceipt types validate");prepared++;
        }
        for(String key:d.keySet()){
            malformed(e,d,"missing top-level "+key,p->p.remove(key));
            malformed(e,d,"null top-level "+key,p->p.put(key,null));
        }
        malformed(e,d,"unknown top-level field",p->p.put("resume_original",true));
        malformed(e,d,"foreign replacement attempt",p->p.put("replacement_attempt_id",id(999)));
        for(String key:List.of("replacement_attempt_id","continuation_id","publication_id"))malformed(e,d,"noncanonical "+key,p->p.put(key,id(910).toUpperCase(Locale.ROOT)));
        for(String key:List.of("continuation_receipt_sha256","publication_receipt_sha256"))malformed(e,d,"malformed "+key,p->p.put(key,"x".repeat(64)));
        malformed(e,d,"nonempty unresolved dependencies",p->p.put("unresolved_dependencies",List.of(id(900))));
        malformed(e,d,"wrong sensing",p->p.put("sensing","assumed-empty"));malformed(e,d,"wrong material state",p->p.put("nozzle_material","physically-confirmed"));
        for(String key:List.of("feeder_loads","board_loads")){
            malformed(e,d,"empty "+key,p->p.put(key,List.of()));
            malformed(e,d,"oversized "+key,p->p.put(key,Collections.nCopies(65,row(p,key,0))));
            for(String field:row(d,key,0).keySet()){
                malformed(e,d,key+" missing "+field,p->row(p,key,0).remove(field));malformed(e,d,key+" null "+field,p->row(p,key,0).put(field,null));
            }
            malformed(e,d,key+" unknown descriptor field",p->row(p,key,0).put("bound",true));
            malformed(e,d,key+" original outcome not preserved",p->row(p,key,0).put("old_outcome_preserved",false));
            malformed(e,d,key+" wrong kind",p->row(p,key,0).put("kind",key.equals("feeder_loads")?"board":"material"));
            malformed(e,d,key+" wrong receipt type",p->row(p,key,0).put("receipt_type",key.equals("feeder_loads")?"board_continuation_adoption":"material_continuation_adoption"));
            malformed(e,d,key+" original receipt type",p->row(p,key,0).put("receipt_type",key.equals("feeder_loads")?"material_replacement_outcome":"board_replacement_outcome"));
            malformed(e,d,key+" pending receipt type",p->row(p,key,0).put("receipt_type",key.equals("feeder_loads")?"material_continuation_intent":"board_continuation_intent"));
            malformed(e,d,key+" malformed receipt hash",p->row(p,key,0).put("receipt_sha256","0".repeat(63)));
            malformed(e,d,key+" old equals new",p->row(p,key,0).put("new_load_id",row(p,key,0).get("old_load_id")));
            for(String field:List.of("old_load_id","new_load_id","receipt_id")){
                malformed(e,d,key+" noncanonical "+field,p->row(p,key,0).put(field,id(911).toUpperCase(Locale.ROOT)));
                malformed(e,d,key+" duplicate "+field,p->row(p,key,1).put(field,row(p,key,0).get(field)));
                malformed(e,d,"cross-kind duplicate "+field,p->row(p,"board_loads",0).put(field,row(p,"feeder_loads",0).get(field)));
            }
        }
        malformed(e,d,"cross-row old/new load overlap",p->row(p,"board_loads",0).put("new_load_id",row(p,"feeder_loads",0).get("old_load_id")));
        e.completed.set(false);refuse("disposition authority without completed wrapper",()->e.r.prepare("sensing_reconciliation_resolved",resolved(e,d),INSTANCE));e.completed.set(true);
        var token=e.r.wrapperCompleted(TASK,RECOP);Map<String,Object> result=resolved(e,d);Runnable commit=e.r.prepare("sensing_reconciliation_resolved",result,INSTANCE);blocked(e.j,"prepared continuation schema");commit.run();e.records.add(m("type","sensing_reconciliation_resolved","payload",result,"bridge_instance_id",INSTANCE));blocked(e.j,"synthetic final receipt without live activation");check(!e.r.liveDisposesOperation(OLDOP,INSTANCE),"Committed schema result alone creates no live operation authority");
        // Do not activate: independent authorities here are synthetic component inputs.
        Env replay=new Env();replay.completed.set(true);replay.disposition.set(false);
        for(Map<String,Object> record:e.records){String type=(String)record.get("type");if(type.equals("sensing_reconciliation_resolved")){refuse("replay final without independent disposition authority",()->replay.r.recover(type,o(record,"payload"),INSTANCE));replay.disposition.set(true);}if(NativeSensingReconciliation.matches(type))replay.r.recover(type,o(record,"payload"),INSTANCE);else replay.j.recover(type,o(record,"payload"),INSTANCE);}
        check(Boolean.TRUE.equals(replay.r.snapshot(TASK).get("historical")),"Resolved continuation history is marked historical");check(!replay.r.liveDisposesOperation(OLDOP,INSTANCE),"Replay restores no live disposition authority");refuse("historical task yields no wrapper token",()->replay.r.wrapperCompleted(TASK,RECOP));
    }
    static void latchDisposal()throws Exception{
        Map<String,Object>before=m("state","retained","original_operation_id",OLDOP,"original_action_id",OLDOP+"/native-action-1","original_part_id","R0603-1K"),after=copy(before);after.put("state","empty");
        Env e=admitted();Map<String,Object> valid=e.r.interventionRecord(TASK,List.of(binding(INSTANCE,SOURCE)),List.of(binding(INSTANCE,NEW_SOURCE)),List.of(id(950)),before,after);
        check(e.r.prepare("sensing_reconciliation_intervention",valid,INSTANCE)!=null,"Continuation permits shaped disposal action to empty while preserving original context");
        for(String field:List.of("original_operation_id","original_action_id","original_part_id")){
            Map<String,Object>bad=copy(valid);obj(obj(bad.get("intervention")).get("synthetic_latch_after")).put(field,null);refuse("latch disposal changes "+field,()->e.r.prepare("sensing_reconciliation_intervention",bad,INSTANCE));
        }
        Map<String,Object>empty=copy(valid);o(empty,"intervention").put("native_action_ids",List.of());refuse("latch disposal without an action",()->e.r.prepare("sensing_reconciliation_intervention",empty,INSTANCE));
        Map<String,Object>duplicate=copy(valid);o(duplicate,"intervention").put("native_action_ids",List.of(id(950),id(950)));refuse("duplicate disposal action",()->e.r.prepare("sensing_reconciliation_intervention",duplicate,INSTANCE));
        Map<String,Object>wrong=copy(valid);obj(o(wrong,"intervention").get("synthetic_latch_after")).put("state","lost");refuse("disposal does not end empty",()->e.r.prepare("sensing_reconciliation_intervention",wrong,INSTANCE));
        Map<String,Object>badId=copy(valid);o(badId,"intervention").put("native_action_ids",List.of(OLDOP+"/native-action-1"));refuse("disposal action is not its canonical source action UUID",()->e.r.prepare("sensing_reconciliation_intervention",badId,INSTANCE));
        e.append("sensing_reconciliation_intervention",valid);blocked(e.j,"disposal intervention alone");check(!e.r.liveDisposesOperation(OLDOP,INSTANCE),"Shaped source intervention grants no readiness");
        Env standalone=new Env();standalone.fault();standalone.task();standalone.admit();Map<String,Object>forbidden=standalone.r.interventionRecord(TASK,List.of(binding(INSTANCE,SOURCE)),List.of(binding(INSTANCE,NEW_SOURCE)),List.of(id(950)),before,after);refuse("standalone still cannot clear material latch",()->standalone.r.prepare("sensing_reconciliation_intervention",forbidden,INSTANCE));
    }
    public static void main(String[]args)throws Exception{
        schemaAndAuthority();latchDisposal();Map<String,Object>proof=m("passed",true,"assertions",assertions,"refusals",rejected,"allowed_descriptor_type_combinations",prepared,"scope","Pinned-Gson schema/reducer and sensing permits; all observation/disposition/completion authority inputs synthetic; no native machine, actual source intervention, FileChannel force, Bridge, GUI, packaged MCP or physical qualification");
        if(args.length>1){Path dir=Path.of(args[1]);Files.createDirectory(dir);Files.writeString(dir.resolve("proof.json"),new com.google.gson.GsonBuilder().setPrettyPrinting().create().toJson(proof)+"\n");}
        System.out.println("NATIVE_SENSING_CONTINUATION_DISPOSITION_PASS "+assertions+" assertions "+rejected+" refusals "+prepared+" descriptor combinations");
    }
}
