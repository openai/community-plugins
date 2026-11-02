/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.lang.management.ManagementFactory;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.ReferenceNozzleTip.VacuumMeasurementMethod;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Actual native-model settings and save/reload. No sampling, valve action or physical evidence. */
public final class NativeVacuumSettingsTest {
    private static final Gson JSON=new Gson();
    private static Configuration config;private static Machine machine;private static ReferenceNozzle nozzle;private static ReferenceNozzleTip tip;private static ReferenceActuator valve;
    private static int checks;
    @FunctionalInterface private interface Action {void run()throws Exception;}
    private static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    private static <T>T task(Callable<T> work)throws Exception {try{return machine.submit(work,null,true).get(30,TimeUnit.SECONDS);}catch(ExecutionException e){if(e.getCause() instanceof Exception)throw(Exception)e.getCause();throw(Error)e.getCause();}}
    private static void expect(String code,Action action)throws Exception {try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"Expected "+code+", got "+e.code);}}
    private static JsonObject change()throws Exception {
        JsonObject value=NativeVacuumSettings.changeForSnapshot(config,nozzle,tip);
        value.addProperty("vacuum_sense_actuator_id",valve.getId());value.addProperty("method_part_on","Absolute");value.addProperty("method_part_off","Absolute");
        value.addProperty("part_on_low",70);value.addProperty("part_on_high",90);value.addProperty("part_off_low",0);value.addProperty("part_off_high",20);
        value.addProperty("part_on_check_after_pick",true);value.addProperty("part_on_check_align",false);value.addProperty("part_on_check_before_place",true);
        value.addProperty("part_off_check_after_place",true);value.addProperty("part_off_check_before_pick",false);value.addProperty("part_off_probe_ms",10);value.addProperty("part_off_dwell_ms",20);return value;
    }
    private static JsonObject copy(JsonObject value){return new JsonParser().parse(value.toString()).getAsJsonObject();}
    private static String state()throws Exception {return NativeVacuumSettings.changeForSnapshot(config,nozzle,tip).toString();}
    private static void setup()throws Exception {
        machine=config.getMachine();nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();valve=(ReferenceActuator)nozzle.getVacuumActuator();
        check(valve!=null&&valve.getClass()==ReferenceActuator.class,"Stock valve is an exact native actuator");
        tip=nozzle.getNozzleTip();if(tip==null)tip=(ReferenceNozzleTip)nozzle.getCompatibleNozzleTips().iterator().next();
        check(tip.getMethodPartOn()==VacuumMeasurementMethod.None&&tip.getMethodPartOff()==VacuumMeasurementMethod.None,"Stock sensing begins disabled");
    }
    private static void rejects()throws Exception {
        JsonObject good=change();String before=state();
        for(String field:new ArrayList<>(NativeVacuumSettings.FIELDS)){JsonObject bad=copy(good);bad.remove(field);expect("INVALID_ARGUMENT",()->NativeVacuumSettings.stage(config,bad));check(before.equals(state()),"Missing "+field+" made no model change");}
        JsonObject extra=copy(good);extra.addProperty("physical_qualification",true);expect("INVALID_ARGUMENT",()->NativeVacuumSettings.stage(config,extra));
        for(String key:List.of("reading_units","threshold_provenance")){JsonObject bad=copy(good);bad.addProperty(key,"Pa");expect("INVALID_ARGUMENT",()->NativeVacuumSettings.stage(config,bad));}
        for(String key:List.of("part_on_low","part_on_high","part_off_low","part_off_high")){
            JsonObject bad=copy(good);bad.addProperty(key,Double.NaN);final JsonObject nan=bad;expect("OUT_OF_RANGE",()->NativeVacuumSettings.stage(config,nan));
            bad=copy(good);bad.addProperty(key,Double.POSITIVE_INFINITY);final JsonObject nonfinite=bad;expect("OUT_OF_RANGE",()->NativeVacuumSettings.stage(config,nonfinite));
        }
        JsonObject equal=copy(good);equal.addProperty("part_on_high",70);expect("INVALID_VACUUM_RANGE",()->NativeVacuumSettings.stage(config,equal));
        JsonObject reversed=copy(good);reversed.addProperty("part_on_high",69);expect("INVALID_VACUUM_RANGE",()->NativeVacuumSettings.stage(config,reversed));
        JsonObject overlap=copy(good);overlap.addProperty("part_off_high",70);expect("OVERLAPPING_VACUUM_RANGES",()->NativeVacuumSettings.stage(config,overlap));
        JsonObject inside=copy(good);inside.addProperty("part_off_high",80);expect("OVERLAPPING_VACUUM_RANGES",()->NativeVacuumSettings.stage(config,inside));
        for(String key:List.of("method_part_on","method_part_off")){JsonObject bad=copy(good);bad.addProperty(key,"Difference");expect("UNSUPPORTED_VACUUM_METHOD",()->NativeVacuumSettings.stage(config,bad));}
        JsonObject absent=copy(good);absent.add("vacuum_sense_actuator_id",JsonNull.INSTANCE);expect("INVALID_VACUUM_BINDING",()->NativeVacuumSettings.stage(config,absent));
        JsonObject wrong=copy(good);wrong.addProperty("vacuum_actuator_id","not-installed");expect("INVALID_VACUUM_BINDING",()->NativeVacuumSettings.stage(config,wrong));
        for(String key:List.of("part_off_probe_ms","part_off_dwell_ms")){
            JsonObject bad=copy(good);bad.addProperty(key,1001);final JsonObject excess=bad;expect("UNBOUNDED_VACUUM_DWELL",()->NativeVacuumSettings.stage(config,excess));
            bad=copy(good);bad.addProperty(key,.5);final JsonObject fractional=bad;expect("INVALID_ARGUMENT",()->NativeVacuumSettings.stage(config,fractional));
        }
        JsonObject booleanString=copy(good);booleanString.addProperty("part_on_check_align","false");expect("INVALID_ARGUMENT",()->NativeVacuumSettings.stage(config,booleanString));
        tip.setEstablishPartOnLevel(true);expect("UNSUPPORTED_VACUUM_METHOD",()->NativeVacuumSettings.stage(config,good));tip.setEstablishPartOnLevel(false);
        int dwell=tip.getPickDwellMilliseconds();tip.setPickDwellMilliseconds(1001);expect("UNBOUNDED_VACUUM_DWELL",()->NativeVacuumSettings.stage(config,good));tip.setPickDwellMilliseconds(dwell);
        check(before.equals(state()),"All rejected schemas and restored native fixtures retain original settings");
    }
    private static void staleAndApply()throws Exception {
        NativeVacuumSettings.Plan stale=NativeVacuumSettings.stage(config,change());double old=tip.getVacuumLevelPartOnLow();tip.setVacuumLevelPartOnLow(old-1);
        expect("STALE_VACUUM_PLAN",stale::apply);tip.setVacuumLevelPartOnLow(old);
        stale=NativeVacuumSettings.stage(config,change());String name=valve.getName();valve.setName(name+" changed");expect("STALE_VACUUM_PLAN",stale::apply);valve.setName(name);
        NativeVacuumSettings.Plan plan=NativeVacuumSettings.stage(config,change());JsonObject expected=change();
        check(!nozzle.isPartOnEnabled(Nozzle.PartOnStep.AfterPick),"Staging does not enable sensing");plan.apply();
        check(expected.equals(NativeVacuumSettings.changeForSnapshot(config,nozzle,tip)),"Exact desired settings read back after native setters");
        check(nozzle.getVacuumActuator()==valve&&nozzle.getVacuumSenseActuator()==valve,"Sense binds the exact existing actuator; valve stays unchanged");
        check(tip.getMethodPartOn()==VacuumMeasurementMethod.Absolute&&tip.getMethodPartOff()==VacuumMeasurementMethod.Absolute,"Both native Absolute methods applied");
        check(Boolean.FALSE.equals(plan.metadata().get("source_authority_granted"))&&Boolean.FALSE.equals(plan.metadata().get("occupancy_reconciled")),"Settings grant no signal authority or occupancy reconciliation");
        expect("PATCH_ALREADY_APPLIED",plan::apply);
        List<?> affected=(List<?>)plan.metadata().get("affected_nozzle_ids");check(affected.contains(nozzle.getId()),"Shared-tip impact includes selected nozzle");
        try{((List)affected).add("forged");throw new AssertionError("Mutable shared-tip metadata");}catch(UnsupportedOperationException expectedImmutable){checks++;}
        // A byte-equivalent native actuator substitute cannot satisfy a retained identity.
        ReferenceActuator separate=new ReferenceActuator();separate.setName("VacuumSettingsTestSense");nozzle.getHead().addActuator(separate);
        JsonObject next=change();next.addProperty("vacuum_sense_actuator_id",separate.getId());NativeVacuumSettings.Plan substituted=NativeVacuumSettings.stage(config,next);
        StringWriter text=new StringWriter();Configuration.createSerializer().write(separate,text);ReferenceActuator clone=Configuration.createSerializer().read(ReferenceActuator.class,text.toString());
        nozzle.getHead().removeActuator(separate);nozzle.getHead().addActuator(clone);expect("INVALID_VACUUM_BINDING",substituted::apply);nozzle.getHead().removeActuator(clone);nozzle.getHead().addActuator(separate);
        NativeVacuumSettings.stage(config,next).apply();check(nozzle.getVacuumSenseActuator()==separate,"Existing distinct sense actuator can be selected without an actuator operation");
        JsonObject disabled=NativeVacuumSettings.changeForSnapshot(config,nozzle,tip);disabled.addProperty("method_part_on","None");disabled.addProperty("method_part_off","None");disabled.add("vacuum_sense_actuator_id",JsonNull.INSTANCE);
        disabled.addProperty("part_on_low",0);disabled.addProperty("part_on_high",0);disabled.addProperty("part_off_low",0);disabled.addProperty("part_off_high",0);
        NativeVacuumSettings.stage(config,disabled).apply();check(nozzle.getVacuumSenseActuator()==null&&tip.getMethodPartOn()==VacuumMeasurementMethod.None,"Stock disabled None/0/0/null binding is representable");
        NativeVacuumSettings.stage(config,change()).apply();
    }
    public static void main(String[] args)throws Exception {
        if(args.length==2&&"reload".equals(args[0])){
            Path root=Path.of(args[1]);Configuration.initialize(root.toFile());config=Configuration.get();config.load();machine=config.getMachine();
            JsonObject expected=JSON.fromJson(Files.readString(root.resolve("expected-vacuum.json")),JsonObject.class);
            for(Head h:machine.getHeads())for(Nozzle n:h.getNozzles())if(n.getId().equals(expected.get("nozzle_id").getAsString()))nozzle=(ReferenceNozzle)n;
            tip=(ReferenceNozzleTip)machine.getNozzleTip(expected.get("nozzle_tip_id").getAsString());
            check(expected.equals(NativeVacuumSettings.changeForSnapshot(config,nozzle,tip)),"Fresh native Configuration.load preserves typed settings and exact canonical references");
            check(!machine.isEnabled()&&!machine.isHomed(),"Saved settings restore no execution state");machine.close();System.out.println("VACUUM_SETTINGS_RELOAD_PASS");System.exit(0);
        }
        Path root=Files.createTempDirectory("native-vacuum-settings77-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();setup();
        try{
            task(()->{rejects();return null;});NativeVacuumSettings.Plan foreign=NativeVacuumSettings.stage(config,change());expect("NATIVE_EXECUTOR_REQUIRED",foreign::apply);
            task(()->{staleAndApply();config.save();Files.writeString(root.resolve("expected-vacuum.json"),state());return null;});machine.close();
            List<String> command=new ArrayList<>();command.add(Path.of(System.getProperty("java.home"),"bin/java").toString());command.addAll(ManagementFactory.getRuntimeMXBean().getInputArguments());command.addAll(List.of("-cp",System.getProperty("java.class.path"),NativeVacuumSettingsTest.class.getName(),"reload",root.toString()));
            Process child=new ProcessBuilder(command).inheritIO().start();boolean exited=child.waitFor(30,TimeUnit.SECONDS);if(!exited){child.destroy();if(!child.waitFor(2,TimeUnit.SECONDS)){child.destroyForcibly();child.waitFor(5,TimeUnit.SECONDS);}}
            check(exited&&child.exitValue()==0,"Owned fresh native reload JVM exited successfully");
            System.out.println("OPENPNP_NATIVE_VACUUM_SETTINGS_RESULT "+JSON.toJson(Bridge.map("checks",checks,"scope","native-model-settings-and-save-reload","sensor_reads",0,"valve_actions",0,"physical_qualification",false,"root",root.toString())));System.exit(0);
        }catch(Throwable failure){failure.printStackTrace();machine.close();System.exit(1);}
    }
}
