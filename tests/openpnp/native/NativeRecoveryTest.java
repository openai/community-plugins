/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;

/** Exercises durability and ownership failures using a real OpenPnP simulator instance. */
public final class NativeRecoveryTest {
    static final Gson GSON=new Gson();static Bridge bridge;static String session;static final List<String> passed=new ArrayList<>();
    public static void main(String[]args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-recovery-");Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());
        Files.createDirectories(root.resolve("config"));Configuration.initialize(root.resolve("config").toFile());Configuration.get().load();SimulatorMain.accelerateFixture(Configuration.get());
        Path journal=root.resolve("journal");bridge=new Bridge(Configuration.get(),token,journal,Paths.get(args[0]),0,true);int result=0;
        try {
            String identity=(String)call("openpnp_get_capabilities").get("machine_id");
            session=(String)call("openpnp_request_control_session","request_id","lease-1","ttl_seconds",1).get("session_id");
            check(session.equals(call("openpnp_request_control_session","request_id","lease-1","ttl_seconds",1).get("session_id")),"lost grant response deduplicates to the same session");
            check(Boolean.TRUE.equals(call("openpnp_get_request_status","request_id","lease-1").get("found")),"grant receipt is discoverable by request ID");
            Thread.sleep(1200);
            check(Boolean.FALSE.equals(call("openpnp_request_control_session","request_id","lease-1","ttl_seconds",1).get("active")),"expired grant replay never creates a new lease");
            expect("SESSION_REQUIRED","openpnp_home_machine",mutation());passed.add("expired lease rejects motion");
            session=(String)call("openpnp_request_control_session","request_id","lease-2","ttl_seconds",300).get("session_id");
            String enableId=(String)invoke("openpnp_set_machine_enabled",mutation("enabled",true)).get("operation_id");await(enableId,"succeeded",10000);
            String homeId=(String)invoke("openpnp_home_machine",mutation()).get("operation_id");await(homeId,"succeeded",10000);
            Map<String,Object> plan=invoke("openpnp_plan_motion",mutation("units","mm","x",5,"y",5,"z",0));
            call("openpnp_release_control_session","session_id",session);
            session=(String)call("openpnp_request_control_session","request_id","lease-3","ttl_seconds",300).get("session_id");
            Map<String,Object> stale=await((String)invoke("openpnp_execute_motion",mutation("plan_id",plan.get("plan_id"))).get("operation_id"),"failed",10000);
            check("PLAN_STALE".equals(((Map<?,?>)stale.get("result")).get("code")),"old owner plan rejected before native move");
            long sequence=((Number)call("openpnp_get_status").get("through_sequence")).longValue();bridge.close();
            Map<String,Object> pending=Bridge.map("operation_id","interrupted-placement","request_id","lost-response","request_digest","fixture","method","openpnp_start_job","state","running","bridge_instance_id","previous-instance");
            Files.writeString(journal.resolve("operations.jsonl"),GSON.toJson(Bridge.map("sequence",sequence+1,"bridge_instance_id","previous-instance","type","operation","payload",pending))+"\n",StandardOpenOption.APPEND);
            byte[] journalPrefix=Files.readAllBytes(journal.resolve("operations.jsonl"));
            bridge=new Bridge(Configuration.get(),token,journal,Paths.get(args[0]),0,true);
            byte[] recoveredJournal=Files.readAllBytes(journal.resolve("operations.jsonl"));
            check(recoveredJournal.length>journalPrefix.length&&Arrays.equals(journalPrefix,Arrays.copyOf(recoveredJournal,journalPrefix.length)),"recovery appends without overwriting journal prefix");
            List<Map<String,Object>> recoveryEvents=(List<Map<String,Object>>)call("openpnp_get_events","after_sequence",sequence,"limit",20).get("events");
            check("running".equals(((Map<?,?>)recoveryEvents.get(0).get("payload")).get("state"))&&"outcome_unknown".equals(((Map<?,?>)recoveryEvents.get(1).get("payload")).get("state")),"original event payload remains immutable beside new recovery transition");
            long recoveredSequence=((Number)call("openpnp_get_status").get("through_sequence")).longValue();bridge.close();
            bridge=new Bridge(Configuration.get(),token,journal,Paths.get(args[0]),0,true);
            check(((Number)call("openpnp_get_status").get("through_sequence")).longValue()>=recoveredSequence,"second reopen parses recovered journal successfully");
            check(identity.equals(call("openpnp_get_capabilities").get("machine_id")),"machine identity persists across bridge restart");
            Map<String,Object> receipt=call("openpnp_get_request_status","request_id","lost-response");
            check("outcome_unknown".equals(((Map<?,?>)receipt.get("operation")).get("state")),"interrupted running journal recovers as outcome unknown");
            session=(String)call("openpnp_request_control_session","request_id","lease-4","ttl_seconds",300).get("session_id");
            expect("RECOVERY_REQUIRED","openpnp_home_machine",mutation());passed.add("unknown prior outcome fences new work");
            Map<String,Object> reconciled=invoke("openpnp_reconcile_operation",mutation("operation_id","interrupted-placement","disposition","abandon-after-simulator-reset"));
            check("cancelled".equals(reconciled.get("state")),"simulator reset reconciliation abandons without retry");
            check("unknown".equals(((Map<?,?>)reconciled.get("result")).get("previous_physical_outcome")),"reconciliation preserves unknown physical outcome");
            await((String)invoke("openpnp_home_machine",mutation()).get("operation_id"),"succeeded",10000);passed.add("new work accepted after explicit simulator reconciliation");
            System.out.println("OPENPNP_NATIVE_RECOVERY_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM)));
        }catch(Throwable t){t.printStackTrace();result=1;}finally{bridge.close();Configuration.get().getMachine().close();}
        System.exit(result);
    }
    static Map<String,Object> call(String method,Object...pairs)throws Exception{return invoke(method,GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject());}
    static Map<String,Object> invoke(String method,JsonObject p)throws Exception{return(Map<String,Object>)bridge.call(method,p);}
    static JsonObject mutation(Object...pairs){Map<String,Object> p=Bridge.map("request_id",UUID.randomUUID().toString(),"session_id",session);p.putAll(Bridge.map(pairs));return GSON.toJsonTree(p).getAsJsonObject();}
    static Map<String,Object> await(String id,String target,long timeout)throws Exception{long end=System.nanoTime()+timeout*1000000;Map<String,Object>op;do{op=call("openpnp_get_operation","operation_id",id);if(target.equals(op.get("state"))&&!Configuration.get().getMachine().isBusy())return op;if(!target.equals(op.get("state"))&&Arrays.asList("failed","aborted","outcome_unknown").contains(op.get("state")))throw new AssertionError(op);Thread.sleep(10);}while(System.nanoTime()<end);throw new AssertionError("Timeout "+op);}
    static void expect(String code,String method,JsonObject p)throws Exception{try{invoke(method,p);}catch(Bridge.Fault e){if(code.equals(e.code))return;throw e;}throw new AssertionError("Expected "+code);}
    static void check(boolean valid,String name){if(!valid)throw new AssertionError(name);passed.add(name);System.out.println("PASS "+name);}
}
