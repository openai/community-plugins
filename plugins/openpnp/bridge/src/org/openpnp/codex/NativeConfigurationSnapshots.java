/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.openpnp.model.Configuration;
import org.openpnp.spi.Feeder;
import org.openpnp.machine.reference.ReferenceFeeder;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Typed configuration rollback, excluding physical state, job history and validation evidence. */
public final class NativeConfigurationSnapshots {
    private static final Gson GSON=new Gson();
    private static final int MAX_CHANGES=10000;
    private static final int MAX_DOCUMENT_BYTES=8*1024*1024,MAX_IDENTITIES=30000,MAX_NODES=500000;
    private static final List<String> IDENTITY_KINDS=Arrays.asList("packages","parts","feeders","cameras","nozzles","nozzle_tips","axes","heads");
    private NativeConfigurationSnapshots() { }
    public static final class Snapshot {
        public final Map<String,Object> document;
        private final JsonArray changes;
        private final Map<String,List<String>> identities;
        private final Map<String,Integer> feedCounts;
        private Snapshot(Map<String,Object> document,JsonArray changes,Map<String,List<String>> identities,Map<String,Integer> feedCounts){this.document=document;this.changes=new JsonParser().parse(changes.toString()).getAsJsonArray();this.identities=new LinkedHashMap<>();for(Map.Entry<String,List<String>> entry:identities.entrySet())this.identities.put(entry.getKey(),new ArrayList<>(entry.getValue()));this.feedCounts=new LinkedHashMap<>(feedCounts);}
    }
    public static final class Restore {
        private final List<NativeSettings.Patch> patches;
        public final Map<String,Object> report;
        private Restore(List<NativeSettings.Patch> patches,Map<String,Object> report){this.patches=patches;this.report=report;}
        public void validateCurrentState()throws Exception{for(NativeSettings.Patch patch:patches)patch.validateCurrentState();}
        public boolean invalidatesAxisDependencies(){return patches.stream().anyMatch(patch->patch.requiresRetainedPlan()&&!patch.isVacuumSensingChange());}
        public boolean invalidatesCameraRegistration(){return patches.stream().anyMatch(patch->patch.metadata().stream().anyMatch(effect->"set_camera_geometry".equals(effect.get("type"))));}
        public void apply()throws Exception{validateCurrentState();for(NativeSettings.Patch patch:patches)patch.apply();}
    }

