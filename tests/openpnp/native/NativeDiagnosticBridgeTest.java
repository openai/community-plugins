/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.tagged;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.codex.*;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.model.*;

/** Actual Bridge admission, forced journal, original native executor, and exact owned tagged peer. */
public final class NativeDiagnosticBridgeTest {
    static final Gson JSON=new Gson();static int assertions;static Bridge bridge;
    static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    static JsonObject object(Object...pairs){JsonObject p=new JsonObject();for(int i=0;i<pairs.length;i+=2)p.add((String)pairs[i],JSON.toJsonTree(pairs[i+1]));return p;}
    static Object plain(Object value){if(value instanceof Map){Map<String,Object> p=new LinkedHashMap<>();((Map<?,?>)value).forEach((k,v)->p.put(String.valueOf(k),plain(v)));return p;}if(value instanceof Iterable){List<Object> p=new ArrayList<>();for(Object v:(Iterable<?>)value)p.add(plain(v));return p;}return value;}
    static JsonObject call(String method,JsonObject p)throws Exception{return JSON.toJsonTree(plain(bridge.call(method,p))).getAsJsonObject();}
    /** This terminal-only HTTP read must need no live native model accessor. */
    static JsonObject configurationOverHttpWithoutModel(Path tokenFile,JsonObject report)throws Exception{
        java.lang.reflect.Field machineField=Bridge.class.getDeclaredField("machine"),configField=Bridge.class.getDeclaredField("config");machineField.setAccessible(true);configField.setAccessible(true);
        java.util.concurrent.atomic.AtomicInteger modelReads=new java.util.concurrent.atomic.AtomicInteger();
        Object blocked=java.lang.reflect.Proxy.newProxyInstance(org.openpnp.spi.Machine.class.getClassLoader(),new Class<?>[]{org.openpnp.spi.Machine.class},(proxy,method,args)->{modelReads.incrementAndGet();throw new IllegalStateException("UNEXPECTED_HTTP_NATIVE_MODEL_READ:"+method.getName());});
        Object originalMachine,originalConfig;
        synchronized(bridge){originalMachine=machineField.get(bridge);originalConfig=configField.get(bridge);machineField.set(bridge,blocked);configField.set(bridge,null);}
        java.net.HttpURLConnection connection=null;
        try{
            bridge.start();connection=(java.net.HttpURLConnection)new java.net.URL("http://127.0.0.1:"+bridge.getPort()+"/rpc").openConnection(java.net.Proxy.NO_PROXY);
            connection.setConnectTimeout(1000);connection.setReadTimeout(1000);connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setRequestProperty("Authorization","Bearer "+Files.readString(tokenFile).trim());connection.setRequestProperty("Content-Type","application/json");
            byte[] request=object("method","openpnp_get_configuration","params",object()).toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);connection.setFixedLengthStreamingMode(request.length);try(java.io.OutputStream body=connection.getOutputStream()){body.write(request);}
            check(connection.getResponseCode()==200,"Owned HTTP configuration read succeeds with native model access guarded");
            byte[] bytes;try(java.io.InputStream body=connection.getInputStream()){bytes=body.readNBytes(65537);}check(bytes.length<=65536,"Configuration receipt remains bounded");
            JsonObject envelope=JSON.fromJson(new String(bytes,java.nio.charset.StandardCharsets.UTF_8),JsonObject.class);check(envelope.has("result")&&!envelope.has("error"),"HTTP read uses cached DTOs despite guarded native model references");
            check(modelReads.get()==0,"HTTP configuration read invokes no Bridge Machine method");report.add("configuration_http_guard",object("machine_methods_invoked",modelReads.get(),"bridge_configuration_reference_unavailable",true,"native_model_getter_substitution",true));return envelope.getAsJsonObject("result");
        }finally{if(connection!=null)connection.disconnect();synchronized(bridge){machineField.set(bridge,originalMachine);configField.set(bridge,originalConfig);}}
    }
    static void refuses(String method,JsonObject p,String code)throws Exception{
        try{call(method,p);throw new AssertionError("Unexpected success "+method);}catch(Bridge.Fault error){check(error.code.equals(code),"Expected "+code+", got "+error.code);}
    }
    static JsonObject done(String id)throws Exception{
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);JsonObject value=null;
        while(System.nanoTime()<deadline){value=call("openpnp_get_operation",object("operation_id",id));String state=value.get("state").getAsString();if(!state.equals("accepted")&&!state.equals("running"))return value;Thread.sleep(5);}
        throw new AssertionError("Native operation failed to settle: "+value);
    }
    public static void main(String[] args)throws Exception{
        String mode=args[0];Path folder=Path.of(args[1]);JsonObject report=new JsonObject();int exit=0;
        TaggedProtocolTest.Controller peer=null;ReferenceMachine machine=null;CountDownLatch release=new CountDownLatch(1);
        try{
            Files.createDirectories(folder.resolve("configuration"));Configuration.initialize(folder.resolve("configuration").toFile());Configuration config=Configuration.get();config.setSystemUnits(LengthUnit.Millimeters);
            peer=new TaggedProtocolTest.Controller(mode.equals("old-ack")?"old-ack":"normal");NativeControllerDiagnostic diagnostic=new NativeControllerDiagnostic(config,TypedGcodeProfile.ownedEndpoint(peer.listener));machine=(ReferenceMachine)config.getMachine();
            Path token=folder.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());
            bridge=new Bridge(config,token,folder.resolve("journal"),Path.of(args[2]),0,true,"owned-tagged-controller-diagnostic-v1",null,null,diagnostic);
            JsonObject caps=call("openpnp_get_capabilities",object());report.add("capabilities",caps);
            JsonObject initialConfiguration=call("openpnp_get_configuration",object());
            check(caps.getAsJsonArray("tools").size()==11,"Only declared diagnostic and receipt tools available");
            check(peer.accepted==0&&peer.commands.isEmpty(),"Preparation, Bridge construction and reads open no controller connection");
            for(String method:List.of("openpnp_home_machine","openpnp_set_machine_enabled","openpnp_prepare_job","openpnp_plan_configuration","openpnp_control_actuator","openpnp_reconcile_operation"))refuses(method,object(),"UNSUPPORTED_PROFILE_TOOL");
            JsonObject lease=call("openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",mode.equals("expired-dequeue")?1:30));
            String session=lease.get("session_id").getAsString();String controller=caps.getAsJsonObject("controller_diagnostic").get("controller_instance_id").getAsString();
            JsonObject request=object("request_id",UUID.randomUUID().toString(),"session_id",session,"expected_config_revision","cfg-1","controller_instance_id",controller);
            if(mode.equals("arguments")){
                JsonObject extra=JSON.fromJson(request.toString(),JsonObject.class);extra.addProperty("host","127.0.0.1");refuses("openpnp_run_controller_diagnostic",extra,"UNKNOWN_FIELD");
                JsonObject wrong=JSON.fromJson(request.toString(),JsonObject.class);wrong.addProperty("controller_instance_id",UUID.randomUUID().toString());refuses("openpnp_run_controller_diagnostic",wrong,"CONTROLLER_INSTANCE_MISMATCH");
                JsonObject stale=JSON.fromJson(request.toString(),JsonObject.class);stale.addProperty("expected_config_revision","cfg-999");refuses("openpnp_run_controller_diagnostic",stale,"REVISION_CONFLICT");
                JsonObject malformed=JSON.fromJson(request.toString(),JsonObject.class);malformed.addProperty("request_id","not-a-uuid");refuses("openpnp_run_controller_diagnostic",malformed,"INVALID_ARGUMENT");
                check(peer.accepted==0,"Rejected arguments have no protocol effects");
            }
            Future<?> blocker=null;
            if(mode.equals("expired-dequeue")){
                CountDownLatch entered=new CountDownLatch(1);blocker=machine.submit(()->{entered.countDown();release.await(3,TimeUnit.SECONDS);return null;},null,true);check(entered.await(1,TimeUnit.SECONDS),"Owned native blocker active");
            }
            // Expiry is injected after admission using the exact Bridge deadline; no long sleep or native method replacement.
            if(mode.equals("wrapper-error"))machine.addListener(new org.openpnp.spi.MachineListener.Adapter(){boolean thrown;@Override public void machineBusy(org.openpnp.spi.Machine m,boolean busy){if(!busy&&!thrown){thrown=true;throw new AssertionError("CONTROLLED_DIAGNOSTIC_WRAPPER_FINALLY_ERROR");}}});
            if(mode.equals("configuration-save-failure")){Files.createDirectory(folder.resolve("configuration/machine.xml"));Files.writeString(folder.resolve("configuration/machine.xml/blocked"),"owned native save fault");}
            JsonObject accepted;
            if(mode.equals("expired-dequeue")){
                release.countDown();blocker.get(2,TimeUnit.SECONDS);
                synchronized(bridge){accepted=call("openpnp_run_controller_diagnostic",request);java.lang.reflect.Field deadline=Bridge.class.getDeclaredField("sessionDeadline");deadline.setAccessible(true);deadline.setLong(bridge,System.nanoTime()-1);}
            }else accepted=call("openpnp_run_controller_diagnostic",request);
            String operation=accepted.get("operation_id").getAsString();JsonObject settled=done(operation);report.add("operation",settled);
            boolean unknown=mode.equals("old-ack")||mode.equals("wrapper-error")||mode.equals("configuration-save-failure");String expected=unknown?"outcome_unknown":mode.equals("expired-dequeue")?"failed":"succeeded";
            check(expected.equals(settled.get("state").getAsString()),"Exact final disposition "+expected);
            JsonObject status=call("openpnp_get_status",object());report.add("status",status);
            if(mode.equals("normal")||mode.equals("configuration-save-failure")){
                JsonObject configuration=configurationOverHttpWithoutModel(token,report);report.add("configuration",configuration);
                check(configuration.getAsJsonObject("controller_diagnostic").equals(status.getAsJsonObject("controller_diagnostic")),"Configuration returns latest captured diagnostic after success or failure");
                check(configuration.getAsJsonObject("controller_diagnostic").get("generation_spent").getAsBoolean(),"Configuration never reports the consumed generation as fresh");
                check(configuration.getAsJsonObject("controller_diagnostic").equals(status.getAsJsonObject("machine").getAsJsonObject("controller_diagnostic")),"Status machine and configuration use the same latest diagnostic DTO");
                check(configuration.get("snapshot_at").equals(initialConfiguration.get("snapshot_at")),"Historical configuration snapshot timestamp is retained");
                check(configuration.get("configuration_snapshot_scope").getAsString().equals("initial-empty-controller-shell")&&configuration.get("controller_diagnostic_source").getAsString().equals("latest-cached-diagnostic-observation"),"Historical configuration and current cached diagnostic provenance are separate");
                check(!configuration.get("native_model_read_performed").getAsBoolean(),"Read receipt explicitly reports no native model observation");
            }
            check(settled.getAsJsonObject("native_completion").get("native_wrapper_completed").getAsBoolean(),"Terminal operation follows actual native Future completion");
            if(mode.equals("wrapper-error")){check(settled.getAsJsonObject("result").get("code").getAsString().equals("NATIVE_WRAPPER_FAILED"),"Native wrapper Error remains distinct from completed controller body");check(settled.getAsJsonObject("result").getAsJsonObject("known_body_outcome").getAsJsonObject("result").get("completed_recipe").getAsBoolean(),"Known closed controller recipe survives wrapper Error");}
            check(!machine.isEnabled()&&!machine.isHomed(),"Native machine stays disabled and unhomed");
            if(!mode.equals("expired-dequeue")){
                JsonObject replay=call("openpnp_run_controller_diagnostic",request);check(operation.equals(replay.get("operation_id").getAsString()),"Replay returns exact original operation");
                if(!unknown){JsonObject again=JSON.fromJson(request.toString(),JsonObject.class);again.addProperty("request_id",UUID.randomUUID().toString());refuses("openpnp_run_controller_diagnostic",again,"CONTROLLER_GENERATION_SPENT");}
                JsonObject changed=JSON.fromJson(request.toString(),JsonObject.class);changed.addProperty("controller_instance_id",UUID.randomUUID().toString());refuses("openpnp_run_controller_diagnostic",changed,"REQUEST_ID_CONFLICT");
            }
            TaggedProtocolTest.Controller observedPeer=peer;if(!mode.equals("expired-dequeue"))TaggedProtocolTest.await(()->observedPeer.peerEof,1000);
            int expectedCommands=mode.equals("expired-dequeue")||mode.equals("configuration-save-failure")?0:3;
            check(peer.commands.size()==expectedCommands&&peer.accepted==(expectedCommands==0?0:1),"Exact single connection/startup/identification counts, no native wrapper command");
            if(expectedCommands>0)check(peer.peerEof,"Peer observed closure before fixture cleanup");
            report.add("wire",peer.snapshot());
            if(mode.equals("normal")){check(Files.isRegularFile(folder.resolve("configuration/machine.xml")),"Real native model saved before controller connection");String xml=Files.readString(folder.resolve("configuration/machine.xml"));check(xml.contains("OwnedTaggedGcodeDriver")&&xml.contains("OwnedBoundedTcp"),"Saved model retains exact tagged driver/communications classes");}
            if(mode.equals("configuration-save-failure"))check(Files.exists(folder.resolve("configuration/machine.xml/blocked")),"Failed native save preserves owned blocking fixture");
            List<String> lines=Files.readAllLines(folder.resolve("journal/operations.jsonl"));int intents=0,outcomes=0,admissions=0,retired=0;
            for(String line:lines){JsonObject event=JSON.fromJson(line,JsonObject.class);String type=event.get("type").getAsString();if(type.equals("controller_diagnostic_step_intent"))intents++;if(type.equals("controller_diagnostic_step_outcome"))outcomes++;if(type.equals("controller_diagnostic_admission"))admissions++;if(type.equals("controller_diagnostic_retired"))retired++;}
            check(admissions==1&&retired==1,"Single durable controller admission and retirement");
            check(intents==outcomes&&intents==(mode.equals("expired-dequeue")?0:mode.equals("configuration-save-failure")?1:mode.equals("old-ack")?3:4),"Every admitted native step has one durable typed outcome");
            report.addProperty("intent_count",intents);report.addProperty("outcome_count",outcomes);
        }catch(Throwable error){error.printStackTrace();report.addProperty("failure",error.toString());exit=1;}
        finally{release.countDown();if(bridge!=null)try{bridge.close();}catch(Throwable error){report.addProperty("bridge_close_failure",error.toString());exit=1;}if(peer!=null)try{peer.close();}catch(Throwable error){report.addProperty("peer_close_failure",error.toString());exit=1;}}
        report.addProperty("mode",mode);report.addProperty("passed",exit==0);report.addProperty("assertions",assertions);report.addProperty("physical_qualification",false);
        Files.writeString(folder.resolve("observation.json"),report.toString()+"\n",StandardOpenOption.CREATE_NEW);System.out.println("BRIDGE_DIAGNOSTIC_RESULT "+report);System.exit(exit);
    }
}
