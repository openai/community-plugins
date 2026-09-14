/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.geom.AffineTransform;
import java.beans.PropertyChangeListener;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.*;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;

/** Actual Bridge/native executor/model/persistence tests. Reflection ONLY seeds synthetic stale
 * validation/calibration fixtures and inspects private retention; it never substitutes effect code.
 * No hardware, enable, home, motion, capture, feed, pick or job-processing calls. */
public final class NativeMappedAxisBridgeTest {
 static final Gson G=new Gson(); static Bridge bridge; static Configuration config;static Machine machine;
 static ReferenceControllerAxis raw,other;static ReferenceMappedAxis mapped;static Path root;static String session;
 static int assertions,enabled,homed,activity;static Map<String,Integer> before;static Job job;
 interface Run {void run()throws Exception;}
 static void check(boolean b,String m){assertions++;if(!b)throw new AssertionError(m);}
 static void near(double a,double e,String m){check(Double.isFinite(a)&&Math.abs(a-e)<1e-8,m+": "+a);}
 static JsonObject obj(Object...p){JsonObject j=new JsonObject();for(int i=0;i<p.length;i+=2)j.add((String)p[i],G.toJsonTree(p[i+1]));return j;}
 static JsonObject mutation(Object...p){JsonObject j=obj(p);j.addProperty("session_id",session);j.addProperty("request_id",UUID.randomUUID().toString());return j;}
 @SuppressWarnings("unchecked") static Map<String,Object> call(String n,JsonObject p)throws Exception{return(Map<String,Object>)bridge.call(n,p);}
 static Map<String,Object> status()throws Exception{return call("openpnp_get_status",obj());}
 static String revision()throws Exception{return(String)status().get("config_revision");}
 static Object field(Object o,String name)throws Exception {Field f=o.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(o);}
 static void set(Object o,String name,Object value)throws Exception{Field f=o.getClass().getDeclaredField(name);f.setAccessible(true);f.set(o,value);}
 static <T>T task(Callable<T> c)throws Exception {T value=machine.submit(c,null,true).get(30,TimeUnit.SECONDS);idle();return value;}
 static void idle()throws Exception{long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);while(machine.isBusy()){if(System.nanoTime()>until)throw new AssertionError("native executor remained busy");Thread.sleep(5);}}
 static Map<String,Object> terminal(String method,JsonObject p,String expected)throws Exception{
  Map<String,Object> accepted=call(method,p);String id=(String)accepted.get("operation_id");long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(30);
  while(true){Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",id));String s=(String)op.get("state");if(Arrays.asList("succeeded","failed","outcome_unknown","cancelled").contains(s)){check(expected.equals(s),method+" expected "+expected+": "+op);idle();return op;}if(System.nanoTime()>until)throw new AssertionError("native operation deadline");Thread.sleep(5);}
 }
 static Map<?,?> result(Map<String,Object> op){return(Map<?,?>)op.get("result");}
 static void expect(String code,Run r)throws Exception {try{r.run();throw new AssertionError("expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"expected "+code+" got "+e.code);}}
 static Length mm(double n){return new Length(n,LengthUnit.Millimeters);}
 static void limits(ReferenceControllerAxis a){a.setSoftLimitLow(mm(-100));a.setSoftLimitHigh(mm(100));a.setSoftLimitLowEnabled(true);a.setSoftLimitHighEnabled(true);}
 static Map<String,Integer> counts(){Map<String,Integer> r=new TreeMap<>();for(Feeder f:machine.getFeeders())if(f instanceof ReferenceStripFeeder)r.put(f.getId(),((ReferenceStripFeeder)f).getFeedCount());return r;}
 static JsonObject change(){return obj("type",NativeMappedAxisSettings.TYPE,"axis_id",mapped.getId(),"input_axis_id",other.getId(),"input_0_mm",5,"output_0_mm",-20,"input_1_mm",15,"output_1_mm",0);}
 static Map<String,Object> plan(JsonObject row)throws Exception{JsonArray a=new JsonArray();a.add(row);return call("openpnp_plan_configuration",mutation("changes",a,"expected_config_revision",revision()));}
 static Map<String,Object> apply(Map<String,Object> plan,String state)throws Exception{return terminal("openpnp_apply_configuration",mutation("plan_id",plan.get("plan_id"),"expected_config_revision",revision()),state);}
 static void setup(Path samples)throws Exception{
  Files.createDirectories(root);Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();machine=config.getMachine();
  task(()->{for(Axis a:machine.getAxes())if(a.getClass()==ReferenceControllerAxis.class&&a.getType()==Axis.Type.X)raw=(ReferenceControllerAxis)a;limits(raw);
   other=new ReferenceControllerAxis();other.setType(Axis.Type.X);other.setName("Alternate existing native X");other.setDriver(machine.getDrivers().get(0));limits(other);machine.addAxis(other);
   mapped=new ReferenceMappedAxis();mapped.setName("Existing direct mapped X");mapped.setType(Axis.Type.X);mapped.setInputAxis(raw);machine.addAxis(mapped);config.save();return null;});
  Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());bridge=new Bridge(config,token,root.resolve("journal"),samples,0,true);
  session=(String)call("openpnp_request_control_session",obj("request_id","mapped-grant","ttl_seconds",300)).get("session_id");
  machine.addListener(new MachineListener.Adapter(){@Override public void machineEnabled(Machine m){enabled++;}@Override public void machineHomed(Machine m,boolean h){if(h)homed++;}@Override public void machineHeadActivity(Machine m,Head h){activity++;}});
  before=counts();
 }
 @SuppressWarnings("unchecked") static NativeBoardLoads seedDependencies()throws Exception{
  terminal("openpnp_prepare_job",mutation("sample","pnp-test"),"succeeded");job=(Job)field(bridge,"job");NativeBoardLoads loads=(NativeBoardLoads)field(bridge,"boardLoads");
  task(()->{loads.registrationCompleted(job,revision());for(PlacementsHolderLocation<?> r:job.getRootPanelLocation().getChildren()){r.setLocalToParentTransform(AffineTransform.getTranslateInstance(11,12));r.setPlacementsTransformStatus(PlacementsHolderLocation.PlacementsTransformStatus.LocallySet);}
   for(Camera camera:machine.getAllCameras())((org.openpnp.machine.reference.camera.ReferenceCamera)camera).getAdvancedCalibration().setValid(true);
   ReferenceNozzle nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();for(NozzleTip tip:machine.getNozzleTips())if(tip.getClass()==ReferenceNozzleTip.class){ReferenceNozzleTipCalibration c=((ReferenceNozzleTip)tip).getCalibration();Map<String,Object> lookup=(Map<String,Object>)field(c,"runoutCompensationLookup");lookup.put(nozzle.getId(),new ReferenceNozzleTipCalibration.TableBasedRunoutCompensation(Arrays.asList(new Location(LengthUnit.Millimeters,0.1,0,0,0),new Location(LengthUnit.Millimeters,-0.1,0,0,180))));check(c.isCalibrated(nozzle),"synthetic native runout cache is populated");}return null;});
  // Seed validation *evidence state only*. No native validation/home/vision success is claimed.
  synchronized(bridge){set(bridge,"jobState","validated");set(bridge,"validatedBoardLoadRevision",loads.revision());}
  return loads;
 }
 static void dependenciesRevoked(String old)throws Exception{
  check(!old.equals(revision()),"revision advances before axis setter");check("prepared".equals(field(bridge,"jobState")),"job validation revoked before axis setter");
  check(field(bridge,"validatedBoardLoadRevision")==null,"validated load revision cleared before setter");
  check(((Map<?,?>)field(bridge,"plans")).isEmpty()&&((Map<?,?>)field(bridge,"configurationPlans")).isEmpty(),"all generic/retained plans cleared before setter");
  for(PlacementsHolderLocation<?> r:job.getRootPanelLocation().getChildren())check(r.getPlacementsTransformStatus()==PlacementsHolderLocation.PlacementsTransformStatus.NotSet,"native registration transform cleared before setter");
  ReferenceNozzle n=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();for(NozzleTip tip:machine.getNozzleTips())if(tip instanceof ReferenceNozzleTip)check(!((ReferenceNozzleTip)tip).getCalibration().isCalibrated(n),"native runout cache revoked before setter");
  for(Camera camera:machine.getAllCameras())check(Boolean.FALSE.equals(((org.openpnp.machine.reference.camera.ReferenceCamera)camera).getAdvancedCalibration().isValid()),"dormant advanced camera calibration validity revoked before setter");
  check(!machine.isHomed(),"homed observation false before setter");String journal=Files.readString(root.resolve("journal/operations.jsonl"));check(journal.contains("mapped-axis-configuration-model")&&journal.contains("mapped-axis-geometry")&&journal.contains("configuration_change_started"),"durable effect/revision/registration records precede setter");
 }
 static void success()throws Exception{
  NativeBoardLoads loads=seedDependencies();Map<String,Boolean> history=new TreeMap<>(job.getPlacedStatusSnapshot());String loadsBefore=loads.revision();String beforeLoad=G.toJson(loads.snapshot());String old=revision();
  plan(obj("type","set_machine_speed","speed",0.8));Map<String,Object> p=plan(change());AtomicInteger setter=new AtomicInteger();
  PropertyChangeListener observe=e->{setter.incrementAndGet();try{dependenciesRevoked(old);}catch(Exception x){throw new RuntimeException(x);}};mapped.addPropertyChangeListener("inputAxis",observe);
  try{apply(p,"succeeded");}finally{mapped.removePropertyChangeListener("inputAxis",observe);}
  check(setter.get()==1,"one actual native link setter callback");check(mapped.getInputAxis()==other,"existing exact source object applied");near(mapped.toTransformed(new AxesLocation(other,42.5)).getCoordinate(mapped),55,"independent native forward golden");near(mapped.toRaw(new AxesLocation(mapped,55)).getCoordinate(other),42.5,"independent native inverse golden");
  check(history.equals(job.getPlacedStatusSnapshot()),"all native placed-history keys preserved");check(loadsBefore.equals(loads.revision()),"registration invalidation does not replace logical load");check(!beforeLoad.equals(G.toJson(loads.snapshot())),"load registration observation changed");check(G.toJson(loads.snapshot()).contains("mapped-axis-geometry"),"load snapshot explicitly revoked");
  check(Files.readString(root.resolve("config/machine.xml")).contains("ReferenceMappedAxis"),"actual native save retains mapped topology");
  Map<?,?> described=(Map<?,?>)((Map<?,?>)status().get("machine")).get("settings");check(G.toJson(described).contains("mapped_geometry_editable"),"Bridge snapshot exposes native typed mapped metadata");
  NativeConfigurationSnapshots.Snapshot snap=task(()->NativeConfigurationSnapshots.capture(config));check(G.toJson(snap.document).contains("MAPPED_GEOMETRY_NOT_IN_VERSION_ONE_RESTORE"),"version1 snapshot explicit mapped omission");check(G.toJson(snap.document).contains("MAPPED_SOURCE_LIMITS_UNSUPPORTED"),"source-limit snapshot omission is explicit");
  JsonObject forged=G.toJsonTree(snap.document).getAsJsonObject();forged.getAsJsonArray("typed_changes").add(change());expect("SNAPSHOT_INVALID",()->NativeConfigurationSnapshots.decode(forged));
  // Independently prove unchanged portable admission rejects the actual saved mapped class.
  expect("CLASS_REJECTED",()->NativePortableConfiguration.validate(NativePortableConfiguration.xml(Files.readAllBytes(root.resolve("config/machine.xml"))),"machine.xml"));
  // Full export must also refuse this current unsupported configuration before archive publication.
  try{task(()->NativePortableConfiguration.export(config,root.resolve("must-not-export.zip")));throw new AssertionError("portable unexpectedly accepted mapped axis");}catch(ExecutionException e){check(e.getCause() instanceof Bridge.Fault,"portable retains typed refusal");check(!Files.exists(root.resolve("must-not-export.zip")),"portable unsupported class does not publish archive");}
  Files.writeString(root.resolve("expected.json"),G.toJson(obj("axis",mapped.getId(),"source",other.getId(),"counters",counts())));
 }
 static void stale()throws Exception{
  String rev=revision();Map<String,Object> p=plan(change());task(()->{mapped.setInputAxis(other);return null;});Map<String,Object> op=apply(p,"failed");check("STALE_AXIS_PLAN".equals(result(op).get("code")),"same-revision native source drift rejects original plan");check(rev.equals(revision()),"stale preflight does not advance revision");check("PLAN_CONSUMED".equals(result(apply(p,"failed")).get("code")),"stale attempt consumes retained guard");
  task(()->{mapped.setInputAxis(raw);return null;});p=plan(change());ReferenceNozzle nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();task(()->{nozzle.setAxisX(other);return null;});check("STALE_AXIS_PLAN".equals(result(apply(p,"failed")).get("code")),"shared native consumer reassignment rejects original plan");
  task(()->{nozzle.setAxisX(raw);return null;});p=plan(change());task(()->{raw.setSoftLimitHigh(mm(50));return null;});check("STALE_AXIS_PLAN".equals(result(apply(p,"failed")).get("code")),"source envelope drift invalidates original guard");
  expect("MAPPED_SOURCE_LIMITS_UNSUPPORTED",()->plan(obj("type","set_axis_motion_limits","axis_id",raw.getId(),"soft_limit_low_mm",-100,"soft_limit_high_mm",100,"feedrate_mm_per_s",10,"acceleration_mm_per_s2",100,"jerk_mm_per_s3",0)));
  org.openpnp.machine.reference.camera.ReferenceCamera camera=(org.openpnp.machine.reference.camera.ReferenceCamera)machine.getDefaultHead().getDefaultCamera();
  task(()->{camera.setEnableUnitsPerPixel3D(true);return null;});expect("UNSUPPORTED_CAMERA_GEOMETRY",()->plan(change()));task(()->{camera.setEnableUnitsPerPixel3D(false);camera.getAdvancedCalibration().setEnabled(true);return null;});expect("UNSUPPORTED_CAMERA_GEOMETRY",()->plan(change()));task(()->{camera.getAdvancedCalibration().setEnabled(false);return null;});
  JsonArray mixed=new JsonArray();mixed.add(change());mixed.add(obj("type","set_machine_speed","speed",0.5));expect("INVALID_PATCH",()->call("openpnp_plan_configuration",mutation("changes",mixed)));
 }
 static void failure(String mode)throws Exception{
  seedDependencies();String old=revision();Map<String,Object> p=plan(change());AtomicInteger setters=new AtomicInteger();
  PropertyChangeListener listener=e->{setters.incrementAndGet();try{dependenciesRevoked(old);if("save-failure".equals(mode)){Path f=root.resolve("config/machine.xml");Files.delete(f);Files.createDirectory(f);}else if("fatal".equals(mode))throw new AssertionError("injected post-setter fatal native listener");else throw new IllegalStateException("injected post-setter ordinary native listener");}catch(Exception x){throw new RuntimeException(x);}};
  mapped.addPropertyChangeListener("inputAxis",listener);JsonObject args=mutation("plan_id",p.get("plan_id"),"expected_config_revision",revision());Map<String,Object> op;
  try{op=terminal("openpnp_apply_configuration",args,"outcome_unknown");}finally{mapped.removePropertyChangeListener("inputAxis",listener);}
  check(setters.get()==1&&mapped.getInputAxis()==other,"actual setter effect remains once after failure");check(Boolean.TRUE.equals(field(bridge,"configurationFault")),"configuration admission is fenced");check(!old.equals(revision()),"failed apply retains advanced revision");
  check(Boolean.TRUE.equals(op.get("native_effect_pending")),"failed effect remains pending instead of safe replay");
  // Fault guard rejects even same-request dispatch; original outcome stays available by read-only lookup.
  expect("CONFIGURATION_FAULT",()->call("openpnp_apply_configuration",args));Map<String,Object> receipt=call("openpnp_get_request_status",obj("request_id",args.get("request_id").getAsString()));check(Boolean.TRUE.equals(receipt.get("found")),"original failed request receipt remains discoverable");check(setters.get()==1,"no failed setter replay");
 }
 @SuppressWarnings("unchecked") static void lifecycle()throws Exception{
  Map<String,Object> first=plan(change());for(int i=0;i<128;i++)plan(change());check(((Map<?,?>)field(bridge,"plans")).size()==128&&((Map<?,?>)field(bridge,"configurationPlans")).size()==128,"native references obey128plan cap");check(!((Map<?,?>)field(bridge,"configurationPlans")).containsKey(first.get("plan_id")),"generic oldest eviction removes native guard");
  Map<String,Long> deadlines=(Map<String,Long>)field(bridge,"planDeadlines");for(String id:new ArrayList<>(deadlines.keySet()))deadlines.put(id,0L);status();check(((Map<?,?>)field(bridge,"configurationPlans")).isEmpty(),"expired plans release all retained native references");
  Map<String,Object> p=plan(change());call("openpnp_release_control_session",mutation());check(((Map<?,?>)field(bridge,"configurationPlans")).isEmpty(),"ownership release immediately clears retained references");session=(String)call("openpnp_request_control_session",obj("request_id","second-grant","ttl_seconds",300)).get("session_id");check("PLAN_STALE".equals(result(apply(p,"failed")).get("code")),"new owner cannot apply old plan");
 }
 static final class MonitorCheckedReceipt extends LinkedHashMap<String,Object>{
  final List<String> violations=new ArrayList<>();MonitorCheckedReceipt(Map<String,Object> original){super(original);}
  @Override public Object put(String key,Object value){if(!Thread.holdsLock(bridge))violations.add(key);return super.put(key,value);}
 }
 static final class MonitorCheckedOperations extends LinkedHashMap<String,Map<String,Object>>{
  final List<MonitorCheckedReceipt> versions=new ArrayList<>();
  @Override public Map<String,Object> put(String id,Map<String,Object> value){MonitorCheckedReceipt tracked=new MonitorCheckedReceipt(value);versions.add(tracked);return super.put(id,tracked);}
 }
 @SuppressWarnings("unchecked") static void receiptLock()throws Exception{
  MonitorCheckedOperations tracked=new MonitorCheckedOperations();String id;
  synchronized(bridge){
   Map<String,Map<String,Object>> prior=(Map<String,Map<String,Object>>)field(bridge,"operations");
   prior.forEach(tracked::put);set(bridge,"operations",tracked);
   Map<String,Object> accepted=call("openpnp_prepare_job",mutation("sample","pnp-test"));id=(String)accepted.get("operation_id");
  }
  long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(30);
  while(true){Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",id));String state=(String)op.get("state");if("succeeded".equals(state))break;if("failed".equals(state)||"outcome_unknown".equals(state)||System.nanoTime()>until)throw new AssertionError("native prepare did not complete: "+op);Thread.sleep(1);}
  idle();synchronized(bridge){
   check(tracked.versions.size()>=3,"observed accepted/running/completed committed map generations");
   check(tracked.versions.stream().allMatch(v->v.violations.isEmpty()),"every native receipt update holds Bridge monitor across immutable map replacement");
   check(tracked.versions.stream().anyMatch(v->v.containsKey("native_effect_pending")&&v.containsKey("native_effect_kind")),"real native load sink published effect receipt on a monitored generation");
  }
  check(Files.readString(root.resolve("journal/operations.jsonl")).contains("board_load_outcome"),"actual native logical board load completed");
 }
 static void reload()throws Exception{
  JsonObject ids=new JsonParser().parse(Files.readString(root.resolve("expected.json"))).getAsJsonObject();Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();machine=config.getMachine();mapped=(ReferenceMappedAxis)machine.getAxis(ids.get("axis").getAsString());other=(ReferenceControllerAxis)machine.getAxis(ids.get("source").getAsString());check(mapped.getInputAxis()==other,"separate JVM native reload resolves exact source object");near(mapped.toTransformed(new AxesLocation(other,42.5)).getCoordinate(mapped),55,"reloaded native golden coordinate");check(G.toJsonTree(counts()).equals(ids.get("counters")),"native reload preserves finite feed counters");before=counts();
 }
 private static String digest(byte[] bytes)throws Exception{byte[] d=java.security.MessageDigest.getInstance("SHA-256").digest(bytes);StringBuilder s=new StringBuilder();for(byte b:d)s.append(String.format("%02x",b&255));return s.toString();}
 private static void canonicalSuite(Path samples)throws Exception{
  Path evidence=Files.createTempDirectory("openpnp-mapped-native-suite-").toRealPath();
  List<String> phases=Arrays.asList("success","reload","stale","listener","fatal","save-failure","lifecycle","receipt-lock");
  List<Object> results=new ArrayList<>();boolean passed=true;int total=0;
  for(String phase:phases){
   Path fixture=evidence.resolve(phase.equals("success")||phase.equals("reload")?"roundtrip":phase);
   Path log=evidence.resolve(phase+".log");List<String> command=new ArrayList<>();
   command.add(Paths.get(System.getProperty("java.home"),"bin","java").toString());
   command.addAll(java.lang.management.ManagementFactory.getRuntimeMXBean().getInputArguments());
   command.add("-Djava.io.tmpdir="+evidence);command.add("-cp");command.add(System.getProperty("java.class.path"));
   command.add(NativeMappedAxisBridgeTest.class.getName());command.add(phase);command.add(fixture.toString());command.add(samples.toRealPath().toString());
   ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());
   builder.environment().keySet().removeIf(key->{String k=key.toUpperCase(Locale.ROOT);return Arrays.asList("JAVA_TOOL_OPTIONS","_JAVA_OPTIONS","JDK_JAVA_OPTIONS","CLASSPATH").contains(k)||k.startsWith("LD_")||k.startsWith("DYLD_");});
   Process child=builder.start();boolean forced=false;int code=-1;
   try{if(!child.waitFor(60,TimeUnit.SECONDS)){forced=true;child.destroy();if(!child.waitFor(5,TimeUnit.SECONDS)){child.destroyForcibly();child.waitFor(5,TimeUnit.SECONDS);}}if(!child.isAlive())code=child.exitValue();}
   finally{if(child.isAlive()){forced=true;child.destroyForcibly();child.waitFor(5,TimeUnit.SECONDS);}}
   JsonObject record=null;int rows=0;for(String line:Files.readAllLines(log))if(line.startsWith("MAPPED_AXIS_INTEGRATION ")){rows++;record=new JsonParser().parse(line.substring("MAPPED_AXIS_INTEGRATION ".length())).getAsJsonObject();}
   boolean ok=code==0&&!forced&&!child.isAlive()&&rows==1&&record.get("passed").getAsBoolean()&&phase.equals(record.get("phase").getAsString());
   if(ok)total+=record.get("assertions").getAsInt();passed &=ok;results.add(obj("phase",phase,"passed",ok,"exit_code",code,"forced_cleanup",forced,"record",record,"log_sha256",digest(Files.readAllBytes(log))));
   // No dependent reload is attempted if the writing phase failed.
   if(!ok)break;
  }
  JsonObject report=obj("passed",passed&&results.size()==8&&total==167,"phases",results,"assertions",total,"evidence_directory",evidence.toString(),"fresh_jvms",results.size(),"hardware_qualified",false,"synthetic_validation_prerequisites",true);
  Files.writeString(evidence.resolve("report.json"),G.toJson(report)+"\n",StandardOpenOption.CREATE_NEW);
  System.out.println("OPENPNP_NATIVE_MAPPED_AXIS_RESULT "+G.toJson(report));
  if(!report.get("passed").getAsBoolean())throw new AssertionError("Mapped native phase failed; retained "+evidence);
 }
 public static void main(String[] args)throws Exception{
  if(args.length==1){canonicalSuite(Paths.get(args[0]));return;}
  if(args.length!=3)throw new IllegalArgumentException("Expected sample-root, or internal phase fixture-root sample-root");
  String phase=args[0];root=Paths.get(args[1]);Throwable failure=null;try{if("reload".equals(phase))reload();else{setup(Paths.get(args[2]));switch(phase){case"success":success();break;case"stale":stale();break;case"listener":case"fatal":case"save-failure":failure(phase);break;case"lifecycle":lifecycle();break;case"receipt-lock":receiptLock();break;default:throw new IllegalArgumentException(phase);}}
   check(before.equals(counts()),"all native finite feeder counters unchanged");check(!machine.isEnabled()&&!machine.isHomed(),"native machine remains disabled/unhomed");check(enabled==0&&homed==0&&activity==0,"zero enable/true-home/head-activity events");
  }catch(Throwable t){failure=t;t.printStackTrace();}finally{if(bridge!=null)bridge.close();if(machine!=null)machine.close();}
  System.out.println("MAPPED_AXIS_INTEGRATION "+G.toJson(obj("phase",phase,"passed",failure==null,"assertions",assertions,"enable_events",enabled,"true_home_events",homed,"head_activity_events",activity,"native_placement_calls",0,"physical_qualification",false,"synthetic_validation_prerequisites",true)));System.exit(failure==null?0:1);
 }
}
