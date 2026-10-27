/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.io.*;
import java.lang.reflect.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import com.google.gson.Gson;
import org.openpnp.model.*;

/** Actual native models and forced historical records; no execution or sensing authority. */
public final class NativeLegacyOperationHistoryTest {
 static Configuration config;static Path root;static int checks,refusals;static List<String> labels=new ArrayList<>();static long sequence;
 static void check(boolean yes,String label){if(!yes)throw new AssertionError(label);checks++;labels.add(label);}
 interface Action{void run()throws Exception;}
 static void refused(Action action,String label)throws Exception{try{action.run();throw new AssertionError("Accepted: "+label);}catch(IOException expected){refusals++;check(true,label);}}
 static NativeFaultedJobReplacement owner()throws Exception{return new NativeFaultedJobReplacement(config,new NativeMaterialLoads(config,(t,p)->{throw new AssertionError("Unexpected material effect");}),new NativeBoardLoads((t,p)->{throw new AssertionError("Unexpected board effect");}),new NativeJobLineage((t,p)->{throw new AssertionError("Unexpected lineage effect");}),(t,p)->{throw new AssertionError("Unexpected replacement effect");});}
 static Map<String,Object> op(Object id,String state){return NativeFaultedJobReplacement.map("operation_id",id,"request_id","legacy-request","request_digest","legacy-digest","method","openpnp_start_job","state",state,"bridge_instance_id","prior-instance");}
 @SuppressWarnings("unchecked")static Map<String,Map<String,Object>> observed(NativeFaultedJobReplacement owner)throws Exception {Field field=NativeFaultedJobReplacement.class.getDeclaredField("operations");field.setAccessible(true);return(Map<String,Map<String,Object>>)field.get(owner);}
 static void forceAndObserve(NativeFaultedJobReplacement owner,String type,Map<String,Object> p)throws Exception {
  Map<String,Object> event=NativeFaultedJobReplacement.map("sequence",++sequence,"type",type,"payload",p);byte[] bytes=(new Gson().toJson(event)+"\n").getBytes(java.nio.charset.StandardCharsets.UTF_8);try(FileChannel journal=FileChannel.open(root.resolve("forced-history.jsonl"),StandardOpenOption.CREATE,StandardOpenOption.WRITE,StandardOpenOption.APPEND)){ByteBuffer data=ByteBuffer.wrap(bytes);while(data.hasRemaining())journal.write(data);journal.force(true);}Map<String,Object> durable=NativeJournalJson.parseObject(new String(bytes,java.nio.charset.StandardCharsets.UTF_8));owner.observe(type,NativeFaultedJobReplacement.object(durable.get("payload")));
 }
 static void noAuthority(NativeFaultedJobReplacement owner)throws Exception {Map<String,Object> status=owner.status();check(Boolean.FALSE.equals(status.get("execution_authority_restored")),"History restores no execution authority");check(((Number)status.get("retained_candidate_count")).intValue()==0&&((Number)status.get("retained_restart_count")).intValue()==0,"History attaches no candidate or restart");for(String key:List.of("intents","receipts","publications","continuation_intents","continuation_receipts","restart_attachments","restart_prior_process_dispositions"))check(((Map<?,?>)status.get(key)).isEmpty(),"Generic history creates no "+key);}
 public static void main(String[] args)throws Exception {
  String stateProperty=System.getProperty("openpnp.codex.testLegacyHistoryState");root=stateProperty==null?Files.createTempDirectory("openpnp-legacy-operation-history-"):Path.of(stateProperty);Files.createDirectories(root.resolve("config"));Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
  try{
   try(NativeFaultedJobReplacement owner=owner()){
    for(String id:List.of("interrupted-placement","op-0182","x","x".repeat(1024)," leading and trailing ",UUID.randomUUID().toString())){Map<String,Object> row=op(id,"running");forceAndObserve(owner,"operation",row);check(observed(owner).containsKey(id),"Exact bounded opaque history key retained length="+id.length());row.put("state","succeeded");check("running".equals(observed(owner).get(id).get("state")),"Observed history is immutable after supplied map changes");}
    byte[] prefix=Files.readAllBytes(root.resolve("forced-history.jsonl"));forceAndObserve(owner,"operation",op("interrupted-placement","outcome_unknown"));check("outcome_unknown".equals(observed(owner).get("interrupted-placement").get("state")),"New unknown disposition preserves opaque identity");check(Arrays.equals(prefix,Arrays.copyOf(Files.readAllBytes(root.resolve("forced-history.jsonl")),prefix.length)),"Forced original history prefix is preserved");noAuthority(owner);
    try{config.getMachine().submit(()->{owner.capture(new Job(),UUID.randomUUID().toString(),"op-0182");return null;},null,true).get(5,TimeUnit.SECONDS);throw new AssertionError("Opaque ID accepted as typed replacement capture");}catch(ExecutionException expected){check(expected.getCause() instanceof IOException&&"Canonical UUID required".equals(expected.getCause().getMessage()),"Typed replacement capture retains canonical UUID guard: "+expected.getCause());refusals++;}
   }
   for(Object id:Arrays.asList(null,"",1,true,List.of("op"),"x".repeat(1025))){try(NativeFaultedJobReplacement owner=owner()){refused(()->forceAndObserve(owner,"operation",op(id,"running")),"Invalid/overlong history ID refused: "+(id==null?"null":id.getClass().getSimpleName()));check(Boolean.TRUE.equals(owner.status().get("publication_fault")),"Invalid observed history faults reducer conservatively");noAuthority(owner);}}
   try(NativeFaultedJobReplacement owner=owner()){forceAndObserve(owner,"operation",op("op-0182","running"));refused(()->forceAndObserve(owner,"native_action_intent",NativeFaultedJobReplacement.map("operation_id","op-0182","event_id","event")),"Ledger-specific native action operation ID remains UUID");}
   try(NativeFaultedJobReplacement owner=owner()){forceAndObserve(owner,"operation",op("op-0182","running"));Map<String,Object> foreign=op("op-0182","outcome_unknown");foreign.put("method","openpnp_home_machine");refused(()->forceAndObserve(owner,"operation",foreign),"Opaque history cannot change admitted dependency scope");}
   check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"History component ends disabled and idle");proof.put("passed",true);
  }catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}finally{config.getMachine().close();proof.putAll(NativeFaultedJobReplacement.map("checks",checks,"refusals",refusals,"labels",labels,"forced_records",sequence,"native_feeds",0,"native_placements",0,"sensing_or_execution_authority_created",false,"process_id",ProcessHandle.current().pid()));Files.writeString(root.resolve("proof.json"),new Gson().toJson(proof));System.out.println(new Gson().toJson(proof));}System.exit(exit);
 }
}
