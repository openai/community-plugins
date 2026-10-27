/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.*;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.openpnp.machine.reference.ReferenceNozzle;

/** Ordered recorded sensing facts. Neither replay nor a null native Part grants empty-nozzle authority. */
final class NativeVacuumJournal {
    static final String PROFILE="native-vacuum-observation-v1";
    static final int MAX_OBSERVATIONS=200000,MAX_NOZZLES=64,MAX_ACTIVE=256,MAX_RECORD_BYTES=16384;
    private static final Gson JSON=new GsonBuilder().serializeNulls().create();
    private static final Set<String> EVENTS=Set.of("read.before","read.returned","read.failed","check.before","check.returned","check.failed","valve.before","valve.returned","valve.failed");
    private static final Set<String> STAGES=Set.of("after_pick","align","before_place","before_pick","after_place","direct");
    private static final List<String> COMMON=List.of("api_version","observation_id","parent_observation_id","native_stage","check_kind","nozzle_tip_id","sensor_id","source");
    @FunctionalInterface interface Sink {void append(String type,Map<String,Object> payload)throws Exception;}
    static final class Fence extends Error {
        private static final long serialVersionUID=1L;
        final String code;
        Fence(String code,String message,Throwable cause){super(message,cause);this.code=code;}
    }
    static final class Fault extends Exception {
        private static final long serialVersionUID=1L;
        final String code;
        Fault(String code,String message){super(message);this.code=code;}
    }
    private static final class Active {
        final Map<String,Object> payload,context,data;
        final String kind,nozzle;
        int reads;
        boolean valveOn,valveOff,childFailed;
        Active(Map<String,Object> p)throws IOException {payload=p;context=object(p,"context");data=object(p,"data");kind=text(p,"native_event",32).split("\\.")[0];nozzle=text(context,"nozzle_id",128);}
        Active(Active a){payload=a.payload;context=a.context;data=a.data;kind=a.kind;nozzle=a.nozzle;reads=a.reads;valveOn=a.valveOn;valveOff=a.valveOff;childFailed=a.childFailed;}
    }
    private static final class NozzleState {
        String occupancy="unobserved",lastObservation,lastInstance,reason;
        boolean sticky;
        long observations,returned,failed;
        Map<String,Object> last,emptyBinding,lastSuccessfulCheckBinding;
        NozzleState copy(){NozzleState n=new NozzleState();n.occupancy=occupancy;n.lastObservation=lastObservation;n.lastInstance=lastInstance;n.reason=reason;n.sticky=sticky;n.observations=observations;n.returned=returned;n.failed=failed;n.last=last;n.emptyBinding=emptyBinding;n.lastSuccessfulCheckBinding=lastSuccessfulCheckBinding;return n;}
    }
    private final Map<String,Active> active=new LinkedHashMap<>();
    private final Set<String> seen=new HashSet<>();
    private final Map<String,NozzleState> nozzles=new LinkedHashMap<>();
    private String machineId;
    private long generation;
    private boolean recovered;

    static boolean matches(String type){return type!=null&&type.startsWith("vacuum_");}

