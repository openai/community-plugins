/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Actual native reads/checks/pulses and processor stages, with explicitly synthetic samples. */
public final class NativeVacuumSensingTest {
    private static int assertions, placements;
    private static final List<String> passed=new ArrayList<>();
    private static final Gson GSON=new Gson();
    @FunctionalInterface private interface Work {void run()throws Exception;}
    public static void main(String[] args)throws Exception {
        int exit=0;
        try{foundation();nativeJob();System.out.println("OPENPNP_NATIVE_VACUUM_SENSING_RESULT "+GSON.toJson(map("assertions",assertions,"passed",passed,"native_placements",placements,"simulation_only",true,"hardware_qualified",false,"api_version",VacuumSensing.API_VERSION)));}
        catch(Throwable failure){failure.printStackTrace();exit=1;}
        System.exit(exit);
    }
    private static void foundation()throws Exception {
        Path root=Files.createTempDirectory("native-vacuum-foundation-");Configuration c=fixture(root);Machine m=c.getMachine();
        ReferenceNozzle n=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip tip=n.getNozzleTip();
        ReferenceActuator sensor=(ReferenceActuator)n.getVacuumSenseActuator();ReferenceActuator valve=(ReferenceActuator)n.getVacuumActuator();NullDriver d=(NullDriver)sensor.getDriver();
        Object owner=new Object();AtomicReference<String> raw=new AtomicReference<>("0.8");AtomicInteger reads=new AtomicInteger();
        Map<String,String> declared=new HashMap<>(Map.of("fixture_id","foundation77","scenario_id","controlled-raw-values","units","native-actuator-units"));
        Map<Actuator,VacuumSensing.SampleSource> input=new IdentityHashMap<>();input.put(sensor,i->{reads.incrementAndGet();return raw.get();});
        VacuumSensing.ControlledSource source=d.installControlledVacuumSource(owner,input,declared);input.clear();declared.put("scenario_id","mutated");
        try {
            check(n.getClass()==ReferenceNozzle.class&&d.getClass()==NullDriver.class&&sensor.getClass()==ReferenceActuator.class,"exact native implementations");
            check(source.provenance().get("scenario_id").equals("controlled-raw-values"),"source provenance copied");
            expect(UnsupportedOperationException.class,()->source.provenance().put("units","changed"));
            expect(IllegalStateException.class,()->source.close(new Object()));
            expect(IllegalStateException.class,()->d.installControlledVacuumSource(owner,Map.of(sensor,i->"1"),Map.of("fixture_id","x","scenario_id","x","units","x")));
            expect(Exception.class,n::readVacuumLevel);
            expect(IllegalStateException.class,()->d.actuatorRead(sensor));
            check(reads.get()==0,"foreign-thread source read refused before callback");
            on(m,()->{m.setEnabled(true);settings(tip);});
            List<Map<String,Object>> events=new ArrayList<>();
            on(m,()->{
                try(VacuumSensing.Scope scope=VacuumSensing.observe((event,nozzle,data)->{
                    check(nozzle==n,"observer actual nozzle identity");
                    expectUnchecked(UnsupportedOperationException.class,()->data.put("x",1));
                    events.add(map("event",event,"data",data));
                })){
                    check(n.isPartOn(),"native absolute part-on accepts configured sample");
                    raw.set("0.2");check(!n.isPartOn(),"native absolute part-on rejects missed pick sample");
                    check(n.isPartOff(),"native part-off pulse accepts empty sample");
                    check(Boolean.FALSE.equals(valve.getLastActuationValue()),"native part-off returns with valve closed");
                    raw.set("0.8");check(!n.isPartOff(),"native part-off rejects retained sample");
                    check(scope.stickyFault()==null,"ordinary negative verdict is not observer failure");
                }
                check(n.isPartOn(),"scope close restores unobserved native execution");
                for(String invalid:List.of("NaN","Infinity","-Infinity","oops","1".repeat(129),"\u00e9")){
                    raw.set(invalid);expect(VacuumSensing.SensorValueException.class,n::readVacuumLevel);
                }
                raw.set("0.5");check(n.readVacuumLevel()==0.5,"unconditional finite guard preserves finite native reading");
            });
            check(events.stream().anyMatch(e->e.get("event").equals("valve.before")&&Boolean.TRUE.equals(data(e).get("cleanup_attempt"))),"actual pulse cleanup boundary observed");
            verifyPairs(events);
            on(m,()->{
                AtomicInteger callback=new AtomicInteger();VacuumSensing.Scope scope=VacuumSensing.observe((e,nozzle,data)->callback.incrementAndGet());
                expect(IllegalStateException.class,()->VacuumSensing.observe((e,nozzle,data)->{}));
                AtomicReference<Throwable> wrong=new AtomicReference<>();Thread other=new Thread(()->{try{VacuumSensing.check(n,"part_on",()->true);scope.close();}catch(Throwable t){wrong.set(t);}});other.start();other.join(2000);
                check(!other.isAlive()&&wrong.get() instanceof IllegalStateException&&callback.get()==0,"thread-local observer and owner-bound close");scope.close();scope.close();
                int before=reads.get();try(VacuumSensing.Scope reject=VacuumSensing.observe((e,nozzle,data)->{if(e.equals("read.before"))throw new IllegalStateException("intent refused");})){
                    expect(VacuumSensing.ObserverFailure.class,n::readVacuumLevel);check(reads.get()==before,"observer gates native sensor acquisition");check(reject.stickyFault()!=null,"observer failure remains sticky");
                }
                raw.set("0.2");valve.actuate(false);
                List<String> observed=new ArrayList<>();try(VacuumSensing.Scope reject=VacuumSensing.observe((e,nozzle,data)->{
                    observed.add(e+":"+data.get("enabled"));if(e.equals("read.returned"))throw new AssertionError("receipt refused after vacuum opened");
                })){
                    expect(VacuumSensing.ObserverFailure.class,n::isPartOff);
                    check(Boolean.FALSE.equals(valve.getLastActuationValue()),"read receipt failure still executes native valve-off finally");
                    check(reject.stickyFault()!=null&&!observed.contains("valve.returned:false"),"failed observer does not publish cleanup success");
                }
                valve.actuate(false);try(VacuumSensing.Scope reject=VacuumSensing.observe((e,nozzle,data)->{if(e.equals("valve.before")&&Boolean.FALSE.equals(data.get("enabled")))throw new IllegalStateException("close intent refused");})){
                    expect(VacuumSensing.ObserverFailure.class,n::isPartOff);check(Boolean.FALSE.equals(valve.getLastActuationValue()),"close callback refusal cannot skip mandatory valve-off");
                }
                valve.actuate(false);try(VacuumSensing.Scope reject=VacuumSensing.observe((e,nozzle,data)->{if(e.equals("valve.before")&&Boolean.TRUE.equals(data.get("enabled")))throw new IllegalStateException("open intent refused");})){
                    expect(VacuumSensing.ObserverFailure.class,n::isPartOff);check(Boolean.FALSE.equals(valve.getLastActuationValue()),"open intent rejection leaves valve off");
                }
                valve.actuate(false);
                try(VacuumSensing.Scope reject=VacuumSensing.observe((e,nozzle,data)->{
                    if(e.equals("valve.before")&&Boolean.FALSE.equals(data.get("enabled"))){try{d.setEnabled(false);}catch(Exception failure){throw new AssertionError(failure);}throw new AssertionError("close receipt refused and native driver unavailable");}
                })){
                    try{n.isPartOff();throw new AssertionError("Expected observer fence");}
                    catch(VacuumSensing.ObserverFailure failure){
                        check(failure==reject.stickyFault(),"native close failure cannot mask nonretryable observer fence");
                        check(Arrays.stream(failure.getSuppressed()).anyMatch(x->x instanceof Exception),"actual failed native close retained as suppressed cause");
                        check(Boolean.TRUE.equals(valve.getLastActuationValue()),"failed native close cannot claim valve command completed");
                    }
                }finally{d.setEnabled(true);valve.actuate(false);}
                List<Map<String,Object>> failures=new ArrayList<>();raw.set("Z".repeat(200));try(VacuumSensing.Scope ignored=VacuumSensing.observe((e,nozzle,data)->failures.add(map("event",e,"data",data)))){expect(VacuumSensing.SensorValueException.class,n::readVacuumLevel);}
                Map<String,Object> last=data(failures.get(failures.size()-1));check(((String)last.get("raw")).length()==128&&last.get("raw_length").equals(200)&&Boolean.TRUE.equals(last.get("raw_truncated")),"invalid native input bounded in failure observation");
                source.close(owner);expect(IllegalStateException.class,n::readVacuumLevel);
            });
            check(source.isClosed(),"source capability revoked");
            AtomicReference<VacuumSensing.ControlledSource> revoking=new AtomicReference<>();
            revoking.set(d.installControlledVacuumSource(owner,Map.of(sensor,i->{revoking.get().close(owner);return "0.8";}),Map.of("fixture_id","foundation77","scenario_id","revoked-during-read","units","native-actuator-units")));
            on(m,()->expect(IllegalStateException.class,n::readVacuumLevel));
            check(revoking.get().isClosed(),"revocation during native source callback rejects its returned sample");
            VacuumSensing.ControlledSource rebound=d.installControlledVacuumSource(owner,Map.of(sensor,i->{sensor.setDriver(new NullDriver());return "0.8";}),Map.of("fixture_id","foundation77","scenario_id","driver-changed-during-read","units","native-actuator-units"));
            try{on(m,()->expect(IllegalStateException.class,n::readVacuumLevel));}finally{sensor.setDriver(d);rebound.close(owner);}
            Head sensorHead=sensor.getHead();
            VacuumSensing.ControlledSource removed=d.installControlledVacuumSource(owner,Map.of(sensor,i->{sensorHead.removeActuator(sensor);return "0.8";}),Map.of("fixture_id","foundation77","scenario_id","sensor-removed-during-read","units","native-actuator-units"));
            try{on(m,()->expect(IllegalStateException.class,n::readVacuumLevel));}finally{sensorHead.addActuator(sensor);removed.close(owner);}
            VacuumSensing.ControlledSource replacement=d.installControlledVacuumSource(owner,Map.of(sensor,i->"0.8"),Map.of("fixture_id","foundation77","scenario_id","replacement-not-serialized","units","native-actuator-units"));
            check(!replacement.provenance().get("source_id").equals(source.provenance().get("source_id")),"new source identity after explicit reattachment");
            on(m,()->{check(n.readVacuumLevel()==0.8,"new declared provider is actually read");m.setEnabled(false);c.save();});
            String xml=Files.readString(c.getConfigurationDirectory().toPath().resolve("machine.xml"));check(!xml.contains("replacement-not-serialized")&&!xml.contains((String)replacement.provenance().get("source_id")),"source callbacks and authority absent from native XML");
            m.close();check(replacement.isClosed(),"driver close revokes provider");
            Configuration.initialize(c.getConfigurationDirectory());Configuration.get().load();Machine restored=Configuration.get().getMachine();
            try{check(((NullDriver)((ReferenceMachine)restored).getDefaultDriver()).getControlledVacuumSource()==null,"native configuration reload grants no provider authority");}finally{restored.close();}
            passed.add("native read/check/pulse guards, observer fencing, controlled source ownership and serialization exclusion");
        }finally{m.close();}
    }
    private static void nativeJob()throws Exception {
        Path root=Files.createTempDirectory("native-vacuum-job-");Configuration c=fixture(root);Machine m=c.getMachine();
        ReferenceNozzle n=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip tip=n.getNozzleTip();Actuator sensor=n.getVacuumSenseActuator();NullDriver d=(NullDriver)sensor.getDriver();
        Object owner=new Object();AtomicReference<String> signal=new AtomicReference<>("0.2");
        VacuumSensing.ControlledSource source=d.installControlledVacuumSource(owner,Map.of(sensor,i->signal.get()),Map.of("fixture_id","native-one-placement77","scenario_id","finite-correct-readings","units","native-actuator-units"));
        List<Map<String,Object>> events=new ArrayList<>();Set<String> stages=new HashSet<>();
        try{
            on(m,()->{m.setEnabled(true);settings(tip);});m.execute(()->{m.home();return null;},false,5000,30000);
            on(m,()->{
                Job job=CanonicalJobImporter.load(c,canonical(c.getPart("R0603-1K")));
                try(VacuumSensing.Scope scope=VacuumSensing.observe((e,nozzle,data)->{
                    events.add(map("event",e,"data",data));if(e.equals("check.before")){stages.add((String)data.get("native_stage"));signal.set(data.get("check_kind").equals("part_on")?"0.8":"0.2");}
                })){
                    m.getPnpJobProcessor().initialize(job);boolean more;int steps=0;do{more=m.getPnpJobProcessor().next();check(++steps<200,"bounded actual native job");}while(more);
                    check(scope.stickyFault()==null,"native job sensing observer completed");
                }
                check(job.retrievePlacedStatus(job.getBoardLocations().get(0),"R1"),"actual native job records placed after sensing checks");placements++;
            });
            check(stages.containsAll(Set.of("before_pick","after_pick","align","before_place","after_place")),"actual processor supplies all five native sensing stages");verifyPairs(events);
            passed.add("actual native processor placement with all five sensing stages");
        }finally{source.close(owner);m.close();}
    }
    private static void settings(ReferenceNozzleTip t){
        t.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);t.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);
        t.setVacuumLevelPartOnLow(.7);t.setVacuumLevelPartOnHigh(.9);t.setVacuumLevelPartOffLow(.1);t.setVacuumLevelPartOffHigh(.3);
        t.setEstablishPartOnLevel(false);t.setEstablishPartOffLevel(false);t.setPickDwellMilliseconds(0);t.setPlaceDwellMilliseconds(0);t.setPartOffProbingMilliseconds(0);t.setPartOffDwellMilliseconds(0);
        t.setPartOnCheckAfterPick(true);t.setPartOnCheckAlign(true);t.setPartOnCheckBeforePlace(true);t.setPartOffCheckBeforePick(true);t.setPartOffCheckAfterPlace(true);
    }
    private static void verifyPairs(List<Map<String,Object>> events){Map<Object,Map<String,Object>> pending=new HashMap<>();Set<Object> seen=new HashSet<>();for(Map<String,Object> row:events){String e=(String)row.get("event");Map<String,Object>d=data(row);Object id=d.get("observation_id");if(e.endsWith(".before")){check(seen.add(id),"unique native observation id");Object parent=d.get("parent_observation_id");check(parent==null||pending.containsKey(parent),"native child has active parent");pending.put(id,d);}else{Map<String,Object> before=pending.remove(id);check(before!=null,"native outcome has matching before");for(String key:List.of("parent_observation_id","native_stage","check_kind","source","sensor_id","nozzle_tip_id"))check(Objects.equals(before.get(key),d.get(key)),"native outcome preserves "+key);}}check(pending.isEmpty(),"all successful native observations close");}
    private static Configuration fixture(Path root)throws Exception{Path p=root.resolve("config");Files.createDirectory(p);Configuration.initialize(p.toFile());Configuration c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.configureSustainedWorkload(c);return c;}
    private static void on(Machine machine,Work work)throws Exception{machine.submit(()->{work.run();return null;},null,true).get(60,TimeUnit.SECONDS);}
    private static JsonObject canonical(Part part){double h=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();return GSON.toJsonTree(map("schemaVersion",1,"id","vacuum77","units","mm","coordinateConvention","openpnp-top-view","parts",List.of(map("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",h)),"boards",List.of(map("id","board","widthMm",20,"heightMm",20,"placements",List.of(map("ref","R1","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",h,"x",10,"y",10,"z",0,"rotation",0,"side","top","enabled",true,"type","placement")))),"panels",List.of(),"instances",List.of(map("id","instance","kind","board","definitionId","board","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true)))).getAsJsonObject();}
    private static Map<String,Object> map(Object...args){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<args.length;i+=2)m.put((String)args[i],args[i+1]);return m;}
    @SuppressWarnings("unchecked")private static Map<String,Object> data(Map<String,Object> row){return(Map<String,Object>)row.get("data");}
    private static void check(boolean value,String message){assertions++;if(!value)throw new AssertionError(message);}
    private static void expect(Class<? extends Throwable> type,Work work)throws Exception{try{work.run();throw new AssertionError("Expected "+type.getName());}catch(Throwable failure){if(!type.isInstance(failure))throw new AssertionError("Wrong failure, expected "+type.getName(),failure);assertions++;}}
    private static void expectUnchecked(Class<? extends Throwable> type,Work work){try{expect(type,work);}catch(Exception e){throw new AssertionError(e);}}
}
