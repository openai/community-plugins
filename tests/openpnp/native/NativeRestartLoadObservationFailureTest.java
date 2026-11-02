/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.Placement;
import static org.openpnp.codex.NativeReplacementContinuationTest.*;
import static org.openpnp.codex.NativeRestartLoadObservationTest.sizeField;
import static org.openpnp.codex.NativeRestartLoadObservationTest.field;
import static org.openpnp.codex.NativeRestartLoadObservationTest.boardState;
import org.openpnp.codex.NativeRestartSensingTaskTest.RestartFixture;
import org.openpnp.codex.NativeRestartSensingTaskTest.AdmissionEnv;
/** Own journal sink injects boundaries around actual FileChannel.force and child/root commits.
 * Native drift changes actual current native objects after that force; it cannot grant a permit. */
public final class NativeRestartLoadObservationFailureTest {
 static int assertions,refusals;static final List<Map<String,Object>> groups=new ArrayList<>();
 interface Checked{void run()throws Exception;}
 static void check(boolean value,String why){assertions++;if(!value)throw new AssertionError(why);}
 static void reject(String why,Checked work)throws Exception{try{work.run();throw new AssertionError("Accepted: "+why);}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){refusals++;check(true,why);}}
 static void same(Object a,Object b,String why)throws Exception{check(NativeFaultedJobReplacement.same(a,b),why);}
    static final class BoundaryEnv implements AutoCloseable {
        final NativeMaterialLoads material;final NativeBoardLoads boards;final NativeJobLineage lineage;final NativeFaultedJobReplacement replacement;
        final Path file;final FileChannel channel;final List<Map<String,Object>> history=new ArrayList<>();long sequence;String boundary;
        NativeFaultedJobReplacement.RestartStage stage;NativeRestartSensingTaskTest.Journal decision;boolean emptyBeforeForce,emptyAfterForce;String faultType,mode;Runnable drift;Map<String,Object> targeted;boolean forced,committed;
        BoundaryEnv(String name,List<Map<String,Object>> original)throws Exception{
            file=root.resolve(name+".jsonl");channel=FileChannel.open(file,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);lineage=new NativeJobLineage(this::append);boards=new NativeBoardLoads(this::append);material=new NativeMaterialLoads(config,this::append);replacement=new NativeFaultedJobReplacement(config,material,boards,lineage,this::append,operation->false);
            replay(original);history.addAll(original);sequence=original.size();
        }
        void replay(List<Map<String,Object>> events)throws Exception{for(Map<String,Object> event:events){String type=(String)event.get("type");Map<String,Object> p=o(event.get("payload"));Runnable observation=replacement.prepareContinuationObservation(type,p);material.recoverEvent(type,p);boards.recoverEvent(type,p);boards.observeNativeEvent(type,p);lineage.observe(type,p);replacement.observe(type,p);if(type.startsWith("faulted_job_replacement_"))replacement.recover(type,p);observation.run();}material.finishRecovery();boards.finishRecovery();}
        void append(String type,Map<String,Object> p)throws Exception{
            Runnable mat=material==null?()->{}:material.prepareEvent(type,p),lin=lineage==null||type.startsWith("job_lineage_")?()->{}:lineage.prepareObservation(type,p),observation=replacement==null?()->{}:replacement.prepareContinuationObservation(type,p);
            boolean fault=type.equals(faultType);if(fault){targeted=p;if("before".equals(mode))throw new IOException("Injected before observation force");}boolean target="faulted_job_replacement_restart_attachment".equals(type);if(target){emptyBeforeForce=((Number)replacement.status().get("retained_candidate_count")).intValue()==0;if("before".equals(boundary))throw new IOException("Injected before actual restart attachment force");}
            Map<String,Object> event=NativeFaultedJobReplacement.frozen(m("sequence",++sequence,"type",type,"payload",p));ByteBuffer buffer=ByteBuffer.wrap((JSON.toJson(event)+"\n").getBytes(StandardCharsets.UTF_8));while(buffer.hasRemaining())channel.write(buffer);channel.force(true);history.add(event);if(fault){forced=true;if("after".equals(mode))throw new IOException("Injected unknown return after observation force");}
            if(target){emptyAfterForce=((Number)replacement.status().get("retained_candidate_count")).intValue()==0;if("after".equals(boundary))throw new IOException("Injected unknown return after actual restart attachment force");if("drift".equals(boundary))stage.candidate().job().getBoardLocations().get(0).getBoard().getPlacements().get(0).setRank(977);if("revoke".equals(boundary))decision.permit.revoke();}
            mat.run();lin.run();observation.run();if(boards!=null)boards.observeNativeEvent(type,p);if(replacement!=null)replacement.observe(type,p);if(fault){committed=true;if("drift".equals(mode))drift.run();if("revoke".equals(mode))decision.permit.revoke();}
        }
        public void close()throws Exception{try{replacement.close();}finally{channel.close();}}
    }

 static void failedObservation(String kind,String mode)throws Exception {
  String name="observation-"+kind+"-"+mode;
  try(RestartFixture source=new RestartFixture(name+"-base","completed");BoundaryEnv e=new BoundaryEnv(name+"-fresh",source.source.events)){
   e.stage=task(()->e.replacement.stageRestart(source.attempt));var capture=task(()->e.replacement.captureRestart(e.stage));
   try(NativeRestartSensingTaskTest.Journal decision=new NativeRestartSensingTaskTest.Journal(name+"-decision")){
    e.decision=decision;decision.fault(capture);decision.admit();e.append("operation",m("operation_id",decision.operation,"request_id",id(),"request_digest",decision.capture.digest,"bridge_instance_id",decision.instanceId,"config_revision","cfg-2","method","local_native_sensing_reconciliation","task_id",decision.taskId,"reconciliation_request_id",decision.record.get("request_id"),"state","running"));
    var permit=task(()->e.replacement.beginRestart(capture,decision.coordinator,decision.permit,decision.owner));
    String oldId=(String)((List<?>)source.source.capture.dependencies().get("material_load_ids")).get(0);Map<String,Object> authority=task(()->permit.authorizeMaterial(e.material,oldId));ReferenceTrayFeeder feeder=(ReferenceTrayFeeder)config.getMachine().getFeeder((String)o(o(authority.get("phase_row")).get("old_load")).get("feeder_id"));Placement placement=permit.candidate().job().getBoardLocations().get(0).getBoard().getPlacements().get(0);int initialIndex=feeder.getFeedCount(),initialRank=placement.getRank();
    Map<String,Object> material=e.material.captureReplacementState(),boards=boardState(e.boards);Map<String,Integer> counts=counters();Map<String,String> files=configurationFiles();Map<String,Boolean> oldHistory=new TreeMap<>(permit.original().getPlacedStatusSnapshot());long bytes=Files.size(e.file);int prior=e.history.size();
    e.faultType=kind+"_restart_observation";e.mode=mode;e.drift=kind.equals("material")?()->feeder.setFeedCount(initialIndex+1):()->placement.setRank(initialRank+1);
    task(()->{reject("actual "+kind+" observation "+mode+" boundary",()->{if(kind.equals("material"))e.material.observeRestart(permit,oldId);else e.boards.observeRestart(permit,permit.original(),permit.candidate().job(),source.attempt);});return null;});
    check(e.targeted!=null,"Boundary reached the actual typed observation sink");check(e.forced==!mode.equals("before"),"Force receipt distinguishes before/after boundary");check(e.committed==Set.of("drift","revoke").contains(mode),"Actual reducer commit boundary recorded");
    check(Files.size(e.file)>bytes==e.forced,"Only a reached force appends actual journal bytes");check(e.history.size()-prior==(e.forced?1:0),"At most one actual observation record forced");
    check(sizeField(e.material,"restartWitnesses")==0&&sizeField(e.boards,"restartWitnesses")==0,"Failed observation installs no process-local witness");check(sizeField(e.material,"bindings")==0&&field(e.boards,"boundNativeJob")==null&&((Set<?>)field(e.boards,"confirmed")).isEmpty(),"Failed publication cannot create ordinary load readiness");
    same(material,e.material.captureReplacementState(),"Old material/feed/current load facts unchanged");Map<String,Object> afterBoard=boardState(e.boards);if(kind.equals("board")&&Set.of("before","after").contains(mode))afterBoard.put("restart_observation_publication_uncertain",false);same(boards,afterBoard,"Old board/history/presence facts unchanged");same(oldHistory,permit.original().getPlacedStatusSnapshot(),"Old native history unchanged");same(files,configurationFiles(),"Failed observation never saves native configuration");
    if(mode.equals("drift")){check(kind.equals("material")?feeder.getFeedCount()==initialIndex+1:placement.getRank()==initialRank+1,"Post-force drift changed actual native object");task(()->{feeder.setFeedCount(initialIndex);placement.setRank(initialRank);return null;});}same(counts,counters(),"Only explicit test drift changed native counters");
    String receipt=(String)e.targeted.get("receipt_id");Map<String,Object> stored=kind.equals("material")?e.material.restartObservationReceipt(receipt):e.boards.restartObservationReceipt(receipt);check((stored!=null)==e.committed,"No child commit falsely inferred from force alone");
    task(()->{if(kind.equals("material")&&mode.equals("drift")){permit.requireSealedMaterial(e.material,oldId,receipt);check(true,"Root retains accepted forced fact; failed child post-force check still installs no witness");}else reject("unaccepted observation has no sealed root fact",()->{if(kind.equals("material"))permit.requireSealedMaterial(e.material,oldId,receipt);else permit.requireSealedBoards(e.boards,permit.original(),permit.candidate().job(),source.attempt,receipt);});reject("failed observation cannot provide ordinary material authority",e.material::requireReady);reject("failed observation cannot provide board authority",()->e.boards.requireReady(permit.candidate().job()));return null;});
    if(Set.of("before","after").contains(mode)){Map<String,Object> snapshot=kind.equals("material")?e.material.snapshot():e.boards.snapshot();check(Boolean.TRUE.equals(snapshot.get("restart_observation_publication_uncertain")),"Sink uncertainty fences future observation publication");e.mode=null;e.faultType=null;task(()->{reject("fenced observation cannot be retried in this owner",()->{if(kind.equals("material"))e.material.observeRestart(permit,oldId);else e.boards.observeRestart(permit,permit.original(),permit.candidate().job(),source.attempt);});return null;});}
    long endBytes=Files.size(e.file);try(AdmissionEnv replay=new AdmissionEnv(name+"-history",new ArrayList<>(e.history))){Map<String,Object> read=kind.equals("material")?replay.material.restartObservationReceipt(receipt):replay.boards.restartObservationReceipt(receipt);check((read!=null)==e.forced,"Replay reports exact forced observation despite unknown append return");if(read!=null)same(e.targeted,read,"Replay preserves exact observation facts");check(sizeField(replay.material,"restartWitnesses")==0&&sizeField(replay.boards,"restartWitnesses")==0&&sizeField(replay.material,"bindings")==0&&field(replay.boards,"boundNativeJob")==null,"Replay of forced-but-failed observation grants no binding/witness");check(Files.size(replay.file)==0,"Historical replay appends no bytes");}check(endBytes==Files.size(e.file),"Failure verification appends no original-journal bytes");
    permit.close();groups.add(m("name",name,"actual_forced",e.forced,"actual_committed",e.committed,"receipt",e.targeted,"native_witness_installed",false,"ordinary_execution_authority_restored",false));
   }
  }
 }
 public static void main(String[] args)throws Exception{if(args.length!=1)throw new IllegalArgumentException("Expected exclusive native state directory");initialize(Path.of(args[0]));Throwable failure=null;int exit=0;try{for(String kind:List.of("material","board"))for(String mode:List.of("before","after","revoke","drift"))failedObservation(kind,mode);}catch(Throwable error){failure=error;exit=1;error.printStackTrace();}finally{config.getMachine().close();Map<String,Object> proof=m("passed",exit==0,"assertions",assertions,"refusals",refusals,"groups",groups,"error",failure==null?null:failure.toString(),"pid",ProcessHandle.current().pid(),"scope","Actual native reconstructed graph and tray observations, local RestartPermit, real FileChannel force boundary injection and native object drift; same-JVM replay, synthetic initial fault/task decision/terminal authority","actual_native_job_initializations",0,"actual_native_placements",0,"restart_execution_qualified",false,"bridge_integration_qualified",false,"hardware_qualified",false);Files.writeString(root.resolve("proof.json"),JSON.toJson(proof)+"\n",StandardOpenOption.CREATE_NEW);System.out.println("NATIVE_RESTART_LOAD_OBSERVATION_FAILURE_RESULT "+JSON.toJson(proof));}System.exit(exit);}
}
