/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;

/** Runs the bounded native maintenance adapters on the upstream default simulator. */
public final class NativeCapabilityTest {
    static Bridge bridge;static String session;static final Gson GSON=new Gson();static final List<String> passed=new ArrayList<>();
    public static void main(String[]args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-capability-");Path config=root.resolve("config");Files.createDirectory(config);
        Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());
        Configuration.initialize(config.toFile());Configuration.get().load();SimulatorMain.accelerateFixture(Configuration.get());
        bridge=new Bridge(Configuration.get(),token,root.resolve("journal"),Paths.get(args[0]),0,true);int result=0;
        try {
            session=(String)call("openpnp_request_control_session",GSON.toJsonTree(Bridge.map("request_id","capability-grant")).getAsJsonObject()).get("session_id");
            run("openpnp_set_machine_enabled","enabled",true);run("openpnp_home_machine");
            String actuator=Configuration.get().getMachine().getAllActuators().get(0).getId();
            run("openpnp_control_actuator","actuator_id",actuator,"enabled",true);run("openpnp_control_actuator","actuator_id",actuator,"enabled",false);passed.add("native actuator commands with cached-command provenance");
            run("openpnp_test_feeder","feeder_id",Configuration.get().getMachine().getFeeders().get(0).getId());passed.add("native strip feeder test");
            run("openpnp_list_issues");passed.add("native Issues and Solutions discovery");
            run("openpnp_prepare_job","sample","pnp-test");run("openpnp_locate_fiducials");passed.add("native board fiducial registration");
            String firstArtifact=null;
            for(int i=0;i<129;i++){Map<String,Object> capture=run("openpnp_capture_camera","mode","raw");if(firstArtifact==null)firstArtifact=(String)((Map<?,?>)capture.get("result")).get("artifact_id");}
            Map<?,?> metrics=(Map<?,?>)call("openpnp_get_status",new JsonObject()).get("metrics");
            if(((Number)metrics.get("artifact_metadata_cache_count")).intValue()>128||((Number)metrics.get("artifact_memory_content_bytes")).longValue()!=0)throw new AssertionError(metrics);
            Map<String,Object> oldest=call("openpnp_get_artifact",GSON.toJsonTree(Bridge.map("artifact_id",firstArtifact)).getAsJsonObject());
            if(((String)oldest.get("base64")).length()<100)throw new AssertionError(oldest);passed.add("129 native captures keep bounded metadata and retrieve evicted artifact bytes from disk");
            for(int i=0;i<130;i++)call("openpnp_plan_motion",GSON.toJsonTree(Bridge.map("session_id",session,"units","mm","x",1,"y",1,"z",0)).getAsJsonObject());
            metrics=(Map<?,?>)call("openpnp_get_status",new JsonObject()).get("metrics");if(((Number)metrics.get("plan_count")).intValue()!=128)throw new AssertionError(metrics);passed.add("motion plan retention bounded at 128");
            String nozzle=Configuration.get().getMachine().getDefaultHead().getDefaultNozzle().getId();
            run("openpnp_validate_calibration","nozzle_id",nozzle);passed.add("native calibration status");
            Map<String,Object> calibration=run("openpnp_run_calibration","recipe_id","nozzle-tip-runout","nozzle_id",nozzle,"enable",true);
            if(!Boolean.TRUE.equals(((Map<?,?>)calibration.get("result")).get("calibrated")))throw new AssertionError(calibration);
            passed.add("native nozzle-tip runout calibration and fitted offsets");
            System.out.println("OPENPNP_NATIVE_CAPABILITY_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"physical_qualification",false)));
        }catch(Throwable t){t.printStackTrace();result=1;}finally{bridge.close();Configuration.get().getMachine().close();}System.exit(result);
    }
    static Map<String,Object> call(String method,JsonObject p)throws Exception{return(Map<String,Object>)bridge.call(method,p);}
    static Map<String,Object> run(String method,Object...pairs)throws Exception {
        Map<String,Object> p=Bridge.map("request_id",UUID.randomUUID().toString(),"session_id",session);p.putAll(Bridge.map(pairs));
        Map<String,Object> op=call(method,GSON.toJsonTree(p).getAsJsonObject());String id=(String)op.get("operation_id");long deadline=System.nanoTime()+120_000_000_000L;
        do{op=call("openpnp_get_operation",GSON.toJsonTree(Bridge.map("operation_id",id)).getAsJsonObject());if("succeeded".equals(op.get("state"))){while(Configuration.get().getMachine().isBusy())Thread.sleep(5);return op;}if(Arrays.asList("failed","outcome_unknown","aborted").contains(op.get("state")))throw new AssertionError(method+" "+op);Thread.sleep(10);}while(System.nanoTime()<deadline);throw new AssertionError("Timeout "+op);
    }
}
