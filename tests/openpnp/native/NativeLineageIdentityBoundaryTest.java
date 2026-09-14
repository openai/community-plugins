/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.Gson;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.ReferenceMachine;

/** Actual native existing identities + reducer-only controlled checkpoint boundary.
 * No processor, feeding, placement, movement, HTTP or TCP operation is executed. */
public final class NativeLineageIdentityBoundaryTest {
 static final Gson G=new Gson();static int checks;static final List<Object> results=new ArrayList<>();
 static void check(boolean v,String m){checks++;if(!v)throw new AssertionError(m);}
 interface Checked{void run()throws Exception;}
 static void fail(String code,Checked run)throws Exception{try{run.run();throw new AssertionError("Expected "+code);}catch(NativeJobLineage.Fault e){check(code.equals(e.code),"expected exact reducer boundary fault");}}
 static Map<String,Object> m(Object...v){Map<String,Object> out=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)out.put((String)v[i],v[i+1]);return out;}
 static final class Journal implements AutoCloseable{
  final Path path;final FileChannel file;final NativeJobLineage lineage;final NativeBoardLoads loads;long sequence;
  Journal(Path p)throws Exception{path=p;file=FileChannel.open(p,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);lineage=new NativeJobLineage(this::write);loads=new NativeBoardLoads(this::observed);}
  void write(String type,Map<String,Object> payload)throws Exception{ByteBuffer bytes=ByteBuffer.wrap((G.toJson(m("sequence",++sequence,"type",type,"payload",payload))+"\n").getBytes(StandardCharsets.UTF_8));while(bytes.hasRemaining())file.write(bytes);file.force(true);}
  void observed(String type,Map<String,Object> payload)throws Exception{Runnable commit=lineage.prepareObservation(type,payload);write(type,payload);commit.run();}
  @SuppressWarnings("unchecked")NativeJobLineage replay()throws Exception{NativeJobLineage recovered=new NativeJobLineage((t,p)->{throw new AssertionError("no effect during replay");});for(String line:Files.readAllLines(path)){Map<String,Object> e=G.fromJson(line,Map.class);recovered.observe((String)e.get("type"),(Map<String,Object>)e.get("payload"));}return recovered;}
  public void close()throws Exception{file.close();}
 }
 public static void main(String[]args)throws Exception{
  Path root=Files.createTempDirectory("native-lineage-id-boundary-");boolean fixed=true;Configuration.initialize(root.resolve("config").toFile());Configuration.get().setMachine(new ReferenceMachine());
  for(int length:new int[]{128,129,512,513}){
   String ref="R"+"x".repeat(length-1),jobId=UUID.randomUUID().toString();Job job=new Job();Board board=new Board();board.setDimensions(new Location(LengthUnit.Millimeters,20,20,0,0));Placement placement=new Placement(ref);placement.setLocation(new Location(LengthUnit.Millimeters,3,4,0,0));board.addPlacement(placement);BoardLocation loc=new BoardLocation(board);loc.setId("B1");job.addBoardOrPanelLocation(loc);PanelLocation.setParentsOfAllDescendants(job.getRootPanelLocation());
   try(Journal j=new Journal(root.resolve("reference-"+length+".jsonl"))){
    j.lineage.createFresh(jobId,"pinned-sample-simulator");long created=j.sequence;
    if(length==513){try{j.loads.bindJob(job,jobId,true);throw new AssertionError("Native512 limit must reject513");}catch(IllegalArgumentException expected){check(j.sequence==created,"native oversized identity rejects before load append");}results.add(m("reference_length",length,"native_identity_admitted",false,"checkpoint_attempted",false));continue;}
    j.loads.bindJob(job,jobId,true);check(j.lineage.requirePristine(jobId,null).loadIds.size()==1,"actual native load accepts original identity contract");
    Map<?,?> rootRow=(Map<?,?>)((List<?>)j.loads.snapshot().get("roots")).get(0);Map<?,?> boardRow=(Map<?,?>)((List<?>)rootRow.get("boards")).get(0);
    // This is an explicit synthetic checkpoint input for reducer validation, not
    // evidence that OpenPnP physically or operationally placed this component.
    Map<String,Object> checkpoint=m("state","native-placement-complete-hook","context",m("native_placed_status",true,"board_load_id",rootRow.get("load_id"),"board_instance_id",loc.getUniqueId(),"loaded_board_id",boardRow.get("loaded_board_id"),"placement_id",ref));
    long bytes=Files.size(j.path);
    if(!fixed&&length>128){fail("LINEAGE_RECORD",()->j.observed("native_placement_checkpoint",checkpoint));check(Files.size(j.path)==bytes,"mismatch rejects before checkpoint append");check(j.replay().requirePristine(jobId,null).loadIds.size()==1,"earlier accepted native identity survives reducer replay");results.add(m("reference_length",length,"native_identity_admitted",true,"checkpoint_rejected",true,"fault","LINEAGE_RECORD"));}
    else{j.observed("native_placement_checkpoint",checkpoint);j.loads.observeNativeEvent("native_placement_checkpoint",checkpoint);j.loads.publishSnapshot();fail("LINEAGE_HISTORY_PRESENT",()->j.lineage.requirePristine(jobId,null));fail("LINEAGE_HISTORY_PRESENT",()->j.replay().requirePristine(jobId,null));Map<?,?> load=(Map<?,?>)((List<?>)j.loads.snapshot().get("loads")).get(0);check(((Number)load.get("native_history_entries")).intValue()==1,"native load also retains one complete history identity");check(Files.readString(j.path).contains(ref),"forced checkpoint retains full existing reference without truncation");results.add(m("reference_length",length,"native_identity_admitted",true,"checkpoint_rejected",false,"history_retained_after_replay",true));}
   }
  }
  Configuration.get().getMachine().close();Map<String,Object> report=m("passed",true,"corrected_bound",fixed,"assertions",checks,"cases",results,"checkpoint_input","controlled reducer fixture; not emitted by job processor","native_existing_models_used",true,"actual_processor_calls",0,"feed_calls",0,"physical_qualification",false);Files.writeString(root.resolve("result.json"),G.toJson(report)+"\n");System.out.println(G.toJson(report));System.exit(0);
 }
}
