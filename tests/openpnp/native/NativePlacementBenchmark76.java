/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.image.BufferedImage;
import java.io.*;
import java.lang.management.ManagementFactory;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;
import javax.imageio.ImageIO;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.MotionPlanner.CompletionType;
import org.openpnp.machine.reference.ReferencePnpJobProcessor;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** One fresh-JVM matched native trial. Production bridge journaling/guards are never disabled. */
public final class NativePlacementBenchmark76 {
    static final Gson GSON=new Gson();static Configuration config;static Machine machine;static Bridge bridge;static String session;static Path journal;static String expectedRevision;static Path trialRoot;
    public static void main(String[] args)throws Exception {
        if(args.length!=4)throw new IllegalArgumentException("Usage: direct|bridge SAMPLE_ROOT COUNT WARMUP");
        String mode=args[0];int count=Integer.parseInt(args[2]),warmup=Integer.parseInt(args[3]);
        if(!Arrays.asList("direct","bridge").contains(mode)||count<100||count>200||warmup<10||warmup>50)throw new IllegalArgumentException("Expected mode, 100..200 measured and 10..50 warmup placements");
        Path root=Path.of(System.getProperty("openpnp.benchmark.root"));trialRoot=root;if(!Files.isDirectory(root)||Files.exists(root.resolve("config")))throw new IllegalArgumentException("Fresh owned trial root required");Path configuration=root.resolve("config");Files.createDirectory(configuration);
        Configuration.initialize(configuration.toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);machine=config.getMachine();machine.setSpeed(1);if(!org.openpnp.scripting.Scripting.hasNativeActionObserverApi())throw new AssertionError("Required patched native action observer missing");if(((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).getJobOrder()!=ReferencePnpJobProcessor.JobOrderHint.Unsorted)throw new AssertionError("Exact Unsorted recipe required");
        int exit=0;
        try {
            if(mode.equals("bridge")) {
                Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());journal=root.resolve("journal").resolve("operations.jsonl");
                bridge=new Bridge(config,token,journal.getParent(),Paths.get(args[1]),0,true,"sustained-workload");
                Map<String,Object> capabilities=call("openpnp_get_capabilities",new JsonObject());if(!Boolean.TRUE.equals(((Map<?,?>)capabilities.get("native_action_ledger")).get("available")))throw new AssertionError("Native ledger unavailable");expectedRevision=(String)call("openpnp_get_status",new JsonObject()).get("config_revision");
                session=(String)call("openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",600)).get("session_id");
                run("openpnp_set_machine_enabled","enabled",true);refreshRevision();run("openpnp_home_machine");refreshRevision();
            } else {machine.submit(()->{machine.setEnabled(true);return null;},null,true).get(30,TimeUnit.SECONDS);machine.execute(()->{machine.home();return null;},false,5000,30000);}
            Part part=config.getPart("R0603-1K");ReferenceTrayFeeder feeder=(ReferenceTrayFeeder)machine.getFeeders().stream().filter(f->f.getPart()==part).findFirst().orElseThrow();
            int initialCount=feeder.getFeedCount();if(initialCount!=0)throw new AssertionError("Fresh fixture feed count is not zero");
            String jobOrder=((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).getJobOrder().name();
            JsonObject warmupJob=canonical(part,warmup,"warmup"),measuredJob=canonical(part,count,"measured");
            Map<String,Object> warmupResult=job(mode,warmupJob,warmup);int afterWarmup=feeder.getFeedCount();
            if(afterWarmup!=warmup)throw new AssertionError("Warmup feed count mismatch");
            Map<String,Object> measured=job(mode,measuredJob,count);int finalCount=feeder.getFeedCount();
            if(finalCount-afterWarmup!=count||finalCount!=warmup+count)throw new AssertionError("Measured feed count mismatch");
            if(!jobOrder.equals(((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).getJobOrder().name()))throw new AssertionError("Native job order changed during the trial");
            // Match capture and PNG encoding on both paths. Bridge additionally retains an artifact
            // and durable operation receipt. These samples are separate from placement duration.
            if(bridge!=null)refreshRevision();capture(mode);capture(mode);List<Double> captures=new ArrayList<>();List<Integer> pngSizes=new ArrayList<>();
            for(int i=0;i<20;i++){Map<String,Object> sample=capture(mode);captures.add(((Number)sample.get("seconds")).doubleValue());pngSizes.add(((Number)sample.get("png_bytes")).intValue());}
            Map<String,Object> classes=new LinkedHashMap<>();for(Class<?> clazz:Arrays.asList(Bridge.class,SimulatorMain.class,CanonicalJobImporter.class,NativeSettings.class,NativePlacementBenchmark76.class,ReferencePnpJobProcessor.class))classes.put(clazz.getName(),classHash(clazz));
            Map<String,Object> runtime=Bridge.map("max_heap_bytes",Runtime.getRuntime().maxMemory(),"jvm_input_arguments",new ArrayList<String>(ManagementFactory.getRuntimeMXBean().getInputArguments()),"memory_measurement","Configured maximum Java heap; total RSS and native image memory are not measured by this benchmark");
            System.out.println("OPENPNP_NATIVE_PLACEMENT_BENCHMARK_RESULT "+GSON.toJson(Bridge.map("mode",mode,"count",count,"warmup_count",warmup,"warmup",warmupResult,"measured",measured,"canonical_job_sha256",sha(measuredJob.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8)),"native_feed_count_initial",initialCount,"native_feed_count_after_warmup",afterWarmup,"native_feed_count_final",finalCount,"measured_native_feeds",finalCount-afterWarmup,"finite_tray_capacity",10000,"native_capture_samples",captures,"capture_png_bytes",pngSizes,"capture_params",Bridge.map("camera","default-head-camera","mode","settled","png_encoding",true,"warmup_captures",2,"measured_captures",20),"machine_speed",machine.getSpeed(),"job_order",jobOrder,"native_processor_class",machine.getPnpJobProcessor().getClass().getName(),"fixture","pinned-default-with-upstream-acceleration-recipe-and-finite-zero-pitch-trays","upstream_commit",Bridge.UPSTREAM,"java_runtime",System.getProperty("java.runtime.version"),"java_vm",System.getProperty("java.vm.name"),"java_vm_version",System.getProperty("java.vm.version"),"os",System.getProperty("os.name"),"architecture",System.getProperty("os.arch"),"available_processors",Runtime.getRuntime().availableProcessors(),"runtime_limits",runtime,"class_sha256",classes,"journal_bytes_final",journal==null?0:Files.size(journal),"journal_path",journal==null?null:journal.toString(),"production_journal_force_enabled",mode.equals("bridge"),"bridge_code_origin",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString(),"native_code_origin",ReferencePnpJobProcessor.class.getProtectionDomain().getCodeSource().getLocation().toString(),"harness_code_origin",NativePlacementBenchmark76.class.getProtectionDomain().getCodeSource().getLocation().toString(),"journal_sha256",journal==null?null:sha(Files.readAllBytes(journal)),"boundary","native executor versus in-process Bridge.call; HTTP/MCP transport excluded; no extra status reads inside the timed interval","poll_interval_ms",mode.equals("bridge")?2:0,"physical_qualification",false)));
        }catch(Throwable e){e.printStackTrace();exit=1;}finally{boolean idle=!machine.isBusy();if(bridge!=null)bridge.close();machine.close();Files.writeString(root.resolve("closure.json"),GSON.toJson(Bridge.map("exit_code",exit,"native_machine_idle_before_close",idle,"bridge_close_returned",true,"machine_close_returned",true,"pid",ProcessHandle.current().pid())));}System.exit(exit);
    }
    static Map<String,Object> job(String mode,JsonObject canonical,int requested)throws Exception {
        Job nativeJob=null;String jobId=null;
        if(mode.equals("direct")){nativeJob=CanonicalJobImporter.load(config,canonical);Map<String,Object> validation=NativeJobPreflight.validate(config,nativeJob);if(!Boolean.TRUE.equals(validation.get("valid")))throw new AssertionError(validation);}
        else {refreshRevision();Map<String,Object> prepared=run("openpnp_prepare_job","canonical_job",canonical);jobId=(String)((Map<?,?>)prepared.get("result")).get("job_id");refreshRevision();Map<String,Object> validation=run("openpnp_validate_job");if(!Boolean.TRUE.equals(((Map<?,?>)validation.get("result")).get("valid")))throw new AssertionError(validation);refreshRevision();}
        Map<String,Object> beforeDiagnostics=diagnostics();long journalBefore=journal==null?0:Files.size(journal),started=System.nanoTime();long steps;int placed;String operationId=null;
        if(mode.equals("direct")){
            final Job job=nativeJob;
            steps=machine.submit(()->{machine.getPnpJobProcessor().initialize(job);long n=0;boolean more;do{n++;more=machine.getPnpJobProcessor().next();}while(more);machine.getMotionPlanner().waitForCompletion(null,CompletionType.WaitForStillstand);return n;},null,false).get(300,TimeUnit.SECONDS);
            placed=0;for(BoardLocation b:nativeJob.getBoardLocations())for(Placement p:b.getBoard().getPlacements())if(nativeJob.retrievePlacedStatus(b,p.getId()))placed++;
        } else {Map<String,Object> result=run("openpnp_start_job","job_id",jobId);operationId=(String)result.get("operation_id");placed=((Number)((Map<?,?>)result.get("result")).get("placed")).intValue();steps=((Number)result.get("native_steps_started")).longValue();}
        double seconds=(System.nanoTime()-started)/1e9;Map<String,Object> afterDiagnostics=diagnostics();if(placed!=requested)throw new AssertionError("Placed "+placed+" of "+requested);
        long intents=0,completions=0,checkpointComplete=0;Map<String,Map<String,Object>> actionIntents=new LinkedHashMap<>(),actionOutcomes=new LinkedHashMap<>();Map<String,Integer> actionKinds=new TreeMap<>(),recordTypes=new TreeMap<>();
        if(journal!=null)for(String line:Files.readAllLines(journal)){
            JsonObject event=GSON.fromJson(line,JsonObject.class);JsonObject payload=event.getAsJsonObject("payload");
            if(payload.has("operation_id")&&operationId.equals(payload.get("operation_id").getAsString())){
                String type=event.get("type").getAsString();recordTypes.put(type,recordTypes.getOrDefault(type,0)+1);if(type.equals("native_step_intent"))intents++;if(type.equals("native_step_complete"))completions++;if(type.equals("native_placement_checkpoint")&&"Job.Placement.Complete".equals(payload.get("hook").getAsString())){if(!payload.getAsJsonObject("context").get("native_placed_status").getAsBoolean())throw new AssertionError("Complete without native placed flag");checkpointComplete++;}if(type.equals("native_action_intent")||type.equals("native_action_outcome")){Map<String,Object> value=NativeJournalJson.parseObject(payload.toString());Map<String,Map<String,Object>> target=type.equals("native_action_intent")?actionIntents:actionOutcomes;if(target.put((String)value.get("action_id"),value)!=null)throw new AssertionError("Duplicate native action ID");}
            }
        }
        if(journal!=null&&(intents!=steps||completions!=steps))throw new AssertionError("Missing durable native step receipts");
        if(journal!=null){if(checkpointComplete!=requested||actionIntents.size()<requested*4||!actionIntents.keySet().equals(actionOutcomes.keySet()))throw new AssertionError("Native action/checkpoint count mismatch");for(String id:actionIntents.keySet()){Map<String,Object> a=actionIntents.get(id),b=actionOutcomes.get(id);String kind=(String)a.get("kind");if(!kind.equals(b.get("kind"))||!"native_hook_returned".equals(b.get("state")))throw new AssertionError("Native action mismatch/unknown");actionKinds.put(kind,actionKinds.getOrDefault(kind,0)+1);}for(String kind:List.of("feed","pick","align","release"))if(actionKinds.getOrDefault(kind,0)!=requested)throw new AssertionError("Native kind count mismatch");Map<?,?> state=call("openpnp_get_status",new JsonObject()),ledger=(Map<?,?>)state.get("native_action_ledger");if(!"completed".equals(state.get("job_state"))||!((List<?>)ledger.get("pending_actions")).isEmpty()||Boolean.TRUE.equals(ledger.get("durability_fault"))||Boolean.TRUE.equals(ledger.get("unresolved_action_fault")))throw new AssertionError("Job/ledger did not complete");}
        return Bridge.map("diagnostics_before",beforeDiagnostics,"diagnostics_after",afterDiagnostics,"durable_record_types",recordTypes,"native_complete_checkpoints",checkpointComplete,"matched_native_actions",actionIntents.size(),"native_action_kinds",actionKinds,"requested",requested,"placed",placed,"native_steps",steps,"seconds",seconds,"milliseconds_per_placement",seconds*1000/requested,"journal_bytes_added",journal==null?0:Files.size(journal)-journalBefore,"journal_native_step_intents",intents,"journal_native_step_completions",completions,"completion","native-standstill");
    }
    static Map<String,Object> capture(String mode)throws Exception {
        long start=System.nanoTime();int bytes;
        if(mode.equals("direct"))bytes=machine.execute(()->{BufferedImage image=machine.getDefaultHead().getDefaultCamera().settleAndCapture();ByteArrayOutputStream out=new ByteArrayOutputStream();ImageIO.write(image,"PNG",out);return out.size();},false,5000,30000);
        else bytes=((Number)((Map<?,?>)run("openpnp_capture_camera","mode","settled").get("result")).get("size")).intValue();
        return Bridge.map("seconds",(System.nanoTime()-start)/1e9,"png_bytes",bytes);
    }
    static JsonObject canonical(Part part,int count,String suffix) {
        double height=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();JsonArray parts=new JsonArray();parts.add(object("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height));
        JsonArray placements=new JsonArray();for(int i=0;i<count;i++)placements.add(object("ref","R"+i,"partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height,"x",1.5*(i%100),"y",1.5*(i/100),"z",0,"rotation",0,"side","top","enabled",true,"type","placement"));
        JsonObject board=object("id","benchmark-board","widthMm",150,"heightMm",150);board.add("placements",placements);JsonArray boards=new JsonArray();boards.add(board);
        JsonArray instances=new JsonArray();instances.add(object("id","benchmark-copy","kind","board","definitionId","benchmark-board","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true));
        JsonObject canonical=object("schemaVersion",1,"id","benchmark-"+suffix,"units","mm","coordinateConvention","openpnp-top-view");canonical.add("parts",parts);canonical.add("boards",boards);canonical.add("panels",new JsonArray());canonical.add("instances",instances);return canonical;
    }
    static JsonObject object(Object... pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    @SuppressWarnings("unchecked") static Map<String,Object> call(String method,JsonObject p)throws Exception{return (Map<String,Object>)bridge.call(method,p);}
    static Map<String,Object> run(String method,Object... pairs)throws Exception {
        JsonObject p=object("request_id",UUID.randomUUID().toString(),"session_id",session,"expected_config_revision",expectedRevision);for(Map.Entry<String,JsonElement> e:object(pairs).entrySet())p.add(e.getKey(),e.getValue());Map<String,Object> operation=call(method,p);String id=(String)operation.get("operation_id");long deadline=System.nanoTime()+300_000_000_000L;
        do{operation=call("openpnp_get_operation",object("operation_id",id));if("succeeded".equals(operation.get("state"))){while(machine.isBusy())Thread.sleep(1);return operation;}if(Arrays.asList("failed","aborted","outcome_unknown").contains(operation.get("state")))throw new AssertionError(operation);Thread.sleep(2);}while(System.nanoTime()<deadline);throw new AssertionError("Native benchmark timeout: "+operation);
    }
    static void refreshRevision()throws Exception {expectedRevision=(String)call("openpnp_get_status",new JsonObject()).get("config_revision");if(expectedRevision==null||!expectedRevision.matches("cfg-[0-9]+"))throw new AssertionError("Missing current revision");}
    @SuppressWarnings("unchecked") static Map<String,Object> diagnostics()throws Exception {
        if(bridge==null)return Map.of("available",false);
        try{return Bridge.map("available",true,"snapshot",bridge.getClass().getMethod("diagnosticTimingSnapshot").invoke(bridge));}
        catch(NoSuchMethodException absent){return Map.of("available",false);}
    }
    static String classHash(Class<?> type)throws Exception{try(InputStream in=type.getResourceAsStream("/"+type.getName().replace('.','/')+".class")){return sha(in.readAllBytes());}}
    static String sha(byte[] bytes)throws Exception{StringBuilder result=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))result.append(String.format(java.util.Locale.ROOT,"%02x",b));return result.toString();}
}
