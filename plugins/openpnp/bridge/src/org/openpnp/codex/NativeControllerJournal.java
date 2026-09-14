/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.io.IOException;
import java.util.*;

/** Typed reducer in the Bridge's existing forced journal; never restores a native handle. */
final class NativeControllerJournal {
    static final String PROFILE="owned-tagged-controller-diagnostic-v1";
    static final List<String> STEPS=List.of("bind","connect","identify","close");
    private static final Gson JSON=new Gson();
    private Map<String,Object> binding;
    private final List<Map<String,Object>> outcomes=new ArrayList<>();
    private Map<String,Object> pending;
    private boolean retired;
    private Map<String,Object> retirement;
    private String modelFingerprint,protocolGeneration;
    private long ownerThread,observationSequence,commands,readBytes,writtenBytes;
    static boolean matches(String type){return type.startsWith("controller_diagnostic_");}
    boolean hasHistory(){return binding!=null;}
    String operationId(){return binding==null?null:(String)binding.get("operation_id");}
    Map<String,Object> snapshot(){return copy(map("profile",PROFILE,"binding",binding,"steps",outcomes,"pending",pending,"retired",retired,"native_authority_restored",false,"physical_qualification",false));}
    void finishRecovery(Map<String,Object> op)throws IOException{
        for(String key:List.of("operation_id","request_id","request_digest","bridge_instance_id","config_revision","controller_instance_id"))if(!Objects.equals(op.get(key),binding.get(key)))throw bad("Final controller operation identity differs: "+key);
        if(!"openpnp_run_controller_diagnostic".equals(op.get("method")))throw bad("Final controller operation method differs");
        if("succeeded".equals(op.get("state")))successful(op);
    }
    /** Replay ordering is checked against the operation visible at this exact record, not its final value. */
    void acceptRecovered(String type,Map<String,Object> value,Map<String,Map<String,Object>> operations)throws Exception {
        if(!"controller_diagnostic_admission".equals(type)) {
            Map<String,Object> op=binding==null?null:operations.get(operationId());
            if(op==null||!"running".equals(op.get("state")))throw bad("Controller effect record outside running operation");
        }
        accept(type,value,operations);
    }
    void observeRecoveredOperation(Map<String,Object> op,Map<String,Object> previous)throws IOException {
        if(!"openpnp_run_controller_diagnostic".equals(op.get("method"))&&!Objects.equals(operationId(),op.get("operation_id")))return;
        String state=string(op,"state");
        if(previous==null){if(!"accepted".equals(state))throw bad("Controller operation missing accepted prefix");return;}
        for(String key:List.of("operation_id","request_id","request_digest","bridge_instance_id","config_revision","controller_instance_id","method"))if(!Objects.equals(op.get(key),previous.get(key)))throw bad("Controller operation identity changed: "+key);
        String prior=string(previous,"state");
        // The generic simulator can explicitly abandon an old unknown result. This changes
        // its disposition only, preserving the prior physical outcome as unknown and never replaying.
        if("outcome_unknown".equals(prior)&&"cancelled".equals(state)) {
            if(!(op.get("result") instanceof Map))throw bad("Missing conservative abandonment result");
            Map<String,Object> result=(Map<String,Object>)op.get("result");keys(result,"resolution","previous_physical_outcome","repeat_action_performed");
            if(!"abandoned-after-simulator-reset".equals(result.get("resolution"))||!"unknown".equals(result.get("previous_physical_outcome"))||!Boolean.FALSE.equals(result.get("repeat_action_performed")))throw bad("Invalid conservative controller abandonment");return;
        }
        // A diagnostic is one use: no resume, terminal rewrite, or replay after a recovery disposition.
        if(!List.of("accepted","running").contains(prior))throw bad("Controller operation changed after terminal disposition");
        if("running".equals(state)){if(!"accepted".equals(prior)||binding==null)throw bad("Controller running transition out of order");return;}
        if(!List.of("succeeded","failed","outcome_unknown","cancelled").contains(state))throw bad("Invalid controller terminal state");
        if("succeeded".equals(state)){if(!"running".equals(prior)||binding==null)throw bad("Controller success precedes recipe");finishRecovery(op);}
    }
    private void successful(Map<String,Object> op)throws IOException {
        if(!retired||retirement==null||!Boolean.TRUE.equals(retirement.get("completed_recipe"))||pending!=null||outcomes.size()!=4||outcomes.stream().anyMatch(o->!Boolean.TRUE.equals(o.get("native_returned"))))throw bad("Successful operation lacks complete durable controller recipe");
        if(!(op.get("result") instanceof Map)||!(op.get("native_completion") instanceof Map))throw bad("Successful operation lacks body or native wrapper evidence");
        Map<String,Object> result=copy((Map<String,Object>)op.get("result")),completion=copy((Map<String,Object>)op.get("native_completion"));
        Set<String> fields=new HashSet<>(List.of("controller_instance_id","profile","completed_recipe","steps_attempted","outcome_unknown","observation","physical_qualification","physical_standstill_verified","motion_completion_observed","error_type","uncommitted_observation"));
        if(!fields.containsAll(result.keySet())||!Objects.equals(binding.get("controller_instance_id"),result.get("controller_instance_id"))||!PROFILE.equals(result.get("profile"))||!Boolean.TRUE.equals(result.get("completed_recipe"))||number(result,"steps_attempted")!=4||!Boolean.FALSE.equals(result.get("outcome_unknown"))||!Objects.equals(retirement.get("resource_teardown"),result.get("observation"))||result.get("error_type")!=null||result.get("uncommitted_observation")!=null)throw bad("Successful body differs from completed controller retirement");
        for(String key:List.of("physical_qualification","physical_standstill_verified","motion_completion_observed"))if(!Boolean.FALSE.equals(result.get(key)))throw bad("Successful body scope differs");
        keys(completion,"submission_id","phase","native_wrapper_completed","native_wrapper_succeeded","physical_outcome_verified");uuid(completion,"submission_id");
        if(!"completed".equals(completion.get("phase"))||!Boolean.TRUE.equals(completion.get("native_wrapper_completed"))||!Boolean.TRUE.equals(completion.get("native_wrapper_succeeded"))||!Boolean.FALSE.equals(completion.get("physical_outcome_verified")))throw bad("Success lacks completed native wrapper observation");
    }
    Runnable prepare(String type,Map<String,Object> payload,Map<String,Map<String,Object>> operations)throws Exception{
        NativeControllerJournal candidate=new NativeControllerJournal();candidate.binding=copy(binding);candidate.pending=copy(pending);candidate.retired=retired;candidate.retirement=copy(retirement);candidate.modelFingerprint=modelFingerprint;candidate.protocolGeneration=protocolGeneration;candidate.ownerThread=ownerThread;candidate.observationSequence=observationSequence;candidate.commands=commands;candidate.readBytes=readBytes;candidate.writtenBytes=writtenBytes;
        for(Map<String,Object> outcome:outcomes)candidate.outcomes.add(copy(outcome));
        candidate.accept(type,payload,operations);
        return ()->{binding=candidate.binding;pending=candidate.pending;retired=candidate.retired;retirement=candidate.retirement;modelFingerprint=candidate.modelFingerprint;protocolGeneration=candidate.protocolGeneration;ownerThread=candidate.ownerThread;observationSequence=candidate.observationSequence;commands=candidate.commands;readBytes=candidate.readBytes;writtenBytes=candidate.writtenBytes;outcomes.clear();outcomes.addAll(candidate.outcomes);};
    }
    void accept(String type,Map<String,Object> value,Map<String,Map<String,Object>> operations)throws Exception{
        Map<String,Object> p=copy(value);if(p==null)throw bad("Missing controller payload");
        if("controller_diagnostic_admission".equals(type)){
            keys(p,"schema_version","profile","controller_instance_id","operation_id","request_id","request_digest","bridge_instance_id","machine_id","config_revision","ownership_epoch","owner_generation");
            if(binding!=null||number(p,"schema_version")!=1||!PROFILE.equals(p.get("profile")))throw bad("Controller admission/profile conflict");
            for(String key:List.of("controller_instance_id","operation_id","request_id","bridge_instance_id","machine_id","owner_generation"))uuid(p,key);
            if(!string(p,"request_digest").matches("[a-f0-9]{64}")||!string(p,"config_revision").matches("cfg-[0-9]+")||number(p,"ownership_epoch")<1)throw bad("Invalid controller binding");
            Map<String,Object> op=operations.get(p.get("operation_id"));
            if(op==null||!"accepted".equals(op.get("state"))||!"openpnp_run_controller_diagnostic".equals(op.get("method")))throw bad("Missing original controller operation admission");
            for(String key:List.of("operation_id","request_id","request_digest","bridge_instance_id","config_revision","controller_instance_id"))if(!Objects.equals(op.get(key),p.get(key)))throw bad("Controller operation binding differs: "+key);
            binding=p;return;
        }
        if(binding==null||retired)throw bad("No live controller receipt generation");
        if(!Objects.equals(binding.get("operation_id"),p.get("operation_id"))||!Objects.equals(binding.get("controller_instance_id"),p.get("controller_instance_id"))||number(p,"schema_version")!=1)throw bad("Controller receipt identity mismatch");
        if("controller_diagnostic_step_intent".equals(type)){
            keys(p,"schema_version","operation_id","controller_instance_id","step","step_index","before");
            long index=number(p,"step_index");
            if(pending!=null||(!outcomes.isEmpty()&&!Boolean.TRUE.equals(outcomes.get(outcomes.size()-1).get("native_returned")))||index!=outcomes.size()+1||index<1||index>4||!STEPS.get((int)index-1).equals(p.get("step")))throw bad("Controller step order mismatch");
            observation(p.get("before"),"before-"+p.get("step"));pending=p;return;
        }
        if("controller_diagnostic_step_outcome".equals(type)){
            keys(p,"schema_version","operation_id","controller_instance_id","step","step_index","dispatched","native_returned","after","error_type");
            if(pending==null||!Objects.equals(p.get("step"),pending.get("step"))||number(p,"step_index")!=number(pending,"step_index"))throw bad("Controller outcome without exact intent");
            bool(p,"dispatched");bool(p,"native_returned");if(Boolean.TRUE.equals(p.get("native_returned"))&&!Boolean.TRUE.equals(p.get("dispatched")))throw bad("Native return without dispatch");
            if(!(p.get("error_type") instanceof String)||((String)p.get("error_type")).length()>200||Boolean.TRUE.equals(p.get("native_returned"))!= "none".equals(p.get("error_type")))throw bad("Invalid error type/return binding");
            observation(p.get("after"),"after-"+p.get("step"));
            if(Boolean.TRUE.equals(p.get("native_returned"))){
                Map<String,Object> after=(Map<String,Object>)p.get("after");Map<String,Object> nativeState=(Map<String,Object>)after.get("native");if(nativeState==null)throw bad("Native return lacks native observation");
                Map<String,Object> protocol=(Map<String,Object>)nativeState.get("protocol");String step=(String)p.get("step");long expected=step.equals("bind")?0:step.equals("connect")?2:3;
                if(commands!=expected)throw bad("Unexpected command count for completed step");
                if(step.equals("connect")||step.equals("identify")){if(!Boolean.TRUE.equals(protocol.get("native_connected_flag"))||!Boolean.TRUE.equals(protocol.get("native_connect_completed"))||!Boolean.TRUE.equals(protocol.get("admission_not_fenced")))throw bad("Completed command lacks healthy native response observation");}
                if(step.equals("close")&&(!Boolean.FALSE.equals(protocol.get("reader_alive"))||!Boolean.FALSE.equals(protocol.get("channel_open"))||!Boolean.FALSE.equals(protocol.get("native_connected_flag"))||protocol.get("transport_fault")!=null))throw bad("Close return lacks resource closure");
            }
            outcomes.add(p);pending=null;return;
        }
        if("controller_diagnostic_retired".equals(type)){
            keys(p,"schema_version","operation_id","controller_instance_id","completed_recipe","resource_teardown","physical_standstill_verified");
            bool(p,"completed_recipe");if(!Boolean.FALSE.equals(p.get("physical_standstill_verified")))throw bad("Physical standstill cannot be inferred");
            if(Boolean.TRUE.equals(p.get("completed_recipe"))&&(pending!=null||outcomes.size()!=4||outcomes.stream().anyMatch(o->!Boolean.TRUE.equals(o.get("native_returned")))))throw bad("Incomplete controller recipe claimed complete");
            observation(p.get("resource_teardown"),null);retired=true;retirement=p;return;
        }
        throw bad("Unknown controller event version/type");
    }
    private void observation(Object value,String expectedPhase)throws IOException{
        if(!(value instanceof Map)||JSON.toJson(value).getBytes(java.nio.charset.StandardCharsets.UTF_8).length>16384)throw bad("Controller observation must be a bounded object");
        Map<String,Object> p=(Map<String,Object>)value;Set<String> permitted=new HashSet<>(List.of("phase","observed_at","observation_sequence","physical_qualification","motion_completion_observed","physical_standstill_verified","native","error_type"));
        if(!permitted.containsAll(p.keySet())||!Boolean.FALSE.equals(p.get("physical_qualification"))||!Boolean.FALSE.equals(p.get("motion_completion_observed"))||!Boolean.FALSE.equals(p.get("physical_standstill_verified")))throw bad("Observation scope differs");
        String phase=string(p,"phase");if(expectedPhase!=null&&!expectedPhase.equals(phase)||expectedPhase==null&&!List.of("retired","resource-teardown-failed").contains(phase))throw bad("Observation phase differs");
        try{java.time.Instant.parse(string(p,"observed_at"));}catch(RuntimeException invalid){throw bad("Invalid observation timestamp");}
        long next=number(p,"observation_sequence");if(next!=observationSequence+1)throw bad("Observation sequence is not contiguous");
        Object raw=p.get("native");
        if(raw==null){if(modelFingerprint!=null&&!"resource-teardown-failed".equals(phase))throw bad("Bound native observation disappeared");observationSequence=next;return;}
        if(!(raw instanceof Map))throw bad("Invalid native observation");Map<String,Object> n=(Map<String,Object>)raw;
        keys(n,"profile","native_task","owner_thread_id","model_binding_sha256","connect_attempted","owner_fenced","closed","protocol","current_model_validation_performed_by_snapshot","durable_receipt","physical_qualification");
        if(!"owned-tagged-gcode-v1".equals(n.get("profile"))||!Boolean.TRUE.equals(n.get("native_task"))||!Boolean.FALSE.equals(n.get("physical_qualification"))||!Boolean.FALSE.equals(n.get("durable_receipt"))||!Boolean.FALSE.equals(n.get("current_model_validation_performed_by_snapshot")))throw bad("Native observation provenance differs");
        for(String key:List.of("connect_attempted","owner_fenced","closed"))bool(n,key);
        long thread=number(n,"owner_thread_id");String fingerprint=string(n,"model_binding_sha256");if(thread<1||!fingerprint.matches("[a-f0-9]{64}"))throw bad("Invalid native owner/model binding");
        if(!(n.get("protocol") instanceof Map))throw bad("Protocol observation missing");Map<String,Object> protocol=(Map<String,Object>)n.get("protocol");
        Set<String> protocolKeys=new HashSet<>(List.of("profile","native_driver","generation","commands_attempted","native_connected_flag","native_connect_completed","channel_open","reader_alive","reader_uncaught_error_type","transport_phase","transport_fault","admission_not_fenced","wire_bytes_read","wire_bytes_written","native_response_queue_size","native_confirmation_queue_size","physical_qualification","physical_standstill_verified"));
        if(!protocolKeys.containsAll(protocol.keySet())||!"owned-tagged-gcode-v1".equals(protocol.get("profile"))||!"org.openpnp.codex.prototype.tagged.OwnedTaggedGcodeDriver".equals(protocol.get("native_driver"))||!Boolean.FALSE.equals(protocol.get("physical_qualification"))||!Boolean.FALSE.equals(protocol.get("physical_standstill_verified")))throw bad("Protocol provenance differs");
        String generation=string(protocol,"generation");if(!generation.matches("[a-f0-9]{32}"))throw bad("Invalid protocol generation");
        if(modelFingerprint!=null&&(!modelFingerprint.equals(fingerprint)||!protocolGeneration.equals(generation)||ownerThread!=thread))throw bad("Native model/owner/protocol generation changed");
        for(String key:List.of("native_connected_flag","native_connect_completed","channel_open","reader_alive","admission_not_fenced"))bool(protocol,key);
        long commandCount=number(protocol,"commands_attempted"),read=number(protocol,"wire_bytes_read"),written=number(protocol,"wire_bytes_written");
        if(commandCount<commands||commandCount>3||read<readBytes||read>32768||written<writtenBytes||written>32768||number(protocol,"native_response_queue_size")>8||number(protocol,"native_confirmation_queue_size")>8)throw bad("Protocol counter bound/monotonicity differs");
        string(protocol,"transport_phase");for(String key:List.of("transport_fault","reader_uncaught_error_type"))if(protocol.get(key)!=null&&string(protocol,key).length()>200)throw bad("Protocol fault exceeds bound");
        modelFingerprint=fingerprint;protocolGeneration=generation;ownerThread=thread;observationSequence=next;commands=commandCount;readBytes=read;writtenBytes=written;
    }
    private static Map<String,Object> copy(Map<String,Object> p){return NativeJournalJson.copy(p);}
    private static void keys(Map<String,Object> p,String...keys)throws IOException{if(!p.keySet().equals(new HashSet<>(Arrays.asList(keys))))throw bad("Unexpected controller fields");}
    private static void uuid(Map<String,Object> p,String k)throws IOException{String s=string(p,k);try{if(!UUID.fromString(s).toString().equals(s))throw new IllegalArgumentException();}catch(Exception e){throw bad("Invalid controller UUID "+k);}}
    private static String string(Map<String,Object> p,String k)throws IOException{if(!(p.get(k) instanceof String))throw bad("Expected string "+k);return (String)p.get(k);}
    private static void bool(Map<String,Object> p,String k)throws IOException{if(!(p.get(k) instanceof Boolean))throw bad("Expected boolean "+k);}
    private static long number(Map<String,Object> p,String k)throws IOException{try{return NativeJournalJson.integer(p.get(k),0,9007199254740991L);}catch(IllegalArgumentException invalid){throw bad("Invalid exact integer "+k);}}
    private static IOException bad(String message){return new IOException(message);}
    static Map<String,Object> map(Object...pairs){Map<String,Object> p=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)p.put((String)pairs[i],pairs[i+1]);return p;}
}
