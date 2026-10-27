/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;

/** Real native configuration models and persistence; never enables, homes, feeds or moves. */
public final class PlannerSettingsTest {
    static final Gson GSON=new Gson();static final List<String> passed=new ArrayList<>();
    static Configuration config;static Machine machine;static ReferencePnpJobProcessor processor;
    static ReferenceHead head;static ReferenceStripFeeder feeder;static Part part;
    static JsonObject object(Object... pairs){return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static JsonArray array(JsonObject... rows){JsonArray a=new JsonArray();for(JsonObject r:rows)a.add(r);return a;}
    static JsonObject pose(double x,double y,double z,double rotation){return object("x_mm",x,"y_mm",y,"z_mm",z,"rotation_deg",rotation);}
    static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    static void near(double actual,double expected,String message){check(Double.isFinite(actual)&&Math.abs(actual-expected)<1e-8,message+": "+actual+" != "+expected);}
    @FunctionalInterface interface Action{void run()throws Exception;}
    static void reject(String code,Action action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault error){check(code.equals(error.code),"Expected "+code+", got "+error.code);}}
    static JsonObject planner(String order){return object("type","set_job_planner_settings","job_order",order,"strategy","FullyAsPlanned","optimize_multiple_nozzles",false,"pre_rotate_all_nozzles",false,"stepping_to_next_motion",false);}
    static JsonObject retry(int vision,int placement,int limit,int window){return object("type","set_job_retry_settings","vision_attempts",vision,"placement_attempts",placement,"feeder_fault_limit",limit,"feeder_fault_window",window);}
    static JsonObject park(double x,double y){return object("type","set_head_park_location","head_id",head.getId(),"x_mm",x,"y_mm",y);}
    static JsonObject partRetry(int value){return object("type","set_part_retry_settings","part_id",part.getId(),"pick_retries",value);}
    static JsonObject feederRetry(int feed,int pick){return object("type","set_feeder_retry_settings","feeder_id",feeder.getId(),"feed_retries",feed,"pick_retries",pick);}
    static JsonObject discard(double x,double y,double z,double rotation){return object("type","set_machine_discard_location","location",pose(x,y,z,rotation));}
    static void bind()throws Exception {
        machine=config.getMachine();processor=(ReferencePnpJobProcessor)machine.getPnpJobProcessor();head=(ReferenceHead)machine.getDefaultHead();
        feeder=(ReferenceStripFeeder)machine.getFeeders().stream().filter(f->f.getClass()==ReferenceStripFeeder.class).findFirst().orElseThrow();part=feeder.getPart();
        check(processor.planner.getClass()==ReferencePnpJobProcessor.SimplePnpJobPlanner.class,"fixture uses native Simple planner");
    }
    static String settings(){return GSON.toJson(Bridge.map("order",processor.getJobOrder().name(),"strategy",processor.planner.getStrategy().name(),"multiple",processor.isOptimizeMultipleNozzles(),"rotate",processor.isPreRotateAllNozzles(),"stepping",processor.isSteppingToNextMotion(),"vision",processor.getMaxVisionRetries(),"placement",processor.getMaxPlacementRetries(),"fault_limit",processor.getFeederFaultLimit(),"fault_window",processor.getFeederFaultWindowSize(),"part_retry",part.getPickRetryCount(),"feed_retry",feeder.getFeedRetryCount(),"pick_retry",feeder.getPickRetryCount(),"park",location(head.getParkLocation()),"discard",location(machine.getDiscardLocation())));}
    static Map<String,Object> location(Location p){Location mm=p.convertToUnits(LengthUnit.Millimeters);return Bridge.map("x",mm.getX(),"y",mm.getY(),"z",mm.getZ(),"r",mm.getRotation());}

    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-planner-settings-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();bind();int exit=0;
        try {
            check(!machine.isEnabled()&&!machine.isHomed(),"fixture begins disabled and unhomed");
            atomicValidation();strictTypes();applyAndReadback();snapshotRestore();omissions();
            String expected=settings();String headId=head.getId(),feederId=feeder.getId(),partId=part.getId();
            config.save();check(Files.readString(root.resolve("machine.xml")).contains("job-order=\"Unsorted\""),"public order setter persisted in actual machine XML");
            machine.close();Configuration.initialize(root.toFile());config=Configuration.get();config.load();bind();
            check(head.getId().equals(headId)&&feeder.getId().equals(feederId)&&part.getId().equals(partId),"native reload retains referenced identities");
            check(settings().equals(expected),"Configuration.save/load round-trips every supported new field");
            check(!machine.isEnabled()&&!machine.isHomed(),"settings persistence performs no enable or homing");
            passed.add("actual native Configuration.save/load round-trips planner, retry and operating locations with identities intact");
            System.out.println("OPENPNP_PLANNER_SETTINGS_RESULT "+GSON.toJson(Bridge.map("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false,"motion_performed",false,"feed_operations_performed",0)));
        }catch(Throwable error){error.printStackTrace();exit=1;}finally{machine.close();}System.exit(exit);
    }
    static void atomicValidation()throws Exception {
        String before=settings();JsonObject bad=park(4,5);bad.addProperty("head_id","missing-head");
        reject("NOT_FOUND",()->NativeSettings.stage(config,array(planner("Unsorted"),retry(2,4,2,5),partRetry(2),feederRetry(1,2),discard(40,50,3,90),bad)));
        check(settings().equals(before),"late invalid row must leave all preceding planner/retry/location values unchanged");
        reject("INVALID_RETRY_POLICY",()->NativeSettings.stage(config,array(park(7,8),retry(2,4,7,6))));
        check(settings().equals(before),"cross-field retry policy failure must reject the entire patch");
        passed.add("whole-patch staging rejects late missing targets and invalid retry policies before any model changes");
    }
    static void strictTypes()throws Exception {
        reject("INVALID_ENUM",()->NativeSettings.validate(config,planner("nearest-neighbor-custom")));
        JsonObject customStrategy=planner("Unsorted");customStrategy.addProperty("strategy","eval");reject("INVALID_ENUM",()->NativeSettings.validate(config,customStrategy));
        JsonObject extra=planner("Unsorted");extra.addProperty("planner_class","arbitrary.Class");reject("UNKNOWN_FIELD",()->NativeSettings.validate(config,extra));
        JsonObject wrongBoolean=planner("Unsorted");wrongBoolean.addProperty("pre_rotate_all_nozzles","false");reject("INVALID_ARGUMENT",()->NativeSettings.validate(config,wrongBoolean));
        reject("OUT_OF_RANGE",()->NativeSettings.validate(config,retry(0,4,2,5)));reject("OUT_OF_RANGE",()->NativeSettings.validate(config,retry(2,11,2,5)));
        reject("OUT_OF_RANGE",()->NativeSettings.validate(config,partRetry(6)));reject("OUT_OF_RANGE",()->NativeSettings.validate(config,feederRetry(-1,2)));
        JsonObject fractional=retry(2,4,2,5);fractional.addProperty("vision_attempts",1.5);reject("INVALID_ARGUMENT",()->NativeSettings.validate(config,fractional));
        JsonObject nonfinite=park(1,2);nonfinite.addProperty("x_mm",Double.NaN);reject("OUT_OF_RANGE",()->NativeSettings.validate(config,nonfinite));
        reject("OUT_OF_RANGE",()->NativeSettings.validate(config,discard(1,2,101,0)));
        JsonObject noOpAxis=park(1,2);noOpAxis.addProperty("z_mm",4);reject("UNKNOWN_FIELD",()->NativeSettings.validate(config,noOpAxis));
        PnpJobPlanner previous=processor.planner;processor.planner=new ReferencePnpJobProcessor.TrivialPnpJobPlanner();
        try{reject("UNSUPPORTED_NATIVE_CLASS",()->NativeSettings.validate(config,planner("Unsorted")));reject("UNSUPPORTED_NATIVE_CLASS",()->NativeSettings.validate(config,retry(2,3,2,4)));}finally{processor.planner=previous;}
        for(ReferencePnpJobProcessor.JobOrderHint order:ReferencePnpJobProcessor.JobOrderHint.values())NativeSettings.validate(config,planner(order.name()));
        passed.add("bounded enums, integer/finite ranges, exact Simple planner, booleans and unknown/no-op fields are enforced");
    }
    static void applyAndReadback()throws Exception {
        // Native park ignores Z/rotation; the typed XY edit preserves their stored values exactly.
        head.setParkLocation(new Location(LengthUnit.Inches,1,2,0.5,35));
        ReferenceNozzle nozzle=(ReferenceNozzle)head.getDefaultNozzle();ReferenceNozzleTip tip=(ReferenceNozzleTip)nozzle.getNozzleTip();
        Object calibration=tip.getCalibration();boolean valid=tip.getCalibration().isCalibrated(nozzle),enabled=tip.getCalibration().isEnabled();
        feeder.recordJobFault(100,6,new Exception("Native model history sentinel; no feed or placement performed"));
        int count=feeder.getFeedCount();String faults=feeder.summariseJobFaults();
        JsonObject park=park(31,42),plan=planner("Unsorted");
        NativeSettings.Patch patch=NativeSettings.stage(config,array(plan,retry(2,4,2,5),partRetry(2),feederRetry(1,2),park,discard(50,60,3,90)));
        plan.addProperty("job_order","BoardPart");park.addProperty("x_mm",999);patch.apply();reject("PATCH_ALREADY_APPLIED",patch::apply);
        check(processor.getJobOrder()==ReferencePnpJobProcessor.JobOrderHint.Unsorted&&processor.planner.getStrategy()==PnpJobPlanner.Strategy.FullyAsPlanned,"native planner accepts only previously validated input");
        check(!processor.isOptimizeMultipleNozzles()&&!processor.isPreRotateAllNozzles()&&!processor.isSteppingToNextMotion(),"all explicit public planner flags applied");
        check(processor.getMaxVisionRetries()==2&&processor.getMaxPlacementRetries()==4&&processor.getFeederFaultLimit()==2&&processor.getFeederFaultWindowSize()==5,"attempt limits map directly to native total-attempt fields");
        check(part.getPickRetryCount()==2&&feeder.getFeedRetryCount()==1&&feeder.getPickRetryCount()==2,"extra retry counts map to native part/feeder fields");
        Location p=head.getParkLocation().convertToUnits(LengthUnit.Millimeters);near(p.getX(),31,"park X");near(p.getY(),42,"park Y");near(p.getZ(),12.7,"stored park Z preserved with unit conversion");near(p.getRotation(),35,"stored park rotation preserved");
        Location d=machine.getDiscardLocation().convertToUnits(LengthUnit.Millimeters);near(d.getX(),50,"discard X");near(d.getY(),60,"discard Y");near(d.getZ(),3,"discard Z");near(d.getRotation(),90,"discard rotation");
        check(feeder.getFeedCount()==count&&feeder.summariseJobFaults().equals(faults),"setting retry budgets cannot consume or reset material/fault history");
        check(tip.getCalibration()==calibration&&tip.getCalibration().isCalibrated(nozzle)==valid&&tip.getCalibration().isEnabled()==enabled,"planner/retry/route settings preserve unrelated native calibration model and state");
        JsonObject view=GSON.toJsonTree(NativeSettings.describe(config)).getAsJsonObject();
        check(view.getAsJsonObject("job_processor").get("job_order").getAsString().equals("Unsorted"),"readback reports actual native order");
        check(view.getAsJsonArray("heads").get(0).getAsJsonObject().getAsJsonObject("park_location").get("z_mm").getAsDouble()==12.7,"readback explicitly exposes stored-but-ignored park axes");
        check(patch.metadata().get(1).get("count_semantics").toString().contains("first attempt"),"attempt/retry semantics remain visible");
        check(Boolean.FALSE.equals(patch.metadata().get(5).get("physical_qualification")),"route setting is not collision or hardware qualification");
        passed.add("native setters/readback preserve staged input, park ignored axes, calibration models and material/fault history");
    }
    static void snapshotRestore()throws Exception {
        NativeConfigurationSnapshots.Snapshot snapshot=NativeConfigurationSnapshots.capture(config);JsonArray changes=(JsonArray)snapshot.document.get("typed_changes");Set<String> types=new HashSet<>();
        for(JsonElement row:changes)types.add(row.getAsJsonObject().get("type").getAsString());
        for(String type:Arrays.asList("set_job_planner_settings","set_job_retry_settings","set_part_retry_settings","set_feeder_retry_settings","set_head_park_location","set_machine_discard_location"))check(types.contains(type),"snapshot represents "+type);
        check(((Map<?,?>)snapshot.document.get("identity_inventory")).containsKey("heads"),"snapshot binds operating locations to existing head identities");
        NativeSettings.stage(config,array(planner("BoardPart"),retry(3,5,3,6),partRetry(0),feederRetry(0,0),park(99,88),discard(1,2,4,10))).apply();
        feeder.recordJobSuccess(6);
        int feedBefore=feeder.getFeedCount();String faultsBefore=feeder.summariseJobFaults();
        NativeConfigurationSnapshots.Restore restore=NativeConfigurationSnapshots.prepareRestore(config,snapshot);
        check("Unsorted".equals(restore.report.get("restored_job_order")),"restore reports proposed order before effects so the bridge can enforce profile constraints");
        check(processor.getJobOrder()==ReferencePnpJobProcessor.JobOrderHint.BoardPart,"restore preparation is passive");restore.apply();
        check(processor.getJobOrder()==ReferencePnpJobProcessor.JobOrderHint.Unsorted&&processor.getMaxVisionRetries()==2&&processor.getMaxPlacementRetries()==4,"restore includes actual planner/attempt limits");
        check(part.getPickRetryCount()==2&&feeder.getFeedRetryCount()==1&&feeder.getPickRetryCount()==2,"restore includes part/feeder retry values");
        near(head.getParkLocation().convertToUnits(LengthUnit.Millimeters).getX(),31,"restored park X");near(machine.getDiscardLocation().getX(),50,"restored discard X");
        check(feeder.getFeedCount()==feedBefore&&feeder.summariseJobFaults().equals(faultsBefore),"restore preserves current material and fault history");
        feeder.setEnabled(false);NativeConfigurationSnapshots.Restore faultRestore=NativeConfigurationSnapshots.prepareRestore(config,snapshot);faultRestore.apply();
        check(!feeder.isEnabled()&&feeder.summariseJobFaults().equals(faultsBefore),"restoring an earlier enabled flag cannot re-enable a currently fault-disabled feeder or erase its observations");
        check(GSON.toJson(faultRestore.report.get("material_state_preserved")).contains("disabled-with-current-native-fault-history"),"preserved fault-disabled state has an explicit reason");
        check(Boolean.FALSE.equals(restore.report.get("physical_state_restored"))&&Boolean.FALSE.equals(restore.report.get("calibration_evidence_restored")),"restore reports limited scope");
        passed.add("typed snapshot capture/restore includes all six new families and preserves current material/fault state");
    }
    static void omissions()throws Exception {
        PnpJobPlanner previous=processor.planner;processor.planner=new ReferencePnpJobProcessor.TrivialPnpJobPlanner();
        try{NativeConfigurationSnapshots.Snapshot s=NativeConfigurationSnapshots.capture(config);String omitted=GSON.toJson(s.document.get("omissions"));check(omitted.contains("set_job_planner_settings")&&omitted.contains("set_job_retry_settings")&&omitted.contains("UNSUPPORTED_NATIVE_CLASS"),"custom planner is explicitly omitted, not claimed restored");}finally{processor.planner=previous;}
        int before=processor.getFeederFaultLimit();processor.setFeederFaultLimit(0);
        try{NativeConfigurationSnapshots.Snapshot s=NativeConfigurationSnapshots.capture(config);String omitted=GSON.toJson(s.document.get("omissions"));check(omitted.contains("set_job_retry_settings")&&omitted.contains("OUT_OF_RANGE"),"native fault-disable value outside bounded policy is explicitly omitted");}finally{processor.setFeederFaultLimit(before);}
        passed.add("unsupported planner and out-of-schema native retry values produce explicit snapshot omissions");
    }
}
