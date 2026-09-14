/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.io.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.*;

/** Forced replacement progress boundaries on actual native Job, tray, board and lineage components.
 * Initial action/sensing failure records are explicit synthetic contract fixtures; no native PnP job runs.
 * This suite proves read/replay classification and ownership retention, not continuation or reattachment. */
public final class NativeReplacementProgressTest {
 static final Gson JSON=new GsonBuilder().serializeNulls().create();static Configuration config;static ReferenceTrayFeeder tray,otherTray;static Path root;static int checks,refusals;static final List<Map<String,Object>> cases=new ArrayList<>();
 interface Work{void run()throws Exception;}static void yes(boolean x,String why){checks++;if(!x)throw new AssertionError(why);}static void no(String why,Work w)throws Exception{try{w.run();throw new AssertionError("Accepted "+why);}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){checks++;refusals++;}}
 static Map<String,Object> m(Object...v){return NativeFaultedJobReplacement.map(v);}static Map<String,Object> o(Object p)throws IOException{return NativeFaultedJobReplacement.object(p);}static String id(){return UUID.randomUUID().toString();}
 static <T>T task(Callable<T> c)throws Exception{try{return config.getMachine().submit(c,null,true).get(30,TimeUnit.SECONDS);}catch(ExecutionException e){if(e.getCause() instanceof Error)throw(Error)e.getCause();throw(Exception)e.getCause();}}
 static final class Env implements AutoCloseable {
  final String jobId=id(),aliasId=id(),opId=id(),requestId=id();final NativeMaterialLoads material;final NativeBoardLoads boards;final NativeJobLineage lineage;final NativeFaultedJobReplacement replacement;final Path file;final FileChannel channel;
  final List<Map<String,Object>> events=new ArrayList<>();Job oldJob,freshJob;Map<String,Object> operation,oldMaterial,oldBoard,oldLineage;NativeFaultedJobReplacement.Capture capture;NativeFaultedJobReplacement.Permit permit;NativeSensingReconciliation.Capture faultCapture;NativeSensingReconciliation coordinator;NativeVacuumJournal vacuum;final Object localOwner=new Object();String failType;boolean failAfterForce;int failOccurrence=1,seenFailures;long seq;NativeReplacementJob.Candidate candidate;String taskId;
  Env(String name,boolean replay)throws Exception {
   file=root.resolve(name+".jsonl");channel=FileChannel.open(file,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);lineage=new NativeJobLineage(this::append);boards=new NativeBoardLoads(this::append);material=new NativeMaterialLoads(config,this::append);replacement=new NativeFaultedJobReplacement(config,material,boards,lineage,this::append);
   if(!replay)task(()->{otherTray.setFeedCount(2);material.register(otherTray.getId(),otherTray.getPart().getId(),(String)NativeMaterialLoads.describe(otherTray).get("geometry_sha256"),"bind-existing",null,material.revision());tray.setFeedCount(2);material.register(tray.getId(),tray.getPart().getId(),(String)NativeMaterialLoads.describe(tray).get("geometry_sha256"),"bind-existing",null,material.revision());oldJob=CanonicalJobImporter.load(config,NativeBoardLoadsTest.canonical(tray.getPart()));lineage.createFresh(jobId,"canonical-simulator");Map<String,Object> lineageFacts=lineage.admissionFacts(jobId);lineage.bindDocument(jobId,"b".repeat(64));lineage.bindReload(aliasId,"b".repeat(64),(String)lineageFacts.get("lineage_id"),((Number)lineageFacts.get("lineage_revision")).longValue());boards.bindJob(oldJob,jobId,true);BoardLocation board=oldJob.getBoardLocations().get(0);oldJob.storePlacedStatus(board,"R1",true);boards.checkpoint(oldJob);
     operation=m("operation_id",opId,"job_id",jobId,"job_revision",boards.jobRevision(),"board_load_revision",boards.revision(),"request_id",requestId,"request_digest","a".repeat(64),"config_revision","cfg-2","method","openpnp_start_job","job_lineage",lineage.admissionFacts(jobId),"state","accepted");append("operation",operation);
     Map<String,Object> loadScope=boards.ledgerScope(oldJob);NativeActionLedger ledger=new NativeActionLedger(this::append,opId,jobId,"cfg-2",(String)loadScope.get("scope_id"),oldJob,loadScope).withMaterialObserver(material);
     try(NativeActionLedger.StepScope step=ledger.openStep(1)){config.getScripting().on("Feeder.BeforeFeed",m("nozzle",config.getMachine().getDefaultHead().getDefaultNozzle(),"feeder",tray,"part",tray.getPart()));tray.setFeedCount(3);}catch(NativeActionLedger.UnresolvedActionFence expected){}
     operation.put("state","outcome_unknown");append("operation",operation);oldMaterial=material.captureReplacementState();oldBoard=boards.captureReplacementState(jobId);oldLineage=lineage.captureReplacementState(jobId);capture=replacement.capture(oldJob,jobId,opId);candidate=NativeReplacementJob.build(config,oldJob);freshJob=candidate.job();return null;});
  }
  void append(String type,Map<String,Object> p)throws Exception {
   Runnable mat=material==null?()->{}:material.prepareEvent(type,p);Runnable lin=lineage==null||type.startsWith("job_lineage_")?()->{}:lineage.prepareObservation(type,p);
   boolean fail=type.equals(failType)&&++seenFailures==failOccurrence;
   if(fail&&!failAfterForce)throw new IOException("injected before force: "+type);Map<String,Object> event=NativeFaultedJobReplacement.frozen(m("sequence",++seq,"type",type,"payload",p));byte[] bytes=(JSON.toJson(event)+"\n").getBytes(java.nio.charset.StandardCharsets.UTF_8);ByteBuffer buffer=ByteBuffer.wrap(bytes);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);events.add(event);if(fail&&failAfterForce)throw new IOException("injected uncertain return after actual force: "+type);mat.run();lin.run();if(boards!=null)boards.observeNativeEvent(type,p);if(replacement!=null)replacement.observe(type,p);
  }
  void admit()throws Exception {
   vacuum=new NativeVacuumJournal();coordinator=new NativeSensingReconciliation(vacuum,(op,instance,replay)->false,replacement::verified);
   String instance=NativeSensingReconciliationTest.INSTANCE,source=NativeSensingReconciliationTest.SOURCE,machine=NativeSensingReconciliationTest.MACHINE;
   Map<String,Object> jobContext=m("job_id",jobId,"job_revision",oldBoard.get("job_revision"),"board_load_revision",oldBoard.get("revision"),"material_setup_revision",oldMaterial.get("revision"),"lineage_id",oldLineage.get("lineage_id"),"lineage_revision",oldLineage.get("lineage_revision"));
   Map<String,Object> context=NativeSensingReconciliationTest.context();context.put("operation_id",opId);context.put("scope","job");context.put("job_context",jobContext);
   Map<String,Object> event=NativeSensingReconciliationTest.event("read.before",100,null,context,source);vacuum.prepare("vacuum_observation_intent",event,instance).run();Map<String,Object> scope=NativeSensingReconciliationTest.scope(instance,source);scope.put("job_context",jobContext);scope.put("dependencies",capture.dependencies());faultCapture=vacuum.captureFaultSet(scope);
   String task=id(),requestOperation=id(),recovery=id();taskId=task;Map<String,Object> taskRecord=coordinator.taskRecord(task,requestOperation,id(),faultCapture,"replace-faulted-job-attempt",1,"2030-01-01T00:00:00Z");coordinator.prepare("sensing_reconciliation_task",taskRecord,instance).run();coordinator.prepare("sensing_reconciliation_intent",coordinator.intentRecord(task,recovery,id(),"2026-09-14T00:00:00Z",1,"synthetic-local-test"),instance).run();NativeSensingReconciliation.Permit recoveryPermit=coordinator.issuePermit(task,recovery,faultCapture.digest,localOwner);
   permit=replacement.begin(capture,freshJob,coordinator,recoveryPermit,localOwner);
  }
  void replay(List<Map<String,Object>> source)throws Exception {for(Map<String,Object> event:source){String t=(String)event.get("type");Map<String,Object> p=o(event.get("payload"));material.recoverEvent(t,p);boards.recoverEvent(t,p);boards.observeNativeEvent(t,p);lineage.observe(t,p);replacement.observe(t,p);if(t.startsWith("faulted_job_replacement_"))replacement.recover(t,p);}material.finishRecovery();boards.finishRecovery();}
  public void close()throws Exception{try{replacement.close();}finally{if(candidate!=null&&!replacement.ownsCandidate(candidate))candidate.close();channel.close();}}
 }


 static Map<String,Integer> counters(){return Map.of(tray.getId(),tray.getFeedCount(),otherTray.getId(),otherTray.getFeedCount());}
 static Map<String,Object> cp(Map<String,Object> p)throws IOException{return NativeFaultedJobReplacement.mutable(p);}
 static List<String> ids(Map<String,Object> p,String key){return new ArrayList<>((List<String>)p.get(key));}
 static int number(Object n){return ((Number)n).intValue();}
 static void write(String name,Object value)throws Exception{Files.writeString(root.resolve(name),JSON.toJson(value)+"\n",StandardOpenOption.CREATE_NEW);}
 static String phase(Map<String,Object> p,String stage)throws Exception{return (String)o(p.get(stage)).get("phase");}
 static void stageCounts(Map<String,Object> p,String stage,int completed,int pending)throws Exception {
  Map<String,Object> stageView=o(p.get(stage)),counts=o(stageView.get("counts"));List<?> rows=(List<?>)stageView.get("rows");
  yes(rows.size()==2&&number(counts.get("completed"))==completed&&number(counts.get("pending"))==pending&&number(counts.get("untouched"))==2-completed-pending,stage+" reports exact mixed completed/pending/untouched counts");
  Set<String> seen=new HashSet<>();for(Object raw:rows){Map<String,Object> row=o(raw);String state=(String)row.get("phase"),old=(String)row.get("old_load_id");yes(seen.add(old),stage+" original load IDs are unique");
   if(state.equals("untouched"))yes(row.get("new_load")==null&&row.get("parent_intent")==null&&row.get("parent_outcome")==null&&old.equals(row.get("current_load_id")),stage+" untouched load has no fabricated receipt or replacement");
   else {Map<String,Object> fresh=o(row.get("new_load"));yes(!old.equals(fresh.get("load_id"))&&fresh.get("load_id").equals(row.get("current_load_id")),stage+" replacement names distinct current load");Map<String,Object> parent=o(row.get("parent_intent"));yes(parent.get("payload_sha256").equals(NativeFaultedJobReplacement.digest(o(parent.get("payload")))),stage+" intent descriptor has exact forced record digest");
    yes(state.equals("completed")== (row.get("parent_outcome")!=null),stage+" completion requires its own actual outcome record");
    if(state.equals("pending"))yes("loading_unknown".equals(fresh.get("state")),stage+" pending replacement retains loading_unknown despite native setters");
   }
  }
  yes(Boolean.FALSE.equals(stageView.get("execution_authority_restored")),stage+" progress grants no execution authority");
 }
 static void preserved(Env e,Map<String,Object> p,Map<String,Boolean> history)throws Exception {
  yes(Boolean.TRUE.equals(p.get("original_outcomes_preserved"))&&e.jobId.equals(p.get("original_job_id"))&&e.opId.equals(p.get("original_operation_id")),"Progress binds preserved exact original operation and job");
  yes(NativeFaultedJobReplacement.same(p.get("original_dependencies"),e.capture.dependencies()),"Progress exports the complete exact captured dependency union");
  yes(history.equals(e.oldJob.getPlacedStatusSnapshot()),"Original native placed history remains unchanged");
  for(String stage:List.of("material","boards")){Map<String,Object> old=o(o(e.capture.payload.get(stage)).get("loads"));
   for(Object raw:(List<?>)o(p.get(stage)).get("rows")){Map<String,Object> row=o(raw);yes(NativeFaultedJobReplacement.same(old.get(row.get("old_load_id")),row.get("old_load")),stage+" original forced load facts remain unchanged");}
  }
  yes(Boolean.FALSE.equals(p.get("execution_authority_restored"))&&Boolean.FALSE.equals(p.get("continuation_supported")),"Progress remains diagnostic and grants no continuation authority");
 }
 static void unchangedRead(Env e,String attempt)throws Exception {
  long size=Files.size(e.file);List<Map<String,Object>> rows=new ArrayList<>(e.events);Map<String,Integer> before=counters();Map<String,Object> first=e.replacement.progress(attempt);
  yes(NativeFaultedJobReplacement.same(first,e.replacement.progress(attempt)),"Repeated progress read is stable");
  yes(e.replacement.progressForTask(e.taskId).size()==1&&NativeFaultedJobReplacement.same(first,e.replacement.progressForTask(e.taskId).get(0)),"Task lookup returns only its exact transaction progress");
  yes(e.replacement.progressForTask(id()).isEmpty(),"Foreign task lookup has no transaction to expose");
  no("foreign replacement transaction",()->e.replacement.progress(id()));
  yes(before.equals(counters())&&size==Files.size(e.file)&&rows.equals(e.events),"Progress, foreign lookups and task lookup append no event and preserve native tray counters");
 }
 static void existingCaptureRefused(Env e)throws Exception {
  long bytes=Files.size(e.file);Map<String,Integer> before=counters();
  task(()->{for(String job:List.of(e.jobId,e.aliasId)){try{e.replacement.capture(e.oldJob,job,e.opId);throw new AssertionError("Existing alias replacement accepted");}
   catch(IOException refused){yes(refused.getMessage().contains("continuation")||refused.getMessage().contains("uncertain replacement"),"Existing replacement or uncertain owner fences recapture before repeated native effects");}}
   return null;});
  yes(before.equals(counters())&&bytes==Files.size(e.file),"Rejected recapture on every original alias appends nothing and changes no native material");
 }
 static final class Boundary {
  final String name,event;final int occurrence,matDone,matPending,boardDone,boardPending;final boolean retain,replace,lineage,compound,afterForce;
  Boundary(String name,String event,int occurrence,boolean retain,boolean replace,boolean lineage,int md,int mp,int bd,int bp,boolean compound,boolean afterForce){this.name=name;this.event=event;this.occurrence=occurrence;this.retain=retain;this.replace=replace;this.lineage=lineage;matDone=md;matPending=mp;boardDone=bd;boardPending=bp;this.compound=compound;this.afterForce=afterForce;}
 }
 static void boundary(Boundary b)throws Exception {
  int start=checks;try(Env e=new Env(b.name,false)){
   task(()->{e.admit();if(b.retain)e.replacement.retainCandidate(e.permit,e.candidate);return null;});String attempt=e.permit.attemptId();Map<String,Boolean> history=new TreeMap<>(e.oldJob.getPlacedStatusSnapshot());
   yes(ids(e.capture.dependencies(),"material_load_ids").size()==2&&ids(e.capture.dependencies(),"board_load_ids").size()==2,"Boundary owns two actual native trays and two board roots");
   e.failType=b.event;e.failOccurrence=b.occurrence;e.failAfterForce=b.afterForce;
   if(b.replace)task(()->{if(b.event==null)e.replacement.replace(e.permit);else no("controlled stop at "+b.name,()->e.replacement.replace(e.permit));return null;});e.failType=null;
   Map<String,Object> live=e.replacement.progress(attempt);write(b.name+"-live-progress.json",live);
   yes(phase(live,"lineage").equals(b.lineage?"completed":"untouched"),"Lineage phase reflects its exact forced boundary");
   yes(phase(live,"definition").equals(b.retain?"completed":"untouched"),"Definition phase reflects explicit retained candidate record");
   yes(phase(live,"compound").equals(b.compound&&!b.afterForce?"completed":"pending"),"Current compound phase never guesses completion after uncertain force return");
   yes(phase(live,"publication").equals("untouched"),"Subreceipt progress cannot claim GUI job publication");
   stageCounts(live,"material",b.matDone,b.matPending);stageCounts(live,"boards",b.boardDone,b.boardPending);preserved(e,live,history);unchangedRead(e,attempt);existingCaptureRefused(e);
   if(b.retain){yes(e.replacement.ownsCandidate(e.candidate),"Component retains candidate beyond a callback's lifetime");if(!e.replacement.ownsCandidate(e.candidate))e.candidate.close();task(()->{e.candidate.requireCurrent();return null;});yes(true,"Callback cleanup preserves valid detached native candidate");}
   e.permit.close();task(()->{no("spent authority cannot repeat replacement",()->e.replacement.replace(e.permit));return null;});
   if(b.retain){if(Boolean.FALSE.equals(live.get("publication_fault")))yes(task(()->e.replacement.retainedCandidate(attempt))==e.candidate,"Read-only retained candidate lookup survives permit closure");
    else no("uncertain owner cannot return continuation candidate",()->task(()->e.replacement.retainedCandidate(attempt)));}
   if(b.compound){Map<String,Object> d=(b.afterForce?null:e.replacement.dispositions(attempt,"none-present"));if(d!=null)yes(!e.replacement.verified(e.faultCapture.payload,d,false),"Closed callback permit cannot turn completed subreceipts into live disposition authority");}
   // Nonzero sentinels make any accidental replay reset observable on both actual native feeders.
   task(()->{tray.setFeedCount(4);otherTray.setFeedCount(5);return null;});Map<String,Integer> sentinel=counters();
   try(Env replay=new Env(b.name+"-replay",true)){
    replay.replay(e.events);Map<String,Object> recovered=replay.replacement.progress(attempt);write(b.name+"-replayed-progress.json",recovered);
    stageCounts(recovered,"material",b.matDone,b.matPending);stageCounts(recovered,"boards",b.boardDone,b.boardPending);preserved(e,recovered,history);
    Map<String,Object> boardSnapshot=replay.boards.snapshot();yes(Boolean.TRUE.equals(boardSnapshot.get("restart_presence_confirmation_required")),"Replayed board presence still requires an explicit fresh confirmation");
    for(Object raw:(List<?>)boardSnapshot.get("loads")){Map<String,Object> load=o(raw);if(ids(e.capture.dependencies(),"board_load_ids").contains(load.get("load_id")))yes("presence_unconfirmed".equals(load.get("state")),"Original loaded board retains exact durable facts while public restart view is presence_unconfirmed");}
    for(Object raw:(List<?>)o(recovered.get("boards")).get("rows")){Map<String,Object> binding=o(o(raw).get("binding_status"));yes(Boolean.FALSE.equals(binding.get("native_job_binding_retained"))&&Boolean.FALSE.equals(binding.get("root_confirmation_retained")),"Board progress cannot recover native job binding or root confirmation");}
    for(Object raw:(List<?>)replay.material.snapshot().get("loads"))yes(Boolean.FALSE.equals(o(raw).get("native_authority")),"Material replay leaves every load without native authority");
    no("replayed original board cannot become ready",()->task(()->{replay.boards.requireReady(e.oldJob);return null;}));
    no("replayed replacement board cannot become ready",()->task(()->{replay.boards.requireReady(e.freshJob);return null;}));
    yes(phase(recovered,"compound").equals(b.compound?"completed":"pending"),"Replay classifies the actual forced compound prefix");
    yes(Boolean.FALSE.equals(o(recovered.get("definition")).get("candidate_retained_in_process")),"Reducer replay cannot reconstruct native candidate ownership");
    yes(sentinel.equals(counters())&&Files.size(replay.file)==0&&replay.events.isEmpty(),"Replay keeps both nonzero native sentinels and writes no native or journal action");
    no("replay candidate is not attached",()->task(()->replay.replacement.retainedCandidate(attempt)));
    if(b.compound){Map<String,Object> d=replay.replacement.dispositions(attempt,"none-present");yes(replay.replacement.verified(e.faultCapture.payload,d,true)&&!replay.replacement.verified(e.faultCapture.payload,d,false),"Completed replayed subreceipts verify history only");}
    no("replayed material lacks execution binding",()->task(()->{replay.material.requireReady();return null;}));
    long length=Files.size(replay.file);Map<String,Object> reread=replay.replacement.progress(attempt);yes(NativeFaultedJobReplacement.same(recovered,reread)&&sentinel.equals(counters())&&length==Files.size(replay.file),"Repeated recovered progress remains read-only and stable");
   }
   if(b.matDone+b.matPending>0){List<String> loads=ids(e.capture.dependencies(),"material_load_ids");String recovery=(String)live.get("recovery_operation_id"),digest=(String)live.get("fault_set_sha256");
    no("foreign material operation",()->e.material.replacementProgress(id(),digest,loads));no("foreign material fault digest",()->e.material.replacementProgress(recovery,"f".repeat(64),loads));
    String affected=null;for(Object raw:(List<?>)o(live.get("material")).get("rows"))if(!"untouched".equals(o(raw).get("phase")))affected=(String)o(raw).get("old_load_id");List<String> omitted=new ArrayList<>(loads);omitted.remove(affected);no("omitted material parent",()->e.material.replacementProgress(recovery,digest,omitted));
   }
   if(b.boardDone+b.boardPending>0){List<String> loads=ids(e.capture.dependencies(),"board_load_ids");String recovery=(String)live.get("recovery_operation_id"),digest=(String)live.get("fault_set_sha256");
    no("foreign board replacement attempt",()->e.boards.replacementProgress(recovery,digest,id(),loads));
    String affected=null;for(Object raw:(List<?>)o(live.get("boards")).get("rows"))if(!"untouched".equals(o(raw).get("phase")))affected=(String)o(raw).get("old_load_id");List<String> omitted=new ArrayList<>(loads);omitted.remove(affected);no("omitted board parent",()->e.boards.replacementProgress(recovery,digest,attempt,omitted));
   }
   cases.add(m("name",b.name,"assertions",checks-start,"replacement_attempt_id",attempt,"forced_event_count",e.events.size(),"live_progress",b.name+"-live-progress.json","replayed_progress",b.name+"-replayed-progress.json","actual_native_job_initializations",0,"actual_native_feeds",0,"actual_native_placements",0));
  }
 }
 static void definitionForceFailure(boolean afterForce)throws Exception {
  String name="definition-"+(afterForce?"after":"before")+"-force";int start=checks;
  try(Env e=new Env(name,false)){
   task(()->{e.admit();e.failType="faulted_job_replacement_definition";e.failAfterForce=afterForce;no("definition force failure",()->e.replacement.retainCandidate(e.permit,e.candidate));return null;});String attempt=e.permit.attemptId();
   yes(e.replacement.ownsCandidate(e.candidate),"Ownership transfer survives ambiguous definition append");
   if(!e.replacement.ownsCandidate(e.candidate))e.candidate.close();task(()->{e.candidate.requireCurrent();return null;});yes(true,"Unknown definition publication cannot destroy the component-owned native graph");
   Map<String,Object> p=e.replacement.progress(attempt);yes(phase(p,"definition").equals("untouched")&&Boolean.TRUE.equals(p.get("publication_fault")),"Current uncertain definition publication is not reported completed");
   e.permit.close();task(()->{no("uncertain owner has no execution permit",()->e.replacement.replace(e.permit));return null;});
   try(Env replay=new Env(name+"-replay",true)){Map<String,Integer> before=counters();replay.replay(e.events);Map<String,Object> r=replay.replacement.progress(attempt);yes(phase(r,"definition").equals(afterForce?"completed":"untouched")&&Boolean.FALSE.equals(o(r.get("definition")).get("candidate_retained_in_process")),"Definition replay reflects durable record but restores no candidate");yes(before.equals(counters())&&Files.size(replay.file)==0,"Definition replay performs no native tray change or journal write");}
   e.replacement.close();task(()->{try{e.candidate.requireCurrent();throw new AssertionError("Closed component leaked unpublished candidate");}catch(IOException expected){yes(true,"Component shutdown releases unpublished native candidate graph");}return null;});
   cases.add(m("name",name,"assertions",checks-start,"replacement_attempt_id",attempt));
  }
 }
 static void coordinatorRevocation()throws Exception {
  String name="coordinator-ownership-revoked";int start=checks;try(Env e=new Env(name,false)){
   task(()->{e.admit();e.replacement.retainCandidate(e.permit,e.candidate);Map<String,Object> unknown=e.coordinator.unknownRecord(e.taskId,"ownership_changed");Runnable revoke=e.coordinator.prepare("sensing_reconciliation_unknown",unknown,NativeSensingReconciliationTest.INSTANCE);e.append("sensing_reconciliation_unknown",unknown);revoke.run();return null;});
   String attempt=e.permit.attemptId();Map<String,Integer> before=counters();long bytes=Files.size(e.file);Map<String,Boolean> old=new TreeMap<>(e.oldJob.getPlacedStatusSnapshot());
   yes("reconciliation_unknown".equals(e.coordinator.snapshot(e.taskId).get("state")),"Actual coordinator transition revokes the original local permit");
   yes(task(()->e.replacement.retainedCandidate(attempt))==e.candidate,"Revoked coordinator still preserves the exact component-owned graph for later diagnosis");
   task(()->{no("revoked coordinator cannot execute replacement",()->e.replacement.replace(e.permit));no("revoked coordinator cannot begin publication",()->e.replacement.beginPublication(e.permit));return null;});
   Map<String,Object> progress=e.replacement.progress(attempt);preserved(e,progress,old);stageCounts(progress,"material",0,0);stageCounts(progress,"boards",0,0);
   yes("pending".equals(phase(progress,"compound"))&&"untouched".equals(phase(progress,"publication")),"Retained graph after revocation is not a completed or published replacement");
   yes(before.equals(counters())&&bytes==Files.size(e.file),"Revoked execution/publication attempts perform no native setter or new journal effect");
   write(name+"-progress.json",progress);cases.add(m("name",name,"assertions",checks-start,"replacement_attempt_id",attempt));
  }
 }
 public static void main(String[]args)throws Exception {
  if(args.length!=2)throw new IllegalArgumentException("Expected runtime marker and exclusive evidence directory");root=Path.of(args[1]);Files.createDirectory(root);int exit=0;Throwable error=null;
  Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);
  for(Feeder f:config.getMachine().getFeeders()){if(f.getPart()==config.getPart("R0603-1K"))tray=(ReferenceTrayFeeder)f;if(f.getPart()==config.getPart("R0402-1K"))otherTray=(ReferenceTrayFeeder)f;}
  tray.setTrayCountX(3);tray.setTrayCountY(2);otherTray.setTrayCountX(3);otherTray.setTrayCountY(2);
  try {
   List<Boundary> boundaries=List.of(
    new Boundary("intent-only",null,1,false,false,false,0,0,0,0,false,false),
    new Boundary("definition-only",null,1,true,false,false,0,0,0,0,false,false),
    new Boundary("lineage-completed","material_replacement_intent",1,true,true,true,0,0,0,0,false,false),
    new Boundary("first-material-pending","material_replacement_outcome",1,true,true,true,0,1,0,0,false,false),
    new Boundary("first-material-completed","material_replacement_intent",2,true,true,true,1,0,0,0,false,false),
    new Boundary("second-material-pending","material_replacement_outcome",2,true,true,true,1,1,0,0,false,false),
    new Boundary("materials-completed","board_replacement_intent",1,true,true,true,2,0,0,0,false,false),
    new Boundary("first-board-pending","board_replacement_outcome",1,true,true,true,2,0,0,1,false,false),
    new Boundary("first-board-completed","board_replacement_intent",2,true,true,true,2,0,1,0,false,false),
    new Boundary("last-board-pending","board_replacement_outcome",2,true,true,true,2,0,1,1,false,false),
    new Boundary("all-subreceipts-completed","faulted_job_replacement_outcome",1,true,true,true,2,0,2,0,false,false),
    new Boundary("compound-completed",null,1,true,true,true,2,0,2,0,true,false),
    new Boundary("compound-force-uncertain","faulted_job_replacement_outcome",1,true,true,true,2,0,2,0,true,true));
   for(Boundary b:boundaries)boundary(b);definitionForceFailure(false);definitionForceFailure(true);coordinatorRevocation();
  }catch(Throwable t){t.printStackTrace();error=t;exit=1;}finally{config.getMachine().close();Map<String,Object> result=m("passed",exit==0,"assertions",checks,"refusals",refusals,"cases",cases,"error",error==null?null:error.toString(),"initial_fault_fixture","synthetic sensing and manually invoked native action hooks; actual native models and FileChannel.force","actual_native_job_initializations",0,"actual_native_feeds",0,"actual_native_placements",0,"bridge_integration_qualified",false,"continuation_qualified",false,"restart_reattachment_qualified",false,"hardware_qualified",false);write("proof.json",result);System.out.println("NATIVE_REPLACEMENT_PROGRESS_RESULT "+JSON.toJson(result));}
  System.exit(exit);
 }
}
