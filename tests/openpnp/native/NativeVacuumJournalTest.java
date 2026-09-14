/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Strict synthetic lifecycle tests with actual pinned Gson. No native machine or sensor is opened. */
public final class NativeVacuumJournalTest {
    private static int checks,refusals;
    private static final String INSTANCE=id(1),MACHINE=id(2),OP=id(3),REQUEST=id(4),SOURCE=id(5);
    @FunctionalInterface private interface Checked {void run()throws Exception;}
    @FunctionalInterface private interface Change {void run(Map<String,Object> p)throws Exception;}
    private static String id(int n){return UUID.nameUUIDFromBytes(("vacuum77-"+n).getBytes(StandardCharsets.UTF_8)).toString();}
    private static Map<String,Object> m(Object...v){Map<String,Object> p=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)p.put((String)v[i],v[i+1]);return p;}
    @SuppressWarnings("unchecked")private static Map<String,Object> o(Map<String,Object> p,String k){return(Map<String,Object>)p.get(k);}
    private static Map<String,Object> copy(Map<String,Object> p){return NativeJournalJson.copy(p);}
    private static void yes(boolean value,String label){checks++;if(!value)throw new AssertionError(label);}
    private static void refused(String label,Checked call)throws Exception {try{call.run();throw new AssertionError("Accepted "+label);}catch(IOException expected){checks++;refusals++;}}
    private static void blocked(String label,Checked call)throws Exception {try{call.run();throw new AssertionError("Allowed "+label);}catch(NativeVacuumJournal.Fault expected){checks++;}}
    private static void fence(String label,Checked call)throws Exception {try{call.run();throw new AssertionError("No fence "+label);}catch(NativeVacuumJournal.Fence expected){checks++;}}
    private static Map<String,Object> context(){return m("operation_id",OP,"request_id",REQUEST,"machine_id",MACHINE,"bridge_instance_id",INSTANCE,"config_revision","cfg-7","nozzle_id","N1","scope","manual","job_context",null);}
    private static Map<String,Object> source(){return m("origin","controlled-simulator","profile","controlled-native-vacuum-v1","source_id",SOURCE,"process_id",1234,"simulation_only",true,"hardware_qualified",false,"fixture_id","closed-synthetic-journal","scenario_id","normal","units","native-actuator-units");}
    private static Map<String,Object> binding()throws Exception{return NativeVacuumJournal.binding(context(),data(9,null,null));}
    private static Map<String,Object> data(int n,Integer parent,String check){return m("api_version",1,"observation_id",id(n),"parent_observation_id",parent==null?null:id(parent),"native_stage","direct","check_kind",check,"nozzle_tip_id","NT1","sensor_id","A1","source",source());}
    private static Map<String,Object> event(String event,int n,Integer parent,String check){Map<String,Object>d=data(n,parent,check);if(event.startsWith("valve.")){d.put("enabled",true);d.put("reason","part_off_probe");d.put("cleanup_attempt",false);}return m("schema_version",1,"profile",NativeVacuumJournal.PROFILE,"context",context(),"native_event",event,"data",d);}
    private static Map<String,Object> read(int n,Integer parent,String check){Map<String,Object>p=event("read.returned",n,parent,check);o(p,"data").putAll(m("raw","75.25","raw_length",5,"value",75.25));return p;}
    private static Map<String,Object> check(int n,String kind,boolean value){Map<String,Object>p=event("check.returned",n,null,kind);o(p,"data").put("verdict",value);return p;}
    private static Map<String,Object> fail(int n,Integer parent,String check){Map<String,Object>p=event("read.failed",n,parent,check);o(p,"data").putAll(m("failure_code","SENSOR_VALUE_NONFINITE","failure_type","org.openpnp.machine.reference.VacuumSensing$SensorValueException","raw","NaN","raw_length",3));return p;}
    private static void append(NativeVacuumJournal j,Map<String,Object> p)throws Exception {String type=((String)p.get("native_event")).endsWith(".before")?"vacuum_observation_intent":"vacuum_observation_outcome";j.prepare(type,p,INSTANCE).run();}
    private static void observe(NativeVacuumJournal j,Map<String,Object> p,NativeVacuumJournal.Sink sink){j.observe((String)p.get("native_event"),"N1",o(p,"data"),o(p,"context"),sink);}
    private static NativeVacuumJournal.Sink sink(NativeVacuumJournal j){return(type,p)->j.prepare(type,p,INSTANCE).run();}
    @SuppressWarnings("unchecked")private static Map<String,Object> nozzle(NativeVacuumJournal j){return(Map<String,Object>)((List<?>)j.snapshot().get("nozzles")).get(0);}
    private static NativeVacuumJournal prefix()throws Exception {NativeVacuumJournal j=new NativeVacuumJournal();append(j,event("read.before",10,null,null));return j;}
    private static void badBefore(String label,Change change)throws Exception {Map<String,Object>p=event("read.before",10,null,null);change.run(p);refused(label,()->append(new NativeVacuumJournal(),p));}
    private static void badAfter(String label,Change change)throws Exception {Map<String,Object>p=read(10,null,null);change.run(p);refused(label,()->append(prefix(),p));}
    private static Map<String,Object> valve(String phase,int n,int parent,boolean enabled){Map<String,Object>p=event("valve."+phase,n,parent,"part_off");o(p,"data").put("enabled",enabled);o(p,"data").put("cleanup_attempt",!enabled);return p;}
    private static void pulse(NativeVacuumJournal j,int n,int parent,boolean enabled)throws Exception {append(j,valve("before",n,parent,enabled));append(j,valve("returned",n,parent,enabled));}
    private static void checked(NativeVacuumJournal j,int n,String kind,boolean verdict)throws Exception {append(j,event("check.before",n,null,kind));if(kind.equals("part_off"))pulse(j,n+2,n,true);append(j,event("read.before",n+1,n,kind));append(j,read(n+1,n,kind));if(kind.equals("part_off"))pulse(j,n+3,n,false);append(j,check(n,kind,verdict));}

    public static void main(String[] args)throws Exception {
        NativeVacuumJournal j=new NativeVacuumJournal();Map<String,Object> before=event("read.before",10,null,null);
        j.requireUnoccupied();checks++;blocked("unobserved selected nozzle",()->j.requireUnoccupied("N1"));
        Runnable commit=j.prepare("vacuum_observation_intent",before,INSTANCE);
        yes(((List<?>)j.snapshot().get("nozzles")).isEmpty(),"prepare does not publish");
        o(before,"context").put("nozzle_id","changed-after-prepare");commit.run();
        yes("N1".equals(nozzle(j).get("nozzle_id")),"prepared DTO is detached");
        yes("unknown".equals(nozzle(j).get("state")),"unfinished read remains unknown");blocked("pending read",j::requireNoFault);
        try{commit.run();throw new AssertionError("commit reused");}catch(IllegalStateException expected){checks++;}
        append(j,read(10,null,null));j.requireNoFault();yes("unobserved".equals(nozzle(j).get("state")),"raw read does not prove occupancy");
        blocked("passive samples do not grant empty authority",()->j.requireUnoccupied("N1"));
        try{o(nozzle(j),"last_record").put("mutate",true);throw new AssertionError("mutable snapshot");}catch(UnsupportedOperationException expected){checks++;}
        NativeVacuumJournal stale=new NativeVacuumJournal();Runnable a=stale.prepare("vacuum_observation_intent",event("read.before",20,null,null),INSTANCE);Runnable b=stale.prepare("vacuum_observation_intent",event("read.before",21,null,null),INSTANCE);a.run();try{b.run();throw new AssertionError("stale commit");}catch(IllegalStateException expected){checks++;}
        refused("duplicate completed ID",()->append(j,event("read.before",10,null,null)));
        refused("outcome before intent",()->append(new NativeVacuumJournal(),read(10,null,null)));
        refused("unknown event",()->new NativeVacuumJournal().prepare("vacuum_clear_occupancy",event("read.before",10,null,null),INSTANCE));
        refused("event phase mismatch",()->new NativeVacuumJournal().prepare("vacuum_observation_outcome",event("read.before",10,null,null),INSTANCE));
        refused("foreign envelope",()->new NativeVacuumJournal().prepare("vacuum_observation_intent",event("read.before",10,null,null),id(99)));
        badBefore("unknown wrapper field",p->p.put("clear_occupancy",true));badBefore("missing context",p->p.remove("context"));
        badBefore("unknown context field",p->o(p,"context").put("execution_authority",true));badBefore("wrong config revision",p->o(p,"context").put("config_revision","cfg-07"));
        badBefore("revision over safe integer",p->o(p,"context").put("config_revision","cfg-9007199254740992"));
        badBefore("abbreviated UUID",p->o(p,"context").put("operation_id","1-1-1-1-1"));badBefore("uppercase UUID",p->o(p,"data").put("observation_id",id(10).toUpperCase(Locale.ROOT)));
        badBefore("request aliases operation",p->o(p,"context").put("request_id",OP));badBefore("fractional schema",p->p.put("schema_version",new BigDecimal("1.000000000000000001")));
        badBefore("fractional API",p->o(p,"data").put("api_version",new BigDecimal("1.00000000000000001")));badBefore("missing sensor",p->o(p,"data").put("sensor_id",null));
        badBefore("unknown source field",p->o(o(p,"data"),"source").put("physical_verified",true));badBefore("source claims hardware",p->o(o(p,"data"),"source").put("hardware_qualified",true));
        badBefore("fractional process ID",p->o(o(p,"data"),"source").put("process_id",new BigDecimal("1234.000000000000001")));badBefore("foreign source",p->o(o(p,"data"),"source").put("origin","native-driver"));
        badBefore("unknown native stage",p->o(p,"data").put("native_stage","cleanup"));badBefore("standalone read claims check",p->o(p,"data").put("check_kind","part_on"));
        badBefore("oversized native ID",p->o(p,"data").put("sensor_id","A".repeat(129)));badBefore("malformed Unicode",p->o(p,"data").put("sensor_id","\ud800"));
        badBefore("parent absent",p->o(p,"data").put("parent_observation_id",id(42)));badBefore("self parent",p->o(p,"data").put("parent_observation_id",id(10)));
        badBefore("job context absent",p->o(p,"context").put("scope","job"));badBefore("manual context invents job",p->o(p,"context").put("job_context",m()));
        badAfter("changed operation",p->o(p,"context").put("operation_id",id(88)));badAfter("changed config",p->o(p,"context").put("config_revision","cfg-8"));badAfter("changed nozzle",p->o(p,"context").put("nozzle_id","N2"));
        badAfter("changed sensor",p->o(p,"data").put("sensor_id","A2"));badAfter("changed source",p->o(o(p,"data"),"source").put("scenario_id","other"));badAfter("wrong raw length",p->o(p,"data").put("raw_length",6));
        badAfter("rounded raw length",p->o(p,"data").put("raw_length",new BigDecimal("5.000000000000000001")));badAfter("contradictory parsed sample",p->o(p,"data").put("value",76.25));badAfter("near-identical invented value",p->o(p,"data").put("value",new BigDecimal("75.250000000000000001")));
        badAfter("NaN",p->o(p,"data").putAll(m("raw","NaN","raw_length",3,"value",Double.NaN)));badAfter("nonfinite raw",p->o(p,"data").putAll(m("raw","Infinity","raw_length",8,"value",1)));
        badAfter("nonASCII sample",p->o(p,"data").putAll(m("raw","é","raw_length",1)));badAfter("oversized raw",p->o(p,"data").putAll(m("raw","1".repeat(129),"raw_length",129)));
        badAfter("spurious truncation",p->o(p,"data").put("raw_truncated",true));badAfter("wrong outcome kind",p->p.put("native_event","check.returned"));

        NativeVacuumJournal nested=new NativeVacuumJournal();append(nested,event("check.before",100,null,"part_off"));pulse(nested,103,100,true);append(nested,event("read.before",101,100,"part_off"));
        refused("parent completes with unresolved child",()->append(nested,check(100,"part_off",true)));
        refused("overlapping sibling",()->append(nested,event("read.before",102,100,"part_off")));
        append(nested,read(101,100,"part_off"));refused("part-off lacks valve cleanup",()->append(nested,check(100,"part_off",true)));pulse(nested,104,100,false);append(nested,check(100,"part_off",true));nested.requireUnoccupied("N1",binding());yes("observed_empty".equals(nozzle(nested).get("state")),"bounded successful part-off evidence");blocked("current binding mandatory",()->nested.requireUnoccupied("N1"));
        NativeVacuumJournal cycle=new NativeVacuumJournal();checked(cycle,200,"part_on",false);yes("not_detected".equals(nozzle(cycle).get("state")),"known missed pick is not asserted empty");cycle.requireNoFault();blocked("not detected is not empty",cycle::requireUnoccupied);
        checked(cycle,210,"part_on",true);yes("held".equals(nozzle(cycle).get("state")),"native configured retry can succeed");cycle.requireNoFault();blocked("held nozzle",cycle::requireUnoccupied);
        checked(cycle,220,"part_off",true);cycle.requireUnoccupied("N1",binding());yes("observed_empty".equals(nozzle(cycle).get("state")),"successful normal release check");
        NativeVacuumJournal retained=new NativeVacuumJournal();checked(retained,300,"part_off",false);yes("retained".equals(nozzle(retained).get("state")),"failed release retains material despite native Part null");blocked("retained nozzle",retained::requireNoFault);
        Map<String,Object> changedCfg=event("read.before",310,null,null);o(changedCfg,"context").put("config_revision","cfg-8");refused("settings cannot clear retained state",()->append(retained,changedCfg));
        refused("new UUID cannot retry retained check",()->append(retained,event("check.before",320,null,"part_off")));
        NativeVacuumJournal failed=new NativeVacuumJournal();append(failed,event("check.before",400,null,"part_on"));append(failed,event("read.before",401,400,"part_on"));
        fence("failed read cannot enter native Exception retry",()->observe(failed,fail(401,400,"part_on"),sink(failed)));
        yes("unknown".equals(nozzle(failed).get("state")),"failed child leaves pending parent unknown");blocked("failed read sticky",failed::requireNoFault);
        NativeVacuumJournal beforeFailure=new NativeVacuumJournal();fence("journal before force",()->observe(beforeFailure,event("read.before",500,null,null),(type,p)->{throw new IOException("injected before append");}));
        yes(((Number)beforeFailure.snapshot().get("observations")).intValue()==0,"no durable intent on before-write failure");blocked("publication overlay sticky",beforeFailure::requireUnoccupied);
        NativeVacuumJournal sinkFence=new NativeVacuumJournal();fence("sink-thrown Fence",()->observe(sinkFence,event("read.before",509,null,null),(type,p)->{throw new NativeVacuumJournal.Fence("TEST","sink failed",null);}));blocked("sink Fence invalidates first read",sinkFence::requireNoFault);
        NativeVacuumJournal afterFailure=new NativeVacuumJournal();observe(afterFailure,event("read.before",501,null,null),sink(afterFailure));fence("journal after native read",()->observe(afterFailure,read(501,null,null),(type,p)->{throw new IOException("injected outcome append failure");}));
        yes(((List<?>)afterFailure.snapshot().get("pending")).size()==1,"outcome publication failure preserves pending intent");blocked("failed outcome retry",afterFailure::requireNoFault);
        NativeVacuumJournal committedThenFailure=new NativeVacuumJournal();fence("sink force success then uncertain return",()->observe(committedThenFailure,event("read.before",502,null,null),(type,p)->{sink(committedThenFailure).append(type,p);throw new IOException("injected after commit");}));
        yes(((List<?>)committedThenFailure.snapshot().get("pending")).size()==1,"committed intent remains durable");blocked("postcommit uncertainty",committedThenFailure::requireNoFault);

        NativeVacuumJournal replay=new NativeVacuumJournal();Map<String,Object> rb=event("read.before",600,null,null);replay.recover("vacuum_observation_intent",rb,INSTANCE);replay.verifyMachineIdentity(MACHINE);refused("foreign durable machine",()->replay.verifyMachineIdentity(id(999)));blocked("interrupted historical read",replay::requireUnoccupied);
        yes(Boolean.FALSE.equals(replay.snapshot().get("execution_authority_restored")),"recovery never restores authority");
        NativeVacuumJournal replaySuccess=new NativeVacuumJournal();Map<String,Object> cp=event("check.before",601,null,"part_off");replaySuccess.recover("vacuum_observation_intent",cp,INSTANCE);pulse(replaySuccess,603,601,true);append(replaySuccess,event("read.before",602,601,"part_off"));append(replaySuccess,read(602,601,"part_off"));pulse(replaySuccess,604,601,false);replaySuccess.recover("vacuum_observation_outcome",check(601,"part_off",true),INSTANCE);blocked("even successful historical check cannot authorize empty",replaySuccess::requireUnoccupied);
        String raw=new Gson().toJson(m("type","vacuum_observation_intent","payload",event("read.before",700,null,null),"sequence",1,"bridge_instance_id",INSTANCE));NativeVacuumJournal.validateRawRecord(raw);checks++;
        refused("raw duplicate keys",()->NativeVacuumJournal.validateRawRecord("{\"x\":1,\"x\":2}"));refused("nested duplicate keys",()->NativeVacuumJournal.validateRawRecord("{\"x\":{\"a\":1,\"a\":2}}"));
        refused("raw trailing value",()->NativeVacuumJournal.validateRawRecord("{}{}"));refused("raw nonfinite",()->NativeVacuumJournal.validateRawRecord("{\"x\":NaN}"));refused("raw oversized",()->NativeVacuumJournal.validateRawRecord(" ".repeat(20481)));refused("raw depth",()->NativeVacuumJournal.validateRawRecord("[".repeat(34)+"0"+"]".repeat(34)));
        NativeVacuumJournal exact=new NativeVacuumJournal();append(exact,event("read.before",710,null,null));Map<String,Object> minusZero=read(710,null,null);o(minusZero,"data").putAll(m("raw","-0.0","raw_length",4,"value",-0.0));append(exact,minusZero);checks++;
        NativeVacuumJournal capacity=new NativeVacuumJournal();for(int n=0;n<64;n++){Map<String,Object>p=event("read.before",800+n,null,null);o(p,"context").put("nozzle_id","N"+n);append(capacity,p);}Map<String,Object>extra=event("read.before",900,null,null);o(extra,"context").put("nozzle_id","N64");refused("bounded nozzle count",()->append(capacity,extra));
        Map<String,Object> job=event("read.before",910,null,null);o(job,"context").putAll(m("scope","job","job_context",m("job_id",id(80),"job_revision","a".repeat(64),"board_load_revision","load-2","material_setup_revision","material-3","lineage_id",id(81),"lineage_revision",2)));append(new NativeVacuumJournal(),job);checks++;
        Map<String,Object> malformedJob=copy(job);o(o(malformedJob,"context"),"job_context").put("lineage_revision",new BigDecimal("2.000000000000000001"));refused("fractional lineage revision",()->append(new NativeVacuumJournal(),malformedJob));
        NativeVacuumJournal drift=new NativeVacuumJournal();checked(drift,1000,"part_off",true);Map<String,Object> oldBinding=binding();drift.requireUnoccupied("N1",oldBinding);checks++;
        Map<String,Object> changedBefore=event("read.before",1010,null,null),changedAfter=read(1010,null,null);
        for(Map<String,Object> p:List.of(changedBefore,changedAfter)){o(p,"context").put("config_revision","cfg-8");o(p,"data").put("nozzle_tip_id","NT2");o(p,"data").put("sensor_id","A2");o(o(p,"data"),"source").put("source_id",id(100));}
        Map<String,Object> newBinding=NativeVacuumJournal.binding(o(changedBefore,"context"),o(changedBefore,"data"));append(drift,changedBefore);append(drift,changedAfter);
        blocked("off A then passive B cannot grant empty",()->drift.requireUnoccupied("N1",newBinding));yes("unobserved".equals(nozzle(drift).get("state")),"binding drift invalidates empty state");yes(oldBinding.equals(nozzle(drift).get("last_successful_check_binding")),"passive sample preserves successful-check binding");
        NativeVacuumJournal driftWithoutRead=new NativeVacuumJournal();checked(driftWithoutRead,1100,"part_off",true);blocked("current B without new read differs",()->driftWithoutRead.requireUnoccupied("N1",newBinding));blocked("old binding cannot revive invalidated empty",()->driftWithoutRead.requireUnoccupied("N1",oldBinding));
        NativeVacuumJournal instanceDrift=new NativeVacuumJournal();checked(instanceDrift,1200,"part_off",true);Map<String,Object> otherInstance=copy(binding());otherInstance.put("bridge_instance_id",id(120));blocked("new current instance",()->instanceDrift.requireUnoccupied("N1",otherInstance));
        blocked("replay current binding never restores authority",()->replaySuccess.requireUnoccupied("N1",binding()));
        System.out.println("NATIVE_VACUUM_JOURNAL_PASS "+checks+" checks "+refusals+" refusals; synthetic pinned-Gson reducer only; no machine/sensor IO");
    }
}
