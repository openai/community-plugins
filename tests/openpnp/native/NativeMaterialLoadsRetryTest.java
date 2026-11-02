/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.beans.PropertyChangeListener;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.*;

/** One real native feed increments then throws; no placement or physical hardware. */
public final class NativeMaterialLoadsRetryTest {
 public static void main(String[] args)throws Exception{
  Path root=(args.length>1?Paths.get(args[1]):Files.createTempDirectory("material-load-native-"));Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);ReferenceTrayFeeder tray=null;for(Feeder f:config.getMachine().getFeeders())if(f.getPart()==config.getPart("R0603-1K"))tray=(ReferenceTrayFeeder)f;tray.setTrayCountX(2);tray.setTrayCountY(2);tray.setFeedCount(0);tray.setFeedRetryCount(3);config.save();
  Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());Bridge bridge=null;int code=0;List<String> checks=new ArrayList<>();
  try{
   bridge=new Bridge(config,token,root.resolve("journal"),Paths.get(args[0]),0,true,"sustained-workload");NativeMaterialLoadsWorkflowTest.bridge=bridge;NativeMaterialLoadsWorkflowTest.config=config;NativeMaterialLoadsWorkflowTest.grant();
   NativeMaterialLoadsWorkflowTest.mutate("openpnp_register_material_load","feeder_id",tray.getId(),"part_id",tray.getPart().getId(),"expected_geometry_sha256",NativeMaterialLoads.describe(tray).get("geometry_sha256"),"expected_material_revision","material-0","action","bind-existing");NativeMaterialLoadsWorkflowTest.mutate("openpnp_set_machine_enabled","enabled",true);NativeMaterialLoadsWorkflowTest.mutate("openpnp_home_machine");String job=(String)NativeMaterialLoadsWorkflowTest.mutate("openpnp_prepare_job","canonical_job",NativeMaterialLoadsWorkflowTest.canonical(1,"fault")).get("job_id");NativeMaterialLoadsWorkflowTest.mutate("openpnp_validate_job");
   PropertyChangeListener failure=e->{if("feedCount".equals(e.getPropertyName())&&((Number)e.getNewValue()).intValue()==1)throw new IllegalStateException("intentional post-index native feed failure");};tray.addPropertyChangeListener(failure);Map<String,Object> op;try{op=NativeMaterialLoadsWorkflowTest.operation("openpnp_start_job","job_id",job);}finally{tray.removePropertyChangeListener(failure);}
   check("outcome_unknown".equals(op.get("state")),checks,"native post-feed exception settles unknown");check(tray.getFeedCount()==1,checks,"configured three retries do not advance a second native tray slot");Map<String,Object> state=NativeMaterialLoadsWorkflowTest.call("openpnp_get_material_loads");Map<?,?> load=(Map<?,?>)((List<?>)state.get("loads")).get(0);check(((Number)load.get("current_index")).intValue()==0&&((Number)load.get("observed_advances")).intValue()==0,checks,"unmatched after-hook never invents a committed consumption observation");check(((List<?>)state.get("pending_feeds")).size()==1,checks,"original material load and before-index remain explicitly unknown");
   int feedIntents=0,feedOutcomes=0,otherActions=0,placed=0;for(String line:Files.readAllLines(root.resolve("journal/operations.jsonl"))){Map<?,?> event=NativeMaterialLoadsWorkflowTest.G.fromJson(line,Map.class);Map<?,?> p=(Map<?,?>)event.get("payload");if("native_action_intent".equals(event.get("type"))){if("feed".equals(p.get("kind")))feedIntents++;else otherActions++;}if("native_action_outcome".equals(event.get("type"))&&"feed".equals(p.get("kind")))feedOutcomes++;if("native_placement_checkpoint".equals(event.get("type"))&&"native-placement-complete-hook".equals(p.get("state")))placed++;}
   check(feedIntents==1&&feedOutcomes==0&&otherActions==0&&placed==0,checks,"one durable feed intent, no repeated feed, pick, release, cleanup action or placement");
   JsonObject p=NativeMaterialLoadsWorkflowTest.params("feeder_id",tray.getId(),"part_id",tray.getPart().getId(),"expected_geometry_sha256",NativeMaterialLoads.describe(tray).get("geometry_sha256"),"expected_material_revision","material-1","expected_load_id",load.get("load_id"),"action","replace-full-tray");try{bridge.call("openpnp_register_material_load",p);throw new AssertionError("unknown was bypassed");}catch(Bridge.Fault e){check("RECOVERY_REQUIRED".equals(e.code),checks,"Bridge rejects changeover while the native operation remains unknown");}
   bridge.close();bridge=new Bridge(config,token,root.resolve("journal"),Paths.get(args[0]),0,true,"sustained-workload");Map<?,?> after=(Map<?,?>)bridge.call("openpnp_get_material_loads",new JsonObject());check(tray.getFeedCount()==1&&((List<?>)after.get("pending_feeds")).size()==1,checks,"same-journal replay preserves unknown without a reset or feed");Map<?,?> replayOp=(Map<?,?>)bridge.call("openpnp_get_operation",NativeMaterialLoadsWorkflowTest.object("operation_id",op.get("operation_id")));check("outcome_unknown".equals(replayOp.get("state")),checks,"original operation identity remains unknown on restart");
   Files.writeString(root.resolve("observations.json"),NativeMaterialLoadsWorkflowTest.G.toJson(Bridge.map("operation",op,"material",state,"recovered_material",after,"recovered_operation",replayOp)));
   System.out.println("MATERIAL_LOAD_RETRY_RESULT "+NativeMaterialLoadsWorkflowTest.G.toJson(Bridge.map("passed",true,"assertions",checks.size(),"checks",checks,"native_feed_index_advances",1,"native_placements",0,"native_feed_intents",feedIntents,"native_feed_outcomes",feedOutcomes,"configured_feed_retry_count",3,"independent_physical_effect_observation",false,"simulation_only",true)));
  }catch(Throwable t){t.printStackTrace();code=1;}finally{try{if(bridge!=null)bridge.close();}catch(Throwable t){t.printStackTrace();code=1;}try{config.getMachine().close();}catch(Throwable t){t.printStackTrace();code=1;}}System.exit(code);
 }
 static void check(boolean ok,List<String> checks,String name){if(!ok)throw new AssertionError(name);checks.add(name);}
}
