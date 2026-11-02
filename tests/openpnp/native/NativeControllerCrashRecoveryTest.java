/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import static org.openpnp.codex.NativeControllerTestProcesses.*;

/** Actual own-child process death and two fresh native Bridge recoveries at three fixed boundaries. */
public final class NativeControllerCrashRecoveryTest {
    private static int checks,producerAssertions,recoveryAssertions;
    private static void check(boolean value,String message){checks++;require(value,message);}
    private static JsonObject copy(JsonObject value){return JSON.fromJson(value.toString(),JsonObject.class);}
    private static JsonObject runCase(String mode,Path root,Path samples)throws Exception{
        Path caseRoot=Files.createDirectory(root.resolve(mode)),producer=caseRoot.resolve("producer");
        JsonObject producerProcess=run(NativeControllerCrashProducerTest.class,producer,74,mode,producer.toString(),samples.toString());
        check(!Files.exists(producer.resolve("unexpected-shutdown-hook.txt")),"Runtime.halt must bypass ordinary shutdown hooks");
        JsonObject proof=read(producer.resolve("crash-proof.json")),request=read(producer.resolve("admitted-request.json"));producerAssertions+=proof.get("assertions").getAsInt();
        Path journal=producer.resolve("journal/operations.jsonl"),identity=producer.resolve("journal/machine-id");byte[] original=Files.readAllBytes(journal);String originalSha=sha(original),identitySha=sha(identity);JsonArray all=events(journal),outcomes=new JsonArray();JsonObject admission=null,operation=null,pending=null;boolean retired=false;
        for(JsonElement raw:all){JsonObject event=raw.getAsJsonObject(),payload=event.getAsJsonObject("payload");switch(event.get("type").getAsString()){
            case "controller_diagnostic_admission":check(admission==null,"Only one actual controller admission");admission=payload;break;
            case "controller_diagnostic_step_intent":pending=payload;break;
            case "controller_diagnostic_step_outcome":outcomes.add(payload);pending=null;break;
            case "controller_diagnostic_retired":retired=true;break;
            case "operation":if(payload.get("method").getAsString().equals("openpnp_run_controller_diagnostic"))operation=payload;break;
            default:break;
        }}
        check(admission!=null&&operation!=null,"Original operation and controller binding must be durable");
        check(originalSha.equals(proof.get("journal_sha256").getAsString())&&original.length==proof.get("journal_bytes").getAsInt(),"Halted journal equals the exact forced crash proof");
        check(all.get(all.size()-1).equals(proof.get("last_real_forced_event")),"Last complete crashed record matches observed actual force");
        check(operation.get("operation_id").equals(proof.get("original_operation_id"))&&operation.get("request_id").equals(proof.get("original_request_id"))&&operation.get("request_id").equals(request.get("request_id")),"Original operation and request identities remain bound");
        check(admission.get("controller_instance_id").equals(proof.get("original_controller_instance_id"))&&admission.get("controller_instance_id").equals(request.get("controller_instance_id")),"Original controller identity remains bound");
        boolean interrupted=!mode.equals("succeeded-force");String expectedState=interrupted?"outcome_unknown":"succeeded";
        check(operation.get("state").getAsString().equals(interrupted?"running":"succeeded"),"Crashed source has the selected incomplete or complete disposition");
        check(outcomes.size()==(mode.equals("connect-intent-force")?1:mode.equals("identify-before-ack")?2:4),"Exact durable native outcome count at crash");
        JsonObject wire=proof.getAsJsonObject("wire");check(wire.get("accepted_connections").getAsInt()==(mode.equals("connect-intent-force")?0:1)&&wire.getAsJsonArray("commands").size()==(mode.equals("connect-intent-force")?0:3)&&wire.getAsJsonArray("acks").size()==(mode.equals("connect-intent-force")?0:mode.equals("identify-before-ack")?2:3),"Exact actual native commands and ACK count at crash");
        Path state=Files.createDirectories(caseRoot.resolve("recovery-state/journal")).getParent();Files.copy(journal,state.resolve("journal/operations.jsonl"));Files.copy(identity,state.resolve("journal/machine-id"));
        JsonObject expected=object("source_journal_sha256",originalSha,"source_journal_bytes",original.length,"machine_id",Files.readString(identity).trim(),"terminal_operation",operation,"admission",admission,"outcomes",outcomes,"events",all,"expected_state",expectedState,"interrupted",interrupted,"retired",retired);if(pending!=null)expected.add("pending",pending);save(caseRoot.resolve("expectation.json"),expected);
        JsonArray recoveryRuns=new JsonArray();JsonObject first=null,second=null;
        for(int iteration=1;iteration<=2;iteration++){
            Path output=Files.createDirectory(caseRoot.resolve("recovery"+iteration)),current=state.resolve("journal/operations.jsonl");JsonObject iterationExpected=copy(expected);iterationExpected.addProperty("starting_journal_sha256",sha(current));iterationExpected.add("events",events(current));iterationExpected.addProperty("expected_recovery_events",interrupted&&iteration==1?1:0);save(output.resolve("expectation.json"),iterationExpected);
            JsonObject process=run(NativeControllerRecoveryTest.class,output,0,state.toString(),samples.toString(),output.resolve("expectation.json").toString(),output.toString()),observation=read(output.resolve("observation.json"));
            check(observation.get("passed").getAsBoolean(),"Fresh native recovery fixture must pass");recoveryAssertions+=observation.get("assertions").getAsInt();recoveryRuns.add(object("process",process,"observation",observation));if(iteration==1)first=observation;else second=observation;
        }
        check(!first.get("bridge_instance_id").equals(second.get("bridge_instance_id")),"Distinct recovery Bridge instances");
        check(!first.get("new_controller_instance_id").equals(second.get("new_controller_instance_id")),"Distinct fresh spent controller generations");
        for(JsonObject observed:List.of(first,second)){
            check(observed.get("original_operation_id").equals(proof.get("original_operation_id"))&&observed.get("original_request_id").equals(proof.get("original_request_id"))&&observed.get("original_controller_instance_id").equals(proof.get("original_controller_instance_id")),"Fresh recovery retains original identities");
            check(observed.get("state").getAsString().equals(expectedState),"Fresh recovery has exact conservative or successful disposition");
            check(observed.get("controller_connections").getAsInt()==0&&observed.get("controller_bytes").getAsInt()==0,"Fresh recovery never connects or writes controller bytes");
            check(observed.get("producer_prefix_preserved").getAsBoolean(),"Entire halted producer prefix preserved");
            check(observed.get("bridge_artifact_sha256").equals(proof.get("bridge_artifact_sha256")),"Producer and recoveries use the same exact Bridge artifact");
        }
        check(first.get("journal_after_sha256").equals(second.get("journal_after_sha256")),"Second fresh recovery appends nothing");
        check(first.get("recovery_events_appended").getAsInt()==(interrupted?1:0)&&second.get("recovery_events_appended").getAsInt()==0,"Only first incomplete recovery adds its one conservative disposition");
        check(sha(journal).equals(originalSha)&&sha(identity).equals(identitySha),"Original producer journal and identity remain unchanged");
        return object("mode",mode,"passed",true,"producer_process",producerProcess,"crash_proof",proof,"recoveries",recoveryRuns,"original_journal_sha256",originalSha,"physical_qualification",false);
    }
    public static void main(String[] args)throws Exception{
        if(args.length!=1)throw new IllegalArgumentException("Expected pinned sample root");Path samples=Paths.get(args[0]).toRealPath(),root=Files.createTempDirectory("openpnp-controller-crash-").toRealPath();JsonArray cases=new JsonArray();
        for(String mode:List.of("connect-intent-force","identify-before-ack","succeeded-force"))cases.add(runCase(mode,root,samples));
        JsonObject report=object("passed",true,"suite","NativeControllerCrashRecoveryTest","producer_assertions",producerAssertions,"recovery_assertions",recoveryAssertions,"cross_process_checks",checks,"assertions",producerAssertions+recoveryAssertions+checks,"halted_producers",3,"fresh_recovery_JVMs",6,"cases",cases,"physical_qualification",false,"power_loss_qualification",false);save(root.resolve("report.json"),report);System.out.println("OPENPNP_CONTROLLER_CRASH_RECOVERY_RESULT "+report);
    }
}
