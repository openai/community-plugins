/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import static org.openpnp.codex.NativeLineageBridgeTest.*;

/** Actual processor-emitted checkpoint through Bridge persistence and a fresh Bridge replay. */
public final class NativeLineageCompletedJobTest {
 @SuppressWarnings("unchecked") public static void main(String[] args)throws Exception {
  Path root=Files.createTempDirectory("native-lineage-completed-"),samples=Path.of(args[0]);int exit=0;
  try {
   setup(root,samples);
   Map<String,Object> prepared=success("prepare_job",mutation("canonical_job",canonical()));
   String jobId=(String)prepared.get("job_id"),document=save();
   String lineageId=(String)lineage(prepared).get("lineage_id");
   Map<String,Object> loads=call("get_board_loads",obj());
   String loadId=(String)((Map<?,?>)((List<?>)loads.get("roots")).get(0)).get("load_id");
   success("set_machine_enabled",mutation("enabled",true));success("home_machine",mutation());
   check(Boolean.TRUE.equals(success("validate_job",mutation()).get("valid")),"real one-part job validates");
   Map<String,Object> op=await(call("start_job",mutation("job_id",jobId)));
   check("succeeded".equals(op.get("state")),"real native placement succeeds: "+G.toJson(op));
   check(((Number)result(op).get("placed")).intValue()==1&&feeds()==1,"one actual native placement and exactly one feed");
   check(((Number)op.get("native_steps_started")).longValue()>1,"processor actually advanced beyond initialization");
   Map<?,?> outcomes=(Map<?,?>)((Map<?,?>)op.get("native_action_ledger")).get("outcomes");
   check(((Number)outcomes.get("feed:native_hook_returned")).intValue()==1,"actual feed hook ledger agrees");
   List<Map<String,Object>> checkpoints=new ArrayList<>();
   for(String line:Files.readAllLines(root.resolve("journal/operations.jsonl"))){
    Map<String,Object> event=G.fromJson(line,Map.class);
    if("native_placement_checkpoint".equals(event.get("type")))checkpoints.add((Map<String,Object>)event.get("payload"));
   }
   check(checkpoints.size()==3,"all three producer-emitted checkpoint states reached the forced journal");
   check(List.of("placement-starting","before-assembly","native-placement-complete-hook").equals(List.of(checkpoints.get(0).get("state"),checkpoints.get(1).get("state"),checkpoints.get(2).get("state"))),"one exact ordered native checkpoint sequence for the single placement");
   Map<?,?> context=(Map<?,?>)checkpoints.get(2).get("context");
   check("R1".equals(context.get("placement_id"))&&loadId.equals(context.get("board_load_id"))&&Boolean.TRUE.equals(context.get("native_placed_status")),"durable checkpoint retains exact native placement and load identity");
   success("set_machine_enabled",mutation("enabled",false));
   String completedDocument=save();
   bridge.close();bridge=null;
   bridge=new Bridge(config,root.resolve("token"),root.resolve("journal"),samples,0,true,"sustained-workload");
   session=(String)call("request_control_session",obj("request_id",UUID.randomUUID().toString(),"ttl_seconds",300)).get("session_id");
   check("absent".equals(call("get_status",obj()).get("job_state")),"fresh Bridge does not reconstruct execution authority");
   reject("DOCUMENT_DEPENDENCY_CHANGED","load_job",mutation("artifact_id",document));
   Map<String,Object> restored=success("load_job",mutation("artifact_id",completedDocument));
   Map<String,Object> restoredLineage=lineage((Map<String,Object>)restored.get("job"));
   check(lineageId.equals(restoredLineage.get("lineage_id"))&&Boolean.TRUE.equals(restoredLineage.get("processor_admitted")),"completed archive retains consumed original lineage after replay");
   reject("STRUCTURE_HISTORY_PRESENT","plan_placement_structure",scope("changes",add("R2")));
   List<?> recoveredLoads=(List<?>)call("get_board_loads",obj()).get("loads");Map<?,?> consumed=null;
   for(Object value:recoveredLoads)if(loadId.equals(((Map<?,?>)value).get("load_id")))consumed=(Map<?,?>)value;
   check(consumed!=null&&((Number)consumed.get("native_history_entries")).intValue()==1,"retired original load retains exact complete placement history after replay");
   check(feeds()==1&&!machine.isEnabled()&&!machine.isBusy(),"readback, replay and rejected edit perform no additional feed or enable");
   System.out.println(G.toJson(Bridge.map("passed",true,"assertions",checks,"native_next_calls",op.get("native_steps_started"),"native_feeds",feeds(),"native_placements",1,"producer_emitted_checkpoints",checkpoints.size(),"fresh_bridge_same_jvm",true,"fresh_jvm",false,"physical_qualification",false)));
  }catch(Throwable e){e.printStackTrace();exit=1;}
  finally{if(bridge!=null)bridge.close();if(machine!=null)machine.close();}
  System.exit(exit);
 }
}
