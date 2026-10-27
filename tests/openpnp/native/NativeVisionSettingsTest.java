/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.vision.*;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;
import org.openpnp.vision.pipeline.*;
import org.openpnp.vision.pipeline.stages.*;
import org.opencv.core.*;

/** Actual native configuration and OpenCV scalar pipeline fixtures; no camera, motion, or feed. */
public final class NativeVisionSettingsTest {
    static Configuration config; static Part part; static org.openpnp.model.Package pkg;
    static final Gson GSON=new Gson(); static final List<String> passed=new ArrayList<>();
    static JsonObject object(Object... pairs){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return GSON.toJsonTree(m).getAsJsonObject();}
    static JsonArray array(JsonObject... rows){JsonArray a=new JsonArray();for(JsonObject r:rows)a.add(r);return a;}
    static void check(boolean c,String message){if(!c)throw new AssertionError(message);}
    interface Action {void run()throws Exception;}
    static void reject(String code,Action action)throws Exception {try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(e.code.equals(code),"Expected "+code+", got "+e.code+": "+e.getMessage());}}
    static JsonObject clone(String source,String id){return object("type","clone_vision_settings","source_vision_settings_id",source,"vision_settings_id",id,"name","Fixture "+id);}
    static JsonObject assign(String holder,String kind,String id){JsonObject c=object("type","assign_vision_settings","holder",holder,"kind",kind);c.add("vision_settings_id",id==null?JsonNull.INSTANCE:new JsonPrimitive(id));return c;}
    static JsonObject remove(String id){return object("type","remove_vision_settings","vision_settings_id",id);}
    static JsonObject bottom(String id){return object("type","set_bottom_vision_settings","vision_settings_id",id,"enabled",true,"pre_rotate_usage","AlwaysOff","part_size_check","BodySize","size_tolerance_percent",15,"max_rotation","Full","asymmetric",true,"offset_x_mm",0.12,"offset_y_mm",-0.08);}
    static JsonObject fiducial(String id){return object("type","set_fiducial_vision_settings","vision_settings_id",id,"enabled",true,"max_vision_passes",4,"max_linear_offset_mm",0.15,"parallax_diameter_mm",0.7,"parallax_angle_deg",30);}
    static String snapshot()throws Exception{return GSON.toJson(NativeVisionSettings.describe(config));}
    static void apply(JsonObject... rows)throws Exception{NativeVisionSettings.stage(config,array(rows)).apply();}

    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-vision-settings-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();
        // The pinned upstream adds its stock body profile on the second load; begin the round-trip fixture after that native migration.
        config.save();config.getMachine().close();Configuration.initialize(root.toFile());config=Configuration.get();config.load();int exit=0;
        try {
            part=config.getPart("R0805-1K");check(part!=null,"Native sample part exists");pkg=part.getPackage();
            ReferenceStripFeeder feeder=(ReferenceStripFeeder)config.getMachine().getFeeders().stream().filter(f->f.getClass()==ReferenceStripFeeder.class).findFirst().orElseThrow();int feeds=feeder.getFeedCount();
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Fixture begins disabled and unhomed");
            String before=snapshot();check(snapshot().equals(before),"Repeated read is stable");
            JsonObject view=GSON.fromJson(before,JsonObject.class);check(view.getAsJsonArray("profiles").size()>0,"Profiles exposed");
            ReferenceBottomVision nativeBottom=(ReferenceBottomVision)config.getMachine().getPartAlignments().get(0);
            check(nativeBottom.getInheritedVisionSettings(part)!=null,"Native inherited profile resolves");
            passed.add("read-only native machine/package/part inheritance inventory");

            reject("NOT_FOUND",()->NativeVisionSettings.stage(config,array(clone("BVS_Stock","BVS_Test"),bottom("missing"))));
            check(snapshot().equals(before)&&config.getVisionSettings("BVS_Test")==null,"Late invalid row is atomic");
            reject("STOCK_SETTINGS_IMMUTABLE",()->NativeVisionSettings.stage(config,array(bottom("BVS_Stock"))));
            JsonObject unknown=clone("BVS_Stock","BVS_Test");unknown.addProperty("xml","<script/>");reject("UNKNOWN_FIELD",()->NativeVisionSettings.stage(config,array(unknown)));
            reject("INVALID_ARGUMENT",()->NativeVisionSettings.stage(config,array(clone("BVS_Stock","Stock_Escape"))));
            passed.add("whole-patch validation, stock immutability and unknown-field rejection");

            NativeVisionSettings.Patch patch=NativeVisionSettings.stage(config,array(clone("BVS_Stock","BVS_Test"),bottom("BVS_Test"),assign("package:"+pkg.getId(),"bottom","BVS_Test"),assign("part:"+part.getId(),"bottom",null)));
            check(config.getVisionSettings("BVS_Test")==null,"Preview creates no native profile");
            check(patch.metadata().toString().contains("part:"+part.getId()),"Preview includes effective part impact");
            patch.apply();reject("PATCH_ALREADY_APPLIED",patch::apply);
            BottomVisionSettings settings=(BottomVisionSettings)config.getVisionSettings("BVS_Test");
            check(nativeBottom.getInheritedVisionSettings(part)==settings,"Native part inherits edited package profile");
            check(settings.getCheckSizeTolerancePercent()==15&&settings.getPreRotateUsage()==ReferenceBottomVision.PreRotateUsage.AlwaysOff,"Native scalar setters applied");
            check(settings.getVisionOffset().getX()==0.12&&settings.getVisionOffset().getY()==-0.08,"Native offsets preserved");
            check(settings.getPipeline()!=config.getVisionSettings("BVS_Stock").getPipeline(),"Pipeline clone is independent");
            JsonObject modified=bottom("BVS_Test");NativeVisionSettings.Patch immutable=NativeVisionSettings.stage(config,array(modified));modified.addProperty("offset_x_mm",40);immutable.apply();
            check(settings.getVisionOffset().getX()==0.12,"Caller JSON cannot change a staged plan");
            passed.add("native clone/assignment, exact inheritance, shared impact and immutable reviewed input");

            NativeVisionSettings.Patch stale=NativeVisionSettings.stage(config,array(bottom("BVS_Test")));settings.setName("Local edit");
            reject("STALE_MODEL",stale::apply);check(settings.getName().equals("Local edit"),"Stale patch does not overwrite local edit");
            reject("INVALID_VISION_REFERENCE",()->NativeVisionSettings.stage(config,array(remove("BVS_Test"))));
            reject("INVALID_VISION_REFERENCE",()->NativeVisionSettings.stage(config,array(assign("part:"+part.getId(),"fiducial","BVS_Test"))));
            reject("INVALID_VISION_REFERENCE",()->NativeVisionSettings.stage(config,array(assign("machine:bottom","bottom",null))));
            reject("ALREADY_EXISTS",()->NativeVisionSettings.stage(config,array(clone("BVS_Stock","bvs_test"))));
            apply(clone("BVS_Stock","BVS_Replacement"));
            reject("ALREADY_EXISTS",()->NativeVisionSettings.stage(config,array(remove("BVS_Replacement"),clone("BVS_Stock","bvs_replacement"))));
            check(config.getVisionSettings("BVS_Replacement")!=null,"Rejected case-variant replacement preserves original identity");
            apply(remove("BVS_Replacement"));
            passed.add("stale native edit, referenced removal, kind mismatch, root and identity collisions reject");

            apply(clone("FVS_Stock","FVS_Test"),fiducial("FVS_Test"),assign("part:"+part.getId(),"fiducial","FVS_Test"));
            FiducialVisionSettings fs=(FiducialVisionSettings)config.getVisionSettings("FVS_Test");
            check(((ReferenceFiducialLocator)config.getMachine().getFiducialLocator()).getInheritedVisionSettings(part)==fs,"Native fiducial inheritance");
            check(fs.getMaxVisionPasses()==4&&fs.getMaxLinearOffset().getValue()==0.15&&fs.getParallaxDiameter().getValue()==0.7&&fs.getParallaxAngle()==30,"Native fiducial scalars applied");
            apply(assign("part:"+part.getId(),"fiducial",null),remove("FVS_Test"));check(config.getVisionSettings("FVS_Test")==null,"Explicit detach then unused delete");
            passed.add("fiducial configuration and explicit unused profile deletion");

            pipelineFixtures(settings);
            check(feeder.getFeedCount()==feeds&&!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Vision configuration and image fixtures consume no native material or movement");
            String expected=snapshot();String id=part.getId();config.save();config.getMachine().close();Configuration.initialize(root.toFile());config=Configuration.get();config.load();part=config.getPart(id);pkg=part.getPackage();
            String observed=snapshot();if(!observed.equals(expected)){Files.writeString(root.resolve("vision-before.json"),expected);Files.writeString(root.resolve("vision-after.json"),observed);System.out.println("VISION_ROUNDTRIP_DIFFERENCE "+root);}
            check(observed.equals(expected),"Actual native save/load round-trips settings, pipeline and assignment");
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Persistence retains disabled/unhomed state");
            passed.add("native Configuration.save/load round-trip with no motion or feeds");
            System.out.println("OPENPNP_VISION_SETTINGS_RESULT "+GSON.toJson(object("passed",passed,"upstream_commit",Bridge.UPSTREAM,"simulation_only",true,"physical_qualification",false,"camera_capture_performed",false,"feed_operations_performed",0,"image_evidence","synthetic scalar OpenCV fixtures; no detector accuracy or independent inspection claim")));
        }catch(Throwable e){e.printStackTrace();exit=1;}finally{config.getMachine().close();}System.exit(exit);
    }
    static void pipelineFixtures(BottomVisionSettings settings)throws Exception {
        CvStage gaussian=settings.getPipeline().getStages().stream().filter(s->s.getClass()==BlurGaussian.class).findFirst().orElseThrow();
        CvStage threshold=settings.getPipeline().getStages().stream().filter(s->s.getClass()==Threshold.class).findFirst().orElseThrow();
        JsonObject g=object("type","set_vision_pipeline_stage","vision_settings_id","BVS_Test","stage_name",gaussian.getName(),"stage_type","gaussian","parameters",object("kernel_size",5));
        JsonObject t=object("type","set_vision_pipeline_stage","vision_settings_id","BVS_Test","stage_name",threshold.getName(),"stage_type","threshold","parameters",object("threshold",128,"auto",false,"invert",false));
        reject("VISION_PARAMETER_OVERRIDDEN",()->NativeVisionSettings.stage(config,array(g)));
        reject("VISION_PARAMETER_OVERRIDDEN",()->NativeVisionSettings.stage(config,array(t)));
        // Prepare an explicitly uncontrolled native model fixture. The plugin does not offer these topology edits.
        ((BlurGaussian)gaussian).setPropertyName("");
        for(CvStage stage:new ArrayList<>(settings.getPipeline().getStages())) if(stage instanceof CvAbstractParameterStage && threshold.getName().equals(((CvAbstractParameterStage)stage).getStageName())) settings.getPipeline().remove(stage);
        apply(g,t);check(((BlurGaussian)settings.getPipeline().getStage(gaussian.getName())).getKernelSize()==5,"Exact existing gaussian stage edited");
        JsonObject bad=GSON.fromJson(g.toString(),JsonObject.class);bad.getAsJsonObject("parameters").addProperty("kernel_size",4);String before=snapshot();reject("INVALID_ARGUMENT",()->NativeVisionSettings.stage(config,array(t,bad)));check(snapshot().equals(before),"Invalid kernel cannot silently round or partially update");
        JsonObject wrong=GSON.fromJson(t.toString(),JsonObject.class);wrong.addProperty("stage_type","gaussian");reject("UNSUPPORTED_SETTING",()->NativeVisionSettings.stage(config,array(wrong)));
        byte[] input={0,127,(byte)128,(byte)255};Threshold actual=(Threshold)settings.getPipeline().getStage(threshold.getName());
        Mat image=new Mat(1,4,CvType.CV_8UC1);image.put(0,0,input);
        try(CvPipeline fixture=new CvPipeline()) {
            fixture.add("fixture-input",new SetResult(image,null));fixture.add(actual.getName(),actual);fixture.process();
            byte[] result=new byte[4];fixture.getWorkingImage().get(0,0,result);check(Arrays.equals(result,new byte[]{0,0,0,(byte)255}),"Actual edited native Threshold separates synthetic intensity boundary");
        }finally{image.release();}
        t.getAsJsonObject("parameters").addProperty("invert",true);apply(t);actual=(Threshold)settings.getPipeline().getStage(threshold.getName());
        image=new Mat(1,4,CvType.CV_8UC1);image.put(0,0,input);
        try(CvPipeline fixture=new CvPipeline()) {
            fixture.add("fixture-input",new SetResult(image,null));fixture.add(actual.getName(),actual);fixture.process();
            byte[] result=new byte[4];fixture.getWorkingImage().get(0,0,result);check(Arrays.equals(result,new byte[]{(byte)255,(byte)255,(byte)255,0}),"Native inversion flips exact fixture mask");
        }finally{image.release();}
        passed.add("allowlisted pipeline scalar edits, rejection boundaries and actual OpenCV positive/negative mask fixture");
    }
}
