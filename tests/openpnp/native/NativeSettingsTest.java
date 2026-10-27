/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.camera.*;
import org.openpnp.machine.reference.feeder.*;

/** Real pinned native models, no mocks or reflective setters; isolated default simulator only. */
public final class NativeSettingsTest {
    static final Gson GSON=new Gson();
    static final List<String> passed=new ArrayList<>();
    static Configuration config;
    static Machine machine;
    static ReferenceTrayFeeder tray;
    static ReferenceNozzle nozzle;
    static ReferenceNozzleTip tip;
    static JsonObject object(Object... pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static JsonArray array(JsonObject... changes){JsonArray a=new JsonArray();for(JsonObject c:changes)a.add(c);return a;}
    static JsonObject pose(double x,double y,double z,double r){return object("x_mm",x,"y_mm",y,"z_mm",z,"rotation_deg",r);}
    @FunctionalInterface interface Action {void run()throws Exception;}
    static void expect(String code,Action action)throws Exception {try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){if(!code.equals(e.code))throw new AssertionError("Expected "+code+", got "+e.code,e);}}
    static void check(boolean ok,String message){if(!ok)throw new AssertionError(message);}
    static void near(double actual,double expected,String message){if(Math.abs(actual-expected)>1e-8)throw new AssertionError(message+": "+actual+" != "+expected);}

    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-settings-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);machine=config.getMachine();
        nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();tip=(ReferenceNozzleTip)nozzle.getNozzleTip();
        tray=new ReferenceTrayFeeder();tray.setName("Settings geometry fixture");machine.addFeeder(tray);
        int exit=0;
        try {
            machine.submit(() -> { machine.setEnabled(true); return null; },null,true).get(30,TimeUnit.SECONDS);
            machine.execute(() -> {checkAtomicStage();checkAbandonedPartDrafts();checkPartPackageAndTray();checkStrip();checkTooling();checkStrictInputs();return null;},false,5000,60000);
            machine.execute(() -> {machine.setEnabled(true);machine.home();tip.getCalibration().setEnabled(true);nozzle.calibrate();return null;},false,5000,90000);
            check(tip.getCalibration().isCalibrated(nozzle),"actual native calibration must precede invalidation test");
            machine.execute(() -> {checkCameraInvalidation();checkAxis();checkReadback();config.save();return null;},false,5000,60000);
            check(Files.size(root.resolve("packages.xml"))>0 && Files.size(root.resolve("parts.xml"))>0,"created native parts/packages persist through Configuration.save");
            String packages=Files.readString(root.resolve("packages.xml")),parts=Files.readString(root.resolve("parts.xml"));
            check(packages.contains("settings-package") && parts.contains("settings-part"),"saved resources retain new native identities");
            passed.add("native configuration save contains staged part/package definitions");
            System.out.println("OPENPNP_NATIVE_SETTINGS_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false)));
        } catch(Throwable failure){failure.printStackTrace();exit=1;}finally{machine.close();}System.exit(exit);
    }
    static JsonObject packageChange(String type,String id){JsonArray pads=new JsonArray();pads.add(object("name","1","x_mm",-0.9,"y_mm",0,"width_mm",0.8,"height_mm",1,"rotation_deg",0,"roundness",0));pads.add(object("name","2","x_mm",0.9,"y_mm",0,"width_mm",0.8,"height_mm",1,"rotation_deg",0,"roundness",0));return object("type",type,"package_id",id,"description","Explicit two-pad footprint","body_width_mm",1.6,"body_height_mm",0.8,"pads",pads);}
    static JsonObject partChange(String type,String id,String pkg){return object("type",type,"part_id",id,"package_id",pkg,"name","10k","height_mm",0.6,"speed",0.8);}

    static void checkAtomicStage()throws Exception {
        int packageCount=config.getPackages().size(),partCount=config.getParts().size();double speed=machine.getSpeed();
        JsonArray invalid=array(packageChange("create_package","bad-stage-package"),partChange("create_part","bad-stage-part","bad-stage-package"),object("type","set_machine_speed","speed",0.2),object("type","assign_feeder","feeder_id","missing","part_id","bad-stage-part"));
        expect("NOT_FOUND",()->NativeSettings.stage(config,invalid));
        check(config.getPackage("bad-stage-package")==null && config.getPart("bad-stage-part")==null,"failed later row must not add staged definitions");
        check(config.getPackages().size()==packageCount && config.getParts().size()==partCount,"failed stage leaves installed collections unchanged");near(machine.getSpeed(),speed,"failed stage leaves preceding existing setting unchanged");
        expect("ALREADY_EXISTS",()->NativeSettings.stage(config,array(packageChange("create_package","duplicate-new"),packageChange("create_package","duplicate-new"))));
        check(config.getPackage("duplicate-new")==null,"duplicate staged create is rejected atomically");
        expect("ALREADY_EXISTS",()->NativeSettings.stage(config,array(packageChange("create_package","Case-Collision"),packageChange("create_package","case-collision"))));
        String existingPackage=config.getPackages().get(0).getId();
        expect("ALREADY_EXISTS",()->NativeSettings.stage(config,array(partChange("create_part","Part-Collision",existingPackage),partChange("create_part","part-collision",existingPackage))));
        check(config.getPackage("case-collision")==null&&config.getPart("part-collision")==null,"case-insensitive native identities cannot overwrite a staged create");
        passed.add("late-invalid patches and exact/case-folded duplicate creates mutate no native model");
    }
    static void checkPartPackageAndTray()throws Exception {
        JsonArray tips=new JsonArray();tips.add(new JsonPrimitive(tip.getId()));
        JsonArray changes=array(packageChange("create_package","settings-package"),partChange("create_part","settings-part","settings-package"),object("type","set_package_compatibility","package_id","settings-package","nozzle_tip_ids",tips),object("type","assign_feeder","feeder_id",tray.getId(),"part_id","settings-part"),object("type","set_tray_feeder_geometry","feeder_id",tray.getId(),"location",pose(100,200,2,90),"count_x",4,"count_y",3,"pitch_x_mm",6,"pitch_y_mm",8,"feed_count",5));
        NativeSettings.Patch patch=NativeSettings.stage(config,changes);
        check(config.getPart("settings-part")==null && tray.getPart()==null,"stage remains passive until apply");
        // Change caller data after staging; staged setters must retain the already validated values.
        changes.get(1).getAsJsonObject().addProperty("height_mm",49);
        patch.apply();expect("PATCH_ALREADY_APPLIED",patch::apply);
        Part part=config.getPart("settings-part");near(part.getHeight().getValue(),0.6,"validated staged value is isolated from caller JSON mutation");
        check(part.getPackage()==config.getPackage("settings-package") && tray.getPart()==part,"new definitions resolve within a complete ordered patch");
        check(part.getPackage().getCompatibleNozzleTips().contains(tip),"native shared package compatibility is applied");
        check(part.getPackage().getFootprint().getPads().size()==2,"explicit footprint pads are preserved");
        // Native 4x3 tray is X-major: feedCount5 => zero-based4 => col1,row1.
        Location pick=tray.getPickLocation().convertToUnits(LengthUnit.Millimeters);near(pick.getX(),106,"native tray X-major column");near(pick.getY(),208,"native tray Y-major-within-column");near(pick.getRotation(),90,"tray location rotation does not rotate native grid offsets");
        check(Boolean.TRUE.equals(patch.metadata().get(4).get("material_state_edit")),"counter edit advertises material-state effect");
        expect("OUT_OF_RANGE",()->NativeSettings.validate(config,object("type","set_tray_feeder_geometry","feeder_id",tray.getId(),"location",pose(0,0,0,0),"count_x",4,"count_y",3,"pitch_x_mm",6,"pitch_y_mm",8,"feed_count",13)));
        passed.add("staged package-part-feeder dependencies, immutable plan input and actual native tray indexing");
    }
    // Test-only inspection of one fixed pinned upstream field. The production adapter exposes no
    // reflection or arbitrary-property mechanism; native Configuration has no public count getter.
    static int nativeConfigurationListenerCount()throws Exception {
        java.lang.reflect.Field field=Configuration.class.getDeclaredField("listeners");field.setAccessible(true);
        return ((java.util.Set<?>)field.get(config)).size();
    }
    static void checkAbandonedPartDrafts()throws Exception {
        int before=nativeConfigurationListenerCount();
        for(int i=0;i<100;i++) {
            String id="abandoned-part-"+i,packageId="abandoned-package-"+i;
            NativeSettings.stage(config,array(packageChange("create_package",packageId),partChange("create_part",id,packageId)));
            expect("NOT_FOUND",()->NativeSettings.stage(config,array(packageChange("create_package",packageId),partChange("create_part",id,packageId),object("type","assign_feeder","feeder_id","missing","part_id",id))));
            check(config.getPart(id)==null&&config.getPackage(packageId)==null,"abandoned or rejected preview installs neither part nor package");
        }
        check(nativeConfigurationListenerCount()==before,"abandoned and late-invalid package/part plans retain no native ConfigurationListener");
        NativeSettings.Patch patch=NativeSettings.stage(config,array(packageChange("create_package","listener-applied-package"),partChange("create_part","listener-applied-part","listener-applied-package"),object("type","set_part_height","part_id","listener-applied-part","height_mm",0.7),object("type","assign_feeder","feeder_id",tray.getId(),"part_id","listener-applied-part")));
        check(nativeConfigurationListenerCount()==before,"accepted preview also defers listener registration");patch.apply();
        check(nativeConfigurationListenerCount()==before+3,"applied create constructs exactly one native Package (1 listener) and Part (2 listeners)");
        check(tray.getPart()==config.getPart("listener-applied-part"),"deferred native part resolves in later feeder assignment");near(tray.getPart().getHeight().getValue(),0.7,"later staged part edit applies after deferred construction");tray.setPart(null);
        passed.add("100 abandoned and 100 rejected package/part previews retain zero listeners; applied creates materialize once");
    }
    static void checkStrip()throws Exception {
        ReferenceStripFeeder strip=null;for(Feeder f:machine.getFeeders())if(f.getClass()==ReferenceStripFeeder.class){strip=(ReferenceStripFeeder)f;break;}
        check(strip!=null,"default simulator has real strip feeder");final ReferenceStripFeeder target=strip;
        JsonObject c=object("type","set_strip_feeder_geometry","feeder_id",target.getId(),"location",pose(0,0,1,0),"reference_hole",pose(10,10,1,0),"last_hole",pose(30,10,1,0),"part_pitch_mm",4,"hole_pitch_mm",4,"tape_width_mm",8,"hole_diameter_mm",1.5,"reference_hole_to_part_mm",2,"feed_count",3,"max_feed_count",10);
        NativeSettings.apply(config,c);near(target.getPartPitch().getValue(),4,"native strip part pitch");check(target.getFeedCount()==3 && target.getMaxFeedCount()==10,"bounded native strip counters");
        Location pick=target.getPickLocation();check(Double.isFinite(pick.getX()) && Double.isFinite(pick.getY()),"native strip pickup computation remains finite with configured hole geometry");
        c.add("last_hole",pose(10,10,1,0));expect("INVALID_GEOMETRY",()->NativeSettings.validate(config,c));check(target.getLastHoleLocation().getX()==30,"degenerate hole proposal leaves native geometry intact");
        passed.add("strip hole baseline, pitch, pickup and bounded count configuration");
    }
    static void checkTooling()throws Exception {
        JsonArray ids=new JsonArray();ids.add(new JsonPrimitive(tip.getId()));
        NativeSettings.apply(config,object("type","set_nozzle_settings","nozzle_id",nozzle.getId(),"pick_dwell_ms",12,"place_dwell_ms",8,"nozzle_tip_ids",ids));
        NativeSettings.apply(config,object("type","set_nozzle_tip_settings","nozzle_tip_id",tip.getId(),"pick_dwell_ms",3,"place_dwell_ms",4));
        check(nozzle.getPickDwellMilliseconds()==12 && nozzle.getPlaceDwellMilliseconds()==8 && tip.getPickDwellMilliseconds()==3 && tip.getPlaceDwellMilliseconds()==4,"native nozzle and tip dwell remain distinct");
        expect("TOOL_STILL_INSTALLED",()->NativeSettings.validate(config,object("type","set_nozzle_settings","nozzle_id",nozzle.getId(),"pick_dwell_ms",1,"place_dwell_ms",1,"nozzle_tip_ids",new JsonArray())));
        passed.add("native nozzle/tip dwell and installed-tip compatibility guard");
    }
    static void checkStrictInputs()throws Exception {
        expect("UNKNOWN_FIELD",()->NativeSettings.validate(config,object("type","set_machine_speed","speed",0.2,"gcode","G0 X99")));
        JsonObject notFinite=object("type","set_machine_speed","speed",0.2);notFinite.addProperty("speed",Double.NaN);expect("OUT_OF_RANGE",()->NativeSettings.validate(config,notFinite));
        JsonObject fractional=object("type","set_nozzle_tip_settings","nozzle_tip_id",tip.getId(),"pick_dwell_ms",1.5,"place_dwell_ms",0);expect("INVALID_ARGUMENT",()->NativeSettings.validate(config,fractional));
        ReferenceTrayFeeder custom=new UnqualifiedTray();machine.addFeeder(custom);
        try{expect("UNSUPPORTED_NATIVE_CLASS",()->NativeSettings.validate(config,object("type","assign_feeder","feeder_id",custom.getId(),"part_id","settings-part")));}finally{machine.removeFeeder(custom);}
        passed.add("unknown fields, nonfinite/fractional numbers and custom native subclasses are rejected");
    }
    static void checkCameraInvalidation()throws Exception {
        ReferenceCamera camera=(ReferenceCamera)machine.getDefaultHead().getDefaultCamera();
        check(tip.getCalibration().isCalibrated(nozzle),"real native model is valid before camera edit");
        NativeSettings.Patch patch=NativeSettings.stage(config,array(object("type","set_camera_geometry","camera_id",camera.getId(),"units_per_pixel_x_mm",0.04,"units_per_pixel_y_mm",0.05,"working_plane_z_mm",3,"head_offsets",pose(1,2,3,0)),object("type","set_camera_settling","camera_id",camera.getId(),"settle_time_ms",25)));
        check(tip.getCalibration().isCalibrated(nozzle),"planning camera edit does not invalidate a valid model");patch.apply();
        near(camera.getUnitsPerPixelPrimary().getX(),0.04,"native camera UPP X");near(camera.getUnitsPerPixelPrimary().getY(),0.05,"native camera UPP Y");near(camera.getUnitsPerPixelPrimary().getZ(),3,"camera scale working plane");
        near(camera.getHeadOffsets().getX(),1,"native camera offset");check(camera.getSettleTimeMs()==25 && camera.getSettleMethod()==AbstractSettlingCamera.SettleMethod.FixedTime,"explicit fixed-time native settling");
        check(!tip.getCalibration().isCalibrated(nozzle),"camera geometry clears actual native runout model, not just metadata");
        camera.setEnableUnitsPerPixel3D(true);
        try{expect("UNSUPPORTED_MODEL",()->NativeSettings.validate(config,object("type","set_camera_geometry","camera_id",camera.getId(),"units_per_pixel_x_mm",0.04,"units_per_pixel_y_mm",0.05,"working_plane_z_mm",3,"head_offsets",pose(1,2,3,0))));}finally{camera.setEnableUnitsPerPixel3D(false);}
        passed.add("camera edits invalidate a successfully calibrated native nozzle-tip model and reject 3D override");
    }
    static void checkAxis()throws Exception {
        ReferenceControllerAxis axis=null,rotation=null;for(Axis a:machine.getAxes())if(a.getClass()==ReferenceControllerAxis.class){if(a.getType()==Axis.Type.X)axis=(ReferenceControllerAxis)a;if(a.getType()==Axis.Type.Rotation)rotation=(ReferenceControllerAxis)a;}
        check(axis!=null,"default simulator has a real controller X axis");
        JsonObject change=object("type","set_axis_motion_limits","axis_id",axis.getId(),"soft_limit_low_mm",-1000,"soft_limit_high_mm",1000,"feedrate_mm_per_s",100,"acceleration_mm_per_s2",500,"jerk_mm_per_s3",0);
        NativeSettings.apply(config,change);near(axis.getFeedratePerSecond().getValue(),100,"native axis feedrate");check(axis.isSoftLimitLowEnabled() && axis.isSoftLimitHighEnabled(),"both native soft limits enabled");check(!machine.isHomed(),"axis edit revokes native homing confidence");
        change.addProperty("soft_limit_low_mm",1000);expect("INVALID_GEOMETRY",()->NativeSettings.validate(config,change));
        if(rotation!=null){change.addProperty("axis_id",rotation.getId());change.addProperty("soft_limit_low_mm",-1000);expect("UNSUPPORTED_MODEL",()->NativeSettings.validate(config,change));}
        passed.add("linear axis limits/speed persist and revoke homed state; rotational unit mismatch rejected");
    }
    static JsonObject find(JsonObject view,String collection,String key,String id) {
        for(JsonElement entry:view.getAsJsonArray(collection))if(entry.getAsJsonObject().get(key).getAsString().equals(id))return entry.getAsJsonObject();
        throw new AssertionError("Missing native readback: "+collection+" "+id);
    }
    static void checkReadback() throws Exception {
        JsonObject view=GSON.toJsonTree(NativeSettings.describe(config)).getAsJsonObject();
        JsonObject part=find(view,"parts","part_id","settings-part"),pkg=find(view,"packages","package_id","settings-package"),trayView=find(view,"feeders","feeder_id",tray.getId());
        near(part.get("height_mm").getAsDouble(),0.6,"part height readback");check(part.get("package_id").getAsString().equals("settings-package"),"part/package native linkage readback");
        near(pkg.getAsJsonArray("pads").get(0).getAsJsonObject().get("x_mm").getAsDouble(),-0.9,"footprint pad readback in millimeters");
        check(trayView.get("feed_count").getAsInt()==5 && trayView.get("capacity").getAsInt()==12,"native feeder counter readback");
        ReferenceCamera camera=(ReferenceCamera)machine.getDefaultHead().getDefaultCamera();JsonObject cameraView=find(view,"cameras","camera_id",camera.getId());
        near(cameraView.get("units_per_pixel_y_mm").getAsDouble(),0.05,"camera scale readback");check(cameraView.get("settle_time_ms").getAsInt()==25,"fixed-time setting readback");
        JsonObject nozzleView=find(view,"nozzles","nozzle_id",nozzle.getId());
        check(nozzleView.get("pick_dwell_ms").getAsInt()==12 && !nozzleView.get("installed_tip_runout_calibrated").getAsBoolean(),"native tool dwell and invalidation readback");
        check(find(view,"nozzle_tips","nozzle_tip_id",tip.getId()).get("place_dwell_ms").getAsInt()==4,"native tip dwell readback");
        for(JsonElement entry:view.getAsJsonArray("axes")){JsonObject a=entry.getAsJsonObject();if(a.get("axis_type").getAsString().equals("Rotation"))check(!a.has("feedrate_mm_per_s")&&!a.get("linear_motion_settings_editable").getAsBoolean(),"rotational readback cannot mislabel degrees as millimeters");}
        check(!view.get("physical_qualification").getAsBoolean()&&!view.get("machine_homed").getAsBoolean(),"readback retains simulation scope and revoked homing");
        // Exercise native units conversion with an inch footprint, restoring its original unit object afterward.
        Footprint footprint=config.getPackage("settings-package").getFootprint();footprint.setUnits(LengthUnit.Inches);
        try{JsonObject inch=GSON.toJsonTree(NativeSettings.describe(config)).getAsJsonObject();near(find(inch,"packages","package_id","settings-package").get("body_width_mm").getAsDouble(),40.64,"readback converts native footprint inches to millimeters");}finally{footprint.setUnits(LengthUnit.Millimeters);}
        passed.add("read-only setting snapshot covers geometry, tooling, counts and calibration state with native unit conversion");
    }
    static final class UnqualifiedTray extends ReferenceTrayFeeder { }
}
