/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.*;
import java.util.*;
import java.util.zip.*;
import javax.xml.XMLConstants;
import javax.xml.parsers.*;
import javax.xml.transform.*;
import javax.xml.transform.dom.DOMSource;
import javax.xml.transform.stream.StreamResult;
import org.w3c.dom.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;

/** Exact pinned native simulator configuration, fresh-instance adoption only.
 * Hashes establish integrity, not permission to deserialize arbitrary native classes.
 * Every dynamic class is checked against an independent bounded allowlist before native load.
 */
public final class NativePortableConfiguration {
 public static final int MAX_FILE=8*1024*1024,MAX_BUNDLE=32*1024*1024,MAX_FILES=256,MAX_XML_NODES=100000;
 static final Gson JSON=new Gson();
 static final List<String> CONFIG=List.of("machine.xml","parts.xml","packages.xml","boards.xml","panels.xml","vision-settings.xml","script-state.xml");
 static final Set<String> CLASSES=new HashSet<>(Arrays.asList(
  "java.lang.Boolean","java.lang.String","java.lang.Integer","java.lang.Double","java.util.ArrayList","java.util.HashMap","java.util.HashSet","java.util.LinkedHashMap",
  "org.openpnp.machine.reference.ReferenceActuator","org.openpnp.machine.reference.ReferenceHead","org.openpnp.machine.reference.ReferenceMachine","org.openpnp.machine.reference.ReferenceNozzle","org.openpnp.machine.reference.ReferenceNozzleTip","org.openpnp.machine.reference.ReferencePnpJobProcessor","org.openpnp.machine.reference.ReferencePnpJobProcessor$SimplePnpJobPlanner",
  "org.openpnp.machine.reference.axis.ReferenceControllerAxis","org.openpnp.machine.reference.axis.ReferenceVirtualAxis","org.openpnp.machine.reference.camera.AutoFocusProvider","org.openpnp.machine.reference.camera.ImageCamera","org.openpnp.machine.reference.camera.SimulatedUpCamera","org.openpnp.machine.reference.driver.NullDriver","org.openpnp.machine.reference.driver.NullMotionPlanner","org.openpnp.machine.reference.feeder.ReferenceStripFeeder","org.openpnp.machine.reference.feeder.ReferenceTrayFeeder","org.openpnp.machine.reference.vision.OpenCvVisionProvider","org.openpnp.machine.reference.vision.ReferenceBottomVision","org.openpnp.machine.reference.vision.ReferenceFiducialLocator","org.openpnp.model.BottomVisionSettings","org.openpnp.model.FiducialVisionSettings"
 ));
 static { for(String name:List.of("BlurGaussian","ConvertColor","ConvertModelToKeyPoints","CreateFootprintTemplateImage","DetectCircularSymmetry","DetectRectlinearSymmetry","DrawCircles","DrawContours","DrawKeyPoints","DrawRotatedRects","DrawTemplateMatches","FilterContours","FindContours","ImageCapture","ImageRecall","ImageWriteDebug","MaskCircle","MaskHsv","MatchTemplate","MinAreaRect","ParameterBool","ParameterNumeric","Threshold"))CLASSES.add("org.openpnp.vision.pipeline.stages."+name); }
 public static final class Prepared {
  public final Path staging,destination;private final Map<String,byte[]> entries;private final JsonObject manifest;
  private final Map<String,String> stagedHashes;private final String reservationHash;private final Object directoryKey;
  Prepared(Path staging,Path destination,Map<String,byte[]> entries,JsonObject manifest)throws Exception{this.staging=staging;this.destination=destination;this.entries=entries;this.manifest=manifest;this.stagedHashes=treeHashes(staging);this.reservationHash=sha(read(destination.resolve("reservation.json"),4096));this.directoryKey=Files.readAttributes(destination,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey();}
 }
 public static Map<String,Object> export(Configuration config,Path output)throws Exception {
  quiescent(config);NativePortableLibraries.Captured library=NativePortableLibraries.capture(config);
  config.save();Path base=config.getConfigurationDirectory().toPath().toRealPath();Map<String,byte[]> entries=new TreeMap<>(library.entries);Map<String,String> semantics=new TreeMap<>();
  for(String name:CONFIG){byte[] bytes=read(base.resolve(name),MAX_FILE);Document doc=xml(bytes);validate(doc,name,library.panels!=null);library.registry(doc,name);rewriteResources(doc,(uri)->resource(base,uri,entries));byte[] encoded=encode(doc);entries.put("config/"+name,encoded);semantics.put(name,semantic(doc));checkEntries(entries);}
  Path scripts=base.resolve("scripts");if(Files.exists(scripts,LinkOption.NOFOLLOW_LINKS)){
   try(var paths=Files.walk(scripts)){for(Path path:(Iterable<Path>)paths::iterator){if(Files.isSymbolicLink(path))fail("PATH_REJECTED","Script inventory cannot follow symbolic links");if(Files.isDirectory(path,LinkOption.NOFOLLOW_LINKS))continue;if(!Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS))fail("PATH_REJECTED","Unknown script file type");String name=scripts.relativize(path).toString().replace(File.separatorChar,'/');entryName("quarantine/scripts/"+name);entries.put("quarantine/scripts/"+name,read(path,1024*1024));checkEntries(entries);}}
  }
  checkEntries(entries);NativePortableLimits.resourceBudget(entries);NativePortableLimits.validate(entries,library.panels!=null);Map<String,Object> manifest=Bridge.map("version",library.version(),"upstream_commit",Bridge.UPSTREAM,"scope","fresh-native-simulator-adoption","entry_sha256",hashes(entries),"native_xml_semantic_sha256",semantics,"native_xml_documents",CONFIG,"scripts_activated",false,"operational_journal_included",false,"in_place_restore",false,"physical_state_transferred",false,"calibration_trust","serialized coefficients preserved as unqualified simulator data; no physical validation transfers","material_counts","captured native counters retained as source simulator data; destination has a distinct operational identity","library_scope",library.scope(),"limits",Bridge.map("files",MAX_FILES,"file_bytes",MAX_FILE,"expanded_bytes",MAX_BUNDLE,"native_model",NativePortableLimits.describe()));
  library.addManifest(manifest);NativePortableLibraries.validateArchive(entries,JSON.toJsonTree(manifest).getAsJsonObject());
  byte[] manifestBytes=JSON.toJson(manifest).getBytes(StandardCharsets.UTF_8);if(manifestBytes.length>256*1024)fail("CAPACITY","Manifest too large");entries.put("manifest.json",manifestBytes);
  byte[] archive=zip(entries);if(archive.length>MAX_BUNDLE)fail("CAPACITY","Archive too large");library.verifyCurrent(config);quiescent(config);writeNew(output,archive);return Bridge.map("sha256",sha(archive),"bytes",archive.length,"manifest",manifest);
 }
 interface WriteProbe{void written(int count,Path staging)throws Exception;}
 public static Prepared prepare(Path bundle,String expectedSha,Path destination)throws Exception {return prepare(bundle,expectedSha,destination,(count,staging)->{});}
 static Prepared prepare(Path bundle,String expectedSha,Path destination,WriteProbe probe)throws Exception {
  if(expectedSha==null||!expectedSha.matches("[a-f0-9]{64}"))fail("INTEGRITY","Explicit expected archive SHA-256 required");
  destination=destination.toAbsolutePath().normalize();if(Files.exists(destination,LinkOption.NOFOLLOW_LINKS))fail("DESTINATION_EXISTS","Fresh adoption cannot overwrite an existing instance or its operational history");checkAncestors(destination.getParent());
  byte[] bytes=read(bundle,MAX_BUNDLE);if(!sha(bytes).equals(expectedSha))fail("INTEGRITY","Archive SHA mismatch");Map<String,byte[]> entries=unzip(bytes);byte[] raw=entries.get("manifest.json");if(raw==null||raw.length>256*1024)fail("MANIFEST","Missing bounded manifest");
  checkJson(raw);JsonObject m=new JsonParser().parse(new String(raw,StandardCharsets.UTF_8)).getAsJsonObject();
  NativePortableLimits.resourceBudget(entries);
  for(var entry:entries.entrySet())if(entry.getKey().startsWith("resources/")){png(entry.getValue());if(!entry.getKey().equals("resources/"+sha(entry.getValue())+".png"))fail("RESOURCE_REJECTED","Resource content address does not match its bytes");}
  if(!Set.of("1","2","3").contains(m.get("version").toString())||!Bridge.UPSTREAM.equals(m.get("upstream_commit").getAsString())||!"fresh-native-simulator-adoption".equals(m.get("scope").getAsString())||!isBoolean(m,"scripts_activated",false)||!isBoolean(m,"in_place_restore",false)||!isBoolean(m,"physical_state_transferred",false)||!isBoolean(m,"operational_journal_included",false))fail("MANIFEST","Unsupported adoption scope");
  JsonObject expected=m.getAsJsonObject("entry_sha256");Map<String,String> actual=hashes(entries);actual.remove("manifest.json");if(!JSON.toJsonTree(actual).equals(expected))fail("INTEGRITY","Archive entry inventory/hash mismatch");
  if(!JSON.toJsonTree(CONFIG).equals(m.get("native_xml_documents")))fail("MANIFEST","Expected every native configuration document");
  for(String name:CONFIG){byte[] content=entries.get("config/"+name);if(content==null)fail("MANIFEST","Native configuration document missing");Document doc=xml(content);validate(doc,name,NativePortableLibraries.panels(m));if(!semantic(doc).equals(m.getAsJsonObject("native_xml_semantic_sha256").get(name).getAsString()))fail("INTEGRITY","Native XML semantic hash mismatch");rewriteResources(doc,(uri)->{if(!uri.matches("codex-resource:[a-f0-9]{64}"))fail("RESOURCE_REJECTED","Only inventoried resources may enter adopted native XML");String key="resources/"+uri.substring(15)+".png";if(!entries.containsKey(key)||!sha(entries.get(key)).equals(uri.substring(15)))fail("RESOURCE_REJECTED","Missing resource");return uri;});}
  NativePortableLimits.validate(entries,NativePortableLibraries.panels(m));NativePortableLibraries.validateArchive(entries,m);
  try{Files.createDirectory(destination,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));}catch(FileAlreadyExistsException e){fail("DESTINATION_EXISTS","Fresh adoption never replaces an existing destination");}
  Path pending=Files.createTempDirectory(destination,"generation-",PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
  writeNew(destination.resolve("reservation.json"),JSON.toJson(Bridge.map("version",1,"reservation_id",UUID.randomUUID().toString(),"archive_sha256",expectedSha,"generation_directory",pending.getFileName().toString(),"active",false)).getBytes(StandardCharsets.UTF_8));
  try {
   int count=0;for(Map.Entry<String,byte[]> entry:entries.entrySet()){writeNew(pending.resolve(entry.getKey()),entry.getValue());probe.written(++count,pending);}
   for(String name:CONFIG){Document doc=xml(entries.get("config/"+name));NativePortableLibraries.stageRegistry(doc,name,m,pending);rewriteResources(doc,(uri)->pending.resolve("resources/"+uri.substring(15)+".png").toUri().toString());replace(pending.resolve("config/"+name),encode(doc));}
   NativePortableLibraries.stageAssets(entries,m,pending);return new Prepared(pending,destination,entries,m);
  }catch(Exception failure){writeFailure(pending,failure);throw failure;}
 }
 /** Must run in a dedicated validation JVM with no other initialized Configuration. */
 public static Map<String,Object> validateAndPublish(Prepared p)throws Exception {
  verifyReservation(p);if(!p.stagedHashes.equals(treeHashes(p.staging)))fail("STAGING_CHANGED","Prepared native files/resources/scripts changed before native loading");
  Configuration.initialize(p.staging.resolve("config").toFile());Configuration c=Configuration.get();boolean activated=false;org.openpnp.scripting.Scripting.ExecutionConstraint scriptPolicy=null;
  try {
   scriptPolicy=c.getScripting().constrainExecution(file->{throw new IllegalStateException("Script evaluation is disabled during portable adoption validation");});
   c.load();quiescent(c);Map<String,Object> boardLibrary=NativePortableLibraries.verifyLoaded(c,JSON.toJsonTree(p.manifest).getAsJsonObject(),p.staging);if(c.getMachine().isHomed())fail("AUTHORITY","Adoption unexpectedly restored homed state");
   c.save();Map<String,String> actual=new TreeMap<>();
   for(String name:CONFIG){Document doc=xml(read(p.staging.resolve("config/"+name),MAX_FILE));validate(doc,name,NativePortableLibraries.panels(p.manifest));NativePortableLibraries.normalizeRegistry(doc,name,p.manifest,p.staging);rewriteResources(doc,(uri)->resource(p.staging.resolve("config"),uri,new TreeMap<>()));actual.put(name,semantic(doc));}
   JsonObject manifest=JSON.toJsonTree(p.manifest).getAsJsonObject();
   if(!JSON.toJsonTree(actual).equals(manifest.get("native_xml_semantic_sha256")))fail("ROUNDTRIP_CHANGED","Native load/save changed configuration fields; staged candidate was not published");
   if(NativePortableLibraries.panels(manifest))NativePortableLibraries.verifyLoaded(c,manifest,p.staging);c.getMachine().close();
   Map<String,Object> receipt=Bridge.map("version",1,"instance_id",UUID.randomUUID().toString(),"upstream_commit",Bridge.UPSTREAM,"configuration_directory",p.staging.getFileName()+"/config","reservation_sha256",p.reservationHash,"native_xml_roundtrip_verified",true,"enabled",false,"homed",false,"physical_state_transferred",false,"scripts_activated",false,"operational_history","new and empty; existing instances were not touched","generation_files_sha256",activationFiles(p.staging));
   Path receiptFile=p.staging.resolve("activation.receipt.json");writeNew(receiptFile,JSON.toJson(receipt).getBytes(StandardCharsets.UTF_8));
   verifyReservation(p);try{Files.createLink(p.destination.resolve("adoption.json"),receiptFile);}catch(FileAlreadyExistsException collision){fail("ACTIVATION_CONFLICT","Existing activation record was not overwritten");}activated=true;forceDirectory(p.destination);
   Map<String,Object> result=Bridge.map("adopted",true,"native_xml_roundtrip_verified",true,"documents",CONFIG.size(),"configuration_directory",p.staging.resolve("config").toString(),"scripts_activated",false,"physical_state_transferred",false,"board_library",boardLibrary);if(NativePortableLibraries.panels(manifest))result.put("panel_library",NativePortablePanelLibrary.receipt(manifest));return result;
  }catch(Exception|Error failure){try{c.getMachine().close();}catch(Exception ignored){}if(activated)throw new Bridge.Fault("ADOPTION_PUBLICATION_UNKNOWN","Activation link exists but publication completion failed; inspect the same reservation without repeating adoption");writeFailure(p.staging,failure);throw failure;}finally{if(scriptPolicy!=null)scriptPolicy.close();}

 }
 static void verifyReservation(Prepared p)throws Exception {
  checkAncestors(p.destination);if(!Objects.equals(p.directoryKey,Files.readAttributes(p.destination,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS).fileKey())||!p.reservationHash.equals(sha(read(p.destination.resolve("reservation.json"),4096))))fail("RESERVATION_CONFLICT","Adoption reservation changed");
  try(var paths=Files.list(p.destination)){for(Path path:(Iterable<Path>)paths::iterator)if(!Set.of("reservation.json",p.staging.getFileName().toString()).contains(path.getFileName().toString()))fail("ACTIVATION_CONFLICT","Unrelated state or activation appeared in the reserved destination");}
 }
 static Map<String,String> treeHashes(Path root)throws Exception {Map<String,String> result=new TreeMap<>();long total=0;try(var paths=Files.walk(root)){for(Path path:(Iterable<Path>)paths::iterator){if(Files.isSymbolicLink(path))fail("STAGING_CHANGED","Prepared tree contains a symbolic link");if(Files.isDirectory(path,LinkOption.NOFOLLOW_LINKS))continue;byte[] bytes=read(path,MAX_FILE);total+=bytes.length;if(total>MAX_BUNDLE||result.size()>=MAX_FILES)fail("STAGING_CHANGED","Prepared tree exceeds its bounded inventory");result.put(root.relativize(path).toString(),sha(bytes));}}return result;}
 /** Resolve only the unchanged, newly activated configuration; operational relaunch is a separate contract. */
 public static Path activeConfiguration(Path destination)throws Exception {
  destination=destination.toAbsolutePath().normalize();checkAncestors(destination);
  try {
   byte[] reservationBytes=read(destination.resolve("reservation.json"),4096);JsonObject reservation=object(reservationBytes);
   fields(reservation,Set.of("version","reservation_id","archive_sha256","generation_directory","active"));
   if(!"1".equals(reservation.get("version").toString())||!isBoolean(reservation,"active",false)||!string(reservation,"reservation_id").matches("[a-f0-9-]{36}")||!string(reservation,"archive_sha256").matches("[a-f0-9]{64}"))fail("ACTIVATION_INVALID","Invalid reservation record");
   String generation=string(reservation,"generation_directory");if(!generation.matches("generation-[A-Za-z0-9_-]+"))fail("ACTIVATION_INVALID","Invalid reserved generation");
   Path stage=destination.resolve(generation),config=stage.resolve("config");checkAncestors(config);
   if(!Files.isDirectory(stage,LinkOption.NOFOLLOW_LINKS)||!Files.isDirectory(config,LinkOption.NOFOLLOW_LINKS))fail("ACTIVATION_INVALID","Reserved native generation is absent");
   Path activation=destination.resolve("adoption.json"),original=stage.resolve("activation.receipt.json");byte[] bytes=read(activation,128*1024),internal=read(original,128*1024);
   if(!Files.isSameFile(activation,original)||!Arrays.equals(bytes,internal))fail("ACTIVATION_INVALID","Activation is not the published generation receipt");
   JsonObject value=object(bytes);fields(value,Set.of("version","instance_id","upstream_commit","configuration_directory","reservation_sha256","native_xml_roundtrip_verified","enabled","homed","physical_state_transferred","scripts_activated","operational_history","generation_files_sha256"));
   if(!"1".equals(value.get("version").toString())||!string(value,"instance_id").matches("[a-f0-9-]{36}")||!Bridge.UPSTREAM.equals(string(value,"upstream_commit"))||!(generation+"/config").equals(string(value,"configuration_directory"))||!sha(reservationBytes).equals(string(value,"reservation_sha256"))||!isBoolean(value,"native_xml_roundtrip_verified",true)||!isBoolean(value,"enabled",false)||!isBoolean(value,"homed",false)||!isBoolean(value,"physical_state_transferred",false)||!isBoolean(value,"scripts_activated",false)||!"new and empty; existing instances were not touched".equals(string(value,"operational_history")))fail("ACTIVATION_INVALID","Invalid generation activation receipt");
   Map<String,String> actual=activationFiles(stage);if(!JSON.toJsonTree(actual).equals(value.get("generation_files_sha256")))fail("ACTIVATION_INVALID","Activated native configuration or resource bytes changed");
   JsonObject manifest=object(read(stage.resolve("manifest.json"),256*1024));Map<String,byte[]> boundedEntries=new TreeMap<>();for(String name:CONFIG){Document d=xml(read(config.resolve(name),MAX_FILE));validate(d,name,NativePortableLibraries.panels(manifest));rewriteResources(d,uri->resource(config,uri,boundedEntries));boundedEntries.put("config/"+name,encode(d));}NativePortableLibraries.collectActivated(boundedEntries,manifest,stage);NativePortableLimits.validate(boundedEntries,NativePortableLibraries.panels(manifest));
   return config;
  }catch(Bridge.Fault failure){throw failure;}catch(Exception failure){throw new Bridge.Fault("ACTIVATION_INVALID","Cannot resolve a complete validated native generation: "+failure.getClass().getSimpleName());}
 }
 static Map<String,String> activationFiles(Path stage)throws Exception {
  Map<String,String> files=new TreeMap<>();for(String name:CONFIG)files.put("config/"+name,sha(read(stage.resolve("config/"+name),MAX_FILE)));
  for(String subtree:List.of("resources","quarantine","library")){Path dir=stage.resolve(subtree);if(Files.exists(dir,LinkOption.NOFOLLOW_LINKS)){for(var entry:treeHashes(dir).entrySet())files.put(subtree+"/"+entry.getKey().replace(File.separatorChar,'/'),entry.getValue());}}
  Path scripts=stage.resolve("config/scripts");if(Files.exists(scripts,LinkOption.NOFOLLOW_LINKS)&&!treeHashes(scripts).isEmpty())fail("ACTIVATION_INVALID","Activated native scripts must remain empty");
  files.put("manifest.json",sha(read(stage.resolve("manifest.json"),256*1024)));if(files.size()>MAX_FILES)fail("ACTIVATION_INVALID","Activation inventory exceeds bounds");return files;
 }
 static JsonObject object(byte[] bytes)throws Exception {checkJson(bytes);JsonElement value=new JsonParser().parse(new String(bytes,StandardCharsets.UTF_8));if(!value.isJsonObject())fail("ACTIVATION_INVALID","Expected receipt object");return value.getAsJsonObject();}
 static void fields(JsonObject object,Set<String> expected)throws Exception {Set<String> keys=new HashSet<>();for(var entry:object.entrySet())keys.add(entry.getKey());if(!keys.equals(expected))fail("ACTIVATION_INVALID","Unexpected receipt fields");}
 static String string(JsonObject object,String key)throws Exception {JsonElement value=object.get(key);if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString())fail("ACTIVATION_INVALID","Expected receipt string");return value.getAsString();}
 static boolean isBoolean(JsonObject object,String key,boolean expected){JsonElement value=object.get(key);return value!=null&&value.isJsonPrimitive()&&value.getAsJsonPrimitive().isBoolean()&&value.getAsBoolean()==expected;}
 public static Map<String,String> canonicalFiles(Configuration c)throws Exception {NativePortableVacuum.requireDisabled(c);Map<String,String> values=new TreeMap<>();c.save();for(String name:CONFIG){Document d=xml(read(c.getConfigurationDirectory().toPath().resolve(name),MAX_FILE));validate(d,name);rewriteResources(d,u->resource(c.getConfigurationDirectory().toPath(),u,new TreeMap<>()));values.put(name,semantic(d));}return values;}
 static void quiescent(Configuration c)throws Exception {Machine m=c.getMachine();if(m.getClass()!=ReferenceMachine.class||m.isEnabled()||(m.isBusy()&&!m.isTask(Thread.currentThread())))fail("AUTHORITY","Export/adoption requires disabled, idle exact native ReferenceMachine");for(Driver d:m.getDrivers())if(d.getClass()!=NullDriver.class)fail("AUTHORITY","Only exact native NullDriver is supported");for(Head h:m.getHeads())for(Nozzle n:h.getNozzles())if(n.getPart()!=null)fail("AUTHORITY","Held part state does not transfer");NativePortableVacuum.requireDisabled(c);}
 interface ResourceMap {String map(String uri)throws Exception;}
 static void rewriteResources(Document d,ResourceMap mapper)throws Exception {NodeList list=d.getElementsByTagName("source-uri");for(int i=0;i<list.getLength();i++){Element e=(Element)list.item(i);e.setTextContent(mapper.map(e.getTextContent()));}}
 static String resource(Path base,String uri,Map<String,byte[]> entries)throws Exception {
  byte[] bytes;if(uri.startsWith("classpath://")){String path=uri.substring(12);if(!path.equals("samples/pnp-test/pnp-test.png"))fail("RESOURCE_REJECTED","Only the pinned sample classpath resource is supported");try(InputStream in=NativePortableConfiguration.class.getClassLoader().getResourceAsStream(path)){if(in==null)fail("RESOURCE_REJECTED","Classpath resource absent");bytes=in.readNBytes(MAX_FILE+1);}}
  else {URI value=URI.create(uri);if(!"file".equals(value.getScheme()))fail("RESOURCE_REJECTED","Network and unrecognized resources are not portable");Path file=Path.of(value).toAbsolutePath().normalize();Path baseReal=base.toRealPath();Path resources=baseReal.getParent().resolve("resources");if(!file.startsWith(baseReal)&&!file.startsWith(resources))fail("RESOURCE_REJECTED","External file is outside the owned configuration/resource roots");checkAncestors(file);bytes=read(file,MAX_FILE);}
  png(bytes);String hash=sha(bytes);entries.put("resources/"+hash+".png",bytes);return "codex-resource:"+hash;
 }
 static void png(byte[] bytes)throws Exception {
  if(bytes.length>MAX_FILE||bytes.length<24||bytes[0]!=(byte)137||bytes[1]!=80||bytes[2]!=78||bytes[3]!=71)fail("RESOURCE_REJECTED","Expected bounded PNG resource");int w=ByteBuffer.wrap(bytes,16,4).getInt(),h=ByteBuffer.wrap(bytes,20,4).getInt();if(w<1||h<1||w>8192||h>8192||(long)w*h>32000000)fail("RESOURCE_REJECTED","PNG dimensions exceed the supported profile");java.awt.image.BufferedImage decoded=javax.imageio.ImageIO.read(new ByteArrayInputStream(bytes));if(decoded==null||decoded.getWidth()!=w||decoded.getHeight()!=h)fail("RESOURCE_REJECTED","PNG resource cannot be decoded consistently");decoded.flush();
 }
 static void checkJson(byte[] bytes)throws Exception {
  var decoder=StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT);
  try(var reader=new com.google.gson.stream.JsonReader(new InputStreamReader(new ByteArrayInputStream(bytes),decoder))){reader.setLenient(false);checkJsonValue(reader,0,new int[1]);if(reader.peek()!=com.google.gson.stream.JsonToken.END_DOCUMENT)fail("MANIFEST","Trailing JSON data");}
 }
 static void checkJsonValue(com.google.gson.stream.JsonReader reader,int depth,int[] nodes)throws Exception {
  if(depth>48||++nodes[0]>50000)fail("MANIFEST","Manifest structure limit");
  switch(reader.peek()){
  case BEGIN_OBJECT:reader.beginObject();Set<String> keys=new HashSet<>();while(reader.hasNext()){String key=reader.nextName();if(key.length()>8192||!keys.add(key))fail("MANIFEST","Duplicate or oversized manifest key");checkJsonValue(reader,depth+1,nodes);}reader.endObject();break;
  case BEGIN_ARRAY:reader.beginArray();while(reader.hasNext())checkJsonValue(reader,depth+1,nodes);reader.endArray();break;
  case STRING:case NUMBER:if(reader.nextString().length()>8192)fail("MANIFEST","Manifest scalar limit");break;
  case BOOLEAN:reader.nextBoolean();break;
  case NULL:reader.nextNull();break;
  default:fail("MANIFEST","Unexpected JSON token");}
 }
 static void validate(Document doc,String name)throws Exception {validate(doc,name,false);}
 static void validate(Document doc,String name,boolean panels)throws Exception {Element root=doc.getDocumentElement();String expected="openpnp-"+name.substring(0,name.length()-4);if(!root.getTagName().equals(expected))fail("XML_REJECTED","Unexpected native document root");int[] count={0};validateNode(root,0,count);if(name.equals("machine.xml"))NativePortableVacuum.requireDisabled(doc);if(!panels&&name.equals("panels.xml")&&root.getElementsByTagName("panel").getLength()!=0)fail("LIBRARY_NOT_SUPPORTED","Native panel libraries remain unsupported");}
 static void validateNode(Element e,int depth,int[] count)throws Exception {if(depth>48||++count[0]>MAX_XML_NODES)fail("XML_REJECTED","XML exceeds structure limits");if(e.hasAttribute("class")&&!CLASSES.contains(e.getAttribute("class")))fail("CLASS_REJECTED","Native class is not in the independently pinned simulator allowlist: "+e.getAttribute("class"));if(e.hasAttribute("class")&&e.getAttribute("class").endsWith(".ImageWriteDebug")&&(!e.getAttribute("prefix").matches("[A-Za-z0-9_-]{1,64}")||!".png".equals(e.getAttribute("suffix"))))fail("RESOURCE_REJECTED","Native debug writers must use bounded basenames and PNG suffix under the owned configuration directory");if(e.getTagName().matches("(?i).*(?:file|path|url|uri).*" )&&!e.getTagName().equals("source-uri"))fail("RESOURCE_REJECTED","Unclassified native file/resource field: "+e.getTagName());for(int i=0;i<e.getAttributes().getLength();i++){Node a=e.getAttributes().item(i);if(a.getNodeName().matches("(?i).*(?:file|path|url|uri).*"))fail("RESOURCE_REJECTED","Unclassified native resource attribute");}for(Node n=e.getFirstChild();n!=null;n=n.getNextSibling())if(n instanceof Element)validateNode((Element)n,depth+1,count);}
 static Document xml(byte[] bytes)throws Exception {if(bytes.length>MAX_FILE)fail("CAPACITY","XML too large");DocumentBuilderFactory f=DocumentBuilderFactory.newDefaultInstance();f.setFeature("http://apache.org/xml/features/disallow-doctype-decl",true);f.setFeature("http://xml.org/sax/features/external-general-entities",false);f.setFeature("http://xml.org/sax/features/external-parameter-entities",false);f.setAttribute("http://www.oracle.com/xml/jaxp/properties/maxElementDepth",49);f.setXIncludeAware(false);f.setExpandEntityReferences(false);f.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD,"");f.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA,"");return f.newDocumentBuilder().parse(new ByteArrayInputStream(bytes));}
 static byte[] encode(Document doc)throws Exception {TransformerFactory f=TransformerFactory.newDefaultInstance();f.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD,"");f.setAttribute(XMLConstants.ACCESS_EXTERNAL_STYLESHEET,"");Transformer t=f.newTransformer();t.setOutputProperty(OutputKeys.INDENT,"no");ByteArrayOutputStream out=new ByteArrayOutputStream();t.transform(new DOMSource(doc),new StreamResult(out));return out.toByteArray();}
 static String semantic(Document doc)throws Exception {return sha(JSON.toJson(node(doc.getDocumentElement())).getBytes(StandardCharsets.UTF_8));}
 static Object node(Element e){Map<String,Object> result=new TreeMap<>();result.put("name",e.getTagName());Map<String,String> attrs=new TreeMap<>();for(int i=0;i<e.getAttributes().getLength();i++){Node a=e.getAttributes().item(i);attrs.put(a.getNodeName(),a.getNodeValue());}result.put("attributes",attrs);List<Object> children=new ArrayList<>();for(Node n=e.getFirstChild();n!=null;n=n.getNextSibling())if(n instanceof Element)children.add(node((Element)n));else if(n.getNodeType()==Node.TEXT_NODE&&!n.getTextContent().trim().isEmpty())children.add(n.getTextContent());result.put("content",children);return result;}
 static byte[] zip(Map<String,byte[]> entries)throws Exception {ByteArrayOutputStream bytes=new ByteArrayOutputStream();try(ZipOutputStream zip=new ZipOutputStream(bytes)){for(var e:entries.entrySet()){ZipEntry entry=new ZipEntry(e.getKey());entry.setTime(0);zip.putNextEntry(entry);zip.write(e.getValue());zip.closeEntry();}}return bytes.toByteArray();}
 static Map<String,byte[]> unzip(byte[] archive)throws Exception {Map<String,byte[]> result=new TreeMap<>();long total=0;try(ZipInputStream zip=new ZipInputStream(new ByteArrayInputStream(archive))){ZipEntry e;while((e=zip.getNextEntry())!=null){entryName(e.getName());if(e.isDirectory()||result.size()>=MAX_FILES||result.containsKey(e.getName()))fail("PATH_REJECTED","Duplicate/directory/oversized ZIP inventory");byte[] bytes=zip.readNBytes(MAX_FILE+1);total+=bytes.length;if(bytes.length>MAX_FILE||total>MAX_BUNDLE)fail("CAPACITY","Expanded ZIP exceeds bounds");result.put(e.getName(),bytes);}}checkEntries(result);return result;}
 static void entryName(String name)throws Exception {if(!name.matches("(?:manifest\\.json|config/(?:machine|parts|packages|boards|panels|vision-settings|script-state)\\.xml|resources/[a-f0-9]{64}\\.png|library/boards/board-[0-9]{3}-[a-f0-9]{64}\\.board\\.xml|library/panels/panel-[0-9]{3}-[a-f0-9]{64}\\.panel\\.xml|quarantine/scripts/[A-Za-z0-9_. -]+(?:/[A-Za-z0-9_. -]+){0,4})")||Arrays.asList(name.split("/")).contains("..")||Arrays.asList(name.split("/")).contains("."))fail("PATH_REJECTED","Non-generated or traversal ZIP entry");}
 static void checkEntries(Map<String,byte[]> entries)throws Exception {long total=0;if(entries.size()>MAX_FILES)fail("CAPACITY","Too many files");for(var e:entries.entrySet()){entryName(e.getKey());total+=e.getValue().length;if(e.getValue().length>MAX_FILE||total>MAX_BUNDLE)fail("CAPACITY","Expanded entries exceed bounds");}}
 static Map<String,String> hashes(Map<String,byte[]> entries)throws Exception {Map<String,String> values=new TreeMap<>();for(var e:entries.entrySet())values.put(e.getKey(),sha(e.getValue()));return values;}
 static String sha(byte[] bytes)throws Exception {StringBuilder s=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))s.append(String.format(Locale.ROOT,"%02x",b));return s.toString();}
 static void checkAncestors(Path p)throws Exception {for(Path a=p;a!=null;a=a.getParent())if(Files.isSymbolicLink(a))fail("PATH_REJECTED","Symbolic path component");}
 static byte[] read(Path p,int max)throws Exception {checkAncestors(p);if(!Files.isRegularFile(p,LinkOption.NOFOLLOW_LINKS)||Files.size(p)>max)fail("PATH_REJECTED","Expected bounded regular input");try(InputStream in=Files.newInputStream(p,LinkOption.NOFOLLOW_LINKS)){byte[] b=in.readNBytes(max+1);if(b.length>max)fail("CAPACITY","Input grew past bounds");return b;}}
 static void writeNew(Path path,byte[] bytes)throws Exception {checkAncestors(path.toAbsolutePath().getParent());Files.createDirectories(path.toAbsolutePath().getParent());Files.createFile(path,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));try(FileChannel c=FileChannel.open(path,StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS)){ByteBuffer b=ByteBuffer.wrap(bytes);while(b.hasRemaining())c.write(b);c.force(true);}forceDirectory(path.getParent());}
 static void replace(Path p,byte[] bytes)throws Exception {Path temp=p.resolveSibling(".tmp-"+UUID.randomUUID());writeNew(temp,bytes);Files.move(temp,p,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);forceDirectory(p.getParent());}
 static void forceDirectory(Path path)throws Exception {try(FileChannel c=FileChannel.open(path,StandardOpenOption.READ)){c.force(true);}}
 static void writeFailure(Path stage,Throwable e){try{writeNew(stage.resolve("ADOPTION_FAILED.txt"),e.toString().getBytes(StandardCharsets.UTF_8));}catch(Exception ignored){}}
 static void fail(String code,String text)throws Bridge.Fault {throw new Bridge.Fault(code,text);}
}
