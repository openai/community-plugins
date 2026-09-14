/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.*;
import org.openpnp.machine.reference.*;
import org.openpnp.model.*;

/** Actual native observers plus forced local records. Explicit test overlay; not packaged Bridge qualification. */
public final class NativeVacuumJournalIntegrationTest {
    private static final Gson JSON=new GsonBuilder().serializeNulls().create();
    private static final String INSTANCE=UUID.randomUUID().toString(),MACHINE=UUID.randomUUID().toString();
    private static int checks;
    private static final List<Object> cases=new ArrayList<>();
    private static final class Durable implements AutoCloseable {
        final NativeVacuumJournal journal=new NativeVacuumJournal();
        final FileChannel channel;final Path path;
        final List<Map<String,Object>> records=new ArrayList<>();
        String failEvent;boolean afterForce,triggered,cleanupOnly;long sequence;
        Durable(Path path)throws Exception{this.path=path;channel=FileChannel.open(path,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);}
        void append(String type,Map<String,Object> p)throws Exception {
            Runnable commit=journal.prepare(type,p,INSTANCE);
            boolean inject=failEvent!=null&&Objects.equals(failEvent,p.get("native_event"))&&!triggered&&(!cleanupOnly||Boolean.FALSE.equals(((Map<?,?>)p.get("data")).get("enabled")));
            if(inject){triggered=true;if(!afterForce)throw new IOException("Injected before journal append");}
            Map<String,Object> record=m("sequence",++sequence,"type",type,"bridge_instance_id",INSTANCE,"payload",p);
            byte[] bytes=(JSON.toJson(record)+"\n").getBytes(StandardCharsets.UTF_8);ByteBuffer buf=ByteBuffer.wrap(bytes);while(buf.hasRemaining())channel.write(buf);channel.force(true);records.add(record);
            if(inject)throw new IOException("Injected after force before reducer publication");
            commit.run();
        }
        public void close()throws Exception{channel.close();}
    }
    private static Map<String,Object> context(NativeVacuumOperationsTest.Fixture f,String scope,String op,String request){return m("operation_id",op,"request_id",request,"machine_id",MACHINE,"bridge_instance_id",INSTANCE,"config_revision","cfg-1","nozzle_id",f.nozzle.getId(),"scope",scope,
        "job_context",scope.equals("manual")?null:m("job_id",UUID.nameUUIDFromBytes(op.getBytes(StandardCharsets.UTF_8)).toString(),"job_revision","a".repeat(64),"board_load_revision","load-1","material_setup_revision","material-1","lineage_id",UUID.nameUUIDFromBytes(request.getBytes(StandardCharsets.UTF_8)).toString(),"lineage_revision",1));}
    private static VacuumSensing.Observer observer(NativeVacuumOperationsTest.Fixture f,Durable d,Map<String,Object> context){return(event,nozzle,data)->{f.observer.onEvent(event,nozzle,data);d.journal.observe(event,nozzle,data,context,d::append);};}
    private static Map<String,Object> currentBinding(NativeVacuumOperationsTest.Fixture f)throws Exception{return NativeVacuumJournal.binding(MACHINE,INSTANCE,"cfg-1",f.nozzle.getId(),f.nozzle.getNozzleTip().getId(),f.nozzle.getVacuumSenseActuator().getId(),VacuumSensing.sourceProvenance(f.nozzle));}
    private static void yes(boolean b,String label){checks++;if(!b)throw new AssertionError(label);}
    @SuppressWarnings("unchecked")private static Map<String,Object> nozzle(NativeVacuumJournal j){return(Map<String,Object>)((List<?>)j.snapshot().get("nozzles")).get(0);}
    private static void blocked(NativeVacuumJournal j,String label)throws Exception{try{j.requireUnoccupied();throw new AssertionError("Empty admission allowed: "+label);}catch(NativeVacuumJournal.Fault expected){checks++;}}
    private static boolean causedBy(Throwable t,Class<?> type){for(int n=0;t!=null&&n<16;n++,t=t.getCause())if(type.isInstance(t))return true;return false;}

