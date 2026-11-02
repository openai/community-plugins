/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.Configuration;

/** Distinct bridge JVMs recover the original native artifact UUID from the persistent journal. */
public final class NativeBridgeDocumentRestartTest {
    static final Gson GSON=new Gson();static Bridge bridge;static String session;
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception {
        if(args.length==3){child(args[0],Paths.get(args[1]),Paths.get(args[2]));return;}
        if(args.length!=1)throw new IllegalArgumentException("Expected pinned native sample root");
        Path root=Files.createTempDirectory("openpnp-bridge-doc-restart-");String sampleRoot=Paths.get(args[0]).toAbsolutePath().toString();
        for(String phase:Arrays.asList("save","reload")){
            Path log=root.resolve(phase+".log");Path childHome=Files.createDirectory(root.resolve("child-"+phase));
            Process child=new ProcessBuilder(Paths.get(System.getProperty("java.home"),"bin","java").toString(),"-Xmx2g","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+childHome,"-Djava.io.tmpdir="+childHome,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativeBridgeDocumentRestartTest.class.getName(),phase,root.toString(),sampleRoot).redirectErrorStream(true).redirectOutput(log.toFile()).start();
            if(!child.waitFor(90,TimeUnit.SECONDS)){child.destroyForcibly();throw new AssertionError("Native bridge restart phase timed out: "+phase);}
            if(child.exitValue()!=0)throw new AssertionError("Native bridge restart phase failed: "+phase+"\n"+Files.readString(log));
        }
        System.out.println("OPENPNP_NATIVE_BRIDGE_DOCUMENT_RESTART_RESULT {\"passed\":[\"original native artifact UUID resolves after JVM restart\",\"32 recorded native placements survive restart\",\"reloaded job start requires revalidation and has no feed effect\"],\"native_jvm_phases\":2,\"executed_placements\":32,\"hardware_qualified\":false}");
    }
    static void child(String phase,Path root,Path samples)throws Exception {
        NativeJobDocumentsRestartTest.assertChildIsolation(root.resolve("child-"+phase));
        Path configRoot=root.resolve("persisted-config");if(phase.equals("save"))Files.createDirectory(configRoot);else check(Files.isDirectory(configRoot),"reload requires the same persisted simulator configuration");Configuration.initialize(configRoot.toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);
        Path token=root.resolve("token");if(phase.equals("save"))Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
        int exit=0;
        try {
            bridge=new Bridge(config,token,root.resolve("journal"),samples,0,true);
            session=(String)call("openpnp_request_control_session",Bridge.map("request_id",UUID.randomUUID().toString(),"ttl_seconds",300)).get("session_id");
            if(phase.equals("save")){
                run("openpnp_set_machine_enabled",Bridge.map("enabled",true),"succeeded");run("openpnp_home_machine",Collections.emptyMap(),"succeeded");
                Map<String,Object> job=result(run("openpnp_prepare_job",Bridge.map("sample","pnp-test"),"succeeded"));
                run("openpnp_validate_job",Collections.emptyMap(),"succeeded");Map<String,Object> completed=result(run("openpnp_start_job",Bridge.map("job_id",job.get("job_id")),"succeeded"));
                check(((Number)completed.get("placed")).intValue()==32,"native sample completes 32 before save");
                // Job processing can migrate native package vision settings. Persist the actual
                // authoritative configuration; fresh defaults are different dependencies.
                config.save();
                Map<String,Object> saved=result(run("openpnp_save_job",Collections.emptyMap(),"succeeded"));Map<String,Object> artifact=(Map<String,Object>)saved.get("artifact");
                Files.writeString(root.resolve("artifact-id"),(String)artifact.get("artifact_id"),StandardOpenOption.CREATE_NEW);
                Files.writeString(root.resolve("first-instance"),(String)call("openpnp_get_status",Collections.emptyMap()).get("bridge_instance_id"),StandardOpenOption.CREATE_NEW);
            }else{
                Map<String,Object> before=call("openpnp_get_status",Collections.emptyMap());check(!Files.readString(root.resolve("first-instance")).equals(before.get("bridge_instance_id")),"bridge process instance changes on restart");
                long feeds=feedCount();Map<String,Object> restored=result(run("openpnp_load_job",Bridge.map("artifact_id",Files.readString(root.resolve("artifact-id"))),"succeeded"));
                Map<String,Object> job=(Map<String,Object>)restored.get("job");check(((Number)job.get("placed")).intValue()==32,"recorded native placed history survives bridge restart");
                check(Boolean.TRUE.equals(restored.get("requires_validation"))&&"invalidated".equals(restored.get("registration")),"restart document reload requires validation/alignment");
                check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"reload never enables or homes a fresh simulator");
                Map<String,Object> rejected=result(run("openpnp_start_job",Bridge.map("job_id",job.get("job_id")),"failed"));
                check("JOB_NOT_VALIDATED".equals(rejected.get("code")),"reloaded history cannot start without validation");check(feeds==feedCount(),"rejected start has no native feed effect");
                check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"rejected reload start leaves machine disabled and unhomed");
            }
        }catch(Throwable error){error.printStackTrace();exit=1;}finally{if(bridge!=null)bridge.close();config.getMachine().close();}System.exit(exit);
    }
    static Map<String,Object> call(String name,Map<String,Object> parameters)throws Exception{return(Map<String,Object>)bridge.call(name,GSON.toJsonTree(new LinkedHashMap<>(parameters)).getAsJsonObject());}
    static Map<String,Object> run(String name,Map<String,Object> fields,String target)throws Exception {
        Map<String,Object> parameters=Bridge.map("request_id",UUID.randomUUID().toString(),"session_id",session);parameters.putAll(fields);Map<String,Object> operation=call(name,parameters);String id=(String)operation.get("operation_id");long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(60);
        while(System.nanoTime()<deadline){operation=call("openpnp_get_operation",Bridge.map("operation_id",id));String state=(String)operation.get("state");if(target.equals(state)&&!Configuration.get().getMachine().isBusy())return operation;if(!target.equals(state)&&Arrays.asList("failed","aborted","outcome_unknown").contains(state))throw new AssertionError(operation);Thread.sleep(10);}
        throw new AssertionError("Timed out waiting for "+name+": "+operation);
    }
    static Map<String,Object> result(Map<String,Object> operation){return(Map<String,Object>)operation.get("result");}
    static long feedCount()throws Exception {
        Map<String,Object> snapshot=call("openpnp_get_configuration",Collections.emptyMap());Map<String,Object> settings=(Map<String,Object>)snapshot.get("settings");long count=0;
        for(Map<String,Object> feeder:(List<Map<String,Object>>)settings.get("feeders"))if(feeder.get("feed_count") instanceof Number)count+=((Number)feeder.get("feed_count")).longValue();return count;
    }
}
