/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.geom.Ellipse2D;
import java.awt.image.BufferedImage;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import javax.imageio.ImageIO;
import org.openpnp.machine.reference.camera.ImageCamera;
import org.openpnp.machine.reference.camera.AbstractSettlingCamera.SettleMethod;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Actual Bridge request/receipt, native image, idempotency and separate typed-apply test.
 * Fixture writes are explicit simulator setup, not public configuration capability. */
public final class NativeCameraScaleBridgeTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();
    static Configuration config;static Machine machine;static ImageCamera camera;static Bridge bridge;
    static Path root;static String session;static int checks;
    static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    static JsonObject obj(Object...args){return JSON.toJsonTree(Bridge.map(args)).getAsJsonObject();}
    @SuppressWarnings("unchecked") static Map<String,Object> call(String method,JsonObject args)throws Exception{return(Map<String,Object>)bridge.call(method,args);}
    static String revision()throws Exception{return(String)call("openpnp_get_status",obj()).get("config_revision");}
    static JsonObject command(Object...args)throws Exception{JsonObject p=obj(args);p.addProperty("request_id",UUID.randomUUID().toString());p.addProperty("session_id",session);p.addProperty("expected_config_revision",revision());return p;}
    static void idle()throws Exception{long limit=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);while(machine.isBusy()){if(System.nanoTime()>limit)throw new AssertionError("Native task did not close");Thread.sleep(5);}}
    static Map<String,Object> await(Map<String,Object> accepted)throws Exception {
        long limit=System.nanoTime()+TimeUnit.SECONDS.toNanos(35);String id=(String)accepted.get("operation_id");
        while(true){Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",id));
            if(Set.of("succeeded","failed","outcome_unknown").contains(op.get("state"))){idle();return op;}
            if(System.nanoTime()>limit)throw new AssertionError("Native operation deadline");Thread.sleep(10);}
    }
    static Map<String,Object> operation(String method,JsonObject p)throws Exception{return await(call(method,p));}
    static Map<?,?> result(Map<String,Object> op){return(Map<?,?>)op.get("result");}
    static List<Map<String,Object>> journal()throws Exception{List<Map<String,Object>> rows=new ArrayList<>();for(String line:Files.readAllLines(root.resolve("journal/operations.jsonl")))rows.add(NativeJournalJson.parseObject(line));return rows;}
    static long events(String name)throws Exception{return journal().stream().filter(r->name.equals(r.get("type"))).count();}
    static void setup(String mode,Path samples)throws Exception {
        Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);machine=config.getMachine();camera=(ImageCamera)machine.getDefaultHead().getDefaultCamera();camera.close();
        camera.setImageUnitsPerPixel(new Location(LengthUnit.Millimeters,.04,.04,0,0));camera.setUnitsPerPixelPrimary(new Location(LengthUnit.Millimeters,.044,.043,0,0));
        camera.setViewWidth(640);camera.setViewHeight(480);camera.setImageOffset(new Location(LengthUnit.Millimeters));camera.setSimulatedRotation(0);camera.setSimulatedScale(1);camera.setSimulatedDistortion(0);camera.setSimulatedYRotation(0);camera.setSimulatedFlipped(false);camera.setPrimaryFiducial(new Location(LengthUnit.Millimeters));camera.setSecondaryFiducial(new Location(LengthUnit.Millimeters));camera.setFlipX(false);camera.setFlipY(false);camera.setRotation(0);camera.setSettleMethod(SettleMethod.FixedTime);camera.setSettleTimeMs(600);
        BufferedImage image=new BufferedImage(2048,2048,BufferedImage.TYPE_INT_RGB);Graphics2D graphics=image.createGraphics();graphics.setColor(Color.WHITE);graphics.fillRect(0,0,2048,2048);graphics.setColor(Color.BLACK);graphics.setRenderingHint(RenderingHints.KEY_ANTIALIASING,RenderingHints.VALUE_ANTIALIAS_ON);
        if(!"blank".equals(mode)){graphics.fill(new Ellipse2D.Double("two-circle".equals(mode)?940:980,1028,40,40));if("two-circle".equals(mode))graphics.fill(new Ellipse2D.Double(1020,1028,40,40));}graphics.dispose();Path source=root.resolve("source.png");check(ImageIO.write(image,"PNG",source.toFile()),"Fixture source PNG");camera.setSourceUri(source.toUri().toString());config.save();
        Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());bridge=new Bridge(config,token,root.resolve("journal"),samples,0,true);
        session=(String)call("openpnp_request_control_session",obj("request_id",UUID.randomUUID().toString(),"ttl_seconds",300)).get("session_id");
        check("succeeded".equals(operation("openpnp_set_machine_enabled",command("enabled",true)).get("state")),"Native enable through Bridge");
        check("succeeded".equals(operation("openpnp_home_machine",command()).get("state")),"Native home through Bridge");
        machine.submit(()->{camera.moveTo(new Location(LengthUnit.Millimeters,40,40,0,0),.2);machine.getMotionPlanner().waitForCompletion(camera,MotionPlanner.CompletionType.WaitForStillstand);return null;},null,true).get(10,TimeUnit.SECONDS);idle();
        check("succeeded".equals(operation("openpnp_capture_camera",command("camera_id",camera.getId(),"mode","raw")).get("state")),"Native source initialized through existing capture tool");
    }
    @SuppressWarnings("unchecked") static void run(String mode)throws Exception {
        String before=revision();Location geometry=camera.getUnitsPerPixelPrimary();Map<String,Object> lights=new TreeMap<>();for(Camera c:machine.getAllCameras())if(c.getLightActuator()!=null)lights.put(c.getId(),c.getLightActuator().getLastActuationValue());
        Map<?,?> capability=(Map<?,?>)call("openpnp_get_capabilities",obj()).get("camera_planar_scale");check(Boolean.TRUE.equals(((Map<?,?>)capability.get("runtime")).get("available")),"Actual fifth-patch runtime advertised");
        JsonObject request=command("recipe_id","camera-planar-scale","camera_id",camera.getId(),"displacement_mm",1,"expected_feature_diameter_px",40);
        JsonObject invalid=new JsonParser().parse(request.toString()).getAsJsonObject();invalid.addProperty("request_id","not-a-uuid");
        long initial=events("camera_scale_admission");try{call("openpnp_run_calibration",invalid);throw new AssertionError("Noncanonical request accepted");}catch(Bridge.Fault e){check("INVALID_ARGUMENT".equals(e.code),"Canonical UUID validation");}
        check(events("camera_scale_admission")==initial,"Malformed admission has no recipe events");
        Map<String,Object> accepted=call("openpnp_run_calibration",request);
        if("release".equals(mode)){
            long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);while(events("camera_scale_effect_intent")==0){if(System.nanoTime()>until)throw new AssertionError("No recipe intent");Thread.sleep(2);}
            call("openpnp_release_control_session",obj("session_id",session,"request_id",UUID.randomUUID().toString()));
        }
        Map<String,Object> op=await(accepted);Files.writeString(root.resolve("operation.json"),JSON.toJson(op));
        if("release".equals(mode)){
            check(!"succeeded".equals(op.get("state")),"Released lease cannot finish recipe successfully");
            check(events("camera_scale_observation")<=1,"Lease release prevents subsequent capture");
            check(journal().stream().filter(r->"camera_scale_effect_intent".equals(r.get("type"))).noneMatch(r->"camera-scale-move".equals(((Map<?,?>)r.get("payload")).get("kind"))),"Lease release prevents next native move");
            check(camera.getLocation().getX()==40&&camera.getLocation().getY()==40,"Last pose retained without cleanup move");return;
        }
        check("succeeded".equals(op.get("state")),"Recipe returns through the actual native wrapper");Map<?,?> measured=result(op);boolean positive="positive".equals(mode);
        check((positive?"accepted":"rejected").equals(measured.get("measurement_status")),"Expected image quality disposition");
        check(before.equals(revision())&&geometry.equals(camera.getUnitsPerPixelPrimary()),"Measurement does not apply or advance geometry");
        check(Boolean.FALSE.equals(measured.get("calibration_applied"))&&Boolean.FALSE.equals(measured.get("physical_qualification")),"Measurement scope explicit");
        check(((Number)measured.get("capture_calls")).intValue()==(positive?8:1)&&((Number)measured.get("recipe_move_calls")).intValue()==(positive?7:0),"Bounded actual recipe effects");
        for(Camera c:machine.getAllCameras())if(c.getLightActuator()!=null)check(Objects.equals(lights.get(c.getId()),c.getLightActuator().getLastActuationValue()),"Bound native lighting unchanged");
        long count=events("camera_scale_observation");check(count==(positive?8:1),"Exact durable observation count");
        for(Object value:(List<?>)measured.get("observations")){
            Map<?,?> observation=(Map<?,?>)value;String id=(String)observation.get("artifact_id");Path png=root.resolve("journal").resolve(id+".artifact");byte[] bytes=Files.readAllBytes(png);BufferedImage image=ImageIO.read(png.toFile());
            check(image.getWidth()==640&&image.getHeight()==480,"Actual native PNG dimensions");check(GuiBootstrap.hash(bytes).equals(observation.get("png_sha256")),"Exact saved observation pixels");
            check(Files.isRegularFile(root.resolve("journal").resolve(id+".metadata.json")),"Artifact metadata retained");
        }
        Map<String,Object> duplicate=call("openpnp_run_calibration",request);check(op.get("operation_id").equals(duplicate.get("operation_id"))&&events("camera_scale_observation")==count,"Repeated exact request never reruns recipe");
        JsonObject conflict=new JsonParser().parse(request.toString()).getAsJsonObject();conflict.addProperty("displacement_mm",.8);try{call("openpnp_run_calibration",conflict);throw new AssertionError("Reused request accepted different recipe");}catch(Bridge.Fault e){check("REQUEST_ID_CONFLICT".equals(e.code),"Request digest binding");}
        if(positive){
            Map<?,?> proposed=(Map<?,?>)measured.get("proposed_configuration_change");check(Math.abs(((Number)proposed.get("units_per_pixel_x_mm")).doubleValue()-.04)<1e-9&&Math.abs(((Number)proposed.get("units_per_pixel_y_mm")).doubleValue()-.04)<1e-9,"Independent renderer scale recovered");
            JsonArray changes=new JsonArray();changes.add(JSON.toJsonTree(proposed));Map<String,Object> plan=call("openpnp_plan_configuration",command("changes",changes));
            check(geometry.equals(camera.getUnitsPerPixelPrimary()),"Separate configuration preview does not apply measurement");
            Map<String,Object> applied=operation("openpnp_apply_configuration",command("plan_id",plan.get("plan_id")));check("succeeded".equals(applied.get("state")),"Existing typed apply accepts the generated proposal");
            check(camera.getUnitsPerPixelPrimary().getX()==.04&&camera.getUnitsPerPixelPrimary().getY()==.04&&!before.equals(revision()),"Only explicit apply changes native geometry/revision");
            JsonObject stale=new JsonParser().parse(request.toString()).getAsJsonObject();stale.addProperty("request_id",UUID.randomUUID().toString());try{call("openpnp_run_calibration",stale);throw new AssertionError("Stale recipe admitted");}catch(Bridge.Fault e){check("REVISION_CONFLICT".equals(e.code),"Old measurement request rejected after application");}
        }else check(!measured.containsKey("proposed_configuration_change"),"Rejected measurement has no proposal");
    }
    public static void main(String[]args)throws Exception {
        String mode=args.length>1?args[1]:"positive";root=Files.createTempDirectory("openpnp-camera-scale-bridge-");int exit=1;Throwable failure=null;
        try{setup(mode,Path.of(args[0]));run(mode);exit=0;}catch(Throwable e){failure=e;e.printStackTrace();}
        finally{if(bridge!=null)bridge.close();if(machine!=null)machine.close();Map<String,Object> report=Bridge.map("mode",mode,"passed",exit==0,"checks",checks,"failure",failure==null?null:failure.toString(),"root",root.toString(),"physical_qualification",false);Files.writeString(root.resolve("result.json"),JSON.toJson(report));System.out.println("OPENPNP_CAMERA_SCALE_BRIDGE_RESULT "+JSON.toJson(report));}
        System.exit(exit);
    }
}