    /** The caller serializes append/force/commit. A stale or reused commit is rejected. */
    synchronized Runnable prepare(String type,Map<String,Object> payload,String envelopeInstance)throws IOException {
        if(!matches(type))return ()->{};
        if(!Set.of("vacuum_observation_intent","vacuum_observation_outcome").contains(type))throw bad("Unknown event type");
        Map<String,Object> p=bounded(payload);keys(p,"schema_version","profile","context","native_event","data");
        integer(p,"schema_version",1,1);if(!PROFILE.equals(p.get("profile")))throw bad("Unknown profile");
        Map<String,Object> c=object(p,"context");validateContext(c);String instance=uuid(c,"bridge_instance_id"),machine=uuid(c,"machine_id");
        if(!instance.equals(uuidValue(envelopeInstance))||machineId!=null&&!machineId.equals(machine))throw bad("Foreign envelope or machine identity");
        String event=text(p,"native_event",32);if(!EVENTS.contains(event))throw bad("Unknown native event");
        boolean before=event.endsWith(".before"),failed=event.endsWith(".failed");
        if(before!=type.equals("vacuum_observation_intent"))throw bad("Event type/phase disagreement");
        Map<String,Object> d=object(p,"data");validateData(event,d);
        String id=uuid(d,"observation_id"),nozzle=text(c,"nozzle_id",128),kind=event.split("\\.")[0];
        NozzleState prior=nozzles.get(nozzle),next=prior==null?new NozzleState():prior.copy();
        Map<String,Object> currentBinding=binding(c,d);
        if("observed_empty".equals(next.occupancy)&&!currentBinding.equals(next.emptyBinding)){next.occupancy="unobserved";next.reason="empty-binding-changed";}
        Active pending=active.get(id),admitted,parentUpdate=null;
        if(before){
            if(seen.contains(id)||seen.size()>=MAX_OBSERVATIONS||active.size()>=MAX_ACTIVE||prior==null&&nozzles.size()>=MAX_NOZZLES)throw bad("Duplicate observation or retained limit");
            validateParent(c,d,kind);
            // A failed source/check cannot be retried under a new UUID. Mandatory valve-off is the sole exception.
            if(next.sticky&&!cleanup(event,d))throw bad("Nozzle retains unresolved or retained-material evidence");
            admitted=new Active(p);next.observations++;
        }else{
            if(pending==null||!pending.kind.equals(kind)||!pending.context.equals(c))throw bad("Outcome lacks its exact intent/context");
            for(String key:COMMON)if(!Objects.equals(pending.data.get(key),d.get(key)))throw bad("Native outcome identity changed: "+key);
            if(kind.equals("valve"))for(String key:List.of("enabled","reason","cleanup_attempt"))if(!Objects.equals(pending.data.get(key),d.get(key)))throw bad("Valve outcome differs");
            for(Active child:active.values())if(id.equals(child.data.get("parent_observation_id")))throw bad("Parent completed before child outcome");
            if(!failed&&kind.equals("check")&&(pending.reads<1||pending.childFailed||"part_off".equals(d.get("check_kind"))&&(!pending.valveOn||!pending.valveOff)))throw bad("Successful check lacks completed native read/probe lifecycle");
            Object parent=d.get("parent_observation_id");
            if(parent!=null){parentUpdate=new Active(active.get(parent));if(failed)parentUpdate.childFailed=true;
                else if(kind.equals("read"))parentUpdate.reads++;
                else if(kind.equals("valve")){if(Boolean.TRUE.equals(d.get("enabled")))parentUpdate.valveOn=true;else parentUpdate.valveOff=true;}}
            admitted=null;
            if(failed){next.failed++;next.sticky=true;next.occupancy="unknown";next.reason="native-observation-failed";}
            else {
                next.returned++;
                if(kind.equals("check")&&!next.sticky){
                    boolean verdict=(Boolean)d.get("verdict");
                    if(verdict)next.lastSuccessfulCheckBinding=currentBinding;
                    if("part_on".equals(d.get("check_kind")))next.occupancy=verdict?"held":"not_detected";
                    else if(verdict){next.occupancy="observed_empty";next.emptyBinding=currentBinding;next.reason=null;}
                    else {next.occupancy="retained";next.sticky=true;next.reason="part-off-false";}
                }
            }
        }
        next.lastObservation=id;next.lastInstance=instance;next.last=p;
        final long expected=generation;final Active add=admitted,parentNext=parentUpdate;
        return new Runnable(){boolean used;public void run(){synchronized(NativeVacuumJournal.this){
            if(used||generation!=expected)throw new IllegalStateException("Vacuum journal stale or reused commit");
            used=true;machineId=machine;if(before){seen.add(id);active.put(id,add);}else active.remove(id);
            if(parentNext!=null)active.put((String)d.get("parent_observation_id"),parentNext);
            nozzles.put(nozzle,next);generation++;
        }}};
    }

    void recover(String type,Map<String,Object> payload,String envelopeInstance)throws IOException {
        prepare(type,payload,envelopeInstance).run();if(matches(type))synchronized(this){recovered=true;}
    }
    synchronized void verifyMachineIdentity(String id)throws IOException {uuidValue(id);if(machineId!=null&&!machineId.equals(id))throw bad("Durable machine identity differs");}

