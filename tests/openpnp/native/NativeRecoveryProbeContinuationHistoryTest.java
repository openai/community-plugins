/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeFaultedSensingReplacementContinuationBridgeTest.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.driver.NullDriver;

/** Fresh JVM historical read only. Pending native observation retirement and exact resolution history restore no candidate, source or authority. */
public final class NativeRecoveryProbeContinuationHistoryTest {
    public static void main(String[] args)throws Exception {
        if(args.length!=3)throw new IllegalArgumentException("Expected samples, completed live evidence state, scenario");
        state=Path.of(args[1]).toAbsolutePath();scenario=args[2];phase="history";
        Map<String,Object> proof=new LinkedHashMap<>();int exit=0;
        try {
            Map<String,Object> live=NativeJournalJson.parseObject(Files.readString(state.resolve("live-proof.json")));
            check(Boolean.TRUE.equals(live.get("passed"))&&((Number)live.get("pid")).longValue()!=ProcessHandle.current().pid(),"Reader starts only after a passed live case in a distinct JVM");
            byte[] journal=Files.readAllBytes(state.resolve("journal/operations.jsonl"));Map<String,Object> documents=documentInventory();
            String firstTask=(String)live.get("initial_task_id"),nextTask=(String)live.get("continuation_task_id");
            Map<String,Object> firstBefore=NativeJournalJson.parseObject(Files.readString(state.resolve("interrupted-task.json"))),nextBefore=NativeJournalJson.parseObject(Files.readString(state.resolve("continuation-task.json")));
            Map<String,Object> originalBefore=latestOperation((String)live.get("original_operation_id")),firstOpBefore=latestOperation((String)live.get("initial_recovery_operation_id")),nextOpBefore=latestOperation((String)live.get("continuation_operation_id"));
            Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();NullDriver driver=(NullDriver)config.getMachine().getDrivers().get(0);
            check(driver.getControlledVacuumSource()==null,"Fresh configuration has no process-owned native sensing source");
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"native-simulator");
            Map<String,Object> capabilities=read("get_capabilities"),status=read("get_status");
            check(((List<?>)capabilities.get("tools")).contains("openpnp_get_sensing_reconciliation")&&!((List<?>)capabilities.get("tools")).contains("openpnp_request_sensing_reconciliation"),"Fresh reader advertises historical inspection without local recovery requests");
            check(Boolean.FALSE.equals(map(capabilities.get("vacuum_sensing")).get("available"))&&Boolean.FALSE.equals(map(capabilities.get("sensing_reconciliation")).get("available")),"Historical reader has no live sensing or reconciliation source authority");
            check(status.get("gui_ownership")==null&&read("get_control_session").get("session_id")==null,"Fresh historical Bridge has no local GUI ownership or control lease");
            Map<String,Object> first=read("get_sensing_reconciliation","task_id",firstTask),next=read("get_sensing_reconciliation","task_id",nextTask);
            check("reconciliation_unknown".equals(first.get("state")),"Original interrupted recovery remains unknown in the fresh process");
            same(firstBefore.get("state"),first.get("state"),"Original task state remains exact");
            for(String field:List.of("task","intervention","verification","recovery_operation_id","receipt"))same(firstBefore.get(field),first.get(field),"Original interrupted task retains exact "+field);
            same(map(progress(firstBefore).get("publication")),map(progress(first).get("publication")),"Original untouched publication history remains byte-equivalent JSON");
            check("untouched".equals(map(progress(first).get("publication")).get("phase")),"Historical continuation never rewrites original unattempted publication");
            check("completed".equals(map(progress(firstBefore).get("document")).get("phase")),"Original live task snapshot retains the document completed before its interrupted native probe");
            same(nextBefore.get("receipt"),next.get("receipt"),"Fresh process preserves exact verified continuation resolution receipt");
            for(String field:List.of("task","intervention","verification","recovery_operation_id"))same(nextBefore.get(field),next.get(field),"Fresh continuation task retains exact "+field);
            check(Boolean.TRUE.equals(next.get("historical"))&&Boolean.FALSE.equals(next.get("live_resolution_activated"))&&Boolean.FALSE.equals(next.get("execution_authority_restored")),"Replayed continuation resolution is historical and grants no live execution authority");
            Map<String,Object> p=progress(next);
            same(map(progress(nextBefore).get("document")),map(p.get("document")),"Fresh reader preserves the exact forced document manifest, hashes and continuation completion chain");
            check("completed".equals(map(p.get("document")).get("phase")),"Replayed replacement document remains completed");
            same(map(progress(nextBefore).get("definition")).get("record"),map(p.get("definition")).get("record"),"Original candidate definition remains exact without reconstruction");
            List<Map<String,Object>> rows=events();int retiredAt=-1,disposedAt=-1,firstProbe=-1;String recovery=(String)live.get("continuation_operation_id");
            Set<String> pending=new TreeSet<>((List<String>)live.get("pending_observation_ids"));Set<String> initialIntents=new TreeSet<>();
            for(int i=0;i<rows.size();i++){
                Map<String,Object> e=rows.get(i),payload=map(e.get("payload"));String type=(String)e.get("type");
                if(recovery.equals(payload.get("operation_id"))&&type.equals("sensing_source_scope_retirement_returned")){
                    retiredAt=i;same(pending,new TreeSet<>((List<String>)map(payload.get("facts")).get("pending_observation_ids")),"Replayed retirement binds exact original pending native call IDs");
                    check(Boolean.FALSE.equals(map(payload.get("facts")).get("original_outcomes_known"))&&Boolean.FALSE.equals(map(payload.get("facts")).get("occupancy_cleared")),"Historical scope retirement does not assert old outcome or cleared occupancy");
                }
                if(recovery.equals(payload.get("operation_id"))&&type.equals("sensing_source_disposal_returned"))disposedAt=i;
                if(type.equals("vacuum_observation_intent")&&payload.get("context") instanceof Map&&recovery.equals(map(payload.get("context")).get("operation_id"))&&firstProbe<0)firstProbe=i;
                if(type.startsWith("vacuum_observation_")&&payload.get("data") instanceof Map){String id=(String)map(payload.get("data")).get("observation_id");if(pending.contains(id)){check(type.equals("vacuum_observation_intent"),"Unknown original native observation retains its intent without fabricated outcome");initialIntents.add(id);}}
            }
            check(!pending.isEmpty()&&initialIntents.equals(pending),"Fresh reader preserves the complete original pending native observation union");
            check(retiredAt>=0&&disposedAt>retiredAt&&firstProbe>disposedAt,"Saved native chain retires exact pending calls, then actually disposes before any fresh probe");
            proof.putAll(Bridge.map("retirement_event_index",retiredAt,"disposal_event_index",disposedAt,"first_fresh_probe_index",firstProbe,"original_pending_observation_ids",pending,"boundary",live.get("boundary")));
            check(Boolean.FALSE.equals(map(p.get("definition")).get("candidate_retained_in_process")),"Journal replay reconstructs no process-owned native candidate");
            for(String component:List.of("material","boards"))for(Object raw:(List<?>)map(p.get(component)).get("rows")){Map<String,Object> binding=map(map(raw).get("binding_status"));if(component.equals("material"))check(Boolean.FALSE.equals(binding.get("retained_binding_present")),"Historical material has no process-owned tray binding");else check(Boolean.FALSE.equals(binding.get("native_job_binding_retained"))&&Boolean.FALSE.equals(binding.get("root_confirmation_retained")),"Historical board has no native job binding or confirmation");}
            for(Map<String,Object> original:List.of(originalBefore,firstOpBefore,nextOpBefore)){Map<String,Object> now=read("get_operation","operation_id",original.get("operation_id"));for(Map.Entry<String,Object> entry:original.entrySet())same(entry.getValue(),now.get(entry.getKey()),"Fresh historical operation preserves exact forced "+entry.getKey());}
            Map<String,Object> materials=read("get_material_loads"),boards=read("get_board_loads");for(Object row:(List<?>)materials.get("loads"))check(Boolean.FALSE.equals(map(row).get("native_authority")),"Fresh material ledger restores no native authority");check(Boolean.TRUE.equals(boards.get("restart_presence_confirmation_required")),"Fresh board ledger requires explicit restart presence confirmation");
            check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy()&&driver.getControlledVacuumSource()==null,"Historical read leaves native machine disabled, idle and without source");
            bridge.close();bridge=null;check(Arrays.equals(journal,Files.readAllBytes(state.resolve("journal/operations.jsonl"))),"Fresh JVM construction, historical reads and close append no journal bytes");same(documents,documentInventory(),"Fresh historical reader performs no candidate document save or alteration");
            write("history-task.json",next);write("history-original-task.json",first);write("history-status.json",status);
            proof.putAll(Bridge.map("passed",true,"original_pid",live.get("pid"),"journal_unchanged",true,"document_inventory_unchanged",true,"exact_continuation_receipt_preserved",true,"original_unknown_preserved",true,"original_publication_untouched_preserved",true,"exact_document_completion_preserved",true,"live_authority_restored",false));
        }catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally {
            try{if(bridge!=null)bridge.close();proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}
            try{if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("assertions",checks.size(),"checks",checks,"public_calls",calls,"pid",ProcessHandle.current().pid(),"scenario",scenario,"fresh_jvm_historical_read_qualified",exit==0,"native_job_placements",0,"restart_reattachment_qualified",false,"hardware_qualified",false,"public_package_qualified",false));write("history-proof.json",proof);System.out.println("NATIVE_RECOVERY_PROBE_CONTINUATION_HISTORY_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