    /**
     * Decode a typed snapshot after the Bridge verifies its original owned artifact UUID, kind and
     * SHA-256. This is data-only: it grants no artifact authority and reads no native XML or paths.
     * NativeSettings.stage in prepareRestore validates every exact typed field/reference/range
     * against the current native models before any patch is applied. Version-1 snapshots predating
     * head-location settings may omit the heads inventory; a head-targeted change still requires it.
     */
    public static Snapshot decode(JsonObject input)throws Exception {
        if(input==null)invalid("Expected a typed snapshot object");
        bounded(input,0,new int[]{0});String encoded=input.toString();
        if(encoded.getBytes(StandardCharsets.UTF_8).length>MAX_DOCUMENT_BYTES)invalid("Typed snapshot exceeds eight MiB");
        JsonObject value=new JsonParser().parse(encoded).getAsJsonObject();
        keys(value,new HashSet<>(Arrays.asList("version","upstream_commit","scope","typed_changes","omissions","identity_inventory","feed_counts_at_capture","settings_readback","physical_state_restored","full_configuration_restore","newer_material_counts_preserved","job_placed_history_replaced","excluded_state")),true);
        if(integral(value.get("version"),1,1,"version")!=1||!Bridge.UPSTREAM.equals(string(value.get("upstream_commit"),128,"upstream_commit"))||!"representable-typed-settings".equals(string(value.get("scope"),128,"scope")))invalid("Typed snapshot version, pinned upstream or scope mismatch");
        flag(value,"physical_state_restored",false);flag(value,"full_configuration_restore",false);flag(value,"newer_material_counts_preserved",true);flag(value,"job_placed_history_replaced",false);
        JsonObject inventory=object(value.get("identity_inventory"),"identity_inventory");keys(inventory,new HashSet<>(IDENTITY_KINDS),false);
        Map<String,List<String>> identities=new LinkedHashMap<>();int total=0;
        for(String kind:IDENTITY_KINDS){
            if(!inventory.has(kind)){if(kind.equals("heads"))continue;invalid("Missing native identity inventory: "+kind);}
            JsonArray entries=array(inventory.get(kind),10000,"identity_inventory."+kind);List<String> ids=new ArrayList<>();Set<String> unique=new HashSet<>();
            for(JsonElement entry:entries){String id=string(entry,1024,"native identity");if(id.isEmpty()||!unique.add(identityKey(kind,id)))invalid("Missing or duplicated native identity in "+kind);ids.add(id);if(++total>MAX_IDENTITIES)invalid("Native identity inventory exceeds its aggregate limit");}
            identities.put(kind,ids);
        }
        JsonObject rawCounts=object(value.get("feed_counts_at_capture"),"feed_counts_at_capture");if(rawCounts.entrySet().size()>10000)invalid("Too many native feeder count records");Map<String,Integer> feedCounts=new LinkedHashMap<>();
        for(Map.Entry<String,JsonElement> entry:rawCounts.entrySet()){requireIdentity(identities,"feeders",entry.getKey());feedCounts.put(entry.getKey(),integral(entry.getValue(),0,Integer.MAX_VALUE,"feed count"));}
        JsonArray changes=array(value.get("typed_changes"),MAX_CHANGES,"typed_changes");
        for(JsonElement item:changes){JsonObject change=object(item,"typed change");String type=string(change.get("type"),128,"change type");
            if(!NativeSettings.hasType(type)||type.startsWith("create_")||NativeVisionSettings.TYPES.contains(type)||NativeMappedAxisSettings.TYPE.equals(type)||NativeVacuumSettings.TYPE.equals(type))invalid("Unsupported snapshot setting type: "+type);
            for(String spec:Arrays.asList("package_id:packages","part_id:parts","feeder_id:feeders","camera_id:cameras","nozzle_id:nozzles","nozzle_tip_id:nozzle_tips","axis_id:axes","head_id:heads")){String[] pair=spec.split(":");if(change.has(pair[0]))requireIdentity(identities,pair[1],string(change.get(pair[0]),1024,pair[0]));}
            if(change.has("nozzle_tip_ids"))for(JsonElement tip:array(change.get("nozzle_tip_ids"),64,"nozzle_tip_ids"))requireIdentity(identities,"nozzle_tips",string(tip,1024,"nozzle tip identity"));
            if(change.has("feed_count")){String id=string(change.get("feeder_id"),1024,"feeder_id");int count=integral(change.get("feed_count"),0,Integer.MAX_VALUE,"feed_count");if(!feedCounts.containsKey(id)||feedCounts.get(id)!=count)invalid("Geometry count does not match the captured native feeder count record");}
        }
        JsonArray omissions=array(value.get("omissions"),30000,"omissions");
        for(JsonElement item:omissions){JsonObject omission=object(item,"omission");keys(omission,new HashSet<>(Arrays.asList("type","target_id","code","message")),false);for(String required:Arrays.asList("type","target_id","code"))string(omission.get(required),1024,"omission."+required);if(omission.has("message"))string(omission.get("message"),4096,"omission.message");}
        object(value.get("settings_readback"),"settings_readback");
        for(JsonElement item:array(value.get("excluded_state"),64,"excluded_state"))string(item,4096,"excluded_state entry");
        Map<String,Object> document=new LinkedHashMap<>();for(Map.Entry<String,JsonElement> entry:value.entrySet())document.put(entry.getKey(),plain(entry.getValue()));
        document.put("typed_changes",new JsonParser().parse(changes.toString()).getAsJsonArray());document.put("settings_readback",value.getAsJsonObject("settings_readback"));
        document.put("identity_inventory",identities);document.put("feed_counts_at_capture",feedCounts);
        return new Snapshot(document,changes,identities,feedCounts);
    }

