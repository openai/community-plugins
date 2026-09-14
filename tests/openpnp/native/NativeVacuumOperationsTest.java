/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Actual native read/check/job behavior. Controlled source is scripted from explicit test stages,
 * not valve commands, physical pressure or a replacement nozzle/job processor. */
public final class NativeVacuumOperationsTest {
    private static int checks;
    private static final List<String> cases = new ArrayList<>();
    public static void main(String[] args) throws Exception {
        int exit=0;
        try {
            operations();
            try(Fixture units=new Fixture("wrong-units", "Pa")) {
                expect("SENSING_SOURCE_UNQUALIFIED",()->NativeVacuumSensing.admit(units.config,units.nozzle.getId()));
                check(units.events.isEmpty(),"declared physical units never authorize native-unit sensing");
            }
            job("success",1,1);
            job("missed-pick-retry",2,1);
            job("retained-after-place",1,0);
            job("lost-before-place",1,0);
            System.out.println("OPENPNP_NATIVE_VACUUM_OPERATIONS_RESULT "+new Gson().toJson(Map.of(
                "passed",cases,"assertions",checks,"simulation_only",true,"physical_qualification",false,
                "scope","private-native-adapter-and-job-behavior; no-public-Bridge-or-restart-qualification")));
        } catch(Throwable failure) { failure.printStackTrace(); exit=1; }
        System.exit(exit);
    }
    private static void operations() throws Exception {
        try(Fixture f=new Fixture("operations")) {
            ReferenceHead head=(ReferenceHead)f.nozzle.getHead();
            ReferenceActuator valve=(ReferenceActuator)f.nozzle.getVacuumActuator();
            head.setPumpActuator(valve);
            expect("SENSING_PROFILE_UNSUPPORTED",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()));
            head.setPumpActuator(null);
            valve.setValueType(Actuator.ActuatorValueType.Profile);
            expect("SENSING_PROFILE_UNSUPPORTED",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()));
            valve.setValueType(Actuator.ActuatorValueType.Boolean);
            ReferenceControllerAxis z=(ReferenceControllerAxis)f.nozzle.getAxisZ();
            f.nozzle.setAxisZ(null);
            expect("SENSING_SAFE_Z_REQUIRED",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()));
            f.nozzle.setAxisZ(z);
            z.setSafeZoneLowEnabled(false);
            expect("SENSING_SAFE_Z_REQUIRED",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()));
            z.setSafeZoneLowEnabled(true);
            Length originalLow=z.getSafeZoneLow();z.setSafeZoneLow(new Length(Double.NaN,LengthUnit.Millimeters));
            expect("SENSING_SAFE_Z_REQUIRED",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()));
            z.setSafeZoneLow(originalLow);
            check(f.events.isEmpty(),"unsupported pump, Profile valve and Safe Z refuse before native observations");
            NativeVacuumSensing.Plan binding=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            int originalIndex=valve.getIndex();valve.setIndex(originalIndex+1);
            expect("SENSING_CONFIGURATION_CHANGED",binding::validateCurrentState);valve.setIndex(originalIndex);
            NativeVacuumSensing.Plan p=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            expect("NATIVE_EXECUTOR_REQUIRED",()->p.measure(2,()->{},f.observer));
            Map<String,Object> sample=f.task(()->p.measure(2,()->{},f.observer));
            check(sample.get("samples").equals(List.of(70.0,70.0)),"actual native repeated readings");
            check(Boolean.FALSE.equals(sample.get("part_state_inferred")),"passive samples do not infer part state");
            check(f.events.stream().filter(e->e.get("event").equals("read.returned")).count()==2,"two actual native read callbacks");
            f.task(()->{expect("SENSING_PLAN_CONSUMED",()->p.measure(1,()->{},f.observer));return null;});
            NativeVacuumSensing.Plan stale=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            f.tip.setVacuumLevelPartOnHigh(81);
            f.task(()->{expect("SENSING_CONFIGURATION_CHANGED",()->stale.measure(1,()->{},f.observer));return null;});
            f.tip.setVacuumLevelPartOnHigh(80);
            NativeVacuumSensing.Plan tooMany=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            f.task(()->{expect("SENSING_SAMPLE_LIMIT",()->tooMany.measure(33,()->{},f.observer));return null;});
            int before=f.events.size();
            NativeVacuumSensing.Plan denied=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            f.task(()->{expect("OWNERSHIP_REVOKED",()->denied.measure(1,()->{throw new Bridge.Fault("OWNERSHIP_REVOKED","test expired owner");},f.observer));return null;});
            check(f.events.size()==before,"owner rejection performs zero native reads");
            f.enableHome();
            Map<String,Object> on=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_on",()->{},f.observer));
            check(Boolean.TRUE.equals(on.get("native_verdict")),"actual native absolute part-on pass");
            f.raw.set("0");
            Map<String,Object> off=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},f.observer));
            check(Boolean.TRUE.equals(off.get("native_verdict")),"actual native part-off pulse pass");
            check(f.events.stream().filter(e->e.get("event").equals("valve.returned")).count()==2,"actual probe open and close callbacks");
            check(Boolean.FALSE.equals(f.nozzle.getVacuumActuator().getLastActuationValue()),"native finally leaves valve command off");
            f.tip.setPartOffDwellMilliseconds(1);f.events.clear();
            Map<String,Object> delayed=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},f.observer));
            List<String> order=new ArrayList<>();
            for(Map<String,Object> event:f.events) {
                if("valve.returned".equals(event.get("event")))order.add(Boolean.TRUE.equals(event.get("enabled"))?"on":"off");
                if("read.returned".equals(event.get("event")))order.add("read");
            }
            check(order.equals(List.of("on","off","read")),"positive native dwell reads after valve-off");
            check(Boolean.FALSE.equals(delayed.get("native_effects_ordered")),"receipt effect kinds do not fabricate order");
            f.tip.setPartOffDwellMilliseconds(0);f.events.clear();
            f.task(()->{
                NativeVacuumSensing.Plan changed=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
                expect("SENSING_SAFE_Z_REQUIRED",()->changed.verify("part_off",()->f.machine.setHomed(false),f.observer));return null;
            });
            check(f.events.isEmpty(),"guard invalidated homing refuses before native probe");f.enableHome();f.events.clear();
            f.task(()->{
                NativeVacuumSensing.Plan changed=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
                expectCause("SENSING_SAFE_Z_REQUIRED",()->changed.verify("part_off",()->{},(event,n,data)->{
                    f.observer.onEvent(event,n,data);if(event.equals("check.before"))f.machine.setHomed(false);
                }));return null;
            });
            check(f.events.stream().noneMatch(e->e.get("event").equals("valve.returned")),"observer invalidated homing cannot dispatch probe");
            f.enableHome();f.raw.set("NaN");f.events.clear();
            f.task(()->{expectCause("SENSOR_VALUE_NONFINITE",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},f.observer));return null;});
            check(Boolean.FALSE.equals(valve.getLastActuationValue()),"malformed native sample still closes probe valve");
            check(f.events.stream().noneMatch(e->e.get("event").equals("check.returned")),"invalid sample never returns a verdict");
            f.raw.set("0");f.events.clear();AtomicBoolean leaseLost=new AtomicBoolean();
            f.task(()->{
                NativeVacuumSensing.Plan interrupted=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
                expectCause("OWNERSHIP_REVOKED",()->interrupted.verify("part_off",()->{if(leaseLost.get())throw new Bridge.Fault("OWNERSHIP_REVOKED","test lease lost after valve-on");},(event,n,data)->{
                    f.observer.onEvent(event,n,data);if(event.equals("valve.returned")&&Boolean.TRUE.equals(data.get("enabled")))leaseLost.set(true);
                }));
                expect("SENSING_PLAN_CONSUMED",()->interrupted.verify("part_off",()->{},f.observer));return null;
            });
            check(Boolean.FALSE.equals(valve.getLastActuationValue()),"ownership loss after on preserves mandatory native valve-off");
            check(f.events.stream().noneMatch(e->e.get("event").equals("read.returned")),"ownership loss prevents following sample");
            NativeVacuumSensing.Plan revoked=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            f.source.close(f.owner);
            f.task(()->{expect("SENSING_SOURCE_CHANGED",()->revoked.measure(1,()->{},f.observer));return null;});
            cases.add("bounded samples, exact settings/source identity, ownership, native checks, cleanup and no replay");
        }
    }
    private static void job(String scenario,int expectedFeeds,int expectedPlaced) throws Exception {
        try(Fixture f=new Fixture(scenario)) {
            f.scenario=scenario;
            f.part.setPickRetryCount(scenario.equals("missed-pick-retry")?1:0);
            f.tray.setPickRetryCount(0);f.tray.setFeedRetryCount(0);
            f.enableHome();
            Job job=CanonicalJobImporter.load(f.config,canonical(f.part));
            AtomicReference<Throwable> failure=new AtomicReference<>();
            f.task(()->{
                try(VacuumSensing.Scope scope=VacuumSensing.observe(f.observer)) {
                    f.machine.getPnpJobProcessor().initialize(job);
                    int step=0;boolean more;
                    do {if(++step>500)throw new AssertionError("native job did not terminate");more=f.machine.getPnpJobProcessor().next();}while(more);
                }catch(Exception e){failure.set(e);}
                return null;
            });
            long placed=job.getBoardLocations().stream().filter(b->job.retrievePlacedStatus(b,"R1")).count();
            check(f.tray.getFeedCount()==expectedFeeds,scenario+" exact native material advancement: "+f.tray.getFeedCount());
            check(placed==expectedPlaced,scenario+" exact native placed state: "+placed);
            if(expectedPlaced==1)check(failure.get()==null,scenario+" native success: "+failure.get());
            else {
                check(failure.get()!=null,scenario+" native failure remains visible");
                String expectedStage=scenario.equals("retained-after-place")?"after_place":"before_place";
                check(f.events.stream().anyMatch(e->"check.returned".equals(e.get("event"))&&expectedStage.equals(e.get("native_stage"))&&Boolean.FALSE.equals(e.get("verdict"))),scenario+" failed at exact expected native sensing stage");
            }
            if(scenario.equals("retained-after-place")) {
                check(f.nozzle.getPart()==null,"retained sensor outcome coexists with empty native model");
                check(f.events.stream().anyMatch(e->e.get("event").equals("check.returned")&&"after_place".equals(e.get("native_stage"))&&Boolean.FALSE.equals(e.get("verdict"))),"retained native verdict observed");
            }
            if(scenario.equals("missed-pick-retry")) check(f.onAfterPick.get()==2,"known missed pick performs one configured retry");
            cases.add("actual native job: "+scenario);
        }
    }
    static final class Fixture implements AutoCloseable {
        final Configuration config; final Machine machine; final ReferenceNozzle nozzle; final ReferenceNozzleTip tip;
        final Part part; final ReferenceTrayFeeder tray; final Object owner=new Object();
        final VacuumSensing.ControlledSource source;
        final AtomicReference<String> raw=new AtomicReference<>("70"),stage=new AtomicReference<>("direct");
        final AtomicInteger onAfterPick=new AtomicInteger(); final List<Map<String,Object>> events=new ArrayList<>();
        String scenario="operations";
        final VacuumSensing.Observer observer=(event,n,data)->{
            Map<String,Object> row=new LinkedHashMap<>(data);row.put("event",event);events.add(row);
            if(event.equals("check.before")) {
                stage.set(String.valueOf(data.get("native_stage")));
                if("after_pick".equals(stage.get()))onAfterPick.incrementAndGet();
            }
        };
        Fixture(String id) throws Exception { this(id,"native-actuator-units"); }
        Fixture(String id,String units) throws Exception {
            Path root=Files.createTempDirectory("vacuum-operations-"+id+"-");
            Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();
            SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);machine=config.getMachine();
            nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();tip=nozzle.getNozzleTip();
            ((ReferenceHead)nozzle.getHead()).setPumpActuator(null);
            part=config.getPart("R0603-1K");
            tray=(ReferenceTrayFeeder)machine.getFeeders().stream().filter(feeder->feeder.getPart()==part).findFirst().orElseThrow();
            tray.setTrayCountX(10);tray.setTrayCountY(1);tray.setFeedCount(0);
            ReferenceActuator sensor=new ReferenceActuator();sensor.setName("Controlled vacuum "+id);sensor.setDriver(machine.getDrivers().get(0));
            nozzle.getHead().addActuator(sensor);nozzle.setVacuumSenseActuator(sensor);
            tip.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);tip.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);
            tip.setVacuumLevelPartOnLow(60);tip.setVacuumLevelPartOnHigh(80);tip.setVacuumLevelPartOffLow(-5);tip.setVacuumLevelPartOffHigh(5);
            tip.setPartOnCheckAfterPick(true);tip.setPartOnCheckAlign(true);tip.setPartOnCheckBeforePlace(true);
            tip.setPartOffCheckBeforePick(true);tip.setPartOffCheckAfterPlace(true);
            tip.setEstablishPartOnLevel(false);tip.setEstablishPartOffLevel(false);tip.setPartOffProbingMilliseconds(0);tip.setPartOffDwellMilliseconds(0);
            // This explicit sensing fixture excludes stock camera-light disable actuation.
            SimulatorMain.configureVacuumLifecycleFixture(machine);
            NullDriver driver=(NullDriver)sensor.getDriver();
            source=driver.installControlledVacuumSource(owner,Map.of(sensor,index->signal()),Map.of("fixture_id","native-vacuum-operations","scenario_id",id,"units",units));
        }
        String signal() {
            if(scenario.equals("operations"))return raw.get();
            if(stage.get().equals("before_pick"))return "0";
            if(stage.get().equals("after_place"))return scenario.equals("retained-after-place")?"70":"0";
            if(stage.get().equals("after_pick")&&scenario.equals("missed-pick-retry")&&onAfterPick.get()==1)return "0";
            if(stage.get().equals("before_place")&&scenario.equals("lost-before-place"))return "0";
            return "70";
        }
        <T>T task(Callable<T> body)throws Exception{return machine.submit(body,null,true).get(60,TimeUnit.SECONDS);}
        void enableHome()throws Exception{task(()->{machine.setEnabled(true);machine.home();return null;});}
        public void close()throws Exception{source.close(owner);machine.close();}
    }
    static com.google.gson.JsonObject canonical(Part part) {
        double h=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
        Map<String,Object> placement=new LinkedHashMap<>();
        placement.put("ref","R1");placement.put("partId",part.getId());placement.put("packageId",part.getPackage().getId());placement.put("heightMm",h);
        placement.put("x",10);placement.put("y",10);placement.put("z",0);placement.put("rotation",0);placement.put("side","top");placement.put("enabled",true);placement.put("type","placement");
        Map<String,Object> instance=new LinkedHashMap<>();instance.put("id","instance");instance.put("kind","board");instance.put("definitionId","board");
        instance.put("x",0);instance.put("y",0);instance.put("z",0);instance.put("rotation",0);instance.put("side","top");instance.put("enabled",true);
        return new Gson().toJsonTree(Map.of("schemaVersion",1,"id","native-vacuum-job","units","mm","coordinateConvention","openpnp-top-view",
            "parts",List.of(Map.of("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",h)),
            "boards",List.of(Map.of("id","board","widthMm",20,"heightMm",20,"placements",List.of(placement))),"panels",List.of(),"instances",List.of(instance))).getAsJsonObject();
    }
    @FunctionalInterface private interface Work{void run()throws Exception;}
    private static void expect(String code,Work action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"expected "+code+", got "+e.code);}}
    private static void expectCause(String code,Work action)throws Exception {
        try {action.run();throw new AssertionError("Expected causal "+code);}
        catch(Throwable failure) {
            Throwable current=failure;
            for(int i=0;current!=null&&i<12;i++,current=current.getCause()) {
                if(current instanceof Bridge.Fault && code.equals(((Bridge.Fault)current).code)) {checks++;return;}
                if(current instanceof VacuumSensing.SensorValueException && code.equals(((VacuumSensing.SensorValueException)current).code)) {checks++;return;}
            }
            throw new AssertionError("Missing expected causal "+code,failure);
        }
    }
    private static void check(boolean yes,String message){checks++;if(!yes)throw new AssertionError(message);}
}
