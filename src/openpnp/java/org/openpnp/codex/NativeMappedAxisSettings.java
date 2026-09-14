/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.JsonObject;
import com.google.gson.JsonElement;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.AbstractAxis;
import org.openpnp.spi.base.AbstractHeadMountable;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.axis.ReferenceMappedAxis;
import org.openpnp.machine.reference.axis.ReferenceVirtualAxis;
import org.openpnp.machine.reference.driver.NullDriver;

/**
 * Bounded existing native two-point mapped linear axes directly over a same-type
 * native controller axis on NullDriver. No persistence, motion, hardware, scripts or reflection.
 * Caller MUST serialize idle staging and native-executor apply, bind its configuration revision,
 * journal intent before apply, invalidate dependent calibration/job/registration, then persist.
 * Setter/save failures MUST fence configuration; this helper does not roll back or retry.
 */
public final class NativeMappedAxisSettings {
    public static final String TYPE = "set_mapped_axis_geometry";
    public static final double MAX_MM = 1000, MIN_SPAN_MM = 0.001, MIN_SCALE = 0.001, MAX_SCALE = 1000;
    private NativeMappedAxisSettings() { }

    public static final class Fault extends Exception {
        public final String code;
        public Fault(String code, String message) { super(message); this.code=code; }
        Fault(String code, String message, Throwable cause) { super(message,cause); this.code=code; }
    }
    /** Immutable explicit-mm proposal. No native class, arbitrary property, ID or type setter. */
    public static final class Change {
        public final String axisId, inputAxisId;
        public final double input0Mm, output0Mm, input1Mm, output1Mm;
        public Change(String axisId, String inputAxisId, double input0Mm, double output0Mm,
                      double input1Mm, double output1Mm) {
            this.axisId=axisId; this.inputAxisId=inputAxisId; this.input0Mm=input0Mm;
            this.output0Mm=output0Mm; this.input1Mm=input1Mm; this.output1Mm=output1Mm;
        }
    }
    public static final class Plan {
        private final Configuration config;
        private final Machine machine;
        private final ReferenceMappedAxis axis;
        private final ReferenceControllerAxis input;
        private final Change change;
        private final Snapshot baseline;
        private final Snapshot expectedAfter;
        private final Map<String,Object> metadata;
        private boolean used;
        Plan(Configuration c, ReferenceMappedAxis a, ReferenceControllerAxis i, Change change,
             Snapshot snapshot, Map<String,Object> metadata) throws Exception {
            this.config=c;machine=c.getMachine();axis=a;input=i;this.change=change;
            baseline=snapshot;this.metadata=Collections.unmodifiableMap(metadata);
            expectedAfter=snapshot(machine,axis,input,change);
        }
        public Map<String,Object> metadata() { return metadata; }
        /** Pure original-plan guard; caller serializes idle reads against its native executor. */
        public synchronized void validateCurrentState() throws Exception {
            if(used) fail("PLAN_ALREADY_USED","A used plan cannot be replayed");
            if(Configuration.get()!=config || config.getMachine()!=machine)
                fail("STALE_AXIS_PLAN","Native configuration identity changed");
            requireSimulator(config,true);
            if(!baseline.same(snapshot(machine)) || machine.getAxis(change.axisId)!=axis || machine.getAxis(change.inputAxisId)!=input)
                fail("STALE_AXIS_PLAN","Axis identity, source linkage or native geometry changed since staging");
            validateGraph(machine);validateMapping(axis,input,change);
        }
        /** Once-only model edit. Requires caller's native executor/revision/durability fence. */
        public synchronized void apply() throws Exception {
            if(!machine.isTask(Thread.currentThread()))fail("NATIVE_EXECUTOR_REQUIRED","Only the native executor may apply axis geometry");
            try{validateCurrentState();}finally{used=true;}
            try {
                // Revocation is a model observation change, not native homing or driver motion.
                machine.setHomed(false);
                axis.setMapInput0(mm(change.input0Mm)); axis.setMapOutput0(mm(change.output0Mm));
                axis.setMapInput1(mm(change.input1Mm)); axis.setMapOutput1(mm(change.output1Mm));
                // Setter publishes native property change after its reference/ID update.
                axis.setInputAxis(input);
                if(!expectedAfter.same(snapshot(machine)))
                    fail("CONFIGURATION_FAULT","A native setter/listener changed staged axis values or references");
            } catch(Exception e) {
                throw new Fault("CONFIGURATION_FAULT","Native setters may have changed the model; fence, retain evidence and never replay this plan",e);
            }
        }
    }
    public static Plan stage(Configuration config, Change change) throws Exception {
        requireSimulator(config,true);
        if(change==null) fail("INVALID_ARGUMENT","Expected immutable mapped-axis change");
        id(change.axisId);id(change.inputAxisId);
        Machine machine=config.getMachine();validateGraph(machine);
        Axis selected=machine.getAxis(change.axisId), source=machine.getAxis(change.inputAxisId);
        if(selected==null || source==null) fail("AXIS_NOT_FOUND","Both axis IDs must already exist");
        if(selected==source) fail("AXIS_CYCLE","Mapped axis cannot depend on itself");
        if(selected.getClass()!=ReferenceMappedAxis.class) fail("UNSUPPORTED_AXIS_TOPOLOGY","Only exact existing ReferenceMappedAxis targets are editable");
        if(source.getClass()!=ReferenceControllerAxis.class) fail("UNSUPPORTED_AXIS_TOPOLOGY","Input must be a direct exact native controller axis; chains/coupling/cams/virtual inputs are unsupported");
        ReferenceMappedAxis axis=(ReferenceMappedAxis)selected;
        ReferenceControllerAxis input=(ReferenceControllerAxis)source;
        validateMapping(axis,input,change);
        Snapshot baseline=snapshot(machine);
        double scale=(change.output1Mm-change.output0Mm)/(change.input1Mm-change.input0Mm);
        double offset=change.output0Mm-scale*change.input0Mm;
        double low=length(input.getSoftLimitLow()),high=length(input.getSoftLimitHigh());
        double a=scale*low+offset,b=scale*high+offset;
        Map<String,Object> m=map("type",TYPE,"axis_id",axis.getId(),"axis_type",axis.getType().name(),
            "native_class",axis.getClass().getName(),"input_axis_id",input.getId(),
            "input_0_mm",change.input0Mm,"output_0_mm",change.output0Mm,
            "input_1_mm",change.input1Mm,"output_1_mm",change.output1Mm,
            "derived_scale",scale,"derived_offset_mm",offset,"direction",scale<0?"reversed":"forward",
            "mapped_soft_limit_low_mm",Math.min(a,b),"mapped_soft_limit_high_mm",Math.max(a,b),
            "requires_validation",true,"physical_calibration_valid",false,
            "caller_must_invalidate",Collections.unmodifiableList(Arrays.asList("homing","motion-envelope","nozzle-runout","vision-registration","fiducial-registration","job-validation")),
            "scope","model-only-existing-direct-mapped-linear-axis-native-simulator");
        return new Plan(config,axis,input,change,baseline,m);
    }
    /** Strict typed NativeSettings seam; no arbitrary native property writes. */
    public static Plan stage(Configuration config,JsonObject json)throws Exception {
        Set<String> fields=new HashSet<>(Arrays.asList("type","axis_id","input_axis_id","input_0_mm","output_0_mm","input_1_mm","output_1_mm"));
        if(json==null || json.entrySet().size()!=fields.size())fail("INVALID_ARGUMENT","Expected exactly seven typed mapped-axis fields");
        for(Map.Entry<String,JsonElement> entry:json.entrySet())if(!fields.contains(entry.getKey()))fail("UNKNOWN_FIELD","Unsupported mapped-axis field "+entry.getKey());
        if(!TYPE.equals(text(json,"type")))fail("INVALID_ARGUMENT","Unexpected mapped-axis change type");
        return stage(config,new Change(text(json,"axis_id"),text(json,"input_axis_id"),number(json,"input_0_mm"),number(json,"output_0_mm"),number(json,"input_1_mm"),number(json,"output_1_mm")));
    }
    /** Read-only native field copy. It does not save, create an axis or populate a native transform. */
    public static Map<String,Object> describe(Configuration config,String axisId) throws Exception {
        requireSimulator(config,false);id(axisId);validateGraph(config.getMachine());
        Axis candidate=config.getMachine().getAxis(axisId);
        if(candidate==null) fail("AXIS_NOT_FOUND","Unknown axis ID");
        if(candidate.getClass()!=ReferenceMappedAxis.class) fail("UNSUPPORTED_AXIS_TOPOLOGY","Expected exact ReferenceMappedAxis");
        ReferenceMappedAxis a=(ReferenceMappedAxis)candidate;
        return Collections.unmodifiableMap(map("axis_id",a.getId(),"axis_type",a.getType()==null?null:a.getType().name(),
            "native_class",a.getClass().getName(),"input_axis_id",a.getInputAxis()==null?null:a.getInputAxis().getId(),
            "input_0_mm",length(a.getMapInput0()),"output_0_mm",length(a.getMapOutput0()),
            "input_1_mm",length(a.getMapInput1()),"output_1_mm",length(a.getMapOutput1()),
            "physical_calibration_valid",false));
    }
    private static void requireSimulator(Configuration config,boolean editing) throws Exception {
        if(config==null || Configuration.get()!=config || config.getMachine()==null || config.getMachine().getClass()!=ReferenceMachine.class)
            fail("UNSUPPORTED_SIMULATOR","Expected the current exact native ReferenceMachine configuration");
        Machine m=config.getMachine();
        if(m.getDrivers().size()!=1 || m.getDrivers().get(0).getClass()!=NullDriver.class)
            fail("UNSUPPORTED_SIMULATOR","Only one exact native NullDriver is admitted; no controller access");
        if(editing) {
            for(Camera camera:m.getAllCameras())if(camera instanceof org.openpnp.machine.reference.camera.ReferenceCamera){
                org.openpnp.machine.reference.camera.ReferenceCamera c=(org.openpnp.machine.reference.camera.ReferenceCamera)camera;
                if(c.isEnableUnitsPerPixel3D()||c.getAdvancedCalibration().isEnabled())fail("UNSUPPORTED_CAMERA_GEOMETRY","Mapped geometry requires basic2D cameras; advanced/3D recalibration is outside this profile");
            }
            if(m.isBusy()&&!m.isTask(Thread.currentThread()))fail("MACHINE_NOT_QUIESCENT","Staging requires the caller's serialized idle native state");
            if(m.isEnabled()) fail("MACHINE_NOT_QUIESCENT","Native simulator must be disabled; caller must exclude active jobs/unknown outcomes");
            for(Head h:m.getHeads()) for(Nozzle n:h.getNozzles())
                if(n.getPart()!=null) fail("HELD_PART","A nozzle's native held-part state blocks axis edits");
        }
    }
    /** Validate before recursive conversion. Unedited native virtual camera axes may coexist. */
    private static void validateGraph(Machine m) throws Exception {
        List<Axis> axes=m.getAxes();
        if(axes.size()<1 || axes.size()>16) fail("AXIS_LIMIT","Expected 1..16 existing native axes");
        Set<Axis> objects=Collections.newSetFromMap(new IdentityHashMap<>());
        Set<String> ids=new HashSet<>();
        for(Axis a:axes) {
            id(a.getId());
            if(!objects.add(a) || !ids.add(a.getId().toLowerCase(Locale.ROOT)) || m.getAxis(a.getId())!=a)
                fail("AXIS_ID_CONFLICT","Axis IDs and installed object identities must be unique");
            if(a.getClass()!=ReferenceControllerAxis.class && a.getClass()!=ReferenceMappedAxis.class && a.getClass()!=ReferenceVirtualAxis.class)
                fail("UNSUPPORTED_AXIS_TOPOLOGY","The graph admits exact controller/mapped axes and unchanged virtual axes only");
        }
        for(Axis a:axes) if(a.getClass()==ReferenceMappedAxis.class) {
            Set<Axis> visited=Collections.newSetFromMap(new IdentityHashMap<>());
            Axis cursor=a;
            while(cursor!=null && cursor.getClass()==ReferenceMappedAxis.class) {
                if(!visited.add(cursor)) fail("AXIS_CYCLE","Existing native mapped-axis graph has a cycle");
                cursor=((ReferenceMappedAxis)cursor).getInputAxis();
                if(cursor!=null && !objects.contains(cursor)) fail("INVALID_AXIS_REFERENCE","Native input reference is detached from the machine");
            }
            AbstractAxis input=((ReferenceMappedAxis)a).getInputAxis();
            if(input!=null && input.getClass()!=ReferenceControllerAxis.class)
                fail("UNSUPPORTED_AXIS_TOPOLOGY","Existing transformed-axis chains are outside this direct-input profile");
        }
    }
    private static void validateMapping(ReferenceMappedAxis axis,ReferenceControllerAxis input,Change c) throws Exception {
        if(axis.getType()==null || axis.getType()==Axis.Type.Rotation || input.getType()!=axis.getType() || input.isInvertLinearRotational())
            fail("AXIS_TYPE_MISMATCH","Only matching X/Y/Z linear input and output types are supported");
        if(input.getDriver()!=Configuration.get().getMachine().getDrivers().get(0))
            fail("INVALID_AXIS_REFERENCE","Source controller axis must reference the installed NullDriver");
        for(double value:new double[]{c.input0Mm,c.output0Mm,c.input1Mm,c.output1Mm}) bound(value,MAX_MM,"mapping endpoint");
        double dx=c.input1Mm-c.input0Mm,dy=c.output1Mm-c.output0Mm;
        if(Math.abs(dx)<MIN_SPAN_MM || Math.abs(dy)<MIN_SPAN_MM)
            fail("SINGULAR_AXIS_MAPPING","Both point spans must be at least0.001mm; native fallback-to-one scale is forbidden");
        double scale=dy/dx;
        if(!Double.isFinite(scale) || Math.abs(scale)<MIN_SCALE || Math.abs(scale)>MAX_SCALE)
            fail("AXIS_SCALE_LIMIT","Absolute scale must be0.001..1000");
        double offset=c.output0Mm-scale*c.input0Mm;bound(offset,MAX_MM,"mapping offset");
        if(!input.isSoftLimitLowEnabled() || !input.isSoftLimitHighEnabled())
            fail("AXIS_LIMITS_REQUIRED","Both source native soft limits must already be enabled");
        double low=length(input.getSoftLimitLow()),high=length(input.getSoftLimitHigh());
        bound(low,MAX_MM,"source low");bound(high,MAX_MM,"source high");
        if(low>=high) fail("AXIS_LIMITS_REQUIRED","Source native soft limits must have low<high");
        if(c.input0Mm<low || c.input0Mm>high || c.input1Mm<low || c.input1Mm>high)
            fail("AXIS_ENDPOINT_OUTSIDE_LIMITS","Input calibration points must lie inside the configured source envelope");
        bound(scale*low+offset,MAX_MM,"mapped low endpoint");bound(scale*high+offset,MAX_MM,"mapped high endpoint");
    }
    /** Immutable primitive values plus exact native object identities; no reflection/serialization. */
    private static Snapshot snapshot(Machine m) throws Exception {
        return snapshot(m,null,null,null);
    }
    private static Snapshot snapshot(Machine m,ReferenceMappedAxis target,ReferenceControllerAxis source,Change change) throws Exception {
        List<Object> identities=new ArrayList<>();List<Object> values=new ArrayList<>();
        identities.add(m);identities.addAll(m.getDrivers());
        if(m.getHeads().size()>8)fail("AXIS_LIMIT","This profile admits at most8heads and64axis consumers");
        Set<HeadMountable> seen=Collections.newSetFromMap(new IdentityHashMap<>());
        List<HeadMountable> consumers=new ArrayList<>();
        for(Head head:m.getHeads()) {identities.add(head);for(HeadMountable item:head.getHeadMountables())if(seen.add(item))consumers.add(item);}
        for(Camera camera:m.getCameras())if(seen.add(camera))consumers.add(camera);
        for(Actuator actuator:m.getActuators())if(seen.add(actuator))consumers.add(actuator);
        if(consumers.size()>64)fail("AXIS_LIMIT","This profile admits at most64axis consumers");
        for(HeadMountable item:consumers) {
            if(!(item instanceof AbstractHeadMountable))fail("UNSUPPORTED_AXIS_TOPOLOGY","Unsupported axis consumer implementation");
            AbstractHeadMountable consumer=(AbstractHeadMountable)item;
            identities.add(item);identities.add(item.getHead());values.addAll(Arrays.asList(item.getId(),item.getClass()));
            for(Axis.Type type:Axis.Type.values()) {
                AbstractAxis axis=consumer.getAxis(type);identities.add(axis);
                if(axis!=null && (m.getAxis(axis.getId())!=axis || axis.getType()!=type))
                    fail("INVALID_AXIS_REFERENCE","Native consumer axis must be an installed same-type object");
            }
        }
        for(Axis a:m.getAxes()) {
            identities.add(a);values.addAll(Arrays.asList(a.getId(),a.getName(),a.getType(),a.getClass()));
            if(a.getClass()==ReferenceMappedAxis.class) {
                ReferenceMappedAxis x=(ReferenceMappedAxis)a;identities.add(x==target?source:x.getInputAxis());
                if(x==target)values.addAll(Arrays.asList(change.input0Mm,change.output0Mm,change.input1Mm,change.output1Mm));
                else values.addAll(Arrays.asList(length(x.getMapInput0()),length(x.getMapOutput0()),length(x.getMapInput1()),length(x.getMapOutput1())));
            } else if(a.getClass()==ReferenceControllerAxis.class) {
                ReferenceControllerAxis x=(ReferenceControllerAxis)a;identities.add(x.getDriver());
                values.addAll(Arrays.asList(x.isInvertLinearRotational(),x.isSoftLimitLowEnabled(),x.isSoftLimitHighEnabled(),
                    length(x.getSoftLimitLow()),length(x.getSoftLimitHigh()),length(x.getFeedratePerSecond()),
                    length(x.getAccelerationPerSecond2()),length(x.getJerkPerSecond3())));
            } else if(a.getClass()==ReferenceVirtualAxis.class) {
                values.add(length(((ReferenceVirtualAxis)a).getHomeCoordinate()));
            }
        }
        return new Snapshot(identities,values);
    }
    private static final class Snapshot {
        final List<Object> identities,values;
        Snapshot(List<Object> i,List<Object> v) {identities=i;values=v;}
        boolean same(Snapshot other) {
            if(identities.size()!=other.identities.size() || !values.equals(other.values))return false;
            for(int i=0;i<identities.size();i++)if(identities.get(i)!=other.identities.get(i))return false;
            return true;
        }
    }
    private static Length mm(double v) { return new Length(v,LengthUnit.Millimeters); }
    private static String text(JsonObject json,String key)throws Exception {
        JsonElement e=json.get(key);if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isString())fail("INVALID_ARGUMENT","Expected string "+key);
        return e.getAsString();
    }
    private static double number(JsonObject json,String key)throws Exception {
        JsonElement e=json.get(key);if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isNumber())fail("INVALID_ARGUMENT","Expected number "+key);
        return e.getAsDouble();
    }
    private static double length(Length v) throws Exception {
        if(v==null || v.getUnits()==null) {fail("INVALID_AXIS_GEOMETRY","Missing native length/units");return 0;}
        double value=v.convertToUnits(LengthUnit.Millimeters).getValue();
        if(!Double.isFinite(value))fail("INVALID_AXIS_GEOMETRY","Nonfinite native geometry");
        return value;
    }
    private static void bound(double v,double max,String label)throws Exception {
        if(!Double.isFinite(v)||Math.abs(v)>max)fail("AXIS_GEOMETRY_LIMIT",label+" must be finite and inside +/-"+max+"mm");
    }
    private static void id(String id)throws Exception {if(id==null||id.isEmpty()||id.length()>128)fail("INVALID_ARGUMENT","Axis ID must be1..128characters");}
    private static void fail(String code,String message)throws Fault {throw new Fault(code,message);}
    private static Map<String,Object> map(Object...v) {Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<v.length;i+=2)m.put((String)v[i],v[i+1]);return m;}
}
