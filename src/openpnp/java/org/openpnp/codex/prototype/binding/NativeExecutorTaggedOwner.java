/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.binding;

import java.util.*;
import java.util.concurrent.Future;
import org.openpnp.codex.prototype.TypedGcodeProfile;
import org.openpnp.codex.prototype.tagged.OwnedTaggedGcodeDriver;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.model.*;
import org.openpnp.spi.Axis;

/** Private ownership composition experiment. No journal, raw command API, adoption or hardware support. */
public final class NativeExecutorTaggedOwner {
    private final Thread owner=Thread.currentThread();
    private final ReferenceMachine machine;
    private final OwnedTaggedGcodeDriver driver;
    private final TaggedModelBinding model;
    private boolean attempted,closed;
    private volatile boolean fenced;
    private static final class Bootstrap {
        final Configuration config;final ReferenceMachine machine;final Object planner,processor,locator,scripting;
        boolean consumed;
        Bootstrap(Configuration config,ReferenceMachine machine){this.config=config;this.machine=machine;planner=machine.getMotionPlanner();processor=machine.getPnpJobProcessor();locator=machine.getFiducialLocator();scripting=config.getScripting();}
        void target(){
            if(Configuration.get()!=config||config.getMachine()!=machine||machine.getClass()!=ReferenceMachine.class||config.getSystemUnits()!=LengthUnit.Millimeters
                ||machine.getMotionPlanner()!=planner||machine.getPnpJobProcessor()!=processor||machine.getFiducialLocator()!=locator||config.getScripting()!=scripting)
                throw new IllegalStateException("BOOTSTRAP_TARGET_CHANGED");
        }
        void empty(){
            target();
            if(!machine.getDrivers().isEmpty()||!machine.getAxes().isEmpty()||!machine.getHeads().isEmpty()||!machine.getCameras().isEmpty()||!machine.getFeeders().isEmpty()
                ||!machine.getActuators().isEmpty()||!machine.getSignalers().isEmpty()||!machine.getNozzleTips().isEmpty()||!config.getPackages().isEmpty()||!config.getParts().isEmpty()
                ||!config.getVisionSettings().isEmpty()||!config.getBoards().isEmpty()||!config.getPanels().isEmpty()||machine.isEnabled()||machine.isHomed())
                throw new IllegalStateException("BOOTSTRAP_SHELL_CHANGED");
        }
        void consume(){if(consumed)throw new IllegalStateException("BOOTSTRAP_ALREADY_CONSUMED");consumed=true;empty();}
    }
    private NativeExecutorTaggedOwner(Bootstrap bootstrap,TypedGcodeProfile.OwnedEndpoint endpoint)throws Exception{
        Configuration config=bootstrap.config;this.machine=bootstrap.machine;
        if(!machine.isTask(owner))throw new IllegalStateException("NATIVE_EXECUTOR_REQUIRED");
        bootstrap.consume();driver=new OwnedTaggedGcodeDriver(endpoint);bootstrap.empty();machine.addDriver(driver);
        ReferenceHead head=new ReferenceHead();machine.addHead(head);ReferenceNozzle nozzle=new ReferenceNozzle();head.addNozzle(nozzle);
        for(Axis.Type type:Axis.Type.values()){
            ReferenceControllerAxis axis=new ReferenceControllerAxis();axis.setType(type);axis.setName("Owned diagnostic "+type);axis.setLetter(type==Axis.Type.Rotation?"A":type.name());axis.setDriver(driver);
            axis.setHomeCoordinate(mm(0));axis.setSoftLimitLow(mm(type==Axis.Type.Rotation?-180:0));axis.setSoftLimitHigh(mm(type==Axis.Type.Rotation?180:100));
            axis.setSoftLimitLowEnabled(type!=Axis.Type.Rotation);axis.setSoftLimitHighEnabled(type!=Axis.Type.Rotation);
            axis.setFeedratePerSecond(mm(1));axis.setAccelerationPerSecond2(mm(1));axis.setJerkPerSecond3(mm(0));axis.setResolution(.001);
            machine.addAxis(axis);
            switch(type){case X:nozzle.setAxisX(axis);break;case Y:nozzle.setAxisY(axis);break;case Z:nozzle.setAxisZ(axis);break;case Rotation:nozzle.setAxisRotation(axis);break;}
        }
        bootstrap.target();model=new TaggedModelBinding(config);bootstrap.target();
    }
    private static Length mm(double n){return new Length(n,LengthUnit.Millimeters);}
    public static final class Launch {
        private final Bootstrap bootstrap;
        private final TypedGcodeProfile.OwnedEndpoint endpoint;
        private Launch(Bootstrap bootstrap,TypedGcodeProfile.OwnedEndpoint endpoint){this.bootstrap=bootstrap;this.endpoint=endpoint;}
        public NativeExecutorTaggedOwner bind()throws Exception{return new NativeExecutorTaggedOwner(bootstrap,endpoint);}
        public void guard(Configuration config){if(config!=bootstrap.config||config.getMachine()!=bootstrap.machine)throw new IllegalStateException("LAUNCH_TARGET_CHANGED");bootstrap.target();}
    }
    public static Launch prepare(Configuration config,TypedGcodeProfile.OwnedEndpoint endpoint)throws Exception{
        if(config==null||Configuration.get()!=config||config.getMachine()!=null||config.getSystemUnits()!=LengthUnit.Millimeters
            ||!config.getPackages().isEmpty()||!config.getParts().isEmpty()||!config.getVisionSettings().isEmpty()||!config.getBoards().isEmpty()||!config.getPanels().isEmpty())
            throw new IllegalStateException("FRESH_EMPTY_CONFIGURATION_REQUIRED");
        endpoint.check();ReferenceMachine machine=new ReferenceMachine();Bootstrap bootstrap=new Bootstrap(config,machine);config.setMachine(machine);
        return new Launch(bootstrap,endpoint);
    }
    public void validate()throws Exception{preflight();}
    private void thread(){if(Thread.currentThread()!=owner||!machine.isTask(owner))throw new IllegalStateException("ORIGINAL_NATIVE_EXECUTOR_REQUIRED");}
    private void preflight()throws Exception{
        thread();if(closed||fenced)throw new IllegalStateException("TAGGED_OWNER_FENCED");
        try{model.check();}catch(Throwable error){fenced=true;driver.disconnect();throw error;}
    }
    public synchronized void connect()throws Exception{
        preflight();if(attempted)throw new IllegalStateException("CONNECT_ALREADY_ATTEMPTED");attempted=true;
        try{driver.connect();model.check();}catch(Throwable error){fenced=true;driver.disconnect();throw error;}
    }
    public synchronized void identify()throws Exception{
        preflight();if(!attempted)throw new IllegalStateException("CONNECT_REQUIRED");
        try{driver.sendCommand("M115",200);model.check();}catch(Throwable error){fenced=true;driver.disconnect();throw error;}
    }
    public synchronized void close(){thread();closed=true;driver.disconnect();}
    /** Resource revocation waits for an in-progress owner action, then closes only this immutable owned transport from another thread.
     * It cannot issue a controller command, bind a new worker or establish physical standstill. */
    public synchronized void revoke()throws Exception{fenced=true;driver.getCommunications().disconnect();}
    public Map<String,Object> snapshot(){
        thread();Map<String,Object> out=new LinkedHashMap<>();out.put("profile",OwnedTaggedGcodeDriver.PROFILE);out.put("native_task",machine.isTask(owner));
        out.put("owner_thread_id",owner.getId());out.put("model_binding_sha256",model.fingerprint());out.put("connect_attempted",attempted);
        out.put("owner_fenced",fenced);out.put("closed",closed);out.put("protocol",driver.protocolSnapshot());out.put("current_model_validation_performed_by_snapshot",false);out.put("durable_receipt",false);out.put("physical_qualification",false);return Collections.unmodifiableMap(out);
    }
}
