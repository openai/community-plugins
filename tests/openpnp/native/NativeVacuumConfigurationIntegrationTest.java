/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.lang.management.ManagementFactory;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.model.Configuration;
import org.openpnp.spi.*;

/** Settings integration, actual native save/reload and settings-only snapshot boundaries.
 * This suite never enables a machine, reads a sample or invokes a vacuum valve. */
public final class NativeVacuumConfigurationIntegrationTest {
    static final Gson G=new GsonBuilder().serializeNulls().create();static int checks;
    interface Action {void run()throws Exception;}
    static void check(boolean yes,String why){checks++;if(!yes)throw new AssertionError(why);}
    static void reject(String code,Action a)throws Exception {try{a.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault f){check(code.equals(f.code),"Expected "+code+", got "+f.code);}}
    static JsonArray rows(JsonObject...values){JsonArray a=new JsonArray();for(JsonObject v:values)a.add(v);return a;}
    static <T>T task(Machine m,Callable<T> a)throws Exception {try{return m.submit(a,null,true).get(30,TimeUnit.SECONDS);}catch(ExecutionException e){if(e.getCause() instanceof Exception)throw(Exception)e.getCause();throw(Error)e.getCause();}}
    static JsonObject absolute(Configuration c,ReferenceNozzle n,ReferenceNozzleTip t)throws Exception {
        JsonObject v=NativeVacuumSettings.changeForSnapshot(c,n,t);v.addProperty("vacuum_sense_actuator_id",n.getVacuumActuator().getId());
        v.addProperty("method_part_on","Absolute");v.addProperty("method_part_off","Absolute");v.addProperty("part_on_low",60);v.addProperty("part_on_high",80);v.addProperty("part_off_low",-5);v.addProperty("part_off_high",5);v.addProperty("part_off_probe_ms",0);v.addProperty("part_off_dwell_ms",0);return v;
    }
    public static void main(String[] args)throws Exception {
        if(args.length==2&&args[0].equals("reload")){reload(Path.of(args[1]));return;}
        Path root=Files.createTempDirectory("vacuum-settings78-");Configuration.initialize(root.resolve("config").toFile());Configuration c=Configuration.get();c.load();Machine m=c.getMachine();
        ReferenceNozzle n=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip t=n.getNozzleTip();AtomicInteger reads=new AtomicInteger();Object owner=new Object();VacuumSensing.ControlledSource source=null;
        try {
            SimulatorMain.accelerateFixture(c);
            source=((NullDriver)m.getDrivers().get(0)).installControlledVacuumSource(owner,Map.of(n.getVacuumActuator(),index->{reads.incrementAndGet();return "70";}),Map.of("fixture_id","settings-integration78","scenario_id","no-sampling","units","native-actuator-units"));
            VacuumSensing.ControlledSource retained=source;
            task(m,()->{
                check(NativeSettings.hasType(NativeVacuumSettings.TYPE)&&Collections.frequency(NativeSettings.types(),NativeVacuumSettings.TYPE)==1,"Dedicated sensing type is advertised once");
                JsonObject desired=absolute(c,n,t);NativeSettings.Patch patch=NativeSettings.stage(c,rows(desired));
                check(patch.requiresRetainedPlan()&&patch.isVacuumSensingChange(),"Sensing plan is retained and explicitly classified");
                NativeSettings.Patch speed=NativeSettings.stage(c,rows(G.toJsonTree(Bridge.map("type","set_machine_speed","speed",0.7)).getAsJsonObject()));
                check(!speed.isVacuumSensingChange(),"Other settings do not acquire sensing effect classification");
                reject("INVALID_PATCH",()->NativeSettings.stage(c,rows(desired,G.toJsonTree(Bridge.map("type","set_machine_speed","speed",0.5)).getAsJsonObject())));
                check("None".equals(NativeVacuumSettings.changeForSnapshot(c,n,t).get("method_part_on").getAsString()),"Staging/mixed refusal never enables sensing");
                Map<String,Object> metadata=patch.metadata().get(0);try{metadata.put("source_authority_granted",true);throw new AssertionError("Mutable plan metadata");}catch(UnsupportedOperationException expected){checks++;}
                desired.addProperty("part_on_low",61);patch.apply();check(t.getVacuumLevelPartOnLow()==60,"Retained complete plan owns immutable desired arguments");
                reject("PATCH_ALREADY_APPLIED",patch::apply);
                JsonObject readback=G.toJsonTree(NativeSettings.describe(c)).getAsJsonObject().getAsJsonArray("nozzles").get(0).getAsJsonObject().getAsJsonObject("vacuum_sensing");
                check(readback.get("settings_editable").getAsBoolean()&&readback.get("part_on_low").getAsDouble()==60&&!readback.get("source_authority_granted").getAsBoolean(),"Native settings readback exposes actual values without signal authority");
                NativeSettings.Patch stale=NativeSettings.stage(c,rows(absolute(c,n,t)));t.setVacuumLevelPartOnLow(59);reject("STALE_VACUUM_PLAN",stale::apply);check(t.getVacuumLevelPartOnLow()==59,"Stale retained plan causes no setter effects");t.setVacuumLevelPartOnLow(60);
                ReferenceNozzle shared=new ReferenceNozzle();shared.setName("Shared settings consumer");shared.addCompatibleNozzleTip(t);n.getHead().addNozzle(shared);
                NativeSettings.Patch sharedPlan=NativeSettings.stage(c,rows(absolute(c,n,t)));List<?> affected=(List<?>)sharedPlan.metadata().get(0).get("affected_nozzle_ids");
                check(affected.contains(n.getId())&&affected.contains(shared.getId())&&Boolean.TRUE.equals(sharedPlan.metadata().get(0).get("tip_settings_shared")),"Metadata identifies each actual shared-tip consumer");n.getHead().removeNozzle(shared);reject("STALE_VACUUM_PLAN",sharedPlan::validateCurrentState);
                JsonObject currentChange=absolute(c,n,t);ReferenceNozzleTip.VacuumMeasurementMethod previous=t.getMethodPartOn();t.setMethodPartOn(null);
                reject("UNSUPPORTED_VACUUM_METHOD",()->NativeSettings.stage(c,rows(currentChange)));
                JsonObject unknown=G.toJsonTree(NativeSettings.describe(c)).getAsJsonObject().getAsJsonArray("nozzles").get(0).getAsJsonObject().getAsJsonObject("vacuum_sensing");
                check(!unknown.get("settings_editable").getAsBoolean()&&unknown.get("code").getAsString().equals("UNSUPPORTED_VACUUM_METHOD")&&NativeVacuumSettings.readMethod(t,true)==null,"Installed raw-null method is refused and never normalized by readback or staging");t.setMethodPartOn(previous);
                ReferenceNozzleTip unloaded=new ReferenceNozzleTip();unloaded.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);unloaded.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.None);unloaded.setVacuumLevelPartOnLow(10);unloaded.setVacuumLevelPartOnHigh(20);unloaded.setPartOnCheckAlign(false);unloaded.setPartOffProbingMilliseconds(17);m.addNozzleTip(unloaded);n.addCompatibleNozzleTip(unloaded);
                JsonObject unloadedView=tipReadback(c,unloaded.getId());
                check(unloadedView.get("readback_only").getAsBoolean()&&unloadedView.get("requires_nozzle_binding_for_edit").getAsBoolean()&&unloadedView.get("method_part_on").getAsString().equals("Absolute")&&unloadedView.get("method_part_off").getAsString().equals("None")&&unloadedView.get("part_on_low").getAsDouble()==10&&unloadedView.get("part_on_high").getAsDouble()==20&&!unloadedView.get("part_on_check_align").getAsBoolean()&&unloadedView.get("part_off_probe_ms").getAsInt()==17,"Unloaded compatible tip has precise independent native sensing readback");
                check(unloadedView.getAsJsonArray("compatible_nozzle_ids").size()==1&&unloadedView.getAsJsonArray("compatible_nozzle_ids").get(0).getAsString().equals(n.getId())&&unloadedView.getAsJsonArray("installed_on_nozzle_ids").size()==0,"Tip readback distinguishes compatibility from actual installed state");
                for(String key:List.of("source_authority_granted","occupancy_reconciled","measurement_performed","physical_qualification"))check(!unloadedView.get(key).getAsBoolean(),"Unloaded tip readback denies "+key);
                unloaded.setVacuumLevelPartOnLow(Double.NaN);check(tipReadback(c,unloaded.getId()).get("part_on_low").isJsonNull()&&Double.isNaN(unloaded.getVacuumLevelPartOnLow()),"Invalid threshold readback is nullable and leaves native NaN untouched");unloaded.setVacuumLevelPartOnLow(10);
                unloaded.setMethodPartOn(null);check(tipReadback(c,unloaded.getId()).get("method_part_on").isJsonNull(),"Unknown unloaded native method stays explicitly nullable");java.lang.reflect.Field methodField=ReferenceNozzleTip.class.getDeclaredField("methodPartOn");methodField.setAccessible(true);check(methodField.get(unloaded)==null,"Unused tip readback never normalizes unknown to None");
                n.removeCompatibleNozzleTip(unloaded);m.removeNozzleTip(unloaded);
                NativeConfigurationSnapshots.Snapshot snapshot=NativeConfigurationSnapshots.capture(c);JsonObject document=G.toJsonTree(snapshot.document).getAsJsonObject();
                Set<String> omissions=new HashSet<>();for(JsonElement row:document.getAsJsonArray("omissions"))omissions.add(row.getAsJsonObject().get("type").getAsString());
                check(omissions.containsAll(List.of("vacuum_sensing_settings","vacuum_sensing_source","nozzle_occupancy")),"Snapshot explicitly omits settings, live source authority and occupancy");
                for(JsonElement row:document.getAsJsonArray("typed_changes"))check(!NativeVacuumSettings.TYPE.equals(row.getAsJsonObject().get("type").getAsString()),"Snapshot does not emit a sensing restore edit");
                NativeConfigurationSnapshots.Snapshot decoded=NativeConfigurationSnapshots.decode(document);
                JsonObject injected=new JsonParser().parse(document.toString()).getAsJsonObject();injected.getAsJsonArray("typed_changes").add(absolute(c,n,t));reject("SNAPSHOT_INVALID",()->NativeConfigurationSnapshots.decode(injected));
                t.setVacuumLevelPartOnLow(62);Map<String,Object> provenance=VacuumSensing.sourceProvenance(n);String current=NativeVacuumSettings.changeForSnapshot(c,n,t).toString();
                NativeConfigurationSnapshots.Restore restore=NativeConfigurationSnapshots.prepareRestore(c,decoded);restore.apply();
                check(current.equals(NativeVacuumSettings.changeForSnapshot(c,n,t).toString()),"Typed restore preserves newer sensing settings");
                check(provenance.equals(VacuumSensing.sourceProvenance(n))&&!retained.isClosed()&&n.getPart()==null,"Typed restore does not replace existing source or native empty part state");
                for(String key:List.of("sensing_settings_restored","sensing_source_authority_restored","nozzle_occupancy_restored"))check(Boolean.FALSE.equals(restore.report.get(key)),"Restore report denies "+key);
                JsonObject disabled=NativeVacuumSettings.changeForSnapshot(c,n,t);disabled.addProperty("method_part_on","None");disabled.addProperty("method_part_off","None");disabled.add("vacuum_sense_actuator_id",JsonNull.INSTANCE);
                for(String k:List.of("part_on_low","part_on_high","part_off_low","part_off_high"))disabled.addProperty(k,0);
                for(String k:List.of("part_on_check_after_pick","part_on_check_align","part_on_check_before_place","part_off_check_after_place","part_off_check_before_pick"))disabled.addProperty(k,false);
                NativeSettings.stage(c,rows(disabled)).apply();check(n.getVacuumSenseActuator()==null&&disabled.equals(NativeVacuumSettings.changeForSnapshot(c,n,t)),"None/zero thresholds/disabled flags/null sensor is represented exactly");
                NativeSettings.stage(c,rows(absolute(c,n,t))).apply();c.save();Files.writeString(root.resolve("expected.json"),NativeVacuumSettings.changeForSnapshot(c,n,t).toString());
                check(reads.get()==0&&!m.isEnabled()&&!m.isHomed(),"Settings and snapshots performed no sample reads and grant no execution state");return null;
            });
            source.close(owner);source=null;m.close();
            List<String> command=new ArrayList<>();command.add(Path.of(System.getProperty("java.home"),"bin/java").toString());command.addAll(ManagementFactory.getRuntimeMXBean().getInputArguments());command.addAll(List.of("-cp",System.getProperty("java.class.path"),NativeVacuumConfigurationIntegrationTest.class.getName(),"reload",root.toString()));
            Process child=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(root.resolve("reload.log").toFile()).start();boolean exit=child.waitFor(35,TimeUnit.SECONDS);if(!exit){child.destroyForcibly();child.waitFor(5,TimeUnit.SECONDS);}check(exit&&child.exitValue()==0,"Fresh settings reload child: "+Files.readString(root.resolve("reload.log")));
            System.out.println("OPENPNP_NATIVE_VACUUM_CONFIGURATION_RESULT "+G.toJson(Bridge.map("checks",checks,"fresh_reload",true,"sensor_reads",reads.get(),"physical_qualification",false,"root",root.toString())));System.exit(0);
        }catch(Throwable e){e.printStackTrace();if(source!=null)source.close(owner);m.close();System.exit(1);}
    }
    static JsonObject tipReadback(Configuration c,String id)throws Exception {
        for(JsonElement e:G.toJsonTree(NativeSettings.describe(c)).getAsJsonObject().getAsJsonArray("nozzle_tips"))if(e.getAsJsonObject().get("nozzle_tip_id").getAsString().equals(id))return e.getAsJsonObject().getAsJsonObject("vacuum_sensing");
        throw new AssertionError("Missing tip readback: "+id);
    }
    static void reload(Path root)throws Exception {
        Configuration.initialize(root.resolve("config").toFile());Configuration c=Configuration.get();c.load();Machine m=c.getMachine();try{
            JsonObject expected=new JsonParser().parse(Files.readString(root.resolve("expected.json"))).getAsJsonObject();ReferenceNozzle n=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip t=(ReferenceNozzleTip)m.getNozzleTip(expected.get("nozzle_tip_id").getAsString());
            check(expected.equals(NativeVacuumSettings.changeForSnapshot(c,n,t)),"Fresh native load preserves integrated settings and canonical bindings");
            check("native-driver".equals(VacuumSensing.sourceProvenance(n).get("origin")),"Fresh native load does not transfer the process-local source");
            reject(NativePortableVacuum.CODE,()->NativePortableConfiguration.quiescent(c));check(!m.isEnabled()&&!m.isHomed(),"Reload grants no enable/home state");
            System.out.println("OPENPNP_NATIVE_VACUUM_CONFIGURATION_RELOAD_RESULT "+G.toJson(Bridge.map("checks",checks,"sample_reads",0,"execution_authority",false)));
        }finally{m.close();}System.exit(0);
    }
}
