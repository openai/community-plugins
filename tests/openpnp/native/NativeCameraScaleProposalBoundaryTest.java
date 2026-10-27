/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.spi.MotionPlanner;

/** Native fixture with an explicitly unsupported typed-apply Z range.
 * The measurement can be valid while its preserved geometry is not representable by the public setter. */
public final class NativeCameraScaleProposalBoundaryTest {
    public static void main(String[] args)throws Exception {
        NativeCameraScaleBridgeTest.root=Files.createTempDirectory("openpnp-camera-scale-proposal-");int exit=1;
        try {
            NativeCameraScaleBridgeTest.setup("positive",Path.of(args[0]));
            NativeCameraScaleBridgeTest.machine.submit(()->{
                var camera=NativeCameraScaleBridgeTest.camera;
                camera.setHeadOffsets(camera.getHeadOffsets().convertToUnits(LengthUnit.Millimeters).derive(null,null,150.,null));
                camera.setUnitsPerPixelPrimary(camera.getUnitsPerPixelPrimary().convertToUnits(LengthUnit.Millimeters).derive(null,null,150.,null));
                camera.moveTo(new Location(LengthUnit.Millimeters,40,40,150,0),.2);
                NativeCameraScaleBridgeTest.machine.getMotionPlanner().waitForCompletion(camera,MotionPlanner.CompletionType.WaitForStillstand);return null;
            },null,true).get(10,TimeUnit.SECONDS);NativeCameraScaleBridgeTest.idle();
            String revision=NativeCameraScaleBridgeTest.revision();
            var op=NativeCameraScaleBridgeTest.operation("openpnp_run_calibration",NativeCameraScaleBridgeTest.command("recipe_id","camera-planar-scale","camera_id",NativeCameraScaleBridgeTest.camera.getId(),"displacement_mm",1,"expected_feature_diameter_px",40));
            var result=NativeCameraScaleBridgeTest.result(op);
            NativeCameraScaleBridgeTest.check("succeeded".equals(op.get("state")),"Measurement completes through native wrapper");
            NativeCameraScaleBridgeTest.check("accepted".equals(result.get("measurement_status")),"Image measurement still accepted");
            NativeCameraScaleBridgeTest.check("unavailable".equals(result.get("configuration_proposal_status")),"Out-of-range preserved geometry prevents apply proposal");
            NativeCameraScaleBridgeTest.check(!result.containsKey("proposed_configuration_change")&&!result.containsKey("application"),"No unstageable proposal or apply instructions");
            NativeCameraScaleBridgeTest.check("OUT_OF_RANGE".equals(((Map<?,?>)result.get("configuration_proposal_error")).get("code")),"Existing typed validator supplies refusal");
            NativeCameraScaleBridgeTest.check(revision.equals(NativeCameraScaleBridgeTest.revision())&&NativeCameraScaleBridgeTest.camera.getUnitsPerPixelPrimary().getZ()==150,"Measurement preserves source plane and revision");
            NativeCameraScaleBridgeTest.check(NativeCameraScaleBridgeTest.events("camera_scale_observation")==8,"All measured image evidence retained");
            Files.writeString(NativeCameraScaleBridgeTest.root.resolve("operation.json"),NativeCameraScaleBridgeTest.JSON.toJson(op));exit=0;
        }catch(Throwable failure){failure.printStackTrace();}
        finally {
            if(NativeCameraScaleBridgeTest.bridge!=null)NativeCameraScaleBridgeTest.bridge.close();
            if(NativeCameraScaleBridgeTest.machine!=null)NativeCameraScaleBridgeTest.machine.close();
            System.out.println("OPENPNP_CAMERA_SCALE_PROPOSAL_RESULT "+NativeCameraScaleBridgeTest.JSON.toJson(Bridge.map("passed",exit==0,"checks",NativeCameraScaleBridgeTest.checks,"fixture_only_extended_plane_z_mm",150,"physical_qualification",false,"root",NativeCameraScaleBridgeTest.root.toString())));
        }
        System.exit(exit);
    }
}
