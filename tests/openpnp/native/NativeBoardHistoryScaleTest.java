/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.Gson;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;

/** Native Job history and real forced load journal only. These 10k flags are model stimuli,
 * not 10k native placement or feed claims. Guards/serialization must preserve all opaque keys. */
public final class NativeBoardHistoryScaleTest {
 static final List<String> checks=new ArrayList<>();
 static void check(boolean ok,String text){if(!ok)throw new AssertionError(text);checks.add(text);}
 public static void main(String[] args)throws Exception {
  Path root=Files.createTempDirectory("native-board-history-scale-");Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();int exit=0;
  try(NativeBoardLoadsTest.Recorder recorder=new NativeBoardLoadsTest.Recorder(root.resolve("history.jsonl"))) {
   NativeBoardLoads loads=new NativeBoardLoads(recorder::append);Job job=CanonicalJobImporter.load(config,NativeBoardLoadsTest.canonical(config.getPart("R0603-1K")));BoardLocation board=job.getBoardLocations().get(0);loads.bindJob(job,"scale-model-job",true);int previous=0;
   for(int count:new int[]{2000,3000,3050,10000}) {
    for(int i=previous;i<count;i++)job.storePlacedStatus(board,"R"+i,true);Map<String,Boolean> nativeHistory=job.getPlacedStatusSnapshot();check(nativeHistory.size()==count,"native history has exact "+count+" records");
    loads.requireReady(job);loads.checkpoint(job);loads.requireReady(job);loads.ledgerScope(job);
    byte[] committed=Files.readAllBytes(recorder.path);loads.checkpoint(job);loads.requireReady(job);check(Arrays.equals(committed,Files.readAllBytes(recorder.path)),"repeated "+count+" checkpoint changes no bytes");
    Map<String,Object> latest=null;for(String line:Files.readAllLines(recorder.path)){Map<String,Object> e=NativeJournalJson.parseObject(line);if("board_load_history".equals(e.get("type"))){Map<String,Object> record=(Map<String,Object>)((Map<?,?>)e.get("payload")).get("record");if(board.getUniqueId().equals(record.get("root_instance_id")))latest=record;}}
    Map<?,?> retained=(Map<?,?>)latest.get("placed_history");check(retained.size()==count&&retained.equals(nativeHistory),"all "+count+" native keys/values round trip through journal");
    for(int i=0;i<count;i++){String key=board.getUniqueId()+PlacementsHolderLocation.ID_DELIMITTER+"R"+i;if(!Boolean.TRUE.equals(retained.get(key)))throw new AssertionError("Lost lookup "+key);}check(true,"all "+count+" retained key lookups succeed, including collision-prone designators");
    job.removePlacedStatus(board,"R1079");try{loads.requireReady(job);throw new AssertionError("removed large-history key admitted");}catch(Bridge.Fault expected){check("BOARD_LOAD_HISTORY_MISMATCH".equals(expected.code),"removed key rejects at scale "+count);}job.storePlacedStatus(board,"R1079",true);loads.requireReady(job);check(Arrays.equals(committed,Files.readAllBytes(recorder.path)),"scale rejection/restoration has no journal write");previous=count;
   }
   check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"scale fixture remains disabled/unhomed without machine effects");System.out.println("NATIVE_BOARD_HISTORY_SCALE_RESULT "+new Gson().toJson(Bridge.map("passed",true,"assertions",checks.size(),"checks",checks,"max_native_history_records",10000,"actual_native_placements",0,"actual_native_feeds",0,"model_only_history_stimuli",true,"physical_qualification",false)));
  }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
 }
}
