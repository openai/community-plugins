/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.driver.NullMotionPlanner;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.axis.ReferenceVirtualAxis;
import org.openpnp.spi.base.AbstractHeadMountable;
import org.openpnp.model.*;
import org.openpnp.spi.*;

/** Bounded native sensing operations. The caller supplies durable observations and ownership fences.
 * A reading is synthetic native-actuator units, never physical qualification or an inventory count.
 * Deliberately not exposed by Bridge until journal, restart and job integration are qualified. */
public final class NativeVacuumSensing {
    public static final String PROFILE = "native-vacuum-sensing-v1";
    private NativeVacuumSensing() { }
    @FunctionalInterface public interface Guard { void check() throws Exception; }

    /** Retains actual identities and all sensing-relevant settings before entering the native task. */
    public static Plan admit(Configuration config, String nozzleId) throws Exception {
        if (config == null || config.getMachine() == null || Configuration.get() != config)
            throw fault("SENSING_CONFIGURATION_CHANGED", "A current native configuration is required");
        Machine machine = config.getMachine();
        if (machine.getClass() != ReferenceMachine.class)
            throw fault("SENSING_PROFILE_UNSUPPORTED", "Exact native ReferenceMachine required");
        Bridge.verifyNativeSimulatorClasses(machine);
        if (machine.getHeads().size() > 8) throw fault("SENSING_GRAPH_LIMIT", "At most eight native heads are supported");
        ReferenceNozzle selected = null;
        for (Head head : machine.getHeads()) for (Nozzle candidate : head.getNozzles()) {
            if (Objects.equals(candidate.getId(), nozzleId)) {
                if (selected != null || candidate.getClass() != ReferenceNozzle.class)
                    throw fault("SENSING_IDENTITY_AMBIGUOUS", "Select one exact native ReferenceNozzle");
                selected = (ReferenceNozzle) candidate;
            }
        }
        if (selected == null) throw fault("NOT_FOUND", "Unknown native nozzle");
        return new Plan(config, selected);
    }

    public static final class Plan {
        private final Configuration config;
        private final Machine machine;
        private final ReferenceNozzle nozzle;
        private final ReferenceNozzleTip tip;
        private final Actuator sensor, valve;
        private final Driver driver;
        private final VacuumSensing.ControlledSource authority;
        private final Map<String,Object> source;
        private final List<Object> baseline;
        private final Graph graph;
        private boolean used;

        private Plan(Configuration config, ReferenceNozzle nozzle) throws Exception {
            this.config = config; this.machine = config.getMachine(); this.nozzle = nozzle;
            tip = nozzle.getNozzleTip(); sensor = nozzle.getVacuumSenseActuator(); valve = nozzle.getVacuumActuator();
            if (tip == null || tip.getClass() != ReferenceNozzleTip.class || sensor == null || valve == null
                    || sensor.getClass() != ReferenceActuator.class || valve.getClass() != ReferenceActuator.class)
                throw fault("SENSING_BINDING_REQUIRED", "Install one native tip and bind exact native sensor and vacuum valve");
            driver = ((ReferenceActuator) sensor).getDriver();
            if (driver == null || driver.getClass() != NullDriver.class || ((ReferenceActuator) valve).getDriver() != driver)
                throw fault("SENSING_PROFILE_UNSUPPORTED", "Sensor and valve must use the same exact native NullDriver");
            authority = ((NullDriver) driver).getControlledVacuumSource();
            if (authority == null || authority.isClosed() || !authority.contains(sensor))
                throw fault("SENSING_SOURCE_UNQUALIFIED", "An active native controlled simulator source is required");
            source = Collections.unmodifiableMap(new LinkedHashMap<>(VacuumSensing.sourceProvenance(nozzle)));
            requireSource(source);
            validateSettings(nozzle, tip);
            baseline = state(nozzle, tip);
            graph = graph(machine, nozzle, tip, sensor, valve, driver);
            validateCurrentState();
        }

