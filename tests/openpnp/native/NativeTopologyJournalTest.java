/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import org.openpnp.model.Configuration;
import static org.openpnp.codex.NativeControllerTestProcesses.*;

/** Genuine native producer, ordered reducer negatives, and fresh-JVM Bridge admission probes. */
public final class NativeTopologyJournalTest {
    static int checks;static String machineIdentity;
    static void check(boolean v,String why){checks++;require(v,why);}
    static List<JsonObject> copy(List<JsonObject> rows){List<JsonObject> out=new ArrayList<>();for(JsonObject r:rows)out.add(JSON.fromJson(r.toString(),JsonObject.class));return out;}
    static int index(List<JsonObject> rows,String type){for(int i=0;i<rows.size();i++)if(type.equals(rows.get(i).get("type").getAsString()))return i;throw new AssertionError(type);}
    static JsonObject payload(List<JsonObject> rows,String type){return rows.get(index(rows,type)).getAsJsonObject("payload");}
    static JsonObject terminal(List<JsonObject> rows){for(int i=rows.size()-1;i>=0;i--)if("operation".equals(rows.get(i).get("type").getAsString()))return rows.get(i).getAsJsonObject("payload");throw new AssertionError();}
    static boolean replay(List<JsonObject> rows)throws Exception{NativeTopologyJournal reducer=new NativeTopologyJournal();for(JsonObject r:rows)reducer.recover(r.get("type").getAsString(),NativeJournalJson.parseObject(r.get("payload").toString()),r.get("bridge_instance_id").getAsString());if(machineIdentity!=null)reducer.verifyMachineIdentity(machineIdentity);return reducer.recoveryRequired();}
    static void reject(List<JsonObject> original,String name,Consumer<List<JsonObject>> mutate,List<String> cases)throws Exception{List<JsonObject> rows=copy(original);mutate.accept(rows);try{replay(rows);throw new AssertionError("Contradiction accepted: "+name);}catch(java.io.IOException expected){check(expected.getMessage().startsWith("Topology journal:"),name);}cases.add(name);}
    static void writeJournal(Path file,List<JsonObject> rows)throws Exception{StringBuilder text=new StringBuilder();int sequence=0;for(JsonObject r:rows){r.addProperty("sequence",++sequence);text.append(r).append('\n');}Files.writeString(file,text,StandardOpenOption.CREATE_NEW);}
    static void copyConfig(Path from,Path to)throws Exception{try(var paths=Files.walk(from)){for(Path p:(Iterable<Path>)paths::iterator){Path target=to.resolve(from.relativize(p));if(Files.isDirectory(p))Files.createDirectories(target);else Files.copy(p,target);}}}
    static void producer(Path root,String samples)throws Exception{
        Path config=root.resolve("config");Configuration.initialize(config.toFile());Configuration.get().load();SimulatorMain.accelerateFixture(Configuration.get());SimulatorMain.settleFreshFixture(config);Configuration c=Configuration.get();Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());Bridge b=new Bridge(c,token,root.resolve("journal"),Path.of(samples),0,true);
        try{
            Map<String,Object> status=(Map<String,Object>)b.call("openpnp_get_status",object()),lease=(Map<String,Object>)b.call("openpnp_request_control_session",object("request_id","topology-reducer-grant","ttl_seconds",300));
            Map<String,Object> plan=(Map<String,Object>)b.call("openpnp_plan_configuration",object("session_id",lease.get("session_id"),"expected_config_revision",status.get("config_revision"),"changes",NativeNozzleAssemblyTest.rows(NativeNozzleAssemblyTest.proposal(c))));
            Map<String,Object> op=(Map<String,Object>)b.call("openpnp_apply_configuration",object("session_id",lease.get("session_id"),"request_id","topology-reducer-create","plan_id",plan.get("plan_id")));long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(60);
            while(Set.of("accepted","running").contains(op.get("state"))){if(System.nanoTime()>deadline)throw new AssertionError("native producer timeout");Thread.sleep(5);op=(Map<String,Object>)b.call("openpnp_get_operation",object("operation_id",op.get("operation_id")));}
            check("succeeded".equals(op.get("state")),"Actual native assembly success: "+op);save(root.resolve("operation.json"),JSON.toJsonTree(op).getAsJsonObject());
        }finally{b.close();c.getMachine().close();}
    }
    static void probe(Path root,String samples,boolean expectedReject)throws Exception{
        Configuration.initialize(root.resolve("config").toFile());Configuration c=Configuration.get();c.load();Path token=root.resolve("probe-token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());Bridge b=null;boolean rejected=false;
        try{
            try{b=new Bridge(c,token,root.resolve("journal"),Path.of(samples),0,true);}catch(java.io.IOException malformed){if(!expectedReject)throw malformed;rejected=true;check(malformed.getMessage().toLowerCase(Locale.ROOT).contains("journal"),"strict history rejection");}
            if(!expectedReject){Map<String,Object> status=(Map<String,Object>)b.call("openpnp_get_status",object());check(!Boolean.TRUE.equals(status.get("configuration_fault")),"normal completed topology restart stays usable");check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"no enable/home authority restored");Map<?,?> lease=(Map<?,?>)b.call("openpnp_request_control_session",object("request_id","fresh-recovery-probe","ttl_seconds",30));check(lease.get("session_id")!=null,"fresh lease permitted for consistent completed history");}
            check(rejected==expectedReject,"Bridge acceptance matches expected history disposition");
            save(root.resolve("probe.json"),object("rejected",rejected,"checks",checks,"enabled",c.getMachine().isEnabled(),"homed",c.getMachine().isHomed(),"native_action_performed",false));
        }finally{if(b!=null)b.close();c.getMachine().close();}
    }
    public static void main(String[] args)throws Exception{
        if(args.length>1){try{if(args[0].equals("produce"))producer(Path.of(args[1]),args[2]);else probe(Path.of(args[1]),args[2],Boolean.parseBoolean(args[3]));System.exit(0);}catch(Throwable failure){failure.printStackTrace();System.exit(1);}return;}
        Path root=Files.createTempDirectory("native-topology-journal61-"),source=root.resolve("source");run(NativeTopologyJournalTest.class,source,0,"produce",source.toString(),args[0]);Path raw=source.resolve("journal/operations.jsonl");String before=sha(raw);machineIdentity=Files.readString(source.resolve("journal/machine-id")).trim();List<JsonObject> rows=new ArrayList<>();for(JsonElement e:events(raw))rows.add(e.getAsJsonObject());check(!replay(rows),"actual successful native history accepted by reducer");List<String> cases=new ArrayList<>();
        for(String type:List.of("topology_recovery_available","native_effect_intent","topology_model_persisted","native_effect_outcome"))reject(rows,"missing-"+type,r->r.remove(index(r,type)),cases);
        for(String type:List.of("topology_recovery_available","native_effect_intent","topology_model_persisted","native_effect_outcome"))reject(rows,"duplicate-"+type,r->r.add(index(r,type)+1,JSON.fromJson(r.get(index(r,type)).toString(),JsonObject.class)),cases);
        reject(rows,"missing-accepted",r->{for(int i=0;i<r.size();i++)if("operation".equals(r.get(i).get("type").getAsString())){r.remove(i);break;}},cases);
        reject(rows,"missing-running",r->r.removeIf(e->"operation".equals(e.get("type").getAsString())&&"running".equals(e.getAsJsonObject("payload").get("state").getAsString())),cases);
        reject(rows,"outcome-before-intent",r->Collections.swap(r,index(r,"native_effect_intent"),index(r,"native_effect_outcome")),cases);
        reject(rows,"success-before-outcome",r->{int t=r.size()-1;while(!"operation".equals(r.get(t).get("type").getAsString()))t--;Collections.swap(r,t,index(r,"native_effect_outcome"));},cases);
        reject(rows,"persisted-before-intent",r->Collections.swap(r,index(r,"topology_model_persisted"),index(r,"native_effect_intent")),cases);
        reject(rows,"conflicting-native-outcome",r->payload(r,"native_effect_outcome").addProperty("native_call_returned",false),cases);
        reject(rows,"foreign-effect-operation",r->payload(r,"native_effect_outcome").addProperty("operation_id",UUID.randomUUID().toString()),cases);
        reject(rows,"foreign-effect-kind",r->payload(r,"native_effect_outcome").addProperty("kind","home"),cases);
        reject(rows,"foreign-effect-instance",r->r.get(index(r,"native_effect_outcome")).addProperty("bridge_instance_id",UUID.randomUUID().toString()),cases);
        reject(rows,"foreign-bound-machine",r->payload(r,"topology_recovery_available").addProperty("machine_id",UUID.randomUUID().toString()),cases);
        reject(rows,"foreign-profile",r->payload(r,"topology_recovery_available").addProperty("profile","hardware"),cases);
        reject(rows,"foreign-binding-request",r->payload(r,"topology_recovery_available").addProperty("request_id","another-request"),cases);
        reject(rows,"foreign-persisted-submission",r->payload(r,"topology_model_persisted").addProperty("submission_id",UUID.randomUUID().toString()),cases);
        reject(rows,"missing-wrapper",r->terminal(r).remove("native_completion"),cases);
        reject(rows,"foreign-wrapper",r->terminal(r).getAsJsonObject("native_completion").addProperty("submission_id",UUID.randomUUID().toString()),cases);
        reject(rows,"failed-wrapper",r->terminal(r).getAsJsonObject("native_completion").addProperty("native_wrapper_succeeded",false),cases);
        for(String key:List.of("operation_id","request_id","request_digest","method","bridge_instance_id","config_revision","accepted_at"))reject(rows,"terminal-identity-"+key,r->terminal(r).addProperty(key,key.equals("operation_id")?UUID.randomUUID().toString():"changed"),cases);
        reject(rows,"unsaved-result",r->terminal(r).getAsJsonObject("result").addProperty("persisted",false),cases);
        reject(rows,"foreign-generated-result",r->terminal(r).getAsJsonObject("result").getAsJsonObject("created_assembly").addProperty("nozzle_id","N1"),cases);
        reject(rows,"near-integer-preimage-size",r->{JsonObject artifact=terminal(r).getAsJsonObject("result").getAsJsonObject("recovery_preimage");artifact.add("size",new JsonPrimitive(new java.math.BigDecimal(artifact.get("size").getAsString()).add(new java.math.BigDecimal("0.000000000000000000000000000001"))));},cases);
        reject(rows,"foreign-preimage-result",r->terminal(r).getAsJsonObject("result").getAsJsonObject("recovery_preimage").addProperty("artifact_id",UUID.randomUUID().toString()),cases);
        reject(rows,"duplicate-terminal",r->{JsonObject last=null;for(JsonObject e:r)if("operation".equals(e.get("type").getAsString()))last=e;r.add(JSON.fromJson(last.toString(),JsonObject.class));},cases);
        List<JsonObject> partial=copy(rows);int end=partial.size()-1;while(!"operation".equals(partial.get(end).get("type").getAsString()))end--;partial.remove(end);check(replay(partial),"completed body without wrapper/terminal stays fenced");
        List<JsonObject> unknown=copy(partial);JsonObject disposition=JSON.fromJson(rows.get(end).toString(),JsonObject.class);disposition.getAsJsonObject("payload").addProperty("state","outcome_unknown");disposition.getAsJsonObject("payload").add("result",object("recovery","prior-instance-interrupted","repeat_action_performed",false));unknown.add(disposition);check(replay(unknown),"unknown operation remains fenced even after returned body");
        List<JsonObject> flipped=copy(unknown);terminal(flipped).addProperty("state","succeeded");try{replay(flipped);throw new AssertionError("unknown-only terminal flip accepted");}catch(java.io.IOException expected){checks++;cases.add("unknown-terminal-success-flip");}
        NativeTopologyJournal transaction=new NativeTopologyJournal();int binding=index(rows,"topology_recovery_available");for(int i=0;i<binding;i++){JsonObject r=rows.get(i);transaction.recover(r.get("type").getAsString(),NativeJournalJson.parseObject(r.get("payload").toString()),r.get("bridge_instance_id").getAsString());}JsonObject r=rows.get(binding);Runnable commit=transaction.prepare(r.get("type").getAsString(),NativeJournalJson.parseObject(r.get("payload").toString()),r.get("bridge_instance_id").getAsString());check(!transaction.recoveryRequired(),"unforced prepared event does not change reducer");commit.run();check(transaction.recoveryRequired(),"forced preimage publication activates conservative fence");
        List<Object> probes=new ArrayList<>();for(String name:List.of("normal","missing-outcome","foreign-wrapper","success-before-outcome","foreign-machine")){Path target=root.resolve(name);Files.createDirectories(target.resolve("journal"));Files.writeString(target.resolve("journal/machine-id"),name.equals("foreign-machine")?UUID.randomUUID().toString():machineIdentity,StandardOpenOption.CREATE_NEW);copyConfig(source.resolve("config"),target.resolve("config"));List<JsonObject> selected=copy(rows);if(name.equals("missing-outcome"))selected.remove(index(selected,"native_effect_outcome"));if(name.equals("foreign-wrapper"))terminal(selected).getAsJsonObject("native_completion").addProperty("submission_id",UUID.randomUUID().toString());if(name.equals("success-before-outcome")){int t=selected.size()-1;while(!"operation".equals(selected.get(t).get("type").getAsString()))t--;Collections.swap(selected,t,index(selected,"native_effect_outcome"));}writeJournal(target.resolve("journal/operations.jsonl"),selected);probes.add(run(NativeTopologyJournalTest.class,target,0,"probe",target.toString(),args[0],String.valueOf(!name.equals("normal"))));}
        check(sha(raw).equals(before),"original native producer journal unchanged");JsonObject report=object("passed",true,"checks",checks,"negative_cases",cases,"native_restart_probes",probes,"source_journal",raw.toString(),"source_sha256",before,"source_unchanged",true,"physical_qualification",false);save(root.resolve("report.json"),report);System.out.println("OPENPNP_TOPOLOGY_JOURNAL_RESULT "+report);
    }
}
