/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.nio.file.*;
import java.util.*;
import java.util.concurrent.CountDownLatch;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.ReferenceActuator;
import org.openpnp.machine.reference.ReferenceHead;
import org.openpnp.machine.reference.ReferenceNozzleTip;
import org.openpnp.machine.reference.ReferenceNozzle;
import org.openpnp.machine.reference.ReferencePnpJobProcessor;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.camera.AbstractSettlingCamera;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Always starts the trusted upstream default fixture in a new directory, never a supplied machine.xml. */
public final class SimulatorMain {
    private static Configuration settledFixture;
    static boolean isSettledFreshFixture(Configuration config){return settledFixture==config;}
    /** Accepts only a one-use claim created after the GUI's prepared-file verification. */
    static void installPreparedGuiSource(Configuration config,String scenario,GuiSensingFixture.Prepared claim)throws Exception {
        if(claim==null)throw new IllegalArgumentException("Prepared GUI fixture claim required");
        claim.consume(config,scenario);
        Configuration previous=settledFixture;settledFixture=config;
        try{NativeVacuumSources.installFixture(config,scenario);}
        catch(Exception|Error failure){settledFixture=previous;throw failure;}
    }
    public static void main(String[] args) throws Exception {
        Map<String,String> options=new HashMap<>();
        for(int i=0;i<args.length;i+=2) {
            if(i+1>=args.length||!args[i].startsWith("--"))throw new IllegalArgumentException("Expected --option value pairs");
            options.put(args[i],args[i+1]);
        }
        Path base=Paths.get(required(options,"--config-dir")).toAbsolutePath();
        Files.createDirectories(base);
        Path fresh=Files.createTempDirectory(base,"native-simulator-");
        Configuration.initialize(fresh.toFile());
        Configuration.get().load();
        accelerateFixture(Configuration.get());
        String profile=options.getOrDefault("--profile","native-simulator");
        String sensingScenario=options.getOrDefault("--sensing-scenario","success");
        if(options.containsKey("--sensing-scenario")&&!"vacuum-sensing".equals(profile))throw new IllegalArgumentException("Sensing scenario requires vacuum-sensing profile");
        if("sustained-workload".equals(profile))configureSustainedWorkload(Configuration.get());
        else if(!"native-simulator".equals(profile)&&!"vacuum-sensing".equals(profile))throw new IllegalArgumentException("Unknown simulator profile");
        if("vacuum-sensing".equals(profile)&&!NativeVacuumSources.SCENARIOS.contains(sensingScenario))throw new IllegalArgumentException("Unknown fixed sensing scenario");
        settleFreshFixture(fresh);
        if("vacuum-sensing".equals(profile)) {
            configureVacuumSensingFixture(Configuration.get());
            settleFreshFixture(fresh);
            NativeVacuumSources.installFixture(Configuration.get(),sensingScenario);
        }
        Bridge bridge=new Bridge(Configuration.get(),Paths.get(required(options,"--token-file")),
            Paths.get(required(options,"--journal-dir")),Paths.get(required(options,"--sample-root")),
            Integer.parseInt(options.getOrDefault("--port","0")),true,profile);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {try {bridge.close();Configuration.get().getMachine().close();}catch(Exception ignored){}}));
        bridge.start();
        System.out.println("OPENPNP_CODEX_READY port="+bridge.getPort()+" upstream="+Bridge.UPSTREAM+" mode="+profile);
        new CountDownLatch(1).await();
    }
    /** Explicit synthetic sensing fixture; called only after the initial fresh configuration reload. */
    static void configureVacuumSensingFixture(Configuration config)throws Exception {
        if(!isSettledFreshFixture(config)||config.getMachine().isEnabled()||config.getMachine().isHomed()||config.getMachine().isBusy())throw new IllegalStateException("A disabled settled fresh fixture is required");
        configureSustainedWorkload(config);
        Machine machine=config.getMachine();
        for(Head item:machine.getHeads()) {
            if(item.getClass()!=ReferenceHead.class)throw new IllegalStateException("Exact native fixture head required");
            ReferenceHead head=(ReferenceHead)item;head.setPumpActuator(null);
            for(Nozzle tool:head.getNozzles()) {
                ReferenceNozzle nozzle=(ReferenceNozzle)tool;
                if(nozzle.getVacuumActuator()==null||nozzle.getVacuumActuator().getClass()!=ReferenceActuator.class)throw new IllegalStateException("Exact native fixture valve required");
                ((ReferenceActuator)nozzle.getVacuumActuator()).setValueType(Actuator.ActuatorValueType.Boolean);
                ReferenceActuator sensor=new ReferenceActuator();sensor.setName("Explicit synthetic vacuum sensor "+nozzle.getId());sensor.setDriver(machine.getDrivers().get(0));sensor.setValueType(Actuator.ActuatorValueType.Boolean);
                head.addActuator(sensor);nozzle.setVacuumSenseActuator(sensor);
                if(nozzle.getAxisZ()==null||nozzle.getAxisZ().getClass()!=ReferenceControllerAxis.class)throw new IllegalStateException("Direct native fixture Z required");
                ReferenceControllerAxis z=(ReferenceControllerAxis)nozzle.getAxisZ();z.setSafeZoneLow(new Length(0,LengthUnit.Millimeters));z.setSafeZoneHigh(new Length(0,LengthUnit.Millimeters));z.setSafeZoneLowEnabled(true);z.setSafeZoneHighEnabled(true);
            }
        }
        for(NozzleTip item:machine.getNozzleTips()) {
            if(item.getClass()!=ReferenceNozzleTip.class)throw new IllegalStateException("Exact native fixture tip required");
            ReferenceNozzleTip tip=(ReferenceNozzleTip)item;
            tip.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);tip.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.Absolute);
            tip.setVacuumLevelPartOnLow(60);tip.setVacuumLevelPartOnHigh(80);tip.setVacuumLevelPartOffLow(-5);tip.setVacuumLevelPartOffHigh(5);
            tip.setPartOnCheckAfterPick(true);tip.setPartOnCheckAlign(true);tip.setPartOnCheckBeforePlace(true);tip.setPartOffCheckBeforePick(true);tip.setPartOffCheckAfterPlace(true);
            tip.setEstablishPartOnLevel(false);tip.setEstablishPartOffLevel(false);tip.setPickDwellMilliseconds(0);tip.setPlaceDwellMilliseconds(0);tip.setPartOffProbingMilliseconds(0);tip.setPartOffDwellMilliseconds(0);
        }
        configureVacuumLifecycleFixture(machine);
        config.save();
    }
    /** Explicit synthetic fixture policy; ordinary native/sustained profiles never invoke this. */
    static void configureVacuumLifecycleFixture(Machine machine)throws Exception {
        if(machine.getClass()!=ReferenceMachine.class||machine.isEnabled()||machine.isHomed()||machine.isBusy())throw new IllegalStateException("Disabled, unhomed native sensing fixture required");
        NativeVacuumSources.requireBoundedMachine(Configuration.get());
        ((ReferenceMachine)machine).setHomeAfterEnabled(false);
        for(Actuator item:machine.getAllActuators()) {
            if(item.getClass()!=ReferenceActuator.class)throw new IllegalStateException("Exact native fixture actuator required");
            ReferenceActuator actuator=(ReferenceActuator)item;
            actuator.setEnabledActuation(ReferenceActuator.MachineStateActuation.AssumeUnknown);
            actuator.setHomedActuation(ReferenceActuator.MachineStateActuation.LeaveAsIs);
            actuator.setDisabledActuation(ReferenceActuator.MachineStateActuation.LeaveAsIs);
        }
    }
    public static void configureSustainedWorkload(Configuration config)throws Exception {
        Machine machine=config.getMachine();
        if(machine.getPnpJobProcessor().getClass()!=ReferencePnpJobProcessor.class)throw new IllegalStateException("Sustained fixture requires the pinned native ReferencePnpJobProcessor");
        // The canonical workload is a preordered grid. Keep native planning and every native
        // placement step while avoiding whole-remaining-job TSP optimization on every cycle.
        ((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).setJobOrder(ReferencePnpJobProcessor.JobOrderHint.Unsorted);
        for(Feeder original:new ArrayList<>(machine.getFeeders())){
            if(original.getPart()==null)continue;
            ReferenceTrayFeeder tray=new ReferenceTrayFeeder();tray.setName("Finite virtual supply: "+original.getPart().getId());
            tray.setPart(original.getPart());tray.setLocation(original.getPickLocation());tray.setOffsets(new Location(LengthUnit.Millimeters));
            tray.setTrayCountX(10000);tray.setTrayCountY(1);tray.setFeedCount(0);tray.setEnabled(true);
            machine.removeFeeder(original);machine.addFeeder(tray);
        }
        config.save();
    }
    public static void settleFreshFixture(Path fresh)throws Exception {
        Configuration original=Configuration.get();
        if(!original.getConfigurationDirectory().toPath().toAbsolutePath().normalize().equals(fresh.toAbsolutePath().normalize()))throw new IllegalArgumentException("Only the newly generated fixture may settle");
        if(original.getMachine().isEnabled()||original.getMachine().isHomed()||original.getMachine().isBusy())throw new IllegalStateException("Fresh fixture must remain disabled/unhomed/idle");
        Bridge.verifyNativeSimulatorClasses(original.getMachine());original.save();original.getMachine().close();Configuration.initialize(fresh.toFile());Configuration.get().load();Bridge.verifyNativeSimulatorClasses(Configuration.get().getMachine());
        if(Configuration.get().getMachine().isEnabled()||Configuration.get().getMachine().isHomed())throw new IllegalStateException("Settled fixture unexpectedly acquired enable/home state");settledFixture=Configuration.get();
    }
    private static String required(Map<String,String> options,String key){String value=options.get(key);if(value==null)throw new IllegalArgumentException("Missing "+key);return value;}
    public static void accelerateFixture(Configuration config)throws Exception {
        // Same documented acceleration recipe as upstream SampleJobTest.makeMachineFastest().
        ReferenceMachine machine=(ReferenceMachine)config.getMachine();
        ((NullDriver)machine.getDefaultDriver()).setFeedRateMmPerMinute(0);
        for(Axis axis:machine.getAxes())if(axis instanceof ReferenceControllerAxis){ReferenceControllerAxis a=(ReferenceControllerAxis)axis;a.setFeedratePerSecond(new Length(1000000,LengthUnit.Millimeters));a.setAccelerationPerSecond2(new Length(2000000,LengthUnit.Millimeters));a.setJerkPerSecond3(new Length(0,LengthUnit.Millimeters));}
        for(Camera camera:machine.getAllCameras())if(camera instanceof AbstractSettlingCamera){AbstractSettlingCamera c=(AbstractSettlingCamera)camera;c.setSettleMethod(AbstractSettlingCamera.SettleMethod.FixedTime);c.setSettleTimeMs(0);}
        for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles()){ReferenceNozzle n=(ReferenceNozzle)nozzle;n.setPickDwellMilliseconds(0);n.setPlaceDwellMilliseconds(0);}
    }
}
