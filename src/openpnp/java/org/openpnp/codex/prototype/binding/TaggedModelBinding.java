/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex.prototype.binding;

import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.model.Configuration;
import org.openpnp.model.LengthUnit;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.driver.GcodeDriver;
import org.openpnp.codex.prototype.tagged.OwnedTaggedGcodeDriver;
import org.openpnp.spi.*;

/** Explicit tagged-profile derivative of the frozen model-binding53 guard.
 * Private preflight binding for the newly created owned controller's persisted machine model.
 * Serializer-visible configuration is not physical state, arbitrary-memory integrity or a sandbox.
 * Caller must hold exclusive model ownership; captured/replayed JSON cannot recreate this object. */
public final class TaggedModelBinding {
    private static final int MAX_XML_BYTES = 1024 * 1024;
    private final Configuration configuration;
    private final ReferenceMachine machine;
    private final ReferenceHead head;
    private final ReferenceNozzle nozzle;
    private final GcodeDriver driver;
    private final List<Axis> axes;
    private final Object planner, processor, locator, scripting;
    private final Thread owner = Thread.currentThread();
    private final String fingerprint;
    private boolean fault;

    public TaggedModelBinding(Configuration configuration) throws Exception {
        if (configuration == null || Configuration.get() != configuration || configuration.getMachine() == null || configuration.getMachine().getClass() != ReferenceMachine.class)
            throw new IllegalArgumentException("OWNED_NATIVE_MODEL_REQUIRED");
        this.configuration = configuration; machine = (ReferenceMachine) configuration.getMachine();
        if (machine.getDrivers().size() != 1 || machine.getDrivers().get(0).getClass() != OwnedTaggedGcodeDriver.class || machine.getHeads().size() != 1 || machine.getHeads().get(0).getClass() != ReferenceHead.class
            || machine.getHeads().get(0).getNozzles().size() != 1 || machine.getHeads().get(0).getNozzles().get(0).getClass() != ReferenceNozzle.class)
            throw new IllegalArgumentException("OWNED_NATIVE_GRAPH_REQUIRED");
        head = (ReferenceHead) machine.getHeads().get(0); nozzle = (ReferenceNozzle) head.getNozzles().get(0); driver = (GcodeDriver) machine.getDrivers().get(0);
        axes = List.copyOf(machine.getAxes()); planner = machine.getMotionPlanner(); processor = machine.getPnpJobProcessor(); locator = machine.getFiducialLocator(); scripting = configuration.getScripting();
        graph(); String first = persistedDigest(), second = persistedDigest();
        if (!first.equals(second)) throw new IllegalArgumentException("UNSTABLE_NATIVE_SERIALIZATION");
        fingerprint = second;
    }
    public String fingerprint() { thread(); return fingerprint; }
    public void check() throws Exception {
        thread(); if (fault) throw new IllegalStateException("NATIVE_MODEL_BINDING_FENCED");
        try { graph(); if (!fingerprint.equals(persistedDigest())) throw new IllegalStateException("NATIVE_PERSISTED_MODEL_CHANGED"); }
        catch (Throwable failure) { fault = true; throw failure; }
    }
    private void thread() { if (Thread.currentThread() != owner) throw new IllegalStateException("WRONG_OWNER_THREAD"); }
    private void graph() {
        if (Configuration.get() != configuration || configuration.getMachine() != machine || configuration.getSystemUnits() != LengthUnit.Millimeters || configuration.getScripting() != scripting)
            throw new IllegalStateException("NATIVE_CONFIGURATION_CHANGED");
        if (machine.getDrivers().size() != 1 || machine.getDrivers().get(0) != driver || machine.getHeads().size() != 1 || machine.getHeads().get(0) != head
            || head.getNozzles().size() != 1 || head.getNozzles().get(0) != nozzle || nozzle.getHead() != head || head.getMachine() != machine
            || machine.getMotionPlanner() != planner || machine.getPnpJobProcessor() != processor || machine.getFiducialLocator() != locator)
            throw new IllegalStateException("NATIVE_GRAPH_CHANGED");
        if (axes.size() != 4 || machine.getAxes().size() != 4) throw new IllegalStateException("NATIVE_AXIS_GRAPH_CHANGED");
        Set<Axis.Type> types = EnumSet.noneOf(Axis.Type.class);
        for (int i = 0; i < axes.size(); i++) {
            Axis axis = axes.get(i);
            if (machine.getAxes().get(i) != axis || axis.getClass() != ReferenceControllerAxis.class || axis.getType() == null || ((ReferenceControllerAxis)axis).getDriver() != driver
                || !types.add(axis.getType()) || nozzle.getAxis(axis.getType()) != axis) throw new IllegalStateException("NATIVE_AXIS_GRAPH_CHANGED");
        }
        if (!machine.getCameras().isEmpty() || !machine.getFeeders().isEmpty() || !machine.getActuators().isEmpty() || !machine.getSignalers().isEmpty() || !machine.getNozzleTips().isEmpty()
            || !head.getCameras().isEmpty() || !head.getActuators().isEmpty() || !configuration.getPackages().isEmpty() || !configuration.getParts().isEmpty()
            || !configuration.getVisionSettings().isEmpty() || !configuration.getBoards().isEmpty() || !configuration.getPanels().isEmpty())
            throw new IllegalStateException("NATIVE_GRAPH_EXPANDED");
        if (head.getPumpActuator() != null || head.getzProbeActuator() != null || nozzle.getVacuumActuator() != null || nozzle.getVacuumSenseActuator() != null || nozzle.getBlowOffActuator() != null)
            throw new IllegalStateException("NATIVE_ACTUATOR_REFERENCE_ADDED");
        // The native executor reports busy while running its current task. That exact
        // owning task is permitted; an unrelated busy owner is not quiescent admission.
        if (machine.isEnabled() || machine.isHomed() || (machine.isBusy() && !machine.isTask(owner)) || nozzle.getPart() != null || nozzle.getNozzleTip() != null)
            throw new IllegalStateException("NATIVE_STATE_NOT_QUIESCENT");
    }
    private String persistedDigest() throws Exception {
        BoundedWriter writer = new BoundedWriter(); Configuration.createSerializer().write(machine, writer);
        byte[] bytes = (configuration.getSystemUnits().name() + "\n" + writer).getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_XML_BYTES) throw new IOException("NATIVE_MODEL_TOO_LARGE");
        StringBuilder result = new StringBuilder(); for (byte value : MessageDigest.getInstance("SHA-256").digest(bytes)) result.append(String.format("%02x", value)); return result.toString();
    }
    private static final class BoundedWriter extends Writer {
        private final StringBuilder text = new StringBuilder();
        @Override public void write(char[] value, int start, int length) throws IOException {
            if (length < 0 || text.length() + length > MAX_XML_BYTES) throw new IOException("NATIVE_MODEL_TOO_LARGE"); text.append(value,start,length);
        }
        @Override public void flush() {} @Override public void close() {} @Override public String toString() { return text.toString(); }
    }
}
