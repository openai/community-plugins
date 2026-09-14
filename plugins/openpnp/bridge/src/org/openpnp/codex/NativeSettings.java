/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.camera.*;
import org.openpnp.machine.reference.feeder.*;

/**
 * Bounded typed native-model edits. No I/O, motion, scripts, arbitrary properties or persistence.
 * The bridge must enforce simulator-only identity, executor ownership, quiescence, revisions and
 * its configuration-fault fence. Stage the WHOLE patch before beginning a configuration mutation.
 * Changes use explicit millimeters/degrees and native feed-count semantics (count before next feed).
 * New package/part references resolve in caller order; native subclasses are deliberately rejected.
 */
public final class NativeSettings {
    private NativeSettings() { }
    private static final int MAX_CHANGES = 100, MAX_PADS = 256;
    private static final List<String> TYPES = Collections.unmodifiableList(Arrays.asList(
        "set_part_height", "assign_feeder", "set_feeder_enabled", "set_machine_speed",
        "create_package", "set_package_footprint", "create_part", "set_part_properties",
        "set_package_compatibility", "set_tray_feeder_geometry", "set_strip_feeder_geometry",
        "set_camera_geometry", "set_camera_settling", "set_camera_dynamic_settling", "set_nozzle_settings",
        "set_nozzle_tip_settings", "set_axis_motion_limits", NativeAxisBacklashSettings.TYPE, NativeMappedAxisSettings.TYPE, NativeNozzleAssembly.TYPE, NativeVacuumSettings.TYPE, "set_job_planner_settings",
        "set_job_retry_settings", "set_part_retry_settings", "set_feeder_retry_settings",
        "set_head_park_location", "set_machine_discard_location"));
    public static List<String> types() { List<String> result=new ArrayList<>(TYPES);result.addAll(NativeVisionSettings.TYPES);return Collections.unmodifiableList(result); }
    public static boolean hasType(String type) { return TYPES.contains(type)||NativeVisionSettings.TYPES.contains(type); }
    @FunctionalInterface private interface Edit { void apply() throws Exception; }
    @FunctionalInterface private interface Guard { void validate() throws Exception; }

    public static final class Patch {
        private final List<Edit> edits;
        private final List<Map<String,Object>> effects;
        private boolean used;
        private final Guard retainedGuard;
        private final boolean vacuumSensingChange;
        private java.util.function.Supplier<Map<String,Object>> result=Collections::emptyMap;
        public Map<String,Object> result(){return result.get();}
        private Patch(List<Edit> edits, List<Map<String,Object>> effects) { this(edits,effects,null); }
        private Patch(List<Edit> edits,List<Map<String,Object>> effects,Guard guard){this(edits,effects,guard,false);}
        private Patch(List<Edit> edits,List<Map<String,Object>> effects,Guard guard,boolean vacuum){this.edits=edits;this.effects=effects;retainedGuard=guard;vacuumSensingChange=vacuum;}
        public boolean isVacuumSensingChange(){return vacuumSensingChange;}
        public boolean requiresRetainedPlan(){return retainedGuard!=null;}
        public synchronized void validateCurrentState()throws Exception{
            if(used)fail("PATCH_ALREADY_APPLIED","A used patch cannot be replayed");
            if(retainedGuard!=null)retainedGuard.validate();
        }
        /** Read-only serializable impact description. Native model edits are not measurements. */
        public List<Map<String,Object>> metadata() { return Collections.unmodifiableList(effects); }
        /** Once only; a setter failure is partial persistence/model state for the bridge fault fence. */
        public synchronized void apply() throws Exception {
            if (used) fail("PATCH_ALREADY_APPLIED", "Stage a new revision-bound patch; a used patch cannot be replayed");
            if(retainedGuard!=null)retainedGuard.validate();
            used = true;
            for (Edit edit : edits) edit.apply();
        }
    }

    /** Single-change compatibility API; use stage for related create/assign sequences. */
    public static void validate(Configuration config, JsonObject change) throws Exception { stage(config, singleton(change)); }
    public static void apply(Configuration config, JsonObject change) throws Exception { stage(config, singleton(change)).apply(); }
    private static JsonArray singleton(JsonObject change) { JsonArray changes = new JsonArray(); changes.add(change); return changes; }

    public static Patch stage(Configuration config, JsonArray changes) throws Exception {
        if (config == null || config.getMachine() == null || changes == null || changes.size() < 1 || changes.size() > MAX_CHANGES)
            fail("INVALID_PATCH", "Expected 1–100 typed changes and a loaded native configuration");
        boolean vacuum=false;
        for(JsonElement row:changes)if(row.isJsonObject()&&row.getAsJsonObject().has("type")&&row.getAsJsonObject().get("type").isJsonPrimitive()&&NativeVacuumSettings.TYPE.equals(row.getAsJsonObject().get("type").getAsString()))vacuum=true;
        if(vacuum){
            if(changes.size()!=1)fail("INVALID_PATCH","One complete vacuum sensing change requires its own retained plan");
            NativeVacuumSettings.Plan plan=NativeVacuumSettings.stage(config,changes.get(0).getAsJsonObject());
            return new Patch(Collections.singletonList(plan::apply),Collections.singletonList(Collections.unmodifiableMap(plan.metadata())),plan::validateCurrentState,true);
        }
        boolean assembly=false;
        for(JsonElement row:changes)if(row.isJsonObject()&&row.getAsJsonObject().has("type")&&row.getAsJsonObject().get("type").isJsonPrimitive()&&NativeNozzleAssembly.TYPE.equals(row.getAsJsonObject().get("type").getAsString()))assembly=true;
        if(assembly){
            if(changes.size()!=1)fail("INVALID_PATCH","One complete nozzle assembly requires its own retained plan");
            NativeNozzleAssembly.Plan plan=NativeNozzleAssembly.stage(config,changes.get(0).getAsJsonObject());
            Patch patch=new Patch(Collections.singletonList(plan::apply),Collections.singletonList(plan.metadata()),plan::validateCurrentState);patch.result=plan::result;return patch;
        }
        boolean mapped=false;
        for(JsonElement row:changes)if(row.isJsonObject()&&row.getAsJsonObject().has("type")&&row.getAsJsonObject().get("type").isJsonPrimitive()&&NativeMappedAxisSettings.TYPE.equals(row.getAsJsonObject().get("type").getAsString()))mapped=true;
        if(mapped){
            if(changes.size()!=1)fail("INVALID_PATCH","One mapped-axis change requires its own original-reference-bound plan");
            final NativeMappedAxisSettings.Plan plan;
            try{plan=NativeMappedAxisSettings.stage(config,changes.get(0).getAsJsonObject());}catch(NativeMappedAxisSettings.Fault e){throw new Bridge.Fault(e.code,e.getMessage());}
            return new Patch(Collections.singletonList(()->{try{plan.apply();}catch(NativeMappedAxisSettings.Fault e){throw new Bridge.Fault(e.code,e.getMessage());}}),Collections.singletonList(plan.metadata()),()->{try{plan.validateCurrentState();}catch(NativeMappedAxisSettings.Fault e){throw new Bridge.Fault(e.code,e.getMessage());}});
        }
        boolean vision=false;
        for(JsonElement row:changes) if(row.isJsonObject()&&row.getAsJsonObject().has("type")&&row.getAsJsonObject().get("type").isJsonPrimitive()&&NativeVisionSettings.TYPES.contains(row.getAsJsonObject().get("type").getAsString())) vision=true;
        if(vision) {
            for(JsonElement row:changes) if(!row.isJsonObject()||!row.getAsJsonObject().has("type")||!row.getAsJsonObject().get("type").isJsonPrimitive()||!NativeVisionSettings.TYPES.contains(row.getAsJsonObject().get("type").getAsString())) fail("INVALID_PATCH","Vision changes use a separate configuration plan so staged inheritance remains explicit");
            NativeVisionSettings.Patch patch=NativeVisionSettings.stage(config,changes);
            return new Patch(Collections.singletonList(patch::apply),patch.metadata());
        }
        return new Stager(config).stage(changes);
    }

