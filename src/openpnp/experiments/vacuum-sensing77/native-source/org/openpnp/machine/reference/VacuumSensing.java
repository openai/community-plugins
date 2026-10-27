/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.machine.reference;

import java.util.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.model.Configuration;
import org.openpnp.spi.Actuator;
import org.openpnp.spi.Machine;

/** Process-local native sensing observation. This API is not an in-process sandbox. */
public final class VacuumSensing {
    public static final int API_VERSION = 1;
    private VacuumSensing() { }
    @FunctionalInterface public interface Observer {
        void onEvent(String event, ReferenceNozzle nozzle, Map<String,Object> data);
    }
    @FunctionalInterface public interface SampleSource { String read(long readIndex) throws Exception; }
    @FunctionalInterface public interface CheckedString { String get() throws Exception; }
    @FunctionalInterface public interface CheckedBoolean { boolean get() throws Exception; }
    @FunctionalInterface public interface CheckedVoid { void run() throws Exception; }
    private static final ThreadLocal<Scope> ACTIVE = new ThreadLocal<>();
    private static final ThreadLocal<String> STAGE = new ThreadLocal<>();
    public static final class ObserverFailure extends Error {
        private static final long serialVersionUID = 1L;
        private ObserverFailure(Throwable cause) { super("Native sensing observer failed; outcome must remain fenced", cause); }
    }
    public static final class Scope implements AutoCloseable {
        private final Thread owner = Thread.currentThread();
        private final Observer observer;
        private final Deque<Frame> frames = new ArrayDeque<>();
        private ObserverFailure fault;
        private boolean closed;
        private Scope(Observer observer) { this.observer = observer; }
        public ObserverFailure stickyFault() { return fault; }
        @Override public void close() {
            if (owner != Thread.currentThread()) throw new IllegalStateException("Sensing observer thread changed");
            if (closed) return;
            if (ACTIVE.get() != this || !frames.isEmpty()) throw new IllegalStateException("Sensing observer scope changed or active");
            ACTIVE.remove(); closed = true;
        }
    }
    public static Scope observe(Observer observer) {
        if (observer == null) throw new IllegalArgumentException("Observer required");
        if (ACTIVE.get() != null) throw new IllegalStateException("Sensing observer already registered");
        Scope scope = new Scope(observer); ACTIVE.set(scope); return scope;
    }
    public static boolean atStage(String stage, CheckedBoolean body) throws Exception {
        if (!Arrays.asList("after_pick","align","before_place","before_pick","after_place","direct").contains(stage))
            throw new IllegalArgumentException("Unknown native sensing stage");
        String previous = STAGE.get(); STAGE.set(stage);
        try { return body.get(); }
        finally { if (previous == null) STAGE.remove(); else STAGE.set(previous); }
    }
    private static final class Frame {
        final Scope scope; final ReferenceNozzle nozzle; final String kind;
        final Map<String,Object> base;
        Frame(ReferenceNozzle nozzle, String kind, String checkKind, Map<String,Object> extra) {
            this.scope=ACTIVE.get(); this.nozzle=nozzle; this.kind=kind;
            Frame parent=scope==null?null:scope.frames.peek();
            Map<String,Object> m=new LinkedHashMap<>();
            m.put("api_version",API_VERSION);m.put("observation_id",UUID.randomUUID().toString());
            m.put("parent_observation_id",parent==null?null:parent.base.get("observation_id"));
            m.put("native_stage",STAGE.get()==null?"direct":STAGE.get());
            m.put("check_kind",checkKind!=null?checkKind:parent==null?null:parent.base.get("check_kind"));
            m.put("nozzle_tip_id",nozzle.getNozzleTip()==null?null:nozzle.getNozzleTip().getId());
            m.put("sensor_id",nozzle.getVacuumSenseActuator()==null?null:nozzle.getVacuumSenseActuator().getId());
            m.put("source",sourceProvenance(nozzle));m.putAll(extra);base=Collections.unmodifiableMap(m);
        }
        void push() { if(scope!=null)scope.frames.push(this); }
        void pop() { if(scope!=null){if(scope.frames.peek()!=this)throw new IllegalStateException("Native sensing stack changed");scope.frames.pop();} }
        void emit(String phase, Map<String,Object> extra) {
            if(scope==null)return;
            if(scope.fault!=null)throw scope.fault;
            Map<String,Object> m=new LinkedHashMap<>(base);m.putAll(extra);
            try { scope.observer.onEvent(kind+"."+phase,nozzle,Collections.unmodifiableMap(m)); }
            catch(Throwable failure) {
                scope.fault=failure instanceof ObserverFailure?(ObserverFailure)failure:new ObserverFailure(failure);
                throw scope.fault;
            }
        }
        void failed(Throwable failure, String code, Map<String,Object> extra) {
            Map<String,Object> m=new LinkedHashMap<>(extra);m.put("failure_code",code);m.put("failure_type",failure.getClass().getName().substring(0,Math.min(128,failure.getClass().getName().length())));
            try { emit("failed",m); }
            catch(ObserverFailure observerFailure) { if(observerFailure!=failure)observerFailure.addSuppressed(failure);throw observerFailure; }
        }
    }
    private static Map<String,Object> raw(String value) {
        Map<String,Object> m=new LinkedHashMap<>();
        if(value==null)return m;
        StringBuilder b=new StringBuilder();
        boolean sanitized=false;
        for(int i=0;i<Math.min(value.length(),128);i++){char c=value.charAt(i);b.append(c<=127?c:'?');if(c>127)sanitized=true;}
        if(sanitized)m.put("raw_sanitized",true);
        m.put("raw",b.toString());m.put("raw_length",value.length());
        if(value.length()>128)m.put("raw_truncated",true);
        return m;
    }
    public static double read(ReferenceNozzle nozzle, CheckedString body) throws Exception {
        Frame f=new Frame(nozzle,"read",null,Collections.emptyMap());f.emit("before",Collections.emptyMap());f.push();
        String value=null;
        try {
            value=body.get();
            if(value==null||value.length()>128)throw new SensorValueException("SENSOR_VALUE_INVALID");
            for(int i=0;i<value.length();i++)if(value.charAt(i)>127)throw new SensorValueException("SENSOR_VALUE_INVALID");
            final double number;
            try{number=Double.parseDouble(value);}catch(NumberFormatException malformed){throw new SensorValueException("SENSOR_VALUE_INVALID");}
            if(!Double.isFinite(number))throw new SensorValueException("SENSOR_VALUE_NONFINITE");
            Map<String,Object> data=raw(value);data.put("value",number);f.emit("returned",data);return number;
        } catch(Exception failure) {f.failed(failure,failure instanceof SensorValueException?((SensorValueException)failure).code:"SENSOR_READ_FAILED",raw(value));throw failure;}
        catch(Error failure){f.failed(failure,"SENSOR_READ_FAILED",raw(value));throw failure;}
        finally {f.pop();}
    }
    public static final class SensorValueException extends Exception {
        private static final long serialVersionUID=1L;
        public final String code;
        private SensorValueException(String code){super(code);this.code=code;}
    }
    public static boolean check(ReferenceNozzle nozzle,String kind,CheckedBoolean body)throws Exception {
        Frame f=new Frame(nozzle,"check",kind,Collections.emptyMap());f.emit("before",Collections.emptyMap());f.push();
        try{boolean value=body.get();f.emit("returned",Collections.singletonMap("verdict",value));return value;}
        catch(Exception failure){f.failed(failure,"NATIVE_CHECK_FAILED",Collections.emptyMap());throw failure;}
        catch(Error failure){f.failed(failure,"NATIVE_CHECK_FAILED",Collections.emptyMap());throw failure;}
        finally{f.pop();}
    }
    /** The native mandatory valve-off finally must run even after an observer failed. */
    public static void probeValve(ReferenceNozzle nozzle,boolean enabled,CheckedVoid body)throws Exception {
        Map<String,Object> data=new LinkedHashMap<>();data.put("enabled",enabled);data.put("reason","part_off_probe");data.put("cleanup_attempt",!enabled);
        Frame f=new Frame(nozzle,"valve",null,data);ObserverFailure beforeFailure=null;
        try{f.emit("before",Collections.emptyMap());}
        catch(ObserverFailure failure){if(enabled)throw failure;beforeFailure=failure;}
        f.push();
        try{
            try{body.run();}
            catch(Exception|Error nativeFailure){if(beforeFailure!=null){beforeFailure.addSuppressed(nativeFailure);throw beforeFailure;}throw nativeFailure;}
            if(beforeFailure!=null)throw beforeFailure;
            f.emit("returned",Collections.emptyMap());
        }catch(Exception failure){f.failed(failure,"VALVE_ACTUATION_FAILED",Collections.emptyMap());throw failure;}
        catch(Error failure){f.failed(failure,"VALVE_ACTUATION_FAILED",Collections.emptyMap());throw failure;}
        finally{f.pop();}
    }
    public static Map<String,Object> sourceProvenance(ReferenceNozzle nozzle) {
        Actuator a=nozzle.getVacuumSenseActuator();
        if(a!=null&&a.getDriver() instanceof NullDriver){ControlledSource source=((NullDriver)a.getDriver()).getControlledVacuumSource();if(source!=null&&source.contains(a))return source.provenance();}
        Map<String,Object> m=new LinkedHashMap<>();m.put("origin","native-driver");m.put("driver_id",a==null||a.getDriver()==null?null:a.getDriver().getId());
        m.put("driver_class",a==null||a.getDriver()==null?null:a.getDriver().getClass().getName());m.put("hardware_qualified",false);
        return Collections.unmodifiableMap(m);
    }
    /** Native driver holds this transient capability; configuration serialization cannot copy it. */
    public static final class ControlledSource {
        private final NullDriver driver;private final Machine machine;private final Object owner;
        private final Map<Actuator,SampleSource> sources;private final Map<Actuator,Long> reads=new IdentityHashMap<>();
        private final Map<String,Object> provenance;private boolean closed;
        public ControlledSource(NullDriver driver,Object owner,Map<Actuator,SampleSource> sources,Map<String,String> declared) {
            if(driver==null||driver.getClass()!=NullDriver.class||owner==null||sources==null||sources.isEmpty()||sources.size()>64)throw new IllegalArgumentException("Bounded exact simulator source required");
            if(declared==null||!declared.keySet().equals(Set.of("fixture_id","scenario_id","units")))throw new IllegalArgumentException("Exact source provenance required");
            this.driver=driver;this.machine=Configuration.get().getMachine();this.owner=owner;
            if(machine==null||machine.getClass()!=ReferenceMachine.class||!machine.getDrivers().contains(driver))throw new IllegalArgumentException("Source driver is not in current exact native machine");
            Map<Actuator,SampleSource> copy=new IdentityHashMap<>();
            for(Map.Entry<Actuator,SampleSource> e:sources.entrySet()){
                Actuator a=e.getKey();if(a==null||a.getClass()!=ReferenceActuator.class||a.getDriver()!=driver||!machine.getAllActuators().contains(a)||e.getValue()==null)throw new IllegalArgumentException("Exact native actuator binding required");
                copy.put(a,e.getValue());reads.put(a,0L);
            }
            this.sources=Collections.unmodifiableMap(copy);
            Map<String,Object> p=new LinkedHashMap<>();p.put("origin","controlled-simulator");p.put("profile","controlled-native-vacuum-v1");p.put("source_id",UUID.randomUUID().toString());p.put("process_id",ProcessHandle.current().pid());p.put("simulation_only",true);p.put("hardware_qualified",false);
            for(String key:List.of("fixture_id","scenario_id","units")){String value=declared.get(key);if(value==null||value.isEmpty()||value.length()>128)throw new IllegalArgumentException("Bounded source provenance required");for(int i=0;i<value.length();i++)if(value.charAt(i)<32||value.charAt(i)>126)throw new IllegalArgumentException("ASCII source provenance required");p.put(key,value);}
            provenance=Collections.unmodifiableMap(p);
        }
        public Map<String,Object> provenance(){return provenance;}
        public synchronized boolean isClosed(){return closed;}
        public synchronized boolean contains(Actuator a){return sources.containsKey(a);}
        public synchronized void close(Object candidate){if(candidate!=owner)throw new IllegalStateException("Controlled source owner changed");closed=true;}
        public synchronized void driverClosed(NullDriver candidate){if(candidate!=driver)throw new IllegalStateException("Controlled source driver changed");closed=true;}
        private void requireCurrent(Actuator actuator) {
            if(closed||Configuration.get().getMachine()!=machine||!machine.isTask(Thread.currentThread()))throw new IllegalStateException("Controlled source is closed, changed or outside native executor");
            if(!machine.getDrivers().contains(driver)||actuator.getDriver()!=driver||!machine.getAllActuators().contains(actuator)||!sources.containsKey(actuator))throw new IllegalStateException("Controlled source actuator or driver changed");
        }
        public String read(Actuator actuator)throws Exception {
            SampleSource source;long index;
            synchronized(this){
                requireCurrent(actuator);
                index=reads.get(actuator);if(index>=1000000)throw new IllegalStateException("Controlled source sample limit reached");reads.put(actuator,index+1);source=sources.get(actuator);
            }
            String result=source.read(index);
            synchronized(this){requireCurrent(actuator);}
            return result;
        }
    }
}