    /** Capture every representable current setting; omitted values have explicit reasons. */
    public static Snapshot capture(Configuration config)throws Exception {
        JsonObject view=GSON.toJsonTree(NativeSettings.describe(config)).getAsJsonObject();
        JsonArray changes=new JsonArray();List<Map<String,Object>> omissions=new ArrayList<>();
        omissions.add(Bridge.map("type","vision_configuration","target_id","machine","code","NOT_IN_VERSION_ONE_RESTORE","message","Vision profile/assignment/pipeline readback is retained, but this version-one snapshot does not restore it"));
        omissions.add(Bridge.map("type","vacuum_sensing_settings","target_id","machine","code","NOT_IN_VERSION_ONE_RESTORE","message","Sensing methods, thresholds, check flags, part-off probe/dwell timing and sensor bindings are readback only; general nozzle/tip pick/place dwell remains in its existing restore families and can affect sensing timing"));
        omissions.add(Bridge.map("type","vacuum_sensing_source","target_id","machine","code","PROCESS_LOCAL_AUTHORITY_NOT_RESTORED","message","Controlled sensor callbacks, source generation and sensing readiness never transfer through a typed snapshot"));
        omissions.add(Bridge.map("type","nozzle_occupancy","target_id","machine","code","OPERATIONAL_STATE_NOT_RESTORED","message","Native held parts, measured occupancy and durable retained/unknown history are not restored or reconciled"));
        add(config,changes,omissions,select(view,"set_machine_speed","speed:machine_speed"));
        JsonObject processor=view.getAsJsonObject("job_processor");
        add(config,changes,omissions,select(processor,"set_job_planner_settings","job_order","strategy","optimize_multiple_nozzles","pre_rotate_all_nozzles","stepping_to_next_motion"));
        add(config,changes,omissions,select(processor,"set_job_retry_settings","vision_attempts","placement_attempts","feeder_fault_limit","feeder_fault_window"));
        add(config,changes,omissions,select(view,"set_machine_discard_location","location:discard_location"));
        for(JsonElement value:view.getAsJsonArray("heads")){
            JsonObject head=value.getAsJsonObject(),location=head.has("park_location")&&head.get("park_location").isJsonObject()?head.getAsJsonObject("park_location"):new JsonObject();
            JsonObject change=select(location,"set_head_park_location","x_mm","y_mm");change.add("head_id",head.get("head_id"));add(config,changes,omissions,change);
        }
        for(JsonElement value:view.getAsJsonArray("packages")){JsonObject p=value.getAsJsonObject();add(config,changes,omissions,select(p,"set_package_footprint","package_id","description","body_width_mm","body_height_mm","pads"));add(config,changes,omissions,select(p,"set_package_compatibility","package_id","nozzle_tip_ids"));}
        for(JsonElement value:view.getAsJsonArray("parts")){JsonObject part=value.getAsJsonObject();if(!add(config,changes,omissions,select(part,"set_part_properties","part_id","package_id","name","height_mm","speed")))add(config,changes,omissions,select(part,"set_part_height","part_id","height_mm"));add(config,changes,omissions,select(part,"set_part_retry_settings","part_id","pick_retries"));}
        Map<String,Integer> feedCounts=new LinkedHashMap<>();
        for(JsonElement value:view.getAsJsonArray("feeders")){
            JsonObject f=value.getAsJsonObject();String id=f.get("feeder_id").getAsString();
            add(config,changes,omissions,select(f,"assign_feeder","feeder_id","part_id"));add(config,changes,omissions,select(f,"set_feeder_enabled","feeder_id","enabled"));
            add(config,changes,omissions,select(f,"set_feeder_retry_settings","feeder_id","feed_retries","pick_retries"));
            if(f.has("feed_count"))feedCounts.put(id,integral(f.get("feed_count"),0,Integer.MAX_VALUE,"native captured feed count"));
            String type=f.get("native_class").getAsString();
            if(type.equals(ReferenceStripFeeder.class.getName()))add(config,changes,omissions,select(f,"set_strip_feeder_geometry","feeder_id","location","reference_hole","last_hole","part_pitch_mm","hole_pitch_mm","tape_width_mm","hole_diameter_mm","reference_hole_to_part_mm","feed_count","max_feed_count"));
            else if(type.equals(ReferenceTrayFeeder.class.getName()))add(config,changes,omissions,select(f,"set_tray_feeder_geometry","feeder_id","location","count_x","count_y","pitch_x_mm","pitch_y_mm","feed_count"));
            else omissions.add(Bridge.map("target_id",id,"type","feeder_geometry","code","UNSUPPORTED_NATIVE_CLASS"));
        }
        for(JsonElement value:view.getAsJsonArray("cameras")){
            JsonObject c=value.getAsJsonObject();add(config,changes,omissions,select(c,"set_camera_geometry","camera_id","units_per_pixel_x_mm","units_per_pixel_y_mm","working_plane_z_mm","head_offsets"));
            captureCameraSettling(config,changes,omissions,c);
        }
        for(JsonElement value:view.getAsJsonArray("nozzles"))add(config,changes,omissions,select(value.getAsJsonObject(),"set_nozzle_settings","nozzle_id","pick_dwell_ms","place_dwell_ms","nozzle_tip_ids"));
        for(JsonElement value:view.getAsJsonArray("nozzle_tips"))add(config,changes,omissions,select(value.getAsJsonObject(),"set_nozzle_tip_settings","nozzle_tip_id","pick_dwell_ms","place_dwell_ms"));
        for(JsonElement value:view.getAsJsonArray("axes")){
            JsonObject a=value.getAsJsonObject();
            if(a.has("backlash_settings_editable")&&a.get("backlash_settings_editable").getAsBoolean())add(config,changes,omissions,select(a,NativeAxisBacklashSettings.TYPE,"axis_id","method","offset_mm","speed_factor","sneak_up_mm","acceptable_tolerance_mm"));
            else omissions.add(Bridge.map("target_id",a.get("axis_id").getAsString(),"type",NativeAxisBacklashSettings.TYPE,"code",a.has("backlash_settings_fault")?a.get("backlash_settings_fault").getAsString():"UNSUPPORTED_AXIS_TOPOLOGY"));
            if(a.has("soft_limit_low_enabled")&&a.get("soft_limit_low_enabled").getAsBoolean()&&a.get("soft_limit_high_enabled").getAsBoolean())add(config,changes,omissions,select(a,"set_axis_motion_limits","axis_id","soft_limit_low_mm","soft_limit_high_mm","feedrate_mm_per_s","acceleration_mm_per_s2","jerk_mm_per_s3"));
            else omissions.add(Bridge.map("target_id",a.get("axis_id").getAsString(),"type",a.has("mapped_geometry_editable")?NativeMappedAxisSettings.TYPE:"set_axis_motion_limits","code",a.has("mapped_geometry_editable")?"MAPPED_GEOMETRY_NOT_IN_VERSION_ONE_RESTORE":"DISABLED_OR_NONLINEAR_AXIS_LIMITS_NOT_REPRESENTABLE"));
        }
        Map<String,List<String>> identities=identities(view);
        Map<String,Object> document=Bridge.map("version",1,"upstream_commit",Bridge.UPSTREAM,"scope","representable-typed-settings","typed_changes",changes,"omissions",omissions,"identity_inventory",identities,"feed_counts_at_capture",feedCounts,"settings_readback",view,
            "physical_state_restored",false,"full_configuration_restore",false,"newer_material_counts_preserved",true,"job_placed_history_replaced",false,
            "excluded_state",Arrays.asList("vacuum sensing methods, thresholds, check flags, probe timing and sensor bindings (readback only)","process-local sensing source authority, generation and readiness","native/measured nozzle occupancy and durable retained/unknown sensing history","calibration measurement models and enabled flags","homed/enabled physical machine state","installed nozzle tip","job documents and placed history","planner implementation replacement and unsupported processor/strategy classes","job processor immediate-calibration and fiducial-nesting fields outside the bounded public setter surface","native part/feeder fault history and processing counters","park Z/rotation ignored by the native park recipe","unsupported or non-representable native fields","camera Motion/nondefault dynamic preprocessing and unsupported image dimensions/transforms (readback only)","FixedTime camera inactive settle_timeout_ms, settle_debounce, settle_threshold_percent and settle_full_color (readback only)","deletion of later-added identities","vision profiles, assignments and pipeline parameters (readback only; not restored by version-one typed snapshots)","mapped-axis point geometry/source links and their source soft-limit transactions (readback only; not restored by version-one typed snapshots)"));
        // Capture and restart decoding enforce the same representational bounds. Invalid native
        // counters cannot become an archive that would later invent or refund consumed material.
        return decode(GSON.toJsonTree(document).getAsJsonObject());
    }