    public static void main(String[] args)throws Exception {
        int exit=0;try{
            if(args.length>0&&args[0].equals("recover")){recoverAll(Paths.get(args[1]));System.out.println("VACUUM_JOURNAL_FRESH_RECOVERY_PASS "+checks+" checks; no native model initialized");}
            else {
                Path out=Paths.get(args[0]);Files.createDirectories(out);
                direct(out);job(out,"success",1,1);job(out,"missed-pick-retry",2,1);job(out,"retained-after-place",1,0);job(out,"lost-before-place",1,0);
                failure(out,"invalid-read",false);failure(out,"outcome-write-failure",false);failure(out,"outcome-force-publication-failure",true);failure(out,"cleanup-intent-failure",false);
                Map<String,Object> report=m("passed",true,"assertions",checks,"cases",cases,"simulation_only",true,"physical_qualification",false,"packaged_bridge_qualified",false,
                    "scope","actual native controlled-source observer + forced journal + action ledger; exact current class_sources below, optional explicit test-overlay classes; logical fixture job context, no Bridge binding qualification",
                    "class_sources",m("Bridge",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString(),"VacuumSensing",VacuumSensing.class.getProtectionDomain().getCodeSource().getLocation().toString(),"ReferenceNozzle",ReferenceNozzle.class.getProtectionDomain().getCodeSource().getLocation().toString(),"Journal",NativeVacuumJournal.class.getProtectionDomain().getCodeSource().getLocation().toString()));
                Files.writeString(out.resolve("native-result.json"),JSON.toJson(report)+"\n");System.out.println("NATIVE_VACUUM_JOURNAL_INTEGRATION_PASS "+checks+" checks "+cases.size()+" cases");
            }
        }catch(Throwable failure){failure.printStackTrace();exit=1;}System.exit(exit);
    }
    private static void direct(Path out)throws Exception {
        try(NativeVacuumOperationsTest.Fixture f=new NativeVacuumOperationsTest.Fixture("journal-direct");Durable d=new Durable(out.resolve("direct.jsonl"))){
            Map<String,Object> c=context(f,"manual",UUID.randomUUID().toString(),UUID.randomUUID().toString());VacuumSensing.Observer observer=observer(f,d,c);
            Map<String,Object> result=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).measure(2,()->{},observer));
            yes(((Number)result.get("sample_count")).intValue()==2,"two actual samples");yes(d.records.size()==4,"four forced read records");yes("unobserved".equals(nozzle(d.journal).get("state")),"read does not assert part state");
            f.enableHome();f.raw.set("0");Map<String,Object> off=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},observer));
            yes(Boolean.TRUE.equals(off.get("native_verdict")),"actual off verdict");yes(d.records.size()==12,"read/check/pulse twelve records");d.journal.requireUnoccupied(f.nozzle.getId(),currentBinding(f));yes(Boolean.FALSE.equals(f.nozzle.getVacuumActuator().getLastActuationValue()),"normal native valve cleanup off");
            cases.add(m("case","direct","journal",d.path.getFileName().toString(),"forced_records",d.records.size(),"native_verdict",off.get("native_verdict"),"state",d.journal.snapshot()));
        }
    }
    private static void job(Path out,String scenario,int expectedFeeds,int expectedPlaced)throws Exception {
        try(NativeVacuumOperationsTest.Fixture f=new NativeVacuumOperationsTest.Fixture("journal-"+scenario);Durable d=new Durable(out.resolve(scenario+".jsonl"))){
            f.scenario=scenario;f.part.setPickRetryCount(scenario.equals("missed-pick-retry")?1:0);f.tray.setPickRetryCount(0);f.tray.setFeedRetryCount(0);f.enableHome();
            Job job=CanonicalJobImporter.load(f.config,NativeVacuumOperationsTest.canonical(f.part));String op=UUID.randomUUID().toString(),req=UUID.randomUUID().toString();
            Map<String,Object> c=context(f,"job",op,req);VacuumSensing.Observer observer=observer(f,d,c);AtomicReference<Throwable> failure=new AtomicReference<>();
            NativeActionLedger ledger=new NativeActionLedger((type,p)->{if(type.equals("native_action_intent"))d.journal.requireNoFault();d.append(type,p);},op,(String)((Map<?,?>)c.get("job_context")).get("job_id"),"cfg-1","test-logical-load",job);
            f.task(()->{try(VacuumSensing.Scope scope=VacuumSensing.observe(observer)){
                f.machine.getPnpJobProcessor().initialize(job);boolean more;int step=0;
                do {if(++step>500)throw new AssertionError("Native step bound");d.journal.requireNoFault();try(NativeActionLedger.StepScope actions=ledger.openStep(step)){more=f.machine.getPnpJobProcessor().next();actions.complete();}}while(more);
            }catch(Throwable t){failure.set(t);}return null;});
            long placed=job.getBoardLocations().stream().filter(b->job.retrievePlacedStatus(b,"R1")).count();
            if(failure.get()!=null)failure.get().printStackTrace();
            yes(f.tray.getFeedCount()==expectedFeeds,scenario+" exact feed count: "+f.tray.getFeedCount());yes(placed==expectedPlaced,scenario+" exact native completions: "+placed);
            if(expectedPlaced==1){yes(failure.get()==null,scenario+" returned successfully: "+failure.get());d.journal.requireUnoccupied(f.nozzle.getId(),currentBinding(f));}
            else {yes(failure.get()!=null,scenario+" failure visible");blocked(d.journal,scenario);}
            if(scenario.equals("missed-pick-retry"))yes(f.onAfterPick.get()==2,"one native configured retry preserved");
            if(scenario.equals("retained-after-place")){
                yes(f.nozzle.getPart()==null,"retained verdict survives null native Part");yes("retained".equals(nozzle(d.journal).get("state")),"sticky retained state");
                long actionCount=d.records.stream().filter(r->r.get("type").equals("native_action_intent")).count();
                f.task(()->{try(NativeActionLedger.StepScope actions=ledger.openAbortScope()){org.openpnp.util.Cycles.discardAlways(f.nozzle);actions.complete();throw new AssertionError("Retained cleanup admitted");}catch(NativeActionLedger.DurabilityFence expected){checks++;}return null;});
                yes(d.records.stream().filter(r->r.get("type").equals("native_action_intent")).count()==actionCount,"no new native cleanup action after retained verdict");
            }
            long complete=d.records.stream().filter(r->r.get("type").equals("native_placement_checkpoint")&&"native-placement-complete-hook".equals(((Map<?,?>)r.get("payload")).get("state"))).count();
            yes(complete==placed,"actual Complete hooks agree with native placed flags");
            cases.add(m("case",scenario,"journal",d.path.getFileName().toString(),"forced_records",d.records.size(),"native_feed_count",f.tray.getFeedCount(),"native_placed_count",placed,"complete_hooks",complete,"native_part_null",f.nozzle.getPart()==null,"failure_type",failure.get()==null?null:failure.get().getClass().getName(),"state",d.journal.snapshot(),"action_ledger",ledger.snapshot()));
        }
    }
    private static void failure(Path out,String scenario,boolean afterForce)throws Exception {
        try(NativeVacuumOperationsTest.Fixture f=new NativeVacuumOperationsTest.Fixture("journal-"+scenario);Durable d=new Durable(out.resolve(scenario+".jsonl"))){
            f.enableHome();f.raw.set(scenario.equals("invalid-read")?"NaN":"0");if(!scenario.equals("invalid-read")){d.failEvent=scenario.equals("cleanup-intent-failure")?"valve.before":"read.returned";d.cleanupOnly=scenario.equals("cleanup-intent-failure");d.afterForce=afterForce;}
            Map<String,Object> c=context(f,"manual",UUID.randomUUID().toString(),UUID.randomUUID().toString());VacuumSensing.Observer observer=observer(f,d,c);AtomicReference<Throwable> failure=new AtomicReference<>();
            f.task(()->{try{NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},observer);}catch(Throwable t){failure.set(t);}return null;});
            yes(failure.get()!=null&&causedBy(failure.get(),VacuumSensing.ObserverFailure.class),scenario+" nonretryable native observer fence");yes(Boolean.FALSE.equals(f.nozzle.getVacuumActuator().getLastActuationValue()),scenario+" mandatory valve finally off");blocked(d.journal,scenario);
            int before=f.events.size();f.task(()->{try{d.journal.requireNoFault();throw new AssertionError("Fault reset");}catch(NativeVacuumJournal.Fault expected){checks++;}return null;});yes(f.events.size()==before,"no extra native read or pulse on retry admission");
            yes(((List<?>)d.journal.snapshot().get("pending")).size()>=1,"parent check remains pending after failure");
            cases.add(m("case",scenario,"journal",d.path.getFileName().toString(),"forced_records",d.records.size(),"failure_type",failure.get().getClass().getName(),"injection_after_force",afterForce,"native_valve_off",true,"state",d.journal.snapshot()));
        }
    }
    private static void recoverAll(Path out)throws Exception {
        List<Path> paths;try(java.util.stream.Stream<Path> stream=Files.list(out)){paths=stream.filter(p->p.getFileName().toString().endsWith(".jsonl")).sorted().collect(java.util.stream.Collectors.toList());}
        yes(paths.size()==9,"all nine closed journals present");
        for(Path path:paths){byte[] before=Files.readAllBytes(path);NativeVacuumJournal j=new NativeVacuumJournal();long expected=0;String machine=null;
            for(String line:Files.readAllLines(path)){Map<String,Object> r=NativeJournalJson.parseObject(line);yes(NativeJournalJson.integer(r.get("sequence"),1,9007199254740991L)==++expected,"contiguous records");String type=(String)r.get("type");if(NativeVacuumJournal.matches(type)){NativeVacuumJournal.validateRawRecord(line);Map<String,Object> p=(Map<String,Object>)r.get("payload");j.recover(type,p,(String)r.get("bridge_instance_id"));machine=(String)((Map<?,?>)p.get("context")).get("machine_id");}}
            j.verifyMachineIdentity(machine);blocked(j,"fresh JVM historical "+path.getFileName());yes(Boolean.FALSE.equals(j.snapshot().get("execution_authority_restored")),"no restored authority");yes(Arrays.equals(before,Files.readAllBytes(path)),"journal bytes unchanged");
        }
    }
    private static Map<String,Object> m(Object...v){Map<String,Object> p=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)p.put((String)v[i],v[i+1]);return p;}
}
