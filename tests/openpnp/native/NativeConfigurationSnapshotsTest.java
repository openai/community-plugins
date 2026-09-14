/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.camera.ReferenceCamera;
import org.openpnp.machine.reference.feeder.*;
import org.openpnp.spi.*;

/** Actual typed native rollback, with monotonic finite material state and explicit omissions. */
public final class NativeConfigurationSnapshotsTest {
    static final Gson GSON=new Gson();static final List<String> passed=new ArrayList<>();
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static JsonObject object(Object...pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    public static void main(String[]args)throws Exception {
        Configuration.initialize(Files.createTempDirectory("openpnp-native-snapshots-").toFile());Configuration c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);Machine m=c.getMachine();int exit=0;
        try {
            NativeSettings.stage(c,NativeSettingsTest.array(NativeSettingsTest.packageChange("create_package","snapshot-package"),NativeSettingsTest.partChange("create_part","snapshot-part","snapshot-package"))).apply();
            ReferenceTrayFeeder tray=new ReferenceTrayFeeder();tray.setPart(c.getPart("snapshot-part"));tray.setEnabled(true);tray.setTrayCountX(10);tray.setTrayCountY(1);tray.setOffsets(new Location(LengthUnit.Millimeters,2,2,0,0));tray.setFeedCount(2);m.addFeeder(tray);
            ReferenceStripFeeder strip=(ReferenceStripFeeder)m.getFeeders().stream().filter(f->f.getClass()==ReferenceStripFeeder.class).findFirst().orElseThrow();strip.setMaxFeedCount(20);strip.setFeedCount(2);
            ReferenceControllerAxis axis=(ReferenceControllerAxis)m.getAxes().stream().filter(a->a.getType()==Axis.Type.X).findFirst().orElseThrow();
            NativeSettings.apply(c,object("type","set_axis_motion_limits","axis_id",axis.getId(),"soft_limit_low_mm",-500,"soft_limit_high_mm",500,"feedrate_mm_per_s",100,"acceleration_mm_per_s2",500,"jerk_mm_per_s3",0));
            ReferenceCamera camera=(ReferenceCamera)m.getDefaultHead().getDefaultCamera();ReferenceNozzle nozzle=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip tip=(ReferenceNozzleTip)nozzle.getNozzleTip();
            NativeSettings.apply(c,object("type","set_camera_settling","camera_id",camera.getId(),"settle_time_ms",10));
            NativeConfigurationSnapshots.Snapshot snapshot=NativeConfigurationSnapshots.capture(c);
            JsonArray rows=(JsonArray)snapshot.document.get("typed_changes");Set<String> types=new HashSet<>();for(JsonElement row:rows)types.add(row.getAsJsonObject().get("type").getAsString());
            for(String type:Arrays.asList("set_package_footprint","set_package_compatibility","set_part_properties","assign_feeder","set_feeder_enabled","set_tray_feeder_geometry","set_strip_feeder_geometry","set_camera_geometry","set_camera_settling","set_nozzle_settings","set_nozzle_tip_settings","set_axis_motion_limits","set_machine_speed"))check(types.contains(type),"snapshot contains "+type);
            check(!((List<?>)snapshot.document.get("omissions")).isEmpty(),"default unsupported settings are explicitly omitted");passed.add("snapshot captures all thirteen existing typed setting families and identifies nonrepresentable fields");
            double speed=m.getSpeed(),upp=camera.getUnitsPerPixelPrimary().getX();int nozzleDwell=nozzle.getPickDwellMilliseconds(),tipDwell=tip.getPickDwellMilliseconds();
            c.getPart("snapshot-part").setHeight(new Length(3,LengthUnit.Millimeters));c.getPackage("snapshot-package").getFootprint().setBodyWidth(9);m.setSpeed(0.2);camera.setUnitsPerPixelPrimary(new Location(LengthUnit.Millimeters,0.3,0.3,0,0));camera.setSettleTimeMs(200);nozzle.setPickDwellMilliseconds(900);tip.setPickDwellMilliseconds(800);axis.setFeedratePerSecond(new Length(33,LengthUnit.Millimeters));tray.setOffsets(new Location(LengthUnit.Millimeters,3,3,0,0));tray.setFeedCount(5);tray.setEnabled(false);strip.setFeedCount(5);
            NativeSettings.apply(c,NativeSettingsTest.partChange("create_part","later-part","snapshot-package"));
            NativeConfigurationSnapshots.Restore restore=NativeConfigurationSnapshots.prepareRestore(c,snapshot);check(c.getPart("snapshot-part").getHeight().getValue()==3,"restore planning is passive");restore.apply();
            check(c.getPart("snapshot-part").getHeight().getValue()==0.6&&c.getPackage("snapshot-package").getFootprint().getBodyWidth()==1.6,"part and footprint restored");check(m.getSpeed()==speed&&camera.getUnitsPerPixelPrimary().getX()==upp&&camera.getSettleTimeMs()==10,"speed and camera restored");check(nozzle.getPickDwellMilliseconds()==nozzleDwell&&tip.getPickDwellMilliseconds()==tipDwell&&axis.getFeedratePerSecond().getValue()==100,"native tool and axis settings restored");
            check(tray.getOffsets().getX()==2&&tray.getFeedCount()==5&&!tray.isEnabled()&&strip.getFeedCount()==5,"newer consumption and subsequent disable preserved");check(c.getPart("later-part")!=null&&!((Map<?,?>)restore.report.get("added_identities_preserved")).isEmpty(),"later identities remain installed and reported");check(Boolean.FALSE.equals(restore.report.get("job_placed_history_replaced")),"rollback does not replace job history");passed.add("native restore reinstates supported settings while preserving newer counts, disabled consumed feeder and later identities");
            tray.setTrayCountX(20);tray.setFeedCount(15);m.setSpeed(0.4);try{NativeConfigurationSnapshots.prepareRestore(c,snapshot);throw new AssertionError("expected material conflict");}catch(Bridge.Fault e){check(e.code.equals("RESTORE_MATERIAL_CONFLICT"),e.code);}check(m.getSpeed()==0.4&&tray.getFeedCount()==15,"capacity conflict rejects all changes before first effect");passed.add("older capacity below consumed material fails staging without partial rollback");
            m.removeFeeder(tray);try{NativeConfigurationSnapshots.prepareRestore(c,snapshot);throw new AssertionError("expected missing identity");}catch(Bridge.Fault e){check(e.code.equals("RESTORE_IDENTITY_MISSING"),e.code);}passed.add("missing native identity rejects rollback without recreating or deleting hardware models");
            c.save();System.out.println("OPENPNP_NATIVE_SNAPSHOTS_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false,"portable_machine_restore",false)));
        }catch(Throwable e){e.printStackTrace();exit=1;}finally{m.close();}System.exit(exit);
    }
}
