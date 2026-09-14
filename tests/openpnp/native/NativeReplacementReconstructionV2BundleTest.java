/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.geom.AffineTransform;
import java.beans.PropertyChangeListener;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.ReferencePnpJobProcessor;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.base.AbstractMachine;

/** Separate JVMs exchange only a closed reconstruction bundle and read-only expected facts.
 * Native model fixtures and reflection helpers are reused unchanged. No Bridge, archive store,
 * load enrollment, source binding, lease acquisition, job initialization or execution is invoked. */
public final class NativeReplacementReconstructionV2BundleTest {
 static final com.google.gson.Gson JSON=new com.google.gson.GsonBuilder().serializeNulls().setPrettyPrinting().create();
 static Configuration config;static Path root,legacy;static int checks,refusals;static final List<String> facts=new ArrayList<>();
 interface Work{void run()throws Exception;}
 static void yes(boolean v,String why){checks++;if(!v)throw new AssertionError(why);facts.add(why);}
 static void no(String why,Work w)throws Exception{try{w.run();throw new AssertionError("Accepted "+why);}catch(IOException|Bridge.Fault expected){checks++;refusals++;facts.add("refused "+why+": "+expected.getMessage());}}
 static Map<String,Object> obj(Object x)throws Exception{return NativeFaultedJobReplacement.object(x);}
 static Map<String,Object> copy(Map<String,Object> x){return NativeJournalJson.parseObject(JSON.toJson(x));}
 static boolean same(Object a,Object b)throws Exception{return NativeFaultedJobReplacement.same(a,b);}
 static String digest(Map<String,Object> x)throws Exception{return NativeFaultedJobReplacement.digest(x);}
 static <T>T task(Callable<T> c)throws Exception{try{return config.getMachine().submit(c,null,true).get(30,TimeUnit.SECONDS);}catch(ExecutionException e){if(e.getCause() instanceof Error)throw(Error)e.getCause();throw(Exception)e.getCause();}}
 static Object field(Class<?> c,String n,Object target)throws Exception{java.lang.reflect.Field f=c.getDeclaredField(n);f.setAccessible(true);return f.get(target);}
 static Map<String,Object> shape(Job job)throws Exception{Class<?> c=Class.forName("org.openpnp.codex.NativeReplacementJob$Snapshot");java.lang.reflect.Constructor<?> k=c.getDeclaredConstructor(Configuration.class,Job.class);k.setAccessible(true);return obj(field(c,"shape",k.newInstance(config,job)));}
 static Set<Object> graph(Job j)throws Exception{return NativeReplacementJobTest.objects(j);}
 static Map<Object,List<Object>> listeners(Set<Object> g)throws Exception{return NativeReplacementJobTest.listeners(g);}
 static List<Map<String,Object>> catalog()throws Exception{return (List<Map<String,Object>>)(Object)NativeJournalJson.parseObject(Files.readString(root.resolve("catalog.json"))).get("rows");}
 static Map<String,Object> bundle(String name)throws Exception{return NativeJournalJson.parseObject(Files.readString(root.resolve(name+"-bundle.json")));}
 static List<String> names(){return List.of("canonical","nested","native","instances");}
 static Map<String,Object> nativeFacts()throws Exception{
  Map<String,Object> trays=new TreeMap<>(),nozzles=new TreeMap<>();for(org.openpnp.spi.Feeder f:config.getMachine().getFeeders())if(f instanceof ReferenceTrayFeeder)trays.put(f.getId(),((ReferenceTrayFeeder)f).getFeedCount());
  for(org.openpnp.spi.Head h:config.getMachine().getHeads())for(org.openpnp.spi.Nozzle n:h.getNozzles())nozzles.put(n.getId(),n.getPart()==null?null:n.getPart().getId());
  org.openpnp.spi.base.ExternalExecutionControl gate=((AbstractMachine)config.getMachine()).getExternalExecutionControl();
  return NativeFaultedJobReplacement.map("enabled",config.getMachine().isEnabled(),"homed",config.getMachine().isHomed(),"owner",gate.getOwnerLabel(),"pending",gate.getPendingTaskCount(),"processor_job_absent",field(ReferencePnpJobProcessor.class,"job",config.getMachine().getPnpJobProcessor())==null,"vacuum_source",NativeVacuumSources.inspect(config),"trays",trays,"nozzles",nozzles,"boards",config.getBoards().size(),"panels",config.getPanels().size());
 }
 static Map<String,String> settings()throws Exception{Map<String,String> x=new TreeMap<>();Path p=root.resolve("config");if(Files.exists(p))try(var paths=Files.walk(p)){for(Path f:paths.filter(Files::isRegularFile).toList())x.put(p.relativize(f).toString(),NativeReplacementDocumentsTest.sha(Files.readAllBytes(f)));}return x;}
 static void noAuthority(Map<String,Object> before,Map<String,String> files,String why)throws Exception{
  yes(same(before,nativeFacts()),why+" leaves native job, counters, nozzle contents, machine state, gate and library sizes unchanged");yes(Boolean.TRUE.equals(before.get("processor_job_absent"))&&before.get("owner")==null,why+" has no initialized native job or external owner");yes(files.equals(settings()),why+" performs no native configuration persistence");
  try(var paths=Files.walk(root)){yes(paths.noneMatch(p->p.toString().endsWith(".zip")||p.getFileName().toString().equals("receipt-key")||p.toString().endsWith(".receipt.json")),why+" has no native document archive, signing key or receipt");}
 }
 static Job fixture(String name)throws Exception{
  Job j=NativeReplacementDocumentsTest.fixture(name);BoardLocation a=j.getBoardLocations().get(0),b=j.getBoardLocations().get(1);Placement p=a.getBoard().getPlacement(0),q=b.getBoard().getPlacement(0);
  j.storePlacedStatus(a,p.getId(),true);j.storePlacedStatus(b,q.getId(),false);j.storePlacedStatus(a,"removed-opaque-true",true);j.storePlacedStatus(b,"removed-opaque-false",false);
  j.storeEnabledState(a,p,false);j.storeErrorHandlingState(a,p,Placement.ErrorHandling.Defer);j.storeCheckFiducialsState(a,true);a.setLocalToParentTransform(AffineTransform.getTranslateInstance(0.25,0.75));a.setFileName(root.resolve("must-not-open/source.board.xml").toString());j.setFile(root.resolve("must-not-open/source.job.xml").toFile());return j;
 }
 static String rawPackage(org.openpnp.model.Package pkg)throws Exception{ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(pkg,out);return NativeReplacementDocumentsTest.sha(out.toByteArray());}
 static Object vision(org.openpnp.model.Package pkg)throws Exception{return field(org.openpnp.model.Package.class,"visionCompositing",pkg);}
 static void capture()throws Exception{
  List<Object> rows=new ArrayList<>();Map<String,Object> nativeBefore=nativeFacts();Map<String,String> files=settings();
  for(String name:names()){
   Job original=fixture(name);Map<String,Boolean> history=new TreeMap<>(original.getPlacedStatusSnapshot());Map<String,Object> originalShape=shape(original);Map<Object,List<Object>> before=listeners(graph(original));
   try(NativeReplacementJob.Candidate candidate=NativeReplacementJob.build(config,original)){
    Map<String,Object> b=NativeReplacementDocuments.captureReconstruction(config,original,candidate);NativeReplacementDocuments.validateReconstruction(b);yes(((Number)b.get("schema_version")).intValue()==2,name+" new capture uses version2 semantics");
    yes(history.size()==4&&history.containsValue(true)&&history.containsValue(false),name+" original has full true/false and opaque orphan history");yes(same(history,b.get("original_history")),name+" captures every history key and Boolean value");yes(same(candidate.mapping(),b.get("source_mapping")),name+" captures exact candidate source mapping");yes(candidate.job().getPlacedStatusSnapshot().isEmpty(),name+" capture never marks the candidate placed");yes(history.equals(original.getPlacedStatusSnapshot())&&before.equals(listeners(graph(original))),name+" capture preserves original history and exact subscriptions");
    no(name+" unrelated same-shape original identity",()->NativeReplacementDocuments.captureReconstruction(config,NativeReplacementDocumentsTest.fixture(name),candidate));
    Files.writeString(root.resolve(name+"-bundle.json"),JSON.toJson(b));rows.add(NativeFaultedJobReplacement.map("name",name,"original_history",history,"original_shape",originalShape,"candidate_shape",shape(candidate.job()),"mapping",candidate.mapping(),"bundle_sha256",digest(b),"writer_pid",ProcessHandle.current().pid()));
   }
  }
  Files.writeString(root.resolve("catalog.json"),JSON.toJson(NativeFaultedJobReplacement.map("rows",rows)));noAuthority(nativeBefore,files,"Bundle capture");
 }
 static void reconstructed()throws Exception{
  Map<String,Object> nativeBefore=nativeFacts();Map<String,String> files=settings();
  for(Map<String,Object> row:catalog()){
   String name=(String)row.get("name");Map<String,Object> b=bundle(name);yes(((Number)row.get("writer_pid")).longValue()!=ProcessHandle.current().pid(),name+" reader is a different actual JVM from capture");yes(digest(b).equals(row.get("bundle_sha256")),name+" bundle bytes decode to exact captured value");
   NativeReplacementDocuments.Reconstructed r=NativeReplacementDocuments.reconstruct(config,b);Job old=r.original(),fresh=r.candidate().job();Set<Object> oldGraph=graph(old),freshGraph=graph(fresh);
   try{
    yes(old!=fresh&&Collections.disjoint(oldGraph,freshGraph),name+" original and candidate full mutable graphs are disjoint");yes(same(row.get("original_shape"),shape(old))&&same(row.get("candidate_shape"),shape(fresh)),name+" exact full native original and candidate shapes reconstructed");yes(same(row.get("original_history"),old.getPlacedStatusSnapshot()),name+" true, false and orphan history survive fresh JVM reconstruction");yes(fresh.getPlacedStatusSnapshot().isEmpty(),name+" reconstructed candidate has empty history");yes(same(row.get("mapping"),r.candidate().mapping()),name+" source mapping remains exact");yes(same(b,r.bundle()),name+" original immutable bundle remains readable");
    BoardLocation first=old.getBoardLocations().get(0);Placement p=first.getBoard().getPlacement(0);yes(!old.retrieveEnabledState(first,p)&&old.retrieveCheckFiducialsState(first)&&old.retrieveErrorHandlingState(first,p)==Placement.ErrorHandling.Defer,name+" native per-placement overrides survive");
    for(Job j:List.of(old,fresh)){yes(j.getFile()==null,name+" reconstructed job has no imported file binding");for(BoardLocation loc:j.getBoardLocations()){yes(loc.getFileName()==null&&loc.getPlacementsTransformStatus()==PlacementsHolderLocation.PlacementsTransformStatus.NotSet,name+" reconstructed board has no old registration or file binding");for(Placement placement:loc.getBoard().getPlacements())if(placement.getPart()!=null)yes(placement.getPart()==config.getPart(placement.getPart().getId())&&placement.getPart().getPackage()==config.getPackage(placement.getPart().getPackage().getId()),name+" only canonical native Part/Package are shared");}}
    yes(!Files.exists(root.resolve("must-not-open")),name+" inert source provenance never opens files");r.requireCurrent();
   }finally{r.close();}
   yes(listeners(oldGraph).values().stream().allMatch(List::isEmpty)&&listeners(freshGraph).values().stream().allMatch(List::isEmpty),name+" close releases every reconstructed original/candidate subscription");no(name+" closed reconstructed witness",r::requireCurrent);r.close();
  }
  NativeReplacementDocuments.Reconstructed transfer=NativeReplacementDocuments.reconstruct(config,bundle("canonical"));Job transferred=transfer.candidate().job();Set<Object> inert=graph(transfer.original()),owned=graph(transferred);Map<Object,List<Object>> beforeTransfer=listeners(owned);transfer.candidate().publish();transfer.close();yes(listeners(inert).values().stream().allMatch(List::isEmpty),"Closing transferred reconstruction releases only inert original subscriptions");yes(beforeTransfer.equals(listeners(owned)),"Closing reconstruction preserves transferred candidate subscriptions");yes(transferred.getPlacedStatusSnapshot().isEmpty(),"Candidate resource ownership transfer never initializes or places native work");no("closed reconstruction after candidate resource transfer",transfer::requireCurrent);
  noAuthority(nativeBefore,files,"Fresh-JVM bundle reconstruction");
 }
 static void changedHash(Map<String,Object> b,String key)throws Exception{b.put(key+"_sha256",digest(obj(b.get(key))));}
 static void schema()throws Exception{
  Map<String,Object> original=bundle("canonical");NativeReplacementDocuments.validateReconstruction(original);
  for(String key:original.keySet()){Map<String,Object> b=copy(original);b.remove(key);no("missing closed bundle field "+key,()->NativeReplacementDocuments.validateReconstruction(b));}
  Map<String,Object> extra=copy(original);extra.put("foreign",true);no("foreign top-level field",()->NativeReplacementDocuments.validateReconstruction(extra));
  for(String key:List.of("format","schema_version","upstream_commit")){Map<String,Object>b=copy(original);b.put(key,key.equals("schema_version")?999:"foreign");no("foreign bundle "+key,()->NativeReplacementDocuments.validateReconstruction(b));}
  for(String key:original.keySet())if(Boolean.FALSE.equals(original.get(key))){Map<String,Object>b=copy(original);b.put(key,true);no("forged authority "+key,()->NativeReplacementDocuments.validateReconstruction(b));}
  for(String key:original.keySet())if(key.endsWith("_sha256")){Map<String,Object>b=copy(original);b.put(key,"0".repeat(64));no("changed digest "+key,()->NativeReplacementDocuments.validateReconstruction(b));}
  Map<String,Object> wrongHistory=copy(original);Map<String,Object> h=obj(wrongHistory.get("original_history"));h.put(h.keySet().iterator().next(),"false");changedHash(wrongHistory,"original_history");no("non-Boolean original placed history",()->NativeReplacementDocuments.validateReconstruction(wrongHistory));
  Map<String,Object> large=copy(original);large.put("original_history",new LinkedHashMap<String,Object>());Map<String,Object> bigHistory=obj(large.get("original_history"));for(int i=0;i<100001;i++)bigHistory.put("opaque-"+i,false);changedHash(large,"original_history");no("history count exceeds native reconstruction bound",()->NativeReplacementDocuments.validateReconstruction(large));
  Map<String,Object> text=copy(original);obj(text.get("original_provenance")).put("job_file","x".repeat(8193));changedHash(text,"original_provenance");no("provenance string exceeds finite text bound",()->NativeReplacementDocuments.validateReconstruction(text));
  Map<String,Object> bytes=copy(original);Map<String,Object> byteHistory=new LinkedHashMap<>();for(int i=0;i<10000;i++)byteHistory.put("opaque-"+i+"-"+"x".repeat(1000),false);bytes.put("original_history",byteHistory);changedHash(bytes,"original_history");try{NativeReplacementDocuments.validateReconstruction(bytes);throw new AssertionError("Accepted bundle above journal byte bound");}catch(IOException expected){yes(expected.getMessage().contains("byte bound"),"Total bundle byte bound is checked before hash/semantic mismatch with individually bounded strings");refusals++;}
 }
 static void negative()throws Exception{
  Map<String,Object> nativeBefore=nativeFacts();Map<String,String> files=settings();Map<String,Object> b=bundle("canonical");
  for(String which:List.of("original","candidate","history","registration","identity","part-identity","package-identity")){
   NativeReplacementDocuments.Reconstructed r=NativeReplacementDocuments.reconstruct(config,b);Part part=config.getPart("R0603-1K");org.openpnp.model.Package pkg=part.getPackage();
   try{
    if(which.equals("original"))r.original().getBoardLocations().get(0).getBoard().getPlacement(0).setRank(999);
    else if(which.equals("candidate"))r.candidate().job().getBoardLocations().get(0).getBoard().getPlacement(0).setComments("changed candidate");
    else if(which.equals("history"))r.original().storePlacedStatus(r.original().getBoardLocations().get(0),"new-orphan",false);
    else if(which.equals("registration"))r.original().getBoardLocations().get(0).setLocalToParentTransform(AffineTransform.getTranslateInstance(8,9));
    else if(which.equals("identity")){Placement p=r.candidate().job().getBoardLocations().get(0).getBoard().getPlacement(0);p.setDefinition(p);}
    else if(which.equals("part-identity")){ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(part,out);Part other=Configuration.createSerializer().read(Part.class,new ByteArrayInputStream(out.toByteArray()));other.setPackage(pkg);config.addPart(other);}
    else {ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(pkg,out);org.openpnp.model.Package other=Configuration.createSerializer().read(org.openpnp.model.Package.class,new ByteArrayInputStream(out.toByteArray()));config.addPackage(other);}
    no("reconstructed live "+which+" drift",r::requireCurrent);
   }finally{config.addPart(part);config.addPackage(pkg);r.close();}
  }
  Part part=config.getPart("R0603-1K");Length height=part.getHeight();part.setHeight(new Length(1.25,LengthUnit.Millimeters));try{for(String name:names())no("fresh JVM changed Part dependency "+name,()->NativeReplacementDocuments.reconstruct(config,bundle(name)));}finally{part.setHeight(height);}
  String description=part.getPackage().getDescription();part.getPackage().setDescription("foreign package");try{for(String name:names())no("fresh JVM changed Package dependency "+name,()->NativeReplacementDocuments.reconstruct(config,bundle(name)));}finally{part.getPackage().setDescription(description);}
  Map<String,Object> mapping=copy(b);obj(mapping.get("source_mapping")).put("source_completed_history_entries",0);changedHash(mapping,"source_mapping");no("self-digested mapping inconsistent with native original history",()->NativeReplacementDocuments.reconstruct(config,mapping));
  Map<String,Object> malformed=copy(b);Map<String,Object> g=obj(malformed.get("candidate_graph"));obj(((List<?>)g.get("nodes")).get(0)).put("foreign_node_field",true);changedHash(malformed,"candidate_graph");no("self-digested foreign native graph field",()->NativeReplacementDocuments.reconstruct(config,malformed));
  Map<String,Object> badReference=copy(b);obj(badReference.get("candidate_graph")).put("root",-1);changedHash(badReference,"candidate_graph");no("negative native graph reference",()->NativeReplacementDocuments.reconstruct(config,badReference));
  noAuthority(nativeBefore,files,"Reconstruction refusals");
 }
 static void semantics()throws Exception{
  Map<String,Object> nativeBefore=nativeFacts();Map<String,String> files=settings();org.openpnp.model.Package pkg=config.getPart("R0603-1K").getPackage();VisionCompositing prior=(VisionCompositing)vision(pkg);
  try{
   pkg.setVisionCompositing(null);String absentXml=rawPackage(pkg);Job old=fixture("canonical");
   try(NativeReplacementJob.Candidate candidate=NativeReplacementJob.build(config,old)){
    Map<String,Object> absent=NativeReplacementDocuments.captureReconstruction(config,old,candidate);yes(((Number)absent.get("schema_version")).intValue()==2,"Absent-default capture is version2");yes(vision(pkg)==null&&absentXml.equals(rawPackage(pkg)),"Capture fingerprint does not initialize or mutate native absent vision-compositing");
    VisionCompositing explicit=pkg.getVisionCompositing();String explicitXml=rawPackage(pkg);yes(explicit!=null&&!absentXml.equals(explicitXml),"Actual native lazy getter produces a distinct serialized explicit default");
    try(NativeReplacementDocuments.Reconstructed r=NativeReplacementDocuments.reconstruct(config,absent)){r.requireCurrent();yes(same(r.candidate().mapping(),candidate.mapping()),"Version2 absent-default capture reconstructs against explicit pinned default");}yes(vision(pkg)==explicit&&explicitXml.equals(rawPackage(pkg)),"Reconstructing absent-to-default leaves current native package object and XML untouched");
    Map<String,Object> stated=NativeReplacementDocuments.captureReconstruction(config,old,candidate);yes(same(absent.get("required_packages"),stated.get("required_packages")),"Absent and explicit pinned default have exactly equal effective package fingerprints");yes(vision(pkg)==explicit&&explicitXml.equals(rawPackage(pkg)),"Explicit-default capture fingerprint preserves current native package identity and XML");
    pkg.setVisionCompositing(null);try(NativeReplacementDocuments.Reconstructed r=NativeReplacementDocuments.reconstruct(config,stated)){r.requireCurrent();yes(same(r.candidate().mapping(),candidate.mapping()),"Version2 explicit-default capture reconstructs against absent native default");}yes(vision(pkg)==null&&absentXml.equals(rawPackage(pkg)),"Reconstructing default-to-absent never invokes lazy package getter");
    for(String field:List.of("maxPickTolerance","extraShots")){
     VisionCompositing changed=new VisionCompositing();if(field.equals("maxPickTolerance"))changed.setMaxPickTolerance(new Length(9,LengthUnit.Millimeters));else changed.setExtraShots(changed.getExtraShots()+1);pkg.setVisionCompositing(changed);String changedXml=rawPackage(pkg);yes(!explicitXml.equals(changedXml),"Actual nondefault native "+field+" alters package serialization");no("nondefault package vision-compositing "+field,()->NativeReplacementDocuments.reconstruct(config,absent));yes(vision(pkg)==changed&&changedXml.equals(rawPackage(pkg)),"Refused nondefault "+field+" reconstruction preserves native package object and XML");
    }
    Files.writeString(root.resolve("semantics-absent-bundle.json"),JSON.toJson(absent));Files.writeString(root.resolve("semantics-default-bundle.json"),JSON.toJson(stated));
   }
  }finally{pkg.setVisionCompositing(prior);}noAuthority(nativeBefore,files,"Version2 package default equivalence");
 }
 static void compatibility()throws Exception{
  Map<String,Object> nativeBefore=nativeFacts();Map<String,String> files=settings();List<?> rows=(List<?>)NativeJournalJson.parseObject(Files.readString(legacy.resolve("catalog.json"))).get("rows");
  for(Object raw:rows){Map<String,Object> row=obj(raw);String name=(String)row.get("name");Map<String,Object> b=NativeJournalJson.parseObject(Files.readString(legacy.resolve(name+"-bundle.json")));yes(((Number)b.get("schema_version")).intValue()==1,"Recorded26 "+name+" bundle retains original version1");
   try(NativeReplacementDocuments.Reconstructed r=NativeReplacementDocuments.reconstruct(config,b)){r.requireCurrent();yes(same(row.get("original_shape"),shape(r.original()))&&same(row.get("candidate_shape"),shape(r.candidate().job()))&&same(row.get("original_history"),r.original().getPlacedStatusSnapshot())&&same(row.get("mapping"),r.candidate().mapping()),"Recorded26 "+name+" raw version1 reconstructs exact native content under matching fresh config");}
  }
  Map<String,Object> b=NativeJournalJson.parseObject(Files.readString(legacy.resolve("canonical-bundle.json")));org.openpnp.model.Package pkg=config.getPart("R0603-1K").getPackage();VisionCompositing prior=(VisionCompositing)vision(pkg);String before=rawPackage(pkg);try{if(prior==null)pkg.getVisionCompositing();else pkg.setVisionCompositing(null);yes(!before.equals(rawPackage(pkg)),"Legacy test changes only absent versus explicit native default XML");no("version1 retains raw serialized package strictness",()->NativeReplacementDocuments.reconstruct(config,b));}finally{pkg.setVisionCompositing(prior);}noAuthority(nativeBefore,files,"Version1 recorded bundle compatibility");
 }
 public static void main(String[] args)throws Exception{
  String mode=args[0];root=Paths.get(args[1]);legacy=args.length>2?Paths.get(args[2]):null;Files.createDirectories(root);int exit=0;
  try{
   if(mode.equals("schema")){schema();}
   else{
    Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);NativeReplacementJobTest.config=config;NativeReplacementJobTest.root=root;NativeReplacementDocumentsTest.config=config;NativeReplacementDocumentsTest.root=root;
    if(mode.equals("read"))no("reconstruction outside native executor",()->NativeReplacementDocuments.reconstruct(config,bundle("canonical")));
    task(()->{if(mode.equals("capture"))capture();else if(mode.equals("read"))reconstructed();else if(mode.equals("negative"))negative();else if(mode.equals("semantics"))semantics();else if(mode.equals("compatibility"))compatibility();else throw new AssertionError(mode);return null;});
   }
  }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{if(config!=null)config.getMachine().close();Map<String,Object> proof=NativeFaultedJobReplacement.map("passed",exit==0,"mode",mode,"assertions",checks,"refusals",refusals,"pid",ProcessHandle.current().pid(),"checks",facts,"scope","native graph reconstruction bundle only; fresh JVM without archive; no Bridge/lease/load/source binding/native initialization or placement","physical_qualified",false,"restart_reattachment_qualified",false);Files.writeString(root.resolve(mode+"-proof.json"),JSON.toJson(proof));System.out.println(JSON.toJson(NativeFaultedJobReplacement.map("passed",exit==0,"mode",mode,"assertions",checks,"refusals",refusals,"pid",ProcessHandle.current().pid())));}
  System.exit(exit);
 }
}
