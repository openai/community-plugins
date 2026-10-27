/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.lang.reflect.Field;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;

/** Actual two-root native load replay and explicit same-load confirmation. Placed history is an
 * explicitly seeded contract fixture; no processor initialization, feed, or placement is performed. */
public final class NativeBoardPresenceRecoveryTest {
    private static final Gson JSON=new GsonBuilder().serializeNulls().create();
    private static final List<String> passed=new ArrayList<>();private static int refusals;
    private static Configuration config;
    interface Work {void run()throws Exception;}
    static void yes(boolean ok,String message){if(!ok)throw new AssertionError(message);passed.add(message);}
    static void refused(Work action)throws Exception {try{action.run();throw new AssertionError("Expected BOARD_LOAD_REQUIRED");}catch(Bridge.Fault expected){yes("BOARD_LOAD_REQUIRED".equals(expected.code),"readiness refuses absent native root confirmation");refusals++;}}
    static <T>T nativeTask(Callable<T> action)throws Exception {try{return config.getMachine().submit(action,null,true).get(30,TimeUnit.SECONDS);}catch(ExecutionException failure){if(failure.getCause() instanceof Error)throw(Error)failure.getCause();throw(Exception)failure.getCause();}}
    static Object field(Object value,String name)throws Exception {Field f=value.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(value);}
    @SuppressWarnings("unchecked")static Map<String,Object> object(Object value){return(Map<String,Object>)value;}
    static Map<String,Object> row(NativeBoardLoads ledger,String root){for(Object raw:(List<?>)ledger.snapshot().get("roots")){Map<String,Object> row=object(raw);if(root.equals(row.get("root_instance_id")))return row;}throw new AssertionError("Missing root "+root);}
    static Map<String,Object> retained(NativeBoardLoads ledger,String load){for(Object raw:(List<?>)ledger.snapshot().get("loads")){Map<String,Object> row=object(raw);if(load.equals(row.get("load_id")))return row;}throw new AssertionError("Missing load "+load);}
    static boolean required(NativeBoardLoads ledger){return Boolean.TRUE.equals(ledger.snapshot().get("restart_presence_confirmation_required"));}
    static final class Journal implements AutoCloseable {
        final Path path;final FileChannel channel;long sequence;
        Journal(Path path)throws Exception {this.path=path;channel=FileChannel.open(path,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);}
        void append(String type,Map<String,Object> payload)throws Exception {byte[] bytes=(JSON.toJson(Bridge.map("sequence",++sequence,"type",type,"payload",payload))+"\n").getBytes(StandardCharsets.UTF_8);ByteBuffer buffer=ByteBuffer.wrap(bytes);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);}
        public void close()throws Exception {channel.close();}
    }
    public static void main(String[] args)throws Exception {
        Path root=Path.of(args[0]);Files.createDirectories(root);Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();
        int code=0;String failure=null;Map<String,Object> proof=new LinkedHashMap<>();
        try(Journal journal=new Journal(root.resolve("board-loads.jsonl"))){nativeTask(()->{
            Job job=CanonicalJobImporter.load(config,NativeBoardLoadsTest.canonical(config.getPart("R0603-1K")));
            String jobId=UUID.randomUUID().toString();NativeBoardLoads original=new NativeBoardLoads(journal::append);original.bindJob(job,jobId,true);
            BoardLocation a=job.getBoardLocations().get(0),b=job.getBoardLocations().get(1);String rootA=a.getUniqueId(),rootB=b.getUniqueId(),retiredId=(String)row(original,rootA).get("load_id");
            original.change(job,rootA,"replace","top",retiredId,original.revision(),false);
            String loadA=(String)row(original,rootA).get("load_id"),loadB=(String)row(original,rootB).get("load_id");
            yes(!retiredId.equals(loadA)&&!loadA.equals(loadB),"two active distinct native loads and one retained inactive load exist");
            job.storePlacedStatus(a,"R1",true);job.storePlacedStatus(b,"R1",false);original.checkpoint(job);
            Map<String,Boolean> history=new TreeMap<>(job.getPlacedStatusSnapshot());yes(history.size()==2&&history.containsValue(true)&&history.containsValue(false),"explicit fixture retains both true and false native history keys");
            Map<String,Object> originalRecords=NativeJournalJson.copy((Map<?,?>)field(original,"loads"));byte[] prefix=Files.readAllBytes(journal.path);long initialSequence=journal.sequence;
            NativeBoardLoads replayed=new NativeBoardLoads(journal::append);
            for(String line:Files.readAllLines(journal.path)){Map<String,Object> e=NativeJournalJson.parseObject(line);replayed.recoverEvent((String)e.get("type"),object(e.get("payload")));replayed.observeNativeEvent((String)e.get("type"),object(e.get("payload")));}
            replayed.finishRecovery();
            yes(field(replayed,"boundNativeJob")==null&&((Set<?>)field(replayed,"confirmed")).isEmpty(),"fresh reducer replay restores zero native bindings or confirmations");
            yes(originalRecords.equals(NativeJournalJson.copy((Map<?,?>)field(replayed,"loads"))),"replay preserves complete durable load records byte-equivalently");
            yes(required(replayed),"two active unconfirmed loads require restart confirmation");
            yes("presence_unconfirmed".equals(retained(replayed,loadA).get("state"))&&"presence_unconfirmed".equals(retained(replayed,loadB).get("state")),"both active retained loads report presence unconfirmed");
            refused(()->replayed.requireReady(job));
            replayed.bindJob(job,UUID.randomUUID().toString(),false);
            yes(required(replayed)&&((Set<?>)field(replayed,"confirmed")).isEmpty(),"explicit native job binding grants no root presence confirmations");
            yes(Arrays.equals(prefix,Files.readAllBytes(journal.path))&&journal.sequence==initialSequence,"replay, binding, and readback append no records");
            yes(history.equals(job.getPlacedStatusSnapshot()),"read/replay leaves all original native history unchanged");
            replayed.change(job,rootA,"same-load","top",loadA,replayed.revision(),false);
            yes(required(replayed),"aggregate restart prerequisite remains true after only the first confirmation");
            yes("loaded".equals(row(replayed,rootA).get("load_state"))&&"presence_unconfirmed".equals(row(replayed,rootB).get("load_state")),"first root is confirmed while second root remains presence unconfirmed");
            yes(((Set<?>)field(replayed,"confirmed")).equals(Set.of(rootA)),"first native confirmation grants only its own root binding");
            refused(()->replayed.requireReady(job));
            yes(history.equals(job.getPlacedStatusSnapshot()),"first same-load confirmation preserves original true and false placed history");
            replayed.change(job,rootB,"same-load","top",loadB,replayed.revision(),false);
            yes(!required(replayed),"aggregate restart prerequisite clears only after both active roots are confirmed");
            yes("loaded".equals(row(replayed,rootA).get("load_state"))&&"loaded".equals(row(replayed,rootB).get("load_state")),"both roots now report loaded");
            yes(((Set<?>)field(replayed,"confirmed")).equals(Set.of(rootA,rootB)),"both explicit root confirmations are retained");
            replayed.requireReady(job);yes(true,"board component readiness accepts both explicit simulator confirmations");
            yes("presence_unconfirmed".equals(retained(replayed,retiredId).get("state")),"inactive historical load remains unconfirmed without blocking current aggregate");
            yes(loadA.equals(row(replayed,rootA).get("load_id"))&&loadB.equals(row(replayed,rootB).get("load_id")),"same-load confirmations preserve both active load identities");
            Map<String,Object> afterRecords=object(field(replayed,"loads"));
            for(String id:List.of(retiredId,loadA,loadB))yes(object(originalRecords.get(id)).get("placed_history").equals(object(afterRecords.get(id)).get("placed_history")),"durable original placed history is preserved for load "+id);
            yes(history.equals(job.getPlacedStatusSnapshot()),"all native original placed history remains unchanged after both confirmations");
            yes(journal.sequence==initialSequence+4,"each explicit confirmation forces exactly its intent and outcome");
            long endSize=Files.size(journal.path);Map<String,Object> firstView=NativeJournalJson.copy(replayed.snapshot());replayed.requireReady(job);Map<String,Object> secondView=NativeJournalJson.copy(replayed.snapshot());
            yes(firstView.equals(secondView)&&Files.size(journal.path)==endSize,"final repeated readiness/readback has zero journal effects");
            yes(!config.getMachine().isEnabled()&&Boolean.FALSE.equals(replayed.snapshot().get("physical_load_verified")),"test stays disabled and never claims physical loading");
            proof.put("original_native_placed_history",history);proof.put("final_board_loads",replayed.snapshot());return null;
        });}catch(Throwable e){failure=e.toString();e.printStackTrace();code=1;}finally{
            try{config.getMachine().close();}catch(Throwable e){if(failure==null)failure=e.toString();e.printStackTrace();code=1;}
            proof.putAll(Bridge.map("passed",code==0,"assertions",passed.size(),"refusals",refusals,"checks",passed,"error",failure,"synthetic_initial_placed_history",true,"native_job_initializations",0,"native_feeds",0,"native_placements",0,"process_restart_test",false,"bridge_integration_qualified",false,"execution_authority_restored_by_replay",false,"physical_loading_verified",false));
            Files.writeString(root.resolve("proof.json"),JSON.toJson(proof)+"\n");System.out.println("OPENPNP_NATIVE_BOARD_PRESENCE_RECOVERY_RESULT "+JSON.toJson(proof));
        }System.exit(code);
    }
}