    /** Package-private native camera row builder; capacity failures must abort the whole snapshot. */
    static void captureCameraSettling(Configuration config,JsonArray changes,List<Map<String,Object>> omissions,JsonObject c)throws Exception {
        if(c.has("settle_method")&&!c.get("settle_method").isJsonNull()&&"FixedTime".equals(c.get("settle_method").getAsString())) {
            add(config,changes,omissions,select(c,"set_camera_settling","camera_id","settle_time_ms"));
            omissions.add(Bridge.map("target_id",c.get("camera_id").getAsString(),"type","inactive_dynamic_settling_parameters","code","INACTIVE_CAMERA_PARAMETERS_NOT_RESTORED","message","FixedTime snapshots restore active method and settle_time_ms only; inactive settle_timeout_ms, settle_debounce, settle_threshold_percent and settle_full_color remain readback-only"));
            return;
        }
        JsonObject dynamic=select(c,NativeCameraSettling.TYPE,"camera_id","method:settle_method","timeout_ms:settle_timeout_ms","debounce:settle_debounce","threshold_percent:settle_threshold_percent","full_color:settle_full_color");
        try {NativeSettings.validate(config,dynamic);}
        catch(Bridge.Fault failure){omissions.add(Bridge.map("target_id",c.get("camera_id").getAsString(),"type",NativeCameraSettling.TYPE,"code",failure.code,"message",failure.getMessage()));return;}
        // Only representability errors become omissions. In particular, a capacity failure while
        // appending the final dynamic row must never publish a snapshot containing only FixedTime.
        add(config,changes,omissions,select(c,"set_camera_settling","camera_id","settle_time_ms"));
        add(config,changes,omissions,dynamic);
    }

