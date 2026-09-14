/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;

/** Native Bridge durability, request deduplication, retained guards and partial-write fencing. */
public final class NativeNozzleAssemblyBridgeTest {
 static final Gson G=new Gson();static Configuration c;static Machine m;static Bridge bridge;static String session;static int checks;
 static void check(boolean b,String why){checks++;if(!b)throw new AssertionError(why);}
 static JsonObject obj(Object...v){return G.toJsonTree(Bridge.map(v)).getAsJsonObject();}
 @SuppressWarnings("unchecked")static Map<String,Object> call(String name,JsonObject args)throws Exception{return (Map<String,Object>)bridge.call("openpnp_"+name,args);}
 static String revision()throws Exception{return(String)call("get_status",obj()).get("config_revision");}
 static JsonObject mutate(Object...v)throws Exception{JsonObject j=obj(v);j.addProperty("session_id",session);j.addProperty("request_id",UUID.randomUUID().toString());j.addProperty("expected_config_revision",revision());return j;}
 static Map<String,Object> waitFor(Map<String,Object> op,String expected)throws Exception{long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(90);while(List.of("accepted","running").contains(op.get("state"))){if(System.nanoTime()>end)throw new AssertionError("deadline");Thread.sleep(5);op=call("get_operation",obj("operation_id",op.get("operation_id")));}check(expected.equals(op.get("state")),"terminal: "+op);while(m.isBusy())Thread.sleep(5);return op;}
 static Map<String,Object> plan(JsonObject j)throws Exception{return call("plan_configuration",mutate("changes",NativeNozzleAssemblyTest.rows(j)));}
 static void expect(String code,NativeNozzleAssemblyTest.Run action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"expected "+code+" got "+e.code);}}
 public static void main(String[] args)throws Exception{
  Path root=Files.createTempDirectory("native-topology-bridge61-");Path dir=root.resolve("config");Configuration.initialize(dir.toFile());c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.settleFreshFixture(dir);c=Configuration.get();m=c.getMachine();Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());bridge=new Bridge(c,token,root.resolve("journal"),Path.of(args[0]),0,true);session=(String)call("request_control_session",obj("request_id","topology-grant","ttl_seconds",600)).get("session_id");
  try{
   JsonObject j=NativeNozzleAssemblyTest.proposal(c);Map<String,Object> stale=plan(j);ReferenceNozzle old=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();String oldName=old.getName();old.setName("Native outside edit");String rev=revision();Map<String,Object> refused=waitFor(call("apply_configuration",mutate("plan_id",stale.get("plan_id"))),"failed");check("STALE_TOPOLOGY_PLAN".equals(((Map<?,?>)refused.get("result")).get("code")),"native drift guard precedes effects");check(rev.equals(revision()),"stale plan preserves revision");old.setName(oldName);
   Map<String,Object> p=plan(j);JsonObject request=mutate("plan_id",p.get("plan_id"));Map<String,Object> accepted=call("apply_configuration",request),duplicate=call("apply_configuration",request);check(accepted.get("operation_id").equals(duplicate.get("operation_id")),"same request returns same native operation");Map<String,Object> done=waitFor(accepted,"succeeded");Map<?,?> result=(Map<?,?>)done.get("result"),created=(Map<?,?>)result.get("created_assembly"),preimage=(Map<?,?>)result.get("recovery_preimage");check(created.get("nozzle_id")!=null,"generated IDs returned");check(m.getDefaultHead().getNozzles().size()==2,"dedup prevents duplicate assembly");check(!m.isHomed()&&!m.isEnabled(),"creation leaves native execution disabled/unhomed");check(!rev.equals(revision()),"creation advances revision");
   NativeNozzleAssembly.verify(c,(Map<String,Object>)created,j);Map<String,Object> file=call("get_artifact",obj("artifact_id",preimage.get("artifact_id")));check(file.toString().contains(preimage.get("sha256").toString()),"recovery artifact accessible through read-only API");
   String journal=Files.readString(root.resolve("journal/operations.jsonl"));check(journal.indexOf("topology_recovery_available")<journal.indexOf("nozzle-assembly-configuration-model"),"forced recovery event precedes first native effect");
   JsonObject next=NativeNozzleAssemblyTest.proposal(c);next.addProperty("nozzle_name","Second simulator nozzle");next.addProperty("tip_name","Second simulator tip");next.addProperty("valve_name","Second simulator valve");next.add("exclusive_package_ids",G.toJsonTree(List.of("R0603")));Map<String,Object> failing=plan(next);
   // Test-only public native listener: throw after the actual list insertion, not a mocked setter.
   java.beans.PropertyChangeListener listener=event->{if("axes".equals(event.getPropertyName()))throw new AssertionError("injected native axis insertion listener failure");};((ReferenceMachine)m).addPropertyChangeListener(listener);
   Map<String,Object> unknown=waitFor(call("apply_configuration",mutate("plan_id",failing.get("plan_id"))),"outcome_unknown");((ReferenceMachine)m).removePropertyChangeListener(listener);
   Map<String,Object> status=call("get_status",obj());check(Boolean.TRUE.equals(status.get("configuration_fault")),"partial native listener failure fences configuration");expect("CONFIGURATION_FAULT",()->plan(next));check(m.getDefaultHead().getNozzles().size()==2,"failed partial graph is never retried/rolled back");
   List<JsonObject> recoveryEvents=new ArrayList<>();for(String line:Files.readAllLines(root.resolve("journal/operations.jsonl"))){JsonObject row=G.fromJson(line,JsonObject.class);if("topology_recovery_available".equals(row.get("type").getAsString()))recoveryEvents.add(row);}check(recoveryEvents.size()==2,"each admitted creation retained a distinct preimage");JsonObject failedRecovery=recoveryEvents.get(1).getAsJsonObject("payload");check(unknown.get("operation_id").equals(failedRecovery.get("operation_id").getAsString()),"partial outcome maps to exact recovery artifact");
   JsonObject a=failedRecovery.getAsJsonObject("artifact");Path artifact=root.resolve("journal").resolve(a.get("artifact_id").getAsString()+".artifact");check(NativePortableConfiguration.sha(Files.readAllBytes(artifact)).equals(a.get("sha256").getAsString()),"preimage bytes retain durable hash after failure");Files.writeString(root.resolve("failed-recovery.json"),G.toJson(failedRecovery));
   System.out.println("OPENPNP_NATIVE_TOPOLOGY_BRIDGE_RESULT "+G.toJson(Bridge.map("checks",checks,"root",root.toString(),"outcome_unknown",unknown.get("operation_id"),"recovery_artifact",a,"simulation_only",true,"physical_qualification",false)));bridge.close();m.close();System.exit(0);
  }catch(Throwable e){e.printStackTrace();bridge.close();m.close();System.exit(1);}
 }
}
