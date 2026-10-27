/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import static org.openpnp.codex.NativeFaultedSensingReplacementContinuationBridgeTest.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.driver.NullDriver;

/** Corrupts only a disposable copy of a real crash journal. No original file or native authority is changed. */
public final class NativeReplacementIntentReplayRejectionTest {
    public static void main(String[] args)throws Exception {
        if(args.length!=3)throw new IllegalArgumentException("Expected samples, disposable copied crash state, negative case");state=Path.of(args[1]).toAbsolutePath();String mode=args[2];phase="negative";int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try {
            List<Map<String,Object>> rows=events();Map<String,Object> intent=null,definition=null;
            for(Map<String,Object> e:rows){if("faulted_job_replacement_intent".equals(e.get("type")))intent=map(e.get("payload"));if("faulted_job_replacement_definition".equals(e.get("type")))definition=map(e.get("payload"));}
            check(intent!=null&&((Number)intent.get("schema_version")).intValue()==2,"Negative begins from an actual forced schema2 replacement intent");
            NativeReplacementDocuments.validateReconstruction(map(intent.get("reconstruction_bundle")));Map<String,Object> bundle=map(intent.get("reconstruction_bundle"));
            String zero="0".repeat(64);
            switch(mode){
                case "missing-bundle":intent.remove("reconstruction_bundle");break;
                case "wrong-bundle-digest":intent.put("reconstruction_sha256",zero);break;
                case "rehashed-board-model":{
                    Map<String,Object> models=map(bundle.get("original_board_models"));String id=models.keySet().iterator().next();models.put(id,zero);bundle.put("original_board_models_sha256",NativeFaultedJobReplacement.digest(models));intent.put("reconstruction_sha256",NativeFaultedJobReplacement.digest(bundle));NativeReplacementDocuments.validateReconstruction(bundle);check(true,"Rehashed board-model corruption passes pure bundle schema before exact captured-load binding is checked");break;
                }
                case "changed-definition-mapping":{
                    check(definition!=null,"Definition mutation uses actual later crash prefix");Map<String,Object> mapping=map(definition.get("mapping"));mapping.put("source_model_sha256",zero);definition.put("mapping_sha256",NativeFaultedJobReplacement.digest(mapping));break;
                }
                default:throw new IllegalArgumentException("Unknown negative case");
            }
            Path journal=state.resolve("journal/operations.jsonl");String originalSha=sha(Files.readAllBytes(journal));StringBuilder changed=new StringBuilder();for(Map<String,Object> e:rows)changed.append(JSON.toJson(e)).append('\n');Files.writeString(journal,changed,StandardOpenOption.TRUNCATE_EXISTING);byte[] mutated=Files.readAllBytes(journal);check(!originalSha.equals(sha(mutated)),"Only the disposable journal copy is intentionally changed");
            Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();Exception rejected=null;
            try{bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"native-simulator");}catch(Exception expected){rejected=expected;}
            check(rejected!=null,"Actual Bridge refuses malformed or rehashed replacement history during journal replay");
            List<Map<String,Object>> causes=new ArrayList<>();boolean replacementRefusal=false;for(Throwable cause=rejected;cause!=null;cause=cause.getCause()){List<String> frames=new ArrayList<>();for(StackTraceElement frame:cause.getStackTrace()){frames.add(frame.toString());if(frame.getClassName().equals("org.openpnp.codex.NativeFaultedJobReplacement"))replacementRefusal=true;}causes.add(Bridge.map("class",cause.getClass().getName(),"message",cause.getMessage(),"stack",frames));}
            check(replacementRefusal,"Malformed-history rejection originates in the exact native replacement reducer");proof.put("rejection_causes",causes);
            check(bridge==null&&!config.getMachine().isEnabled()&&!config.getMachine().isBusy()&&((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource()==null,"Rejected replay creates no Bridge authority, enabled machine or source");
            check(Arrays.equals(mutated,Files.readAllBytes(journal)),"Rejected replay does not rewrite even the deliberately malformed history");
            proof.putAll(Bridge.map("passed",true,"rejection_class",rejected.getClass().getName(),"rejection",rejected.getMessage(),"source_journal_sha256",originalSha,"mutated_journal_sha256",sha(mutated),"native_placements",0));
        }catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally{try{if(bridge!=null)bridge.close();}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}try{if(config!=null)config.getMachine().close();}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("assertions",checks.size(),"checks",checks,"pid",ProcessHandle.current().pid(),"case",mode,"scope","Disposable-copy negative replay of actual forced crash history; no native reconstruction or authority","restart_execution_qualified",false,"hardware_qualified",false));write("negative-proof.json",proof);System.out.println("NATIVE_REPLACEMENT_INTENT_REPLAY_REJECTION_RESULT "+JSON.toJson(proof));}System.exit(exit);
    }
}
