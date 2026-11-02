/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.tagged;
import com.google.gson.*;
import java.nio.*;import java.nio.channels.*;import java.nio.file.*;import java.io.*;import java.util.*;import java.util.concurrent.*;
import org.openpnp.codex.*;import org.openpnp.codex.prototype.TypedGcodeProfile;import org.openpnp.machine.reference.ReferenceMachine;import org.openpnp.model.*;
public final class NativeDiagnosticFaultTest {
    static final Gson JSON=new Gson();static int assertions;
    static void check(boolean b,String message){assertions++;if(!b)throw new AssertionError(message);}
    static JsonObject object(Object...pairs){return NativeDiagnosticBridgeTest.object(pairs);}
    static JsonObject call(String method,JsonObject p)throws Exception{return NativeDiagnosticBridgeTest.call(method,p);}
    static final class FaultChannel extends FileChannel {
        final FileChannel delegate;int forcedThenFailed;final String mode;boolean armed=true,pending,pendingMutation;final Runnable mutation;
        FaultChannel(FileChannel delegate,String mode,Runnable mutation){this.delegate=delegate;this.mode=mode;this.mutation=mutation;}
        public void force(boolean metadata)throws IOException{delegate.force(metadata);if(pendingMutation){pendingMutation=false;mutation.run();}if(pending){pending=false;forcedThenFailed++;throw new IOException("CONTROLLED_AFTER_REAL_CONTROLLER_FORCE_FAILURE");}}
        public int read(ByteBuffer dst)throws IOException{return delegate.read(dst);}public long read(ByteBuffer[] dst,int offset,int length)throws IOException{return delegate.read(dst,offset,length);}
        public int read(ByteBuffer dst,long position)throws IOException{return delegate.read(dst,position);}public int write(ByteBuffer src)throws IOException{
            if(armed&&src.hasRemaining()){
                ByteBuffer view=src.asReadOnlyBuffer();byte[] bytes=new byte[view.remaining()];view.get(bytes);JsonObject event=JSON.fromJson(new String(bytes,java.nio.charset.StandardCharsets.UTF_8),JsonObject.class);String type=event.get("type").getAsString();JsonObject p=event.getAsJsonObject("payload");
                boolean mutationHit=type.equals("controller_diagnostic_step_intent")&&((mode.startsWith("model-")&&p.get("step").getAsString().equals("connect"))||(mode.equals("lease-after-connect")&&p.get("step").getAsString().equals("identify"))||(mode.equals("close-observed-fault")&&p.get("step").getAsString().equals("close")));
                if(mutationHit){armed=false;if(mode.equals("model-pre-force"))mutation.run();else pendingMutation=true;}
                boolean hit=(mode.equals("admission-force")&&type.equals("operation")&&p.has("method")&&p.get("method").getAsString().equals("openpnp_run_controller_diagnostic")&&p.get("state").getAsString().equals("accepted"))
                    ||(mode.equals("binding-force")&&type.equals("controller_diagnostic_admission"))
                    ||((mode.equals("intent-before-write")||mode.equals("intent-force"))&&type.equals("controller_diagnostic_step_intent")&&p.get("step").getAsString().equals("connect"))
                    ||(mode.equals("outcome-force")&&type.equals("controller_diagnostic_step_outcome")&&p.get("step").getAsString().equals("identify"))
                    ||(mode.equals("retirement-force")&&type.equals("controller_diagnostic_retired"));
                if(hit){armed=false;if(mode.equals("intent-before-write"))throw new IOException("CONTROLLED_CONTROLLER_PRE_WRITE_FAILURE");pending=true;}
            }
            return delegate.write(src);
        }
        public long write(ByteBuffer[] src,int offset,int length)throws IOException{return delegate.write(src,offset,length);}public int write(ByteBuffer src,long position)throws IOException{return delegate.write(src,position);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return mode.equals("capacity")&&armed?512L*1024*1024-1:delegate.size();}
        public FileChannel truncate(long size)throws IOException{delegate.truncate(size);return this;}public long transferTo(long p,long count,WritableByteChannel target)throws IOException{return delegate.transferTo(p,count,target);}
        public long transferFrom(ReadableByteChannel src,long p,long count)throws IOException{return delegate.transferFrom(src,p,count);}public MappedByteBuffer map(MapMode mode,long p,long size)throws IOException{return delegate.map(mode,p,size);}
        public FileLock lock(long p,long size,boolean shared)throws IOException{return delegate.lock(p,size,shared);}public FileLock tryLock(long p,long size,boolean shared)throws IOException{return delegate.tryLock(p,size,shared);}
        protected void implCloseChannel()throws IOException{delegate.close();}
    }
    public static void main(String[] args)throws Exception{
        String mode=args[0];Path folder=Path.of(args[1]);Files.createDirectories(folder.resolve("configuration"));int exit=0;JsonObject result=new JsonObject();Bridge bridge=null;TaggedProtocolTest.Controller peer=null;
        try{
            Configuration.initialize(folder.resolve("configuration").toFile());Configuration config=Configuration.get();config.setSystemUnits(LengthUnit.Millimeters);peer=new TaggedProtocolTest.Controller("normal");
            NativeControllerDiagnostic diagnostic=new NativeControllerDiagnostic(config,TypedGcodeProfile.ownedEndpoint(peer.listener));ReferenceMachine machine=(ReferenceMachine)config.getMachine();
            Path token=folder.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());bridge=new Bridge(config,token,folder.resolve("journal"),Path.of(args[2]),0,true,"owned-tagged-controller-diagnostic-v1",null,null,diagnostic);NativeDiagnosticBridgeTest.bridge=bridge;
            JsonObject caps=call("openpnp_get_capabilities",object());String controller=caps.getAsJsonObject("controller_diagnostic").get("controller_instance_id").getAsString();
            JsonObject lease=call("openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",30));String request=UUID.randomUUID().toString();JsonObject input=object("session_id",lease.get("session_id").getAsString(),"request_id",request,"expected_config_revision","cfg-1","controller_instance_id",controller);
            java.lang.reflect.Field field=Bridge.class.getDeclaredField("journal");field.setAccessible(true);Bridge currentBridge=bridge;Runnable mutation=()->{try{
                if(mode.startsWith("model-"))machine.getHeads().get(0).getNozzles().get(0).setName("test-model-drift");
                if(mode.equals("lease-after-connect")){java.lang.reflect.Field deadlineField=Bridge.class.getDeclaredField("sessionDeadline");deadlineField.setAccessible(true);deadlineField.setLong(currentBridge,System.nanoTime()-1);}
                if(mode.equals("close-observed-fault"))((OwnedBoundedTcp)machine.getDrivers().get(0).getClass().getMethod("getCommunications").invoke(machine.getDrivers().get(0))).fail("CONTROLLED_READER_CLOSE_OBSERVATION_FAULT");
            }catch(Exception e){throw new IllegalStateException(e);}};FaultChannel channel=new FaultChannel((FileChannel)field.get(bridge),mode,mutation);field.set(bridge,channel);
            JsonObject accepted=null;try{accepted=call("openpnp_run_controller_diagnostic",input);}catch(Exception failure){result.addProperty("admission_error_type",failure.getClass().getName());}
            boolean observedCase=mode.startsWith("model-")||mode.equals("lease-after-connect")||mode.equals("close-observed-fault");
            if(observedCase){
                check(accepted!=null,"Original diagnostic operation admitted");JsonObject op=NativeDiagnosticBridgeTest.done(accepted.get("operation_id").getAsString());result.add("operation",op);
                check(op.get("state").getAsString().equals(mode.equals("close-observed-fault")?"outcome_unknown":"failed"),"Observed drift/lease/close fault never becomes success");
                check(!call("openpnp_get_status",object()).get("journal_fault").getAsBoolean(),"Semantic fault preserves journal availability");
                if(mode.startsWith("model-"))check(machine.getHeads().get(0).getNozzles().get(0).getName().equals("test-model-drift"),"Preflight refuses and preserves changed native model");
                if(mode.equals("lease-after-connect"))check(!call("openpnp_get_control_session",object()).has("session_id"),"Lease expiration remains visible");
                result.addProperty("injection",mode.equals("close-observed-fault")?"test-only sticky transport-fault flag before actual close; not an actual reader join failure":mode);
            }else if(mode.equals("capacity")){
                check(accepted==null&&channel.armed,"Capacity refusal precedes consuming a generation or writing an operation");
                JsonObject status=call("openpnp_get_status",object());check(!status.getAsJsonObject("controller_diagnostic").get("generation_spent").getAsBoolean(),"Capacity refusal preserves fresh generation");
                check(!call("openpnp_get_request_status",object("request_id",request)).get("found").getAsBoolean(),"Capacity refusal has no accepted request");
                channel.armed=false;accepted=call("openpnp_run_controller_diagnostic",input);JsonObject op=NativeDiagnosticBridgeTest.done(accepted.get("operation_id").getAsString());check(op.get("state").getAsString().equals("succeeded"),"Same request succeeds after test capacity seam removed");result.add("operation",op);
            }else{
                long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);JsonObject status;
                do{status=call("openpnp_get_status",object());if(status.get("journal_fault").getAsBoolean()&&!status.get("native_busy").getAsBoolean()&&(!status.has("native_submission")||status.get("native_submission").isJsonNull()||status.getAsJsonObject("native_submission").get("phase").getAsString().equals("publication-fault")))break;Thread.sleep(5);}while(System.nanoTime()<deadline);
                check(status.get("journal_fault").getAsBoolean(),"Injected publication fault is sticky");check(!machine.isBusy(),"Native wrapper completed before post-fault assertions");
                JsonObject receipt=call("openpnp_get_request_status",object("request_id",request));check(receipt.get("found").getAsBoolean(),"Original request remains discoverable despite admission/terminal publication uncertainty");
                if(mode.equals("admission-force")){check(receipt.has("uncommitted_admission"),"Uncommitted initial admission is explicitly distinct from accepted state");check(!receipt.getAsJsonObject("uncommitted_admission").get("native_dispatch_performed").getAsBoolean(),"Initial admission uncertainty dispatched no native task");}
                else{String state=receipt.getAsJsonObject("operation").get("state").getAsString();check(state.equals("accepted")||state.equals("running"),"Live state retains its last committed nonterminal operation");}
                byte[] prefix=Files.readAllBytes(folder.resolve("journal/operations.jsonl"));NativeDiagnosticBridgeTest.refuses("openpnp_run_controller_diagnostic",input,"JOURNAL_FAULT");check(Arrays.equals(prefix,Files.readAllBytes(folder.resolve("journal/operations.jsonl"))),"Retry cannot append or redispatch after unknown force");
                check(channel.forcedThenFailed==(mode.equals("intent-before-write")?0:1),"Exact actual-force versus pre-write injection count");
                result.add("request_receipt",receipt);result.add("status",status);
            }
            int count=mode.equals("lease-after-connect")?2:mode.equals("close-observed-fault")||mode.equals("outcome-force")||mode.equals("retirement-force")||mode.equals("capacity")?3:0;
            TaggedProtocolTest.Controller observed=peer;if(count>0)TaggedProtocolTest.await(()->observed.peerEof,1000);
            check(peer.commands.size()==count&&peer.accepted==(count==0?0:1),"Exact recorded native effect count; no replay or wrapper commands");if(count>0)check(peer.peerEof,"Owned connection closed before test cleanup");
            check(!machine.isEnabled()&&!machine.isHomed(),"Machine remains disabled/unhomed");result.add("wire",peer.snapshot());
        }catch(Throwable failure){failure.printStackTrace();result.addProperty("failure",failure.toString());exit=1;}
        finally{if(bridge!=null){try{bridge.close();}catch(Bridge.Fault fenced){try{bridge.recordGuiUnknownExit();}catch(Exception ignored){}try{bridge.close();}catch(Exception failure){result.addProperty("cleanup_failure",failure.toString());exit=1;}}}if(peer!=null)try{peer.close();}catch(Exception failure){result.addProperty("peer_cleanup_failure",failure.toString());exit=1;}}
        result.addProperty("passed",exit==0);result.addProperty("mode",mode);result.addProperty("assertions",assertions);result.addProperty("physical_qualification",false);Files.writeString(folder.resolve("observation.json"),result.toString()+"\n",StandardOpenOption.CREATE_NEW);System.out.println("BRIDGE_DIAGNOSTIC_RESULT "+result);System.exit(exit);
    }
}