    /** Sink must call Bridge.event, which owns prepare/force/commit. Never double-apply here. */
    void observe(String event,ReferenceNozzle nozzle,Map<String,Object> data,Map<String,Object> context,Sink sink) {
        observe(event,nozzle==null?null:nozzle.getId(),data,context,sink);
    }
    void observe(String event,String nozzleId,Map<String,Object> data,Map<String,Object> context,Sink sink) {
        try {
            if(!Objects.equals(nozzleId,context.get("nozzle_id")))throw bad("Observer nozzle differs from bound context");
            Map<String,Object> p=bounded(map("schema_version",1,"profile",PROFILE,"context",context,"native_event",event,"data",data));
            if(!EVENTS.contains(event))throw bad("Unknown native event");
            String type=event.endsWith(".before")?"vacuum_observation_intent":"vacuum_observation_outcome";
            // Prevalidate independently of the sink; publication still belongs solely to Bridge.event.
            prepare(type,p,text(context,"bridge_instance_id",36));
            sink.append(type,p);
        } catch(Throwable failure){invalidate(nozzleId,"journal-publication-failed");throw new Fence("VACUUM_DURABILITY_UNKNOWN","Native sensing publication failed; retry is not authorized",failure);}
        // Only this post-commit fence bypasses publication-failure invalidation; sink-thrown Errors never do.
        if(event.endsWith(".failed"))throw new Fence("VACUUM_OUTCOME_UNKNOWN","Native sensing failed; retry is not authorized",null);
    }

    /** Conservative process-local overlay for failed publication. It never replaces durable evidence. */
    synchronized void invalidate(String nozzleId,String reason) {
        if(nozzleId==null||nozzleId.isBlank()||nozzleId.length()>128)return;
        NozzleState state=nozzles.get(nozzleId);if(state==null){if(nozzles.size()>=MAX_NOZZLES)return;state=new NozzleState();nozzles.put(nozzleId,state);}
        state.sticky=true;state.occupancy="unknown";state.reason="publication-or-native-fault";generation++;
    }
    synchronized void requireNoFault()throws Fault {for(String id:nozzles.keySet())requireNoFault(id);}
    synchronized void requireNoFault(String nozzleId)throws Fault {
        NozzleState state=nozzles.get(nozzleId);
        if(state!=null&&state.sticky||hasPending(nozzleId))throw new Fault("VACUUM_OUTCOME_UNKNOWN","Nozzle "+nozzleId+" retains unresolved or retained-material sensing evidence");
    }
    /** Global legacy guard visits known nozzles but cannot grant empty authority without current bindings. */
    synchronized void requireUnoccupied()throws Fault {for(String id:nozzles.keySet())requireUnoccupied(id);}
    synchronized void requireUnoccupied(String nozzleId)throws Fault {
        requireNoFault(nozzleId);throw new Fault("VACUUM_CURRENT_BINDING_REQUIRED","Current native sensing binding is required for empty admission: "+nozzleId);
    }
    synchronized void requireUnoccupied(String nozzleId,Map<String,Object> suppliedBinding)throws Fault {
        requireNoFault(nozzleId);NozzleState state=nozzles.get(nozzleId);Map<String,Object> current;
        try{current=bounded(suppliedBinding);validateBinding(current);if(!nozzleId.equals(current.get("nozzle_id")))throw bad("Current nozzle binding differs");}
        catch(IOException invalid){throw new Fault("VACUUM_CURRENT_BINDING_REQUIRED","Invalid current native sensing binding");}
        if(state==null||recovered||!"observed_empty".equals(state.occupancy))throw new Fault("NOZZLE_OCCUPANCY_UNRESOLVED","Nozzle "+nozzleId+" lacks current successful part-off evidence");
        if(!current.equals(state.emptyBinding)){state.occupancy="unobserved";state.reason="empty-binding-changed";generation++;throw new Fault("VACUUM_BINDING_CHANGED","Empty observation has a different native sensing binding");}
    }
    private boolean hasPending(String nozzle){for(Active entry:active.values())if(entry.nozzle.equals(nozzle))return true;return false;}
    synchronized Map<String,Object> snapshot(){return snapshot(null);}
    synchronized Map<String,Object> snapshot(String currentInstance){
        List<Object> rows=new ArrayList<>();
        for(Map.Entry<String,NozzleState> entry:nozzles.entrySet()){
            NozzleState n=entry.getValue();boolean pending=hasPending(entry.getKey());
            rows.add(map("nozzle_id",entry.getKey(),"state",pending?"unknown":n.occupancy,"recorded_state",n.occupancy,"sticky_fault",n.sticky||pending,
                "reason",pending?"intent-without-outcome":n.reason,"observation_count",n.observations,"returned_count",n.returned,"failed_count",n.failed,
                "last_observation_id",n.lastObservation,"last_bridge_instance_id",n.lastInstance,
                "empty_observation_binding",n.emptyBinding,"last_successful_check_binding",n.lastSuccessfulCheckBinding,
                "historical",recovered||currentInstance!=null&&!Objects.equals(currentInstance,n.lastInstance),"last_record",n.last));
        }
        List<Object> unresolved=new ArrayList<>();for(Active a:active.values())unresolved.add(map("observation_id",a.data.get("observation_id"),"nozzle_id",a.nozzle,"operation_id",a.context.get("operation_id"),"kind",a.kind));
        try{return bounded(map("profile",PROFILE,"observations",seen.size(),"observation_limit",MAX_OBSERVATIONS,"nozzle_limit",MAX_NOZZLES,"pending",unresolved,"nozzles",rows,
            "recovered_history",recovered,"execution_authority_restored",false,"physical_occupancy_verified",false,"hardware_qualified",false,"reconciliation_supported",false),1024*1024);}catch(IOException impossible){throw new IllegalStateException(impossible);}
    }

