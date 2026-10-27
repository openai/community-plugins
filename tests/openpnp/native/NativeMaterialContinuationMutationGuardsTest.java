/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationTest.*;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.Part;

/** Actual native component continuation permits and records. Initial fault and wrapper
 * authority are explicit synthetic fixture inputs. Reflection is limited to negative
 * witness/capacity/nozzle corruption; it never manufactures an execution permit. */
public final class NativeMaterialContinuationMutationGuardsTest {
    static int checks, refusals, mutations;
    static final List<Map<String,Object>> evidence = new ArrayList<>();
    interface Checked { void run() throws Exception; }
    static void verify(boolean result, String why) { checks++; if (!result) throw new AssertionError(why); }
    static void same(Object a, Object b, String why) throws Exception { verify(NativeFaultedJobReplacement.same(a,b),why); }
    static void reject(String why, String code, Checked action) throws Exception {
        try { action.run(); throw new AssertionError("Accepted " + why); }
        catch (Bridge.Fault expected) {
            verify(code == null || code.equals(expected.code), why + " returned " + expected.code + " instead of " + code);
            refusals++; evidence.add(m("refusal",why,"code",expected.code));
        }
        catch (java.io.IOException expected) {
            verify(code != null && code.startsWith("IO:") && code.substring(3).equals(expected.getMessage()), why+" returned unexpected coordinator refusal: "+expected);
            refusals++;evidence.add(m("refusal",why,"type",expected.getClass().getName(),"message",expected.getMessage()));
        }
    }
    @SuppressWarnings("unchecked") static Map<String,Object> internal(Object instance, String name) throws Exception {
        Field field=instance.getClass().getDeclaredField(name);field.setAccessible(true);return (Map<String,Object>)field.get(instance);
    }
    static List<Map<String,Object>> materialRows(Fixture f) throws Exception {
        List<Map<String,Object>> rows=new ArrayList<>();for(Object value:(List<?>)o(f.e.replacement.progress(f.attempt).get("material")).get("rows"))rows.add(o(value));return rows;
    }
    static Map<String,Object> selectedRow(Fixture f, String prefix) throws Exception {
        for(Map<String,Object> row:materialRows(f))if((prefix.equals("pending-material")?"pending":"untouched").equals(row.get("phase")))return row;
        throw new AssertionError("Missing native fixture target");
    }
    static ReferenceTrayFeeder feeder(Map<String,Object> row) { return (ReferenceTrayFeeder)config.getMachine().getFeeder((String)row.get("feeder_id")); }
    static void terminalize(Fresh fresh, NativeFaultedJobReplacement.ContinuationPermit permit) throws Exception {
        Map<String,Object> terminal=freshOperation(fresh);terminal.put("state","outcome_unknown");terminal.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",false,"physical_outcome_verified",false));
        fresh.fixture.e.append("operation",terminal);fresh.fixture.e.terminalWrappers.add(fresh.operation);fresh.revoke();permit.close();
    }
    static void replay(Env target,List<Map<String,Object>> events)throws Exception {
        for(Map<String,Object> event:events){String type=(String)event.get("type");Map<String,Object> payload=o(event.get("payload"));Runnable common=target.replacement.prepareContinuationObservation(type,payload);target.material.recoverEvent(type,payload);target.boards.recoverEvent(type,payload);target.boards.observeNativeEvent(type,payload);target.lineage.observe(type,payload);target.replacement.observe(type,payload);if(type.startsWith("faulted_job_replacement_"))target.replacement.recover(type,payload);common.run();}
        target.material.finishRecovery();target.boards.finishRecovery();
    }
    static void nativeGuard(String prefix,String kind,String expectedCode)throws Exception {
        try(Fixture f=new Fixture("material-guard-"+prefix+"-"+kind,prefix)){
            f.closeOriginal("outcome_unknown");Map<String,Object> row=selectedRow(f,prefix);String old=(String)row.get("old_load_id");Fresh fresh=f.fresh(f.capture(),"continue-faulted-job-replacement");var permit=fresh.begin();
            // UUID-sorted original load union may select either tray. A package used by
            // the retained candidate is rejected by its coordinator before child checks.
            String code=kind.equals("package-clone")?(feeder(row).getPart()==NativeReplacementContinuationTest.tray.getPart()?"IO:Native library Part/Package identity differs":"MATERIAL_MODEL_CHANGED"):expectedCode;
            Map<String,Object> before=f.e.material.captureReplacementState();Map<String,Integer> counts=counters();Map<String,String> files=configurationFiles();long bytes=Files.size(f.e.file);int events=f.e.events.size();
            task(()->{
                permit.check();ReferenceTrayFeeder tray=feeder(row);Checked undo=()->{};
                switch(kind){
                    case "geometry": {var previous=tray.getOffsets();tray.setOffsets(previous.derive(previous.getX()+1,null,null,null));undo=()->tray.setOffsets(previous);break;}
                    case "part-clone": {Part previous=tray.getPart(),clone=new Part(previous.getId());clone.setPackage(previous.getPackage());tray.setPart(clone);undo=()->tray.setPart(previous);break;}
                    case "package-clone": {Part part=tray.getPart();var previous=part.getPackage();part.setPackage(new org.openpnp.model.Package(previous.getId()));undo=()->part.setPackage(previous);break;}
                    case "counter-out-of-range": {int previous=tray.getFeedCount();tray.setFeedCount(tray.getTrayCountX()*tray.getTrayCountY()+1);undo=()->tray.setFeedCount(previous);break;}
                    case "missing-pending-witness": {String parent=(String)o(o(row.get("parent_intent")).get("payload")).get("receipt_id");Map<String,Object> witnesses=internal(f.e.material,"pendingWitnesses");Object previous=witnesses.remove(parent);verify(previous!=null,"Actual pending native intent retained its process-local witness");undo=()->witnesses.put(parent,previous);break;}
                    case "retained-load-limit": {Map<String,Object> loads=internal(f.e.material,"loads");List<String> added=new ArrayList<>();while(loads.size()<NativeMaterialLoads.MAX_LOADS){String id=id();Map<String,Object> copy=NativeFaultedJobReplacement.mutable(o(row.get("old_load")));copy.put("load_id",id);loads.put(id,copy);added.add(id);}undo=()->added.forEach(loads::remove);break;}
                    case "enabled": {config.getMachine().setEnabled(true);undo=()->config.getMachine().setEnabled(false);break;}
                    case "occupied": {var nozzle=config.getMachine().getDefaultHead().getDefaultNozzle();Field field=org.openpnp.spi.base.AbstractNozzle.class.getDeclaredField("part");field.setAccessible(true);Object previous=field.get(nozzle);field.set(nozzle,tray.getPart());undo=()->field.set(nozzle,previous);break;}
                    default: throw new AssertionError(kind);
                }
                try{reject(prefix+" native "+kind,code,()->f.e.material.continueReplacement(permit,old));}finally{undo.run();}
                return null;
            });
            same(before,f.e.material.captureReplacementState(),"Native guard preserves all exact material load/feed/parent facts");
            verify(bytes==Files.size(f.e.file)&&events==f.e.events.size(),"Native guard refuses before new forced intent");verify(counts.equals(counters())&&files.equals(configurationFiles()),"Native refusal leaves counters and persisted config unchanged after test perturbation restores");
            verify(!Boolean.TRUE.equals(f.e.material.snapshot().get("continuation_mutation_publication_uncertain")),"Known native guard refusal is not journal publication damage");terminalize(fresh,permit);
        }
    }
    static void altered(Env reduced,String type,Map<String,Object> original,String name,Consumer<Map<String,Object>> edit)throws Exception {
        Map<String,Object> bad=NativeFaultedJobReplacement.mutable(original);edit.accept(bad);var before=reduced.material.captureReplacementState();long bytes=Files.size(reduced.file);
        reject(type+" strict "+name,null,()->reduced.material.prepareEvent(type,bad));same(before,reduced.material.captureReplacementState(),"Rejected child record cannot change reducer facts");verify(bytes==Files.size(reduced.file),"Rejected child record appends no bytes");
    }
    static void strictRecords(Fixture f,String prefix)throws Exception {
        for(String suffix:List.of("intent","outcome")){
            String type="material_continuation_"+suffix;int index=-1;for(int i=0;i<f.e.events.size();i++)if(type.equals(f.e.events.get(i).get("type"))){index=i;break;}verify(index>=0,"Actual native continuation forced "+type);Map<String,Object> record=o(f.e.events.get(index).get("payload"));
            try(Env reduced=new Env("material-schema-"+prefix+"-"+suffix,true)){
                replay(reduced,f.e.events.subList(0,index));var counts=counters();var files=configurationFiles();var before=reduced.material.captureReplacementState();
                verify(reduced.material.prepareEvent(type,record)!=null,"Exact actually forced child record validates against its reducer prefix");same(before,reduced.material.captureReplacementState(),"Valid pre-force child validation is pure");
                altered(reduced,type,record,"extra-field",p->p.put("auto_resume",true));
                altered(reduced,type,record,"missing-original",p->p.remove("old_load_id"));
                altered(reduced,type,record,"receipt-step-mismatch",p->p.put("step_id",id()));
                altered(reduced,type,record,"invalid-hash",p->p.put("continuation_capture_sha256","invalid"));
                altered(reduced,type,record,"old-operation-reused",p->p.put("recovery_operation_id",p.get("original_recovery_operation_id")));
                altered(reduced,type,record,"restored-authority",p->p.put("execution_authority_restored",true));
                altered(reduced,type,record,"claims-physical-verification",p->p.put("physical_inventory_verified",true));
                altered(reduced,type,record,"changed-old-load",p->{try{o(p.get("old_load")).put("current_index",99);}catch(Exception e){throw new RuntimeException(e);}});
                altered(reduced,type,record,"changed-old-feed-union",p->p.put("pending_feeds",m("foreign",m())));
                altered(reduced,type,record,"changed-previous-load",p->{try{o(p.get("previous_load")).put("current_index",99);}catch(Exception e){throw new RuntimeException(e);}});
                altered(reduced,type,record,"new-load-extra-field",p->{try{o(p.get("new_load")).put("auto_bound",true);}catch(Exception e){throw new RuntimeException(e);}});
                altered(reduced,type,record,"nonzero-new-index",p->{try{o(p.get("new_load")).put("current_index",1);}catch(Exception e){throw new RuntimeException(e);}});
                altered(reduced,type,record,"old-new-id-collision",p->{try{o(p.get("new_load")).put("load_id",p.get("old_load_id"));}catch(Exception e){throw new RuntimeException(e);}});
                altered(reduced,type,record,"changed-revision",p->p.put("revision",((Number)p.get("revision")).intValue()+1));
                altered(reduced,type,record,"wrong-mode",p->p.put("mode","adopt-zero-counter"));
                altered(reduced,type,record,"foreign-parent",p->p.put("parent_receipt_id",id()));
                if(prefix.equals("pending-material")){
                    altered(reduced,type,record,"missing-parent-hash",p->p.remove("parent_sha256"));
                    altered(reduced,type,record,"wrong-parent-hash",p->p.put("parent_sha256","f".repeat(64)));
                    altered(reduced,type,record,"wrong-parent-type",p->p.put("parent_type","material_continuation_intent"));
                }
                verify(counts.equals(counters())&&files.equals(configurationFiles())&&Files.size(reduced.file)==0,"Strict record replay checks execute no counter setter/configuration save/journal append");
            }
        }
    }
    static void positiveAndSchema(String prefix)throws Exception {
        try(Fixture f=new Fixture("material-mutation-schema-"+prefix,prefix)){
            f.closeOriginal("outcome_unknown");Map<String,Object> before=selectedRow(f,prefix);String old=(String)before.get("old_load_id");Fresh fresh=f.fresh(f.capture(),"continue-faulted-job-replacement");var permit=fresh.begin();
            List<Map<String,Object>> original=List.copyOf(f.e.events);Map<String,Object> receipt=task(()->f.e.material.continueReplacement(permit,old));mutations++;Map<String,Object> after=materialRows(f).stream().filter(r->old.equals(r.get("old_load_id"))).findFirst().orElseThrow();
            verify("completed".equals(after.get("effective_phase")),"Actual native reset/save and forced outcome completes effective stage");same(before.get("phase"),after.get("phase"),"Original phase is not rewritten");same(original,f.e.events.subList(0,original.size()),"Every earlier forced record remains exact");same(receipt,f.e.material.continuationMutationReceipt((String)receipt.get("receipt_id")),"Read accessor returns exact forced mutation receipt");
            verify(feeder(after).getFeedCount()==0,"Actual native continuation produces zero index");verify(Boolean.FALSE.equals(receipt.get("execution_authority_restored")),"Material completion alone grants no execution readiness");terminalize(fresh,permit);strictRecords(f,prefix);
            try(Env reduced=new Env("material-full-replay-"+prefix,true)){
                var counts=counters();var files=configurationFiles();replay(reduced,f.e.events);same(receipt,reduced.material.continuationMutationReceipt((String)receipt.get("receipt_id")),"Replay preserves exact completed child receipt");verify(internal(reduced.material,"pendingWitnesses").isEmpty()&&internal(reduced.material,"bindings").isEmpty(),"Replay restores neither live binding nor unknown-candidate native witness");
                verify(counts.equals(counters())&&files.equals(configurationFiles())&&Files.size(reduced.file)==0,"Receipt replay performs no native work or journal append");
            }
            Files.writeString(root.resolve(prefix+"-material-progress.json"),JSON.toJson(f.e.replacement.progress(f.attempt))+"\n");
        }
    }
    public static void main(String[]args)throws Exception {
        int status=0;try{initialize(Path.of(args[1]));for(String prefix:List.of("lineage","pending-material")){
            for(String kind:List.of("geometry","part-clone"))nativeGuard(prefix,kind,"MATERIAL_MODEL_CHANGED");
            nativeGuard(prefix,"package-clone",null);
            nativeGuard(prefix,"counter-out-of-range","MATERIAL_PROFILE_UNSUPPORTED");nativeGuard(prefix,"retained-load-limit","MATERIAL_CAPACITY");nativeGuard(prefix,"enabled","MACHINE_ENABLED");nativeGuard(prefix,"occupied","NOZZLE_OCCUPIED");
            if(prefix.equals("pending-material"))nativeGuard(prefix,"missing-pending-witness","MATERIAL_MODEL_CHANGED");positiveAndSchema(prefix);
        }Files.writeString(root.resolve("proof.json"),JSON.toJson(m("passed",true,"assertions",checks,"refusals",refusals,"actual_native_material_mutations",mutations,"native_placements",0,"initial_fault_and_wrapper_authority","explicit synthetic fixture","negative_reflection_scope","missing pending witness, bounded retained-load capacity, occupied nozzle model only","scope","Real native simulator component guards and exact journal child schema/replay, no Bridge/UI/packaged MCP or physical qualification","evidence",evidence))+"\n");System.out.println("NATIVE_MATERIAL_CONTINUATION_MUTATION_GUARDS_PASS "+checks+" assertions "+refusals+" refusals "+mutations+" actual mutations");}
        catch(Throwable failure){failure.printStackTrace();status=1;}finally{if(config!=null)config.getMachine().close();}System.exit(status);
    }
}
