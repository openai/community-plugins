/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.atomic.*;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.model.*;

/** Actual fresh Bridge recovery; never imports an endpoint or dispatches a machine/controller action. */
public final class NativeControllerRecoveryTest {
    static final Gson JSON=new Gson();
    static int assertions;
    static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    static JsonObject args(Object...pairs){JsonObject p=new JsonObject();for(int i=0;i<pairs.length;i+=2)p.add((String)pairs[i],JSON.toJsonTree(pairs[i+1]));return p;}
    static Object plain(Object value){if(value instanceof Map){Map<String,Object> p=new LinkedHashMap<>();((Map<?,?>)value).forEach((k,v)->p.put(String.valueOf(k),plain(v)));return p;}if(value instanceof Iterable){List<Object> p=new ArrayList<>();for(Object v:(Iterable<?>)value)p.add(plain(v));return p;}return value;}
    static JsonObject call(Bridge bridge,String method,JsonObject p)throws Exception{return JSON.toJsonTree(plain(bridge.call(method,p))).getAsJsonObject();}
    static boolean absent(JsonObject p,String key){return !p.has(key)||p.get(key).isJsonNull();}
    static String sha(byte[] bytes)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format("%02x",b&255));return out.toString();}
    static void sameFields(JsonObject expected,JsonObject actual,List<String> fields,String what){for(String key:fields)check(Objects.equals(expected.get(key),actual.get(key)),what+" differs at "+key);}

    /** An owned detector, not an endpoint loaded from a file. Any unexpected connection fails qualification. */
    static final class Peer implements AutoCloseable {
        final ServerSocket listener;
        final AtomicInteger accepted=new AtomicInteger();
        final AtomicLong bytes=new AtomicLong();
        final AtomicReference<String> failure=new AtomicReference<>();
        final Thread thread;
        volatile boolean closing;
        Peer()throws Exception{
            listener=new ServerSocket();listener.bind(new InetSocketAddress(InetAddress.getByAddress(new byte[]{127,0,0,1}),0),1);listener.setSoTimeout(40);
            thread=new Thread(()->{while(!closing){try(Socket socket=listener.accept()){accepted.incrementAndGet();socket.setSoTimeout(40);byte[] input=new byte[512];int n;try{while((n=socket.getInputStream().read(input))!=-1){bytes.addAndGet(n);if(bytes.get()>32768)break;}}catch(SocketTimeoutException expected){}}catch(SocketTimeoutException expected){}catch(Exception e){if(!closing)failure.compareAndSet(null,e.toString());}}},"owned-recovery-peer");
            thread.setDaemon(true);thread.start();
        }
        public void close()throws Exception{closing=true;listener.close();thread.join(500);if(thread.isAlive())throw new AssertionError("Owned detector did not terminate");}
    }

    public static void main(String[] argv)throws Exception{
        Path folder=Path.of(argv[0]),samples=Path.of(argv[1]),expectation=Path.of(argv[2]),output=Path.of(argv[3]);
        JsonObject expected=JSON.fromJson(Files.readString(expectation),JsonObject.class),report=new JsonObject();
        Bridge bridge=null;Peer peer=null;int exit=0;
        byte[] before=Files.readAllBytes(folder.resolve("journal/operations.jsonl"));
        try{
            check(sha(before).equals(expected.get("starting_journal_sha256").getAsString()),"Recovery input is the exact recorded private journal");
            int originalBytes=expected.get("source_journal_bytes").getAsInt();
            check(before.length>=originalBytes&&sha(Arrays.copyOf(before,originalBytes)).equals(expected.get("source_journal_sha256").getAsString()),"Original producer byte prefix preserved before recovery");
            Files.createDirectories(output.resolve("configuration"));Configuration.initialize(output.resolve("configuration").toFile());Configuration config=Configuration.get();config.setSystemUnits(LengthUnit.Millimeters);
            peer=new Peer();NativeControllerDiagnostic diagnostic=new NativeControllerDiagnostic(config,TypedGcodeProfile.ownedEndpoint(peer.listener));
            ReferenceMachine machine=(ReferenceMachine)config.getMachine();
            Path token=output.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
            bridge=new Bridge(config,token,folder.resolve("journal"),samples,0,true,"owned-tagged-controller-diagnostic-v1",null,null,diagnostic);
            byte[] afterConstruction=Files.readAllBytes(folder.resolve("journal/operations.jsonl"));
            JsonObject caps=call(bridge,"openpnp_get_capabilities",args()),status=call(bridge,"openpnp_get_status",args()),session=call(bridge,"openpnp_get_control_session",args());
            JsonObject original=expected.getAsJsonObject("terminal_operation");String operationId=original.get("operation_id").getAsString();
            JsonObject operation=call(bridge,"openpnp_get_operation",args("operation_id",operationId));
            sameFields(original,operation,List.of("operation_id","request_id","request_digest","method","bridge_instance_id","config_revision","controller_instance_id"),"Recovered operation");
            check(operation.get("state").equals(expected.get("expected_state")),"Original known disposition or conservative unknown is retained");
            if(!expected.get("interrupted").getAsBoolean())sameFields(original,operation,List.of("result","native_completion"),"Recovered terminal receipt");
            else{check(operation.getAsJsonObject("result").get("recovery").getAsString().equals("prior-instance-interrupted"),"Interrupted operation has explicit prior-instance recovery disposition");check(!operation.getAsJsonObject("result").get("repeat_action_performed").getAsBoolean(),"Recovery disposition forbids replay");}
            check(!caps.get("bridge_instance_id").equals(original.get("bridge_instance_id")),"Recovery creates a distinct Bridge instance");
            JsonObject current=caps.getAsJsonObject("controller_diagnostic");
            check(current.get("generation_spent").getAsBoolean(),"Fresh diagnostic generation is spent after recovery");
            check(!current.get("controller_instance_id").equals(original.get("controller_instance_id")),"No controller handle identity is restored");
            check(absent(session,"session_id"),"No session authority restored");
            check(session.get("expires_in_ms").getAsLong()==0,"Recovered control session has no lease");
            check(!status.get("native_busy").getAsBoolean()&&absent(status,"native_submission")&&absent(status,"active_operation_id"),"Recovery admits no native submission");
            check(!machine.isEnabled()&&!machine.isHomed(),"Recovery shell remains disabled/unhomed");
            check(machine.getDrivers().isEmpty()&&machine.getHeads().isEmpty()&&machine.getAxes().isEmpty(),"Recovery never binds a driver/head/axis graph");
            JsonObject history=status.getAsJsonObject("controller_history");
            if(!absent(expected,"admission"))check(history.getAsJsonObject("binding").equals(expected.getAsJsonObject("admission")),"Exact original controller binding preserved");
            else check(absent(history,"binding"),"Recovery does not invent absent typed controller admission");
            check(history.getAsJsonArray("steps").equals(expected.getAsJsonArray("outcomes")),"Every native-step receipt remains unchanged");
            check(history.get("retired").equals(expected.get("retired")),"Recorded retirement or absence preserved");
            check(Objects.equals(history.get("pending"),expected.get("pending")),"Exact pending intent or absence preserved");
            check(!history.get("native_authority_restored").getAsBoolean(),"History grants no native authority");
            if(!absent(expected,"admission"))check(history.equals(operation.getAsJsonObject("controller_recovery")),"Original operation exposes identical controller recovery history");
            else check(absent(operation,"controller_recovery"),"Operation does not invent typed controller recovery without admission");
            JsonObject request=call(bridge,"openpnp_get_request_status",args("request_id",original.get("request_id").getAsString()));
            check(request.get("found").getAsBoolean(),"Original request remains found");
            check(request.getAsJsonObject("operation").equals(operation),"Request lookup returns the original recovered operation");
            JsonObject events=call(bridge,"openpnp_get_events",args("after_sequence",0,"limit",500));
            check(!events.get("resync_required").getAsBoolean(),"All bounded source journal events remain available");
            JsonArray returned=events.getAsJsonArray("events"),starting=expected.getAsJsonArray("events");
            int appended=expected.get("expected_recovery_events").getAsInt();
            check(returned.size()==starting.size()+appended,"Recovery adds only its expected disposition event");
            for(int i=0;i<starting.size();i++)check(returned.get(i).equals(starting.get(i)),"Existing event prefix preserved at index "+i);
            if(appended==1){JsonObject extra=returned.get(returned.size()-1).getAsJsonObject();check(extra.get("type").getAsString().equals("operation"),"Only an operation disposition is appended");check(extra.getAsJsonObject("payload").equals(operation),"Appended recovery receipt is the observed original operation");check(extra.get("sequence").getAsLong()==starting.get(starting.size()-1).getAsJsonObject().get("sequence").getAsLong()+1,"Recovery append sequence is contiguous");}
            check(status.get("machine_id").getAsString().equals(expected.get("machine_id").getAsString()),"Original machine journal identity retained");
            check(!status.get("journal_fault").getAsBoolean(),"Valid captured journal recovers without fault");
            check(peer.accepted.get()==0&&peer.bytes.get()==0,"Recovery construction and reads make no controller connection/bytes");
            bridge.close();bridge=null;
            Thread.sleep(100); // Bounded detector opportunity after all Bridge shutdown paths.
            check(peer.accepted.get()==0&&peer.bytes.get()==0,"Read-only shutdown opens no controller connection/bytes");
            check(peer.failure.get()==null,"Owned detector has no unexpected error");
            byte[] afterClose=Files.readAllBytes(folder.resolve("journal/operations.jsonl"));
            check(Arrays.equals(afterConstruction,afterClose),"Read tools and shutdown leave recovered journal bytes unchanged");
            check(afterClose.length>=before.length&&Arrays.equals(before,Arrays.copyOf(afterClose,before.length)),"Recovery preserves its entire input byte prefix");
            check(appended!=0||Arrays.equals(before,afterClose),"Settled history adds no journal event on repeated recovery");
            report.addProperty("bridge_instance_id",caps.get("bridge_instance_id").getAsString());report.addProperty("new_controller_instance_id",current.get("controller_instance_id").getAsString());
            report.addProperty("original_operation_id",operationId);report.addProperty("original_request_id",original.get("request_id").getAsString());report.addProperty("original_controller_instance_id",original.get("controller_instance_id").getAsString());
            report.addProperty("state",operation.get("state").getAsString());report.addProperty("native_step_receipts",history.getAsJsonArray("steps").size());report.addProperty("bridge_artifact_sha256",caps.get("bridge_artifact_sha256").getAsString());
            report.addProperty("recovery_events_appended",appended);report.addProperty("producer_prefix_preserved",true);report.addProperty("read_only_shutdown",true);
        }catch(Throwable failure){failure.printStackTrace();report.addProperty("failure",failure.toString());exit=1;}
        finally{
            if(bridge!=null)try{bridge.close();}catch(Throwable failure){report.addProperty("bridge_close_failure",failure.toString());exit=1;}
            if(peer!=null){report.addProperty("controller_connections",peer.accepted.get());report.addProperty("controller_bytes",peer.bytes.get());try{peer.close();report.addProperty("peer_thread_reaped",true);}catch(Throwable failure){report.addProperty("peer_close_failure",failure.toString());exit=1;}}
        }
        report.addProperty("journal_before_sha256",sha(before));report.addProperty("journal_after_sha256",sha(Files.readAllBytes(folder.resolve("journal/operations.jsonl"))));
        report.addProperty("passed",exit==0);report.addProperty("assertions",assertions);report.addProperty("physical_qualification",false);
        Files.writeString(output.resolve("observation.json"),report+"\n",StandardOpenOption.CREATE_NEW);System.out.println("CONTROLLER_RECOVERY_RESULT "+report);System.exit(exit);
    }
}
