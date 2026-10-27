/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.machine.reference.ReferencePnpJobProcessor;

/** Bounded real native workload; count is explicit, finite inventory is never reset. */
public final class NativeSustainedTest {
    static Bridge bridge;static String session;static final Gson GSON=new Gson();
    public static void main(String[]args)throws Exception {
        int count=args.length>1?Integer.parseInt(args[1]):100;if(count<1||count>10000)throw new IllegalArgumentException("Count must be 1..10000");
        Path root=Files.createTempDirectory("openpnp-native-sustained-");Path config=root.resolve("config");Files.createDirectory(config);
        Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());
        Configuration.initialize(config.toFile());Configuration.get().load();SimulatorMain.accelerateFixture(Configuration.get());
        ReferencePnpJobProcessor processor=(ReferencePnpJobProcessor)Configuration.get().getMachine().getPnpJobProcessor();
        if(processor.getJobOrder()!=ReferencePnpJobProcessor.JobOrderHint.NozzleTips)throw new AssertionError("default native job order changed");
        SimulatorMain.configureSustainedWorkload(Configuration.get());
        if(processor.getJobOrder()!=ReferencePnpJobProcessor.JobOrderHint.Unsorted||!Files.readString(config.resolve("machine.xml")).contains("job-order=\"Unsorted\""))throw new AssertionError("sustained native order not set/persisted");
        bridge=new Bridge(Configuration.get(),token,root.resolve("journal"),Paths.get(args[0]),0,true,"sustained-workload");int exitCode=0;long started=System.nanoTime();
        try {
            if(!"Unsorted".equals(call("openpnp_get_capabilities",new JsonObject()).get("native_job_order")))throw new AssertionError("actual job order missing from capabilities");
            session=(String)call("openpnp_request_control_session",object("request_id","sustained-grant","ttl_seconds",600)).get("session_id");
            run("openpnp_set_machine_enabled",10000,"enabled",true);run("openpnp_home_machine",10000);
            Part part=Configuration.get().getPart("R0603-1K");double height=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
            JsonArray parts=new JsonArray();parts.add(object("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height));
            JsonArray placements=new JsonArray();for(int i=0;i<count;i++)placements.add(object("ref","R"+i,"partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height,"x",1.5*(i%100),"y",1.5*(i/100),"z",0,"rotation",0,"side","top","enabled",true,"type","placement"));
            JsonObject board=object("id","sustained-board","widthMm",150,"heightMm",150);board.add("placements",placements);JsonArray boards=new JsonArray();boards.add(board);
            JsonArray instances=new JsonArray();instances.add(object("id","sustained-copy","kind","board","definitionId","sustained-board","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true));
            JsonObject canonical=object("schemaVersion",1,"id","sustained-"+count,"units","mm","coordinateConvention","openpnp-top-view");canonical.add("parts",parts);canonical.add("boards",boards);canonical.add("panels",new JsonArray());canonical.add("instances",instances);
            Map<String,Object> prepared=run("openpnp_prepare_job",30000,"canonical_job",canonical);
            Map<String,Object> validated=run("openpnp_validate_job",30000);if(!Boolean.TRUE.equals(((Map<?,?>)validated.get("result")).get("valid")))throw new AssertionError(validated);
            Map<String,Object> result=run("openpnp_start_job",Math.max(120000,count*3000L),"job_id",((Map<?,?>)prepared.get("result")).get("job_id"));
            Map<?,?> summary=(Map<?,?>)result.get("result");if(((Number)summary.get("placed")).intValue()!=count)throw new AssertionError(summary);
            ReferenceTrayFeeder feeder=(ReferenceTrayFeeder)Configuration.get().getMachine().getFeeders().stream().filter(f->f.getPart()==part).findFirst().orElseThrow();
            if(feeder.getFeedCount()!=count)throw new AssertionError("Feed inventory mismatch "+feeder.getFeedCount());
            Map<?,?> status=call("openpnp_get_status",new JsonObject()),metrics=(Map<?,?>)status.get("metrics"),progress=(Map<?,?>)status.get("job_progress");
            if(((Number)progress.get("placed")).intValue()!=count||((Number)progress.get("requested")).intValue()!=count||progress.get("observed_at")==null||progress.get("through_sequence")==null)throw new AssertionError("published native progress missing: "+progress);
            long recomputations=((Number)metrics.get("job_count_recomputations")).longValue(),steps=((Number)result.get("native_steps_started")).longValue();
            if(recomputations>count+5||steps<=recomputations*2)throw new AssertionError("native event-driven count cache failed: "+metrics+" steps="+steps);
            processor.setJobOrder(ReferencePnpJobProcessor.JobOrderHint.NozzleTips);
            try{call("openpnp_home_machine",object("request_id","order-drift-test","session_id",session));throw new AssertionError("changed native order was admitted");}catch(Bridge.Fault fault){if(!"SIMULATOR_PROFILE_MISMATCH".equals(fault.code))throw fault;}finally{processor.setJobOrder(ReferencePnpJobProcessor.JobOrderHint.Unsorted);}
            System.out.println("OPENPNP_NATIVE_SUSTAINED_RESULT "+GSON.toJson(Bridge.map("requested",count,"placed",count,"native_feed_count",feeder.getFeedCount(),"initial_capacity",10000,"remaining",10000-feeder.getFeedCount(),"profile","sustained-workload","native_job_order",processor.getJobOrder().name(),"native_order_persisted",true,"native_order_drift_rejected",true,"fixture","native-finite-tray-supply-zero-pitch","upstream_commit",Bridge.UPSTREAM,"elapsed_seconds",(System.nanoTime()-started)/1e9,"native_steps",steps,"job_count_recomputations",recomputations,"machine_snapshot_refreshes",metrics.get("machine_snapshot_refreshes"),"journal",root.resolve("journal").toString(),"physical_qualification",false,"eight_hour_soak",false)));
        }catch(Throwable t){t.printStackTrace();exitCode=1;}finally{bridge.close();Configuration.get().getMachine().close();}System.exit(exitCode);
    }
    static JsonObject object(Object...pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static Map<String,Object> call(String method,JsonObject p)throws Exception{return(Map<String,Object>)bridge.call(method,p);}
    static Map<String,Object> run(String method,long timeout,Object...pairs)throws Exception {
        JsonObject p=object("request_id",UUID.randomUUID().toString(),"session_id",session);for(Map.Entry<String,JsonElement> e:object(pairs).entrySet())p.add(e.getKey(),e.getValue());
        Map<String,Object> op=call(method,p);String id=(String)op.get("operation_id");long deadline=System.nanoTime()+timeout*1000000L,lastRenew=System.nanoTime();
        do{op=call("openpnp_get_operation",object("operation_id",id));if("succeeded".equals(op.get("state"))){while(Configuration.get().getMachine().isBusy())Thread.sleep(5);return op;}if(Arrays.asList("failed","aborted","outcome_unknown").contains(op.get("state")))throw new AssertionError(method+" "+op);if(System.nanoTime()-lastRenew>60_000_000_000L){call("openpnp_renew_control_session",object("session_id",session,"ttl_seconds",600));lastRenew=System.nanoTime();}Thread.sleep(20);}while(System.nanoTime()<deadline);throw new AssertionError("Timeout "+op);
    }
}
