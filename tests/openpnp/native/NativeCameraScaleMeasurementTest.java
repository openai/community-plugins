/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.*;
import java.awt.geom.Ellipse2D;
import java.awt.image.BufferedImage;
import java.io.StringWriter;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.List;
import javax.imageio.ImageIO;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.camera.ImageCamera;
import org.openpnp.machine.reference.camera.ReferenceCamera;
import org.openpnp.machine.reference.camera.AbstractSettlingCamera.SettleMethod;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.AbstractNozzle;
import org.openpnp.scripting.Scripting;
import org.openpnp.spi.MotionPlanner.CompletionType;
import org.openpnp.vision.pipeline.stages.DetectCircularSymmetry;

/** Private actual-native tests. Fixture preparation may write settings; helper may not. */
public final class NativeCameraScaleMeasurementTest {
 static final Gson JSON=new GsonBuilder().serializeNulls().setPrettyPrinting().create();
 static Path out;static Configuration config;static ReferenceMachine machine;static ImageCamera camera;
 static int checks;static final Map<String,Object> result=new LinkedHashMap<>();
 static final List<Map<String,Object>> tests=new ArrayList<>();static Map<String,Object> expectedLights;static final List<String> nativeEvents=new ArrayList<>();
 static Map<String,Object> map(Object...p){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<p.length;i+=2)m.put((String)p[i],p[i+1]);return m;}
 static void check(boolean b,String label){checks++;if(!b)throw new AssertionError(label);}
 interface Action{void run()throws Exception;}
 static Throwable rejected(String code,Action action)throws Exception{Throwable caught=null;try{action.run();}catch(Throwable t){caught=t;}check(caught!=null,"Expected refusal "+code);Throwable root=caught;while(root.getCause()!=null&&!(root instanceof NativeCameraScaleMeasurement.Fault))root=root.getCause();check(root instanceof NativeCameraScaleMeasurement.Fault&&code.equals(((NativeCameraScaleMeasurement.Fault)root).code),"Expected "+code+" got "+caught);tests.add(map("case",code,"class",root.getClass().getName(),"message",root.getMessage()));return root;}
 static String sha(byte[] b)throws Exception{byte[] digest=MessageDigest.getInstance("SHA-256").digest(b);StringBuilder hex=new StringBuilder(64);for(byte value:digest){hex.append(Character.forDigit((value>>>4)&15,16));hex.append(Character.forDigit(value&15,16));}return hex.toString();}
 static String sha(Path p)throws Exception{return sha(Files.readAllBytes(p));}
 static void write(Path p,Object value)throws Exception{Files.writeString(p,JSON.toJson(value)+"\n",StandardOpenOption.CREATE_NEW);}
 static Field field(Class<?> c,String name)throws Exception{Field f=c.getDeclaredField(name);f.setAccessible(true);return f;}
 static Map<String,String> xmlHashes()throws Exception{Map<String,String> m=new TreeMap<>();try(var paths=Files.walk(config.getConfigurationDirectory().toPath())){for(Path p:paths.filter(x->Files.isRegularFile(x)&&x.toString().endsWith(".xml")).collect(java.util.stream.Collectors.toList()))m.put(config.getConfigurationDirectory().toPath().relativize(p).toString(),sha(p));}return m;}
 static Map<String,Object> lightValues(){Map<String,Object> values=new TreeMap<>();for(Camera c:machine.getAllCameras())if(c.getLightActuator()!=null)values.put(c.getId()+"/"+c.getLightActuator().getId(),c.getLightActuator().getLastActuationValue());return values;}
 static String modelHash()throws Exception{StringWriter s=new StringWriter();Configuration.createSerializer().write(machine,s);return sha(s.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));}
 static NativeCameraScaleMeasurement.Plan plan()throws Exception{return NativeCameraScaleMeasurement.admit(config,camera,1,40);}
 static void fixture(String kind)throws Exception{
   Configuration.initialize(out.resolve("config").toFile());Configuration.get().load();config=Configuration.get();SimulatorMain.accelerateFixture(config);machine=(ReferenceMachine)config.getMachine();camera=(ImageCamera)machine.getDefaultHead().getDefaultCamera();camera.close();
   camera.setImageUnitsPerPixel(new Location(LengthUnit.Millimeters,.040,.040,0,0));camera.setUnitsPerPixelPrimary(new Location(LengthUnit.Millimeters,.044,.043,0,0));
   camera.setViewWidth(640);camera.setViewHeight(480);camera.setImageOffset(new Location(LengthUnit.Millimeters));camera.setSimulatedRotation(0);camera.setSimulatedScale(1);camera.setSimulatedDistortion(0);camera.setSimulatedYRotation(0);camera.setSimulatedFlipped(false);camera.setPrimaryFiducial(new Location(LengthUnit.Millimeters));camera.setSecondaryFiducial(new Location(LengthUnit.Millimeters));camera.setFlipX(false);camera.setFlipY(false);camera.setRotation(0);camera.setSettleMethod(SettleMethod.FixedTime);camera.setSettleTimeMs(600);
   BufferedImage img=new BufferedImage(2048,2048,BufferedImage.TYPE_INT_RGB);Graphics2D g=img.createGraphics();g.setColor(Color.WHITE);g.fillRect(0,0,2048,2048);g.setColor(Color.BLACK);g.setRenderingHint(RenderingHints.KEY_ANTIALIASING,RenderingHints.VALUE_ANTIALIAS_ON);
   if(kind.equals("two-circle")){g.fill(new Ellipse2D.Double(940,1028,40,40));g.fill(new Ellipse2D.Double(1020,1028,40,40));}else if(!kind.equals("blank"))g.fill(new Ellipse2D.Double(980,1028,40,40));g.dispose();Path file=out.resolve("source.png");check(ImageIO.write(img,"PNG",file.toFile()),"PNG encoder");camera.setSourceUri(file.toUri().toString());config.save();
   result.put("source",map("sha256",sha(file),"size",List.of(2048,2048),"independent_renderer_scale_mm_per_px",List.of(.040,.040),"reported_scale_mm_per_px",List.of(.044,.043)));
   machine.submit(()->{machine.setEnabled(true);machine.home();camera.moveTo(camera.getLocation().convertToUnits(LengthUnit.Millimeters).derive(40.,40.,null,null),.2);machine.getMotionPlanner().waitForCompletion(camera,CompletionType.WaitForStillstand);return null;},null,true).get();
   result.put("setup",map("enable_calls",1,"home_calls",1,"move_calls",1,"prewarm_capture_calls",1));
   machine.execute(()->{camera.lightSettleAndCapture();return null;});
   result.put("default_light_actuator_count",machine.getAllCameras().stream().filter(c->c.getLightActuator()!=null).count());
   machine.execute(()->{for(Camera c:machine.getAllCameras())if(c.getLightActuator()!=null)c.getLightActuator().actuate(c.getLooking()==Camera.Looking.Up);return null;});
   result.put("fixture_setup_lights_opposite_optional_capture_lighting",true);
 }
 static final class CallbackFailure extends Exception{CallbackFailure(String message){super(message);}}
 static class Recorder implements NativeCameraScaleMeasurement.Callbacks {
   int begins,ends,persists,moveBegins,captureBegins;String pending;final String injection;final List<Map<String,Object>> events=new ArrayList<>();
   Recorder(String mode){injection=mode;}
   public void checkCurrent()throws Exception{check(machine.isTask(Thread.currentThread()),"Native executor callback");check(expectedLights.equals(lightValues()),"Bound light values unchanged at each callback check");if(injection.equals("callback-after-intent")&&begins==1&&persists==0)throw new CallbackFailure("injected lease expiration after intent and validation");if(injection.equals("callback-expire")&&persists==1)throw new CallbackFailure("injected lease expiration after first frame");}
   @SuppressWarnings("unchecked") public void beginEffect(String kind,Map<String,Object> detail)throws Exception{
     check(pending==null,"No overlapping effect");begins++;if(kind.endsWith("move"))moveBegins++;else captureBegins++;pending=kind;events.add(map("event","begin","kind",kind,"detail",detail));
     try{detail.put("injected",true);throw new AssertionError("mutable detail");}catch(UnsupportedOperationException expected){checks++;}
     if(detail.get("target") instanceof Map)try{((Map<String,Object>)detail.get("target")).put("x_mm",999);throw new AssertionError("mutable nested detail");}catch(UnsupportedOperationException expected){checks++;}
   }
   public void endEffect(String kind,Map<String,Object> detail)throws Exception{
     check(kind.equals(pending),"Matching pending effect");
     if(injection.equals("callback-after-move")&&kind.endsWith("move"))throw new CallbackFailure("injected move outcome persistence failure");
     ends++;events.add(map("event","end","kind",kind,"detail",detail));pending=null;
     if(injection.equals("callback-mutate")&&persists==1&&kind.endsWith("capture")){BufferedImage image=(BufferedImage)field(ImageCamera.class,"source").get(camera);image.setRGB(0,0,image.getRGB(0,0)^0xffffff);}
   }
   public String persistObservation(byte[] png,Map<String,Object> observation)throws Exception{
     check("camera-scale-capture".equals(pending),"PNG persisted within pending capture");check(sha(png).equals(observation.get("png_sha256")),"Exact PNG digest");
     if(injection.equals("callback-persist"))throw new CallbackFailure("injected artifact persistence failure");
     persists++;String id=String.format("measurement-%02d",persists);Files.write(out.resolve(id+".png"),png,StandardOpenOption.CREATE_NEW);write(out.resolve(id+".json"),observation);events.add(map("event","persist","artifact_id",id,"observation",observation));return id;
   }
   Map<String,Object> receipt(){return map("begins",begins,"ends",ends,"persisted_frames",persists,"move_begins",moveBegins,"capture_begins",captureBegins,"pending",pending,"events",events,"callbacks_are_test_recorders_not_bridge_durable_journal",true);}
 }
 static void parserTests()throws Exception{
   JsonObject good=new JsonParser().parse("{\"request_id\":\"r\",\"session_id\":\"s\",\"expected_config_revision\":\"cfg-1\",\"recipe_id\":\"camera-planar-scale\",\"camera_id\":\"c\",\"displacement_mm\":1,\"expected_feature_diameter_px\":40}").getAsJsonObject();
   check(NativeCameraScaleMeasurement.parseArguments(good).diameterPx==40,"Exact command accepted");
   for(String mutation:List.of("extra","missing","recipe","string-number","fraction","near-integer","d-small","d-large","near-small","near-large","diam-small","diam-large","revision","bool")){
     JsonObject j=new JsonParser().parse(good.toString()).getAsJsonObject();String expected="INVALID_ARGUMENT";
     switch(mutation){case"extra":j.addProperty("limit",10);break;case"missing":j.remove("camera_id");break;case"recipe":j.addProperty("recipe_id","lens");break;case"string-number":j.addProperty("displacement_mm","1");break;case"fraction":j.addProperty("expected_feature_diameter_px",40.5);break;case"near-integer":j.add("expected_feature_diameter_px",new JsonParser().parse("40.0000000000000001"));break;case"near-small":j.add("displacement_mm",new JsonParser().parse("0.499999999999999999"));break;case"near-large":j.add("displacement_mm",new JsonParser().parse("2.000000000000000001"));break;case"d-small":j.addProperty("displacement_mm",.49);break;case"d-large":j.addProperty("displacement_mm",2.01);break;case"diam-small":j.addProperty("expected_feature_diameter_px",11);break;case"diam-large":j.addProperty("expected_feature_diameter_px",97);break;case"revision":j.addProperty("expected_config_revision","cfg-NaN");break;case"bool":j.addProperty("camera_id",true);break;}
     rejected(expected,()->NativeCameraScaleMeasurement.parseArguments(j));
   }
   for(double d:new double[]{.5,2})for(int diameter:new int[]{12,96}){JsonObject j=new JsonParser().parse(good.toString()).getAsJsonObject();j.addProperty("displacement_mm",d);j.addProperty("expected_feature_diameter_px",diameter);check(NativeCameraScaleMeasurement.parseArguments(j).diameterPx==diameter,"Exact boundaries accepted");}
 }
 static void admissions()throws Exception{
   parserTests();
   camera.setLooking(Camera.Looking.Up);rejected("UNSUPPORTED_CAMERA_ORIENTATION",()->plan());camera.setLooking(Camera.Looking.Down);
   Recorder r=new Recorder("none");NativeCameraScaleMeasurement.Plan p=plan();rejected("NATIVE_EXECUTOR_REQUIRED",()->p.run(r));check(r.begins==0,"Off-executor no effects");rejected("PLAN_ALREADY_USED",()->p.run(r));
   Field sourceField=field(ImageCamera.class,"source"),rendererField=field(ImageCamera.class,"imageUnitsPerPixel");Object source=sourceField.get(camera),renderer=rendererField.get(camera);
   sourceField.set(camera,null);rejected("CAMERA_SOURCE_UNINITIALIZED",()->plan());check(sourceField.get(camera)==null,"No lazy source initialization");sourceField.set(camera,source);
   rendererField.set(camera,null);rejected("CAMERA_SOURCE_UNINITIALIZED",()->plan());check(rendererField.get(camera)==null,"No lazy renderer initialization");rendererField.set(camera,renderer);
   sourceField.set(camera,new BufferedImage(8193,1,BufferedImage.TYPE_INT_RGB));rejected("SOURCE_IMAGE_LIMIT",()->plan());sourceField.set(camera,source);
   camera.setCropWidth(10);rejected("UNSUPPORTED_CAMERA_TRANSFORMS",()->plan());camera.setCropWidth(0);
   camera.setFlipX(true);rejected("UNSUPPORTED_CAMERA_TRANSFORMS",()->plan());camera.setFlipX(false);
   camera.getCalibration().setEnabled(true);rejected("UNSUPPORTED_CALIBRATION",()->plan());camera.getCalibration().setEnabled(false);
   camera.getAdvancedCalibration().setEnabled(true);rejected("UNSUPPORTED_CALIBRATION",()->plan());camera.getAdvancedCalibration().setEnabled(false);
   camera.setEnableUnitsPerPixel3D(true);rejected("UNSUPPORTED_CALIBRATION",()->plan());camera.setEnableUnitsPerPixel3D(false);
   camera.setViewWidth(641);rejected("FRAME_LIMIT",()->plan());camera.setViewWidth(640);
   camera.setSettleTimeMs(599);rejected("SETTLING_RANGE",()->plan());camera.setSettleTimeMs(600);
   camera.setSimulatedRotation(1);rejected("UNSUPPORTED_SIMULATED_OPTICS",()->plan());camera.setSimulatedRotation(0);
   camera.setPrimaryFiducial(new Location(LengthUnit.Millimeters,1,1,0,0));rejected("UNSUPPORTED_SIMULATED_OPTICS",()->plan());camera.setPrimaryFiducial(new Location(LengthUnit.Millimeters));
   Location upp=camera.getUnitsPerPixelPrimary();camera.setUnitsPerPixelPrimary(upp.derive(null,null,1.,null));rejected("WORKING_PLANE",()->plan());camera.setUnitsPerPixelPrimary(upp);
   ReferenceControllerAxis x=(ReferenceControllerAxis)camera.getAxisX();Length oldLow=x.getSoftLimitLow();boolean enabled=x.isSoftLimitLowEnabled();
   x.setSoftLimitLow(new Length(39.75,LengthUnit.Millimeters));x.setSoftLimitLowEnabled(true);rejected("NATIVE_SOFT_LIMIT",()->plan());x.setSoftLimitLow(new Length(Double.NaN,LengthUnit.Millimeters));rejected("NATIVE_SOFT_LIMIT",()->plan());x.setSoftLimitLow(oldLow);x.setSoftLimitLowEnabled(enabled);
   x.setBacklashCompensationMethod(ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp);rejected("UNSUPPORTED_AXES",()->plan());x.setBacklashCompensationMethod(ReferenceControllerAxis.BacklashCompensationMethod.None);
   Nozzle nozzle=machine.getDefaultHead().getDefaultNozzle();Field part=field(AbstractNozzle.class,"part");part.set(nozzle,config.getParts().get(0));rejected("NOZZLE_NOT_SAFE",()->plan());part.set(nozzle,null);
   NativeCameraScaleMeasurement.Plan stale=plan();camera.setSettleTimeMs(700);machine.execute(()->{rejected("STALE_MEASUREMENT_PLAN",()->stale.run(r));return null;});camera.setSettleTimeMs(600);
   NativeCameraScaleMeasurement.Plan pixels=plan();BufferedImage loaded=(BufferedImage)source;int rgb=loaded.getRGB(0,0);loaded.setRGB(0,0,rgb^0xffffff);machine.execute(()->{rejected("STALE_MEASUREMENT_PLAN",()->pixels.run(r));return null;});loaded.setRGB(0,0,rgb);
   check(r.begins==0&&r.persists==0,"All admission failures have zero effects");result.put("callback_recorder",r.receipt());
 }
 @SuppressWarnings("unchecked") static void measurement(String mode)throws Exception{
   NativeCameraScaleMeasurement.Plan p=plan();Recorder recorder=new Recorder(mode);Map<String,Object> receipt=null;Throwable failure=null;
   try{receipt=machine.execute(()->{try(Scripting.NativeObserverScope scope=Scripting.observeNativeEvents(new Scripting.NativeObserver(){public void beforeScripts(String event,Map<String,Object> globals){nativeEvents.add("before-scripts:"+event);}public void afterScripts(String event,Map<String,Object> globals){nativeEvents.add("after-scripts:"+event);}})){return p.run(recorder);}});}catch(Throwable t){failure=t;}
   result.put("measurement",receipt);result.put("callback_recorder",recorder.receipt());result.put("native_events",nativeEvents);long nativeCaptureHooks=nativeEvents.stream().filter(x->x.equals("before-scripts:Camera.BeforeCapture")).count();result.put("actual_native_capture_before_hooks",nativeCaptureHooks);
   if(mode.startsWith("callback-")){
     check(failure!=null,"Injected callback/native coherence failure propagated");result.put("injected_failure",map("class",failure.getClass().getName(),"message",failure.getMessage()));
     int expectedMoves=mode.equals("callback-after-move")?1:0;check(recorder.moveBegins==expectedMoves,"No cleanup or extra move");check(recorder.captureBegins==1,"No subsequent capture");check(nativeCaptureHooks==(mode.equals("callback-after-intent")?0:1),"Actual native capture hook bound");check(recorder.persists==((mode.equals("callback-persist")||mode.equals("callback-after-intent"))?0:1),"Bounded persisted observations");
     check(Math.abs(camera.getLocation().getX()-(expectedMoves==1?39.5:40))<1e-9,"Last actual native pose retained");
     if(mode.equals("callback-persist")||mode.equals("callback-after-move")||mode.equals("callback-after-intent"))check(recorder.pending!=null,"Failed persistence leaves pending test effect");
   }else{
     if(failure!=null)throw new AssertionError("Unexpected measurement exception",failure);
     boolean positive=mode.equals("positive");check((positive?"accepted":"rejected").equals(receipt.get("measurement_status")),"Expected quality disposition");
     check(((Number)receipt.get("capture_calls")).intValue()==(positive?8:1),"Fixed capture bound");check(((Number)receipt.get("recipe_move_calls")).intValue()==(positive?7:0),"Fixed native move bound");
     check(nativeCaptureHooks==(positive?8:1),"Actual native capture hooks match receipt");check(recorder.pending==null&&recorder.begins==recorder.ends,"Every completed effect has outcome");
     check(Boolean.FALSE.equals(receipt.get("calibration_applied"))&&Boolean.FALSE.equals(receipt.get("hardware_qualified")),"No calibration/hardware claim");
     if(positive){Map<String,Object> fit=(Map<String,Object>)receipt.get("proposed_scale_mm_per_px");check(Math.abs(((Number)fit.get("x")).doubleValue()/.04-1)<=.02&&Math.abs(((Number)fit.get("y")).doubleValue()/.04-1)<=.02,"Independent renderer model agreement");check(((List<?>)receipt.get("heldout_simulator_image_prediction_errors")).size()==2,"Two heldout frames");}
     else check((mode.equals("blank")?"TARGET_NOT_FOUND":"AMBIGUOUS_TARGET").equals(((Map<?,?>)receipt.get("rejection")).get("code")),"Exact quality rejection");
   }
   machine.execute(()->{rejected("PLAN_ALREADY_USED",()->p.run(recorder));return null;});
 }
 public static void main(String[] args)throws Exception{
   out=Paths.get(args[0]).toAbsolutePath();String mode=args[1];Files.createDirectories(out);int exit=1;boolean closed=false;String beforeModel=null;Map<String,String> beforeXml=null;
   result.putAll(map("mode",mode,"pid",ProcessHandle.current().pid(),"process_start",ProcessHandle.current().info().startInstant().map(Object::toString).orElse(null),"production_qualified",false,"hardware_qualified",false,"native_effect_callback_integration_qualified",false));
   try{
     fixture(mode);expectedLights=lightValues();check(expectedLights.size()==2,"Both stock camera lights remain bound");result.put("lights_before",expectedLights);beforeModel=modelHash();beforeXml=xmlHashes();result.put("before_model_sha256",beforeModel);result.put("before_xml",beforeXml);
     result.put("code_sources",map("helper",NativeCameraScaleMeasurement.class.getProtectionDomain().getCodeSource().getLocation().toString(),"bridge",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString(),"camera",ImageCamera.class.getProtectionDomain().getCodeSource().getLocation().toString(),"detector",DetectCircularSymmetry.class.getProtectionDomain().getCodeSource().getLocation().toString()));
     if(mode.equals("admission"))admissions();else measurement(mode);
     result.put("lights_after",lightValues());check(expectedLights.equals(lightValues()),"Actual bound light actuator values unchanged");result.put("after_model_sha256",modelHash());result.put("after_xml",xmlHashes());boolean unchanged=beforeModel.equals(modelHash())&&beforeXml.equals(xmlHashes());result.put("configuration_unchanged",unchanged);check(unchanged,"Helper does not save or change native geometry/model");exit=0;
   }catch(Throwable t){result.put("failure",map("class",t.getClass().getName(),"message",Objects.toString(t.getMessage(),"")));t.printStackTrace();}
   finally{try{if(machine!=null){machine.close();closed=true;}}catch(Throwable t){result.put("close_failure",t.toString());exit=1;}result.put("closed",closed);result.put("checks",checks);result.put("refusals",tests);result.put("exit_code",exit);write(out.resolve("result.json"),result);System.out.println("CAMERA_SCALE_HELPER_RESULT "+new Gson().toJson(map("mode",mode,"checks",checks,"closed",closed,"exit",exit)));}
   System.exit(exit);
 }
}
