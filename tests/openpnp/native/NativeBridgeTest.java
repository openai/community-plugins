/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.net.URI;
import java.net.http.*;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import org.openpnp.model.Configuration;
import org.openpnp.model.*;
import org.openpnp.model.Abstract2DLocatable.Side;
import org.openpnp.machine.reference.driver.GcodeDriver;

/** Real OpenPnP model/processor/vision integration. Does not substitute a fake machine implementation. */
public final class NativeBridgeTest {
    static Bridge bridge;
    static String session;
    static String token;
    static final HttpClient HTTP=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    static final List<String> passed=new ArrayList<>();
    public static void main(String[]args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-test-");
        Path configRoot=root.resolve("config");Files.createDirectories(configRoot);
        Path tokenFile=root.resolve("token");token=UUID.randomUUID().toString()+UUID.randomUUID();Files.writeString(tokenFile,token);
        Configuration.initialize(configRoot.toFile());Configuration.get().load();SimulatorMain.accelerateFixture(Configuration.get());
        bridge=new Bridge(Configuration.get(),tokenFile,root.resolve("journal"),Paths.get(args[0]),0,true);bridge.start();
        int exitCode=0;
        try {
            HttpResponse<String> denied=HTTP.send(HttpRequest.newBuilder(URI.create(url())).POST(HttpRequest.BodyPublishers.ofString("{\"method\":\"openpnp_get_status\"}")).build(),HttpResponse.BodyHandlers.ofString());
            check(denied.statusCode()==401,"unauthenticated RPC rejected");
            JsonObject caps=rpc("openpnp_get_capabilities",new JsonObject());
            check("NozzleTips".equals(caps.get("native_job_order").getAsString()),"default simulator retains upstream native NozzleTips job order");
            check(caps.get("upstream_commit").getAsString().equals(Bridge.UPSTREAM),"pinned native upstream");
            check(!rpc("openpnp_get_status",new JsonObject()).getAsJsonObject("machine").get("enabled").getAsBoolean(),"observe does not enable");
            session=rpc("openpnp_request_control_session",obj("request_id","grant-1","ttl_seconds",300)).get("session_id").getAsString();
            String enableRequest=UUID.randomUUID().toString();JsonObject enable=mutation("request_id",enableRequest,"enabled",true);
            JsonObject enabled=await(rpc("openpnp_set_machine_enabled",enable));check(enabled.get("state").getAsString().equals("succeeded"),"native simulator enabled");
            JsonObject duplicate=rpc("openpnp_set_machine_enabled",enable);check(duplicate.get("operation_id").equals(enabled.get("operation_id")),"mutation request deduplicated");
            JsonObject collision=new JsonParser().parse(enable.toString()).getAsJsonObject();collision.addProperty("enabled",false);expectError("openpnp_set_machine_enabled",collision,"REQUEST_ID_CONFLICT");passed.add("request collision rejected");
            check(await(rpc("openpnp_home_machine",mutation())).get("state").getAsString().equals("succeeded"),"native homing completed");
            JsonObject plan=rpc("openpnp_plan_motion",mutation("units","mm","x",10,"y",10,"z",0,"rotation",0,"speed",1));
            check(await(rpc("openpnp_execute_motion",mutation("plan_id",plan.get("plan_id").getAsString()))).get("state").getAsString().equals("succeeded"),"native safe-Z motion and standstill");
            JsonObject image=await(rpc("openpnp_capture_camera",mutation("mode","raw")));
            check(image.get("state").getAsString().equals("succeeded"),"native simulated camera captured");
            JsonObject artifact=rpc("openpnp_get_artifact",obj("artifact_id",image.getAsJsonObject("result").get("artifact_id").getAsString()));
            byte[] bytes=Base64.getDecoder().decode(artifact.get("base64").getAsString());check(bytes.length>100&&bytes[0]==(byte)137,"camera artifact is PNG");
            JsonObject changes=mutation();JsonArray edits=new JsonArray();edits.add(obj("type","set_machine_speed","speed",1));changes.add("changes",edits);
            JsonObject configPlan=rpc("openpnp_plan_configuration",changes);
            check(await(rpc("openpnp_apply_configuration",mutation("plan_id",configPlan.get("plan_id").getAsString()))).get("state").getAsString().equals("succeeded"),"native model persisted");
            check(Files.readString(configRoot.resolve("machine.xml")).contains("speed=\"1.0\""),"saved XML agrees with model");
            GcodeDriver realDriver=new GcodeDriver();Configuration.get().getMachine().addDriver(realDriver);
            expectError("openpnp_plan_motion",mutation("units","mm","x",1,"y",1,"z",0),"HARDWARE_UNQUALIFIED");
            Configuration.get().getMachine().removeDriver(realDriver);passed.add("mixed real/simulator drivers rejected");
            JsonObject prepared=await(rpc("openpnp_prepare_job",mutation("sample","pnp-test")));
            check(prepared.get("state").getAsString().equals("succeeded"),"native sample loaded");
            JsonObject validated=await(rpc("openpnp_validate_job",mutation()));
            check(validated.getAsJsonObject("result").get("valid").getAsBoolean(),"native sample preflight passed");
            String jobId=prepared.getAsJsonObject("result").get("job_id").getAsString();
            JsonObject job=rpc("openpnp_start_job",mutation("job_id",jobId));String operation=job.get("operation_id").getAsString();
            rpc("openpnp_pause_job",mutation("operation_id",operation));
            JsonObject paused=awaitState(operation,"paused",30_000);check(paused.get("state").getAsString().equals("paused"),"native job cooperatively paused");
            rpc("openpnp_resume_job",mutation("operation_id",operation));JsonObject completed=awaitState(operation,"succeeded",180_000);
            if(!"succeeded".equals(completed.get("state").getAsString()))throw new AssertionError("Native sample failed: "+completed);
            JsonObject summary=completed.getAsJsonObject("result");int requested=summary.get("requested").getAsInt();int placed=summary.get("placed").getAsInt();
            check(requested>0&&placed==requested,"real ReferencePnpJobProcessor completed every sample placement");
            check(summary.get("independently_inspected").getAsInt()==0,"native completion is not independent inspection");
            JsonObject requestStatus=rpc("openpnp_get_request_status",obj("request_id",enableRequest));
            check(requestStatus.get("found").getAsBoolean()&&requestStatus.getAsJsonObject("operation").get("operation_id").equals(enabled.get("operation_id")),"lost response reconciles by request ID");
            JsonObject backup=await(rpc("openpnp_backup_configuration",mutation()));
            check("succeeded".equals(backup.get("state").getAsString()),"native configuration backup artifact");
            JsonObject restored=await(rpc("openpnp_restore_configuration",mutation("artifact_id",backup.getAsJsonObject("result").get("artifact_id").getAsString())));
            check(restored.getAsJsonObject("result").get("restored").getAsBoolean()&&!restored.getAsJsonObject("result").get("full_configuration_restore").getAsBoolean(),"typed native restore declares exact scope");
            check(restored.getAsJsonObject("result").getAsJsonObject("job").get("placed").getAsInt()==32,"configuration rollback retains actual native placed history");
            JsonObject savedJob=await(rpc("openpnp_save_job",mutation()));
            check("succeeded".equals(savedJob.get("state").getAsString()),"native job document saved through authenticated RPC");
            String document=savedJob.getAsJsonObject("result").getAsJsonObject("artifact").get("artifact_id").getAsString();
            JsonObject loadedJob=await(rpc("openpnp_load_job",mutation("artifact_id",document)));
            check("succeeded".equals(loadedJob.get("state").getAsString())&&loadedJob.getAsJsonObject("result").getAsJsonObject("job").get("placed").getAsInt()==32,"native job reload through RPC retains completed placement history");
            check(loadedJob.getAsJsonObject("result").get("requires_validation").getAsBoolean(),"native job reload invalidates registration and validation");
            JsonObject canonical=canonicalSample(Paths.get(args[0]));
            JsonObject imported=await(rpc("openpnp_prepare_job",mutation("canonical_job",canonical)));
            check("succeeded".equals(imported.get("state").getAsString()),"canonical JSON becomes real native board and placement models");
            check(await(rpc("openpnp_validate_job",mutation())).getAsJsonObject("result").get("valid").getAsBoolean(),"canonical native job validates");
            JsonObject importedRun=awaitState(rpc("openpnp_start_job",mutation("job_id",imported.getAsJsonObject("result").get("job_id").getAsString())).get("operation_id").getAsString(),"succeeded",60_000);
            check("succeeded".equals(importedRun.get("state").getAsString()),"canonical import drives real native job processor");
            check(importedRun.getAsJsonObject("result").get("placed").getAsInt()>0,"canonical native placements recorded");
            JsonObject invalid=new JsonParser().parse(canonical.toString()).getAsJsonObject();invalid.getAsJsonArray("parts").get(0).getAsJsonObject().addProperty("packageId","unknown-package");
            JsonObject rejected=await(rpc("openpnp_prepare_job",mutation("canonical_job",invalid)));
            check("failed".equals(rejected.get("state").getAsString())&&"PACKAGE_UNMAPPED".equals(rejected.getAsJsonObject("result").get("code").getAsString()),"unmapped footprint rejected by native importer");
            JsonObject events=rpc("openpnp_get_events",obj("after_sequence",0,"limit",500));check(events.getAsJsonArray("events").size()>10,"durable native operation events");
            rpc("openpnp_release_control_session",obj("session_id",session));
            expectError("openpnp_home_machine",mutation(),"SESSION_REQUIRED");passed.add("released ownership rejected");
            System.out.println("OPENPNP_NATIVE_TEST_RESULT "+new Gson().toJson(Bridge.map("passed",passed,"requested",requested,"placed",placed,"upstream_commit",Bridge.UPSTREAM,"journal",root.resolve("journal").toString())));
        } catch(Throwable t) {t.printStackTrace();exitCode=1;} finally {bridge.close();Configuration.get().getMachine().close();}
        System.exit(exitCode);
    }
    static String url(){return "http://127.0.0.1:"+bridge.getPort()+"/rpc";}
    static JsonObject canonicalSample(Path samples)throws Exception {
        Job job=Configuration.get().loadJob(Bridge.copySample(samples,Configuration.get().getConfigurationDirectory().toPath()).resolve("pnp-test.job.xml").toFile());BoardLocation source=job.getBoardLocations().get(0);
        JsonArray parts=new JsonArray(),placements=new JsonArray();Set<String> added=new HashSet<>();
        for(Placement p:source.getBoard().getPlacements())if(p.isEnabled()&&p.getType()==Placement.Type.Placement&&p.getSide()==source.getGlobalSide()){
            Part part=p.getPart();double height=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();Location pose=p.getLocation().convertToUnits(LengthUnit.Millimeters);
            if(added.add(part.getId()))parts.add(obj("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height,"value",part.getId()));
            placements.add(obj("ref",p.getId(),"partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height,"x",pose.getX(),"y",pose.getY(),"z",pose.getZ(),"rotation",pose.getRotation(),"side",p.getSide()==Side.Top?"top":"bottom","enabled",true,"type","placement"));
        }
        JsonObject board=obj("id","canonical-board","widthMm",100,"heightMm",100);board.add("placements",placements);JsonArray boards=new JsonArray();boards.add(board);
        Location position=source.getLocation().convertToUnits(LengthUnit.Millimeters);JsonArray instances=new JsonArray();instances.add(obj("id","canonical-load","kind","board","definitionId","canonical-board","side",source.getGlobalSide()==Side.Top?"top":"bottom","x",position.getX(),"y",position.getY(),"z",position.getZ(),"rotation",position.getRotation(),"enabled",true));
        JsonObject canonical=obj("schemaVersion",1,"id","canonical-test","units","mm","coordinateConvention","openpnp-top-view");canonical.add("parts",parts);canonical.add("boards",boards);canonical.add("panels",new JsonArray());canonical.add("instances",instances);return canonical;
    }
    static JsonObject raw(String method,JsonObject params)throws Exception {
        JsonObject req=obj("method",method);req.add("params",params);
        HttpRequest request=HttpRequest.newBuilder(URI.create(url())).timeout(Duration.ofSeconds(20)).header("Authorization","Bearer "+token).header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofString(req.toString())).build();
        return new JsonParser().parse(HTTP.send(request,HttpResponse.BodyHandlers.ofString()).body()).getAsJsonObject();
    }
    static JsonObject rpc(String method,JsonObject params)throws Exception {JsonObject r=raw(method,params);if(r.has("error"))throw new AssertionError(method+": "+r);return r.getAsJsonObject("result");}
    static void expectError(String method,JsonObject params,String code)throws Exception {JsonObject r=raw(method,params);if(!r.has("error")||!code.equals(r.getAsJsonObject("error").get("code").getAsString()))throw new AssertionError("Expected "+code+": "+r);}
    static JsonObject await(JsonObject op)throws Exception{return awaitState(op.get("operation_id").getAsString(),"succeeded",30_000);}
    static JsonObject awaitState(String id,String target,long timeout)throws Exception {long end=System.nanoTime()+timeout*1_000_000;JsonObject op;do{op=rpc("openpnp_get_operation",obj("operation_id",id));String s=op.get("state").getAsString();if((s.equals(target)||Arrays.asList("failed","aborted","outcome_unknown").contains(s))&&!rpc("openpnp_get_status",new JsonObject()).get("native_busy").getAsBoolean())return op;Thread.sleep(20);}while(System.nanoTime()<end);throw new AssertionError("Timed out waiting for "+target+": "+op);}
    static JsonObject mutation(Object...pairs){JsonObject p=obj("request_id",UUID.randomUUID().toString(),"session_id",session);JsonObject extras=obj(pairs);for(Map.Entry<String,JsonElement> e:extras.entrySet())p.add(e.getKey(),e.getValue());return p;}
    static JsonObject obj(Object...pairs){return new Gson().toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static void check(boolean condition,String name){if(!condition)throw new AssertionError(name);passed.add(name);System.out.println("PASS "+name);}
}
