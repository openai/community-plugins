/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.beans.PropertyChangeListener;
import java.util.concurrent.atomic.AtomicBoolean;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Real feeder mutates its native count, then a property listener throws: standstill is not an effect receipt. */
public final class NativeEffectBoundaryTest {
    static final Gson JSON=new Gson();static Bridge bridge;static String session;
    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-effect-");Path config=root.resolve("config");Files.createDirectory(config);
        Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());
        Configuration.initialize(config.toFile());Configuration.get().load();SimulatorMain.accelerateFixture(Configuration.get());SimulatorMain.configureSustainedWorkload(Configuration.get());
        bridge=new Bridge(Configuration.get(),token,root.resolve("journal"),Paths.get(args[0]),0,true,"sustained-workload");int result=0;
        try {
            session=(String)call("openpnp_request_control_session",object("request_id","effect-grant","ttl_seconds",300)).get("session_id");
            await(call("openpnp_set_machine_enabled",mutation("enabled",true)),"succeeded");await(call("openpnp_home_machine",mutation()),"succeeded");
            ReferenceTrayFeeder feeder=(ReferenceTrayFeeder)Configuration.get().getMachine().getFeeders().get(0);
            Map<String,Object> before=await(call("openpnp_test_feeder",mutation("feeder_id","unknown-feeder")),"failed");
            check(feeder.getFeedCount()==0&&!Boolean.TRUE.equals(before.get("native_effect_pending")),"pre-effect lookup failure stays failed without consuming material");
            AtomicBoolean once=new AtomicBoolean();PropertyChangeListener fault=e->{if("feedCount".equals(e.getPropertyName())&&once.compareAndSet(false,true))throw new IllegalStateException("injected-after-real-feed-count-change");};
            feeder.addPropertyChangeListener(fault);JsonObject params=mutation("feeder_id",feeder.getId());Map<String,Object> unknown;
            try{unknown=await(call("openpnp_test_feeder",params),"outcome_unknown");}finally{feeder.removePropertyChangeListener(fault);}
            check(feeder.getFeedCount()==1,"one actual native material count changed before exception");
            Map<?,?> body=(Map<?,?>)unknown.get("result");check(Boolean.TRUE.equals(body.get("effect_outcome_unknown")),"native effect is unknown despite exception");
            check(Boolean.TRUE.equals(((Map<?,?>)body.get("completion")).get("standstill_confirmed")),"native standstill succeeded and did not erase ambiguity");
            Map<String,Object> replay=call("openpnp_test_feeder",params);check(unknown.get("operation_id").equals(replay.get("operation_id"))&&"outcome_unknown".equals(replay.get("state"))&&feeder.getFeedCount()==1,"same request observes original uncertain effect without replay");
            try{call("openpnp_test_feeder",mutation("feeder_id",feeder.getId()));throw new AssertionError("new effect admitted while unknown");}catch(Bridge.Fault expected){check("RECOVERY_REQUIRED".equals(expected.code),"unknown fences later effects: "+expected.code);}
            String journal=Files.readString(root.resolve("journal/operations.jsonl"));check(journal.contains("native_effect_intent")&&!journal.contains("\"type\":\"feed_complete\""),"intent survives without fabricated completion");
            System.out.println("OPENPNP_NATIVE_EFFECT_BOUNDARY_RESULT "+JSON.toJson(Bridge.map("passed_groups",5,"actual_native_feed_count",feeder.getFeedCount(),"standstill_confirmed",true,"outcome_unknown",true,"replay_performed",false,"simulation_only",true,"upstream_commit",Bridge.UPSTREAM)));
        }catch(Throwable error){error.printStackTrace();result=1;}finally{bridge.close();Configuration.get().getMachine().close();}System.exit(result);
    }
    static JsonObject object(Object...pairs){return JSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static JsonObject mutation(Object...pairs){JsonObject value=object("request_id",UUID.randomUUID().toString(),"session_id",session);for(Map.Entry<String,JsonElement> e:object(pairs).entrySet())value.add(e.getKey(),e.getValue());return value;}
    @SuppressWarnings("unchecked")static Map<String,Object> call(String method,JsonObject p)throws Exception{return(Map<String,Object>)bridge.call(method,p);}
    static Map<String,Object> await(Map<String,Object> op,String expected)throws Exception {long deadline=System.nanoTime()+30_000_000_000L;while(System.nanoTime()<deadline){op=call("openpnp_get_operation",object("operation_id",op.get("operation_id")));if(Arrays.asList("succeeded","failed","outcome_unknown","aborted").contains(op.get("state"))){check(expected.equals(op.get("state")),"expected "+expected+": "+op);while(Configuration.get().getMachine().isBusy())Thread.sleep(5);return op;}Thread.sleep(10);}throw new AssertionError("operation timeout: "+op);}
    static void check(boolean yes,String message){if(!yes)throw new AssertionError(message);}
}
