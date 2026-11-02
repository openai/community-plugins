/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.geom.AffineTransform;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.*;
import org.openpnp.machine.reference.camera.ReferenceCamera;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;
import org.w3c.dom.*;

/** Test-only launcher, never packaged as an RPC/profile. Fresh native NullDriver configuration
 * with pre-existing mapped X. Closed filesystem controls inject labeled test prerequisites/drift;
 * no class/path/axis/property values can be supplied through them. No motion/feed calls. */
public final class NativeMappedAxisFixtureMain {
 static final Gson G=new Gson();static Path state,fixture,controls;static Configuration config;static Machine machine;static Bridge bridge;
 static ReferenceMappedAxis mapped;static ReferenceControllerAxis raw,other;static final AtomicInteger enable=new AtomicInteger(),home=new AtomicInteger(),activity=new AtomicInteger(),links=new AtomicInteger();
 static int sourceChanges;static final Set<String> processed=new HashSet<>();static volatile boolean closing;
 static Map<String,Object> map(Object...p){return Bridge.map(p);}
 static JsonObject obj(Object...p){return G.toJsonTree(map(p)).getAsJsonObject();}
 static Object field(Object target,String name)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);}
 static void set(Object target,String name,Object value)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);f.set(target,value);}
 static void privateDirectory(Path p)throws Exception{Files.createDirectory(p,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));}
 static void write(Path p,Object value)throws Exception{
  Path temporary=p.resolveSibling("."+UUID.randomUUID()+".pending");
  try(java.nio.channels.FileChannel out=java.nio.channels.FileChannel.open(temporary,EnumSet.of(StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE),PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))){java.nio.ByteBuffer bytes=java.nio.ByteBuffer.wrap((G.toJson(value)+"\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));while(bytes.hasRemaining())out.write(bytes);out.force(true);}
  try{Files.move(temporary,p);}finally{Files.deleteIfExists(temporary);}
 }
 static <T>T task(Callable<T> action)throws Exception{Future<T> future;synchronized(bridge){Map<?,?> status=(Map<?,?>)bridge.call("openpnp_get_status",obj());if(status.get("active_operation_id")!=null||machine.isBusy()||machine.isEnabled())throw new IllegalStateException("Test control requires disabled idle fixture");future=machine.submit(()->{synchronized(bridge){return action.call();}},null,true);}T value=future.get(30,TimeUnit.SECONDS);long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);while(machine.isBusy()){if(System.nanoTime()>deadline)throw new IllegalStateException("Fixture executor did not settle");Thread.sleep(5);}return value;}
 static Length mm(double v){return new Length(v,LengthUnit.Millimeters);}
 static void limits(ReferenceControllerAxis a){a.setSoftLimitLow(mm(-100));a.setSoftLimitHigh(mm(100));a.setSoftLimitLowEnabled(true);a.setSoftLimitHighEnabled(true);}
 static Map<String,Integer> counts(){Map<String,Integer> result=new TreeMap<>();for(Feeder f:machine.getFeeders())if(f instanceof ReferenceStripFeeder)result.put(f.getId(),((ReferenceStripFeeder)f).getFeedCount());return result;}
 static Map<String,Object> persisted()throws Exception{
  byte[] bytes=NativePortableConfiguration.read(fixture.resolve("configuration/machine.xml"),8*1024*1024);Document xml=NativePortableConfiguration.xml(bytes);Element selected=null;NodeList axes=xml.getElementsByTagName("axis");
  for(int i=0;i<axes.getLength();i++){Element e=(Element)axes.item(i);if(mapped.getId().equals(e.getAttribute("id"))){if(selected!=null)throw new IllegalStateException("Duplicate persisted mapped axis");selected=e;}}
  if(selected==null)throw new IllegalStateException("Mapped axis missing from native saved XML");Map<String,Object> row=map("axis_id",selected.getAttribute("id"),"native_class",selected.getAttribute("class"),"input_axis_id",selected.getAttribute("input-axis-id"),"machine_xml_sha256",NativePortableConfiguration.sha(bytes));
  for(String name:Arrays.asList("map-input-0","map-output-0","map-input-1","map-output-1")){NodeList items=selected.getElementsByTagName(name);if(items.getLength()!=1)throw new IllegalStateException("Missing native mapped point");Element e=(Element)items.item(0);row.put(name,map("value",Double.valueOf(e.getAttribute("value")),"units",e.getAttribute("units")));}return row;
 }
 static Map<String,Object> observation()throws Exception{
  Job job=(Job)field(bridge,"job");ReferenceNozzle nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();int calibrated=0,transforms=0,validCameras=0;
  for(NozzleTip tip:machine.getNozzleTips())if(tip instanceof ReferenceNozzleTip&&((ReferenceNozzleTip)tip).getCalibration().isCalibrated(nozzle))calibrated++;
  if(job!=null)for(PlacementsHolderLocation<?> root:job.getRootPanelLocation().getChildren())if(root.getPlacementsTransformStatus()!=PlacementsHolderLocation.PlacementsTransformStatus.NotSet)transforms++;
  for(Camera camera:machine.getAllCameras())if(Boolean.TRUE.equals(((ReferenceCamera)camera).getAdvancedCalibration().isValid()))validCameras++;
  return map("test_fixture",true,"physical_qualification",false,"enabled",machine.isEnabled(),"homed",machine.isHomed(),"enable_events",enable.get(),"true_home_events",home.get(),"head_activity_events",activity.get(),"mapped_source_setter_events",links.get(),"out_of_band_source_changes",sourceChanges,"feed_counts",counts(),"job_state",field(bridge,"jobState"),"validated_board_load_revision",field(bridge,"validatedBoardLoadRevision"),"registered_native_roots",transforms,"calibrated_nozzle_tip_caches",calibrated,"valid_advanced_camera_caches",validCameras,"native_mapped",NativeMappedAxisSettings.describe(config,mapped.getId()),"persisted_mapped",persisted());
 }
 @SuppressWarnings("unchecked") static Map<String,Object> perform(String action)throws Exception{
  switch(action){
   case "seed_dependencies":return task(()->{
    Job job=(Job)field(bridge,"job");if(job==null)throw new IllegalStateException("Prepare the native sample through MCP first");NativeBoardLoads loads=(NativeBoardLoads)field(bridge,"boardLoads");
    String revision=(String)((Map<?,?>)bridge.call("openpnp_get_status",obj())).get("config_revision");loads.registrationCompleted(job,revision);
    for(PlacementsHolderLocation<?> root:job.getRootPanelLocation().getChildren()){root.setLocalToParentTransform(AffineTransform.getTranslateInstance(11,12));root.setPlacementsTransformStatus(PlacementsHolderLocation.PlacementsTransformStatus.LocallySet);}
    ReferenceNozzle nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();for(NozzleTip tip:machine.getNozzleTips())if(tip instanceof ReferenceNozzleTip){ReferenceNozzleTipCalibration c=((ReferenceNozzleTip)tip).getCalibration();((Map<String,Object>)field(c,"runoutCompensationLookup")).put(nozzle.getId(),new ReferenceNozzleTipCalibration.TableBasedRunoutCompensation(Arrays.asList(new Location(LengthUnit.Millimeters,.1,0,0,0),new Location(LengthUnit.Millimeters,-.1,0,0,180))));}
    for(Camera camera:machine.getAllCameras())((ReferenceCamera)camera).getAdvancedCalibration().setValid(true);set(bridge,"jobState","validated");set(bridge,"validatedBoardLoadRevision",loads.revision());Map<String,Object> result=observation();result.put("synthetic_validation_prerequisites",true);result.put("native_validation_or_calibration_performed",false);return result;
   });
   case "change_source":return task(()->{if(mapped.getInputAxis()!=other||sourceChanges!=0)throw new IllegalStateException("Fixed drift injection requires the once-configured alternate source");mapped.setInputAxis(raw);sourceChanges++;Map<String,Object> result=observation();result.put("synthetic_source_drift",true);result.put("configuration_saved",false);return result;});
   case "inspect":return task(()->observation());
   default:throw new IllegalArgumentException("Unknown closed test action");
  }
 }
 static void commands()throws Exception{
  try(java.util.stream.Stream<Path> list=Files.list(controls)){
   List<Path> files=new ArrayList<>();list.filter(p->p.getFileName().toString().endsWith(".request.json")).limit(17).forEach(files::add);if(files.size()>16)throw new IllegalStateException("Test command capacity exceeded");Collections.sort(files);
   for(Path path:files){String filename=path.getFileName().toString(),id=filename.substring(0,filename.length()-13);UUID.fromString(id);if(processed.contains(id))continue;if(Files.isSymbolicLink(path)||!Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)||Files.size(path)>4096)throw new IllegalStateException("Test command file rejected");processed.add(id);Map<String,Object> receipt=map("request_id",id,"test_fixture",true,"native_action_repeated",false);
    try{JsonObject command=new JsonParser().parse(Files.readString(path)).getAsJsonObject();if(command.entrySet().size()!=2||!id.equals(command.get("request_id").getAsString())||!command.has("action"))throw new IllegalArgumentException("Expected exact closed test command");String action=command.get("action").getAsString();receipt.put("action",action);receipt.put("result",perform(action));receipt.put("state","succeeded");}catch(Exception error){receipt.put("state","failed");receipt.put("error",error.getClass().getSimpleName()+": "+error.getMessage());}
    write(controls.resolve(id+".receipt.json"),receipt);
   }
  }
 }
 public static void main(String[] args)throws Exception{
  if(args.length!=4||!"--state-dir".equals(args[0])||!"--sample-root".equals(args[2]))throw new IllegalArgumentException("Expected --state-dir OWNED_STATE --sample-root VERIFIED_SAMPLES");state=Paths.get(args[1]).toRealPath();fixture=state.resolve("mapped-fixture");privateDirectory(fixture);controls=fixture.resolve("controls");privateDirectory(controls);Path configuration=fixture.resolve("configuration");privateDirectory(configuration);
  Configuration.initialize(configuration.toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);machine=config.getMachine();Bridge.verifyNativeSimulatorClasses(machine);
  for(Axis axis:machine.getAxes())if(axis.getClass()==ReferenceControllerAxis.class&&axis.getType()==Axis.Type.X)raw=(ReferenceControllerAxis)axis;limits(raw);other=new ReferenceControllerAxis();other.setName("Fixture existing alternate X");other.setType(Axis.Type.X);other.setDriver(machine.getDrivers().get(0));limits(other);machine.addAxis(other);mapped=new ReferenceMappedAxis();mapped.setName("Fixture existing mapped X");mapped.setType(Axis.Type.X);mapped.setInputAxis(raw);machine.addAxis(mapped);config.save();
  machine.addListener(new MachineListener.Adapter(){@Override public void machineEnabled(Machine m){enable.incrementAndGet();}@Override public void machineHomed(Machine m,boolean h){if(h)home.incrementAndGet();}@Override public void machineHeadActivity(Machine m,Head h){activity.incrementAndGet();}});mapped.addPropertyChangeListener("inputAxis",e->links.incrementAndGet());
  Path token=state.resolve("bridge.token");byte[] secret=new byte[32];new SecureRandom().nextBytes(secret);Files.writeString(token,Base64.getUrlEncoder().withoutPadding().encodeToString(secret)+"\n",StandardOpenOption.CREATE_NEW);Files.setPosixFilePermissions(token,PosixFilePermissions.fromString("rw-------"));
  bridge=new Bridge(config,token,state.resolve("journal"),Paths.get(args[3]),0,true);bridge.start();
  Runtime.getRuntime().addShutdownHook(new Thread(()->{closing=true;try{write(fixture.resolve("final-observation.json"),observation());}catch(Exception ignored){}try{bridge.close();machine.close();}catch(Exception ignored){}}));
  Map<?,?> caps=(Map<?,?>)bridge.call("openpnp_get_capabilities",obj());write(state.resolve("connection.json"),map("schemaVersion",1,"url","http://127.0.0.1:"+bridge.getPort()+"/","tokenFile",token.toString(),"machineId",caps.get("machine_id")));
  write(fixture.resolve("manifest.json"),map("test_fixture",true,"profile","native-simulator","public_axis_creation_api",false,"synthetic_prerequisites_supported",true,"physical_qualification",false,"mapped_axis_id",mapped.getId(),"initial_source_axis_id",raw.getId(),"alternate_source_axis_id",other.getId(),"controls_directory","controls","bridge_artifact_sha256",caps.get("bridge_artifact_sha256"),"initial",observation()));
  System.out.println("OPENPNP_MAPPED_TEST_FIXTURE_READY port="+bridge.getPort()+" upstream="+Bridge.UPSTREAM);
  while(!closing){commands();Thread.sleep(20);}
 }
}
