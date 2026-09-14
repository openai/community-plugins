/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.camera.ReferenceCamera;

/** One complete bounded simulator assembly. Staging creates no native objects/listeners.
 * Caller owns executor, durable portable preimage, intent, persistence and fault fencing.
 * A partial apply MUST be recovered in a fresh generation; removing objects is not rollback.
 */
public final class NativeNozzleAssembly {
    public static final String TYPE="create_simulator_nozzle_assembly";
    private static final Gson G=new Gson();
    private NativeNozzleAssembly(){}
    @FunctionalInterface interface Probe { void after(String stage)throws Exception; }
    static final Probe NONE=stage->{};
    public static final class Plan {
        final Configuration c; final Machine m; final ReferenceHead head; final NullDriver driver;
        final ReferenceControllerAxis x,y; final JsonObject change; final List<org.openpnp.model.Package> packages;
        final Snapshot baseline; final Probe probe; final String zLetter,rLetter; boolean used;
        Map<String,Object> result=Collections.emptyMap();
        Plan(Configuration c,JsonObject j,ReferenceHead h,NullDriver d,ReferenceControllerAxis x,ReferenceControllerAxis y,List<org.openpnp.model.Package> p,Probe probe)throws Exception{
            this.c=c;m=c.getMachine();head=h;driver=d;this.x=x;this.y=y;change=j;packages=List.copyOf(p);this.probe=probe;
            List<String> letters=new ArrayList<>();for(char letter: "UVWABCDERST".toCharArray()) {String candidate=String.valueOf(letter);if(m.getAxes().stream().noneMatch(a->a instanceof AbstractControllerAxis&&candidate.equals(((AbstractControllerAxis)a).getLetter())))letters.add(candidate);}
            if(letters.size()<2)fail("AXIS_LETTERS_EXHAUSTED","Two unused simulator axis letters are required");zLetter=letters.get(0);rLetter=letters.get(1);baseline=snapshot(c);
        }
        public Map<String,Object> metadata(){return Bridge.map("type",TYPE,"head_id",head.getId(),"driver_id",driver.getId(),"x_axis_id",x.getId(),"y_axis_id",y.getId(),"proposal",G.fromJson(change,Map.class),"created_native_ids","allocated only during apply","scope","complete-isolated-NullDriver-nozzle-assembly","initial_tip_state","simulated-installed","measurement_performed",false,"physical_qualification",false,"recovery","fresh generation from durable pre-creation portable artifact","caller_must_invalidate",List.of("homing","runout-calibration","camera-calibration","board-registration","job-validation","plans"));}
        public Map<String,Object> result(){return result;}
        public synchronized void validateCurrentState()throws Exception{
            if(used)fail("PATCH_ALREADY_APPLIED","Assembly plan is single use");require(c);
            if(!baseline.same(snapshot(c)))fail("STALE_TOPOLOGY_PLAN","Native identities, references or settings changed after staging");
        }
        public synchronized void apply()throws Exception{
            if(!m.isTask(Thread.currentThread()))fail("NATIVE_EXECUTOR_REQUIRED","Assembly creation requires the native machine executor");
            validateCurrentState();used=true;probe.after("before-insertion");
            // Constructor listeners are allocated only after durable caller intent. Set offsets
            // before head attachment so ReferenceNozzle does not adjust existing camera frames.
            ReferenceControllerAxis z=axis(Axis.Type.Z,zLetter,txt(change,"nozzle_name")+" Z",obj(change,"z_axis"),false);
            ReferenceControllerAxis r=axis(Axis.Type.Rotation,rLetter,txt(change,"nozzle_name")+" Rotation",obj(change,"rotation_axis"),true);
            ReferenceNozzleTip tip=new ReferenceNozzleTip();tip.getCalibration().commit();tip.setName(txt(change,"tip_name"));JsonObject t=obj(change,"tip");
            tip.setMinPartDiameter(mm(num(t,"min_part_diameter_mm")));tip.setMaxPartDiameter(mm(num(t,"max_part_diameter_mm")));tip.setMaxPartHeight(mm(num(t,"max_part_height_mm")));tip.setMaxPickTolerance(mm(num(t,"max_pick_tolerance_mm")));
            tip.setPickDwellMilliseconds((int)num(t,"pick_dwell_ms"));tip.setPlaceDwellMilliseconds((int)num(t,"place_dwell_ms"));
            tip.setMethodPartOn(ReferenceNozzleTip.VacuumMeasurementMethod.None);tip.setMethodPartOff(ReferenceNozzleTip.VacuumMeasurementMethod.None);tip.getCalibration().setEnabled(false);
            ReferenceActuator valve=new ReferenceActuator();valve.setName(txt(change,"valve_name"));valve.setDriver(driver);valve.setValueType(Actuator.ActuatorValueType.Boolean);
            valve.setEnabledActuation(ReferenceActuator.MachineStateActuation.AssumeUnknown);valve.setHomedActuation(ReferenceActuator.MachineStateActuation.LeaveAsIs);valve.setDisabledActuation(ReferenceActuator.MachineStateActuation.LeaveAsIs);
            ReferenceNozzle nozzle=new ReferenceNozzle();nozzle.setName(txt(change,"nozzle_name"));JsonObject offsets=obj(change,"head_offsets");nozzle.setHeadOffsets(new Location(LengthUnit.Millimeters,num(offsets,"x_mm"),num(offsets,"y_mm"),num(offsets,"z_mm"),0));
            nozzle.setPickDwellMilliseconds((int)num(change,"pick_dwell_ms"));nozzle.setPlaceDwellMilliseconds((int)num(change,"place_dwell_ms"));nozzle.setChangerEnabled(false);nozzle.setEnableDynamicSafeZ(false);nozzle.setRotationMode(Nozzle.RotationMode.AbsolutePartAngle);
            nozzle.setAxisX(x);nozzle.setAxisY(y);nozzle.setAxisZ(z);nozzle.setAxisRotation(r);nozzle.setVacuumActuator(valve);nozzle.setVacuumSenseActuator(valve);nozzle.addCompatibleNozzleTip(tip);
            m.addAxis(z);probe.after("z-inserted");m.addAxis(r);probe.after("rotation-inserted");m.addNozzleTip(tip);probe.after("tip-inserted");head.addActuator(valve);probe.after("valve-inserted");head.addNozzle(nozzle);probe.after("nozzle-inserted");
            // Explicit simulated initial tool state. loadNozzleTip would perform a manual-change
            // motion and throw ManualLoadException; that is deliberately not an initializer.
            nozzle.setNozzleTip(tip);
            // Native public persistence hooks fill IDs/names, then its public loader applies
            // version migration once while the complete graph is already attached.
            java.io.StringWriter nozzleXml=new java.io.StringWriter();Configuration.createSerializer().write(nozzle,nozzleXml);nozzle.applyConfiguration(c);
            for(org.openpnp.model.Package p:packages){for(NozzleTip old:new ArrayList<>(p.getCompatibleNozzleTips()))p.removeCompatibleNozzleTip(old);p.addCompatibleNozzleTip(tip);}
            probe.after("compatibility-assigned");
            result=Collections.unmodifiableMap(Bridge.map("nozzle_id",nozzle.getId(),"nozzle_tip_id",tip.getId(),"vacuum_actuator_id",valve.getId(),"z_axis_id",z.getId(),"rotation_axis_id",r.getId(),"z_axis_letter",zLetter,"rotation_axis_letter",rLetter,"head_id",head.getId(),"driver_id",driver.getId(),"x_axis_id",x.getId(),"y_axis_id",y.getId(),"exclusive_package_ids",packages.stream().map(org.openpnp.model.Package::getId).collect(java.util.stream.Collectors.toList()),"simulated_initial_tool_state","installed-on-new-nozzle","physical_qualification",false));
            verify(c,result,change);baseline.verifyPreserved(c,nozzle,z,r,tip,valve,packages);probe.after("verified");
        }
        ReferenceControllerAxis axis(Axis.Type type,String letter,String name,JsonObject v,boolean rotation)throws Exception{
            ReferenceControllerAxis a=new ReferenceControllerAxis();a.setType(type);
            // A brand-new controller axis starts at legacy version0. Native @Commit would
            // erase custom angular limits on first reload. Normalize this SAME object using
            // the public native serializer before applying any proposed settings.
            java.io.StringWriter xml=new java.io.StringWriter();Configuration.createSerializer().write(a,xml);
            if(Configuration.createSerializer().read(a,xml.toString())!=a)fail("CONFIGURATION_FAULT","Native axis initialization replaced object identity");
            a.setName(name);a.setDriver(driver);a.setLetter(letter);a.setResolution(0.0001);
            String unit=rotation?"deg":"mm";a.setHomeCoordinate(mm(num(v,"home_"+unit)));a.setSoftLimitLow(mm(num(v,"low_"+unit)));a.setSoftLimitHigh(mm(num(v,"high_"+unit)));a.setSoftLimitLowEnabled(true);a.setSoftLimitHighEnabled(true);
            a.setFeedratePerSecond(mm(num(v,"feedrate_"+unit+"_per_s")));a.setAccelerationPerSecond2(mm(num(v,"acceleration_"+unit+"_per_s2")));a.setJerkPerSecond3(mm(num(v,"jerk_"+unit+"_per_s3")));
            a.setInvertLinearRotational(false);a.setBacklashCompensationMethod(ReferenceControllerAxis.BacklashCompensationMethod.None);a.setWrapAroundRotation(false);a.setLimitRotation(rotation);
            if(!rotation){a.setSafeZoneLow(mm(num(v,"safe_z_mm")));a.setSafeZoneHigh(mm(num(v,"safe_z_mm")));a.setSafeZoneLowEnabled(true);a.setSafeZoneHighEnabled(true);}
            return a;
        }
    }
    public static Plan stage(Configuration c,JsonObject input)throws Exception{return stage(c,input,NONE);}
    static Plan stage(Configuration c,JsonObject input,Probe probe)throws Exception{
        require(c);fields(input,"type","head_id","driver_id","x_axis_id","y_axis_id","nozzle_name","tip_name","valve_name","head_offsets","z_axis","rotation_axis","tip","pick_dwell_ms","place_dwell_ms","exclusive_package_ids","simulated_initial_tool_state");
        if(!TYPE.equals(txt(input,"type"))||!"installed-on-new-nozzle".equals(txt(input,"simulated_initial_tool_state")))fail("INVALID_ARGUMENT","Expected explicit simulated assembly initial tool state");
        Machine m=c.getMachine();Head h=m.getHead(txt(input,"head_id"));if(h==null||h!=m.getDefaultHead()||h.getClass()!=ReferenceHead.class)fail("UNSUPPORTED_TOPOLOGY","Assembly requires the existing exact default ReferenceHead");
        if(!m.getDrivers().get(0).getId().equals(txt(input,"driver_id")))fail("INVALID_AXIS_REFERENCE","Use the installed NullDriver ID");
        ReferenceControllerAxis x=direct(m,txt(input,"x_axis_id"),Axis.Type.X),y=direct(m,txt(input,"y_axis_id"),Axis.Type.Y);
        AbstractHeadMountable original=(AbstractHeadMountable)h.getDefaultNozzle();if(original.getAxisX()!=x||original.getAxisY()!=y)fail("INVALID_AXIS_REFERENCE","New nozzle must share the default nozzle's direct X/Y route");
        int nozzles=0,actuators=m.getActuators().size();for(Head head:m.getHeads()){nozzles+=head.getNozzles().size();actuators+=head.getActuators().size();}
        if(nozzles>=4||m.getAxes().size()>14||m.getNozzleTips().size()>=16||actuators>=16)fail("TOPOLOGY_LIMIT","Result exceeds4nozzles/16axes/16tips/16actuators");
        Set<String> names=new HashSet<>();for(Head head:m.getHeads()){for(Nozzle n:head.getNozzles())names.add(n.getName());for(Actuator a:head.getActuators())names.add(a.getName());}for(Actuator a:m.getActuators())names.add(a.getName());for(NozzleTip t:m.getNozzleTips())names.add(t.getName());for(Axis a:m.getAxes())names.add(a.getName());
        for(String key:List.of("nozzle_name","tip_name","valve_name")){String n=txt(input,key);if(n.length()>64||n.trim().length()==0||n.chars().anyMatch(ch->ch<32||ch==127)||!names.add(n))fail("NAME_CONFLICT","New bounded display names must be unique");}
        if(!names.add(txt(input,"nozzle_name")+" Z")||!names.add(txt(input,"nozzle_name")+" Rotation"))fail("NAME_CONFLICT","Generated axis display names conflict");
        JsonObject o=obj(input,"head_offsets");fields(o,"x_mm","y_mm","z_mm");bounded(o,"x_mm",-100,100);bounded(o,"y_mm",-100,100);bounded(o,"z_mm",-100,100);
        validateAxis(obj(input,"z_axis"),false);validateAxis(obj(input,"rotation_axis"),true);
        JsonObject t=obj(input,"tip");fields(t,"min_part_diameter_mm","max_part_diameter_mm","max_part_height_mm","max_pick_tolerance_mm","pick_dwell_ms","place_dwell_ms");bounded(t,"min_part_diameter_mm",0,50);bounded(t,"max_part_diameter_mm",0.001,50);bounded(t,"max_part_height_mm",0.001,50);bounded(t,"max_pick_tolerance_mm",0,10);dwell(t);dwell(input);
        if(num(t,"min_part_diameter_mm")>=num(t,"max_part_diameter_mm"))fail("INVALID_GEOMETRY","Tip minimum diameter must be below maximum");
        if(!input.get("exclusive_package_ids").isJsonArray())fail("INVALID_ARGUMENT","Expected existing package ID array");JsonArray ids=input.getAsJsonArray("exclusive_package_ids");if(ids.size()<1||ids.size()>64)fail("INVALID_ARGUMENT","Expected1..64exclusive packages");Set<String> seen=new HashSet<>();List<org.openpnp.model.Package> p=new ArrayList<>();
        for(JsonElement id:ids){if(!id.isJsonPrimitive()||!id.getAsJsonPrimitive().isString()||!seen.add(id.getAsString().toUpperCase(Locale.ROOT)))fail("INVALID_ARGUMENT","Expected unique existing package IDs");org.openpnp.model.Package pkg=c.getPackage(id.getAsString());if(pkg==null||pkg.getClass()!=org.openpnp.model.Package.class)fail("PACKAGE_NOT_FOUND","Expected exact existing Package");p.add(pkg);}
        return new Plan(c,G.fromJson(input,JsonObject.class),(ReferenceHead)h,(NullDriver)m.getDrivers().get(0),x,y,p,probe);
    }
    static void validateAxis(JsonObject a,boolean r)throws Exception{
        String u=r?"deg":"mm";if(r)fields(a,"home_deg","low_deg","high_deg","feedrate_deg_per_s","acceleration_deg_per_s2","jerk_deg_per_s3");else fields(a,"home_mm","low_mm","high_mm","safe_z_mm","feedrate_mm_per_s","acceleration_mm_per_s2","jerk_mm_per_s3");
        double limit=r?360:100;for(String k:List.of("home_","low_","high_"))bounded(a,k+u,-limit,limit);bounded(a,"feedrate_"+u+"_per_s",0.001,1000);bounded(a,"acceleration_"+u+"_per_s2",0.001,10000);bounded(a,"jerk_"+u+"_per_s3",0,1000000);
        double low=num(a,"low_"+u),high=num(a,"high_"+u),home=num(a,"home_"+u);if(low>=high||home<low||home>high)fail("INVALID_GEOMETRY","Axis home must be inside increasing soft limits");
        if(r){if(low>-180||high<180)fail("INVALID_GEOMETRY","Absolute rotation must admit at least-180..180degrees");}else{bounded(a,"safe_z_mm",low,high);if(num(a,"safe_z_mm")<home)fail("INVALID_GEOMETRY","Safe Z must be at or above home");}
    }
    static void require(Configuration c)throws Exception{
        if(c==null||Configuration.get()!=c||c.getMachine()==null||c.getMachine().getClass()!=ReferenceMachine.class)fail("UNSUPPORTED_SIMULATOR","Expected the current exact ReferenceMachine");Machine m=c.getMachine();
        if(m.getDrivers().size()!=1||m.getDrivers().get(0).getClass()!=NullDriver.class)fail("UNSUPPORTED_SIMULATOR","Only one exact NullDriver is supported");
        if(m.isEnabled()||(m.isBusy()&&!m.isTask(Thread.currentThread())))fail("MACHINE_NOT_QUIESCENT","Assembly creation requires disabled idle native state");
        Set<String> ids=new HashSet<>();Set<Object> identities=Collections.newSetFromMap(new IdentityHashMap<>());
        for(Axis a:m.getAxes()){unique(ids,identities,a.getId(),a);if(a.getClass()!=ReferenceControllerAxis.class&&a.getClass()!=ReferenceVirtualAxis.class)fail("UNSUPPORTED_TOPOLOGY","Creation supports existing direct controller and unchanged virtual camera axes");if(a instanceof ReferenceControllerAxis&&((ReferenceControllerAxis)a).getDriver()!=m.getDrivers().get(0))fail("INVALID_AXIS_REFERENCE","Detached native axis driver");}
        for(Head h:m.getHeads()){unique(ids,identities,h.getId(),h);if(h.getClass()!=ReferenceHead.class)fail("UNSUPPORTED_TOPOLOGY","Exact ReferenceHead required");for(Nozzle n:h.getNozzles()){unique(ids,identities,n.getId(),n);if(n.getClass()!=ReferenceNozzle.class||n.getHead()!=h)fail("UNSUPPORTED_TOPOLOGY","Exact attached ReferenceNozzle required");if(n.getPart()!=null)fail("HELD_PART","Held native parts block topology changes");ReferenceNozzle nozzle=(ReferenceNozzle)n;for(Axis a:Arrays.asList(nozzle.getAxisX(),nozzle.getAxisY(),nozzle.getAxisZ(),nozzle.getAxisRotation()))if(a==null||!m.getAxes().contains(a))fail("INVALID_AXIS_REFERENCE","All existing nozzle axes must be installed");for(NozzleTip t:n.getCompatibleNozzleTips())if(!m.getNozzleTips().contains(t))fail("INVALID_TOOL_REFERENCE","Detached compatible tip");if(n.getNozzleTip()!=null&&!n.getCompatibleNozzleTips().contains(n.getNozzleTip()))fail("INVALID_TOOL_REFERENCE","Loaded tip must be compatible");}
            Set<String> actuatorNames=new HashSet<>();for(Actuator a:h.getActuators()){unique(ids,identities,a.getId(),a);if(a.getClass()!=ReferenceActuator.class||a.getHead()!=h||!actuatorNames.add(a.getName()))fail("INVALID_TOOL_REFERENCE","Head actuator names and references must resolve uniquely");}}
        Set<NozzleTip> loaded=Collections.newSetFromMap(new IdentityHashMap<>());for(Head h:m.getHeads())for(Nozzle n:h.getNozzles())if(n.getNozzleTip()!=null&&!loaded.add(n.getNozzleTip()))fail("INVALID_TOOL_REFERENCE","A loaded tip cannot have two owners");
        for(NozzleTip t:m.getNozzleTips()){unique(ids,identities,t.getId(),t);if(t.getClass()!=ReferenceNozzleTip.class)fail("UNSUPPORTED_TOPOLOGY","Exact native tips required");}
        for(Camera camera:m.getAllCameras())if(camera instanceof ReferenceCamera){ReferenceCamera v=(ReferenceCamera)camera;if(v.isEnableUnitsPerPixel3D()||v.getAdvancedCalibration().isEnabled())fail("UNSUPPORTED_CAMERA_GEOMETRY","Creation requires basic2D camera geometry");}
    }
    static ReferenceControllerAxis direct(Machine m,String id,Axis.Type type)throws Exception{Axis a=m.getAxis(id);if(a==null||a.getClass()!=ReferenceControllerAxis.class||a.getType()!=type||((ReferenceControllerAxis)a).isInvertLinearRotational())fail("INVALID_AXIS_REFERENCE","Expected installed direct matching linear X/Y axis");return (ReferenceControllerAxis)a;}
    static void unique(Set<String> ids,Set<Object> objects,String id,Object o)throws Exception{if(id==null||!ids.add(id.toLowerCase(Locale.ROOT))||!objects.add(o))fail("IDENTITY_CONFLICT","Native IDs and object identities must be unique");}
    static void verify(Configuration c,Map<String,Object> result,JsonObject change)throws Exception{
        require(c);Machine m=c.getMachine();Head h=m.getHead((String)result.get("head_id"));ReferenceNozzle n=(ReferenceNozzle)h.getNozzle((String)result.get("nozzle_id"));NozzleTip t=m.getNozzleTip((String)result.get("nozzle_tip_id"));Actuator v=h.getActuator((String)result.get("vacuum_actuator_id"));
        if(n==null||t==null||v==null||n.getAxisX()!=m.getAxis((String)result.get("x_axis_id"))||n.getAxisY()!=m.getAxis((String)result.get("y_axis_id"))||n.getAxisZ()!=m.getAxis((String)result.get("z_axis_id"))||n.getAxisRotation()!=m.getAxis((String)result.get("rotation_axis_id"))||n.getVacuumActuator()!=v||n.getNozzleTip()!=t||!n.getCompatibleNozzleTips().equals(Set.of(t))||n.isChangerEnabled()||((ReferenceNozzleTip)t).getCalibration().isEnabled())fail("CONFIGURATION_FAULT","Created native references do not match the complete assembly");
        if(!n.getName().equals(txt(change,"nozzle_name"))||!t.getName().equals(txt(change,"tip_name"))||!v.getName().equals(txt(change,"valve_name")))fail("CONFIGURATION_FAULT","Native display name drift");
        ReferenceNozzleTip tip=(ReferenceNozzleTip)t;ReferenceActuator valve=(ReferenceActuator)v;
        if(valve.getEnabledActuation()!=ReferenceActuator.MachineStateActuation.AssumeUnknown||valve.getHomedActuation()!=ReferenceActuator.MachineStateActuation.LeaveAsIs||valve.getDisabledActuation()!=ReferenceActuator.MachineStateActuation.LeaveAsIs||valve.getIndex()!=0)fail("CONFIGURATION_FAULT","Created valve initialization policy drift");
        if(valve.getDriver()!=m.getDriver((String)result.get("driver_id"))||valve.getValueType()!=Actuator.ActuatorValueType.Boolean||n.getVacuumSenseActuator()!=v||n.getBlowOffActuator()!=null||n.isEnableDynamicSafeZ()||n.getRotationMode()!=Nozzle.RotationMode.AbsolutePartAngle||tip.getMethodPartOn()!=ReferenceNozzleTip.VacuumMeasurementMethod.None||tip.getMethodPartOff()!=ReferenceNozzleTip.VacuumMeasurementMethod.None)fail("CONFIGURATION_FAULT","Native tooling mode drift");
        JsonObject offsets=obj(change,"head_offsets");Location location=n.getHeadOffsets().convertToUnits(LengthUnit.Millimeters);eq(location.getX(),num(offsets,"x_mm"));eq(location.getY(),num(offsets,"y_mm"));eq(location.getZ(),num(offsets,"z_mm"));eq(location.getRotation(),0);eq(n.getPickDwellMilliseconds(),num(change,"pick_dwell_ms"));eq(n.getPlaceDwellMilliseconds(),num(change,"place_dwell_ms"));
        JsonObject tv=obj(change,"tip");eq(value(tip.getMinPartDiameter()),num(tv,"min_part_diameter_mm"));eq(value(tip.getMaxPartDiameter()),num(tv,"max_part_diameter_mm"));eq(value(tip.getMaxPartHeight()),num(tv,"max_part_height_mm"));eq(value(tip.getMaxPickTolerance()),num(tv,"max_pick_tolerance_mm"));eq(tip.getPickDwellMilliseconds(),num(tv,"pick_dwell_ms"));eq(tip.getPlaceDwellMilliseconds(),num(tv,"place_dwell_ms"));
        verifyAxis((ReferenceControllerAxis)n.getAxisZ(),obj(change,"z_axis"),false);verifyAxis((ReferenceControllerAxis)n.getAxisRotation(),obj(change,"rotation_axis"),true);
        if(!Objects.equals(((ReferenceControllerAxis)n.getAxisZ()).getLetter(),result.get("z_axis_letter"))||!Objects.equals(((ReferenceControllerAxis)n.getAxisRotation()).getLetter(),result.get("rotation_axis_letter")))fail("CONFIGURATION_FAULT","Created axis letter drift");
        for(Axis a:List.of(n.getAxisZ(),n.getAxisRotation()))if(((ReferenceControllerAxis)a).getDriver()!=m.getDriver((String)result.get("driver_id")))fail("CONFIGURATION_FAULT","Created axis driver drift");
        for(JsonElement id:change.getAsJsonArray("exclusive_package_ids"))if(!c.getPackage(id.getAsString()).getCompatibleNozzleTips().equals(Set.of(t)))fail("CONFIGURATION_FAULT","Exclusive package compatibility was not applied");
    }
    static void verifyAxis(ReferenceControllerAxis a,JsonObject v,boolean rotation)throws Exception{
        eq(a.getResolution(),0.0001);
        String unit=rotation?"deg":"mm";if(a.getType()!=(rotation?Axis.Type.Rotation:Axis.Type.Z)||a.isInvertLinearRotational()||a.getBacklashCompensationMethod()!=ReferenceControllerAxis.BacklashCompensationMethod.None||a.isWrapAroundRotation()||a.isLimitRotation()!=rotation||!a.isSoftLimitLowEnabled()||!a.isSoftLimitHighEnabled())fail("CONFIGURATION_FAULT","Created axis profile drift");
        eq(value(a.getHomeCoordinate()),num(v,"home_"+unit));eq(value(a.getSoftLimitLow()),num(v,"low_"+unit));eq(value(a.getSoftLimitHigh()),num(v,"high_"+unit));eq(value(a.getFeedratePerSecond()),num(v,"feedrate_"+unit+"_per_s"));eq(value(a.getAccelerationPerSecond2()),num(v,"acceleration_"+unit+"_per_s2"));eq(value(a.getJerkPerSecond3()),num(v,"jerk_"+unit+"_per_s3"));
        if(!rotation){if(!a.isSafeZoneLowEnabled()||!a.isSafeZoneHighEnabled())fail("CONFIGURATION_FAULT","Created Z safe zone drift");eq(value(a.getSafeZoneLow()),num(v,"safe_z_mm"));eq(value(a.getSafeZoneHigh()),num(v,"safe_z_mm"));}
    }
    static double value(Length l){return l.convertToUnits(LengthUnit.Millimeters).getValue();}
    static void eq(double actual,double expected)throws Exception{if(!Double.isFinite(actual)||Math.abs(actual-expected)>1e-9)fail("CONFIGURATION_FAULT","Native created setting changed: "+actual+" != "+expected);}
    static final class Snapshot{
        final List<Object> ids;final String values;final JsonObject description;final List<HeadMountable> consumers;final List<Location> offsets;
        Snapshot(List<Object> ids,String v,JsonObject description,List<HeadMountable> consumers,List<Location> offsets){this.ids=ids;values=v;this.description=description;this.consumers=consumers;this.offsets=offsets;}
        boolean same(Snapshot s){if(!values.equals(s.values)||ids.size()!=s.ids.size())return false;for(int i=0;i<ids.size();i++)if(ids.get(i)!=s.ids.get(i))return false;return true;}
        void verifyPreserved(Configuration c,Nozzle n,Axis z,Axis r,NozzleTip t,Actuator v,List<org.openpnp.model.Package> edited)throws Exception{
            Set<Object> excluded=Collections.newSetFromMap(new IdentityHashMap<>());excluded.addAll(Arrays.asList(n,z,r,t,v));
            Map<String,JsonElement> compatible=new HashMap<>();Set<String> editedIds=new HashSet<>();for(org.openpnp.model.Package pkg:edited)editedIds.add(pkg.getId());
            for(JsonElement row:description.getAsJsonArray("packages")){JsonObject pkg=row.getAsJsonObject();if(editedIds.contains(pkg.get("package_id").getAsString()))compatible.put(pkg.get("package_id").getAsString(),pkg.get("nozzle_tip_ids"));}
            if(!same(snapshot(c,excluded,compatible)))fail("CONFIGURATION_FAULT","A native setter changed unrelated settings or original references");
            for(int i=0;i<consumers.size();i++)if(!Objects.equals(offsets.get(i),consumers.get(i).getHeadOffsets()))fail("CONFIGURATION_FAULT","Existing mountable frame changed during nozzle insertion");
            List<Object> after=identity(c);after.removeIf(o->o==n||o==z||o==r||o==t||o==v);if(after.size()!=ids.size())fail("CONFIGURATION_FAULT","Unexpected native object insertion/removal");for(int i=0;i<ids.size();i++)if(ids.get(i)!=after.get(i))fail("CONFIGURATION_FAULT","Existing native object order/identity changed");
        }
    }
    static List<Object> identity(Configuration c){Machine m=c.getMachine();List<Object> ids=new ArrayList<>();ids.add(c);ids.add(m);ids.addAll(m.getDrivers());ids.addAll(m.getAxes());ids.addAll(m.getNozzleTips());ids.addAll(m.getActuators());for(Head h:m.getHeads()){ids.add(h);ids.addAll(h.getNozzles());ids.addAll(h.getActuators());ids.addAll(h.getCameras());}ids.addAll(m.getCameras());ids.addAll(c.getPackages());ids.addAll(c.getParts());return ids;}
    static Snapshot snapshot(Configuration c){return snapshot(c,Collections.emptySet(),Collections.emptyMap());}
    static Snapshot snapshot(Configuration c,Set<Object> excluded,Map<String,JsonElement> packageTips){JsonObject values=G.toJsonTree(NativeSettings.describe(c)).getAsJsonObject();values.remove("machine_homed");for(JsonElement n:values.getAsJsonArray("nozzles"))n.getAsJsonObject().remove("installed_tip_runout_calibrated");for(JsonElement camera:values.getAsJsonArray("cameras"))camera.getAsJsonObject().remove("advanced_calibration_valid");
        Set<String> excludedIds=new HashSet<>();for(Object o:excluded)if(o instanceof org.openpnp.model.Identifiable)excludedIds.add(((org.openpnp.model.Identifiable)o).getId());
        for(String[] pair:new String[][]{{"axes","axis_id"},{"nozzles","nozzle_id"},{"nozzle_tips","nozzle_tip_id"}}){JsonArray keep=new JsonArray();for(JsonElement row:values.getAsJsonArray(pair[0]))if(!excludedIds.contains(row.getAsJsonObject().get(pair[1]).getAsString()))keep.add(row);values.add(pair[0],keep);}
        for(JsonElement row:values.getAsJsonArray("packages")){JsonObject pkg=row.getAsJsonObject();String id=pkg.get("package_id").getAsString();if(packageTips.containsKey(id))pkg.add("nozzle_tip_ids",packageTips.get(id));}
        List<HeadMountable> consumers=new ArrayList<>();for(Head h:c.getMachine().getHeads())consumers.addAll(h.getHeadMountables());consumers.addAll(c.getMachine().getCameras());consumers.addAll(c.getMachine().getActuators());consumers.removeIf(excluded::contains);List<Location> offsets=new ArrayList<>();List<Object> detail=new ArrayList<>();
        for(HeadMountable h:consumers){offsets.add(h.getHeadOffsets());detail.add(h.getName());detail.add(h.getHeadOffsets());if(h instanceof AbstractHeadMountable){AbstractHeadMountable a=(AbstractHeadMountable)h;for(Axis axis:Arrays.asList(a.getAxisX(),a.getAxisY(),a.getAxisZ(),a.getAxisRotation()))detail.add(axis==null?null:axis.getId());}if(h instanceof ReferenceNozzle){ReferenceNozzle n=(ReferenceNozzle)h;detail.add(n.getVacuumActuator()==null?null:n.getVacuumActuator().getId());detail.add(n.isChangerEnabled());detail.add(n.getRotationMode());detail.add(n.isEnableDynamicSafeZ());detail.add(n.getVacuumSenseActuator()==null?null:n.getVacuumSenseActuator().getId());detail.add(n.getBlowOffActuator()==null?null:n.getBlowOffActuator().getId());}if(h instanceof ReferenceActuator){ReferenceActuator a=(ReferenceActuator)h;detail.add(a.getDriver()==null?null:a.getDriver().getId());detail.add(a.getValueType());detail.add(a.getIndex());detail.add(a.getEnabledActuation());detail.add(a.getHomedActuation());detail.add(a.getDisabledActuation());detail.add(a.getCoordinatedBeforeActuateEnum());detail.add(a.getCoordinatedAfterActuateEnum());detail.add(a.getCoordinatedBeforeReadEnum());}}
        for(Axis axis:c.getMachine().getAxes()){if(excluded.contains(axis))continue;detail.add(axis.getName());if(axis instanceof ReferenceControllerAxis){ReferenceControllerAxis a=(ReferenceControllerAxis)axis;detail.add(a.getDriver());detail.add(a.getLetter());detail.add(a.getHomeCoordinate());detail.add(a.getSafeZoneLow());detail.add(a.getSafeZoneHigh());detail.add(a.isSafeZoneLowEnabled());detail.add(a.isSafeZoneHighEnabled());detail.add(a.getType());detail.add(a.getResolution());detail.add(a.getSoftLimitLow());detail.add(a.getSoftLimitHigh());detail.add(a.isSoftLimitLowEnabled());detail.add(a.isSoftLimitHighEnabled());detail.add(a.getFeedratePerSecond());detail.add(a.getAccelerationPerSecond2());detail.add(a.getJerkPerSecond3());detail.add(a.isLimitRotation());detail.add(a.isWrapAroundRotation());detail.add(a.isInvertLinearRotational());detail.add(a.getBacklashCompensationMethod());detail.add(a.getBacklashOffset());detail.add(a.getSneakUpOffset());detail.add(a.getAcceptableTolerance());detail.add(a.getBacklashSpeedFactor());}}
        for(NozzleTip t:c.getMachine().getNozzleTips())if(!excluded.contains(t)&&t instanceof ReferenceNozzleTip){ReferenceNozzleTip tip=(ReferenceNozzleTip)t;detail.add(tip.getName());detail.add(tip.getMinPartDiameter());detail.add(tip.getMaxPartDiameter());detail.add(tip.getMaxPartHeight());detail.add(tip.getMaxPickTolerance());detail.add(tip.getMethodPartOn());detail.add(tip.getMethodPartOff());}
        // Serialize copied scalars only, never live driver/native objects with callbacks.
        for(int i=0;i<detail.size();i++)if(detail.get(i) instanceof Driver)detail.set(i,((Driver)detail.get(i)).getId());
        List<Object> identity=identity(c);identity.removeIf(excluded::contains);return new Snapshot(identity,values.toString()+G.toJson(detail),values,consumers,offsets);
    }
    static JsonObject obj(JsonObject o,String k)throws Exception{if(o==null||!o.has(k)||!o.get(k).isJsonObject())fail("INVALID_ARGUMENT","Expected object "+k);return o.getAsJsonObject(k);}
    static void fields(JsonObject o,String...keys)throws Exception{Set<String>s=new HashSet<>(Arrays.asList(keys));if(o==null)fail("INVALID_ARGUMENT","Expected object");for(Map.Entry<String,JsonElement> entry:o.entrySet())if(!s.contains(entry.getKey()))fail("UNKNOWN_FIELD","Unknown field "+entry.getKey());if(o.entrySet().size()!=keys.length)fail("INVALID_ARGUMENT","Missing required typed field");}
    static String txt(JsonObject o,String k)throws Exception{if(!o.has(k)||!o.get(k).isJsonPrimitive()||!o.getAsJsonPrimitive(k).isString())fail("INVALID_ARGUMENT","Expected string "+k);return o.get(k).getAsString();}
    static double num(JsonObject o,String k)throws Exception{if(!o.has(k)||!o.get(k).isJsonPrimitive()||!o.getAsJsonPrimitive(k).isNumber())fail("INVALID_ARGUMENT","Expected number "+k);double n=o.get(k).getAsDouble();if(!Double.isFinite(n))fail("OUT_OF_RANGE","Nonfinite "+k);return n;}
    static void bounded(JsonObject o,String k,double low,double high)throws Exception{double n=num(o,k);if(n<low||n>high)fail("OUT_OF_RANGE",k+" outside bounded simulator profile");}
    static void dwell(JsonObject o)throws Exception{for(String k:List.of("pick_dwell_ms","place_dwell_ms")){bounded(o,k,0,10000);if(num(o,k)!=Math.rint(num(o,k)))fail("INVALID_ARGUMENT","Dwell must be an integer");}}
    static Length mm(double x){return new Length(x,LengthUnit.Millimeters);}
    static void fail(String code,String message)throws Bridge.Fault{throw new Bridge.Fault(code,message);}
}
