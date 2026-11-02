/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.StringWriter;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;
import org.openpnp.machine.reference.vision.*;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;
import org.openpnp.vision.pipeline.*;
import org.openpnp.vision.pipeline.stages.*;

/** Independent review regressions using native objects and native parameter setters, with no pipeline execution. */
public final class NativeVisionSettingsReviewTest {
    private static final Gson JSON = new Gson();
    private static final String BOTTOM = "BVS_Review", FIDUCIAL = "FVS_Review", BOOLEAN = "BVS_ReviewBool";
    private static Configuration config;
    private static Part part;
    private static org.openpnp.model.Package pkg;
    private static int assertions, numericApplications, booleanApplications;
    private static final List<String> groups = new ArrayList<>();
    private static final List<String> rejected = new ArrayList<>();
    private static int identity;

    interface Checked { void run() throws Exception; }
    static void check(boolean condition, String message) {
        assertions++;
        if (!condition) throw new AssertionError(message);
    }
    static void reject(String code, String label, Checked action) throws Exception {
        try { action.run(); throw new AssertionError(label + ": expected " + code); }
        catch (Bridge.Fault fault) {
            check(code.equals(fault.code), label + ": expected " + code + ", got " + fault.code + ": " + fault.getMessage());
            rejected.add(label + ":" + code);
        }
    }
    static JsonObject object(Object... pairs) {
        Map<String,Object> map = new LinkedHashMap<>();
        for (int i=0; i<pairs.length; i+=2) map.put((String)pairs[i], pairs[i+1]);
        return JSON.toJsonTree(map).getAsJsonObject();
    }
    static JsonArray rows(JsonObject... values) { JsonArray rows=new JsonArray(); for(JsonObject v:values)rows.add(v); return rows; }
    static JsonObject cloneRow(String source, String target) { return object("type","clone_vision_settings","source_vision_settings_id",source,"vision_settings_id",target,"name","Review "+target); }
    static void apply(JsonObject... changes) throws Exception { NativeVisionSettings.stage(config, rows(changes)).apply(); }
    static String xml(Object value) throws Exception {
        // CvPipeline.toXmlString() resets native parameters. This deliberately uses the nonmutating serializer.
        StringWriter writer=new StringWriter(); Configuration.createSerializer().write(value,writer); return writer.toString();
    }
    static AbstractVisionSettings detached(AbstractVisionSettings original) throws Exception {
        return Configuration.createSerializer().read(original.getClass(),xml(original));
    }
    static JsonObject describe() throws Exception { return JSON.toJsonTree(NativeVisionSettings.describe(config)).getAsJsonObject(); }
    static JsonObject profile(JsonObject view,String id) {
        for(JsonElement raw:view.getAsJsonArray("profiles"))if(id.equals(raw.getAsJsonObject().get("vision_settings_id").getAsString()))return raw.getAsJsonObject();
        throw new AssertionError("Missing profile "+id);
    }
    static JsonObject stage(JsonObject profile,String name) {
        for(JsonElement raw:profile.getAsJsonArray("stages"))if(name.equals(raw.getAsJsonObject().get("name").getAsString()))return raw.getAsJsonObject();
        throw new AssertionError("Missing stage "+name);
    }
    static JsonObject parameter(JsonObject profile,String name) {
        for(JsonElement raw:profile.getAsJsonArray("pipeline_parameters"))if(name.equals(raw.getAsJsonObject().get("parameter_name").getAsString()))return raw.getAsJsonObject();
        throw new AssertionError("Missing parameter "+name);
    }
    static JsonObject bottomRow() { return object("type","set_bottom_vision_settings","vision_settings_id",BOTTOM,"enabled",true,"pre_rotate_usage","AlwaysOff","part_size_check","BodySize","size_tolerance_percent",13,"max_rotation","Full","asymmetric",true,"offset_x_mm",0.125,"offset_y_mm",-0.0625); }
    static JsonObject fiducialRow() { return object("type","set_fiducial_vision_settings","vision_settings_id",FIDUCIAL,"enabled",false,"max_vision_passes",5,"max_linear_offset_mm",0.4,"parallax_diameter_mm",1.5,"parallax_angle_deg",-30); }
    static JsonObject parameterRow(String id,String name,Object value) {
        JsonObject row=object("type","set_vision_parameter","vision_settings_id",id,"parameter_name",name);
        row.add("value",value==null?JsonNull.INSTANCE:JSON.toJsonTree(value)); return row;
    }

