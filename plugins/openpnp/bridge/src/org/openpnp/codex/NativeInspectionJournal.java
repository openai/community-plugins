/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.*;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;

/** Data-only inspection history. A replayed record never reconstructs a local capability. */
final class NativeInspectionJournal {
    static final String PROFILE="native-loaded-board-inspection-v1";
    private static final int LIMIT=64,MAX_BYTES=1024*1024,MAX_DEPTH=64,MAX_NODES=1000000;
    private static final Gson JSON=new Gson();
    private static final Set<String> TYPES=Set.of("inspection_task_pending","inspection_submission_intent","inspection_receipt","inspection_task_closed");
    private static final Set<String> REASONS=Set.of("cancelled","expired","ownership-revoked","scope-stale","presentation-failed","request-failed");
    private static final String[] INTENT={"task_id","submission_operation_id","request_id","submission_sha256","scope_fingerprint"};
    private final LinkedHashMap<String,Entry> entries=new LinkedHashMap<>();
    private long generation;
    private static final class Entry {
        Map<String,Object> pending,intent,receipt,closed;
        Entry copy(){Entry e=new Entry();e.pending=pending;e.intent=intent;e.receipt=receipt;e.closed=closed;return e;}
    }
    static boolean matches(String type){return type!=null&&type.startsWith("inspection_");}
    /** Validate detached state now; commit exactly once after the caller's durable force. */
    synchronized Runnable prepare(String type,Map<String,Object> payload,String eventInstance)throws IOException {
        if(!matches(type))return ()->{};
        if(!TYPES.contains(type))throw bad("Unknown inspection event");
        Map<String,Object> p=bounded(payload,MAX_BYTES);uuidValue(eventInstance,"event instance");
        String task=uuid(p,"task_id");Entry prior=entries.get(task),next=prior==null?new Entry():prior.copy();
        if(type.equals("inspection_task_pending")){
            keys(p,"profile","schema_version","task_id","request_operation_id","request_id","machine_id","bridge_instance_id","created_at","expires_at","context","snapshot");
            if(prior!=null||entries.size()>=LIMIT)throw bad("Duplicate task or retained task limit");
            if(number(p,"schema_version",1,1)!=1||!PROFILE.equals(p.get("profile")))throw bad("Invalid task profile/version");
            String op=uuid(p,"request_operation_id"),request=uuid(p,"request_id"),machine=uuid(p,"machine_id");uuid(p,"bridge_instance_id");
            if(task.equals(op)||task.equals(request)||op.equals(request)||!eventInstance.equals(p.get("bridge_instance_id")))throw bad("Aliased task/request or foreign instance");
            for(Entry old:entries.values()){
                if(!machine.equals(old.pending.get("machine_id")))throw bad("Foreign machine in inspection history");
                uniqueIdentity(old,op,request,task);
            }
            Instant created=time(p,"created_at"),expires=time(p,"expires_at");
            if(!expires.isAfter(created)||expires.isAfter(created.plusSeconds(600)))throw bad("Invalid task lifetime");
            Map<String,Object> context=object(p,"context"),snapshot=object(p,"snapshot");context(context,p);
            snapshot(snapshot,context);next.pending=p;
        }else{
            if(prior==null)throw bad("Inspection event lacks pending task");
            if(!eventInstance.equals(prior.pending.get("bridge_instance_id")))throw bad("Inspection event instance differs");
            if(next.closed!=null||next.receipt!=null)throw bad("Inspection task already terminal");
            if(type.equals("inspection_submission_intent")){
                keys(p,INTENT);if(next.intent!=null)throw bad("Duplicate submission intent");
                String op=uuid(p,"submission_operation_id"),request=uuid(p,"request_id");
                if(op.equals(request)||op.equals(task)||request.equals(task))throw bad("Aliased submission identity");
                for(Entry old:entries.values())uniqueIdentity(old,op,request,null);
                digest(p,"submission_sha256");scope(p,next);next.intent=p;
            }else if(type.equals("inspection_receipt")){
                keys(p,"task_id","submission_operation_id","request_id","submission_sha256","scope_fingerprint","artifact","result");
                if(next.intent==null)throw bad("Receipt lacks submission intent");
                for(String key:INTENT)if(!Objects.equals(next.intent.get(key),p.get(key)))throw bad("Receipt submission identity differs: "+key);
                scope(p,next);artifact(object(p,"artifact"));result(object(p,"result"),object(next.pending,"snapshot"));next.receipt=p;
            }else{
                keys(p,"task_id","reason");if(next.intent!=null||!REASONS.contains(text(p,"reason",64)))throw bad("Only pending tasks can close for a declared reason");next.closed=p;
            }
        }
        final long version=generation;final Entry selected=next;return new Runnable(){boolean committed;
            public void run(){synchronized(NativeInspectionJournal.this){if(committed||generation!=version)throw new IllegalStateException("Inspection journal stale or reused commit");committed=true;entries.put(task,selected);generation++;}}
        };
    }
    void recover(String type,Map<String,Object> payload,String eventInstance)throws IOException{prepare(type,payload,eventInstance).run();}
    synchronized void verifyMachineIdentity(String machineId)throws IOException{uuidValue(machineId,"machine_id");for(Entry e:entries.values())if(!machineId.equals(e.pending.get("machine_id")))throw bad("Durable machine identity differs");}
    synchronized Map<String,Object> get(String taskId,String currentInstance)throws IOException {
        uuidValue(taskId,"task_id");uuidValue(currentInstance,"current instance");Entry e=entries.get(taskId);
        if(e==null)return immutable(map("found",false,"task_id",taskId));return view(taskId,e,currentInstance,true);
    }
    synchronized Map<String,Object> snapshot(String currentInstance)throws IOException{
        uuidValue(currentInstance,"current instance");List<Object> tasks=new ArrayList<>();for(Map.Entry<String,Entry> e:entries.entrySet())tasks.add(view(e.getKey(),e.getValue(),currentInstance,false));
        return immutable(map("profile",PROFILE,"retained_count",entries.size(),"retained_limit",LIMIT,"task_authority_restored",false,"tasks",tasks));
    }
    private static Map<String,Object> view(String id,Entry e,String current,boolean detail){
        boolean historical=!current.equals(e.pending.get("bridge_instance_id"));String recorded=e.closed!=null?"closed":e.receipt!=null?"receipt-recorded":e.intent!=null?"submitting":"pending";
        Map<String,Object> out=map("found",true,"profile",PROFILE,"task_id",id,"recorded_state",recorded,"state",historical&&e.intent!=null&&e.receipt==null?"outcome_unknown":recorded,
            "historical",historical,"task_active",false,"execution_authority_granted",false,"operator_label_verified",false,"request_operation_id",e.pending.get("request_operation_id"),"created_at",e.pending.get("created_at"),"expires_at",e.pending.get("expires_at"));
        if(!detail)return immutable(out);out.put("pending",e.pending);
        if(e.intent!=null)out.put("submission_intent",e.intent);if(e.receipt!=null)out.put("receipt",e.receipt);if(e.closed!=null)out.put("closure",e.closed);return immutable(out);
    }
    private static void context(Map<String,Object> c,Map<String,Object> p)throws IOException{
        for(String key:List.of("bridge_instance_id","job_id","lineage_id"))uuid(c,key);
        if(!Objects.equals(c.get("bridge_instance_id"),p.get("bridge_instance_id"))||!"completed".equals(c.get("job_state")))throw bad("Context instance/state differs");
        if(c.containsKey("machine_id")&&!Objects.equals(c.get("machine_id"),p.get("machine_id")))throw bad("Context machine differs");
        digest(c,"job_revision");revision(c,"config_revision","cfg-");revision(c,"board_load_revision","load-");number(c,"lineage_revision",0,9007199254740991L);number(c,"ownership_epoch",0,9007199254740991L);
    }
    private static void snapshot(Map<String,Object> s,Map<String,Object> context)throws IOException{
        keys(s,"profile","loaded_board_id","board_load_id","holder_instance_id","root_instance_id","board_side","source_fingerprint","library_fingerprint","context","board_load_scope","placed_history_fingerprint","required_count","required_placements","coordinate_frame","location_units","rotation_units","authority","simulation_only","hardware_qualified","production_authority_granted","instrument_authenticity_verified","limits","scope_fingerprint");
        if(!same(context,s.get("context"))||!PROFILE.equals(s.get("profile")))throw bad("Snapshot context/profile differs");
        for(String key:List.of("scope_fingerprint","source_fingerprint","library_fingerprint","placed_history_fingerprint"))digest(s,key);
        String loaded=uuid(s,"loaded_board_id"),load=uuid(s,"board_load_id"),holder=identifier(s,"holder_instance_id",2048),root=identifier(s,"root_instance_id",2048);
        if(!Set.of("top","bottom").contains(s.get("board_side"))||!"holder".equals(s.get("coordinate_frame"))||!"mm".equals(s.get("location_units"))||!"degrees".equals(s.get("rotation_units")))throw bad("Invalid snapshot coordinates/side");
        Map<String,Object> limits=object(s,"limits");keys(limits,"placements","artifact_refs");number(limits,"placements",200,200);number(limits,"artifact_refs",32,32);
        Map<String,Object> scope=object(s,"board_load_scope");keys(scope,"scope_id","board_load_revision","job_revision","authority","physical_load_verified","boards");digest(scope,"scope_id");
        if(!"native-simulator".equals(scope.get("authority"))||!Boolean.FALSE.equals(scope.get("physical_load_verified"))||!Objects.equals(context.get("board_load_revision"),scope.get("board_load_revision"))||!Objects.equals(context.get("job_revision"),scope.get("job_revision")))throw bad("Snapshot load scope differs");
        Set<String> loadedIds=new HashSet<>(),holderIds=new HashSet<>();int selected=0;
        for(Object raw:list(scope,"boards",1000)){Map<String,Object> b=object(raw);keys(b,"board_instance_id","loaded_board_id","root_instance_id","side","enabled","board_load_id");
            String bid=uuid(b,"loaded_board_id"),hid=identifier(b,"board_instance_id",2048);uuid(b,"board_load_id");identifier(b,"root_instance_id",2048);
            if(!loadedIds.add(bid)||!holderIds.add(hid)||!(b.get("enabled") instanceof Boolean)||!Set.of("top","bottom").contains(b.get("side")))throw bad("Ambiguous or invalid load bindings");
            if(bid.equals(loaded)){selected++;if(!holder.equals(hid)||!load.equals(b.get("board_load_id"))||!root.equals(b.get("root_instance_id"))||!s.get("board_side").equals(b.get("side"))||!Boolean.TRUE.equals(b.get("enabled")))throw bad("Selected board binding differs");}
        }
        if(selected!=1)throw bad("Selected board absent from load scope");
        long count=number(s,"required_count",1,200);List<?> rows=list(s,"required_placements",200);if(rows.size()!=count)throw bad("Required placement count differs");
        Set<String> ids=new HashSet<>();for(Object raw:rows){Map<String,Object> row=object(raw);keys(row,"holder_instance_id","placement_id","part_id","location","native_placed");String id=identifier(row,"placement_id",128);identifier(row,"part_id",128);
            if(!ids.add(id)||!holder.equals(row.get("holder_instance_id"))||!Boolean.TRUE.equals(row.get("native_placed")))throw bad("Invalid required placement identity/history");
            Map<String,Object> loc=object(row,"location");keys(loc,"x","y","z","rotation","units");if(!"mm".equals(loc.get("units")))throw bad("Required placement units differ");
            decimal(loc,"x",-10000,10000,false);decimal(loc,"y",-10000,10000,false);decimal(loc,"z",-1000,1000,false);decimal(loc,"rotation",-360,360,false);
        }
        scopeFlags(s);noAuthority(context);
    }
    private static void revision(Map<String,Object> p,String key,String prefix)throws IOException{String value=text(p,key,32);if(!value.matches(prefix+"(?:0|[1-9][0-9]*)"))throw bad("Invalid native revision");try{Long.parseLong(value.substring(prefix.length()));}catch(RuntimeException e){throw bad("Native revision overflow");}}
    private static void uniqueIdentity(Entry old,String op,String request,String task)throws IOException{
        List<Object> retained=new ArrayList<>(List.of(old.pending.get("task_id"),old.pending.get("request_operation_id"),old.pending.get("request_id")));
        if(old.intent!=null){retained.add(old.intent.get("submission_operation_id"));retained.add(old.intent.get("request_id"));}
        if(retained.contains(op)||retained.contains(request)||(task!=null&&retained.contains(task)))throw bad("Inspection identity reused");
    }
    private static void scope(Map<String,Object> p,Entry e)throws IOException{digest(p,"scope_fingerprint");if(!Objects.equals(p.get("scope_fingerprint"),object(e.pending,"snapshot").get("scope_fingerprint")))throw bad("Inspection scope differs");}
    private static void artifact(Map<String,Object> a)throws IOException{
        keys(a,"artifact_id","mime_type","sha256","size","metadata");uuid(a,"artifact_id");digest(a,"sha256");number(a,"size",1,MAX_BYTES);
        if(!"application/json".equals(a.get("mime_type")))throw bad("Inspection receipt artifact must be JSON");bounded(object(a,"metadata"),60000);noAuthority(a);
    }
    private static void result(Map<String,Object> r,Map<String,Object> snapshot)throws IOException{
        keys(r,"profile","loaded_board_id","holder_instance_id","source_fingerprint","scope_fingerprint","operator_label","operator_note","tolerances","artifact_refs","required_count","observed_count","complete_coverage","passed_count","failed_count","uncertain_count","outcome","records","authority","polarity_applicability","instrument_authenticity_verified","artifact_bytes_verified_by_model","native_placed_history_modified","production_authority_granted","simulation_only","hardware_qualified");
        for(String key:List.of("profile","loaded_board_id","holder_instance_id","source_fingerprint","scope_fingerprint"))if(!Objects.equals(r.get(key),snapshot.get(key)))throw bad("Inspection result scope differs: "+key);
        scopeFlags(r);if(!"operator-reported".equals(r.get("polarity_applicability"))||!Boolean.FALSE.equals(r.get("artifact_bytes_verified_by_model"))||!Boolean.FALSE.equals(r.get("native_placed_history_modified"))||!Boolean.TRUE.equals(r.get("complete_coverage")))throw bad("Inspection result authority/provenance differs");
        text(r,"operator_label",128);text(r,"operator_note",4000);Map<String,Object> t=object(r,"tolerances");keys(t,"xy_mm","rotation_deg");BigDecimal xyTolerance=decimal(t,"xy_mm",0,100,true),rotationTolerance=decimal(t,"rotation_deg",0,180,true);
        long required=number(r,"required_count",1,200),observed=number(r,"observed_count",1,200),passed=number(r,"passed_count",0,200),failed=number(r,"failed_count",0,200),uncertain=number(r,"uncertain_count",0,200);
        if(required!=number(snapshot,"required_count",1,200)||observed!=required||passed+failed+uncertain!=required)throw bad("Result count disagreement");
        String expected=failed>0?"failed":uncertain>0?"uncertain":"passed";if(!expected.equals(r.get("outcome")))throw bad("Result disposition differs from counts");
        Map<String,Object> requiredRows=new LinkedHashMap<>();for(Object raw:list(snapshot,"required_placements",200)){Map<String,Object> row=object(raw);requiredRows.put(text(row,"placement_id",128),row);}
        List<?> rows=list(r,"records",200);if(rows.size()!=required)throw bad("Result row count differs");Set<String> ids=new HashSet<>();long pa=0,fa=0,un=0;
        for(Object raw:rows){Map<String,Object> row=object(raw);keys(row,"holder_instance_id","placement_id","presence","polarity","measurements","outcome","failures","uncertainties","native_placed");
            String id=text(row,"placement_id",128),presence=text(row,"presence",16),polarity=text(row,"polarity",32);
            if(!requiredRows.containsKey(id)||!ids.add(id)||!Objects.equals(row.get("holder_instance_id"),r.get("holder_instance_id"))||!Boolean.TRUE.equals(row.get("native_placed")))throw bad("Result placement identity differs");
            if(!Set.of("present","missing","unknown").contains(presence)||!Set.of("correct","incorrect","unknown","not_applicable").contains(polarity))throw bad("Invalid observation enum");
            List<?> failures=list(row,"failures",4),uncertainties=list(row,"uncertainties",4);strings(failures,Set.of("component_missing","polarity_incorrect","xy_out_of_tolerance","rotation_out_of_tolerance"));strings(uncertainties,Set.of("presence_unknown","polarity_unknown","xy_uncertainty_crosses_tolerance","rotation_uncertainty_crosses_tolerance"));
            List<String> expectedFailures=new ArrayList<>(),expectedUncertainties=new ArrayList<>();
            Map<String,Object> measured=object(row,"measurements");if(!presence.equals("present")){if(!measured.isEmpty()||!polarity.equals("unknown"))throw bad("Unknown/missing row invented measurements");
                if(presence.equals("missing"))expectedFailures.add("component_missing");else expectedUncertainties.add("presence_unknown");}
            else{keys(measured,"dx_mm","dy_mm","rotation_deg","xy_uncertainty_mm","rotation_uncertainty_deg","xy_error_mm","rotation_error_deg","xy_interval_mm","rotation_interval_deg");
                BigDecimal x=decimal(measured,"dx_mm",-1000000,1000000,false),y=decimal(measured,"dy_mm",-1000000,1000000,false),rotation=decimal(measured,"rotation_deg",-360,360,false),ux=decimal(measured,"xy_uncertainty_mm",0,1000000,false),ur=decimal(measured,"rotation_uncertainty_deg",0,180,false);
                // Re-evaluate the retained exact decimal inputs, never rounded display intervals.
                BigDecimal squared=x.multiply(x).add(y.multiply(y)),outer=xyTolerance.add(ux),inner=xyTolerance.subtract(ux);
                if(squared.compareTo(outer.multiply(outer))>0)expectedFailures.add("xy_out_of_tolerance");else if(inner.signum()<0||squared.compareTo(inner.multiply(inner))>0)expectedUncertainties.add("xy_uncertainty_crosses_tolerance");
                BigDecimal angle=rotation.abs().remainder(BigDecimal.valueOf(360));if(angle.compareTo(BigDecimal.valueOf(180))>0)angle=BigDecimal.valueOf(360).subtract(angle);
                BigDecimal lower=angle.subtract(ur).max(BigDecimal.ZERO),upper=angle.add(ur).min(BigDecimal.valueOf(180));
                if(lower.compareTo(rotationTolerance)>0)expectedFailures.add("rotation_out_of_tolerance");else if(upper.compareTo(rotationTolerance)>0)expectedUncertainties.add("rotation_uncertainty_crosses_tolerance");
                double xy=Math.hypot(x.doubleValue(),y.doubleValue()),a=Math.abs(Math.IEEEremainder(rotation.doubleValue(),360));
                display(decimal(measured,"xy_error_mm",0,2000000,false),xy);display(decimal(measured,"rotation_error_deg",0,180,false),a);
                displayInterval(measured,"xy_interval_mm",3000000,Math.max(0,xy-ux.doubleValue()),xy+ux.doubleValue());displayInterval(measured,"rotation_interval_deg",180,Math.max(0,a-ur.doubleValue()),Math.min(180,a+ur.doubleValue()));}
            if(polarity.equals("incorrect"))expectedFailures.add("polarity_incorrect");else if(polarity.equals("unknown"))expectedUncertainties.add("polarity_unknown");
            if(!failures.equals(expectedFailures)||!uncertainties.equals(expectedUncertainties))throw bad("Result reasons contradict retained observations/tolerances");
            String outcome=!failures.isEmpty()?"failed":!uncertainties.isEmpty()?"uncertain":"passed";if(!outcome.equals(row.get("outcome")))throw bad("Row disposition differs");if(outcome.equals("passed"))pa++;else if(outcome.equals("failed"))fa++;else un++;
        }
        if(pa!=passed||fa!=failed||un!=uncertain)throw bad("Result row dispositions differ from counts");
        Set<String> artifacts=new HashSet<>();for(Object raw:list(r,"artifact_refs",32)){Map<String,Object> a=object(raw);keys(a,"artifact_id","sha256","kind");if(!artifacts.add(uuid(a,"artifact_id")))throw bad("Duplicate evidence artifact");digest(a,"sha256");if(!Set.of("image","measurement-file","operator-note").contains(a.get("kind")))throw bad("Invalid artifact reference kind");}
        noAuthority(r);
    }
    private static void scopeFlags(Map<String,Object> p)throws IOException{if(!"local-operator-reported".equals(p.get("authority"))||!Boolean.TRUE.equals(p.get("simulation_only")))throw bad("Inspection authority differs");for(String k:List.of("hardware_qualified","production_authority_granted","instrument_authenticity_verified"))if(!Boolean.FALSE.equals(p.get(k)))throw bad("Inspection must not grant qualification");}
    private static void noAuthority(Object value)throws IOException{if(value instanceof Map){for(Map.Entry<?,?> e:((Map<?,?>)value).entrySet()){String k=(String)e.getKey();if(Set.of("hardware_qualified","physical_qualification","production_qualified","production_authority_granted","execution_authority_granted","execution_authority_restored","native_placed_history_modified","unknown_operation_resolved","full_implementation_plan_completed").contains(k)&&!Boolean.FALSE.equals(e.getValue()))throw bad("Inspection attempted authority/qualification grant");noAuthority(e.getValue());}}else if(value instanceof List)for(Object v:(List<?>)value)noAuthority(v);}
    // Derived display values tolerate cross-platform Math rounding only; classification above is exact.
    private static void display(BigDecimal recorded,double expected)throws IOException{double actual=recorded.doubleValue();if(Math.abs(actual-expected)>4*Math.ulp(expected))throw bad("Derived display value differs from retained inputs");}
    private static void displayInterval(Map<String,Object> p,String key,double high,double lowExpected,double highExpected)throws IOException{List<?> v=list(p,key,2);if(v.size()!=2)throw bad("Expected two interval endpoints");BigDecimal a=decimal(v.get(0),0,high,false),b=decimal(v.get(1),0,high,false);if(a.compareTo(b)>0)throw bad("Reversed interval");display(a,lowExpected);display(b,highExpected);}
    private static void strings(List<?> rows,Set<String> allowed)throws IOException{Set<Object> seen=new HashSet<>();for(Object row:rows)if(!(row instanceof String)||!allowed.contains(row)||!seen.add(row))throw bad("Invalid/repeated result reason");}

