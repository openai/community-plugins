/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.time.Instant;
import java.util.*;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.codex.prototype.binding.NativeExecutorTaggedOwner;
import org.openpnp.model.Configuration;
import static org.openpnp.codex.NativeControllerJournal.map;

/** Fresh fixed-recipe native diagnostic. Endpoint comes only from an owned in-process listener. */
public final class NativeControllerDiagnostic {
    interface Sink {void append(String type,Map<String,Object> payload)throws Exception;}
    interface Fence {void check()throws Exception;}
    private final NativeExecutorTaggedOwner.Launch launch;
    private final String id=UUID.randomUUID().toString(),ownerGeneration=UUID.randomUUID().toString();
    private boolean spent;
    private NativeExecutorTaggedOwner owner;
    private volatile Map<String,Object> observed=map("phase","fresh","physical_qualification",false,"native_authority_restored",false);
    private volatile Map<String,Object> pendingKnown;
    private Map<String,Object> pendingAdmission;
    private int observationSequence;
    public NativeControllerDiagnostic(Configuration config,TypedGcodeProfile.OwnedEndpoint endpoint)throws Exception{launch=NativeExecutorTaggedOwner.prepare(config,endpoint);}
    String id(){return id;}
    void guard(Configuration config){launch.guard(config);}
    void recovered(){spent=true;}
    void consume(Map<String,Object> operation){if(spent)throw new IllegalStateException("CONTROLLER_GENERATION_SPENT");spent=true;pendingAdmission=map("operation_id",operation.get("operation_id"),"request_id",operation.get("request_id"),"request_digest",operation.get("request_digest"),"controller_instance_id",id,"durable_admission_confirmed",false,"native_dispatch_performed",false,"physical_qualification",false);pendingKnown=map("admission",pendingAdmission,"publication_confirmed",false,"physical_qualification",false);}
    void admitted(){pendingAdmission=null;pendingKnown=null;}
    Map<String,Object> pendingRequest(String request){return pendingAdmission!=null&&request.equals(pendingAdmission.get("request_id"))?new LinkedHashMap<>(pendingAdmission):null;}
    Map<String,Object> admission(String operation,String request,String digest,String bridge,String machine,String revision,long epoch){return map("schema_version",1,"profile",NativeControllerJournal.PROFILE,"controller_instance_id",id,"operation_id",operation,"request_id",request,"request_digest",digest,"bridge_instance_id",bridge,"machine_id",machine,"config_revision",revision,"ownership_epoch",epoch,"owner_generation",ownerGeneration);}
    Map<String,Object> cached(){return map("controller_instance_id",id,"profile",NativeControllerJournal.PROFILE,"generation_spent",spent,"observation",observed,"uncommitted_observation",pendingKnown,"physical_qualification",false);}
    private Map<String,Object> observe(String phase){
        Map<String,Object> next=map("phase",phase,"observed_at",Instant.now().toString(),"observation_sequence",++observationSequence,"physical_qualification",false,"motion_completion_observed",false,"physical_standstill_verified",false);
        if(owner!=null)next.put("native",owner.snapshot());observed=Collections.unmodifiableMap(next);return observed;
    }
    Map<String,Object> run(String operation,Sink sink,Fence fence)throws Exception{
        boolean complete=false,unknown=false;Throwable failure=null;int attempted=0;
        try{
            for(int index=1;index<=4;index++){
                String step=NativeControllerJournal.STEPS.get(index-1);fence.check();if(owner!=null)owner.validate();
                Map<String,Object> before=observe("before-"+step);
                Map<String,Object> intent=map("schema_version",1,"operation_id",operation,"controller_instance_id",id,"step",step,"step_index",index,"before",before);
                // The in-memory pending marker survives a write/force exception. It never grants a retry.
                pendingKnown=map("intent",intent,"publication_confirmed",false,"physical_qualification",false);unknown=true;
                sink.append("controller_diagnostic_step_intent",intent);
                boolean dispatched=false,returned=false;Throwable nativeFailure=null;attempted=index;
                try{
                    fence.check();if(owner!=null)owner.validate();dispatched=true;
                    switch(step){case "bind":owner=launch.bind();Configuration.get().save();owner.validate();break;case "connect":owner.connect();break;case "identify":owner.identify();break;case "close":owner.close();Map<String,Object> state=owner.snapshot();Map<?,?> protocol=(Map<?,?>)state.get("protocol");if(!Boolean.FALSE.equals(protocol.get("reader_alive"))||!Boolean.FALSE.equals(protocol.get("channel_open"))||!Boolean.FALSE.equals(protocol.get("native_connected_flag"))||protocol.get("transport_fault")!=null)throw new IllegalStateException("CONTROLLER_CLOSE_NOT_CONFIRMED");break;default:throw new AssertionError(step);}
                    returned=true;
                }catch(Throwable error){nativeFailure=error;}
                Map<String,Object> outcome=map("schema_version",1,"operation_id",operation,"controller_instance_id",id,"step",step,"step_index",index,"dispatched",dispatched,"native_returned",returned,"after",observe("after-"+step),"error_type",nativeFailure==null?"none":nativeFailure.getClass().getName());
                pendingKnown=map("known_native_outcome",outcome,"publication_confirmed",false,"physical_qualification",false);
                sink.append("controller_diagnostic_step_outcome",outcome);pendingKnown=null;unknown=dispatched&&!returned;
                if(nativeFailure!=null)throw nativeFailure;
            }
            complete=true;
        }catch(Throwable error){failure=error;}
        Map<String,Object> teardown;
        try{if(owner!=null&&!complete)owner.close();teardown=observe("retired");}
        catch(Throwable closeError){unknown=true;teardown=map("phase","resource-teardown-failed","error_type",closeError.getClass().getName(),"observed_at",Instant.now().toString(),"observation_sequence",++observationSequence,"motion_completion_observed",false,"physical_standstill_verified",false,"physical_qualification",false);observed=teardown;if(failure==null)failure=closeError;else failure.addSuppressed(closeError);}
        try{sink.append("controller_diagnostic_retired",map("schema_version",1,"operation_id",operation,"controller_instance_id",id,"completed_recipe",complete,"resource_teardown",teardown,"physical_standstill_verified",false));}
        catch(Throwable journalError){unknown=true;if(failure==null)failure=journalError;else failure.addSuppressed(journalError);}
        return map("controller_instance_id",id,"profile",NativeControllerJournal.PROFILE,"completed_recipe",complete,"steps_attempted",attempted,"outcome_unknown",unknown,"error_type",failure==null?null:failure.getClass().getName(),"observation",observed,"uncommitted_observation",pendingKnown,"physical_qualification",false,"physical_standstill_verified",false,"motion_completion_observed",false);
    }
}
