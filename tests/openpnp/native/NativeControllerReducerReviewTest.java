/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;

/** Reducer-only independent review. Reads captured Bridge receipts; never creates native objects or sockets. */
public final class NativeControllerReducerReviewTest {
    private static final Gson JSON=new GsonBuilder().serializeNulls().create();
    private static final String OTHER="00000000-0000-4000-8000-000000000001";
    private static final List<Map<String,Object>> checks=new ArrayList<>();
    private static List<Map<String,Object>> events;
    private static Map<String,Object> admitted,terminal;
    interface Checked {void run()throws Exception;}
    static Map<String,Object> map(Object...values){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<values.length;i+=2)m.put((String)values[i],values[i+1]);return m;}
    @SuppressWarnings("unchecked") static Map<String,Object> obj(Object x){return (Map<String,Object>)x;}
    @SuppressWarnings("unchecked") static List<Map<String,Object>> array(Object x){return (List<Map<String,Object>>)x;}
    static Map<String,Object> copy(Map<String,Object> x){return NativeJournalJson.parseObject(JSON.toJson(x));}
    static Map<String,Object> payload(int i){return copy(obj(events.get(i).get("payload")));}
    static String type(int i){return (String)events.get(i).get("type");}
    static Map<String,Map<String,Object>> operations(){Map<String,Map<String,Object>> m=new LinkedHashMap<>();m.put((String)admitted.get("operation_id"),copy(admitted));return m;}
    static void need(boolean yes,String message){if(!yes)throw new AssertionError(message);}
    static void equal(Object a,Object b,String message){need(JSON.toJson(a).equals(JSON.toJson(b)),message);}
    static void check(String name,Checked action){try{action.run();checks.add(map("name",name,"passed",true));}catch(Throwable t){checks.add(map("name",name,"passed",false,"error_type",t.getClass().getName(),"message",String.valueOf(t.getMessage())));}}
    static void rejects(Checked action)throws Exception{try{action.run();}catch(IOException|IllegalArgumentException expected){return;}throw new AssertionError("Reducer accepted invalid input");}
    static NativeControllerJournal prefix(int count)throws Exception{NativeControllerJournal j=new NativeControllerJournal();for(int i=0;i<count;i++)j.accept(type(i),payload(i),operations());return j;}
    static void altered(String name,int index,int prefix,String key,Object value){check(name,()->{Map<String,Object> p=payload(index);p.put(key,value);NativeControllerJournal j=prefix(prefix);rejects(()->j.accept(type(index),p,operations()));});}
    static void nestedAltered(String name,int index,int prefix,String[] path,Object value){check(name,()->{Map<String,Object> p=payload(index),cursor=p;for(int i=0;i<path.length-1;i++){need(cursor.get(path[i]) instanceof Map,"Captured observation lacks "+path[i]);cursor=obj(cursor.get(path[i]));}cursor.put(path[path.length-1],value);NativeControllerJournal j=prefix(prefix);rejects(()->j.accept(type(index),p,operations()));});}
    static void run()throws Exception{
        need(events.size()==10,"Expected actual complete admission/four paired steps/retirement transcript");
        check("actual-normal-transcript-recovers",()->{NativeControllerJournal j=prefix(events.size());j.finishRecovery(copy(terminal));need(j.hasHistory(),"Missing history");equal(j.operationId(),admitted.get("operation_id"),"Original operation changed");need(Boolean.TRUE.equals(j.snapshot().get("retired")),"Missing retirement");});
        check("foreign-prefix-not-selected",()->need(!NativeControllerJournal.matches("native_effect_intent"),"Unrelated journal event selected"));
        check("unknown-controller-prefix-rejected",()->rejects(()->prefix(1).accept("controller_diagnostic_future_v2",payload(1),operations())));
        check("admission-requires-operation",()->rejects(()->new NativeControllerJournal().accept(type(0),payload(0),new LinkedHashMap<>())));
        for(String key:List.of("request_id","request_digest","bridge_instance_id","config_revision","controller_instance_id")){
            check("admission-operation-mismatch-"+key,()->{Map<String,Map<String,Object>> ops=operations();ops.values().iterator().next().put(key,key.equals("request_digest")?"0".repeat(64):OTHER);rejects(()->new NativeControllerJournal().accept(type(0),payload(0),ops));});
        }
        check("admission-operation-own-id-mismatch",()->{Map<String,Map<String,Object>> ops=operations();ops.values().iterator().next().put("operation_id",OTHER);rejects(()->new NativeControllerJournal().accept(type(0),payload(0),ops));});
        for(String key:List.of("state","method"))check("admission-operation-invalid-"+key,()->{Map<String,Map<String,Object>> ops=operations();ops.values().iterator().next().put(key,"foreign");rejects(()->new NativeControllerJournal().accept(type(0),payload(0),ops));});
        for(String key:List.of("controller_instance_id","operation_id","request_id","bridge_instance_id","machine_id","owner_generation"))altered("admission-invalid-uuid-"+key,0,0,key,"not-a-uuid");
        for(Object value:List.of(0,2,1.5,"1",true))altered("admission-version-"+value,0,0,"schema_version",value);
        for(Object value:List.of(0,-1,1.5,"1",9007199254740992L))altered("admission-epoch-"+value,0,0,"ownership_epoch",value);
        altered("admission-foreign-profile",0,0,"profile","other-controller");
        altered("admission-invalid-digest",0,0,"request_digest","bad");
        altered("admission-invalid-revision",0,0,"config_revision","abc");
        altered("admission-unknown-field",0,0,"extra",false);
        check("admission-missing-field",()->{Map<String,Object> p=payload(0);p.remove("owner_generation");rejects(()->new NativeControllerJournal().accept(type(0),p,operations()));});
        check("admission-cannot-repeat",()->rejects(()->prefix(1).accept(type(0),payload(0),operations())));
        for(int i:new int[]{1,2,3,9}){
            final int index=i;
            altered("event-foreign-operation-"+i,i,i,"operation_id",OTHER);
            altered("event-foreign-controller-"+i,i,i,"controller_instance_id",OTHER);
            altered("event-version-"+i,i,i,"schema_version",2);
            altered("event-unknown-field-"+i,i,i,"extra",0);
            check("event-missing-operation-"+i,()->{Map<String,Object> p=payload(index);p.remove("operation_id");rejects(()->prefix(index).accept(type(index),p,operations()));});
        }
        for(Object value:List.of(0,2,5,1.5,"1",-1))altered("intent-index-"+value,1,1,"step_index",value);
        altered("intent-wrong-step",1,1,"step","connect");
        altered("intent-nonobject-observation",1,1,"before","bad");
        nestedAltered("intent-physical-qualification",1,1,new String[]{"before","physical_qualification"},true);
        check("intent-before-admission",()->rejects(()->prefix(0).accept(type(1),payload(1),operations())));
        check("outcome-before-intent",()->rejects(()->prefix(1).accept(type(2),payload(2),operations())));
        check("duplicate-pending-intent",()->rejects(()->prefix(2).accept(type(1),payload(1),operations())));
        check("duplicate-outcome",()->rejects(()->prefix(3).accept(type(2),payload(2),operations())));
        check("skip-connect-step",()->rejects(()->prefix(3).accept(type(5),payload(5),operations())));
        altered("outcome-wrong-index",2,2,"step_index",2);
        altered("outcome-wrong-step",2,2,"step","connect");
        altered("outcome-dispatched-type",2,2,"dispatched","true");
        altered("outcome-returned-type",2,2,"native_returned",1);
        altered("outcome-return-without-dispatch",2,2,"dispatched",false);
        altered("outcome-error-type",2,2,"error_type",17);
        altered("outcome-error-size",2,2,"error_type","X".repeat(201));
        altered("outcome-nonobject-observation",2,2,"after",false);
        nestedAltered("outcome-physical-qualification",2,2,new String[]{"after","physical_qualification"},true);
        check("failed-native-outcome-stops-recipe",()->{NativeControllerJournal j=prefix(2);Map<String,Object> p=payload(2);p.put("native_returned",false);p.put("error_type","java.io.IOException");j.accept(type(2),p,operations());rejects(()->j.accept(type(3),payload(3),operations()));});
        check("complete-retirement-with-pending-rejected",()->rejects(()->prefix(8).accept(type(9),payload(9),operations())));
        check("complete-retirement-with-partial-recipe-rejected",()->rejects(()->prefix(3).accept(type(9),payload(9),operations())));
        altered("retirement-physical-standstill",9,9,"physical_standstill_verified",true);
        altered("retirement-completion-type",9,9,"completed_recipe","true");
        check("event-after-retirement",()->rejects(()->prefix(10).accept(type(1),payload(1),operations())));
        check("duplicate-retirement",()->rejects(()->prefix(10).accept(type(9),payload(9),operations())));
        for(int n=1;n<10;n++){final int count=n;check("success-requires-full-durable-recipe-prefix-"+n,()->rejects(()->prefix(count).finishRecovery(copy(terminal))));}
        for(String key:List.of("operation_id","request_id","request_digest","bridge_instance_id","config_revision","controller_instance_id","method"))check("recovery-original-operation-binding-"+key,()->{Map<String,Object> op=copy(terminal);op.put(key,OTHER);rejects(()->prefix(10).finishRecovery(op));});
        check("snapshot-not-live-list",()->{NativeControllerJournal j=prefix(3);Map<String,Object> old=j.snapshot(),frozen=copy(old);j.accept(type(3),payload(3),operations());j.accept(type(4),payload(4),operations());equal(old,frozen,"Previously returned snapshot changed");});
        check("snapshot-binding-mutation-does-not-change-reducer",()->{NativeControllerJournal j=prefix(3);Map<String,Object> before=j.snapshot(),publicCopy=j.snapshot();try{obj(publicCopy.get("binding")).put("request_id",OTHER);}catch(UnsupportedOperationException allowed){}equal(j.snapshot(),before,"Public binding mutates reducer");});
        check("snapshot-list-mutation-does-not-change-reducer",()->{NativeControllerJournal j=prefix(3);Map<String,Object> before=j.snapshot(),publicCopy=j.snapshot();try{array(publicCopy.get("steps")).clear();}catch(UnsupportedOperationException allowed){}equal(j.snapshot(),before,"Public step list mutates reducer");});
        check("snapshot-nested-observation-mutation-does-not-change-reducer",()->{NativeControllerJournal j=prefix(3);Map<String,Object> before=j.snapshot(),publicCopy=j.snapshot();try{obj(obj(array(publicCopy.get("steps")).get(0).get("after")).get("native")).put("owner_thread_id",-1);}catch(UnsupportedOperationException allowed){}equal(j.snapshot(),before,"Nested public observation mutates reducer");});
        check("prepare-does-not-publish-before-commit",()->{NativeControllerJournal j=prefix(1);Map<String,Object> before=j.snapshot();Runnable commit=j.prepare(type(1),payload(1),operations());equal(j.snapshot(),before,"Prepare changed durable reducer");commit.run();need(j.snapshot().get("pending")!=null,"Commit missing intent");});
        check("prepared-payload-detached-from-caller",()->{NativeControllerJournal j=prefix(1);Map<String,Object> p=payload(1);Runnable commit=j.prepare(type(1),p,operations());p.put("step","foreign");obj(p.get("before")).put("phase","foreign");commit.run();equal(obj(j.snapshot().get("pending")).get("step"),"bind","Caller changed prepared payload");});
        check("rejected-prepare-leaves-committed-state",()->{NativeControllerJournal j=prefix(1);Map<String,Object> before=j.snapshot(),p=payload(1);p.put("step_index",9);rejects(()->j.prepare(type(1),p,operations()));equal(j.snapshot(),before,"Rejected prepare mutated state");});
        nestedAltered("observation-protocol-generation-mismatch",4,4,new String[]{"after","native","protocol","generation"},"0".repeat(32));
        nestedAltered("observation-model-fingerprint-mismatch",4,4,new String[]{"after","native","model_binding_sha256"},"0".repeat(64));
        nestedAltered("observation-owner-thread-mismatch",4,4,new String[]{"after","native","owner_thread_id"},9007199254740990L);
        nestedAltered("observation-sequence-regression",4,4,new String[]{"after","observation_sequence"},1);
        nestedAltered("observation-wire-counter-negative",4,4,new String[]{"after","native","protocol","wire_bytes_written"},-1);
        nestedAltered("observation-command-counter-regression",4,4,new String[]{"after","native","protocol","commands_attempted"},0);
        nestedAltered("observation-counter-invalid-type",4,4,new String[]{"after","native","protocol","commands_attempted"},"2");
        nestedAltered("observation-native-physical-qualification",4,4,new String[]{"after","native","physical_qualification"},true);
        nestedAltered("observation-protocol-physical-standstill",4,4,new String[]{"after","native","protocol","physical_standstill_verified"},true);
        check("observation-oversize-rejected",()->{Map<String,Object> p=payload(4);obj(p.get("after")).put("extra","A".repeat(17000));rejects(()->prefix(4).accept(type(4),p,operations()));});
    }
    public static void main(String[] args)throws Exception{
        Map<String,Object> fixture=JSON.fromJson(Files.readString(Path.of(args[0])),Map.class);
        events=array(fixture.get("controller_events"));admitted=obj(fixture.get("admitted_operation"));terminal=obj(fixture.get("terminal_operation"));
        run();long failures=checks.stream().filter(x->!Boolean.TRUE.equals(x.get("passed"))).count();
        Map<String,Object> report=map("passed",failures==0,"checks",checks.size(),"failures",failures,"cases",checks,"scope","offline typed reducer only; captured actual Bridge observations; no network/native operations","physical_qualification",false);
        Files.writeString(Path.of(args[1]),JSON.toJson(report)+"\n");System.out.println("reducer checks="+checks.size()+" failures="+failures);if(failures!=0)System.exit(1);
    }
}