    /** All native patches stage before the first effect; current consumed counts never decrease. */
    public static Restore prepareRestore(Configuration config,Snapshot snapshot)throws Exception {
        JsonObject current=GSON.toJsonTree(NativeSettings.describe(config)).getAsJsonObject();Map<String,List<String>> currentIds=identities(current);
        Map<String,List<String>> extra=new LinkedHashMap<>();
        for(Map.Entry<String,List<String>> entry:snapshot.identities.entrySet()){
            List<String> present=currentIds.get(entry.getKey());List<String> missing=new ArrayList<>(entry.getValue());missing.removeAll(present);
            if(!missing.isEmpty())throw new Bridge.Fault("RESTORE_IDENTITY_MISSING","Snapshot identities are missing; no models were changed",Bridge.map("kind",entry.getKey(),"missing",missing));
            List<String> additions=new ArrayList<>(present);additions.removeAll(entry.getValue());if(!additions.isEmpty())extra.put(entry.getKey(),additions);
        }
        // Validate the original rows before inspecting or filtering them. In particular, a
        // redundant enabled flag with extra fields must not evade NativeSettings' exact schema.
        stageAll(config,snapshot.changes);
        JsonArray restored=new JsonParser().parse(snapshot.changes.toString()).getAsJsonArray();List<Map<String,Object>> preserved=new ArrayList<>();String restoredJobOrder=null;
        for(JsonElement value:restored){JsonObject change=value.getAsJsonObject();if("set_job_planner_settings".equals(change.get("type").getAsString()))restoredJobOrder=change.get("job_order").getAsString();if(!change.has("feeder_id"))continue;
            Feeder feeder=config.getMachine().getFeeder(change.get("feeder_id").getAsString());int now=feedCount(feeder);
            if(feeder instanceof ReferenceStripFeeder||feeder instanceof ReferenceTrayFeeder){if(!snapshot.feedCounts.containsKey(feeder.getId()))invalid("Missing captured native count for a restored feeder: "+feeder.getId());if(now<0)throw new Bridge.Fault("RESTORE_MATERIAL_CONFLICT","Current native feeder consumption is negative; typed rollback cannot infer its history",Bridge.map("feeder_id",feeder.getId()));}
            int before=snapshot.feedCounts.getOrDefault(feeder.getId(),now);
            if(change.has("feed_count")){int captured=change.get("feed_count").getAsInt();int floor=Math.max(now,captured);int capacity=change.has("max_feed_count")?change.get("max_feed_count").getAsInt():change.get("count_x").getAsInt()*change.get("count_y").getAsInt();if(floor>capacity)throw new Bridge.Fault("RESTORE_MATERIAL_CONFLICT","Restoring the older feeder geometry would reduce capacity below already consumed material",Bridge.map("feeder_id",feeder.getId(),"retained_count",floor,"snapshot_capacity",capacity));change.addProperty("feed_count",floor);if(floor>captured)preserved.add(Bridge.map("feeder_id",feeder.getId(),"captured_count",captured,"retained_count",floor));}
            if("set_feeder_enabled".equals(change.get("type").getAsString())&&now>before&&!feeder.isEnabled()&&change.get("enabled").getAsBoolean()){change.addProperty("enabled",false);preserved.add(Bridge.map("feeder_id",feeder.getId(),"enabled_retained",false,"reason","disabled-after-newer-consumption"));}
            if("set_feeder_enabled".equals(change.get("type").getAsString())&&!feeder.isEnabled()&&change.get("enabled").getAsBoolean()&&feeder instanceof ReferenceFeeder&&!((ReferenceFeeder)feeder).summariseJobFaults().isEmpty()){change.addProperty("enabled",false);preserved.add(Bridge.map("feeder_id",feeder.getId(),"enabled_retained",false,"reason","disabled-with-current-native-fault-history"));}
        }
        // ReferenceFeeder.setEnabled(true) clears fault history even for true -> true. A typed
        // rollback must not erase newer fault observations merely to repeat the same flag.
        JsonArray effective=new JsonArray();for(JsonElement value:restored){JsonObject change=value.getAsJsonObject();
            if(NativeAxisBacklashSettings.TYPE.equals(change.get("type").getAsString())){
                JsonObject axis=null;for(JsonElement entry:current.getAsJsonArray("axes"))if(entry.getAsJsonObject().get("axis_id").getAsString().equals(change.get("axis_id").getAsString()))axis=entry.getAsJsonObject();
                if(axis!=null&&select(axis,NativeAxisBacklashSettings.TYPE,"axis_id","method","offset_mm","speed_factor","sneak_up_mm","acceptable_tolerance_mm").equals(change))continue;
            }
            if("set_feeder_enabled".equals(change.get("type").getAsString())){Feeder feeder=config.getMachine().getFeeder(change.get("feeder_id").getAsString());if(feeder.isEnabled()==change.get("enabled").getAsBoolean())continue;}
            effective.add(change);
        }restored=effective;
        List<NativeSettings.Patch> patches=stageAll(config,restored);
        Map<String,Object> report=Bridge.map("restored",true,"scope","representable-typed-settings","restored_change_count",restored.size(),"restored_job_order",restoredJobOrder,"snapshot_omissions",snapshot.document.get("omissions"),"added_identities_preserved",extra,"material_state_preserved",preserved,"newer_material_counts_preserved",true,"native_feeder_fault_history_replaced",false,"job_placed_history_replaced",false,"calibration_evidence_restored",false,"sensing_settings_restored",false,"sensing_source_authority_restored",false,"nozzle_occupancy_restored",false,"requires_validation",true,"physical_state_restored",false,"full_configuration_restore",false);
        return new Restore(patches,report);
    }
    private static List<NativeSettings.Patch> stageAll(Configuration config,JsonArray changes)throws Exception{List<NativeSettings.Patch> patches=new ArrayList<>();JsonArray chunk=new JsonArray();for(JsonElement change:changes){chunk.add(change);if(chunk.size()==100){patches.add(NativeSettings.stage(config,chunk));chunk=new JsonArray();}}if(chunk.size()>0)patches.add(NativeSettings.stage(config,chunk));return patches;}
    private static boolean add(Configuration config,JsonArray changes,List<Map<String,Object>> omissions,JsonObject change)throws Exception {
        if(changes.size()>=MAX_CHANGES)throw new Bridge.Fault("SNAPSHOT_CAPACITY","Typed snapshot exceeds 10000 changes");
        try{NativeSettings.validate(config,change);changes.add(change);return true;}catch(Bridge.Fault e){omissions.add(Bridge.map("type",change.get("type").getAsString(),"target_id",target(change),"code",e.code,"message",e.getMessage()));return false;}
    }
    private static JsonObject select(JsonObject source,String type,String...fields){JsonObject change=new JsonObject();change.addProperty("type",type);for(String spec:fields){String[] pair=spec.split(":",2);String key=pair[0],from=pair.length==2?pair[1]:key;if(source.has(from))change.add(key,source.get(from));}return change;}
    private static String target(JsonObject c){for(String field:Arrays.asList("feeder_id","part_id","package_id","camera_id","nozzle_id","nozzle_tip_id","axis_id","head_id"))if(c.has(field)&&!c.get(field).isJsonNull())return c.get(field).getAsString();return c.has("type")&&c.get("type").getAsString().startsWith("set_job_")?"job-processor":"machine";}
    private static Map<String,List<String>> identities(JsonObject view){Map<String,List<String>> result=new LinkedHashMap<>();for(String spec:Arrays.asList("packages:package_id","parts:part_id","feeders:feeder_id","cameras:camera_id","nozzles:nozzle_id","nozzle_tips:nozzle_tip_id","axes:axis_id","heads:head_id")){String[] p=spec.split(":");List<String> ids=new ArrayList<>();for(JsonElement item:view.getAsJsonArray(p[0]))ids.add(item.getAsJsonObject().get(p[1]).getAsString());Collections.sort(ids);result.put(p[0],ids);}return result;}
    private static int feedCount(Feeder feeder){if(feeder instanceof ReferenceTrayFeeder)return((ReferenceTrayFeeder)feeder).getFeedCount();if(feeder instanceof ReferenceStripFeeder)return((ReferenceStripFeeder)feeder).getFeedCount();return 0;}
    private static void invalid(String message)throws Bridge.Fault{throw new Bridge.Fault("SNAPSHOT_INVALID",message);}
    private static String string(JsonElement value,int maximum,String label)throws Exception{if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString())invalid("Expected string "+label);String text=value.getAsString();if(text.length()>maximum||text.chars().anyMatch(c->c<32||c==127))invalid("Invalid string "+label);return text;}
    private static int integral(JsonElement value,int minimum,int maximum,String label)throws Exception{if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber())invalid("Expected integral "+label);int n;try{n=new BigDecimal(value.getAsString()).intValueExact();}catch(NumberFormatException|ArithmeticException error){invalid("Invalid integral "+label);return 0;}if(n<minimum||n>maximum)invalid("Invalid integral "+label);return n;}
    private static JsonObject object(JsonElement value,String label)throws Exception{if(value==null||!value.isJsonObject())invalid("Expected object "+label);return value.getAsJsonObject();}
    private static JsonArray array(JsonElement value,int maximum,String label)throws Exception{if(value==null||!value.isJsonArray()||value.getAsJsonArray().size()>maximum)invalid("Expected bounded array "+label);return value.getAsJsonArray();}
    private static void keys(JsonObject object,Set<String> allowed,boolean exact)throws Exception{Set<String> observed=new HashSet<>();for(Map.Entry<String,JsonElement> entry:object.entrySet())observed.add(entry.getKey());if(!allowed.containsAll(observed)||(exact&&!observed.equals(allowed)))invalid("Unexpected or missing typed snapshot fields");}
    private static void flag(JsonObject value,String key,boolean expected)throws Exception{JsonElement field=value.get(key);if(field==null||!field.isJsonPrimitive()||!field.getAsJsonPrimitive().isBoolean()||field.getAsBoolean()!=expected)invalid("Unsupported snapshot claim: "+key);}
    private static String identityKey(String kind,String id){return kind.equals("parts")||kind.equals("packages")?id.toUpperCase():id;}
    private static void requireIdentity(Map<String,List<String>> inventory,String kind,String id)throws Exception{List<String> ids=inventory.get(kind);if(ids==null||ids.stream().noneMatch(candidate->identityKey(kind,candidate).equals(identityKey(kind,id))))invalid("Typed change/count references an identity outside its snapshot inventory: "+kind+"/"+id);}
    private static void bounded(JsonElement value,int depth,int[] nodes)throws Exception{if(value==null||++nodes[0]>MAX_NODES||depth>24)invalid("Typed snapshot JSON exceeds structural limits");if(value.isJsonObject()){for(Map.Entry<String,JsonElement> entry:value.getAsJsonObject().entrySet()){if(entry.getKey().length()>1024||entry.getKey().chars().anyMatch(c->c<32||c==127))invalid("Invalid snapshot key");bounded(entry.getValue(),depth+1,nodes);}}else if(value.isJsonArray()){if(value.getAsJsonArray().size()>30000)invalid("Snapshot array exceeds structural limit");for(JsonElement item:value.getAsJsonArray())bounded(item,depth+1,nodes);}else if(value.isJsonPrimitive()){JsonPrimitive p=value.getAsJsonPrimitive();if(p.isString()&&p.getAsString().length()>1048576)invalid("Snapshot string exceeds structural limit");if(p.isNumber()&&!Double.isFinite(p.getAsDouble()))invalid("Snapshot numeric value is not finite");}}
    // Pinned Gson serializes its LazilyParsedNumber as a reflective object in a generic Map.
    // Materialize numbers as BigDecimal so exported decoded documents remain numeric JSON.
    private static Object plain(JsonElement value){if(value==null||value.isJsonNull())return null;if(value.isJsonObject()){Map<String,Object> object=new LinkedHashMap<>();for(Map.Entry<String,JsonElement> entry:value.getAsJsonObject().entrySet())object.put(entry.getKey(),plain(entry.getValue()));return object;}if(value.isJsonArray()){List<Object> list=new ArrayList<>();for(JsonElement item:value.getAsJsonArray())list.add(plain(item));return list;}JsonPrimitive p=value.getAsJsonPrimitive();return p.isBoolean()?p.getAsBoolean():p.isNumber()?new BigDecimal(p.getAsString()):p.getAsString();}
}
