/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.io.IOException;
import java.util.*;

/** Ordered, data-only topology receipts. Replaying them never restores native authority. */
final class NativeTopologyJournal {
    static final String PROFILE="isolated-NullDriver-nozzle-assembly-v1";
    static final String EFFECT="nozzle-assembly-configuration-model";
    private static final String METHOD="openpnp_apply_configuration";
    private static final List<String> IDENTITY=List.of("operation_id","request_id","request_digest","method","bridge_instance_id","config_revision","accepted_at");
    private final Map<String,Entry> entries=new LinkedHashMap<>();
    private static final class Entry {
        Map<String,Object> admission,operation,binding,body;
        boolean intent,outcome;
        Entry copy(){Entry e=new Entry();e.admission=copyMap(admission);e.operation=copyMap(operation);e.binding=copyMap(binding);e.body=copyMap(body);e.intent=intent;e.outcome=outcome;return e;}
    }
    static boolean matches(String type){return type.startsWith("topology_")||Set.of("operation","native_effect_intent","native_effect_outcome").contains(type);}
    /** Validate before the durable append; publish the reducer state only after force succeeds. */
    Runnable prepare(String type,Map<String,Object> p,String envelopeInstance)throws IOException {
        Object rawId=p.get("operation_id");
        boolean operation="operation".equals(type),effect=type.equals("native_effect_intent")||type.equals("native_effect_outcome");
        boolean own=type.startsWith("topology_");
        Entry previous=entries.get(rawId);
        if(!own&&!(operation&&(METHOD.equals(p.get("method"))||previous!=null))&&!(effect&&(EFFECT.equals(p.get("kind"))||previous!=null&&previous.binding!=null)))return ()->{};
        String id=uuid(p,"operation_id");Entry next=previous==null?new Entry():previous.copy();
        if(operation)observe(next,p,envelopeInstance);
        else accept(next,type,p,envelopeInstance);
        return ()->entries.put(id,next);
    }
    void recover(String type,Map<String,Object> p,String envelopeInstance)throws IOException{prepare(type,p,envelopeInstance).run();}
    void verifyMachineIdentity(String machineId)throws IOException{for(Entry e:entries.values())if(e.binding!=null&&!Objects.equals(machineId,e.binding.get("machine_id")))throw bad("Topology binding differs from durable journal machine identity");}
    boolean recoveryRequired(){return entries.values().stream().anyMatch(e->e.binding!=null&&!"succeeded".equals(e.operation.get("state")));}
    private static void observe(Entry e,Map<String,Object> p,String envelope)throws IOException {
        String state=text(p,"state");
        if(e.admission==null){
            if(!METHOD.equals(p.get("method"))||!"accepted".equals(state))throw bad("Missing accepted topology-capable operation prefix");
            for(String key:List.of("operation_id","bridge_instance_id"))uuid(p,key);
            if(text(p,"request_id").isEmpty()||text(p,"request_id").length()>1024||!text(p,"request_digest").matches("[a-f0-9]{64}")||!text(p,"config_revision").matches("cfg-[0-9]+")||!Objects.equals(envelope,p.get("bridge_instance_id")))throw bad("Invalid operation admission identity");
            try{java.time.Instant.parse(text(p,"accepted_at"));}catch(RuntimeException invalid){throw bad("Invalid admission timestamp");}
            e.admission=copyMap(p);e.operation=copyMap(p);return;
        }
        sameIdentity(e.admission,p);
        String prior=text(e.operation,"state");
        if(!Set.of("accepted","running").contains(prior)){
            // Explicit abandonment preserves unknown outcome; it cannot clear a topology fence.
            if(!"outcome_unknown".equals(prior)||!"cancelled".equals(state))throw bad("Operation changed after terminal disposition");
            Map<String,Object> result=object(p,"result");
            keys(result,"resolution","previous_physical_outcome","repeat_action_performed");
            if(!"abandoned-after-simulator-reset".equals(result.get("resolution"))||!"unknown".equals(result.get("previous_physical_outcome"))||!Boolean.FALSE.equals(result.get("repeat_action_performed")))throw bad("Invalid conservative abandonment");
        } else if("running".equals(state)){
            if(!"accepted".equals(prior)||!Objects.equals(envelope,p.get("bridge_instance_id")))throw bad("Running transition out of order or instance");
        } else if(!Set.of("succeeded","failed","outcome_unknown","cancelled").contains(state))throw bad("Invalid operation disposition");
        if(e.binding==null&&(EFFECT.equals(p.get("native_effect_kind"))||p.get("result") instanceof Map&&((Map<?,?>)p.get("result")).containsKey("created_assembly")))throw bad("Topology operation lacks bound preimage");
        if(e.binding!=null&&"succeeded".equals(state)){
            if(!"running".equals(prior)||!Objects.equals(envelope,e.binding.get("bridge_instance_id")))throw bad("Success out of order or instance");
            successful(e,p);
        }
        e.operation=copyMap(p);
    }
    private static void accept(Entry e,String type,Map<String,Object> p,String envelope)throws IOException {
        if(e.operation==null||!"running".equals(e.operation.get("state")))throw bad("Topology receipt outside running operation");
        if(!Objects.equals(envelope,e.admission.get("bridge_instance_id")))throw bad("Topology receipt instance differs");
        if("topology_recovery_available".equals(type)){
            keys(p,"schema_version","profile","operation_id","request_id","request_digest","method","bridge_instance_id","machine_id","config_revision","accepted_at","submission_id","artifact");
            if(e.binding!=null||number(p,"schema_version")!=1||!PROFILE.equals(p.get("profile")))throw bad("Topology binding version/profile conflict");
            sameIdentity(e.admission,p);uuid(p,"machine_id");uuid(p,"submission_id");
            Map<String,Object> artifact=object(p,"artifact");keys(artifact,"artifact_id","mime_type","sha256","size","metadata");uuid(artifact,"artifact_id");
            if(!"application/zip".equals(artifact.get("mime_type"))||!text(artifact,"sha256").matches("[a-f0-9]{64}")||number(artifact,"size")<1||number(artifact,"size")>8*1024*1024)throw bad("Invalid topology preimage artifact");
            Map<String,Object> metadata=object(artifact,"metadata");keys(metadata,"kind","scope","operation_id","config_revision","in_place_restore","execution_authority_transferred");
            if(!"nozzle-assembly-recovery-preimage".equals(metadata.get("kind"))||!"fresh-native-simulator-adoption".equals(metadata.get("scope"))||!Objects.equals(p.get("operation_id"),metadata.get("operation_id"))||!Objects.equals(p.get("config_revision"),metadata.get("config_revision"))||!Boolean.FALSE.equals(metadata.get("in_place_restore"))||!Boolean.FALSE.equals(metadata.get("execution_authority_transferred")))throw bad("Preimage identity or recovery scope differs");
            e.binding=copyMap(p);return;
        }
        if(e.binding==null)throw bad("Topology effect lacks durable preimage binding");
        if("topology_model_persisted".equals(type)){
            keys(p,"schema_version","profile","operation_id","submission_id","result");
            if(!e.intent||e.outcome||e.body!=null||number(p,"schema_version")!=1||!PROFILE.equals(p.get("profile"))||!Objects.equals(p.get("submission_id"),e.binding.get("submission_id")))throw bad("Persisted body order or submission differs");
            Map<String,Object> body=object(p,"result");validateBody(e,body);e.body=copyMap(body);return;
        }
        if(!EFFECT.equals(p.get("kind"))||!Boolean.FALSE.equals(p.get("physical_outcome_verified")))throw bad("Native effect identity/scope differs");
        if("native_effect_intent".equals(type)){
            keys(p,"operation_id","kind","physical_outcome_verified");
            if(e.intent||e.outcome||e.body!=null)throw bad("Duplicate or reordered topology intent");e.intent=true;return;
        }
        if("native_effect_outcome".equals(type)){
            keys(p,"operation_id","kind","native_call_returned","physical_outcome_verified");
            if(!e.intent||e.outcome||e.body==null||!Boolean.TRUE.equals(p.get("native_call_returned")))throw bad("Missing, duplicate, conflicting or reordered topology outcome");e.outcome=true;return;
        }
        throw bad("Unknown topology receipt type");
    }
    private static void validateBody(Entry e,Map<String,Object> body)throws IOException {
        keys(body,"config_revision","persisted","configuration_root","created_assembly","recovery_preimage");
        long revision;try{revision=Long.parseLong(text(e.binding,"config_revision").substring(4));}catch(RuntimeException invalid){throw bad("Invalid topology revision");}
        if(revision==Long.MAX_VALUE||!("cfg-"+(revision+1)).equals(body.get("config_revision"))||!Boolean.TRUE.equals(body.get("persisted"))||text(body,"configuration_root").isBlank()||text(body,"configuration_root").length()>4096||!same(body.get("recovery_preimage"),e.binding.get("artifact")))throw bad("Persisted body revision/preimage differs");
        Map<String,Object> created=object(body,"created_assembly");
        keys(created,"nozzle_id","nozzle_tip_id","vacuum_actuator_id","z_axis_id","rotation_axis_id","z_axis_letter","rotation_axis_letter","head_id","driver_id","x_axis_id","y_axis_id","exclusive_package_ids","simulated_initial_tool_state","physical_qualification");
        Set<String> ids=new HashSet<>();for(String key:List.of("nozzle_id","nozzle_tip_id","vacuum_actuator_id","z_axis_id","rotation_axis_id","head_id","driver_id","x_axis_id","y_axis_id")){String id=text(created,key);if(id.isBlank()||id.length()>128||!ids.add(id))throw bad("Invalid or aliased assembly identity");}
        String z=text(created,"z_axis_letter"),r=text(created,"rotation_axis_letter");if(!z.matches("[UVWABCDERST]")||!r.matches("[UVWABCDERST]")||z.equals(r))throw bad("Invalid assembly axis letters");
        if(!"installed-on-new-nozzle".equals(created.get("simulated_initial_tool_state"))||!Boolean.FALSE.equals(created.get("physical_qualification")))throw bad("Assembly state/scope differs");
        Object raw=created.get("exclusive_package_ids");if(!(raw instanceof List)||((List<?>)raw).isEmpty()||((List<?>)raw).size()>64)throw bad("Invalid package references");
        Set<String> packages=new HashSet<>();for(Object id:(List<?>)raw)if(!(id instanceof String)||((String)id).isBlank()||((String)id).length()>128||!packages.add(((String)id).toLowerCase(Locale.ROOT)))throw bad("Invalid or aliased package reference");
    }
    private static void successful(Entry e,Map<String,Object> p)throws IOException {
        if(!e.intent||!e.outcome||e.body==null||!Boolean.FALSE.equals(p.get("native_effect_pending"))||!EFFECT.equals(p.get("native_effect_kind"))||!same(p.get("result"),e.body))throw bad("Successful topology lacks exact persisted body and effect completion");
        Map<String,Object> completion=object(p,"native_completion");keys(completion,"submission_id","phase","native_wrapper_completed","native_wrapper_succeeded","physical_outcome_verified");
        if(!Objects.equals(completion.get("submission_id"),e.binding.get("submission_id"))||!"completed".equals(completion.get("phase"))||!Boolean.TRUE.equals(completion.get("native_wrapper_completed"))||!Boolean.TRUE.equals(completion.get("native_wrapper_succeeded"))||!Boolean.FALSE.equals(completion.get("physical_outcome_verified")))throw bad("Success lacks the bound completed native wrapper");
    }
    private static void sameIdentity(Map<String,Object> a,Map<String,Object> b)throws IOException{for(String k:IDENTITY)if(!Objects.equals(a.get(k),b.get(k)))throw bad("Immutable topology operation differs: "+k);}
    private static Map<String,Object> copyMap(Map<String,Object> p){return p==null?null:NativeJournalJson.copy(p);}
    private static boolean same(Object a,Object b){return NativeJournalJson.copy(Collections.singletonMap("value",a)).equals(NativeJournalJson.copy(Collections.singletonMap("value",b)));}
    private static Map<String,Object> object(Map<String,Object> p,String k)throws IOException{if(!(p.get(k) instanceof Map))throw bad("Expected object "+k);return (Map<String,Object>)p.get(k);}
    private static String text(Map<String,Object> p,String k)throws IOException{if(!(p.get(k) instanceof String))throw bad("Expected string "+k);return (String)p.get(k);}
    private static String uuid(Map<String,Object> p,String k)throws IOException{String s=text(p,k);try{if(!UUID.fromString(s).toString().equals(s))throw new IllegalArgumentException();return s;}catch(RuntimeException invalid){throw bad("Invalid UUID "+k);}}
    private static long number(Map<String,Object> p,String k)throws IOException{try{return NativeJournalJson.integer(p.get(k),0,9007199254740991L);}catch(RuntimeException invalid){throw bad("Invalid exact integer "+k);}}
    private static void keys(Map<String,Object> p,String...keys)throws IOException{if(!p.keySet().equals(new HashSet<>(Arrays.asList(keys))))throw bad("Unexpected topology receipt fields");}
    private static IOException bad(String message){return new IOException("Topology journal: "+message);}
}