        public void validateCurrentState() throws Exception {
            if (Configuration.get() != config || config.getMachine() != machine || nozzle.getNozzleTip() != tip
                    || nozzle.getVacuumSenseActuator() != sensor || nozzle.getVacuumActuator() != valve
                    || ((ReferenceActuator) sensor).getDriver() != driver || ((ReferenceActuator) valve).getDriver() != driver
                    || !machine.getDrivers().contains(driver) || !containsExactly(machine.getAllActuators(), sensor)
                    || !containsExactly(machine.getAllActuators(), valve) || !machine.getNozzleTips().contains(tip))
                throw fault("SENSING_IDENTITY_CHANGED", "Native sensing identities changed after admission");
            if (((NullDriver) driver).getControlledVacuumSource() != authority || authority.isClosed())
                throw fault("SENSING_SOURCE_CHANGED", "The admitted controlled source is revoked or replaced");
            if (!graph.same(graph(machine, nozzle, tip, sensor, valve, driver)) || !baseline.equals(state(nozzle, tip)) || !source.equals(VacuumSensing.sourceProvenance(nozzle)))
                throw fault("SENSING_CONFIGURATION_CHANGED", "Native sensing settings or source generation changed after admission");
        }

        public Map<String,Object> measure(int samples, Guard guard, VacuumSensing.Observer observer) throws Exception {
            if (samples < 1 || samples > 32) throw fault("SENSING_SAMPLE_LIMIT", "Collect 1–32 immediate native samples");
            enter(guard, observer);
            List<Double> values = new ArrayList<>(); long started = System.nanoTime();
            try (VacuumSensing.Scope scope = VacuumSensing.observe(observe(guard, observer, null))) {
                for (int i = 0; i < samples; i++) {
                    guard.check(); validateCurrentState();
                    values.add(nozzle.readVacuumLevel());
                    guard.check(); validateCurrentState();
                }
            }
            Map<String,Object> result = receipt(started);
            result.put("samples", Collections.unmodifiableList(values)); result.put("sample_count", values.size());
            result.put("native_effects", List.of("sensor-read")); result.put("native_effects_ordered", false); result.put("part_state_inferred", false);
            return result;
        }

        public Map<String,Object> verify(String state, Guard guard, VacuumSensing.Observer observer) throws Exception {
            if (!Set.of("part_on", "part_off").contains(state)) throw fault("INVALID_PART_STATE", "Expected part_on or part_off");
            ReferenceNozzleTip.VacuumMeasurementMethod method = state.equals("part_on") ? tip.getMethodPartOn() : tip.getMethodPartOff();
            if (method != ReferenceNozzleTip.VacuumMeasurementMethod.Absolute)
                throw fault("SENSING_METHOD_UNSUPPORTED", "Configure an Absolute threshold before native verification");
            enter(guard, observer); boolean verdict; long started = System.nanoTime();
            try (VacuumSensing.Scope scope = VacuumSensing.observe(observe(guard, observer, state))) {
                boundary(guard, state);
                verdict = state.equals("part_on") ? nozzle.isPartOn() : nozzle.isPartOff();
                boundary(guard, state);
            }
            Map<String,Object> result = receipt(started);
            result.put("check_kind", state); result.put("native_verdict", verdict);
            result.put("native_effects", state.equals("part_off") ? List.of("vacuum-probe-on", "sensor-read", "vacuum-probe-off") : List.of("sensor-read"));
            result.put("native_effects_ordered", false);
            result.put("occupancy_authority", "requires-separate-journal-disposition");
            return result;
        }

        public ReferenceNozzle nozzle() { return nozzle; }
        public Map<String,Object> source() { return source; }
        private void enter(Guard guard, VacuumSensing.Observer observer) throws Exception {
            if (used) throw fault("SENSING_PLAN_CONSUMED", "A sensing operation cannot be replayed");
            if (guard == null || observer == null) throw fault("SENSING_CONTEXT_REQUIRED", "Ownership guard and durable observer required");
            if (!machine.isTask(Thread.currentThread())) throw fault("NATIVE_EXECUTOR_REQUIRED", "Sensing requires the native machine executor");
            guard.check(); validateCurrentState(); used = true;
        }
        private void boundary(Guard guard, String check) throws Exception {
            guard.check(); validateCurrentState();
            if ("part_off".equals(check)) {
                Location location = nozzle.getLocation();
                if (!machine.isEnabled() || !machine.isHomed() || !finite(location)
                        || !nozzle.isInSafeZZone(location.getLengthZ()))
                    throw fault("SENSING_SAFE_Z_REQUIRED", "Part-off probing requires enabled, homed, finite native Safe Z");
                if (nozzle.getPart() != null) throw fault("NOZZLE_OCCUPIED", "Part-off probing cannot release a model-held part");
            }
        }
        private VacuumSensing.Observer observe(Guard guard, VacuumSensing.Observer observer, String check) {
            return (event, observedNozzle, data) -> {
                // The native finally must still close the valve after ownership/observer failure.
                boolean cleanup = event.startsWith("valve.") && Boolean.FALSE.equals(data.get("enabled"));
                try {
                    if (observedNozzle != nozzle) throw fault("SENSING_IDENTITY_CHANGED", "Foreign native observation");
                    if (!cleanup) boundary(guard, check);
                    observer.onEvent(event, observedNozzle, data);
                    if (!cleanup) boundary(guard, check);
                } catch (Exception failure) { throw new BoundaryFailure(failure); }
            };
        }
        private Map<String,Object> receipt(long started) {
            Map<String,Object> result = new LinkedHashMap<>();
            result.put("profile", PROFILE); result.put("nozzle_id", nozzle.getId()); result.put("nozzle_tip_id", tip.getId());
            result.put("sensor_id", sensor.getId()); result.put("source", source); result.put("reading_units", "native-actuator-units");
            result.put("elapsed_ns", System.nanoTime() - started); result.put("simulation_only", true); result.put("physical_qualification", false);
            return result;
        }
    }