    /** Call on raw inspection event JSON before Gson can collapse duplicate object keys. */
    static void validateRawRecord(String raw)throws IOException{
        if(raw==null||raw.getBytes(StandardCharsets.UTF_8).length>MAX_BYTES+65536)throw bad("Raw inspection record exceeds bound");
        try(JsonReader reader=new JsonReader(new StringReader(raw))){reader.setLenient(false);if(reader.peek()!=JsonToken.BEGIN_OBJECT)throw bad("Inspection event object required");readStrict(reader,0,new int[1]);if(reader.peek()!=JsonToken.END_DOCUMENT)throw bad("Trailing JSON content");}
        catch(IllegalArgumentException|IllegalStateException invalid){throw bad("Malformed inspection JSON");}
    }
    private static void readStrict(JsonReader r,int depth,int[] nodes)throws IOException{
        if(depth>MAX_DEPTH||++nodes[0]>MAX_NODES)throw bad("Raw inspection JSON exceeds structural bound");
        switch(r.peek()){
            case BEGIN_OBJECT:r.beginObject();Set<String> keys=new HashSet<>();while(r.hasNext()){String key=r.nextName();validString(key);if(!keys.add(key))throw bad("Duplicate JSON object key");readStrict(r,depth+1,nodes);}r.endObject();break;
            case BEGIN_ARRAY:r.beginArray();while(r.hasNext())readStrict(r,depth+1,nodes);r.endArray();break;
            case STRING:validString(r.nextString());break;
            case NUMBER:numberValue(r.nextString());break;
            case BOOLEAN:r.nextBoolean();break;
            case NULL:r.nextNull();break;
            default:throw bad("Unexpected JSON token");
        }
    }
    private static Map<String,Object> bounded(Map<?,?> p,int max)throws IOException{if(p==null)throw bad("Inspection payload object required");try{Map<String,Object> out=(Map<String,Object>)copy(p,0,new int[1]);if(JSON.toJson(out).getBytes(StandardCharsets.UTF_8).length>max)throw bad("Inspection payload exceeds bound");return out;}catch(IllegalArgumentException invalid){throw bad("Invalid inspection payload");}}
    private static Object copy(Object value,int depth,int[] nodes)throws IOException{
        if(depth>MAX_DEPTH||++nodes[0]>MAX_NODES)throw bad("Inspection DTO exceeds structural bound");
        if(value instanceof Map){Map<String,Object> out=new LinkedHashMap<>();for(Map.Entry<?,?> e:((Map<?,?>)value).entrySet()){if(!(e.getKey() instanceof String))throw bad("JSON string keys required");validString((String)e.getKey());out.put((String)e.getKey(),copy(e.getValue(),depth+1,nodes));}return Collections.unmodifiableMap(out);}
        if(value instanceof List){List<Object> out=new ArrayList<>();for(Object v:(List<?>)value)out.add(copy(v,depth+1,nodes));return Collections.unmodifiableList(out);}
        if(value==null||value instanceof Boolean)return value;if(value instanceof String){validString((String)value);return value;}if(value instanceof Number)return numberValue(value.toString());throw bad("Non-JSON object in inspection DTO");
    }
    private static BigDecimal numberValue(String raw)throws IOException{try{if(raw.length()>256)throw new NumberFormatException();BigDecimal n=new BigDecimal(raw);if(Math.abs((long)n.scale())>10000||!Double.isFinite(n.doubleValue()))throw new NumberFormatException();return n.stripTrailingZeros();}catch(NumberFormatException e){throw bad("Invalid finite JSON number");}}
    private static Map<String,Object> immutable(Map<String,Object> p){try{return bounded(p,MAX_BYTES*64);}catch(IOException e){throw new IllegalStateException(e);}}
    private static void validString(String s)throws IOException{for(int i=0;i<s.length();i++){char c=s.charAt(i);if(Character.isHighSurrogate(c)){if(++i>=s.length()||!Character.isLowSurrogate(s.charAt(i)))throw bad("Malformed Unicode string");}else if(Character.isLowSurrogate(c))throw bad("Malformed Unicode string");}}
    private static Map<String,Object> object(Map<String,Object> p,String key)throws IOException{return object(p.get(key));}
    @SuppressWarnings("unchecked")private static Map<String,Object> object(Object value)throws IOException{if(!(value instanceof Map))throw bad("Object required");return(Map<String,Object>)value;}
    private static List<?> list(Map<String,Object> p,String key,int max)throws IOException{Object v=p.get(key);if(!(v instanceof List)||((List<?>)v).size()>max)throw bad("Bounded array required: "+key);return(List<?>)v;}
    private static String text(Map<String,Object> p,String key,int max)throws IOException{Object v=p.get(key);if(!(v instanceof String)||((String)v).isBlank()||((String)v).length()>max)throw bad("Bounded string required: "+key);return(String)v;}
    private static String identifier(Map<String,Object> p,String key,int max)throws IOException{String value=text(p,key,max);if(value.chars().anyMatch(c->c<32||c==127))throw bad("Invalid native identifier");return value;}
    private static String uuid(Map<String,Object> p,String key)throws IOException{return uuidValue(text(p,key,36),key);}
    private static String uuidValue(String s,String key)throws IOException{try{if(s==null||!UUID.fromString(s).toString().equals(s))throw new IllegalArgumentException();return s;}catch(RuntimeException invalid){throw bad("Canonical UUID required: "+key);}}
    private static String digest(Map<String,Object> p,String key)throws IOException{String s=text(p,key,64);if(!s.matches("[a-f0-9]{64}"))throw bad("SHA256 required: "+key);return s;}
    private static long number(Map<String,Object> p,String key,long low,long high)throws IOException{try{return NativeJournalJson.integer(p.get(key),low,high);}catch(RuntimeException invalid){throw bad("Exact bounded integer required: "+key);}}
    private static BigDecimal decimal(Map<String,Object> p,String key,double low,double high,boolean exclusive)throws IOException{return decimal(p.get(key),low,high,exclusive);}
    private static BigDecimal decimal(Object value,double low,double high,boolean exclusive)throws IOException{if(!(value instanceof Number))throw bad("Number required");BigDecimal n=numberValue(value.toString());int c=n.compareTo(BigDecimal.valueOf(low));if(c<0||exclusive&&c==0||n.compareTo(BigDecimal.valueOf(high))>0)throw bad("Number outside bound");return n;}
    private static Instant time(Map<String,Object> p,String key)throws IOException{try{String raw=text(p,key,40);Instant time=Instant.parse(raw);if(!time.toString().equals(raw))throw new IllegalArgumentException();return time;}catch(RuntimeException e){throw bad("Canonical UTC timestamp required");}}
    private static boolean same(Object a,Object b)throws IOException{return Objects.equals(copy(a,0,new int[1]),copy(b,0,new int[1]));}
    private static void keys(Map<String,Object> p,String... names)throws IOException{if(!p.keySet().equals(new HashSet<>(Arrays.asList(names))))throw bad("Unexpected inspection fields");}
    private static Map<String,Object> map(Object... values){Map<String,Object> out=new LinkedHashMap<>();for(int i=0;i<values.length;i+=2)out.put((String)values[i],values[i+1]);return out;}
    private static IOException bad(String message){return new IOException("Inspection journal: "+message);}
}