    /** Caller supplies freshly observed native identities; no field here is remote authority. */
    static Map<String,Object> binding(Map<String,Object> context,Map<String,Object> data)throws IOException {
        return binding((String)context.get("machine_id"),(String)context.get("bridge_instance_id"),(String)context.get("config_revision"),(String)context.get("nozzle_id"),(String)data.get("nozzle_tip_id"),(String)data.get("sensor_id"),object(data,"source"));
    }
    static Map<String,Object> binding(String machine,String instance,String revision,String nozzle,String tip,String sensor,Map<String,Object> source)throws IOException {
        Map<String,Object> b=bounded(map("machine_id",machine,"bridge_instance_id",instance,"config_revision",revision,"nozzle_id",nozzle,"nozzle_tip_id",tip,"sensor_id",sensor,"source",source));validateBinding(b);return b;
    }
    private static void validateBinding(Map<String,Object> b)throws IOException {
        keys(b,"machine_id","bridge_instance_id","config_revision","nozzle_id","nozzle_tip_id","sensor_id","source");uuid(b,"machine_id");uuid(b,"bridge_instance_id");revision(b,"config_revision","cfg-");for(String k:List.of("nozzle_id","nozzle_tip_id","sensor_id"))identifier(b,k);source(object(b,"source"));
    }

