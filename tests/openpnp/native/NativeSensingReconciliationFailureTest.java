/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.IOException;
import java.lang.reflect.Field;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.SwingUtilities;
import org.openpnp.machine.reference.ReferenceNozzle;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.*;

/** Test-only failure injection into a real prepared native simulator and its owned executor.
 * No native effects, source readings, successful receipts, or occupancy are synthesized here.
 * Each primary/replay case runs in a separate JVM. This does not qualify Swing or hardware. */
public final class NativeSensingReconciliationFailureTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();
    static final List<String> checks=new ArrayList<>();
    static Configuration config;static Bridge bridge;static Adapter adapter;static Path state;static String session,mode;
    static ForceFault channel;static String taskId,originalId,recoveryId;
    static final Map<String,Object> proof=new LinkedHashMap<>();
    static void check(boolean v,String label){if(!v)throw new AssertionError(label);checks.add(label);}
    @SuppressWarnings("unchecked") static Map<String,Object> map(Object v){return (Map<String,Object>)v;}
    static Object plain(Object v){if(v instanceof Map){Map<String,Object> out=new LinkedHashMap<>();((Map<?,?>)v).forEach((k,x)->out.put(String.valueOf(k),plain(x)));return out;}if(v instanceof Iterable){List<Object> out=new ArrayList<>();for(Object x:(Iterable<?>)v)out.add(plain(x));return out;}return v;}
    static JsonObject json(Object... v){return JSON.toJsonTree(plain(Bridge.map(v))).getAsJsonObject();}
    static Map<String,Object> copy(Object v){return NativeJournalJson.parseObject(JSON.toJson(plain(v)));}
    static Map<String,Object> call(String name,JsonObject p)throws Exception{return copy(bridge.call("openpnp_"+name,p));}
    static Map<String,Object> read(String name,Object... v)throws Exception{return call(name,json(v));}
    static JsonObject command(Object... v)throws Exception{JsonObject p=json(v);p.addProperty("session_id",session);p.addProperty("request_id",UUID.randomUUID().toString());p.addProperty("expected_config_revision",(String)read("get_status").get("config_revision"));return p;}
    static Map<String,Object> settle(Map<String,Object> op,String expected)throws Exception{long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(30);while(System.nanoTime()<deadline){op=read("get_operation","operation_id",op.get("operation_id"));if(Set.of("succeeded","failed","outcome_unknown").contains(op.get("state"))&&!config.getMachine().isBusy()){check(expected.equals(op.get("state")),"Native operation settled as "+expected+": "+op.get("method"));return op;}Thread.sleep(10);}throw new AssertionError("Operation did not settle: "+op);}
    static Map<String,Object> run(String name,JsonObject p)throws Exception{return settle(call(name,p),"succeeded");}
    static ReferenceNozzle nozzle()throws Exception{return (ReferenceNozzle)config.getMachine().getDefaultHead().getDefaultNozzle();}
    static Object field(Object target,String name)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);}
    static void set(Object target,String name,Object value)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);f.set(target,value);}
    static void write(String name,Object value)throws Exception{Files.writeString(state.resolve(name),JSON.toJson(plain(value))+"\n",StandardOpenOption.CREATE_NEW);}
    static List<Map<String,Object>> events()throws Exception{List<Map<String,Object>> out=new ArrayList<>();for(String s:Files.readAllLines(state.resolve("journal/operations.jsonl")))out.add(NativeJournalJson.parseObject(s));return out;}
    static long count(String type)throws Exception{return events().stream().filter(e->type.equals(e.get("type"))).count();}
    static <T>T edt(Callable<T> task)throws Exception{AtomicReference<T> out=new AtomicReference<>();AtomicReference<Throwable> error=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{out.set(task.call());}catch(Throwable f){error.set(f);}});if(error.get()!=null)throw new Exception(error.get());return out.get();}
    static final class Presentation{final Map<String,Object> task;final GuiOwnership.SensingReconciliationSubmission callback;Presentation(Map<String,Object> t,GuiOwnership.SensingReconciliationSubmission c){task=copy(t);callback=c;}}
    static final class Adapter implements GuiOwnership{
        final AbstractMachine machine=(AbstractMachine)config.getMachine();final ExternalExecutionControl gate=machine.getExternalExecutionControl();final Object token;
        final BlockingQueue<Presentation> presentations=new LinkedBlockingQueue<>();
        final CountDownLatch bodyReturned=new CountDownLatch(1),releaseWrapper=new CountDownLatch(1);
        volatile boolean inject;volatile Future<?> injectedFuture;
        Adapter()throws Exception{token=gate.claim("sensing79 failure test: native gate with explicit fault injection");}
        public void checkAttachment(Configuration c,Machine m)throws Exception{if(c!=config||m!=machine)throw new IllegalStateException("Wrong native fixture");Bridge.verifyNativeSimulatorClasses(m);}
        public void requireNativeOwnership()throws Exception{if(!gate.owns(token))throw new IllegalStateException("Native ownership lost");}
        public void requireRemoteGrant()throws Exception{requireNativeOwnership();}
        public <T>T invokeNative(Callable<T> task)throws Exception{return gate.withOwner(token,task);}
        public <T>Future<T> submitNative(Callable<T> task,boolean ignoreEnabled)throws Exception{
            boolean fault=inject;inject=false;
            Future<T> future=gate.withOwner(token,()->machine.submit(()->{
                T result=task.call();
                if(fault){bodyReturned.countDown();if(!releaseWrapper.await(30,TimeUnit.SECONDS))throw new AssertionError("Test wrapper release timed out");}
                return result;
            },null,ignoreEnabled));if(fault)injectedFuture=future;return future;
        }
        public void publishJob(Job j){throw new AssertionError("Standalone recovery must not publish a job");}
        public Map<String,Object> snapshot(){return Bridge.map("validation_only",true,"native_ownership_held",gate.owns(token));}
        public boolean supportsSensingReconciliation(){return true;}
        public void presentSensingReconciliation(Map<String,Object> t,SensingReconciliationSubmission c){presentations.add(new Presentation(t,c));}
        public void dismissSensingReconciliation(String id,String reason){}
        boolean authority(){return gate.owns(token);}
    }
    /** Delegates every byte to the original journal; injects one actual force IOException,
     * only after the recovery's real native valve-on has returned. */
    static final class ForceFault extends FileChannel{
        final FileChannel delegate;volatile boolean fired;volatile String last="";volatile Map<String,Object> injectedRecord;
        ForceFault(FileChannel delegate){this.delegate=delegate;}
        public int write(ByteBuffer src)throws IOException{ByteBuffer b=src.duplicate();byte[] bytes=new byte[b.remaining()];b.get(bytes);last=new String(bytes,StandardCharsets.UTF_8);return delegate.write(src);}
        public void force(boolean metadata)throws IOException{
            if(!fired&&last.contains("vacuum_observation_outcome")&&last.contains("valve.returned")){
                Map<String,Object> e=NativeJournalJson.parseObject(last);Map<String,Object> p=NativeSensingReconciliationFailureTest.map(e.get("payload")),d=NativeSensingReconciliationFailureTest.map(p.get("data")),c=NativeSensingReconciliationFailureTest.map(p.get("context"));
                if("recovery".equals(c.get("scope"))&&Boolean.TRUE.equals(d.get("enabled"))){
                    fired=true;injectedRecord=e;
                    try{check(config.getMachine().isEnabled(),"Force fault is injected while the actual native machine is enabled");check(Boolean.TRUE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Actual valve-on returned before the injected force failure");}catch(Exception ex){throw new IOException(ex);}
                    throw new IOException("TEST_ONLY_FORCE_FAILURE_AFTER_NATIVE_VALVE_ON");
                }
            }delegate.force(metadata);
        }
        public int read(ByteBuffer b)throws IOException{return delegate.read(b);}public long read(ByteBuffer[] b,int o,int l)throws IOException{return delegate.read(b,o,l);}
        public long write(ByteBuffer[] b,int o,int l)throws IOException{return delegate.write(b,o,l);}public int read(ByteBuffer b,long p)throws IOException{return delegate.read(b,p);}public int write(ByteBuffer b,long p)throws IOException{return delegate.write(b,p);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return delegate.size();}public FileChannel truncate(long s)throws IOException{delegate.truncate(s);return this;}
        public long transferTo(long p,long c,WritableByteChannel t)throws IOException{return delegate.transferTo(p,c,t);}public long transferFrom(ReadableByteChannel s,long p,long c)throws IOException{return delegate.transferFrom(s,p,c);}
        public MappedByteBuffer map(MapMode m,long p,long s)throws IOException{return delegate.map(m,p,s);}public FileLock lock(long p,long s,boolean sh)throws IOException{return delegate.lock(p,s,sh);}public FileLock tryLock(long p,long s,boolean sh)throws IOException{return delegate.tryLock(p,s,sh);}
        protected void implCloseChannel()throws IOException{delegate.close();}
    }
    static void assertUnresolved(Map<String,Object> status,Map<String,Object> view)throws Exception{
        check(view.get("receipt")==null&&!Boolean.TRUE.equals(view.get("live_resolution_activated")),"Failure has no resolution receipt or activated readiness");
        check(count("sensing_reconciliation_resolved")==0,"No resolved reconciliation record exists");
        Map<String,Object> journal=map(status.get("vacuum_sensing_journal"));boolean sticky=false;
        for(Object item:(List<?>)journal.get("nozzles"))if(Boolean.TRUE.equals(map(item).get("sticky_fault")))sticky=true;
        check(sticky,"Original sensing fault remains sticky");
        NativeVacuumJournal vacuum=(NativeVacuumJournal)field(bridge,"vacuumJournal");
        try{vacuum.requireNoFault();throw new AssertionError("Unexpected ready vacuum journal");}catch(NativeVacuumJournal.Fault expected){check(true,"Native admission still rejects unresolved vacuum faults");}
    }
    static void primary()throws Exception{
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> original=settle(call("measure_sensor",command("nozzle_id",nozzle().getId(),"samples",2)),"outcome_unknown");originalId=(String)original.get("operation_id");write("original-operation.json",original);Map<String,Object> persisted=null;for(Map<String,Object> e:events())if("operation".equals(e.get("type"))&&originalId.equals(map(e.get("payload")).get("operation_id")))persisted=map(e.get("payload"));check(persisted!=null,"Original terminal operation exists in the durable journal");write("original-persisted-operation.json",persisted);
        run("set_machine_enabled",command("enabled",false));run("request_sensing_reconciliation",command("recovery_kind","restore-sensing-readiness"));
        Presentation p=adapter.presentations.poll(10,TimeUnit.SECONDS);check(p!=null,"Public request reaches actual owned local adapter");taskId=(String)p.task.get("task_id");
        if(mode.equals("force-failure")){channel=new ForceFault((FileChannel)field(bridge,"journal"));set(bridge,"journal",channel);}else adapter.inject=true;
        CompletableFuture<Map<String,Object>> done=p.callback.submit(adapter::authority).toCompletableFuture();
        if(!mode.equals("force-failure")){
            check(adapter.bodyReturned.await(15,TimeUnit.SECONDS),"Actual recovery body returns before injected wrapper outcome");
            Map<String,Object> before=read("get_sensing_reconciliation","task_id",taskId);write("body-returned-task.json",before);
            check("verified_pending_commit".equals(before.get("state")),"Successful native probe awaits wrapper completion");assertUnresolved(read("get_status"),before);
            if(mode.equals("wrapper-cancel")){check(adapter.injectedFuture.cancel(false),"Actual native Future cancellation succeeds after body return");}
            adapter.releaseWrapper.countDown();
        }
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(15);Map<String,Object> status=null,view=null;
        while(System.nanoTime()<deadline){status=read("get_status");view=read("get_sensing_reconciliation","task_id",taskId);recoveryId=(String)view.get("recovery_operation_id");
            if(!config.getMachine().isBusy()&&((channel!=null&&channel.fired&&Boolean.TRUE.equals(field(bridge,"journalFault"))&&status.get("native_submission") instanceof Map&&map(status.get("native_submission")).get("publication_fault") instanceof Map)||"reconciliation_unknown".equals(view.get("state"))))break;Thread.sleep(10);}
        check(!config.getMachine().isBusy(),"Actual native wrapper has stopped executing");
        check(!config.getMachine().isEnabled(),"Actual native machine is disabled after failure cleanup");
        check(Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Native valve-off was attempted and returned after valve-on");
        assertUnresolved(status,view);check(original.equals(read("get_operation","operation_id",originalId)),"Original failed operation is unchanged");
        if(channel!=null){check(channel.fired,"Exact armed journal force failure fired");write("injected-force-record.json",channel.injectedRecord);check(Boolean.TRUE.equals(field(bridge,"journalFault")),"Publication failure remains fenced in the running Bridge");Map<String,Object> sub=map(status.get("native_submission")),pub=map(sub.get("publication_fault"));check("publication-fault".equals(sub.get("phase"))&&Boolean.TRUE.equals(sub.get("ownership_retained")),"Failed completion publication retains exact native submission ownership");check("outcome_unknown".equals(map(pub.get("known_body_outcome")).get("state"))&&Boolean.FALSE.equals(pub.get("durable")),"Read-only status retains unknown recovery body without claiming durable completion");}
        else {Map<String,Object> op=read("get_operation","operation_id",recoveryId);check("outcome_unknown".equals(op.get("state")),"Failed/cancelled wrapper retains recovery outcome_unknown");Map<String,Object> nc=map(op.get("native_completion"));if(nc.get("completion_observation") instanceof Map)nc=map(nc.get("completion_observation"));check(Boolean.FALSE.equals(nc.get("native_wrapper_succeeded"))&&Boolean.FALSE.equals(nc.get("native_wrapper_completed")),"Cancelled native wrapper explicitly reports unsuccessful and incomplete completion");if(mode.equals("wrapper-cancel"))check(adapter.injectedFuture.isCancelled(),"Cancellation remains observable on exact native Future");}
        long intents=count("sensing_reconciliation_intent");try{p.callback.submit(adapter::authority).toCompletableFuture().get(5,TimeUnit.SECONDS);throw new AssertionError("Replayed callback admitted");}catch(ExecutionException expected){check(true,"Consumed local callback cannot replay failed recovery");}
        check(intents==count("sensing_reconciliation_intent"),"Callback replay adds no recovery intent");check(!done.isDone()||done.isCompletedExceptionally(),"Local completion never reports a successful receipt");
        write("failure-status.json",status);write("failure-task.json",view);write("identity.json",Bridge.map("task_id",taskId,"original_operation_id",originalId,"recovery_operation_id",recoveryId));
    }
    static void replay()throws Exception{
        Map<String,Object> ids=NativeJournalJson.parseObject(Files.readString(state.resolve("identity.json")));taskId=(String)ids.get("task_id");originalId=(String)ids.get("original_operation_id");recoveryId=(String)ids.get("recovery_operation_id");
        Map<String,Object> status=read("get_status"),view=read("get_sensing_reconciliation","task_id",taskId);assertUnresolved(status,view);
        check(Boolean.TRUE.equals(view.get("historical")),"Fresh process exposes recovery only as historical evidence");check(!Boolean.TRUE.equals(view.get("execution_authority_restored")),"Restart restores no execution authority");
        check(adapter.presentations.isEmpty(),"Replay creates no local callback or new intervention");check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Fresh native process remains idle and disabled");
        check("outcome_unknown".equals(read("get_operation","operation_id",recoveryId).get("state")),"Restart retains unknown recovery operation");
        check(NativeJournalJson.parseObject(Files.readString(state.resolve("original-persisted-operation.json"))).equals(read("get_operation","operation_id",originalId)),"Restart preserves exact persisted original sensing operation");
        write("replay-status.json",status);write("replay-task.json",view);
    }
    public static void main(String[] args)throws Exception{
        if(args.length!=3)throw new IllegalArgumentException("Expected runtime samples, exclusive state directory, case or replay");state=Path.of(args[1]);mode=args[2];if(!Set.of("force-failure","wrapper-cancel","replay").contains(mode))throw new IllegalArgumentException("Unsupported failure case");boolean replay=mode.equals("replay");if(!replay)Files.createDirectory(state);int exit=0;
        try{
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=replay?null:GuiSensingFixture.prepare(configDir,manifest,"invalid-read");
            if(!replay)Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            String digest=replay?(String)NativeJournalJson.parseObject(Files.readString(state.resolve("fixture-digest.json"))).get("sha256"):(String)prepared.get("manifest_sha256");
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,digest,"invalid-read"));check(Boolean.TRUE.equals(claimed.get("sensing_fixture_attested")),"Fresh process owns the attested prepared native simulator source");
            if(!replay){write("fixture-digest.json",Bridge.map("sha256",digest));Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);}
            adapter=new Adapter();bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]),0,true,"gui-simulator",adapter);
            if(replay)replay();else primary();proof.put("passed",true);
        }catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally{
            if(adapter!=null)adapter.releaseWrapper.countDown();
            try{if(bridge!=null){try{bridge.recordGuiUnknownExit();}catch(Exception expected){if(!Boolean.TRUE.equals(field(bridge,"journalFault")))throw expected;proof.put("damaged_journal_exit_record_unavailable",true);}bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("cleanup_failure",failure.toString());exit=1;}
            try{if(adapter!=null&&adapter.gate.owns(adapter.token))adapter.gate.release(adapter.token);if(config!=null)config.getMachine().close();proof.put("native_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("native_cleanup_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("mode",mode,"pid",ProcessHandle.current().pid(),"checks",checks,"assertions",checks.size(),"actual_native_simulator",true,"test_fault_injection",true,"swing_qualified",false,"hardware_qualified",false,"current_source_qualified",false));write(replay?"replay-proof.json":"failure-proof.json",proof);System.out.println("NATIVE_SENSING_RECONCILIATION_FAILURE_RESULT "+JSON.toJson(proof));
        }System.exit(exit);
    }
}
