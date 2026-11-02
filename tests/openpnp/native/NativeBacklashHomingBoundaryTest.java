/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeMappedAxisBridgeTest.*;
import com.google.gson.*;
import java.beans.PropertyChangeListener;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/** Actual native acceptableTolerance setter event and public Bridge lifecycle. The listener's
 * setHomed(true) is an explicitly synthetic observation mutation, never a native home operation.
 * Existing helper reflection only seeds/reads validation state; no effect or outcome is injected.
 * Adapted from the retained S81 counterexample to the PR's version1 typed restore. */
public final class NativeBacklashHomingBoundaryTest {
    static int checks;
    static final List<Object> cases=new ArrayList<>();
    static void verify(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    static Map<String,Object> observedTerminal(String method,JsonObject request)throws Exception {
        Map<String,Object> accepted=call(method,request);String id=(String)accepted.get("operation_id");
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(30);
        while(System.nanoTime()<deadline){
            Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",id));
            if(List.of("succeeded","failed","outcome_unknown","cancelled").contains(op.get("state"))){idle();return op;}
            Thread.sleep(5);
        }
        throw new AssertionError("Native operation did not terminate");
    }
    static JsonObject backlash(double tolerance){return obj("type",NativeAxisBacklashSettings.TYPE,"axis_id",other.getId(),"method","DirectionalCompensation","offset_mm",0.2,"speed_factor",0.5,"sneak_up_mm",0.3,"acceptable_tolerance_mm",tolerance);}
    static void scenario(String kind,boolean interfere)throws Exception {
        boolean restore=kind.equals("restore");
        task(()->{other.setBacklashCompensationMethod(org.openpnp.machine.reference.axis.ReferenceControllerAxis.BacklashCompensationMethod.DirectionalCompensation);other.setBacklashOffset(mm(0.2));other.setBacklashSpeedFactor(0.5);other.setSneakUpOffset(mm(0.3));other.setAcceptableTolerance(mm(0.02));config.save();return null;});
        String artifact=null;byte[] artifactBefore=null;JsonObject typed=null;
        if(restore){
            artifact=(String)result(terminal("openpnp_backup_configuration",mutation(),"succeeded")).get("artifact_id");
            Map<String,Object> data=call("openpnp_get_artifact",obj("artifact_id",artifact));
            artifactBefore=Base64.getDecoder().decode((String)data.get("base64"));
            typed=new JsonParser().parse(new String(artifactBefore,StandardCharsets.UTF_8)).getAsJsonObject().getAsJsonObject("typed_snapshot");
            verify(typed.get("version").getAsInt()==1,"Actual owned backup uses version1");
            task(()->{other.setAcceptableTolerance(mm(0.03));return null;});
            final JsonObject captured=typed;
            NativeConfigurationSnapshots.Restore preview=task(()->NativeConfigurationSnapshots.prepareRestore(config,NativeConfigurationSnapshots.decode(captured)));
            verify(preview.invalidatesAxisDependencies(),"Restore contains an actual backlash change");
            for(JsonElement row:captured.getAsJsonArray("typed_changes"))
                verify(!NativeMappedAxisSettings.TYPE.equals(row.getAsJsonObject().get("type").getAsString()),"Version1 restore must not apply mapped geometry");
        }
        NativeBoardLoads loads=seedDependencies();
        Map<String,Boolean> history=new TreeMap<>(job.getPlacedStatusSnapshot());String loadRevision=loads.revision();Map<String,Integer> oldCounts=counts();
        String oldRevision=revision();Map<String,Object> proposal=restore?null:plan(backlash(0.04));
        AtomicInteger events=new AtomicInteger(),preconditions=new AtomicInteger();double desired=restore?0.02:0.04;
        PropertyChangeListener listener=e->{
            events.incrementAndGet();
            try{
                verify(machine.isTask(Thread.currentThread()),"Actual property event runs on native executor");
                verify(!machine.isHomed()&&!machine.isEnabled(),"Homing and enable observations are false before final backlash setter event");
                verify(!oldRevision.equals(revision())&&field(bridge,"validatedBoardLoadRevision")==null,"Revision and validation invalidated before actual setter");
                verify("prepared".equals(field(bridge,"jobState")),"Job validation was revoked before native callback");
                verify(Math.abs(other.getAcceptableTolerance().getValue()-desired)<1e-9,"Actual native setter changed tolerance before event");
                String journal=Files.readString(root.resolve("journal/operations.jsonl"));
                verify(journal.contains("native_effect_intent")&&journal.contains("configuration_change_started"),"Actual forced intent and configuration records precede setter");
                preconditions.incrementAndGet();
                if(interfere)machine.setHomed(true);
            }catch(Exception failure){throw new RuntimeException(failure);}
        };
        other.addPropertyChangeListener("acceptableTolerance",listener);
        String method=restore?"openpnp_restore_configuration":"openpnp_apply_configuration";
        JsonObject request=restore?mutation("artifact_id",artifact,"expected_config_revision",oldRevision):mutation("plan_id",proposal.get("plan_id"),"expected_config_revision",oldRevision);
        Map<String,Object> op;
        try{op=observedTerminal(method,request);}finally{other.removePropertyChangeListener("acceptableTolerance",listener);}
        Map<String,Object> observed=new LinkedHashMap<>();
        observed.put("kind",kind);observed.put("interference",interfere);observed.put("operation",op);observed.put("acceptable_tolerance_events",events.get());observed.put("precondition_callbacks",preconditions.get());observed.put("actual_tolerance_mm",other.getAcceptableTolerance().getValue());observed.put("machine_homed_after",machine.isHomed());observed.put("machine_enabled_after",machine.isEnabled());observed.put("configuration_fault",field(bridge,"configurationFault"));observed.put("synthetic_true_homed_events",homed);observed.put("enable_events",enabled);observed.put("head_activity_events",activity);observed.put("counts_preserved",oldCounts.equals(counts()));observed.put("native_history_preserved",history.equals(job.getPlacedStatusSnapshot()));observed.put("load_revision_preserved",loadRevision.equals(loads.revision()));observed.put("mapped_source_unchanged",mapped.getInputAxis()==raw);observed.put("mapped_axis_changes",restore?0:null);observed.put("native_home_calls",0);
        Files.writeString(root.resolve("observed-before-assertions.json"),G.toJson(observed)+"\n");cases.add(observed);
        verify(events.get()==1&&preconditions.get()==1,"Exactly one actual last-setter callback");
        verify((interfere?"outcome_unknown":"succeeded").equals(op.get("state")),"Unexpected terminal state after returning listener: "+op);
        verify(!machine.isHomed()&&!machine.isEnabled(),"No homed or enabled authority survives configuration");
        verify(Boolean.TRUE.equals(field(bridge,"configurationFault"))==interfere,"Only interrupted authority is fenced");
        verify(Boolean.TRUE.equals(op.get("native_effect_pending"))==interfere,"Unknown native outcome is retained; normal outcome is completed");
        verify(homed==(interfere?1:0)&&enabled==0&&activity==0,"Synthetic homed event only; no enable or motion activity");
        verify(oldCounts.equals(counts())&&history.equals(job.getPlacedStatusSnapshot())&&loadRevision.equals(loads.revision()),"Consumption, full native job history and load identities survive");
        verify(mapped.getInputAxis()==raw,"Existing mapped source remains untouched");
        if(restore)verify(Arrays.equals(artifactBefore,Base64.getDecoder().decode((String)call("openpnp_get_artifact",obj("artifact_id",artifact)).get("base64"))),"Original owned backup stays immutable");
        if(interfere){
            String retained=G.toJson(call("openpnp_get_operation",obj("operation_id",op.get("operation_id"))));
            expect("CONFIGURATION_FAULT",()->call(method,request));
            verify(events.get()==1&&retained.equals(G.toJson(call("openpnp_get_operation",obj("operation_id",op.get("operation_id"))))),"Refusal does not replay setter or rewrite unknown operation");
            verify(Boolean.TRUE.equals(call("openpnp_get_request_status",obj("request_id",request.get("request_id").getAsString())).get("found")),"Original failed request remains readable");
        }
    }
    public static void main(String[] args)throws Exception {
        Path base=args.length==1?Files.createTempDirectory("openpnp-backlash-homing-"):Paths.get(args[0]);Path samples=Paths.get(args[args.length-1]);List<String> failures=new ArrayList<>();
        for(String kind:List.of("direct","restore"))for(boolean interfere:List.of(false,true)){
            String label=kind+(interfere?"-returning-listener":"-control");root=base.resolve(label);enabled=homed=activity=0;
            try{setup(samples);scenario(kind,interfere);}catch(Throwable failure){failures.add(label+": "+failure);failure.printStackTrace();}
            finally{
                try{if(bridge!=null)bridge.close();}catch(Throwable cleanup){failures.add(label+" Bridge cleanup: "+cleanup);cleanup.printStackTrace();}
                try{if(machine!=null)machine.close();}catch(Throwable cleanup){failures.add(label+" machine cleanup: "+cleanup);cleanup.printStackTrace();}
                try{NativeFixtureExecutorCleanup.closeIdleExecutor(machine);}
                catch(Throwable cleanup){failures.add(label+" native executor cleanup: "+cleanup);cleanup.printStackTrace();}
                bridge=null;machine=null;
            }
        }
        Map<String,Object> summary=new LinkedHashMap<>();summary.put("passed",failures.isEmpty());summary.put("assertions",checks);summary.put("helper_assertions",assertions);summary.put("cases",cases);summary.put("failures",failures);summary.put("native_home_calls",0);summary.put("synthetic_observation_injection",true);summary.put("physical_qualification",false);summary.put("evidence_directory",base.toString());
        Files.writeString(base.resolve("result.json"),G.toJson(summary)+"\n");System.out.println("BACKLASH_HOMING_BOUNDARIES "+G.toJson(summary));
        if(!failures.isEmpty())throw new AssertionError("Backlash homing boundary failures: "+failures);
    }
}
