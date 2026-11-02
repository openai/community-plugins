/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Actual Bridge ownership, operation, forced observer records and native job lifecycle.
 * Explicit process-owned simulator fixture. No hardware or independently measured signal claim. */
public final class NativeVacuumBridgeTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();
    static Bridge bridge;static Configuration config;static Machine machine;static Path root,samples;static String session;static int checks;
    static JsonObject obj(Object...p){return JSON.toJsonTree(Bridge.map(p)).getAsJsonObject();}
    @SuppressWarnings("unchecked") static Map<String,Object> call(String m,JsonObject p)throws Exception{return(Map<String,Object>)bridge.call(m,p);}
    static void check(boolean yes,String message){checks++;if(!yes)throw new AssertionError(message);}
    static String revision()throws Exception{return(String)call("openpnp_get_status",obj()).get("config_revision");}
    static JsonObject command(Object...p)throws Exception{JsonObject q=obj(p);q.addProperty("session_id",session);q.addProperty("request_id",UUID.randomUUID().toString());q.addProperty("expected_config_revision",revision());return q;}
    static Map<String,Object> await(Map<String,Object> a)throws Exception{long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(40);while(true){Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",a.get("operation_id")));if(Set.of("succeeded","failed","outcome_unknown","aborted").contains(op.get("state")))return op;if(System.nanoTime()>until)throw new AssertionError("Native wrapper did not finish: "+op);Thread.sleep(5);}}
    static Map<String,Object> execute(String m,JsonObject p)throws Exception{return await(call(m,p));}
    static Map<?,?> result(Map<String,Object> op){return(Map<?,?>)op.get("result");}
    static void succeeded(Map<String,Object> op){check("succeeded".equals(op.get("state")),"Expected success: "+JSON.toJson(op));}
    static void refuse(String code,String method,JsonObject p)throws Exception{try{call(method,p);throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"Expected "+code+", got "+e.code);}}
    static ReferenceNozzle nozzle()throws Exception{return(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();}
    static List<Map<String,Object>> journal()throws Exception{List<Map<String,Object>> rows=new ArrayList<>();for(String line:Files.readAllLines(root.resolve("journal/operations.jsonl")))rows.add(NativeJournalJson.parseObject(line));return rows;}
    static long count(String type)throws Exception{return journal().stream().filter(r->type.equals(r.get("type"))).count();}
    static void setup(String scenario,boolean source)throws Exception {
        Path fresh=root.resolve("config");Configuration.initialize(fresh.toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.settleFreshFixture(fresh);
        SimulatorMain.configureVacuumSensingFixture(Configuration.get());SimulatorMain.settleFreshFixture(fresh);config=Configuration.get();machine=config.getMachine();
        if(source)NativeVacuumSources.installFixture(config,scenario);
        if(scenario.equals("missed-pick-retry"))config.getPart("R0603-1K").setPickRetryCount(1);
        for(Feeder f:machine.getFeeders()){((ReferenceTrayFeeder)f).setPickRetryCount(0);((ReferenceTrayFeeder)f).setFeedRetryCount(0);}
        Files.writeString(root.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID());
        bridge=new Bridge(config,root.resolve("token"),root.resolve("journal"),samples,0,true,"vacuum-sensing");
        session=(String)call("openpnp_request_control_session",obj("request_id",UUID.randomUUID().toString(),"ttl_seconds",300)).get("session_id");
    }
    static void home()throws Exception{succeeded(execute("openpnp_set_machine_enabled",command("enabled",true)));succeeded(execute("openpnp_home_machine",command()));}
    static Map<String,Object> verify(String state)throws Exception{return execute("openpnp_verify_part_state",command("nozzle_id",nozzle().getId(),"state",state));}
    static void manual()throws Exception {
        Map<?,?> cap=(Map<?,?>)call("openpnp_get_capabilities",obj()).get("vacuum_sensing");check(Boolean.TRUE.equals(cap.get("available")),"Controlled source advertised");
        JsonObject p=command("nozzle_id",nozzle().getId(),"samples",2);long initial=count("vacuum_observation_intent");
        JsonObject invalid=new JsonParser().parse(p.toString()).getAsJsonObject();invalid.addProperty("samples",new java.math.BigDecimal("1.0000000000000000001"));refuse("INVALID_ARGUMENT","openpnp_measure_sensor",invalid);
        invalid=new JsonParser().parse(p.toString()).getAsJsonObject();invalid.addProperty("script","forbidden");refuse("UNKNOWN_FIELD","openpnp_measure_sensor",invalid);
        invalid=new JsonParser().parse(p.toString()).getAsJsonObject();invalid.remove("expected_config_revision");refuse("INVALID_ARGUMENT","openpnp_measure_sensor",invalid);
        check(count("vacuum_observation_intent")==initial,"Closed admission failures perform no read");
        Map<String,Object> measured=execute("openpnp_measure_sensor",p);succeeded(measured);check(((Number)result(measured).get("sample_count")).intValue()==2,"Two actual finite native samples");
        long reads=count("vacuum_observation_intent");Map<String,Object> replay=call("openpnp_measure_sensor",p);check(replay.get("operation_id").equals(measured.get("operation_id"))&&reads==count("vacuum_observation_intent"),"Exact request reattaches without repeat read");
        invalid=new JsonParser().parse(p.toString()).getAsJsonObject();invalid.addProperty("samples",3);refuse("REQUEST_ID_CONFLICT","openpnp_measure_sensor",invalid);
        home();succeeded(verify("part_off"));
        check(Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Probe finishes with native valve off");
        succeeded(execute("openpnp_control_actuator",command("actuator_id",nozzle().getVacuumActuator().getId(),"enabled",true)));
        Map<String,Object> staleEmpty=execute("openpnp_prepare_job",command("canonical_job",NativeVacuumOperationsTest.canonical(config.getPart("R0603-1K"))));
        check("failed".equals(staleEmpty.get("state"))&&"NOZZLE_OCCUPANCY_UNRESOLVED".equals(result(staleEmpty).get("code")),"Direct valve actuation cannot reuse old empty evidence for job preparation");
        succeeded(verify("part_off"));
        check(Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Fresh native off check reestablishes empty evidence after ordinary actuation");
        for(Map<String,Object> row:journal())if(row.get("type").toString().startsWith("vacuum_observation")){
            Map<?,?> payload=(Map<?,?>)row.get("payload"),context=(Map<?,?>)payload.get("context"),data=(Map<?,?>)payload.get("data");
            check(context.containsKey("job_context")&&context.get("job_context")==null,"Manual durable null retained");check(data.containsKey("parent_observation_id"),"Explicit parent field retained");
            Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",context.get("operation_id")));check(op.get("request_id").equals(context.get("request_id"))&&op.get("config_revision").equals(context.get("config_revision")),"Authentic Bridge operation context");
        }
        String before=revision();succeeded(execute("openpnp_set_machine_enabled",command("enabled",false)));
        JsonObject change=settingsChange();JsonArray changes=new JsonArray();changes.add(change);
        Map<String,Object> plan=call("openpnp_plan_configuration",command("changes",changes));Map<String,Object> applied=execute("openpnp_apply_configuration",command("plan_id",plan.get("plan_id")));succeeded(applied);
        check(!before.equals(revision()),"Typed sensing change advances revision");
        check(journal().stream().noneMatch(r->r.get("type").equals("board_registration_invalidated")),"Sensing does not invalidate axis registration");
        home();Map<String,Object> retained=verify("part_off");succeeded(retained);check(Boolean.FALSE.equals(result(retained).get("native_verdict")),"Known retained sample is a known false verdict");
        refuse("VACUUM_OUTCOME_UNKNOWN","openpnp_measure_sensor",command("nozzle_id",nozzle().getId()));
        succeeded(execute("openpnp_set_machine_enabled",command("enabled",false)));
        call("openpnp_release_control_session",obj("session_id",session,"request_id",UUID.randomUUID().toString()));
        check(nozzle().getPart()==null,"Retained sensor evidence survives null native Part");
    }
    static Map<?,?> sensing()throws Exception{return(Map<?,?>)call("openpnp_get_status",obj()).get("vacuum_sensing_journal");}
    static void lifecycle(String mode)throws Exception {
        home();ReferenceActuator valve=(ReferenceActuator)nozzle().getVacuumActuator();
        if(!mode.equals("lifecycle-empty"))succeeded(verify("part_off"));
        if(mode.equals("lifecycle-enable")||mode.equals("lifecycle-postforce")){
            succeeded(execute("openpnp_set_machine_enabled",command("enabled",false)));
            check(((List<?>)sensing().get("nozzles")).stream().anyMatch(n->"observed_empty".equals(((Map<?,?>)n).get("state"))),"Proven no-actuation disable preserves fresh empty evidence");
            long effects=count("native_effect_intent");Object prior=valve.getLastActuationValue();
            if(mode.equals("lifecycle-enable"))valve.setEnabledActuation(ReferenceActuator.MachineStateActuation.ActuateOn);
            else {java.lang.reflect.Field field=Bridge.class.getDeclaredField("journal");field.setAccessible(true);field.set(bridge,new Boundary((java.nio.channels.FileChannel)field.get(bridge),mode));}
            Map<String,Object> refused=execute("openpnp_set_machine_enabled",command("enabled",true));
            check((mode.equals("lifecycle-enable")?"failed":"outcome_unknown").equals(refused.get("state")),"Unsafe enable settles at its actual effect boundary");
            check(!machine.isEnabled()&&Objects.equals(prior,valve.getLastActuationValue()),"Policy-drift enable performs zero native enable/valve effects");
            check(count("native_effect_intent")==effects+(mode.equals("lifecycle-enable")?0:1),"Pre-dispatch refusal versus forced but fenced intent distinguished");
            if(mode.equals("lifecycle-postforce"))return;
            check("SENSING_LIFECYCLE_UNSAFE".equals(result(refused).get("code")),"Typed policy refusal");
            valve.setEnabledActuation(ReferenceActuator.MachineStateActuation.AssumeUnknown);succeeded(execute("openpnp_set_machine_enabled",command("enabled",true)));
            effects=count("native_effect_intent");prior=valve.getLastActuationValue();valve.setHomedActuation(ReferenceActuator.MachineStateActuation.ActuateOn);
            refused=execute("openpnp_home_machine",command());check("failed".equals(refused.get("state"))&&"SENSING_LIFECYCLE_UNSAFE".equals(result(refused).get("code")),"Unsafe homing policy refuses before native home");
            check(count("native_effect_intent")==effects&&Objects.equals(prior,valve.getLastActuationValue()),"Home refusal has zero native home/valve effects");
            valve.setHomedActuation(ReferenceActuator.MachineStateActuation.LeaveAsIs);succeeded(execute("openpnp_set_machine_enabled",command("enabled",false)));return;
        }
        valve.setDisabledActuation(ReferenceActuator.MachineStateActuation.ActuateOn);
        JsonObject request=command("enabled",false);Map<String,Object> disabled=execute("openpnp_set_machine_enabled",request);succeeded(disabled);
        check(!machine.isEnabled()&&Boolean.TRUE.equals(valve.getLastActuationValue()),"Safe-stop stays available and actual native disable policy actuates valve");
        check(Boolean.TRUE.equals(sensing().get("lifecycle_fault")),"Unsafe disable publishes sticky global uncertainty");
        if(mode.equals("lifecycle-empty"))check(((List<?>)sensing().get("nozzles")).isEmpty(),"Global lifecycle fault does not fabricate nozzle observations");
        else check(((List<?>)sensing().get("nozzles")).stream().noneMatch(n->"observed_empty".equals(((Map<?,?>)n).get("state"))),"Unsafe disable cannot retain old empty authority");
        List<Map<String,Object>> rows=journal();int lifecycle=-1,effect=-1;Map<String,Object> retained=null;String eventInstance=null;
        for(int i=0;i<rows.size();i++){Map<String,Object> r=rows.get(i),body=(Map<String,Object>)r.get("payload");if(r.get("type").equals("vacuum_lifecycle_uncertain")){lifecycle=i;retained=body;eventInstance=(String)r.get("bridge_instance_id");}if(r.get("type").equals("native_effect_intent")&&disabled.get("operation_id").equals(body.get("operation_id")))effect=i;}
        check(lifecycle>=0&&effect>lifecycle,"Sticky uncertainty forced before native disable intent/effect");
        check(disabled.get("operation_id").equals(retained.get("operation_id"))&&request.get("request_id").getAsString().equals(retained.get("request_id")),"Actual disable operation/request binding retained");
        NativeVacuumJournal reducer=new NativeVacuumJournal();Runnable commit=reducer.prepare("vacuum_lifecycle_uncertain",retained,eventInstance);reducer.requireNoFault();check(!Boolean.TRUE.equals(reducer.snapshot().get("lifecycle_fault")),"Prepared lifecycle record has no pre-force state mutation");commit.run();
        try{reducer.requireNoFault();throw new AssertionError("Lifecycle fault lost");}catch(NativeVacuumJournal.Fault expected){check("VACUUM_OUTCOME_UNKNOWN".equals(expected.code),"Committed lifecycle fault blocks even empty nozzle map");}
        try{reducer.requireUnoccupied();throw new AssertionError("Global empty guard lost lifecycle fault");}catch(NativeVacuumJournal.Fault expected){check("VACUUM_OUTCOME_UNKNOWN".equals(expected.code),"Global empty guard refuses lifecycle fault without nozzle rows");}
        try{commit.run();throw new AssertionError("Reused lifecycle commit");}catch(IllegalStateException expected){checks++;}
        try{reducer.prepare("vacuum_lifecycle_uncertain",retained,eventInstance);throw new AssertionError("Duplicate lifecycle operation");}catch(java.io.IOException expected){checks++;}
        valve.setDisabledActuation(ReferenceActuator.MachineStateActuation.LeaveAsIs);
        long durable=count("vacuum_lifecycle_uncertain");check(disabled.get("operation_id").equals(call("openpnp_set_machine_enabled",request).get("operation_id"))&&durable==count("vacuum_lifecycle_uncertain"),"Duplicate disable request does not repeat policy or publication");
        refuse("VACUUM_OUTCOME_UNKNOWN","openpnp_set_machine_enabled",command("enabled",true));
        succeeded(execute("openpnp_set_machine_enabled",command("enabled",false)));
        check(Boolean.TRUE.equals(sensing().get("lifecycle_fault")),"Restored safe policies and repeated safe disable never clear the fault");
        call("openpnp_release_control_session",obj("session_id",session,"request_id",UUID.randomUUID().toString()));check(Boolean.TRUE.equals(sensing().get("lifecycle_fault")),"Ownership release never clears lifecycle uncertainty");
    }
    @SuppressWarnings("unchecked") static void legacyRequests(String mode)throws Exception {
        home();String requestId=" legacy \n\u03a9-"+mode;
        if(mode.equals("legacy-disable")){
            succeeded(verify("part_off"));((ReferenceActuator)nozzle().getVacuumActuator()).setDisabledActuation(ReferenceActuator.MachineStateActuation.ActuateOn);
            JsonObject request=command("enabled",false);request.addProperty("request_id",requestId);Map<String,Object> disabled=execute("openpnp_set_machine_enabled",request);succeeded(disabled);
            check(!machine.isEnabled()&&Boolean.TRUE.equals(sensing().get("lifecycle_fault")),"Legacy request ID permits safe disable with durable sticky policy fault");
            check(count("vacuum_lifecycle_uncertain")==1,"Exactly one legacy lifecycle record");Map<String,Object> record=journal().stream().filter(r->r.get("type").equals("vacuum_lifecycle_uncertain")).findFirst().get();
            check(requestId.equals(((Map<?,?>)record.get("payload")).get("request_id")),"Lifecycle request retains exact whitespace and Unicode");
            check(disabled.get("operation_id").equals(call("openpnp_set_machine_enabled",request).get("operation_id"))&&count("vacuum_lifecycle_uncertain")==1,"Legacy disable replay never repeats actuation");return;
        }
        succeeded(execute("openpnp_prepare_job",command("canonical_job",NativeVacuumOperationsTest.canonical(config.getPart("R0603-1K")))));String job=(String)call("openpnp_get_status",obj()).get("job_id");succeeded(execute("openpnp_validate_job",command("job_id",job)));
        JsonObject request=command("job_id",job);request.addProperty("request_id",requestId);String method=mode.equals("legacy-start")?"openpnp_start_job":"openpnp_step_job";
        Map<String,Object> state=mode.equals("legacy-start")?execute(method,request):boundary(call(method,request));String operation=(String)state.get("operation_id");
        if(mode.equals("legacy-step")){
            int attempts=0;while(!journal().stream().anyMatch(r->"check.returned".equals(((Map<?,?>)r.get("payload")).get("native_event"))&&"part_on".equals(((Map<?,?>)((Map<?,?>)r.get("payload")).get("data")).get("check_kind")))){
                check("paused".equals(state.get("state")),"Legacy step remains paused before picked-part check");if(++attempts>100)throw new AssertionError("No bounded native part-on check");state=boundary(call("openpnp_step_job",command("operation_id",operation)));
            }
            state=await(call("openpnp_abort_job",command("operation_id",operation)));check("aborted".equals(state.get("state")),"Legacy-origin step preserves request binding through native abort");
        }else succeeded(state);
        check(requestId.equals(state.get("request_id")),"Original legacy request retained on operation");long observations=count("vacuum_observation_intent");check(observations>0,"Actual native sensing occurred with legacy job identity");
        check(operation.equals(call(method,request).get("operation_id"))&&observations==count("vacuum_observation_intent"),"Legacy job request reattaches without repeat effects");
        Map<String,Object> sample=null;
        for(Map<String,Object> r:journal())if(r.get("type").toString().startsWith("vacuum_observation")){
            Map<String,Object> payload=(Map<String,Object>)r.get("payload"),context=(Map<String,Object>)payload.get("context");check(requestId.equals(context.get("request_id")),"Every job/cleanup native record retains exact admitted request");
            if(sample==null&&"check.before".equals(payload.get("native_event")))sample=payload;
        }
        check(sample!=null,"Real root check record supplies reducer grammar fixture");
        for(String scope:List.of("job","cleanup"))for(String value:List.of(" ","\n\u03a9", "x".repeat(1024))){
            Map<String,Object> p=NativeJournalJson.parseObject(JSON.toJson(sample));Map<String,Object> c=(Map<String,Object>)p.get("context");c.put("scope",scope);c.put("request_id",value);new NativeVacuumJournal().prepare("vacuum_observation_intent",p,(String)c.get("bridge_instance_id"));checks++;
        }
        for(Object value:Arrays.asList("","x".repeat(1025),42)){
            Map<String,Object> p=NativeJournalJson.parseObject(JSON.toJson(sample));Map<String,Object> c=(Map<String,Object>)p.get("context");c.put("request_id",value);try{new NativeVacuumJournal().prepare("vacuum_observation_intent",p,(String)c.get("bridge_instance_id"));throw new AssertionError("Invalid legacy request admitted");}catch(java.io.IOException expected){checks++;}
        }
        Map<String,Object> manual=NativeJournalJson.parseObject(JSON.toJson(sample));Map<String,Object> c=(Map<String,Object>)manual.get("context");c.put("scope","manual");c.put("job_context",null);
        try{new NativeVacuumJournal().prepare("vacuum_observation_intent",manual,(String)c.get("bridge_instance_id"));throw new AssertionError("New manual sensing UUID contract relaxed");}catch(java.io.IOException expected){checks++;}
    }
    static JsonObject settingsChange()throws Exception {
        ReferenceNozzle n=nozzle();ReferenceNozzleTip t=n.getNozzleTip();
        return obj("type","set_vacuum_sensing_settings","nozzle_id",n.getId(),"nozzle_tip_id",t.getId(),"vacuum_actuator_id",n.getVacuumActuator().getId(),"vacuum_sense_actuator_id",n.getVacuumSenseActuator().getId(),"reading_units","native-actuator-units","threshold_provenance","configured-thresholds","method_part_on","Absolute","method_part_off","Absolute","part_on_low",60,"part_on_high",80,"part_off_low",20,"part_off_high",30,"part_on_check_after_pick",true,"part_on_check_align",true,"part_on_check_before_place",true,"part_off_check_before_pick",true,"part_off_check_after_place",true,"part_off_probe_ms",0,"part_off_dwell_ms",0);
    }
    static void job(String mode)throws Exception {
        home();Map<String,Object> prepared=execute("openpnp_prepare_job",command("canonical_job",NativeVacuumOperationsTest.canonical(config.getPart("R0603-1K"))));succeeded(prepared);
        String id=(String)call("openpnp_get_status",obj()).get("job_id");Map<String,Object> validation=execute("openpnp_validate_job",command("job_id",id));
        if(mode.equals("unbound")){check("failed".equals(validation.get("state"))&&"SENSING_SOURCE_UNQUALIFIED".equals(result(validation).get("code"))&&count("native_action_intent")==0,"Unbound configured sensor refuses before feed/initialize");return;}
        succeeded(validation);check(Boolean.TRUE.equals(result(validation).get("valid")),"Native job validated");
        if(mode.equals("source-drift"))nozzle().setVacuumSenseActuator(nozzle().getVacuumActuator());
        if(mode.startsWith("job-")){java.lang.reflect.Field field=Bridge.class.getDeclaredField("journal");field.setAccessible(true);field.set(bridge,new Boundary((java.nio.channels.FileChannel)field.get(bridge),mode));}
        Map<String,Object> run=execute("openpnp_start_job",command("job_id",id));Files.writeString(root.resolve("job-operation.json"),JSON.toJson(run));
        if(mode.equals("source-drift"))check("SENSING_CONFIGURATION_CHANGED".equals(result(run).get("code"))&&!Boolean.TRUE.equals(run.get("initialized")),"Changed sensor rejected before native initialization");
        boolean pass=mode.equals("success")||mode.equals("missed-pick-retry");if(pass)succeeded(run);else check(!"succeeded".equals(run.get("state")),"Negative native job cannot succeed");
        List<Map<String,Object>> rows=journal();long feeds=rows.stream().filter(r->r.get("type").equals("native_action_outcome")&&"feed".equals(((Map<?,?>)r.get("payload")).get("kind"))).count();
        check(feeds==(mode.equals("missed-pick-retry")?2:mode.equals("source-drift")||mode.equals("job-action-lease")?0:1),"Exact native feed count: "+feeds);
        long complete=rows.stream().filter(r->r.get("type").equals("native_placement_checkpoint")&&"Job.Placement.Complete".equals(((Map<?,?>)r.get("payload")).get("hook"))).count();
        check(complete==(pass?1:0),"Exact native Complete count: "+complete);
        if(mode.equals("retained-after-place")||mode.equals("invalid-read")){check("outcome_unknown".equals(run.get("state")),"Sticky observation prevents a new action/native retry");refuse("VACUUM_OUTCOME_UNKNOWN","openpnp_home_machine",command());succeeded(execute("openpnp_set_machine_enabled",command("enabled",false)));}
        if(mode.startsWith("job-")){check("outcome_unknown".equals(run.get("state")),"Intent boundary failure settles uncertain");check(rows.stream().noneMatch(r->"read.returned".equals(((Map<?,?>)r.get("payload")).get("native_event"))),"Post-intent fencing prevents the native read");if(mode.equals("job-valve-lease"))check(rows.stream().noneMatch(r->"valve.returned".equals(((Map<?,?>)r.get("payload")).get("native_event"))&&Boolean.TRUE.equals(((Map<?,?>)((Map<?,?>)r.get("payload")).get("data")).get("enabled"))),"Post-intent expiry prevents valve-on");check(!Boolean.TRUE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Intent fence leaves valve off");}
        if(mode.equals("retained-after-place"))check(nozzle().getPart()==null,"Native release clears Part while sensor fence remains");
        for(Map<String,Object> row:rows)if(row.get("type").toString().startsWith("vacuum_observation")){
            Map<?,?> c=(Map<?,?>)((Map<?,?>)row.get("payload")).get("context");Map<?,?> j=(Map<?,?>)c.get("job_context");check(j!=null&&id.equals(j.get("job_id")),"Real Bridge job binding");check(run.get("job_revision").equals(j.get("job_revision"))&&run.get("board_load_revision").equals(j.get("board_load_revision")),"Native load and graph digest binding");
        }
    }
    /** Transparent FileChannel delegation; injected faults never fabricate a native record. */
    static final class Boundary extends java.nio.channels.FileChannel {
        final java.nio.channels.FileChannel delegate;final String mode;boolean armed=true,pending;int hits;
        Boundary(java.nio.channels.FileChannel d,String m){delegate=d;mode=m;}
        public int write(java.nio.ByteBuffer src)throws java.io.IOException {
            if(armed){java.nio.ByteBuffer c=src.asReadOnlyBuffer();byte[] bytes=new byte[c.remaining()];c.get(bytes);JsonObject row=new JsonParser().parse(new String(bytes,java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();
                JsonObject p=row.getAsJsonObject("payload");String e=p.has("native_event")?p.get("native_event").getAsString():"";
                boolean match=mode.equals("lifecycle-postforce")?row.get("type").getAsString().equals("native_effect_intent")&&"machine-enable".equals(p.get("kind").getAsString()):mode.equals("job-action-lease")?row.get("type").getAsString().equals("native_action_intent"):mode.equals("job-read-lease")||mode.equals("job-source-intent")?e.equals("read.before"):mode.equals("job-valve-lease")?e.equals("valve.before")&&p.getAsJsonObject("data").get("enabled").getAsBoolean():mode.equals("intent-write")?e.equals("read.before"):mode.equals("cleanup-write")?e.equals("valve.before")&&!p.getAsJsonObject("data").get("enabled").getAsBoolean():mode.equals("lease")?e.equals("valve.returned")&&p.getAsJsonObject("data").get("enabled").getAsBoolean():e.equals("read.returned");
                if(match){armed=false;pending=true;hits++;if(mode.endsWith("write"))throw new java.io.IOException("CONTROLLED_SENSING_WRITE_FAILURE");if(mode.equals("outcome-error"))throw new AssertionError("CONTROLLED_SENSING_WRITE_ERROR");}}
            return delegate.write(src);
        }
        public void force(boolean meta)throws java.io.IOException {delegate.force(meta);if(pending){pending=false;if(mode.equals("outcome-force"))throw new java.io.IOException("CONTROLLED_AFTER_REAL_FORCE");if(mode.equals("lifecycle-postforce"))try{((ReferenceActuator)nozzle().getVacuumActuator()).setEnabledActuation(ReferenceActuator.MachineStateActuation.ActuateOn);}catch(Exception e){throw new java.io.IOException(e);}if(mode.equals("job-source-intent"))try{nozzle().setVacuumSenseActuator(nozzle().getVacuumActuator());}catch(Exception e){throw new java.io.IOException(e);}if(mode.equals("lease")||mode.endsWith("-lease"))try{java.lang.reflect.Field f=Bridge.class.getDeclaredField("sessionDeadline");f.setAccessible(true);f.setLong(bridge,System.nanoTime()-1);}catch(Exception e){throw new java.io.IOException(e);}}}
        public int read(java.nio.ByteBuffer x)throws java.io.IOException{return delegate.read(x);}public long read(java.nio.ByteBuffer[]x,int o,int l)throws java.io.IOException{return delegate.read(x,o,l);}public int read(java.nio.ByteBuffer x,long p)throws java.io.IOException{return delegate.read(x,p);}
        public long write(java.nio.ByteBuffer[]x,int o,int l)throws java.io.IOException{return delegate.write(x,o,l);}public int write(java.nio.ByteBuffer x,long p)throws java.io.IOException{return delegate.write(x,p);}
        public long position()throws java.io.IOException{return delegate.position();}public java.nio.channels.FileChannel position(long p)throws java.io.IOException{delegate.position(p);return this;}public long size()throws java.io.IOException{return delegate.size();}public java.nio.channels.FileChannel truncate(long s)throws java.io.IOException{delegate.truncate(s);return this;}
        public long transferTo(long p,long n,java.nio.channels.WritableByteChannel t)throws java.io.IOException{return delegate.transferTo(p,n,t);}public long transferFrom(java.nio.channels.ReadableByteChannel x,long p,long n)throws java.io.IOException{return delegate.transferFrom(x,p,n);}
        public java.nio.MappedByteBuffer map(MapMode m,long p,long n)throws java.io.IOException{return delegate.map(m,p,n);}public java.nio.channels.FileLock lock(long p,long n,boolean s)throws java.io.IOException{return delegate.lock(p,n,s);}public java.nio.channels.FileLock tryLock(long p,long n,boolean s)throws java.io.IOException{return delegate.tryLock(p,n,s);}protected void implCloseChannel()throws java.io.IOException{delegate.close();}
    }
    static void failure(String mode)throws Exception {
        home();java.lang.reflect.Field field=Bridge.class.getDeclaredField("journal");field.setAccessible(true);Boundary boundary=new Boundary((java.nio.channels.FileChannel)field.get(bridge),mode);field.set(bridge,boundary);
        JsonObject p=command("nozzle_id",nozzle().getId(),"state","part_off");Map<String,Object> admitted=call("openpnp_verify_part_state",p);String id=(String)admitted.get("operation_id");
        Map<String,Object> op=null;long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(20);
        while(true){op=call("openpnp_get_operation",obj("operation_id",id));if("outcome_unknown".equals(op.get("state"))||op.containsKey("publication_fault"))break;if(System.nanoTime()>until)throw new AssertionError("Faulted native body not observed: "+op);Thread.sleep(10);}
        while(machine.isBusy()){if(System.nanoTime()>until)throw new AssertionError("Native body stranded");Thread.sleep(5);}
        Files.writeString(root.resolve("fault-operation.json"),JSON.toJson(op));check(boundary.hits==1,"Exactly one actual journal boundary fault");
        check(Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Native probe finally closes valve after boundary failure");
        List<Map<String,Object>> rows=journal();check(rows.stream().noneMatch(r->"check.returned".equals(((Map<?,?>)r.get("payload")).get("native_event"))),"No false successful check after failure");
        Map<String,Object> status=call("openpnp_get_status",obj());Map<?,?> ledger=(Map<?,?>)status.get("vacuum_sensing_journal");check(!((List<?>)ledger.get("pending")).isEmpty()||((List<?>)ledger.get("nozzles")).stream().anyMatch(n->Boolean.TRUE.equals(((Map<?,?>)n).get("sticky_fault"))),"Sticky or pending evidence retained");
        if(mode.equals("lease")){check("outcome_unknown".equals(op.get("state")),"Lease expiry settles unknown after opened probe");check(rows.stream().noneMatch(r->"read.returned".equals(((Map<?,?>)r.get("payload")).get("native_event"))),"Expiry prevents later native read");}
        else {check(Boolean.TRUE.equals(status.get("journal_fault")),"Journal failure retained");check(op.containsKey("publication_fault"),"Non-durable final result is explicitly ownership-retained");try{bridge.close();throw new AssertionError("Close released unresolved native publication");}catch(Bridge.Fault blocked){check("BUSY".equals(blocked.code),"Close refuses unresolved native publication");}
            try{bridge.recordGuiUnknownExit();}catch(Exception expected){} // explicit owned test shutdown; preserves journal, does not resolve fault
        }
    }

    static Map<String,Object> boundary(Map<String,Object> accepted)throws Exception {
        long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(35);while(true){Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",accepted.get("operation_id")));if(Set.of("paused","succeeded","failed","outcome_unknown","aborted").contains(op.get("state")))return op;if(System.nanoTime()>until)throw new AssertionError("Missing native step boundary");Thread.sleep(5);}
    }
    static void stepping(String mode)throws Exception {
        home();succeeded(execute("openpnp_prepare_job",command("canonical_job",NativeVacuumOperationsTest.canonical(config.getPart("R0603-1K")))));String job=(String)call("openpnp_get_status",obj()).get("job_id");succeeded(execute("openpnp_validate_job",command("job_id",job)));
        Map<String,Object> paused=boundary(call("openpnp_step_job",command("job_id",job)));String operation=(String)paused.get("operation_id");int attempts=0;
        while(journal().stream().noneMatch(r->r.get("type").equals("native_action_outcome")&&"feed".equals(((Map<?,?>)r.get("payload")).get("kind")))){
            check("paused".equals(paused.get("state")),"Native steps remain explicitly paused before selected feed");if(++attempts>100)throw new AssertionError("No bounded native feed");paused=boundary(call("openpnp_step_job",command("operation_id",operation)));
        }
        check("paused".equals(paused.get("state")),"Actual native feed returns at a paused next boundary");
        if(mode.equals("step-drift"))nozzle().setVacuumSenseActuator(nozzle().getVacuumActuator());
        JsonObject request=command("operation_id",operation);Map<String,Object> ended=await(call(mode.equals("step-abort")?"openpnp_abort_job":"openpnp_resume_job",request));Files.writeString(root.resolve("job-operation.json"),JSON.toJson(ended));
        if(mode.equals("step-resume"))succeeded(ended);else if(mode.equals("step-abort"))check("aborted".equals(ended.get("state")),"Paused native job aborts through retained source guard");else check("failed".equals(ended.get("state"))&&"SENSING_CONFIGURATION_CHANGED".equals(result(ended).get("code")),"Paused source drift refuses before another native step");
        long feeds=journal().stream().filter(r->r.get("type").equals("native_action_outcome")&&"feed".equals(((Map<?,?>)r.get("payload")).get("kind"))).count();check(feeds==1,"Step/resume/abort preserves exact single feed");
        check(operation.equals(ended.get("operation_id")),"Retained operation identity spans steps and disposition");long effects=count("vacuum_observation_intent");check(operation.equals(call(mode.equals("step-abort")?"openpnp_abort_job":"openpnp_resume_job",request).get("operation_id"))&&effects==count("vacuum_observation_intent"),"Original resume/abort request reattaches without extra sensing");
    }

    public static void main(String[]args)throws Exception {
        samples=Path.of(args[0]);String mode=args.length>1?args[1]:"manual";root=args.length>2?Path.of(args[2]):Files.createTempDirectory("vacuum-bridge-");Files.createDirectories(root);Throwable failure=null;
        try{setup(mode.startsWith("legacy-")||mode.startsWith("lifecycle-")||mode.startsWith("job-")||mode.startsWith("step-")?"success":Set.of("manual","unbound","source-drift","intent-write","outcome-write","outcome-force","cleanup-write","outcome-error","lease").contains(mode)?"success":mode,!mode.equals("unbound"));if(mode.equals("manual"))manual();else if(mode.startsWith("legacy-"))legacyRequests(mode);else if(mode.startsWith("lifecycle-"))lifecycle(mode);else if(mode.startsWith("step-"))stepping(mode);else if(Set.of("intent-write","outcome-write","outcome-force","cleanup-write","outcome-error","lease").contains(mode))failure(mode);else job(mode);}
        catch(Throwable e){failure=e;e.printStackTrace();}
        finally{if(bridge!=null)try{bridge.close();}catch(Throwable e){if(failure==null)failure=e;else failure.addSuppressed(e);}if(machine!=null)machine.close();Map<String,Object> r=Bridge.map("passed",failure==null,"mode",mode,"checks",checks,"root",root.toString(),"failure",failure==null?null:failure.toString(),"scope","actual Bridge over explicit process-owned native simulator fixture","hardware_qualified",false);Files.writeString(root.resolve("result.json"),JSON.toJson(r));System.out.println("VACUUM_BRIDGE_RESULT "+JSON.toJson(r));}
        System.exit(failure==null?0:1);
    }
}
