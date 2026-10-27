/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.beans.PropertyChangeListener;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.*;

/** Native boards, real processor/feed/vision, durable load events and bounded replay.
 * In-process replay and listener fault injection are not a power-loss or physical-loading test.
 */
public final class NativeBoardLoadsTest {
    private static final Gson GSON=new Gson();private static final List<String> passed=new ArrayList<>();
    private static NativeBoardLoads loads;private static Recorder recorder;private static Job job;private static long run;
    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-board-loads-");Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);
        Machine machine=config.getMachine();int code=0;
        try {
            recorder=new Recorder(root.resolve("loads.jsonl"));loads=new NativeBoardLoads(recorder::append);
            machine.submit(()->{job=CanonicalJobImporter.load(config,canonical(config.getPart("R0603-1K")));loads.bindJob(job,"test-job-1",true);machine.setEnabled(true);machine.home();return null;},null,true).get(30,TimeUnit.SECONDS);
            Map<?,?> initial=loads.snapshot();List<?> current=(List<?>)initial.get("roots");check(current.size()==2,"two actual native root instances are independently bound");
            String a=job.getBoardLocations().get(0).getUniqueId(),b=job.getBoardLocations().get(1).getUniqueId();String loadA=id(a),loadB=id(b);
            check(!loadA.equals(loadB),"shared definition/repeated designator receives distinct simulator load identities");
            Map<String,Object> scope=loads.ledgerScope(job);List<?> identities=(List<?>)scope.get("boards");check(!((Map<?,?>)identities.get(0)).get("loaded_board_id").equals(((Map<?,?>)identities.get(1)).get("loaded_board_id")),"repeated R1 designators have different loaded board identities");
            execute(machine);check(feedCount(machine)==2,"actual native processor placed and fed two top-side parts");
            check(job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1")&&job.retrievePlacedStatus(job.getBoardLocations().get(1),"R1"),"native top-side placed states are retained per root");
            String oldRevision=loads.revision();long size=Files.size(recorder.path);
            expect("BOARD_LOAD_REVISION_CONFLICT",()->loads.change(job,a,"flip","bottom",loadA,"load-0",false));check(Files.size(recorder.path)==size&&job.getBoardLocations().get(0).getGlobalSide()==Side.Top,"stale load plan changes neither journal nor native side");
            machine.submit(()->{loads.change(job,a,"flip","bottom",loadA,oldRevision,false);return null;},null,false).get(30,TimeUnit.SECONDS);
            check(id(a).equals(loadA)&&id(b).equals(loadB),"flip preserves both the flipped board identity and other root identity");
            check(job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1")&&job.getBoardLocations().get(0).getGlobalSide()==Side.Bottom,"flip preserves completed top history while exposing native bottom side");
            check(job.getBoardLocations().get(0).getPlacementsTransformStatus()==PlacementsHolderLocation.PlacementsTransformStatus.NotSet,"flip invalidates the native registration transform");
            execute(machine);check(feedCount(machine)==3&&job.retrievePlacedStatus(job.getBoardLocations().get(0),"R2"),"real bottom-side run adds one feed and placement without repeating completed top-side work");
            String beforeReplace=loads.revision();machine.submit(()->{loads.change(job,a,"replace","bottom",loadA,beforeReplace,false);return null;},null,false).get(30,TimeUnit.SECONDS);
            check(!id(a).equals(loadA)&&id(b).equals(loadB),"replacement creates a distinct ledger and leaves another physical-simulated board alone");
            check(!job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1")&&!job.retrievePlacedStatus(job.getBoardLocations().get(0),"R2")&&job.retrievePlacedStatus(job.getBoardLocations().get(1),"R1"),"replacement clears only selected subtree's native history");
            execute(machine);check(feedCount(machine)==4,"replacement consumes exactly one new native bottom-side part");
            Map<?,?> retired=find(loadA);check(((Number)retired.get("placed_history_count")).intValue()==2,"retired load retains both top and bottom completion history");
            byte[] prefix=Files.readAllBytes(recorder.path);NativeBoardLoads reopened=replay(recorder.path);reopened.bindJob(job,"test-job-reopened",false);
            expect("BOARD_LOAD_REQUIRED",()->reopened.requireReady(job));check(Arrays.equals(prefix,Files.readAllBytes(recorder.path)),"restart refuses implicit board presence and does not alter durable bytes");
            loads=reopened;String replacement=id(a);
            machine.submit(()->{loads.change(job,a,"same-load","bottom",replacement,loads.revision(),false);loads.change(job,b,"same-load","top",loadB,loads.revision(),false);loads.requireReady(job);return null;},null,false).get(30,TimeUnit.SECONDS);
            check(id(a).equals(replacement)&&feedCount(machine)==4,"explicit same-load reattachment preserves IDs/history and performs no feed");
            PropertyChangeListener fault=event->{if("side".equals(event.getPropertyName()))throw new IllegalStateException("intentional native post-side-change fault");};BoardLocation first=job.getBoardLocations().get(0);first.addPropertyChangeListener(fault);
            try {machine.submit(()->{loads.change(job,a,"flip","top",replacement,loads.revision(),false);return null;},null,false).get(30,TimeUnit.SECONDS);throw new AssertionError("missing injected failure");}catch(java.util.concurrent.ExecutionException expected){check(expected.getCause().getMessage().contains("intentional"),"actual native side setter throws after durable load intent");}finally{first.removePropertyChangeListener(fault);}
            check(loads.hasActiveUnknown()&&first.getGlobalSide()==Side.Top,"incomplete native change remains loading_unknown despite the changed model");
            loads=replay(recorder.path);loads.bindJob(job,"test-job-after-interruption",false);
            expect("BOARD_LOAD_UNKNOWN",()->loads.change(job,a,"same-load","top",replacement,loads.revision(),false));
            check(feedCount(machine)==4&&((Number)loads.snapshot().get("pending_changes")).intValue()==1,"restart preserves unresolved loading intent and never replays a native feed");
            check(Boolean.FALSE.equals(loads.snapshot().get("physical_load_verified")),"agent input cannot make simulator loading physically verified");
            recorder.close();recorder=new Recorder(root.resolve("nested-loads.jsonl"));loads=new NativeBoardLoads(recorder::append);
            machine.submit(()->{job=CanonicalJobImporter.load(config,nested(config.getPart("R0603-1K")));loads.bindJob(job,"nested-native-job",true);return null;},null,false).get(30,TimeUnit.SECONDS);
            Map<?,?> panelRoot=(Map<?,?>)((List<?>)loads.snapshot().get("roots")).get(0);String panelId=(String)panelRoot.get("root_instance_id"),panelLoad=(String)panelRoot.get("load_id");List<?> children=(List<?>)panelRoot.get("boards");
            check(children.size()==2&&((Map<?,?>)children.get(0)).get("board_instance_id").toString().contains("A")&&((Map<?,?>)children.get(1)).get("board_instance_id").toString().contains("B"),"actual native panel maps two child identities independently of repeated designators");
            Set<String> beforeBoards=new HashSet<>();for(Object raw:children)beforeBoards.add((String)((Map<?,?>)raw).get("loaded_board_id"));
            execute(machine);check(feedCount(machine)==6,"native panel executes one top and one globally bottom child placement");
            machine.submit(()->{loads.change(job,panelId,"flip","bottom",panelLoad,loads.revision(),false);return null;},null,false).get(30,TimeUnit.SECONDS);
            Set<String> afterBoards=new HashSet<>();for(Object raw:(List<?>)((Map<?,?>)((List<?>)loads.snapshot().get("roots")).get(0)).get("boards"))afterBoards.add((String)((Map<?,?>)raw).get("loaded_board_id"));
            check(beforeBoards.equals(afterBoards)&&id(panelId).equals(panelLoad),"panel flip preserves its panel load and every child board identity");
            check(job.getBoardLocations().get(0).getGlobalSide()==Side.Bottom&&job.getBoardLocations().get(1).getGlobalSide()==Side.Top,"native parent flip composes child-side transforms including double reflection");
            execute(machine);check(feedCount(machine)==8&&((Number)find(panelLoad).get("placed_history_count")).intValue()==4,"flipped native panel completes opposite child sides while preserving four distinct placement-history keys");
            System.out.println("OPENPNP_NATIVE_BOARD_LOADS_RESULT "+GSON.toJson(Bridge.map("passed",passed,"assertions",passed.size(),"native_feed_effects",feedCount(machine),"completed_native_placements",8,"simulation_only",true,"physical_loading_verified",false,"process_crash_test",false,"journal",recorder.path.toString())));
        }catch(Throwable e){e.printStackTrace();code=1;}finally{if(recorder!=null)recorder.close();machine.close();}System.exit(code);
    }
    private static void execute(Machine machine)throws Exception {machine.submit(()->{loads.requireReady(job);Map<String,Object> scope=loads.ledgerScope(job);NativeActionLedger ledger=new NativeActionLedger((type,payload)->{recorder.append(type,payload);loads.observeNativeEvent(type,payload);},"load-test-op-"+(++run),"test-job-1","cfg-1",(String)scope.get("scope_id"),job,scope);machine.getPnpJobProcessor().initialize(job);boolean more;long step=0;do{try(NativeActionLedger.StepScope s=ledger.openStep(++step)){more=machine.getPnpJobProcessor().next();s.complete();}}while(more);machine.getMotionPlanner().waitForCompletion(null,MotionPlanner.CompletionType.WaitForStillstand);loads.checkpoint(job);loads.publishSnapshot();return null;},null,false).get(60,TimeUnit.SECONDS);}
    private static NativeBoardLoads replay(Path file)throws Exception {NativeBoardLoads copy=new NativeBoardLoads(recorder::append);for(String line:Files.readAllLines(file)){Map<String,Object> event=GSON.fromJson(line,Map.class);String type=(String)event.get("type");Map<String,Object> payload=(Map<String,Object>)event.get("payload");copy.recoverEvent(type,payload);copy.observeNativeEvent(type,payload);}copy.finishRecovery();return copy;}
    private static String id(String root){for(Object raw:(List<?>)loads.snapshot().get("roots")){Map<?,?> x=(Map<?,?>)raw;if(root.equals(x.get("root_instance_id")))return (String)x.get("load_id");}throw new AssertionError(root);}
    private static Map<?,?> find(String id){for(Object raw:(List<?>)loads.snapshot().get("loads")){Map<?,?> x=(Map<?,?>)raw;if(id.equals(x.get("load_id")))return x;}throw new AssertionError(id);}
    private static int feedCount(Machine m){int n=0;for(Feeder f:m.getFeeders())if(f instanceof ReferenceTrayFeeder)n+=((ReferenceTrayFeeder)f).getFeedCount();return n;}
    interface Checked{void run()throws Exception;}
    private static void expect(String code,Checked call)throws Exception{try{call.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"native load refuses "+code);}}
    private static void check(boolean condition,String name){if(!condition)throw new AssertionError(name);passed.add(name);}
    static JsonObject canonical(Part part){JsonObject job=object("schemaVersion",1,"id","board-load-fixture","units","mm","coordinateConvention","openpnp-top-view");JsonArray parts=new JsonArray();parts.add(object("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue()));job.add("parts",parts);JsonObject board=object("id","shared","widthMm",20,"heightMm",20);JsonArray placements=new JsonArray();placements.add(object("ref","R1","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue(),"x",5,"y",5,"z",0,"rotation",0,"side","top","type","placement","enabled",true));placements.add(object("ref","R2","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue(),"x",6,"y",6,"z",0,"rotation",0,"side","bottom","type","placement","enabled",true));board.add("placements",placements);JsonArray boards=new JsonArray();boards.add(board);job.add("boards",boards);job.add("panels",new JsonArray());JsonArray instances=new JsonArray();instances.add(object("id","A","kind","board","definitionId","shared","x",20,"y",20,"z",0,"rotation",0,"side","top","enabled",true));instances.add(object("id","B","kind","board","definitionId","shared","x",50,"y",20,"z",0,"rotation",0,"side","top","enabled",true));job.add("instances",instances);return job;}
    static JsonObject nested(Part part){JsonObject input=canonical(part);JsonArray children=input.getAsJsonArray("instances");children.get(1).getAsJsonObject().addProperty("side","bottom");JsonObject panel=object("id","panel-definition","widthMm",100,"heightMm",80);panel.add("children",children);JsonArray panels=new JsonArray();panels.add(panel);input.add("panels",panels);JsonArray roots=new JsonArray();roots.add(object("id","P","kind","panel","definitionId","panel-definition","x",10,"y",20,"z",0,"rotation",0,"side","top","enabled",true));input.add("instances",roots);return input;}
    static JsonObject object(Object... fields){return GSON.toJsonTree(Bridge.map(fields)).getAsJsonObject();}
    static final class Recorder implements AutoCloseable{final Path path;final FileChannel channel;long sequence;Recorder(Path p)throws Exception{path=p;channel=FileChannel.open(p,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);}void append(String type,Map<String,Object> payload)throws Exception{byte[] b=(GSON.toJson(Bridge.map("sequence",++sequence,"type",type,"payload",payload))+"\n").getBytes(StandardCharsets.UTF_8);ByteBuffer buffer=ByteBuffer.wrap(b);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);}public void close()throws Exception{channel.close();}}
}
