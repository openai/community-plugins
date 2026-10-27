/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.codex.prototype.tagged.NativeDiagnosticBridgeTest;
import static org.openpnp.codex.NativeControllerTestProcesses.*;

/** Captured actual writer transcript, then reducer-only controlled alterations. */
public final class NativeControllerRecoveryOrderTest {
 static int checks;static List<Map<String,Object>> original;
 interface Edit {void edit(List<Map<String,Object>> rows)throws Exception;}
 static Map<String,Object> obj(Object x){return (Map<String,Object>)x;}
 static List<Map<String,Object>> copy(){List<Map<String,Object>> rows=new ArrayList<>();for(Map<String,Object> r:original)rows.add(NativeJournalJson.copy(r));return rows;}
 static Map<String,Object> last(List<Map<String,Object>> rows){return obj(rows.get(rows.size()-1).get("payload"));}
 static void replay(List<Map<String,Object>> rows)throws Exception {Map<String,Map<String,Object>> ops=new LinkedHashMap<>();NativeControllerJournal reducer=new NativeControllerJournal();for(Map<String,Object> row:rows){String type=(String)row.get("type");Map<String,Object> p=obj(row.get("payload"));if(NativeControllerJournal.matches(type))reducer.acceptRecovered(type,p,ops);if(type.equals("operation")){String id=(String)p.get("operation_id");reducer.observeRecoveredOperation(p,ops.get(id));ops.put(id,p);}}if(reducer.hasHistory()){reducer.finishRecovery(ops.get(reducer.operationId()));require(Boolean.FALSE.equals(reducer.snapshot().get("native_authority_restored")),"No replay authority");}}
 static void rejects(Edit edit)throws Exception {List<Map<String,Object>> rows=copy();edit.edit(rows);try{replay(rows);throw new AssertionError("Malformed transcript accepted");}catch(java.io.IOException|IllegalArgumentException expected){checks++;}}
 public static void main(String[] args)throws Exception {
  Path root=Files.createTempDirectory("openpnp-controller-recovery-order-"),producer=root.resolve("producer");
  run(NativeDiagnosticBridgeTest.class,producer,0,"normal",producer.toString(),Path.of(args[0]).toRealPath().toString());Path journal=producer.resolve("journal/operations.jsonl");String before=sha(journal);original=new ArrayList<>();for(String line:Files.readAllLines(journal))original.add(NativeJournalJson.parseObject(line));replay(copy());checks++;
  rejects(rows->rows.add(NativeJournalJson.copy(rows.get(rows.size()-1))));
  rejects(rows->{Map<String,Object> finalRecord=rows.remove(rows.size()-1);rows.add(6,finalRecord);});
  rejects(rows->obj(last(rows).get("native_completion")).put("phase","wrapper-failed"));
  rejects(rows->obj(last(rows).get("native_completion")).put("native_wrapper_completed",false));
  rejects(rows->obj(last(rows).get("native_completion")).put("native_wrapper_succeeded",false));
  rejects(rows->obj(last(rows).get("native_completion")).put("submission_id","not-uuid"));
  rejects(rows->last(rows).remove("native_completion"));
  rejects(rows->last(rows).remove("result"));
  rejects(rows->obj(last(rows).get("result")).put("controller_instance_id",UUID.randomUUID().toString()));
  rejects(rows->obj(last(rows).get("result")).put("profile","foreign"));
  rejects(rows->obj(last(rows).get("result")).put("steps_attempted",new java.math.BigDecimal("4.00000000000000001")));
  rejects(rows->obj(last(rows).get("result")).put("completed_recipe",false));
  rejects(rows->obj(last(rows).get("result")).put("outcome_unknown",true));
  rejects(rows->obj(last(rows).get("result")).put("error_type","IOException"));
  rejects(rows->obj(last(rows).get("result")).put("uncommitted_observation",new LinkedHashMap<>()));
  rejects(rows->obj(obj(last(rows).get("result")).get("observation")).put("observation_sequence",0));
  rejects(rows->obj(obj(last(rows).get("result")).get("observation")).put("observed_at","2020-01-01T00:00:00Z"));
  rejects(rows->{for(Map<String,Object> row:rows)if(row.get("type").equals("controller_diagnostic_retired"))obj(row.get("payload")).put("completed_recipe",false);});
  // Conservative wrapper failure can preserve a successful body without claiming terminal success.
  List<Map<String,Object>> failed=copy();Map<String,Object> op=last(failed),body=op.get("result") instanceof Map?obj(op.get("result")):null;op.put("state","outcome_unknown");op.put("result",NativeControllerJournal.map("code","NATIVE_WRAPPER_FAILED","known_body_outcome",NativeControllerJournal.map("state","succeeded","result",body),"repeat_action_performed",false));op.put("native_completion",NativeControllerJournal.map("phase","wrapper-failed","native_wrapper_completed",true,"native_wrapper_succeeded",false));replay(failed);checks++;
  for(int end:new int[]{4,6,7,8,15}){List<Map<String,Object>> rows=copy().subList(0,end);Map<String,Object> prior=null;for(Map<String,Object> row:rows)if(row.get("type").equals("operation"))prior=obj(row.get("payload"));Map<String,Object> unknown=NativeJournalJson.copy(prior);unknown.put("state","outcome_unknown");unknown.put("result",NativeControllerJournal.map("recovery","prior-instance-interrupted","repeat_action_performed",false));rows.add(NativeControllerJournal.map("type","operation","payload",unknown));replay(rows);checks++;}
  Map<String,Object> abandoned=NativeJournalJson.copy(op);abandoned.put("state","cancelled");abandoned.put("result",NativeControllerJournal.map("resolution","abandoned-after-simulator-reset","previous_physical_outcome","unknown","repeat_action_performed",false));failed.add(NativeControllerJournal.map("type","operation","payload",abandoned));replay(failed);checks++;
  for(String key:List.of("previous_physical_outcome","repeat_action_performed")){Map<String,Object> r=obj(abandoned.get("result"));Object beforeValue=r.put(key,key.equals("previous_physical_outcome")?"known":true);try{replay(failed);throw new AssertionError("Contradictory abandonment accepted");}catch(java.io.IOException expected){checks++;}r.put(key,beforeValue);}
  require(sha(journal).equals(before),"Producer history changed");checks++;System.out.println("CONTROLLER_RECOVERY_ORDER_RESULT {\"passed\":true,\"assertions\":"+checks+",\"source_journal_sha256\":\""+before+"\",\"physical_qualification\":false}");
 }
}
