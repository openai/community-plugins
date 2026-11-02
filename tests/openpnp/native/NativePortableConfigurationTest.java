/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.camera.ImageCamera;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.w3c.dom.*;

public final class NativePortableConfigurationTest {
 static final Gson G=new Gson();static int checks;
 static void check(boolean v,String message){checks++;if(!v)throw new AssertionError(message);}
 static void json(Path p,Object v)throws Exception {Files.writeString(p,G.toJson(v));}
 public static void main(String[] args)throws Exception {
  if(args.length==1){Path root=Files.createTempDirectory("native-portable-configuration-");for(String phase:List.of("seed","produce","boundaries","adopt","verify","unknown-field","crash-stage","verify-crash")){
   java.util.List<String> command=new ArrayList<>(List.of(Paths.get(System.getProperty("java.home"),"bin/java").toString(),"-Xmx2g","-XX:+ExitOnOutOfMemoryError","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Duser.home="+root,"-Djava.io.tmpdir="+root,"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativePortableConfigurationTest.class.getName(),phase,root.toString()));
   Process child=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(root.resolve(phase+".log").toFile()).start();if(!child.waitFor(90,TimeUnit.SECONDS)){child.destroyForcibly();throw new AssertionError("Portable phase timed out: "+phase);}check(child.exitValue()==(phase.equals("crash-stage")?37:0),"Portable phase "+phase+" failed: "+Files.readString(root.resolve(phase+".log")));}
   System.out.println("OPENPNP_PORTABLE_SUITE_RESULT {\"phases\":8,\"actual_placements\":2,\"hardware_qualified\":false}");return;
  }
  Path root=Paths.get(args[1]).toAbsolutePath();String mode=args[0];
  try {
   if(mode.equals("seed"))seed(root);
   else if(mode.equals("produce"))produce(root);
   else if(mode.equals("adopt")){Map<?,?> receipt=G.fromJson(Files.readString(root.resolve("export.json")),Map.class);NativePortableConfiguration.Prepared p=NativePortableConfiguration.prepare(root.resolve("configuration.zip"),(String)receipt.get("sha256"),root.resolve("adopted"));json(root.resolve("adoption-result.json"),NativePortableConfiguration.validateAndPublish(p));}
   else if(mode.equals("verify"))verify(root);
   else if(mode.equals("boundaries"))boundaries(root);
   else if(mode.equals("unknown-field"))unknownField(root);
   else if(mode.equals("crash-stage"))crash(root);
   else if(mode.equals("verify-crash"))verifyCrash(root);
   else throw new IllegalArgumentException(mode);
   System.out.println("OPENPNP_PORTABLE_RESULT "+G.toJson(Bridge.map("phase",mode,"checks",checks,"simulation_only",true,"physical_qualification",false)));
  }catch(Throwable error){error.printStackTrace();System.exit(1);}System.exit(0);
 }
 static void seed(Path root)throws Exception {
  Files.createDirectories(root);Path source=root.resolve("source-config");Configuration.initialize(source.toFile());Configuration c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.configureSustainedWorkload(c);
  try {
   ImageCamera camera=(ImageCamera)c.getMachine().getDefaultHead().getDefaultCamera();Path resource=source.resolve("images/source.png");Files.createDirectories(resource.getParent());try(var in=NativePortableConfigurationTest.class.getClassLoader().getResourceAsStream("samples/pnp-test/pnp-test.png")){Files.copy(in,resource);}camera.setSourceUri(resource.toUri().toString());camera.setRedGamma(1.15);camera.setSettleTimeMs(7);
   ((ReferenceNozzle)c.getMachine().getDefaultHead().getDefaultNozzle()).setPickDwellMilliseconds(3);c.getPart("R0603-1K").setName("Portable native part");c.getPart("R0603-1K").setSpeed(0.9);c.getPackage("R0603").setDescription("Portable package fields retained");
   NativeSettings.stage(c,rows("{\"type\":\"clone_vision_settings\",\"source_vision_settings_id\":\"BVS_Stock\",\"vision_settings_id\":\"BVS_Portable\",\"name\":\"Portable configured vision\"}")).apply();
   NativeSettings.stage(c,rows("{\"type\":\"set_vision_parameter\",\"vision_settings_id\":\"BVS_Portable\",\"parameter_name\":\"pThreshold\",\"value\":128}")).apply();c.getPackage("R0603").setBottomVisionSettings((BottomVisionSettings)c.getVisionSettings("BVS_Portable"));
   for(Feeder f:c.getMachine().getFeeders())((ReferenceTrayFeeder)f).setFeedCount(7);
   Path script=source.resolve("scripts/Events/Job.Starting.js");Files.createDirectories(script.getParent());Files.writeString(script,"java.nio.file.Files.writeString(java.nio.file.Paths.get("+G.toJson(root.resolve("SCRIPT_EXECUTED").toString())+"), 'unsafe');\n");
   c.save();check(!c.getMachine().isEnabled(),"seed prepares simulator configuration without enabling");
  }finally{c.getMachine().close();}
 }
 static void produce(Path root)throws Exception {
  Configuration.initialize(root.resolve("source-config").toFile());Configuration c=Configuration.get();c.load();
  try {
   Path history=root.resolve("source-operational-journal.jsonl");Files.writeString(history,"{\"sequence\":91,\"material_count\":123,\"unknown\":true}\n");String historyHash=NativePortableConfiguration.sha(Files.readAllBytes(history));
   json(root.resolve("source-readback.json"),NativeSettings.describe(c));Map<String,Object> receipt=NativePortableConfiguration.export(c,root.resolve("configuration.zip"));json(root.resolve("export.json"),receipt);
   check(historyHash.equals(NativePortableConfiguration.sha(Files.readAllBytes(history))),"export cannot rewrite operational journal");check(!Files.exists(root.resolve("SCRIPT_EXECUTED")),"export cannot activate scripts");check(c.getVisionSettings("BVS_Portable")!=null,"native detailed vision profile exported");
  }finally{c.getMachine().close();}
 }
 static void verify(Path root)throws Exception {
  Path adopted=root.resolve("adopted");Path active=NativePortableConfiguration.activeConfiguration(adopted);Configuration.initialize(active.toFile());Configuration c=Configuration.get();c.load();
  try {
   JsonObject receipt=new JsonParser().parse(Files.readString(root.resolve("export.json"))).getAsJsonObject();check(G.toJsonTree(NativePortableConfiguration.canonicalFiles(c)).equals(receipt.getAsJsonObject("manifest").get("native_xml_semantic_sha256")),"all seven native XML documents retain every serialized field after a second JVM load/save");
   check(!c.getMachine().isEnabled()&&!c.getMachine().isHomed(),"adoption transfers no enable/home authority");check(c.getBoards().isEmpty()&&c.getPanels().isEmpty(),"job/board libraries are explicitly separate");
   try(var scripts=Files.walk(active.resolve("scripts"))){check(scripts.noneMatch(Files::isRegularFile),"active scripting tree is empty");}check(Files.isRegularFile(active.getParent().resolve("quarantine/scripts/Events/Job.Starting.js")),"script bytes retained outside active scripting tree");
   check(((Number)c.getVisionSettings("BVS_Portable").getPipelineParameterAssignments().get("pThreshold")).intValue()==128,"parameter binding survived exact native persistence");check(c.getPackage("R0603").getBottomVisionSettings()==c.getVisionSettings("BVS_Portable"),"vision inheritance assignment preserved");check(((ImageCamera)c.getMachine().getDefaultHead().getDefaultCamera()).getSourceUri().startsWith(adopted.toUri().toString()),"image resource relocated into adopted owned tree");
   int before=feeds(c);check(before==7*c.getMachine().getFeeders().size(),"captured finite material counts retained as simulator data");
   Job job=CanonicalJobImporter.load(c,NativeBoardLoadsTest.canonical(c.getPart("R0603-1K")));Machine m=c.getMachine();
   m.submit(()->{m.setEnabled(true);m.home();ReferencePnpJobProcessor p=(ReferencePnpJobProcessor)m.getPnpJobProcessor();p.initialize(job);int steps=0;while(p.next())if(++steps>1000)throw new AssertionError("unexpected native loop");m.getMotionPlanner().waitForCompletion(null,MotionPlanner.CompletionType.WaitForStillstand);return null;},null,true).get(60,TimeUnit.SECONDS);
   int placed=0;for(BoardLocation b:job.getBoardLocations())for(Placement p:b.getBoard().getPlacements())if(job.retrievePlacedStatus(b,p.getId()))placed++;
   check(placed==2&&feeds(c)==before+2,"adopted real native processor consumes exactly two parts and records two placements");check(!Files.exists(root.resolve("SCRIPT_EXECUTED")),"quarantined event script cannot execute during actual native job");
   json(root.resolve("native-workload.json"),Bridge.map("native_placements",placed,"feed_delta",feeds(c)-before,"source_feed_counts",before,"physical_state_transferred",false,"source_journal_unchanged",Files.readString(root.resolve("source-operational-journal.jsonl")).contains("123")));
  }finally{c.getMachine().close();}
 }
 interface Test {void run()throws Exception;}
 static void reject(String code,Test action)throws Exception{try{action.run();throw new AssertionError("expected "+code);}catch(Bridge.Fault e){check(e.code.equals(code),"expected "+code+" got "+e.code);}}
 static void boundaries(Path root)throws Exception {
  Path archive=root.resolve("configuration.zip");String hash=(String)G.fromJson(Files.readString(root.resolve("export.json")),Map.class).get("sha256");Path b=root.resolve("boundary");Files.createDirectories(b);
  reject("INTEGRITY",()->NativePortableConfiguration.prepare(archive,"0".repeat(64),b.resolve("hash")));check(!Files.exists(b.resolve("hash")),"wrong digest never publishes a destination");
  Path existing=b.resolve("existing");Files.createDirectory(existing);Path journal=existing.resolve("operations.jsonl");Files.writeString(journal,"newer history remains");reject("DESTINATION_EXISTS",()->NativePortableConfiguration.prepare(archive,hash,existing));check(Files.readString(journal).equals("newer history remains"),"existing operational/material history remains untouched");
  Path link=b.resolve("link.zip");Files.createSymbolicLink(link,archive);reject("PATH_REJECTED",()->NativePortableConfiguration.prepare(link,hash,b.resolve("symlink")));
  Map<String,byte[]> original=NativePortableConfiguration.unzip(Files.readAllBytes(archive));Map<String,byte[]> traversal=new TreeMap<>(original);traversal.put("../escaped",new byte[]{1});Path evil=b.resolve("traversal.zip");byte[] traversalBytes=NativePortableConfiguration.zip(traversal);Files.write(evil,traversalBytes);reject("PATH_REJECTED",()->NativePortableConfiguration.prepare(evil,NativePortableConfiguration.sha(traversalBytes),b.resolve("traversal")));check(!Files.exists(b.resolve("escaped")),"ZIP traversal is rejected before extraction");
  Map<String,byte[]> classes=new TreeMap<>(original);Document d=NativePortableConfiguration.xml(classes.get("config/machine.xml"));((Element)d.getElementsByTagName("machine").item(0)).setAttribute("class","java.lang.ProcessBuilder");classes.put("config/machine.xml",NativePortableConfiguration.encode(d));byte[] changed=rehash(classes);Path cls=b.resolve("classes.zip");Files.write(cls,changed);reject("CLASS_REJECTED",()->NativePortableConfiguration.prepare(cls,NativePortableConfiguration.sha(changed),b.resolve("classes")));check(!Files.exists(b.resolve("classes")),"a recomputed hash cannot authorize a non-allowlisted native class");
  Map<String,byte[]> oversize=new TreeMap<>(original);oversize.put("quarantine/scripts/too-big.js",new byte[NativePortableConfiguration.MAX_FILE+1]);byte[] large=NativePortableConfiguration.zip(oversize);Path lg=b.resolve("large.zip");Files.write(lg,large);reject("CAPACITY",()->NativePortableConfiguration.prepare(lg,NativePortableConfiguration.sha(large),b.resolve("large")));
  Map<String,byte[]> xxe=new TreeMap<>(original);xxe.put("config/machine.xml","<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///never-read'>]><openpnp-machine>&e;</openpnp-machine>".getBytes());byte[] xxeBytes=rehashRaw(xxe);Path xx=b.resolve("xxe.zip");Files.write(xx,xxeBytes);try{NativePortableConfiguration.prepare(xx,NativePortableConfiguration.sha(xxeBytes),b.resolve("xxe"));throw new AssertionError("XXE accepted");}catch(org.xml.sax.SAXParseException expected){checks++;}check(!Files.exists(b.resolve("xxe")),"DTD rejected before native load/publication");
  NativePortableConfiguration.Prepared collision=NativePortableConfiguration.prepare(archive,hash,b.resolve("collision"));Path activation=collision.destination.resolve("adoption.json");Files.writeString(activation,"existing activation stays");reject("ACTIVATION_CONFLICT",()->NativePortableConfiguration.validateAndPublish(collision));check(Files.readString(activation).equals("existing activation stays"),"post-prepare activation collision cannot be overwritten");
  NativePortableConfiguration.Prepared injected=NativePortableConfiguration.prepare(archive,hash,b.resolve("injected"));Path injectedScript=injected.staging.resolve("config/scripts/Events/Job.Starting.js");Files.createDirectories(injectedScript.getParent());Files.writeString(injectedScript,"malicious late script");reject("STAGING_CHANGED",()->NativePortableConfiguration.validateAndPublish(injected));check(!Files.exists(injected.destination.resolve("adoption.json")),"late staged script is rejected before native initialization");
  json(root.resolve("boundary-result.json"),Bridge.map("checks",checks,"native_machine_started",false));
 }
 static void unknownField(Path root)throws Exception {
  Map<String,byte[]> entries=NativePortableConfiguration.unzip(Files.readAllBytes(root.resolve("configuration.zip")));Document doc=NativePortableConfiguration.xml(entries.get("config/machine.xml"));Element field=doc.createElement("unknown-native-field");field.setTextContent("must never silently disappear");doc.getElementsByTagName("machine").item(0).appendChild(field);entries.put("config/machine.xml",NativePortableConfiguration.encode(doc));byte[] changed=rehash(entries);Path file=root.resolve("unknown-field.zip");Files.write(file,changed);Path dest=root.resolve("unknown-adopted");NativePortableConfiguration.Prepared staged=NativePortableConfiguration.prepare(file,NativePortableConfiguration.sha(changed),dest);
  try{NativePortableConfiguration.validateAndPublish(staged);throw new AssertionError("Unknown native field was accepted");}catch(Exception expected){check(!Files.exists(dest.resolve("adoption.json")),"failed native interpretation never activates a partial tree");check(Files.isRegularFile(staged.staging.resolve("ADOPTION_FAILED.txt")),"failed staged evidence retained");}json(root.resolve("unknown-field-result.json"),Bridge.map("checks",checks,"partial_tree_published",false));
 }
 static void crash(Path root)throws Exception {String hash=(String)G.fromJson(Files.readString(root.resolve("export.json")),Map.class).get("sha256");NativePortableConfiguration.prepare(root.resolve("configuration.zip"),hash,root.resolve("crashed-reservation"),(count,stage)->{if(count==3)Runtime.getRuntime().halt(37);});throw new AssertionError("Crash seam not reached");}
 static void verifyCrash(Path root)throws Exception {Path reservation=root.resolve("crashed-reservation");check(Files.exists(reservation.resolve("reservation.json")),"actual process halt retained reservation");check(!Files.exists(reservation.resolve("adoption.json")),"actual process halt cannot expose an active half-written configuration");try(var p=Files.list(reservation)){check(p.anyMatch(x->x.getFileName().toString().startsWith("generation-")),"partial forced files retained for review");}json(root.resolve("crash-result.json"),Bridge.map("checks",checks,"expected_process_halt",37,"activated",false));}
 static byte[] rehash(Map<String,byte[]> entries)throws Exception {JsonObject m=new JsonParser().parse(new String(entries.get("manifest.json"))).getAsJsonObject();for(String name:NativePortableConfiguration.CONFIG)m.getAsJsonObject("native_xml_semantic_sha256").addProperty(name,NativePortableConfiguration.semantic(NativePortableConfiguration.xml(entries.get("config/"+name))));entries.put("manifest.json",G.toJson(m).getBytes());return rehashRaw(entries);}
 static byte[] rehashRaw(Map<String,byte[]> entries)throws Exception {JsonObject m=new JsonParser().parse(new String(entries.get("manifest.json"))).getAsJsonObject();Map<String,String> hashes=NativePortableConfiguration.hashes(entries);hashes.remove("manifest.json");m.add("entry_sha256",G.toJsonTree(hashes));entries.put("manifest.json",G.toJson(m).getBytes());return NativePortableConfiguration.zip(entries);}
 static int feeds(Configuration c){int n=0;for(Feeder f:c.getMachine().getFeeders())n+=((ReferenceTrayFeeder)f).getFeedCount();return n;}
 static JsonArray rows(String row){JsonArray a=new JsonArray();a.add(new JsonParser().parse(row));return a;}
}
