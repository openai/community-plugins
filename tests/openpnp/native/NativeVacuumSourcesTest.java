/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.nio.file.*;
import java.lang.reflect.Field;
import org.openpnp.util.IdentifiableList;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.spi.base.AbstractNozzle;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Actual native fixture and source admission; synthetic check scenarios, no pneumatic qualification. */
public final class NativeVacuumSourcesTest {
    private static int checks;
    @FunctionalInterface interface Checked { void run() throws Exception; }
    private static void yes(boolean value, String label) { checks++; if (!value) throw new AssertionError(label); }
    private static void refused(String code, Checked call) throws Exception {
        try { call.run(); throw new AssertionError("Allowed " + code); }
        catch (Bridge.Fault expected) { yes(code.equals(expected.code), "Expected " + code + ", got " + expected.code); }
    }
    static final class Fixture implements AutoCloseable {
        final Path root; final Configuration config; final Machine machine; final ReferenceNozzle nozzle;
        final ReferenceNozzleTip tip; final Part part; final ReferenceTrayFeeder feeder;
        Fixture(String scenario, boolean install) throws Exception {
            root=Files.createTempDirectory("vacuum-source78-"); Configuration.initialize(root.toFile());Configuration.get().load();
            SimulatorMain.accelerateFixture(Configuration.get());SimulatorMain.settleFreshFixture(root);
            SimulatorMain.configureVacuumSensingFixture(Configuration.get());SimulatorMain.settleFreshFixture(root);
            config=Configuration.get();machine=config.getMachine();nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();tip=nozzle.getNozzleTip();
            part=config.getPart("R0603-1K");feeder=(ReferenceTrayFeeder)machine.getFeeders().stream().filter(f->f.getPart()==part).findFirst().orElseThrow();
            if(install)NativeVacuumSources.installFixture(config,scenario);
        }
        <T>T task(Callable<T> body) throws Exception { return machine.submit(body,null,true).get(30,TimeUnit.SECONDS); }
        Job job() throws Exception { return CanonicalJobImporter.load(config,NativeVacuumOperationsTest.canonical(part)); }
        public void close() throws Exception { machine.close(); }
    }
    public static void main(String[] args) throws Exception {
        int exit=0;
        try { passiveAndManual(); candidateTips(); beforeFirstFeed(); sourceGenerations(); defaultUnchanged(); rawMethodsAndBounds(); finalBoundaryGuard(); lifecyclePolicies();
            jobScenario("success",1,1);jobScenario("missed-pick-retry",2,1);jobScenario("retained-after-place",1,0);jobScenario("lost-before-place",1,0);jobScenario("invalid-read",1,0);
            System.out.println("NATIVE_VACUUM_SOURCES_PASS "+checks+" checks; explicit synthetic source, native job execution, no public Bridge or physical qualification");
        } catch(Throwable failure) { failure.printStackTrace();exit=1; } System.exit(exit);
    }
    @SuppressWarnings("unchecked") private static void passiveAndManual() throws Exception {
        try(Fixture f=new Fixture("success",true)) {
            Map<String,Object> ready=NativeVacuumSources.inspect(f.config);
            yes(Boolean.TRUE.equals(ready.get("available")),"explicit settled source ready");
            Map<?,?> lifecycle=(Map<?,?>)ready.get("lifecycle_policies");
            yes(Boolean.FALSE.equals(lifecycle.get("home_after_enabled"))&&Boolean.FALSE.equals(lifecycle.get("assumptions_are_sensor_evidence")),"discovery exposes explicit lifecycle scope without sensing authority");
            yes(((List<?>)lifecycle.get("actuators")).size()==f.machine.getAllActuators().size()&&((List<?>)lifecycle.get("actuators")).stream().allMatch(a->"LeaveAsIs".equals(((Map<?,?>)a).get("disabled_actuation"))),"all explicit fixture lifecycle policies disclosed, including camera lights");
            yes(f.feeder.getFeedCount()==0&&!f.machine.isEnabled()&&!f.machine.isHomed(),"passive inspection produces no machine work");
            Map<String,Object> row=(Map<String,Object>)((List<?>)ready.get("nozzles")).get(0);
            String sourceId=(String)((Map<?,?>)row.get("source")).get("source_id");f.config.save();
            yes(!Files.readString(f.root.resolve("machine.xml")).contains(sourceId),"source authority absent from native serialization");
            VacuumSensing.Observer observer=(event,nozzle,data)->NativeVacuumSources.observe(f.config,event,nozzle,data);
            Map<String,Object> sample=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).measure(2,()->{},observer));
            yes(sample.get("samples").equals(List.of(70.0,70.0)),"explicit passive native signals");
            f.task(()->{f.machine.setEnabled(true);f.machine.home();return null;});
            Map<String,Object> on=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_on",()->{},observer));
            Map<String,Object> off=f.task(()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()).verify("part_off",()->{},observer));
            yes(Boolean.TRUE.equals(on.get("native_verdict"))&&Boolean.TRUE.equals(off.get("native_verdict")),"direct native checks receive explicit appropriate signals");
            yes(Boolean.FALSE.equals(f.nozzle.getVacuumActuator().getLastActuationValue()),"native off probe closes valve");
        }
    }
    private static void candidateTips() throws Exception {
        try(Fixture f=new Fixture("success",true)) {
            ReferenceNozzleTip extra=new ReferenceNozzleTip();extra.setName("Eligible sensing candidate");f.machine.addNozzleTip(extra);f.nozzle.addCompatibleNozzleTip(extra);f.part.getPackage().addCompatibleNozzleTip(extra);
            extra.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);extra.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.None);extra.setVacuumLevelPartOnLow(60);extra.setVacuumLevelPartOnHigh(80);extra.setPartOnCheckAfterPick(true);
            extra.setPickDwellMilliseconds(0);extra.setPlaceDwellMilliseconds(0);extra.setPartOffProbingMilliseconds(0);extra.setPartOffDwellMilliseconds(0);
            NativeVacuumSensing.CandidateBinding candidate=NativeVacuumSensing.bindCandidate(f.config,f.nozzle,extra);
            yes(f.nozzle.getNozzleTip()==f.tip,"candidate preflight never installs a tip");candidate.validateCurrentState();
            NativeVacuumSensing.Plan installed=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
            NativeVacuumSources.Admission admission=NativeVacuumSources.admitJob(f.config,f.job());
            f.nozzle.setNozzleTip(extra);admission.validateCurrentState();candidate.validateCurrentState();
            refused("SENSING_IDENTITY_CHANGED",installed::validateCurrentState);
            yes(f.nozzle.getNozzleTip()==extra,"eligible installed-tip transition remains admitted");
            extra.setVacuumLevelPartOnHigh(81);refused("SENSING_CONFIGURATION_CHANGED",admission::validateCurrentState);extra.setVacuumLevelPartOnHigh(80);
            f.nozzle.setNozzleTip(f.tip);extra.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.Difference);
            refused("SENSING_METHOD_UNSUPPORTED",()->NativeVacuumSources.admitJob(f.config,f.job()));
            yes(f.feeder.getFeedCount()==0,"unloaded invalid candidate refused before first feed");
        }
    }
    private static void beforeFirstFeed() throws Exception {
        try(Fixture f=new Fixture("success",false)) {
            Job job=f.job();AtomicBoolean initialized=new AtomicBoolean();
            refused("SENSING_SOURCE_UNQUALIFIED",()->{NativeVacuumSources.admitJob(f.config,job);initialized.set(true);f.machine.getPnpJobProcessor().initialize(job);});
            yes(!initialized.get()&&f.feeder.getFeedCount()==0,"missing source refuses before processor initialize and feed");
            f.tip.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.None);f.tip.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.None);
            yes(NativeVacuumSources.admitJob(f.config,job).sensingNozzleIds().isEmpty(),"None methods preserve inactive native true flags");
        }
        try(Fixture f=new Fixture("success",true)) {
            ReferenceNozzle other=new ReferenceNozzle();other.setName("Second eligible consumer");f.nozzle.getHead().addNozzle(other);other.addCompatibleNozzleTip(f.tip);other.setAxisX(f.nozzle.getAxisX());other.setAxisY(f.nozzle.getAxisY());other.setAxisZ(f.nozzle.getAxisZ());other.setAxisRotation(f.nozzle.getAxisRotation());
            other.setVacuumActuator(f.nozzle.getVacuumActuator());ReferenceActuator sensor=new ReferenceActuator();sensor.setName("Unbound second sensor");sensor.setDriver(f.machine.getDrivers().get(0));other.getHead().addActuator(sensor);other.setVacuumSenseActuator(sensor);
            refused("SENSING_SOURCE_UNQUALIFIED",()->NativeVacuumSources.admitJob(f.config,f.job()));
            yes(f.feeder.getFeedCount()==0,"every shared-tip consumer is checked before first feed");
        }
    }
    private static void sourceGenerations() throws Exception {
        try(NativeVacuumOperationsTest.Fixture f=new NativeVacuumOperationsTest.Fixture("source-generations")) {
            SimulatorMain.configureVacuumLifecycleFixture(f.machine);
            Job job=CanonicalJobImporter.load(f.config,NativeVacuumOperationsTest.canonical(f.part));NativeVacuumSources.Admission old=NativeVacuumSources.admitJob(f.config,job);
            f.source.close(f.owner);refused("SENSING_SOURCE_CHANGED",old::validateCurrentState);
            NullDriver driver=(NullDriver)f.machine.getDrivers().get(0);Object replacementOwner=new Object();
            VacuumSensing.ControlledSource replacement=driver.installControlledVacuumSource(replacementOwner,Map.of(f.nozzle.getVacuumSenseActuator(),index->"70"),Map.of("fixture_id","replacement-test","scenario_id","fixed","units","native-actuator-units"));
            refused("SENSING_SOURCE_CHANGED",old::validateCurrentState);NativeVacuumSources.admitJob(f.config,job);checks++;
            replacement.close(replacementOwner);
            driver.installControlledVacuumSource(replacementOwner,Map.of(f.nozzle.getVacuumSenseActuator(),index->"70"),Map.of("fixture_id","units-test","scenario_id","fixed","units","Pa"));
            refused("SENSING_SOURCE_UNQUALIFIED",()->NativeVacuumSources.admitJob(f.config,job));
            yes(f.tray.getFeedCount()==0,"revocation, replacement and units checks are passive");
        }
        try(Fixture f=new Fixture("success",true)) {
            f.config.save();f.machine.close();Configuration.initialize(f.root.toFile());Configuration.get().load();
            try { yes(Boolean.FALSE.equals(NativeVacuumSources.inspect(Configuration.get()).get("available")),"native reload never reconstructs source readiness"); }
            finally { Configuration.get().getMachine().close(); }
        }
    }
    private static void defaultUnchanged() throws Exception {
        Path root=Files.createTempDirectory("vacuum-default78-");Configuration.initialize(root.toFile());Configuration.get().load();
        try {SimulatorMain.accelerateFixture(Configuration.get());SimulatorMain.settleFreshFixture(root);Configuration config=Configuration.get();
            yes(Boolean.FALSE.equals(NativeVacuumSources.inspect(config).get("available")),"default fixture does not gain a synthetic source");
            yes(NativeVacuumSources.admitJob(config,null).sensingNozzleIds().isEmpty(),"default nonsensing native behavior remains admissible");
            List<Actuator> lights=new ArrayList<>();for(Actuator actuator:config.getMachine().getAllActuators())if(actuator.getName().startsWith("LIGHT_"))lights.add(actuator);
            yes(lights.size()==2&&lights.stream().allMatch(a->((ReferenceActuator)a).getDisabledActuation()==ReferenceActuator.MachineStateActuation.ActuateOff),"stock default camera-light disable policies remain unchanged");
            SimulatorMain.configureSustainedWorkload(config);
            yes(lights.stream().allMatch(a->((ReferenceActuator)a).getDisabledActuation()==ReferenceActuator.MachineStateActuation.ActuateOff)&&!NativeVacuumSources.hasConfiguredSensing(config),"sustained fixture keeps stock lifecycle policies and None methods");
        } finally {Configuration.get().getMachine().close();}
    }
    private static final class OversizedList<T extends Identifiable> extends IdentifiableList<T> {
        private static final long serialVersionUID=1L; final int count; OversizedList(int count){this.count=count;}
        public int size(){return count;}
        public Iterator<T> iterator(){throw new AssertionError("Oversized list iterated before refusal");}
        public T get(int index){throw new AssertionError("Oversized list accessed before refusal");}
        public Object[] toArray(){throw new AssertionError("Oversized list materialized before refusal");}
    }
    private static void withField(Object object,Class<?> owner,String name,Object value,Checked body)throws Exception {
        Field field=owner.getDeclaredField(name);field.setAccessible(true);Object original=field.get(object);field.set(object,value);
        try{body.run();}finally{field.set(object,original);}
    }
    private static void rawMethodsAndBounds()throws Exception {
        try(Fixture f=new Fixture("success",true)) {
            f.tip.setMethodPartOn(null);
            yes(Boolean.FALSE.equals(NativeVacuumSources.inspect(f.config).get("available")),"passive readiness refuses raw null method");
            yes(NativeVacuumSettings.readMethod(f.tip,true)==null,"passive readiness leaves raw null unchanged");
            refused("SENSING_METHOD_UNSUPPORTED",()->NativeVacuumSources.admitJob(f.config,f.job()));
            refused("SENSING_METHOD_UNSUPPORTED",()->NativeVacuumSensing.bindCandidate(f.config,f.nozzle,f.tip));
            yes(NativeVacuumSettings.readMethod(f.tip,true)==null,"candidate and job admission leave raw null unchanged");f.tip.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);
            Job job=f.job();NativeVacuumSources.Admission admission=NativeVacuumSources.admitJob(f.config,job);
            ReferencePnpJobProcessor processor=(ReferencePnpJobProcessor)f.machine.getPnpJobProcessor();PnpJobPlanner original=processor.planner;
            processor.planner=new ReferencePnpJobProcessor.SimplePnpJobPlanner();refused("SENSING_CONFIGURATION_CHANGED",admission::validateCurrentState);processor.planner=original;
            processor.planner=new ReferencePnpJobProcessor.SimplePnpJobPlanner(){};refused("SENSING_PLANNER_UNSUPPORTED",()->NativeVacuumSources.admitJob(f.config,job));processor.planner=original;
            withField(f.machine,AbstractMachine.class,"heads",new OversizedList<Head>(9),()->{
                refused("SENSING_GRAPH_LIMIT",()->NativeVacuumSources.admitJob(f.config,job));
                refused("SENSING_GRAPH_LIMIT",()->NativeVacuumSensing.bindCandidate(f.config,f.nozzle,f.tip));
                yes(Boolean.FALSE.equals(NativeVacuumSources.inspect(f.config).get("available")),"oversized passive graph refuses without iteration");
            });
            withField(job.getRootPanelLocation().getPanel(),Panel.class,"children",new OversizedList<PlacementsHolderLocation<?>>(4097),()->refused("SENSING_GRAPH_LIMIT",()->NativeVacuumSources.admitJob(f.config,job)));
            Board board=job.getBoardLocations().get(0).getBoard();
            withField(board,PlacementsHolder.class,"placements",new OversizedList<Placement>(20001),()->refused("SENSING_GRAPH_LIMIT",()->NativeVacuumSources.admitJob(f.config,job)));
            List<String> oversizedIds=new AbstractList<String>(){public int size(){return 129;}public String get(int i){throw new AssertionError("Compatibility IDs resolved before limit");}};
            withField(f.nozzle,AbstractNozzle.class,"compatibleNozzleTipIds",oversizedIds,()->refused("SENSING_GRAPH_LIMIT",()->NativeVacuumSources.admitJob(f.config,job)));
            withField(f.part.getPackage(),org.openpnp.model.Package.class,"compatibleNozzleTipIds",oversizedIds,()->refused("SENSING_GRAPH_LIMIT",()->NativeVacuumSources.admitJob(f.config,job)));
            yes(f.feeder.getFeedCount()==0,"unknown methods, custom planner and graph limits refuse before feed");
        }
    }
    /** A transparent inventory read expires the test lease after the pre-check, before the source call. */
    private static void finalBoundaryGuard()throws Exception {
        try(NativeVacuumOperationsTest.Fixture f=new NativeVacuumOperationsTest.Fixture("boundary-final-guard")) {
            SimulatorMain.configureVacuumLifecycleFixture(f.machine);
            f.source.close(f.owner);AtomicInteger reads=new AtomicInteger();Object owner=new Object();
            VacuumSensing.ControlledSource source=((NullDriver)f.machine.getDrivers().get(0)).installControlledVacuumSource(owner,
                Map.of(f.nozzle.getVacuumSenseActuator(),index->{reads.incrementAndGet();return "70";}),
                Map.of("fixture_id","native-boundary-regression","scenario_id","lease-during-graph","units","native-actuator-units"));
            AtomicBoolean armed=new AtomicBoolean(),revoked=new AtomicBoolean();AtomicInteger before=new AtomicInteger();
            IdentifiableList<Head> heads=new IdentifiableList<Head>(){private static final long serialVersionUID=1L;
                public int size(){if(armed.compareAndSet(true,false))revoked.set(true);return super.size();}};
            heads.addAll(f.machine.getHeads());
            try {withField(f.machine,AbstractMachine.class,"heads",heads,()->{
                NativeVacuumSensing.Plan plan=NativeVacuumSensing.admit(f.config,f.nozzle.getId());
                Throwable failure=f.task(()->{try {plan.measure(1,()->{if(revoked.get())throw new Bridge.Fault("OWNERSHIP_REVOKED","Controlled lease expired during graph read");},
                    (event,nozzle,data)->{if(event.equals("read.before")){before.incrementAndGet();armed.set(true);}});return null;}catch(Throwable caught){return caught;}});
                boolean ownership=false;for(Throwable cause=failure;cause!=null;cause=cause.getCause())if(cause instanceof Bridge.Fault&&"OWNERSHIP_REVOKED".equals(((Bridge.Fault)cause).code))ownership=true;
                yes(before.get()==1&&revoked.get(),"lease changes during the final native graph check after read intent");
                yes(ownership&&reads.get()==0,"post-validation ownership guard prevents the actual native source read");
            });}finally{source.close(owner);}
        }
    }
    private static void lifecyclePolicies()throws Exception {
        try(Fixture f=new Fixture("success",true)) {
            ReferenceActuator unrelated=new ReferenceActuator();unrelated.setName("Lifecycle-only test actuator");unrelated.setDriver(f.machine.getDrivers().get(0));f.machine.addActuator(unrelated);
            Job job=f.job();AtomicInteger callbacks=new AtomicInteger();
            yes(NativeVacuumSources.hasConfiguredSensing(f.config),"configured Absolute methods require lifecycle guards");
            for(Actuator item:List.of(f.nozzle.getVacuumSenseActuator(),f.nozzle.getVacuumActuator(),unrelated)) {
                ReferenceActuator actuator=(ReferenceActuator)item;
                for(String property:List.of("EnabledActuation","HomedActuation","DisabledActuation")) {
                    java.lang.reflect.Method get=ReferenceActuator.class.getMethod("get"+property),set=ReferenceActuator.class.getMethod("set"+property,ReferenceActuator.MachineStateActuation.class);
                    Object original=get.invoke(actuator);
                    try {
                        for(ReferenceActuator.MachineStateActuation policy:Arrays.asList(ReferenceActuator.MachineStateActuation.ActuateOn,ReferenceActuator.MachineStateActuation.ActuateOff,null)) {
                            set.invoke(actuator,new Object[]{policy});
                            refused("SENSING_AUTOMATION_UNSUPPORTED",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()));
                            refused("SENSING_AUTOMATION_UNSUPPORTED",()->NativeVacuumSources.admitJob(f.config,job));
                            yes(get.invoke(actuator)==policy,"refusal preserves the exact unknown or effectful lifecycle policy");
                        }
                    }finally{set.invoke(actuator,original);}
                }
            }
            NativeVacuumSensing.Plan plan=NativeVacuumSensing.admit(f.config,f.nozzle.getId());NativeVacuumSources.Admission admission=NativeVacuumSources.admitJob(f.config,job);
            unrelated.setEnabledActuation(ReferenceActuator.MachineStateActuation.AssumeActuatedOff);
            refused("SENSING_CONFIGURATION_CHANGED",admission::validateCurrentState);
            f.task(()->{refused("SENSING_CONFIGURATION_CHANGED",()->plan.measure(1,()->{},(event,nozzle,data)->callbacks.incrementAndGet()));return null;});
            yes(callbacks.get()==0&&f.feeder.getFeedCount()==0,"allowed-policy drift refuses before native callbacks or feed");
            NativeVacuumSources.requireLifecycleSafe(f.config);
            yes(f.nozzle.getVacuumActuator().getLastActuationValue()==null,"Assume policy admission does not fabricate source or valve state");
            NativeVacuumSensing.Plan effectful=NativeVacuumSensing.admit(f.config,f.nozzle.getId());unrelated.setDisabledActuation(ReferenceActuator.MachineStateActuation.ActuateOff);
            f.task(()->{refused("SENSING_AUTOMATION_UNSUPPORTED",()->effectful.measure(1,()->{},(event,nozzle,data)->callbacks.incrementAndGet()));return null;});unrelated.setDisabledActuation(ReferenceActuator.MachineStateActuation.LeaveAsIs);
            ((ReferenceMachine)f.machine).setHomeAfterEnabled(true);
            refused("SENSING_AUTOMATION_UNSUPPORTED",()->NativeVacuumSensing.admit(f.config,f.nozzle.getId()));
            refused("SENSING_AUTOMATION_UNSUPPORTED",()->NativeVacuumSources.admitJob(f.config,job));((ReferenceMachine)f.machine).setHomeAfterEnabled(false);
            NativeVacuumSensing.Plan autoHome=NativeVacuumSensing.admit(f.config,f.nozzle.getId());((ReferenceMachine)f.machine).setHomeAfterEnabled(true);
            f.task(()->{refused("SENSING_AUTOMATION_UNSUPPORTED",()->autoHome.measure(1,()->{},(event,nozzle,data)->callbacks.incrementAndGet()));return null;});((ReferenceMachine)f.machine).setHomeAfterEnabled(false);
            yes(callbacks.get()==0,"effectful policy and automatic-home drift perform zero native callbacks");
            f.tip.setMethodPartOn(null);yes(NativeVacuumSources.hasConfiguredSensing(f.config)&&NativeVacuumSettings.readMethod(f.tip,true)==null,"unknown method is lifecycle-relevant without normalization");
            f.tip.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.None);f.tip.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.None);
            yes(!NativeVacuumSources.hasConfiguredSensing(f.config),"None methods preserve inactive flags without sensing lifecycle gating");
            unrelated.setEnabledActuation(ReferenceActuator.MachineStateActuation.ActuateOn);
            try{yes(NativeVacuumSources.admitJob(f.config,job).sensingNozzleIds().isEmpty(),"None-only legacy admission remains outside sensing lifecycle policies");}finally{unrelated.setEnabledActuation(ReferenceActuator.MachineStateActuation.AssumeUnknown);}
        }
    }
    private static void jobScenario(String scenario,int feeds,int placed) throws Exception {
        try(Fixture f=new Fixture(scenario,true)) {
            f.part.setPickRetryCount(scenario.equals("missed-pick-retry")?1:0);f.feeder.setPickRetryCount(0);f.feeder.setFeedRetryCount(0);
            Job job=f.job();NativeVacuumSources.Admission admission=NativeVacuumSources.admitJob(f.config,job);AtomicReference<Throwable> failure=new AtomicReference<>();
            f.task(()->{f.machine.setEnabled(true);f.machine.home();admission.validateCurrentState();
                try(VacuumSensing.Scope scope=VacuumSensing.observe((event,nozzle,data)->{try {admission.observe(event,nozzle,data);}catch(Exception e){throw new IllegalStateException(e);}})) {
                    f.machine.getPnpJobProcessor().initialize(job);int step=0;boolean more;
                    do {if(++step>500)throw new AssertionError("Native job step bound");admission.validateCurrentState();more=f.machine.getPnpJobProcessor().next();}while(more);
                }catch(Throwable t){failure.set(t);}return null;});
            long actual=job.getBoardLocations().stream().filter(board->job.retrievePlacedStatus(board,"R1")).count();
            yes(f.feeder.getFeedCount()==feeds,scenario+" exact native feed count "+f.feeder.getFeedCount());yes(actual==placed,scenario+" native completion count "+actual);
            yes((placed==1)==(failure.get()==null),scenario+" expected native termination "+failure.get());
        }
    }
}
