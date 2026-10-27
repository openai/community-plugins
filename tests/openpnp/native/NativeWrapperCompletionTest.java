/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.channels.*;
import java.nio.*;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.driver.ReferenceAdvancedMotionPlanner;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.*;
import org.openpnp.spi.HeadMountable;
import org.openpnp.spi.MotionPlanner.CompletionType;

/** Actual native wrapper tests with a bounded test-only CommandJog gate. No physical devices. */
public final class NativeWrapperCompletionTest {
    static final Gson JSON=new Gson();static final List<String> checks=new ArrayList<>();
    static Bridge bridge;static ReferenceMachine machine;static String session;static Path journal;
    static void check(boolean b,String m){if(!b)throw new AssertionError(m);checks.add(m);}
    // Pinned Gson2.2 cannot reflect JDK17 private collection implementations; normalize evidence only.
    static Object plain(Object value){if(value instanceof Map){Map<String,Object>m=new LinkedHashMap<>();((Map<?,?>)value).forEach((k,v)->m.put(String.valueOf(k),plain(v)));return m;}if(value instanceof Iterable){List<Object>a=new ArrayList<>();for(Object v:(Iterable<?>)value)a.add(plain(v));return a;}return value;}
    static JsonElement json(Object value){return JSON.toJsonTree(plain(value));}
    static JsonObject object(Object...pairs){return json(Bridge.map(pairs)).getAsJsonObject();}
    @SuppressWarnings("unchecked")static Map<String,Object> call(String method,JsonObject args)throws Exception{return(Map<String,Object>)bridge.call("openpnp_"+method,args);}
    static Map<String,Object> read(String method,Object...pairs)throws Exception{return call(method,object(pairs));}
    static JsonObject mutation(Object...pairs)throws Exception{JsonObject p=object("request_id",UUID.randomUUID().toString(),"session_id",session,"expected_config_revision",read("get_configuration").get("config_revision"));for(Map.Entry<String,JsonElement> e:object(pairs).entrySet())p.add(e.getKey(),e.getValue());return p;}
    static boolean boundary(Map<String,Object> op){return Arrays.asList("succeeded","failed","outcome_unknown","aborted","paused","cancelled").contains(op.get("state"));}
    static long steps(Map<String,Object> op){return((Number)op.getOrDefault("native_steps_started",0)).longValue();}
    @SuppressWarnings("unchecked")static Map<String,Object> result(Map<String,Object> op){return(Map<String,Object>)op.get("result");}
    static Map<String,Object> await(String id)throws Exception{long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);Map<String,Object> op;do{op=read("get_operation","operation_id",id);if(boundary(op))return op;Thread.sleep(5);}while(System.nanoTime()<end);throw new AssertionError("No completed boundary: "+op);}
    static void nativeIdle()throws Exception{long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);while(machine.isBusy()&&System.nanoTime()<end)Thread.sleep(5);check(!machine.isBusy(),"native wrapper eventually releases task");}
    static Map<String,Object> success(String method,JsonObject p)throws Exception{Map<String,Object> op=await((String)call(method,p).get("operation_id"));check("succeeded".equals(op.get("state")),method+" succeeds");nativeIdle();return result(op);}
    static void refuses(String method,JsonObject p,String...codes)throws Exception{try{call(method,p);}catch(Bridge.Fault e){check(Arrays.asList(codes).contains(e.code),method+" refusal code "+e.code);return;}throw new AssertionError("Expected refusal of "+method);}
    static boolean containsFalseEnabled(Object value){if(value instanceof Map){Map<?,?>m=(Map<?,?>)value;if(Boolean.FALSE.equals(m.get("enabled")))return true;for(Object v:m.values())if(containsFalseEnabled(v))return true;}else if(value instanceof Iterable)for(Object v:(Iterable<?>)value)if(containsFalseEnabled(v))return true;return false;}
    static boolean containsText(Object value,String text){if(value instanceof String)return ((String)value).contains(text);if(value instanceof Map){for(Object v:((Map<?,?>)value).values())if(containsText(v,text))return true;}else if(value instanceof Iterable)for(Object v:(Iterable<?>)value)if(containsText(v,text))return true;return false;}
    static boolean publicationCase(String mode){return mode.equals("publication-failure")||mode.equals("force-return-failure")||mode.equals("wrapper-force-return-failure");}
    static List<Map<String,Object>> opRecords(String id)throws Exception{List<Map<String,Object>>out=new ArrayList<>();for(String line:Files.readAllLines(journal)){Map<?,?>e=JSON.fromJson(line,Map.class);if("operation".equals(e.get("type"))&&e.get("payload") instanceof Map){Map<String,Object>p=(Map<String,Object>)e.get("payload");if(id.equals(p.get("operation_id")))out.add(p);}}return out;}
    static long boundaryRecords(String id)throws Exception{return opRecords(id).stream().filter(NativeWrapperCompletionTest::boundary).count();}
    public static final class GatePlanner extends ReferenceAdvancedMotionPlanner {
        final CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);volatile boolean armed,fail,threw;volatile int gatedCalls;
        @Override public void waitForCompletion(HeadMountable mountable,CompletionType type)throws Exception{
            if(armed&&type==CompletionType.CommandJog){armed=false;gatedCalls++;entered.countDown();if(!release.await(4,TimeUnit.SECONDS))throw new IllegalStateException("TEST_GATE_DEADLINE");if(fail){threw=true;throw new IllegalStateException("CONTROLLED_POST_CALL_BARRIER_FAILURE");}}
            super.waitForCompletion(mountable,type);
        }
    }
    /** Forces the real retained journal once, then reports an injected return failure. */
    static final class AfterForceChannel extends FileChannel {
        final FileChannel delegate;int forcedThenFailed;
        AfterForceChannel(FileChannel delegate){this.delegate=delegate;}
        public void force(boolean metadata)throws IOException{delegate.force(metadata);if(forcedThenFailed++==0)throw new IOException("CONTROLLED_AFTER_REAL_FORCE_FAILURE");}
        public int read(ByteBuffer dst)throws IOException{return delegate.read(dst);}public long read(ByteBuffer[] dst,int offset,int length)throws IOException{return delegate.read(dst,offset,length);}
        public int read(ByteBuffer dst,long position)throws IOException{return delegate.read(dst,position);}public int write(ByteBuffer src)throws IOException{return delegate.write(src);}
        public long write(ByteBuffer[] src,int offset,int length)throws IOException{return delegate.write(src,offset,length);}public int write(ByteBuffer src,long position)throws IOException{return delegate.write(src,position);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return delegate.size();}
        public FileChannel truncate(long size)throws IOException{delegate.truncate(size);return this;}public long transferTo(long p,long count,WritableByteChannel target)throws IOException{return delegate.transferTo(p,count,target);}
        public long transferFrom(ReadableByteChannel src,long p,long count)throws IOException{return delegate.transferFrom(src,p,count);}public MappedByteBuffer map(MapMode mode,long p,long size)throws IOException{return delegate.map(mode,p,size);}
        public FileLock lock(long p,long size,boolean shared)throws IOException{return delegate.lock(p,size,shared);}public FileLock tryLock(long p,long size,boolean shared)throws IOException{return delegate.tryLock(p,size,shared);}
        protected void implCloseChannel()throws IOException{delegate.close();}
    }
    static void duringGate(String method,JsonObject input,String id,GatePlanner planner,JsonObject observation)throws Exception{
        check(planner.entered.await(4,TimeUnit.SECONDS),"actual native CommandJog gate entered after body");
        Map<String,Object> during=read("get_operation","operation_id",id);observation.add("operation_while_wrapper_blocked",json(during));
        check(machine.isBusy(),"actual native machine remains busy at wrapper gate");check(!boundary(during),"no paused or terminal operation published before wrapper completion");
        check(boundaryRecords(id)==0,"journal contains no premature paused or terminal operation");
        Map<String,Object> replay=call(method,input);check(id.equals(replay.get("operation_id"))&&!boundary(replay),"in-flight identical request returns original nonterminal operation");
        Map<String,Object> receipt=read("get_request_status","request_id",input.get("request_id").getAsString());check(Boolean.TRUE.equals(receipt.get("found"))&&id.equals(((Map<?,?>)receipt.get("operation")).get("operation_id")),"in-flight request receipt retains exact operation identity");
        refuses("set_machine_enabled",mutation("enabled",false),"BUSY");check(planner.gatedCalls==1,"replay or refused overlap does not resubmit native work");
    }
    static void generic(String mode,GatePlanner planner,JsonObject observation)throws Exception{
        JsonObject input=mutation("enabled",false);planner.armed=true;planner.fail=mode.equals("barrier-failure")||mode.equals("wrapper-force-return-failure");
        String id=(String)call("set_machine_enabled",input).get("operation_id");duringGate("set_machine_enabled",input,id,planner,observation);
        if(publicationCase(mode)){
            read("renew_control_session","session_id",session,"ttl_seconds",1);
            List<Map<String,Object>> recorded=opRecords(id);String committed=(String)recorded.get(recorded.size()-1).get("state");byte[] prefix=Files.readAllBytes(journal);
            java.lang.reflect.Field field=Bridge.class.getDeclaredField("journal");field.setAccessible(true);AfterForceChannel injected=null;
            if(!mode.equals("publication-failure")){injected=new AfterForceChannel((FileChannel)field.get(bridge));field.set(bridge,injected);observation.addProperty("test_only_real_force_then_return_failure",true);}
            else{((FileChannel)field.get(bridge)).close();observation.addProperty("test_only_closed_journal_after_body",true);}
            planner.release.countDown();nativeIdle();
            Map<String,Object> after=null,status=null;long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(3);
            do{after=read("get_operation","operation_id",id);status=read("get_status");if(Boolean.TRUE.equals(status.get("journal_fault")))break;Thread.sleep(5);}while(System.nanoTime()<end);
            // These reads are distinct observations: status may drain completion
            // after the earlier operation DTO was copied. Refresh after the wait.
            observation.add("operation_before_publication_refresh",json(after));
            observation.add("status_after_publication_wait",json(status));
            after=read("get_operation","operation_id",id);
            observation.add("operation_after_publication_refresh",json(after));
            check(Boolean.TRUE.equals(status.get("journal_fault")),"forced terminal publication failure exposes journal fault");
            check(committed.equals(after.get("state"))&&!boundary(after),"failed publication preserves last committed operation state");
            check(after.get("publication_fault") instanceof Map&&containsFalseEnabled(after.get("publication_fault")),"publication fault preserves known disabled body result in explicit diagnostics");
            check("publication-fault".equals(((Map<?,?>)after.get("native_completion")).get("phase")),"explicit native completion phase reports publication fault");
            check(Boolean.FALSE.equals(((Map<?,?>)after.get("publication_fault")).get("durable")),"publication fault overlay explicitly disclaims durable terminal receipt");
            Map<?,?> completion=(Map<?,?>)((Map<?,?>)after.get("publication_fault")).get("completion_observation");
            check(completion!=null&&Boolean.TRUE.equals(completion.get("native_wrapper_completed")),"publication fault retains actual wrapper-completion observation");
            check(Boolean.valueOf(!planner.fail).equals(completion.get("native_wrapper_succeeded")),"publication fault preserves wrapper success versus failure");
            if(planner.fail){check(planner.threw&&containsText(completion,"CONTROLLED_POST_CALL_BARRIER_FAILURE"),"combined wrapper and force-return failure retains original wrapper error");}
            check(containsText(after,"PUBLICATION")||containsText(after,"publication"),"operation distinguishes failed publication from a durable terminal receipt");
            check(id.equals(status.get("active_operation_id")),"publication failure retains original active ownership fence");
            byte[] afterFailure=Files.readAllBytes(journal);
            if(injected!=null){check(injected.forcedThenFailed==1,"delegating seam forces actual journal once before throwing");check(afterFailure.length>prefix.length&&Arrays.equals(prefix,Arrays.copyOf(afterFailure,prefix.length)),"real forced operation append preserves original journal prefix");check(boundaryRecords(id)==1&&(planner.fail?"outcome_unknown":"succeeded").equals(opRecords(id).get(opRecords(id).size()-1).get("state")),"force-return uncertainty preserves one real terminal journal record while live memory remains nonterminal");}
            else check(Arrays.equals(prefix,afterFailure)&&boundaryRecords(id)==0,"failed closed-channel publication adds no terminal journal record");
            try{bridge.drainLocalGuiTakeover();throw new AssertionError("Expected lifecycle fault refusal");}catch(Bridge.Fault refused){check("RECOVERY_REQUIRED".equals(refused.code),"headless Bridge lifecycle drain reports retained publication fault instead of ordinary waiting");}
            observation.addProperty("lifecycle_probe_is_headless_bridge_method",true);
            long leaseEnd=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);Map<String,Object> lease;
            do{lease=read("get_control_session");if(lease.get("session_id")==null)break;Thread.sleep(10);}while(System.nanoTime()<leaseEnd);
            check(lease.get("session_id")==null,"watchdog continues lease expiry after publication fault");
            check(id.equals(read("get_status").get("active_operation_id")),"lease expiry does not release publication-fault ownership");
            observation.add("control_session_after_fault_expiry",json(lease));
            refuses("set_machine_enabled",input,"JOURNAL_FAULT");
            Map<String,Object> receipt=read("get_request_status","request_id",input.get("request_id").getAsString());Map<?,?> original=(Map<?,?>)receipt.get("operation");check(Boolean.TRUE.equals(receipt.get("found"))&&id.equals(original.get("operation_id"))&&committed.equals(original.get("state")),"read-only request receipt after refused mutation retry preserves original operation/state");
            refuses("home_machine",mutation(),"JOURNAL_FAULT","BUSY","RECOVERY_REQUIRED");
            check(Arrays.equals(afterFailure,Files.readAllBytes(journal))&&planner.gatedCalls==1,"repeated observations and retry neither append nor redispatch");
            if(injected!=null)check(injected.forcedThenFailed==1,"uncertain real-force result is not automatically forced again");
            observation.add("status_after_publication_failure",json(status));observation.add("operation_after_wrapper",json(after));
        }else{
            planner.release.countDown();Map<String,Object> after=await(id);nativeIdle();
            if(mode.equals("barrier-failure")){check(planner.threw,"controlled post-call wrapper error occurred");check("outcome_unknown".equals(after.get("state")),"wrapper failure downgrades overall completion to unknown");check("NATIVE_WRAPPER_FAILED".equals(result(after).get("code")),"wrapper failure has exact error code");check(containsFalseEnabled(result(after).get("known_body_outcome")),"unknown overall result retains known disabled body outcome");check(containsText(after,"CONTROLLED_POST_CALL_BARRIER_FAILURE"),"unknown result retains native wrapper error");refuses("home_machine",mutation(),"RECOVERY_REQUIRED");}
            else{check("succeeded".equals(after.get("state")),"successful wrapper publishes success");check(Boolean.FALSE.equals(result(after).get("enabled")),"successful body result layout remains unchanged");}
            check(boundaryRecords(id)==1,"exactly one terminal operation record is forced");
            Map<String,Object> replay=call("set_machine_enabled",input);check(id.equals(replay.get("operation_id"))&&after.get("state").equals(replay.get("state")),"post-boundary replay retains original operation and outcome");
            read("get_status");read("get_operation","operation_id",id);check(boundaryRecords(id)==1&&planner.gatedCalls==1,"repeated drain publishes exactly once with no redispatch");
            observation.add("operation_after_wrapper",json(after));
        }
        check(!machine.isEnabled()&&!machine.isHomed(),"generic probe leaves native machine disabled and unhomed");
    }
    static JsonObject canonical(Part part){double h=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();return object("schemaVersion",1,"id","wrapper-step","units","mm","coordinateConvention","openpnp-top-view","parts",List.of(Bridge.map("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",h)),"boards",List.of(Bridge.map("id","board","widthMm",20,"heightMm",20,"placements",List.of(Bridge.map("ref","R1","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",h,"x",10,"y",10,"z",0,"rotation",0,"side","top","enabled",true,"type","placement")))),"panels",List.of(),"instances",List.of(Bridge.map("id","board-1","kind","board","definitionId","board","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true)));}
    static void step(String mode,GatePlanner planner,JsonObject observation)throws Exception{
        success("set_machine_enabled",mutation("enabled",true));success("home_machine",mutation());Part part=Configuration.get().getPart("R0603-1K");
        ReferenceTrayFeeder feeder=(ReferenceTrayFeeder)machine.getFeeders().stream().filter(f->f.isEnabled()&&f.getPart()==part).findFirst().orElseThrow();
        String job=(String)success("prepare_job",mutation("canonical_job",canonical(part))).get("job_id");check(Boolean.TRUE.equals(success("validate_job",mutation()).get("valid")),"actual native one-placement fixture validates");
        JsonObject input=mutation("job_id",job);planner.armed=true;String id=(String)call("step_job",input).get("operation_id");duringGate("step_job",input,id,planner,observation);
        Map<String,Object> during=read("get_operation","operation_id",id);check(steps(during)==1,"exactly one actual native next call returned before wrapper gate");
        check(!"paused".equals(read("get_status").get("job_state")),"job lifecycle does not expose resumable pause before wrapper completion");
        refuses("resume_job",mutation("operation_id",id),"INVALID_STATE","BUSY");refuses("step_job",mutation("operation_id",id),"INVALID_STATE","BUSY");
        JsonObject abort=null;if(mode.equals("step-late-abort")){abort=mutation("operation_id",id);call("abort_job",abort);check(steps(read("get_operation","operation_id",id))==1,"late abort request does not dispatch next while prior wrapper blocked");}
        planner.release.countDown();Map<String,Object> after=await(id);nativeIdle();
        if(mode.equals("step-late-abort")){
            if("paused".equals(after.get("state"))){long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);do{after=read("get_operation","operation_id",id);if("aborted".equals(after.get("state")))break;Thread.sleep(5);}while(System.nanoTime()<end);nativeIdle();}
            check("aborted".equals(after.get("state")),"accepted abort after body pause is fulfilled after wrapper completion");check(steps(after)==1&&feeder.getFeedCount()==0,"late abort cleanup does not repeat next or consume feed");
            check("aborted".equals(call("abort_job",abort).get("state")),"late abort receipt replay does not repeat cleanup");
        }else{
            check("paused".equals(after.get("state"))&&"paused".equals(read("get_status").get("job_state")),"successful wrapper publishes matching resumable pause");
            check(steps(after)==1&&boundaryRecords(id)==1,"one native step and one durable pause boundary");
            Map<String,Object> next=await((String)call("step_job",mutation("operation_id",id)).get("operation_id"));nativeIdle();check(id.equals(next.get("operation_id"))&&steps(next)==2,"next explicit step preserves operation and advances exactly once");
            after=await((String)call("abort_job",mutation("operation_id",id)).get("operation_id"));nativeIdle();check("aborted".equals(after.get("state")),"explicit native cleanup drains step fixture");
        }
        observation.add("operation_after_wrapper",json(after));observation.addProperty("native_steps",steps(after));observation.addProperty("native_feeds",feeder.getFeedCount());
        check(feeder.getFeedCount()==0,"early-step fixture performs no feed");check(machine.getHeads().stream().flatMap(h->h.getNozzles().stream()).allMatch(n->n.getPart()==null),"step fixture ends without a held native part");
        success("set_machine_enabled",mutation("enabled",false));
    }
    public static void main(String[]args)throws Exception{
        if(args.length==1){NativeCompletionCases.run(NativeWrapperCompletionTest.class,Paths.get(args[0]),"WRAPPER_COMPLETION_FIX_RESULT ","blocked-success","barrier-failure","publication-failure","force-return-failure","wrapper-force-return-failure","step-pause","step-late-abort");return;}
        String mode=args[0];if(!Arrays.asList("blocked-success","barrier-failure","publication-failure","force-return-failure","wrapper-force-return-failure","step-pause","step-late-abort").contains(mode))throw new IllegalArgumentException("Unknown bounded case");
        Path root=Paths.get(args[1]);Files.createDirectories(root.resolve("config"));JsonObject observation=new JsonObject();observation.addProperty("mode",mode);GatePlanner planner=new GatePlanner();int exit=0;
        try{
            Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);boolean job=mode.startsWith("step-");if(job)SimulatorMain.configureSustainedWorkload(config);
            machine=(ReferenceMachine)config.getMachine();check(!machine.isEnabled(),"native fixture initially disabled");machine.setMotionPlanner(planner);
            Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);journal=root.resolve("journal/operations.jsonl");
            bridge=new Bridge(config,token,root.resolve("journal"),Paths.get(args[2]),0,true,job?"sustained-workload":"native-simulator");session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
            if(job)step(mode,planner,observation);else generic(mode,planner,observation);
        }catch(Throwable failure){failure.printStackTrace();observation.addProperty("failure",failure.toString());exit=1;}
        finally{
            planner.release.countDown();
            if(machine!=null){long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);while(machine.isBusy()&&System.nanoTime()<end)Thread.sleep(5);observation.addProperty("native_busy_before_cleanup",machine.isBusy());}
            if(bridge!=null)try{bridge.close();if(publicationCase(mode)&&exit==0){observation.addProperty("cleanup_error","Publication-fault close did not refuse");exit=1;}}catch(Bridge.Fault refusal){if(publicationCase(mode)&&"BUSY".equals(refusal.code)){observation.addProperty("publication_fault_close_refused",true);checks.add("ordinary close refuses while completion publication is faulted");}else{observation.addProperty("cleanup_error",refusal.toString());exit=1;}}catch(Throwable failure){observation.addProperty("cleanup_error",failure.toString());exit=1;}
            if(publicationCase(mode)){observation.addProperty("fenced_child_exit_without_native_cleanup",true);}else if(machine!=null)try{machine.close();}catch(Throwable failure){observation.addProperty("machine_cleanup_error",failure.toString());exit=1;}
        }
        observation.addProperty("passed",exit==0);observation.addProperty("assertions",checks.size());JsonArray names=new JsonArray();for(String s:checks)names.add(new JsonPrimitive(s));observation.add("checks",names);observation.addProperty("test_only_motion_planner_gate",true);observation.addProperty("physical_qualification",false);observation.addProperty("physical_or_controller_connections",0);
        Files.writeString(root.resolve("observation.json"),observation.toString()+"\n",StandardOpenOption.CREATE_NEW);System.out.println("WRAPPER_COMPLETION_FIX_RESULT "+observation);System.exit(exit);
    }
}
