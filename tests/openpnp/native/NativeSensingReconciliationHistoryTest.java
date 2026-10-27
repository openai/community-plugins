/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeSensingReconciliationBridgeTest.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.model.Configuration;

/** A real resolved task is read in a later JVM without its source, GUI owner, or lease.
 * The bounded read phase additionally serves the actual Bridge HTTP endpoint to MCP tests.
 * Clean restart/history qualification only; no crash or execution-authority restoration. */
public final class NativeSensingReconciliationHistoryTest {
    static LocalAdapter adapter;
    static String phase;
    static Map<String,Object> persistedOperation(Object id)throws Exception {
        Map<String,Object> found=null;
        for(Map<String,Object> event:events())if("operation".equals(event.get("type"))&&id.equals(map(event.get("payload")).get("operation_id")))found=map(event.get("payload"));
        if(found==null)throw new AssertionError("No exact forced operation record for "+id);
        return found;
    }
    static void create(Path samples,Map<String,Object> proof)throws Exception {
        Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");
        Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,"invalid-read");
        Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
        Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),"invalid-read"));
        write("fixture-attestation.json",claimed);
        Files.createFile(state.resolve("token"),PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID());
        adapter=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
        bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),samples,0,true,"gui-simulator",adapter);
        Map<String,Object> capabilities=read("get_capabilities");
        check(((List<?>)capabilities.get("tools")).containsAll(List.of("openpnp_request_sensing_reconciliation","openpnp_get_sensing_reconciliation")),"Original owned source advertises both recovery request and history read");
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> failed=await(call("measure_sensor",command("nozzle_id",nozzle().getId(),"samples",1)),"outcome_unknown");
        run("set_machine_enabled",command("enabled",false));
        run("request_sensing_reconciliation",command("recovery_kind","restore-sensing-readiness"));
        Presentation shown=adapter.presentations.poll(10,TimeUnit.SECONDS);check(shown!=null,"Real local recovery task was presented before restart");
        shown.callback.submit(adapter::currentAuthority).toCompletableFuture().get(40,TimeUnit.SECONDS);
        String taskId=(String)shown.task.get("task_id");Map<String,Object> resolved=read("get_sensing_reconciliation","task_id",taskId);
        check("resolved_for_current_scope".equals(resolved.get("state"))&&Boolean.TRUE.equals(resolved.get("live_resolution_activated")),"Original native source repair/probe resolved under its real local capability");
        Map<String,Object> recovery=read("get_operation","operation_id",resolved.get("recovery_operation_id"));
        check("succeeded".equals(recovery.get("state"))&&Boolean.TRUE.equals(map(recovery.get("native_completion")).get("native_wrapper_completed")),"Original receipt follows its actual successful native wrapper");
        read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Original simulator is idle and disabled before clean shutdown");
        Map<String,Object> expected=Bridge.map("task_id",taskId,"resolved",resolved,"original_failed_operation",failed,"recovery_operation",recovery,
            "persisted_failed_operation",persistedOperation(failed.get("operation_id")),"persisted_recovery_operation",persistedOperation(recovery.get("operation_id")),
            "original_pid",ProcessHandle.current().pid(),"original_bridge_instance",read("get_status").get("bridge_instance_id"));
        write("expected-history.json",expected);proof.putAll(Bridge.map("task_id",taskId,"resolved_before_restart",true));
    }
    static void history(Path samples,Map<String,Object> proof)throws Exception {
        Map<String,Object> expected=NativeJournalJson.parseObject(Files.readString(state.resolve("expected-history.json")));
        check(((Number)expected.get("original_pid")).longValue()!=ProcessHandle.current().pid(),"Historical read runs in a distinct actual JVM");
        Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();
        NullDriver driver=(NullDriver)config.getMachine().getDrivers().get(0);
        check(driver.getControlledVacuumSource()==null,"Native configuration reload does not reconstruct the old process-owned source");
        bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),samples,0,true,"native-simulator");
        Map<String,Object> capabilities=read("get_capabilities");write("history-capabilities.json",capabilities);
        check(((List<?>)capabilities.get("tools")).contains("openpnp_get_sensing_reconciliation"),"Source-free restarted Bridge advertises historical task reading");
        check(!((List<?>)capabilities.get("tools")).contains("openpnp_request_sensing_reconciliation"),"Source-free restarted Bridge does not advertise local recovery requests");
        check(Boolean.FALSE.equals(map(capabilities.get("sensing_reconciliation")).get("available"))
            &&Boolean.FALSE.equals(map(capabilities.get("vacuum_sensing")).get("available")),"Live recovery and sensing availability both remain false after restart");
        Map<String,Object> beforeStatus=read("get_status"),lease=read("get_control_session");
        check(beforeStatus.get("gui_ownership")==null&&lease.get("session_id")==null,"Historical reader has no GUI ownership adapter or control lease");
        check(!expected.get("original_bridge_instance").equals(beforeStatus.get("bridge_instance_id")),"Restart has a fresh Bridge instance identity");
        String taskId=(String)expected.get("task_id");Map<String,Object> historical=read("get_sensing_reconciliation","task_id",taskId);write("historical-task.json",historical);
        check(Boolean.TRUE.equals(historical.get("historical"))&&Boolean.FALSE.equals(historical.get("live_resolution_activated")),"Replayed resolution is historical and does not activate current readiness");
        check(Boolean.FALSE.equals(historical.get("execution_authority_restored")),"Readback does not restore execution authority");
        check(!Boolean.TRUE.equals(historical.get("task_active")),"Historical task exposes no active local callback capability");
        Map<String,Object> prior=map(expected.get("resolved"));
        for(String key:List.of("task","state","local_action","intervention","verification","receipt","recovery_operation_id"))
            check(Objects.equals(prior.get(key),historical.get(key)),"Historical read preserves exact original "+key);
        check(map(expected.get("persisted_failed_operation")).equals(read("get_operation","operation_id",map(expected.get("original_failed_operation")).get("operation_id"))),"Restart preserves the exact persisted failed sensor operation");
        check(map(expected.get("persisted_recovery_operation")).equals(read("get_operation","operation_id",historical.get("recovery_operation_id"))),"Restart preserves the exact persisted completed recovery operation");
        byte[] before=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        refused(()->call("request_sensing_reconciliation",object("session_id","unowned","request_id",UUID.randomUUID().toString(),
            "expected_config_revision",beforeStatus.get("config_revision"),"recovery_kind","restore-sensing-readiness")),"SESSION_REQUIRED","A direct remote request cannot acquire authority from historical evidence");
        bridge.start();Path connection=state.resolve("history-connection.json");
        Files.createFile(connection,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        Files.writeString(connection,JSON.toJson(Bridge.map("url","http://127.0.0.1:"+bridge.getPort()+"/","tokenFile",state.resolve("token").toString(),"machineId",beforeStatus.get("machine_id"))));
        write("history-ready.json",Bridge.map("connection_file",connection.toString(),"task_id",taskId,"pid",ProcessHandle.current().pid(),"native_port",bridge.getPort(),"stop_file",state.resolve("history-stop").toString()));
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(45);
        while(!Files.exists(state.resolve("history-stop"))&&System.nanoTime()<deadline)Thread.sleep(10);
        check(Files.exists(state.resolve("history-stop")),"Bounded official MCP history probe completed and released the native reader");
        Map<String,Object> afterStatus=read("get_status");write("history-final-status.json",afterStatus);
        check(read("get_control_session").get("session_id")==null&&afterStatus.get("gui_ownership")==null&&driver.getControlledVacuumSource()==null,"Native and MCP history reads create no lease, GUI grant, or source");
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Native and MCP history reads leave the real machine idle and disabled");
        byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(Arrays.equals(before,after),"Direct refusal and native/MCP historical reads append no operation or sensing records");
        proof.putAll(Bridge.map("task_id",taskId,"original_pid",expected.get("original_pid"),"historical_read_without_source_or_authority",true,
            "journal_unchanged",true,"journal_sha256",sha(after),"loopback_port",bridge.getPort()));
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=3||!Set.of("create","history").contains(args[0]))throw new IllegalArgumentException("Expected create/history, pinned sample root, and shared exclusive state path");
        phase=args[0];state=Path.of(args[2]).toAbsolutePath();if("create".equals(phase))Files.createDirectory(state);
        int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try {if("create".equals(phase))create(Path.of(args[1]),proof);else history(Path.of(args[1]),proof);proof.put("passed",true);}
        catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally {
            try{if(bridge!=null)bridge.close();proof.put("bridge_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("bridge_close_failure",failure.toString());exit=1;}
            try{if(adapter!=null){adapter.release();check(!adapter.gate.owns(adapter.token),"Original native ownership gate released");}proof.put("native_gate_released",true);}catch(Throwable failure){failure.printStackTrace();proof.put("gate_release_failure",failure.toString());exit=1;}
            try{if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);
            proof.putAll(Bridge.map("phase",phase,"pid",ProcessHandle.current().pid(),"checks",checks,"assertions",checks.size(),"public_calls",calls,
                "clean_restart_only",true,"mainframe_qualified",false,"crash_recovery_qualified",false,"hardware_qualified",false));
            write(phase+"-proof.json",proof);Files.move(state.resolve("calls.jsonl"),state.resolve(phase+"-calls.jsonl"));
            System.out.println("NATIVE_SENSING_RECONCILIATION_HISTORY_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