    /**
     * Read native values for the typed setting surface, without moving, saving or sampling hardware.
     * Call under the same executor/quiescence discipline as a configuration snapshot. Values are
     * copied into JSON-safe maps; absent/nonfinite native numeric values are represented as null.
     * Rotational/transformed axes deliberately omit the linear millimeter setting schema.
     */
    public static Map<String,Object> describe(Configuration config) {
        Machine machine=config.getMachine();
        List<Map<String,Object>> packages=new ArrayList<>(),parts=new ArrayList<>(),feeders=new ArrayList<>(),cameras=new ArrayList<>(),nozzles=new ArrayList<>(),tips=new ArrayList<>(),axes=new ArrayList<>(),heads=new ArrayList<>();
        PnpJobProcessor jobProcessor=machine.getPnpJobProcessor();
        Map<String,Object> processor=values("native_class",jobProcessor==null?null:jobProcessor.getClass().getName(),"settings_editable",false);
        if(jobProcessor!=null&&jobProcessor.getClass()==ReferencePnpJobProcessor.class) {
            ReferencePnpJobProcessor p=(ReferencePnpJobProcessor)jobProcessor;
            boolean supported=p.planner!=null&&p.planner.getClass()==ReferencePnpJobProcessor.SimplePnpJobPlanner.class;
            processor.putAll(values("planner_native_class",p.planner==null?null:p.planner.getClass().getName(),"settings_editable",supported,
                "job_order",p.getJobOrder()==null?null:p.getJobOrder().name(),"strategy",p.planner==null||p.planner.getStrategy()==null?null:p.planner.getStrategy().name(),
                "optimize_multiple_nozzles",p.isOptimizeMultipleNozzles(),"pre_rotate_all_nozzles",p.isPreRotateAllNozzles(),"stepping_to_next_motion",p.isSteppingToNextMotion(),
                "vision_attempts",p.getMaxVisionRetries(),"placement_attempts",p.getMaxPlacementRetries(),"feeder_fault_limit",p.getFeederFaultLimit(),"feeder_fault_window",p.getFeederFaultWindowSize()));
        }
        for(org.openpnp.model.Package pkg:config.getPackages()) {
            Footprint f=pkg.getFootprint();List<Map<String,Object>> pads=new ArrayList<>();
            double scale=f==null?1:new Length(1,f.getUnits()).convertToUnits(LengthUnit.Millimeters).getValue();
            if(f!=null)for(Footprint.Pad p:f.getPads())pads.add(values("name",p.getName(),"x_mm",finite(p.getX()*scale),"y_mm",finite(p.getY()*scale),"width_mm",finite(p.getWidth()*scale),"height_mm",finite(p.getHeight()*scale),"rotation_deg",finite(p.getRotation()),"roundness",finite(p.getRoundness())));
            packages.add(values("package_id",pkg.getId(),"description",pkg.getDescription(),"body_width_mm",f==null?null:finite(f.getBodyWidth()*scale),"body_height_mm",f==null?null:finite(f.getBodyHeight()*scale),"pads",pads,"nozzle_tip_ids",tipIds(pkg.getCompatibleNozzleTips())));
        }
        for(Part part:config.getParts())parts.add(values("part_id",part.getId(),"package_id",part.getPackage()==null?null:part.getPackage().getId(),"name",part.getName(),"height_mm",millimeters(part.getHeight()),"speed",finite(part.getSpeed()),"native_class",part.getClass().getName(),"pick_retries",part.getPickRetryCount(),"retry_settings_editable",part.getClass()==Part.class));
        for(Feeder feeder:machine.getFeeders()) {
            boolean supported=feeder.getClass()==ReferenceStripFeeder.class || feeder.getClass()==ReferenceTrayFeeder.class || feeder.getClass()==ReferenceRotatedTrayFeeder.class;
            Map<String,Object> view=values("feeder_id",feeder.getId(),"native_class",feeder.getClass().getName(),"assignment_editable",supported,"part_id",feeder.getPart()==null?null:feeder.getPart().getId(),"enabled",feeder.isEnabled(),"feed_retries",feeder.getFeedRetryCount(),"pick_retries",feeder.getPickRetryCount(),"retry_settings_editable",supported);
            if(feeder.getClass()==ReferenceTrayFeeder.class) {
                ReferenceTrayFeeder f=(ReferenceTrayFeeder)feeder;Location pitch=f.getOffsets().convertToUnits(LengthUnit.Millimeters);
                view.putAll(values("location",locationView(f.getLocation()),"count_x",f.getTrayCountX(),"count_y",f.getTrayCountY(),"pitch_x_mm",finite(pitch.getX()),"pitch_y_mm",finite(pitch.getY()),"feed_count",f.getFeedCount(),"capacity",(long)f.getTrayCountX()*f.getTrayCountY(),"geometry_editable",true));
            } else if(feeder.getClass()==ReferenceStripFeeder.class) {
                ReferenceStripFeeder f=(ReferenceStripFeeder)feeder;
                view.putAll(values("location",locationView(f.getLocation()),"reference_hole",locationView(f.getReferenceHoleLocation()),"last_hole",locationView(f.getLastHoleLocation()),"part_pitch_mm",millimeters(f.getPartPitch()),"hole_pitch_mm",millimeters(f.getHolePitch()),"tape_width_mm",millimeters(f.getTapeWidth()),"hole_diameter_mm",millimeters(f.getHoleDiameter()),"reference_hole_to_part_mm",millimeters(f.getReferenceHoleToPartLinear()),"feed_count",f.getFeedCount(),"max_feed_count",f.getMaxFeedCount(),"vision_enabled",f.isVisionEnabled(),"geometry_editable",true));
            } else view.put("geometry_editable",false);
            feeders.add(view);
        }
        for(Camera camera:machine.getAllCameras()) {
            Map<String,Object> view=values("camera_id",camera.getId(),"native_class",camera.getClass().getName(),"geometry_editable",false,"settling_editable",false);
            if(camera.getClass()==ImageCamera.class || camera.getClass()==SimulatedUpCamera.class) {
                ReferenceCamera c=(ReferenceCamera)camera;Location upp=c.getUnitsPerPixelPrimary().convertToUnits(LengthUnit.Millimeters);
                view.putAll(values("units_per_pixel_x_mm",finite(upp.getX()),"units_per_pixel_y_mm",finite(upp.getY()),"working_plane_z_mm",finite(upp.getZ()),"head_offsets",locationView(c.getHeadOffsets()),"units_per_pixel_3d_enabled",c.isEnableUnitsPerPixel3D(),"advanced_calibration_enabled",c.getAdvancedCalibration().isEnabled(),"advanced_calibration_valid",c.getAdvancedCalibration().isValid(),"geometry_editable",!c.isEnableUnitsPerPixel3D()&&!c.getAdvancedCalibration().isEnabled(),"settling_editable",true));
                view.putAll(NativeCameraSettling.describe(c));
            }
            cameras.add(view);
        }
        for(Head head:machine.getHeads())heads.add(values("head_id",head.getId(),"native_class",head.getClass().getName(),"park_location_editable",head.getClass()==ReferenceHead.class,"park_location",locationView(head.getParkLocation()),"park_coordinate_semantics","native default head-mountable machine XY; park moves to native Safe Z and ignores stored Z/rotation"));
        for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles()) {
            Map<String,Object> view=values("nozzle_id",nozzle.getId(),"head_id",head.getId(),"native_class",nozzle.getClass().getName(),"settings_editable",nozzle.getClass()==ReferenceNozzle.class);
            if(nozzle.getClass()==ReferenceNozzle.class) {
                ReferenceNozzle n=(ReferenceNozzle)nozzle;NozzleTip t=n.getNozzleTip();
                view.put("vacuum_sensing",NativeVacuumSettings.describe(config,n));
                view.putAll(values("name",n.getName(),"head_offsets",locationView(n.getHeadOffsets()),"x_axis_id",n.getAxisX()==null?null:n.getAxisX().getId(),"y_axis_id",n.getAxisY()==null?null:n.getAxisY().getId(),"z_axis_id",n.getAxisZ()==null?null:n.getAxisZ().getId(),"rotation_axis_id",n.getAxisRotation()==null?null:n.getAxisRotation().getId(),"vacuum_actuator_id",n.getVacuumActuator()==null?null:n.getVacuumActuator().getId(),"vacuum_sense_actuator_id",n.getVacuumSenseActuator()==null?null:n.getVacuumSenseActuator().getId(),"changer_enabled",n.isChangerEnabled(),"rotation_mode",n.getRotationMode()==null?null:n.getRotationMode().name()));
                view.putAll(values("pick_dwell_ms",n.getPickDwellMilliseconds(),"place_dwell_ms",n.getPlaceDwellMilliseconds(),"nozzle_tip_ids",tipIds(n.getCompatibleNozzleTips()),"installed_nozzle_tip_id",t==null?null:t.getId(),"installed_tip_runout_calibrated",t!=null&&t.getClass()==ReferenceNozzleTip.class&&((ReferenceNozzleTip)t).getCalibration().isCalibrated(n)));
            }
            nozzles.add(view);
        }
        for(NozzleTip tip:machine.getNozzleTips()) {
            Map<String,Object> view=values("nozzle_tip_id",tip.getId(),"native_class",tip.getClass().getName(),"settings_editable",tip.getClass()==ReferenceNozzleTip.class);
            if(tip.getClass()==ReferenceNozzleTip.class){ReferenceNozzleTip t=(ReferenceNozzleTip)tip;view.putAll(values("pick_dwell_ms",t.getPickDwellMilliseconds(),"place_dwell_ms",t.getPlaceDwellMilliseconds(),"runout_calibration_enabled",t.getCalibration().isEnabled()));view.put("vacuum_sensing",vacuumTipView(machine,t));}
            tips.add(view);
        }
        for(Axis axis:machine.getAxes()) {
            Map<String,Object> view=values("axis_id",axis.getId(),"axis_type",axis.getType().name(),"native_class",axis.getClass().getName(),"linear_motion_settings_editable",false);
            if(axis.getClass()==ReferenceControllerAxis.class && axis.getType()!=Axis.Type.Rotation && !((ReferenceControllerAxis)axis).isInvertLinearRotational()) {
                ReferenceControllerAxis a=(ReferenceControllerAxis)axis;
                view.putAll(values("soft_limit_low_mm",millimeters(a.getSoftLimitLow()),"soft_limit_high_mm",millimeters(a.getSoftLimitHigh()),"soft_limit_low_enabled",a.isSoftLimitLowEnabled(),"soft_limit_high_enabled",a.isSoftLimitHighEnabled(),"feedrate_mm_per_s",millimeters(a.getFeedratePerSecond()),"acceleration_mm_per_s2",millimeters(a.getAccelerationPerSecond2()),"jerk_mm_per_s3",millimeters(a.getJerkPerSecond3()),"linear_motion_settings_editable",true));
            }
            if(axis.getClass()==org.openpnp.machine.reference.axis.ReferenceMappedAxis.class){
                view.put("mapped_geometry_editable",false);
                try{view.putAll(NativeMappedAxisSettings.describe(config,axis.getId()));view.put("mapped_geometry_editable",true);view.put("edit_requires_disabled",true);view.put("edit_requires_basic_2d_cameras",true);view.put("restore_supported",false);}
                catch(Exception e){view.put("mapped_geometry_fault",e instanceof NativeMappedAxisSettings.Fault?((NativeMappedAxisSettings.Fault)e).code:"UNSUPPORTED_AXIS_TOPOLOGY");}
            }
            if(axis.getClass()==ReferenceControllerAxis.class)for(Axis dependent:machine.getAxes())if(dependent instanceof org.openpnp.machine.reference.axis.ReferenceMappedAxis&&((org.openpnp.machine.reference.axis.ReferenceMappedAxis)dependent).getInputAxis()==axis){view.put("linear_motion_settings_editable",false);view.put("motion_settings_fault","MAPPED_SOURCE_LIMITS_UNSUPPORTED");}
            if(axis.getClass()==ReferenceControllerAxis.class){view.put("backlash_settings_editable",false);try{view.putAll(NativeAxisBacklashSettings.describe(config,(ReferenceControllerAxis)axis));}catch(Exception e){view.put("backlash_settings_fault",e instanceof Bridge.Fault?((Bridge.Fault)e).code:"UNSUPPORTED_MODEL");}}
            axes.add(view);
        }
        Map<String,Object> result=values("runtime_scope","isolated-native-simulator-only","physical_qualification",false,"machine_speed",finite(machine.getSpeed()),"machine_homed",machine.isHomed(),"machine_native_class",machine.getClass().getName(),"discard_location",locationView(machine.getDiscardLocation()),"discard_location_editable",machine.getClass()==ReferenceMachine.class,"job_processor",processor,"heads",heads,"packages",packages,"parts",parts,"feeders",feeders,"cameras",cameras,"nozzles",nozzles,"nozzle_tips",tips,"axes",axes);
        try { result.put("vision_settings",NativeVisionSettings.describe(config)); }
        catch(Exception e) {result.put("vision_settings",values("settings_editable",false,"code",e instanceof Bridge.Fault?((Bridge.Fault)e).code:"UNSUPPORTED_VISION_MODEL","reason",e.getMessage()));}
        return result;
    }

    /** Every exact tip is visible, including unloaded/unused definitions. These are native
     * settings, not samples; nullable invalid values never imply editability or source authority. */
    private static Map<String,Object> vacuumTipView(Machine machine,ReferenceNozzleTip tip) {
        List<String> compatible=new ArrayList<>(),installed=new ArrayList<>();
        for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles()) {
            if(nozzle.getCompatibleNozzleTips().contains(tip))compatible.add(nozzle.getId());
            if(nozzle.getNozzleTip()==tip)installed.add(nozzle.getId());
        }
        Collections.sort(compatible);Collections.sort(installed);
        return Collections.unmodifiableMap(values("nozzle_tip_id",tip.getId(),"readback_only",true,"requires_nozzle_binding_for_edit",true,
            "reading_units",NativeVacuumSettings.UNITS,"threshold_provenance",NativeVacuumSettings.PROVENANCE,
            "method_part_on",vacuumMethod(tip,true),"method_part_off",vacuumMethod(tip,false),
            "part_on_low",finite(tip.getVacuumLevelPartOnLow()),"part_on_high",finite(tip.getVacuumLevelPartOnHigh()),
            "part_off_low",finite(tip.getVacuumLevelPartOffLow()),"part_off_high",finite(tip.getVacuumLevelPartOffHigh()),
            "part_on_check_after_pick",tip.isPartOnCheckAfterPick(),"part_on_check_align",tip.isPartOnCheckAlign(),"part_on_check_before_place",tip.isPartOnCheckBeforePlace(),
            "part_off_check_after_place",tip.isPartOffCheckAfterPlace(),"part_off_check_before_pick",tip.isPartOffCheckBeforePick(),
            "part_off_probe_ms",tip.getPartOffProbingMilliseconds(),"part_off_dwell_ms",tip.getPartOffDwellMilliseconds(),
            "establish_part_on_level",tip.isEstablishPartOnLevel(),"establish_part_off_level",tip.isEstablishPartOffLevel(),
            "compatible_nozzle_ids",List.copyOf(compatible),"installed_on_nozzle_ids",List.copyOf(installed),
            "signal_provenance","not-attested-by-settings-readback","source_authority_granted",false,"occupancy_reconciled",false,"measurement_performed",false,"physical_qualification",false));
    }
    private static String vacuumMethod(ReferenceNozzleTip tip,boolean partOn) {
        try {ReferenceNozzleTip.VacuumMeasurementMethod value=NativeVacuumSettings.readMethod(tip,partOn);return value==null?null:value.name();}
        catch(Exception unsupported){return null;}
    }
    private static Map<String,Object> values(Object... pairs) {Map<String,Object> values=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)values.put((String)pairs[i],pairs[i+1]);return values;}
    private static List<String> tipIds(Collection<NozzleTip> tips) {List<String> ids=new ArrayList<>();for(NozzleTip t:tips)ids.add(t.getId());Collections.sort(ids);return ids;}
    private static Double finite(double value){return Double.isFinite(value)?value:null;}
    private static Double millimeters(Length value){return value==null?null:finite(value.convertToUnits(LengthUnit.Millimeters).getValue());}
    private static Map<String,Object> locationView(Location value){if(value==null)return null;Location mm=value.convertToUnits(LengthUnit.Millimeters);return values("x_mm",finite(mm.getX()),"y_mm",finite(mm.getY()),"z_mm",finite(mm.getZ()),"rotation_deg",finite(mm.getRotation()));}

    private static final class Stager {
        final Configuration config;
        final Machine machine;
        final Map<String,PackageRef> packages = new LinkedHashMap<>();
        final Map<String,PartRef> parts = new LinkedHashMap<>();
        final List<Edit> edits = new ArrayList<>();
        final List<Guard> guards = new ArrayList<>();
        final Set<String> backlashTargets = new HashSet<>();
        final List<Map<String,Object>> effects = new ArrayList<>();
        Stager(Configuration config) {
            this.config = config; this.machine = config.getMachine();
            for (org.openpnp.model.Package p : config.getPackages()) packages.put(nativeKey(p.getId()), new PackageRef(p.getId(),p));
            for (Part p : config.getParts()) parts.put(nativeKey(p.getId()), new PartRef(p.getId(),p,p.getPackage()==null?null:p.getPackage().getId()));
        }
        Patch stage(JsonArray changes) throws Exception {
            for (JsonElement raw : changes) {
                if (raw == null || !raw.isJsonObject()) fail("INVALID_ARGUMENT", "Every change must be an object");
                // Parse all values now and close over immutable primitives/new model objects, never caller JSON.
                stageChange(raw.getAsJsonObject());
            }
            return new Patch(edits,effects,guards.isEmpty()?null:()->{for(Guard guard:guards)guard.validate();});
        }
        void stageChange(JsonObject c) throws Exception {
            String type = text(c, "type");
            switch (type) {
            case "set_machine_speed": {
                only(c,"type","speed"); double speed = number(c,"speed",0.001,1);
                edits.add(() -> machine.setSpeed(speed)); effect(type,"machine", "job-validation"); break;
            }
            case "set_job_planner_settings": {
                only(c,"type","job_order","strategy","optimize_multiple_nozzles","pre_rotate_all_nozzles","stepping_to_next_motion");
                ReferencePnpJobProcessor processor=processor();
                ReferencePnpJobProcessor.SimplePnpJobPlanner planner=(ReferencePnpJobProcessor.SimplePnpJobPlanner)processor.planner;
                ReferencePnpJobProcessor.JobOrderHint order=enumValue(c,"job_order",ReferencePnpJobProcessor.JobOrderHint.class);
                PnpJobPlanner.Strategy strategy=enumValue(c,"strategy",PnpJobPlanner.Strategy.class);
                boolean multiple=bool(c,"optimize_multiple_nozzles"),rotate=bool(c,"pre_rotate_all_nozzles"),stepping=bool(c,"stepping_to_next_motion");
                edits.add(() -> {if(processor.planner!=planner)fail("STALE_MODEL","Native planner identity changed after staging");processor.setJobOrder(order);planner.setStrategy(strategy);processor.setOptimizeMultipleNozzles(multiple);processor.setPreRotateAllNozzles(rotate);processor.setSteppingToNextMotion(stepping);});
                effect(type,"job-processor","job-planning","tool-order","job-validation","motion-plan"); break;
            }
            case "set_job_retry_settings": {
                only(c,"type","vision_attempts","placement_attempts","feeder_fault_limit","feeder_fault_window");ReferencePnpJobProcessor processor=processor();
                int vision=integer(c,"vision_attempts",1,10),placement=integer(c,"placement_attempts",1,10),limit=integer(c,"feeder_fault_limit",1,100),window=integer(c,"feeder_fault_window",1,100);
                if(limit>window)fail("INVALID_RETRY_POLICY","Feeder fault limit must not exceed its rolling window");
                edits.add(() -> {processor.setMaxVisionRetries(vision);processor.setMaxPlacementRetries(placement);processor.setFeederFaultLimit(limit);processor.setFeederFaultWindowSize(window);});
                effect(type,"job-processor","retry-budget","feeder-fault-policy","job-validation"); break;
            }
            case "set_part_retry_settings": {
                only(c,"type","part_id","pick_retries");PartRef part=part(c);if(part.model!=null)exact(part.model,Part.class,"part");int retries=integer(c,"pick_retries",0,5);
                edits.add(() -> part.get().setPickRetryCount(retries));effect(type,part.id,"part-pick-retry-budget","job-validation");break;
            }
            case "set_feeder_retry_settings": {
                only(c,"type","feeder_id","feed_retries","pick_retries");ReferenceFeeder feeder=(ReferenceFeeder)feeder(c);int feed=integer(c,"feed_retries",0,5),pick=integer(c,"pick_retries",0,5);
                edits.add(() -> {feeder.setFeedRetryCount(feed);feeder.setPickRetryCount(pick);});effect(type,feeder.getId(),"feeder-retry-budget","job-validation");break;
            }
            case "set_head_park_location": {
                only(c,"type","head_id","x_mm","y_mm");ReferenceHead head=head(c);
                double x=number(c,"x_mm",-1000,1000),y=number(c,"y_mm",-1000,1000);Location current=head.getParkLocation();
                if(current==null)fail("UNSUPPORTED_MODEL","Head park location is absent");
                Location next=current.convertToUnits(LengthUnit.Millimeters).derive(x,y,null,null);
                edits.add(() -> head.setParkLocation(next));effect(type,head.getId(),"park-route-validation","motion-plan","job-validation");break;
            }
            case "set_machine_discard_location": {
                only(c,"type","location");ReferenceMachine target=exact(machine,ReferenceMachine.class,"machine");Location next=location(c,"location");
                edits.add(() -> target.setDiscardLocation(next));effect(type,"machine","discard-route-validation","motion-plan","job-validation");break;
            }
            case "set_part_height": {
                only(c,"type","part_id","height_mm"); PartRef part = part(c); Length height = length(c,"height_mm",0.001,50);
                edits.add(() -> part.get().setHeight(height)); effect(type,part.id,"part-height","job-validation","motion-clearance"); break;
            }
            case "assign_feeder": {
                only(c,"type","feeder_id","part_id"); Feeder feeder = feeder(c); PartRef part = part(c);
                edits.add(() -> feeder.setPart(part.get())); effect(type,feeder.getId(),"material-identity","job-validation"); break;
            }
            case "set_feeder_enabled": {
                only(c,"type","feeder_id","enabled"); Feeder feeder = feeder(c); boolean enabled = bool(c,"enabled");
                edits.add(() -> feeder.setEnabled(enabled)); effect(type,feeder.getId(),"job-validation"); break;
            }
            case "create_package": case "set_package_footprint": {
                only(c,"type","package_id","description","body_width_mm","body_height_mm","pads");
                String id = id(c,"package_id"), description = text(c,"description"); Footprint footprint = footprint(c);
                PackageRef pkg = packages.get(nativeKey(id));
                if ("create_package".equals(type)) {
                    if (pkg != null) fail("ALREADY_EXISTS","Package already exists: " + id);
                    // Package's AbstractPartSettingsHolder superclass also registers a listener.
                    pkg = new PackageRef(id,null); packages.put(nativeKey(id),pkg);final PackageRef addition=pkg;
                    edits.add(() -> {org.openpnp.model.Package model=new org.openpnp.model.Package(id);model.setDescription(description);model.setFootprint(footprint);config.addPackage(model);addition.model=model;});
                } else if (pkg == null) fail("NOT_FOUND","Unknown package: " + id);
                final PackageRef target = pkg;
                if(!"create_package".equals(type))edits.add(() -> { target.get().setDescription(description); target.get().setFootprint(footprint); });
                effect(type,pkg.id,"shared-package-footprint","vision","job-validation"); break;
            }
            case "create_part": case "set_part_properties": {
                only(c,"type","part_id","package_id","name","height_mm","speed");
                String id = id(c,"part_id"), name = text(c,"name"); PackageRef pkg = pkg(c);
                Length height = length(c,"height_mm",0.001,50); double speed = number(c,"speed",0.001,1);
                PartRef part = parts.get(nativeKey(id));
                if ("create_part".equals(type)) {
                    if (part != null) fail("ALREADY_EXISTS","Part already exists: " + id);
                    // Part's constructor permanently registers a native ConfigurationListener.
                    // Keep previews pure: only materialize a native Part after the whole patch validates.
                    part = new PartRef(id,null,pkg.id); parts.put(nativeKey(id),part); final PartRef addition = part;
                    edits.add(() -> { Part model=new Part(id);model.setName(name);model.setPackage(pkg.get());model.setHeight(height);model.setSpeed(speed);config.addPart(model);addition.model=model; });
                } else if (part == null) fail("NOT_FOUND","Unknown part: " + id);
                final PartRef target = part;target.packageId=pkg.id;
                if(!"create_part".equals(type))edits.add(() -> { Part model=target.get();model.setName(name);model.setPackage(pkg.get());model.setHeight(height);model.setSpeed(speed); });
                effect(type,part.id,"shared-part-definition","motion-clearance","job-validation"); break;
            }
            case "set_package_compatibility": {
                only(c,"type","package_id","nozzle_tip_ids"); PackageRef pkg = pkg(c);
                List<NozzleTip> tips = tips(c,"nozzle_tip_ids");
                edits.add(() -> {
                    for (NozzleTip old : new ArrayList<>(pkg.get().getCompatibleNozzleTips())) pkg.get().removeCompatibleNozzleTip(old);
                    for (NozzleTip tip : tips) pkg.get().addCompatibleNozzleTip(tip);
                });
                effect(type,pkg.id,"shared-package-tool-compatibility","job-validation"); break;
            }
            case "set_tray_feeder_geometry": {
                only(c,"type","feeder_id","location","count_x","count_y","pitch_x_mm","pitch_y_mm","feed_count");
                ReferenceTrayFeeder tray = exact(feeder(c),ReferenceTrayFeeder.class,"tray feeder");
                Location location = location(c,"location"); int nx = integer(c,"count_x",1,100), ny = integer(c,"count_y",1,100);
                double px = number(c,"pitch_x_mm",-100,100), py = number(c,"pitch_y_mm",-100,100);
                if ((nx > 1 && px == 0) || (ny > 1 && py == 0)) fail("INVALID_GEOMETRY","Nontrivial tray axes need nonzero pitch");
                if (Math.abs(px)*(nx-1)>1000 || Math.abs(py)*(ny-1)>1000) fail("OUT_OF_RANGE","Tray extent exceeds 1000 mm");
                int count = integer(c,"feed_count",0,nx*ny); Location offsets = new Location(LengthUnit.Millimeters,px,py,0,0);
                edits.add(() -> { tray.setLocation(location); tray.setTrayCountX(nx); tray.setTrayCountY(ny); tray.setOffsets(offsets); tray.setFeedCount(count); });
                effect(type,tray.getId(),"feeder-pickup","material-index","job-validation"); break;
            }
            case "set_strip_feeder_geometry": {
                only(c,"type","feeder_id","location","reference_hole","last_hole","part_pitch_mm","hole_pitch_mm","tape_width_mm","hole_diameter_mm","reference_hole_to_part_mm","feed_count","max_feed_count");
                ReferenceStripFeeder strip = exact(feeder(c),ReferenceStripFeeder.class,"strip feeder");
                Location location = location(c,"location"), first = location(c,"reference_hole"), last = location(c,"last_hole");
                Length partPitch = length(c,"part_pitch_mm",0.1,100), holePitch = length(c,"hole_pitch_mm",0.1,100), tape = length(c,"tape_width_mm",2,100), hole = length(c,"hole_diameter_mm",0.1,20), offset = length(c,"reference_hole_to_part_mm",-100,100);
                if (first.getLinearDistanceTo(last)<holePitch.getValue()*0.5) fail("INVALID_GEOMETRY","Reference holes must be distinct with a useful tape baseline");
                if (hole.getValue()>=holePitch.getValue() || hole.getValue()>=tape.getValue()) fail("INVALID_GEOMETRY","Hole diameter must be smaller than pitch and tape width");
                int max = integer(c,"max_feed_count",1,1000000), count = integer(c,"feed_count",0,max);
                edits.add(() -> { strip.setLocation(location); strip.setReferenceHoleLocation(first); strip.setLastHoleLocation(last);
                    strip.setPartPitch(partPitch); strip.setHolePitch(holePitch); strip.setTapeWidth(tape); strip.setHoleDiameter(hole);
                    strip.setReferenceHoleToPartLinear(offset); strip.setMaxFeedCount(max); strip.setFeedCount(count); strip.resetVision(); });
                effect(type,strip.getId(),"feeder-pickup","material-index","feeder-vision","job-validation"); break;
            }
            case "set_camera_geometry": {
                only(c,"type","camera_id","units_per_pixel_x_mm","units_per_pixel_y_mm","working_plane_z_mm","head_offsets");
                ReferenceCamera camera = camera(c); double x = number(c,"units_per_pixel_x_mm",0.000001,10), y = number(c,"units_per_pixel_y_mm",0.000001,10), z = number(c,"working_plane_z_mm",-100,100);
                Location offsets = location(c,"head_offsets"), upp = new Location(LengthUnit.Millimeters,x,y,z,0);
                if (camera.isEnableUnitsPerPixel3D() || camera.getAdvancedCalibration().isEnabled()) fail("UNSUPPORTED_MODEL","Only basic 2D camera scale/offset settings are supported");
                edits.add(() -> { camera.setUnitsPerPixelPrimary(upp); camera.setHeadOffsets(offsets); camera.getAdvancedCalibration().setValid(false); resetTips(); });
                effect(type,camera.getId(),"camera-calibration","nozzle-runout","fiducial-registration","vision","job-validation"); break;
            }
            case "set_camera_settling": {
                only(c,"type","camera_id","settle_time_ms"); ReferenceCamera camera = camera(c); int ms = integer(c,"settle_time_ms",0,10000);
                edits.add(() -> { camera.setSettleMethod(AbstractSettlingCamera.SettleMethod.FixedTime); camera.setSettleTimeMs(ms); });
                effect(type,camera.getId(),"fixed-time-image-settling","vision-validation","job-validation"); break;
            }
            case "set_camera_dynamic_settling": {
                ReferenceCamera camera=camera(c);NativeCameraSettling.Settings settings=NativeCameraSettling.stage(camera,c);
                edits.add(settings::apply);
                effect(type,camera.getId(),"dynamic-image-settling","vision-validation","job-validation");break;
            }
            case "set_nozzle_settings": {
                only(c,"type","nozzle_id","pick_dwell_ms","place_dwell_ms","nozzle_tip_ids"); ReferenceNozzle nozzle = nozzle(c);
                int pick = integer(c,"pick_dwell_ms",0,10000), place = integer(c,"place_dwell_ms",0,10000); List<NozzleTip> tips = tips(c,"nozzle_tip_ids");
                if (nozzle.getNozzleTip()!=null && !tips.contains(nozzle.getNozzleTip())) fail("TOOL_STILL_INSTALLED","Compatibility cannot remove the currently installed tip");
                edits.add(() -> { nozzle.setPickDwellMilliseconds(pick); nozzle.setPlaceDwellMilliseconds(place);
                    for (NozzleTip old : new ArrayList<>(nozzle.getCompatibleNozzleTips())) nozzle.removeCompatibleNozzleTip(old);
                    for (NozzleTip tip : tips) nozzle.addCompatibleNozzleTip(tip); });
                effect(type,nozzle.getId(),"pick-release-validation","tool-compatibility","job-validation"); break;
            }
            case "set_nozzle_tip_settings": {
                only(c,"type","nozzle_tip_id","pick_dwell_ms","place_dwell_ms"); ReferenceNozzleTip tip = tip(id(c,"nozzle_tip_id"));
                int pick = integer(c,"pick_dwell_ms",0,10000), place = integer(c,"place_dwell_ms",0,10000);
                edits.add(() -> { tip.setPickDwellMilliseconds(pick); tip.setPlaceDwellMilliseconds(place); });
                effect(type,tip.getId(),"shared-tip-pick-release-validation","job-validation"); break;
            }
            case NativeAxisBacklashSettings.TYPE: {
                NativeAxisBacklashSettings.Plan plan=NativeAxisBacklashSettings.stage(config,c);
                if(!backlashTargets.add(id(c,"axis_id")))fail("INVALID_PATCH","A patch may contain only one backlash change per axis");
                guards.add(plan::validateCurrentState);edits.add(plan::apply);effects.add(plan.metadata());break;
            }
            case "set_axis_motion_limits": {
                only(c,"type","axis_id","soft_limit_low_mm","soft_limit_high_mm","feedrate_mm_per_s","acceleration_mm_per_s2","jerk_mm_per_s3");
                ReferenceControllerAxis axis = exact(machine.getAxis(id(c,"axis_id")),ReferenceControllerAxis.class,"controller axis");
                for(Axis dependent:machine.getAxes())if(dependent instanceof org.openpnp.machine.reference.axis.ReferenceMappedAxis && ((org.openpnp.machine.reference.axis.ReferenceMappedAxis)dependent).getInputAxis()==axis)fail("MAPPED_SOURCE_LIMITS_UNSUPPORTED","Existing mapped-source soft limits require a separately validated dependent-envelope transaction");
                if (axis.getType()==Axis.Type.Rotation || axis.isInvertLinearRotational()) fail("UNSUPPORTED_MODEL","Only linear controller axes use this millimeter schema");
                double low = number(c,"soft_limit_low_mm",-1000,1000), high = number(c,"soft_limit_high_mm",-1000,1000);
                if (low >= high) fail("INVALID_GEOMETRY","Axis soft limits require low < high");
                if (axis.isSafeZoneLowEnabled()) inside(axis.getSafeZoneLow(),low,high,"safe-zone low");
                if (axis.isSafeZoneHighEnabled()) inside(axis.getSafeZoneHigh(),low,high,"safe-zone high");
                Length feed = length(c,"feedrate_mm_per_s",0.001,1000), acceleration = length(c,"acceleration_mm_per_s2",0.001,10000), jerk = length(c,"jerk_mm_per_s3",0,1000000);
                edits.add(() -> { axis.setSoftLimitLow(mm(low)); axis.setSoftLimitHigh(mm(high)); axis.setSoftLimitLowEnabled(true); axis.setSoftLimitHighEnabled(true);
                    axis.setFeedratePerSecond(feed); axis.setAccelerationPerSecond2(acceleration); axis.setJerkPerSecond3(jerk); machine.setHomed(false); resetTips(); });
                effect(type,axis.getId(),"motion-envelope","homing-validation","nozzle-runout","fiducial-registration","job-validation"); break;
            }
            default: fail("UNSUPPORTED_CHANGE","No typed adapter for " + type);
            }
        }
        void resetTips() { for(NozzleTip tip : machine.getNozzleTips()) if(tip.getClass()==ReferenceNozzleTip.class) ((ReferenceNozzleTip)tip).getCalibration().resetAll(); }
        ReferencePnpJobProcessor processor()throws Exception {
            ReferencePnpJobProcessor p=exact(machine.getPnpJobProcessor(),ReferencePnpJobProcessor.class,"job processor");
            exact(p.planner,ReferencePnpJobProcessor.SimplePnpJobPlanner.class,"job planner");return p;
        }
        ReferenceHead head(JsonObject c)throws Exception {
            String target=id(c,"head_id");Head found=null;
            for(Head head:machine.getHeads())if(target.equals(head.getId())){if(found!=null)fail("AMBIGUOUS_ID","Duplicate head ID");found=head;}
            return exact(found,ReferenceHead.class,"head");
        }
        PartRef part(JsonObject c) throws Exception { String id = id(c,"part_id"); PartRef p = parts.get(nativeKey(id)); if(p==null)fail("NOT_FOUND","Unknown part: "+id); return p; }
        PackageRef pkg(JsonObject c) throws Exception { String id = id(c,"package_id"); PackageRef p = packages.get(nativeKey(id)); if(p==null)fail("NOT_FOUND","Unknown package: "+id); return p; }
        Feeder feeder(JsonObject c) throws Exception {
            String id = id(c,"feeder_id"); Feeder f = machine.getFeeder(id); if(f==null)fail("NOT_FOUND","Unknown feeder: "+id);
            if(f.getClass()!=ReferenceStripFeeder.class && f.getClass()!=ReferenceTrayFeeder.class && f.getClass()!=ReferenceRotatedTrayFeeder.class)fail("UNSUPPORTED_NATIVE_CLASS","Only qualified native strip/tray feeder classes may be edited");
            return f;
        }
        ReferenceCamera camera(JsonObject c) throws Exception {
            String id = id(c,"camera_id"); Camera found = null;
            for(Camera camera:machine.getAllCameras()) if(id.equals(camera.getId())) { if(found!=null)fail("AMBIGUOUS_ID","Duplicate camera ID"); found=camera; }
            if(found==null)fail("NOT_FOUND","Unknown camera: "+id);
            if(found.getClass()!=ImageCamera.class && found.getClass()!=SimulatedUpCamera.class)fail("UNSUPPORTED_NATIVE_CLASS","Only exact native simulator image/up cameras are supported");
            return (ReferenceCamera)found;
        }
        ReferenceNozzle nozzle(JsonObject c) throws Exception {
            String id=id(c,"nozzle_id"); Nozzle found=null;
            for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles())if(id.equals(nozzle.getId())) {if(found!=null)fail("AMBIGUOUS_ID","Duplicate nozzle ID");found=nozzle;}
            return exact(found,ReferenceNozzle.class,"nozzle");
        }
        ReferenceNozzleTip tip(String id) throws Exception { return exact(machine.getNozzleTip(id),ReferenceNozzleTip.class,"nozzle tip"); }
        List<NozzleTip> tips(JsonObject c,String key) throws Exception {
            JsonArray ids=array(c,key,64); List<NozzleTip> values=new ArrayList<>(); Set<String> seen=new HashSet<>();
            for(JsonElement e:ids) {String id=string(e,key);if(!seen.add(id))fail("DUPLICATE_ID","Duplicate nozzle-tip compatibility ID");values.add(tip(id));} return values;
        }
        void effect(String type,String target,String... invalidates) {
            Map<String,Object> m=new LinkedHashMap<>();m.put("type",type);m.put("target_id",target);m.put("effect","native-model-configuration");m.put("runtime_scope","isolated-native-simulator-only");
            m.put("invalidates",Collections.unmodifiableList(Arrays.asList(invalidates)));m.put("physical_qualification",false);
            m.put("shared_references",type.contains("package")?partReferences(target):Collections.emptyList());
            if(type.contains("feeder_geometry")) {m.put("material_state_edit",true);m.put("count_semantics","native feedCount before next feed; not evidence of a physical refill or refund");}
            if("set_tray_feeder_geometry".equals(type))m.put("indexing","native major axis: when count_x >= count_y, Y increments first; otherwise X increments first. Location rotation rotates part orientation, not grid offsets.");
            if(NativeCameraSettling.TYPE.equals(type)){m.put("native_timeout_is_hard_io_deadline",false);m.put("settling_outcome_reported_by_native_api",false);m.put("camera_geometric_calibration_changed",false);m.put("physical_calibration_validity","not-assessed");}
            if("set_job_planner_settings".equals(type))m.put("profile_dependency","The bridge must retain the selected profile's job-order constraint; sustained-workload requires Unsorted before and after the patch.");
            if("set_job_retry_settings".equals(type))m.put("count_semantics","Vision/placement values include the first attempt. Placement reprocessing applies to native Defer handling of feeder-attributed failures; nested part/feeder retries remain separate. Fault history is preserved.");
            if("set_part_retry_settings".equals(type))m.put("count_semantics","Extra outer part-pick retries after the first attempt. Native empty-feeder fallback can allow a second attempt even when configured retries are zero; this is not a total material-use bound.");
            if("set_feeder_retry_settings".equals(type))m.put("count_semantics","Extra feed/pick retries after each first attempt. These native loops may be nested inside part retries and deferred-placement reprocessing; this is not a total material-use bound.");
            if("set_head_park_location".equals(type))m.put("coordinate_semantics","Native default head-mountable machine XY. Existing stored Z/rotation are preserved, but MovableUtils.park ignores both and uses native Safe Z; no movement or collision qualification is performed here.");
            if("set_machine_discard_location".equals(type))m.put("coordinate_semantics","Native machine discard target in mm/degrees. Cycles.discard uses native nozzle placement movement then Safe Z; LimitedArticulation retains current nozzle rotation. No discard or collision qualification is performed here.");
            effects.add(Collections.unmodifiableMap(m));
        }
        List<String> partReferences(String packageId) { List<String> refs=new ArrayList<>();for(PartRef p:parts.values())if(p.packageId!=null&&nativeKey(packageId).equals(nativeKey(p.packageId)))refs.add(p.id);return Collections.unmodifiableList(refs); }
        // Match Configuration.getPart/getPackage/addPart/addPackage identity rules exactly.
        private static String nativeKey(String id){return id.toUpperCase();}
        private static final class PartRef {
            final String id;Part model;String packageId;
            PartRef(String id,Part model,String packageId){this.id=id;this.model=model;this.packageId=packageId;}
            Part get()throws Exception{if(model==null)fail("STAGING_INVARIANT","Native part has not been materialized by its preceding create operation: "+id);return model;}
        }
        private static final class PackageRef {
            final String id;org.openpnp.model.Package model;
            PackageRef(String id,org.openpnp.model.Package model){this.id=id;this.model=model;}
            org.openpnp.model.Package get()throws Exception{if(model==null)fail("STAGING_INVARIANT","Native package has not been materialized by its preceding create operation: "+id);return model;}
        }
    }

    private static Footprint footprint(JsonObject c) throws Exception {
        Footprint f=new Footprint();f.setUnits(LengthUnit.Millimeters);f.setBodyWidth(number(c,"body_width_mm",0.001,100));f.setBodyHeight(number(c,"body_height_mm",0.001,100));
        Set<String> names=new HashSet<>();
        for(JsonElement raw:array(c,"pads",MAX_PADS)) {
            if(!raw.isJsonObject())fail("INVALID_ARGUMENT","Footprint pad must be an object");JsonObject p=raw.getAsJsonObject();
            only(p,"name","x_mm","y_mm","width_mm","height_mm","rotation_deg","roundness");Footprint.Pad pad=new Footprint.Pad();String name=id(p,"name");
            if(!names.add(name))fail("DUPLICATE_ID","Duplicate pad name: "+name);pad.setName(name);pad.setX(number(p,"x_mm",-100,100));pad.setY(number(p,"y_mm",-100,100));
            pad.setWidth(number(p,"width_mm",0.001,100));pad.setHeight(number(p,"height_mm",0.001,100));pad.setRotation(number(p,"rotation_deg",-360,360));pad.setRoundness(number(p,"roundness",0,100));f.addPad(pad);
        }return f;
    }
    private static Location location(JsonObject c,String key)throws Exception {
        if(!c.has(key)||!c.get(key).isJsonObject())fail("INVALID_ARGUMENT","Expected explicit mm/degree location: "+key);JsonObject p=c.getAsJsonObject(key);
        only(p,"x_mm","y_mm","z_mm","rotation_deg");return new Location(LengthUnit.Millimeters,number(p,"x_mm",-1000,1000),number(p,"y_mm",-1000,1000),number(p,"z_mm",-100,100),number(p,"rotation_deg",-360,360));
    }
    private static <E extends Enum<E>>E enumValue(JsonObject c,String key,Class<E> type)throws Exception {String value=text(c,key);try{return Enum.valueOf(type,value);}catch(IllegalArgumentException e){fail("INVALID_ENUM","Unsupported "+key+": "+value);return null;}}
    private static void inside(Length value,double low,double high,String label)throws Exception {double n=value.convertToUnits(LengthUnit.Millimeters).getValue();if(!Double.isFinite(n)||n<low||n>high)fail("INVALID_GEOMETRY","Axis "+label+" lies outside proposed soft limits");}
    private static <T>T exact(Object value,Class<T> type,String label)throws Exception {if(value==null)fail("NOT_FOUND","Unknown "+label);if(value.getClass()!=type)fail("UNSUPPORTED_NATIVE_CLASS","Unsupported "+label+" class: "+value.getClass().getName());return type.cast(value);}
    private static void only(JsonObject c,String... allowed)throws Exception {Set<String> keys=new HashSet<>(Arrays.asList(allowed));for(Map.Entry<String,JsonElement> e:c.entrySet())if(!keys.contains(e.getKey()))fail("UNKNOWN_FIELD","Unknown field: "+e.getKey());}
    private static String text(JsonObject c,String key)throws Exception {return string(c.get(key),key);}
    private static String string(JsonElement e,String key)throws Exception {if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isString())fail("INVALID_ARGUMENT","Expected string: "+key);String s=e.getAsString();if(s.length()>1024||s.chars().anyMatch(x->x<32||x==127))fail("INVALID_ARGUMENT","Invalid text: "+key);return s;}
    private static String id(JsonObject c,String key)throws Exception {String s=text(c,key);if(!s.matches("[A-Za-z0-9_.:+-]{1,128}"))fail("INVALID_ID","Invalid identifier: "+key);return s;}
    private static double number(JsonObject c,String key,double low,double high)throws Exception {JsonElement e=c.get(key);if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isNumber())fail("INVALID_ARGUMENT","Expected number: "+key);double n=e.getAsDouble();if(!Double.isFinite(n)||n<low||n>high)fail("OUT_OF_RANGE","Out of range: "+key);return n;}
    private static int integer(JsonObject c,String key,int low,int high)throws Exception {double n=number(c,key,low,high);if(n!=Math.rint(n))fail("INVALID_ARGUMENT","Expected integer: "+key);return (int)n;}
    private static boolean bool(JsonObject c,String key)throws Exception {JsonElement e=c.get(key);if(e==null||!e.isJsonPrimitive()||!e.getAsJsonPrimitive().isBoolean())fail("INVALID_ARGUMENT","Expected boolean: "+key);return e.getAsBoolean();}
    private static JsonArray array(JsonObject c,String key,int max)throws Exception {JsonElement e=c.get(key);if(e==null||!e.isJsonArray()||e.getAsJsonArray().size()>max)fail("INVALID_ARRAY","Expected bounded array: "+key);return e.getAsJsonArray();}
    private static Length length(JsonObject c,String key,double low,double high)throws Exception {return mm(number(c,key,low,high));}
    private static Length mm(double value){return new Length(value,LengthUnit.Millimeters);}
    private static void fail(String code,String message)throws Bridge.Fault {throw new Bridge.Fault(code,message);}
}
