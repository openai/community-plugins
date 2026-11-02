/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Native Bridge admission, revision and document restart contract with two real placements. */
public final class NativeBoardLoadBridgeTest {
    private static final Gson GSON=new Gson();private static final List<String> passed=new ArrayList<>();
    private static Bridge bridge;private static String session;private static Path root,samples,token;
    public static void main(String[] args)throws Exception {
        root=Files.createTempDirectory("openpnp-native-board-load-bridge-");samples=Paths.get(args[0]);token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);int code=0;
        try {
            bridge=new Bridge(config,token,root.resolve("journal"),samples,0,true,"sustained-workload");grant();mutate("openpnp_set_machine_enabled","enabled",true);mutate("openpnp_home_machine");
            Map<String,Object> prepared=mutate("openpnp_prepare_job","canonical_job",NativeBoardLoadsTest.canonical(config.getPart("R0603-1K")));String jobId=(String)prepared.get("job_id");Map<String,Object> loads=call("openpnp_get_board_loads");List<?> roots=(List<?>)loads.get("roots");Map<?,?> first=(Map<?,?>)roots.get(0),second=(Map<?,?>)roots.get(1);String a=(String)first.get("root_instance_id"),b=(String)second.get("root_instance_id"),loadA=(String)first.get("load_id"),loadB=(String)second.get("load_id");String old=(String)loads.get("board_load_revision"),cfg=(String)call("openpnp_get_status").get("config_revision");
            check(((List<?>)loads.get("loads")).size()==2&&Boolean.FALSE.equals(loads.get("physical_load_verified")),"prepare returns two authoritative simulator-origin loads without physical verification");
            check(Boolean.TRUE.equals(mutate("openpnp_validate_job").get("valid")),"actual native preflight binds current simulator load revision");
            mutate("openpnp_register_board_load","job_id",jobId,"expected_board_load_revision",old,"root_instance_id",a,"action","flip","side","bottom","expected_load_id",loadA);
            check(cfg.equals(call("openpnp_get_status").get("config_revision"))&&!old.equals(call("openpnp_get_board_loads").get("board_load_revision")),"board-load revision advances independently of configuration revision");
            Map<String,Object> refused=operation("openpnp_start_job","job_id",jobId);check("failed".equals(refused.get("state"))&&"JOB_NOT_VALIDATED".equals(((Map<?,?>)refused.get("result")).get("code")),"flip invalidates prior job validation before any native placement");
            int ops=((Number)((Map<?,?>)call("openpnp_get_status").get("metrics")).get("operation_count")).intValue();
            try{operation("openpnp_register_board_load","job_id",jobId,"expected_board_load_revision",old,"root_instance_id",a,"action","same-load","side","bottom","expected_load_id",loadA);throw new AssertionError("missing revision refusal");}catch(Bridge.Fault expected){check("BOARD_LOAD_REVISION_CONFLICT".equals(expected.code),"stale expected load revision is rejected at admission");}
            check(((Number)((Map<?,?>)call("openpnp_get_status").get("metrics")).get("operation_count")).intValue()==ops&&feeds(config.getMachine())==0,"stale admission creates no operation and consumes no material");
            mutate("openpnp_validate_job");Map<String,Object> finished=operation("openpnp_start_job","job_id",jobId);check("succeeded".equals(finished.get("state"))&&feeds(config.getMachine())==2,"actual Bridge processor places exactly one bottom and one top part");
            Map<String,Object> events=call("openpnp_get_events","after_sequence",0,"limit",500);int bound=0;for(Object raw:(List<?>)events.get("events")){Map<?,?> event=(Map<?,?>)raw;if("native_placement_checkpoint".equals(event.get("type"))){Map<?,?> payload=(Map<?,?>)event.get("payload");if("native-placement-complete-hook".equals(payload.get("state"))){Map<?,?> context=(Map<?,?>)payload.get("context");check(Set.of(loadA,loadB).contains(context.get("board_load_id"))&&context.get("loaded_board_id")!=null,"durable native completion binds actual loaded board identity");bound++;}}}check(bound==2,"both native completed placements have scoped durable load checkpoints");
            Map<String,Object> saved=mutate("openpnp_save_job");String artifact=(String)((Map<?,?>)saved.get("artifact")).get("artifact_id");bridge.close();bridge=new Bridge(config,token,root.resolve("journal"),samples,0,true,"sustained-workload");grant();
            Map<String,Object> loaded=mutate("openpnp_load_job","artifact_id",artifact);jobId=(String)((Map<?,?>)loaded.get("job")).get("job_id");Map<String,Object> invalid=operation("openpnp_validate_job");check("failed".equals(invalid.get("state"))&&"BOARD_LOAD_REQUIRED".equals(((Map<?,?>)invalid.get("result")).get("code")),"document reload and restart cannot infer current board presence");
            Map<String,Object> unvalidated=operation("openpnp_start_job","job_id",jobId);check("JOB_NOT_VALIDATED".equals(((Map<?,?>)unvalidated.get("result")).get("code")),"unvalidated reload retains JOB_NOT_VALIDATED admission priority");
            mutate("openpnp_register_board_load","job_id",jobId,"expected_board_load_revision",call("openpnp_get_board_loads").get("board_load_revision"),"root_instance_id",a,"action","same-load","side","bottom","expected_load_id",loadA);
            mutate("openpnp_register_board_load","job_id",jobId,"expected_board_load_revision",call("openpnp_get_board_loads").get("board_load_revision"),"root_instance_id",b,"action","same-load","side","top","expected_load_id",loadB);
            check(Boolean.TRUE.equals(mutate("openpnp_validate_job").get("valid"))&&feeds(config.getMachine())==2,"explicit same-load reattachment reconciles native saved history without replaying placements");
            Map<?,?> current=call("openpnp_get_board_loads");int count=0;for(Object raw:(List<?>)current.get("loads"))count+=((Number)((Map<?,?>)raw).get("placed_history_count")).intValue();check(count==2,"restart replays native checkpoint history from complete journal");
            System.out.println("OPENPNP_NATIVE_BOARD_LOAD_BRIDGE_RESULT "+GSON.toJson(Bridge.map("passed",passed,"assertions",passed.size(),"completed_native_placements",2,"native_feed_effects",feeds(config.getMachine()),"bridge_restarts",1,"physical_load_verified",false,"simulation_only",true)));
        }catch(Throwable e){e.printStackTrace();code=1;}finally{if(bridge!=null)bridge.close();config.getMachine().close();}System.exit(code);
    }
    static void grant()throws Exception{session=(String)call("openpnp_request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");}
    static Map<String,Object> call(String name,Object...fields)throws Exception{return (Map<String,Object>)bridge.call(name,GSON.toJsonTree(Bridge.map(fields)).getAsJsonObject());}
    static Map<String,Object> operation(String name,Object...fields)throws Exception{JsonObject p=GSON.toJsonTree(Bridge.map(fields)).getAsJsonObject();p.addProperty("request_id",UUID.randomUUID().toString());p.addProperty("session_id",session);if(!p.has("expected_config_revision"))p.addProperty("expected_config_revision",(String)call("openpnp_get_status").get("config_revision"));Map<String,Object> op=(Map<String,Object>)bridge.call(name,p);long until=System.nanoTime()+java.util.concurrent.TimeUnit.SECONDS.toNanos(90);while(Set.of("accepted","running").contains(op.get("state"))){if(System.nanoTime()>until)throw new AssertionError("operation timeout "+name);Thread.sleep(5);op=call("openpnp_get_operation","operation_id",op.get("operation_id"));}while(Boolean.TRUE.equals(call("openpnp_get_status").get("native_busy"))){if(System.nanoTime()>until)throw new AssertionError("native executor timeout");Thread.sleep(5);}return op;}
    static Map<String,Object> mutate(String name,Object...fields)throws Exception{Map<String,Object> op=operation(name,fields);if(!"succeeded".equals(op.get("state")))throw new AssertionError(name+": "+GSON.toJson(op));return (Map<String,Object>)op.get("result");}
    static int feeds(Machine m){int n=0;for(Feeder f:m.getFeeders())if(f instanceof ReferenceTrayFeeder)n+=((ReferenceTrayFeeder)f).getFeedCount();return n;}
    static void check(boolean value,String name){if(!value)throw new AssertionError(name);passed.add(name);}
}