    public static void main(String[] args) throws Exception {
        Path fixture=Files.createTempDirectory("openpnp-vision-review-");
        Configuration.initialize(fixture.toFile());config=Configuration.get();config.load();
        int exit=0;
        try {
            part=config.getPart("R0805-1K");check(part!=null,"Pinned native default sample part exists");pkg=part.getPackage();
            Map<String,Integer> initialFeeds=feedCounts();Map<String,String> initialTips=tipIds();
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Native fixture begins disabled/unhomed");
            apply(cloneRow("BVS_Stock",BOTTOM),cloneRow("FVS_Stock",FIDUCIAL));
            detachedSettings();detachedPackages();readOnlyDescription();parameterOverrides();fullReadback();
            check(feedCounts().equals(initialFeeds),"All native strip feeder counters remain unchanged");
            check(tipIds().equals(initialTips),"Every installed nozzle-tip identity remains unchanged");
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"All model tests retain disabled/unhomed state");
            System.out.println("OPENPNP_VISION_REVIEW_RESULT "+JSON.toJson(object("assertions",assertions,"groups",groups,"expected_rejections",rejected,"native_numeric_parameter_applications",numericApplications,"native_boolean_parameter_applications",booleanApplications,"pipeline_process_calls",0,"camera_capture_calls",0,"motion_calls",0,"feed_calls",0,"native_upstream_commit",Bridge.UPSTREAM,"physical_qualification",false)));
        } catch(Throwable error) { error.printStackTrace();exit=1; }
        finally { config.getMachine().close(); }
        System.exit(exit);
    }
    static Map<String,Integer> feedCounts() {
        Map<String,Integer> values=new TreeMap<>();
        for(org.openpnp.spi.Feeder f:config.getMachine().getFeeders())if(f.getClass()==ReferenceStripFeeder.class)values.put(f.getId(),((ReferenceStripFeeder)f).getFeedCount());
        return values;
    }
    static Map<String,String> tipIds() {
        Map<String,String> values=new TreeMap<>();
        for(org.openpnp.spi.Head h:config.getMachine().getHeads())for(org.openpnp.spi.Nozzle n:h.getNozzles())values.put(n.getId(),n.getNozzleTip()==null?null:n.getNozzleTip().getId());
        return values;
    }

    static void detachedSettings() throws Exception {
        PartSettingsHolder bottomRoot=(PartSettingsHolder)config.getMachine().getPartAlignments().get(0);
        PartSettingsHolder fiducialRoot=(PartSettingsHolder)config.getMachine().getFiducialLocator();
        for(boolean fiducial:new boolean[]{false,true}) {
            for(PartSettingsHolder holder:Arrays.asList(part,pkg,fiducial?fiducialRoot:bottomRoot)) {
                AbstractVisionSettings registered=config.getVisionSettings(fiducial?FIDUCIAL:BOTTOM);
                AbstractVisionSettings previous=fiducial?holder.getFiducialVisionSettings():holder.getBottomVisionSettings();
                String label=(fiducial?"fiducial":"bottom")+"/"+holder.getClass().getSimpleName();
                for(boolean afterStage:new boolean[]{false,true}) {
                    String newId="BVS_Reject"+(++identity);String before=xml(registered);
                    AbstractVisionSettings copy=detached(registered);
                    check(copy!=registered&&xml(copy).equals(before),label+": detached clone has identical serialized identity/values");
                    NativeVisionSettings.Patch patch=afterStage?NativeVisionSettings.stage(config,rows(cloneRow(BOTTOM,newId))):null;
                    try {
                        set(holder,fiducial,copy);
                        if(afterStage)reject("INVALID_VISION_REFERENCE",label+"/post-stage",patch::apply);
                        else reject("INVALID_VISION_REFERENCE",label+"/admission",()->NativeVisionSettings.stage(config,rows(cloneRow(BOTTOM,newId))));
                        check(config.getVisionSettings(newId)==null&&xml(registered).equals(before),label+": rejection precedes profile publication/setters");
                        check((fiducial?holder.getFiducialVisionSettings():holder.getBottomVisionSettings())==copy,label+": adapter does not repair or replace the detached reference silently");
                    } finally { set(holder,fiducial,previous); }
                }
            }
        }
        groups.add("same-ID detached bottom/fiducial references on part, package and machine roots reject at admission and after preview");
    }
    static void set(PartSettingsHolder holder,boolean fiducial,AbstractVisionSettings value) {
        if(fiducial)holder.setFiducialVisionSettings((FiducialVisionSettings)value);else holder.setBottomVisionSettings((BottomVisionSettings)value);
    }
    static void detachedPackages() throws Exception {
        for(boolean afterStage:new boolean[]{false,true}) {
            String newId="BVS_Reject"+(++identity);
            NativeVisionSettings.Patch patch=afterStage?NativeVisionSettings.stage(config,rows(cloneRow(BOTTOM,newId))):null;
            org.openpnp.model.Package copy=new org.openpnp.model.Package(pkg.getId());
            try {
                part.setPackage(copy);
                reject("INVALID_VISION_REFERENCE","detached-package/"+(afterStage?"post-stage":"admission"),()->{if(afterStage)patch.apply();else NativeVisionSettings.stage(config,rows(cloneRow(BOTTOM,newId)));});
                check(config.getVisionSettings(newId)==null,"Detached package rejected before publishing the planned profile");
                check(part.getPackage()==copy&&config.getPackage(pkg.getId())==pkg,"Detached package neither silently rebound nor registered");
            } finally { part.setPackage(pkg); }
        }
        groups.add("same-ID detached Part.package admission and post-preview swap reject without repair");
    }

    static void readOnlyDescription() throws Exception {
        BottomVisionSettings settings=(BottomVisionSettings)config.getVisionSettings(BOTTOM);
        CvPipeline pipeline=settings.getPipeline();Threshold threshold=(Threshold)pipeline.getStage("threshold");
        ParameterNumeric parameter=(ParameterNumeric)pipeline.getStage("pThreshold");
        check(((Number)parameter.defaultParameterValue()).intValue()==100,"Pinned stock pThreshold has native default100");
        threshold.setThreshold(150);
        Map<String,String> originals=new TreeMap<>();Map<String,CvPipeline> pipelineObjects=new HashMap<>();
        for(AbstractVisionSettings s:config.getVisionSettings()){originals.put(s.getId(),xml(s));pipelineObjects.put(s.getId(),s.getPipeline());}
        JsonObject first=describe(),second=describe();
        check(first.equals(second),"Repeated describe values remain stable without a model reset");
        check(threshold.getThreshold()==150,"Describe preserves a live scalar targeted by a native ParameterNumeric");
        check(settings.getPipeline()==pipeline&&pipeline.getStage("threshold")==threshold&&pipeline.getStage("pThreshold")==parameter,"Describe preserves profile pipeline and native stage identities");
        for(AbstractVisionSettings s:config.getVisionSettings()) {
            check(originals.get(s.getId()).equals(xml(s)),"Describe preserves exact serialized profile "+s.getId());
            check(pipelineObjects.get(s.getId())==s.getPipeline(),"Describe preserves pipeline identity "+s.getId());
        }
        JsonObject view=profile(first,BOTTOM);
        check(stage(view,"threshold").getAsJsonObject("parameters").get("threshold").getAsInt()==150,"Typed stage readback is the retained live scalar");
        check(!stage(view,"threshold").get("parameter_editable").getAsBoolean(),"Overridden threshold is not advertised as directly editable");
        JsonObject exposed=parameter(view,"pThreshold");
        check(exposed.get("default_value").getAsInt()==100&&exposed.get("value_editable").getAsBoolean(),"Native numeric default and explicit assignment path are exposed");
        threshold.setThreshold(100);
        groups.add("describe preserves all native profile XML, pipeline/stage identities and parameter-targeted live scalar");
    }

    static void parameterOverrides() throws Exception {
        BottomVisionSettings settings=(BottomVisionSettings)config.getVisionSettings(BOTTOM);
        String before=xml(settings);
        JsonObject direct=object("type","set_vision_pipeline_stage","vision_settings_id",BOTTOM,"stage_name","threshold","stage_type","threshold","parameters",object("threshold",150,"auto",false,"invert",false));
        reject("VISION_PARAMETER_OVERRIDDEN","direct active parameter target",()->NativeVisionSettings.stage(config,rows(direct)));
        check(xml(settings).equals(before),"Rejected direct threshold edit leaves native configuration unchanged");
        CvStage gaussian=settings.getPipeline().getStages().stream().filter(s->s.getClass()==BlurGaussian.class).findFirst().orElseThrow();
        check(!((BlurGaussian)gaussian).getPropertyName().isEmpty(),"Native Gaussian fixture has runtime property override");
        JsonObject g=object("type","set_vision_pipeline_stage","vision_settings_id",BOTTOM,"stage_name",gaussian.getName(),"stage_type","gaussian","parameters",object("kernel_size",5));
        reject("VISION_PARAMETER_OVERRIDDEN","direct runtime Gaussian override",()->NativeVisionSettings.stage(config,rows(g)));
        check(xml(settings).equals(before),"Runtime override rejection preserves native configuration");

        NativeVisionSettings.Patch assignment=NativeVisionSettings.stage(config,rows(parameterRow(BOTTOM,"pThreshold",128)));
        check(xml(settings).equals(before),"Numeric assignment preview does not mutate native settings");assignment.apply();
        check(config.getVisionSettings(BOTTOM)==settings,"Assignment preserves the registered native settings object");
        JsonObject exposed=parameter(profile(describe(),BOTTOM),"pThreshold");
        check(exposed.get("assigned_value").getAsInt()==128,"Description exposes explicit numeric assignment128");
        applyNumeric(settings);check(((Threshold)settings.getPipeline().getStage("threshold")).getThreshold()==128,"Actual native ParameterNumeric.process applies assigned128");
        apply(parameterRow(BOTTOM,"pThreshold",null));
        check(settings.getPipelineParameterAssignments()==null||!settings.getPipelineParameterAssignments().containsKey("pThreshold"),"Null removes the assignment rather than storing a null entry");
        applyNumeric(settings);check(((Threshold)settings.getPipeline().getStage("threshold")).getThreshold()==100,"Cleared assignment restores actual native default100");
        nativeBooleanAssignment();
        groups.add("overridden direct edits reject; explicit native numeric/bool assignments execute and null restores native defaults");
    }
    static void applyNumeric(BottomVisionSettings settings) throws Exception {
        CvPipeline pipeline=settings.getPipeline();Map<String,Object> assignments=settings.getPipelineParameterAssignments();
        pipeline.setProperty("pThreshold",assignments==null?null:assignments.get("pThreshold"));
        ((ParameterNumeric)pipeline.getStage("pThreshold")).process(pipeline);numericApplications++;
    }
    static void nativeBooleanAssignment() throws Exception {
        BottomVisionSettings settings=new BottomVisionSettings(BOOLEAN);
        CvPipeline pipeline=new CvPipeline();ParameterBool parameter=new ParameterBool();Threshold threshold=new Threshold();
        parameter.setParameterLabel("Invert");parameter.setStageName("threshold");parameter.setPropertyName("invert");parameter.setDefaultValue(false);
        pipeline.add("pInvert",parameter);pipeline.add("threshold",threshold);settings.setPipeline(pipeline);config.addVisionSettings(settings);
        apply(parameterRow(BOOLEAN,"pInvert",true));applyBoolean(settings);
        check(((Threshold)settings.getPipeline().getStage("threshold")).isInvert(),"Actual native ParameterBool.process applies true assignment");
        check(parameter(profile(describe(),BOOLEAN),"pInvert").get("assigned_value").getAsBoolean(),"Description exposes Boolean assignment without coercion");
        apply(parameterRow(BOOLEAN,"pInvert",null));applyBoolean(settings);
        check(!((Threshold)settings.getPipeline().getStage("threshold")).isInvert(),"Null Boolean assignment restores false native default");
    }
    static void applyBoolean(BottomVisionSettings settings) throws Exception {
        CvPipeline pipeline=settings.getPipeline();Map<String,Object> assignments=settings.getPipelineParameterAssignments();
        pipeline.setProperty("pInvert",assignments==null?null:assignments.get("pInvert"));
        ((ParameterBool)pipeline.getStage("pInvert")).process(pipeline);booleanApplications++;
    }
    static void fullReadback() throws Exception {
        JsonObject bottom=bottomRow(),fiducial=fiducialRow();apply(bottom,fiducial);
        JsonObject view=describe();
        for(JsonObject input:Arrays.asList(bottom,fiducial)) {
            String id=input.get("vision_settings_id").getAsString();JsonObject actual=profile(view,id).getAsJsonObject("parameters");
            check(actual!=null,"Typed profile has editable-field readback: "+id);
            for(Map.Entry<String,JsonElement> entry:input.entrySet())if(!entry.getKey().equals("type")&&!entry.getKey().equals("vision_settings_id"))
                check(entry.getValue().equals(actual.get(entry.getKey())),"Exact typed field readback "+id+"/"+entry.getKey());
        }
        check(view.get("fiducial_enabled_semantics").getAsString().contains("does not use"),"Stored fiducial enabled flag is not advertised as an execution gate");
        groups.add("every typed bottom/fiducial setting reads back without inferring physical qualification");
    }
}