    private static final class BoundaryFailure extends RuntimeException {
        private static final long serialVersionUID = 1L;
        BoundaryFailure(Exception cause) { super(cause); }
    }
    private static void requireSource(Map<String,Object> source) throws Bridge.Fault {
        Set<String> fields = Set.of("origin","profile","source_id","process_id","simulation_only","hardware_qualified","fixture_id","scenario_id","units");
        boolean uuid = false;
        try { uuid = source.get("source_id") instanceof String && UUID.fromString((String)source.get("source_id")).toString().equals(source.get("source_id")); }
        catch (IllegalArgumentException ignored) { }
        if (!source.keySet().equals(fields) || !"controlled-simulator".equals(source.get("origin"))
                || !"controlled-native-vacuum-v1".equals(source.get("profile")) || !Boolean.TRUE.equals(source.get("simulation_only"))
                || !Boolean.FALSE.equals(source.get("hardware_qualified")) || !uuid
                || !Long.valueOf(ProcessHandle.current().pid()).equals(source.get("process_id"))
                || !"native-actuator-units".equals(source.get("units")))
            throw fault("SENSING_SOURCE_UNQUALIFIED", "An explicit process-local controlled native simulator source is required");
    }
    private static final class Graph {
        final List<Object> identities = new ArrayList<>(), values = new ArrayList<>();
        boolean same(Graph other) {
            if (!values.equals(other.values) || identities.size() != other.identities.size()) return false;
            for (int i=0;i<identities.size();i++) if (identities.get(i)!=other.identities.get(i)) return false;
            return true;
        }
    }
    private static Graph graph(Machine m, ReferenceNozzle n, ReferenceNozzleTip t, Actuator sensor, Actuator valve, Driver driver) throws Exception {
        Bridge.verifyNativeSimulatorClasses(m);
        if (m.getDrivers().size()!=1 || m.getDrivers().get(0)!=driver || m.getMotionPlanner().getClass()!=NullMotionPlanner.class)
            throw fault("SENSING_PROFILE_UNSUPPORTED", "One exact NullDriver and exact NullMotionPlanner required");
        if (m.getHeads().size()>8 || m.getAxes().size()>16 || m.getNozzleTips().size()>128 || m.getAllActuators().size()>64)
            throw fault("SENSING_GRAPH_LIMIT", "Native sensing graph exceeds the bounded profile");
        if (n.getHead()==null || n.getHead().getClass()!=ReferenceHead.class || sensor.getHead()!=n.getHead() || valve.getHead()!=n.getHead()
                || !containsExactly(n.getHead().getActuators(),sensor) || !containsExactly(n.getHead().getActuators(),valve)
                || ((ReferenceActuator)valve).getValueType()!=Actuator.ActuatorValueType.Boolean
                || ((ReferenceHead)n.getHead()).getPumpActuator()!=null)
            throw fault("SENSING_PROFILE_UNSUPPORTED", "Same-head exact bindings, Boolean valve and no head pump required");
        if (m.getNozzleTip(t.getId())!=t || !n.getCompatibleNozzleTips().contains(t))
            throw fault("SENSING_IDENTITY_CHANGED", "Installed tip must be exact canonical and compatible");
        Graph g = new Graph(); Collections.addAll(g.identities,m,driver,m.getMotionPlanner(),n.getHead(),n,t,sensor,valve);
        g.values.add(driver.getId()); Set<String> ids = new HashSet<>();
        for (NozzleTip item:m.getNozzleTips()) { unique(ids,item.getId());g.identities.add(item);g.values.add(item.getId()); }
        ids.clear(); int selected=0,count=0;
        for (Head h:m.getHeads()) {
            unique(ids,h.getId());g.identities.add(h);g.values.add(h.getId());
            for(Nozzle nozzle:h.getNozzles()) {
                if (++count>32) throw fault("SENSING_GRAPH_LIMIT", "At most 32 native nozzles required");
                unique(ids,nozzle.getId());g.identities.add(nozzle);g.identities.add(nozzle.getHead());g.values.add(nozzle.getId());
                if(nozzle==n)selected++;
            }
        }
        if(selected!=1)throw fault("SENSING_IDENTITY_CHANGED", "Nozzle membership is ambiguous");
        ids.clear();
        for(Actuator actuator:m.getAllActuators()) {
            unique(ids,actuator.getId());g.identities.add(actuator);g.identities.add(actuator.getHead());g.identities.add(actuator.getDriver());g.values.add(actuator.getId());
        }
        for(Actuator actuator:List.of(sensor,valve)) {
            ReferenceActuator a=(ReferenceActuator)actuator;
            Collections.addAll(g.values,a.getName(),a.getIndex(),a.getValueType(),a.getCoordinatedBeforeActuateEnum(),a.getCoordinatedAfterActuateEnum(),a.getCoordinatedBeforeReadEnum(),a.getHeadOffsets());
        }
        ids.clear();
        for(Axis axis:m.getAxes()) {
            unique(ids,axis.getId());
            if(m.getAxis(axis.getId())!=axis)throw fault("SENSING_IDENTITY_CHANGED", "Native axis identity changed");
            if(axis.getClass()==ReferenceVirtualAxis.class) {
                ReferenceVirtualAxis virtual=(ReferenceVirtualAxis)axis;
                g.identities.add(virtual);Collections.addAll(g.values,virtual.getId(),virtual.getType(),virtual.getHomeCoordinate());
                continue;
            }
            if(axis.getClass()!=ReferenceControllerAxis.class || ((ReferenceControllerAxis)axis).getDriver()!=driver)
                throw fault("SENSING_PROFILE_UNSUPPORTED", "Only exact simulator controller and virtual axes are supported");
            ReferenceControllerAxis a=(ReferenceControllerAxis)axis;
            g.identities.add(a);Collections.addAll(g.values,a.getId(),a.getType(),a.getResolution(),a.isInvertLinearRotational(),a.isSafeZoneLowEnabled(),a.isSafeZoneHighEnabled(),a.getSafeZoneLow(),a.getSafeZoneHigh());
        }
        for(Axis.Type type:Axis.Type.values()) {
            Axis a=((AbstractHeadMountable)n).getAxis(type);g.identities.add(a);
            if(a!=null && (m.getAxis(a.getId())!=a || a.getType()!=type))throw fault("SENSING_IDENTITY_CHANGED", "Native nozzle axis mapping changed");
        }
        Axis az=n.getAxisZ();
        if(az==null || az.getClass()!=ReferenceControllerAxis.class)throw fault("SENSING_SAFE_Z_REQUIRED", "A direct native controller Z is required");
        ReferenceControllerAxis z=(ReferenceControllerAxis)az;
        double low=millimeters(z.getSafeZoneLow()),high=millimeters(z.getSafeZoneHigh());
        if(z.isInvertLinearRotational() || !z.isSafeZoneLowEnabled() || !z.isSafeZoneHighEnabled() || !Double.isFinite(low) || !Double.isFinite(high)
                || low>high || !Double.isFinite(z.getResolution()) || z.getResolution()<=0)
            throw fault("SENSING_SAFE_Z_REQUIRED", "Explicit finite ordered controller Safe Z limits and resolution required");
        Collections.addAll(g.values,n.getHeadOffsets(),n.isEnableDynamicSafeZ());
        return g;
    }
    private static void unique(Set<String> ids, String id) throws Bridge.Fault {
        if(id==null || !id.matches("[A-Za-z0-9_.:+-]{1,128}") || !ids.add(id.toLowerCase(Locale.ROOT)))
            throw fault("SENSING_IDENTITY_CHANGED", "Native identifiers must be bounded and unique");
    }
    private static double millimeters(Length length) { return length==null ? Double.NaN : length.convertToUnits(LengthUnit.Millimeters).getValue(); }
    private static boolean finite(Location location) { return location!=null && Double.isFinite(location.getX()) && Double.isFinite(location.getY()) && Double.isFinite(location.getZ()) && Double.isFinite(location.getRotation()); }
    private static boolean containsExactly(List<Actuator> values, Actuator target) {
        int identical = 0;
        for (Actuator value : values) {
            if (value == target) identical++;
            else if (Objects.equals(value.getId(), target.getId())) return false;
        }
        return identical == 1;
    }
    private static void validateSettings(ReferenceNozzle n, ReferenceNozzleTip t) throws Bridge.Fault {
        for (ReferenceNozzleTip.VacuumMeasurementMethod method : Arrays.asList(t.getMethodPartOn(), t.getMethodPartOff()))
            if (method == null || !(method == ReferenceNozzleTip.VacuumMeasurementMethod.None || method == ReferenceNozzleTip.VacuumMeasurementMethod.Absolute))
                throw fault("SENSING_METHOD_UNSUPPORTED", "This profile requires None or Absolute sensing");
        if (t.isEstablishPartOnLevel() || t.isEstablishPartOffLevel())
            throw fault("SENSING_PROFILE_UNSUPPORTED", "Graph establishment is outside the bounded read profile");
        double[] bounds = {t.getVacuumLevelPartOnLow(),t.getVacuumLevelPartOnHigh(),t.getVacuumLevelPartOffLow(),t.getVacuumLevelPartOffHigh()};
        for (double v : bounds) if (!Double.isFinite(v) || Math.abs(v) > 1e6)
            throw fault("SENSING_THRESHOLDS_INVALID", "Sensing thresholds must be finite native-unit values");
        if (bounds[0] > bounds[1] || bounds[2] > bounds[3]
                || t.getMethodPartOn() == ReferenceNozzleTip.VacuumMeasurementMethod.Absolute && bounds[0] == bounds[1]
                || t.getMethodPartOff() == ReferenceNozzleTip.VacuumMeasurementMethod.Absolute && bounds[2] == bounds[3])
            throw fault("SENSING_THRESHOLDS_INVALID", "Absolute sensing requires a nonempty threshold interval");
        if (t.getMethodPartOn() == ReferenceNozzleTip.VacuumMeasurementMethod.Absolute && t.getMethodPartOff() == ReferenceNozzleTip.VacuumMeasurementMethod.Absolute
                && Math.max(bounds[0],bounds[2]) <= Math.min(bounds[1],bounds[3]))
            throw fault("SENSING_THRESHOLDS_INVALID", "Part-on and part-off threshold intervals must be disjoint");
        int[] times = {t.getPartOffProbingMilliseconds(),t.getPartOffDwellMilliseconds(),n.getPickDwellMilliseconds(),n.getPlaceDwellMilliseconds(),t.getPickDwellMilliseconds(),t.getPlaceDwellMilliseconds()};
        for (int ms : times) if (ms < 0 || ms > 1000)
            throw fault("SENSING_TIMING_INVALID", "Each sensing or tooling dwell must be between 0 and 1000 ms");
    }
    private static List<Object> state(ReferenceNozzle n, ReferenceNozzleTip t) {
        return Arrays.asList(t.getMethodPartOn(), t.getMethodPartOff(), t.getVacuumLevelPartOnLow(), t.getVacuumLevelPartOnHigh(),
            t.getVacuumLevelPartOffLow(), t.getVacuumLevelPartOffHigh(), t.isPartOnCheckAfterPick(), t.isPartOnCheckAlign(),
            t.isPartOnCheckBeforePlace(), t.isPartOffCheckAfterPlace(), t.isPartOffCheckBeforePick(), t.isEstablishPartOnLevel(),
            t.isEstablishPartOffLevel(), t.getPartOffProbingMilliseconds(), t.getPartOffDwellMilliseconds(), n.getPickDwellMilliseconds(),
            n.getPlaceDwellMilliseconds(), t.getPickDwellMilliseconds(), t.getPlaceDwellMilliseconds());
    }
    private static Bridge.Fault fault(String code, String message) { return new Bridge.Fault(code, message); }
}
