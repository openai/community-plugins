/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.ReferencePnpJobProcessor;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.base.AbstractMachine;

/** Actual canonical native Part/Package objects and detached job graphs. Captured manifests
 * are deliberately malformed to test native dependency-set enforcement, never execution. */
public final class NativeReconstructionDependencySetTest {
 static final com.google.gson.Gson JSON=new com.google.gson.GsonBuilder().serializeNulls().setPrettyPrinting().create();
 static Configuration config;static Path root;static int checks,refusals;static final List<String> facts=new ArrayList<>(),violations=new ArrayList<>();static final List<Object> cases=new ArrayList<>();
 static Map<String,Object> map(Object...x){return NativeFaultedJobReplacement.map(x);}
 static Map<String,Object> obj(Object x)throws Exception{return NativeFaultedJobReplacement.object(x);}
 static Map<String,Object> copy(Map<String,Object>x){return NativeJournalJson.parseObject(JSON.toJson(x));}
 static void yes(boolean value,String why){checks++;if(!value)throw new AssertionError(why);facts.add(why);}
 static String sha(byte[] bytes)throws Exception{StringBuilder b=new StringBuilder();for(byte x:java.security.MessageDigest.getInstance("SHA-256").digest(bytes))b.append(String.format("%02x",x&255));return b.toString();}
 static String fingerprint(Object value)throws Exception{ByteArrayOutputStream out=new ByteArrayOutputStream();Configuration.createSerializer().write(value,out);return sha(out.toByteArray());}
 static <T>T task(Callable<T>x)throws Exception{try{return config.getMachine().submit(x,null,true).get(30,TimeUnit.SECONDS);}catch(ExecutionException e){if(e.getCause() instanceof Error)throw(Error)e.getCause();throw(Exception)e.getCause();}}
 static Map<String,Object> nativeFacts()throws Exception{Map<String,Object> trays=new TreeMap<>();for(org.openpnp.spi.Feeder f:config.getMachine().getFeeders())if(f instanceof ReferenceTrayFeeder)trays.put(f.getId(),((ReferenceTrayFeeder)f).getFeedCount());java.lang.reflect.Field job=ReferencePnpJobProcessor.class.getDeclaredField("job");job.setAccessible(true);var gate=((AbstractMachine)config.getMachine()).getExternalExecutionControl();return map("processor_job_absent",job.get(config.getMachine().getPnpJobProcessor())==null,"owner",gate.getOwnerLabel(),"pending",gate.getPendingTaskCount(),"enabled",config.getMachine().isEnabled(),"homed",config.getMachine().isHomed(),"source",NativeVacuumSources.inspect(config),"trays",trays,"boards",config.getBoards().size(),"panels",config.getPanels().size());}
 static Job twoPackageJob(Part a,Part b){Board board=new Board();board.setName("Two exact native dependencies");board.setDimensions(new Location(LengthUnit.Millimeters,20,20,0,0));int i=0;for(Part part:List.of(a,b)){Placement p=new Placement("P"+(++i));p.setPart(part);p.setLocation(new Location(LengthUnit.Millimeters,3+i,5,0,0));p.setType(Placement.Type.Placement);board.addPlacement(p);}BoardLocation loc=new BoardLocation(board);loc.setId("A");Job old=new Job();old.addBoardOrPanelLocation(loc);PanelLocation.setParentsOfAllDescendants(old.getRootPanelLocation());old.storePlacedStatus(loc,"P1",true);old.storePlacedStatus(loc,"removed-opaque",false);return old;}
 static void mustRefuse(String name,Map<String,Object> bundle,org.openpnp.model.Package omitted,String capturedHash)throws Exception{
  Map<String,Object> before=nativeFacts();boolean pureAccepted=false;String pureFailure=null;try{NativeReplacementDocuments.validateReconstruction(bundle);pureAccepted=true;}catch(IOException invalid){pureFailure=invalid.getMessage();}
  boolean accepted=false;String failureType=null,message=null;Map<String,Object> reconstructed=null;
  try(NativeReplacementDocuments.Reconstructed value=NativeReplacementDocuments.reconstruct(config,bundle)){
   value.requireCurrent();accepted=true;reconstructed=map("source_mapping_exact",NativeFaultedJobReplacement.same(value.candidate().mapping(),bundle.get("source_mapping")),"candidate_empty",value.candidate().job().getPlacedStatusSnapshot().isEmpty(),"original_history",value.original().getPlacedStatusSnapshot(),"current_omitted_package_fingerprint",fingerprint(omitted),"actual_reconstructed_package_ids",value.candidate().job().getBoardLocations().get(0).getBoard().getPlacements().stream().map(p->p.getPart().getPackage().getId()).sorted().toList());
  }catch(IOException|Bridge.Fault expected){failureType=expected.getClass().getName();message=expected.getMessage();refusals++;}
  checks++;if(accepted)violations.add(name+" was accepted by native reconstruction");else facts.add("refused "+name+": "+message);
  yes(NativeFaultedJobReplacement.same(before,nativeFacts()),name+" preserves native job/gate/source/counters/library facts");
  Map<String,Object> row=map("name",name,"native_reconstruction_accepted",accepted,"pure_validation_accepted",pureAccepted,"pure_validation_failure",pureFailure,"failure_type",failureType,"failure_message",message,"required_parts",bundle.get("required_parts"),"required_packages",bundle.get("required_packages"),"captured_omitted_package_fingerprint",capturedHash,"current_omitted_package_fingerprint",fingerprint(omitted),"reconstructed",reconstructed,"native_facts_before",before,"native_facts_after",nativeFacts());cases.add(row);Files.writeString(root.resolve(name+"-bundle.json"),JSON.toJson(bundle));Files.writeString(root.resolve(name+"-observation.json"),JSON.toJson(row));
 }
 static void run()throws Exception{
  org.openpnp.model.Package secondPackage=new org.openpnp.model.Package("RECON-SECOND-PACKAGE");secondPackage.setDescription("original exact second package");config.addPackage(secondPackage);Part second=new Part("RECON-SECOND-PART");second.setPackage(secondPackage);second.setHeight(new Length(1,LengthUnit.Millimeters));config.addPart(second);
  org.openpnp.model.Package extra=new org.openpnp.model.Package("RECON-UNREFERENCED-PACKAGE");extra.setDescription("not referenced by either native part");config.addPackage(extra);
  Part first=config.getPart("R0603-1K");yes(first!=null&&first.getPackage()!=secondPackage,"Fixture uses two exact canonical parts in two distinct canonical native packages");Job old=twoPackageJob(first,second);Map<String,Object> nativeBefore=nativeFacts();
  try(NativeReplacementJob.Candidate candidate=NativeReplacementJob.build(config,old)){
   Map<String,Object> bundle=NativeReplacementDocuments.captureReconstruction(config,old,candidate);Files.writeString(root.resolve("captured-bundle.json"),JSON.toJson(bundle));Set<String> partIds=Set.of(first.getId(),second.getId()),packageIds=Set.of(first.getPackage().getId(),secondPackage.getId());yes(obj(bundle.get("required_parts")).keySet().equals(partIds)&&obj(bundle.get("required_packages")).keySet().equals(packageIds),"Real capture binds the exact two-part/two-package dependency union");
   String captured=fingerprint(secondPackage),originalDescription=secondPackage.getDescription();
   try(NativeReplacementDocuments.Reconstructed exact=NativeReplacementDocuments.reconstruct(config,bundle)){exact.requireCurrent();yes(NativeFaultedJobReplacement.same(exact.candidate().mapping(),candidate.mapping())&&exact.candidate().job().getPlacedStatusSnapshot().isEmpty(),"Exact two-package bundle reconstructs without native job execution");}
   Map<String,Object> missing=copy(bundle);obj(missing.get("required_packages")).remove(secondPackage.getId());yes(obj(missing.get("required_packages")).size()==1&&obj(missing.get("required_parts")).size()==2,"Malformed bundle retains another listed package and both original part requirements");mustRefuse("omitted-package-unchanged",missing,secondPackage,captured);
   secondPackage.setDescription("changed real native serialized package after capture");yes(!captured.equals(fingerprint(secondPackage)),"Actual omitted native package serialized fingerprint changed after capture");try{mustRefuse("omitted-package-mutated",missing,secondPackage,captured);}finally{secondPackage.setDescription(originalDescription);}
   Map<String,Object> surplus=copy(bundle);obj(surplus.get("required_packages")).put(extra.getId(),fingerprint(extra));mustRefuse("extra-unreferenced-package",surplus,secondPackage,captured);
   candidate.requireCurrent();try(NativeReplacementDocuments.Reconstructed exact=NativeReplacementDocuments.reconstruct(config,bundle)){exact.requireCurrent();yes(NativeFaultedJobReplacement.same(exact.original().getPlacedStatusSnapshot(),old.getPlacedStatusSnapshot()),"Restored exact dependency set still reconstructs complete original history");}
   yes(NativeFaultedJobReplacement.same(nativeBefore,nativeFacts()),"All dependency-set probes preserve native authority and machine facts");
  }
 }
 public static void main(String[] args)throws Exception{root=Paths.get(args[0]);Files.createDirectories(root);int exit=0;Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);
  try{task(()->{run();return null;});if(!violations.isEmpty())exit=1;}catch(Throwable error){error.printStackTrace();violations.add(error.toString());exit=1;}finally{config.getMachine().close();Map<String,Object> proof=map("passed",exit==0,"assertions",checks,"refusals",refusals,"cases",cases,"violations",violations,"checks",facts,"pid",ProcessHandle.current().pid(),"scope","actual two-part/two-package native reconstruction dependency-set validation only","native_job_execution",false,"restart_reattachment_qualified",false,"physical_qualified",false);Files.writeString(root.resolve("proof.json"),JSON.toJson(proof));System.out.println(JSON.toJson(map("passed",exit==0,"assertions",checks,"refusals",refusals,"violations",violations,"pid",ProcessHandle.current().pid())));}System.exit(exit);
 }
}