    private void validateParent(Map<String,Object> c,Map<String,Object> d,String kind)throws IOException {
        Object parent=d.get("parent_observation_id");
        if(parent==null){
            if(kind.equals("valve"))throw bad("Probe valve lacks check parent");
            for(Active a:active.values())if(a.nozzle.equals(c.get("nozzle_id")))throw bad("Overlapping root observations");
            if(kind.equals("read")&&d.get("check_kind")!=null)throw bad("Standalone read declares check kind");
            return;
        }
        Active p=active.get(uuidValue((String)parent));
        if(p==null||!p.kind.equals("check")||kind.equals("check")||!p.context.equals(c))throw bad("Unknown or foreign parent check");
        for(String key:List.of("native_stage","check_kind","nozzle_tip_id","sensor_id","source"))if(!Objects.equals(p.data.get(key),d.get(key)))throw bad("Child differs from its check: "+key);
        for(Active a:active.values())if(parent.equals(a.data.get("parent_observation_id")))throw bad("Overlapping sibling observations");
        if(kind.equals("valve")&&!"part_off".equals(d.get("check_kind")))throw bad("Valve pulse outside part-off check");
        if(kind.equals("valve")&&(Boolean.TRUE.equals(d.get("enabled"))?(p.valveOn||p.valveOff):p.valveOff||!p.valveOn&&!p.childFailed))throw bad("Probe valve order differs");
    }
    private static boolean cleanup(String event,Map<String,Object> d){return event.equals("valve.before")&&Boolean.FALSE.equals(d.get("enabled"))&&Boolean.TRUE.equals(d.get("cleanup_attempt"));}
    private static void validateContext(Map<String,Object> c)throws IOException {
        keys(c,"operation_id","request_id","machine_id","bridge_instance_id","config_revision","nozzle_id","scope","job_context");
        for(String k:List.of("operation_id","request_id","machine_id","bridge_instance_id"))uuid(c,k);
        if(c.get("operation_id").equals(c.get("request_id")))throw bad("Operation/request identity aliased");
        revision(c,"config_revision","cfg-");identifier(c,"nozzle_id");
        String scope=text(c,"scope",16);if(!Set.of("manual","job","cleanup").contains(scope))throw bad("Unknown observation scope");
        if(scope.equals("manual")){if(c.get("job_context")!=null)throw bad("Manual context has job binding");}
        else {Map<String,Object> j=object(c,"job_context");keys(j,"job_id","job_revision","board_load_revision","material_setup_revision","lineage_id","lineage_revision");
            uuid(j,"job_id");uuid(j,"lineage_id");if(!text(j,"job_revision",64).matches("[a-f0-9]{64}"))throw bad("Invalid job revision digest");
            revision(j,"board_load_revision","load-");revision(j,"material_setup_revision","material-");integer(j,"lineage_revision",0,9007199254740991L);}
    }
    private static void validateData(String event,Map<String,Object> d)throws IOException {
        Set<String> expected=new HashSet<>(COMMON);String kind=event.split("\\.")[0];boolean failed=event.endsWith(".failed"),returned=event.endsWith(".returned");
        if(kind.equals("valve"))expected.addAll(List.of("enabled","reason","cleanup_attempt"));
        if(returned&&kind.equals("check"))expected.add("verdict");
        if(returned&&kind.equals("read"))expected.addAll(List.of("raw","raw_length","value"));
        if(failed){expected.addAll(List.of("failure_code","failure_type"));if(d.containsKey("raw"))expected.addAll(List.of("raw","raw_length"));for(String flag:List.of("raw_truncated","raw_sanitized"))if(d.containsKey(flag))expected.add(flag);}
        if(!expected.equals(d.keySet()))throw bad("Unexpected native observation fields");
        integer(d,"api_version",1,1);uuid(d,"observation_id");if(d.get("parent_observation_id")!=null)uuid(d,"parent_observation_id");
        if(Objects.equals(d.get("observation_id"),d.get("parent_observation_id")))throw bad("Observation is its own parent");
        if(!STAGES.contains(d.get("native_stage")))throw bad("Unknown native sensing stage");
        Object check=d.get("check_kind");if(check!=null&&!Set.of("part_on","part_off").contains(check)||kind.equals("check")&&check==null)throw bad("Invalid check kind");
        identifier(d,"nozzle_tip_id");identifier(d,"sensor_id");source(object(d,"source"));
        if(kind.equals("valve")){bool(d,"enabled");bool(d,"cleanup_attempt");if(!"part_off_probe".equals(d.get("reason"))||d.get("enabled").equals(d.get("cleanup_attempt")))throw bad("Invalid probe valve intent");}
        if(returned&&kind.equals("check"))bool(d,"verdict");
        if(d.containsKey("raw")){
            Object raw=d.get("raw");if(!(raw instanceof String)||((String)raw).length()>128||((String)raw).chars().anyMatch(ch->ch>127))throw bad("Invalid bounded raw sample");
            long length=integer(d,"raw_length",0,Integer.MAX_VALUE);
            if(((String)raw).length()!=Math.min(128,length))throw bad("Raw sample length mismatch");
            if(d.containsKey("raw_truncated")!= (length>128)||d.containsKey("raw_truncated")&&!Boolean.TRUE.equals(d.get("raw_truncated")))throw bad("Truncation flag mismatch");
        }else if(d.containsKey("raw_sanitized")||d.containsKey("raw_truncated"))throw bad("Raw flags without sample");
        if(d.containsKey("raw_sanitized")&&!Boolean.TRUE.equals(d.get("raw_sanitized")))throw bad("Invalid raw sanitation flag");
        if(returned&&kind.equals("read")){
            double parsed;try{parsed=Double.parseDouble((String)d.get("raw"));}catch(RuntimeException invalid){throw bad("Returned raw sample is not numeric");}
            if(!Double.isFinite(parsed)||!(d.get("value") instanceof BigDecimal)||BigDecimal.valueOf(parsed).compareTo((BigDecimal)d.get("value"))!=0)throw bad("Returned finite value differs from raw sample");
        }
        if(failed){
            Set<String> codes=kind.equals("read")?Set.of("SENSOR_VALUE_INVALID","SENSOR_VALUE_NONFINITE","SENSOR_READ_FAILED"):kind.equals("check")?Set.of("NATIVE_CHECK_FAILED"):Set.of("VALVE_ACTUATION_FAILED");
            if(!codes.contains(d.get("failure_code")))throw bad("Failure code differs from kind");identifier(d,"failure_type");
            if(!kind.equals("read")&&d.containsKey("raw"))throw bad("Non-read failure invents raw sample");
        }
    }
    private static void source(Map<String,Object> s)throws IOException {
        keys(s,"origin","profile","source_id","process_id","simulation_only","hardware_qualified","fixture_id","scenario_id","units");
        if(!"controlled-simulator".equals(s.get("origin"))||!"controlled-native-vacuum-v1".equals(s.get("profile"))||!Boolean.TRUE.equals(s.get("simulation_only"))||!Boolean.FALSE.equals(s.get("hardware_qualified")))throw bad("Unqualified source provenance");
        uuid(s,"source_id");integer(s,"process_id",1,9007199254740991L);
        for(String key:List.of("fixture_id","scenario_id","units")){String value=text(s,key,128);if(value.chars().anyMatch(ch->ch<32||ch>126))throw bad("Source provenance must be printable ASCII");}
    }
    static void validateRawRecord(String raw)throws IOException {
        if(raw==null||raw.getBytes(StandardCharsets.UTF_8).length>MAX_RECORD_BYTES+4096)throw bad("Raw record limit");
        try(JsonReader reader=new JsonReader(new StringReader(raw))){reader.setLenient(false);strict(reader,0,new int[1]);if(reader.peek()!=JsonToken.END_DOCUMENT)throw bad("Trailing JSON value");}
        catch(IllegalStateException|NumberFormatException invalid){throw bad("Malformed JSON record");}
    }
    private static void strict(JsonReader r,int depth,int[] nodes)throws IOException {
        if(depth>32||++nodes[0]>4096)throw bad("JSON structural limit");
        switch(r.peek()){
            case BEGIN_OBJECT:r.beginObject();Set<String> names=new HashSet<>();while(r.hasNext()){String name=r.nextName();validString(name);if(!names.add(name))throw bad("Duplicate JSON object key");strict(r,depth+1,nodes);}r.endObject();break;
            case BEGIN_ARRAY:r.beginArray();while(r.hasNext())strict(r,depth+1,nodes);r.endArray();break;
            case STRING:validString(r.nextString());break;
            case NUMBER:decimal(r.nextString());break;
            case BOOLEAN:r.nextBoolean();break;
            case NULL:r.nextNull();break;
            default:throw bad("Invalid JSON token");
        }
    }
    private static Map<String,Object> bounded(Map<String,Object> p)throws IOException{return bounded(p,MAX_RECORD_BYTES);}
    @SuppressWarnings("unchecked") private static Map<String,Object> bounded(Map<String,Object> p,int max)throws IOException {
        if(p==null)throw bad("Object required");Map<String,Object> result=(Map<String,Object>)copy(p,0,new int[1]);
        if(JSON.toJson(result).getBytes(StandardCharsets.UTF_8).length>max)throw bad("Payload size limit");return result;
    }
    private static Object copy(Object v,int depth,int[] nodes)throws IOException {
        if(depth>32||++nodes[0]>16384)throw bad("DTO structural limit");
        if(v instanceof Map){Map<String,Object> out=new LinkedHashMap<>();for(Map.Entry<?,?> e:((Map<?,?>)v).entrySet()){if(!(e.getKey() instanceof String))throw bad("String keys required");validString((String)e.getKey());out.put((String)e.getKey(),copy(e.getValue(),depth+1,nodes));}return Collections.unmodifiableMap(out);}
        if(v instanceof List){List<Object> out=new ArrayList<>();for(Object x:(List<?>)v)out.add(copy(x,depth+1,nodes));return Collections.unmodifiableList(out);}
        if(v==null||v instanceof Boolean)return v;if(v instanceof String){validString((String)v);return v;}if(v instanceof Number)return decimal(v.toString());throw bad("Non-JSON DTO value");
    }
    private static BigDecimal decimal(String raw)throws IOException {try{if(raw.length()>256)throw new NumberFormatException();BigDecimal n=new BigDecimal(raw);if(Math.abs((long)n.scale())>10000||!Double.isFinite(n.doubleValue()))throw new NumberFormatException();return n.stripTrailingZeros();}catch(NumberFormatException e){throw bad("Invalid finite number");}}
    private static void validString(String s)throws IOException {for(int i=0;i<s.length();i++){char c=s.charAt(i);if(Character.isHighSurrogate(c)){if(++i>=s.length()||!Character.isLowSurrogate(s.charAt(i)))throw bad("Malformed Unicode");}else if(Character.isLowSurrogate(c))throw bad("Malformed Unicode");}}
    private static void keys(Map<String,Object> p,String...k)throws IOException {if(!p.keySet().equals(new HashSet<>(Arrays.asList(k))))throw bad("Unexpected fields");}
    @SuppressWarnings("unchecked") private static Map<String,Object> object(Map<String,Object> p,String k)throws IOException {if(!(p.get(k) instanceof Map))throw bad("Object required: "+k);return(Map<String,Object>)p.get(k);}
    private static String text(Map<String,Object> p,String k,int max)throws IOException {if(!(p.get(k) instanceof String)||((String)p.get(k)).isBlank()||((String)p.get(k)).length()>max)throw bad("Bounded string required: "+k);return(String)p.get(k);}
    private static String identifier(Map<String,Object> p,String k)throws IOException {String s=text(p,k,128);if(s.chars().anyMatch(ch->ch<32||ch==127))throw bad("Invalid native identifier");return s;}
    private static String uuid(Map<String,Object> p,String k)throws IOException{return uuidValue(text(p,k,36));}
    private static String uuidValue(String s)throws IOException {try{if(s==null||!UUID.fromString(s).toString().equals(s))throw new IllegalArgumentException();return s;}catch(RuntimeException invalid){throw bad("Canonical UUID required");}}
    private static long integer(Map<String,Object> p,String k,long low,long high)throws IOException {try{return NativeJournalJson.integer(p.get(k),low,high);}catch(RuntimeException invalid){throw bad("Exact bounded integer required: "+k);}}
    private static void revision(Map<String,Object> p,String k,String prefix)throws IOException {String s=text(p,k,32);if(!s.matches(prefix+"(0|[1-9][0-9]*)"))throw bad("Invalid revision: "+k);try{if(new BigDecimal(s.substring(prefix.length())).compareTo(BigDecimal.valueOf(9007199254740991L))>0)throw bad("Revision exceeds exact integer bound");}catch(NumberFormatException invalid){throw bad("Invalid revision");}}
    private static void bool(Map<String,Object> p,String k)throws IOException {if(!(p.get(k) instanceof Boolean))throw bad("Boolean required: "+k);}
    private static Map<String,Object> map(Object...v){Map<String,Object> p=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)p.put((String)v[i],v[i+1]);return p;}
    private static IOException bad(String message){return new IOException("Vacuum journal: "+message);}
}
