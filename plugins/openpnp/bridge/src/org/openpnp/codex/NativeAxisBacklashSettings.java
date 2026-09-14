/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.AbstractHeadMountable;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.*;
import org.openpnp.machine.reference.camera.ReferenceCamera;
import org.openpnp.machine.reference.driver.*;

/** Typed model configuration, never backlash measurement. Caller owns revision, journal and save. */
public final class NativeAxisBacklashSettings {
    public static final String TYPE="set_axis_backlash_settings";
    private static final Set<String> FIELDS=new HashSet<>(Arrays.asList("type","axis_id","method","offset_mm","speed_factor","sneak_up_mm","acceptable_tolerance_mm"));
    private NativeAxisBacklashSettings() { }
    public static final class Plan {
        private final Configuration config; private final Machine machine;
        private final ReferenceControllerAxis axis; private final List<Object> identity;
        private final List<Object> baseline;
        private final ReferenceControllerAxis.BacklashCompensationMethod method;
        private final double offset,speed,sneak,tolerance; private boolean used;
        private Plan(Configuration c,ReferenceControllerAxis a,ReferenceControllerAxis.BacklashCompensationMethod m,double o,double s,double u,double t)throws Exception {
            config=c;machine=c.getMachine();axis=a;method=m;offset=o;speed=s;sneak=u;tolerance=t;identity=identity(c,a);baseline=state(a);
        }
        public void validateCurrentState()throws Exception {
            if(used)fail("PATCH_ALREADY_APPLIED","Backlash plan cannot be replayed");
            if(Configuration.get()!=config||config.getMachine()!=machine)fail("STALE_AXIS_PLAN","Configuration identity changed");
            List<Object> current=identity(config,axis);
            if(!identity.equals(current)||!baseline.equals(state(axis)))fail("STALE_AXIS_PLAN","Axis settings, references or native consumers changed after staging");
            if(machine.isEnabled()||(machine.isBusy()&&!machine.isTask(Thread.currentThread())))fail("MACHINE_NOT_QUIESCENT","Apply backlash settings with the native simulator disabled and idle");
            for(Head head:machine.getHeads())for(Nozzle n:head.getNozzles())if(n.getPart()!=null)fail("HELD_PART","A native held part blocks backlash configuration");
            for(Camera c:machine.getAllCameras())if(c instanceof ReferenceCamera){ReferenceCamera r=(ReferenceCamera)c;if(r.isEnableUnitsPerPixel3D()||r.getAdvancedCalibration().isEnabled())fail("UNSUPPORTED_CAMERA_GEOMETRY","Backlash edits require basic 2D camera geometry");}
        }
        public void apply()throws Exception {
            if(used)fail("PATCH_ALREADY_APPLIED","Backlash plan cannot be replayed");
            if(!machine.isTask(Thread.currentThread()))fail("NATIVE_EXECUTOR_REQUIRED","Backlash configuration requires the native executor");
            validateCurrentState();used=true;machine.setHomed(false);ReferenceNozzleTipCalibration.resetAllNozzleTips();
            for(Camera c:machine.getAllCameras())if(c instanceof ReferenceCamera)((ReferenceCamera)c).getAdvancedCalibration().setValid(false);
            axis.setBacklashCompensationMethod(method);axis.setBacklashOffset(mm(offset));axis.setBacklashSpeedFactor(speed);axis.setSneakUpOffset(mm(sneak));axis.setAcceptableTolerance(mm(tolerance));
            if(!state(axis).equals(Arrays.asList(method,offset,speed,sneak,tolerance)))fail("CONFIGURATION_FAULT","Native setter/listener changed the staged backlash values; never replay");
        }
        public Map<String,Object> metadata(){Map<String,Object> out=behavior(axis,method,offset,speed,sneak,tolerance);out.put("type",TYPE);out.put("requires_disabled",true);out.put("requires_validation",true);out.put("caller_must_invalidate",Arrays.asList("homing","nozzle-runout","vision-registration","fiducial-registration","job-validation"));return out;}
    }
    public static Plan stage(Configuration config,JsonObject c)throws Exception {
        for(Map.Entry<String,JsonElement> e:c.entrySet())if(!FIELDS.contains(e.getKey()))fail("UNKNOWN_FIELD","Unsupported backlash field "+e.getKey());
        if(c.entrySet().size()!=FIELDS.size()||!TYPE.equals(text(c,"type")))fail("INVALID_ARGUMENT","Expected every typed backlash field");
        String id=text(c,"axis_id");if(!id.matches("[A-Za-z0-9_.:+-]{1,128}"))fail("INVALID_ARGUMENT","Invalid axis identifier");
        Axis found=config.getMachine().getAxis(id);if(found==null)fail("AXIS_NOT_FOUND","Axis must already exist");
        if(found.getClass()!=ReferenceControllerAxis.class)fail("UNSUPPORTED_AXIS_TOPOLOGY","Target must be an exact ReferenceControllerAxis");
        ReferenceControllerAxis a=(ReferenceControllerAxis)found;identity(config,a);
        ReferenceControllerAxis.BacklashCompensationMethod m;
        try{m=ReferenceControllerAxis.BacklashCompensationMethod.valueOf(text(c,"method"));}catch(IllegalArgumentException e){fail("INVALID_ARGUMENT","Unknown native backlash method");return null;}
        double offset=number(c,"offset_mm",-10,10),speed=number(c,"speed_factor",0.001,1),sneak=number(c,"sneak_up_mm",0,10),tolerance=number(c,"acceptable_tolerance_mm",0.000001,1);
        if(m!=ReferenceControllerAxis.BacklashCompensationMethod.None&&offset==0)fail("INVALID_GEOMETRY","An enabled compensation method needs a nonzero signed offset");
        if(m==ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp&&sneak<=0)fail("INVALID_GEOMETRY","DirectionalSneakUp needs a positive sneak-up distance");
        return new Plan(config,a,m,offset,speed,sneak,tolerance);
    }
    public static Map<String,Object> describe(Configuration c,ReferenceControllerAxis a)throws Exception {identity(c,a);return behavior(a,a.getBacklashCompensationMethod(),value(a.getBacklashOffset()),a.getBacklashSpeedFactor(),value(a.getSneakUpOffset()),value(a.getAcceptableTolerance()));}
    public static List<Map<String,Object>> motionBehavior(Configuration c){
        List<Map<String,Object>> result=new ArrayList<>();
        for(Axis a:c.getMachine().getAxes())if(a.getClass()==ReferenceControllerAxis.class){
            ReferenceControllerAxis r=(ReferenceControllerAxis)a;
            if(r.getBacklashCompensationMethod()!=ReferenceControllerAxis.BacklashCompensationMethod.None&&value(r.getBacklashOffset())!=0){
                try{
                    Map<String,Object> row=describe(c,r);
                    if(r.getBacklashCompensationMethod()==null||!Double.isFinite(value(r.getBacklashOffset()))||!Double.isFinite(value(r.getSneakUpOffset())))fail("INVALID_NATIVE_BACKLASH","Cannot bound nonfinite or incomplete native settings");
                    double offset=value(r.getBacklashOffset()),sneak=value(r.getSneakUpOffset()),speed=r.getBacklashSpeedFactor(),tolerance=value(r.getAcceptableTolerance());
                    if(Math.abs(offset)>10||sneak<0||sneak>10||!Double.isFinite(speed)||speed<0.001||speed>1||!Double.isFinite(tolerance)||tolerance<0.000001||tolerance>1||(r.getBacklashCompensationMethod()==ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp&&sneak==0))fail("INVALID_NATIVE_BACKLASH","Existing compensation values are outside the qualified software profile");
                    row.put("distance_bound_available",true);result.add(row);
                }catch(Exception e){result.add(Bridge.map("axis_id",r.getId(),"axis_type",r.getType()==null?null:r.getType().name(),"native_class",r.getClass().getName(),
                    "method",r.getBacklashCompensationMethod()==null?null:r.getBacklashCompensationMethod().name(),"backlash_settings_editable",false,"distance_bound_available",false,
                    "preview_fault",e instanceof Bridge.Fault?((Bridge.Fault)e).code:"UNSUPPORTED_NATIVE_BACKLASH","physical_calibration_valid",false,"measurement_performed",false,
                    "soft_limits_bound","logical targets before compensation; controller-segment distances and units are unqualified for this native state"));}
            }
        }return result;
    }
    private static Map<String,Object> behavior(ReferenceControllerAxis a,ReferenceControllerAxis.BacklashCompensationMethod m,double offset,double speed,double sneak,double tolerance){
        // Native limits run BEFORE compensation. This conservative excursion is relative to the
        // logical segment including its starting point, not a claim of bounded physical travel.
        boolean active=m!=null&&m!=ReferenceControllerAxis.BacklashCompensationMethod.None&&offset!=0;
        double excursion=active?Math.abs(offset)+(m==ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp?Math.abs(sneak):0):0;
        return Bridge.map("axis_id",a.getId(),"method",m==null?null:m.name(),"offset_mm",finite(offset),"speed_factor",finite(speed),"sneak_up_mm",finite(sneak),"acceptable_tolerance_mm",finite(tolerance),"backlash_settings_editable",true,
            "maximum_extra_distance_from_logical_segment_mm",finite(excursion),"soft_limits_bound","logical targets before backlash compensation; controller segments may exceed them",
            "planner_behavior",m==null?"unsupported":m.isDirectionalMethod()?"offset added only for matching travel direction; controller endpoint stays offset":m.isOneSidedPositioningMethod()?"offset overshoot followed by return; optimized method skips extra move in the opposite direction":"offset ignored",
            "slow_segment_speed","min(requested speed, speed_factor), not multiplication; only speed-controlled methods",
            "physical_calibration_valid",false,"measurement_performed",false);
    }
    private static List<Object> identity(Configuration c,ReferenceControllerAxis target)throws Exception {
        Machine m=c.getMachine();if(Configuration.get()!=c||m==null||m.getClass()!=ReferenceMachine.class||m.getDrivers().size()!=1||m.getDrivers().get(0).getClass()!=NullDriver.class)fail("UNSUPPORTED_SIMULATOR","Only current exact ReferenceMachine with one exact NullDriver is supported");
        if(m.getMotionPlanner().getClass()!=NullMotionPlanner.class&&m.getMotionPlanner().getClass()!=ReferenceAdvancedMotionPlanner.class)fail("UNSUPPORTED_SIMULATOR","Unqualified motion planner class");
        if(target.getType()==null||target.getType()==Axis.Type.Rotation||target.isInvertLinearRotational())fail("AXIS_TYPE_MISMATCH","Only X/Y/Z linear controller units are supported");
        if(m.getAxis(target.getId())!=target||target.getDriver()!=m.getDrivers().get(0))fail("INVALID_AXIS_REFERENCE","Target/driver must be the currently installed native object");
        if(m.getAxes().size()>16)fail("AXIS_LIMIT","At most 16 axes are admitted");
        List<Object> identity=new ArrayList<>();identity.add(m);identity.add(m.getDrivers().get(0));identity.add(m.getMotionPlanner());Set<String> ids=new HashSet<>();
        for(Axis a:m.getAxes()){
            if(a.getId()==null||!ids.add(a.getId().toLowerCase(Locale.ROOT))||m.getAxis(a.getId())!=a)fail("AXIS_ID_CONFLICT","Axis identifiers must be unique");
            if(a.getClass()!=ReferenceControllerAxis.class&&a.getClass()!=ReferenceVirtualAxis.class&&a.getClass()!=ReferenceMappedAxis.class)fail("UNSUPPORTED_AXIS_TOPOLOGY","Only controller, virtual and direct mapped axes are admitted");
            identity.add(a);identity.add(a.getId());identity.add(a.getType());
            if(a.getClass()==ReferenceMappedAxis.class){ReferenceMappedAxis map=(ReferenceMappedAxis)a;Axis input=map.getInputAxis();if(input==target)fail("MAPPED_SOURCE_BACKLASH_UNSUPPORTED","Mapped-source backlash requires separate transformed-segment qualification");if(input==null||input.getClass()!=ReferenceControllerAxis.class||m.getAxis(input.getId())!=input)fail("INVALID_AXIS_REFERENCE","Mapped axis must have an installed direct controller source");identity.add(input);}
        }
        Set<HeadMountable> consumers=Collections.newSetFromMap(new IdentityHashMap<>());
        if(m.getHeads().size()>8)fail("AXIS_LIMIT","At most 8 heads are admitted");
        for(Head h:m.getHeads()){identity.add(h);consumers.addAll(h.getHeadMountables());}consumers.addAll(m.getCameras());consumers.addAll(m.getActuators());
        if(consumers.size()>64)fail("AXIS_LIMIT","At most 64 axis consumers are admitted");
        // Stable native order is retained separately from identity-set membership.
        List<HeadMountable> ordered=new ArrayList<>(consumers);for(HeadMountable hm:ordered)if(hm.getId()==null)fail("INVALID_AXIS_REFERENCE","Native consumer identifier is missing");ordered.sort(Comparator.comparing(HeadMountable::getId));
        for(HeadMountable hm:ordered){if(!(hm instanceof AbstractHeadMountable))fail("UNSUPPORTED_AXIS_TOPOLOGY","Unknown axis consumer");identity.add(hm);identity.add(hm.getHead());for(Axis.Type type:Axis.Type.values()){Axis a=((AbstractHeadMountable)hm).getAxis(type);if(a!=null&&(m.getAxis(a.getId())!=a||a.getType()!=type))fail("INVALID_AXIS_REFERENCE","Consumers require installed same-type axes");identity.add(a);}}
        return identity;
    }
    private static List<Object> state(ReferenceControllerAxis a){return Arrays.asList(a.getBacklashCompensationMethod(),value(a.getBacklashOffset()),a.getBacklashSpeedFactor(),value(a.getSneakUpOffset()),value(a.getAcceptableTolerance()));}
    private static Double finite(double n){return Double.isFinite(n)?n:null;}
    private static Length mm(double x){return new Length(x,LengthUnit.Millimeters);}
    private static double value(Length x){return x==null?Double.NaN:x.convertToUnits(LengthUnit.Millimeters).getValue();}
    private static String text(JsonObject c,String key)throws Exception {JsonElement x=c.get(key);if(x==null||!x.isJsonPrimitive()||!x.getAsJsonPrimitive().isString())fail("INVALID_ARGUMENT","Expected string "+key);return x.getAsString();}
    private static double number(JsonObject c,String key,double low,double high)throws Exception {JsonElement x=c.get(key);if(x==null||!x.isJsonPrimitive()||!x.getAsJsonPrimitive().isNumber())fail("INVALID_ARGUMENT","Expected number "+key);double n=x.getAsDouble();if(!Double.isFinite(n)||n<low||n>high)fail("OUT_OF_RANGE","Invalid bounded "+key);return n;}
    private static void fail(String code,String message)throws Bridge.Fault{throw new Bridge.Fault(code,message);}
}
